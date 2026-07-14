"""Remotion 特效层桥接（P3）。

流程定位：吃 P2 的 assembly_plan.json（含每节 title/duration），可选叠加 rewrite.json
的 hook / publish_titles，派生一份 effects_manifest_v1，交给 remotion/ 子工程逐条渲染成
带 alpha 的 ProRes 4444 片段（effect_XX.mov），再用 ffmpeg overlay 合成进原片。

特效层是**可选**旁挂子系统：npx 缺失或单条渲染失败都不阻断成片——找不到 npx 时写
effects_skipped.json 说明原因并返回空列表，单条失败收集告警继续。
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from video_factory.sfx import (
    DEFAULT_SFX_VOLUME,
    SfxError,
    ensure_default_pack,
    resolve_sfx_path,
)

Runner = Callable[..., subprocess.CompletedProcess]

DEFAULT_FPS = 30
DEFAULT_WIDTH = 1920
DEFAULT_HEIGHT = 1080
DEFAULT_ACCENT = "#e8b84b"

# 派生规则常量（见设计文档 P3 更新）。
INTRO_MAX_DURATION = 2.5
INTRO_FIRST_SECTION_RATIO = 0.8
INTRO_TITLE_MAX_CHARS = 12
CHAPTER_CARD_DURATION = 1.5
LOWER_THIRD_OFFSET = 1.0
LOWER_THIRD_MAX_DURATION = 4.0

# manifest.type -> remotion composition id
_COMPOSITION_BY_TYPE = {
    "intro": "Intro",
    "chapter_card": "ChapterCard",
    "lower_third": "LowerThird",
    "key_points": "KeyPoints",
    "quote_card": "QuoteCard",
    "number_pop": "NumberPop",
}

# 开屏要点卡：片头结束后逐行浮现各节标题。
KEY_POINTS_MAX_LINES = 4
KEY_POINTS_BASE_SECONDS = 1.2
KEY_POINTS_PER_LINE_SECONDS = 0.8
KEY_POINTS_MAX_SECONDS = 3.5
# 金句卡：放在约 55% 进度处；与章节卡时间窗撞车时顺延到该卡结束之后。
QUOTE_POSITION_RATIO = 0.55
QUOTE_DURATION = 2.8
# 数字强调：每节口播里第一个关键数字，节起点后 0.8s 弹出，全片最多 2 个。
NUMBER_POP_OFFSET = 1.0
NUMBER_POP_DURATION = 1.4
NUMBER_POP_MAX = 2
_NUMBER_RE = re.compile(
    r"\d+(?:\.\d+)?%|\d+(?:\.\d+)?(?:步|倍|年|个|条|招|天|分钟|小时|万|亿)"
)


class EffectsError(RuntimeError):
    pass


@dataclass(frozen=True)
class EffectSpec:
    """单条特效在时间轴上的落点与文案（帧对齐后的秒值）。"""

    type: str
    start: float
    duration: float
    props: dict


def build_effects_manifest(
    assembly_plan: dict,
    rewrite: dict | None = None,
    fps: int = DEFAULT_FPS,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    accent: str = DEFAULT_ACCENT,
    include_lower_thirds: bool = False,
) -> dict:
    """从 assembly_plan.json（可选叠加 rewrite）派生 effects_manifest_v1。

    纯函数，不触碰文件系统。时间一律取整到帧（round(t*fps)/fps），避免 overlay 时间轴漂移。
    """
    sections = _sections_from_plan(assembly_plan)
    if not sections:
        raise EffectsError("assembly_plan 里没有可用的分节（sections 为空），无法派生特效清单。")

    starts = _section_starts(sections)
    effects: list[EffectSpec] = []

    effects.append(_build_intro(sections[0], rewrite, fps, accent))
    for i, section in enumerate(sections):
        if i == 0:
            continue  # 首节不出章节卡（片头已覆盖开场）。
        start = starts[i]
        title = _section_title(section, i)
        effects.append(
            _frame_aligned(
                EffectSpec(
                    type="chapter_card",
                    start=start,
                    duration=CHAPTER_CARD_DURATION,
                    props={"index": i, "title": title, "accent": accent},
                ),
                fps,
            )
        )
        if include_lower_thirds:
            lt = _build_lower_third(section, i, start, accent)
            if lt is not None:
                effects.append(_frame_aligned(lt, fps))

    total_duration = sum(_section_duration(s) for s in sections)
    effects.extend(
        _build_rich_effects(sections, rewrite, effects[0].duration, total_duration, fps, accent)
    )

    return {
        "version": "effects_manifest_v1",
        "fps": fps,
        "width": width,
        "height": height,
        "accent": accent,
        "effects": [
            {"type": e.type, "start": e.start, "duration": e.duration, **e.props}
            for e in effects
        ],
    }


def _sections_from_plan(assembly_plan: dict) -> list[dict]:
    raw = assembly_plan.get("sections")
    if not isinstance(raw, list):
        return []
    sections: list[dict] = []
    for item in raw:
        if isinstance(item, dict) and _section_duration(item) > 0:
            sections.append(item)
    return sections


def _section_duration(section: dict) -> float:
    try:
        return float(section.get("duration_seconds") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _section_starts(sections: list[dict]) -> list[float]:
    starts: list[float] = []
    cursor = 0.0
    for section in sections:
        starts.append(cursor)
        cursor += _section_duration(section)
    return starts


def _section_title(section: dict, index: int) -> str:
    return str(section.get("title") or f"第{index + 1}节").strip()


def _build_intro(
    first_section: dict, rewrite: dict | None, fps: int, accent: str
) -> EffectSpec:
    first_duration = _section_duration(first_section)
    duration = min(INTRO_MAX_DURATION, first_duration * INTRO_FIRST_SECTION_RATIO)
    if duration <= 0:
        duration = INTRO_MAX_DURATION
    title = _intro_title(rewrite)
    return _frame_aligned(
        EffectSpec(
            type="intro",
            start=0.0,
            duration=duration,
            props={"title": title, "subtitle": "", "accent": accent},
        ),
        fps,
    )


def _intro_title(rewrite: dict | None) -> str:
    if isinstance(rewrite, dict):
        hook = str(rewrite.get("hook") or "").strip()
        if hook:
            return hook[:INTRO_TITLE_MAX_CHARS]
        titles = rewrite.get("publish_titles")
        if isinstance(titles, list):
            for title in titles:
                text = str(title or "").strip()
                if text:
                    return text
    return "开场"


def _build_lower_third(
    section: dict, index: int, section_start: float, accent: str
) -> EffectSpec | None:
    duration = min(LOWER_THIRD_MAX_DURATION, _section_duration(section) - 1.0)
    if duration <= 0:
        return None
    return EffectSpec(
        type="lower_third",
        start=section_start + LOWER_THIRD_OFFSET,
        duration=duration,
        props={"text": _section_title(section, index), "accent": accent},
    )


def _build_rich_effects(
    sections: list[dict],
    rewrite: dict | None,
    intro_duration: float,
    total_duration: float,
    fps: int,
    accent: str,
) -> list[EffectSpec]:
    """三个"丰富化"特效的派生（2026-07-14 用户点名新增，默认全开）：
    开屏要点卡（片头结束后逐行浮现各节标题）、金句卡（55% 进度、避开章节卡窗口）、
    数字强调（每节口播首个关键数字，全片最多 2 个）。"""
    rich: list[EffectSpec] = []

    # 开屏要点卡：至少 2 节才有"要点列表"的意义。第 0 节是开场 hook（片头已覆盖，
    # 且拼装计划里它的字面标题就是 "hook"），要点行从第 1 节的正题标题取起。
    if len(sections) >= 2:
        lines = [
            _section_title(s, i) for i, s in enumerate(sections) if i >= 1
        ][:KEY_POINTS_MAX_LINES]
        duration = min(
            KEY_POINTS_MAX_SECONDS,
            KEY_POINTS_BASE_SECONDS + KEY_POINTS_PER_LINE_SECONDS * len(lines),
        )
        rich.append(_frame_aligned(
            EffectSpec(
                type="key_points",
                start=intro_duration,
                duration=duration,
                props={"lines": lines, "accent": accent},
            ),
            fps,
        ))

    # 金句卡：取发布标题候选1（通常最凝练）兜底 hook；撞上章节卡窗口就顺延。
    quote_text = ""
    if isinstance(rewrite, dict):
        titles = rewrite.get("publish_titles")
        if isinstance(titles, list) and titles and str(titles[0] or "").strip():
            quote_text = str(titles[0]).strip()
        else:
            quote_text = str(rewrite.get("hook") or "").strip()
    if quote_text and total_duration > QUOTE_DURATION * 2:
        start = total_duration * QUOTE_POSITION_RATIO
        starts = _section_starts(sections)
        for i in range(1, len(sections)):  # 章节卡窗口 = [节起点, +CHAPTER_CARD_DURATION]
            card_start = starts[i]
            if card_start - QUOTE_DURATION < start < card_start + CHAPTER_CARD_DURATION:
                start = card_start + CHAPTER_CARD_DURATION + 0.2
                break
        if start + QUOTE_DURATION < total_duration:
            rich.append(_frame_aligned(
                EffectSpec(
                    type="quote_card",
                    start=start,
                    duration=QUOTE_DURATION,
                    props={"text": quote_text, "accent": accent},
                ),
                fps,
            ))

    # 数字强调：跳过第 0 节（片头+要点卡已经很满），每节最多 1 个、全片最多 2 个。
    # 口播文本在 rewrite 里（拼装计划的节只有 title/duration）：计划第 i 节（i>=1）
    # 对应 rewrite 第 i-1 节（计划把 hook 计为第 0 节）。
    rewrite_sections = (rewrite or {}).get("sections") or []
    starts = _section_starts(sections)
    count = 0
    for i, section in enumerate(sections):
        if count >= NUMBER_POP_MAX:
            break
        if i == 0:
            continue
        narration = str(section.get("narration") or "")
        if not narration and i - 1 < len(rewrite_sections) and isinstance(rewrite_sections[i - 1], dict):
            narration = str(rewrite_sections[i - 1].get("narration") or "")
        match = _NUMBER_RE.search(narration)
        if not match:
            continue
        rich.append(_frame_aligned(
            EffectSpec(
                type="number_pop",
                start=starts[i] + NUMBER_POP_OFFSET,
                duration=NUMBER_POP_DURATION,
                props={"value": match.group(0), "accent": accent},
            ),
            fps,
        ))
        count += 1
    return rich


def _frame_aligned(spec: EffectSpec, fps: int) -> EffectSpec:
    return EffectSpec(
        type=spec.type,
        start=round(spec.start * fps) / fps,
        duration=round(spec.duration * fps) / fps,
        props=spec.props,
    )


def render_effects(
    manifest: dict,
    output_dir: Path | str,
    runner: Runner = subprocess.run,
    remotion_dir: Path | str | None = None,
) -> list[tuple[int, Path]]:
    """写 manifest JSON 并逐条调 remotion 渲染成 effect_XX.mov（ProRes 4444 带 alpha）。

    npx 缺失时返回 [] 并在 manifest 旁写 effects_skipped.json（不抛错）。单条渲染失败收集
    告警继续。返回 (manifest 内原始索引, 片段路径) 列表——必须带索引，调用方才能在
    部分失败时仍把片段对回自己的 start/duration，避免时间轴错位。
    """
    # 解析成绝对路径：npx remotion render 以 remotion/ 为工作目录运行，
    # 若传相对输出路径，.mov 会被写到 remotion/ 下的错误位置，overlay 便找不到。
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "effects_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    npx = shutil.which("npx")
    if not npx:
        _write_skipped(output_dir, "未找到 npx，跳过 Remotion 特效渲染（特效层可选）。")
        return []

    project = Path(remotion_dir) if remotion_dir else _default_remotion_dir()
    entry = project / "src" / "index.ts"
    fps = int(manifest.get("fps") or DEFAULT_FPS)
    width = int(manifest.get("width") or DEFAULT_WIDTH)
    height = int(manifest.get("height") or DEFAULT_HEIGHT)

    rendered: list[tuple[int, Path]] = []
    warnings: list[str] = []
    for i, effect in enumerate(manifest.get("effects") or []):
        composition = _COMPOSITION_BY_TYPE.get(str(effect.get("type")))
        if not composition:
            warnings.append(f"第 {i} 条特效类型未知：{effect.get('type')}，已跳过。")
            continue
        out_path = output_dir / f"effect_{i:02d}.mov"
        # props 落文件后以路径传给 --props：npx 在 Windows 上经 npx.cmd → cmd.exe
        # 转发，JSON 直接内联会让 & | 等元字符被 cmd 重新解释（标题里很常见）。
        props = {k: v for k, v in effect.items() if k not in ("type", "start", "duration")}
        props_path = output_dir / f"effect_{i:02d}.props.json"
        props_path.write_text(json.dumps(props, ensure_ascii=False), encoding="utf-8")
        command = _build_render_command(
            npx, entry, composition, props_path, out_path, effect, fps, width, height
        )
        try:
            completed = runner(
                command,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=str(project),
            )
        except OSError as exc:
            warnings.append(f"第 {i} 条特效渲染无法启动 npx：{exc}")
            continue
        if getattr(completed, "returncode", 0) != 0:
            stderr = (getattr(completed, "stderr", "") or "")[:300]
            warnings.append(f"第 {i} 条特效渲染失败（退出码 {completed.returncode}）：{stderr}")
            continue
        rendered.append((i, out_path))

    if warnings:
        (output_dir / "effects_warnings.json").write_text(
            json.dumps({"warnings": warnings}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return rendered


def _build_render_command(
    npx: str,
    entry: Path,
    composition: str,
    props_path: Path,
    out_path: Path,
    effect: dict,
    fps: int,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
) -> list[str]:
    frames = max(1, round(float(effect.get("duration") or 0.0) * fps))
    # --width/--height 让 Remotion 4 CLI 按底片实际分辨率覆盖 composition 尺寸，
    # 竖屏（1080x1920）才不会被 Studio 默认 16:9 静默裁剪。
    return [
        npx,
        "remotion",
        "render",
        str(entry),
        composition,
        str(out_path),
        f"--props={props_path.resolve().as_posix()}",
        "--codec=prores",
        "--prores-profile=4444",
        f"--width={width}",
        f"--height={height}",
        f"--frames=0-{frames - 1}",
    ]


def _write_skipped(output_dir: Path, reason: str) -> None:
    (output_dir / "effects_skipped.json").write_text(
        json.dumps({"skipped": True, "reason": reason}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _default_remotion_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "remotion"


def overlay_effects(
    base_video: Path | str,
    effects: list[dict],
    output: Path | str,
    runner: Runner = subprocess.run,
    sfx_enabled: bool = False,
    sfx_volume: float = DEFAULT_SFX_VOLUME,
    sfx_dir: Path | str | None = None,
) -> Path:
    """把若干带 alpha 的特效片段逐层级联 overlay 到原片上。

    音轨：默认直通 copy；sfx_enabled 时按每条特效的 type 取音效（sfx.SFX_BY_TYPE），
    用 adelay 平移到该特效的 start 混进音轨（amix normalize=0 保住人声音量），再 aac 重编码。
    音效缺失 / 底片无音轨都只跳过并留痕，绝不阻断成片。

    effects: [{"path": Path, "start": float, "type": str}]，每条按 enable='between(t,start,end)'
    限定显示窗口。end 由片段自身时长决定（ffprobe 探测；探测不到则退化为长窗口）。
    """
    base_video = Path(base_video)
    output = Path(output)
    if not base_video.exists():
        raise EffectsError(f"原片不存在：{base_video}")
    if not effects:
        raise EffectsError("没有可叠加的特效片段。")

    # ffmpeg overlay 对分辨率不匹配零报错零日志（错位/裁剪静默发生），叠加前先按底片
    # 分辨率校验每条特效，不一致的跳过并留痕；探测失败不阻断（当作放行）只留痕。
    kept, mismatch_warnings = _filter_by_resolution(base_video, effects, runner)
    if mismatch_warnings:
        _append_warnings(output.parent, mismatch_warnings)
    if not kept:
        raise EffectsError("所有特效片段分辨率均与底片不匹配，无可叠加片段。")

    inputs: list[str] = ["-i", str(base_video)]
    for effect in kept:
        inputs += ["-i", str(effect["path"])]

    filter_chain, probe_warnings = _build_overlay_filter(kept, runner)
    if probe_warnings:
        _append_warnings(output.parent, probe_warnings)

    # 特效音：音效作为额外输入，按各自特效的 start 平移后混进音轨。
    audio_filter, sfx_inputs, sfx_warnings = _build_sfx_audio(
        base_video, kept, sfx_enabled, sfx_volume, sfx_dir,
        base_input_offset=1 + len(kept), runner=runner,
    )
    if sfx_warnings:
        _append_warnings(output.parent, sfx_warnings)
    inputs += sfx_inputs

    if audio_filter:
        full_filter = f"{filter_chain};{audio_filter}"
        audio_map = ["-map", "[aout]", "-c:a", "aac", "-b:a", "192k"]
    else:
        full_filter = filter_chain
        audio_map = ["-map", "0:a?", "-c:a", "copy"]

    command = [
        "ffmpeg",
        "-y",
        *inputs,
        "-filter_complex",
        full_filter,
        "-map",
        "[out]",
        *audio_map,
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "18",
        str(output),
    ]
    _run(command, runner, context="特效叠加")
    return output


def _build_sfx_audio(
    base_video: Path,
    kept: list[dict],
    sfx_enabled: bool,
    sfx_volume: float,
    sfx_dir: Path | str | None,
    base_input_offset: int,
    runner: Runner,
) -> tuple[str, list[str], list[str]]:
    """构造特效音的音频 filter 片段与额外 ffmpeg 输入。

    返回 (audio_filter, extra_inputs, warnings)。audio_filter 为空串表示不混音效
    （未开启 / 无音效文件 / 底片无音轨），调用方回退到 -c:a copy 直通。
    """
    warnings: list[str] = []
    if not sfx_enabled:
        return "", [], warnings
    items: list[tuple[float, Path]] = []
    for effect in kept:
        path = resolve_sfx_path(effect.get("type"), sfx_dir)
        if path is not None:
            items.append((float(effect.get("start") or 0.0), path))
    if not items:
        warnings.append("特效音已开启但未找到任何音效文件（assets/sfx/ 缺失或类型无对应），已跳过。")
        return "", [], warnings
    channels = _probe_audio_channels(base_video, runner)
    if channels <= 0:
        warnings.append("特效音已开启但底片没有音轨，已跳过。")
        return "", [], warnings

    vol = max(0.0, min(1.0, float(sfx_volume)))
    # 关键：底片音轨保持原声道布局、只重采样（aresample 不改音量），绝不做 mono→stereo
    # 上混——那会让 ffmpeg 按 0.707 系数把人声整轨压 -3dB。音效（单声道）反过来匹配到
    # 底片的声道数：底片立体声就用 pan 满幅复制到左右（不衰减），底片单声道就保持单声道。
    to_stereo = channels >= 2
    match = "pan=stereo|c0=c0|c1=c0" if to_stereo else "aformat=channel_layouts=mono"
    extra_inputs: list[str] = []
    steps: list[str] = ["[0:a]aresample=44100[ba]"]
    labels: list[str] = ["[ba]"]
    for n, (start, path) in enumerate(items):
        extra_inputs += ["-i", str(path)]
        idx = base_input_offset + n
        ms = max(0, int(round(start * 1000)))
        steps.append(
            f"[{idx}:a]adelay={ms}:all=1,volume={vol:.3f},aresample=44100,{match}[s{n}]"
        )
        labels.append(f"[s{n}]")
    # normalize=0 保住人声原音量（否则 amix 会按输入数均分、把口播压小）；
    # duration=first 让成片音轨长度锁定为底片音轨，音效不会把结尾拖长。
    steps.append(
        "".join(labels)
        + f"amix=inputs={len(items) + 1}:duration=first:normalize=0:dropout_transition=0[aout]"
    )
    return ";".join(steps), extra_inputs, warnings


def _probe_audio_channels(path: Path, runner: Runner) -> int:
    """ffprobe 读 a:0 声道数（无音轨/探测失败返回 0，调用方据此跳过特效音，不炸成片）。"""
    command = [
        "ffprobe", "-v", "error", "-select_streams", "a:0",
        "-show_entries", "stream=channels", "-of", "json", str(path),
    ]
    try:
        completed = runner(command, check=False, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
    except OSError:
        return 0
    if getattr(completed, "returncode", 0) != 0:
        return 0
    try:
        payload = json.loads(completed.stdout or "{}")
        streams = payload.get("streams") or []
        if not streams:
            return 0
        return int(streams[0].get("channels") or 0)
    except (json.JSONDecodeError, TypeError, ValueError):
        return 0


def _build_overlay_filter(effects: list[dict], runner: Runner) -> tuple[str, list[str]]:
    # 逐层级联：每条特效先 setpts 平移到落点、再叠加。
    # [ovK]setpts=PTS+start/TB[eK]; [base][e0]overlay=...[l0]; [l0][e1]overlay=...[out]
    steps: list[str] = []
    warnings: list[str] = []
    current = "0:v"
    for i, effect in enumerate(effects):
        start = float(effect.get("start") or 0.0)
        duration = _probe_duration(Path(effect["path"]), runner)
        if duration <= 0:
            # 探测失败时退化为长窗口（特效层不阻断成片），但必须留痕，
            # 否则损坏的特效片段会"常驻画面"却无任何线索可查。
            warnings.append(
                f"特效片段时长探测失败，按长窗口叠加（可能常驻画面）：{effect['path']}"
            )
        end = start + duration if duration > 0 else start + 3600.0
        # 关键：特效片段各自是从 t=0 起的独立时间轴。必须先用 setpts 把 PTS 平移到成片里
        # 的落点 start，其内容才会落在 enable 窗口内播放。否则短片段（如 1.5s 章节卡）早在
        # 全局 t=0..1.5 就播完并 EOF，overlay 默认 repeat 末帧（淡出残影 alpha≈0），到 start
        # （如 31.5s）时只剩发暗残影——章节卡因此像被遮罩、几乎不可见。首条 intro 因 start=0
        # 恰好对齐才正常，所有 start>0 的章节卡全中招。eof_action=pass 保证片段播完后主画面
        # 直通、不冻结。
        shifted = f"e{i}"
        steps.append(f"[{i + 1}:v]setpts=PTS+{start:.3f}/TB[{shifted}]")
        label = "out" if i == len(effects) - 1 else f"l{i}"
        enable = f"enable='between(t,{start:.3f},{end:.3f})'"
        steps.append(f"[{current}][{shifted}]overlay=0:0:{enable}:eof_action=pass[{label}]")
        current = label
    return ";".join(steps), warnings


def _filter_by_resolution(
    base_video: Path, effects: list[dict], runner: Runner
) -> tuple[list[dict], list[str]]:
    """按底片分辨率筛掉尺寸不一致的特效片段（overlay 静默错位，必须显式校验留痕）。

    - 底片探测失败：无从比对，全部放行只留一条痕（不阻断成片）。
    - 单条特效探测失败：放行该条只留痕（探测失败不等于不匹配）。
    - 尺寸都拿到且不一致：跳过该条并留痕。
    """
    base_w, base_h = _probe_resolution(base_video, runner)
    warnings: list[str] = []
    if base_w <= 0 or base_h <= 0:
        warnings.append(
            f"底片分辨率探测失败，跳过特效尺寸校验（可能错位）：{base_video}"
        )
        return list(effects), warnings

    kept: list[dict] = []
    for effect in effects:
        path = Path(effect["path"])
        eff_w, eff_h = _probe_resolution(path, runner)
        if eff_w <= 0 or eff_h <= 0:
            warnings.append(
                f"特效片段分辨率探测失败，未校验直接叠加（可能错位）：{path}"
            )
            kept.append(effect)
            continue
        if (eff_w, eff_h) != (base_w, base_h):
            warnings.append(
                f"特效片段分辨率 {eff_w}x{eff_h} 与底片 {base_w}x{base_h} 不一致，已跳过：{path}"
            )
            continue
        kept.append(effect)
    return kept, warnings


def _append_warnings(output_dir: Path, new_warnings: list[str]) -> None:
    warnings_path = output_dir / "effects_warnings.json"
    existing: list[str] = []
    if warnings_path.exists():
        try:
            existing = list(json.loads(warnings_path.read_text(encoding="utf-8")).get("warnings") or [])
        except (json.JSONDecodeError, OSError):
            existing = []
    warnings_path.write_text(
        json.dumps({"warnings": existing + new_warnings}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


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


def _probe_resolution(path: Path, runner: Runner) -> tuple[int, int]:
    """ffprobe 读 v:0 的 width,height。探测失败（无流/无 ffprobe/解析失败）返回 (0,0)。"""
    command = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height",
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
        return (0, 0)
    if getattr(completed, "returncode", 0) != 0:
        return (0, 0)
    try:
        payload = json.loads(completed.stdout or "{}")
        streams = payload.get("streams") or []
        if not streams:
            return (0, 0)
        stream = streams[0]
        return (int(stream.get("width") or 0), int(stream.get("height") or 0))
    except (json.JSONDecodeError, TypeError, ValueError):
        return (0, 0)


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
        raise EffectsError(f"{context}失败：无法启动 ffmpeg（{exc}）。") from exc
    if getattr(completed, "returncode", 0) != 0:
        stderr = (getattr(completed, "stderr", "") or "")[:300]
        raise EffectsError(f"{context}失败（ffmpeg 退出码 {completed.returncode}）：{stderr}")


def _load_json(path: Path, label: str) -> dict:
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise EffectsError(f"{label}不存在：{path}") from exc
    except json.JSONDecodeError as exc:
        raise EffectsError(f"{label}无法解析：{path}") from exc
    if not isinstance(body, dict):
        raise EffectsError(f"{label}内容不是 JSON 对象：{path}")
    return body


def _resolve_video_dimensions(
    video: Path, runner: Runner = subprocess.run
) -> tuple[int, int, list[str]]:
    """探测底片分辨率喂给 manifest；探测失败（文件缺失/无 ffprobe/无流）回落 1920x1080 并留痕。"""
    if not video.exists():
        return (
            DEFAULT_WIDTH,
            DEFAULT_HEIGHT,
            [f"底片不存在，特效分辨率回落 {DEFAULT_WIDTH}x{DEFAULT_HEIGHT}：{video}"],
        )
    width, height = _probe_resolution(video, runner)
    if width <= 0 or height <= 0:
        return (
            DEFAULT_WIDTH,
            DEFAULT_HEIGHT,
            [f"底片分辨率探测失败，特效回落 {DEFAULT_WIDTH}x{DEFAULT_HEIGHT}：{video}"],
        )
    return width, height, []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m video_factory.effects",
        description="Remotion 特效层：从 assembly_plan.json 派生特效清单并（可选）渲染叠加进原片",
    )
    parser.add_argument("--video", required=True, help="原片 release.mp4 路径")
    parser.add_argument("--plan", required=True, help="assembly_plan.json 路径（P2 产出）")
    parser.add_argument("--rewrite", default="", help="rewrite.json 路径（可选，用于片头标题）")
    parser.add_argument("--output", default="video_factory/output/effects", help="输出目录")
    parser.add_argument("--lower-thirds", action="store_true", help="附加每节字幕条（lower_third）")
    parser.add_argument("--no-sfx", dest="sfx", action="store_false", help="关闭特效音（默认开：为片头/章节卡/花字条配音效）")
    parser.set_defaults(sfx=True)
    parser.add_argument("--sfx-volume", type=float, default=DEFAULT_SFX_VOLUME, help=f"特效音音量 0~1（默认 {DEFAULT_SFX_VOLUME}）")
    parser.add_argument("--skip-render", action="store_true", help="只产出 manifest，不调 remotion 渲染")
    args = parser.parse_args(argv)

    output_dir = Path(args.output)
    try:
        plan = _load_json(Path(args.plan), "assembly_plan.json")
        rewrite = _load_json(Path(args.rewrite), "rewrite.json") if args.rewrite else None
        # 先探测底片分辨率喂给 manifest：特效片段必须与底片同尺寸，overlay 才不会静默
        # 错位/裁剪。探测失败回落 1920x1080 并留痕（不阻断）。
        width, height, dim_warnings = _resolve_video_dimensions(Path(args.video))
        manifest = build_effects_manifest(
            plan,
            rewrite,
            width=width,
            height=height,
            include_lower_thirds=args.lower_thirds,
        )
        output_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = output_dir / "effects_manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        if dim_warnings:
            _append_warnings(output_dir, dim_warnings)

        if args.skip_render:
            print(f"特效清单已生成（未渲染）：{manifest_path}，共 {len(manifest['effects'])} 条")
            return 0

        video = Path(args.video)
        if not video.exists():
            raise EffectsError(f"原片不存在：{video}")
        rendered = render_effects(manifest, output_dir)
        if not rendered:
            print(f"特效未渲染（npx 缺失或无产出），已保留清单：{manifest_path}")
            return 0

        # 特效音开启时先补齐内置音效包（缺失才合成，尊重用户替换）；ffmpeg 缺失等异常
        # 只留痕不阻断——没有音效包就静默跳过特效音，成片照出。
        if args.sfx:
            try:
                ensure_default_pack()
            except SfxError as exc:
                _append_warnings(output_dir, [f"特效音包生成失败，将无特效音：{exc}"])

        release = output_dir / "release_with_effects.mp4"
        overlay_effects(
            video,
            [
                # 用渲染返回的原始索引对回各自的 start/type，部分失败也不会错位。
                {
                    "path": path,
                    "start": manifest["effects"][index]["start"],
                    "type": manifest["effects"][index]["type"],
                }
                for index, path in rendered
            ],
            release,
            sfx_enabled=args.sfx,
            sfx_volume=args.sfx_volume,
        )
    except (EffectsError, OSError) as exc:
        print(f"特效层失败：{exc}")
        return 1

    print(f"特效层完成：渲染 {len(rendered)} 条特效")
    print(f"- 清单:     {manifest_path}")
    print(f"- 成片:     {release}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
