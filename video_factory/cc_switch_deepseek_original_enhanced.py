from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_VIDEO = Path("/Users/king/Downloads/下载 (1).mp4")
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "video_factory/output/cc-switch-deepseek-original-enhanced"
WIDTH = 1920
HEIGHT = 1080
FPS = 30


@dataclass(frozen=True)
class OriginalEnhancePaths:
    output_dir: Path
    video: Path
    cover: Path
    contact_sheet: Path
    report: Path


def build_original_enhance_paths(output_dir: Path = DEFAULT_OUTPUT_DIR) -> OriginalEnhancePaths:
    output_dir = Path(output_dir)
    return OriginalEnhancePaths(
        output_dir=output_dir,
        video=output_dir / "release.mp4",
        cover=output_dir / "cover.png",
        contact_sheet=output_dir / "contact_sheet.jpg",
        report=output_dir / "render_report.json",
    )


def build_original_enhance_command(output_path: Path | str) -> list[str]:
    video_filter = ",".join(
        [
            f"scale={WIDTH}:{HEIGHT}:flags=lanczos",
            "setsar=1",
            f"fps={FPS}",
            "eq=contrast=1.035:brightness=0.006:saturation=1.035",
            "unsharp=5:5:0.35:3:3:0.08",
            "format=yuv420p",
        ]
    )
    audio_filter = ",".join(
        [
            "highpass=f=70",
            "lowpass=f=14500",
            "loudnorm=I=-16:TP=-1.5:LRA=11",
            "aformat=sample_rates=44100:channel_layouts=stereo",
        ]
    )
    return [
        "ffmpeg",
        "-y",
        "-i",
        str(SOURCE_VIDEO),
        "-map",
        "0:v:0",
        "-map",
        "0:a:0",
        "-vf",
        video_filter,
        "-af",
        audio_filter,
        "-r",
        str(FPS),
        "-c:v",
        "libx264",
        "-preset",
        "slow",
        "-crf",
        "18",
        "-profile:v",
        "high",
        "-level",
        "4.1",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "160k",
        "-movflags",
        "+faststart",
        str(output_path),
    ]


def render_cc_switch_deepseek_original_enhanced(
    output_dir: Path = DEFAULT_OUTPUT_DIR,
) -> Dict[str, Path]:
    if not SOURCE_VIDEO.exists():
        raise FileNotFoundError(f"Source video does not exist: {SOURCE_VIDEO}")

    paths = build_original_enhance_paths(output_dir)
    paths.output_dir.mkdir(parents=True, exist_ok=True)

    _run(build_original_enhance_command(paths.video))
    _run(_build_cover_command(paths.video, paths.cover))
    _run(_build_contact_sheet_command(paths.video, paths.contact_sheet))
    _write_report(paths)

    return {
        "video": paths.video,
        "cover": paths.cover,
        "contact_sheet": paths.contact_sheet,
        "report": paths.report,
    }


def _build_cover_command(video_path: Path, cover_path: Path) -> list[str]:
    return [
        "ffmpeg",
        "-y",
        "-ss",
        "6",
        "-i",
        str(video_path),
        "-frames:v",
        "1",
        "-update",
        "1",
        "-vf",
        f"scale={WIDTH}:{HEIGHT}:flags=lanczos",
        str(cover_path),
    ]


def _build_contact_sheet_command(video_path: Path, contact_sheet_path: Path) -> list[str]:
    return [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-vf",
        "fps=1/50,scale=480:-1:flags=lanczos,tile=4x3",
        "-frames:v",
        "1",
        "-update",
        "1",
        str(contact_sheet_path),
    ]


def _write_report(paths: OriginalEnhancePaths) -> None:
    report = {
        "project": "cc-switch-deepseek-original-enhanced",
        "source": str(SOURCE_VIDEO),
        "artifacts": {key: str(value) for key, value in asdict(paths).items()},
        "editing_rules": {
            "keeps_full_source_duration": True,
            "visible_overlays": False,
            "progress_bars": False,
            "scene_cards": False,
            "synthetic_voiceover": False,
            "decorative_background": False,
        },
        "processing": {
            "video": [
                "lanczos scale to 1920x1080",
                "mild contrast and saturation correction",
                "mild screen-recording sharpening",
                "h264 crf 18 high profile",
            ],
            "audio": [
                "highpass at 70hz",
                "lowpass at 14500hz",
                "loudness normalization to -16 LUFS target",
                "aac 160k",
            ],
        },
        "source_probe": _probe_media(SOURCE_VIDEO),
        "output_probe": _probe_media(paths.video),
    }
    paths.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def _probe_media(path: Path) -> dict:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration,size,bit_rate",
            "-show_entries",
            "stream=index,codec_type,codec_name,width,height,r_frame_rate,avg_frame_rate,bit_rate,duration,channels,sample_rate,nb_frames",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True, encoding="utf-8", errors="replace",
    )
    return json.loads(result.stdout)


def _run(command: list[str]) -> None:
    subprocess.run(command, check=True)
