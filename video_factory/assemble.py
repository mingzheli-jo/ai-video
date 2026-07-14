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
from pathlib import Path
from typing import Callable, Sequence

from video_factory.asset_pool import (
    IMAGE_SUFFIXES,
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

    segment_paths = _render_segments(plan, segments_dir, runner)
    concat_path = output_dir / "segments.txt"
    silent_video = output_dir / "assembly_silent.mp4"
    _concat_segments(segment_paths, concat_path, silent_video, plan.fps, runner)

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
    )
    return {
        "release": release_path,
        "assembly_plan": plan_path,
        "concat": concat_path,
        "silent_video": silent_video,
    }


def _render_segments(plan: AssemblyPlan, segments_dir: Path, runner: Runner) -> list[Path]:
    segment_paths: list[Path] = []
    index = 0
    for allocation in plan.allocations:
        for clip_slice in allocation.slices:
            segment_path = segments_dir / f"segment_{index:02d}.mp4"
            command = _build_segment_command(
                clip_slice, segment_path, plan.width, plan.height, plan.fps, plan.fit
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


# 图片运镜（Ken Burns）：四款动效，按 文件名+切片起点 哈希确定性选择（可复现、
# 同图不同切片动效不同）。zoom 幅度收在 1.10 内，大了会明显抖/晕。
_KENBURNS_ZOOM = 0.10


def _kenburns_expr(variant: int, frames: int) -> str:
    center_x = "x='iw/2-(iw/zoom)/2'"
    center_y = "y='ih/2-(ih/zoom)/2'"
    if variant == 0:  # 缓推近
        return f"z='1+{_KENBURNS_ZOOM}*on/{frames}':{center_x}:{center_y}"
    if variant == 1:  # 缓拉远
        return f"z='{1 + _KENBURNS_ZOOM}-{_KENBURNS_ZOOM}*on/{frames}':{center_x}:{center_y}"
    if variant == 2:  # 缓下移（镜头下摇）
        return f"z={1 + _KENBURNS_ZOOM}:{center_x}:y='(ih-ih/zoom)*on/{frames}'"
    return f"z={1 + _KENBURNS_ZOOM}:{center_x}:y='(ih-ih/zoom)*(1-on/{frames})'"  # 缓上移


def _build_image_segment_command(
    clip_slice: ClipSlice,
    output_path: Path,
    width: int,
    height: int,
    fps: int,
    fit: str,
) -> list[str]:
    """图片素材 → 带运镜的动态片段：几何适配后先 2 倍超采样再 zoompan
    （zoompan 按整数像素取样，直接在目标分辨率上动会一格格抖，超采样显著减抖）。"""
    frames = max(1, round(clip_slice.duration * fps))
    variant = int(hashlib.sha1(
        f"{clip_slice.path.name}:{clip_slice.start}".encode("utf-8")
    ).hexdigest(), 16) % 4
    geometry = _build_video_filter(width, height, fps, fit)
    geometry = geometry.rsplit(",fps=", 1)[0]  # 去掉末尾 fps（zoompan 自己定帧率）
    video_filter = (
        f"{geometry},scale={width * 2}:{height * 2},"
        f"zoompan={_kenburns_expr(variant, frames)}:d={frames}:s={width}x{height}:fps={fps}"
    )
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
) -> list[str]:
    if clip_slice.path.suffix.lower() in IMAGE_SUFFIXES:
        return _build_image_segment_command(clip_slice, output_path, width, height, fps, fit)
    video_filter = _build_video_filter(width, height, fps, fit)
    return [
        "ffmpeg",
        "-y",
        *_FF_QUIET,
        "-ss",
        _format_time(clip_slice.start),
        "-t",
        _format_time(clip_slice.duration),
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


def _concat_segments(
    segment_paths: Sequence[Path],
    concat_path: Path,
    output_path: Path,
    fps: int,
    runner: Runner,
) -> None:
    concat_path.write_text(
        "".join(f"file '{_escape_concat_path(path)}'\n" for path in segment_paths),
        encoding="utf-8",
    )
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
) -> Path:
    narration = str(rewrite.get("full_voiceover") or "").strip()
    if not narration:
        raise AssemblyError("rewrite.json 缺少 full_voiceover，无法现场合成配音。")
    output_dir.mkdir(parents=True, exist_ok=True)
    voiceover_path = output_dir / "voiceover.wav"
    config = TTSConfig(provider=provider, voice=voice) if voice else TTSConfig(provider=provider)
    result = synthesize_voiceover_text(narration, voiceover_path, config)
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
    args = parser.parse_args(argv)

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
        scan = scan_asset_pool(args.assets)
        for warning in scan.warnings:
            print(f"素材告警：{warning}")
        if not audio_path and args.tts:
            audio_path = _synthesize_from_rewrite(
                rewrite, args.tts, args.voice, output_dir, subprocess.run
            )
            # 时长闭环第二级：现场合成的配音偏离目标 >10% 时 atempo 微调（0.9~1.1）。
            fit_target = _resolve_target_duration(rewrite, args.duration)
            audio_path, fit_note = _fit_audio_to_target(
                Path(audio_path), fit_target, output_dir, subprocess.run
            )
            if fit_note:
                print(f"时长微调：{fit_note}")
        width, height = ASPECT_PRESETS[args.aspect]
        for warning in _orientation_warnings(scan.clips, width, height, args.fit):
            print(f"画幅告警：{warning}")
        plan = build_assembly_plan(
            rewrite, scan.clips, args.duration, width=width, height=height, fit=args.fit
        )
        render_kwargs = {"audio_path": audio_path}
        if bgm_path is not None:
            render_kwargs["bgm_path"] = bgm_path
            render_kwargs["bgm_volume"] = args.bgm_volume
            render_kwargs["bgm_fade"] = args.bgm_fade
        outputs = render_assembly(plan, output_dir, **render_kwargs)
    except (AssetPoolError, AssemblyError, TTSProviderError, OSError) as exc:
        print(f"拼装失败：{exc}")
        return 1

    print(f"拼装完成：目标时长 {plan.target_duration:.1f}s，共 {len(plan.allocations)} 节")
    print(f"- 成片:     {outputs['release']}")
    print(f"- 分配明细: {outputs['assembly_plan']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
