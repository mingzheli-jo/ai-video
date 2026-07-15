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

from video_factory import stage_report
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
    "chapter_card": "ChapterCard",
    "lower_third": "LowerThird",
    "key_points": "KeyPoints",
    "quote_card": "QuoteCard",
    "number_pop": "NumberPop",
    "keyword_pop": "KeywordPop",
    "opening_card": "OpeningCard",
    "golden_card": "GoldenCard",
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

# 关键词弹出：每节（跳过第 0 节 hook）在节内 40% 处弹一个关键词，1.6s，全片不设上限。
KEYWORD_POP_OFFSET_RATIO = 0.4
KEYWORD_POP_DURATION = 1.6
KEYWORD_MAX_CHARS = 8            # 引号内长词截断上限，太长弹不下
KEYWORD_TITLE_FALLBACK_CHARS = 6  # 无引号无数字时取节标题头 4-6 字兜底（取上限 6）
_KEYWORD_QUOTE_RE = re.compile(r"「([^」]+)」")

# 关键词弹出三色轮换（博主风格：红/黄/白，按全片 keyword_pop 序号循环）。
KEYWORD_POP_COLORS = ["#e74c3c", "#f1c40f", "#ffffff"]
# 密度控制阈值（秒）：相邻 keyword_pop 事件最小间隔 / 真空补填触发间隔。
DENSITY_MIN_GAP_S = 8.0    # 间隔 < 8s → 抽稀（删后出现的事件）
DENSITY_VACUUM_S = 20.0    # 间隔 > 20s → 补填一个规则抽取的 keyword_pop

# 冷开场卡（复刻对标博主）：视频最开头 1.2s 全屏黑卡，盖住底片开头但不改底片时长。
OPENING_CARD_DURATION = 1.2
OPENING_TITLE_MAX_CHARS = 8
OPENING_POINT_LINES = 2          # 下方要点取第 1、2 节 title
# 开场卡结尾硬切（2026-07-15 用户点名，对标博主同款）：intro 紧接开场卡结束，无交叠淡入。
INTRO_START_AFTER_OPENING = OPENING_CARD_DURATION

# 金句全屏卡（kind=golden emphasis 升级形态）：时长与密度控制。
GOLDEN_CARD_DURATION = 2.4       # 全屏卡时长（秒，比开场卡更充裕，金句要读完）
GOLDEN_CARD_MIN_GAP_S = 20.0     # 相邻 golden_card 最小间隔（秒）


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

    # 冷开场卡放最前（仅当有 publish_titles[0] 主题词时）。它是盖住底片开头 1.2s 的不透明
    # 黑底 overlay（start=0），不改底片时长；有它时 intro 后移到 1.0s 与开场卡尾部交叠淡入。
    opening = _build_opening_card(rewrite, sections, fps, accent)
    if opening is not None:
        effects.append(opening)
    intro_start = INTRO_START_AFTER_OPENING if opening is not None else 0.0
    intro = _build_intro(sections[0], rewrite, fps, accent, start=intro_start)
    effects.append(intro)

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
    # 富化特效的开屏要点卡紧接 intro 结束落点（intro 后移后要点卡随之顺延，避免与片头重叠）。
    effects.extend(
        _build_rich_effects(
            sections, rewrite, intro.start + intro.duration, total_duration, fps, accent
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


def _opening_theme(rewrite: dict | None) -> str:
    """开场卡主题词：取 rewrite.publish_titles[0] 截 8 字（无则返回空串=不出开场卡）。"""
    if isinstance(rewrite, dict):
        titles = rewrite.get("publish_titles")
        if isinstance(titles, list) and titles and str(titles[0] or "").strip():
            return _clip_title(str(titles[0]).strip(), OPENING_TITLE_MAX_CHARS)
    return ""


def _build_opening_card(
    rewrite: dict | None, sections: list[dict], fps: int, accent: str
) -> EffectSpec | None:
    """冷开场卡：仅当有 publish_titles[0] 主题词时出现。全屏黑底大字（红描边由组件出）+
    分隔线 + 两行小字要点（取第 1、2 节 title）。start=0、盖住底片开头 1.2s。"""
    theme = _opening_theme(rewrite)
    if not theme:
        return None
    points = [_section_title(s, i) for i, s in enumerate(sections) if i >= 1][:OPENING_POINT_LINES]
    return _frame_aligned(
        EffectSpec(
            type="opening_card",
            start=0.0,
            duration=OPENING_CARD_DURATION,
            props={"title": theme, "points": points, "accent": accent},
        ),
        fps,
    )


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
    """规则抽取本节关键词：「」引号内词 > 数字短语 > 节标题头 4-6 字兜底。"""
    match = _KEYWORD_QUOTE_RE.search(narration or "")
    if match and match.group(1).strip():
        return match.group(1).strip()[:KEYWORD_MAX_CHARS]
    match = _NUMBER_RE.search(narration or "")
    if match:
        return match.group(0)
    return (title or "").strip()[:KEYWORD_TITLE_FALLBACK_CHARS]


def _derive_keyword_events(
    sections: list[dict],
    rewrite_sections: list,
    starts: list[float],
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

        # 取 keyword/number 类 emphasis（golden 单独派生为 golden_card，不进 keyword_pop）
        em_texts: list[str] = []
        if rw_section is not None:
            for em in (rw_section.get("emphasis") or []):
                if isinstance(em, dict):
                    kind = str(em.get("kind") or "keyword")
                    if kind == "golden":
                        continue  # golden 由 _derive_golden_events 处理，不进弹词
                    text = str(em.get("text") or "").strip()
                    if text:
                        em_texts.append(text)
        em_texts = em_texts[:3]

        if em_texts:
            # 均匀分布：N 条 emphasis → 节内 1/(N+1), 2/(N+1), …, N/(N+1) 处
            n = len(em_texts)
            for idx, text in enumerate(em_texts):
                offset = (idx + 1) / (n + 1) * sec_dur
                events.append((sec_start + offset, text))
        else:
            # 回落原有规则：节内 40% 处抽关键词
            narration = _section_narration(section, rewrite_sections, i)
            keyword = _extract_keyword(narration, _section_title(section, i))
            if keyword:
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
) -> list[tuple[float, str]]:
    """派生 golden_card 落点：仅收集 kind=golden 的 emphasis 条目，均匀分布到节内时刻。

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
            offset = (idx + 1) / (n + 1) * sec_dur
            events.append((sec_start + offset, text))
    return events


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


def _build_rich_effects(
    sections: list[dict],
    rewrite: dict | None,
    key_points_start: float,
    total_duration: float,
    fps: int,
    accent: str,
) -> list[EffectSpec]:
    """"丰富化"特效派生（默认全开）：开屏要点卡（intro 结束后逐行浮现各节标题）、
    金句卡（55% 进度、避开章节卡窗口）、数字强调（每节首个关键数字，全片最多 2 个）、
    关键词弹出（emphasis 均匀分布优先；无 emphasis 回落节内 40% 规则；密度控制+三色轮换）。
    """
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
                start=key_points_start,
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
        narration = _section_narration(section, rewrite_sections, i)
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

    # 关键词弹出：emphasis 均匀分布优先，无 emphasis 回落规则抽取，密度控制，三色轮换。
    keyword_events = _derive_keyword_events(sections, rewrite_sections, starts)
    keyword_events = _apply_density_control(keyword_events, sections, rewrite_sections, starts)

    # 金句全屏卡：kind=golden emphasis 单独派生，密度控制 + 保护窗口碰撞检查。
    # 保护窗口：opening_card / intro / 各章节卡（避免全屏卡与这些固定特效叠加）。
    golden_events = _derive_golden_events(sections, rewrite_sections, starts)
    opening_exists = bool(_opening_theme(rewrite))
    intro_start_t = INTRO_START_AFTER_OPENING if opening_exists else 0.0
    protected: list[tuple[float, float]] = [
        (intro_start_t, key_points_start),  # intro 时间窗
    ]
    if opening_exists:
        protected.append((0.0, OPENING_CARD_DURATION))
    for i in range(1, len(sections)):
        protected.append((starts[i], starts[i] + CHAPTER_CARD_DURATION))
    golden_events = _apply_golden_density_control(golden_events, protected, total_duration)
    # 收集最终 golden_card 时间窗，用于后续过滤 keyword_pop
    golden_windows: list[tuple[float, float]] = []
    for gtime, gtext in golden_events:
        spec = _frame_aligned(
            EffectSpec(
                type="golden_card",
                start=gtime,
                duration=GOLDEN_CARD_DURATION,
                props={"text": gtext, "accent": accent},
            ),
            fps,
        )
        rich.append(spec)
        golden_windows.append((spec.start, spec.start + spec.duration))

    # keyword_pop 三色轮换；过滤掉落在 golden_card 时间窗 ±1s 内的弹词（全屏卡前后弹小词很怪）。
    kw_color_idx = 0
    for kw_start, keyword in keyword_events:
        if any(gs - 1.0 <= kw_start <= ge + 1.0 for gs, ge in golden_windows):
            continue
        color = KEYWORD_POP_COLORS[kw_color_idx % len(KEYWORD_POP_COLORS)]
        rich.append(_frame_aligned(
            EffectSpec(
                type="keyword_pop",
                start=kw_start,
                duration=KEYWORD_POP_DURATION,
                props={"keyword": keyword, "accent": accent, "color": color},
            ),
            fps,
        ))
        kw_color_idx += 1

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
        stage_report.write_stage_error(args.output, "effects", f"特效层失败：{exc}")
        return 1

    print(f"特效层完成：渲染 {len(rendered)} 条特效")
    print(f"- 清单:     {manifest_path}")
    print(f"- 成片:     {release}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
