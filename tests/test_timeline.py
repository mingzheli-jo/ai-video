"""P16 主时间轴测试（全部离线：mock build_cues，不跑 ASR/ffmpeg）。

produce_timeline 复用 subtitles.build_cues 的 align 模式取真实时间线；这里通过
monkeypatch video_factory.subtitles.build_cues 注入伪 cues，验证产出/往返/容错。
"""

import json
from pathlib import Path

from video_factory.subtitles import Cue
from video_factory.timeline import (
    TIMELINE_FILENAME,
    TIMELINE_VERSION,
    load_timeline,
    produce_timeline,
)


def _fake_build_cues(cues):
    """构造一个替身 build_cues：忽略入参，恒定返回给定 cues（align 模式、无告警）。"""

    def _inner(sentences, mode, audio, runner):
        return list(cues), "align", []

    return _inner


# --- produce_timeline -----------------------------------------------------


def test_produce_timeline_writes_sentences(tmp_path, monkeypatch):
    """build_cues 成功 → 写出 timeline.json，逐条 cue 转录为 sentence。"""
    audio = tmp_path / "voiceover.wav"
    audio.write_bytes(b"fake")
    cues = [Cue(0.0, 2.0, "第一句"), Cue(2.0, 5.5, "第二句")]
    monkeypatch.setattr("video_factory.subtitles.build_cues", _fake_build_cues(cues))

    out = produce_timeline(audio, "第一句。第二句。", tmp_path)

    assert out is not None
    assert out == tmp_path / TIMELINE_FILENAME
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["version"] == TIMELINE_VERSION
    assert data["sentences"] == [
        {"text": "第一句", "start": 0.0, "end": 2.0},
        {"text": "第二句", "start": 2.0, "end": 5.5},
    ]


def test_produce_timeline_missing_audio_returns_none(tmp_path):
    """音频不存在 → 返回 None，不写文件（增强件不阻断成片）。"""
    out = produce_timeline(tmp_path / "nope.wav", "任意文本", tmp_path)
    assert out is None
    assert not (tmp_path / TIMELINE_FILENAME).exists()


def test_produce_timeline_build_cues_failure_returns_none(tmp_path, monkeypatch):
    """build_cues align 抛异常（缺依赖/对齐失败）→ 捕获返回 None，不阻断。"""
    audio = tmp_path / "voiceover.wav"
    audio.write_bytes(b"fake")

    def _boom(sentences, mode, audio, runner):
        raise RuntimeError("faster-whisper 不可用")

    monkeypatch.setattr("video_factory.subtitles.build_cues", _boom)

    out = produce_timeline(audio, "第一句。", tmp_path)
    assert out is None


def test_produce_timeline_empty_text_returns_none(tmp_path, monkeypatch):
    """空文本 → split_sentences 抛错 → 捕获返回 None。"""
    audio = tmp_path / "voiceover.wav"
    audio.write_bytes(b"fake")
    # 不 mock build_cues：空文本会先在 split_sentences 抛 SubtitlesError。
    out = produce_timeline(audio, "   ", tmp_path)
    assert out is None


# --- load_timeline --------------------------------------------------------


def test_load_timeline_roundtrip(tmp_path, monkeypatch):
    """produce 后 load 回读，句子结构一致（往返）。"""
    audio = tmp_path / "voiceover.wav"
    audio.write_bytes(b"fake")
    cues = [Cue(0.0, 1.5, "甲"), Cue(1.5, 3.0, "乙")]
    monkeypatch.setattr("video_factory.subtitles.build_cues", _fake_build_cues(cues))

    out = produce_timeline(audio, "甲。乙。", tmp_path)
    loaded = load_timeline(out)

    assert loaded == [
        {"text": "甲", "start": 0.0, "end": 1.5},
        {"text": "乙", "start": 1.5, "end": 3.0},
    ]


def test_load_timeline_missing_file_returns_none(tmp_path):
    assert load_timeline(tmp_path / "absent.json") is None


def test_load_timeline_corrupt_json_returns_none(tmp_path):
    """损坏 JSON → None（容错，下游自行回落估算）。"""
    bad = tmp_path / "timeline.json"
    bad.write_text("{not valid json", encoding="utf-8")
    assert load_timeline(bad) is None


def test_load_timeline_wrong_shape_returns_none(tmp_path):
    """结构不对（sentences 非列表）→ None。"""
    bad = tmp_path / "timeline.json"
    bad.write_text(json.dumps({"version": "timeline_v1", "sentences": "oops"}), encoding="utf-8")
    assert load_timeline(bad) is None


def test_load_timeline_non_dict_item_returns_none(tmp_path):
    """sentences 里混入非 dict 元素 → None。"""
    bad = tmp_path / "timeline.json"
    bad.write_text(
        json.dumps({"version": "timeline_v1", "sentences": [{"text": "ok", "start": 0, "end": 1}, 42]}),
        encoding="utf-8",
    )
    assert load_timeline(bad) is None
