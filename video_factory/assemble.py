"""素材池拼装 + 精确时长控制（P2）。

流程定位（批量生产 P2）：吃上游 rewrite.json 的分节文案 + 一个素材目录，
按各节字数占比从素材池切片、缩放到目标画幅（默认 1920x1080，可选 9:16/1:1/3:4）、
concat 合并成一条成片；素材与画幅不匹配时按 fit 模式（pad 补边 / crop 裁切 /
blur 模糊背景填充）处理；可选配上一条 TTS/本地音轨，并以音频时长驱动最终成片
时长（音频短则截断视频，音频长则补帧），使 release.mp4 与音频对齐（±0.5s）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from dataclasses import asdict, dataclass
from itertools import groupby
from pathlib import Path
from typing import Callable, Sequence

from video_factory import credentials_store, stage_report
from video_factory.asset_pool import (
    IMAGE_SUFFIXES,
    IMAGE_VIRTUAL_DURATION,
    AssetClip,
    AssetPoolError,
    ClipSlice,
    SectionAllocation,
    allocate_sections_to_clips,
    scan_asset_pool,
)
from video_factory.pipeline import (
    TTSConfig,
    TTSProviderError,
    synthesize_voiceover_text,
)

Runner = Callable[..., subprocess.CompletedProcess]

TARGET_WIDTH = 1920
TARGET_HEIGHT = 1080
TARGET_FPS = 30
# 支持的画幅预设：比例名 -> (宽, 高)。默认 16:9。
ASPECT_PRESETS = {
    "16:9": (1920, 1080),
    "9:16": (1080, 1920),
    "1:1": (1080, 1080),
    "3:4": (1080, 1440),
}
# 素材与画幅不匹配时的填充模式：pad 补边、crop 裁切、blur 模糊背景。
FIT_MODES = ("pad", "crop", "blur")
# 成片与音频时长允许的对齐误差（秒），超出会在报告里记为 warning。
DURATION_TOLERANCE = 0.5
DEFAULT_TARGET_DURATION = 90.0
# BGM 自动 ducking（sidechaincompress）四参：配音一响就压低 BGM，配音停再放回。
# 实测该组合可得约 7.7dB 压低，既不糊配音又保留背景氛围，故固定不上 CLI。
BGM_DUCK_THRESHOLD = 0.03
BGM_DUCK_RATIO = 8
BGM_DUCK_ATTACK_MS = 20
BGM_DUCK_RELEASE_MS = 400
# BGM 默认音量与淡入淡出秒数（CLI 可覆盖）。
DEFAULT_BGM_VOLUME = 0.2
DEFAULT_BGM_FADE = 2.0
# ffmpeg 全局静默参数：-hide_banner 去掉版本/编译配置横幅，-loglevel error 只留真正的错误。
# 放在 `ffmpeg -y` 之后、输入之前。不加时 ffmpeg 会先打几百字符版本 banner，_run 取到的
# 报错基本是无用的版本信息而非真实失败原因，严重拖慢排障。
_FF_QUIET = ("-hide_banner", "-loglevel", "error")


class AssemblyError(RuntimeError):
    pass


@dataclass(frozen=True)
class AssemblyPlan:
    target_duration: float
    fps: int
    width: int
    height: int
    clips: tuple[AssetClip, ...]
    allocations: tuple[SectionAllocation, ...]
    section_titles: tuple[str, ...]
    fit: str = "pad"


def build_assembly_plan(
    rewrite: dict,
    clips: Sequence[AssetClip],
    target_duration: float | None,
    width: int = TARGET_WIDTH,
    height: int = TARGET_HEIGHT,
    fit: str = "pad",
) -> AssemblyPlan:
    """从 rewrite.json 的 dict 构造分配计划。hook 也算一节参与字数分配。"""
    if fit not in FIT_MODES:
        raise AssemblyError(f"未知的画幅填充模式：{fit}（可选：{'、'.join(FIT_MODES)}）。")
    sections = _sections_from_rewrite(rewrite)
    if not sections:
        raise AssemblyError("rewrite 里没有可用的文案小节（hook + sections 均为空）。")
    duration = _resolve_target_duration(rewrite, target_duration)
    char_counts = [_char_count(text) for _, text in sections]
    allocations = allocate_sections_to_clips(char_counts, clips, duration)
    return AssemblyPlan(
        target_duration=round(duration, 3),
        fps=TARGET_FPS,
        width=width,
        height=height,
        clips=tuple(clips),
        allocations=tuple(allocations),
        section_titles=tuple(title for title, _ in sections),
        fit=fit,
    )


def _sections_from_rewrite(rewrite: dict) -> list[tuple[str, str]]:
    sections: list[tuple[str, str]] = []
    hook = str(rewrite.get("hook") or "").strip()
    if hook:
        sections.append(("hook", hook))
    for item in rewrite.get("sections") or []:
        if not isinstance(item, dict):
            continue
        narration = str(item.get("narration") or "").strip()
        if not narration:
            continue
        title = str(item.get("title") or f"第{len(sections) + 1}节").strip()
        sections.append((title, narration))
    return sections


def _resolve_target_duration(rewrite: dict, target_duration: float | None) -> float:
    if target_duration is not None:
        if target_duration <= 0:
            raise AssemblyError("目标时长必须为正数。")
        return float(target_duration)
    raw = rewrite.get("target_duration_seconds")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = DEFAULT_TARGET_DURATION
    return value if value > 0 else DEFAULT_TARGET_DURATION


def _char_count(text: str) -> int:
    return len("".join(str(text or "").split()))


def _list_ordered_assets(directory: Path) -> list[Path]:
    """返回 gen_assets/ 目录内 img_NN 格式的图片文件，按文件名升序（对应全局拍序）。

    目录不存在或无图片时抛 AssemblyError，让调用方走 except 块统一打印并返回 1。
    """
    if not directory.is_dir():
        raise AssemblyError(f"--ordered-assets 目录不存在：{directory}")
    paths = sorted(
        p for p in directory.iterdir()
        if p.suffix.lower() in IMAGE_SUFFIXES and p.stem.startswith("img_")
    )
    if not paths:
        raise AssemblyError(f"--ordered-assets 目录内未找到 img_* 图片：{directory}")
    return paths


def build_ordered_assembly_plan(
    rewrite: dict,
    image_paths: list[Path],
    target_duration: float | None,
    width: int = TARGET_WIDTH,
    height: int = TARGET_HEIGHT,
    fit: str = "pad",
) -> AssemblyPlan:
    """拍级配图模式下的分配计划：第 k 拍使用第 k 张图，时长=该拍目标时长。

    图片数少于拍数时尾部循环（取最后一张），打印告警但不中断。
    分段渲染仍是「第 k 拍第 k 图」（切片按拍序排列，与图序一致），但**按 beat.section_index
    归组为真实节**：每个真实节一个 SectionAllocation（duration=该节各拍时长求和，slices=该节
    各拍图片切片按拍序排列），section_titles=真实节标题（hook / 各正题节，绝无「-拍N」）。
    如此特效层的章节卡只在真实节边界出现，内部标签不再泄漏进成片；转场「跨节必转」也回到
    真实节边界。
    """
    # 懒惰导入：避免 assemble 与 image_gen 循环依赖（image_gen 会 import llm/rewrite）。
    from video_factory.image_gen import compute_section_durations, plan_beats

    if not image_paths:
        raise AssemblyError("--ordered-assets 模式下 gen_assets/ 目录没有任何图片（img_*）。")
    if fit not in FIT_MODES:
        raise AssemblyError(f"未知的画幅填充模式：{fit}（可选：{'、'.join(FIT_MODES)}）。")

    duration = _resolve_target_duration(rewrite, target_duration)
    section_durs = compute_section_durations(rewrite, duration)
    beats = plan_beats(rewrite, section_durs)

    if not beats:
        raise AssemblyError("rewrite 里没有可用的文案小节（hook + sections 均为空）。")

    n_images = len(image_paths)
    n_beats = len(beats)
    if n_images < n_beats:
        print(
            f"配图告警：图片数（{n_images}）少于拍数（{n_beats}），"
            "尾部循环最后一张图片补足。"
        )

    # 按真实节归组：beats 已按 (section_index, beat_index) 升序，同节的拍连续排列，
    # 故 groupby(section_index) 即可把每节聚成一个 SectionAllocation。节内切片仍按拍序
    # 排列（第 k 拍第 k 图），跨节顺序不变，摊平后的渲染顺序与旧的逐拍分配完全一致。
    allocations: list[SectionAllocation] = []
    section_titles: list[str] = []
    seen_clips: dict[Path, AssetClip] = {}  # 去重，只建一个 AssetClip per 文件

    for section_pos, (_sec_idx, group) in enumerate(
        groupby(beats, key=lambda b: b.section_index)
    ):
        group_beats = list(group)
        slices: list[ClipSlice] = []
        for beat in group_beats:
            img_path = image_paths[min(beat.global_index, n_images - 1)]
            if img_path not in seen_clips:
                seen_clips[img_path] = AssetClip(
                    path=img_path,
                    duration=IMAGE_VIRTUAL_DURATION,
                    width=width,
                    height=height,
                    is_image=True,
                )
            slices.append(ClipSlice(path=img_path, start=0.0, duration=beat.duration))
        allocations.append(SectionAllocation(
            index=section_pos,
            duration=round(sum(b.duration for b in group_beats), 3),
            slices=tuple(slices),
        ))
        # 真实节标题（hook / 正题各节），绝无「-拍N」内部标签。
        section_titles.append(group_beats[0].section_title)

    return AssemblyPlan(
        target_duration=round(duration, 3),
        fps=TARGET_FPS,
        width=width,
        height=height,
        clips=tuple(seen_clips.values()),
        allocations=tuple(allocations),
        section_titles=tuple(section_titles),
        fit=fit,
    )


def render_assembly(
    plan: AssemblyPlan,
    output_dir: Path | str,
    audio_path: Path | None = None,
    runner: Runner = subprocess.run,
    bgm_path: Path | None = None,
    bgm_volume: float = DEFAULT_BGM_VOLUME,
    bgm_fade: float = DEFAULT_BGM_FADE,
) -> dict[str, Path]:
    # BGM 需要配音作为 ducking 基准；单独给 BGM 没有可压低的触发轨，直接报错。
    if bgm_path is not None and audio_path is None:
        raise AssemblyError("使用 BGM 需要先提供配音（--audio 或 --tts），否则没有 ducking 基准。")
    output_dir = Path(output_dir)
    segments_dir = output_dir / "segments"
    segments_dir.mkdir(parents=True, exist_ok=True)

    # 先按渲染顺序摊平各段（记录所属节 + 原始时长），据此定转场位置（章节边界 + 节内 15s），
    # 再让被转场"跟随"的段在渲染时补偿 +0.4s——补偿与转场位置必须同源，故一次算好共用。
    section_indices, segment_durations = _flatten_segments(plan)
    transition_flags = _transition_flags(section_indices, segment_durations)
    segment_paths = _render_segments(plan, segments_dir, runner, transition_flags)
    concat_path = output_dir / "segments.txt"
    silent_video = output_dir / "assembly_silent.mp4"
    transition_points = _concat_segments(
        segment_paths, concat_path, silent_video, plan.fps, runner,
        segment_durations=segment_durations,
        transition_flags=transition_flags,
    )

    release_path = output_dir / "release.mp4"
    audio_duration = None
    if audio_path is not None:
        audio_duration = _probe_duration(Path(audio_path), runner)
        _mux_audio(
            silent_video,
            Path(audio_path),
            release_path,
            runner,
            audio_duration=audio_duration,
            bgm_path=Path(bgm_path) if bgm_path is not None else None,
            bgm_volume=bgm_volume,
            bgm_fade=bgm_fade,
        )
    else:
        _finalize_silent(silent_video, release_path, runner)

    final_duration = _probe_duration(release_path, runner)
    plan_path = output_dir / "assembly_plan.json"
    _write_plan_json(
        plan,
        plan_path,
        audio_path,
        audio_duration,
        final_duration,
        bgm_path=bgm_path,
        bgm_volume=bgm_volume,
        bgm_fade=bgm_fade,
        transition_points=transition_points,
    )
    return {
        "release": release_path,
        "assembly_plan": plan_path,
        "concat": concat_path,
        "silent_video": silent_video,
    }


def _flatten_segments(plan: AssemblyPlan) -> tuple[list[int], list[float]]:
    """按渲染顺序摊平所有切片，返回 (各段所属节索引, 各段原始时长)。

    节索引取 allocation 在 plan.allocations 中的迭代序（相邻段是否同节据此判断——跨节
    必转场）。原始时长即 clip_slice.duration（不含补偿），是内容时间轴与 offset 的基准。
    """
    section_indices: list[int] = []
    durations: list[float] = []
    for section_pos, allocation in enumerate(plan.allocations):
        for clip_slice in allocation.slices:
            section_indices.append(section_pos)
            durations.append(clip_slice.duration)
    return section_indices, durations


def _transition_flags(
    section_indices: Sequence[int], durations: Sequence[float]
) -> list[bool]:
    """判定每个相邻段之间是否转场，返回长度=段数的布尔列表：flags[i]=True 表示第 i 段
    之后紧接一个转场（末段恒 False）。

    密度规则（用户拍板）：①相邻两段跨节 → 章节边界必转场；②同节内部：距"本节上次转场
    （或节起点）"的累计内容时长 ≥ INTRA_SECTION_TRANSITION_INTERVAL 才转一次并清零；
    ③其余相邻段 = 硬切。这样长节内部按 ~15s 一次而非逐段都转，转场总数与被吃掉的时长
    规模都大幅下降（用户 52 片场景由 51 次降到约 18 次）。
    """
    n = len(section_indices)
    flags = [False] * n
    since_last = 0.0  # 距"本节上次转场或节起点"累计的内容时长
    for i in range(n - 1):
        since_last += durations[i]
        if section_indices[i] != section_indices[i + 1]:
            flags[i] = True  # 跨节：章节边界必转场
            since_last = 0.0  # 进入新节，从下一段起重新累计
        elif since_last >= INTRA_SECTION_TRANSITION_INTERVAL:
            flags[i] = True  # 同节内满 15s：转一次并清零
            since_last = 0.0
    return flags


def _render_segments(
    plan: AssemblyPlan,
    segments_dir: Path,
    runner: Runner,
    transition_flags: Sequence[bool],
) -> list[Path]:
    """渲染每个切片为一段 mp4。

    时长补偿（内容时间轴守恒的关键）：后面紧接转场的段（transition_flags[i]=True）在渲染
    时把目标时长 +0.4s（_XFADE_DURATION）。因为 xfade 每次转场会重叠、吃掉相邻两段各
    0.4s；给"块尾段"多渲 0.4s，恰好补回这段被吃掉的内容，使每段的净贡献回到原始时长，
    成片时间轴与配音/分配计划守恒（推导见 _concat_with_xfade）。
    """
    segment_paths: list[Path] = []
    index = 0
    for allocation in plan.allocations:
        for clip_slice in allocation.slices:
            segment_path = segments_dir / f"segment_{index:02d}.mp4"
            extra = _XFADE_DURATION if transition_flags[index] else 0.0
            command = _build_segment_command(
                clip_slice, segment_path, plan.width, plan.height, plan.fps, plan.fit,
                extra_duration=extra,
            )
            _run(command, runner, context="切片渲染")
            segment_paths.append(segment_path)
            index += 1
    if not segment_paths:
        raise AssemblyError("没有生成任何片段，检查素材与分配结果。")
    return segment_paths


def _build_video_filter(width: int, height: int, fps: int, fit: str) -> str:
    """构造单入单出的 -vf 图，末尾统一接 fps。未知 fit 抛中文错误。"""
    if fit == "pad":
        geometry = (
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2"
        )
    elif fit == "crop":
        geometry = (
            f"scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height}"
        )
    elif fit == "blur":
        geometry = (
            f"split[bg][fg];"
            f"[bg]scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},boxblur=20:2[bg2];"
            f"[fg]scale={width}:{height}:force_original_aspect_ratio=decrease[fg2];"
            f"[bg2][fg2]overlay=(W-w)/2:(H-h)/2"
        )
    else:
        raise AssemblyError(f"未知的画幅填充模式：{fit}（可选：{'、'.join(FIT_MODES)}）。")
    return f"{geometry},fps={fps}"


# 图片运镜（Ken Burns）：八款动效，按 文件名+切片起点 哈希确定性选择（可复现、
# 同图不同切片动效不同）。zoom 幅度收在 1.10 内，大了会明显抖/晕。
_KENBURNS_ZOOM = 0.10
_KENBURNS_VARIANTS = 8
# 旋转漂移专用：最大摆角 ±3°；旋转前把画布放大到留 25% 余量，只裁中心，
# 旋转产生的黑三角落在裁切区之外——省得真填色，也不会露黑角。
_KENBURNS_ROTATE_DEG = 3.0
_KENBURNS_ROTATE_MARGIN = 1.25


def _kenburns_expr(variant: int, frames: int) -> str:
    """纯 zoompan 表达式（变体 5 旋转漂移需 rotate 滤镜，不走这里，见 _kenburns_video_filter）。"""
    center_x = "x='iw/2-(iw/zoom)/2'"
    center_y = "y='ih/2-(ih/zoom)/2'"
    if variant == 0:  # 缓推近
        return f"z='1+{_KENBURNS_ZOOM}*on/{frames}':{center_x}:{center_y}"
    if variant == 1:  # 缓拉远
        return f"z='{1 + _KENBURNS_ZOOM}-{_KENBURNS_ZOOM}*on/{frames}':{center_x}:{center_y}"
    if variant == 2:  # 缓下移（镜头下摇）
        return f"z={1 + _KENBURNS_ZOOM}:{center_x}:y='(ih-ih/zoom)*on/{frames}'"
    if variant == 3:  # 缓上移
        return f"z={1 + _KENBURNS_ZOOM}:{center_x}:y='(ih-ih/zoom)*(1-on/{frames})'"
    if variant == 4:  # 对角推近：zoom 与 x/y 同步变化，斜向运动（左上→右下）
        return (
            f"z='1+{_KENBURNS_ZOOM}*on/{frames}':"
            f"x='(iw-iw/zoom)*on/{frames}':y='(ih-ih/zoom)*on/{frames}'"
        )
    if variant == 6:  # 特写冲击：前 20% 冲到 1.25，再缓落到 1.15
        return _punch_in_expr(frames)
    # variant == 7 缓慢呼吸：zoom 按余弦单周期在 1.0~1.08 往返（cos 起于波谷=1.0 不会 <1）
    return f"z='1.04-0.04*cos(2*PI*on/{frames})':{center_x}:{center_y}"


def _punch_in_expr(frames: int) -> str:
    """特写冲击：前 20% 帧内 zoom 从 1.0 冲到 1.25，之后缓落到 1.15。
    用 if(lte(on,N20)) 分段——两段在 N20 处 zoom 均为 1.25，保证接缝连续无跳变。"""
    n20 = max(1, round(frames * 0.2))
    n_rest = max(1, frames - n20)
    center_x = "x='iw/2-(iw/zoom)/2'"
    center_y = "y='ih/2-(ih/zoom)/2'"
    zoom = f"if(lte(on,{n20}),1+0.25*on/{n20},1.25-0.10*(on-{n20})/{n_rest})"
    return f"z='{zoom}':{center_x}:{center_y}"


def _kenburns_video_filter(
    variant: int, frames: int, width: int, height: int, fps: int, geometry: str
) -> str:
    """图片运镜完整滤镜链：几何适配 → 2 倍超采样 → 运镜。
    变体 5（旋转漂移）需 rotate 滤镜（zoompan 无法旋转），走独立分支。"""
    if variant == 5:
        return _kenburns_rotate_filter(frames, width, height, fps, geometry)
    return (
        f"{geometry},scale={width * 2}:{height * 2},"
        f"zoompan={_kenburns_expr(variant, frames)}:d={frames}:s={width}x{height}:fps={fps}"
    )


def _kenburns_rotate_filter(
    frames: int, width: int, height: int, fps: int, geometry: str
) -> str:
    """旋转漂移：2 倍超采样并缓推 → 按 ±3° 正弦微旋转 → 中心裁回目标。
    zoompan 先把画面输出到留了 25% 余量的画布上，rotate 只在这块大画布里转，黑三角落
    在四角、被后面的中心裁切吃掉，因此裁完看不到黑角（fillcolor 仅作兜底）。rotate 用
    帧号 n 驱动角度做正弦往返摆动（单帧图片没有时间轴，须放在 zoompan 生成多帧之后）。"""
    margin_w = round(width * _KENBURNS_ROTATE_MARGIN)
    margin_h = round(height * _KENBURNS_ROTATE_MARGIN)
    # 缓推幅度取常规 zoom 的 6 成：与旋转叠加后运动量已足，太大会晕。
    push = (
        f"z='1+{_KENBURNS_ZOOM * 0.6:.3f}*on/{frames}':"
        f"x='iw/2-(iw/zoom)/2':y='ih/2-(ih/zoom)/2'"
    )
    angle = f"PI/180*{_KENBURNS_ROTATE_DEG}*sin(2*PI*n/{frames})"
    return (
        f"{geometry},scale={width * 2}:{height * 2},"
        f"zoompan={push}:d={frames}:s={margin_w}x{margin_h}:fps={fps},"
        f"rotate='{angle}':ow=iw:oh=ih:fillcolor=black@0,"
        f"crop={width}:{height}"
    )


def _build_image_segment_command(
    clip_slice: ClipSlice,
    output_path: Path,
    width: int,
    height: int,
    fps: int,
    fit: str,
    extra_duration: float = 0.0,
) -> list[str]:
    """图片素材 → 带运镜的动态片段：几何适配后先 2 倍超采样再 zoompan
    （zoompan 按整数像素取样，直接在目标分辨率上动会一格格抖，超采样显著减抖）。

    extra_duration>0（后接转场的段）时，运镜帧数按"补偿后时长"算：多渲这几帧供 xfade
    重叠吃掉，内容净时长仍回到原始 clip_slice.duration。"""
    frames = max(1, round((clip_slice.duration + extra_duration) * fps))
    variant = int(hashlib.sha1(
        f"{clip_slice.path.name}:{clip_slice.start}".encode("utf-8")
    ).hexdigest(), 16) % _KENBURNS_VARIANTS
    geometry = _build_video_filter(width, height, fps, fit)
    geometry = geometry.rsplit(",fps=", 1)[0]  # 去掉末尾 fps（zoompan 自己定帧率）
    video_filter = _kenburns_video_filter(variant, frames, width, height, fps, geometry)
    return [
        "ffmpeg",
        "-y",
        *_FF_QUIET,
        "-i",
        str(clip_slice.path),
        "-vf",
        video_filter,
        "-frames:v",
        str(frames),
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "18",
        str(output_path),
    ]


def _build_segment_command(
    clip_slice: ClipSlice,
    output_path: Path,
    width: int,
    height: int,
    fps: int,
    fit: str,
    extra_duration: float = 0.0,
) -> list[str]:
    """extra_duration>0（后接转场的段）时把渲染时长 +extra_duration：视频靠 -t 多切、
    图片靠帧数多渲，多出的部分供 xfade 重叠吃掉，内容净时长回到 clip_slice.duration。
    视频若已到素材尾部，ffmpeg 短给可接受（xfade 表现为转场稍早，不阻断）。"""
    if clip_slice.path.suffix.lower() in IMAGE_SUFFIXES:
        return _build_image_segment_command(
            clip_slice, output_path, width, height, fps, fit, extra_duration=extra_duration
        )
    video_filter = _build_video_filter(width, height, fps, fit)
    return [
        "ffmpeg",
        "-y",
        *_FF_QUIET,
        "-ss",
        _format_time(clip_slice.start),
        "-t",
        _format_time(clip_slice.duration + extra_duration),
        "-i",
        str(clip_slice.path),
        "-vf",
        video_filter,
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "18",
        str(output_path),
    ]


def _escape_concat_path(path: Path) -> str:
    # concat demuxer 的 file '...' 语法要求把路径里的单引号转义为 '\''，
    # 否则含单引号的 Windows 路径（如 O'Brien）会在引号处截断。
    return path.resolve().as_posix().replace("'", "'\\''")


# 片段转场：用 xfade 滤镜链做 0.4s 转场，类型按转场序号哈希确定性轮换。
# xfade 没有真"翻页"转场，wiperight/smoothup 是最接近翻页视觉的近似（故列进候选集）。
# _XFADE_DURATION 即"转场时长"（TRANSITION_DURATION）：每次转场两段重叠这么多秒。
_XFADE_DURATION = 0.4
_XFADE_TRANSITIONS = (
    "fade", "slideleft", "slideright", "circleopen", "wiperight", "smoothup",
)
# 转场密度（用户拍板）：早先"每两段都转"（52 片=51 次）既过密、又累计吃掉约 20s 视觉
# 内容致画面超前口播。改为：①章节（节/allocation）边界必转；②同节内部每满这么多秒转
# 一次。此常量即同节内部相邻两次转场的最小内容间隔（秒）。
INTRA_SECTION_TRANSITION_INTERVAL = 15.0


def _transition_type(index: int) -> str:
    """按转场序号哈希确定性挑选转场类型（可复现，且相邻转场不总是同一种）。"""
    pick = int(hashlib.sha1(f"xfade:{index}".encode("utf-8")).hexdigest(), 16)
    return _XFADE_TRANSITIONS[pick % len(_XFADE_TRANSITIONS)]


def _concat_segments(
    segment_paths: Sequence[Path],
    concat_path: Path,
    output_path: Path,
    fps: int,
    runner: Runner,
    segment_durations: Sequence[float] | None = None,
    transition_flags: Sequence[bool] | None = None,
) -> list[float]:
    """合并片段并返回转场时刻列表（成片时间轴，供特效层给转场点配 whoosh）。

    先按转场标志把"连续无转场的段"并成块（块内硬切、块间转场）。≥2 块且可 xfade 时走
    块级 xfade 转场；只有 1 块（无转场）或 xfade 失败时回退 concat demuxer 全量硬切
    （降级不阻断成片）。concat 清单始终写出：既作回退输入，也是可查产物。
    """
    concat_path.write_text(
        "".join(f"file '{_escape_concat_path(path)}'\n" for path in segment_paths),
        encoding="utf-8",
    )
    blocks = _plan_blocks(segment_paths, segment_durations, transition_flags)
    if blocks is not None and _blocks_can_xfade(blocks):
        try:
            return _concat_with_xfade(
                segment_paths, blocks, concat_path.parent, output_path, fps, runner
            )
        except AssemblyError:
            pass  # xfade 失败 → 落到下面的全量硬切回退，不阻断成片
    _concat_demux(concat_path, output_path, fps, runner)
    return []


def _plan_blocks(
    segment_paths: Sequence[Path],
    segment_durations: Sequence[float] | None,
    transition_flags: Sequence[bool] | None,
) -> list[tuple[int, int, float]] | None:
    """按转场标志把"连续无转场的段"并成块，返回 [(lo, hi, 内容时长 D_k), ...]。

    transition_flags[i]=True 表示第 i 段之后断开（该处转场），块在此收尾；下一块从 i+1 起。
    未提供 flags 时退化为"每段自成一块"（等价于每处都转，供直接单测/回退）。块内容时长
    D_k = 块内各段原始时长之和（不含补偿——补偿在渲染期已加到块尾段上，见 _render_segments）。
    段数 <2 或缺时长/标志信息 → 返回 None（不走 xfade）。
    """
    n = len(segment_paths)
    if n < 2 or not segment_durations or len(segment_durations) != n:
        return None
    if transition_flags is None:
        transition_flags = [True] * (n - 1) + [False]
    if len(transition_flags) != n:
        return None
    blocks: list[tuple[int, int, float]] = []
    lo = 0
    for i in range(n):
        if i == n - 1 or transition_flags[i]:  # 末段或该段后转场 → 块在此收尾
            content = round(sum(segment_durations[lo : i + 1]), 3)
            blocks.append((lo, i + 1, content))
            lo = i + 1
    return blocks


def _blocks_can_xfade(blocks: Sequence[tuple[int, int, float]]) -> bool:
    """块级 xfade 触发条件：≥2 块、每块内容 >0，且末块内容时长 ≥ 转场时长。

    末块是唯一"不做补偿"的 xfade 输入（其他块的块尾段渲染期已 +0.4s，实渲时长必 ≥0.4s）；
    末块过短会让末尾转场吃穿整块、offset 越界，故只需单独校验末块。
    """
    if len(blocks) < 2:
        return False
    if any(content <= 0 for _, _, content in blocks):
        return False
    return blocks[-1][2] >= _XFADE_DURATION


def _render_block(
    segment_paths: Sequence[Path],
    lo: int,
    hi: int,
    work_dir: Path,
    block_no: int,
    fps: int,
    runner: Runner,
) -> Path:
    """把块内 [lo, hi) 段合成为一个块文件供块间 xfade 用。

    单段块直接复用该段文件（免多余重编码）；多段块用现有 concat demuxer 硬切合并
    （块内本就是硬切语义）。块尾段在渲染期已带 +0.4s 补偿，故多段块实渲时长 = ΣD + 0.4。
    """
    if hi - lo == 1:
        return Path(segment_paths[lo])
    block_manifest = work_dir / f"block_{block_no:02d}.txt"
    block_manifest.write_text(
        "".join(
            f"file '{_escape_concat_path(Path(segment_paths[i]))}'\n" for i in range(lo, hi)
        ),
        encoding="utf-8",
    )
    block_path = work_dir / f"block_{block_no:02d}.mp4"
    _concat_demux(block_manifest, block_path, fps, runner)
    return block_path


def _concat_with_xfade(
    segment_paths: Sequence[Path],
    blocks: Sequence[tuple[int, int, float]],
    work_dir: Path,
    output_path: Path,
    fps: int,
    runner: Runner,
) -> list[float]:
    """块级 xfade 转场衔接，返回每个转场在成片时间轴上的起始时刻。

    时长守恒推导（错一点就画面超前/黑帧）：设块 k 原始内容时长为 D_k，共 M 块。每个"块尾
    段"（紧接一个转场的段）在渲染期已 +0.4s（_XFADE_DURATION，见 _render_segments），故
    块 k（除末块）实渲时长 = D_k + 0.4，末块 = D_{M-1}。xfade 每次重叠 0.4s 恰好吃掉这段
    补偿：设链结果处理到块 k 后的长度为 L_k，则
        L_0     = D_0 + 0.4
        L_k     = offset_{k-1} + 实渲(块k) = Σ_{j≤k} D_j + 0.4   (k < M-1)
        L_{M-1} = Σ_j D_j                                        (末块不补偿)
    末块长度即分配计划总时长 → 成片时间轴与配音/字幕/分配计划完全守恒。转场须在链尾前
    0.4s 处开始，即
        offset_k = L_k - 0.4 = Σ_{j≤k} D_j   （原始累计内容时长，不再减 i*0.4）
    该 offset 即转场在成片时间轴上的时刻，恰落在分配计划的节/块边界上，特效音自动归位；
    故直接把各 offset 收进 transition_points 返回。
    """
    block_paths = [
        _render_block(segment_paths, lo, hi, work_dir, block_no, fps, runner)
        for block_no, (lo, hi, _content) in enumerate(blocks)
    ]
    inputs: list[str] = []
    for path in block_paths:
        inputs += ["-i", str(path)]
    steps: list[str] = []
    transition_points: list[float] = []
    prev_label = "0:v"
    cumulative = blocks[0][2]  # D_0
    for i in range(1, len(block_paths)):
        offset = cumulative  # = Σ_{j<i} D_j：到前一块为止的累计内容时长 = 块边界时刻
        transition_points.append(round(offset, 3))
        out_label = "vout" if i == len(block_paths) - 1 else f"vx{i}"
        steps.append(
            f"[{prev_label}][{i}:v]xfade=transition={_transition_type(i)}:"
            f"duration={_XFADE_DURATION}:offset={offset:.3f}[{out_label}]"
        )
        prev_label = out_label
        cumulative += blocks[i][2]
    command = [
        "ffmpeg", "-y", *_FF_QUIET,
        *inputs,
        "-filter_complex", ";".join(steps),
        "-map", "[vout]",
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-r", str(fps),
        "-an",
        str(output_path),
    ]
    _run(command, runner, context="片段转场")
    return transition_points


def _concat_demux(concat_path: Path, output_path: Path, fps: int, runner: Runner) -> None:
    """concat demuxer 硬切合并（单段或 xfade 失败时的回退路径）。"""
    _run(
        [
            "ffmpeg",
            "-y",
            *_FF_QUIET,
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_path),
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "18",
            "-r",
            str(fps),
            "-an",
            str(output_path),
        ],
        runner,
        context="片段合并",
    )


def _finalize_silent(silent_video: Path, release_path: Path, runner: Runner) -> None:
    # 无音频：直接把合并结果拷成 release（copy 保持无损、无重编码）。
    _run(
        ["ffmpeg", "-y", *_FF_QUIET, "-i", str(silent_video), "-c", "copy", str(release_path)],
        runner,
        context="成片封装",
    )


def _mux_audio(
    silent_video: Path,
    audio_path: Path,
    release_path: Path,
    runner: Runner,
    audio_duration: float | None = None,
    bgm_path: Path | None = None,
    bgm_volume: float = DEFAULT_BGM_VOLUME,
    bgm_fade: float = DEFAULT_BGM_FADE,
) -> None:
    if not audio_path.exists():
        raise AssemblyError(f"配音文件不存在：{audio_path}")
    if bgm_path is not None and not bgm_path.exists():
        raise AssemblyError(f"BGM 文件不存在：{bgm_path}")
    if bgm_path is not None:
        command = _mux_with_bgm_command(
            silent_video, audio_path, bgm_path, release_path,
            audio_duration, bgm_volume, bgm_fade,
        )
    else:
        command = _mux_voiceover_command(silent_video, audio_path, release_path, audio_duration)
    _run(command, runner, context="音视频合成")


def _duration_cap_args(audio_duration: float | None) -> list[str]:
    # tpad 把视频补到 3600s，单靠 -shortest 截断在真实 ffmpeg 下会过冲（已实测，
    # 依编码器缓冲抖动 1~3s）。配音时长已知时用 -t 强制精确到音频时长，保证对齐。
    if audio_duration is None or audio_duration <= 0:
        return []
    return ["-t", f"{audio_duration:.3f}"]


def _mux_voiceover_command(
    silent_video: Path,
    audio_path: Path,
    release_path: Path,
    audio_duration: float | None = None,
) -> list[str]:
    # 以音频为主轴：视频末段用 tpad 定格补帧到足够长，-t 截到配音时长（无则退回 -shortest）。
    # 这样音频比视频长时不会黑屏，音频短时视频被裁到音频长度，最终以音频时长为准。
    return [
        "ffmpeg",
        "-y",
        *_FF_QUIET,
        "-i",
        str(silent_video),
        "-i",
        str(audio_path),
        "-filter_complex",
        "[0:v]tpad=stop_mode=clone:stop_duration=3600[vpad]",
        "-map",
        "[vpad]",
        "-map",
        "1:a",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "18",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        *_duration_cap_args(audio_duration),
        "-shortest",
        str(release_path),
    ]


def _bgm_fadeout_clause(audio_duration: float | None, fade: float) -> str:
    # 仅当配音时长已知且长于 2*fade 时才拼淡出，否则会算出负 st（绝不能出现）。
    if audio_duration is None or audio_duration <= 0 or audio_duration <= 2 * fade:
        return ""
    return f",afade=t=out:st={audio_duration - fade}:d={fade}"


def _build_ducking_filter(
    audio_duration: float | None, bgm_volume: float, bgm_fade: float
) -> str:
    # 三输入滤镜链：配音 asplit 出一路给 sidechain 当触发轨；BGM 统一采样率/声道后调音量+淡入淡出，
    # 与配音一起过 sidechaincompress 自动压低，再 amix（normalize=0 保配音不被减半）。
    fadeout = _bgm_fadeout_clause(audio_duration, bgm_fade)
    return (
        "[0:v]tpad=stop_mode=clone:stop_duration=3600[vpad];"
        "[1:a]aformat=sample_rates=48000:channel_layouts=stereo,asplit=2[vo][sc];"
        f"[2:a]aformat=sample_rates=48000:channel_layouts=stereo,volume={bgm_volume},"
        f"afade=t=in:st=0:d={bgm_fade}{fadeout}[bgmv];"
        f"[bgmv][sc]sidechaincompress=threshold={BGM_DUCK_THRESHOLD}:ratio={BGM_DUCK_RATIO}:"
        f"attack={BGM_DUCK_ATTACK_MS}:release={BGM_DUCK_RELEASE_MS}[duck];"
        "[vo][duck]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[aout]"
    )


def _mux_with_bgm_command(
    silent_video: Path,
    audio_path: Path,
    bgm_path: Path,
    release_path: Path,
    audio_duration: float | None,
    bgm_volume: float,
    bgm_fade: float,
) -> list[str]:
    # BGM 输入用 -stream_loop -1 在 -i 前无缝循环补足全片；-shortest 让成片对齐配音时长。
    return [
        "ffmpeg",
        "-y",
        *_FF_QUIET,
        "-i",
        str(silent_video),
        "-i",
        str(audio_path),
        "-stream_loop",
        "-1",
        "-i",
        str(bgm_path),
        "-filter_complex",
        _build_ducking_filter(audio_duration, bgm_volume, bgm_fade),
        "-map",
        "[vpad]",
        "-map",
        "[aout]",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "18",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        *_duration_cap_args(audio_duration),
        "-shortest",
        str(release_path),
    ]


def _probe_duration(path: Path, runner: Runner) -> float:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-print_format",
        "json",
        str(path),
    ]
    try:
        completed = runner(
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError:
        return 0.0
    if getattr(completed, "returncode", 0) != 0:
        return 0.0
    try:
        payload = json.loads(completed.stdout or "{}")
        return round(float((payload.get("format") or {}).get("duration") or 0.0), 3)
    except (json.JSONDecodeError, TypeError, ValueError):
        return 0.0


def _aspect_name(width: int, height: int) -> str:
    """从 (宽, 高) 反查画幅比例名，查不到写 "custom"。"""
    for name, (preset_w, preset_h) in ASPECT_PRESETS.items():
        if (preset_w, preset_h) == (width, height):
            return name
    return "custom"


def _write_plan_json(
    plan: AssemblyPlan,
    plan_path: Path,
    audio_path: Path | None,
    audio_duration: float | None,
    final_duration: float,
    bgm_path: Path | None = None,
    bgm_volume: float = DEFAULT_BGM_VOLUME,
    bgm_fade: float = DEFAULT_BGM_FADE,
    transition_points: Sequence[float] | None = None,
) -> None:
    aligned = True
    if audio_duration is not None and audio_duration > 0:
        aligned = abs(final_duration - audio_duration) <= DURATION_TOLERANCE
    payload = {
        "version": "assembly_v1",
        "target_duration_seconds": plan.target_duration,
        "fps": plan.fps,
        "width": plan.width,
        "height": plan.height,
        "aspect": _aspect_name(plan.width, plan.height),
        "fit": plan.fit,
        # 转场时刻列表（成片时间轴，秒）：特效层据此给每个转场点混入 whoosh。空=未走 xfade。
        "transition_points": list(transition_points or []),
        "audio_path": str(audio_path) if audio_path else "",
        "audio_duration_seconds": audio_duration,
        "bgm_path": str(bgm_path) if bgm_path else "",
        "bgm_volume": bgm_volume,
        "bgm_fade": bgm_fade,
        "final_duration_seconds": final_duration,
        "duration_aligned": aligned,
        "duration_tolerance_seconds": DURATION_TOLERANCE,
        "clips": [
            {"path": str(clip.path), "duration": clip.duration, "width": clip.width, "height": clip.height}
            for clip in plan.clips
        ],
        "sections": [
            {
                "index": allocation.index,
                "title": plan.section_titles[allocation.index]
                if allocation.index < len(plan.section_titles)
                else "",
                "duration_seconds": allocation.duration,
                "slices": [asdict(clip_slice) | {"path": str(clip_slice.path)} for clip_slice in allocation.slices],
            }
            for allocation in plan.allocations
        ],
    }
    plan_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _tail_stderr(text: str, max_lines: int = 12, max_chars: int = 300) -> str:
    """取 ffmpeg stderr 的末尾若干非空行：真正的失败原因在结尾，取开头只会拿到
    进度/横幅噪声。配合命令里的 -hide_banner -loglevel error，能让报错直指根因。
    再对末尾内容截断到 max_chars，避免个别无换行的超长行把错误信息撑爆。"""
    lines = [ln for ln in (text or "").splitlines() if ln.strip()]
    if not lines:
        return ""
    return "\n".join(lines[-max_lines:])[-max_chars:]


def _run(command: list[str], runner: Runner, context: str) -> None:
    try:
        completed = runner(
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as exc:
        raise AssemblyError(f"{context}失败：无法启动 ffmpeg（{exc}）。") from exc
    if getattr(completed, "returncode", 0) != 0:
        stderr = _tail_stderr(getattr(completed, "stderr", "") or "")
        raise AssemblyError(f"{context}失败（ffmpeg 退出码 {completed.returncode}）：{stderr}")


def _format_time(seconds: float) -> str:
    if seconds == int(seconds):
        return str(int(seconds))
    return f"{seconds:.3f}".rstrip("0").rstrip(".")


def _load_rewrite(path: Path) -> dict:
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise AssemblyError(f"rewrite.json 无法解析：{path}") from exc
    if not isinstance(body, dict):
        raise AssemblyError(f"rewrite.json 内容不是 JSON 对象：{path}")
    return body


def _synthesize_from_rewrite(
    rewrite: dict,
    provider: str,
    voice: str,
    output_dir: Path,
    runner: Runner,
    speed: float | None = None,
) -> Path:
    narration = str(rewrite.get("full_voiceover") or "").strip()
    if not narration:
        raise AssemblyError("rewrite.json 缺少 full_voiceover，无法现场合成配音。")
    output_dir.mkdir(parents=True, exist_ok=True)
    voiceover_path = output_dir / "voiceover.wav"
    config_kwargs: dict = {"provider": provider, "speed": speed}
    if voice:
        config_kwargs["voice"] = voice
    result = synthesize_voiceover_text(narration, voiceover_path, TTSConfig(**config_kwargs))
    return result.path


# 时长闭环第二级（末级微调）：atempo 变速不变调。触发容差必须小于钳位区间
# （±5% 内不动，5%~10% 精确修到目标，>10% 钳到 0.9/1.1 并提示残差）——若容差=钳位
# （都 10%）则"超容差必被钳位"，精确微调分支永远走不到。0.9~1.1 之外人耳能明显
# 听出拖沓/赶稿，更大的缺口应由 rewrite 的字数闭环（第一级）去补，不在这里硬拉。
_ATEMPO_TOLERANCE = 0.05
_ATEMPO_MIN = 0.9
_ATEMPO_MAX = 1.1


def _fit_audio_to_target(
    audio_path: Path,
    target_seconds: float,
    output_dir: Path,
    runner: Runner,
) -> tuple[Path, str | None]:
    """配音实测时长偏离目标 >5% 时，用 atempo（变速不变调）在 0.9~1.1 内向目标收敛。

    只作用于现场 TTS 合成的配音（用户自带 --audio 不动，变速会毁人家的成品）。
    返回 (最终音频路径, 说明文字或 None)；任何探测/转码失败都放行原音频，不阻断成片。
    """
    if not target_seconds or target_seconds <= 0:
        return audio_path, None
    actual = _probe_duration(audio_path, runner)
    if actual <= 0:
        return audio_path, None
    deviation = actual / target_seconds - 1.0
    if abs(deviation) <= _ATEMPO_TOLERANCE:
        return audio_path, None
    # atempo>1 加速变短、<1 放慢变长：ratio=实际/目标 恰好是把实际拉到目标所需的倍速。
    tempo = max(_ATEMPO_MIN, min(_ATEMPO_MAX, actual / target_seconds))
    fitted = output_dir / "voiceover_fitted.wav"
    command = ["ffmpeg", "-y", *_FF_QUIET, "-i", str(audio_path), "-af", f"atempo={tempo:.4f}", str(fitted)]
    try:
        completed = runner(command, check=False, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
    except OSError:
        return audio_path, None
    if getattr(completed, "returncode", 0) != 0 or not fitted.exists():
        return audio_path, None
    note = (
        f"配音实测 {actual:.1f}s 偏离目标 {target_seconds:.0f}s 超 10%，"
        f"已按 atempo={tempo:.3f} 变速微调至约 {actual / tempo:.1f}s"
    )
    if tempo in (_ATEMPO_MIN, _ATEMPO_MAX):
        note += "（已到变速安全上限，残差需靠文案字数解决）"
    return fitted, note


def _orientation_warnings(
    clips: Sequence[AssetClip], width: int, height: int, fit: str
) -> list[str]:
    """素材朝向与目标画幅不一致且 fit=pad 时提示改用 --fit blur，不动分配算法。"""
    if fit != "pad":
        return []
    target_landscape = width >= height
    warnings: list[str] = []
    for clip in clips:
        if clip.width <= 0 or clip.height <= 0:
            continue
        if (clip.width >= clip.height) != target_landscape:
            warnings.append(
                f"素材 {clip.path} 朝向与目标画幅（{width}x{height}）不匹配，"
                f"pad 模式会留大片黑边，建议改用 --fit blur。"
            )
    return warnings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m video_factory.assemble",
        description="素材池拼装 + 精确时长控制（输出 release.mp4 + assembly_plan.json）",
    )
    parser.add_argument("--rewrite", required=True, help="rewrite.json 路径（P1 产出）")
    parser.add_argument("--assets", required=True, help="素材目录（若干视频文件）")
    parser.add_argument("--duration", type=float, default=None, help="目标秒数，覆盖 rewrite 里的目标时长")
    audio_group = parser.add_mutually_exclusive_group()
    audio_group.add_argument("--audio", default="", help="已有配音文件（与 --tts 互斥）")
    audio_group.add_argument(
        "--tts",
        default="",
        choices=["doubao", "openai", "edge"],
        help="从 rewrite 的 full_voiceover 现场合成配音（与 --audio 互斥）",
    )
    parser.add_argument("--voice", default="", help="TTS 音色（可选）")
    parser.add_argument(
        "--voice-speed",
        dest="voice_speed",
        type=float,
        default=None,
        help="配音语速 0.5~2.0（1.0=原速；仅 --tts 现场合成生效）。显式设语速后跳过 atempo "
        "时长微调，分镜/切屏改以实测配音时长为轴——节奏完全跟随语速",
    )
    parser.add_argument(
        "--aspect",
        default="16:9",
        choices=list(ASPECT_PRESETS.keys()),
        help="目标画幅比例（默认 16:9）",
    )
    parser.add_argument(
        "--fit",
        default="pad",
        choices=list(FIT_MODES),
        help="素材与画幅不匹配时的填充模式（默认 pad）",
    )
    parser.add_argument("--bgm", default="", help="背景音乐文件（默认关；需配合配音才生效）")
    parser.add_argument(
        "--bgm-volume",
        type=float,
        default=DEFAULT_BGM_VOLUME,
        help="BGM 音量（默认 0.2，建议 0.1-0.3）",
    )
    parser.add_argument(
        "--bgm-fade",
        type=float,
        default=DEFAULT_BGM_FADE,
        help="BGM 淡入淡出秒数（默认 2.0）",
    )
    parser.add_argument("--output", default="video_factory/output/assemble", help="输出目录")
    parser.add_argument(
        "--ordered-assets",
        dest="ordered_assets",
        action="store_true",
        default=False,
        help="拍级配图模式：按 gen_assets/img_NN 顺序逐拍分配（ai_image 专用，忽略素材池扫描）",
    )
    args = parser.parse_args(argv)
    # 补齐凭据（TTS 的 VOLC_* 等）：credentials.yaml → 空缺的环境变量（真实 env 优先）。
    credentials_store.ensure_env_loaded()

    output_dir = Path(args.output)
    try:
        rewrite = _load_rewrite(Path(args.rewrite))
        # 先校验 --audio 路径，失败快返回，避免白跑素材扫描。
        audio_path: Path | None = None
        if args.audio:
            audio_path = Path(args.audio)
            if not audio_path.exists():
                raise AssemblyError(f"配音文件不存在：{audio_path}")
        # BGM 同样快返回：文件缺失、或没有配音基准（--audio/--tts）时提前报错。
        bgm_path: Path | None = None
        if args.bgm:
            bgm_path = Path(args.bgm)
            if not bgm_path.exists():
                raise AssemblyError(f"BGM 文件不存在：{bgm_path}")
            if not (args.audio or args.tts):
                raise AssemblyError("使用 BGM 需要先提供配音（--audio 或 --tts），否则没有 ducking 基准。")
        plan_duration = args.duration
        if not audio_path and args.tts:
            audio_path = _synthesize_from_rewrite(
                rewrite, args.tts, args.voice, output_dir, subprocess.run,
                speed=args.voice_speed,
            )
            if args.voice_speed is not None:
                # 用户显式调语速：atempo 微调让位（它会把节奏拉回目标时长、抵消语速意图）；
                # 分配计划改以实测配音时长为轴——解说/切屏/分镜完全跟随语速。
                actual = _probe_duration(Path(audio_path), subprocess.run)
                if actual > 0:
                    plan_duration = actual
            else:
                # 时长闭环第二级：现场合成的配音偏离目标 >5% 时 atempo 微调（0.9~1.1）。
                fit_target = _resolve_target_duration(rewrite, args.duration)
                audio_path, fit_note = _fit_audio_to_target(
                    Path(audio_path), fit_target, output_dir, subprocess.run
                )
                if fit_note:
                    print(f"时长微调：{fit_note}")
        width, height = ASPECT_PRESETS[args.aspect]
        if args.ordered_assets:
            # 拍级配图模式：按 gen_assets/img_NN 顺序逐拍分配，跳过素材池扫描。
            image_paths = _list_ordered_assets(Path(args.assets))
            plan = build_ordered_assembly_plan(
                rewrite, image_paths, plan_duration, width=width, height=height, fit=args.fit
            )
        else:
            scan = scan_asset_pool(args.assets)
            for warning in scan.warnings:
                print(f"素材告警：{warning}")
            for warning in _orientation_warnings(scan.clips, width, height, args.fit):
                print(f"画幅告警：{warning}")
            plan = build_assembly_plan(
                rewrite, scan.clips, plan_duration, width=width, height=height, fit=args.fit
            )
        render_kwargs = {"audio_path": audio_path}
        if bgm_path is not None:
            render_kwargs["bgm_path"] = bgm_path
            render_kwargs["bgm_volume"] = args.bgm_volume
            render_kwargs["bgm_fade"] = args.bgm_fade
        outputs = render_assembly(plan, output_dir, **render_kwargs)
    except (AssetPoolError, AssemblyError, TTSProviderError, OSError) as exc:
        print(f"拼装失败：{exc}")
        stage_report.write_stage_error(args.output, "assemble", f"拼装失败：{exc}")
        return 1

    print(f"拼装完成：目标时长 {plan.target_duration:.1f}s，共 {len(plan.allocations)} 节")
    print(f"- 成片:     {outputs['release']}")
    print(f"- 分配明细: {outputs['assembly_plan']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
