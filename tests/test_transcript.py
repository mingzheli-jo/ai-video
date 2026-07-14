import json

from video_factory.content import ContentAnalysis, ContentCue, ContentProviderStatus
from video_factory.transcript import (
    build_transcript_analysis,
    transcript_analysis_to_dict,
    transcript_cue_by_sample_index,
    write_transcript_analysis_json,
)


def test_transcript_analysis_maps_srt_sidecar_to_sample_points(tmp_path):
    sidecar = tmp_path / "source.srt"
    sidecar.write_text(
        "\n".join(
            [
                "1",
                "00:00:00,500 --> 00:00:03,000",
                "先安装 Codex",
                "",
                "2",
                "00:00:06,000 --> 00:00:09,000",
                "添加 DeepSeek API Key",
                "",
            ]
        ),
        encoding="utf-8",
    )

    analysis = build_transcript_analysis(
        source=tmp_path / "source.mp4",
        sample_points=[(1.2, tmp_path / "s0.jpg"), (7.0, tmp_path / "s1.jpg"), (14.0, tmp_path / "s2.jpg")],
        sidecar_path=sidecar,
    )
    by_index = transcript_cue_by_sample_index(analysis)

    assert analysis.provider.name == "sidecar_transcript"
    assert analysis.coverage["sidecar_cue_count"] == 2
    assert by_index[0].text == "先安装 Codex"
    assert by_index[1].text == "添加 DeepSeek API Key"
    assert by_index[1].confidence >= 0.9


def test_transcript_analysis_maps_vtt_and_plain_text_sidecars(tmp_path):
    vtt = tmp_path / "source.vtt"
    vtt.write_text(
        "\n".join(
            [
                "WEBVTT",
                "",
                "00:00:10.000 --> 00:00:12.000",
                "本地路由服务启动 127.0.0.1",
                "",
            ]
        ),
        encoding="utf-8",
    )
    text = tmp_path / "source.txt"
    text.write_text("这是一段完整口播文稿，说明 Codex 工作流。", encoding="utf-8")

    vtt_analysis = build_transcript_analysis(
        source=tmp_path / "source.mp4",
        sample_points=[(10.5, tmp_path / "s0.jpg")],
        sidecar_path=vtt,
    )
    plain_analysis = build_transcript_analysis(
        source=tmp_path / "source.mp4",
        sample_points=[(0.7, tmp_path / "s0.jpg"), (5.0, tmp_path / "s1.jpg")],
        sidecar_path=text,
    )

    assert transcript_cue_by_sample_index(vtt_analysis)[0].text == "本地路由服务启动 127.0.0.1"
    assert plain_analysis.coverage["text_cue_count"] == 2
    assert all(cue.text == "这是一段完整口播文稿，说明 Codex 工作流。" for cue in plain_analysis.cues)


def test_transcript_analysis_falls_back_to_ocr_content_text_and_serializes(tmp_path):
    content_analysis = ContentAnalysis(
        provider=ContentProviderStatus(name="ocrmac", status="available", message="test OCR"),
        cues=(
            ContentCue(
                sample_index=0,
                timestamp=0.7,
                text_density=0.2,
                subtitle_likelihood=0.7,
                interface_likelihood=0.5,
                recognized_text="Codex 安装入口",
                content_tags=("install", "subtitle"),
                evidence=("ocr:Codex 安装入口",),
            ),
            ContentCue(
                sample_index=1,
                timestamp=8.0,
                text_density=0.2,
                subtitle_likelihood=0.6,
                interface_likelihood=0.7,
                recognized_text="DeepSeek API Key 输入",
                content_tags=("api_key", "interface"),
                evidence=("ocr:DeepSeek API Key 输入",),
            ),
        ),
    )

    analysis = build_transcript_analysis(
        source=tmp_path / "source.mp4",
        sample_points=[(0.7, tmp_path / "s0.jpg"), (8.0, tmp_path / "s1.jpg")],
        content_analysis=content_analysis,
    )
    output = tmp_path / "transcript_analysis.json"
    write_transcript_analysis_json(analysis, output)
    data = json.loads(output.read_text(encoding="utf-8"))

    assert analysis.provider.name == "ocr_transcript_proxy"
    assert data == transcript_analysis_to_dict(analysis)
    assert data["coverage"]["ocr_proxy_cue_count"] == 2
