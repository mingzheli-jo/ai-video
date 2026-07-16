"""P7 逐句动态字幕测试（全部离线：fake runner + monkeypatch asr）。

不依赖 faster-whisper、不真跑 ffmpeg；align 路径通过 monkeypatch
video_factory.subtitles._transcribe_for_timeline 注入伪 ASR 分段。
"""

import json
import subprocess
from pathlib import Path

import pytest

from video_factory.subtitles import (
    ASS_FILENAME,
    DEFAULT_SUBTITLE_FONT,
    MAX_CUE_CHARS,
    MIN_CUE_DURATION,
    RELEASE_FILENAME,
    SUBTITLE_FONT_NAME_ENV,
    SUBTITLE_FONT_OPTIONS,
    SUBTITLE_FONT_SIZE_ENV,
    Cue,
    SubtitlesError,
    build_cue_timeline_align,
    build_cue_timeline_ratio,
    build_cues,
    burn_subtitles,
    ensure_libass,
    generate_subtitles,
    get_subtitle_font_name,
    get_subtitle_font_scale,
    main,
    prepare_single_line_cues,
    render_ass,
    resolve_video_dimensions,
    split_sentences,
    translate_texts_to_english,
)


@pytest.fixture(autouse=True)
def _no_llm_credentials(monkeypatch):
    """隔离 LLM 凭据：字幕单测绝不真打翻译网络。默认 english=True 的 e2e 路径
    会因无凭据自动降级纯中文——这本身就是对降级分支的常态覆盖。"""
    for name in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY"):
        monkeypatch.delenv(name, raising=False)


# --- 分句 ----------------------------------------------------------------


def test_split_sentences_by_terminal_punctuation():
    result = split_sentences("第一句。第二句！第三句？")
    assert result == ["第一句", "第二句", "第三句"]


def test_split_sentences_newline_and_semicolon_are_separators():
    result = split_sentences("上半句；下半句\n换行句")
    assert result == ["上半句", "下半句", "换行句"]


def test_split_sentences_long_sentence_split_by_comma():
    # 超过 25 字的长句按逗号二切成两条，均在 25 字内。
    text = "这是一个相当长的开头部分需要被切开，因为后半段同样很长也超过了限制字数"
    result = split_sentences(text)
    assert len(result) == 2
    assert all(len(piece) <= MAX_CUE_CHARS for piece in result)
    assert result[0] == "这是一个相当长的开头部分需要被切开"


def test_split_sentences_preserves_internal_commas_on_rejoin():
    # 端到端回归：多逗号长句重拼进一条时，内部逗号必须保留（可读性），
    # 但分句边界不留悬挂逗号。修复前重拼会把所有逗号吃掉。
    text = "先把画面裁成竖屏，用模糊背景填满上下留白，观感立刻高级。"
    result = split_sentences(text)

    assert result[0] == "先把画面裁成竖屏，用模糊背景填满上下留白"
    assert "，" in result[0]  # 内部逗号保留
    assert not result[0].endswith("，")  # 边界不留悬挂逗号
    assert all(len(piece) <= MAX_CUE_CHARS for piece in result)


def test_split_sentences_hard_wrap_when_no_comma():
    # 无逗号又超长：按 25 字硬切。
    text = "啊" * 60
    result = split_sentences(text)
    assert len(result) == 3  # 60 / 25 -> 25 + 25 + 10
    assert [len(p) for p in result] == [25, 25, 10]


def test_split_sentences_within_limit_stays_single():
    result = split_sentences("短句一句话")
    assert result == ["短句一句话"]


def test_split_sentences_empty_raises_chinese_error():
    with pytest.raises(SubtitlesError, match="字幕文本为空"):
        split_sentences("   ")


def test_split_sentences_only_punctuation_raises():
    with pytest.raises(SubtitlesError, match="无法分句"):
        split_sentences("。！？；")


# --- ratio 时间轴 --------------------------------------------------------


def test_ratio_timeline_proportional_to_char_count():
    # 甲乙丙(3) + 丁戊(2) 共 5 字，总时长 10s -> 6s + 4s。
    cues = build_cue_timeline_ratio(["甲乙丙", "丁戊"], 10.0)
    assert cues[0].start == 0.0
    assert cues[0].end == 6.0
    assert cues[1].start == 6.0
    assert cues[1].end == 4.0 + 6.0


def test_ratio_timeline_sum_equals_total_duration():
    cues = build_cue_timeline_ratio(["一二", "三四五", "六"], 12.0)
    # 末条 end 收在总时长；相邻不重叠。
    assert cues[-1].end == pytest.approx(12.0, abs=1e-3)
    for a, b in zip(cues, cues[1:]):
        assert a.end <= b.start + 1e-6


def test_ratio_timeline_enforces_min_duration():
    # 一个极短句子在很长音频里占比极小，但显示时长不得低于 0.8s。
    sentences = ["超长句子" * 10, "短"]
    cues = build_cue_timeline_ratio(sentences, 100.0)
    assert cues[1].end - cues[1].start >= MIN_CUE_DURATION - 1e-6


def test_ratio_timeline_rejects_zero_duration():
    with pytest.raises(SubtitlesError, match="总时长"):
        build_cue_timeline_ratio(["甲"], 0.0)


def test_ratio_timeline_rejects_empty_sentences():
    with pytest.raises(SubtitlesError, match="没有句子"):
        build_cue_timeline_ratio([], 10.0)


# --- align 对齐 ----------------------------------------------------------


def test_align_maps_sentences_by_char_ratio():
    # ASR 说 "甲乙丙丁戊" 覆盖 0-5s；原稿分成 甲乙丙(3) / 丁戊(2)。
    segments = ((0.0, 5.0, "甲乙丙丁戊"),)
    cues = build_cue_timeline_align(["甲乙丙", "丁戊"], segments)
    assert cues[0].text == "甲乙丙"
    assert cues[0].start == pytest.approx(0.0, abs=1e-2)
    assert cues[0].end == pytest.approx(3.0, abs=1e-2)
    assert cues[1].text == "丁戊"
    assert cues[1].end == pytest.approx(5.0, abs=1e-2)


def test_align_normalizes_punctuation_in_asr():
    # ASR 分段带标点，归一化后按纯字符数均分时间；标点不占时间权重。
    segments = ((0.0, 4.0, "甲乙，丙丁。"),)
    cues = build_cue_timeline_align(["甲乙", "丙丁"], segments)
    # 4 个有效字符 -> 每字 1s；甲乙 0-2、丙丁 2-4。
    assert cues[0].end == pytest.approx(2.0, abs=1e-2)
    assert cues[1].start == pytest.approx(2.0, abs=1e-2)
    assert cues[1].end == pytest.approx(4.0, abs=1e-2)


def test_align_drift_contained_within_sentence():
    # TTS 把 "30%" 念成 "百分之三十"：ASR 字符数远多于原稿，漂移应限制在这一句内，
    # 整句仍映射到 ASR 的完整时间窗，不外溢到其他句。
    segments = ((0.0, 6.0, "百分之三十的涨幅很可观"),)  # 11 字
    cues = build_cue_timeline_align(["30%涨幅可观"], segments)  # 原稿 8 字
    assert len(cues) == 1
    assert cues[0].start == pytest.approx(0.0, abs=1e-2)
    assert cues[0].end == pytest.approx(6.0, abs=1e-2)


def test_align_multi_segment_timeline():
    # 跨两个 ASR 分段的时间线拼接。
    segments = ((0.0, 2.0, "甲乙"), (2.0, 4.0, "丙丁"))
    cues = build_cue_timeline_align(["甲乙", "丙丁"], segments)
    assert cues[0].start == pytest.approx(0.0, abs=1e-2)
    assert cues[1].end == pytest.approx(4.0, abs=1e-2)


def test_align_empty_asr_raises_for_fallback():
    # ASR 空结果 -> align 抛错，交由 build_cues(auto) 降级 ratio。
    with pytest.raises(SubtitlesError, match="无法 align"):
        build_cue_timeline_align(["甲乙"], ())


def test_align_asr_all_punctuation_raises():
    # ASR 只回标点（归一化后为空）-> 时间线为空 -> 抛错。
    with pytest.raises(SubtitlesError, match="无法 align"):
        build_cue_timeline_align(["甲乙"], ((0.0, 2.0, "，。！"),))


# --- build_cues 编排（含 auto 降级） -------------------------------------


class _DurationRunner:
    """伪 ffprobe：只回一个固定 format.duration，用于 ratio 路径取音频总时长。"""

    def __init__(self, duration=10.0):
        self.duration = duration

    def __call__(self, command, **kwargs):
        payload = json.dumps({"format": {"duration": str(self.duration)}})
        return subprocess.CompletedProcess(command, 0, stdout=payload, stderr="")


def test_build_cues_ratio_mode_uses_probe_duration(tmp_path):
    audio = tmp_path / "voiceover.wav"
    audio.write_bytes(b"wav")
    cues, mode, warnings = build_cues(["甲乙丙", "丁戊"], "ratio", audio, _DurationRunner(10.0))
    assert mode == "ratio"
    assert warnings == []
    assert cues[0].end == pytest.approx(6.0, abs=1e-3)


def test_build_cues_align_mode_uses_injected_asr(tmp_path, monkeypatch):
    audio = tmp_path / "voiceover.wav"
    audio.write_bytes(b"wav")
    monkeypatch.setattr(
        "video_factory.subtitles._transcribe_for_timeline",
        lambda a, r: ((0.0, 5.0, "甲乙丙丁戊"),),
    )
    cues, mode, warnings = build_cues(["甲乙丙", "丁戊"], "align", audio, _DurationRunner())
    assert mode == "align"
    assert cues[1].end == pytest.approx(5.0, abs=1e-2)


def test_build_cues_auto_falls_back_to_ratio_and_records_warning(tmp_path, monkeypatch):
    # align 抛错（模拟 faster-whisper 不可用）-> auto 降级 ratio 并留痕 warning。
    audio = tmp_path / "voiceover.wav"
    audio.write_bytes(b"wav")

    def _boom(a, r):
        raise SubtitlesError("faster-whisper 未安装")

    monkeypatch.setattr("video_factory.subtitles._transcribe_for_timeline", _boom)
    cues, mode, warnings = build_cues(["甲乙丙", "丁戊"], "auto", audio, _DurationRunner(10.0))
    assert mode == "ratio"
    assert len(warnings) == 1 and "降级为 ratio" in warnings[0]
    assert cues[0].end == pytest.approx(6.0, abs=1e-3)


def test_build_cues_auto_prefers_align_when_available(tmp_path, monkeypatch):
    audio = tmp_path / "voiceover.wav"
    audio.write_bytes(b"wav")
    monkeypatch.setattr(
        "video_factory.subtitles._transcribe_for_timeline",
        lambda a, r: ((0.0, 4.0, "甲乙丙丁"),),
    )
    cues, mode, warnings = build_cues(["甲乙", "丙丁"], "auto", audio, _DurationRunner())
    assert mode == "align"
    assert warnings == []


def test_build_cues_rejects_unknown_mode(tmp_path):
    with pytest.raises(SubtitlesError, match="不支持的 mode"):
        build_cues(["甲"], "wat", None, _DurationRunner())


def test_build_cues_align_without_audio_raises(tmp_path):
    with pytest.raises(SubtitlesError, match="时间轴来源"):
        build_cues(["甲"], "align", None, _DurationRunner())


# --- render_ass ----------------------------------------------------------


def test_render_ass_style_line_fields():
    cues = [Cue(0.0, 2.0, "你好")]
    ass = render_ass(cues, 1920, 1080)
    style = next(line for line in ass.splitlines() if line.startswith("Style: Default"))
    assert "Microsoft YaHei" in style  # 默认字体族
    # 对标爆款博主：粗体白字（PrimaryColour &H00FFFFFF）+ 厚黑描边（OutlineColour 不透明纯黑）；
    # 用户点名去掉黑色底板（旧版 BorderStyle=3 半透明底板已废弃，别改回去）。
    assert "&H00FFFFFF" in style
    assert "&H60000000" not in style  # 底板色不复存在
    fields = style[len("Style: "):].split(",")
    assert fields[5] == "&H00000000"  # OutlineColour：不透明纯黑描边
    assert fields[7] == "1"    # Bold=1 粗体（新默认）
    assert fields[15] == "1"   # BorderStyle=1 描边样式（无底板）
    font_size = int(fields[2])
    # 新默认描边加厚：系数 0.10（约旧值 0.05 的 2 倍），与字号联动。
    assert int(fields[16]) == max(4, round(font_size * 0.10))
    assert int(fields[17]) > 0  # Shadow 轻阴影（可读性最后防线）
    assert fields[18] == "2"   # Alignment 底部居中


def test_render_ass_bilingual_zh_top_en_bottom():
    # 双语：每条中文 cue 配一条 EN 事件（同时间窗）；EN 样式小字号且更贴底边
    # （MarginV 小于中文 → 视觉上"中上英下"）。
    cues = [Cue(0.0, 2.0, "你好世界"), Cue(2.0, 4.0, "再见")]
    ass = render_ass(cues, 1080, 1920, english=["Hello world", "Goodbye"])
    lines = ass.splitlines()
    en_style = next(l for l in lines if l.startswith("Style: EN"))
    zh_style = next(l for l in lines if l.startswith("Style: Default"))
    en_fields = en_style[len("Style: "):].split(",")
    zh_fields = zh_style[len("Style: "):].split(",")
    assert int(en_fields[2]) < int(zh_fields[2])      # EN 字号更小
    assert int(en_fields[21]) < int(zh_fields[21])    # EN MarginV 更小（更贴底 → 在中文下方）
    en_events = [l for l in lines if l.startswith("Dialogue:") and ",EN," in l]
    zh_events = [l for l in lines if l.startswith("Dialogue:") and ",Default," in l]
    assert len(en_events) == len(zh_events) == 2
    assert "Hello world" in en_events[0] and "Goodbye" in en_events[1]
    # 同时间窗：EN 事件的时间戳与对应中文一致
    assert en_events[0].split(",")[1:3] == zh_events[0].split(",")[1:3]


def test_render_ass_english_count_mismatch_raises():
    with pytest.raises(SubtitlesError, match="不一致"):
        render_ass([Cue(0, 2, "你好")], 1080, 1920, english=["a", "b", "c"])


def test_render_ass_without_english_has_no_en_style():
    ass = render_ass([Cue(0, 2, "你好")], 1080, 1920)
    assert "Style: EN" not in ass
    assert ",EN," not in ass


def test_fit_english_lines_shrinks_then_truncates():
    from video_factory.subtitles import _fit_english_lines

    # 1080 宽竖屏：usable=950，zh_font=73 → en_font=40，预算 950/(40*0.55)≈43 字符
    short = ["Hello world"]
    font, fitted = _fit_english_lines(short, 950, 73)
    assert font == round(73 * 0.55) and fitted == ["Hello world"]
    # 超预算 → 缩一档（73*0.45≈33），预算变 950/(33*0.55)≈52；仍超 → 省略号截断
    long_line = "x" * 80
    font2, fitted2 = _fit_english_lines([long_line], 950, 73)
    assert font2 == round(73 * 0.45)
    assert fitted2[0].endswith("…") and len(fitted2[0]) <= 52


def test_translate_texts_to_english_batch_ordered(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    captured = {}

    def fake_chat(system, user, config):
        captured["system"] = system
        captured["user"] = user
        return json.dumps(["Line one", "Line two"])

    monkeypatch.setattr("video_factory.llm.chat_completion", fake_chat)
    lines, warnings = translate_texts_to_english(["第一句", "第二句"], char_budget=40)
    assert lines == ["Line one", "Line two"]
    assert warnings == []
    assert "40" in captured["system"]          # 字符预算写进提示词
    assert "第一句" in captured["user"]         # 批量 JSON 输入


def test_translate_count_mismatch_falls_back_per_chunk(monkeypatch):
    """回归（2026-07-14 事故：115 进 111 出整批降级）：条数不匹配重试一次后，
    只有该块回退空串（纯中文），不再抛异常拖垮整片英文。"""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    calls = []
    monkeypatch.setattr(
        "video_factory.llm.chat_completion",
        lambda s, u, c: calls.append(u) or json.dumps(["only one"]),
    )
    lines, warnings = translate_texts_to_english(["第一句", "第二句"], char_budget=40)
    assert lines == ["", ""]                    # 失败块回退空串
    assert len(calls) == 2                      # 首次 + 重试一次
    assert warnings and "回退纯中文" in warnings[0]


def test_translate_retry_succeeds_second_attempt(monkeypatch):
    """首次条数不齐、重试成功 → 无告警、结果完整。"""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    replies = iter([json.dumps(["bad"]), json.dumps(["One", "Two"])])
    monkeypatch.setattr("video_factory.llm.chat_completion", lambda s, u, c: next(replies))
    lines, warnings = translate_texts_to_english(["第一句", "第二句"], char_budget=40)
    assert lines == ["One", "Two"]
    assert warnings == []


def test_translate_chunks_large_input(monkeypatch):
    """超过块大小的输入按块独立调用：50 条 → 3 次调用（24+24+2），失败只影响所在块。"""
    from video_factory.subtitles import _TRANSLATE_CHUNK_SIZE

    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    sizes = []

    def fake_chat(system, user, config):
        chunk = json.loads(user)
        sizes.append(len(chunk))
        return json.dumps([f"en{i}" for i in range(len(chunk))])

    monkeypatch.setattr("video_factory.llm.chat_completion", fake_chat)
    texts = [f"第{i}句" for i in range(50)]
    lines, warnings = translate_texts_to_english(texts, char_budget=40)
    assert len(lines) == 50 and warnings == []
    assert sizes == [_TRANSLATE_CHUNK_SIZE, _TRANSLATE_CHUNK_SIZE, 50 - 2 * _TRANSLATE_CHUNK_SIZE]


def test_prepare_single_line_cues_idempotent():
    # 翻译流程依赖切割幂等：切过的 cues 再切一遍必须逐条相同。
    cues = [Cue(0, 4, "闪存空间不足时读写速度会掉一半，腾出空间立马流畅")]
    once = prepare_single_line_cues(cues, 1080, 1920)
    twice = prepare_single_line_cues(once, 1080, 1920)
    assert once == twice and len(once) >= 2


def test_render_ass_font_size_scales_with_height():
    横 = render_ass([Cue(0, 1, "a")], 1920, 1080)
    竖 = render_ass([Cue(0, 1, "a")], 1080, 1920)
    横_size = int(next(l for l in 横.splitlines() if l.startswith("Style:")).split(",")[2])
    竖_size = int(next(l for l in 竖.splitlines() if l.startswith("Style:")).split(",")[2])
    assert 横_size == 54   # min(96, round(1080*0.05)=54, 宽度约束 120)
    # 竖屏受宽度约束：usable=1080-2*65=950，950//13=73 < 高度算出的 96
    assert 竖_size == 73
    assert 竖_size > 横_size


def test_split_cue_single_line_breaks_long_cue_into_multiple():
    from video_factory.subtitles import _split_cue_single_line

    # 竖屏：长句切成多条单行 cue（每条<=13、不含逗号），时间轴按占比分摊、时间连续。
    parts = _split_cue_single_line(Cue(0.0, 4.0, "闪存空间不足时读写速度会掉一半，腾出空间立马流畅"), 13)
    assert len(parts) >= 2
    assert all(len(p.text) <= 13 for p in parts)
    assert parts[0].start == 0.0 and parts[-1].end == 4.0
    # 时间连续无缝
    assert all(parts[i].end == parts[i + 1].start for i in range(len(parts) - 1))
    # 关键回归：任何 cue 都不含逗号（libass 全角逗号会污染后续 cue 冒出前导逗号）
    assert all("，" not in p.text and "," not in p.text for p in parts)


def test_split_cue_strips_comma_and_edge_punctuation():
    from video_factory.subtitles import _split_cue_single_line

    # 逗号切开成独立短语；首尾标点剥掉
    parts = _split_cue_single_line(Cue(0.0, 2.0, "，动画特效看着酷，"), 13)
    assert [p.text for p in parts] == ["动画特效看着酷"]
    # 内部逗号切成两条短语
    parts2 = _split_cue_single_line(Cue(0.0, 2.0, "别急着换，三步就能救回来"), 13)
    assert [p.text for p in parts2] == ["别急着换", "三步就能救回来"]


def test_render_ass_comma_cue_two_lines_portrait_single_line_landscape():
    # 竖屏（2026-07-16 用户定案）：逗号两个半句（各 ≤ 单行容量）合成一条两行
    # Dialogue（\\N 上下同屏），不再切成快速闪过的碎条；逗号仍被剥离
    # （规避 libass 逗号渲染残留）。
    text = "闪存不足会掉速，腾出空间立马流畅"  # 7 + 8 字，竖屏单行容 13 字
    ass_p = render_ass([Cue(0, 3, text)], 1080, 1920)
    dialogues_p = [l for l in ass_p.splitlines() if l.startswith("Dialogue:")]
    assert len(dialogues_p) == 1
    assert "\\N" in dialogues_p[0]  # 两行同屏
    assert all("，" not in d.split(",", 8)[8] for d in dialogues_p)
    # 横屏维持单行的旧行为（16 字 ≤ 横屏单行容量 → 单条不换行）
    ass_l = render_ass([Cue(0, 3, text)], 1920, 1080)
    dialogues_l = [l for l in ass_l.splitlines() if l.startswith("Dialogue:")]
    assert all("\\N" not in d for d in dialogues_l)


def test_render_ass_vertical_font_constrained_by_width_not_overflow():
    # 回归：竖屏一行 MAX_CUE_CHARS 个字，按字号排布不得超出可用宽度（否则冲出屏幕）。
    from video_factory.subtitles import MAX_CUE_CHARS, _CHARS_PER_LINE_BUDGET

    ass = render_ass([Cue(0, 1, "水" * MAX_CUE_CHARS)], 1080, 1920)
    style = next(l for l in ass.splitlines() if l.startswith("Style:"))
    fields = style[len("Style: "):].split(",")
    font_size = int(fields[2])
    side_margin = int(fields[19])
    usable = 1080 - 2 * side_margin
    # 满长字幕折成最多 2 行：每行 ceil(25/2)=13 字，13*字号须 <= 可用宽度
    assert font_size * _CHARS_PER_LINE_BUDGET <= usable
    # WrapStyle=0 开启换行，长行不会溢出
    assert "WrapStyle: 0" in ass
    assert fields[19] == fields[20]  # MarginL==MarginR 左右对称
    assert side_margin > 0


def test_render_ass_font_size_capped_at_96():
    # 极高画面（4K 竖屏 2160）也不超过 96 上限。
    ass = render_ass([Cue(0, 1, "a")], 2160, 3840)
    size = int(next(l for l in ass.splitlines() if l.startswith("Style:")).split(",")[2])
    assert size == 96


def test_render_ass_margin_v_portrait_lifted_landscape_bottom():
    # 竖屏底边上提到 0.18h（2026-07-16 用户定案：避开平台底部 UI 遮挡）；横屏维持 0.08h。
    ass_p = render_ass([Cue(0, 1, "a")], 1080, 1920)
    style_p = next(l for l in ass_p.splitlines() if l.startswith("Style:"))
    assert int(style_p.split(",")[21]) == round(1920 * 0.18)  # 346
    ass_l = render_ass([Cue(0, 1, "a")], 1920, 1080)
    style_l = next(l for l in ass_l.splitlines() if l.startswith("Style:"))
    assert int(style_l.split(",")[21]) == round(1080 * 0.08)  # 86


def test_render_ass_dialogue_timestamp_format():
    cues = [Cue(3661.23, 3665.5, "文本")]  # 1:01:01.23 -> 1:01:05.50
    ass = render_ass(cues, 1920, 1080)
    dialogue = next(l for l in ass.splitlines() if l.startswith("Dialogue:"))
    assert "1:01:01.23" in dialogue
    assert "1:01:05.50" in dialogue


def test_render_ass_timestamp_short_form():
    cues = [Cue(0.0, 1.5, "a")]
    ass = render_ass(cues, 1920, 1080)
    dialogue = next(l for l in ass.splitlines() if l.startswith("Dialogue:"))
    # H:MM:SS.cc 格式：小时不补零、分秒补两位、厘秒两位
    assert "0:00:00.00" in dialogue
    assert "0:00:01.50" in dialogue


def test_render_ass_escapes_braces_and_backslash():
    # 文本含大括号会被 libass 当 override 块解析，必须转义；反斜杠同理。
    cues = [Cue(0, 2, "价格{涨}了\\一半")]
    ass = render_ass(cues, 1920, 1080)
    dialogue = next(l for l in ass.splitlines() if l.startswith("Dialogue:"))
    assert "\\{" in dialogue and "\\}" in dialogue
    # 裸的 { } 不应出现（除转义后的 \{ \}）
    assert "{涨}" not in dialogue
    assert "\\\\" in dialogue  # 反斜杠转义


def test_render_ass_commas_are_split_out_of_text():
    # 逗号被切成独立短语（不再出现在任一 Dialogue 文本里），从根上规避 libass 逗号渲染残留，
    # 同时也不会破坏 Dialogue 的逗号分隔字段。
    cues = [Cue(0, 3, "一,二,三")]
    ass = render_ass(cues, 1920, 1080)
    dialogues = [l for l in ass.splitlines() if l.startswith("Dialogue:")]
    bodies = [d.split(",", 8)[8] for d in dialogues]
    assert bodies == ["一", "二", "三"]  # 三个短语，各自无逗号


def test_render_ass_dialogue_has_exact_field_count_no_leading_comma():
    # 根因回归：Dialogue 行必须严格 9 字段（Layer,Start,End,Style,Name,MarginL,MarginR,Effect,Text），
    # Style 与 Text 之间只有 ",,0,0," 四段。曾多写一个 margin 字段（",,0,0,0,,"），
    # libass 把越界的逗号并进 Text，烧录出行首幻影逗号「,不关掉手机永远慢」。
    cues = [Cue(0, 1.2, "手机用一年就卡"), Cue(11.82, 13.6, "不关掉手机永远慢")]
    ass = render_ass(cues, 1080, 1920)
    dialogues = [l for l in ass.splitlines() if l.startswith("Dialogue:")]
    for d in dialogues:
        # Style 与 Text 之间恰好 ",,0,0," —— 不能是 ",,0,0,0,,"
        assert ",Default,,0,0,," in d
        assert ",,0,0,0,," not in d
        text = d.split(",", 8)[8]  # 第 9 字段
        assert not text.startswith(",")  # 无行首幻影逗号


def test_render_ass_newline_folded_to_ass_break():
    cues = [Cue(0, 2, "上行\n下行")]
    ass = render_ass(cues, 1920, 1080)
    dialogue = next(l for l in ass.splitlines() if l.startswith("Dialogue:"))
    assert "\\N" in dialogue
    # 真实换行不能出现在 Dialogue 行内（否则 ASS 事件被截断）
    assert dialogue.count("Dialogue:") == 1


def test_render_ass_rejects_empty_cues():
    with pytest.raises(SubtitlesError, match="没有字幕条目"):
        render_ass([], 1920, 1080)


# --- 分辨率探测 ----------------------------------------------------------


class _ProbeRunner:
    def __init__(self, width=1920, height=1080, returncode=0):
        self.width = width
        self.height = height
        self.returncode = returncode

    def __call__(self, command, **kwargs):
        if self.returncode != 0 or self.width <= 0:
            return subprocess.CompletedProcess(command, self.returncode, stdout=json.dumps({"streams": []}), stderr="")
        payload = json.dumps({"streams": [{"width": self.width, "height": self.height}]})
        return subprocess.CompletedProcess(command, 0, stdout=payload, stderr="")


def test_resolve_dimensions_reads_portrait(tmp_path):
    video = tmp_path / "release.mp4"
    video.write_bytes(b"v")
    w, h, warnings = resolve_video_dimensions(video, _ProbeRunner(1080, 1920))
    assert (w, h) == (1080, 1920)
    assert warnings == []


def test_resolve_dimensions_falls_back_when_probe_fails(tmp_path):
    video = tmp_path / "release.mp4"
    video.write_bytes(b"v")
    w, h, warnings = resolve_video_dimensions(video, _ProbeRunner(width=0, height=0))
    assert (w, h) == (1920, 1080)
    assert any("回落" in x for x in warnings)


def test_resolve_dimensions_falls_back_when_missing(tmp_path):
    w, h, warnings = resolve_video_dimensions(tmp_path / "nope.mp4", _ProbeRunner())
    assert (w, h) == (1920, 1080)
    assert any("底片不存在" in x for x in warnings)


# --- libass 探测 ---------------------------------------------------------


def test_ensure_libass_ok_when_filter_present():
    def runner(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, stdout=" .. subtitles     V->V   x", stderr="")

    ensure_libass(runner)  # 不抛异常即通过


def test_ensure_libass_raises_when_missing():
    def runner(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, stdout=" .. scale  V->V", stderr="")

    with pytest.raises(SubtitlesError, match="libass"):
        ensure_libass(runner)


# --- 烧录命令构建 --------------------------------------------------------


class _BurnRunner:
    """记录烧录命令与 kwargs（含 cwd），并把产物写盘。"""

    def __init__(self, returncode=0):
        self.commands = []
        self.kwargs = []
        self.returncode = returncode

    def __call__(self, command, **kwargs):
        self.commands.append(command)
        self.kwargs.append(kwargs)
        if self.returncode == 0:
            # 输出文件是命令最后一项，相对 cwd 解析。
            out = Path(kwargs.get("cwd", ".")) / command[-1]
            out.write_bytes(b"burned")
        return subprocess.CompletedProcess(command, self.returncode, stdout="", stderr="ffmpeg boom")


def test_burn_command_uses_cwd_and_bare_filenames(tmp_path):
    video = tmp_path / "src" / "input.mp4"
    video.parent.mkdir()
    video.write_bytes(b"video")
    ass = tmp_path / "src" / "subs.ass"
    ass.write_bytes(b"[Script Info]")
    out_dir = tmp_path / "out"
    output = out_dir / RELEASE_FILENAME
    runner = _BurnRunner()

    result = burn_subtitles(video, ass, output, runner)

    assert result == output
    assert output.exists()
    command = runner.commands[0]
    kwargs = runner.kwargs[0]
    # cwd 是输出目录（规避 Windows 路径转义坑）
    assert Path(kwargs["cwd"]) == out_dir
    # 滤镜参数用裸文件名 subtitles=subtitles.ass，不含盘符/路径分隔符
    vf_arg = command[command.index("-vf") + 1]
    assert vf_arg == f"subtitles={ASS_FILENAME}"
    assert ":" not in vf_arg and "\\" not in vf_arg and "/" not in vf_arg
    # 音轨直通 copy、视频重编 libx264 crf 18
    assert "-c:a" in command and command[command.index("-c:a") + 1] == "copy"
    assert "-c:v" in command and command[command.index("-c:v") + 1] == "libx264"
    assert "18" in command
    # 输入也是裸文件名（相对 cwd）
    assert command[command.index("-i") + 1] == RELEASE_FILENAME


def test_burn_stages_ass_into_output_dir(tmp_path):
    video = tmp_path / "input.mp4"
    video.write_bytes(b"video")
    ass = tmp_path / "somewhere" / "subs.ass"
    ass.parent.mkdir()
    ass.write_bytes(b"[Script Info]\n")
    out_dir = tmp_path / "out"
    runner = _BurnRunner()

    burn_subtitles(video, ass, out_dir / RELEASE_FILENAME, runner)

    # .ass 以固定 ASCII 名备进输出目录供裸文件名引用
    assert (out_dir / ASS_FILENAME).exists()
    assert (out_dir / ASS_FILENAME).read_bytes() == b"[Script Info]\n"


def test_burn_raises_on_ffmpeg_failure(tmp_path):
    video = tmp_path / "input.mp4"
    video.write_bytes(b"video")
    ass = tmp_path / "subs.ass"
    ass.write_bytes(b"x")
    runner = _BurnRunner(returncode=1)
    with pytest.raises(SubtitlesError, match="烧录失败"):
        burn_subtitles(video, ass, tmp_path / "out" / RELEASE_FILENAME, runner)


def test_burn_rejects_missing_video(tmp_path):
    ass = tmp_path / "subs.ass"
    ass.write_bytes(b"x")
    with pytest.raises(SubtitlesError, match="视频不存在"):
        burn_subtitles(tmp_path / "nope.mp4", ass, tmp_path / "out.mp4", _BurnRunner())


def test_burn_rejects_missing_ass(tmp_path):
    video = tmp_path / "input.mp4"
    video.write_bytes(b"v")
    with pytest.raises(SubtitlesError, match="字幕文件不存在"):
        burn_subtitles(video, tmp_path / "nope.ass", tmp_path / "out.mp4", _BurnRunner())


# --- generate_subtitles 端到端（fake runner） ---------------------------


class _AllRunner:
    """一个 runner 覆盖 ffmpeg -filters / ffprobe / 烧录三类命令。"""

    def __init__(self, width=1920, height=1080, duration=10.0):
        self.width = width
        self.height = height
        self.duration = duration
        self.commands = []
        self.kwargs = []

    def __call__(self, command, **kwargs):
        self.commands.append(command)
        self.kwargs.append(kwargs)
        tool = Path(command[0]).stem.lower()
        if tool == "ffmpeg" and "-filters" in command:
            return subprocess.CompletedProcess(command, 0, stdout=" .. subtitles  V->V x", stderr="")
        if tool == "ffprobe":
            if "stream=width,height" in " ".join(command):
                payload = json.dumps({"streams": [{"width": self.width, "height": self.height}]})
            else:
                payload = json.dumps({"format": {"duration": str(self.duration)}})
            return subprocess.CompletedProcess(command, 0, stdout=payload, stderr="")
        # 烧录 ffmpeg：写产物到 cwd
        out = Path(kwargs.get("cwd", ".")) / command[-1]
        out.write_bytes(b"burned")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")


def test_generate_subtitles_ratio_end_to_end(tmp_path):
    video = tmp_path / "release.mp4"
    video.write_bytes(b"v")
    audio = tmp_path / "voiceover.wav"
    audio.write_bytes(b"wav")
    out_dir = tmp_path / "out"
    runner = _AllRunner(width=1080, height=1920, duration=10.0)

    report = generate_subtitles(
        video, "第一句。第二句。", out_dir, mode="ratio", audio=audio, runner=runner,
    )

    assert report["mode"] == "ratio"
    assert report["cue_count"] == 2
    assert report["width"] == 1080 and report["height"] == 1920
    assert (out_dir / ASS_FILENAME).exists()
    assert (out_dir / RELEASE_FILENAME).exists()
    saved = json.loads((out_dir / "subtitles_report.json").read_text(encoding="utf-8"))
    assert saved["mode"] == "ratio"


def test_generate_subtitles_auto_fallback_records_mode_ratio(tmp_path, monkeypatch):
    video = tmp_path / "release.mp4"
    video.write_bytes(b"v")
    audio = tmp_path / "voiceover.wav"
    audio.write_bytes(b"wav")

    def _boom(a, r):
        raise SubtitlesError("faster-whisper ImportError")

    monkeypatch.setattr("video_factory.subtitles._transcribe_for_timeline", _boom)
    runner = _AllRunner(duration=8.0)

    report = generate_subtitles(video, "甲乙。丙丁。", tmp_path / "out", mode="auto", audio=audio, runner=runner)

    assert report["mode"] == "ratio"
    assert any("降级为 ratio" in w for w in report["warnings"])


def test_generate_subtitles_align_records_source(tmp_path, monkeypatch):
    video = tmp_path / "release.mp4"
    video.write_bytes(b"v")
    audio = tmp_path / "voiceover.wav"
    audio.write_bytes(b"wav")
    monkeypatch.setattr(
        "video_factory.subtitles._transcribe_for_timeline",
        lambda a, r: ((0.0, 4.0, "甲乙丙丁"),),
    )
    runner = _AllRunner()

    report = generate_subtitles(video, "甲乙。丙丁。", tmp_path / "out", mode="align", audio=audio, runner=runner)

    assert report["mode"] == "align"
    assert "faster-whisper" in report["timeline_source"]


def test_generate_subtitles_timeline_mode_skips_asr(tmp_path, monkeypatch):
    """P16：给了 timeline → cues 直接由 sentences 构造，跳过 split+ASR，mode 记 timeline。"""
    video = tmp_path / "release.mp4"
    video.write_bytes(b"v")
    out_dir = tmp_path / "out"
    runner = _AllRunner(width=1080, height=1920, duration=10.0)

    # build_cues 一旦被调用就炸——证明 timeline 模式彻底跳过 ASR/分摊。
    def _must_not_run(*a, **k):
        raise AssertionError("timeline 模式不该调用 build_cues（应跳过 ASR）")

    monkeypatch.setattr("video_factory.subtitles.build_cues", _must_not_run)

    timeline = [
        {"text": "第一句", "start": 0.0, "end": 2.0},
        {"text": "第二句", "start": 2.0, "end": 5.0},
        {"text": "第三句", "start": 5.0, "end": 8.0},
    ]
    report = generate_subtitles(
        video, "随便的文本（timeline 模式下不用于分句）", out_dir,
        mode="auto", audio=None, runner=runner, timeline=timeline,
    )

    assert report["mode"] == "timeline"
    assert report["cue_count"] == 3  # 三句直接来自 timeline
    assert "主时间轴" in report["timeline_source"]
    assert (out_dir / RELEASE_FILENAME).exists()
    saved = json.loads((out_dir / "subtitles_report.json").read_text(encoding="utf-8"))
    assert saved["mode"] == "timeline"


def test_cli_timeline_flag_skips_asr(tmp_path, monkeypatch):
    """CLI --timeline 存在且可加载 → 走 timeline 模式，不抽临时音轨、不跑 ASR。"""
    video = tmp_path / "release.mp4"
    video.write_bytes(b"v")
    rewrite = tmp_path / "rewrite.json"
    rewrite.write_text(json.dumps({"full_voiceover": "甲。乙。"}, ensure_ascii=False), encoding="utf-8")
    tl = tmp_path / "timeline.json"
    tl.write_text(
        json.dumps({"version": "timeline_v1", "sentences": [
            {"text": "甲句", "start": 0.0, "end": 1.5},
            {"text": "乙句", "start": 1.5, "end": 3.0},
        ]}, ensure_ascii=False),
        encoding="utf-8",
    )
    runner = _AllRunner(duration=10.0)
    monkeypatch.setattr("video_factory.subtitles.subprocess.run", runner)
    # 若误走 ASR 路径会调 build_cues；炸掉以坐实跳过。
    monkeypatch.setattr(
        "video_factory.subtitles.build_cues",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("不该跑 ASR")),
    )

    code = main([
        "--video", str(video),
        "--rewrite", str(rewrite),
        "--timeline", str(tl),
        "--output", str(tmp_path / "out"),
    ])
    assert code == 0
    report = json.loads((tmp_path / "out" / "subtitles_report.json").read_text(encoding="utf-8"))
    assert report["mode"] == "timeline"
    assert report["cue_count"] == 2


def test_cli_timeline_missing_file_falls_back_to_normal_flow(tmp_path, monkeypatch):
    """--timeline 指向不存在的文件 → load 返回 None → 回落现有 ratio 全流程。"""
    video = tmp_path / "release.mp4"
    video.write_bytes(b"v")
    audio = tmp_path / "voiceover.wav"
    audio.write_bytes(b"wav")
    runner = _AllRunner(duration=10.0)
    monkeypatch.setattr("video_factory.subtitles.subprocess.run", runner)

    code = main([
        "--video", str(video),
        "--text", "第一句。第二句。",
        "--audio", str(audio),
        "--timeline", str(tmp_path / "absent.json"),  # 不存在 → 回落
        "--mode", "ratio",
        "--output", str(tmp_path / "out"),
    ])
    assert code == 0
    report = json.loads((tmp_path / "out" / "subtitles_report.json").read_text(encoding="utf-8"))
    assert report["mode"] == "ratio"  # 回落，不是 timeline


def test_generate_subtitles_raises_when_libass_missing(tmp_path):
    video = tmp_path / "release.mp4"
    video.write_bytes(b"v")

    def runner(command, **kwargs):
        if "-filters" in command:
            return subprocess.CompletedProcess(command, 0, stdout=" .. scale V->V", stderr="")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    with pytest.raises(SubtitlesError, match="libass"):
        generate_subtitles(video, "甲乙。", tmp_path / "out", mode="ratio", audio=None, runner=runner)


# --- CLI -----------------------------------------------------------------


def test_cli_text_and_rewrite_mutually_exclusive(capsys):
    with pytest.raises(SystemExit):
        main(["--video", "x.mp4", "--text", "abc", "--rewrite", "r.json", "--mode", "ratio"])
    err = capsys.readouterr().err
    assert "--rewrite" in err or "--text" in err


def test_cli_requires_text_or_rewrite(capsys):
    with pytest.raises(SystemExit):
        main(["--video", "x.mp4", "--mode", "ratio"])
    err = capsys.readouterr().err
    assert "--rewrite" in err or "--text" in err or "required" in err.lower()


def test_cli_requires_video(capsys):
    with pytest.raises(SystemExit):
        main(["--text", "abc", "--mode", "ratio"])
    err = capsys.readouterr().err
    assert "--video" in err


def test_cli_missing_video_returns_chinese_error_exit_1(tmp_path, capsys, monkeypatch):
    # ratio 模式 + --text，无需 ASR/音频；视频不存在应报中文错并 exit 1。
    monkeypatch.setattr(
        "video_factory.subtitles.ensure_libass", lambda runner=None: None
    )
    code = main([
        "--video", str(tmp_path / "nope.mp4"),
        "--text", "甲乙。丙丁。",
        "--mode", "ratio",
        "--output", str(tmp_path / "out"),
    ])
    assert code == 1
    out = capsys.readouterr().out
    assert "字幕生成失败" in out and "视频不存在" in out


def test_cli_ratio_with_text_and_audio_succeeds(tmp_path, capsys, monkeypatch):
    video = tmp_path / "release.mp4"
    video.write_bytes(b"v")
    audio = tmp_path / "voiceover.wav"
    audio.write_bytes(b"wav")
    runner = _AllRunner(duration=10.0)
    monkeypatch.setattr("video_factory.subtitles.subprocess.run", runner)

    code = main([
        "--video", str(video),
        "--text", "第一句。第二句。",
        "--audio", str(audio),
        "--mode", "ratio",
        "--output", str(tmp_path / "out"),
    ])

    assert code == 0
    out = capsys.readouterr().out
    assert "字幕烧录完成" in out and "ratio" in out
    assert (tmp_path / "out" / RELEASE_FILENAME).exists()


def test_cli_rewrite_source_reads_full_voiceover(tmp_path, capsys, monkeypatch):
    video = tmp_path / "release.mp4"
    video.write_bytes(b"v")
    audio = tmp_path / "voiceover.wav"
    audio.write_bytes(b"wav")
    rewrite = tmp_path / "rewrite.json"
    rewrite.write_text(json.dumps({"full_voiceover": "钩子句。正文第一句。"}, ensure_ascii=False), encoding="utf-8")
    runner = _AllRunner(duration=10.0)
    monkeypatch.setattr("video_factory.subtitles.subprocess.run", runner)

    code = main([
        "--video", str(video),
        "--rewrite", str(rewrite),
        "--audio", str(audio),
        "--mode", "ratio",
        "--output", str(tmp_path / "out"),
    ])

    assert code == 0
    report = json.loads((tmp_path / "out" / "subtitles_report.json").read_text(encoding="utf-8"))
    assert report["cue_count"] == 2  # 两句


def test_cli_rewrite_missing_full_voiceover_returns_error(tmp_path, capsys, monkeypatch):
    video = tmp_path / "release.mp4"
    video.write_bytes(b"v")
    rewrite = tmp_path / "rewrite.json"
    rewrite.write_text(json.dumps({"hook": "x"}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr("video_factory.subtitles.ensure_libass", lambda runner=None: None)

    code = main([
        "--video", str(video),
        "--rewrite", str(rewrite),
        "--mode", "ratio",
        "--output", str(tmp_path / "out"),
    ])
    assert code == 1
    out = capsys.readouterr().out
    assert "full_voiceover" in out


# --- 审查回归：align 时间窗塌缩 ------------------------------------------

def test_align_collapsed_windows_redistributed():
    """ASR 严重欠转写（字符数远少于原稿句数）时，多句不允许塌缩到同一时间窗完全重叠。"""
    segments = ((0.0, 1.0, "A"),)  # 归一化后只有 1 个字符的时间线
    sentences = ["第一句", "第二句", "第三句", "第四句", "第五句"]

    cues = build_cue_timeline_align(sentences, segments)

    assert len(cues) == 5
    starts = [cue.start for cue in cues]
    assert starts == sorted(starts)
    # 无任何两条 cue 完全同窗（修复前 5 条全是 (0.0, 1.0)）
    assert len({(cue.start, cue.end) for cue in cues}) == 5
    assert all(cue.end > cue.start for cue in cues)
    # 整体仍被约束在 ASR 时间线范围内
    assert cues[0].start == 0.0
    assert cues[-1].end <= 1.0 + 1e-6


def test_english_defaults_off_everywhere(monkeypatch):
    """2026-07-15 用户决定取消英文字幕：generate_subtitles 与 CLI 默认都必须是关。
    能力保留（--english 显式开启），默认路径不再发起任何翻译调用。"""
    import inspect

    from video_factory.subtitles import generate_subtitles

    assert inspect.signature(generate_subtitles).parameters["english"].default is False
    # CLI 默认：--english 未传 → False；传了 → True
    import argparse

    from video_factory import subtitles as subs_mod

    called = []
    monkeypatch.setattr(subs_mod, "_run_cli", lambda args: called.append(args.english) or {
        "mode": "ratio", "cue_count": 1, "ass": "a.ass", "release": "r.mp4"})
    subs_mod.main(["--video", "v.mp4", "--text", "你好", "--mode", "ratio", "--output", "o"])
    subs_mod.main(["--video", "v.mp4", "--text", "你好", "--mode", "ratio", "--output", "o", "--english"])
    assert called == [False, True]


# --- 字幕字号缩放 / 字体族（用户可调设置项） -------------------------------


@pytest.fixture
def _clean_subtitle_env(monkeypatch, tmp_path):
    """隔离字幕设置：清 env + 把 settings.yaml 指向不存在的 tmp 路径（load_settings 返回 {}）。
    这样 get_subtitle_font_scale/name 只看默认值，测试可逐一注入 env 或写临时 settings.yaml。"""
    from video_factory import settings_store

    monkeypatch.delenv(SUBTITLE_FONT_SIZE_ENV, raising=False)
    monkeypatch.delenv(SUBTITLE_FONT_NAME_ENV, raising=False)
    settings_file = tmp_path / "settings.yaml"
    monkeypatch.setattr(settings_store, "SETTINGS_PATH", settings_file)
    return settings_file


def test_subtitle_font_scale_default_is_one(_clean_subtitle_env):
    assert get_subtitle_font_scale() == 1.0


def test_subtitle_font_scale_env_overrides_settings(_clean_subtitle_env, monkeypatch):
    # settings.yaml 写 1.2，env 写 0.8 → env 优先。
    _clean_subtitle_env.write_text('SUBTITLE_FONT_SIZE: "1.2"\n', encoding="utf-8")
    monkeypatch.setenv(SUBTITLE_FONT_SIZE_ENV, "0.8")
    assert get_subtitle_font_scale() == 0.8


def test_subtitle_font_scale_reads_settings_when_no_env(_clean_subtitle_env):
    _clean_subtitle_env.write_text('SUBTITLE_FONT_SIZE: "1.3"\n', encoding="utf-8")
    assert get_subtitle_font_scale() == 1.3


def test_subtitle_font_scale_clamps_out_of_range(_clean_subtitle_env, monkeypatch):
    monkeypatch.setenv(SUBTITLE_FONT_SIZE_ENV, "5.0")
    assert get_subtitle_font_scale() == 3.0   # 上界钳位（2026-07-15 用户点名 1.5→3.0）
    monkeypatch.setenv(SUBTITLE_FONT_SIZE_ENV, "0.1")
    assert get_subtitle_font_scale() == 0.7   # 下界钳位


def test_subtitle_font_scale_invalid_falls_back_to_one(_clean_subtitle_env, monkeypatch):
    for bad in ("abc", "", "  ", "1.0x", "nan"):
        monkeypatch.setenv(SUBTITLE_FONT_SIZE_ENV, bad)
        assert get_subtitle_font_scale() == 1.0


def test_subtitle_font_name_default(_clean_subtitle_env):
    assert get_subtitle_font_name() == DEFAULT_SUBTITLE_FONT == "Microsoft YaHei"


def test_subtitle_font_name_whitelist_accepted(_clean_subtitle_env, monkeypatch):
    for font in SUBTITLE_FONT_OPTIONS:
        monkeypatch.setenv(SUBTITLE_FONT_NAME_ENV, font)
        assert get_subtitle_font_name() == font


def test_subtitle_font_name_rejects_arbitrary(_clean_subtitle_env, monkeypatch):
    # 任意非白名单字体一律回落默认（写进 .ass 前的最后防线）。
    for bad in ("Comic Sans", "'; DROP", "Arial", ""):
        monkeypatch.setenv(SUBTITLE_FONT_NAME_ENV, bad)
        assert get_subtitle_font_name() == DEFAULT_SUBTITLE_FONT


def test_subtitle_font_name_from_settings_reflected_in_ass(_clean_subtitle_env):
    _clean_subtitle_env.write_text('SUBTITLE_FONT_NAME: "SimHei"\n', encoding="utf-8")
    ass = render_ass([Cue(0, 1, "你好")], 1080, 1920)
    style = next(l for l in ass.splitlines() if l.startswith("Style: Default"))
    assert style[len("Style: "):].split(",")[1] == "SimHei"


def test_subtitle_font_name_arbitrary_settings_falls_back_in_ass(_clean_subtitle_env):
    _clean_subtitle_env.write_text('SUBTITLE_FONT_NAME: "Comic Sans"\n', encoding="utf-8")
    ass = render_ass([Cue(0, 1, "你好")], 1080, 1920)
    style = next(l for l in ass.splitlines() if l.startswith("Style: Default"))
    assert style[len("Style: "):].split(",")[1] == "Microsoft YaHei"


def test_render_ass_font_scale_multiplies_zh_font(_clean_subtitle_env, monkeypatch):
    # 默认字号（scale=1.0）对比放大 1.5 倍：中文主字号应显著变大。
    base = render_ass([Cue(0, 1, "a")], 1920, 1080)
    base_size = int(next(l for l in base.splitlines() if l.startswith("Style: Default")).split(",")[2])
    monkeypatch.setenv(SUBTITLE_FONT_SIZE_ENV, "1.5")
    big = render_ass([Cue(0, 1, "a")], 1920, 1080)
    big_size = int(next(l for l in big.splitlines() if l.startswith("Style: Default")).split(",")[2])
    assert base_size == 54                     # 旧默认不变
    assert big_size == round(54 * 1.5) == 81   # 系数乘在基准字号上
    assert big_size > base_size


def test_burn_command_has_faststart_for_web_playback():
    """回归：最终 mp4 必须带 -movflags +faststart（moov 移到文件头），
    否则浏览器需下完整片才能起播——页面播放"闪烁+播2秒停+循环"的根因。"""
    from video_factory.subtitles import _build_burn_command

    cmd = _build_burn_command()
    assert "-movflags" in cmd
    assert cmd[cmd.index("-movflags") + 1] == "+faststart"


# ---- P3a 卡拉OK逐字字幕（2026-07-16 借鉴 Remotion 官网卡拉OK效果）----


def test_render_ass_karaoke_adds_kf_per_char_and_swaps_colors():
    # 每字一个 \kf 标签、时长按字数均分（3s/6字=50cs/字）；
    # Primary 换金黄（唱到色）、Secondary 换白（未唱色）。
    ass = render_ass([Cue(0, 3, "此心不动随机")], 1920, 1080, karaoke=True)
    dialogue = next(l for l in ass.splitlines() if l.startswith("Dialogue:"))
    assert dialogue.count("\\kf") == 6
    assert "{\\kf50}此" in dialogue
    style = next(l for l in ass.splitlines() if l.startswith("Style: Default"))
    assert "&H000FC4F1" in style  # PrimaryColour = #f1c40f 的 BGR
    assert "&H00FFFFFF" in style  # SecondaryColour = 白


def test_render_ass_karaoke_centisecond_sum_matches_cue_span():
    # 余数厘秒摊给前几个字：总和必须等于句时长（2.5s=250cs / 7 字 = 36*5 + 35*2）。
    import re as _re

    ass = render_ass([Cue(0, 2.5, "知行合一致良知")], 1920, 1080, karaoke=True)
    dialogue = next(l for l in ass.splitlines() if l.startswith("Dialogue:"))
    spans = [int(m) for m in _re.findall(r"\\kf(\d+)", dialogue)]
    assert len(spans) == 7
    assert sum(spans) == 250


def test_render_ass_karaoke_off_keeps_plain_text_and_white_primary():
    ass = render_ass([Cue(0, 2, "你好世界")], 1920, 1080)
    dialogue = next(l for l in ass.splitlines() if l.startswith("Dialogue:"))
    assert "\\kf" not in dialogue
    style = next(l for l in ass.splitlines() if l.startswith("Style: Default"))
    assert style.split(",")[3] == "&H00FFFFFF"  # Primary 仍是白


def test_render_ass_karaoke_escapes_special_chars_per_char():
    # 逐字包标签后转义仍生效：{ } 不能裸露破坏 override 块。
    ass = render_ass([Cue(0, 1, "a{b}c")], 1920, 1080, karaoke=True)
    dialogue = next(l for l in ass.splitlines() if l.startswith("Dialogue:"))
    assert "\\{" in dialogue and "\\}" in dialogue


# ---- 2026-07-16 对齐锚定回归：TTS 数字展开不再让漂移跨句累积 ----


def test_align_number_expansion_does_not_shift_later_sentences():
    # 原稿"90%"被 TTS 念成"百分之九十"（ASR 侧字符膨胀）。旧的全局占比映射会把
    # 膨胀点之后所有句子的时间往后推；新的分块锚定把误差锁在膨胀句内部：
    # 第三句必须仍然精确落在自己的 ASR 分段起点 6.0s。
    sentences = ["前面的话", "有90%的人", "后面的话"]
    asr = [
        (0.0, 2.0, "前面的话"),
        (2.0, 6.0, "有百分之九十的人"),
        (6.0, 8.0, "后面的话"),
    ]
    cues = build_cue_timeline_align(sentences, asr)
    assert cues[0].start == pytest.approx(0.0, abs=0.05)
    assert cues[1].start == pytest.approx(2.0, abs=0.05)
    assert cues[2].start == pytest.approx(6.0, abs=0.10)
    assert cues[2].end == pytest.approx(8.0, abs=0.10)


def test_align_identical_text_maps_exactly():
    # 原稿与 ASR 完全一致时逐字符一一对应：每句起止 = 各自分段起止。
    sentences = ["第一句话", "第二句话"]
    asr = [(0.0, 3.0, "第一句话"), (3.0, 7.0, "第二句话")]
    cues = build_cue_timeline_align(sentences, asr)
    assert cues[0].start == pytest.approx(0.0, abs=0.01)
    assert cues[0].end == pytest.approx(3.0, abs=0.01)
    assert cues[1].start == pytest.approx(3.0, abs=0.01)
    assert cues[1].end == pytest.approx(7.0, abs=0.01)


# ---- 2026-07-16 字幕三连：分画幅字号 / 竖屏两行卡拉OK ----


def test_font_scale_per_aspect_with_legacy_fallback(monkeypatch):
    # 画幅专属键优先；缺失回落通用键；无参调用只读通用键（向后兼容）。
    monkeypatch.setenv("SUBTITLE_FONT_SIZE", "1.2")
    monkeypatch.setenv("SUBTITLE_FONT_SIZE_PORTRAIT", "1.5")
    monkeypatch.delenv("SUBTITLE_FONT_SIZE_LANDSCAPE", raising=False)
    assert get_subtitle_font_scale(True) == pytest.approx(1.5)
    assert get_subtitle_font_scale(False) == pytest.approx(1.2)  # 回落通用键
    assert get_subtitle_font_scale() == pytest.approx(1.2)


def test_karaoke_two_line_portrait_newline_has_no_time_share():
    # 竖屏两行成一句：\N 不带 \kf 标签、不占厘秒；点亮跨行连续，总和仍=句时长。
    import re as _re

    text = "闪存不足会掉速，腾出空间立马流畅"  # 7 + 8 字 → 竖屏合成一条两行
    ass = render_ass([Cue(0, 3, text)], 1080, 1920, karaoke=True)
    dialogue = next(l for l in ass.splitlines() if l.startswith("Dialogue:"))
    assert "\\N" in dialogue
    assert "{\\kf" in dialogue
    assert _re.search(r"\{\\kf\d+\}\\N", dialogue) is None  # \N 前无归属它的标签体
    spans = [int(m) for m in _re.findall(r"\\kf(\d+)", dialogue)]
    assert sum(spans) == 300  # 3s = 300cs 全部摊给 15 个实字，\N 不占
