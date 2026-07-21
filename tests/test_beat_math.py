"""beat_math 单测（2026-07-21 去重：此前 asset_pool 与 image_gen 各一份且已分岔）。

核心不变量：两个消费方（image_gen 算生几张图 / asset_pool 铺时间轴）必须算出
完全一致的节时长。不一致时 assemble 不报错、而是静默回落均匀 5s/拍，画面错位难查。
"""

import pytest

from video_factory import beat_math
from video_factory.asset_pool import AssetPoolError, _split_durations
from video_factory.image_gen import compute_section_durations


def test_split_sums_to_total_and_respects_floor():
    durations = beat_math.split_durations([10, 30, 60], 100.0)
    assert durations is not None
    assert sum(durations) == pytest.approx(100.0)
    assert all(d >= beat_math.MIN_SECTION_SECONDS for d in durations)
    # 字数多的节拿到更多时长
    assert durations[0] < durations[1] < durations[2]


def test_split_proportional_to_char_counts():
    # 保底之外的余量严格按字数占比：3 节 × 2s 保底 = 6s，余 94s 按 1:1:2 分
    durations = beat_math.split_durations([25, 25, 50], 100.0)
    flexible = [d - beat_math.MIN_SECTION_SECONDS for d in durations]
    assert flexible[0] == pytest.approx(23.5)
    assert flexible[1] == pytest.approx(23.5)
    assert flexible[2] == pytest.approx(47.0)


def test_split_returns_none_when_total_below_floor():
    # 3 节需要 6s 保底，给 5s 不够 → None（调用方决定抛错还是降级）
    assert beat_math.split_durations([1, 1, 1], 5.0) is None
    assert beat_math.split_durations([], 100.0) is None


def test_split_all_zero_chars_divides_evenly():
    durations = beat_math.split_durations([0, 0, 0], 30.0)
    assert durations == pytest.approx([10.0, 10.0, 10.0])


def test_split_exactly_at_floor_gives_every_section_the_minimum():
    durations = beat_math.split_durations([5, 10], beat_math.MIN_SECTION_SECONDS * 2)
    assert durations == pytest.approx([beat_math.MIN_SECTION_SECONDS] * 2)


def test_or_raise_uses_caller_exception():
    class MyError(Exception):
        pass

    with pytest.raises(MyError, match="太短"):
        beat_math.split_durations_or_raise([1, 1, 1], 5.0, MyError)
    with pytest.raises(MyError, match="没有可分配的小节"):
        beat_math.split_durations_or_raise([], 100.0, MyError)


# ---- 两个消费方同源（这才是去重要守住的东西） ----


def _rewrite_with(narrations: list[str]) -> dict:
    return {
        "hook": narrations[0],
        "sections": [{"title": f"第{i}节", "narration": n}
                     for i, n in enumerate(narrations[1:], 1)],
    }


@pytest.mark.parametrize("total", [30.0, 60.0, 100.0, 187.5])
def test_image_gen_and_asset_pool_agree_exactly(total):
    """同一批文案 + 同一目标时长 → 两侧节时长必须逐项相等。"""
    narrations = ["钩子文案短", "第一节内容" * 8, "第二节" * 20, "尾节收束文案" * 3]
    rewrite = _rewrite_with(narrations)

    from_image_gen = compute_section_durations(rewrite, total)
    counts = [beat_math.char_count(n) for n in narrations]
    from_asset_pool = _split_durations(counts, total)

    assert from_image_gen == pytest.approx(from_asset_pool)


def test_short_duration_divergence_is_gone():
    """曾经的分岔点：asset_pool 抛错、image_gen 却产出可为负的末节。

    现在统一——asset_pool 仍抛错（拼装必须响亮失败），image_gen 返回 []
    （生图可降级），但都不会再产出负时长这种坏数据。
    """
    narrations = ["a", "b", "c", "d"]           # 4 节 → 保底 8s
    rewrite = _rewrite_with(narrations)
    counts = [beat_math.char_count(n) for n in narrations]

    assert compute_section_durations(rewrite, 5.0) == []
    with pytest.raises(AssetPoolError, match="太短"):
        _split_durations(counts, 5.0)


def test_char_count_ignores_whitespace_consistently():
    assert beat_math.char_count(" 中文 内容\n换行\t制表 ") == len("中文内容换行制表")
    assert beat_math.char_count(None) == 0
    assert beat_math.char_count("") == 0


def test_assemble_char_count_is_the_same_function():
    from video_factory.assemble import _char_count

    for text in ("中文 内容", "", "  a b  c ", "混合 mixed 文本123"):
        assert _char_count(text) == beat_math.char_count(text)
