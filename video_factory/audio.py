from __future__ import annotations

import json
import re
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Sequence


@dataclass(frozen=True)
class AudioProviderStatus:
    name: str
    status: str
    message: str


@dataclass(frozen=True)
class AudioCue:
    sample_index: int
    timestamp: float
    mean_volume_db: float
    max_volume_db: float
    energy: float
    speech_likelihood: float
    audio_tags: tuple[str, ...]
    evidence: tuple[str, ...]


@dataclass(frozen=True)
class AudioAnalysis:
    provider: AudioProviderStatus
    cues: tuple[AudioCue, ...]

    @property
    def coverage(self) -> dict[str, int]:
        return {
            "cue_count": len(self.cues),
            "tagged_count": sum(1 for cue in self.cues if cue.audio_tags),
            "speech_like_count": sum(1 for cue in self.cues if "speech_like" in cue.audio_tags),
        }


def analyze_audio_samples(
    source: Path | str,
    sample_points: Sequence[tuple[float, object] | float],
    window_seconds: float = 1.25,
) -> AudioAnalysis:
    cues: list[AudioCue] = []
    successes = 0
    failures = 0
    for index, point in enumerate(sample_points):
        timestamp = _point_timestamp(point)
        try:
            mean_volume_db, max_volume_db = _probe_volume(source, timestamp, window_seconds)
            successes += 1
        except Exception:
            mean_volume_db, max_volume_db = -80.0, -80.0
            failures += 1
        cues.append(_cue_from_volume(index, timestamp, mean_volume_db, max_volume_db))

    if successes:
        status = "available" if failures == 0 else "partial"
        message = f"measured {successes} audio windows"
    else:
        status = "fallback"
        message = "ffmpeg volume probe returned no usable audio; using quiet fallback cues"
    return AudioAnalysis(
        provider=AudioProviderStatus(name="ffmpeg_volumedetect", status=status, message=message),
        cues=tuple(cues),
    )


def audio_analysis_to_dict(analysis: AudioAnalysis) -> dict:
    data = json.loads(json.dumps(asdict(analysis), ensure_ascii=False))
    data["coverage"] = analysis.coverage
    return data


def write_audio_analysis_json(analysis: AudioAnalysis, output_path: Path | str) -> None:
    Path(output_path).write_text(json.dumps(audio_analysis_to_dict(analysis), ensure_ascii=False, indent=2), encoding="utf-8")


def audio_cue_by_sample_index(analysis: AudioAnalysis | None) -> dict[int, AudioCue]:
    if analysis is None:
        return {}
    return {cue.sample_index: cue for cue in analysis.cues}


def _point_timestamp(point: tuple[float, object] | float) -> float:
    if isinstance(point, tuple):
        return float(point[0])
    return float(point)


def _probe_volume(source: Path | str, timestamp: float, window_seconds: float) -> tuple[float, float]:
    result = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-ss",
            _format_seconds(timestamp),
            "-t",
            _format_seconds(max(0.2, window_seconds)),
            "-i",
            str(source),
            "-vn",
            "-af",
            "volumedetect",
            "-f",
            "null",
            "-",
        ],
        check=False,
        capture_output=True,
        text=True, encoding="utf-8", errors="replace",
    )
    text = f"{result.stderr}\n{result.stdout}"
    mean_match = re.search(r"mean_volume:\s*(-?\d+(?:\.\d+)?)\s*dB", text)
    max_match = re.search(r"max_volume:\s*(-?\d+(?:\.\d+)?)\s*dB", text)
    if result.returncode != 0 or mean_match is None or max_match is None:
        raise RuntimeError("ffmpeg volumedetect failed")
    return round(float(mean_match.group(1)), 2), round(float(max_match.group(1)), 2)


def _cue_from_volume(sample_index: int, timestamp: float, mean_volume_db: float, max_volume_db: float) -> AudioCue:
    energy = _energy_from_db(mean_volume_db)
    peak = _energy_from_db(max_volume_db)
    speech_likelihood = round(_clamp01(energy * 0.72 + peak * 0.28), 4)
    tags: list[str] = []
    if energy <= 0.12:
        tags.append("quiet")
    if speech_likelihood >= 0.34:
        tags.append("speech_like")
    if peak >= 0.76:
        tags.append("emphasis")
    if energy >= 0.48:
        tags.append("continuous_audio")
    evidence = (
        f"mean_volume:{mean_volume_db:.1f}dB",
        f"max_volume:{max_volume_db:.1f}dB",
        f"speech_likelihood:{speech_likelihood:.2f}",
    )
    return AudioCue(
        sample_index=sample_index,
        timestamp=round(timestamp, 3),
        mean_volume_db=mean_volume_db,
        max_volume_db=max_volume_db,
        energy=energy,
        speech_likelihood=speech_likelihood,
        audio_tags=tuple(tags),
        evidence=evidence,
    )


def _energy_from_db(value: float) -> float:
    return round(_clamp01((value + 55.0) / 50.0), 4)


def _format_seconds(value: float) -> str:
    return f"{max(0.0, value):.3f}"


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))
