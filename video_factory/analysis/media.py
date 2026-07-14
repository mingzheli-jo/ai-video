from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

from video_factory.analysis.models import MediaInfo


def build_output_slug(source_path: Path) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", source_path.stem.lower()).strip("-")
    return slug or "video"


def probe_media(source_path: Path) -> MediaInfo:
    if not source_path.exists():
        raise FileNotFoundError(source_path)

    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration,bit_rate",
            "-show_streams",
            "-of",
            "json",
            str(source_path),
        ],
        capture_output=True,
        text=True, encoding="utf-8", errors="replace",
        check=True,
    )
    payload = json.loads(result.stdout or "{}")
    streams = payload.get("streams") or []
    video_stream = _first_stream(streams, "video")
    audio_stream = _first_stream(streams, "audio")
    format_info = payload.get("format") or {}

    return MediaInfo(
        source_path=source_path,
        duration=_float_value(format_info.get("duration")),
        width=_int_value(video_stream.get("width")),
        height=_int_value(video_stream.get("height")),
        fps=_parse_rate(video_stream.get("r_frame_rate")),
        video_codec=str(video_stream.get("codec_name") or ""),
        audio_codec=str(audio_stream.get("codec_name") or ""),
        audio_sample_rate=_int_value(audio_stream.get("sample_rate")),
        bit_rate=_int_value(format_info.get("bit_rate")),
    )


def _first_stream(streams: list[dict[str, Any]], codec_type: str) -> dict[str, Any]:
    for stream in streams:
        if stream.get("codec_type") == codec_type:
            return stream
    return {}


def _parse_rate(value: Any) -> float:
    if isinstance(value, str) and "/" in value:
        numerator, denominator = value.split("/", 1)
        denominator_value = _float_value(denominator)
        if denominator_value == 0:
            return 0.0
        return _float_value(numerator) / denominator_value
    return _float_value(value)


def _int_value(value: Any) -> int:
    if value in (None, ""):
        return 0
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _float_value(value: Any) -> float:
    if value in (None, ""):
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
