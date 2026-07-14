from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_VIDEO = Path("/Users/king/Downloads/下载 (1).mp4")
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "video_factory/output/cc-switch-deepseek-human-edit"
WIDTH = 1920
HEIGHT = 1080
FPS = 30


@dataclass(frozen=True)
class HumanEditSegment:
    key: str
    source: str
    start: float
    duration: float
    zoom: float
    crop_x: int
    crop_y: int
    purpose: str


@dataclass(frozen=True)
class HumanEditPaths:
    output_dir: Path
    segments_dir: Path
    video: Path
    cover: Path
    contact_sheet: Path
    edl: Path
    concat: Path
    report: Path


def build_human_edit_storyboard() -> list[HumanEditSegment]:
    return [
        HumanEditSegment(
            key="opening_premise",
            source="reference",
            start=0,
            duration=38,
            zoom=1.0,
            crop_x=0,
            crop_y=0,
            purpose="保留原片开场和工具链问题，不额外加钩子包装。",
        ),
        HumanEditSegment(
            key="codex_context",
            source="reference",
            start=56,
            duration=42,
            zoom=1.1,
            crop_x=96,
            crop_y=30,
            purpose="跳过前面重复铺垫，直接进入 Codex 使用背景。",
        ),
        HumanEditSegment(
            key="cc_switch_overview",
            source="reference",
            start=118,
            duration=50,
            zoom=1.06,
            crop_x=58,
            crop_y=0,
            purpose="保留 CC Switch 是统一入口这条主线。",
        ),
        HumanEditSegment(
            key="release_update",
            source="reference",
            start=190,
            duration=48,
            zoom=1.08,
            crop_x=70,
            crop_y=20,
            purpose="保留版本下载和更新信息，删除较长等待。",
        ),
        HumanEditSegment(
            key="provider_setup",
            source="reference",
            start=260,
            duration=45,
            zoom=1.1,
            crop_x=150,
            crop_y=40,
            purpose="进入服务商配置，让观众看到真实路径。",
        ),
        HumanEditSegment(
            key="deepseek_site",
            source="reference",
            start=315,
            duration=40,
            zoom=1.0,
            crop_x=0,
            crop_y=0,
            purpose="保留 DeepSeek 官网和账户/API 入口。",
        ),
        HumanEditSegment(
            key="api_key_flow",
            source="reference",
            start=365,
            duration=50,
            zoom=1.12,
            crop_x=210,
            crop_y=0,
            purpose="推近 API key 管理页面，突出关键配置动作。",
        ),
        HumanEditSegment(
            key="local_config",
            source="reference",
            start=425,
            duration=50,
            zoom=1.14,
            crop_x=130,
            crop_y=65,
            purpose="推近本地配置和开关，不靠文字框解释。",
        ),
        HumanEditSegment(
            key="model_select",
            source="reference",
            start=485,
            duration=43,
            zoom=1.08,
            crop_x=110,
            crop_y=30,
            purpose="保留模型选择和验证前的关键步骤。",
        ),
        HumanEditSegment(
            key="validation_close",
            source="reference",
            start=532,
            duration=31,
            zoom=1.0,
            crop_x=0,
            crop_y=0,
            purpose="保留结尾验证和收束，避免突然结束。",
        ),
    ]


def human_edit_duration(segments: Sequence[HumanEditSegment]) -> float:
    return sum(segment.duration for segment in segments)


def build_human_edit_paths(output_dir: Path = DEFAULT_OUTPUT_DIR) -> HumanEditPaths:
    output_dir = Path(output_dir)
    return HumanEditPaths(
        output_dir=output_dir,
        segments_dir=output_dir / "segments",
        video=output_dir / "release.mp4",
        cover=output_dir / "cover.png",
        contact_sheet=output_dir / "contact_sheet.jpg",
        edl=output_dir / "edit_decision_list.md",
        concat=output_dir / "segments.txt",
        report=output_dir / "render_report.json",
    )


def build_human_edit_scene_command(
    segment: HumanEditSegment,
    output_path: Path | str,
) -> list[str]:
    scaled_width = int(round(WIDTH * segment.zoom))
    scaled_height = int(round(HEIGHT * segment.zoom))
    crop_x = _bounded_crop_offset(segment.crop_x, scaled_width, WIDTH)
    crop_y = _bounded_crop_offset(segment.crop_y, scaled_height, HEIGHT)
    video_filter = ",".join(
        [
            f"scale={scaled_width}:{scaled_height}:flags=lanczos",
            f"crop={WIDTH}:{HEIGHT}:{crop_x}:{crop_y}",
            "setsar=1",
            f"fps={FPS}",
            "eq=contrast=1.04:brightness=0.006:saturation=1.035",
            "unsharp=5:5:0.34:3:3:0.08",
            "format=yuv420p",
        ]
    )
    audio_filter = ",".join(
        [
            "highpass=f=70",
            "lowpass=f=14500",
            "loudnorm=I=-16:TP=-1.5:LRA=11",
            "aformat=sample_rates=44100:channel_layouts=stereo",
            "aresample=async=1:first_pts=0",
        ]
    )
    return [
        "ffmpeg",
        "-y",
        "-ss",
        _format_time(segment.start),
        "-t",
        _format_time(segment.duration),
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


def render_cc_switch_deepseek_human_edit(
    output_dir: Path = DEFAULT_OUTPUT_DIR,
) -> Dict[str, Path]:
    if not SOURCE_VIDEO.exists():
        raise FileNotFoundError(f"Source video does not exist: {SOURCE_VIDEO}")

    paths = build_human_edit_paths(output_dir)
    paths.output_dir.mkdir(parents=True, exist_ok=True)
    paths.segments_dir.mkdir(parents=True, exist_ok=True)

    segments = build_human_edit_storyboard()
    segment_paths: list[Path] = []
    for index, segment in enumerate(segments):
        segment_path = paths.segments_dir / f"{index:02d}_{segment.key}.mp4"
        subprocess.run(build_human_edit_scene_command(segment, segment_path), check=True)
        segment_paths.append(segment_path)

    _concat_segments(segment_paths, paths.concat, paths.video)
    _run(_build_cover_command(paths.video, paths.cover))
    _run(_build_contact_sheet_command(paths.video, paths.contact_sheet))
    _write_edl(segments, paths.edl)
    _write_report(segments, segment_paths, paths)

    return {
        "video": paths.video,
        "cover": paths.cover,
        "contact_sheet": paths.contact_sheet,
        "edl": paths.edl,
        "report": paths.report,
        "concat": paths.concat,
    }


def _concat_segments(segment_paths: Sequence[Path], concat_path: Path, release_path: Path) -> None:
    concat_path.write_text(
        "".join(f"file '{path.resolve()}'\n" for path in segment_paths),
        encoding="utf-8",
    )
    _run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_path),
            "-fflags",
            "+genpts",
            "-vf",
            "format=yuv420p",
            "-af",
            "aresample=async=1:first_pts=0",
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
            str(release_path),
        ]
    )


def _build_cover_command(video_path: Path, cover_path: Path) -> list[str]:
    return [
        "ffmpeg",
        "-y",
        "-ss",
        "8",
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
        "fps=1/38,scale=480:-1:flags=lanczos,tile=4x3",
        "-frames:v",
        "1",
        "-update",
        "1",
        str(contact_sheet_path),
    ]


def _write_edl(segments: Sequence[HumanEditSegment], output_path: Path) -> None:
    lines = [
        "# CC Switch DeepSeek Human Edit EDL",
        "",
        f"Source: `{SOURCE_VIDEO}`",
        f"Target duration: `{_format_duration(human_edit_duration(segments))}`",
        "",
        "| # | Source In | Duration | Lens | Edit Intent |",
        "|---|---:|---:|---:|---|",
    ]
    for index, segment in enumerate(segments, start=1):
        lines.append(
            "| "
            f"{index} | "
            f"{_format_duration(segment.start)} | "
            f"{_format_duration(segment.duration)} | "
            f"{segment.zoom:.2f}x | "
            f"{segment.purpose} |"
        )
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_report(
    segments: Sequence[HumanEditSegment],
    segment_paths: Sequence[Path],
    paths: HumanEditPaths,
) -> None:
    report = {
        "project": "cc-switch-deepseek-human-edit",
        "source": str(SOURCE_VIDEO),
        "target_duration_seconds": human_edit_duration(segments),
        "segments": [asdict(segment) for segment in segments],
        "segment_files": [str(path) for path in segment_paths],
        "artifacts": {key: str(value) for key, value in asdict(paths).items()},
        "editing_rules": {
            "source_only": True,
            "visible_overlays": False,
            "progress_bars": False,
            "scene_cards": False,
            "synthetic_voiceover": False,
            "uses_real_cuts": True,
            "uses_digital_punch_ins": True,
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


def _bounded_crop_offset(requested: int, scaled: int, target: int) -> int:
    return max(0, min(requested, scaled - target))


def _format_time(seconds: float) -> str:
    if seconds == int(seconds):
        return str(int(seconds))
    return f"{seconds:.3f}".rstrip("0").rstrip(".")


def _format_duration(seconds: float) -> str:
    whole = int(round(seconds))
    minutes, remainder = divmod(whole, 60)
    return f"{minutes:02d}:{remainder:02d}"


def _run(command: list[str]) -> None:
    subprocess.run(command, check=True)
