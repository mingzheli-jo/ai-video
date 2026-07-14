from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Sequence

from video_factory.content import ContentAnalysis


@dataclass(frozen=True)
class TranscriptProviderStatus:
    name: str
    status: str
    message: str


@dataclass(frozen=True)
class TranscriptCue:
    sample_index: int
    start: float
    end: float
    text: str
    source: str
    confidence: float
    evidence: tuple[str, ...]


@dataclass(frozen=True)
class TranscriptAnalysis:
    provider: TranscriptProviderStatus
    cues: tuple[TranscriptCue, ...]

    @property
    def coverage(self) -> dict[str, int]:
        return {
            "cue_count": len(self.cues),
            "text_cue_count": sum(1 for cue in self.cues if cue.text.strip()),
            "sidecar_cue_count": sum(1 for cue in self.cues if cue.source == "sidecar"),
            "ocr_proxy_cue_count": sum(1 for cue in self.cues if cue.source == "ocr_proxy"),
        }


@dataclass(frozen=True)
class _TranscriptSegment:
    start: float
    end: float
    text: str
    source: str


def build_transcript_analysis(
    source: Path | str,
    sample_points: Sequence[tuple[float, object] | float],
    content_analysis: ContentAnalysis | None = None,
    sidecar_path: Path | str | None = None,
) -> TranscriptAnalysis:
    points = tuple((index, _point_timestamp(point)) for index, point in enumerate(sample_points))
    sidecar = Path(sidecar_path) if sidecar_path is not None else _find_sidecar(source)
    if sidecar is not None and sidecar.exists():
        cues = _map_sidecar_segments_to_samples(_parse_sidecar(sidecar), points)
        if cues:
            return TranscriptAnalysis(
                provider=TranscriptProviderStatus(
                    name="sidecar_transcript",
                    status="available",
                    message=f"mapped {len(cues)} transcript cues from {sidecar.name}",
                ),
                cues=tuple(cues),
            )

    if content_analysis is not None:
        cues = tuple(
            TranscriptCue(
                sample_index=cue.sample_index,
                start=cue.timestamp,
                end=cue.timestamp,
                text=cue.recognized_text.strip(),
                source="ocr_proxy",
                confidence=0.62,
                evidence=tuple(f"ocr_proxy:{item}" for item in cue.evidence[:2]) or ("ocr_proxy:recognized_text",),
            )
            for cue in content_analysis.cues
            if cue.recognized_text.strip()
        )
        if cues:
            return TranscriptAnalysis(
                provider=TranscriptProviderStatus(
                    name="ocr_transcript_proxy",
                    status="fallback",
                    message=f"derived {len(cues)} transcript cues from OCR/content text",
                ),
                cues=cues,
            )

    return TranscriptAnalysis(
        provider=TranscriptProviderStatus(
            name="none",
            status="empty",
            message="no transcript sidecar or OCR text available",
        ),
        cues=(),
    )


def transcript_analysis_to_dict(analysis: TranscriptAnalysis) -> dict:
    data = {
        "provider": asdict(analysis.provider),
        "coverage": analysis.coverage,
        "cues": [asdict(cue) for cue in analysis.cues],
    }
    return json.loads(json.dumps(data, ensure_ascii=False))


def write_transcript_analysis_json(analysis: TranscriptAnalysis, output_path: Path | str) -> None:
    Path(output_path).write_text(
        json.dumps(transcript_analysis_to_dict(analysis), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def transcript_cue_by_sample_index(analysis: TranscriptAnalysis | None) -> dict[int, TranscriptCue]:
    if analysis is None:
        return {}
    return {cue.sample_index: cue for cue in analysis.cues}


def _find_sidecar(source: Path | str) -> Path | None:
    source_path = Path(source)
    for suffix in (".srt", ".vtt", ".txt"):
        candidate = source_path.with_suffix(suffix)
        if candidate.exists():
            return candidate
    return None


def _parse_sidecar(path: Path) -> tuple[_TranscriptSegment, ...]:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".txt":
        cleaned = _clean_text(text)
        return (_TranscriptSegment(0.0, float("inf"), cleaned, "plain_text"),) if cleaned else ()

    lines = text.splitlines()
    segments: list[_TranscriptSegment] = []
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        timestamp = _parse_timestamp_line(line)
        if timestamp is None:
            index += 1
            continue
        start, end = timestamp
        index += 1
        text_lines: list[str] = []
        while index < len(lines) and lines[index].strip():
            cleaned = lines[index].strip()
            if not cleaned.isdigit() and cleaned.upper() != "WEBVTT":
                text_lines.append(cleaned)
            index += 1
        cleaned_text = _clean_text(" ".join(text_lines))
        if cleaned_text:
            segments.append(_TranscriptSegment(start=start, end=end, text=cleaned_text, source="sidecar"))
        index += 1
    return tuple(segments)


def _map_sidecar_segments_to_samples(
    segments: Sequence[_TranscriptSegment],
    points: Sequence[tuple[int, float]],
) -> list[TranscriptCue]:
    cues: list[TranscriptCue] = []
    for sample_index, timestamp in points:
        matches = [segment for segment in segments if segment.start <= timestamp <= segment.end]
        if not matches:
            continue
        start = min(segment.start for segment in matches)
        end = max(segment.end for segment in matches)
        text = _clean_text(" ".join(segment.text for segment in matches))
        if not text:
            continue
        cues.append(
            TranscriptCue(
                sample_index=sample_index,
                start=round(start, 3),
                end=round(end if end != float("inf") else timestamp, 3),
                text=text,
                source="sidecar",
                confidence=0.95,
                evidence=(f"sidecar:{text[:80]}",),
            )
        )
    return cues


def _parse_timestamp_line(line: str) -> tuple[float, float] | None:
    match = re.search(
        r"(?P<start>\d{1,2}:\d{2}:\d{2}[,.]\d{1,3})\s*-->\s*"
        r"(?P<end>\d{1,2}:\d{2}:\d{2}[,.]\d{1,3})",
        line,
    )
    if match is None:
        return None
    return _parse_timestamp(match.group("start")), _parse_timestamp(match.group("end"))


def _parse_timestamp(value: str) -> float:
    hours, minutes, seconds = value.replace(",", ".").split(":")
    return round(int(hours) * 3600 + int(minutes) * 60 + float(seconds), 3)


def _point_timestamp(point: tuple[float, object] | float) -> float:
    if isinstance(point, tuple):
        return float(point[0])
    return float(point)


def _clean_text(text: str) -> str:
    return " ".join(str(text).replace("\ufeff", "").split())
