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
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from video_factory.assemble import ASPECT_PRESETS, FIT_MODES
from video_factory.rewrite_styles import STYLES

# 全局默认（无 platform 时的兜底），来自 spec：16:9 / pad / 90s / 无字幕 / 带特效。
DEFAULT_ASPECT = "16:9"
DEFAULT_FIT = "pad"
DEFAULT_DURATION = 90
DEFAULT_SUBTITLES = False
DEFAULT_EFFECTS = True
DEFAULT_TTS = "doubao"

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
    # 视觉来源：video=用上传的视频素材目录（默认，向后兼容）；ai_image=豆包生图为每节配图。
    visual_source: str = "video"
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

    name = str(raw.get("name") or f"job_{index + 1:02d}").strip()
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
        visual_source=_normalize_visual_source(raw.get("visual_source")),
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
        argv += ["--audio", job.audio]
    else:
        if job.tts:
            argv += ["--tts", job.tts]
        if job.voice:
            argv += ["--voice", job.voice]
    if job.bgm:
        argv += ["--bgm", job.bgm]
        if job.bgm_volume is not None:
            argv += ["--bgm-volume", str(job.bgm_volume)]
    argv += ["--output", str(job_dir)]
    return argv


def build_effects_argv(job: ResolvedJob, job_dir: Path) -> list[str]:
    argv = [
        "--video", str(job_dir / "release.mp4"),
        "--plan", str(job_dir / "assembly_plan.json"),
        "--rewrite", str(job_dir / "rewrite.json"),
    ]
    if job.lower_thirds:
        argv += ["--lower-thirds"]
    if not job.sfx:
        argv += ["--no-sfx"]
    if job.sfx_volume is not None:
        argv += ["--sfx-volume", str(job.sfx_volume)]
    argv += ["--output", str(job_dir)]
    return argv


def _subtitles_input_video(job: ResolvedJob, job_dir: Path) -> Path:
    """字幕阶段的输入视频：有特效走 release_with_effects.mp4，否则 release.mp4。"""
    if job.effects:
        return job_dir / "release_with_effects.mp4"
    return job_dir / "release.mp4"


def build_subtitles_argv(job: ResolvedJob, job_dir: Path) -> list[str]:
    argv = [
        "--video", str(_subtitles_input_video(job, job_dir)),
        "--rewrite", str(job_dir / "rewrite.json"),
    ]
    # 时间轴来源：job 显式给的现成配音优先，否则用 assemble --tts 产出的 voiceover.wav。
    voiceover = Path(job.audio) if job.audio else job_dir / "voiceover.wav"
    if voiceover.exists():
        argv += ["--audio", str(voiceover)]
    argv += ["--output", str(job_dir)]
    return argv


def _run_rewrite(job: ResolvedJob, job_dir: Path) -> int:
    from video_factory import rewrite

    return rewrite.main(build_rewrite_argv(job, job_dir))


def _run_image_gen(job: ResolvedJob, job_dir: Path) -> int:
    """ai_image 模式的生图阶段：按 rewrite 各节生图（库命中复用），拷进 gen_assets/
    按节序命名 img_NN，供 assemble 当素材池扫描。无 ARK/LLM 凭据等硬失败返回非 0。"""
    from video_factory import image_gen

    try:
        rewrite = json.loads((job_dir / "rewrite.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"生图失败：无法读取 rewrite.json（{exc}）")
        return 1
    size = _GEN_SIZE_BY_ASPECT.get(job.aspect, image_gen.DEFAULT_SIZE)
    try:
        report = image_gen.ensure_section_images(rewrite, size=size)
    except RuntimeError as exc:  # ImageGenError / RewriteError（缺 ARK/LLM 凭据、条数不齐等）
        print(f"生图失败：{exc}")
        return 1

    gen_dir = job_dir / "gen_assets"
    gen_dir.mkdir(parents=True, exist_ok=True)
    for old in gen_dir.glob("img_*"):  # 重跑幂等：清掉上轮拷贝
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
    for warning in report.get("warnings") or []:
        print(f"生图告警：{warning}")
    if copied == 0:
        print("生图失败：未产出任何可用配图（检查 ARK_API_KEY 与网络）")
        return 1
    print(f"生图完成：{copied} 张（新生成 {report.get('generated', 0)}、库复用 {report.get('reused', 0)}）")
    return 0


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


# 阶段执行器：默认实现均惰性 import 对应模块并调 main(argv)，测试整体 mock 掉本 dict。
STAGE_RUNNERS: dict[str, Callable[[ResolvedJob, Path], int]] = {
    "rewrite": _run_rewrite,
    "image_gen": _run_image_gen,
    "assemble": _run_assemble,
    "effects": _run_effects,
    "subtitles": _run_subtitles,
}

# 全量阶段顺序（进度展示用）；image_gen 仅 ai_image 模式执行，effects/subtitles 由开关决定。
_STAGE_ORDER = ("rewrite", "image_gen", "assemble", "effects", "subtitles")


def _stages_for(job: ResolvedJob) -> list[str]:
    stages = ["rewrite"]
    if job.visual_source == "ai_image":
        stages.append("image_gen")  # 生图夹在 rewrite 与 assemble 之间
    stages.append("assemble")
    if job.effects:
        stages.append("effects")
    if job.subtitles:
        stages.append("subtitles")
    return stages


def _collect_outputs(job: ResolvedJob, job_dir: Path) -> dict[str, str]:
    """收集本 job 已产出的关键文件路径（只收存在的，路径统一 posix 风格）。"""
    candidates = {
        "rewrite": job_dir / "rewrite.json",
        "release": job_dir / "release.mp4",
        "assembly_plan": job_dir / "assembly_plan.json",
        "effects": job_dir / "release_with_effects.mp4",
        "subtitled": job_dir / "release_subtitled.mp4",
    }
    return {key: str(path) for key, path in candidates.items() if path.exists()}


def _final_output(outputs: dict[str, str]) -> str:
    """本 job 的最终成片：字幕 > 特效 > 原片，逐级回落。"""
    for key in ("subtitled", "effects", "release"):
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
        payload["elapsed_seconds"] = round(self.elapsed_seconds, 3)
        return payload


def run_job(job: ResolvedJob, on_stage: Callable[[str], None] | None = None) -> JobReport:
    """串行执行一个 job 的各阶段；任一阶段返回非 0 或抛异常即记 failed 并停止后续阶段。

    on_stage（可选）在每个阶段开始执行前以阶段名回调（用于 studio 的实时进度）；
    默认 None 时行为与历史完全一致。
    """
    started = time.monotonic()
    job_dir = Path(job.output)
    job_dir.mkdir(parents=True, exist_ok=True)
    for stage in _stages_for(job):
        if on_stage is not None:
            try:
                on_stage(stage)
            except Exception:
                # 进度回调只是旁路观察者，坏回调绝不能打断任务执行。
                pass
        runner = STAGE_RUNNERS[stage]
        try:
            code = runner(job, job_dir)
        except SystemExit as exc:
            # argparse 参数不合法时抛 SystemExit（不继承 Exception！），
            # 不单独接住会击穿整批。code 0（如 --help）视作该阶段正常结束。
            code = exc.code if isinstance(exc.code, int) else 1
            if code != 0:
                return JobReport(
                    name=job.name,
                    status="failed",
                    platform=job.platform,
                    stage_failed=stage,
                    error=f"{stage} 阶段参数解析失败（SystemExit {code}）：请检查 batch 组装的 argv 与该阶段 CLI 定义是否一致。",
                    outputs=_collect_outputs(job, job_dir),
                    elapsed_seconds=time.monotonic() - started,
                )
        except Exception as exc:  # 含惰性 import 失败（如 subtitles 尚未落地）
            return JobReport(
                name=job.name,
                status="failed",
                platform=job.platform,
                stage_failed=stage,
                error=f"{stage} 阶段异常：{exc}",
                outputs=_collect_outputs(job, job_dir),
                elapsed_seconds=time.monotonic() - started,
            )
        if code != 0:
            return JobReport(
                name=job.name,
                status="failed",
                platform=job.platform,
                stage_failed=stage,
                error=f"{stage} 阶段返回非 0 退出码：{code}",
                outputs=_collect_outputs(job, job_dir),
                elapsed_seconds=time.monotonic() - started,
            )
    return JobReport(
        name=job.name,
        status="ok",
        platform=job.platform,
        outputs=_collect_outputs(job, job_dir),
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


def _dry_run_argv(job: ResolvedJob) -> dict[str, list[str]]:
    """dry-run：展开每阶段的最终参数（不执行），按 job 开关裁掉未启用阶段。"""
    job_dir = Path(job.output)
    builders = {
        "rewrite": build_rewrite_argv,
        "assemble": build_assemble_argv,
        "effects": build_effects_argv,
        "subtitles": build_subtitles_argv,
    }
    return {stage: builders[stage](job, job_dir) for stage in _stages_for(job)}


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
