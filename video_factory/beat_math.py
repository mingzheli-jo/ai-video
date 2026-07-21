"""节级时长切分的唯一口径（2026-07-21 去重：此前 asset_pool 与 image_gen 各一份）。

为什么必须同源：batch._run_image_gen 按本算法算出「每节多少秒 → 该节几拍」来决定
生成几张图；assemble --ordered-assets 之后又独立重算一次来把图铺到时间轴上。
两边算出的拍数必须严格一致——不一致时 assemble 不会报错，而是**静默回落**到
均匀 5s/拍的老路径（见 assemble._ordered_asset_plan），成片画面与预期错位且难查。

此前两份实现已经分岔：短时长输入下 asset_pool 版响亮抛错、image_gen 版
`max(0.0, ...)` 吞掉再让残差修正把末节压成接近零甚至负值。本模块统一为
「算法只有一份、是否抛错由调用方选」：

- split_durations(...)          → 纯计算，时长不足时返回 None（生图侧：静默不了才降级）
- split_durations_or_raise(...) → 时长不足时抛调用方给的异常（拼装侧：必须响亮失败）
"""

from __future__ import annotations

from typing import Sequence

# 每节保底时长（秒）。曾在 asset_pool.MIN_SECTION_SECONDS 与
# image_gen._MIN_SECTION_SECONDS 各写一份，两处都是 2.0——改一处漏一处即拍数错位。
MIN_SECTION_SECONDS = 2.0


def char_count(text: str) -> int:
    """去空白后的字符数——节时长按它占比分配，两侧必须同口径。"""
    return len("".join(str(text or "").split()))


def min_total_duration(section_count: int) -> float:
    """section_count 节所需的最小总时长（每节保底 MIN_SECTION_SECONDS）。"""
    return MIN_SECTION_SECONDS * section_count


def split_durations(
    sections_char_counts: Sequence[int], total_duration: float
) -> list[float] | None:
    """按字数占比把 total_duration 分到各节；返回 None = 总时长不足以保底。

    每节 = MIN_SECTION_SECONDS + 剩余时长按字数占比的份额；浮点残差归末节，
    保证 sum(结果) == total_duration。空节列表返回 None（无节可分）。
    全零字数时剩余时长均分——避免除零，且此时各节无内容差异、均分即合理。
    """
    counts = [max(0, int(count)) for count in sections_char_counts]
    if not counts:
        return None
    section_count = len(counts)
    floor = min_total_duration(section_count)
    if total_duration < floor:
        return None

    flexible = total_duration - floor
    total_chars = sum(counts)
    if total_chars <= 0:
        shares = [flexible / section_count] * section_count
    else:
        shares = [flexible * count / total_chars for count in counts]
    durations = [MIN_SECTION_SECONDS + share for share in shares]
    durations[-1] += total_duration - sum(durations)
    return durations


def split_durations_or_raise(
    sections_char_counts: Sequence[int],
    total_duration: float,
    error_factory,
) -> list[float]:
    """split_durations 的抛错版：不足以保底时抛 error_factory(消息)。

    error_factory 由调用方给（各阶段保留自己的异常类型），本模块不依赖任何阶段。
    """
    counts = list(sections_char_counts)
    if not counts:
        raise error_factory("没有可分配的小节。")
    durations = split_durations(counts, total_duration)
    if durations is None:
        raise error_factory(
            f"目标时长 {total_duration:.1f}s 太短："
            f"{len(counts)} 节每节至少 {MIN_SECTION_SECONDS}s。"
        )
    return durations
