"""批量驱动器 + 平台预设（P9）。

流程定位：一张任务清单 jobs.json 进 → N 条成片出。每个 job 串行跑四阶段
rewrite → assemble → effects → subtitles；单 job 失败不中断整批，最终写出
batch_report.json（含每 job 状态与耗时、顶部汇总）。

设计要点：
- 平台预设（PLATFORM_PRESETS）来自运营分析结论，抹平各平台的画幅/时长/字幕/特效差异；
  参数优先级 job 显式字段 > platform 预设 > 全局默认。
- 各阶段一律**惰性引用**（STAGE_RUNNERS 里的默认实现在函数内部才 import 对应模块并调
  其 main(argv)）：既保证 subtitles 模块此刻可能尚不存在时 batch 仍可导入，也方便测试整体
  mock 掉 STAGE_RUNNERS 而不真跑任何阶段。
- rewrite_styles.STYLES 顶层 import（它已稳定存在），用于校验 style。
"""

from __future__ import annotations

import argparse
import json
import shutil
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable

from video_factory import credentials_store, stage_report, timeline
from video_factory.assemble import ASPECT_PRESETS, FIT_MODES
from video_factory.rewrite_styles import STYLES

# 全局默认（无 platform 时的兜底），来自 spec：16:9 / pad / 90s / 无字幕 / 带特效。
DEFAULT_ASPECT = "16:9"
DEFAULT_FIT = "pad"
DEFAULT_DURATION = 90
DEFAULT_SUBTITLES = True  # 2026-07-16 用户定案：字幕/特效/特效音/氛围粒子全默认开
DEFAULT_EFFECTS = True
DEFAULT_TTS = "doubao"
# 配音语速默认 1.1×（2026-07-16 用户定案：略快于原速更有网感）。显式语速由 TTS
# 侧直接合成，跳过 atempo 时长闭环——变长拍链路以实际音频为主时间轴，无碍。
DEFAULT_VOICE_SPEED = 1.1

# 合法取值集合（画幅/填充来自 assemble 的权威常量，避免拼写漂移）。
VALID_ASPECTS = frozenset(ASPECT_PRESETS.keys())
VALID_FITS = frozenset(FIT_MODES)
VALID_STYLES = frozenset(STYLES.keys())


@dataclass(frozen=True)
class PlatformPreset:
    aspect: str
    fit: str
    duration: int
    subtitles: bool
    effects: bool


# 平台预设（运营分析结论）。job 未显式覆盖时，按平台套用这些默认。
PLATFORM_PRESETS: dict[str, PlatformPreset] = {
    "douyin": PlatformPreset("9:16", "blur", 60, True, True),
    "kuaishou": PlatformPreset("9:16", "blur", 90, True, True),
    "shipinhao": PlatformPreset("9:16", "blur", 120, True, True),
    "xiaohongshu": PlatformPreset("3:4", "blur", 60, True, True),
    "bilibili": PlatformPreset("16:9", "pad", 180, True, True),
}

VALID_PLATFORMS = frozenset(PLATFORM_PRESETS.keys())


class BatchError(RuntimeError):
    """批量驱动器顶层错误（清单缺失/无法解析等），CLI 转成一行中文 + exit 1。"""


@dataclass(frozen=True)
class ResolvedJob:
    """一个 job 展开预设后的最终参数（不可变）。"""

    name: str
    source: str
    assets: str
    output: Path
    aspect: str
    fit: str
    duration: int
    style: str
    brief: str
    tts: str
    voice: str
    bgm: str
    bgm_volume: float | None
    subtitles: bool
    effects: bool
    lower_thirds: bool
    platform: str
    audio: str = ""
    # 文案改写用的 LLM provider（openai/anthropic/deepseek）；空=auto（按已配置凭据自动选）。
    llm: str = ""
    # 特效音总开关（默认开：为片头/章节卡/花字条配音效）与音量（None=用 effects 默认 0.35）。
    sfx: bool = True
    sfx_volume: float | None = None
    # 氛围粒子：全片低密度金色微尘上浮加质感（2026-07-16 二次定案：默认开）。
    ambient_particles: bool = True
    # 双画幅（2026-07-16 用户定案 A 方案）：勾选后 rewrite/voice 共享，
    # image_gen→subtitles 按画幅在 9x16/、16x9/ 子目录各跑一遍（配图各自原生构图）。
    # 默认关：只出所选画幅。
    dual_aspect: bool = False
    # 配音语速（None=默认；0.5~2.0，1.0=原速）。仅对现场 TTS 生效；分镜/字幕以
    # 音频时长为轴自动跟随，无需额外字段。
    voice_speed: float | None = None
    # 视觉来源：video=用上传的视频素材目录（默认，向后兼容）；ai_image=豆包生图为每节配图。
    visual_source: str = "video"
    # 本任务专属生图风格提示词（2026-07-18 批量按行选预设）：非空时覆盖全局启用的
    # 风格；空=用 get_style_prompt() 当前启用值。
    image_style_prompt: str = ""
    # resolve 阶段字段解析失败的错误（如 duration 非数字）；非空即整个 job 记 invalid。
    resolve_errors: tuple[str, ...] = ()


VALID_VISUAL_SOURCES = ("video", "ai_image")
# ai_image 模式各画幅的生图尺寸。用 2K 级：Seedream 4.0 自定义尺寸要求宽高均 ∈[1280,4096]，
# 旧的 1080x1920/1080x1080 等宽或高低于 1280 下限会被拒；按「张」计费与分辨率无关，
# 放大不加钱、缩回视频尺寸还留超采样余量。未知画幅回落 image_gen.DEFAULT_SIZE。
_GEN_SIZE_BY_ASPECT = {
    "9:16": "1440x2560",
    "16:9": "2560x1440",
    "1:1": "2048x2048",
    "3:4": "1536x2048",
}

# 拍级配图模式：单次批量最多生成图片数（生成超出部分截断并告警，复用库图不计入此限）。
MAX_GENERATED_PER_JOB = 60

# BGM 随机轮换（2026-07-18 抖音同质化对抗：音频指纹也别每支都一样）。
# 表单选"随机"下发本哨兵值，任务执行时从 music/ 目录实时抽一首。
RANDOM_BGM_SENTINEL = "__random__"
MUSIC_DIR = Path(__file__).resolve().parent.parent / "music"
_MUSIC_EXTS = (".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg")


def _resolve_bgm(job: "ResolvedJob") -> str:
    """解析 BGM：哨兵值 → 从 music/ 随机抽一首（空目录回落无 BGM），其余原样。"""
    if job.bgm != RANDOM_BGM_SENTINEL:
        return job.bgm
    if not MUSIC_DIR.is_dir():
        return ""
    files = sorted(
        p for p in MUSIC_DIR.iterdir()
        if p.is_file() and p.suffix.lower() in _MUSIC_EXTS
    )
    if not files:
        return ""
    import random as _random

    return str(_random.choice(files))


def _normalize_visual_source(value) -> str:
    v = str(value or "").strip().lower()
    return v if v in VALID_VISUAL_SOURCES else "video"


def _pick(job: dict, key: str, preset_value, default_value):
    """参数优先级：job 显式字段 > platform 预设 > 全局默认。

    job 里键存在（哪怕是 False/0/""）即视为显式覆盖；键不存在才回落预设、再回落默认。
    """
    if key in job and job[key] is not None:
        return job[key]
    if preset_value is not None:
        return preset_value
    return default_value


def safe_filename(name: str) -> str:
    """剥路径分隔符，只留字母数字中文与 ._-；返回安全文件名（可能为空，调用方拒空）。

    原属 studio.py（网页侧上传/任务名净化），2026-07-21 下沉到 batch：CLI 侧
    jobs.json 的 name 同样直接拼进输出路径，必须过同一道闸——此前裸拼，
    name 填 ../../ 即可把整套产物写穿越到 output 之外。
    """
    base = name.replace("\\", "/").split("/")[-1].strip()
    kept = []
    for ch in base:
        if ch.isalnum() or ch in "._-" or "一" <= ch <= "鿿":
            kept.append(ch)
    cleaned = "".join(kept).strip("._")
    return cleaned


def _default_output(name: str) -> Path:
    return Path("video_factory/output/batch") / name


def resolve_job(raw: dict, index: int) -> ResolvedJob:
    """把一个原始 job dict 展开成 ResolvedJob（不做存在性/合法性校验，只做参数合并）。"""
    platform = str(raw.get("platform") or "").strip()
    preset = PLATFORM_PRESETS.get(platform)
    p_aspect = preset.aspect if preset else None
    p_fit = preset.fit if preset else None
    p_duration = preset.duration if preset else None
    p_subtitles = preset.subtitles if preset else None
    p_effects = preset.effects if preset else None

    # name 直接拼进默认输出路径，必须净化（与 studio 网页侧同一道闸）；
    # 全被剥空（纯路径垃圾）时回落序号名。显式 output 是用户在 JSON 里可见的
    # 路径声明、不在此收紧——穿越风险只在\"name 看着像名字实际是路径\"这条暗道。
    name = safe_filename(str(raw.get("name") or "").strip()) or f"job_{index + 1:02d}"
    output = Path(raw["output"]) if raw.get("output") else _default_output(name)
    return ResolvedJob(
        name=name,
        source=str(raw.get("source") or "").strip(),
        assets=str(raw.get("assets") or "").strip(),
        output=output,
        aspect=str(_pick(raw, "aspect", p_aspect, DEFAULT_ASPECT)),
        fit=str(_pick(raw, "fit", p_fit, DEFAULT_FIT)),
        duration=int(_pick(raw, "duration", p_duration, DEFAULT_DURATION)),
        style=str(raw.get("style") or "").strip(),
        brief=str(raw.get("brief") or "").strip(),
        tts=str(raw.get("tts") or DEFAULT_TTS).strip(),
        voice=str(raw.get("voice") or "").strip(),
        audio=str(raw.get("audio") or "").strip(),
        llm=str(raw.get("llm") or "").strip(),
        bgm=str(raw.get("bgm") or "").strip(),
        bgm_volume=raw.get("bgm_volume"),
        subtitles=bool(_pick(raw, "subtitles", p_subtitles, DEFAULT_SUBTITLES)),
        effects=bool(_pick(raw, "effects", p_effects, DEFAULT_EFFECTS)),
        lower_thirds=bool(raw.get("lower_thirds") or False),
        platform=platform,
        sfx=bool(_pick(raw, "sfx", None, True)),
        sfx_volume=raw.get("sfx_volume"),
        ambient_particles=bool(_pick(raw, "ambient_particles", None, True)),
        dual_aspect=bool(raw.get("dual_aspect") or False),
        visual_source=_normalize_visual_source(raw.get("visual_source")),
        image_style_prompt=str(raw.get("image_style_prompt") or "").strip(),
        voice_speed=(
            float(raw["voice_speed"])
            if raw.get("voice_speed") not in (None, "")
            else DEFAULT_VOICE_SPEED
        ),
    )


def validate_job(job: ResolvedJob) -> list[str]:
    """校验一个展开后的 job，返回中文错误列表（空列表表示合法）。

    校验：source/assets 存在性、platform/style/aspect/fit 合法性。
    platform 非法时列出全部可选值；style 用 rewrite_styles.STYLES 校验。
    """
    if job.resolve_errors:
        # 字段都没解析出来，后续校验只会产生噪音，直接返回解析错误。
        return list(job.resolve_errors)
    errors: list[str] = []
    if job.duration <= 0:
        errors.append("duration 必须为正整数（秒）。")
    if job.audio and not Path(job.audio).exists():
        errors.append(f"audio 不存在：{job.audio}")
    if not job.source:
        errors.append("缺少 source（原片/字幕/文本路径）。")
    elif not Path(job.source).exists():
        errors.append(f"source 不存在：{job.source}")
    elif Path(job.source).is_file() and Path(job.source).stat().st_size == 0:
        # 0 字节 = 上传中断的残骸（2026-07-16 实锤）：在这里拦下并说人话，
        # 别让它混到 rewrite 阶段才以 ffmpeg Invalid data 的面目炸出来。
        errors.append(f"source 是空文件（0 字节，可能上传中断），请重新上传：{job.source}")
    # ai_image 模式：配图由生图阶段现场产出，不需要（也不校验）视频素材目录。
    if job.visual_source == "video":
        if not job.assets:
            errors.append("缺少 assets（素材目录）。")
        elif not Path(job.assets).exists():
            errors.append(f"assets 不存在：{job.assets}")
    if job.platform and job.platform not in VALID_PLATFORMS:
        options = "、".join(sorted(VALID_PLATFORMS))
        errors.append(f"platform 非法：{job.platform}。可选值：{options}。")
    if job.style and job.style not in VALID_STYLES:
        options = "、".join(sorted(VALID_STYLES))
        errors.append(f"style 非法：{job.style}。可选值：{options}。")
    if job.llm and job.llm not in ("auto", "openai", "anthropic", "deepseek"):
        errors.append(f"llm 非法：{job.llm}。可选值：auto、anthropic、deepseek、openai。")
    if job.voice_speed is not None and not (0.5 <= job.voice_speed <= 2.0):
        errors.append(f"voice_speed 非法：{job.voice_speed}。有效范围 0.5~2.0（1.0=原速）。")
    if job.aspect not in VALID_ASPECTS:
        options = "、".join(sorted(VALID_ASPECTS))
        errors.append(f"aspect 非法：{job.aspect}。可选值：{options}。")
    if job.fit not in VALID_FITS:
        options = "、".join(sorted(VALID_FITS))
        errors.append(f"fit 非法：{job.fit}。可选值：{options}。")
    return errors


def build_rewrite_argv(job: ResolvedJob, job_dir: Path) -> list[str]:
    argv = ["--source", job.source, "--duration", str(job.duration)]
    if job.brief:
        argv += ["--brief", job.brief]
    if job.style:
        argv += ["--style", job.style]
    if job.llm and job.llm != "auto":
        argv += ["--provider", job.llm]
    argv += ["--output", str(job_dir)]
    return argv


def build_voice_argv(job: ResolvedJob, job_dir: Path) -> list[str]:
    """配音阶段 argv：与 assemble 同款的配音获取语义（--audio 优先，否则 --tts/--voice/语速）。

    P16 二期把 TTS 前移为独立 voice 阶段，本 builder 组装其 CLI 参数。voice 产出
    voiceover.wav / voiceover_fitted.wav 与 timeline.json 于 job_dir，供下游按盘复用。
    """
    argv = [
        "--rewrite", str(job_dir / "rewrite.json"),
        "--duration", str(job.duration),
    ]
    if job.audio:
        # 复用现成配音：voice 的 --audio 与 --tts 互斥，audio 优先（原样引用，不变速）。
        argv += ["--audio", job.audio]
    else:
        if job.tts:
            argv += ["--tts", job.tts]
        if job.voice:
            argv += ["--voice", job.voice]
        if job.voice_speed is not None:
            # 语速只对现场 TTS 有意义（复用现成配音 --audio 时变速会毁成品，不传）。
            argv += ["--voice-speed", str(job.voice_speed)]
    argv += ["--output", str(job_dir)]
    return argv


def _assets_dir_for(job: ResolvedJob, job_dir: Path) -> str:
    """拼装用的素材目录：ai_image 用生图阶段产出的 gen_assets/，否则用户的视频素材目录。"""
    if job.visual_source == "ai_image":
        return str(job_dir / "gen_assets")
    return job.assets


def build_assemble_argv(job: ResolvedJob, job_dir: Path) -> list[str]:
    argv = [
        "--rewrite", str(job_dir / "rewrite.json"),
        "--assets", _assets_dir_for(job, job_dir),
        "--aspect", job.aspect,
        "--fit", job.fit,
        "--duration", str(job.duration),
    ]
    if job.audio:
        # 复用现成配音：assemble 的 --audio 与 --tts 互斥，audio 优先。
        # voice 阶段对用户自带 --audio 原样直接引用，assemble 照旧读同一文件。
        argv += ["--audio", job.audio]
    else:
        # 运行时改造（P16 二期）：voice 阶段已把配音落盘到 job_dir，优先复用其产物——
        # fitted（atempo 微调过）> raw（未微调）。都没有（voice 阶段失败/老任务无 voice 产物）
        # 才回落现有 --tts/--voice/--voice-speed 组装，由 assemble 内嵌 TTS 兜底（其逻辑不动）。
        # 本 builder 在阶段执行时才被调用，故此刻可查盘判断 voice 是否已产出。
        fitted = job_dir / "voiceover_fitted.wav"
        raw = job_dir / "voiceover.wav"
        if fitted.exists():
            argv += ["--audio", str(fitted)]
        elif raw.exists():
            argv += ["--audio", str(raw)]
        else:
            if job.tts:
                argv += ["--tts", job.tts]
            if job.voice:
                argv += ["--voice", job.voice]
            if job.voice_speed is not None:
                # 语速只对现场 TTS 有意义（复用现成配音 --audio 时变速会毁成品，不传）。
                argv += ["--voice-speed", str(job.voice_speed)]
    bgm = _resolve_bgm(job)
    if bgm:
        argv += ["--bgm", bgm]
        if job.bgm_volume is not None:
            argv += ["--bgm-volume", str(job.bgm_volume)]
    # 拍级配图模式：让 assemble 按拍序分配图片（而非扫描池随机对应）。
    if job.visual_source == "ai_image":
        argv += ["--ordered-assets"]
    argv += ["--output", str(job_dir)]
    return argv


def build_effects_argv(job: ResolvedJob, job_dir: Path) -> list[str]:
    argv = [
        "--video", str(job_dir / "release.mp4"),
        "--plan", str(job_dir / "assembly_plan.json"),
        "--rewrite", str(job_dir / "rewrite.json"),
    ]
    # 主时间轴（P16）无条件透传：文件不存在/损坏时消费方（effects）自然回落字符占比估算。
    argv += ["--timeline", str(job_dir / "timeline.json")]
    if job.lower_thirds:
        argv += ["--lower-thirds"]
    if not job.sfx:
        argv += ["--no-sfx"]
    if job.sfx_volume is not None:
        argv += ["--sfx-volume", str(job.sfx_volume)]
    if job.ambient_particles:
        argv += ["--ambient-particles"]
    argv += ["--output", str(job_dir)]
    return argv


def _subtitles_input_video(job: ResolvedJob, job_dir: Path) -> Path:
    """字幕阶段的输入视频：有特效走 release_with_effects.mp4，否则 release.mp4。"""
    if job.effects:
        return job_dir / "release_with_effects.mp4"
    return job_dir / "release.mp4"


def _subtitles_audio_path(job: ResolvedJob, job_dir: Path) -> Path | None:
    """字幕时间轴的参照音频：必须与实际混入成片的那份配音一致。

    assemble 现场 TTS 后可能触发 atempo 变速微调（字数偏差 >5% 就触发，很常见），真正混进
    release.mp4 的是 voiceover_fitted.wav 而非 voiceover.wav——两者节奏不同。若字幕阶段仍读
    原始 voiceover.wav 取时间轴，逐句字幕会与成片配音系统性错位且不报错。assembly_plan.json
    的 audio_path 字段记录了本次实际使用的音频（fitted 与否都对），故优先读它；读不到再回落
    job 显式 --audio / voiceover.wav。
    """
    plan_path = job_dir / "assembly_plan.json"
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        if isinstance(plan, dict):
            recorded = str(plan.get("audio_path") or "").strip()
            if recorded:
                candidate = Path(recorded)
                if candidate.exists():
                    return candidate
    except (OSError, json.JSONDecodeError):
        pass
    fallback = Path(job.audio) if job.audio else job_dir / "voiceover.wav"
    return fallback if fallback.exists() else None


def build_subtitles_argv(job: ResolvedJob, job_dir: Path) -> list[str]:
    argv = [
        "--video", str(_subtitles_input_video(job, job_dir)),
        "--rewrite", str(job_dir / "rewrite.json"),
    ]
    # 时间轴来源：优先用 assembly_plan.json 记录的真实音频（含变速后的 fitted 版），
    # 否则回落 job 显式 --audio / assemble --tts 产出的 voiceover.wav。
    voiceover = _subtitles_audio_path(job, job_dir)
    if voiceover is not None:
        argv += ["--audio", str(voiceover)]
    # 主时间轴（P16）无条件透传：存在且可加载则字幕直接由 sentences 构造、跳过 ASR；
    # 文件不存在/损坏时消费方（subtitles）自然回落现有全流程。
    argv += ["--timeline", str(job_dir / "timeline.json")]
    argv += ["--output", str(job_dir)]
    return argv


def _run_rewrite(job: ResolvedJob, job_dir: Path) -> int:
    from video_factory import rewrite

    return rewrite.main(build_rewrite_argv(job, job_dir))


def _run_voice(job: ResolvedJob, job_dir: Path) -> int:
    from video_factory import voice

    return voice.main(build_voice_argv(job, job_dir))


def _run_image_gen_section_fallback(
    rewrite: dict, size: str, gen_dir: Path, job_dir: Path, style: str = "",
    extra_warnings: list[str] | None = None,
) -> int:
    """节级配图回落路径：每节 1 张，按 ensure_section_images 选库/生图后按序拷贝。

    当拍级路径（match_beats_to_library）无凭据或解析失败时调用。style 透传任务级
    风格覆盖（2026-07-19：此前回退路径只认全局风格，批量按行选的风格在这里失效）。
    extra_warnings 承载调用方的降级原因（2026-07-21）：让\"为什么没走拍级\"进
    告警通道，而不是只打服务器控制台。
    """
    from video_factory import image_gen

    try:
        report = image_gen.ensure_section_images(rewrite, size=size, style=style)
    except RuntimeError as exc:
        print(f"生图失败：{exc}")
        stage_report.write_stage_error(job_dir, "image_gen", f"生图失败：{exc}")
        return 1

    gen_dir.mkdir(parents=True, exist_ok=True)
    for old in gen_dir.glob("img_*"):
        try:
            old.unlink()
        except OSError:
            pass
    images = sorted(report.get("images") or [], key=lambda r: r.get("section", 0))
    copied = 0
    for i, row in enumerate(images):
        src = Path(row.get("path") or "")
        if not src.exists():
            continue
        dst = gen_dir / f"img_{i:02d}{src.suffix.lower() or '.png'}"
        dst.write_bytes(src.read_bytes())
        copied += 1
    section_warnings = list(extra_warnings or [])
    section_warnings += [str(w) for w in (report.get("warnings") or [])]
    for warning in section_warnings:
        print(f"生图告警：{warning}")
    if section_warnings:
        _write_image_gen_warnings(job_dir, section_warnings)
    if copied == 0:
        print("生图失败：未产出任何可用配图（检查 ARK_API_KEY 与网络）")
        stage_report.write_stage_error(
            job_dir, "image_gen", "生图失败：未产出任何可用配图（检查 ARK_API_KEY 与网络）"
        )
        return 1
    print(f"生图完成（节级模式）：{copied} 张（新生成 {report.get('generated', 0)}、库复用 {report.get('reused', 0)}）")
    return 0


def _run_image_gen(job: ResolvedJob, job_dir: Path) -> int:
    """ai_image 模式生图阶段：优先走拍级路径（plan_beats → match_beats_to_library），
    无凭据或解析失败时自动回落节级路径（ensure_section_images）。

    拍级路径产出 gen_assets/img_NN（N=全局拍序），供 assemble --ordered-assets 按序分配；
    节级回落产出同样文件名前缀，assemble --ordered-assets 仍可读取（拍数缩减为节数）。
    超过 MAX_GENERATED_PER_JOB 的生成请求截断并告警（库复用不计入此限）。
    """
    from video_factory import image_gen

    try:
        rewrite_data = json.loads((job_dir / "rewrite.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"生图失败：无法读取 rewrite.json（{exc}）")
        stage_report.write_stage_error(job_dir, "image_gen", f"生图失败：无法读取 rewrite.json（{exc}）")
        return 1

    size = _GEN_SIZE_BY_ASPECT.get(job.aspect, image_gen.DEFAULT_SIZE)
    gen_dir = job_dir / "gen_assets"

    # 统一画风：主体提示词 + 风格提示词（与节级路径同款拼法）。2026-07-15 修复：
    # 拍级路径上线时漏拼了风格，导致成片画风与用户设置的美式漫画风不符。
    # 2026-07-18：批量行可按任务选预设——job 专属风格非空时覆盖全局启用值。
    # 2026-07-19：风格提前到检索之前算——库检索/复用也要按风格指纹隔离
    # （实锤"王阳明心学测试1"：选了古风，复用层却把美式库图塞进片子）。
    style = job.image_style_prompt.strip() or image_gen.get_style_prompt()
    style_key = image_gen.style_key_for(style)
    print(f"生图风格：{style[:24]}…（指纹 {style_key}，库复用仅限同风格）")

    # 尝试拍级路径（需要 LLM 凭据 + image_gen 新接口）。
    try:
        from video_factory.image_gen import (
            compute_section_durations,
            match_beats_to_library,
            plan_beats,
            plan_beats_from_timeline,
        )

        # 变长拍（卡话切）：voice 阶段已产 timeline.json 时，按逐句真实起止规划变长拍；
        # 拍的规划与 assemble 拼装侧共用同一权威函数 plan_beats_from_timeline、喂同一输入，
        # 拍数（=生图张数）天然与拼装侧一致。无 timeline / 计数不符 → 回落旧 5s 均分。
        timeline_sentences = timeline.load_timeline(job_dir / timeline.TIMELINE_FILENAME)
        beats = None
        if timeline_sentences:
            beats = plan_beats_from_timeline(rewrite_data, timeline_sentences)
        if not beats:
            section_durs = compute_section_durations(rewrite_data, float(job.duration))
            beats = plan_beats(rewrite_data, section_durs)
        library_root = image_gen.LIBRARY_ROOT
        library_index = image_gen.load_index(library_root)
        # 画幅硬过滤（2026-07-17 用户定案严格执行）：16:9 任务的库候选只留 16:9 图。
        # 风格硬过滤（2026-07-19）：库候选只留与本任务风格指纹一致的条目。
        beat_matches = match_beats_to_library(
            beats, library_index, library_root, target_size=size, style_key=style_key
        )
    except (ImportError, OSError) as exc:
        # 仅这两类算\"环境/接口不具备\"的预期降级：老版 image_gen 缺新接口、
        # 图库目录读不到。其余异常一律是 BUG，走下面的 unexpected 分支留真相。
        beat_matches = None
        degrade_reason = f"拍级匹配不可用（{type(exc).__name__}：{exc}）"
    except Exception as exc:  # noqa: BLE001 —— 故意兜住以免单条 job 崩掉整批
        # 2026-07-21 审查实锤：此前这里是 except Exception + 丢弃异常对象，然后
        # 打印\"无凭据或解析失败\"——任何真实回归（AttributeError/KeyError/…）都被
        # 伪装成\"你没配 key\"，静默降级到低质量节级配图且无人察觉。
        # 注意：无凭据/网络失败/解析失败 match_beats_to_library 内部已接住并返回
        # None，根本走不到这里——能到这里的基本就是 BUG，必须带 repr 留痕。
        beat_matches = None
        degrade_reason = f"拍级匹配异常（疑似 BUG，请排查）：{exc!r}"
    else:
        degrade_reason = "拍级匹配不可用（无 LLM 凭据或 LLM 返回无法解析）"

    if beat_matches is None:
        # 回落节级配图（每节 1 张）。降级原因进告警通道 → batch_report + 任务卡黄条，
        # 不再只打服务器控制台（用户看不到）。
        print(f"生图：{degrade_reason}，回落到节级配图模式。")
        return _run_image_gen_section_fallback(
            rewrite_data, size, gen_dir, job_dir, style=style,
            extra_warnings=[degrade_reason],
        )

    # 拍级路径：生成 / 复用图片并拷到 gen_assets/img_NN。
    gen_dir.mkdir(parents=True, exist_ok=True)
    for old in gen_dir.glob("img_*"):
        try:
            old.unlink()
        except OSError:
            pass

    copied = 0
    generated = 0
    reused = 0
    capped = False
    # 失败/告警收集（2026-07-17 实锤 studio_0717_163927：5.0 pro 限额打满，8/9 拍
    # 静默失败、兜底用 1 张图糊满全片，用户肉眼才发现）。全部落盘进
    # image_gen_warnings.json，任务卡黄条可见；不再只打服务器控制台。
    failures: list[str] = []
    # 第二道禁文字保险：除了上游 LLM 写提示词时的约束，最终喂给 Seedream 的提示词里
    # 也钉死（实测拍级图冒出"苦涩/回甘"贴字和乱码气泡——生图模型对提示词内的
    # 负向约束服从度远高于指望上游转述）。
    no_text_guard = "画面中不得出现任何文字、字母、数字、对话气泡、字幕、水印"

    for match in beat_matches:
        beat_idx = match.get("beat_index", copied)
        action = match.get("action", "generate")
        dst_prefix = gen_dir / f"img_{beat_idx:02d}"

        if action == "reuse":
            file_rel = match.get("file") or ""
            src = Path(library_root) / file_rel if file_rel else None
            if src and src.exists():
                # 铁闸（2026-07-17 用户定案严格执行）：复用前读文件真实像素复核画幅，
                # 不符降级现场生成——检索层/LLM 层任何一环漏了，这里也绝不放行
                # （实锤 studio_0717_223252：16:9 成片混进 9:16 库图，整片报废）。
                if not image_gen.image_file_matches_aspect(src, size):
                    message = f"拍 {beat_idx} 复用图画幅与 {size} 不符（{src.name}），已改为现场生成"
                    print(f"生图告警：{message}。")
                    failures.append(message)
                    action = "generate"
                else:
                    dst = dst_prefix.with_suffix(src.suffix.lower() or ".png")
                    # 原样拷贝。2026-07-21 用户定案退役复用图扰动（翻转/裁切/调色）：
                    # 画面效果不划算——翻转让构图和人物朝向失准，收益不抵损失。
                    # 复用优先的成本策略不变，复用不了就直接生成新图。
                    try:
                        shutil.copy2(src, dst)
                    except OSError as exc:
                        message = f"拍 {beat_idx} 复用图拷贝失败（{src.name}）：{exc}，已改为现场生成"
                        print(f"生图告警：{message}。")
                        failures.append(message)
                        action = "generate"
                    else:
                        copied += 1
                        reused += 1
                        continue
            else:
                # 文件缺失：降级为 generate（下面接着处理）。
                action = "generate"

        if action == "generate":
            if generated >= MAX_GENERATED_PER_JOB:
                if not capped:
                    print(
                        f"生图告警：生成张数达上限 {MAX_GENERATED_PER_JOB}，"
                        "后续拍位图片截断（不影响 assemble 运行，尾部循环最后一张）。"
                    )
                    capped = True
                continue
            prompt = match.get("prompt") or ""
            if not prompt:
                continue
            try:
                img_bytes = image_gen.generate_image(f"{prompt}。{style}。{no_text_guard}", size=size)
            except RuntimeError as exc:
                message = f"拍 {beat_idx} 生成失败：{exc}"
                print(f"生图告警：{message}，跳过。")
                failures.append(message)
                continue
            dst = dst_prefix.with_suffix(".png")
            dst.write_bytes(img_bytes)
            copied += 1
            generated += 1
            # 新图写回图片库并登记 index（库越大命中率越高）；入库失败只告警不阻断
            # ——图已进 gen_assets，成片不受影响。
            try:
                image_gen.ingest_generated_image(
                    img_bytes,
                    prompt=prompt,
                    category=match.get("category") or "",
                    tags=match.get("tags") or (),
                    size=size,
                    library_root=library_root,
                    style=style,
                )
            except OSError as exc:
                print(f"生图告警：拍 {beat_idx} 入库失败（{exc}），本单不受影响但该图未积累进图库。")

    if failures:
        _write_image_gen_warnings(job_dir, failures, beats=len(beats), copied=copied,
                                  generated=generated, reused=reused)

    last_reason = f"——最后一次失败原因：{failures[-1]}" if failures else ""
    if copied == 0:
        error = f"生图失败：未产出任何可用配图（检查 ARK_API_KEY 与额度）{last_reason}"
        print(error)
        stage_report.write_stage_error(job_dir, "image_gen", error)
        return 1
    # 成图率红线（2026-07-17 用户拍板"宁失败勿糊弄"）：可用图不足一半时，兜底循环
    # 会让少数图片糊满全片——白白烧掉后续特效/字幕的时间，不如就地判失败。
    if copied * 2 < len(beats):
        error = (
            f"生图成图率过低：{len(beats)} 拍只成 {copied} 张（<50%），判任务失败以免"
            f"少量图片循环糊满全片{last_reason}"
        )
        print(error)
        stage_report.write_stage_error(job_dir, "image_gen", error)
        return 1
    print(f"生图完成（拍级模式）：{copied} 张（新生成 {generated}、库复用 {reused}，共 {len(beats)} 拍）")
    return 0


IMAGE_GEN_WARNINGS_FILENAME = "image_gen_warnings.json"


def _write_image_gen_warnings(
    job_dir: Path, warnings: list[str], beats: int = 0,
    copied: int = 0, generated: int = 0, reused: int = 0,
) -> None:
    """生图告警落盘（任务卡黄条的数据源）；写失败只打印，绝不阻断生图流程。"""
    try:
        (Path(job_dir) / IMAGE_GEN_WARNINGS_FILENAME).write_text(
            json.dumps({
                "warnings": warnings, "beats": beats, "copied": copied,
                "generated": generated, "reused": reused,
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as exc:
        print(f"生图告警落盘失败（不影响流程）：{exc}")


def _run_assemble(job: ResolvedJob, job_dir: Path) -> int:
    from video_factory import assemble

    return assemble.main(build_assemble_argv(job, job_dir))


def _run_effects(job: ResolvedJob, job_dir: Path) -> int:
    from video_factory import effects

    return effects.main(build_effects_argv(job, job_dir))


def _run_subtitles(job: ResolvedJob, job_dir: Path) -> int:
    # subtitles 模块可能尚未落地：惰性 import，import 失败由上层记 failed 继续。
    from video_factory import subtitles

    return subtitles.main(build_subtitles_argv(job, job_dir))


def build_publish_argv(job: ResolvedJob, job_dir: Path) -> list[str]:
    """发布物料阶段参数：成片按最终优先级（字幕>特效>原片）作封面底图抽帧兜底。

    双画幅时成片在画幅子目录：按 主画幅子目录 > 根目录 > 另一画幅子目录 的顺序找。
    """
    argv = ["--rewrite", str(job_dir / "rewrite.json"), "--output", str(job_dir)]
    search_dirs = [job_dir]
    if job.dual_aspect:
        ordered = [_ASPECT_DIRNAMES[a] for a in _dual_aspects(job.aspect)]
        search_dirs = [job_dir / ordered[0], job_dir, job_dir / ordered[1]]
    found = None
    for name in ("release_subtitled.mp4", "release_with_effects.mp4", "release.mp4"):
        for base in search_dirs:
            if (base / name).exists():
                found = base / name
                break
        if found is not None:
            break
    if found is not None:
        argv += ["--video", str(found)]
    if job.llm:
        argv += ["--llm", job.llm]
    return argv


def _run_publish(job: ResolvedJob, job_dir: Path) -> int:
    from video_factory import publish

    return publish.main(build_publish_argv(job, job_dir))


# 阶段执行器：默认实现均惰性 import 对应模块并调 main(argv)，测试整体 mock 掉本 dict。
STAGE_RUNNERS: dict[str, Callable[[ResolvedJob, Path], int]] = {
    "rewrite": _run_rewrite,
    "voice": _run_voice,
    "image_gen": _run_image_gen,
    "assemble": _run_assemble,
    "effects": _run_effects,
    "subtitles": _run_subtitles,
    "publish": _run_publish,
}

# 全量阶段顺序（进度展示用）；image_gen 仅 ai_image 模式执行，effects/subtitles 由开关决定。
# voice（配音 + 主时间轴）P16 二期前移，恒在 rewrite 之后、image_gen/assemble 之前。
# publish（发布物料：双封面+标题/简介/标签留档）恒在末位（2026-07-16 用户定案）。
_STAGE_ORDER = ("rewrite", "voice", "image_gen", "assemble", "effects", "subtitles", "publish")


def _stages_for(job: ResolvedJob) -> list[str]:
    # 配音阶段（voice）前移：主时间轴在生图之前就绪，变长拍（卡话切）才有逐句时钟可依。
    stages = ["rewrite", "voice"]
    if job.visual_source == "ai_image":
        stages.append("image_gen")  # 生图夹在 voice 与 assemble 之间（消费 timeline.json）
    stages.append("assemble")
    if job.effects:
        stages.append("effects")
    if job.subtitles:
        stages.append("subtitles")
    stages.append("publish")  # 发布物料恒开：封面/标题/简介是每支视频的发布刚需
    return stages


def _collect_outputs(job: ResolvedJob, job_dir: Path) -> dict[str, str]:
    """收集本 job 已产出的关键文件路径（只收存在的，路径统一 posix 风格）。

    双画幅时成片在 9x16/、16x9/ 子目录，键加 _9x16/_16x9 后缀区分。
    """
    candidates = {
        "rewrite": job_dir / "rewrite.json",
        "release": job_dir / "release.mp4",
        "assembly_plan": job_dir / "assembly_plan.json",
        "effects": job_dir / "release_with_effects.mp4",
        "subtitled": job_dir / "release_subtitled.mp4",
        "cover_16x9": job_dir / "publish" / "cover_16x9.jpg",
        "cover_9x16": job_dir / "publish" / "cover_9x16.jpg",
        "cover_4x3": job_dir / "publish" / "cover_4x3.jpg",
        "cover_3x4": job_dir / "publish" / "cover_3x4.jpg",
        "publish_kit": job_dir / "publish" / "publish_kit.json",
    }
    for dirname in _ASPECT_DIRNAMES.values():
        sub = job_dir / dirname
        if not sub.is_dir():
            continue
        for key, rel in (
            ("release", "release.mp4"),
            ("effects", "release_with_effects.mp4"),
            ("subtitled", "release_subtitled.mp4"),
        ):
            candidates[f"{key}_{dirname}"] = sub / rel
    return {key: str(path) for key, path in candidates.items() if path.exists()}


def _final_output(outputs: dict[str, str]) -> str:
    """本 job 的最终成片：字幕 > 特效 > 原片，逐级回落；双画幅时竖屏（主战场）优先。"""
    for key in (
        "subtitled", "subtitled_9x16", "subtitled_16x9",
        "effects", "effects_9x16", "effects_16x9",
        "release", "release_9x16", "release_16x9",
    ):
        if key in outputs:
            return outputs[key]
    return ""


@dataclass
class JobReport:
    name: str
    status: str  # ok | failed | invalid
    platform: str
    stage_failed: str | None = None
    error: str | None = None
    outputs: dict[str, str] = field(default_factory=dict)
    # 非致命告警（生图部分失败等）：任务卡黄条展示（2026-07-17）。
    warnings: list[str] = field(default_factory=list)
    elapsed_seconds: float = 0.0

    def to_dict(self) -> dict:
        payload: dict = {"name": self.name, "status": self.status}
        if self.platform:
            payload["platform"] = self.platform
        if self.stage_failed:
            payload["stage_failed"] = self.stage_failed
        if self.error:
            payload["error"] = self.error
        payload["outputs"] = dict(self.outputs)
        payload["final"] = _final_output(self.outputs)
        if self.warnings:
            payload["warnings"] = list(self.warnings)
        payload["elapsed_seconds"] = round(self.elapsed_seconds, 3)
        return payload


# 各阶段非致命降级告警的来源文件（相对 job 目录）→ 中文标签。
# 2026-07-21 审查实锤：此前只聚合生图一路，assemble/effects/subtitles/publish 的
# 降级（特效渲染失败、估算字幕轴、模板简介兜底……）全写在各自文件里但不进
# batch_report——报告显示 ok + 零告警，成片却已静默降级。这里把管道接通：
# 各阶段只要把 warnings 列表写进自己的 JSON，就自动进报告和任务卡黄条。
_WARNING_SOURCES = (
    (IMAGE_GEN_WARNINGS_FILENAME, "生图"),
    ("effects_warnings.json", "特效"),
    ("effects_skipped.json", "特效"),
    ("subtitles_report.json", "字幕"),
    ("publish/publish_kit.json", "发布"),
)


def _collect_job_warnings(job_dir: Path) -> list[str]:
    """汇总本 job 的非致命告警（根目录 + 双画幅子目录的全部阶段告警文件）。"""
    collected: list[str] = []
    dirs = [job_dir] + [job_dir / d for d in _ASPECT_DIRNAMES.values()]
    for base in dirs:
        prefix = f"[{base.name}] " if base != job_dir else ""
        for rel, label in _WARNING_SOURCES:
            path = base / rel
            if not path.exists():
                continue
            try:
                body = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            # 生图带 beats/copied 汇总行（沿用 2026-07-17 任务卡黄条口径）。
            if rel == IMAGE_GEN_WARNINGS_FILENAME:
                beats, copied_n = body.get("beats"), body.get("copied")
                if isinstance(beats, int) and isinstance(copied_n, int) and beats > 0:
                    collected.append(f"{prefix}生图 {beats} 拍成 {copied_n} 张")
            # effects_skipped.json 是 {"skipped": true, "reason": ...} 形态。
            if body.get("skipped") and body.get("reason"):
                collected.append(f"{prefix}{label}：{body['reason']}")
            for w in body.get("warnings") or []:
                collected.append(f"{prefix}{label}：{w}")
    return collected


# (重)跑前先清掉上一轮遗留，避免本轮在早期阶段就失败时，_collect_outputs 把上次遗留的旧
# 成片当成本次 final 汇报，误导只看 final 的下游。
# 注意（P16 二期）：voiceover.wav / voiceover_fitted.wav **不清**——重跑复用配音是特性
# （省 TTS 成本与时长）；但 timeline.json 必须清：它是逐句真实起止的唯一时钟，配音一旦
# 重合成，旧时间轴就与新音轨系统性错位，绝不能让陈旧的它串进本轮生图/特效/字幕。
_OUTPUT_ARTIFACTS = (
    "rewrite.json",
    "timeline.json",
    "release.mp4",
    "assembly_plan.json",
    "release_with_effects.mp4",
    "release_subtitled.mp4",
    # 上一轮的生图告警不清会串进本轮任务卡黄条（2026-07-17）。
    "image_gen_warnings.json",
    # 2026-07-21 起其余阶段告警也进 batch_report（_WARNING_SOURCES），同理必须清：
    # 本轮早期阶段就失败时，别让上一轮的降级告警冒充本轮的。
    "effects_warnings.json",
    "effects_skipped.json",
    "subtitles_report.json",
    "publish/publish_kit.json",
)


def _clear_stale_outputs(job_dir: Path) -> None:
    for name in _OUTPUT_ARTIFACTS:
        try:
            (job_dir / name).unlink()
        except OSError:  # 含 FileNotFoundError：不存在即无需清
            pass
    # 上一轮的 <stage>_error.txt 也要清，防旧错误串进本轮报告。
    stage_report.clear_stage_errors(job_dir)


# ---------- 双画幅（2026-07-16 用户定案 A 方案：勾选才双出，配图各自原生构图） ----------

# 画幅 → 子目录名（Windows 目录名不能含冒号）。
_ASPECT_DIRNAMES = {"9:16": "9x16", "16:9": "16x9"}
# rewrite/voice 的共享产物：双画幅时复制进各画幅子目录，后续阶段的 argv builder
# 全部从 job_dir 组路径，播种后无需任何改动即可在子目录里工作。
_SHARED_ARTIFACTS = (
    "rewrite.json",
    "voiceover.wav",
    "voiceover_fitted.wav",
    "voiceover.txt",
    "voiceover_notes.txt",
    "timeline.json",
)


def _dual_aspects(primary: str) -> list[str]:
    """双画幅的执行顺序：所选画幅优先（final 回显以它为主），另一画幅随后。"""
    if primary not in _ASPECT_DIRNAMES:
        primary = "9:16"
    other = "16:9" if primary == "9:16" else "9:16"
    return [primary, other]


def _seed_shared_artifacts(root: Path, sub: Path) -> None:
    """把 rewrite/voice 的共享产物复制进画幅子目录（存在才复制，覆盖旧副本）。"""
    for name in _SHARED_ARTIFACTS:
        src = root / name
        if src.exists():
            shutil.copy2(src, sub / name)


def _execute_stage(
    report_job: ResolvedJob,
    exec_job: ResolvedJob,
    exec_dir: Path,
    stage: str,
    on_stage: Callable[[str], None] | None,
    started: float,
    error_prefix: str = "",
) -> JobReport | None:
    """执行单个阶段：成功返回 None，失败返回构造好的 JobReport（终止本 job）。

    report_job 提供报告口径（name/platform/根 outputs）；exec_job/exec_dir 是实际
    执行口径——双画幅时 exec 指向画幅子目录的克隆 job，单画幅两者相同。
    """
    root_dir = Path(report_job.output)
    if on_stage is not None:
        try:
            on_stage(stage)
        except Exception:
            # 进度回调只是旁路观察者，坏回调绝不能打断任务执行。
            pass
    runner = STAGE_RUNNERS[stage]
    try:
        code = runner(exec_job, exec_dir)
    except SystemExit as exc:
        # argparse 参数不合法时抛 SystemExit（不继承 Exception！），
        # 不单独接住会击穿整批。code 0（如 --help）视作该阶段正常结束。
        code = exc.code if isinstance(exc.code, int) else 1
        if code != 0:
            return JobReport(
                name=report_job.name,
                status="failed",
                platform=report_job.platform,
                stage_failed=stage,
                error=f"{error_prefix}{stage} 阶段参数解析失败（SystemExit {code}）：请检查 batch 组装的 argv 与该阶段 CLI 定义是否一致。",
                outputs=_collect_outputs(report_job, root_dir),
            warnings=_collect_job_warnings(root_dir),
                elapsed_seconds=time.monotonic() - started,
            )
        return None
    except Exception as exc:  # 含惰性 import 失败（如 subtitles 尚未落地）
        return JobReport(
            name=report_job.name,
            status="failed",
            platform=report_job.platform,
            stage_failed=stage,
            error=f"{error_prefix}{stage} 阶段异常：{exc}",
            outputs=_collect_outputs(report_job, root_dir),
            warnings=_collect_job_warnings(root_dir),
            elapsed_seconds=time.monotonic() - started,
        )
    if code != 0:
        # 阶段失败时回读它落盘的 <stage>_error.txt，把真实原因带进报告/任务看板
        # （阶段的 print 只在服务终端可见，光给退出码用户无从排障）。
        detail = stage_report.read_stage_error(exec_dir, stage)
        error = f"{error_prefix}{stage} 阶段返回非 0 退出码：{code}"
        if detail:
            error = f"{error}——{detail}"
        return JobReport(
            name=report_job.name,
            status="failed",
            platform=report_job.platform,
            stage_failed=stage,
            error=error,
            outputs=_collect_outputs(report_job, root_dir),
            warnings=_collect_job_warnings(root_dir),
            elapsed_seconds=time.monotonic() - started,
        )
    return None


def run_job(job: ResolvedJob, on_stage: Callable[[str], None] | None = None) -> JobReport:
    """串行执行一个 job 的各阶段；任一阶段返回非 0 或抛异常即记 failed 并停止后续阶段。

    on_stage（可选）在每个阶段开始执行前以阶段名回调（用于 studio 的实时进度）；
    默认 None 时行为与历史完全一致。

    双画幅（dual_aspect，2026-07-16 用户定案）：rewrite/voice 在根目录共享执行一次，
    image_gen→subtitles 按画幅在 9x16/、16x9/ 子目录各跑一遍（共享产物先播种进
    子目录，argv builder 全部无需改动），publish 最后在根目录跑一次（封面本就双份）。
    """
    started = time.monotonic()
    job_dir = Path(job.output)
    job_dir.mkdir(parents=True, exist_ok=True)
    _clear_stale_outputs(job_dir)  # 清上一轮遗留，防早期失败误报旧成片为本次 final
    stages = _stages_for(job)

    if not job.dual_aspect:
        for stage in stages:
            report = _execute_stage(job, job, job_dir, stage, on_stage, started)
            if report is not None:
                return report
    else:
        shared = [s for s in stages if s in ("rewrite", "voice")]
        per_aspect = [s for s in stages if s not in ("rewrite", "voice", "publish")]
        for stage in shared:
            report = _execute_stage(job, job, job_dir, stage, on_stage, started)
            if report is not None:
                return report
        for aspect in _dual_aspects(job.aspect):
            sub = job_dir / _ASPECT_DIRNAMES[aspect]
            sub.mkdir(parents=True, exist_ok=True)
            _clear_stale_outputs(sub)
            _seed_shared_artifacts(job_dir, sub)
            sub_job = replace(job, aspect=aspect, output=sub)
            for stage in per_aspect:
                report = _execute_stage(
                    job, sub_job, sub, stage, on_stage, started,
                    error_prefix=f"[画幅 {aspect}] ",
                )
                if report is not None:
                    return report
        report = _execute_stage(job, job, job_dir, "publish", on_stage, started)
        if report is not None:
            return report

    return JobReport(
        name=job.name,
        status="ok",
        platform=job.platform,
        outputs=_collect_outputs(job, job_dir),
        warnings=_collect_job_warnings(job_dir),
        elapsed_seconds=time.monotonic() - started,
    )


def load_jobs(jobs_path: Path | str) -> list[dict]:
    """读取 jobs.json（{"jobs": [...]}），返回原始 job dict 列表。"""
    path = Path(jobs_path)
    if not path.exists():
        raise BatchError(f"任务清单不存在：{path}")
    try:
        body = json.loads(path.read_text(encoding="utf-8", errors="replace").lstrip("﻿"))
    except json.JSONDecodeError as exc:
        raise BatchError(f"任务清单 JSON 无法解析：{path}（{exc}）") from exc
    if not isinstance(body, dict):
        raise BatchError("任务清单顶层必须是对象，形如 {\"jobs\": [...]}。")
    jobs = body.get("jobs")
    if not isinstance(jobs, list) or not jobs:
        raise BatchError("任务清单缺少非空的 jobs 数组。")
    for i, item in enumerate(jobs):
        if not isinstance(item, dict):
            raise BatchError(f"jobs[{i}] 不是对象。")
    return jobs


def _unresolvable_job(name: str, platform: str, error: str) -> ResolvedJob:
    """字段解析失败的 job 占位：带 resolve_errors，走 validate 时必然记 invalid。"""
    return ResolvedJob(
        name=name, source="", assets="", output=_default_output(name),
        aspect=DEFAULT_ASPECT, fit=DEFAULT_FIT, duration=DEFAULT_DURATION,
        style="", brief="", tts=DEFAULT_TTS, voice="", bgm="", bgm_volume=None,
        subtitles=DEFAULT_SUBTITLES, effects=DEFAULT_EFFECTS, lower_thirds=False,
        platform=platform, resolve_errors=(error,),
    )


def resolve_all(raw_jobs: list[dict]) -> list[ResolvedJob]:
    resolved: list[ResolvedJob] = []
    for i, raw in enumerate(raw_jobs):
        try:
            resolved.append(resolve_job(raw, i))
        except (ValueError, TypeError) as exc:
            # 单个 job 字段类型错误（如 duration: "oops"）不许击穿整批：
            # 转成 invalid 占位，让 batch_report 里能看到原因并继续跑其余 job。
            name = str(raw.get("name") or f"job_{i + 1:02d}").strip()
            platform = str(raw.get("platform") or "").strip()
            resolved.append(_unresolvable_job(name, platform, f"job 字段无法解析：{exc}"))
    return resolved


def _summarize(reports: list[JobReport]) -> dict:
    return {
        "total": len(reports),
        "ok": sum(1 for r in reports if r.status == "ok"),
        "failed": sum(1 for r in reports if r.status == "failed"),
        "invalid": sum(1 for r in reports if r.status == "invalid"),
    }


def build_report(reports: list[JobReport]) -> dict:
    return {
        "summary": _summarize(reports),
        "jobs": [r.to_dict() for r in reports],
    }


def _invalid_report(job: ResolvedJob, errors: list[str]) -> JobReport:
    return JobReport(
        name=job.name,
        status="invalid",
        platform=job.platform,
        error="；".join(errors),
    )


def run_batch(jobs: list[ResolvedJob], only: str = "") -> list[JobReport]:
    """执行一批 job：非法记 invalid 继续，合法进 run_job；--only 只跑指定 name。"""
    reports: list[JobReport] = []
    for job in jobs:
        if only and job.name != only:
            continue
        errors = validate_job(job)
        if errors:
            report = _invalid_report(job, errors)
            print(f"[{job.name}] platform={job.platform or '-'} → invalid：{report.error}")
            reports.append(report)
            continue
        report = run_job(job)
        stage_note = f"（{report.stage_failed} 失败）" if report.stage_failed else ""
        print(f"[{job.name}] platform={job.platform or '-'} → {report.status}{stage_note}")
        reports.append(report)
    if only and not reports:
        # --only 拼错 job 名时静默空跑等于掩盖自动化事故，必须显式报错。
        raise BatchError(f"--only 未匹配到任何 job：{only}。请核对任务清单里的 name 字段。")
    return reports


def write_report(report: dict, output_dir: Path | str) -> Path:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "batch_report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report_path


def _image_gen_dry_note(job: ResolvedJob, job_dir: Path) -> list[str]:
    """dry-run 展示 image_gen 阶段（它不走 argv 而是直接调 image_gen，故给一行说明）。"""
    size = _GEN_SIZE_BY_ASPECT.get(job.aspect, "默认尺寸")
    return [f"(豆包 Seedream 生图 size={size} → {job_dir / 'gen_assets'})"]


def _dry_run_argv(job: ResolvedJob) -> dict[str, list[str]]:
    """dry-run：展开每阶段的最终参数（不执行），按 job 开关裁掉未启用阶段。"""
    job_dir = Path(job.output)
    builders = {
        "rewrite": build_rewrite_argv,
        "voice": build_voice_argv,
        "assemble": build_assemble_argv,
        "effects": build_effects_argv,
        "subtitles": build_subtitles_argv,
    }
    # image_gen 阶段没有 CLI argv（batch 直接调 image_gen），给一行说明避免 KeyError。
    return {
        stage: builders[stage](job, job_dir) if stage in builders
        else _image_gen_dry_note(job, job_dir)
        for stage in _stages_for(job)
    }


def _print_dry_run(jobs: list[ResolvedJob], only: str) -> None:
    for job in jobs:
        if only and job.name != only:
            continue
        errors = validate_job(job)
        status = "invalid" if errors else "ok"
        print(f"[{job.name}] platform={job.platform or '-'} aspect={job.aspect} "
              f"fit={job.fit} duration={job.duration}s subtitles={job.subtitles} "
              f"effects={job.effects} → {status}")
        if errors:
            print(f"  校验错误：{'；'.join(errors)}")
            continue
        for stage, argv in _dry_run_argv(job).items():
            print(f"  {stage}: {' '.join(argv)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m video_factory.batch",
        description="批量驱动器：一张 jobs.json → N 条成片（rewrite→assemble→effects→subtitles）",
    )
    parser.add_argument("--jobs", required=True, help="任务清单 jobs.json 路径（{\"jobs\": [...]}）")
    parser.add_argument("--dry-run", action="store_true", help="只校验解析并打印每 job 展开参数，不执行")
    parser.add_argument("--only", default="", help="只跑指定 name 的 job")
    parser.add_argument(
        "--report-dir",
        default="video_factory/output/batch",
        help="batch_report.json 输出目录",
    )
    args = parser.parse_args(argv)
    # 补齐凭据：credentials.yaml → 空缺的环境变量。各阶段 main 也各自兜底，
    # 这里再兜一层是让 dry-run 等 batch 自身路径也有一致的环境视角。
    credentials_store.ensure_env_loaded()

    try:
        raw_jobs = load_jobs(args.jobs)
        jobs = resolve_all(raw_jobs)
    except BatchError as exc:
        print(f"批量任务失败：{exc}")
        return 1

    if args.dry_run:
        _print_dry_run(jobs, args.only)
        return 0

    reports = run_batch(jobs, only=args.only)
    report = build_report(reports)
    report_path = write_report(report, args.report_dir)
    summary = report["summary"]
    print(
        f"批量完成：共 {summary['total']} 个，成功 {summary['ok']}、"
        f"失败 {summary['failed']}、非法 {summary['invalid']}"
    )
    print(f"- 报告: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
