"""原片文案 → LLM 原创改写。

流程定位（批量生产第 1 步）：导入别人的视频后，从其字幕/OCR/转写文本拿到
原始文案，交给 LLM 重构出一版结构和表达都不同的原创口播稿，并按目标时长
控制字数。产出 rewrite.json（结构化）+ voiceover.txt（可直接送 TTS 配音）。

改写遵守项目原创性红线：只保留信息点，不沿用原文句子与结构
（见 originality.py 的建议口径）。
"""

from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from video_factory import credentials_store, stage_report
from video_factory.llm import (
    ANTHROPIC_DEFAULT_MODEL,
    OPENAI_DEFAULT_MODEL,
    LLMConfig,
    LLMProviderError,
    chat_completion,
)
from video_factory.rewrite_styles import (
    DEFAULT_STYLE_KEY,
    STYLES,
    RewriteStyle,
    UnknownStyleError,
    get_style,
)

if TYPE_CHECKING:
    from video_factory.asr import ASRConfig

# 中文口播平均语速（字/秒），用于把目标时长换算成字数预算。
# 2026-07-14 用默认音色（豆包刘飞 zh_male_liufei_uranus_bigtts）真实合成 146 字实测
# 4.86 字/秒后校准；旧值 4.3 偏低 13%，是"设 120s 只出 100 出头"的误差来源之一。
CHARS_PER_SECOND = 4.8
DEFAULT_TARGET_DURATION = 90
MIN_SECTIONS = 2

# 时长闭环（第一级：字数强制）：预估口播时长低于目标的 85% 就让 LLM 扩写重试，
# 最多 2 轮。LLM 普遍欠写（120s 目标常只写一半），提示词里的字数要求约束不住，
# 必须产出后检查再回炉——这是"设 120s 只出 60s"的主因。
MIN_LENGTH_RATIO = 0.85
MAX_EXPAND_ROUNDS = 2

# 用户自定义改写文风指令（仿照生图 IMAGE_STYLE_PROMPT 的同款机制）：
# 优先级 环境变量 > settings.yaml > 空（空=只用内置七种内容类型模板）。
# 非空时作为「全局创作指令」追加进 system prompt，压过模板的文风约定，
# 但绝不允许违反 JSON 输出格式硬性要求（否则解析会崩）。
REWRITE_STYLE_PROMPT_ENV = "REWRITE_STYLE_PROMPT"


def get_rewrite_style_prompt() -> str:
    """当前生效的改写文风指令：环境变量 > settings.yaml > 空串。惰性导入避免环依赖。"""
    env_value = (os.getenv(REWRITE_STYLE_PROMPT_ENV) or "").strip()
    if env_value:
        return env_value
    from video_factory.settings_store import load_settings

    return (load_settings().get(REWRITE_STYLE_PROMPT_ENV) or "").strip()


# 需要走 ASR 语音转写的媒体后缀（视频 + 音频）。
MEDIA_SUFFIXES = (
    ".mp4", ".mov", ".mkv", ".webm", ".m4v",
    ".mp3", ".wav", ".m4a", ".aac", ".flac",
)


class RewriteError(RuntimeError):
    pass


@dataclass(frozen=True)
class RewriteSection:
    index: int
    title: str
    narration: str
    visual_hint: str
    # LLM 强调计划：0~3 条/节，kind ∈ {keyword, number, golden}。
    # 旧 rewrite.json 无此字段时默认空元组（向后兼容）。
    emphasis: tuple[dict, ...] = ()


@dataclass(frozen=True)
class RewriteResult:
    provider: str
    model: str
    target_duration_seconds: int
    hook: str
    sections: tuple[RewriteSection, ...]
    publish_titles: tuple[str, ...]
    notes: str
    style: str = DEFAULT_STYLE_KEY
    # 时长闭环：实际触发的扩写轮数（0=首轮字数即达标）。
    expand_rounds: int = 0

    @property
    def full_voiceover(self) -> str:
        parts = [self.hook] + [section.narration for section in self.sections]
        return "\n".join(part.strip() for part in parts if part.strip())

    @property
    def estimated_duration_seconds(self) -> float:
        char_count = len(re.sub(r"\s", "", self.full_voiceover))
        return round(char_count / CHARS_PER_SECOND, 1)


# auto 模式的凭据探测顺序（provider, 环境变量）。
_LLM_PROVIDER_ENVS = (
    ("openai", "OPENAI_API_KEY"),
    ("anthropic", "ANTHROPIC_API_KEY"),
    ("deepseek", "DEEPSEEK_API_KEY"),
)


def resolve_llm_provider(choice: str) -> str:
    """把 CLI 的 --provider 选项解析成实际 provider：auto 时选第一个已配置凭据的。"""
    if choice != "auto":
        return choice
    for provider, env_name in _LLM_PROVIDER_ENVS:
        if os.getenv(env_name, "").strip():
            return provider
    names = " / ".join(env for _, env in _LLM_PROVIDER_ENVS)
    raise RewriteError(f"未配置任何 LLM 凭据。请先设置 {names} 之一，再运行文案改写。")


def _resolve_style(style: "str | RewriteStyle") -> RewriteStyle:
    """把 str/RewriteStyle 归一成 RewriteStyle；未知 key 转成用户可见的 RewriteError。"""
    try:
        return get_style(style)
    except UnknownStyleError as exc:
        raise RewriteError(str(exc)) from exc


def build_rewrite_prompts(
    source_text: str,
    target_duration_seconds: int = DEFAULT_TARGET_DURATION,
    brief: str = "",
    style: "str | RewriteStyle" = DEFAULT_STYLE_KEY,
) -> tuple[str, str]:
    resolved = _resolve_style(style)
    target_chars = round(target_duration_seconds * CHARS_PER_SECOND)
    system_prompt = (
        f"{resolved.persona}基于给定的原始视频文案，重新创作一版原创口播稿。\n"
        "硬性要求：\n"
        "1. 不得照抄原文句子；开头钩子、叙事顺序、表达方式必须重写，只保留真实有效的信息点。\n"
        f"2. {resolved.tone}\n"
        f"3. {resolved.hook_guidance}\n"
        f"4. {resolved.section_guidance}\n"
        f"5. 全部口播文字总量控制在约 {target_chars} 字（对应 {target_duration_seconds} 秒口播，允许上下 10%）。\n"
        "6. 只输出一个 JSON 对象，不要输出任何其他文字或代码块标记。字段：\n"
        '{"hook": "前3秒钩子口播", '
        '"sections": [{"title": "小节标题", "narration": "该小节口播文案", "visual_hint": "画面/素材建议", '
        '"emphasis": [{"text": "弹出词/短句≤10字", "kind": "keyword|number|golden"}]}], '
        '"publish_titles": ["发布标题候选1", "候选2", "候选3"], '
        '"notes": "改写思路与合规提醒"}\n'
        f"7. sections 至少 {MIN_SECTIONS} 个，按叙事顺序排列。\n"
        "8. 每个 section 可选填 emphasis 数组（0~3 条/节），标注本节最值得屏幕弹字的内容：\n"
        "   keyword=动作/概念关键词（≤8字）、number=关键数字（带单位，如'3倍''5步'）、"
        "golden=金句精华（≤10字）。\n"
        "   没有值得强调的就省略 emphasis 字段或填空数组——宁缺勿滥，每节最多 3 条。"
    )
    # 用户自定义文风指令（全局生效）：压过内容类型模板的文风约定，
    # 但 JSON 输出格式硬性要求不可违反；单条 job 的 brief 优先级仍最高。
    custom_style = get_rewrite_style_prompt()
    if custom_style:
        system_prompt += (
            f"\n全局创作指令（用户自定义，与上述文风/口吻约定冲突时以此为准，"
            f"但第 6 条 JSON 输出格式必须严格遵守）：{custom_style}"
        )
    brief_block = (
        f"创作要求（必须遵守，与内容类型模板冲突时以此为准）：{brief.strip()}\n\n"
        if brief.strip()
        else ""
    )
    user_prompt = f"{brief_block}原始视频文案如下：\n{source_text.strip()}"
    return system_prompt, user_prompt


def parse_rewrite_response(raw_text: str) -> dict:
    """从 LLM 回复中提取 JSON。容忍代码块围栏和前后多余文字。"""
    text = raw_text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise RewriteError(f"LLM 回复中找不到 JSON 对象：{raw_text[:200]}")
    try:
        body = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise RewriteError(f"LLM 回复的 JSON 无法解析：{raw_text[:200]}") from exc
    if not isinstance(body, dict):
        raise RewriteError(f"LLM 回复的 JSON 不是对象：{raw_text[:200]}")

    hook = str(body.get("hook") or "").strip()
    sections = body.get("sections") or []
    if not hook:
        raise RewriteError("LLM 回复缺少 hook 字段。")
    if not isinstance(sections, list) or len(sections) < MIN_SECTIONS:
        raise RewriteError(f"LLM 回复的 sections 少于 {MIN_SECTIONS} 个。")
    for section in sections:
        if not isinstance(section, dict):
            raise RewriteError(f"LLM 回复的 sections 存在非法元素：{section!r}")
        if not str(section.get("narration") or "").strip():
            raise RewriteError("LLM 回复存在空口播的小节。")
    return body


_EMPHASIS_ALLOWED_KINDS = frozenset({"keyword", "number", "golden"})


def _parse_emphasis_items(raw) -> tuple[dict, ...]:
    """宽容解析单节 emphasis：缺失/格式错误直接返回空元组（向后兼容旧 rewrite.json）。"""
    if not isinstance(raw, list):
        return ()
    result: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()[:10]  # 截断到 10 字
        kind = str(item.get("kind") or "keyword").strip()
        if not text:
            continue
        if kind not in _EMPHASIS_ALLOWED_KINDS:
            kind = "keyword"
        result.append({"text": text, "kind": kind})
    return tuple(result[:3])  # 每节最多 3 条


def _result_from_body(
    body: dict,
    config: LLMConfig,
    target_duration_seconds: int,
    style_key: str,
    expand_rounds: int = 0,
) -> RewriteResult:
    sections = tuple(
        RewriteSection(
            index=index,
            title=str(item.get("title") or f"第{index + 1}节").strip(),
            narration=str(item.get("narration") or "").strip(),
            visual_hint=str(item.get("visual_hint") or "").strip(),
            emphasis=_parse_emphasis_items(item.get("emphasis")),
        )
        for index, item in enumerate(body["sections"])
    )
    titles = tuple(str(title).strip() for title in (body.get("publish_titles") or []) if str(title).strip())
    return RewriteResult(
        provider=config.provider,
        model=config.model or "(provider default)",
        target_duration_seconds=target_duration_seconds,
        hook=str(body["hook"]).strip(),
        sections=sections,
        publish_titles=titles,
        notes=str(body.get("notes") or "").strip(),
        style=style_key,
        expand_rounds=expand_rounds,
    )


def build_expand_prompts(result: RewriteResult, style: RewriteStyle) -> tuple[str, str]:
    """字数不足时的扩写提示词：保结构保风格，只把每节口播写厚到目标字数。"""
    target_chars = round(result.target_duration_seconds * CHARS_PER_SECOND)
    current_chars = len(re.sub(r"\s", "", result.full_voiceover))
    system_prompt = (
        f"{style.persona}你上一轮产出的口播稿总量只有约 {current_chars} 字，"
        f"远低于目标 {target_chars} 字（对应 {result.target_duration_seconds} 秒口播）。\n"
        "请在【不改变 hook、不增删小节、不改小节标题、不改叙事顺序、保持口吻】的前提下，"
        "把每个小节的 narration 扩写得更充实：补充具体细节、例子、数据或转折，"
        f"使全部口播文字总量达到约 {target_chars} 字（允许上下 10%）。\n"
        "只输出一个 JSON 对象，格式与输入完全相同，不要输出任何其他文字或代码块标记。"
    )
    payload = {
        "hook": result.hook,
        "sections": [
            {"title": s.title, "narration": s.narration, "visual_hint": s.visual_hint}
            for s in result.sections
        ],
        "publish_titles": list(result.publish_titles),
        "notes": result.notes,
    }
    user_prompt = json.dumps(payload, ensure_ascii=False)
    return system_prompt, user_prompt


def rewrite_copy(
    source_text: str,
    config: LLMConfig | None = None,
    target_duration_seconds: int = DEFAULT_TARGET_DURATION,
    brief: str = "",
    style: "str | RewriteStyle" = DEFAULT_STYLE_KEY,
) -> RewriteResult:
    if not source_text.strip():
        raise RewriteError("原始文案为空。请提供字幕文件、转写文本或直接粘贴文案。")
    config = config or LLMConfig()
    resolved_style = _resolve_style(style)
    system_prompt, user_prompt = build_rewrite_prompts(
        source_text, target_duration_seconds, brief, resolved_style
    )
    raw_reply = chat_completion(system_prompt, user_prompt, config)
    body = parse_rewrite_response(raw_reply)
    result = _result_from_body(body, config, target_duration_seconds, resolved_style.key)

    # 时长闭环第一级（字数强制）：LLM 普遍欠写且提示词约束不住，产出后必须检查。
    # 低于目标 85% 就回炉扩写（保结构只写厚），最多 MAX_EXPAND_ROUNDS 轮；
    # 扩写失败或越写越短则止损保留现有结果（宁短勿废，残差交给 assemble 的 atempo 微调）。
    for round_no in range(1, MAX_EXPAND_ROUNDS + 1):
        if result.estimated_duration_seconds >= target_duration_seconds * MIN_LENGTH_RATIO:
            break
        expand_system, expand_user = build_expand_prompts(result, resolved_style)
        try:
            raw = chat_completion(expand_system, expand_user, config)
            expanded_body = parse_rewrite_response(raw)
        except (LLMProviderError, RewriteError):
            break
        candidate = _result_from_body(
            expanded_body, config, target_duration_seconds, resolved_style.key, round_no
        )
        if candidate.estimated_duration_seconds <= result.estimated_duration_seconds:
            break
        result = candidate
    return result


def rewrite_result_to_dict(result: RewriteResult) -> dict:
    return {
        "version": "rewrite_v1",
        "provider": result.provider,
        "model": result.model,
        "style": result.style,
        "target_duration_seconds": result.target_duration_seconds,
        "estimated_duration_seconds": result.estimated_duration_seconds,
        "expand_rounds": result.expand_rounds,
        "hook": result.hook,
        "sections": [
            {
                "index": s.index,
                "title": s.title,
                "narration": s.narration,
                "visual_hint": s.visual_hint,
                # emphasis 是 tuple[dict]；显式转 list 保证 JSON 和 in-memory dict 格式一致。
                "emphasis": list(s.emphasis),
            }
            for s in result.sections
        ],
        "publish_titles": list(result.publish_titles),
        "notes": result.notes,
        "full_voiceover": result.full_voiceover,
    }


def write_rewrite_outputs(result: RewriteResult, output_dir: Path | str) -> dict[str, Path]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "rewrite.json"
    voiceover_path = output_dir / "voiceover.txt"
    json_path.write_text(
        json.dumps(rewrite_result_to_dict(result), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    voiceover_path.write_text(result.full_voiceover + "\n", encoding="utf-8")
    return {"rewrite_json": json_path, "voiceover_txt": voiceover_path}


_SRT_TIMESTAMP = re.compile(r"\d{1,2}:\d{2}:\d{2}[.,]\d{3}\s*-->")
_VTT_HEADER = re.compile(r"^(WEBVTT|NOTE|STYLE|REGION)\b")


def load_source_text(path: Path | str, asr_config: "ASRConfig | None" = None) -> str:
    """从 SRT/VTT/纯文本/transcript JSON/视频/音频 中提取口播文案。

    媒体文件（.mp4/.mov/... 音视频后缀）走 asr.transcribe_media 语音转写取文本；
    asr 的 import 放在此函数内，避免模块顶层引入 ASR 依赖链。
    """
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix in MEDIA_SUFFIXES:
        from video_factory import asr

        result = asr.transcribe_media(path, asr_config or asr.ASRConfig())
        return result.text.strip()
    # Windows 记事本等工具常存 UTF-8 with BOM，统一剥掉再进各分支。
    raw = path.read_text(encoding="utf-8", errors="replace").lstrip("﻿")
    if suffix == ".json":
        return _text_from_transcript_json(raw)
    if suffix in (".srt", ".vtt"):
        return _text_from_subtitles(raw)
    return raw.strip()


def _text_from_subtitles(raw: str) -> str:
    lines = []
    previous = ""
    for line in raw.splitlines():
        line = line.strip().lstrip("﻿")
        if not line or line.isdigit() or _SRT_TIMESTAMP.search(line) or _VTT_HEADER.match(line):
            continue
        if line == previous:  # 相邻重复行（滚动字幕常见）
            continue
        lines.append(line)
        previous = line
    return "\n".join(lines)


def _text_from_transcript_json(raw: str) -> str:
    try:
        body = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RewriteError("transcript JSON 无法解析。") from exc
    cues = body.get("cues") or []
    texts = [str((cue or {}).get("text") or "").strip() for cue in cues]
    return "\n".join(text for text in texts if text)


def _load_source_for_cli(args: argparse.Namespace) -> str:
    """CLI 分支：媒体源才构造 ASRConfig 并透传，其余走原有文本解析。"""
    if Path(args.source).suffix.lower() in MEDIA_SUFFIXES:
        from video_factory.asr import ASRConfig

        asr_config = ASRConfig(provider=args.asr_provider, model=args.asr_model)
        return load_source_text(args.source, asr_config)
    return load_source_text(args.source)


def _write_source_transcript(output_dir: Path | str, source_text: str) -> None:
    """把原文案落盘 source_transcript.txt（调 LLM 之前）。

    价值：①作为核查 AI 改写质量的对照物；②whisper 转写很贵，改写失败重跑时
    至少留下了转写成果，不必再转一遍。写盘失败不阻断主流程（宁缺勿废）。
    """
    try:
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        (out / "source_transcript.txt").write_text(source_text, encoding="utf-8")
    except OSError:
        pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m video_factory.rewrite",
        description="原片文案 → LLM 原创改写（输出 rewrite.json + voiceover.txt）",
    )
    source_group = parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument("--source", help="原始文案文件：.srt / .vtt / .txt / transcript .json")
    source_group.add_argument("--text", help="直接粘贴原始文案")
    parser.add_argument("--duration", type=int, default=DEFAULT_TARGET_DURATION, help="目标口播秒数（默认 90）")
    parser.add_argument("--brief", default="", help="创作要求：目标观众、重点、禁忌等")
    parser.add_argument(
        "--style",
        default=DEFAULT_STYLE_KEY,
        choices=sorted(STYLES),
        help="内容类型模板（决定人设/口吻/钩子/节结构），默认 general 通用",
    )
    parser.add_argument(
        "--provider",
        default="auto",
        choices=["auto", "openai", "anthropic", "deepseek"],
        help="auto=按已配置的凭据自动选择（openai→anthropic→deepseek）",
    )
    parser.add_argument(
        "--model",
        default="",
        help=f"留空则 openai 用 {OPENAI_DEFAULT_MODEL}、anthropic 用 {ANTHROPIC_DEFAULT_MODEL}、deepseek 用 deepseek-chat",
    )
    parser.add_argument("--output", default="video_factory/output/rewrite", help="输出目录")
    parser.add_argument(
        "--asr-provider",
        default="auto",
        choices=["auto", "faster_whisper", "openai"],
        help="仅当 --source 是视频/音频文件时生效",
    )
    parser.add_argument("--asr-model", default="", help="ASR 模型，仅媒体转写时生效")
    args = parser.parse_args(argv)
    # 补齐凭据：credentials.yaml → 空缺的环境变量（真实 env 优先）。必须在
    # resolve_llm_provider 之前，否则「服务启动后才配的 key」在本进程里不可见。
    credentials_store.ensure_env_loaded()

    try:
        source_text = _load_source_for_cli(args) if args.source else args.text
        # 调 LLM 之前先把原文案落盘：核查对照物 + 保住昂贵的转写成果（失败重跑不必再转）。
        _write_source_transcript(args.output, source_text)
        config = LLMConfig(provider=resolve_llm_provider(args.provider), model=args.model)
        result = rewrite_copy(
            source_text,
            config,
            target_duration_seconds=args.duration,
            brief=args.brief,
            style=args.style,
        )
        outputs = write_rewrite_outputs(result, args.output)
    except (RewriteError, LLMProviderError, OSError) as exc:
        print(f"改写失败：{exc}")
        stage_report.write_stage_error(args.output, "rewrite", f"改写失败：{exc}")
        return 1
    except RuntimeError as exc:  # 含 asr.ASRError（媒体转写失败）
        print(f"改写失败：{exc}")
        stage_report.write_stage_error(args.output, "rewrite", f"改写失败：{exc}")
        return 1
    used_style = get_style(result.style)
    expand_note = f"，触发扩写 {result.expand_rounds} 轮" if result.expand_rounds else ""
    print(f"改写完成：预计口播 {result.estimated_duration_seconds}s（目标 {args.duration}s{expand_note}）")
    print(f"- 内容类型: {used_style.label}（{used_style.key}）")
    print(f"- 配音建议: {used_style.tts_hint}")
    print(f"- 结构化文案: {outputs['rewrite_json']}")
    print(f"- 配音稿:     {outputs['voiceover_txt']}")
    for index, title in enumerate(result.publish_titles, start=1):
        print(f"- 标题候选{index}: {title}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
