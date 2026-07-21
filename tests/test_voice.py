"""配音阶段（P16 二期）单测：全部离线（mock TTS 合成 / atempo 微调 / 时间轴对齐 / ffprobe）。

voice.py 把 assemble 的「配音获取 + 主时间轴」整段前移为独立阶段。这里验证：
- --tts 现场合成 + 未设语速时 atempo 微调 → produce_timeline 收到最终音轨；
- --audio 现成配音原样直接引用（不复制），照样产时间轴；
- 显式语速跳过 atempo 微调；
- 时间轴失败只告警不阻断（返回 0）；TTS 失败上抛记 failed（返回 1）；
- 重合成前清掉上一轮遗留的 voiceover_fitted.wav（防按盘误选陈旧 fitted）；
- 既无 --audio 也无 --tts 时按 assemble 同款语义留空（silent），跳过合成与时间轴。
"""

import json
from pathlib import Path

import pytest

from video_factory import voice
from video_factory.pipeline import TTSProviderError
from video_factory.voice import main


REWRITE = {
    "version": "rewrite_v1",
    "hook": "三秒钩子",
    "sections": [
        {"index": 0, "title": "第一节", "narration": "第一节口播文案内容一二三", "visual_hint": "画面"},
        {"index": 1, "title": "第二节", "narration": "第二节口播", "visual_hint": "画面"},
    ],
    "full_voiceover": "三秒钩子\n第一节口播文案内容一二三\n第二节口播",
    "target_duration_seconds": 90,
}


def _write_rewrite(tmp_path) -> Path:
    p = tmp_path / "rewrite.json"
    p.write_text(json.dumps(REWRITE, ensure_ascii=False), encoding="utf-8")
    return p


def _fake_synth(write_name="voiceover.wav"):
    """替身 _synthesize_from_rewrite：写出指定文件并返回其路径（不真跑 TTS）。"""

    def _inner(rewrite, provider, voice_name, output_dir, runner, speed=None):
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        path = Path(output_dir) / write_name
        path.write_bytes(b"wav")
        return path

    return _inner


# ---------- --tts 现场合成 + 微调 + 时间轴 ----------

def test_voice_tts_synthesizes_fits_and_produces_timeline(tmp_path, monkeypatch):
    """--tts 无语速：合成 voiceover.wav → atempo 微调出 fitted → 时间轴对齐 fitted。"""
    rewrite_path = _write_rewrite(tmp_path)
    out = tmp_path / "out"
    captured = {}

    def fake_fit(audio_path, target, output_dir, runner):
        fitted = Path(output_dir) / "voiceover_fitted.wav"
        fitted.write_bytes(b"fitted")
        return fitted, "已微调"

    def fake_produce(audio_path, full_text, output_dir, runner=None):
        captured["audio_path"] = Path(audio_path)
        captured["full_text"] = full_text
        return Path(output_dir) / "timeline.json"

    monkeypatch.setattr(voice, "_synthesize_from_rewrite", _fake_synth())
    monkeypatch.setattr(voice, "_fit_audio_to_target", fake_fit)
    monkeypatch.setattr(voice, "_probe_duration", lambda p, r: 50.0)
    monkeypatch.setattr("video_factory.timeline.produce_timeline", fake_produce)

    code = main(["--rewrite", str(rewrite_path), "--tts", "doubao",
                 "--duration", "90", "--output", str(out)])
    assert code == 0
    # 时间轴对齐的是微调后的 fitted 音轨（与最终混进成片那条一致）
    assert captured["audio_path"] == out / "voiceover_fitted.wav"
    assert captured["full_text"] == REWRITE["full_voiceover"]
    assert (out / "voiceover.wav").exists()
    assert (out / "voiceover_fitted.wav").exists()


def test_voice_voice_speed_skips_atempo_fit(tmp_path, monkeypatch):
    """显式设语速：跳过 atempo 微调，时间轴对齐未微调的 voiceover.wav。"""
    rewrite_path = _write_rewrite(tmp_path)
    out = tmp_path / "out"
    captured = {}
    fit_called = {"n": 0}

    def fake_fit(*a, **k):
        fit_called["n"] += 1
        return a[0], None

    def fake_produce(audio_path, full_text, output_dir, runner=None):
        captured["audio_path"] = Path(audio_path)
        return Path(output_dir) / "timeline.json"

    monkeypatch.setattr(voice, "_synthesize_from_rewrite", _fake_synth())
    monkeypatch.setattr(voice, "_fit_audio_to_target", fake_fit)
    monkeypatch.setattr(voice, "_probe_duration", lambda p, r: 42.0)
    monkeypatch.setattr("video_factory.timeline.produce_timeline", fake_produce)

    code = main(["--rewrite", str(rewrite_path), "--tts", "doubao",
                 "--voice-speed", "1.5", "--duration", "90", "--output", str(out)])
    assert code == 0
    assert fit_called["n"] == 0  # 语速已设 → 微调让位
    assert captured["audio_path"] == out / "voiceover.wav"


# ---------- --audio 现成配音 ----------

def test_voice_user_audio_referenced_not_copied(tmp_path, monkeypatch):
    """--audio 现成配音：原样直接引用（不落盘复制），照常产时间轴。"""
    rewrite_path = _write_rewrite(tmp_path)
    user_audio = tmp_path / "my_voice.wav"
    user_audio.write_bytes(b"wav")
    out = tmp_path / "out"
    captured = {}

    def fake_produce(audio_path, full_text, output_dir, runner=None):
        captured["audio_path"] = Path(audio_path)
        return Path(output_dir) / "timeline.json"

    monkeypatch.setattr(voice, "_probe_duration", lambda p, r: 90.0)
    monkeypatch.setattr("video_factory.timeline.produce_timeline", fake_produce)

    code = main(["--rewrite", str(rewrite_path), "--audio", str(user_audio),
                 "--duration", "90", "--output", str(out)])
    assert code == 0
    assert captured["audio_path"] == user_audio  # 原样引用
    assert not (out / "voiceover.wav").exists()  # 不复制到输出目录


def test_voice_missing_user_audio_returns_1(tmp_path, monkeypatch):
    """--audio 指向不存在的文件 → 记 failed（返回 1），落盘 stage error。"""
    rewrite_path = _write_rewrite(tmp_path)
    out = tmp_path / "out"
    code = main(["--rewrite", str(rewrite_path), "--audio", str(tmp_path / "nope.wav"),
                 "--output", str(out)])
    assert code == 1
    assert (out / "voice_error.txt").exists()


# ---------- 时间轴失败容错 / TTS 失败 ----------

def test_voice_timeline_failure_still_returns_0(tmp_path, monkeypatch):
    """时间轴对齐失败（produce_timeline 返回 None）→ 只告警不阻断，仍返回 0。"""
    rewrite_path = _write_rewrite(tmp_path)
    out = tmp_path / "out"
    monkeypatch.setattr(voice, "_synthesize_from_rewrite", _fake_synth())
    monkeypatch.setattr(voice, "_fit_audio_to_target", lambda a, t, o, r: (a, None))
    monkeypatch.setattr(voice, "_probe_duration", lambda p, r: 50.0)
    monkeypatch.setattr("video_factory.timeline.produce_timeline", lambda *a, **k: None)

    code = main(["--rewrite", str(rewrite_path), "--tts", "doubao",
                 "--duration", "90", "--output", str(out)])
    assert code == 0  # 时间轴是增强件，失败不阻断成片


def test_voice_tts_failure_returns_1_and_writes_error(tmp_path, monkeypatch):
    """TTS 合成抛 TTSProviderError → 记 failed（返回 1），落盘 stage error。"""
    rewrite_path = _write_rewrite(tmp_path)
    out = tmp_path / "out"

    def boom(*a, **k):
        raise TTSProviderError("豆包配音失败：网络不可达")

    monkeypatch.setattr(voice, "_synthesize_from_rewrite", boom)
    code = main(["--rewrite", str(rewrite_path), "--tts", "doubao",
                 "--duration", "90", "--output", str(out)])
    assert code == 1
    assert "网络不可达" in (out / "voice_error.txt").read_text(encoding="utf-8")


# ---------- 重合成前清陈旧 fitted ----------

def test_voice_clears_stale_fitted_before_resynthesis(tmp_path, monkeypatch):
    """重合成且本轮微调未触发时，上一轮遗留的 voiceover_fitted.wav 必须先清掉。"""
    rewrite_path = _write_rewrite(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    (out / "voiceover_fitted.wav").write_bytes(b"stale-fitted")  # 上一轮遗留

    # 本轮微调不触发（偏差在容差内）：返回原音轨、不写新 fitted。
    monkeypatch.setattr(voice, "_synthesize_from_rewrite", _fake_synth())
    monkeypatch.setattr(voice, "_fit_audio_to_target", lambda a, t, o, r: (a, None))
    monkeypatch.setattr(voice, "_probe_duration", lambda p, r: 90.0)
    monkeypatch.setattr("video_factory.timeline.produce_timeline",
                        lambda *a, **k: out / "timeline.json")

    code = main(["--rewrite", str(rewrite_path), "--tts", "doubao",
                 "--duration", "90", "--output", str(out)])
    assert code == 0
    assert not (out / "voiceover_fitted.wav").exists()  # 陈旧 fitted 已清


# ---------- 无配音来源：silent 路径 ----------

def test_voice_no_source_skips_gracefully(tmp_path, monkeypatch):
    """既无 --audio 也无 --tts → 与 assemble 同款语义留空，跳过合成与时间轴，返回 0。"""
    rewrite_path = _write_rewrite(tmp_path)
    out = tmp_path / "out"
    produce_called = {"n": 0}
    monkeypatch.setattr("video_factory.timeline.produce_timeline",
                        lambda *a, **k: produce_called.__setitem__("n", produce_called["n"] + 1))

    code = main(["--rewrite", str(rewrite_path), "--duration", "90", "--output", str(out)])
    assert code == 0
    assert produce_called["n"] == 0  # 无音轨 → 不产时间轴


# ---------- TTS 瞬时网络故障重试（2026-07-21 实测 studio_0721_230653） ----------

def test_tts_retries_remote_disconnected_and_succeeds(monkeypatch, tmp_path):
    """复现实测故障：豆包 TTS 撞 RemoteDisconnected，重试后成功、任务不该死。

    RemoteDisconnected 是 OSError 子类且在 response.read() 阶段抛出，旧代码的
    HTTPError/URLError 网兜不住，一路漏到 voice.py 的通用 OSError 分支被判死，
    整单 267s 白跑。
    """
    from http.client import RemoteDisconnected

    from video_factory import pipeline

    monkeypatch.setattr(pipeline.time, "sleep", lambda _s: None)
    calls = []

    class _Resp:
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def read(self):
            return b"AUDIOBYTES"

    def flaky(request, timeout=None):
        calls.append(1)
        if len(calls) < 3:
            raise RemoteDisconnected("Remote end closed connection without response")
        return _Resp()

    monkeypatch.setattr(pipeline, "urlopen", flaky)

    data = pipeline._urlopen_read_with_retry(
        pipeline.Request("https://x", data=b"{}"), 30.0, "Doubao TTS"
    )
    assert data == b"AUDIOBYTES"
    assert len(calls) == 3, "应重试到第 3 次"


def test_tts_gives_up_after_max_attempts_with_clear_error(monkeypatch):
    """重试耗尽后抛 TTSProviderError（而非裸 OSError），错误文案带真实原因。"""
    from http.client import RemoteDisconnected

    from video_factory import pipeline

    monkeypatch.setattr(pipeline.time, "sleep", lambda _s: None)
    calls = []

    def always_fail(request, timeout=None):
        calls.append(1)
        raise RemoteDisconnected("Remote end closed connection without response")

    monkeypatch.setattr(pipeline, "urlopen", always_fail)

    with pytest.raises(pipeline.TTSProviderError) as exc_info:
        pipeline._urlopen_read_with_retry(
            pipeline.Request("https://x", data=b"{}"), 30.0, "Doubao TTS"
        )
    assert len(calls) == pipeline.TTS_MAX_ATTEMPTS
    message = str(exc_info.value)
    assert "已重试" in message
    assert "Remote end closed connection" in message   # 真实原因不被吞掉


def test_tts_does_not_retry_http_errors(monkeypatch):
    """明确 HTTP 状态码（鉴权/配额）不重试——重试无意义还多花钱。"""
    import io
    from urllib.error import HTTPError

    from video_factory import pipeline

    calls = []

    def unauthorized(request, timeout=None):
        calls.append(1)
        raise HTTPError("u", 401, "Unauthorized", {}, io.BytesIO(b""))

    monkeypatch.setattr(pipeline, "urlopen", unauthorized)

    with pytest.raises(pipeline.TTSProviderError, match="HTTP error: 401"):
        pipeline._urlopen_read_with_retry(
            pipeline.Request("https://x", data=b"{}"), 30.0, "Doubao TTS"
        )
    assert len(calls) == 1, "HTTP 错误码不该重试"


def test_incomplete_read_is_also_retried(monkeypatch):
    """IncompleteRead（HTTPException 族）同样属于瞬时故障——2026-07-15 生图同款教训。"""
    from http.client import IncompleteRead

    from video_factory import pipeline

    monkeypatch.setattr(pipeline.time, "sleep", lambda _s: None)
    calls = []

    class _Resp:
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def read(self):
            return b"OK"

    def flaky(request, timeout=None):
        calls.append(1)
        if len(calls) < 2:
            raise IncompleteRead(b"partial", 100)
        return _Resp()

    monkeypatch.setattr(pipeline, "urlopen", flaky)

    assert pipeline._urlopen_read_with_retry(
        pipeline.Request("https://x", data=b"{}"), 30.0, "OpenAI TTS"
    ) == b"OK"
    assert len(calls) == 2
