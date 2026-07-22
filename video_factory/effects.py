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
import math
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from video_factory import stage_report, timeline as timeline_mod
from video_factory.sfx import (
    DEFAULT_SFX_VOLUME,
    SfxError,
    ensure_default_pack,
    resolve_sfx_file,
    resolve_sfx_path,
)

Runner = Callable[..., subprocess.CompletedProcess]

DEFAULT_FPS = 30
DEFAULT_WIDTH = 1920
DEFAULT_HEIGHT = 1080
DEFAULT_ACCENT = "#e8b84b"
# ffmpeg 全局静默参数（与 assemble 一致）：去横幅、只留真错误，放在 `ffmpeg -y` 之后。
_FF_QUIET = ("-hide_banner", "-loglevel", "error")

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
    "hook_opener": "HookOpener",
    "chapter_card": "ChapterCard",
    "lower_third": "LowerThird",
    "key_points": "KeyPoints",
    "quote_card": "QuoteCard",
    "number_pop": "NumberPop",
    "keyword_pop": "KeywordPop",
    # golden_card 全屏黑卡已退役（2026-07-15 用户拍板）：映射保留，仅为旧 manifest 重渲兼容。
    "golden_card": "GoldenCard",
    # 金句开屏三行式（黑卡退役后的形态）：复用 HookOpener 组件，不新建 TSX。
    "golden_lines": "HookOpener",
    # 荧光笔高亮 / 打字机引用（2026-07-16 用户拍板借鉴 Remotion 官网）。
    "highlight_sweep": "HighlightSweep",
    "typewriter_quote": "TypewriterQuote",
}

# 各 composition 的 durationInFrames 上限，与 remotion/src/Root.tsx 逐条同源。
# 契约：CLI --frames 越过上限时 Remotion 渲染直接失败、且失败会被吞成 warning
# （2026-07-16 金句卡 84 帧事故、2026-07-21 HookOpener 150 帧复用事故，同一病根）。
# 渲染前用本表钳制并响亮告警，让两侧漂移在日志里现形而不是产出静默缺特效的成片。
# 改 Root.tsx 的 durationInFrames 时必须同步改这里（有测试盯守两侧一致性）。
_MAX_FRAMES_BY_COMPOSITION = {
    "Intro": 75,
    "ChapterCard": 45,
    "LowerThird": 120,
    "KeyPoints": 100,
    "QuoteCard": 180,
    "NumberPop": 42,
    "KeywordPop": 48,
    "OpeningCard": 36,
    "GoldenCard": 72,
    "AmbientParticles": 240,
    "HighlightSweep": 96,
    "TypewriterQuote": 180,
    "HookOpener": 210,
}


def _clamped_frames(composition: str, duration: float, fps: int) -> tuple[int, str | None]:
    """按 composition 上限钳制渲染帧数；越界时返回告警文本（None = 未越界）。"""
    frames = max(1, round(float(duration or 0.0) * fps))
    ceiling = _MAX_FRAMES_BY_COMPOSITION.get(composition)
    if ceiling is None or frames <= ceiling:
        return frames, None
    return ceiling, (
        f"特效时长 {duration:.2f}s（{frames} 帧）超过 {composition} 的 durationInFrames "
        f"上限 {ceiling}，已钳制渲染——请同步抬高 remotion/src/Root.tsx 与 "
        f"_MAX_FRAMES_BY_COMPOSITION 的上限。"
    )

# 金句卡：放在约 55% 进度处；与章节卡时间窗撞车时顺延到该卡结束之后。
# 时长 2.8→4.5（2026-07-16 用户反馈"金句停留时间太短"：一句 15+ 字的观点要读完）。
# QUOTE_POSITION_RATIO 仅用于无 timeline 的旧链路（单张估算位金句卡）；有 timeline
# 时改为在真金句处沿片长铺多张（2026-07-22 用户定案，见 _select_quote_cards）。
QUOTE_POSITION_RATIO = 0.55
QUOTE_DURATION = 4.5
# 金句卡沿片长铺开（2026-07-22 用户定案："后续特效别只像开头三个，多来点居中金句卡"）。
# 只在 LLM 标记的真金句处出，间隔铺开不扎堆、封顶张数；其余金句仍由 sync 层做成
# 堆叠式（golden_lines）作节奏变化。过短/过长的口播句跳过（过长交给堆叠式多行呈现）。
QUOTE_CARD_MIN_GAP_S = 35.0     # 相邻金句卡最小间隔（秒）
QUOTE_CARD_MAX_COUNT = 5        # 全片金句卡张数上限
QUOTE_CARD_MIN_CHARS = 8        # 口播原句最短字数（太短没有"金句"分量）
QUOTE_CARD_MAX_CHARS = 44       # 口播原句最长字数（QuoteCard 两行放得下，再长留给堆叠式）
# 数字强调：每节口播里第一个关键数字，节起点后 0.8s 弹出，全片最多 2 个。
# NUMBER_POP_* 于 2026-07-21 用户定案随 number_pop 退役而删除。_NUMBER_RE 保留：
# 现在的用途反了过来——不再用于**派生**数字特效，而是用于**排除**数字文本
# 混进 keyword_pop（见 _derive_keyword_events）。
_NUMBER_RE = re.compile(
    r"\d+(?:\.\d+)?%|\d+(?:\.\d+)?(?:步|倍|年|个|条|招|天|分钟|小时|万|亿)"
)

# 关键词弹出：每节（跳过第 0 节 hook）在节内 40% 处弹一个关键词，1.6s，全片不设上限。
KEYWORD_POP_OFFSET_RATIO = 0.4
KEYWORD_POP_DURATION = 1.6
KEYWORD_MAX_CHARS = 8            # 引号内长词截断上限，太长弹不下
KEYWORD_TITLE_FALLBACK_CHARS = 6  # 无引号无数字时取节标题头 4-6 字兜底（取上限 6）
_KEYWORD_QUOTE_RE = re.compile(r"「([^」]+)」")

# 关键词弹出三色轮换（博主风格：红/黄/白，按全片 keyword_pop 序号循环）。
KEYWORD_POP_COLORS = ["#e74c3c", "#f1c40f", "#ffffff"]

# 荧光笔高亮（2026-07-16）：命中主时间轴的关键词事件在 keyword_pop（弹出=喊）与
# highlight_sweep（划重点=点）间隔条轮替；没命中的没有句子上下文，维持弹出。
HIGHLIGHT_SWEEP_DURATION = 2.0
HIGHLIGHT_CONTEXT_MAX_CHARS = 16  # 上下文行超此长度以关键词为中心裁窗（单行放得下）

# 打字机引用（2026-07-16）：金句文本形如「出处：正文」（出处 ≤6 字）时改走打字机
# 逐字呈现；时长随正文字数伸缩（打字要打得完），普通金句仍走 quote_card。
_TYPEWRITER_QUOTE_RE = re.compile(r"^([^：:，。！？\s]{1,6})[：:](.{4,})$")
TYPEWRITER_CHAR_SECONDS = 0.12   # 每字打字耗时（前 65% 时长打完，见组件）
TYPEWRITER_MIN_DURATION = QUOTE_DURATION
TYPEWRITER_MAX_DURATION = 4.5

# 氛围粒子（2026-07-16，默认关按选题开）：只渲染一小段无缝循环，ffmpeg -stream_loop
# 循环铺满全片——2 分钟正片只需 8s 的 ProRes 4444 粒子层，成本可控。
AMBIENT_LOOP_SECONDS = 8.0

# 首屏式同步时刻（2026-07-16 用户定案：中段特效文字必须与字幕语音对接上）：
# 行文本、逐行弹入时刻、时长全部取自 timeline 真句——和首屏同一套对接方式。
# 次数随片长动态（2026-07-18 用户定案密度大改：60s ≈10 个、可更多）：每 ~6s 一个
# 理想槽位，封顶 30；实际次数仍由 AI 导演式的候选质量决定——补填只收 6~16 字的
# 适配短句、避让窗（开屏/金句卡）照旧、最小间隔兜底，凑不齐就自然少出。
SYNC_MOMENT_EVERY_S = 6.0
SYNC_MOMENT_MAX_COUNT = 30
SYNC_MOMENT_MIN_GAP = 4.0        # 相邻时刻最小间隔（秒）
SYNC_RUN_MAX_LINES = 3           # 一个时刻最多叠 3 行（首屏同款）
SYNC_RUN_LINE_MAX_CHARS = 14     # 并入后续句的单行字数上限（长句自己成段更清晰）
SYNC_RUN_MAX_GAP = 0.8           # 相邻句并入的最大静默间隙（秒）
SYNC_RUN_MAX_SPAN = 6.0          # 并入后总口播跨度上限（秒）
SYNC_MOMENT_MAX_DURATION = 7.0   # 多句时刻的时长封顶
SYNC_FILL_MIN_CHARS = 6          # 补填候选句的最短字数（太短没内容感）
SYNC_FILL_MAX_CHARS = 16         # 补填候选句的最长字数（单行放得下）
# 密度控制阈值（秒）：相邻 keyword_pop 事件最小间隔 / 真空补填触发间隔。
DENSITY_MIN_GAP_S = 8.0    # 间隔 < 8s → 抽稀（删后出现的事件）
DENSITY_VACUUM_S = 20.0    # 间隔 > 20s → 补填一个规则抽取的 keyword_pop

# 开屏钩子序列（2026-07-15 用户点名，替代已退役的冷开场黑卡）：透明叠层在画面上、
# start=0，2~3 条 LLM 钩子短句逐条弹入。时长 min(5s, 首节×0.9)，避免盖过正片开头。
# 2026-07-16 修"开屏一闪而过"：单句短钩子会让首节只有 1~2s、开屏被压到 1.37s。
# 开屏是透明叠层、可越节展示，时长下限 2.5s；timeline 命中时以"盖住 hook 口播
# 终点 + 尾留白"为准（仍封顶 5s）。
HOOK_OPENER_MAX_DURATION = 5.0
HOOK_OPENER_MIN_DURATION = 2.5
HOOK_OPENER_TAIL = 1.0           # hook 口播说完后的尾部留白（秒）
HOOK_OPENER_FIRST_SECTION_RATIO = 0.9
HOOK_OPENER_MAX_LINES = 3        # 最多 3 行钩子短句

# 金句全屏卡（kind=golden emphasis）：时长与密度控制。GOLDEN_CARD_* 仍用于 golden_lines
# 无 timeline 时的回落时长与密度间隔（黑卡退役但密度规则沿用）。
GOLDEN_CARD_DURATION = 2.4       # 无 timeline 回落时的 golden_lines 时长（秒）
GOLDEN_CARD_MIN_GAP_S = 20.0     # 相邻金句最小间隔（秒）
# 金句开屏三行式（有 timeline 时）：时长 = 该句时长 + 尾留白，封顶 4.5s（金句要读完）。
GOLDEN_LINES_TAIL = 1.2          # 金句读完后的尾部留白（秒）
GOLDEN_LINES_MAX_DURATION = 4.5  # 金句开屏卡最长时长（秒）


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
    timeline: list[dict] | None = None,
) -> dict:
    """从 assembly_plan.json（可选叠加 rewrite）派生 effects_manifest_v1。

    纯函数，不触碰文件系统。时间一律取整到帧（round(t*fps)/fps），避免 overlay 时间轴漂移。
    timeline（P16 主时间轴逐句真实起止）可选：给了则 hook offsets/金句落点用真时间，
    没给则所有行为与现状完全一致（回落字符占比估算）。
    """
    sections = _sections_from_plan(assembly_plan)
    if not sections:
        raise EffectsError("assembly_plan 里没有可用的分节（sections 为空），无法派生特效清单。")

    starts = _section_starts(sections)
    effects: list[EffectSpec] = []

    # 开屏动效放最前（start=0）：rewrite 有 opening_hooks 时派生透明叠层的 hook_opener
    # （5 秒钩子序列），否则回落传统 intro（兼容旧 rewrite.json）。二者均占 [0, opener 时长]，
    # 不改底片时长。
    opener = _build_opener(sections[0], rewrite, fps, accent, timeline)
    effects.append(opener)

    # 章节卡已彻底退役（2026-07-15 用户点名去掉章节/序号「01」）：它按节标题机械插入、
    # 与关键词弹出内容重复（节标题≈关键词）、落点又不跟音频，是"特效文字乱掉"的一大来源。
    # 节奏改由随音频走的 keyword_pop/golden_lines 承担。lower_thirds 是独立可选功能，保留。
    if include_lower_thirds:
        for i, section in enumerate(sections):
            if i == 0:
                continue
            lt = _build_lower_third(section, i, starts[i], accent)
            if lt is not None:
                effects.append(_frame_aligned(lt, fps))

    total_duration = sum(_section_duration(s) for s in sections)
    effects.extend(
        _build_rich_effects(
            sections, rewrite, opener.start + opener.duration, total_duration, fps, accent,
            timeline,
        )
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
    first_section: dict, rewrite: dict | None, fps: int, accent: str, start: float = 0.0
) -> EffectSpec:
    first_duration = _section_duration(first_section)
    duration = min(INTRO_MAX_DURATION, first_duration * INTRO_FIRST_SECTION_RATIO)
    if duration <= 0:
        duration = INTRO_MAX_DURATION
    title = _intro_title(rewrite)
    return _frame_aligned(
        EffectSpec(
            type="intro",
            start=start,
            duration=duration,
            props={"title": title, "subtitle": "", "accent": accent},
        ),
        fps,
    )


# 开屏钩子分句：按子句标点切分（丢标点，问叹号例外见函数内）。
_HOOK_SPLIT_RE = re.compile(r"[，。！？…；、,!?;]+")
# 末句至少停留秒数：偏移钳位用，保证最后一句弹出后观众来得及看。
_HOOK_LAST_LINE_MIN_SHOW = 0.6


def _split_clause_lines(text: str, max_lines: int = HOOK_OPENER_MAX_LINES) -> list[str]:
    """把一段文本按子句标点切成 1~max_lines 行大字（开屏钩子/金句共用切法）。

    末句保留原文收尾的问/叹号（悬念/强调的语气）。空文本返回空列表。
    """
    text = (text or "").strip()
    if not text:
        return []
    pieces = [p.strip() for p in _HOOK_SPLIT_RE.split(text) if p.strip()]
    if pieces and text[-1] in "？?！!":
        pieces[-1] += text[-1]
    return pieces[:max_lines]


def _split_hook_lines(rewrite: dict | None) -> list[str]:
    """把 hook 口播原文按子句 1:1 切成开屏大字行（2026-07-15 用户定案）。

    此前用 LLM 另写的 opening_hooks：屏幕文字与耳朵听到的完全是两套内容（用户
    直感"字幕和解说没对上"），且 ≤12 字约束还产生截断残句。改为口播原文 1:1，
    所见即所听，两个问题一起消失。切法复用 _split_clause_lines（金句开屏卡同款）。
    """
    if not isinstance(rewrite, dict):
        return []
    return _split_clause_lines(str(rewrite.get("hook") or "").strip())


# 归一化匹配：剥离空白与标点后比对，容忍 ASR 断句与 emphasis/hook 文案的标点差异。
_NORMALIZE_RE = re.compile(r"[\s，。！？…；、,.!?;:：\"'“”‘’「」『』（）()【】\[\]\-—~·]+")


def _normalize_for_match(text: str) -> str:
    """归一化文本用于时间轴匹配：去空白与标点，只留实词字符。"""
    return _NORMALIZE_RE.sub("", str(text or ""))


def _char_ratio_offsets(lines: list[str], span: float, duration: float) -> list[float]:
    """按字符占比把各行弹入时刻分摊到 span 上（钳位保证末行至少停留 _HOOK_LAST_LINE_MIN_SHOW）。"""
    total_chars = sum(len(line) for line in lines) or 1
    span = span if span > 0 else duration
    offsets: list[float] = []
    consumed = 0
    for line in lines:
        raw_offset = consumed / total_chars * span
        offsets.append(round(min(raw_offset, max(0.0, duration - _HOOK_LAST_LINE_MIN_SHOW)), 3))
        consumed += len(line)
    return offsets


def _find_timeline_sentence(timeline: list[dict] | None, text: str) -> dict | None:
    """在 timeline 里找第一条（归一化后）包含 text 的句子；找不到返回 None。"""
    if not timeline:
        return None
    norm_text = _normalize_for_match(text)
    if not norm_text:
        return None
    for sentence in timeline:
        norm_sent = _normalize_for_match(sentence.get("text"))
        if norm_text in norm_sent:
            return sentence
    return None


# 强特征 token：数字短语（90%、5000、1506 等）。引号词由 _KEYWORD_QUOTE_RE 提取。
_TOKEN_NUM_RE = re.compile(r"\d+(?:\.\d+)?%?")


# 金句 emphasis 常是口播的**压缩转述**：字都在、顺序也对，但中间被抽词（"躺着收钱"
# ← "躺着就能收钱"、"资源比努力值钱" ← "资源比努力更值钱"）。连续子串匹配整句失手，
# 但按字符有序子序列 + 跨度足够紧凑仍能唯一锚定（2026-07-22 实测 studio_0722_095342：
# 4 句金句 3 句因此被丢，全片金句卡凑不齐）。跨度比 = 命中区间长 / emphasis 字数，
# 越接近 1 越像原句被小幅改写；放宽到 1.9 覆盖常见抽词，再松易误锚到散字长句。
_GOLDEN_SUBSEQ_MAX_SPAN_RATIO = 1.9


def _ordered_subseq_span(needle: str, hay: str) -> int | None:
    """needle 的字符是否按序（可不连续）出现在 hay 中；是则返回命中区间长度，否则 None。"""
    first = last = None
    ni = 0
    for hi, ch in enumerate(hay):
        if ni < len(needle) and ch == needle[ni]:
            if first is None:
                first = hi
            last = hi
            ni += 1
    if ni == len(needle) and first is not None:
        return last - first + 1
    return None


def _find_timeline_sentence_fuzzy(timeline: list[dict] | None, text: str) -> dict | None:
    """全文包含匹配失败时，依次用 (1) 数字/「」引号词二次锚定 (2) 有序子序列紧凑匹配。

    LLM 的 emphasis 常是口播的转述（"有句话说"vs"有句话讲"一字之差，2026-07-16
    实锤 studio_0716_113911 金句因此早了 5.7s）：全文匹配整句失手，但数字与引号词
    几乎不会被转述改写、全片唯一性也高。仅当 token 恰好命中一个句子才认。

    token 也不中时，退到有序子序列匹配（2026-07-22）：emphasis 字按序出现在某句里、
    且命中区间足够紧凑（跨度比 <= 1.9）即认。多句命中取最紧凑的一句——金句卡渲染
    的是锚点处的**口播整句**（非 emphasis 文本），锚点即便略偏也仍与当下语音同步，
    误锚顶多"金句卡落在不那么金的句子"，不会 desync，故可比 token 档略放宽。
    """
    sentence = _find_timeline_sentence(timeline, text)
    if sentence is not None or not timeline:
        return sentence
    tokens = _TOKEN_NUM_RE.findall(text or "") + _KEYWORD_QUOTE_RE.findall(text or "")
    for token in tokens:
        norm_token = _normalize_for_match(token)
        if not norm_token:
            continue
        hits = [
            s for s in timeline
            if norm_token in _normalize_for_match(s.get("text"))
        ]
        if len(hits) == 1:
            return hits[0]
    # 有序子序列紧凑匹配：取跨度比最小（最像原句）的一句，需通过紧凑度闸。
    norm_text = _normalize_for_match(text)
    if len(norm_text) >= 3:  # 太短的 emphasis 子序列易误命中，跳过
        best: dict | None = None
        best_ratio = _GOLDEN_SUBSEQ_MAX_SPAN_RATIO
        for s in timeline:
            span = _ordered_subseq_span(norm_text, _normalize_for_match(s.get("text")))
            if span is None:
                continue
            ratio = span / len(norm_text)
            if ratio < best_ratio:
                best, best_ratio = s, ratio
        if best is not None:
            return best
    return None


def _hook_offsets_from_timeline(
    lines: list[str], timeline: list[dict] | None, duration: float
) -> tuple[list[float], float] | None:
    """逐行在 timeline 上定位起点作 offset，相对开屏（start=0）。

    显示行按子句切（_split_hook_lines），比 timeline 的整句更细——多个显示行常落在
    同一句里。因此定位不是"一行配一句"，而是在句内按**行首字符位置**在该句
    [start,end] 区间插值：offset = 句起点 + (行首字符位 / 句字符数) × 句时长。
    这与字幕阶段（whisper / 比例）在句内定位的口径一致，大字与字幕天然对齐。

    2026-07-21 修（实测 studio_0721_213852）：旧版一行配一句、游标只进不退，两个
    显示行落在同一句时第二行在剩余句子里永远匹配不上 → 整体返回 None → 回落字符
    占比估算，逐行滞后越来越大（第一句准、后面越来越慢）。

    任一行定位失败返回 None（调用方整体回落字符占比估算）。钳位到
    [0, duration - 末行最短停留]。返回 (offsets, hook 口播终点秒)——终点供调用方把
    开屏时长拉到"盖住整段 hook 口播"（2026-07-16 修开屏一闪而过）。
    """
    if not timeline:
        return None
    offsets: list[float] = []
    speech_end = 0.0
    sent_idx = 0
    char_cursor = 0  # 当前句已消费到的归一化字符位置（同句多行时逐行右移，防重复命中）
    for line in lines:
        norm_line = _normalize_for_match(line)
        if not norm_line:
            return None
        located: tuple[int, int, str] | None = None
        while sent_idx < len(timeline):
            norm_sent = _normalize_for_match(timeline[sent_idx].get("text"))
            pos = norm_sent.find(norm_line, char_cursor)
            if pos != -1:
                located = (sent_idx, pos, norm_sent)
                break
            sent_idx += 1  # 本行不在此句剩余部分 → 进入下一句从头找
            char_cursor = 0
        if located is None:
            return None
        idx, pos, norm_sent = located
        sent = timeline[idx]
        s_start = float(sent.get("start") or 0.0)
        s_end = float(sent.get("end") or 0.0)
        speech_end = max(speech_end, s_end)
        # 句内按字符占比插值定位行首（与字幕阶段同口径）。
        frac = pos / max(1, len(norm_sent))
        raw = s_start + frac * max(0.0, s_end - s_start)
        offsets.append(round(min(max(0.0, raw), max(0.0, duration - _HOOK_LAST_LINE_MIN_SHOW)), 3))
        char_cursor = pos + len(norm_line)  # 下一行从本行之后找（同句继续 / 溢出则进下句）
    return offsets, speech_end


def _build_opener(
    first_section: dict,
    rewrite: dict | None,
    fps: int,
    accent: str,
    timeline: list[dict] | None = None,
) -> EffectSpec:
    """开屏动效：hook 可分句 → hook_opener（透明叠层，大字随口播逐句弹出）；
    否则回落传统 intro（旧/异常 rewrite.json 兼容）。"""
    lines = _split_hook_lines(rewrite)
    if lines:
        first_duration = _section_duration(first_section)
        duration = min(
            HOOK_OPENER_MAX_DURATION, first_duration * HOOK_OPENER_FIRST_SECTION_RATIO
        )
        if duration <= 0:
            duration = HOOK_OPENER_MAX_DURATION
        # 单句短钩子首节可能只有 1~2s：开屏是透明叠层、可越节展示，托底 2.5s
        # 不再一闪而过（2026-07-16 修复）。
        duration = max(duration, HOOK_OPENER_MIN_DURATION)
        # 有 timeline：逐行在真实句子起止时间上取 offset（不再靠字符占比估算），
        # 并把时长拉到"盖住 hook 口播终点 + 尾留白"（仍封顶 5s）。
        # 任一行没匹配上 → 整体回落字符占比估算（口播占满首节，近匀速，误差可忽略）。
        matched = _hook_offsets_from_timeline(lines, timeline, HOOK_OPENER_MAX_DURATION)
        if matched is not None:
            offsets, speech_end = matched
            duration = min(
                HOOK_OPENER_MAX_DURATION,
                max(duration, speech_end + HOOK_OPENER_TAIL),
            )
            # 用最终时长复钳位（匹配时用上限做的基准）。
            offsets = [
                round(min(o, max(0.0, duration - _HOOK_LAST_LINE_MIN_SHOW)), 3)
                for o in offsets
            ]
        else:
            speech_span = first_duration if first_duration > 0 else duration
            offsets = _char_ratio_offsets(lines, speech_span, duration)
        return _frame_aligned(
            EffectSpec(
                type="hook_opener",
                start=0.0,
                duration=duration,
                props={"lines": lines, "offsets": offsets, "accent": accent},
            ),
            fps,
        )
    return _build_intro(first_section, rewrite, fps, accent, start=0.0)


# 标题截断的标点边界：句末标点保留在标题里，子句标点本身丢弃。
_TITLE_SENTENCE_END = "？！。?!"
_TITLE_CLAUSE_BREAK = "，、；：,;:"


def _clip_title(text: str, max_chars: int = INTRO_TITLE_MAX_CHARS) -> str:
    """标题截断（防悬挂残字）：超预算时优先切到预算内最后一个标点边界。

    2026-07-14 用户红框反馈：hook「…有多牛？一句话…」被 [:12] 硬截成「…有多牛？一」，
    句尾挂着下一子句的首字「一」，观感像误加的横杠。改为：句末标点（？！。）切在其后
    保留语气；子句标点（，；：）切在其前丢标点；预算内无任何标点才退回硬截。
    """
    text = text.strip()
    if len(text) <= max_chars:
        return text
    head = text[:max_chars]
    for i in range(len(head) - 1, 0, -1):
        ch = head[i]
        if ch in _TITLE_SENTENCE_END:
            return head[: i + 1]
        if ch in _TITLE_CLAUSE_BREAK:
            return head[:i]
    return head


def _intro_title(rewrite: dict | None) -> str:
    if isinstance(rewrite, dict):
        hook = str(rewrite.get("hook") or "").strip()
        if hook:
            return _clip_title(hook)
        titles = rewrite.get("publish_titles")
        if isinstance(titles, list):
            for title in titles:
                text = str(title or "").strip()
                if text:
                    return _clip_title(text)
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


def _section_narration(section: dict, rewrite_sections: list, index: int) -> str:
    """取某节口播文本：优先计划节自带 narration，缺失则回退 rewrite 对应节
    （计划把 hook 计为第 0 节，故计划第 i 节对应 rewrite 第 i-1 节）。"""
    narration = str(section.get("narration") or "")
    if not narration and index - 1 < len(rewrite_sections) and isinstance(
        rewrite_sections[index - 1], dict
    ):
        narration = str(rewrite_sections[index - 1].get("narration") or "")
    return narration


def _extract_keyword(narration: str, title: str) -> str:
    """规则抽取本节关键词：「」引号内词 > 节标题头 4-6 字兜底。

    2026-07-21 摘除数字短语分支：number_pop 退役后，数字若还留在这里当兜底，
    就会改从 keyword_pop 冒出来，用户要取消的「50%」「3步」大字照样在画面上。
    """
    match = _KEYWORD_QUOTE_RE.search(narration or "")
    if match and match.group(1).strip():
        return match.group(1).strip()[:KEYWORD_MAX_CHARS]
    return (title or "").strip()[:KEYWORD_TITLE_FALLBACK_CHARS]


def _clip_context(sentence: str, keyword: str, max_chars: int = HIGHLIGHT_CONTEXT_MAX_CHARS) -> str:
    """荧光笔高亮的上下文行：取含关键词的句子，超长时以关键词为中心裁窗。

    裁剪保证关键词完整保留，被裁的一端补省略号；句中找不到关键词（标点/空白差异）
    或关键词本身超窗时，直接返回关键词（组件对整词扫高亮）。
    """
    sentence = str(sentence or "").strip()
    keyword = str(keyword or "").strip()
    at = sentence.find(keyword)
    if at < 0 or len(keyword) >= max_chars:
        return keyword
    if len(sentence) <= max_chars:
        return sentence
    pad = (max_chars - len(keyword)) // 2
    left = max(0, at - pad)
    right = min(len(sentence), left + max_chars)
    left = max(0, right - max_chars)
    prefix = "…" if left > 0 else ""
    suffix = "…" if right < len(sentence) else ""
    return f"{prefix}{sentence[left:right]}{suffix}"


def _derive_keyword_events(
    sections: list[dict],
    rewrite_sections: list,
    starts: list[float],
    timeline: list[dict] | None = None,
) -> list[tuple[float, str]]:
    """为每个非 hook 节生成候选 keyword_pop 落点列表 [(abs_time, text), ...]。

    - 有 emphasis（kind 为 keyword/number/golden）时按均匀分布取落点，最多 3 条/节。
    - 无 emphasis 时回落现有规则（节内 40% 处），与原逻辑完全一致（向后兼容）。
    """
    events: list[tuple[float, str]] = []
    for i, section in enumerate(sections):
        if i == 0:
            continue  # hook 节片头已充分覆盖，跳过
        sec_start = starts[i]
        sec_dur = _section_duration(section)

        # plan 第 i 节对应 rewrite 第 i-1 节（plan 把 hook 计为第 0 节）
        rw_idx = i - 1
        rw_section = (
            rewrite_sections[rw_idx]
            if isinstance(rewrite_sections, list)
            and rw_idx < len(rewrite_sections)
            and isinstance(rewrite_sections[rw_idx], dict)
            else None
        )

        # 只取 keyword 类 emphasis。golden 由 _derive_golden_events 处理；
        # number 类 2026-07-21 起一并排除——数字/百分比不再以任何形式成为文字特效
        # （与 number_pop 退役、_extract_keyword 摘除数字兜底是同一个决定的三处落点）。
        em_texts: list[str] = []
        if rw_section is not None:
            for em in (rw_section.get("emphasis") or []):
                if isinstance(em, dict):
                    kind = str(em.get("kind") or "keyword")
                    if kind in ("golden", "number"):
                        continue
                    text = str(em.get("text") or "").strip()
                    if text and not _NUMBER_RE.fullmatch(text.strip()):
                        em_texts.append(text)
        em_texts = em_texts[:3]

        if em_texts:
            # 落点优先用主时间轴：在 timeline 里找到真正说这个词的句子 → 用句子真实起点
            # （2026-07-15 修复"5000条"等弹词落到错误位置——根因是估算落点不跟音频）。
            # 找不到（词非口播原文子串）才回落节内均匀分布估算。
            n = len(em_texts)
            for idx, text in enumerate(em_texts):
                sentence = _find_timeline_sentence(timeline, text)
                if sentence is not None:
                    events.append((float(sentence.get("start") or 0.0), text))
                else:
                    offset = (idx + 1) / (n + 1) * sec_dur
                    events.append((sec_start + offset, text))
        else:
            # 回落原有规则：节内抽关键词；同样优先用 timeline 真实句起点。
            narration = _section_narration(section, rewrite_sections, i)
            keyword = _extract_keyword(narration, _section_title(section, i))
            if keyword:
                sentence = _find_timeline_sentence(timeline, keyword)
                if sentence is not None:
                    events.append((float(sentence.get("start") or 0.0), keyword))
                else:
                    events.append((sec_start + sec_dur * KEYWORD_POP_OFFSET_RATIO, keyword))

    return events


def _find_fill_event(
    target_time: float,
    sections: list[dict],
    rewrite_sections: list,
    starts: list[float],
    existing_events: list[tuple[float, str]],
) -> tuple[float, str] | None:
    """在 target_time 附近找一个还没有 keyword_pop 的非 hook 节，规则抽取关键词作真空填充。

    选取「40% 落点与 target_time 最近」且「该落点未被 existing_events 覆盖（容差 1s）」的节。
    """
    existing_times = [e[0] for e in existing_events]
    best_dist = float("inf")
    best_ev: tuple[float, str] | None = None

    for i, section in enumerate(sections):
        if i == 0:
            continue
        sec_start = starts[i]
        sec_dur = _section_duration(section)
        center = sec_start + sec_dur * KEYWORD_POP_OFFSET_RATIO
        # 跳过该节已有落点的（1s 容差）
        if any(abs(center - et) < 1.0 for et in existing_times):
            continue
        dist = abs(center - target_time)
        if dist < best_dist:
            narration = _section_narration(section, rewrite_sections, i)
            keyword = _extract_keyword(narration, _section_title(section, i))
            if keyword:
                best_dist = dist
                best_ev = (center, keyword)

    return best_ev


def _apply_density_control(
    events: list[tuple[float, str]],
    sections: list[dict],
    rewrite_sections: list,
    starts: list[float],
) -> list[tuple[float, str]]:
    """密度控制：抽稀过密事件（< DENSITY_MIN_GAP_S），并补填真空区间（> DENSITY_VACUUM_S）。

    抽稀规则：按时间升序，与前一保留事件间隔 < DENSITY_MIN_GAP_S 的事件被跳过。
    补填规则：单轮扫描 kept 中所有相邻对，> DENSITY_VACUUM_S 时在中点插入规则抽取的词。
    关键设计：补填时把整个 kept 列表（全部保留事件）一起传给 _find_fill_event，
    确保已被 kept 覆盖的节不会被重复选中，只有真正没有事件的节才被填入。
    """
    if not events:
        return events

    # 按时间升序排列
    events = sorted(events, key=lambda e: e[0])

    # Pass 1：抽稀（保留间隔 >= DENSITY_MIN_GAP_S 的事件）
    kept: list[tuple[float, str]] = [events[0]]
    for ev in events[1:]:
        if ev[0] - kept[-1][0] >= DENSITY_MIN_GAP_S:
            kept.append(ev)

    # Pass 2：单轮真空补填。
    # 把全部 kept 事件传给 _find_fill_event，让它跳过所有已被覆盖的节，
    # 只在没有任何事件的节里找填充点——避免填出与 kept 重复的位置。
    fills: list[tuple[float, str]] = []
    for i in range(1, len(kept)):
        prev_time = kept[i - 1][0]
        cur_time = kept[i][0]
        if cur_time - prev_time > DENSITY_VACUUM_S:
            mid = (prev_time + cur_time) / 2
            # 让 _find_fill_event 看到所有已有事件（kept + 本轮已收集的 fills）
            fill = _find_fill_event(mid, sections, rewrite_sections, starts, kept + fills)
            if fill is not None:
                fills.append(fill)

    # 合并 kept + fills，按时间升序
    return sorted(kept + fills, key=lambda e: e[0])


def _derive_golden_events(
    sections: list[dict],
    rewrite_sections: list,
    starts: list[float],
    timeline: list[dict] | None = None,
) -> list[tuple[float, str]]:
    """派生金句落点：仅收集 kind=golden 的 emphasis 条目，取时刻。

    有 timeline：命中含该金句的句子 → 用句子真实 start；否则回落节内均匀分布估算。
    与 _derive_keyword_events 镜像逻辑，但只处理 kind=golden；第 0 节（hook）始终跳过。
    """
    events: list[tuple[float, str]] = []
    for i, section in enumerate(sections):
        if i == 0:
            continue
        sec_start = starts[i]
        sec_dur = _section_duration(section)
        rw_idx = i - 1
        rw_section = (
            rewrite_sections[rw_idx]
            if isinstance(rewrite_sections, list)
            and rw_idx < len(rewrite_sections)
            and isinstance(rewrite_sections[rw_idx], dict)
            else None
        )
        if rw_section is None:
            continue
        golden_texts: list[str] = []
        for em in (rw_section.get("emphasis") or []):
            if isinstance(em, dict) and str(em.get("kind") or "") == "golden":
                text = str(em.get("text") or "").strip()
                if text:
                    golden_texts.append(text)
        golden_texts = golden_texts[:3]
        n = len(golden_texts)
        for idx, text in enumerate(golden_texts):
            # 转述容错：全文匹配 → 数字/引号词二次锚定（2026-07-16 修金句早 5.7s）。
            sentence = _find_timeline_sentence_fuzzy(timeline, text)
            if sentence is not None:
                events.append((float(sentence.get("start") or 0.0), text))
            elif not timeline:
                # 无主时间轴（旧链路）才允许均匀分布估算；有 timeline 却对不上的
                # 一律丢弃——估算落点的"突兀特效"已被用户多次点名（宁缺勿滥）。
                offset = (idx + 1) / (n + 1) * sec_dur
                events.append((sec_start + offset, text))
    return events


def _build_golden_lines(
    text: str,
    start: float,
    timeline: list[dict] | None,
    fps: int,
    accent: str,
) -> EffectSpec:
    """金句开屏三行式（golden_card 黑卡退役后的形态，复用 HookOpener 组件渲染）。

    有 timeline 命中：duration = min(该句时长 + 尾留白, 4.5)，行内 offsets 按字符占比
    分摊到句时长；无 timeline：回落 GOLDEN_CARD_DURATION 时长与同款分摊。
    命中时屏幕文字用**口播原句**而非 LLM 转述（2026-07-16：所见即所听，
    "有句话说"vs"有句话讲"的转述差异不再上屏）。
    """
    sentence = _find_timeline_sentence_fuzzy(timeline, text)
    if sentence is not None:
        span = max(0.0, float(sentence.get("end") or 0.0) - float(sentence.get("start") or 0.0))
        duration = min(span + GOLDEN_LINES_TAIL, GOLDEN_LINES_MAX_DURATION)
        if duration <= 0:
            duration = GOLDEN_CARD_DURATION
        spoken = str(sentence.get("text") or "").strip()
        if spoken:
            text = spoken
    else:
        span = GOLDEN_CARD_DURATION
        duration = GOLDEN_CARD_DURATION
    lines = _split_clause_lines(text) or [str(text).strip()]
    offsets = _char_ratio_offsets(lines, span, duration)
    return _frame_aligned(
        EffectSpec(
            type="golden_lines",
            start=start,
            duration=duration,
            props={"lines": lines, "offsets": offsets, "accent": accent},
        ),
        fps,
    )


def _apply_golden_density_control(
    events: list[tuple[float, str]],
    protected_windows: list[tuple[float, float]],
    total_duration: float,
) -> list[tuple[float, str]]:
    """密度控制：相邻 golden_card 间隔 >= GOLDEN_CARD_MIN_GAP_S；
    撞保护窗口（opening/intro/chapter_card）则顺延 0.5s 再检查，仍撞则丢弃（宁缺勿滥）。

    保护窗口格式：[(start, end), ...]，撞窗判断为 golden_card 时间窗 [t, t+DURATION]
    与保护窗口有任意交叠即算碰撞。
    """
    if not events:
        return events

    events = sorted(events, key=lambda e: e[0])

    def _overlaps_any(t: float) -> bool:
        """检查 [t, t+GOLDEN_CARD_DURATION] 是否与任意保护窗口重叠。"""
        end = t + GOLDEN_CARD_DURATION
        for ws, we in protected_windows:
            if t < we and end > ws:
                return True
        return False

    kept: list[tuple[float, str]] = []
    for time, text in events:
        # 超出视频长度则丢弃
        if time + GOLDEN_CARD_DURATION > total_duration:
            continue
        # 最小间隔检查：相邻 golden_card 保留先出现的
        if kept and time - kept[-1][0] < GOLDEN_CARD_MIN_GAP_S:
            continue
        # 保护窗口碰撞检查：顺延 0.5s 一次，仍撞则丢弃
        if _overlaps_any(time):
            time = time + 0.5
            if _overlaps_any(time):
                continue
        # 顺延后二次间隔检查（防御）
        if kept and time - kept[-1][0] < GOLDEN_CARD_MIN_GAP_S:
            continue
        kept.append((time, text))

    return kept


def _quote_card_spec(spoken: str, start: float, span: float, accent: str) -> EffectSpec:
    """把一句口播原句做成金句卡：「出处：正文」形态走打字机逐字，否则居中引号卡。

    时长口径与原单张金句卡一致：min(max(句时长+1.2, 4.5), 6.0)。
    """
    duration = min(max(span + 1.2, QUOTE_DURATION), TYPEWRITER_MAX_DURATION + 1.5)
    tw_match = _TYPEWRITER_QUOTE_RE.match(spoken)
    if tw_match is not None:
        return EffectSpec(
            type="typewriter_quote",
            start=start,
            duration=duration,
            props={"source": tw_match.group(1).strip(),
                   "text": tw_match.group(2).strip(), "accent": accent},
        )
    return EffectSpec(
        type="quote_card",
        start=start,
        duration=duration,
        props={"text": spoken, "accent": accent},
    )


def _select_quote_cards(
    golden_events: list[tuple[float, str]],
    timeline: list[dict],
    opener_end: float,
    total_duration: float,
    fps: int,
    accent: str,
) -> list[EffectSpec]:
    """在真金句处沿片长铺金句卡（2026-07-22 用户定案：不再全片仅 1 张）。

    仅取 LLM 标记的金句（golden_events），按时间贪心铺开：相邻间隔 >=
    QUOTE_CARD_MIN_GAP_S、封顶 QUOTE_CARD_MAX_COUNT。避让开场 hook，太靠尾（卡放
    不下）跳过；口播原句过短没分量、过长两行放不下也跳过（过长交给 sync 层堆叠式）。
    未被选中的金句仍会进 sync 层做成 golden_lines，形成"居中金句卡 + 堆叠式"的节奏变化。
    """
    specs: list[EffectSpec] = []
    last_start = float("-inf")
    for gstart, _text in sorted(golden_events, key=lambda e: e[0]):
        if len(specs) >= QUOTE_CARD_MAX_COUNT:
            break
        if gstart < opener_end:                       # 别压在开场 hook 上
            continue
        if gstart > total_duration - QUOTE_DURATION:  # 太靠尾，卡放不下
            continue
        if gstart - last_start < QUOTE_CARD_MIN_GAP_S:  # 间隔不足，跳过（贪心保先出）
            continue
        idx = _sentence_index_near(timeline, gstart)
        if idx is None:
            continue
        spoken = str(timeline[idx].get("text") or "").strip()
        if not (QUOTE_CARD_MIN_CHARS <= len(spoken) <= QUOTE_CARD_MAX_CHARS):
            continue
        span = max(0.0, float(timeline[idx].get("end") or 0.0) - gstart)
        specs.append(_frame_aligned(_quote_card_spec(spoken, gstart, span, accent), fps))
        last_start = gstart
    return specs


def _build_rich_effects(
    sections: list[dict],
    rewrite: dict | None,
    opener_end: float,
    total_duration: float,
    fps: int,
    accent: str,
    timeline: list[dict] | None = None,
) -> list[EffectSpec]:
    """"丰富化"特效派生（默认全开）：
    - 居中金句卡：有 timeline 时在 LLM 标记的真金句处沿片长铺多张（间隔 >= 35s、
      封顶 5 张，2026-07-22 用户定案；旧版仅取 55% 处 1 张）；无 timeline 时回落
      发布标题 + 估算位单张。
    - 关键词弹出：emphasis 均匀分布优先；无 emphasis 回落节内 40% 规则；密度控制+三色轮换。
    - 中段堆叠式（golden_lines）：未成金句卡的金句 + 关键词时刻由 sync 层派生，
      与居中金句卡交替形成节奏变化。

    已退役：开屏要点卡（2026-07-15）、数字强调 number_pop（2026-07-21，画面弹数字
    与口播重复且观感生硬）。
    """
    rich: list[EffectSpec] = []
    quote_windows: list[tuple[float, float]] = []
    rewrite_sections = (rewrite or {}).get("sections") or []
    starts = _section_starts(sections)
    # 金句 emphasis 候选提前派生：有 timeline 时金句卡要从中选锚（见下）。
    golden_events = _derive_golden_events(sections, rewrite_sections, starts, timeline)

    # 金句卡（2026-07-16 三次定案：它是全链路最后一个不锚音频的特效——55% 估算位 +
    # 从未被口播的发布标题，实锤 145315/162530 两支"很突兀"）。改为：
    # - 有 timeline：取**离视频中点最近的命中金句**，内容=口播原句、落点=真句起点，
    #   出卡瞬间说的就是卡上那句话；没有命中金句就不出卡（宁缺勿滥）。
    # - 无 timeline（旧链路）：维持发布标题 + 55% 估算位的原行为。
    if timeline:
        # 在真金句处沿片长铺多张居中金句卡（2026-07-22 用户定案，替代原"全片仅取
        # 离中点最近的 1 张"）。每张的时间窗登记进 quote_windows：sync 层据此避让，
        # 同一金句不会又在堆叠层重复出场。
        for spec in _select_quote_cards(
            golden_events, timeline, opener_end, total_duration, fps, accent
        ):
            rich.append(spec)
            quote_windows.append((spec.start, spec.start + spec.duration))
    else:
        quote_text = ""
        if isinstance(rewrite, dict):
            titles = rewrite.get("publish_titles")
            if isinstance(titles, list) and titles and str(titles[0] or "").strip():
                quote_text = str(titles[0]).strip()
            else:
                quote_text = str(rewrite.get("hook") or "").strip()
        if quote_text and total_duration > QUOTE_DURATION * 2:
            start = total_duration * QUOTE_POSITION_RATIO
            for i in range(1, len(sections)):  # 章节卡窗口 = [节起点, +CHAPTER_CARD_DURATION]
                card_start = starts[i]
                if card_start - QUOTE_DURATION < start < card_start + CHAPTER_CARD_DURATION:
                    start = card_start + CHAPTER_CARD_DURATION + 0.2
                    break
            # 「出处：正文」形态（王阳明：此心不动…）走打字机逐字呈现，
            # 时长随正文字数伸缩（前 65% 时长要打得完，见组件）。
            tw_match = _TYPEWRITER_QUOTE_RE.match(quote_text)
            if tw_match is not None:
                body = tw_match.group(2).strip()
                duration = min(
                    TYPEWRITER_MAX_DURATION,
                    max(TYPEWRITER_MIN_DURATION, TYPEWRITER_CHAR_SECONDS * len(body) / 0.65),
                )
                spec = EffectSpec(
                    type="typewriter_quote",
                    start=start,
                    duration=duration,
                    props={"source": tw_match.group(1).strip(), "text": body, "accent": accent},
                )
            else:
                spec = EffectSpec(
                    type="quote_card",
                    start=start,
                    duration=QUOTE_DURATION,
                    props={"text": quote_text, "accent": accent},
                )
            if start + spec.duration < total_duration:
                rich.append(_frame_aligned(spec, fps))
                quote_windows.append((rich[-1].start, rich[-1].start + rich[-1].duration))

    # 数字强调（number_pop）于 2026-07-21 用户定案退役：画面上额外弹「50%」「3步」
    # 这类数字/百分比大字，观感是硬贴上去的，与口播已经说出的信息重复。
    # 注意退役不是简单删派生：数字曾经通过 number_pop 与 keyword_pop 的去重
    # 相互排斥（"已弹过的数字不再作 keyword_pop"），只删这一段的话，那些数字会
    # 反过来**流回 keyword_pop** 继续以大字出现——所以 _extract_keyword 的数字
    # 兜底分支同步摘除，两处一起断，数字才真正不再成为文字特效。

    # 关键词弹出：emphasis 优先用真实句起点，无 emphasis 回落规则抽取，密度控制，三色轮换。
    keyword_events = _derive_keyword_events(sections, rewrite_sections, starts, timeline)
    keyword_events = _apply_density_control(keyword_events, sections, rewrite_sections, starts)

    # 中段时刻（2026-07-16 二次定案：**首屏式同步**）：
    # 实锤事故 studio_0716_125928——真空补填事件带着估算时间进最终循环，行文本取自
    # 69.68s 的真句、落点却用了 79.97s 的估算值，特效与字幕语音整整错开 10 秒。
    # 根治规则：凡进成片的中段时刻，**起点/行文本/逐行弹入/时长全部取自 timeline
    # 真句**（估算时间只用于候选排序，绝不落屏）；次数随片长 ceil(总长/35s) 动态
    # 控制在 1~6 次，候选不足时从 timeline 里挑适配短句补足。
    # golden_events 已在函数开头派生（金句卡选锚用同一份）；被金句卡选中的句子
    # 落在 quote_windows 里，sync 层自动避让不重复出场。
    if timeline:
        rich.extend(_build_sync_layer(
            timeline, golden_events, keyword_events,
            opener_end, quote_windows, total_duration, fps, accent,
        ))
    else:
        # 无主时间轴（旧链路兼容）：沿用金句估算 + 密度/撞窗控制；
        # 关键词时刻无从对接语音，一律不出。
        protected: list[tuple[float, float]] = [(0.0, opener_end)]
        for i in range(1, len(sections)):
            protected.append((starts[i], starts[i] + CHAPTER_CARD_DURATION))
        golden_events = _apply_golden_density_control(golden_events, protected, total_duration)
        for gtime, gtext in golden_events:
            rich.append(_build_golden_lines(gtext, gtime, timeline, fps, accent))

    return rich


def _sentence_index_near(timeline: list[dict], start: float, tol: float = 0.06) -> int | None:
    """按起点时间（容差内）反查句子下标；找不到返回 None。"""
    for i, s in enumerate(timeline):
        if abs(float(s.get("start") or 0.0) - start) <= tol:
            return i
    return None


def _build_sync_moment(
    timeline: list[dict], idx: int, fps: int, accent: str
) -> EffectSpec:
    """以第 idx 句为首，构造一个首屏式同步时刻（golden_lines）。

    多句成段：后续句子足够短（≤14 字）、紧邻（静默 <0.8s）、总跨度 ≤6s 时并入，
    最多 3 行；每行弹入时刻 = 该句真实起点（特效音逐行"刷"随之对齐）。
    首句过长或无可并句时退化为单句模式（子句拆行 + 字符占比 offsets）。
    """
    base = timeline[idx]
    start = float(base.get("start") or 0.0)
    base_text = str(base.get("text") or "").strip()
    run = [base]
    if len(base_text) <= SYNC_RUN_LINE_MAX_CHARS:
        j = idx + 1
        while len(run) < SYNC_RUN_MAX_LINES and j < len(timeline):
            nxt = timeline[j]
            text = str(nxt.get("text") or "").strip()
            if not text or len(text) > SYNC_RUN_LINE_MAX_CHARS:
                break
            if float(nxt.get("start") or 0.0) - float(run[-1].get("end") or 0.0) > SYNC_RUN_MAX_GAP:
                break
            if float(nxt.get("end") or 0.0) - start > SYNC_RUN_MAX_SPAN:
                break
            run.append(nxt)
            j += 1
    if len(run) == 1:
        return _build_golden_lines(base_text, start, timeline, fps, accent)
    lines = [str(s.get("text") or "").strip() for s in run]
    offsets = [round(float(s.get("start") or 0.0) - start, 3) for s in run]
    end = float(run[-1].get("end") or 0.0)
    duration = min(end - start + GOLDEN_LINES_TAIL, SYNC_MOMENT_MAX_DURATION)
    return _frame_aligned(
        EffectSpec(
            type="golden_lines",
            start=start,
            duration=duration,
            props={"lines": lines, "offsets": offsets, "accent": accent},
        ),
        fps,
    )


def _build_sync_layer(
    timeline: list[dict],
    golden_events: list[tuple[float, str]],
    keyword_events: list[tuple[float, str]],
    opener_end: float,
    quote_windows: list[tuple[float, float]],
    total_duration: float,
    fps: int,
    accent: str,
) -> list[EffectSpec]:
    """统一派生全部中段时刻：候选重锚真句 → 目标次数均匀选取/补填 → 成段产出。

    候选优先级：0=金句 emphasis（LLM 点名的记忆点）> 1=关键词事件 > 2=补填短句。
    关键词候选隔条轮替荧光笔高亮（也锚真句起点），其余全部是首屏式同步时刻。
    """
    avoid = [(0.0, opener_end)] + list(quote_windows)

    def blocked(t: float) -> bool:
        return any(ws - 1.0 <= t <= we + 1.0 for ws, we in avoid)

    # 候选收集：sentence 下标 → (优先级, 关键词)。金句先到先得，关键词不覆盖金句。
    candidates: dict[int, tuple[int, str | None]] = {}
    for gstart, _gtext in golden_events:
        gidx = _sentence_index_near(timeline, gstart)
        if gidx is not None and gidx not in candidates:
            candidates[gidx] = (0, None)
    for _kstart, keyword in keyword_events:
        sentence = _find_timeline_sentence(timeline, keyword)
        if sentence is None:
            continue  # 对不上口播的一律不出（估算时间绝不落屏）
        kidx = _sentence_index_near(timeline, float(sentence.get("start") or 0.0))
        if kidx is not None and kidx not in candidates:
            candidates[kidx] = (1, keyword)

    pool = [
        (idx, prio, kw) for idx, (prio, kw) in candidates.items()
        if not blocked(float(timeline[idx].get("start") or 0.0))
    ]
    target = max(1, min(SYNC_MOMENT_MAX_COUNT, math.ceil(total_duration / SYNC_MOMENT_EVERY_S)))

    # 超额：按均匀理想槽位就近选取；金句候选减 2s 加权（等距时金句赢）。
    if len(pool) > target:
        slots = [total_duration * (k + 0.5) / target for k in range(target)]
        chosen: list[tuple[int, int, str | None]] = []
        taken: set[int] = set()
        for slot in slots:
            best = None
            best_score = float("inf")
            for cand in pool:
                if cand[0] in taken:
                    continue
                t = float(timeline[cand[0]].get("start") or 0.0)
                score = abs(t - slot) - (2.0 if cand[1] == 0 else 0.0)
                if score < best_score:
                    best, best_score = cand, score
            if best is not None:
                taken.add(best[0])
                chosen.append(best)
        pool = chosen

    # 缺口：从 timeline 里补适配短句（6~16 字、不撞窗、与已选间隔 ≥8s）。
    if len(pool) < target:
        used = {idx for idx, _, _ in pool}
        picked_times = [float(timeline[idx].get("start") or 0.0) for idx, _, _ in pool]
        missing = target - len(pool)
        slots = [total_duration * (k + 0.5) / (missing + 1) for k in range(1, missing + 1)]
        for slot in slots:
            best_idx = None
            best_dist = float("inf")
            for i, s in enumerate(timeline):
                if i in used:
                    continue
                text = str(s.get("text") or "").strip()
                if not (SYNC_FILL_MIN_CHARS <= len(text) <= SYNC_FILL_MAX_CHARS):
                    continue
                t = float(s.get("start") or 0.0)
                if blocked(t):
                    continue
                if any(abs(t - pt) < SYNC_MOMENT_MIN_GAP for pt in picked_times):
                    continue
                if abs(t - slot) < best_dist:
                    best_idx, best_dist = i, abs(t - slot)
            if best_idx is not None:
                used.add(best_idx)
                picked_times.append(float(timeline[best_idx].get("start") or 0.0))
                pool.append((best_idx, 2, None))

    # 时间排序 + 最小间隔兜底（过近保留先出现的；金句优先已由选取阶段保证）。
    pool.sort(key=lambda c: float(timeline[c[0]].get("start") or 0.0))
    picked: list[tuple[int, int, str | None]] = []
    for cand in pool:
        t = float(timeline[cand[0]].get("start") or 0.0)
        if picked and t - float(timeline[picked[-1][0]].get("start") or 0.0) < SYNC_MOMENT_MIN_GAP:
            continue
        picked.append(cand)

    # 产出：关键词候选隔条轮替荧光笔（第 2、4…个），其余首屏式同步时刻。
    specs: list[EffectSpec] = []
    kw_ordinal = 0
    for idx, prio, keyword in picked:
        sentence = timeline[idx]
        s_start = float(sentence.get("start") or 0.0)
        if prio == 1 and keyword:
            kw_ordinal += 1
            if kw_ordinal % 2 == 0:
                span = max(0.0, float(sentence.get("end") or 0.0) - s_start)
                duration = min(max(span + 0.4, HIGHLIGHT_SWEEP_DURATION), 3.2)
                context = _clip_context(str(sentence.get("text") or ""), keyword)
                specs.append(_frame_aligned(
                    EffectSpec(
                        type="highlight_sweep",
                        start=s_start,
                        duration=duration,
                        props={"text": context, "keyword": keyword, "accent": accent},
                    ),
                    fps,
                ))
                continue
        specs.append(_build_sync_moment(timeline, idx, fps, accent))
    return specs


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
        frames, over_warning = _clamped_frames(
            composition, float(effect.get("duration") or 0.0), fps
        )
        if over_warning:
            warnings.append(f"第 {i} 条特效（{effect.get('type')}）：{over_warning}")
        command = _build_render_command(
            npx, entry, composition, props_path, out_path, frames, fps, width, height
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
    frames: int,
    fps: int,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
) -> list[str]:
    # frames 由调用方经 _clamped_frames 算好（含 composition 上限钳制），这里只拼命令。
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


def render_ambient_particles(
    output_dir: Path | str,
    fps: int = DEFAULT_FPS,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    accent: str = DEFAULT_ACCENT,
    runner: Runner = subprocess.run,
    remotion_dir: Path | str | None = None,
) -> Path | None:
    """渲染 AMBIENT_LOOP_SECONDS 的无缝循环氛围粒子层（ProRes 4444 带 alpha）。

    与特效清单是两套生命周期（粒子层循环铺满全片、不进 manifest）。npx 缺失或
    渲染失败都返回 None 并留痕——氛围粒子是锦上添花，绝不阻断成片。
    """
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    npx = shutil.which("npx")
    if not npx:
        _append_warnings(output_dir, ["未找到 npx，跳过氛围粒子层。"])
        return None
    project = Path(remotion_dir) if remotion_dir else _default_remotion_dir()
    entry = project / "src" / "index.ts"
    out_path = output_dir / "ambient_particles.mov"
    props_path = output_dir / "ambient_particles.props.json"
    props_path.write_text(json.dumps({"accent": accent}, ensure_ascii=False), encoding="utf-8")
    frames, over_warning = _clamped_frames("AmbientParticles", AMBIENT_LOOP_SECONDS, fps)
    if over_warning:
        _append_warnings(output_dir, [f"氛围粒子：{over_warning}"])
    command = _build_render_command(
        npx, entry, "AmbientParticles", props_path, out_path,
        frames, fps, width, height,
    )
    try:
        completed = runner(
            command, check=False, capture_output=True, text=True,
            encoding="utf-8", errors="replace", cwd=str(project),
        )
    except OSError as exc:
        _append_warnings(output_dir, [f"氛围粒子渲染无法启动 npx：{exc}"])
        return None
    if getattr(completed, "returncode", 0) != 0:
        stderr = (getattr(completed, "stderr", "") or "")[:300]
        _append_warnings(output_dir, [f"氛围粒子渲染失败（退出码 {completed.returncode}）：{stderr}"])
        return None
    return out_path


def overlay_ambient_loop(
    base_video: Path | str,
    particles: Path | str,
    output: Path | str,
    runner: Runner = subprocess.run,
) -> Path:
    """把无缝循环粒子层 -stream_loop 循环叠加到成片全程（shortest=1 以正片长度收尾）。"""
    base_video = Path(base_video)
    output = Path(output)
    if not base_video.exists():
        raise EffectsError(f"原片不存在：{base_video}")
    command = [
        "ffmpeg", "-y", *_FF_QUIET,
        "-i", str(base_video),
        "-stream_loop", "-1", "-i", str(particles),
        "-filter_complex",
        "[1:v]setpts=PTS-STARTPTS[p];[0:v][p]overlay=0:0:shortest=1[out]",
        "-map", "[out]", "-map", "0:a?", "-c:a", "copy",
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-movflags", "+faststart",
        str(output),
    ]
    _run(command, runner, context="氛围粒子叠加")
    return output


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
        *_FF_QUIET,
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
        # moov 挪到文件头，浏览器可边下边播（特效层是"关字幕时"的最终产物）。
        "-movflags",
        "+faststart",
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
        start = float(effect.get("start") or 0.0)
        offsets = effect.get("offsets")
        etype = str(effect.get("type"))
        if etype in ("hook_opener", "golden_lines") and isinstance(offsets, list) and offsets:
            # 逐行配音：开屏首行 whoosh 起势、后续每行一声 swoosh"刷"（2026-07-15 定案）；
            # 金句开屏卡每行都是 swoosh（2026-07-16 定案：whoosh 只留给真正的首屏，
            # 中段金句时刻与首屏同样式同节奏、但用第 2/3 声的"刷"）。
            for i, offset in enumerate(offsets):
                name = "whoosh.wav" if (etype == "hook_opener" and i == 0) else "swoosh.wav"
                line_path = resolve_sfx_file(name, sfx_dir)
                if line_path is not None:
                    try:
                        items.append((start + float(offset), line_path))
                    except (TypeError, ValueError):
                        continue  # 单个坏 offset 只丢这一声，不拖垮其余
            continue
        path = resolve_sfx_path(effect.get("type"), sfx_dir)
        if path is not None:
            items.append((start, path))
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


def _tail_stderr(text: str, max_lines: int = 12, max_chars: int = 300) -> str:
    """取 ffmpeg stderr 末尾若干非空行：真正的失败原因在结尾，取开头只会拿到噪声。
    再截断到 max_chars，避免个别无换行的超长行把错误信息撑爆。"""
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
        raise EffectsError(f"{context}失败：无法启动 ffmpeg（{exc}）。") from exc
    if getattr(completed, "returncode", 0) != 0:
        stderr = _tail_stderr(getattr(completed, "stderr", "") or "")
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
    parser.add_argument(
        "--timeline",
        default="",
        help="timeline.json 路径（P16 主时间轴，可选）：给了则 hook offsets/金句落点用真实句子"
        "起止时间；文件不存在或损坏自然回落字符占比估算。",
    )
    parser.add_argument("--output", default="video_factory/output/effects", help="输出目录")
    parser.add_argument("--lower-thirds", action="store_true", help="附加每节字幕条（lower_third）")
    parser.add_argument("--no-sfx", dest="sfx", action="store_false", help="关闭特效音（默认开：为片头/章节卡/花字条配音效）")
    parser.set_defaults(sfx=True)
    parser.add_argument("--sfx-volume", type=float, default=DEFAULT_SFX_VOLUME, help=f"特效音音量 0~1（默认 {DEFAULT_SFX_VOLUME}）")
    parser.add_argument(
        "--ambient-particles",
        action="store_true",
        help="叠加全片低密度金色氛围粒子（默认关，按选题开；渲染 8s 无缝循环层后 ffmpeg 循环铺满）",
    )
    parser.add_argument("--skip-render", action="store_true", help="只产出 manifest，不调 remotion 渲染")
    args = parser.parse_args(argv)

    output_dir = Path(args.output)
    try:
        plan = _load_json(Path(args.plan), "assembly_plan.json")
        rewrite = _load_json(Path(args.rewrite), "rewrite.json") if args.rewrite else None
        # 先探测底片分辨率喂给 manifest：特效片段必须与底片同尺寸，overlay 才不会静默
        # 错位/裁剪。探测失败回落 1920x1080 并留痕（不阻断）。
        width, height, dim_warnings = _resolve_video_dimensions(Path(args.video))
        # 主时间轴（P16）：给了 --timeline 且能加载则 hook/金句用真实句子时间；否则 None 回落估算。
        timeline = timeline_mod.load_timeline(args.timeline) if args.timeline else None
        manifest = build_effects_manifest(
            plan,
            rewrite,
            width=width,
            height=height,
            include_lower_thirds=args.lower_thirds,
            timeline=timeline,
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
                    # 钩子序列逐行音效需要各行弹入时刻（无该字段的类型为 None，自动走单声路径）。
                    "offsets": manifest["effects"][index].get("offsets"),
                }
                for index, path in rendered
            ],
            release,
            sfx_enabled=args.sfx,
            sfx_volume=args.sfx_volume,
        )

        # 氛围粒子（可选，默认关）：渲染 8s 无缝循环层后循环叠满全片。
        # 任一步失败只留痕不阻断——粒子是锦上添花，成片保持无粒子版本。
        if args.ambient_particles:
            particles = render_ambient_particles(
                output_dir,
                fps=int(manifest.get("fps") or DEFAULT_FPS),
                width=int(manifest.get("width") or DEFAULT_WIDTH),
                height=int(manifest.get("height") or DEFAULT_HEIGHT),
                accent=str(manifest.get("accent") or DEFAULT_ACCENT),
            )
            if particles is not None:
                try:
                    ambient_out = output_dir / "release_with_effects_ambient.mp4"
                    overlay_ambient_loop(release, particles, ambient_out)
                    ambient_out.replace(release)
                except (EffectsError, OSError) as exc:
                    _append_warnings(output_dir, [f"氛围粒子叠加失败，保持无粒子成片：{exc}"])
    except (EffectsError, OSError) as exc:
        print(f"特效层失败：{exc}")
        stage_report.write_stage_error(args.output, "effects", f"特效层失败：{exc}")
        return 1

    print(f"特效层完成：渲染 {len(rendered)} 条特效")
    print(f"- 清单:     {manifest_path}")
    print(f"- 成片:     {release}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
