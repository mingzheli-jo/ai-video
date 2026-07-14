from __future__ import annotations

import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from video_factory import build_portugal_dr_congo_prediction_plan
from video_factory.pipeline import (
    _concat_segment_videos_with_audio,
    _draw_cinematic_broll_overlay_frame,
    _extract_cover_frame,
)


SOURCE_VIDEO = Path("/Users/king/Downloads/下载.mp4")
OUTPUT_DIR = Path("video_factory/output/portugal-dr-congo-reference-edit")
WIDTH = 1920
HEIGHT = 1080
FPS = 30


def main() -> None:
    plan = build_portugal_dr_congo_prediction_plan()
    overlays_dir = OUTPUT_DIR / "overlays"
    segments_dir = OUTPUT_DIR / "segments"
    overlays_dir.mkdir(parents=True, exist_ok=True)
    segments_dir.mkdir(parents=True, exist_ok=True)

    segment_videos: list[Path] = []
    for index, segment in enumerate(plan.segments):
        overlay_path = overlays_dir / f"overlay_{index:02d}_{segment.role}.png"
        segment_path = segments_dir / f"segment_{index:02d}_{segment.role}.mp4"
        progress = (index + 1) / len(plan.segments)
        _draw_cinematic_broll_overlay_frame(plan, segment, index, overlay_path, progress=progress)
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-ss",
                str(segment.start),
                "-t",
                str(segment.duration),
                "-i",
                str(SOURCE_VIDEO),
                "-loop",
                "1",
                "-i",
                str(overlay_path),
                "-filter_complex",
                (
                    f"[0:v]scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,"
                    f"crop={WIDTH}:{HEIGHT},setsar=1,fps={FPS},"
                    "eq=contrast=1.04:brightness=-0.03:saturation=1.03[bg];"
                    "[bg][1:v]overlay=0:0:format=auto,format=yuv420p[v]"
                ),
                "-t",
                str(segment.duration),
                "-map",
                "[v]",
                "-an",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                str(segment_path),
            ],
            check=True,
        )
        segment_videos.append(segment_path)

    voiceover = Path("video_factory/output/portugal-dr-congo-release/voiceover.wav")
    release = OUTPUT_DIR / "release.mp4"
    _concat_segment_videos_with_audio(segment_videos, voiceover, release, plan.config.target_duration)
    _extract_cover_frame(release, OUTPUT_DIR / "cover.png")


if __name__ == "__main__":
    main()
