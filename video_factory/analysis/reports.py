from __future__ import annotations

from pathlib import Path
from typing import Optional

from video_factory.analysis.models import AnalysisPaths, MediaInfo, SampleFrame


def build_timeline_markdown(
    media: MediaInfo, frames: list[SampleFrame], base_dir: Optional[Path] = None
) -> str:
    relative_base = base_dir or media.source_path.parent
    lines = [
        "# Reference Video Timeline Seed",
        "",
        "Use this as an evidence scaffold for expert review. Each sampled frame should be checked against the source video before drawing creative conclusions.",
        "",
        "| Time | Frame | Segment function | Visual evidence | Audio/subtitle notes |",
        "| --- | --- | --- | --- | --- |",
    ]

    for frame in frames:
        frame_path = _markdown_code_path(_relative_to_parent(frame.path, relative_base))
        lines.append(
            f"| {frame.label} | {frame_path} | Segment function placeholder | Visual evidence placeholder | Audio/subtitle notes placeholder |"
        )

    return "\n".join(lines) + "\n"


def build_quality_report_markdown(media: MediaInfo) -> str:
    return (
        "# Reference Video Quality Report\n"
        "\n"
        "本报告需要结合抽帧由 Codex 进行专家判断。自动媒体信息只提供事实，不替代审美判断。\n"
        "\n"
        "## 基础信息\n"
        f"- Source: {media.source_path}\n"
        f"- Duration: {media.duration:.2f}s\n"
        f"- Resolution: {media.width}x{media.height}\n"
        f"- Aspect ratio: {media.aspect_ratio}\n"
        f"- Orientation: {media.orientation}\n"
        f"- FPS: {media.fps:.3f}\n"
        f"- Video codec: {media.video_codec}\n"
        f"- Audio codec: {media.audio_codec}\n"
        f"- Audio sample rate: {media.audio_sample_rate} Hz\n"
        f"- Bit rate: {media.bit_rate} bps\n"
        "\n"
        "## 时间线拆解\n"
        "- 结合 timeline.md 和抽帧，标注每个片段的功能、转场、信息密度与情绪变化。\n"
        "\n"
        "## 镜头与画面系统\n"
        "- 审查构图、景别、机位、运动方式、光线、色彩和主体清晰度。\n"
        "\n"
        "## 字幕系统\n"
        "- 审查字幕位置、字号、换行、强调方式、遮挡风险和阅读节奏。\n"
        "\n"
        "## 声音与口播\n"
        "- 审查口播自然度、信息顺序、停顿、音乐音量、环境声和混音清晰度。\n"
        "\n"
        "## 剪辑节奏\n"
        "- 审查镜头时长、节奏峰谷、信息承接、重复片段和跳切合理性。\n"
        "\n"
        "## 真实感来源\n"
        "- 记录让样片显得可信的现场证据、人物行为、环境细节和非模板化表达。\n"
        "\n"
        "## 可复刻规则\n"
        "- 提炼可迁移到生产流程的结构、素材、字幕、声音和剪辑规则。\n"
        "\n"
        "## 失败样片对照\n"
        "- 列出与参考视频相反的失败表现，用于后续生成结果的人工对照审查。\n"
    )


def build_production_template_markdown(media: MediaInfo) -> str:
    return (
        "# Production Template Draft\n"
        "\n"
        f"Reference source: {media.source_path}\n"
        "\n"
        "## 开头结构规则\n"
        "- 填写前 3 秒如何建立场景、对象、冲突或明确收益。\n"
        "\n"
        "## 叙事结构规则\n"
        "- 填写信息展开顺序、段落长度、转折点和收束方式。\n"
        "\n"
        "## 素材规则\n"
        "- 填写必须出现的素材类型、镜头证据、环境细节和禁用素材。\n"
        "\n"
        "## 字幕规则\n"
        "- 填写字幕密度、断句、强调、位置、样式和遮挡规避规则。\n"
        "\n"
        "## 口播规则\n"
        "- 填写语气、人称、停顿、句长、情绪和信息优先级。\n"
        "\n"
        "## 剪辑规则\n"
        "- 填写镜头切换节奏、转场、音画同步和保留真实停顿的规则。\n"
        "\n"
        "## 禁止规则\n"
        "- 填写会破坏参考风格、可信度或清晰度的做法。\n"
    )


def build_scorecard_markdown(media: MediaInfo) -> str:
    return (
        "# Reference-Derived Quality Scorecard\n"
        "\n"
        f"Reference source: {media.source_path}\n"
        "\n"
        "| 维度 | 满分 | 不合格表现 |\n"
        "| --- | ---: | --- |\n"
        "| 语义一致性 | 15 | 主题、对象或结论与参考规则不一致。 |\n"
        "| 素材可信度 | 15 | 素材像库存拼贴，缺少现场证据或真实细节。 |\n"
        "| 口播自然度 | 15 | 语气机械、断句不自然或信息顺序难以理解。 |\n"
        "| 字幕干净度 | 10 | 字幕遮挡主体、换行混乱、强调滥用或阅读压力过大。 |\n"
        "| 剪辑节奏 | 15 | 镜头过密或拖沓，音画不同步，信息承接断裂。 |\n"
        "| 包装统一性 | 10 | 字体、色彩、版式、转场或声音包装不统一。 |\n"
        "| 整体真实感 | 20 | 生成痕迹明显，人物、环境或叙事缺少可信动机。 |\n"
        "\n"
        "总分说明：低于 80 分不得进入发布候选。\n"
    )


def write_report_artifacts(
    media: MediaInfo,
    frames: list[SampleFrame],
    paths: AnalysisPaths,
    overwrite: bool = False,
) -> None:
    paths.output_dir.mkdir(parents=True, exist_ok=True)
    _write_text_if_needed(
        paths.timeline,
        build_timeline_markdown(media, frames, base_dir=paths.output_dir),
        overwrite=overwrite,
    )
    _write_text_if_needed(
        paths.quality_report,
        build_quality_report_markdown(media),
        overwrite=overwrite,
    )
    _write_text_if_needed(
        paths.production_template,
        build_production_template_markdown(media),
        overwrite=overwrite,
    )
    _write_text_if_needed(
        paths.scorecard,
        build_scorecard_markdown(media),
        overwrite=overwrite,
    )


def _relative_to_parent(path: Path, parent: Path) -> Path:
    try:
        return path.relative_to(parent)
    except ValueError:
        return path


def _markdown_code_path(path: Path) -> str:
    safe_path = path.as_posix().replace("`", "'").replace("|", "\\|")
    return f"`{safe_path}`"


def _write_text_if_needed(path: Path, text: str, overwrite: bool) -> None:
    if path.exists() and not overwrite:
        return
    path.write_text(text, encoding="utf-8")
