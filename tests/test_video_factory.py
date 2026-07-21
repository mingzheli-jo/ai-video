import json
import wave
from pathlib import Path

import pytest

from video_factory import (
    TTSConfig,
    TTSProviderError,
)
from video_factory.legacy_v1 import (
    VideoConfig,
    build_ffmpeg_command,
    build_tone_track,
    build_video_plan,
    render_video,
    synthesize_voiceover,
    wrap_video_text,
    write_artifacts,
)
from video_factory.pipeline import (
    build_doubao_speech_payload,
    build_openai_speech_payload,
)
from video_factory.cli import build_tts_config, parse_args


def test_default_creator_monetization_plan_matches_requested_contract():
    config = VideoConfig()

    plan = build_video_plan(config)

    assert plan.width == 1080
    assert plan.height == 1920
    assert plan.fps == 30
    assert plan.config.target_duration == 45
    assert plan.config.style == "ai_realistic_montage"
    assert plan.config.goal == "retention_growth"
    assert plan.config.tts_provider == "openai"
    assert plan.config.voice == "marin"
    assert plan.config.tts_model == "gpt-4o-mini-tts"
    assert plan.segments[0].start == 0
    assert plan.segments[0].end == 3
    assert plan.segments[-1].end == 45
    assert [segment.role for segment in plan.segments] == [
        "hook",
        "counterintuitive_truth",
        "step_1",
        "step_2",
        "step_3",
        "summary",
        "follow",
    ]
    assert "关注" in plan.segments[-1].narration
    assert all(segment.visual_prompt for segment in plan.segments)
    assert all(segment.screen_text for segment in plan.segments)


def test_portugal_dr_congo_plan_matches_long_form_contract():
    from video_factory.legacy_v1 import build_portugal_dr_congo_prediction_plan

    plan = build_portugal_dr_congo_prediction_plan()

    assert plan.width == 1920
    assert plan.height == 1080
    assert plan.fps == 30
    assert plan.config.target_duration == 340
    assert plan.config.style == "premium_studio_tutorial"
    assert plan.config.goal == "sports_prediction_retention"
    assert "Portugal vs DR Congo" in plan.config.topic
    assert [segment.role for segment in plan.segments] == [
        "hook",
        "match_setup",
        "portugal_advantage",
        "dr_congo_risk",
        "ai_skill_simulation",
        "final_prediction",
    ]
    assert plan.segments[0].start == 0
    assert plan.segments[-1].end == 340
    assert plan.segments[0].english_subtitle.startswith("This is not")
    assert plan.segments[4].panel_type == "simulation"
    assert "Portugal 2:1 DR Congo" in plan.segments[-1].screen_text
    assert all(segment.key_points for segment in plan.segments)


def test_portugal_dr_congo_plan_has_long_form_narration_density():
    from video_factory.legacy_v1 import build_portugal_dr_congo_prediction_plan

    plan = build_portugal_dr_congo_prediction_plan()
    total_narration_chars = sum(len(segment.narration) for segment in plan.segments)

    assert total_narration_chars >= 1750
    assert total_narration_chars <= 2050
    assert all(len(segment.narration) / segment.duration >= 4.0 for segment in plan.segments)


def test_write_artifacts_creates_script_storyboard_prompts_and_srt(tmp_path):
    plan = build_video_plan(VideoConfig(topic="流量分账收入公开"))

    artifact_paths = write_artifacts(plan, tmp_path)

    assert artifact_paths["script"].name == "script.md"
    assert artifact_paths["storyboard"].name == "storyboard.json"
    assert artifact_paths["prompts"].name == "visual_prompts.md"
    assert artifact_paths["subtitles"].name == "subtitles.srt"

    script = artifact_paths["script"].read_text(encoding="utf-8")
    assert "# Codex 自动化 AI 混剪短视频脚本" in script
    assert "流量分账收入公开" in script
    assert "0-3s" in script

    storyboard = json.loads(artifact_paths["storyboard"].read_text(encoding="utf-8"))
    assert storyboard["config"]["target_duration"] == 45
    assert storyboard["config"]["style"] == "ai_realistic_montage"
    assert len(storyboard["segments"]) == 7
    assert storyboard["segments"][0]["start"] == 0
    assert storyboard["segments"][-1]["end"] == 45

    prompts = artifact_paths["prompts"].read_text(encoding="utf-8")
    assert "AI realistic montage" in prompts
    assert "no copied Douyin footage" in prompts

    subtitles = artifact_paths["subtitles"].read_text(encoding="utf-8")
    assert "00:00:00,000 --> 00:00:03,000" in subtitles
    assert "00:00:42,000 --> 00:00:45,000" in subtitles


def test_portugal_dr_congo_artifacts_include_bilingual_subtitles_and_sources(tmp_path):
    from video_factory.legacy_v1 import build_portugal_dr_congo_prediction_plan

    plan = build_portugal_dr_congo_prediction_plan()
    artifact_paths = write_artifacts(plan, tmp_path)

    script = artifact_paths["script"].read_text(encoding="utf-8")
    assert "Portugal vs DR Congo" in script
    assert "premium_studio_tutorial" in script
    assert "FIFA official fixtures page" in script
    assert "model simulation" in script
    assert "English Subtitle" in script

    subtitles = artifact_paths["subtitles"].read_text(encoding="utf-8")
    assert "葡萄牙对刚果民主共和国" in subtitles
    assert "This is not a simple mismatch" in subtitles

    prompts = artifact_paths["prompts"].read_text(encoding="utf-8")
    assert "16:9 horizontal composition" in prompts
    assert "AI silhouette presenter" in prompts

    storyboard = json.loads(artifact_paths["storyboard"].read_text(encoding="utf-8"))
    assert storyboard["segments"][0]["english_subtitle"].startswith("This is not")
    assert storyboard["segments"][4]["panel_type"] == "simulation"
    assert storyboard["segments"][5]["key_points"][-1] == "model estimate only"


def test_portugal_visual_asset_manifest_records_safe_sources():
    from video_factory.legacy_v1 import load_visual_asset_manifest

    manifest = load_visual_asset_manifest(
        Path("video_factory/assets/portugal_dr_congo/asset_manifest.json")
    )

    assert manifest["license_policy"] == "No official FIFA, broadcaster, federation, or match-highlight footage."
    assert len(manifest["assets"]) >= 8
    assert {asset["segment_role"] for asset in manifest["assets"]} >= {
        "hook",
        "match_setup",
        "portugal_advantage",
        "dr_congo_risk",
        "ai_skill_simulation",
        "final_prediction",
    }
    assert all(
        asset["license_url"] == "https://mixkit.co/license/#videoFree"
        or asset["kind"] == "generated_image"
        for asset in manifest["assets"]
    )
    assert all("official" not in asset["notes"].lower() for asset in manifest["assets"])


def test_portugal_visual_asset_manifest_schedules_only_real_video_footage():
    from video_factory.legacy_v1 import build_portugal_dr_congo_prediction_plan
    from video_factory.legacy_v1 import load_visual_asset_manifest

    plan = build_portugal_dr_congo_prediction_plan()
    manifest = load_visual_asset_manifest(
        Path("video_factory/assets/portugal_dr_congo/asset_manifest.json")
    )
    assets_by_key = {asset["key"]: asset for asset in manifest["assets"]}
    scheduled_keys = [
        asset_key
        for segment in plan.segments
        for asset_key in manifest["segment_asset_order"][segment.role]
    ]

    assert manifest["overlay_style"] == "cinematic_broll"
    assert len(scheduled_keys) >= 10
    assert all(assets_by_key[asset_key]["kind"] == "video" for asset_key in scheduled_keys)
    assert all("/broll/" in assets_by_key[asset_key]["local_path"] for asset_key in scheduled_keys)


def test_build_ffmpeg_command_targets_vertical_douyin_export(tmp_path):
    frames = tmp_path / "frames.txt"
    audio = tmp_path / "voice.aiff"
    output = tmp_path / "sample.mp4"

    command = build_ffmpeg_command(frames, audio, output)

    assert command[0] == "ffmpeg"
    assert "-safe" in command
    assert str(frames) in command
    assert str(audio) in command
    assert "scale=1080:1920" in command
    assert "-r" in command
    assert "30" in command
    assert str(output) == command[-1]


def test_build_ffmpeg_command_targets_horizontal_premium_export(tmp_path):
    frames = tmp_path / "frames.txt"
    audio = tmp_path / "voice.wav"
    output = tmp_path / "premium.mp4"

    command = build_ffmpeg_command(
        frames,
        audio,
        output,
        duration=340,
        fps=30,
        width=1920,
        height=1080,
    )

    assert command[0] == "ffmpeg"
    assert str(frames) in command
    assert str(audio) in command
    assert "scale=1920:1080" in command
    assert "-t" in command
    assert "340" in command
    assert str(output) == command[-1]


def test_build_tone_track_creates_audible_fallback_audio(tmp_path):
    plan = build_video_plan(VideoConfig())
    output = tmp_path / "voiceover.wav"

    build_tone_track(plan, output)

    assert output.exists()
    assert output.stat().st_size > 1000


def test_premium_studio_frame_renderer_creates_horizontal_dark_tech_frame(tmp_path):
    from PIL import Image
    from video_factory.legacy_v1 import build_portugal_dr_congo_prediction_plan
    from video_factory.legacy_v1 import _render_frames

    plan = build_portugal_dr_congo_prediction_plan()
    frames = _render_frames(plan, tmp_path, frames_per_segment=1)

    assert len(frames) == len(plan.segments)
    with Image.open(frames[0]) as image:
        assert image.size == (1920, 1080)
        assert image.getpixel((30, 30))[1] > image.getpixel((30, 30))[0]
        assert image.getpixel((1520, 210))[1] > 80


def test_premium_overlay_renderer_outputs_transparent_1080p_panel(tmp_path):
    from PIL import Image
    from video_factory.legacy_v1 import build_portugal_dr_congo_prediction_plan
    from video_factory.legacy_v1 import _draw_premium_overlay_frame

    plan = build_portugal_dr_congo_prediction_plan()
    overlay = tmp_path / "overlay.png"

    _draw_premium_overlay_frame(plan, plan.segments[0], 0, overlay, progress=0.5)

    with Image.open(overlay) as image:
        assert image.mode == "RGBA"
        assert image.size == (1920, 1080)
        assert image.getpixel((10, 10))[3] == 0
        assert image.getpixel((900, 190))[3] > 180


def test_cinematic_broll_overlay_renderer_stays_lightweight(tmp_path):
    from PIL import Image
    from video_factory.legacy_v1 import build_portugal_dr_congo_prediction_plan
    from video_factory.legacy_v1 import _draw_cinematic_broll_overlay_frame

    plan = build_portugal_dr_congo_prediction_plan()
    overlay = tmp_path / "overlay.png"

    _draw_cinematic_broll_overlay_frame(plan, plan.segments[0], 0, overlay, progress=0.5)

    with Image.open(overlay) as image:
        assert image.mode == "RGBA"
        assert image.size == (1920, 1080)
        assert image.getpixel((960, 410))[3] == 0
        assert image.getpixel((1770, 92))[3] > 100
        assert image.getpixel((960, 930))[3] > 140
        opaque_pixels = sum(1 for pixel in image.getdata() if pixel[3] > 80)
        assert opaque_pixels / (1920 * 1080) < 0.23


def test_build_segment_video_command_uses_video_background(tmp_path):
    from video_factory.legacy_v1 import build_segment_video_command

    command = build_segment_video_command(
        background_path=tmp_path / "broll.mp4",
        overlay_path=tmp_path / "overlay.png",
        output_path=tmp_path / "segment.mp4",
        duration=25,
        width=1920,
        height=1080,
        fps=30,
        background_kind="video",
    )

    assert command[:2] == ["ffmpeg", "-y"]
    assert "-stream_loop" in command
    assert "scale=1920:1080:force_original_aspect_ratio=increase" in " ".join(command)
    assert str(tmp_path / "segment.mp4") == command[-1]


def test_build_segment_video_command_uses_image_background(tmp_path):
    from video_factory.legacy_v1 import build_segment_video_command

    command = build_segment_video_command(
        background_path=tmp_path / "keyframe.png",
        overlay_path=tmp_path / "overlay.png",
        output_path=tmp_path / "segment.mp4",
        duration=55,
        width=1920,
        height=1080,
        fps=30,
        background_kind="generated_image",
    )

    assert "-loop" in command
    assert str(tmp_path / "keyframe.png") in command
    assert "-t" in command
    assert "55" in command


def test_concat_segment_videos_pads_short_audio_to_target_duration(tmp_path, monkeypatch):
    from video_factory import legacy_v1

    segment = tmp_path / "segment.mp4"
    audio = tmp_path / "voiceover.wav"
    output = tmp_path / "release.mp4"
    segment.write_bytes(b"video")
    audio.write_bytes(b"audio")
    captured = {}

    def fake_run(command, check):
        captured["command"] = command
        captured["check"] = check

    monkeypatch.setattr(legacy_v1.subprocess, "run", fake_run)

    legacy_v1._concat_segment_videos_with_audio([segment], audio, output, duration=340)

    assert captured["check"] is True
    assert "-shortest" not in captured["command"]
    assert "-af" in captured["command"]
    assert "apad" in captured["command"]
    assert "-t" in captured["command"]
    assert "340" in captured["command"]


def test_wrap_video_text_keeps_trailing_punctuation_with_previous_line():
    lines = wrap_video_text("想要这套选题模板，关注我，评论分账。", 17)

    assert lines == ["想要这套选题模板，关注我，评论分账。"]


def test_premium_title_wrap_keeps_dr_congo_together():
    from video_factory.legacy_v1 import _wrap_premium_title_text

    lines = _wrap_premium_title_text("AI预测\n葡萄牙 2:1 DR Congo", 13)

    assert lines == ["AI预测", "葡萄牙 2:1", "DR Congo"]


def test_pixel_width_wrap_keeps_mixed_subtitle_inside_box():
    from video_factory.legacy_v1 import _font, _wrap_text_to_pixel_width

    font = _font(36, bold=True)
    text = "这场葡萄牙对刚果民主共和国，表面看是强弱局，但我的AI Skill给出的结论不是大胜，而是葡萄牙二比一小胜。"
    lines = _wrap_text_to_pixel_width(text, font, 1480)

    assert len(lines) >= 2
    assert all(font.getlength(line) <= 1480 for line in lines)


def test_openai_speech_payload_matches_release_contract():
    plan = build_video_plan(VideoConfig())
    tts_config = TTSConfig(
        provider="openai",
        voice="cedar",
        voice_instructions="用中文短视频教程口吻，语速偏快但清楚。",
    )

    payload = build_openai_speech_payload(plan, tts_config)

    assert payload["model"] == "gpt-4o-mini-tts"
    assert payload["voice"] == "cedar"
    assert payload["response_format"] == "wav"
    assert "用中文短视频教程口吻" in payload["instructions"]
    assert "十万粉账号" in payload["input"]


def test_openai_tts_without_key_fails_unless_fallback_allowed(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    plan = build_video_plan(VideoConfig())
    output = tmp_path / "voiceover.wav"

    with pytest.raises(TTSProviderError, match="OPENAI_API_KEY"):
        synthesize_voiceover(plan, output, TTSConfig(provider="openai"))

    synthesize_voiceover(plan, output, TTSConfig(provider="openai", allow_fallback=True))

    assert output.exists()
    assert output.stat().st_size > 1000
    assert "fallback" in (tmp_path / "voiceover_notes.txt").read_text(encoding="utf-8").lower()


def test_doubao_speech_payload_matches_release_contract():
    plan = build_video_plan(VideoConfig())
    tts_config = TTSConfig(provider="doubao", voice="zh_female_shuangkuaisisi_moon_bigtts")

    payload = build_doubao_speech_payload(plan, tts_config, appid="appid-123", token="token-456")

    assert payload["app"]["appid"] == "appid-123"
    assert payload["app"]["cluster"] == "volcano_tts"
    assert payload["audio"]["voice_type"] == "zh_female_shuangkuaisisi_moon_bigtts"
    assert payload["audio"]["encoding"] == "mp3"
    assert payload["request"]["operation"] == "query"
    assert payload["request"]["reqid"]
    assert "十万粉账号" in payload["request"]["text"]


def test_doubao_tts_without_credentials_fails_unless_fallback_allowed(tmp_path, monkeypatch):
    monkeypatch.delenv("VOLC_TTS_APIKEY", raising=False)
    monkeypatch.delenv("VOLC_TTS_APPID", raising=False)
    monkeypatch.delenv("VOLC_TTS_TOKEN", raising=False)
    plan = build_video_plan(VideoConfig())
    output = tmp_path / "voiceover.wav"

    with pytest.raises(TTSProviderError, match="VOLC_TTS_APPID"):
        synthesize_voiceover(plan, output, TTSConfig(provider="doubao"))

    synthesize_voiceover(plan, output, TTSConfig(provider="doubao", allow_fallback=True))

    assert output.exists()
    assert "fallback" in (tmp_path / "voiceover_notes.txt").read_text(encoding="utf-8").lower()


def test_doubao_tts_synthesizes_and_converts_to_wav(tmp_path, monkeypatch):
    import base64

    plan = build_video_plan(VideoConfig())
    output = tmp_path / "voiceover.wav"
    monkeypatch.setenv("VOLC_TTS_APPID", "appid-123")
    monkeypatch.setenv("VOLC_TTS_TOKEN", "token-456")
    requests = []

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            data = base64.b64encode(b"fake-mp3-bytes").decode("ascii")
            return json.dumps({"code": 3000, "data": data}).encode("utf-8")

    def fake_urlopen(request, timeout):
        requests.append(request)
        return FakeResponse()

    def fake_run(command, **kwargs):
        if Path(command[0]).stem.lower() == "ffmpeg":
            _write_test_wav(output, duration_seconds=45)

    monkeypatch.setattr("video_factory.pipeline.urlopen", fake_urlopen)
    monkeypatch.setattr("video_factory.pipeline.subprocess.run", fake_run)
    monkeypatch.setattr("video_factory.pipeline._probe_duration_seconds", lambda path: 45)

    result = synthesize_voiceover(
        plan,
        output,
        TTSConfig(provider="doubao", voice="zh_male_yuanboxiaoshu_moon_bigtts"),
    )

    assert result.provider == "doubao"
    assert result.used_fallback is False
    assert output.exists()
    request = requests[0]
    assert request.get_header("Authorization") == "Bearer;token-456"
    body = json.loads(request.data.decode("utf-8"))
    assert body["app"]["appid"] == "appid-123"
    assert body["audio"]["voice_type"] == "zh_male_yuanboxiaoshu_moon_bigtts"
    assert body["request"]["operation"] == "query"
    notes = (tmp_path / "voiceover_notes.txt").read_text(encoding="utf-8")
    assert "Doubao" in notes


def test_resolve_provider_voice_falls_back_on_default_and_marin():
    from video_factory.pipeline import (
        DOUBAO_DEFAULT_VOICE,
        EDGE_DEFAULT_VOICE,
        _resolve_provider_voice,
    )

    # 空音色或跨 provider 默认 "marin" 都回落到 provider 自己的默认。
    assert _resolve_provider_voice("", EDGE_DEFAULT_VOICE) == EDGE_DEFAULT_VOICE
    assert _resolve_provider_voice("marin", EDGE_DEFAULT_VOICE) == EDGE_DEFAULT_VOICE
    assert _resolve_provider_voice("marin", DOUBAO_DEFAULT_VOICE) == DOUBAO_DEFAULT_VOICE
    # 用户显式指定的音色必须原样保留。
    assert _resolve_provider_voice("zh-CN-YunxiNeural", EDGE_DEFAULT_VOICE) == "zh-CN-YunxiNeural"


def test_doubao_default_voice_used_when_config_voice_is_marin(tmp_path, monkeypatch):
    # 回归：--tts doubao 不给 --voice 时 TTSConfig.voice 是 "marin"（openai 音色），
    # 会被火山引擎拒；必须回落到豆包默认音色。
    import base64

    from video_factory.pipeline import DOUBAO_DEFAULT_VOICE, synthesize_voiceover_text

    output = tmp_path / "voiceover.wav"
    monkeypatch.setenv("VOLC_TTS_APPID", "appid-123")
    monkeypatch.setenv("VOLC_TTS_TOKEN", "token-456")
    requests = []

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            data = base64.b64encode(b"fake-mp3-bytes").decode("ascii")
            return json.dumps({"code": 3000, "data": data}).encode("utf-8")

    monkeypatch.setattr("video_factory.pipeline.urlopen", lambda request, timeout: requests.append(request) or FakeResponse())
    monkeypatch.setattr(
        "video_factory.pipeline.subprocess.run",
        lambda command, **kwargs: _write_test_wav(output, duration_seconds=20) if Path(command[0]).stem.lower() == "ffmpeg" else None,
    )
    monkeypatch.setattr("video_factory.pipeline._probe_duration_seconds", lambda path: 20)

    synthesize_voiceover_text("测试文案", output, TTSConfig(provider="doubao"))

    body = json.loads(requests[0].data.decode("utf-8"))
    assert body["audio"]["voice_type"] == DOUBAO_DEFAULT_VOICE


# ---------- 豆包 v3（新版控制台 API Key）----------

@pytest.fixture(autouse=True)
def _isolate_doubao_apikey(monkeypatch):
    # v3 API Key 优先级高于 appid+token：本机若设了 VOLC_TTS_APIKEY，
    # 会把上面全部 v1 测试拐进 v3 路径，必须隔离。
    monkeypatch.delenv("VOLC_TTS_APIKEY", raising=False)


def _v3_stream_body(*chunks, done=True):
    import base64 as _b64

    lines = [
        json.dumps({"code": 0, "message": "", "data": _b64.b64encode(c).decode("ascii")})
        for c in chunks
    ]
    if done:
        lines.append(json.dumps({"code": 20000000, "message": "ok"}))
    return "\n".join(lines).encode("utf-8")


class _V3FakeResponse:
    def __init__(self, body):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self._body


def test_doubao_v3_payload_matches_contract():
    from video_factory.pipeline import build_doubao_v3_payload

    tts_config = TTSConfig(provider="doubao", voice="zh_female_shuangkuaisisi_moon_bigtts")
    payload = build_doubao_v3_payload("测试文案", tts_config)

    assert payload["user"]["uid"] == "video_factory"
    assert payload["req_params"]["text"] == "测试文案"
    assert payload["req_params"]["speaker"] == "zh_female_shuangkuaisisi_moon_bigtts"
    assert payload["req_params"]["audio_params"]["format"] == "mp3"


def test_doubao_v3_api_key_mode_synthesizes(tmp_path, monkeypatch):
    from video_factory.pipeline import synthesize_voiceover_text

    output = tmp_path / "voiceover.wav"
    monkeypatch.setenv("VOLC_TTS_APIKEY", "vk-test-1")
    monkeypatch.delenv("VOLC_TTS_APPID", raising=False)
    monkeypatch.delenv("VOLC_TTS_TOKEN", raising=False)
    requests = []
    monkeypatch.setattr(
        "video_factory.pipeline.urlopen",
        lambda request, timeout: requests.append(request)
        or _V3FakeResponse(_v3_stream_body(b"part1-", b"part2")),
    )
    written = {}

    def fake_run(command, **kwargs):
        if Path(command[0]).stem.lower() == "ffmpeg":
            src = Path(command[command.index("-i") + 1])
            written["mp3"] = src.read_bytes()
            _write_test_wav(output, duration_seconds=20)

    monkeypatch.setattr("video_factory.pipeline.subprocess.run", fake_run)
    monkeypatch.setattr("video_factory.pipeline._probe_duration_seconds", lambda path: 20)

    result = synthesize_voiceover_text("测试文案", output, TTSConfig(provider="doubao"))

    request = requests[0]
    assert request.full_url.endswith("/api/v3/tts/unidirectional")
    assert request.get_header("X-api-key") == "vk-test-1"
    # 默认音色是刘飞（uranus 系）→ 资源ID 自动匹配 seed-tts-2.0（错配会报 55000000）。
    assert request.get_header("X-api-resource-id") == "seed-tts-2.0"
    body = json.loads(request.data.decode("utf-8"))
    assert "req_params" in body  # v3 报文，不是 v1 的 app/audio 结构
    assert written["mp3"] == b"part1-part2"  # 流式分片按序拼接
    assert result.model == "volcengine/v3-seed-tts"


def test_doubao_v3_resource_id_matches_voice_family(monkeypatch):
    # 根因回归：v3 资源ID 必须按音色族匹配，错配豆包报 55000000（resource ID mismatched
    # with speaker），配音失败→assemble 中断→视频生不出来。uranus 系（刘飞）要 2.0、
    # moon 系（爽快思思）要 1.0；VOLC_TTS_RESOURCE_ID 显式设置时优先。
    from video_factory.pipeline import (
        DOUBAO_V3_RESOURCE_ID_ENV,
        _doubao_v3_resource_for_voice,
    )

    monkeypatch.delenv(DOUBAO_V3_RESOURCE_ID_ENV, raising=False)
    assert _doubao_v3_resource_for_voice("zh_male_liufei_uranus_bigtts") == "seed-tts-2.0"
    assert _doubao_v3_resource_for_voice("zh_female_shuangkuaisisi_moon_bigtts") == "seed-tts-1.0"
    assert _doubao_v3_resource_for_voice("") == "seed-tts-1.0"
    monkeypatch.setenv(DOUBAO_V3_RESOURCE_ID_ENV, "seed-tts-custom")
    assert _doubao_v3_resource_for_voice("zh_male_liufei_uranus_bigtts") == "seed-tts-custom"


def test_doubao_apikey_takes_priority_over_legacy(tmp_path, monkeypatch):
    from video_factory.pipeline import synthesize_voiceover_text

    output = tmp_path / "voiceover.wav"
    monkeypatch.setenv("VOLC_TTS_APIKEY", "vk-test-1")
    monkeypatch.setenv("VOLC_TTS_APPID", "appid-123")
    monkeypatch.setenv("VOLC_TTS_TOKEN", "token-456")
    requests = []
    monkeypatch.setattr(
        "video_factory.pipeline.urlopen",
        lambda request, timeout: requests.append(request) or _V3FakeResponse(_v3_stream_body(b"x")),
    )
    monkeypatch.setattr(
        "video_factory.pipeline.subprocess.run",
        lambda command, **kwargs: _write_test_wav(output, duration_seconds=20)
        if Path(command[0]).stem.lower() == "ffmpeg"
        else None,
    )
    monkeypatch.setattr("video_factory.pipeline._probe_duration_seconds", lambda path: 20)

    synthesize_voiceover_text("测试文案", output, TTSConfig(provider="doubao"))

    assert "/api/v3/" in requests[0].full_url  # 两套凭据都在时 API Key 优先


def test_doubao_v3_stream_error_raises(tmp_path, monkeypatch):
    from video_factory.pipeline import synthesize_voiceover_text

    output = tmp_path / "voiceover.wav"
    monkeypatch.setenv("VOLC_TTS_APIKEY", "vk-test-1")
    body = json.dumps({"code": 45000001, "message": "invalid speaker"}).encode("utf-8")
    monkeypatch.setattr("video_factory.pipeline.urlopen", lambda request, timeout: _V3FakeResponse(body))

    with pytest.raises(TTSProviderError, match="45000001"):
        synthesize_voiceover_text("测试文案", output, TTSConfig(provider="doubao"))


def test_doubao_tts_error_code_raises_provider_error(tmp_path, monkeypatch):
    plan = build_video_plan(VideoConfig())
    output = tmp_path / "voiceover.wav"
    monkeypatch.setenv("VOLC_TTS_APPID", "appid-123")
    monkeypatch.setenv("VOLC_TTS_TOKEN", "token-456")

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps({"code": 4001, "message": "invalid voice_type"}).encode("utf-8")

    monkeypatch.setattr("video_factory.pipeline.urlopen", lambda request, timeout: FakeResponse())

    with pytest.raises(TTSProviderError, match="Doubao TTS error 4001"):
        synthesize_voiceover(plan, output, TTSConfig(provider="doubao"))


def test_doubao_tts_malformed_base64_raises_provider_error(tmp_path, monkeypatch):
    plan = build_video_plan(VideoConfig())
    output = tmp_path / "voiceover.wav"
    monkeypatch.setenv("VOLC_TTS_APPID", "appid-123")
    monkeypatch.setenv("VOLC_TTS_TOKEN", "token-456")

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps({"code": 3000, "data": "!!!not-base64!!!"}).encode("utf-8")

    monkeypatch.setattr("video_factory.pipeline.urlopen", lambda request, timeout: FakeResponse())

    with pytest.raises(TTSProviderError, match="malformed base64"):
        synthesize_voiceover(plan, output, TTSConfig(provider="doubao"))
    assert not output.with_suffix(".doubao.mp3").exists()


def test_build_tts_config_defaults_doubao_voice():
    args = parse_args(["--tts-provider", "doubao"])

    config = build_tts_config(args)

    assert config.provider == "doubao"
    assert config.voice == "zh_male_liufei_uranus_bigtts"


def test_file_tts_requires_existing_audio_file(tmp_path):
    plan = build_video_plan(VideoConfig())
    output = tmp_path / "voiceover.wav"

    with pytest.raises(TTSProviderError, match="audio file"):
        synthesize_voiceover(
            plan,
            output,
            TTSConfig(provider="file", audio_file=tmp_path / "missing.wav"),
        )


def test_render_video_with_mock_openai_tts_creates_release_artifacts(tmp_path, monkeypatch):
    wav_bytes = _make_test_wav_bytes(duration_seconds=45)

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def read(self):
            return wav_bytes

    def fake_urlopen(request, timeout):
        assert request.full_url == "https://api.openai.com/v1/audio/speech"
        assert request.headers["Authorization"].startswith("Bearer ")
        assert timeout == 120
        return FakeResponse()

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr("video_factory.pipeline.urlopen", fake_urlopen)

    plan = build_video_plan(VideoConfig(output_slug="release"))
    result = render_video(
        plan,
        tmp_path,
        TTSConfig(provider="openai", voice="marin"),
        release=True,
    )

    assert result.video.name == "release.mp4"
    assert result.cover.name == "cover.png"
    assert result.report.name == "render_report.json"
    assert result.video.exists()
    assert result.cover.exists()
    report = json.loads(result.report.read_text(encoding="utf-8"))
    assert report["video"]["width"] == 1080
    assert report["video"]["height"] == 1920
    assert report["tts"]["provider"] == "openai"
    assert report["tts"]["used_fallback"] is False


def test_cli_parses_release_tts_options(tmp_path):
    audio_file = tmp_path / "voice.wav"
    args = parse_args(
        [
            "--tts-provider",
            "file",
            "--audio-file",
            str(audio_file),
            "--voice",
            "cedar",
            "--voice-instructions",
            "热情一点",
            "--allow-fallback",
        ]
    )

    tts_config = build_tts_config(args)

    assert tts_config.provider == "file"
    assert tts_config.audio_file == audio_file
    assert tts_config.voice == "cedar"
    assert tts_config.voice_instructions == "热情一点"
    assert tts_config.allow_fallback is True


def test_cli_defaults_edge_voice_for_edge_provider():
    args = parse_args(["--tts-provider", "edge"])

    tts_config = build_tts_config(args)

    assert tts_config.provider == "edge"
    assert tts_config.voice == "zh-CN-XiaoxiaoNeural"


def test_cli_parses_portugal_template_options():
    args = parse_args(["--template", "portugal-dr-congo", "--tts-provider", "edge"])

    assert args.template == "portugal-dr-congo"
    assert args.tts_provider == "edge"


def test_cli_builds_portugal_template_plan():
    from video_factory.cli import build_plan_from_args

    args = parse_args(["--template", "portugal-dr-congo", "--tts-provider", "edge"])
    plan = build_plan_from_args(args)

    assert plan.config.target_duration == 340
    assert plan.width == 1920
    assert plan.height == 1080
    assert plan.config.style == "premium_studio_tutorial"


def test_edge_tts_provider_invokes_edge_tts_and_converts_to_wav(tmp_path, monkeypatch):
    plan = build_video_plan(VideoConfig())
    output = tmp_path / "voiceover.wav"
    commands = []

    def fake_run(command, check):
        commands.append(command)
        if "edge_tts" in command:
            edge_output = command[command.index("--write-media") + 1]
            _write_test_wav(Path(edge_output), duration_seconds=45)
        elif Path(command[0]).stem.lower() == "ffmpeg":
            _write_test_wav(output, duration_seconds=45)

    monkeypatch.setattr("video_factory.pipeline.subprocess.run", fake_run)
    monkeypatch.setattr("video_factory.pipeline._probe_duration_seconds", lambda path: 45)

    result = synthesize_voiceover(
        plan,
        output,
        TTSConfig(provider="edge", voice="zh-CN-XiaoxiaoNeural"),
    )

    assert result.provider == "edge"
    assert result.used_fallback is False
    assert output.exists()
    assert any("edge_tts" in command for command in commands)
    edge_command = next(command for command in commands if "edge_tts" in command)
    assert edge_command[edge_command.index("--rate") + 1] == "+20%"
    notes = (tmp_path / "voiceover_notes.txt").read_text(encoding="utf-8")
    assert "edge-tts" in notes


def test_render_video_with_premium_plan_wires_horizontal_export(tmp_path, monkeypatch):
    from video_factory import TTSResult
    from video_factory.legacy_v1 import build_portugal_dr_congo_prediction_plan
    import video_factory.legacy_v1 as legacy_v1

    plan = build_portugal_dr_congo_prediction_plan()
    commands = []

    def fake_render_frames(render_plan, frames_dir):
        frames_dir.mkdir(exist_ok=True)
        frames = []
        for index, segment in enumerate(render_plan.segments):
            frame = frames_dir / f"frame_{index:02d}_{segment.role}.png"
            frame.write_bytes(b"fake-png")
            frames.append(frame)
        return frames

    def fake_synthesize_voiceover(render_plan, voiceover_path, tts_config):
        Path(voiceover_path).write_bytes(_make_test_wav_bytes(duration_seconds=340))
        return TTSResult(
            path=Path(voiceover_path),
            provider="edge",
            voice="zh-CN-YunxiNeural",
            model="edge-tts",
            used_fallback=False,
            notes="test voiceover",
        )

    def fake_run(command, check):
        commands.append(command)
        output = Path(command[-1])
        output.write_bytes(b"fake-video")

    monkeypatch.setattr(legacy_v1, "_render_frames", fake_render_frames)
    monkeypatch.setattr(legacy_v1, "synthesize_voiceover", fake_synthesize_voiceover)
    monkeypatch.setattr(legacy_v1.subprocess, "run", fake_run)

    result = render_video(plan, tmp_path, TTSConfig(provider="edge", voice="zh-CN-YunxiNeural"), release=True)

    assert result.video.name == "release.mp4"
    assert result.video.exists()
    assert result.cover.exists()
    assert any("scale=1920:1080" in command for command in commands)
    report = json.loads(result.report.read_text(encoding="utf-8"))
    assert report["video"]["width"] == 1920
    assert report["video"]["height"] == 1080
    assert report["video"]["duration"] == 340
    assert report["tts"]["provider"] == "edge"


def test_render_video_with_visual_asset_manifest_uses_segment_export(tmp_path, monkeypatch):
    from video_factory import TTSConfig, TTSResult
    from video_factory.legacy_v1 import build_portugal_dr_congo_prediction_plan
    import video_factory.legacy_v1 as legacy_v1

    plan = build_portugal_dr_congo_prediction_plan()
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "version": 1,
                "license_policy": "No official FIFA, broadcaster, federation, or match-highlight footage.",
                "assets": [
                    {
                        "key": segment.role,
                        "kind": "generated_image",
                        "segment_role": segment.role,
                        "local_path": str(tmp_path / f"{segment.role}.png"),
                        "source_url": "test",
                        "license_url": "project-generated",
                        "notes": "Generic generated test asset with no official media.",
                    }
                    for segment in plan.segments
                ],
                "segment_asset_order": {segment.role: [segment.role] for segment in plan.segments},
            }
        ),
        encoding="utf-8",
    )
    for segment in plan.segments:
        (tmp_path / f"{segment.role}.png").write_bytes(b"asset")

    def fake_synthesize_voiceover(render_plan, voiceover_path, tts_config):
        Path(voiceover_path).write_bytes(_make_test_wav_bytes(duration_seconds=340))
        return TTSResult(Path(voiceover_path), "edge", "test", "edge-tts", False, "test")

    monkeypatch.setattr(legacy_v1, "synthesize_voiceover", fake_synthesize_voiceover)
    monkeypatch.setattr(
        legacy_v1,
        "_render_premium_asset_segments",
        lambda render_plan, out, data: [out / f"{i}.mp4" for i in range(6)],
    )
    monkeypatch.setattr(
        legacy_v1,
        "_concat_segment_videos_with_audio",
        lambda segments, audio, output, duration: output.write_bytes(b"video"),
    )
    monkeypatch.setattr(legacy_v1, "_extract_cover_frame", lambda source, dest: Path(dest).write_bytes(b"cover"))

    result = legacy_v1.render_video(
        plan,
        tmp_path / "out",
        TTSConfig(provider="edge"),
        release=True,
        visual_asset_manifest=manifest,
    )

    assert result.video.name == "release.mp4"
    assert result.video.exists()
    report = json.loads(result.report.read_text(encoding="utf-8"))
    assert report["artifacts"]["render_mode"] == "premium_asset_edit"
    assert report["artifacts"]["asset_manifest"].endswith("manifest.json")


def _make_test_wav_bytes(duration_seconds: int) -> bytes:
    import io

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(44100)
        wav_file.writeframes(b"\x00\x00" * 44100 * duration_seconds)
    return buffer.getvalue()


def _write_test_wav(path, duration_seconds: int) -> None:
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(44100)
        wav_file.writeframes(b"\x00\x00" * 44100 * duration_seconds)


# ---------- TTS 语速可调（P12：用户要求语速可控、链路随语速自适应） ----------

def test_tts_speed_clamped_and_normalized():
    from video_factory.pipeline import TTSConfig, _tts_speed

    assert _tts_speed(TTSConfig()) is None                       # 未设 → 各家默认
    assert _tts_speed(TTSConfig(speed=1.2)) == 1.2               # 正常直传
    assert _tts_speed(TTSConfig(speed=5.0)) == 2.0               # 超上限钳位
    assert _tts_speed(TTSConfig(speed=0.1)) == 0.5               # 低于下限钳位
    assert _tts_speed(TTSConfig(speed=0)) is None                # 非正 → 默认


def test_doubao_payloads_carry_speed():
    from video_factory.pipeline import TTSConfig, build_doubao_speech_payload, build_doubao_v3_payload

    config = TTSConfig(provider="doubao", voice="zh_male_liufei_uranus_bigtts", speed=1.2)
    v1 = build_doubao_speech_payload("文本", config, appid="a", token="t")
    assert v1["audio"]["speed_ratio"] == 1.2                     # v1 直传比例
    v3 = build_doubao_v3_payload("文本", config)
    assert v3["req_params"]["audio_params"]["speech_rate"] == 20  # v3 换算 (r-1)*100
    # 未设语速：v1 落默认 1.0、v3 不带 speech_rate 字段（用服务端默认）
    default = TTSConfig(provider="doubao")
    assert build_doubao_speech_payload("x", default, appid="a", token="t")["audio"]["speed_ratio"] == 1.0
    assert "speech_rate" not in build_doubao_v3_payload("x", default)["req_params"]["audio_params"]


def test_openai_payload_and_edge_rate_carry_speed():
    from video_factory.pipeline import TTSConfig, _edge_rate_for, build_openai_speech_payload

    assert build_openai_speech_payload("文本", TTSConfig(speed=1.5))["speed"] == 1.5
    assert "speed" not in build_openai_speech_payload("文本", TTSConfig())
    assert _edge_rate_for(TTSConfig(speed=1.2)) == "+20%"        # 显式语速换算 ±N%
    assert _edge_rate_for(TTSConfig(speed=0.8)) == "-20%"
    assert _edge_rate_for(TTSConfig()) == "+20%"                 # 未设沿用 edge_rate 默认
