from __future__ import annotations

from dataclasses import dataclass, field
from math import gcd
from pathlib import Path


@dataclass(frozen=True)
class MediaInfo:
    source_path: Path
    duration: float
    width: int
    height: int
    fps: float
    video_codec: str
    audio_codec: str
    audio_sample_rate: int
    bit_rate: int

    @property
    def orientation(self) -> str:
        if self.width > self.height:
            return "landscape"
        if self.height > self.width:
            return "portrait"
        return "square"

    @property
    def aspect_ratio(self) -> str:
        if self.width == 0 or self.height == 0:
            return "unknown"

        divisor = gcd(self.width, self.height)
        return f"{self.width // divisor}:{self.height // divisor}"

    def to_json_dict(self) -> dict[str, object]:
        return {
            "source_path": str(self.source_path),
            "duration": round(self.duration, 3),
            "width": self.width,
            "height": self.height,
            "fps": round(self.fps, 3),
            "video_codec": self.video_codec,
            "audio_codec": self.audio_codec,
            "audio_sample_rate": self.audio_sample_rate,
            "bit_rate": self.bit_rate,
            "orientation": self.orientation,
            "aspect_ratio": self.aspect_ratio,
        }


@dataclass(frozen=True)
class SampleFrame:
    timestamp: float
    path: Path

    @property
    def label(self) -> str:
        total_centiseconds = round(self.timestamp * 100)
        total_seconds, centiseconds = divmod(total_centiseconds, 100)
        minutes, seconds = divmod(total_seconds, 60)
        return f"{minutes:02d}:{seconds:02d}.{centiseconds:02d}"


@dataclass(frozen=True)
class AnalysisPaths:
    output_dir: Path
    media_info: Path
    contact_sheet: Path
    sample_frames_dir: Path
    timeline: Path
    quality_report: Path
    production_template: Path
    scorecard: Path

    @classmethod
    def for_output_dir(cls, output_dir: Path) -> "AnalysisPaths":
        return cls(
            output_dir=output_dir,
            media_info=output_dir / "media_info.json",
            contact_sheet=output_dir / "contact_sheet.jpg",
            sample_frames_dir=output_dir / "sample_frames",
            timeline=output_dir / "timeline.md",
            quality_report=output_dir / "quality_report.md",
            production_template=output_dir / "production_template.md",
            scorecard=output_dir / "scorecard.md",
        )


@dataclass(frozen=True)
class AnalysisResult:
    media: MediaInfo
    paths: AnalysisPaths
    sample_frames: list[SampleFrame] = field(default_factory=list)
