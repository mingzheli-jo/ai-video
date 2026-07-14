from __future__ import annotations

import subprocess
from pathlib import Path

from video_factory.analysis.models import MediaInfo, SampleFrame


def choose_sample_timestamps(duration: float, count: int = 8) -> list[float]:
    if count <= 0:
        return []
    if duration <= 2:
        return [0.0]

    start = round(min(3.0, max(0.0, duration * 0.08)), 1)
    end = round(max(start, duration - min(3.0, max(1.0, duration * 0.08))), 1)
    if count == 1:
        return [round(duration / 2, 1)]

    step = (end - start) / (count - 1)
    return [round(start + step * index, 1) for index in range(count)]


def build_contact_sheet_command(
    source_path: Path, output_path: Path, duration: float
) -> list[str]:
    interval = max(1, int(round(duration / 8)))
    return [
        "ffmpeg",
        "-hide_banner",
        "-y",
        "-i",
        str(source_path),
        "-vf",
        f"fps=1/{interval},scale=320:-1,tile=4x2",
        "-frames:v",
        "1",
        str(output_path),
    ]


def build_sample_frame_command(
    source_path: Path, output_path: Path, timestamp: float
) -> list[str]:
    return [
        "ffmpeg",
        "-hide_banner",
        "-y",
        "-ss",
        f"{timestamp:.3f}",
        "-i",
        str(source_path),
        "-frames:v",
        "1",
        str(output_path),
    ]


def render_contact_sheet(media: MediaInfo, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        build_contact_sheet_command(media.source_path, output_path, media.duration),
        check=True,
    )


def render_sample_frames(
    media: MediaInfo, output_dir: Path, count: int = 8
) -> list[SampleFrame]:
    output_dir.mkdir(parents=True, exist_ok=True)
    frames = []
    for index, timestamp in enumerate(choose_sample_timestamps(media.duration, count)):
        output_path = output_dir / (
            f"frame_{index:02d}_{int(timestamp):03d}_"
            f"{int(round((timestamp % 1) * 100)):02d}.jpg"
        )
        subprocess.run(
            build_sample_frame_command(media.source_path, output_path, timestamp),
            check=True,
        )
        frames.append(SampleFrame(timestamp=timestamp, path=output_path))
    return frames
