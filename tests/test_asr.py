import json
import sys
import types

import pytest

from video_factory.asr import (
    ASRConfig,
    ASRError,
    ASRResult,
    format_timestamp,
    transcribe_media,
    write_transcript_outputs,
)


class FakeCompleted:
    def __init__(self, returncode=0, stdout=""):
        self.returncode = returncode
        self.stdout = stdout


def _make_media(tmp_path, name="clip.mp4"):
    media = tmp_path / name
    media.write_bytes(b"fake media bytes")
    return media


# ---- ffmpeg 提音命令构建 -------------------------------------------------


def test_faster_whisper_builds_16k_mono_wav_command(tmp_path, monkeypatch):
    media = _make_media(tmp_path)
    commands = []

    def fake_runner(command, **kwargs):
        commands.append(command)
        # 让 ffmpeg 提音"生成"一个 wav，供后续 model.transcribe 读取
        return FakeCompleted(returncode=0)

    class FakeSegment:
        def __init__(self, start, end, text):
            self.start, self.end, self.text = start, end, text

    class FakeModel:
        def __init__(self, model, device, compute_type):
            captured["model_args"] = (model, device, compute_type)

        def transcribe(self, wav, **kwargs):
            captured["transcribe_kwargs"] = kwargs
            return [FakeSegment(0.0, 1.5, "第一句"), FakeSegment(1.5, 3.0, "第二句")], object()

    captured = {}
    fake_module = types.SimpleNamespace(WhisperModel=FakeModel)
    monkeypatch.setitem(sys.modules, "faster_whisper", fake_module)

    result = transcribe_media(media, ASRConfig(provider="faster_whisper", initial_prompt="术语"), runner=fake_runner)

    wav_cmd = commands[0]
    assert wav_cmd[0] == "ffmpeg"
    assert "-ac" in wav_cmd and wav_cmd[wav_cmd.index("-ac") + 1] == "1"
    assert "-ar" in wav_cmd and wav_cmd[wav_cmd.index("-ar") + 1] == "16000"
    assert captured["model_args"] == ("small", "cpu", "int8")
    assert captured["transcribe_kwargs"]["language"] == "zh"
    assert captured["transcribe_kwargs"]["initial_prompt"] == "术语"
    assert captured["transcribe_kwargs"]["vad_filter"] is True
    assert result.provider == "faster_whisper"
    assert result.text == "第一句\n第二句"
    assert result.segments == ((0.0, 1.5, "第一句"), (1.5, 3.0, "第二句"))


def test_faster_whisper_ffmpeg_failure_raises(tmp_path, monkeypatch):
    media = _make_media(tmp_path)
    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=object))

    def failing_runner(command, **kwargs):
        return FakeCompleted(returncode=1)

    with pytest.raises(ASRError, match="ffmpeg"):
        transcribe_media(media, ASRConfig(provider="faster_whisper"), runner=failing_runner)


def test_faster_whisper_missing_package_raises_install_hint(tmp_path, monkeypatch):
    media = _make_media(tmp_path)
    monkeypatch.delitem(sys.modules, "faster_whisper", raising=False)

    # 强制 import faster_whisper 抛 ImportError
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "faster_whisper":
            raise ImportError("no module")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(ASRError, match=r'pip install -e ".\[asr\]"'):
        transcribe_media(media, ASRConfig(provider="faster_whisper"), runner=lambda *a, **k: FakeCompleted())


# ---- openai provider -----------------------------------------------------


class FakeResponse:
    def __init__(self, body):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self._body).encode("utf-8")


def test_openai_provider_posts_multipart_with_auth(tmp_path, monkeypatch):
    media = _make_media(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-asr-123")
    captured = {}

    def fake_runner(command, **kwargs):
        # 模拟 ffmpeg 生成小 mp3
        out = command[-1]
        from pathlib import Path

        Path(out).write_bytes(b"tiny mp3 payload")
        return FakeCompleted(returncode=0)

    def fake_urlopen(request, timeout):
        captured["request"] = request
        captured["body"] = request.data
        return FakeResponse({"text": "云端转写文本"})

    monkeypatch.setattr("video_factory.asr.urlopen", fake_urlopen)

    result = transcribe_media(media, ASRConfig(provider="openai"), runner=fake_runner)

    request = captured["request"]
    assert request.full_url == "https://api.openai.com/v1/audio/transcriptions"
    assert request.get_header("Authorization") == "Bearer sk-asr-123"
    content_type = request.get_header("Content-type")
    assert content_type.startswith("multipart/form-data; boundary=----videofactory")
    body = captured["body"].decode("utf-8", errors="replace")
    assert 'name="model"' in body
    assert "gpt-4o-mini-transcribe" in body
    assert 'name="file"; filename="audio.mp3"' in body
    assert result.provider == "openai"
    assert result.text == "云端转写文本"
    assert result.segments == ()


def test_openai_missing_key_raises(tmp_path, monkeypatch):
    media = _make_media(tmp_path)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with pytest.raises(ASRError, match="OPENAI_API_KEY"):
        transcribe_media(media, ASRConfig(provider="openai"), runner=lambda *a, **k: FakeCompleted())


def test_openai_http_error_normalized(tmp_path, monkeypatch):
    from urllib.error import HTTPError

    media = _make_media(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-asr-123")

    def fake_runner(command, **kwargs):
        from pathlib import Path

        Path(command[-1]).write_bytes(b"tiny")
        return FakeCompleted(returncode=0)

    def raising_urlopen(request, timeout):
        raise HTTPError(request.full_url, 429, "Too Many Requests", {}, None)

    monkeypatch.setattr("video_factory.asr.urlopen", raising_urlopen)

    with pytest.raises(ASRError, match="HTTP 429"):
        transcribe_media(media, ASRConfig(provider="openai"), runner=fake_runner)


def test_openai_chunks_when_over_size_limit(tmp_path, monkeypatch):
    import video_factory.asr as asr

    media = _make_media(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-asr-123")
    monkeypatch.setattr(asr, "OPENAI_MAX_UPLOAD_BYTES", 4)  # 强制走分块
    monkeypatch.setattr(asr, "OPENAI_CHUNK_SECONDS", 600)
    chunk_commands = []

    def fake_runner(command, **kwargs):
        from pathlib import Path

        if "-show_entries" in command:  # ffprobe
            return FakeCompleted(returncode=0, stdout=json.dumps({"format": {"duration": "900"}}))
        out = command[-1]
        Path(out).write_bytes(b"this payload is definitely bigger than four bytes")
        if "-ss" in command:
            chunk_commands.append(command)
        return FakeCompleted(returncode=0)

    calls = []

    def fake_urlopen(request, timeout):
        calls.append(request)
        return FakeResponse({"text": f"分块{len(calls)}"})

    monkeypatch.setattr("video_factory.asr.urlopen", fake_urlopen)

    result = transcribe_media(media, ASRConfig(provider="openai"), runner=fake_runner)

    # 900 秒 / 600 秒每块 = 2 块
    assert len(chunk_commands) == 2
    assert chunk_commands[0][chunk_commands[0].index("-ss") + 1] == "0"
    assert chunk_commands[1][chunk_commands[1].index("-ss") + 1] == "600"
    assert result.text == "分块1\n分块2"


def test_openai_long_media_skips_full_extraction(tmp_path, monkeypatch):
    """预估体积超限时必须直接分块，不允许整段提取后丢弃再重提（长视频白跑一遍转码）。"""
    import video_factory.asr as asr

    media = _make_media(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-asr-123")
    monkeypatch.setattr(asr, "OPENAI_MAX_UPLOAD_BYTES", 4)
    ffmpeg_commands = []

    def fake_runner(command, **kwargs):
        from pathlib import Path

        if "-show_entries" in command:  # ffprobe
            return FakeCompleted(returncode=0, stdout=json.dumps({"format": {"duration": "900"}}))
        ffmpeg_commands.append(command)
        Path(command[-1]).write_bytes(b"chunk bytes")
        return FakeCompleted(returncode=0)

    monkeypatch.setattr(
        "video_factory.asr.urlopen", lambda request, timeout: FakeResponse({"text": "片段"})
    )

    transcribe_media(media, ASRConfig(provider="openai"), runner=fake_runner)

    assert ffmpeg_commands, "应有分块提取命令"
    # 每一条 ffmpeg 提取命令都必须带 -ss（分块），不允许出现无 -ss 的整段提取
    assert all("-ss" in command for command in ffmpeg_commands)


def test_faster_whisper_model_failure_normalized(tmp_path, monkeypatch):
    """模型加载/推理抛任意异常时必须归一化为 ASRError（中文提示），不能裸 traceback。"""
    media = _make_media(tmp_path)

    class BrokenModel:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("unsupported compute type int8 on this CPU")

    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=BrokenModel))

    with pytest.raises(ASRError, match="faster-whisper 转写失败"):
        transcribe_media(
            media,
            ASRConfig(provider="faster_whisper"),
            runner=lambda *a, **k: FakeCompleted(returncode=0),
        )


# ---- auto 选择三分支 -----------------------------------------------------


def test_auto_prefers_faster_whisper_when_importable(tmp_path, monkeypatch):
    media = _make_media(tmp_path)

    class FakeSegment:
        start, end, text = 0.0, 1.0, "内容"

    class FakeModel:
        def __init__(self, *a, **k):
            pass

        def transcribe(self, *a, **k):
            return [FakeSegment()], object()

    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=FakeModel))

    result = transcribe_media(media, ASRConfig(provider="auto"), runner=lambda *a, **k: FakeCompleted())
    assert result.provider == "faster_whisper"


def test_auto_falls_back_to_openai_when_key_present(tmp_path, monkeypatch):
    media = _make_media(tmp_path)
    monkeypatch.delitem(sys.modules, "faster_whisper", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-asr-123")

    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "faster_whisper":
            raise ImportError("no module")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    def fake_runner(command, **kwargs):
        from pathlib import Path

        Path(command[-1]).write_bytes(b"tiny")
        return FakeCompleted(returncode=0)

    monkeypatch.setattr("video_factory.asr.urlopen", lambda req, timeout: FakeResponse({"text": "云端"}))

    result = transcribe_media(media, ASRConfig(provider="auto"), runner=fake_runner)
    assert result.provider == "openai"


def test_auto_raises_when_no_backend(tmp_path, monkeypatch):
    media = _make_media(tmp_path)
    monkeypatch.delitem(sys.modules, "faster_whisper", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "faster_whisper":
            raise ImportError("no module")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(ASRError, match="找不到可用的转写后端"):
        transcribe_media(media, ASRConfig(provider="auto"), runner=lambda *a, **k: FakeCompleted())


# ---- 输出与时间戳 --------------------------------------------------------


def test_format_timestamp():
    assert format_timestamp(0) == "00:00:00,000"
    assert format_timestamp(3661.5) == "01:01:01,500"


def test_write_outputs_with_segments(tmp_path):
    result = ASRResult(
        text="第一句\n第二句",
        segments=((0.0, 1.5, "第一句"), (1.5, 3.0, "第二句")),
        provider="faster_whisper",
        model="small",
    )
    outputs = write_transcript_outputs(result, tmp_path)

    assert outputs["transcript_txt"].read_text(encoding="utf-8") == "第一句\n第二句\n"
    srt = outputs["transcript_srt"].read_text(encoding="utf-8")
    assert "00:00:00,000 --> 00:00:01,500" in srt
    assert "第一句" in srt
    assert srt.startswith("1\n")


def test_write_outputs_without_segments_only_txt(tmp_path):
    result = ASRResult(text="整段文本", segments=(), provider="openai", model="gpt-4o-mini-transcribe")
    outputs = write_transcript_outputs(result, tmp_path)

    assert "transcript_srt" not in outputs
    assert outputs["transcript_txt"].read_text(encoding="utf-8") == "整段文本\n"
    assert "未返回逐句时间分段" in outputs["transcript_notes"].read_text(encoding="utf-8")


def test_missing_media_file_raises(tmp_path):
    with pytest.raises(ASRError, match="不存在"):
        transcribe_media(tmp_path / "nope.mp4", ASRConfig(provider="faster_whisper"))
