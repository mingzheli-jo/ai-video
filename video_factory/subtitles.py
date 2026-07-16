"""逐句动态字幕（P7）：把原稿文本按配音时间轴烧成硬字幕。

推荐流水线顺序：rewrite → assemble → effects → subtitles。字幕放最后一步烧录，
确保它盖在特效之上、不被章节卡/片头等遮挡。

核心思路是**时间轴与文本分离**：
- 时间轴来自对**配音音频**的 ASR（faster-whisper，只取 start/end/text，不信任其文字）；
- 显示文本来自 rewrite.json 的 full_voiceover 原稿（零错别字）。
把原稿分句后，按字符占比映射到 ASR 给出的时间线，TTS 把 "30%" 念成 "百分之三十"
这类局部字符数不一致按比例分摊、漂移限制在单句内。

两种模式：
- align（默认优先）：用 ASR 时间轴对齐原稿句子；
- ratio（降级）：faster-whisper 不可用时，按字符占比把音频总时长分摊成时间轴，
  产物 JSON 里留痕 mode=ratio。
mode 参数 auto（默认，align 失败降 ratio）| align | ratio。

烧录用 ffmpeg libass subtitles 滤镜。为规避 Windows 路径转义坑，
烧录时 cwd=输出目录、滤镜参数写裸文件名 subtitles=subtitles.ass（文件名固定纯 ASCII）。
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from video_factory import credentials_store, stage_report, timeline as timeline_mod

Runner = Callable[..., subprocess.CompletedProcess]

DEFAULT_WIDTH = 1920
DEFAULT_HEIGHT = 1080

# .ass 文件名固定纯 ASCII，不给用户自定义——libass 滤镜参数写裸文件名才不踩 Windows 路径坑。
ASS_FILENAME = "subtitles.ass"
RELEASE_FILENAME = "release_subtitled.mp4"
REPORT_FILENAME = "subtitles_report.json"

# 单条字幕最长字数，超过按逗号二切、再不行硬切。
MAX_CUE_CHARS = 25
# 每条字幕最短显示时长（秒），太短会一闪而过看不清。
MIN_CUE_DURATION = 0.8
# 分句主分隔符（句末标点 + 换行）。
_SENTENCE_SPLIT_RE = re.compile(r"[。！？…；\n]+")
# 二切用逗号类分隔符。
# 在逗号「后面」零宽切分，逗号留在前半句——重拼碎片时内部逗号不丢（可读性）。
_COMMA_KEEP_RE = re.compile(r"(?<=[，、,])")
# 按逗号切分并丢弃逗号（字幕单行切割用；也规避 libass 全角逗号渲染残留 bug）。
_COMMA_SPLIT_RE = re.compile(r"[，、,]")
# 归一化 ASR 文本时剥离的标点（对齐只看字符占比，标点不计入时间权重）。
_PUNCT_RE = re.compile(r"[^\w一-鿿]+")

# 字号自适应：同时受高度(h*0.05)和宽度约束，封顶 96。竖屏(宽小)必须按宽度收字号，
# 否则一行放不下会溢出屏幕两侧（WrapStyle=0 会换行，但字号过大仍难看）。
_FONT_RATIO = 0.05
_FONT_SIZE_CAP = 96
# 左右安全边距占宽比例；配合按 _CHARS_PER_LINE_BUDGET 收字号，保证 MAX_CUE_CHARS 最多折 2 行。
_SIDE_MARGIN_RATIO = 0.06
_CHARS_PER_LINE_BUDGET = 13  # ceil(MAX_CUE_CHARS/2)，令满长字幕最多折成 2 行都在可用宽度内
# 底部安全边距：h*0.08 取整。
_MARGIN_RATIO = 0.08

# 英文副字幕（中上英下）：字号相对中文的比例、再缩一档的比例、以及拉丁字符平均宽度
# 相对字号的经验系数（proportional 字体约 0.5~0.6em，取 0.55 估算每行可容字符数）。
_EN_FONT_RATIO = 0.55
_EN_FONT_RATIO_SHRUNK = 0.45
_EN_CHAR_WIDTH_EM = 0.55
_EN_FONT_MIN = 12

# 字幕字号缩放系数（用户可调）：合法区间 [0.7, 1.5]，空/非法回落默认 1.0。
# 优先级：环境变量 SUBTITLE_FONT_SIZE > settings.yaml > 默认值（仿 image_gen.get_style_prompt）。
SUBTITLE_FONT_SIZE_ENV = "SUBTITLE_FONT_SIZE"
_SUBTITLE_FONT_SCALE_DEFAULT = 1.0
_SUBTITLE_FONT_SCALE_MIN = 0.7
# 上限 3.0（2026-07-15 用户点名从 1.5 放开）：大字号会顶着宽度约束自动折行，
# 观感由用户预览自行把关。
_SUBTITLE_FONT_SCALE_MAX = 3.0

# 字幕字体族（用户可调，白名单制）：非白名单/空值回落默认 Microsoft YaHei。
# 优先级：环境变量 SUBTITLE_FONT_NAME > settings.yaml > 默认值。
SUBTITLE_FONT_NAME_ENV = "SUBTITLE_FONT_NAME"
DEFAULT_SUBTITLE_FONT = "Microsoft YaHei"
SUBTITLE_FONT_OPTIONS = (
    "Microsoft YaHei",
    "SimHei",
    "Source Han Sans SC",
    "KaiTi",
    "SimSun",
)


def _read_setting(env_name: str) -> str:
    """按「环境变量 > settings.yaml」取一个白名单设置的原始字符串（缺失=空串）。
    惰性导入 settings_store 避免模块级环依赖。"""
    env_value = (os.getenv(env_name) or "").strip()
    if env_value:
        return env_value
    from video_factory.settings_store import load_settings

    return (load_settings().get(env_name) or "").strip()


def get_subtitle_font_scale() -> float:
    """当前生效的字幕字号缩放系数：环境变量 > settings.yaml > 默认 1.0。

    宽容策略：空值/非数字回落 1.0；数字但越界钳位到 [0.7, 1.5] 边界。
    """
    raw = _read_setting(SUBTITLE_FONT_SIZE_ENV)
    if not raw:
        return _SUBTITLE_FONT_SCALE_DEFAULT
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return _SUBTITLE_FONT_SCALE_DEFAULT
    if value != value:  # NaN 宽容回落
        return _SUBTITLE_FONT_SCALE_DEFAULT
    return max(_SUBTITLE_FONT_SCALE_MIN, min(_SUBTITLE_FONT_SCALE_MAX, value))


def get_subtitle_font_name() -> str:
    """当前生效的字幕字体族：环境变量 > settings.yaml > 默认 Microsoft YaHei。

    只认白名单 SUBTITLE_FONT_OPTIONS，非白名单/空值一律回落默认（写进 .ass 前的最后防线）。
    """
    raw = _read_setting(SUBTITLE_FONT_NAME_ENV)
    return raw if raw in SUBTITLE_FONT_OPTIONS else DEFAULT_SUBTITLE_FONT


class SubtitlesError(RuntimeError):
    pass


@dataclass(frozen=True)
class Cue:
    """一条字幕：显示区间 [start, end] 与文本。"""

    start: float
    end: float
    text: str


# --- 分句 ----------------------------------------------------------------


def split_sentences(text: str) -> list[str]:
    """把原稿切成适合单条字幕的短句。

    规则：先按句末标点/换行切；超过 MAX_CUE_CHARS 的长句按逗号二切；
    仍超长的按 MAX_CUE_CHARS 硬切。空文本报中文错误。
    """
    if not text or not text.strip():
        raise SubtitlesError("字幕文本为空，无法分句。请提供 rewrite 的 full_voiceover 或 --text。")
    sentences: list[str] = []
    for raw in _SENTENCE_SPLIT_RE.split(text):
        chunk = raw.strip()
        if not chunk:
            continue
        sentences.extend(_split_long(chunk))
    if not sentences:
        raise SubtitlesError("字幕文本只含标点或空白，无法分句。")
    return sentences


def _split_long(chunk: str) -> list[str]:
    """长句二切：先按逗号（逗号保留在前半，重拼时不丢内部标点），仍超长再按字数硬切。"""
    if len(chunk) <= MAX_CUE_CHARS:
        return [chunk]
    pieces: list[str] = []
    buffer = ""
    for part in _COMMA_KEEP_RE.split(chunk):
        if not part.strip():
            continue
        candidate = f"{buffer}{part}"
        if len(candidate) <= MAX_CUE_CHARS:
            buffer = candidate
        else:
            if buffer:
                pieces.append(buffer)
            buffer = part
    if buffer:
        pieces.append(buffer)
    # 每片去掉边界悬挂的逗号（分句末尾不需要）；无逗号仍超长的按字数硬切。
    result: list[str] = []
    for piece in pieces:
        piece = piece.strip().rstrip("，、,").strip()
        if piece:
            result.extend(_hard_wrap(piece))
    return result


def _hard_wrap(piece: str) -> list[str]:
    if len(piece) <= MAX_CUE_CHARS:
        return [piece]
    return [piece[i : i + MAX_CUE_CHARS] for i in range(0, len(piece), MAX_CUE_CHARS)]


# --- 时间轴：ratio 降级模式 ---------------------------------------------


def build_cue_timeline_ratio(sentences: list[str], total_duration: float) -> list[Cue]:
    """按字符占比把音频总时长分摊成时间轴（faster-whisper 不可用时的降级路径）。

    每句时长 = 总时长 * 本句字符数 / 全部字符数；单条不短于 MIN_CUE_DURATION。
    """
    if not sentences:
        raise SubtitlesError("没有句子可分配时间轴。")
    if total_duration <= 0:
        raise SubtitlesError(f"音频总时长非法（{total_duration}），无法分摊字幕时间轴。")
    lengths = [max(1, len(s)) for s in sentences]
    total_chars = sum(lengths)
    cues: list[Cue] = []
    cursor = 0.0
    for sentence, length in zip(sentences, lengths):
        span = total_duration * length / total_chars
        start = cursor
        end = min(total_duration, start + span)
        cues.append(Cue(start, end, sentence))
        cursor = end
    return _enforce_min_duration(cues, total_duration)


def _enforce_min_duration(cues: list[Cue], hard_cap: float) -> list[Cue]:
    """保证每条不短于 MIN_CUE_DURATION：太短就借用下一条起点后延；末条封在 hard_cap。"""
    fixed: list[Cue] = []
    for i, cue in enumerate(cues):
        start = cue.start
        end = cue.end
        if end - start < MIN_CUE_DURATION:
            end = start + MIN_CUE_DURATION
        # 不越过下一条起点（保持时间轴单调不重叠）；末条封在 hard_cap。
        if i + 1 < len(cues):
            end = min(end, cues[i + 1].start) if cues[i + 1].start > start else end
        else:
            end = min(end, hard_cap) if hard_cap > start else end
        fixed.append(Cue(round(start, 3), round(end, 3), cue.text))
    return fixed


# --- 时间轴：align 对齐模式 ---------------------------------------------


def build_cue_timeline_align(
    sentences: list[str],
    asr_segments: tuple[tuple[float, float, str], ...] | list[tuple[float, float, str]],
) -> list[Cue]:
    """用 ASR 分段的字符时间线对齐原稿句子。

    把每个 ASR 分段按归一化字符数展开成一条"字符时间线"（每个字符占一段等长时间），
    再把原稿句子的字符边界映射到这条时间线上取 start/end。

    映射用 SequenceMatcher 逐块锚定（2026-07-16 修"字幕跑着跑着对不上"）：旧实现按
    **全局**累计字符占比映射，TTS 把 "90%" 念成 "百分之九十" 这类字数膨胀会让映射
    误差跨句累积——中段漂到秒级、结尾又归零。改为在归一化后的原稿串与 ASR 串上做
    分块对齐：匹配块内一一对应，不匹配的空隙内按比例插值，误差被锁死在空隙局部，
    不再全片累积。ASR 空结果时抛错，交由上层降级到 ratio。
    """
    if not sentences:
        raise SubtitlesError("没有句子可对齐。")
    timeline = _char_timeline(asr_segments)
    if not timeline:
        raise SubtitlesError("ASR 未返回任何有效时间分段，无法 align 对齐。")
    total_asr_chars = len(timeline)

    # 原稿侧同款标点归一化（与 _char_timeline 对 ASR 的处理口径一致），
    # 记录每句在归一化大串里的 [start, end) 边界。
    norm_parts = [_PUNCT_RE.sub("", s) for s in sentences]
    boundaries: list[tuple[int, int]] = []
    cursor = 0
    for part in norm_parts:
        boundaries.append((cursor, cursor + len(part)))
        cursor += len(part)
    src_all = "".join(norm_parts)
    asr_all = "".join(
        _PUNCT_RE.sub("", str(text)) for _, _, text in asr_segments
    )
    index_of = _build_char_index_map(src_all, asr_all)

    cues: list[Cue] = []
    for sentence, (b_start, b_end) in zip(sentences, boundaries):
        start_idx = min(total_asr_chars - 1, index_of(b_start))
        end_idx = min(total_asr_chars - 1, max(start_idx, index_of(max(b_start, b_end - 1))))
        start = timeline[start_idx][0]
        end = timeline[end_idx][1]
        cues.append(Cue(start, end if end > start else start + MIN_CUE_DURATION, sentence))
    total_duration = timeline[-1][1]
    return _enforce_min_duration(_redistribute_collided_cues(cues), total_duration)


def _build_char_index_map(src: str, asr: str) -> Callable[[int], int]:
    """返回 原稿字符位 → ASR 字符位 的映射函数（SequenceMatcher 分块锚定）。

    匹配块内逐字符平移；块间空隙（TTS 数字展开、ASR 幻听/漏字）按空隙两端锚点
    线性插值。src/asr 任一为空时退化为全局比例映射（与旧行为一致）。
    """
    if not src or not asr:
        total_src = max(1, len(src))
        return lambda i: int(i / total_src * max(1, len(asr)))
    matcher = difflib.SequenceMatcher(None, src, asr, autojunk=False)
    # 锚点表：[(src_pos, asr_pos)]，含起终点哨兵，保证全域可插值。
    anchors: list[tuple[int, int]] = [(0, 0)]
    for block in matcher.get_matching_blocks():
        if block.size > 0:
            anchors.append((block.a, block.b))
            anchors.append((block.a + block.size, block.b + block.size))
    anchors.append((len(src), len(asr)))
    anchors.sort()

    def index_of(src_pos: int) -> int:
        # 找 src_pos 所在的锚点区间 [lo, hi)，区间内线性插值。
        lo_s, lo_a = anchors[0]
        for hi_s, hi_a in anchors[1:]:
            if src_pos < hi_s:
                span_s = hi_s - lo_s
                if span_s <= 0:
                    return lo_a
                ratio = (src_pos - lo_s) / span_s
                return lo_a + int(ratio * (hi_a - lo_a))
            lo_s, lo_a = hi_s, hi_a
        return len(asr) - 1 if asr else 0

    return index_of


def _redistribute_collided_cues(cues: list[Cue]) -> list[Cue]:
    """修复时间窗塌缩：ASR 字符数远少于原稿句数时，多句会映射到同一 (start, end)
    完全重叠（多条字幕同屏同窗）。把连续同窗的句子按字符占比在窗内均摊，恢复单调时间轴。"""
    fixed: list[Cue] = []
    i = 0
    while i < len(cues):
        j = i
        while (
            j + 1 < len(cues)
            and cues[j + 1].start == cues[i].start
            and cues[j + 1].end == cues[i].end
        ):
            j += 1
        if j == i:
            fixed.append(cues[i])
        else:
            group = cues[i : j + 1]
            span_start, span_end = group[0].start, group[0].end
            span = span_end - span_start
            total_chars = sum(max(1, len(cue.text)) for cue in group)
            cursor = span_start
            for cue in group:
                share = span * (max(1, len(cue.text)) / total_chars)
                fixed.append(Cue(round(cursor, 3), round(cursor + share, 3), cue.text))
                cursor += share
        i = j + 1
    return fixed


def _char_timeline(
    asr_segments: tuple[tuple[float, float, str], ...] | list[tuple[float, float, str]],
) -> list[tuple[float, float]]:
    """把 ASR 分段展开成逐字符时间区间列表（标点归一化后按字符数均分段内时长）。"""
    timeline: list[tuple[float, float]] = []
    for start, end, text in asr_segments:
        normalized = _PUNCT_RE.sub("", str(text))
        if not normalized:
            continue
        span = float(end) - float(start)
        if span <= 0:
            continue
        step = span / len(normalized)
        for i in range(len(normalized)):
            c_start = float(start) + step * i
            c_end = c_start + step
            timeline.append((c_start, c_end))
    return timeline


# --- ASS 渲染 ------------------------------------------------------------


def _compute_layout(width: int, height: int) -> dict:
    """按分辨率计算字幕排版参数（中文主字幕 + 英文副字幕共用）。"""
    side_margin = max(0, round(width * _SIDE_MARGIN_RATIO))
    usable_width = max(1, width - 2 * side_margin)
    # 字号取三者最小：高度自适应、宽度可容 _CHARS_PER_LINE_BUDGET 个字、绝对上限。
    width_font = usable_width // _CHARS_PER_LINE_BUDGET
    base_font = min(_FONT_SIZE_CAP, round(height * _FONT_RATIO), width_font)
    # 用户可调字号系数：乘在自适应基准字号上（默认 1.0 与旧行为完全一致）。
    # chars_per_line 也随缩放后的字号重算，字放大时自动少排字、不溢出宽度。
    font_size = max(1, round(base_font * get_subtitle_font_scale()))
    margin_v = max(0, round(height * _MARGIN_RATIO))
    return {
        "side_margin": side_margin,
        "usable_width": usable_width,
        "font_size": font_size,
        "margin_v": margin_v,
        # 竖屏窄、字大：一行能放的字数 = 可用宽度 // 字号。
        "chars_per_line": max(1, usable_width // font_size),
    }


def prepare_single_line_cues(cues: list[Cue], width: int, height: int) -> list[Cue]:
    """把 cues 预切成单行（不换行、不溢出）。切割幂等：已达标的 cue 原样返回，
    因此对同一批 cues 重复调用结果一致——翻译流程依赖这一点对齐中英条目。"""
    layout = _compute_layout(width, height)
    single: list[Cue] = []
    for cue in cues:
        single.extend(_split_cue_single_line(cue, layout["chars_per_line"]))
    return single


def render_ass(
    cues: list[Cue],
    width: int,
    height: int,
    english: list[str] | None = None,
    karaoke: bool = False,
) -> str:
    """把 cues 渲染成完整 .ass 文件文本（Microsoft YaHei、白字细黑描边、底部居中）。

    english 非空时输出双语：中文在上（主字号）、英文在下（小字号），逐条同时间窗。
    english 必须与**切成单行后**的 cues 一一对应（长度一致），否则抛错——调用方应先
    prepare_single_line_cues 再翻译再传入。

    karaoke=True（2026-07-16 用户拍板对标头部博主）：中文字幕逐字卡拉OK——每字一个
    \\kf 标签，句内时长按字数均分（句级起止已是 whisper 真实时间，句内均分误差可忽略），
    白字随语音逐字填充成黄。英文副字幕不参与。此函数默认关，由 generate_subtitles 默认开。
    """
    if not cues:
        raise SubtitlesError("没有字幕条目可渲染 ASS。")
    layout = _compute_layout(width, height)
    single: list[Cue] = []
    for cue in cues:
        single.extend(_split_cue_single_line(cue, layout["chars_per_line"]))
    if english is not None and len(english) != len(single):
        raise SubtitlesError(
            f"英文字幕条数（{len(english)}）与切行后的中文条数（{len(single)}）不一致。"
        )

    font_size = layout["font_size"]
    en_font, en_lines = (0, [])
    if english is not None:
        en_font, en_lines = _fit_english_lines(english, layout["usable_width"], font_size)
    # 垂直排布（Alignment=2，MarginV=距底边距离）：英文贴近底边，中文抬高让出英文行高。
    en_margin_v = layout["margin_v"] - max(0, round(en_font * 1.4)) if en_font else 0
    en_margin_v = max(8, en_margin_v)
    zh_margin_v = layout["margin_v"] if not en_font else en_margin_v + round(en_font * 1.6)

    header = _ass_header(
        width, height, font_size, zh_margin_v, layout["side_margin"], en_font, en_margin_v,
        karaoke=karaoke,
    )
    events: list[str] = []
    for i, cue in enumerate(single):
        events.append(_ass_dialogue(cue, karaoke=karaoke))
        if en_lines and en_lines[i]:
            events.append(_ass_dialogue_en(cue, en_lines[i]))
    return header + "\n".join(events) + "\n"


def _fit_english_lines(texts: list[str], usable_width: int, zh_font: int) -> tuple[int, list[str]]:
    """英文行适配：先按 0.55 倍中文字号排；最长行超出预算就整体缩一档字号；
    仍超长的行省略号截断（翻译提示词已要求精简，截断是最后防线）。"""
    en_font = max(_EN_FONT_MIN, round(zh_font * _EN_FONT_RATIO))
    budget = max(8, int(usable_width / (en_font * _EN_CHAR_WIDTH_EM)))
    longest = max((len(t) for t in texts), default=0)
    if longest > budget:
        shrunk = max(_EN_FONT_MIN, round(zh_font * _EN_FONT_RATIO_SHRUNK))
        if shrunk < en_font:
            en_font = shrunk
            budget = max(8, int(usable_width / (en_font * _EN_CHAR_WIDTH_EM)))
    fitted = [
        t if len(t) <= budget else (t[: max(1, budget - 1)].rstrip() + "…")
        for t in (s.strip() for s in texts)
    ]
    return en_font, fitted


def _edge_punct_stripped(text: str) -> str:
    """剥掉一段字幕首尾的标点与空白，避免行首/行尾悬挂逗号句号。"""
    return text.strip().strip("，、,。！？；;：: ")


def _chunk_text(text: str, limit: int) -> list[str]:
    """切成若干短语，每条都不含逗号：先按逗号切开（丢弃逗号），仍超 limit 的短语按字数
    均分硬切。**必须去掉内部逗号**——libass 对全角逗号「，」有渲染残留 bug：含逗号的 cue
    会让后续 cue 冒出前导逗号（已实测复现）。同时短语切分也更适合竖屏单行观看。"""
    if limit <= 0:
        return [text]
    pieces: list[str] = []
    for phrase in _COMMA_SPLIT_RE.split(text):
        phrase = phrase.strip()
        if not phrase:
            continue
        if len(phrase) <= limit:
            pieces.append(phrase)
            continue
        n = -(-len(phrase) // limit)  # 需要切成几片（向上取整）
        size = -(-len(phrase) // n)   # 每片大小（尽量均匀，避免留下 1~2 字的碎片）
        pieces.extend(phrase[i : i + size] for i in range(0, len(phrase), size))
    return pieces or [text.strip()]


def _split_cue_single_line(cue: Cue, limit: int) -> list[Cue]:
    """把一条 cue 切成若干单行 cue：文本按 limit 字切，时间轴按字符占比分摊，
    每段剥掉边缘标点。切完的每条都能在一行内显示（竖屏不换行）。"""
    chunks = _chunk_text(cue.text.strip(), limit)
    if len(chunks) <= 1:
        cleaned = _edge_punct_stripped(cue.text)
        return [Cue(cue.start, cue.end, cleaned)] if cleaned else []
    total = sum(len(c) for c in chunks) or 1
    span = cue.end - cue.start
    result: list[Cue] = []
    cursor = cue.start
    for i, chunk in enumerate(chunks):
        end = cue.end if i == len(chunks) - 1 else cursor + span * (len(chunk) / total)
        cleaned = _edge_punct_stripped(chunk)
        if cleaned:
            result.append(Cue(round(cursor, 3), round(end, 3), cleaned))
        cursor = end
    return result


def _ass_header(
    width: int,
    height: int,
    font_size: int,
    margin_v: int,
    side_margin: int,
    en_font: int = 0,
    en_margin_v: int = 0,
    karaoke: bool = False,
) -> str:
    # Alignment=2 底部居中。BorderStyle=1=白字+厚黑描边+轻阴影（用户点名去掉黑色底板；
    # 纯白无描边在亮背景会看不清，厚描边是可读性主力）。OutlineColour 是描边色
    # （不透明纯黑），BackColour 是阴影色（半透明黑）。描边/阴影随字号缩放。
    # 默认样式对标爆款博主：Bold=1 粗体白字 + 加厚黑描边（描边系数 0.10 约为旧值 2 倍）。
    outline = max(4, round(font_size * 0.10))
    shadow = max(1, round(font_size * 0.03))
    font_name = get_subtitle_font_name()
    # 卡拉OK填充语义（\kf）：字从 SecondaryColour 填成 PrimaryColour。开卡拉OK时
    # Primary=金黄（#f1c40f 的 BGR）作"唱到"色、Secondary=白作"未唱"色；
    # 关卡拉OK维持原样（Primary 白）。
    primary = "&H000FC4F1" if karaoke else "&H00FFFFFF"
    secondary = "&H00FFFFFF" if karaoke else "&H000000FF"
    styles = (
        f"Style: Default,{font_name},{font_size},{primary},{secondary},"
        f"&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,{outline},{shadow},2,"
        f"{side_margin},{side_margin},{margin_v},1\n"
    )
    if en_font > 0:
        en_outline = max(1, round(en_font * 0.05))
        en_shadow = max(1, round(en_font * 0.03))
        styles += (
            f"Style: EN,Arial,{en_font},&H00FFFFFF,&H000000FF,"
            f"&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,{en_outline},{en_shadow},2,"
            f"{side_margin},{side_margin},{en_margin_v},1\n"
        )
    return (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {width}\n"
        f"PlayResY: {height}\n"
        "WrapStyle: 0\n"
        "ScaledBorderAndShadow: yes\n"
        "\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        + styles +
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, "
        "Effect, Text\n"
    )


def _ass_dialogue(cue: Cue, karaoke: bool = False) -> str:
    # cue 已在 render_ass 里预切成单行，这里只做 ASS 转义（卡拉OK时逐字加 \kf 标签）。
    text = _karaoke_text(cue) if karaoke and cue.text else _escape_ass_text(cue.text)
    # 字段严格对齐 Format（Layer,Start,End,Style,Name,MarginL,MarginR,Effect,Text）：
    # Style 与 Text 之间只有 Name/MarginL/MarginR/Effect 四段 → ",,0,0,"。
    # 曾多写一个 margin 字段（",,0,0,0,,"），libass 把越界的逗号并入 Text，
    # 渲染出行首幻影逗号「,不关掉手机永远慢」。少一个都不行。
    return (
        f"Dialogue: 0,{_format_ass_timestamp(cue.start)},"
        f"{_format_ass_timestamp(cue.end)},Default,,0,0,,{text}"
    )


def _karaoke_text(cue: Cue) -> str:
    """逐字卡拉OK文本：每字一个 {\\kf厘秒} 标签，句内时长按字数均分。

    句级起止来自 whisper 真实时间轴，句内均分对中文口播误差可忽略（对标博主
    的逐字点亮观感）。余数厘秒摊给前几个字，保证总和等于句时长不漂移。
    """
    chars = list(cue.text)
    total_cs = max(len(chars), int(round((cue.end - cue.start) * 100)))
    base, extra = divmod(total_cs, len(chars))
    parts: list[str] = []
    for i, ch in enumerate(chars):
        span = base + (1 if i < extra else 0)
        parts.append(f"{{\\kf{span}}}{_escape_ass_text(ch)}")
    return "".join(parts)


def _ass_dialogue_en(cue: Cue, english: str) -> str:
    """英文副字幕事件：与对应中文 cue 同时间窗，走 EN 样式（小字号、更贴底边）。"""
    text = _escape_ass_text(english)
    return (
        f"Dialogue: 0,{_format_ass_timestamp(cue.start)},"
        f"{_format_ass_timestamp(cue.end)},EN,,0,0,,{text}"
    )


def _escape_ass_text(text: str) -> str:
    """转义会破坏 ASS 语法的字符：{} 是 override 块、反斜杠是转义引导、换行折成 \\N。"""
    text = text.replace("\\", "\\\\")
    text = text.replace("{", "\\{").replace("}", "\\}")
    text = text.replace("\r\n", "\\N").replace("\n", "\\N").replace("\r", "\\N")
    return text


def _format_ass_timestamp(seconds: float) -> str:
    """秒 → ASS 时间戳 H:MM:SS.cc（厘秒，两位）。"""
    if seconds < 0:
        seconds = 0.0
    centis = int(round(seconds * 100))
    hours, centis = divmod(centis, 360000)
    minutes, centis = divmod(centis, 6000)
    secs, centis = divmod(centis, 100)
    return f"{hours:d}:{minutes:02d}:{secs:02d}.{centis:02d}"


# --- ffprobe / libass 探测 ----------------------------------------------


def _probe_resolution(path: Path, runner: Runner) -> tuple[int, int]:
    """ffprobe 读 v:0 宽高。探测失败（无流/无 ffprobe/解析失败）返回 (0,0)。"""
    command = [
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height", "-print_format", "json", str(path),
    ]
    try:
        completed = runner(
            command, check=False, capture_output=True,
            text=True, encoding="utf-8", errors="replace",
        )
    except OSError:
        return (0, 0)
    if getattr(completed, "returncode", 0) != 0:
        return (0, 0)
    try:
        payload = json.loads(getattr(completed, "stdout", "") or "{}")
        streams = payload.get("streams") or []
        if not streams:
            return (0, 0)
        stream = streams[0]
        return (int(stream.get("width") or 0), int(stream.get("height") or 0))
    except (json.JSONDecodeError, TypeError, ValueError):
        return (0, 0)


def resolve_video_dimensions(video: Path, runner: Runner = subprocess.run) -> tuple[int, int, list[str]]:
    """探测底片宽高定字号；探测失败回落 1920x1080 并留痕。"""
    if not video.exists():
        return (DEFAULT_WIDTH, DEFAULT_HEIGHT, [f"底片不存在，字号按 {DEFAULT_WIDTH}x{DEFAULT_HEIGHT} 回落：{video}"])
    width, height = _probe_resolution(video, runner)
    if width <= 0 or height <= 0:
        return (DEFAULT_WIDTH, DEFAULT_HEIGHT, [f"底片分辨率探测失败，字号按 {DEFAULT_WIDTH}x{DEFAULT_HEIGHT} 回落：{video}"])
    return width, height, []


def ensure_libass(runner: Runner = subprocess.run) -> None:
    """探测 ffmpeg 是否带 libass 的 subtitles 滤镜，缺失抛中文 SubtitlesError。"""
    try:
        completed = runner(
            ["ffmpeg", "-hide_banner", "-filters"],
            check=False, capture_output=True,
            text=True, encoding="utf-8", errors="replace",
        )
    except OSError as exc:
        raise SubtitlesError(f"无法运行 ffmpeg 探测滤镜：{exc}。请确认 ffmpeg 已安装并在 PATH 中。") from exc
    output = getattr(completed, "stdout", "") or ""
    if " subtitles " not in output:
        raise SubtitlesError(
            "当前 ffmpeg 不含 libass 的 subtitles 滤镜，无法烧录字幕。"
            "请换用带 libass 的 ffmpeg 构建（如 gyan.dev essentials/full 版）。"
        )


# --- 烧录 ----------------------------------------------------------------


def _build_burn_command() -> list[str]:
    """libass 烧录命令：滤镜参数写裸文件名（配合 cwd=输出目录规避 Windows 路径坑）。"""
    return [
        "ffmpeg", "-y",
        "-i", RELEASE_FILENAME,
        "-vf", f"subtitles={ASS_FILENAME}",
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-c:a", "copy",
        # +faststart：把 moov 索引原子挪到文件头，浏览器才能边下边播（2026-07-15 修复
        # 页面播放"闪烁+播2秒就停+循环"——根因是 moov 默认在文件末尾、需下完整片才能起播）。
        "-movflags", "+faststart",
        RELEASE_FILENAME + ".sub.tmp.mp4",
    ]


def burn_subtitles(
    video: Path | str,
    ass_path: Path | str,
    output: Path | str,
    runner: Runner = subprocess.run,
) -> Path:
    """把 .ass 硬烧进视频。视频重编 libx264 crf 18，音轨 -c:a copy 直通。

    为规避 Windows 路径转义坑：cwd=输出目录、滤镜参数写裸文件名 subtitles=subtitles.ass。
    因此把输入视频与 .ass 先备到输出目录里以固定名参与命令，产物再改名到 output。
    """
    video = Path(video)
    ass_path = Path(ass_path)
    output = Path(output)
    if not video.exists():
        raise SubtitlesError(f"待烧录视频不存在：{video}")
    if not ass_path.exists():
        raise SubtitlesError(f"字幕文件不存在：{ass_path}")

    work_dir = output.parent
    work_dir.mkdir(parents=True, exist_ok=True)
    staged_video = work_dir / RELEASE_FILENAME
    staged_ass = work_dir / ASS_FILENAME
    _stage_copy(video, staged_video)
    _stage_copy(ass_path, staged_ass)

    command = _build_burn_command()
    tmp_output = work_dir / (RELEASE_FILENAME + ".sub.tmp.mp4")
    try:
        completed = runner(
            command, check=False, capture_output=True,
            text=True, encoding="utf-8", errors="replace",
            cwd=str(work_dir),
        )
    except OSError as exc:
        raise SubtitlesError(f"字幕烧录失败：无法启动 ffmpeg（{exc}）。") from exc
    if getattr(completed, "returncode", 0) != 0:
        stderr = (getattr(completed, "stderr", "") or "")[:300]
        raise SubtitlesError(f"字幕烧录失败（ffmpeg 退出码 {completed.returncode}）：{stderr}")
    if tmp_output.resolve() != output.resolve():
        tmp_output.replace(output)
    return output


def _stage_copy(src: Path, dst: Path) -> None:
    """把源文件内容备到目标固定名（同路径则跳过），供烧录命令以裸文件名引用。"""
    if src.resolve() == dst.resolve():
        return
    dst.write_bytes(src.read_bytes())


# --- 编排 ----------------------------------------------------------------


def _load_voiceover_text(rewrite_path: Path) -> str:
    try:
        body = json.loads(rewrite_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SubtitlesError(f"rewrite.json 不存在：{rewrite_path}") from exc
    except json.JSONDecodeError as exc:
        raise SubtitlesError(f"rewrite.json 无法解析：{rewrite_path}") from exc
    if not isinstance(body, dict):
        raise SubtitlesError(f"rewrite.json 内容不是 JSON 对象：{rewrite_path}")
    text = str(body.get("full_voiceover") or "").strip()
    if not text:
        raise SubtitlesError("rewrite.json 缺少 full_voiceover 文本，无法生成字幕。")
    return text


def _transcribe_for_timeline(
    audio: Path, runner: Runner
) -> tuple[tuple[float, float, str], ...]:
    """强制 faster_whisper 转写配音，只取时间轴分段。失败抛异常交由上层降级。"""
    from video_factory import asr  # 惰性导入：与 rewrite.py 一致，避免顶层耦合 ASR 依赖

    config = asr.ASRConfig(provider="faster_whisper")
    result = asr.transcribe_media(audio, config, runner)
    return result.segments


def _extract_audio(video: Path, wav_path: Path, runner: Runner) -> None:
    """从视频抽 16kHz 单声道 wav 供 ASR（视频已混 BGM 时时间轴会含背景音，建议显式传 --audio）。"""
    command = [
        "ffmpeg", "-i", str(video),
        "-vn", "-ac", "1", "-ar", "16000",
        "-f", "wav", "-y", str(wav_path),
    ]
    try:
        completed = runner(
            command, check=False, capture_output=True,
            text=True, encoding="utf-8", errors="replace",
        )
    except OSError as exc:
        raise SubtitlesError(f"从视频抽取音轨失败：无法启动 ffmpeg（{exc}）。") from exc
    if getattr(completed, "returncode", 0) != 0:
        stderr = (getattr(completed, "stderr", "") or "")[:300]
        raise SubtitlesError(f"从视频抽取音轨失败（ffmpeg 退出码 {completed.returncode}）：{stderr}")


def _probe_duration(path: Path, runner: Runner) -> float:
    command = [
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-print_format", "json", str(path),
    ]
    try:
        completed = runner(
            command, check=False, capture_output=True,
            text=True, encoding="utf-8", errors="replace",
        )
    except OSError:
        return 0.0
    if getattr(completed, "returncode", 0) != 0:
        return 0.0
    try:
        payload = json.loads(getattr(completed, "stdout", "") or "{}")
        return float((payload.get("format") or {}).get("duration") or 0.0)
    except (json.JSONDecodeError, TypeError, ValueError):
        return 0.0


def build_cues(
    sentences: list[str],
    mode: str,
    audio: Path | None,
    runner: Runner,
) -> tuple[list[Cue], str, list[str]]:
    """按 mode 生成 cues，返回 (cues, 实际使用的 mode, 警告列表)。

    auto：优先 align，任何失败降级 ratio 并留痕；align：失败即抛错；ratio：直接分摊。
    """
    warnings: list[str] = []
    normalized = (mode or "auto").strip().lower()
    if normalized not in ("auto", "align", "ratio"):
        raise SubtitlesError(f"不支持的 mode：{mode}（可选 auto / align / ratio）。")

    if normalized == "ratio":
        cues = build_cue_timeline_ratio(sentences, _audio_duration(audio, runner))
        return cues, "ratio", warnings

    if normalized == "align":
        segments = _transcribe_for_timeline(_require_audio(audio), runner)
        cues = build_cue_timeline_align(sentences, segments)
        return cues, "align", warnings

    # auto：先试 align，失败降 ratio。ASR 可能抛任意异常（缺依赖/模型失败/空结果），
    # 全部当作"align 不可用"降级；ratio 阶段再失败（无音频/时长为零）才真正报错。
    try:
        segments = _transcribe_for_timeline(_require_audio(audio), runner)
        cues = build_cue_timeline_align(sentences, segments)
        return cues, "align", warnings
    except Exception as exc:  # noqa: BLE001 - align 任何失败都要能降级到 ratio
        warnings.append(f"align 对齐失败，已降级为 ratio 分摊：{exc}")
        cues = build_cue_timeline_ratio(sentences, _audio_duration(audio, runner))
        return cues, "ratio", warnings


def _require_audio(audio: Path | None) -> Path:
    if audio is None:
        raise SubtitlesError("align 模式需要配音音频作时间轴来源，但未提供音频。")
    if not audio.exists():
        raise SubtitlesError(f"配音音频不存在：{audio}")
    return audio


def _audio_duration(audio: Path | None, runner: Runner) -> float:
    if audio is None or not audio.exists():
        raise SubtitlesError("ratio 模式需要音频总时长，但未提供有效音频。")
    duration = _probe_duration(audio, runner)
    if duration <= 0:
        raise SubtitlesError(f"音频总时长探测失败或为零：{audio}")
    return duration


# 分块翻译参数。一次塞上百条（如 115 条）时 LLM 合并/漏行概率显著升高（2026-07-14
# 真实事故：115 进 111 出，整批降级纯中文）；小块 + 块级重试把爆炸半径缩到单块。
_TRANSLATE_CHUNK_SIZE = 24
_TRANSLATE_ATTEMPTS = 2  # 每块最多尝试次数（首次 + 1 次重试）


def translate_texts_to_english(texts: list[str], char_budget: int) -> tuple[list[str], list[str]]:
    """把切好行的中文字幕分块翻成英文，返回 (英文行, 告警)。

    每块 ≤_TRANSLATE_CHUNK_SIZE 条、独立 LLM 调用；条数不匹配/解析失败重试一次，
    仍失败则**该块**回退空串（对应 cue 只显示中文）并留告警——绝不再因个别块失败
    把整片英文一起拖下水。无 LLM 凭据在进块前就抛异常，由调用方整体降级。
    惰性导入 llm/rewrite，避免顶层依赖。
    """
    if not texts:
        return [], []
    from video_factory.llm import LLMConfig, LLMProviderError, chat_completion
    from video_factory.rewrite import resolve_llm_provider

    provider = resolve_llm_provider("auto")
    system_prompt = (
        "你是短视频双语字幕翻译。把用户给出的 JSON 数组里的每条中文字幕翻成口语化英文。\n"
        "硬性要求：\n"
        "1. 输出一个 JSON 字符串数组，与输入等长、同序、逐条对应，绝不合并或拆分任何一条；\n"
        f"2. 每条尽量不超过 {char_budget} 个字符（含空格），宁可意译精简也不要长句；\n"
        "3. 只输出 JSON 数组本身，不要输出任何其他文字或代码块标记。"
    )
    lines: list[str] = []
    warnings: list[str] = []
    for start in range(0, len(texts), _TRANSLATE_CHUNK_SIZE):
        chunk = texts[start : start + _TRANSLATE_CHUNK_SIZE]
        translated: list[str] | None = None
        last_error = ""
        for _attempt in range(_TRANSLATE_ATTEMPTS):
            try:
                raw = chat_completion(
                    system_prompt,
                    json.dumps(chunk, ensure_ascii=False),
                    LLMConfig(provider=provider),
                )
                parsed = _parse_json_string_array(raw)
            except (SubtitlesError, LLMProviderError) as exc:
                last_error = str(exc)
                continue
            if len(parsed) == len(chunk):
                translated = parsed
                break
            last_error = f"期望 {len(chunk)} 条，LLM 返回 {len(parsed)} 条"
        if translated is None:
            chunk_no = start // _TRANSLATE_CHUNK_SIZE + 1
            warnings.append(
                f"英文字幕第 {chunk_no} 块（{len(chunk)} 条）翻译失败，该块回退纯中文：{last_error}"
            )
            translated = [""] * len(chunk)
        lines.extend(translated)
    return lines, warnings


def _parse_json_string_array(raw_text: str) -> list[str]:
    """从 LLM 回复中提取 JSON 字符串数组，容忍代码块围栏与前后多余文字。"""
    text = raw_text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end <= start:
        raise SubtitlesError(f"LLM 翻译回复中找不到 JSON 数组：{raw_text[:200]}")
    try:
        body = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise SubtitlesError(f"LLM 翻译回复的 JSON 无法解析：{raw_text[:200]}") from exc
    if not isinstance(body, list):
        raise SubtitlesError("LLM 翻译回复不是 JSON 数组。")
    return [str(item or "").strip() for item in body]


def _english_char_budget(width: int, height: int) -> int:
    """按排版参数估算英文每行字符预算（与 _fit_english_lines 的初始字号一致）。"""
    layout = _compute_layout(width, height)
    en_font = max(_EN_FONT_MIN, round(layout["font_size"] * _EN_FONT_RATIO))
    return max(8, int(layout["usable_width"] / (en_font * _EN_CHAR_WIDTH_EM)))


def generate_subtitles(
    video: Path | str,
    text: str,
    output_dir: Path | str,
    mode: str = "auto",
    audio: Path | str | None = None,
    runner: Runner | None = None,
    english: bool = False,
    timeline: list[dict] | None = None,
    karaoke: bool = True,
) -> dict:
    """编排：分句 → 定字号 → 生成时间轴 → （可选）翻译英文 → 写 .ass → 烧录 → 落 report。

    karaoke 默认开（2026-07-16 用户拍板对标头部博主）：字幕随语音逐字点亮成金黄；
    CLI --no-karaoke 可关。

    english=True 时输出中英双语（中上英下）；翻译任何失败（无 LLM 凭据/超时/条数不齐）
    都降级为纯中文并在 warnings 留痕，绝不因字幕翻译阻断成片。

    runner 缺省时取 subprocess.run（在函数体内取值，便于 CLI 场景 monkeypatch 顶层
    subprocess.run 也能生效——默认参数会在定义时绑定旧引用，故不用默认参数写死）。
    """
    runner = runner or subprocess.run
    video = Path(video)
    output_dir = Path(output_dir)
    audio_path = Path(audio) if audio else None
    if not video.exists():
        raise SubtitlesError(f"待烧录视频不存在：{video}")
    ensure_libass(runner)

    width, height, dim_warnings = resolve_video_dimensions(video, runner)
    warnings = list(dim_warnings)
    if timeline:
        # P16 主时间轴：直接由逐句真实起止构造 cues，跳过分句 + ASR（净成本为零——对齐
        # 已在 assemble 阶段做过，这里不再自跑 whisper）。后续单行切割/渲染/烧录管线不变。
        cues = [
            Cue(float(s.get("start") or 0.0), float(s.get("end") or 0.0), str(s.get("text") or ""))
            for s in timeline
        ]
        used_mode = "timeline"
    else:
        sentences = split_sentences(text)
        cues, used_mode, cue_warnings = build_cues(sentences, mode, audio_path, runner)
        warnings += cue_warnings

    # 双语：对切成单行后的最终 cue 逐条翻译（切割幂等，render_ass 内重切结果一致）。
    single = prepare_single_line_cues(cues, width, height)
    english_lines: list[str] | None = None
    if english:
        try:
            english_lines, translate_warnings = translate_texts_to_english(
                [cue.text for cue in single], _english_char_budget(width, height)
            )
            warnings.extend(translate_warnings)
            if english_lines and not any(english_lines):
                # 所有块都失败 → 等价于整体降级，别让全空英文行占排版位置。
                english_lines = None
        except Exception as exc:  # noqa: BLE001 - 翻译失败必须降级纯中文，不阻断成片
            warnings.append(f"英文字幕翻译失败，已降级为纯中文：{exc}")

    output_dir.mkdir(parents=True, exist_ok=True)
    ass_path = output_dir / ASS_FILENAME
    ass_path.write_text(
        render_ass(single, width, height, english=english_lines, karaoke=karaoke),
        encoding="utf-8",
    )

    release_path = output_dir / RELEASE_FILENAME
    burn_subtitles(video, ass_path, release_path, runner)

    if used_mode == "timeline":
        timeline_source = "主时间轴 timeline.json（P16，逐句真实起止，已跳过 ASR）"
    elif used_mode == "align":
        timeline_source = "配音音频 ASR（faster-whisper）"
    else:
        timeline_source = "按字符占比分摊音频总时长"
    report = {
        "version": "subtitles_report_v1",
        "mode": used_mode,
        "requested_mode": (mode or "auto").strip().lower(),
        "cue_count": len(single),
        "bilingual": english_lines is not None,
        "karaoke": karaoke,
        "timeline_source": timeline_source,
        "width": width,
        "height": height,
        "warnings": warnings,
        "ass": str(ass_path),
        "release": str(release_path),
    }
    (output_dir / REPORT_FILENAME).write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


# --- CLI -----------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m video_factory.subtitles",
        description="逐句动态字幕（P7）：把 rewrite 原稿按配音时间轴烧成硬字幕",
    )
    parser.add_argument("--video", required=True, help="要烧字幕的成片（effects 之后的 release）")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--rewrite", default="", help="rewrite.json 路径（取 full_voiceover 作字幕文本）")
    source.add_argument("--text", default="", help="直接给定字幕文本（与 --rewrite 二选一）")
    parser.add_argument(
        "--audio",
        default="",
        help="时间轴来源的纯净配音 wav（推荐 assemble --tts 产出的 voiceover.wav）；"
        "缺省时从 --video 抽音轨做 ASR——若视频已混 BGM，强烈建议显式传 --audio 用纯配音。",
    )
    parser.add_argument(
        "--timeline",
        default="",
        help="timeline.json 路径（P16 主时间轴，可选）：存在且可加载则字幕直接由逐句真实起止"
        "构造、跳过分句与 ASR；文件不存在/损坏时回落现有 --mode 全流程。",
    )
    parser.add_argument("--mode", default="auto", choices=["auto", "align", "ratio"], help="时间轴模式")
    parser.add_argument(
        "--english",
        dest="english",
        action="store_true",
        help="开启英文副字幕（中上英下双语，翻译失败自动降级纯中文）。"
        "2026-07-15 起默认关闭（用户决定不再需要英文），能力保留。",
    )
    parser.add_argument(
        "--no-english",
        dest="english",
        action="store_false",
        help="显式关闭英文副字幕（当前默认即关，此开关为兼容保留）",
    )
    parser.set_defaults(english=False)
    parser.add_argument(
        "--no-karaoke",
        dest="karaoke",
        action="store_false",
        help="关闭逐字卡拉OK点亮（默认开：字幕随语音逐字填充成金黄，对标头部博主）",
    )
    parser.set_defaults(karaoke=True)
    parser.add_argument("--output", default="video_factory/output/subtitles", help="输出目录")
    args = parser.parse_args(argv)
    # 补齐凭据（英文副字幕的 LLM 翻译等）：credentials.yaml → 空缺的环境变量。
    credentials_store.ensure_env_loaded()

    try:
        report = _run_cli(args)
    except (SubtitlesError, OSError) as exc:
        print(f"字幕生成失败：{exc}")
        stage_report.write_stage_error(args.output, "subtitles", f"字幕生成失败：{exc}")
        return 1

    print(f"字幕烧录完成：mode={report['mode']}，{report['cue_count']} 条")
    print(f"- 字幕:     {report['ass']}")
    print(f"- 成片:     {report['release']}")
    print(f"- 报告:     {Path(args.output) / REPORT_FILENAME}")
    return 0


def _run_cli(args: argparse.Namespace) -> dict:
    """CLI 主体：解析文本源、必要时抽临时音轨（用 with 管临时目录生命周期）。"""
    text = args.text.strip() if args.text else _load_voiceover_text(Path(args.rewrite))
    video = Path(args.video)
    # P16 主时间轴：存在且可加载则字幕直接消费它（跳过分句/ASR，也无需抽临时音轨）；
    # load 失败（None）自然回落下面的现有全流程。
    timeline = timeline_mod.load_timeline(args.timeline) if args.timeline else None
    if timeline:
        return generate_subtitles(
            video, text, Path(args.output), mode=args.mode, audio=None,
            english=args.english, timeline=timeline, karaoke=args.karaoke,
        )
    # 显式给了音频，或 ratio 模式且没给音频（会在下游报错）——都无需抽临时音轨。
    if args.audio or args.mode == "ratio":
        audio = Path(args.audio) if args.audio else None
        return generate_subtitles(
            video, text, Path(args.output), mode=args.mode, audio=audio,
            english=args.english, karaoke=args.karaoke,
        )
    # 未显式给音频、又需要 ASR 时间轴（auto/align）：从视频抽临时 wav，用完即删。
    with tempfile.TemporaryDirectory(prefix="vf_subtitles_") as tmp:
        wav_path = Path(tmp) / "audio.wav"
        _extract_audio(video, wav_path, subprocess.run)
        return generate_subtitles(
            video, text, Path(args.output), mode=args.mode, audio=wav_path,
            english=args.english, karaoke=args.karaoke,
        )


if __name__ == "__main__":
    raise SystemExit(main())
