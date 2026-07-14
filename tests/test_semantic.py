from video_factory.audio import AudioAnalysis, AudioCue, AudioProviderStatus
from video_factory.content import ContentAnalysis, ContentCue, ContentProviderStatus
from video_factory.semantic import (
    build_semantic_timeline,
    chapter_by_sample_index,
    semantic_timeline_to_dict,
    write_semantic_timeline_json,
)
from video_factory.transcript import TranscriptAnalysis, TranscriptCue, TranscriptProviderStatus


def _content_analysis():
    cues = (
        ContentCue(
            sample_index=0,
            timestamp=0.7,
            text_density=0.2,
            subtitle_likelihood=0.7,
            interface_likelihood=0.6,
            recognized_text="Codex 保姆级安装教程",
            content_tags=("codex", "install", "subtitle"),
            evidence=("ocr:Codex 保姆级安装教程",),
        ),
        ContentCue(
            sample_index=1,
            timestamp=80.0,
            text_density=0.2,
            subtitle_likelihood=0.5,
            interface_likelihood=0.6,
            recognized_text="Download for Windows 下载",
            content_tags=("install", "interface"),
            evidence=("ocr:Download for Windows",),
        ),
        ContentCue(
            sample_index=2,
            timestamp=180.0,
            text_density=0.2,
            subtitle_likelihood=0.5,
            interface_likelihood=0.8,
            recognized_text="添加供应商 DeepSeek API Key",
            content_tags=("deepseek", "api_key", "provider", "interface"),
            evidence=("ocr:添加供应商 DeepSeek API Key",),
        ),
        ContentCue(
            sample_index=3,
            timestamp=300.0,
            text_density=0.2,
            subtitle_likelihood=0.5,
            interface_likelihood=0.8,
            recognized_text="本地路由服务已启动 127.0.0.1",
            content_tags=("configuration", "validation", "interface"),
            evidence=("ocr:本地路由服务已启动",),
        ),
    )
    return ContentAnalysis(
        provider=ContentProviderStatus(name="ocrmac", status="available", message="test OCR"),
        cues=cues,
    )


def _audio_analysis():
    cues = tuple(
        AudioCue(
            sample_index=index,
            timestamp=timestamp,
            mean_volume_db=-20.0,
            max_volume_db=-4.0,
            energy=0.7,
            speech_likelihood=0.8,
            audio_tags=("speech_like", "emphasis"),
            evidence=("mean_volume:-20.0dB", "max_volume:-4.0dB"),
        )
        for index, timestamp in enumerate((0.7, 80.0, 180.0, 300.0))
    )
    return AudioAnalysis(
        provider=AudioProviderStatus(name="ffmpeg_volumedetect", status="available", message="test audio"),
        cues=cues,
    )


def test_semantic_timeline_groups_ocr_cues_into_readable_chapters(tmp_path):
    timeline = build_semantic_timeline(
        content_analysis=_content_analysis(),
        audio_analysis=_audio_analysis(),
        title="Codex DeepSeek API 配置教程",
        source_duration=360.0,
    )
    data = semantic_timeline_to_dict(timeline)
    chapter_titles = [chapter.title for chapter in timeline.chapters]

    assert timeline.provider.name == "ocr_audio_semantic"
    assert data["coverage"]["chapter_count"] >= 3
    assert "安装入口" in chapter_titles
    assert "DeepSeek API Key" in chapter_titles
    assert "本地路由与验证" in chapter_titles
    assert timeline.chapters[-1].audio_emphasis_count >= 1

    output = tmp_path / "semantic_timeline.json"
    write_semantic_timeline_json(timeline, output)

    assert "semantic_chapters" not in data
    assert output.read_text(encoding="utf-8").startswith("{")


def test_chapter_by_sample_index_maps_each_sample_to_chapter():
    timeline = build_semantic_timeline(
        content_analysis=_content_analysis(),
        audio_analysis=_audio_analysis(),
        title="Codex DeepSeek API 配置教程",
        source_duration=360.0,
    )
    mapping = chapter_by_sample_index(timeline)

    assert mapping[0].title == "安装入口"
    assert mapping[2].topic == "api_key"
    assert mapping[3].title == "本地路由与验证"


def test_semantic_timeline_uses_transcript_text_when_ocr_text_is_blank():
    content_analysis = ContentAnalysis(
        provider=ContentProviderStatus(name="vision_lite", status="fallback", message="visual only"),
        cues=(
            ContentCue(
                sample_index=0,
                timestamp=10.0,
                text_density=0.2,
                subtitle_likelihood=0.4,
                interface_likelihood=0.5,
                recognized_text="",
                content_tags=("subtitle",),
                evidence=("subtitle_band",),
            ),
            ContentCue(
                sample_index=1,
                timestamp=20.0,
                text_density=0.2,
                subtitle_likelihood=0.4,
                interface_likelihood=0.5,
                recognized_text="",
                content_tags=("interface",),
                evidence=("interface_layout",),
            ),
        ),
    )
    transcript_analysis = TranscriptAnalysis(
        provider=TranscriptProviderStatus(name="sidecar_transcript", status="available", message="test transcript"),
        cues=(
            TranscriptCue(
                sample_index=0,
                start=9.0,
                end=12.0,
                text="这里创建 DeepSeek API Key",
                source="sidecar",
                confidence=0.95,
                evidence=("srt:这里创建 DeepSeek API Key",),
            ),
            TranscriptCue(
                sample_index=1,
                start=19.0,
                end=22.0,
                text="然后启动本地路由服务 127.0.0.1",
                source="sidecar",
                confidence=0.95,
                evidence=("srt:然后启动本地路由服务",),
            ),
        ),
    )

    timeline = build_semantic_timeline(
        content_analysis=content_analysis,
        audio_analysis=None,
        transcript_analysis=transcript_analysis,
        title="Codex 配置教程",
        source_duration=60.0,
    )
    mapping = chapter_by_sample_index(timeline)

    assert timeline.provider.name == "ocr_audio_transcript_semantic"
    assert timeline.coverage["transcript_evidence_count"] == 2
    assert mapping[0].topic == "api_key"
    assert mapping[1].topic == "local_route"
    assert mapping[0].evidence[0] == "这里创建 DeepSeek API Key"
