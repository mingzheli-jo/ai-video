from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from video_factory.audio import AudioAnalysis, AudioCue
from video_factory.content import ContentAnalysis, ContentCue
from video_factory.transcript import TranscriptAnalysis, TranscriptCue, transcript_cue_by_sample_index


@dataclass(frozen=True)
class SemanticProviderStatus:
    name: str
    status: str
    message: str


@dataclass(frozen=True)
class SemanticChapter:
    index: int
    topic: str
    title: str
    start: float
    end: float
    sample_indices: tuple[int, ...]
    evidence: tuple[str, ...]
    audio_emphasis_count: int
    transcript_evidence_count: int


@dataclass(frozen=True)
class SemanticTimeline:
    provider: SemanticProviderStatus
    chapters: tuple[SemanticChapter, ...]

    @property
    def coverage(self) -> dict[str, int]:
        return {
            "chapter_count": len(self.chapters),
            "sample_count": sum(len(chapter.sample_indices) for chapter in self.chapters),
            "titled_count": sum(1 for chapter in self.chapters if chapter.title.strip()),
            "audio_emphasis_count": sum(chapter.audio_emphasis_count for chapter in self.chapters),
            "transcript_evidence_count": sum(chapter.transcript_evidence_count for chapter in self.chapters),
        }


def build_semantic_timeline(
    content_analysis: ContentAnalysis | None,
    audio_analysis: AudioAnalysis | None,
    title: str,
    source_duration: float,
    transcript_analysis: TranscriptAnalysis | None = None,
) -> SemanticTimeline:
    if content_analysis is None or not content_analysis.cues:
        return SemanticTimeline(
            provider=SemanticProviderStatus(
                name="ocr_audio_semantic",
                status="fallback",
                message="no content cues; semantic timeline unavailable",
            ),
            chapters=(),
        )

    audio_by_index = {cue.sample_index: cue for cue in audio_analysis.cues} if audio_analysis else {}
    transcript_by_index = transcript_cue_by_sample_index(transcript_analysis)
    has_transcript = any(cue.text.strip() for cue in transcript_by_index.values())
    cue_topics = [(_topic_for_cue(cue, title, transcript_by_index.get(cue.sample_index)), cue) for cue in content_analysis.cues]
    chapters: list[SemanticChapter] = []
    current_topic = ""
    current_cues: list[ContentCue] = []

    def flush() -> None:
        if not current_cues:
            return
        chapters.append(
            _chapter_from_cues(
                len(chapters),
                current_topic,
                current_cues,
                audio_by_index,
                transcript_by_index,
                source_duration,
            )
        )

    for topic, cue in cue_topics:
        if current_cues and topic != current_topic:
            flush()
            current_cues = []
        current_topic = topic
        current_cues.append(cue)
    flush()

    return SemanticTimeline(
        provider=SemanticProviderStatus(
            name="ocr_audio_transcript_semantic" if has_transcript else "ocr_audio_semantic",
            status="available",
            message=(
                f"derived {len(chapters)} semantic chapters from {len(content_analysis.cues)} OCR/content cues"
                + (" with transcript evidence" if has_transcript else "")
            ),
        ),
        chapters=tuple(chapters),
    )


def semantic_timeline_to_dict(timeline: SemanticTimeline) -> dict:
    return {
        "provider": asdict(timeline.provider),
        "coverage": timeline.coverage,
        "chapters": [asdict(chapter) for chapter in timeline.chapters],
    }


def write_semantic_timeline_json(timeline: SemanticTimeline, output_path: Path | str) -> None:
    Path(output_path).write_text(
        json.dumps(semantic_timeline_to_dict(timeline), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def chapter_by_sample_index(timeline: SemanticTimeline | None) -> dict[int, SemanticChapter]:
    if timeline is None:
        return {}
    mapping: dict[int, SemanticChapter] = {}
    for chapter in timeline.chapters:
        for sample_index in chapter.sample_indices:
            mapping[sample_index] = chapter
    return mapping


def _chapter_from_cues(
    index: int,
    topic: str,
    cues: list[ContentCue],
    audio_by_index: dict[int, AudioCue],
    transcript_by_index: dict[int, TranscriptCue],
    source_duration: float,
) -> SemanticChapter:
    start = min(cue.timestamp for cue in cues)
    next_hint = max(cue.timestamp for cue in cues) + _chapter_tail(source_duration)
    end = min(source_duration, max(start + 0.5, next_hint))
    transcript_evidence = tuple(
        transcript_by_index[cue.sample_index].text.strip()[:80]
        for cue in cues
        if cue.sample_index in transcript_by_index and transcript_by_index[cue.sample_index].text.strip()
    )
    ocr_evidence = tuple(
        cue.recognized_text.strip()[:80]
        for cue in cues
        if cue.recognized_text.strip()
    )
    evidence = _ordered_unique(transcript_evidence + ocr_evidence)[:4]
    transcript_evidence_count = sum(
        1
        for cue in cues
        if cue.sample_index in transcript_by_index and transcript_by_index[cue.sample_index].text.strip()
    )
    audio_emphasis_count = sum(
        1
        for cue in cues
        if "emphasis" in audio_by_index.get(cue.sample_index, _EMPTY_AUDIO_CUE).audio_tags
    )
    return SemanticChapter(
        index=index,
        topic=topic,
        title=_title_for_topic(topic),
        start=round(start, 3),
        end=round(end, 3),
        sample_indices=tuple(cue.sample_index for cue in cues),
        evidence=evidence,
        audio_emphasis_count=audio_emphasis_count,
        transcript_evidence_count=transcript_evidence_count,
    )


def _topic_for_cue(cue: ContentCue, title: str, transcript_cue: TranscriptCue | None = None) -> str:
    transcript_text = transcript_cue.text if transcript_cue is not None else ""
    text = f"{cue.recognized_text} {transcript_text} {' '.join(cue.content_tags)}".lower()
    title_text = title.lower()
    if any(token in text for token in ("api key", "apikey", "api_key", "key 输入", "密钥")):
        return "api_key"
    if any(token in text for token in ("路由", "127.0.0.1", "localhost", "route")):
        return "local_route"
    if any(token in text for token in ("deepseek", "供应商", "provider", "模型")):
        return "provider_setup"
    if any(token in text for token in ("download", "下载", "安装", "install")):
        return "install"
    if any(token in text for token in ("验证", "成功", "测试", "validation")):
        return "validation"
    if any(token in text for token in ("plus", "$20", "付费", "订阅")):
        return "pricing"
    if any(token in text for token in ("关注", "帮助", "结尾")):
        return "closing"
    if any(token in title_text for token in ("安装", "install")) and cue.sample_index == 0:
        return "install"
    return "context"


def _title_for_topic(topic: str) -> str:
    titles = {
        "install": "安装入口",
        "pricing": "订阅与限制",
        "provider_setup": "供应商配置",
        "api_key": "DeepSeek API Key",
        "local_route": "本地路由与验证",
        "validation": "结果验证",
        "closing": "结尾提示",
        "context": "上下文铺垫",
    }
    return titles.get(topic, "内容章节")


def _chapter_tail(source_duration: float) -> float:
    if source_duration >= 300:
        return 16.0
    if source_duration >= 120:
        return 10.0
    return 5.0


def _ordered_unique(values: tuple[str, ...]) -> tuple[str, ...]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        cleaned = " ".join(value.split())
        if not cleaned:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(cleaned)
    return tuple(ordered)


_EMPTY_AUDIO_CUE = AudioCue(
    sample_index=-1,
    timestamp=0.0,
    mean_volume_db=-80.0,
    max_volume_db=-80.0,
    energy=0.0,
    speech_likelihood=0.0,
    audio_tags=(),
    evidence=(),
)
