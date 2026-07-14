from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
import unicodedata
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Callable, Dict, Literal, Sequence

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

from video_factory.fonts import load_font

from video_factory.audio import analyze_audio_samples, write_audio_analysis_json
from video_factory.content import analyze_content_samples, write_content_analysis_json
from video_factory.creative import (
    CreativePlan,
    analyze_frame_image,
    build_creative_plan,
    build_sample_schedule,
    ranges_overlap,
    write_candidate_edl,
    write_creative_plan_json,
)
from video_factory.semantic import build_semantic_timeline, write_semantic_timeline_json
from video_factory.transcript import build_transcript_analysis, write_transcript_analysis_json


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "video_factory/output"
WIDTH = 1920
HEIGHT = 1080
FPS = 30
CREATIVE_SAMPLE_WIDTH = 1280
CREATIVE_CARD_SECONDS = 2.0
HIGH_QUALITY_CRF = "16"
HIGH_QUALITY_AUDIO_BITRATE = "192k"
CREATIVE_SOURCE_REUSE_BUDGET = 0.62
Mode = Literal["auto", "original-enhanced", "human-edit", "creative-edit"]
ResolvedMode = Literal["original-enhanced", "human-edit", "creative-edit"]
ProgressCallback = Callable[[str], None]


@dataclass(frozen=True)
class VideoGeometry:
    width: int
    height: int


DEFAULT_GEOMETRY = VideoGeometry(width=WIDTH, height=HEIGHT)


@dataclass(frozen=True)
class ReplicatePaths:
    output_dir: Path
    segments_dir: Path
    video: Path
    release_no_subtitles: Path
    cover: Path
    contact_sheet: Path
    edl: Path
    concat: Path
    report: Path
    quality_report: Path
    creative_brief: Path
    creative_plan: Path
    candidate_edl: Path
    cover_candidates: Path
    content_analysis: Path
    audio_analysis: Path
    semantic_timeline: Path
    transcript_analysis: Path
    visual_insert_plan: Path
    images2_prompt_pack: Path
    generated_visual_manifest: Path
    cover_brief: Path
    caption_timeline: Path
    subtitles: Path


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
class FaceOverlayRegion:
    x: int
    y: int
    width: int
    height: int
    confidence: float
    source: str
    start: float = 0.0
    end: float | None = None


def build_replicate_paths(
    input_video: Path | str,
    mode: str,
    output_root: Path | str | None = None,
    job_id: str | None = None,
) -> ReplicatePaths:
    input_video = Path(input_video)
    root = Path(output_root) if output_root is not None else DEFAULT_OUTPUT_ROOT
    suffix = f"-{job_id}" if job_id else ""
    output_dir = root / f"{_slugify(input_video.stem)}-{mode}{suffix}"
    return ReplicatePaths(
        output_dir=output_dir,
        segments_dir=output_dir / "segments",
        video=output_dir / "release.mp4",
        release_no_subtitles=output_dir / "release_no_subtitles.mp4",
        cover=output_dir / "cover.png",
        contact_sheet=output_dir / "contact_sheet.jpg",
        edl=output_dir / "edit_decision_list.md",
        concat=output_dir / "segments.txt",
        report=output_dir / "render_report.json",
        quality_report=output_dir / "quality_report.json",
        creative_brief=output_dir / "creative_brief.md",
        creative_plan=output_dir / "creative_plan.json",
        candidate_edl=output_dir / "candidate_edl.md",
        cover_candidates=output_dir / "cover_candidates.jpg",
        content_analysis=output_dir / "content_analysis.json",
        audio_analysis=output_dir / "audio_analysis.json",
        semantic_timeline=output_dir / "semantic_timeline.json",
        transcript_analysis=output_dir / "transcript_analysis.json",
        visual_insert_plan=output_dir / "visual_insert_plan.json",
        images2_prompt_pack=output_dir / "images2_prompt_pack.json",
        generated_visual_manifest=output_dir / "generated_visual_manifest.json",
        cover_brief=output_dir / "cover_brief.json",
        caption_timeline=output_dir / "caption_timeline.json",
        subtitles=output_dir / "subtitles.srt",
    )


def choose_mode(input_video: Path | str, source_duration: float) -> ResolvedMode:
    del input_video
    if source_duration >= 180:
        return "human-edit"
    return "original-enhanced"


def geometry_from_probe(probe: dict) -> VideoGeometry:
    for stream in probe.get("streams", []):
        if stream.get("codec_type") == "video":
            width = _even_dimension(int(stream.get("width") or WIDTH))
            height = _even_dimension(int(stream.get("height") or HEIGHT))
            return VideoGeometry(width=width, height=height)
    return DEFAULT_GEOMETRY


def cover_seek_seconds(duration: float) -> float:
    if duration <= 0:
        return 0.0
    return round(min(8.0, max(0.0, duration * 0.35)), 3)


def cover_seek_seconds_for_mode(duration: float, mode: str, paths: ReplicatePaths) -> float:
    if mode == "creative-edit":
        return _creative_cover_seek_seconds(paths.creative_plan, duration)
    return cover_seek_seconds(duration)


def build_human_edit_storyboard(source_duration: float) -> list[HumanEditSegment]:
    if source_duration <= 0:
        raise ValueError("source_duration must be positive")

    segment_count = 10 if source_duration >= 300 else max(4, min(8, int(source_duration // 25)))
    target_duration = _target_human_edit_duration(source_duration)
    weights = [0.95, 1.05, 1.12, 1.05, 1.0, 0.95, 1.12, 1.12, 1.0, 0.78][:segment_count]
    total_weight = sum(weights)
    durations = [target_duration * weight / total_weight for weight in weights]
    gap_total = max(0.0, source_duration - target_duration)
    gap = gap_total / max(1, segment_count - 1)
    zooms = [1.0, 1.1, 1.06, 1.08, 1.1, 1.0, 1.12, 1.14, 1.08, 1.0][:segment_count]
    crop_points = [
        (0, 0),
        (96, 30),
        (58, 0),
        (70, 20),
        (150, 40),
        (0, 0),
        (210, 0),
        (130, 65),
        (110, 30),
        (0, 0),
    ][:segment_count]
    purposes = [
        "保留开场和主题，不额外加钩子包装。",
        "跳过重复铺垫，进入背景或核心对象。",
        "保留第一个关键工具、页面或观点。",
        "保留下载、入口、版本或规则说明。",
        "保留配置路径，让观众看到真实过程。",
        "保留账户、官网、数据源或上下文。",
        "推近关键表单、API、数据或比分区域。",
        "推近本地配置、参数、模型或关键开关。",
        "保留验证前的关键选择。",
        "保留验证、结论和结尾收束。",
    ][:segment_count]

    segments: list[HumanEditSegment] = []
    cursor = 0.0
    for index, duration in enumerate(durations):
        start = min(cursor, max(0.0, source_duration - duration))
        if index == segment_count - 1:
            start = max(0.0, source_duration - duration)
        crop_x, crop_y = crop_points[index]
        segments.append(
            HumanEditSegment(
                key=f"segment_{index:02d}",
                source="reference",
                start=round(start, 3),
                duration=round(min(duration, source_duration - start), 3),
                zoom=zooms[index],
                crop_x=crop_x,
                crop_y=crop_y,
                purpose=purposes[index],
            )
        )
        cursor = start + duration + gap
    return segments


def human_edit_duration(segments: Sequence[HumanEditSegment]) -> float:
    return sum(segment.duration for segment in segments)


def build_original_enhance_command(
    source_video: Path | str,
    output_path: Path | str,
    geometry: VideoGeometry | None = None,
    production_options: dict | None = None,
) -> list[str]:
    geometry = geometry or DEFAULT_GEOMETRY
    video_filters = _apply_visual_transform(
        [
            f"scale={geometry.width}:{geometry.height}:flags=lanczos",
            "setsar=1",
            f"fps={FPS}",
            "eq=contrast=1.035:brightness=0.006:saturation=1.035",
            "unsharp=5:5:0.35:3:3:0.08",
            "format=yuv420p",
        ],
        production_options,
    )
    audio_filters = _audio_filters_for_options(production_options)
    return _base_encode_command(source_video, output_path, ",".join(video_filters), ",".join(audio_filters))


def build_human_edit_scene_command(
    source_video: Path | str,
    segment: HumanEditSegment,
    output_path: Path | str,
    geometry: VideoGeometry | None = None,
    production_options: dict | None = None,
) -> list[str]:
    geometry = geometry or DEFAULT_GEOMETRY
    scaled_width = _even_dimension(int(round(geometry.width * segment.zoom)))
    scaled_height = _even_dimension(int(round(geometry.height * segment.zoom)))
    requested_x = _scaled_crop_offset(segment.crop_x, geometry.width, WIDTH)
    requested_y = _scaled_crop_offset(segment.crop_y, geometry.height, HEIGHT)
    crop_x = _bounded_crop_offset(requested_x, scaled_width, geometry.width)
    crop_y = _bounded_crop_offset(requested_y, scaled_height, geometry.height)
    video_filters = _apply_visual_transform(
        [
            f"scale={scaled_width}:{scaled_height}:flags=lanczos",
            f"crop={geometry.width}:{geometry.height}:{crop_x}:{crop_y}",
            "setsar=1",
            f"fps={FPS}",
            "eq=contrast=1.04:brightness=0.006:saturation=1.035",
            "unsharp=5:5:0.34:3:3:0.08",
            "format=yuv420p",
        ],
        production_options,
    )
    audio_filters = _audio_filters_for_options(production_options, resample=True)
    command = _base_encode_command(source_video, output_path, ",".join(video_filters), ",".join(audio_filters))
    return command[:2] + ["-ss", _format_time(segment.start), "-t", _format_time(segment.duration)] + command[2:]


def _apply_visual_transform(filters: list[str], production_options: dict | None) -> list[str]:
    return filters


def _audio_filters_for_options(production_options: dict | None, resample: bool = False) -> list[str]:
    if _audio_policy(production_options) == "replace_later":
        filters = ["volume=0", "aformat=sample_rates=44100:channel_layouts=stereo"]
    else:
        filters = [
            "highpass=f=70",
            "lowpass=f=14500",
            "loudnorm=I=-16:TP=-1.5:LRA=11",
            "aformat=sample_rates=44100:channel_layouts=stereo",
        ]
    if resample:
        filters.append("aresample=async=1:first_pts=0")
    return filters


def _visual_transform_policy(production_options: dict | None) -> str:
    policy = str((production_options or {}).get("visual_transform_policy", "none"))
    return policy if policy in {"none", "remove_presenter", "face_only"} else "none"


def _audio_policy(production_options: dict | None) -> str:
    return str((production_options or {}).get("audio_policy", "preserve_source"))


def _segment_render_options(production_options: dict | None) -> dict | None:
    if not production_options:
        return None
    options = dict(production_options)
    options["visual_transform_policy"] = "none"
    return options


def _apply_release_visual_transform(
    video_path: Path,
    production_options: dict | None,
    progress: ProgressCallback | None = None,
) -> None:
    policy = _visual_transform_policy(production_options)
    if policy not in {"remove_presenter", "face_only"}:
        return
    if policy == "face_only":
        _progress(progress, "人像窗口修补")
        temp_path = video_path.with_name(f"{video_path.stem}.face-only-tmp{video_path.suffix}")
        probe = probe_media(video_path)
        geometry = geometry_from_probe(probe)
        face_region = _detect_face_overlay_region(video_path, geometry=geometry, probe=probe, progress=progress)
        _run(
            _build_face_only_transform_command(
                video_path,
                temp_path,
                geometry=geometry,
                face_region=face_region,
            )
        )
        temp_path.replace(video_path)
        return
    if policy == "remove_presenter":
        _progress(progress, "移除讲解人区域")
        temp_path = video_path.with_name(f"{video_path.stem}.remove-presenter-tmp{video_path.suffix}")
        geometry = geometry_from_probe(probe_media(video_path))
        _run(_build_presenter_remove_transform_command(video_path, temp_path, geometry=geometry))
        temp_path.replace(video_path)
        return

def _build_face_only_transform_command(
    source_video: Path,
    output_path: Path,
    geometry: VideoGeometry | None = None,
    face_path: Path | None = None,
    face_region: FaceOverlayRegion | None = None,
) -> list[str]:
    geometry = geometry or DEFAULT_GEOMETRY
    face_region = face_region or _fallback_face_overlay_region(geometry)
    privacy_region = _expanded_face_privacy_region(face_region, geometry)
    delogo_x, delogo_y, delogo_width, delogo_height = _delogo_privacy_region(privacy_region, geometry)
    enable_filter = _face_overlay_enable_filter(face_region)
    filter_complex = (
        f"[0:v]scale={geometry.width}:{geometry.height}:flags=lanczos,"
        "setsar=1,"
        f"fps={FPS},"
        "eq=contrast=1.018:brightness=0.002:saturation=1.018,"
        "unsharp=3:3:0.12:3:3:0.03,"
        f"delogo=x={delogo_x}:y={delogo_y}:w={delogo_width}:h={delogo_height}:show=0{enable_filter},"
        "format=yuv420p[v]"
    )
    return [
        "ffmpeg",
        "-y",
        "-i",
        str(source_video),
        "-map",
        "[v]",
        "-map",
        "0:a:0?",
        "-filter_complex",
        filter_complex,
        "-r",
        str(FPS),
        "-c:v",
        "libx264",
        "-preset",
        "slow",
        "-crf",
        HIGH_QUALITY_CRF,
        "-profile:v",
        "high",
        "-level",
        "4.1",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "copy",
        "-movflags",
        "+faststart",
        str(output_path),
    ]


def _expanded_face_privacy_region(face_region: FaceOverlayRegion, geometry: VideoGeometry) -> FaceOverlayRegion:
    size_cap = min(int(geometry.width * 0.30), int(geometry.height * 0.48))
    size = _even_dimension(max(96, min(int(round(max(face_region.width, face_region.height) * 1.9)), size_cap)))
    center_x = face_region.x + face_region.width / 2
    center_y = face_region.y + face_region.height / 2
    x = max(0, min(int(round(center_x - size / 2)), geometry.width - size))
    y = max(0, min(int(round(center_y - size / 2)), geometry.height - size))
    return replace(face_region, x=x, y=y, width=size, height=size)


def _delogo_privacy_region(
    privacy_region: FaceOverlayRegion,
    geometry: VideoGeometry,
) -> tuple[int, int, int, int]:
    padding = 1
    x = min(max(1, privacy_region.x + padding), max(1, geometry.width - 3))
    y = min(max(1, privacy_region.y + padding), max(1, geometry.height - 3))
    width = max(2, min(privacy_region.width - padding * 2, geometry.width - x - 1))
    height = max(2, min(privacy_region.height - padding * 2, geometry.height - y - 1))
    return x, y, width, height


def _face_overlay_enable_filter(face_region: FaceOverlayRegion) -> str:
    start = max(0.0, face_region.start)
    end = face_region.end
    if start <= 0 and end is None:
        return ""
    if end is None:
        return f":enable='gte(t,{_format_time(start)})'"
    return f":enable='between(t,{_format_time(start)},{_format_time(max(start, end))})'"


def _build_presenter_remove_transform_command(
    source_video: Path,
    output_path: Path,
    geometry: VideoGeometry | None = None,
) -> list[str]:
    geometry = geometry or DEFAULT_GEOMETRY
    filter_complex = (
        f"[0:v]scale={geometry.width}:{geometry.height}:flags=lanczos,"
        "setsar=1,"
        f"fps={FPS},"
        "eq=contrast=1.025:brightness=0.003:saturation=1.035,"
        "unsharp=3:3:0.18:3:3:0.04,"
        "drawbox=x=iw*0.28:y=ih*0.04:w=iw*0.42:h=ih*0.92:color=0x071018@1.0:t=fill,"
        "drawbox=x=iw*0.285:y=ih*0.055:w=iw*0.41:h=ih*0.89:color=0x0f2130@0.38:t=5,"
        "drawbox=x=0:y=ih*0.82:w=iw:h=ih*0.18:color=0x05080c@1.0:t=fill,"
        "format=yuv420p[v]"
    )
    return [
        "ffmpeg",
        "-y",
        "-i",
        str(source_video),
        "-map",
        "[v]",
        "-map",
        "0:a:0?",
        "-filter_complex",
        filter_complex,
        "-r",
        str(FPS),
        "-c:v",
        "libx264",
        "-preset",
        "slow",
        "-crf",
        HIGH_QUALITY_CRF,
        "-profile:v",
        "high",
        "-level",
        "4.1",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "copy",
        "-movflags",
        "+faststart",
        str(output_path),
    ]


def _write_virtual_face_asset(output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGBA", (720, 720), (0, 0, 0, 0))

    shadow = Image.new("RGBA", image.size, (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow, "RGBA")
    shadow_draw.ellipse((106, 106, 614, 614), fill=(0, 0, 0, 58))
    shadow = shadow.filter(ImageFilter.GaussianBlur(28))
    image.alpha_composite(shadow)

    disc = Image.new("RGBA", image.size, (0, 0, 0, 0))
    disc_draw = ImageDraw.Draw(disc, "RGBA")
    for inset, alpha in ((96, 255), (128, 255), (172, 245), (230, 225)):
        tone = 226 + min(18, inset // 18)
        disc_draw.ellipse((inset, inset, 720 - inset, 720 - inset), fill=(tone, tone + 3, tone + 1, alpha))
    disc_draw.ellipse((112, 112, 608, 608), outline=(255, 255, 255, 210), width=16)
    disc_draw.ellipse((148, 132, 572, 392), fill=(255, 255, 255, 54))
    disc_draw.ellipse((204, 204, 516, 516), fill=(238, 241, 239, 255))
    disc = disc.filter(ImageFilter.GaussianBlur(0.6))
    image.alpha_composite(disc)

    edge_mask = Image.new("L", image.size, 0)
    mask_draw = ImageDraw.Draw(edge_mask)
    mask_draw.ellipse((82, 82, 638, 638), fill=255)
    edge_mask = edge_mask.filter(ImageFilter.GaussianBlur(7))
    current_alpha = image.getchannel("A")
    image.putalpha(Image.composite(current_alpha, Image.new("L", image.size, 0), edge_mask))
    image.save(output_path)


def _detect_face_overlay_region(
    video_path: Path,
    geometry: VideoGeometry,
    probe: dict | None = None,
    progress: ProgressCallback | None = None,
) -> FaceOverlayRegion:
    probe = probe or probe_media(video_path)
    duration = _duration_from_probe(probe)
    sample_width = _even_dimension(min(max(480, geometry.width // 3), 1280))
    sample_height = _even_dimension(int(round(geometry.height * sample_width / max(1, geometry.width))))
    sample_geometry = VideoGeometry(width=sample_width, height=sample_height)
    sample_dir = video_path.with_name(f"{video_path.stem}.face-samples")
    sample_dir.mkdir(parents=True, exist_ok=True)
    sample_timestamps = _face_detection_timestamps(duration)
    candidates: list[tuple[float, FaceOverlayRegion]] = []

    for index, timestamp in enumerate(sample_timestamps):
        frame_path = sample_dir / f"face_{index:02d}_{_format_time(timestamp).replace('.', '_')}.jpg"
        _run(_build_face_detection_frame_command(video_path, timestamp, frame_path, sample_width))
        if not frame_path.exists():
            continue
        region = _estimate_face_overlay_region_from_image(frame_path, sample_geometry)
        if region.source == "skin_tone" and region.confidence >= 0.35:
            candidates.append((timestamp, _scale_face_overlay_region(region, sample_geometry, geometry)))

    if candidates:
        chosen = _median_face_overlay_region([region for _, region in candidates], geometry)
        start, end = _face_overlay_time_window([timestamp for timestamp, _ in candidates], sample_timestamps, duration)
        chosen = replace(chosen, start=start, end=end)
        _progress(progress, f"脸部区域估计 {chosen.confidence:.2f}")
        return chosen

    fallback = _fallback_face_overlay_region(geometry)
    _progress(progress, "未稳定识别脸部，使用保守脸部位置")
    return fallback


def _build_face_detection_frame_command(
    video_path: Path,
    timestamp: float,
    frame_path: Path,
    sample_width: int,
) -> list[str]:
    return [
        "ffmpeg",
        "-y",
        "-ss",
        _format_time(timestamp),
        "-i",
        str(video_path),
        "-frames:v",
        "1",
        "-update",
        "1",
        "-vf",
        f"scale={sample_width}:-1:flags=lanczos",
        str(frame_path),
    ]


def _face_detection_timestamps(duration: float) -> list[float]:
    if duration <= 0:
        return [0.0]
    ratios = (0.12, 0.28, 0.44, 0.60, 0.76)
    end = max(0.0, duration - 0.1)
    return sorted({round(min(end, max(0.0, duration * ratio)), 3) for ratio in ratios})


def _face_overlay_time_window(
    detected_timestamps: Sequence[float],
    sample_timestamps: Sequence[float],
    duration: float,
) -> tuple[float, float | None]:
    if not detected_timestamps:
        return 0.0, None
    detected = sorted(detected_timestamps)
    samples = sorted(sample_timestamps)
    first = detected[0]
    last = detected[-1]
    previous_samples = [timestamp for timestamp in samples if timestamp < first]
    next_samples = [timestamp for timestamp in samples if timestamp > last]
    if previous_samples:
        start = round((previous_samples[-1] + first) / 2, 3)
    else:
        start = 0.0
    if next_samples:
        end = round((last + next_samples[0]) / 2, 3)
    else:
        end = None
    if end is not None and duration > 0:
        end = min(end, duration)
    return start, end


def _estimate_face_overlay_region_from_image(frame_path: Path, geometry: VideoGeometry | None = None) -> FaceOverlayRegion:
    with Image.open(frame_path) as raw_image:
        image = raw_image.convert("RGB")
    width, height = image.size
    geometry = geometry or VideoGeometry(width=width, height=height)
    best = _best_skin_component(image)
    if best is None:
        return _fallback_face_overlay_region(geometry)

    min_x, min_y, max_x, max_y, score = best
    box_width = max(1, max_x - min_x)
    box_height = max(1, max_y - min_y)
    center_x = (min_x + max_x) / 2
    center_y = (min_y + max_y) / 2
    min_size = _even_dimension(int(min(width, height) * 0.13))
    max_size = _even_dimension(int(min(width, height) * 0.36))
    size = _even_dimension(int(max(box_width, box_height) * 1.32))
    size = max(min_size, min(size, max_size))
    x = int(round(center_x - size / 2))
    y = int(round(center_y - size * 0.56))
    x = max(0, min(x, geometry.width - size))
    y = max(0, min(y, geometry.height - size))
    confidence = round(max(0.45, min(0.95, score)), 3)
    return FaceOverlayRegion(x=x, y=y, width=size, height=size, confidence=confidence, source="skin_tone")


def _best_skin_component(image: Image.Image) -> tuple[int, int, int, int, float] | None:
    width, height = image.size
    pixels = image.load()
    step = max(2, min(width, height) // 260)
    grid_width = math.ceil(width / step)
    grid_height = math.ceil(height / step)
    mask = bytearray(grid_width * grid_height)

    for gy in range(grid_height):
        y = min(height - 1, gy * step + step // 2)
        if y > height * 0.9:
            continue
        for gx in range(grid_width):
            x = min(width - 1, gx * step + step // 2)
            if _is_skin_pixel(pixels[x, y]):
                mask[gy * grid_width + gx] = 1

    visited = bytearray(len(mask))
    best: tuple[int, int, int, int, float] | None = None
    min_component_pixels = max(16, int(grid_width * grid_height * 0.00045))

    for start in range(len(mask)):
        if not mask[start] or visited[start]:
            continue
        stack = [start]
        visited[start] = 1
        count = 0
        min_gx = max_gx = start % grid_width
        min_gy = max_gy = start // grid_width
        while stack:
            current = stack.pop()
            count += 1
            gx = current % grid_width
            gy = current // grid_width
            min_gx = min(min_gx, gx)
            max_gx = max(max_gx, gx)
            min_gy = min(min_gy, gy)
            max_gy = max(max_gy, gy)
            for nx, ny in ((gx - 1, gy), (gx + 1, gy), (gx, gy - 1), (gx, gy + 1)):
                if nx < 0 or ny < 0 or nx >= grid_width or ny >= grid_height:
                    continue
                index = ny * grid_width + nx
                if mask[index] and not visited[index]:
                    visited[index] = 1
                    stack.append(index)

        if count < min_component_pixels:
            continue
        min_x = min_gx * step
        min_y = min_gy * step
        max_x = min(width, (max_gx + 1) * step)
        max_y = min(height, (max_gy + 1) * step)
        box_width = max(1, max_x - min_x)
        box_height = max(1, max_y - min_y)
        aspect = box_width / box_height
        if not 0.48 <= aspect <= 1.52:
            continue
        if not height * 0.05 <= box_height <= height * 0.44:
            continue
        if not width * 0.035 <= box_width <= width * 0.36:
            continue
        area_ratio = (box_width * box_height) / max(1, width * height)
        if area_ratio > 0.20:
            continue
        center_y = (min_y + max_y) / 2
        upper_body_score = max(0.0, 1.0 - abs(center_y - height * 0.34) / (height * 0.48))
        compact_score = max(0.0, 1.0 - abs(1.0 - aspect) * 0.55)
        size_score = min(1.0, count / max(1, grid_width * grid_height * 0.018))
        score = 0.35 + 0.28 * upper_body_score + 0.22 * compact_score + 0.15 * size_score
        if best is None or score > best[4]:
            best = (min_x, min_y, max_x, max_y, score)
    return best


def _is_skin_pixel(pixel: tuple[int, int, int]) -> bool:
    red, green, blue = pixel
    if red < 80 or green < 35 or blue < 20:
        return False
    if red <= green * 0.95 or red <= blue * 1.05:
        return False
    if max(red, green, blue) - min(red, green, blue) < 18:
        return False
    luminance = 0.299 * red + 0.587 * green + 0.114 * blue
    cb = 128 - 0.168736 * red - 0.331264 * green + 0.5 * blue
    cr = 128 + 0.5 * red - 0.418688 * green - 0.081312 * blue
    return luminance > 70 and 72 <= cb <= 146 and 128 <= cr <= 190


def _scale_face_overlay_region(
    region: FaceOverlayRegion,
    source_geometry: VideoGeometry,
    target_geometry: VideoGeometry,
) -> FaceOverlayRegion:
    scale_x = target_geometry.width / max(1, source_geometry.width)
    scale_y = target_geometry.height / max(1, source_geometry.height)
    width = _even_dimension(int(round(region.width * scale_x)))
    height = _even_dimension(int(round(region.height * scale_y)))
    x = int(round(region.x * scale_x))
    y = int(round(region.y * scale_y))
    return _clamp_face_overlay_region(
        FaceOverlayRegion(x=x, y=y, width=width, height=height, confidence=region.confidence, source=region.source),
        target_geometry,
    )


def _median_face_overlay_region(candidates: Sequence[FaceOverlayRegion], geometry: VideoGeometry) -> FaceOverlayRegion:
    centers_x = sorted(candidate.x + candidate.width / 2 for candidate in candidates)
    centers_y = sorted(candidate.y + candidate.height / 2 for candidate in candidates)
    sizes = sorted((candidate.width + candidate.height) / 2 for candidate in candidates)
    mid = len(candidates) // 2
    size = _even_dimension(int(round(sizes[mid])))
    x = int(round(centers_x[mid] - size / 2))
    y = int(round(centers_y[mid] - size / 2))
    confidence = round(sum(candidate.confidence for candidate in candidates) / len(candidates), 3)
    return _clamp_face_overlay_region(
        FaceOverlayRegion(x=x, y=y, width=size, height=size, confidence=confidence, source="skin_tone_median"),
        geometry,
    )


def _fallback_face_overlay_region(geometry: VideoGeometry) -> FaceOverlayRegion:
    size = _even_dimension(int(min(geometry.width, geometry.height) * 0.19))
    x = int(round(geometry.width * 0.5 - size / 2))
    y = int(round(geometry.height * 0.14))
    return _clamp_face_overlay_region(
        FaceOverlayRegion(x=x, y=y, width=size, height=size, confidence=0.18, source="fallback"),
        geometry,
    )


def _clamp_face_overlay_region(region: FaceOverlayRegion, geometry: VideoGeometry) -> FaceOverlayRegion:
    width = _even_dimension(max(2, min(region.width, geometry.width)))
    height = _even_dimension(max(2, min(region.height, geometry.height)))
    x = max(0, min(region.x, geometry.width - width))
    y = max(0, min(region.y, geometry.height - height))
    return FaceOverlayRegion(
        x=x,
        y=y,
        width=width,
        height=height,
        confidence=region.confidence,
        source=region.source,
        start=region.start,
        end=region.end,
    )


def render_replicate(
    input_video: Path | str,
    mode: Mode = "auto",
    output_dir: Path | str | None = None,
    progress: ProgressCallback | None = None,
    production_options: dict | None = None,
) -> Dict[str, Path | str]:
    source = Path(input_video).expanduser()
    if not source.exists():
        raise FileNotFoundError(f"Input video does not exist: {source}")

    _progress(progress, "分析源视频")
    source_probe = probe_media(source)
    duration = _duration_from_probe(source_probe)
    geometry = geometry_from_probe(source_probe)
    actual_mode = choose_mode(source, duration) if mode == "auto" else mode
    if actual_mode not in ("original-enhanced", "human-edit", "creative-edit"):
        raise ValueError(f"Unsupported mode: {mode}")

    paths = (
        build_replicate_paths(source, actual_mode)
        if output_dir is None
        else _paths_for_explicit_output_dir(Path(output_dir))
    )
    paths.output_dir.mkdir(parents=True, exist_ok=True)

    if actual_mode == "human-edit":
        _render_human_edit(source, duration, geometry, paths, progress, production_options=production_options)
    elif actual_mode == "creative-edit":
        _render_creative_edit(source, duration, geometry, paths, progress, production_options=production_options)
    else:
        _progress(progress, "渲染原片增强版")
        _run(build_original_enhance_command(source, paths.video, geometry=geometry, production_options=production_options))
        _apply_release_visual_transform(paths.video, production_options, progress)

    if actual_mode == "creative-edit" and paths.creative_plan.exists():
        _progress(progress, "生成自动字幕")
        _write_release_subtitle_artifacts(paths)
        _mux_release_subtitles(paths, progress)

    _progress(progress, "生成封面和质检图")
    output_probe = probe_media(paths.video)
    output_duration = _duration_from_probe(output_probe)
    output_geometry = geometry_from_probe(output_probe)
    cover_seek = cover_seek_seconds_for_mode(output_duration, actual_mode, paths)
    _write_release_cover(
        paths.video,
        paths.cover,
        output_geometry,
        cover_seek,
        title=_release_cover_title_from_paths(paths),
    )
    _write_contact_sheet(paths.video, paths.contact_sheet, output_duration or duration)

    if actual_mode in ("human-edit", "creative-edit") and not paths.edl.exists():
        _write_edl(build_human_edit_storyboard(duration), source, paths.edl)

    quality_result = run_quality_checks(source_probe, output_probe, actual_mode, paths)
    _write_quality_report(quality_result, paths.quality_report)
    if quality_result["status"] != "passed":
        failed_codes = ", ".join(issue["code"] for issue in quality_result["issues"])
        raise ValueError(f"Quality self-check failed: {failed_codes}")

    _write_report(source, source_probe, output_probe, actual_mode, paths, geometry, production_options=production_options)
    _progress(progress, "完成")
    artifacts: Dict[str, Path | str] = {
        "mode": actual_mode,
        "video": paths.video,
        "cover": paths.cover,
        "contact_sheet": paths.contact_sheet,
        "report": paths.report,
        "quality_report": paths.quality_report,
    }
    if actual_mode in ("human-edit", "creative-edit"):
        artifacts["edl"] = paths.edl
    if actual_mode == "creative-edit":
        artifacts["creative_brief"] = paths.creative_brief
        artifacts["creative_plan"] = paths.creative_plan
        artifacts["candidate_edl"] = paths.candidate_edl
        artifacts["cover_candidates"] = paths.cover_candidates
        artifacts["content_analysis"] = paths.content_analysis
        artifacts["audio_analysis"] = paths.audio_analysis
        artifacts["semantic_timeline"] = paths.semantic_timeline
        artifacts["transcript_analysis"] = paths.transcript_analysis
        artifacts["visual_insert_plan"] = paths.visual_insert_plan
        artifacts["images2_prompt_pack"] = paths.images2_prompt_pack
        artifacts["generated_visual_manifest"] = paths.generated_visual_manifest
        artifacts["cover_brief"] = paths.cover_brief
        artifacts["caption_timeline"] = paths.caption_timeline
        artifacts["subtitles"] = paths.subtitles
    return artifacts


def _write_release_subtitle_artifacts(paths: ReplicatePaths) -> None:
    try:
        plan_data = json.loads(paths.creative_plan.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    captions = []
    cursor = 0.0
    segments = plan_data.get("recommended_variant", {}).get("segments", [])
    for index, segment in enumerate(segments):
        duration = max(0.8, min(float(segment.get("duration") or 0.8), 4.2))
        text = _caption_text_for_segment_dict(str(plan_data.get("title") or ""), segment)
        if text:
            captions.append(
                {
                    "index": index,
                    "start": round(cursor, 3),
                    "end": round(cursor + duration, 3),
                    "text": _short_text(text, 32),
                }
            )
        cursor += float(segment.get("duration") or duration)
    caption_timeline = {
        "version": "release_caption_timeline_v1",
        "style": {"placement": "bottom_safe_area", "burn_into_release": True},
        "readability": {"max_chars_per_caption": 32, "min_duration_seconds": 0.8},
        "captions": captions,
    }
    paths.caption_timeline.write_text(json.dumps(caption_timeline, ensure_ascii=False, indent=2), encoding="utf-8")
    write_subtitles_for_release(paths.subtitles, caption_timeline)


def _mux_release_subtitles(paths: ReplicatePaths, progress: ProgressCallback | None = None) -> None:
    if not paths.subtitles.exists() or not paths.video.exists():
        return
    captions = _caption_items_from_timeline(paths.caption_timeline)
    if not captions:
        _progress(progress, "无真实可用字幕，跳过额外字幕")
        return
    paths.release_no_subtitles.unlink(missing_ok=True)
    paths.video.replace(paths.release_no_subtitles)
    _progress(progress, "烧录可见字幕")
    try:
        geometry = geometry_from_probe(probe_media(paths.release_no_subtitles))
        overlays = _write_caption_overlay_images(captions, paths.output_dir / "caption_overlays", geometry)
        if not overlays:
            raise ValueError("no caption overlays generated")
        _run(_build_caption_overlay_burn_command(paths.release_no_subtitles, overlays, paths.video))
    except (OSError, ValueError, subprocess.CalledProcessError):
        paths.video.unlink(missing_ok=True)
        _progress(progress, "烧录字幕失败，降级为字幕轨")
        _run(_build_subtitle_mux_command(paths.release_no_subtitles, paths.subtitles, paths.video))


def _caption_items_from_timeline(caption_timeline_path: Path | str) -> list[dict[str, object]]:
    try:
        payload = json.loads(Path(caption_timeline_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    raw_captions = payload.get("captions", []) if isinstance(payload.get("captions"), list) else []
    captions: list[dict[str, object]] = []
    for raw in raw_captions:
        if not isinstance(raw, dict):
            continue
        text = str(raw.get("text") or "").strip()
        start = float(raw.get("start") or 0.0)
        end = float(raw.get("end") or 0.0)
        if not text or end <= start:
            continue
        captions.append({"start": round(start, 3), "end": round(end, 3), "text": _short_text(text, 32)})
    return captions


def _write_caption_overlay_images(
    captions: Sequence[dict[str, object]],
    output_dir: Path | str,
    geometry: VideoGeometry,
) -> list[dict[str, object]]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    for stale in output_dir.glob("caption_*.png"):
        stale.unlink(missing_ok=True)
    font_size = max(18, int(geometry.height * 0.056))
    font = _caption_font(font_size)
    overlays: list[dict[str, object]] = []
    for index, caption in enumerate(captions, start=1):
        text = str(caption.get("text") or "").strip()
        if not text:
            continue
        lines = _wrap_caption_text(text, font, max_width=int(geometry.width * 0.84), max_lines=2)
        if not lines:
            continue
        image = Image.new("RGBA", (geometry.width, geometry.height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        line_gap = max(4, int(font_size * 0.22))
        padding_x = max(14, int(geometry.width * 0.03))
        padding_y = max(8, int(geometry.height * 0.018))
        line_heights = [_text_bbox(draw, line, font)[3] - _text_bbox(draw, line, font)[1] for line in lines]
        box_height = sum(line_heights) + line_gap * max(0, len(lines) - 1) + padding_y * 2
        bottom_margin = max(18, int(geometry.height * 0.075))
        box_width = min(
            int(geometry.width * 0.90),
            max(_text_bbox(draw, line, font)[2] - _text_bbox(draw, line, font)[0] for line in lines) + padding_x * 2,
        )
        left = int((geometry.width - box_width) / 2)
        top = max(0, geometry.height - bottom_margin - box_height)
        draw.rounded_rectangle(
            (left, top, left + box_width, top + box_height),
            radius=max(8, int(font_size * 0.45)),
            fill=(0, 0, 0, 168),
        )
        y = top + padding_y
        for line, line_height in zip(lines, line_heights):
            bbox = _text_bbox(draw, line, font)
            text_width = bbox[2] - bbox[0]
            x = int((geometry.width - text_width) / 2)
            draw.text((x + 1, y + 1), line, font=font, fill=(0, 0, 0, 210))
            draw.text((x, y), line, font=font, fill=(255, 255, 255, 255))
            y += line_height + line_gap
        path = output_dir / f"caption_{index:03d}.png"
        image.save(path)
        overlays.append({"path": path, "start": caption["start"], "end": caption["end"]})
    return overlays


def _build_caption_overlay_burn_command(
    input_video: Path | str,
    overlays: Sequence[dict[str, object]],
    output_video: Path | str,
) -> list[str]:
    if not overlays:
        raise ValueError("no caption overlays supplied")
    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(input_video),
    ]
    for overlay in overlays:
        command += ["-loop", "1", "-i", str(overlay["path"])]
    current = "0:v"
    filters: list[str] = []
    for index, overlay in enumerate(overlays, start=1):
        output_label = f"v{index}"
        start = float(overlay["start"])
        end = float(overlay["end"])
        filters.append(
            f"[{current}][{index}:v]overlay=0:0:eof_action=pass:enable='between(t\\,{start:.3f}\\,{end:.3f})'[{output_label}]"
        )
        current = output_label
    command += [
        "-filter_complex",
        ";".join(filters),
        "-map",
        f"[{current}]",
        "-map",
        "0:a?",
        "-c:v",
        "libx264",
        "-preset",
        "slow",
        "-crf",
        HIGH_QUALITY_CRF,
        "-c:a",
        "copy",
        "-movflags",
        "+faststart",
        str(output_video),
    ]
    return command


def _caption_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    return load_font(size)


def _wrap_caption_text(text: str, font: ImageFont.FreeTypeFont | ImageFont.ImageFont, max_width: int, max_lines: int) -> list[str]:
    probe = Image.new("RGBA", (10, 10), (0, 0, 0, 0))
    draw = ImageDraw.Draw(probe)
    units = text.split() if " " in text else list(text)
    lines: list[str] = []
    current = ""
    separator = " " if " " in text else ""
    for unit in units:
        candidate = unit if not current else f"{current}{separator}{unit}"
        bbox = _text_bbox(draw, candidate, font)
        if bbox[2] - bbox[0] <= max_width:
            current = candidate
            continue
        if current:
            lines.append(current)
        current = unit
        if len(lines) >= max_lines:
            break
    if current and len(lines) < max_lines:
        lines.append(current)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
    return lines


def _text_bbox(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont | ImageFont.ImageFont) -> tuple[int, int, int, int]:
    bbox = draw.textbbox((0, 0), text, font=font)
    return int(bbox[0]), int(bbox[1]), int(bbox[2]), int(bbox[3])


def _build_subtitle_mux_command(input_video: Path | str, subtitles_path: Path | str, output_video: Path | str) -> list[str]:
    return [
        "ffmpeg",
        "-y",
        "-i",
        str(input_video),
        "-i",
        str(subtitles_path),
        "-map",
        "0",
        "-map",
        "1:0",
        "-c:v",
        "copy",
        "-c:a",
        "copy",
        "-c:s",
        "mov_text",
        "-movflags",
        "+faststart",
        str(output_video),
    ]


def _ffmpeg_filter_path(path: Path | str) -> str:
    value = str(path)
    return value.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def probe_media(path: Path | str) -> dict:
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


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Replicate a reference video with the Video Factory standards.")
    parser.add_argument("--input", required=True, type=Path, help="Source video path.")
    parser.add_argument("--mode", choices=["auto", "original-enhanced", "human-edit", "creative-edit"], default="auto")
    parser.add_argument("--output", type=Path, help="Optional output directory.")
    args = parser.parse_args(argv)

    artifacts = render_replicate(args.input, mode=args.mode, output_dir=args.output, progress=print)
    for key, value in artifacts.items():
        print(f"{key}: {value}")


def _render_human_edit(
    source: Path,
    duration: float,
    geometry: VideoGeometry,
    paths: ReplicatePaths,
    progress: ProgressCallback | None,
    release_path: Path | None = None,
    production_options: dict | None = None,
) -> list[HumanEditSegment]:
    paths.segments_dir.mkdir(parents=True, exist_ok=True)
    segments = build_human_edit_storyboard(duration)
    segment_paths: list[Path] = []
    if _audio_policy(production_options) == "replace_later":
        _progress(progress, "移除原片音频，等待原创配音")
    segment_options = _segment_render_options(production_options)
    for index, segment in enumerate(segments):
        segment_path = paths.segments_dir / f"{index:02d}_{segment.key}.mp4"
        _progress(progress, f"渲染剪辑片段 {index + 1}/{len(segments)}")
        _run(build_human_edit_scene_command(source, segment, segment_path, geometry=geometry, production_options=segment_options))
        segment_paths.append(segment_path)

    _write_edl(segments, source, paths.edl)
    output_path = release_path or paths.video
    _concat_segments(segment_paths, paths.concat, output_path)
    _apply_release_visual_transform(output_path, production_options, progress)
    return segments


def _render_creative_edit(
    source: Path,
    duration: float,
    geometry: VideoGeometry,
    paths: ReplicatePaths,
    progress: ProgressCallback | None,
    production_options: dict | None = None,
) -> None:
    paths.segments_dir.mkdir(parents=True, exist_ok=True)
    _progress(progress, "抽取真实画面样本")
    title = _creative_title_for_render(source, production_options)
    sample_paths = _extract_creative_sample_frames(source, duration, paths)
    samples = []
    previous_path: Path | None = None
    for index, (timestamp, sample_path) in enumerate(sample_paths):
        samples.append(analyze_frame_image(sample_path, timestamp=timestamp, index=index, previous_image_path=previous_path))
        previous_path = sample_path
    content_analysis = analyze_content_samples(sample_paths, title=title)
    write_content_analysis_json(content_analysis, paths.content_analysis)
    audio_analysis = analyze_audio_samples(source, sample_paths)
    write_audio_analysis_json(audio_analysis, paths.audio_analysis)
    transcript_analysis = build_transcript_analysis(source, sample_paths, content_analysis=content_analysis)
    write_transcript_analysis_json(transcript_analysis, paths.transcript_analysis)
    semantic_timeline = build_semantic_timeline(
        content_analysis,
        audio_analysis,
        title=title,
        source_duration=duration,
        transcript_analysis=transcript_analysis,
    )
    write_semantic_timeline_json(semantic_timeline, paths.semantic_timeline)
    _progress(progress, "生成创作剪辑方案")
    plan = build_creative_plan(
        duration,
        samples,
        title=title,
        content_analysis=content_analysis,
        audio_analysis=audio_analysis,
        semantic_timeline=semantic_timeline,
        production_options=production_options,
    )
    write_creative_plan_json(plan, paths.creative_plan)
    write_candidate_edl(plan, paths.candidate_edl)
    _write_cover_candidates(plan, {index: path for index, (_, path) in enumerate(sample_paths)}, paths.cover_candidates)
    visual_insert_plan = build_visual_insert_plan(plan, production_options)
    paths.visual_insert_plan.write_text(json.dumps(visual_insert_plan, ensure_ascii=False, indent=2), encoding="utf-8")
    prompt_pack = build_images2_prompt_pack(visual_insert_plan, production_options)
    paths.images2_prompt_pack.write_text(json.dumps(prompt_pack, ensure_ascii=False, indent=2), encoding="utf-8")
    provider = str((production_options or {}).get("image_provider") or prompt_pack.get("provider") or "mock_images2")
    generated_visual_manifest = build_generated_visual_manifest(
        paths.output_dir / "generated_visuals",
        prompt_pack,
        provider=provider,
    )
    paths.generated_visual_manifest.write_text(
        json.dumps(generated_visual_manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    paths.cover_brief.write_text(
        json.dumps(_build_cover_brief(plan, visual_insert_plan), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if _visual_asset_strategy(production_options) == "images2_contextual_inserts":
        original_inserts = _publishable_release_visual_inserts(
            visual_insert_plan.get("inserts", []),
            generated_visual_manifest,
        )
        segments = _trim_source_segments_for_insert_budget(
            creative_segments_from_plan(plan),
            sum(float(insert.get("duration") or 0.0) for insert in original_inserts),
            plan.creative_strategy.target_duration,
        )
    else:
        segments, original_inserts = creative_release_timeline(plan, production_options=production_options)
    inserts_by_after: dict[int, list[dict[str, object]]] = {}
    for insert in original_inserts:
        after_index = int(insert.get("after_index", 0))
        inserts_by_after.setdefault(after_index, []).append(insert)
    generated_visual_by_id = {
        str(item.get("insert_id")): Path(str(item.get("path")))
        for item in generated_visual_manifest.get("visuals", [])
        if item.get("insert_id") and item.get("path")
    }
    segment_paths: list[Path] = []
    if _audio_policy(production_options) == "replace_later":
        _progress(progress, "移除原片音频，等待原创配音")
    segment_options = _segment_render_options(production_options)
    for index, segment in enumerate(segments):
        segment_path = paths.segments_dir / f"{index:02d}_{segment.key}.mp4"
        _progress(progress, f"渲染创作片段 {index + 1}/{len(segments)}")
        _run(build_human_edit_scene_command(source, segment, segment_path, geometry=geometry, production_options=segment_options))
        segment_paths.append(segment_path)
        for insert in inserts_by_after.get(index, []):
            insert_key = str(insert.get("key") or f"original_explainer_{len(segment_paths):02d}")
            card_image = paths.segments_dir / f"{len(segment_paths):02d}_{insert_key}.png"
            card_video = paths.segments_dir / f"{len(segment_paths):02d}_{insert_key}.mp4"
            if str(insert.get("source_type")) == "ai_contextual_visual":
                insert_key = str(insert.get("insert_id") or insert_key)
                card_image = generated_visual_by_id.get(insert_key, card_image)
                _progress(progress, f"渲染 AI 补充镜头 {len(segment_paths)}/{len(segments) + len(original_inserts)}")
            else:
                _progress(progress, f"渲染原创解释段 {len(segment_paths)}/{len(segments) + len(original_inserts)}")
                build_creative_card(
                    str(insert.get("title") or "原创解读"),
                    str(insert.get("subtitle") or "补充原创观点和背景解释。"),
                    geometry,
                    card_image,
                )
            _run(_build_card_video_command(card_image, card_video, float(insert.get("duration") or CREATIVE_CARD_SECONDS), geometry))
            segment_paths.append(card_video)
    _write_edl(segments, source, paths.edl)
    _concat_segments(segment_paths, paths.concat, paths.video)
    _apply_release_visual_transform(paths.video, production_options, progress)
    title = _creative_title_for_render(source, production_options)
    _write_creative_brief(
        source,
        title,
        segments,
        geometry,
        paths.creative_brief,
        plan=plan,
        original_inserts=original_inserts,
    )


def creative_segments_from_plan(plan: CreativePlan) -> list[HumanEditSegment]:
    return [
        HumanEditSegment(
            key=segment.key,
            source="reference",
            start=segment.start,
            duration=segment.duration,
            zoom=segment.zoom,
            crop_x=segment.crop_x,
            crop_y=segment.crop_y,
            purpose=segment.purpose,
        )
        for segment in plan.recommended_variant.segments
    ]


def creative_release_timeline(
    plan: CreativePlan,
    production_options: dict | None = None,
) -> tuple[list[HumanEditSegment], list[dict[str, object]]]:
    source_segments = creative_segments_from_plan(plan)
    if not _should_insert_original_explainers(plan, production_options):
        return source_segments, []

    target_duration = max(plan.creative_strategy.target_duration, sum(segment.duration for segment in source_segments))
    source_budget = max(18.0, target_duration * CREATIVE_SOURCE_REUSE_BUDGET)
    source_total = sum(segment.duration for segment in source_segments)
    if source_total > source_budget:
        scale = source_budget / source_total
        source_segments = [_trim_source_segment_for_original_mix(segment, scale, plan.source_duration) for segment in source_segments]
        source_total = sum(segment.duration for segment in source_segments)

    insert_total = max(0.0, target_duration - source_total)
    if plan.source_duration >= 300:
        insert_total = max(insert_total, 60.0)
    if insert_total < 12.0:
        return source_segments, []

    insert_count = min(7, max(3, math.ceil(len(source_segments) / 5)))
    insert_duration = round(insert_total / insert_count, 3)
    anchors = _original_insert_anchor_indices(len(source_segments), insert_count)
    inserts: list[dict[str, object]] = []
    for index, after_index in enumerate(anchors):
        segment = plan.recommended_variant.segments[min(after_index, len(plan.recommended_variant.segments) - 1)]
        title = _original_insert_title(index, segment.semantic_role)
        subtitle = _original_insert_subtitle(plan.title, segment)
        inserts.append(
            {
                "key": f"original_explainer_{index:02d}",
                "source_type": "original_explainer",
                "after_index": after_index,
                "duration": insert_duration,
                "title": title,
                "subtitle": subtitle,
            }
        )
    return source_segments, inserts


def build_visual_insert_plan(plan: CreativePlan, production_options: dict | None = None) -> dict:
    strategy = _visual_asset_strategy(production_options)
    release_duration = float(plan.creative_strategy.target_duration or plan.recommended_variant.total_duration)
    base = {
        "version": "visual_insert_plan_v1",
        "strategy": strategy,
        "source_title": plan.title,
        "source_video_is_primary": True,
        "release_duration_seconds": round(release_duration, 3),
        "max_ai_insert_ratio": 0.08,
        "total_ai_insert_duration": 0.0,
        "inserts": [],
        "policy": {
            "no_ppt_cards": True,
            "no_consecutive_ai_visuals": True,
            "must_bind_to_source_evidence": True,
            "ai_visuals_are_supporting_only": True,
        },
    }
    if strategy != "images2_contextual_inserts":
        return base

    segments = list(plan.recommended_variant.segments)
    if len(segments) < 3 or plan.source_duration < 40:
        return base

    insert_count = min(3, max(1, math.floor(plan.source_duration / 180)))
    anchors = _original_insert_anchor_indices(len(segments), insert_count)
    max_total = min(release_duration * 0.08, 18.0)
    if max_total < 2.0:
        return base
    per_insert_duration = round(min(4.0, max(2.4, max_total / max(1, len(anchors)))), 3)

    inserts: list[dict[str, object]] = []
    for index, after_index in enumerate(anchors):
        segment = segments[after_index]
        insert_type = ("explanation_visual", "detail_cutaway", "comparison_visual", "chapter_transition")[
            index % 4
        ]
        evidence = _visual_insert_source_evidence(segment)
        context_binding = {
            "chapter": segment.chapter_title or segment.semantic_topic,
            "semantic_role": segment.semantic_role,
            "source_time": _format_duration(segment.start),
            "creative_move": segment.creative_move,
        }
        goal = _visual_insert_prompt_goal(plan.title, segment, insert_type)
        inserts.append(
            {
                "insert_id": f"ai_visual_{index:02d}",
                "source_type": "ai_contextual_visual",
                "insert_type": insert_type,
                "after_index": after_index,
                "duration": per_insert_duration,
                "source_evidence": evidence,
                "context_binding": context_binding,
                "prompt_goal": goal,
                "visual_style": (
                    "documentary contextual still, cinematic but natural, no PPT card, "
                    "no text-heavy layout, match the source video's mood"
                ),
                "placement_reason": "补足原片没有拍清楚但观众需要理解的上下文。",
            }
        )
    base["inserts"] = inserts
    base["total_ai_insert_duration"] = round(sum(float(insert["duration"]) for insert in inserts), 3)
    return base


def build_images2_prompt_pack(visual_insert_plan: dict, options: dict | None = None) -> dict:
    options = options or {}
    provider = str(options.get("image_provider") or "mock_images2").strip() or "mock_images2"
    title = str(visual_insert_plan.get("source_title") or "视频发布增强")
    prompts = []
    for insert in visual_insert_plan.get("inserts", []):
        evidence = " / ".join(str(item) for item in insert.get("source_evidence", []) if str(item).strip())
        context = insert.get("context_binding", {}) if isinstance(insert.get("context_binding"), dict) else {}
        prompt = (
            "High quality documentary contextual visual, realistic editorial still, "
            f"topic: {title}, goal: {insert.get('prompt_goal', '')}, "
            f"chapter: {context.get('chapter', '')}, evidence: {evidence}, "
            "natural lighting, cinematic composition, no fake screenshot, no copied source frame."
        )
        prompts.append(
            {
                "insert_id": str(insert.get("insert_id") or f"ai_visual_{len(prompts):02d}"),
                "insert_type": str(insert.get("insert_type") or "explanation_visual"),
                "prompt": prompt,
                "negative_prompt": (
                    "do not create a PPT slide, no heavy text, no UI template, no title card, "
                    "no fake news image, no unrelated object, no copied source frame"
                ),
                "duration": float(insert.get("duration") or 0.0),
                "source_evidence": list(insert.get("source_evidence", [])),
                "context_binding": context,
            }
        )
    return {
        "version": "images2_prompt_pack_v1",
        "provider": provider,
        "built_from": "visual_insert_plan",
        "source_title": title,
        "prompt_count": len(prompts),
        "prompts": prompts,
    }


def build_generated_visual_manifest(output_dir: Path | str, prompt_pack: dict, provider: str = "mock_images2") -> dict:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    publish_ready = provider == "images2"
    visuals = []
    for index, prompt in enumerate(prompt_pack.get("prompts", [])):
        insert_id = str(prompt.get("insert_id") or f"ai_visual_{index:02d}")
        path = output_dir / f"{insert_id}.png"
        _write_contextual_visual_asset(path, prompt, index)
        visuals.append(
            {
                "insert_id": insert_id,
                "path": str(path),
                "origin": "ai_generated",
                "provider": provider,
                "usage": "contextual_insert",
                "publish_ready": publish_ready,
                "source_evidence": list(prompt.get("source_evidence", [])),
            }
        )
    return {
        "version": "generated_visual_manifest_v1",
        "status": "ready" if visuals else "not_needed",
        "provider": provider,
        "publish_ready": publish_ready and bool(visuals),
        "visual_count": len(visuals),
        "visuals": visuals,
        "publish_note": (
            "images2 assets ready" if publish_ready else "mock_images2 preview only; replace with real images2 assets before publish"
        ),
    }


def _publishable_release_visual_inserts(
    inserts: Sequence[dict[str, object]],
    generated_visual_manifest: dict,
) -> list[dict[str, object]]:
    if not generated_visual_manifest.get("publish_ready"):
        return []
    visuals = generated_visual_manifest.get("visuals", [])
    publishable_ids = {
        str(item.get("insert_id"))
        for item in visuals
        if isinstance(item, dict)
        and item.get("publish_ready")
        and str(item.get("insert_id") or "").strip()
        and str(item.get("path") or "").strip()
    }
    return [
        insert
        for insert in inserts
        if isinstance(insert, dict)
        and str(insert.get("source_type") or "") == "ai_contextual_visual"
        and str(insert.get("insert_id") or "") in publishable_ids
    ]


def build_caption_timeline_for_release(plan: CreativePlan) -> dict:
    captions = []
    cursor = 0.0
    for index, segment in enumerate(plan.recommended_variant.segments):
        text = _caption_text_for_segment(plan.title, segment)
        duration = max(0.8, min(float(segment.duration), 4.2))
        if text:
            captions.append(
                {
                    "index": index,
                    "start": round(cursor, 3),
                    "end": round(cursor + duration, 3),
                    "text": _short_text(text, 32),
                    "source_segment_key": segment.key,
                }
            )
        cursor += float(segment.duration)
    return {
        "version": "release_caption_timeline_v1",
        "style": {"placement": "bottom_safe_area", "burn_into_release": True},
        "readability": {"max_chars_per_caption": 32, "min_duration_seconds": 0.8},
        "captions": captions,
    }


def write_subtitles_for_release(output_path: Path | str, caption_timeline: dict) -> None:
    captions = caption_timeline.get("captions", []) if isinstance(caption_timeline.get("captions"), list) else []
    lines: list[str] = []
    for index, caption in enumerate(captions, start=1):
        start = _srt_time(float(caption.get("start") or 0.0))
        end = _srt_time(float(caption.get("end") or 0.0))
        text = str(caption.get("text") or "").strip()
        if not text:
            continue
        lines.extend([str(index), f"{start} --> {end}", text, ""])
    Path(output_path).write_text("\n".join(lines), encoding="utf-8")


def _visual_asset_strategy(production_options: dict | None) -> str:
    strategy = str((production_options or {}).get("visual_asset_strategy", "user_owned_first")).strip()
    if strategy == "images2_contextual_inserts":
        return strategy
    return strategy if strategy in {"images2_first", "images2_only", "licensed_stock_fallback", "user_owned_first"} else "user_owned_first"


def _visual_insert_source_evidence(segment) -> list[str]:
    evidence = []
    if segment.chapter_title:
        evidence.append(f"chapter:{segment.chapter_title}")
    if segment.content_evidence:
        evidence.append(f"content:{segment.content_evidence}")
    if segment.audio_evidence:
        evidence.append(f"audio:{segment.audio_evidence}")
    if segment.transcript_evidence:
        evidence.append(f"transcript:{segment.transcript_evidence}")
    if not evidence:
        evidence.append(f"source_time:{_format_duration(segment.start)}")
        evidence.append(f"semantic_role:{segment.semantic_role}")
    return evidence[:4]


def _visual_insert_prompt_goal(title: str, segment, insert_type: str) -> str:
    chapter = segment.chapter_title or segment.semantic_topic or segment.semantic_role or "关键段落"
    goals = {
        "explanation_visual": f"解释 {chapter} 背后的上下文，让观众理解这一段为什么重要",
        "detail_cutaway": f"补一个 {chapter} 的细节近景，强化原片没拍清楚的重点",
        "comparison_visual": f"用前后对比方式表达 {chapter} 的变化",
        "chapter_transition": f"为 {chapter} 做自然过渡，连接前后真实画面",
    }
    return _short_text(f"{title} / {goals.get(insert_type, goals['explanation_visual'])}", 140)


def _trim_source_segments_for_insert_budget(
    segments: Sequence[HumanEditSegment],
    insert_duration: float,
    target_duration: float,
) -> list[HumanEditSegment]:
    source_total = sum(segment.duration for segment in segments)
    if source_total <= 0:
        return list(segments)
    source_budget = max(12.0, float(target_duration or source_total) - max(0.0, insert_duration))
    if source_total <= source_budget:
        return list(segments)
    scale = source_budget / source_total
    return [replace(segment, duration=round(max(1.4, segment.duration * scale), 3)) for segment in segments]


def _build_cover_brief(plan: CreativePlan, visual_insert_plan: dict) -> dict:
    chapters = [
        segment.chapter_title or segment.semantic_topic or segment.semantic_role
        for segment in plan.recommended_variant.segments[:5]
    ]
    chapters = [_public_caption_candidate(chapter) for chapter in chapters]
    chapters = [chapter for chapter in chapters if chapter]
    return {
        "version": "release_cover_brief_v1",
        "source_title": plan.title,
        "cover_goal": "用简洁封面传达原片最核心的信息，不堆砌文字。",
        "recommended_text": _release_cover_display_title(plan.title),
        "visual_direction": "优先使用原片关键帧；images2 可生成更清晰的发布封面。",
        "context_chapters": chapters,
        "ai_visual_insert_count": len(visual_insert_plan.get("inserts", [])),
        "constraints": {
            "max_title_chars": 22,
            "avoid_overcomplicated_layout": True,
            "avoid_fake_news_scene": True,
        },
    }


def _caption_text_for_segment(title: str, segment) -> str:
    return _caption_text_from_candidates(
        title,
        (
            segment.transcript_evidence,
            segment.chapter_title,
            segment.semantic_topic,
            segment.content_evidence,
        ),
    )


def _caption_text_for_segment_dict(title: str, segment: dict) -> str:
    return _caption_text_from_candidates(
        title,
        (
            segment.get("transcript_evidence"),
            segment.get("chapter_title"),
            segment.get("semantic_topic"),
            segment.get("content_evidence"),
        ),
    )


def _caption_text_from_candidates(title: str, candidates: Sequence[object]) -> str:
    for value in candidates:
        cleaned = _public_caption_candidate(value)
        if cleaned:
            return cleaned
    return ""


_INTERNAL_CAPTION_LABELS = {
    "上下文铺垫",
    "内容章节",
    "context",
    "install",
    "pricing",
    "provider_setup",
    "api_key",
    "local_route",
    "validation",
    "closing",
    "visual_hook",
    "context_bridge",
    "action_moment",
    "decision_moment",
    "detail_moment",
    "result_moment",
    "tutorial_hook",
    "operation_step",
    "interface_state",
    "configuration_detail",
    "result_validation",
    "food_hook",
    "prep_action",
    "cook_transform",
    "final_payoff",
}


def _public_caption_candidate(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if ":" in text:
        prefix, suffix = text.split(":", 1)
        if prefix.strip().lower() in {"ocr", "srt", "subtitle", "transcript"}:
            text = suffix.strip()
    normalized = re.sub(r"\s+", " ", text).strip()
    normalized_lower = normalized.lower()
    if normalized_lower in _INTERNAL_CAPTION_LABELS:
        return ""
    if _looks_like_analysis_metric(normalized_lower):
        return ""
    return normalized


def _looks_like_analysis_metric(text: str) -> bool:
    metric_markers = (
        "text_density",
        "subtitle_likelihood",
        "interface_likelihood",
        "mean_volume",
        "max_volume",
        "speech_likelihood",
        "visual_tags",
        "motion=",
        "sharpness=",
        "color=",
        "t=",
    )
    return any(marker in text for marker in metric_markers)


def _write_contextual_visual_asset(output_path: Path, prompt: dict, index: int) -> None:
    width, height = WIDTH, HEIGHT
    palette = [
        ("#192127", "#2f6257", "#d8b45a"),
        ("#161b26", "#365d86", "#d9c8a2"),
        ("#1f1c18", "#73543d", "#e0a950"),
        ("#111f1d", "#4c806d", "#efe6cf"),
    ][index % 4]
    image = Image.new("RGB", (width, height), palette[0])
    draw = ImageDraw.Draw(image, "RGBA")
    for y in range(height):
        ratio = y / max(1, height - 1)
        red = int(int(palette[0][1:3], 16) * (1 - ratio) + int(palette[1][1:3], 16) * ratio)
        green = int(int(palette[0][3:5], 16) * (1 - ratio) + int(palette[1][3:5], 16) * ratio)
        blue = int(int(palette[0][5:7], 16) * (1 - ratio) + int(palette[1][5:7], 16) * ratio)
        draw.line((0, y, width, y), fill=(red, green, blue, 255))
    draw.rectangle((int(width * 0.08), int(height * 0.16), int(width * 0.92), int(height * 0.82)), outline=palette[2], width=5)
    for offset in range(5):
        x0 = int(width * (0.16 + offset * 0.13))
        y0 = int(height * (0.25 + (offset % 2) * 0.08))
        x1 = x0 + int(width * 0.16)
        y1 = y0 + int(height * 0.32)
        draw.rounded_rectangle((x0, y0, x1, y1), radius=18, fill=(245, 238, 220, 32), outline=(245, 238, 220, 82), width=3)
    draw.ellipse((int(width * 0.62), int(height * 0.18), int(width * 0.84), int(height * 0.58)), fill=(230, 182, 76, 132))
    draw.line((int(width * 0.1), int(height * 0.74), int(width * 0.9), int(height * 0.58)), fill=(255, 255, 255, 92), width=4)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path)


def _srt_time(seconds: float) -> str:
    milliseconds = int(round(max(0.0, seconds) * 1000))
    hours, remainder = divmod(milliseconds, 3600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def _should_insert_original_explainers(plan: CreativePlan, production_options: dict | None) -> bool:
    options = production_options or {}
    policy = str(options.get("original_insert_policy", "")).strip()
    if policy == "none":
        return False
    if policy == "chapter_explainers":
        return True
    if str(options.get("audio_policy", "")) == "replace_later":
        return True
    if str(options.get("quality_strictness", "")) == "audit":
        return True
    return str(options.get("creative_strength", "")) == "strong" and plan.source_duration >= 120


def _trim_source_segment_for_original_mix(
    segment: HumanEditSegment,
    scale: float,
    source_duration: float,
) -> HumanEditSegment:
    minimum = 2.4 if source_duration < 120 else 3.2
    duration = max(minimum, segment.duration * scale)
    duration = min(segment.duration, duration)
    return replace(segment, duration=round(duration, 3))


def _original_insert_anchor_indices(source_count: int, insert_count: int) -> list[int]:
    if source_count <= 0:
        return []
    anchors: list[int] = []
    for index in range(insert_count):
        anchor = round((index + 1) * source_count / (insert_count + 1)) - 1
        anchors.append(max(0, min(source_count - 1, anchor)))
    return sorted(set(anchors))


def _original_insert_title(index: int, semantic_role: str) -> str:
    labels = {
        "visual_hook": "先给判断",
        "context_bridge": "补齐背景",
        "action_moment": "关键动作",
        "decision_moment": "转折判断",
        "detail_moment": "细节证据",
        "result_moment": "结果解释",
        "result_validation": "验证结论",
    }
    label = labels.get(semantic_role, "原创解读")
    return f"{index + 1:02d} {label}"


def _original_insert_subtitle(title: str, segment) -> str:
    source_title = str(title or "参考视频").strip()
    chapter = str(getattr(segment, "chapter_title", "") or getattr(segment, "semantic_topic", "") or "").strip()
    evidence = str(getattr(segment, "role_evidence", "") or getattr(segment, "content_evidence", "") or "").strip()
    parts = [part for part in (chapter, evidence) if part]
    if parts:
        return _short_text(f"{source_title} / {' / '.join(parts)}", 68)
    return _short_text(f"{source_title} / 这一段补充原创观点、背景和观看判断", 68)


def _short_text(value: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def creative_sample_count_for_duration(duration: float) -> int:
    if duration < 12:
        return 6
    if duration < 40:
        return max(6, int(duration // 2))
    if duration < 240:
        return 18
    return 36


def build_creative_sample_extract_command(source: Path, timestamp: float, frame_path: Path) -> list[str]:
    return [
        "ffmpeg",
        "-y",
        "-ss",
        _format_time(timestamp),
        "-i",
        str(source),
        "-frames:v",
        "1",
        "-update",
        "1",
        "-vf",
        f"scale={CREATIVE_SAMPLE_WIDTH}:-1:flags=lanczos",
        str(frame_path),
    ]


def _extract_creative_sample_frames(source: Path, duration: float, paths: ReplicatePaths) -> list[tuple[float, Path]]:
    sample_dir = paths.output_dir / "creative_samples"
    sample_dir.mkdir(parents=True, exist_ok=True)
    sample_count = creative_sample_count_for_duration(duration)
    samples: list[tuple[float, Path]] = []
    for index, timestamp in enumerate(build_sample_schedule(duration, sample_count=sample_count)):
        frame_path = sample_dir / f"sample_{index:02d}_{_format_time(timestamp).replace('.', '_')}.jpg"
        _run(build_creative_sample_extract_command(source, timestamp, frame_path))
        samples.append((timestamp, frame_path))
    return samples


def _write_cover_candidates(plan: CreativePlan, sample_paths: dict[int, Path], output_path: Path) -> None:
    candidates = list(plan.cover_candidates) or list(plan.moments[:4])
    if not candidates:
        return
    thumbs: list[Image.Image] = []
    for candidate in candidates[:4]:
        path = sample_paths.get(candidate.sample_index)
        if path is None or not path.exists():
            continue
        image = Image.open(path).convert("RGB")
        thumbs.append(ImageOps.contain(image, (360, 640), method=Image.Resampling.LANCZOS))
    if not thumbs:
        return

    width = max(image.width for image in thumbs)
    height = max(image.height for image in thumbs)
    sheet = Image.new("RGB", (width * len(thumbs), height), "#111111")
    for index, image in enumerate(thumbs):
        x = index * width + (width - image.width) // 2
        y = (height - image.height) // 2
        sheet.paste(image, (x, y))
    sheet.save(output_path)


def _concat_segments(segment_paths: Sequence[Path], concat_path: Path, release_path: Path) -> None:
    concat_path.write_text("".join(f"file '{path.resolve()}'\n" for path in segment_paths), encoding="utf-8")
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
            HIGH_QUALITY_CRF,
            "-profile:v",
            "high",
            "-level",
            "4.1",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            HIGH_QUALITY_AUDIO_BITRATE,
            "-movflags",
            "+faststart",
            str(release_path),
        ]
    )


def _base_encode_command(
    source_video: Path | str,
    output_path: Path | str,
    video_filter: str,
    audio_filter: str,
) -> list[str]:
    return [
        "ffmpeg",
        "-y",
        "-i",
        str(source_video),
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
        HIGH_QUALITY_CRF,
        "-profile:v",
        "high",
        "-level",
        "4.1",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        HIGH_QUALITY_AUDIO_BITRATE,
        "-movflags",
        "+faststart",
        str(output_path),
    ]


def _build_cover_command(
    video_path: Path,
    cover_path: Path,
    geometry: VideoGeometry,
    seek_seconds: float,
) -> list[str]:
    return [
        "ffmpeg",
        "-y",
        "-ss",
        _format_time(seek_seconds),
        "-i",
        str(video_path),
        "-frames:v",
        "1",
        "-update",
        "1",
        "-vf",
        f"scale={geometry.width}:{geometry.height}:flags=lanczos",
        str(cover_path),
    ]


def _write_release_cover(
    video_path: Path,
    cover_path: Path,
    geometry: VideoGeometry,
    seek_seconds: float,
    title: str = "",
) -> None:
    cover_path.parent.mkdir(parents=True, exist_ok=True)
    source_frame = cover_path.with_name("_cover_source_frame.jpg")
    _run(_build_cover_command(video_path, source_frame, geometry, seek_seconds))
    try:
        _compose_release_cover(source_frame, cover_path, geometry, title)
    except Exception:
        cover_path.write_bytes(source_frame.read_bytes())


def _compose_release_cover(
    source_frame: Path | str,
    cover_path: Path | str,
    geometry: VideoGeometry,
    title: str = "",
) -> None:
    width, height = geometry.width, geometry.height
    image = Image.open(source_frame).convert("RGB")
    image = ImageOps.fit(image, (width, height), method=Image.Resampling.LANCZOS)
    image = _cover_frame_without_burned_subtitles(image)
    image = ImageOps.autocontrast(image)
    canvas = image.convert("RGBA")

    overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay, "RGBA")
    for y in range(height):
        ratio = y / max(1, height - 1)
        alpha = int(16 + 150 * (ratio**1.9))
        draw.line((0, y, width, y), fill=(0, 0, 0, alpha))
    canvas = Image.alpha_composite(canvas, overlay)

    display_title = _release_cover_display_title(title)
    if display_title:
        draw = ImageDraw.Draw(canvas)
        margin = max(28, int(min(width, height) * 0.075))
        max_text_width = int(width * (0.72 if width >= height else 0.84))
        title_font = _font(max(26, int(min(width, height) * 0.105)), bold=True)
        title_lines = _wrap_text_to_pixel_width(display_title, title_font, max_text_width)[:2]
        line_gap = max(6, int(height * 0.018))
        block_height = sum(_text_height(line, title_font) for line in title_lines) + line_gap * max(0, len(title_lines) - 1)
        y = height - margin - block_height
        accent_width = max(46, int(width * 0.12))
        draw.rounded_rectangle(
            (margin, y - margin // 2, margin + accent_width, y - margin // 2 + max(4, height // 90)),
            radius=max(1, height // 220),
            fill="#20b486",
        )
        for line in title_lines:
            draw.text(
                (margin, y),
                line,
                fill="#ffffff",
                font=title_font,
                stroke_width=max(2, width // 360),
                stroke_fill="#111111",
            )
            y += _text_height(line, title_font) + line_gap

    Path(cover_path).parent.mkdir(parents=True, exist_ok=True)
    canvas.convert("RGB").save(cover_path, quality=94)


def _cover_frame_without_burned_subtitles(image: Image.Image) -> Image.Image:
    width, height = image.size
    crop_ratio = 0.80 if width >= height else 0.86
    cropped = image.crop((0, 0, width, max(1, int(height * crop_ratio))))
    return ImageOps.fit(cropped, (width, height), method=Image.Resampling.LANCZOS, centering=(0.5, 0.38))


def _release_cover_title_from_paths(paths: ReplicatePaths) -> str:
    title = _source_download_title(paths.output_dir / "source_download.json")
    if title:
        return title
    if paths.creative_plan.exists():
        try:
            plan = json.loads(paths.creative_plan.read_text(encoding="utf-8"))
            title = str(plan.get("title") or "").strip()
        except (OSError, json.JSONDecodeError):
            title = ""
        if title and title.lower() not in {"source", "download", "reference"}:
            return title
    return ""


def _source_download_title(report_path: Path) -> str:
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    for key in ("source_title", "title", "recommended_publish_title"):
        value = str(report.get(key) or "").strip()
        if value:
            return value
    return ""


def _release_cover_display_title(value: str) -> str:
    text = re.sub(r"https?://\S+", "", str(value or ""))
    text = "".join(ch for ch in text if unicodedata.category(ch)[0] not in {"C", "S"})
    text = re.sub(r"[#@]+", "", text)
    text = re.sub(r"\s+", " ", text).strip(" -_｜|:：!?！？")
    if text.lower() in {"source", "download", "reference"}:
        return ""
    for separator in ("｜", "|", "：", ":", "？", "?", "！", "!", " - ", "—"):
        if separator in text:
            first = text.split(separator, 1)[0].strip()
            if len(first) >= 4:
                text = first
                break
    return _short_text(text, 18)


def _build_card_video_command(
    card_image: Path,
    output_video: Path,
    duration: float,
    geometry: VideoGeometry,
) -> list[str]:
    video_filter = ",".join(
        [
            f"scale={geometry.width}:{geometry.height}:flags=lanczos",
            "setsar=1",
            f"fps={FPS}",
            "format=yuv420p",
        ]
    )
    return [
        "ffmpeg",
        "-y",
        "-loop",
        "1",
        "-t",
        _format_time(duration),
        "-i",
        str(card_image),
        "-f",
        "lavfi",
        "-t",
        _format_time(duration),
        "-i",
        "anullsrc=channel_layout=stereo:sample_rate=44100",
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-vf",
        video_filter,
        "-r",
        str(FPS),
        "-c:v",
        "libx264",
        "-preset",
        "slow",
        "-crf",
        HIGH_QUALITY_CRF,
        "-profile:v",
        "high",
        "-level",
        "4.1",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        HIGH_QUALITY_AUDIO_BITRATE,
        "-movflags",
        "+faststart",
        str(output_video),
    ]


def contact_sheet_timestamps(duration: float, count: int = 12) -> list[float]:
    if count <= 1:
        return [0.0]
    safe_duration = max(0.0, duration)
    end = max(0.0, safe_duration - max(2.0, 3 / FPS))
    if end == 0:
        return [0.0 for _ in range(count)]
    return [0.0] + [round(min(end, safe_duration * index / (count - 1)), 3) for index in range(1, count)]


def _build_contact_sheet_frame_command(video_path: Path, timestamp: float, frame_path: Path) -> list[str]:
    return [
        "ffmpeg",
        "-y",
        "-ss",
        _format_time(timestamp),
        "-i",
        str(video_path),
        "-frames:v",
        "1",
        "-update",
        "1",
        "-vf",
        "scale=480:-1:flags=lanczos",
        str(frame_path),
    ]


def _write_contact_sheet(video_path: Path, contact_sheet_path: Path, duration: float) -> None:
    frame_dir = contact_sheet_path.parent / "contact_sheet_frames"
    frame_dir.mkdir(parents=True, exist_ok=True)
    frame_paths: list[Path] = []
    for index, timestamp in enumerate(contact_sheet_timestamps(duration)):
        frame_path = frame_dir / f"frame_{index:02d}_{_format_time(timestamp).replace('.', '_')}.jpg"
        _run(_build_contact_sheet_frame_command(video_path, timestamp, frame_path))
        frame_paths.append(frame_path)

    thumbs: list[Image.Image] = []
    for frame_path in frame_paths:
        if not frame_path.exists():
            continue
        with Image.open(frame_path) as image:
            thumbs.append(image.convert("RGB"))
    if not thumbs:
        return

    columns = 4
    rows = math.ceil(len(thumbs) / columns)
    cell_width = max(image.width for image in thumbs)
    cell_height = max(image.height for image in thumbs)
    sheet = Image.new("RGB", (cell_width * columns, cell_height * rows), "#000000")
    for index, image in enumerate(thumbs):
        x = (index % columns) * cell_width + (cell_width - image.width) // 2
        y = (index // columns) * cell_height + (cell_height - image.height) // 2
        sheet.paste(image, (x, y))
    sheet.save(contact_sheet_path, quality=92)


def _creative_cover_seek_seconds(plan_path: Path, output_duration: float) -> float:
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0.0

    segments = plan.get("recommended_variant", {}).get("segments", [])
    if not segments:
        return 0.0
    first = segments[0]
    try:
        start = float(first.get("start") or 0.0)
        duration = float(first.get("duration") or output_duration)
    except (TypeError, ValueError):
        return 0.0
    sample_timestamp = first.get("source_sample_timestamp")
    if sample_timestamp is None:
        return 0.0
    try:
        sample_offset = float(sample_timestamp) - start
    except (TypeError, ValueError):
        return 0.0

    output_limit = max(0.0, output_duration - 0.1)
    segment_limit = max(0.0, duration - 0.1)
    return round(min(max(0.0, sample_offset), output_limit, segment_limit), 3)


def build_creative_card(
    title: str,
    subtitle: str,
    geometry: VideoGeometry,
    output_path: Path | str,
) -> None:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (geometry.width, geometry.height), "#111418")
    draw = ImageDraw.Draw(image)

    for y in range(geometry.height):
        ratio = y / max(1, geometry.height - 1)
        red = int(17 + ratio * 10)
        green = int(20 + ratio * 14)
        blue = int(24 + ratio * 18)
        draw.line((0, y, geometry.width, y), fill=(red, green, blue))

    margin = max(36, int(min(geometry.width, geometry.height) * 0.065))
    accent = "#24b47e"
    muted = "#9aa4a8"
    line_y = margin
    draw.line((margin, line_y, geometry.width - margin, line_y), fill="#2c3338", width=max(2, geometry.width // 480))
    draw.line((margin, line_y, margin + int((geometry.width - margin * 2) * 0.28), line_y), fill=accent, width=max(3, geometry.width // 360))

    title_font = _font(max(34, int(geometry.width * 0.052)), bold=True)
    subtitle_font = _font(max(20, int(geometry.width * 0.027)))
    eyebrow_font = _font(max(14, int(geometry.width * 0.018)), bold=True)
    max_text_width = geometry.width - margin * 2
    title_lines = _wrap_text_to_pixel_width(title, title_font, max_text_width)
    title_height = sum(_text_height(line, title_font) for line in title_lines) + max(10, len(title_lines) - 1) * 12
    subtitle_lines = _wrap_text_to_pixel_width(subtitle, subtitle_font, max_text_width)
    subtitle_height = sum(_text_height(line, subtitle_font) for line in subtitle_lines) + max(0, len(subtitle_lines) - 1) * 8
    block_height = title_height + subtitle_height + int(geometry.height * 0.05)
    y = max(margin * 2, int((geometry.height - block_height) * 0.48))

    eyebrow = "原创解读"
    draw.text((margin, y), eyebrow, fill=accent, font=eyebrow_font)
    y += _text_height(eyebrow, eyebrow_font) + max(16, int(geometry.height * 0.018))
    for line in title_lines:
        draw.text((margin, y), line, fill="#f7f3ea", font=title_font)
        y += _text_height(line, title_font) + 12
    y += max(10, int(geometry.height * 0.012))
    for line in subtitle_lines:
        draw.text((margin, y), line, fill=muted, font=subtitle_font)
        y += _text_height(line, subtitle_font) + 8

    footer_font = _font(max(13, int(geometry.width * 0.016)))
    footer = "source quote + original context"
    footer_y = geometry.height - margin - _text_height(footer, footer_font)
    draw.text((margin, footer_y), footer, fill="#6f7a80", font=footer_font)
    image.save(output_path)


def run_quality_checks(source_probe: dict, output_probe: dict, mode: str, paths: ReplicatePaths) -> dict:
    source_geometry = geometry_from_probe(source_probe)
    output_geometry = geometry_from_probe(output_probe)
    source_duration = _duration_from_probe(source_probe)
    output_duration = _duration_from_probe(output_probe)
    issues: list[dict[str, str]] = []

    if output_geometry != source_geometry:
        issues.append(
            {
                "severity": "blocker",
                "code": "geometry_mismatch",
                "message": (
                    f"输出尺寸 {output_geometry.width}x{output_geometry.height} 与源视频 "
                    f"{source_geometry.width}x{source_geometry.height} 不一致。"
                ),
            }
        )

    if output_duration <= 0:
        issues.append(
            {
                "severity": "blocker",
                "code": "empty_output",
                "message": "输出视频时长为 0，不能交付。",
            }
        )

    if mode in ("creative-edit", "human-edit") and source_duration > 0 and output_duration > source_duration + 0.5:
        issues.append(
            {
                "severity": "blocker",
                "code": "duration_inflation",
                "message": (
                    f"输出时长 {output_duration:.3f}s 超过源视频 {source_duration:.3f}s，"
                    "疑似为了凑时长重复内容。"
                ),
            }
        )

    if mode == "creative-edit":
        concat_text = paths.concat.read_text(encoding="utf-8") if paths.concat.exists() else ""
        if "creative_intro" in concat_text or "creative_outro" in concat_text:
            issues.append(
                {
                    "severity": "blocker",
                    "code": "generated_card_in_release",
                    "message": "创作增强成片包含生成片头/片尾卡片，容易变成模板感和工程说明，禁止交付。",
                }
            )
        if not paths.creative_plan.exists():
            issues.append(
                {
                    "severity": "blocker",
                    "code": "missing_creative_plan",
                    "message": "创作增强缺少 creative_plan.json，无法证明剪辑判断和片段选择理由。",
                }
            )
        else:
            issues.extend(_creative_plan_quality_issues(paths.creative_plan))
        if not paths.content_analysis.exists():
            issues.append(
                {
                    "severity": "blocker",
                    "code": "missing_content_analysis",
                    "message": "创作增强缺少 content_analysis.json，无法说明内容理解来源。",
                }
            )
        if not paths.audio_analysis.exists():
            issues.append(
                {
                    "severity": "blocker",
                    "code": "missing_audio_analysis",
                    "message": "创作增强缺少 audio_analysis.json，无法说明声音节奏和讲解依据。",
                }
            )
        if not paths.semantic_timeline.exists():
            issues.append(
                {
                    "severity": "blocker",
                    "code": "missing_semantic_timeline",
                    "message": "创作增强缺少 semantic_timeline.json，无法证明系统理解了原片章节结构。",
                }
            )
        if not paths.transcript_analysis.exists():
            issues.append(
                {
                    "severity": "blocker",
                    "code": "missing_transcript_analysis",
                    "message": "创作增强缺少 transcript_analysis.json，无法说明口播/字幕文本理解来源。",
                }
            )
        if paths.visual_insert_plan.exists():
            issues.extend(_visual_insert_plan_quality_issues(paths.visual_insert_plan, output_duration))
        minimum_longform_duration = _minimum_creative_output_duration(source_duration, paths.creative_plan)
        if minimum_longform_duration and output_duration < minimum_longform_duration:
            issues.append(
                {
                    "severity": "blocker",
                    "code": "creative_output_too_short_for_longform",
                    "message": (
                        f"长源视频创作增强输出只有 {output_duration:.1f}s，低于最低长版覆盖 "
                        f"{minimum_longform_duration:.1f}s，容易退化成摘要片。"
                    ),
                }
            )

    issues.extend(_contact_sheet_visual_artifact_issues(paths.contact_sheet))

    status = "passed" if not issues else "failed"
    return {
        "status": status,
        "mode": mode,
        "checks": {
            "preserve_source_geometry": output_geometry == source_geometry,
            "non_empty_output": output_duration > 0,
            "no_duration_inflation": not any(issue["code"] == "duration_inflation" for issue in issues),
            "no_generated_cards_in_release": not any(
                issue["code"] == "generated_card_in_release" for issue in issues
            ),
            "creative_plan_exists": mode != "creative-edit" or paths.creative_plan.exists(),
            "creative_plan_non_overlapping": not any(
                issue["code"] == "creative_plan_overlap" for issue in issues
            ),
            "creative_plan_has_profile": not any(
                issue["code"] == "creative_plan_missing_profile" for issue in issues
            ),
            "creative_plan_has_semantic_roles": not any(
                issue["code"] == "creative_plan_missing_semantic_role" for issue in issues
            ),
            "creative_plan_has_role_variety": not any(
                issue["code"]
                in {
                    "creative_plan_insufficient_role_variety",
                    "creative_plan_low_intent_roles",
                    "creative_plan_dominant_semantic_role",
                }
                for issue in issues
            ),
            "creative_plan_has_role_spacing": not any(
                issue["code"] == "creative_plan_tight_same_role_repetition" for issue in issues
            ),
            "creative_plan_has_release_chronology": not any(
                issue["code"] == "creative_plan_nonchronological_after_hook" for issue in issues
            ),
            "creative_plan_hook_starts_near_sample": not any(
                issue["code"] == "creative_plan_hook_preroll_too_long" for issue in issues
            ),
            "content_analysis_exists": mode != "creative-edit" or paths.content_analysis.exists(),
            "audio_analysis_exists": mode != "creative-edit" or paths.audio_analysis.exists(),
            "semantic_timeline_exists": mode != "creative-edit" or paths.semantic_timeline.exists(),
            "transcript_analysis_exists": mode != "creative-edit" or paths.transcript_analysis.exists(),
            "creative_plan_has_content_provider": not any(
                issue["code"] == "creative_plan_missing_content_provider" for issue in issues
            ),
            "creative_plan_has_content_evidence": not any(
                issue["code"] == "creative_plan_weak_content_evidence" for issue in issues
            ),
            "creative_plan_has_audio_provider": not any(
                issue["code"] == "creative_plan_missing_audio_provider" for issue in issues
            ),
            "creative_plan_has_audio_evidence": not any(
                issue["code"] == "creative_plan_weak_audio_evidence" for issue in issues
            ),
            "creative_plan_has_semantic_provider": not any(
                issue["code"] == "creative_plan_missing_semantic_provider" for issue in issues
            ),
            "creative_plan_has_semantic_chapters": not any(
                issue["code"] == "creative_plan_weak_semantic_chapters" for issue in issues
            ),
            "creative_plan_has_director_strategy": not any(
                issue["code"] == "creative_plan_missing_strategy" for issue in issues
            ),
            "creative_plan_has_director_moves": not any(
                issue["code"] == "creative_plan_weak_director_moves" for issue in issues
            ),
            "creative_plan_source_only_release": not any(
                issue["code"]
                in {
                    "creative_plan_contains_synthetic_segment",
                    "creative_plan_template_like_hook",
                }
                for issue in issues
            ),
            "creative_plan_template_like_budget": not any(
                issue["code"]
                in {
                    "creative_plan_template_like_overuse",
                    "creative_plan_template_like_finish_run",
                }
                for issue in issues
            ),
            "longform_duration_floor": not any(
                issue["code"] == "creative_output_too_short_for_longform" for issue in issues
            ),
            "ai_visual_insert_budget": not any(
                issue["code"] == "ai_visual_insert_budget_exceeded" for issue in issues
            ),
            "ai_visual_insert_non_consecutive": not any(
                issue["code"] == "ai_visual_insert_consecutive" for issue in issues
            ),
            "ai_visual_insert_relevance": not any(
                issue["code"] == "ai_visual_insert_missing_source_evidence" for issue in issues
            ),
            "caption_timeline_present": paths.caption_timeline.exists(),
            "subtitles_present": paths.subtitles.exists(),
            "visual_artifact_free": not any(
                issue["code"] == "visual_line_art_artifact" for issue in issues
            ),
        },
        "source_geometry": asdict(source_geometry),
        "output_geometry": asdict(output_geometry),
        "issues": issues,
    }


def _contact_sheet_visual_artifact_issues(contact_sheet_path: Path) -> list[dict[str, str]]:
    if not contact_sheet_path.exists():
        return []
    try:
        with Image.open(contact_sheet_path) as raw_image:
            image = ImageOps.contain(raw_image.convert("RGB"), (640, 640), method=Image.Resampling.BILINEAR)
            pixels = list(image.getdata())
    except OSError as exc:
        return [
            {
                "severity": "blocker",
                "code": "invalid_contact_sheet",
                "message": f"质检图无法读取：{exc}",
            }
        ]
    if not pixels:
        return []

    count = len(pixels)
    luma_sum = 0.0
    luma_square_sum = 0.0
    chroma_sum = 0.0
    near_white_count = 0
    dark_count = 0
    for red, green, blue in pixels:
        luma = 0.299 * red + 0.587 * green + 0.114 * blue
        luma_sum += luma
        luma_square_sum += luma * luma
        chroma_sum += abs(red - green) + abs(((red + green) * 0.5) - blue)
        if luma >= 238 and max(red, green, blue) - min(red, green, blue) <= 48:
            near_white_count += 1
        if luma <= 70:
            dark_count += 1

    mean = luma_sum / count / 255.0
    variance = max(0.0, (luma_square_sum / count) - (luma_sum / count) ** 2)
    contrast = math.sqrt(variance) / 255.0
    colorfulness = chroma_sum / count / 255.0
    near_white_ratio = near_white_count / count
    dark_ratio = dark_count / count

    if (
        mean >= 0.90
        and contrast <= 0.16
        and colorfulness <= 0.10
        and near_white_ratio >= 0.62
        and dark_ratio <= 0.08
    ):
        return [
            {
                "severity": "blocker",
                "code": "visual_line_art_artifact",
                "message": (
                    "质检图呈现过曝线稿/滤镜化特征，画面不像真实视频，"
                    "禁止作为发布成片。"
                ),
            }
        ]
    return []


def _visual_insert_plan_quality_issues(plan_path: Path, output_duration: float) -> list[dict[str, str]]:
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [
            {
                "severity": "blocker",
                "code": "invalid_visual_insert_plan",
                "message": f"visual_insert_plan.json 无法读取或解析：{exc}",
            }
        ]
    if plan.get("strategy") != "images2_contextual_inserts":
        return []
    inserts = plan.get("inserts", []) if isinstance(plan.get("inserts"), list) else []
    issues: list[dict[str, str]] = []
    max_ratio = float(plan.get("max_ai_insert_ratio") or 0.08)
    total_duration = sum(float(insert.get("duration") or 0.0) for insert in inserts if isinstance(insert, dict))
    release_duration = float(plan.get("release_duration_seconds") or output_duration or 0.0)
    if release_duration > 0 and total_duration > release_duration * max_ratio + 0.001:
        issues.append(
            {
                "severity": "blocker",
                "code": "ai_visual_insert_budget_exceeded",
                "message": (
                    f"AI 补充镜头总时长 {total_duration:.1f}s 超过预算 "
                    f"{release_duration * max_ratio:.1f}s，容易变成 AI 图片片。"
                ),
            }
        )
    previous_after_index: int | None = None
    for index, insert in enumerate(inserts):
        if not isinstance(insert, dict):
            continue
        evidence = [str(item).strip() for item in insert.get("source_evidence", []) if str(item).strip()]
        if not evidence:
            issues.append(
                {
                    "severity": "blocker",
                    "code": "ai_visual_insert_missing_source_evidence",
                    "message": f"AI 补充镜头 {index + 1} 没有绑定原片章节、声音或内容证据。",
                }
            )
        after_index = int(insert.get("after_index") or 0)
        if previous_after_index is not None and after_index == previous_after_index:
            issues.append(
                {
                    "severity": "blocker",
                    "code": "ai_visual_insert_consecutive",
                    "message": "同一剪辑位置连续插入多个 AI 补充镜头，容易变成图片堆叠。",
                }
            )
            break
        previous_after_index = after_index
    return issues


def _write_quality_report(result: dict, output_path: Path) -> None:
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_edl(segments: Sequence[HumanEditSegment], source: Path, output_path: Path) -> None:
    lines = [
        "# Video Factory Human Edit EDL",
        "",
        f"Source: `{source}`",
        f"Target duration: `{_format_duration(human_edit_duration(segments))}`",
        "",
        "| # | Source In | Duration | Lens | Edit Intent |",
        "|---|---:|---:|---:|---|",
    ]
    for index, segment in enumerate(segments, start=1):
        lines.append(
            f"| {index} | {_format_duration(segment.start)} | {_format_duration(segment.duration)} | "
            f"{segment.zoom:.2f}x | {segment.purpose} |"
        )
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_creative_brief(
    source: Path,
    title: str,
    segments: Sequence[HumanEditSegment],
    geometry: VideoGeometry,
    output_path: Path,
    plan: CreativePlan | None = None,
    original_inserts: Sequence[dict[str, object]] = (),
) -> None:
    lines = [
        "# Creative Edit Brief",
        "",
        f"Source: `{source}`",
        f"Auto title draft: `{title}`",
        f"Canvas: `{geometry.width}x{geometry.height}`",
        f"Generated video inserts: `{len(original_inserts)}`",
        "",
        "Creative rule:",
        "- 源视频只作为引用片段，强创作会插入原创观点、背景解释和证据说明。",
        "- 禁止无意义工程说明卡、模板片头或模板片尾。",
        "- 音频策略为后续重配时，源片原声会静音，等待原创配音或真人录音。",
    ]
    if original_inserts:
        lines.extend(["", "Original inserts:"])
        for insert in original_inserts:
            lines.append(
                f"- after #{int(insert.get('after_index', 0)) + 1:02d}: "
                f"{insert.get('title', '')} / {_format_duration(float(insert.get('duration') or 0.0))} / "
                f"{insert.get('subtitle', '')}"
            )
    if plan is not None and plan.semantic_chapters:
        lines.extend(["", "Semantic chapters:"])
        for chapter in plan.semantic_chapters:
            evidence = " / ".join(chapter.evidence[:2]) if chapter.evidence else chapter.topic
            lines.append(
                f"- {chapter.index + 1:02d}. {chapter.title} "
                f"({_format_duration(chapter.start)}-{_format_duration(chapter.end)}): {evidence}"
            )
    lines.extend(["", "Cut summary:"])
    for index, segment in enumerate(segments, start=1):
        chapter = ""
        if plan is not None and index <= len(plan.recommended_variant.segments):
            creative_segment = plan.recommended_variant.segments[index - 1]
            if creative_segment.chapter_title:
                chapter = f" / {creative_segment.chapter_title}"
        lines.append(
            f"- {index:02d}. {_format_duration(segment.start)} / {_format_duration(segment.duration)}"
            f"{chapter} / {segment.purpose}"
        )
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_report(
    source: Path,
    source_probe: dict,
    output_probe: dict,
    mode: str,
    paths: ReplicatePaths,
    geometry: VideoGeometry,
    production_options: dict | None = None,
) -> None:
    is_creative = mode == "creative-edit"
    has_original_inserts = is_creative and _release_has_original_inserts(paths.concat)
    report = {
        "project": "video-factory-replicate",
        "mode": mode,
        "source": str(source),
        "artifacts": {key: str(value) for key, value in asdict(paths).items()},
        "output_geometry_rule": {
            "strategy": "preserve_source_dimensions",
            "width": geometry.width,
            "height": geometry.height,
            "fps": FPS,
        },
        "quality_standard": str(PROJECT_ROOT / "VIDEO_QUALITY_STANDARD.md"),
        "workflow": str(PROJECT_ROOT / "VIDEO_FACTORY_WORKFLOW.md"),
        "editing_rules": {
            "visible_overlays": False,
            "progress_bars": False,
            "scene_cards": False,
            "synthetic_voiceover": False,
            "source_only": not has_original_inserts,
            "source_quote_with_original_inserts": has_original_inserts,
            "generated_intro_outro": False,
            "creative_decision_artifacts": is_creative,
            "audio_policy": _audio_policy(production_options),
        },
        "source_probe": source_probe,
        "output_probe": output_probe,
    }
    paths.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def _release_has_original_inserts(concat_path: Path) -> bool:
    try:
        text = concat_path.read_text(encoding="utf-8")
    except OSError:
        return False
    return "original_explainer" in text


def _paths_for_explicit_output_dir(output_dir: Path) -> ReplicatePaths:
    return ReplicatePaths(
        output_dir=output_dir,
        segments_dir=output_dir / "segments",
        video=output_dir / "release.mp4",
        release_no_subtitles=output_dir / "release_no_subtitles.mp4",
        cover=output_dir / "cover.png",
        contact_sheet=output_dir / "contact_sheet.jpg",
        edl=output_dir / "edit_decision_list.md",
        concat=output_dir / "segments.txt",
        report=output_dir / "render_report.json",
        quality_report=output_dir / "quality_report.json",
        creative_brief=output_dir / "creative_brief.md",
        creative_plan=output_dir / "creative_plan.json",
        candidate_edl=output_dir / "candidate_edl.md",
        cover_candidates=output_dir / "cover_candidates.jpg",
        content_analysis=output_dir / "content_analysis.json",
        audio_analysis=output_dir / "audio_analysis.json",
        semantic_timeline=output_dir / "semantic_timeline.json",
        transcript_analysis=output_dir / "transcript_analysis.json",
        visual_insert_plan=output_dir / "visual_insert_plan.json",
        images2_prompt_pack=output_dir / "images2_prompt_pack.json",
        generated_visual_manifest=output_dir / "generated_visual_manifest.json",
        cover_brief=output_dir / "cover_brief.json",
        caption_timeline=output_dir / "caption_timeline.json",
        subtitles=output_dir / "subtitles.srt",
    )


def creative_title_from_source(source: Path | str) -> str:
    stem = Path(source).stem.replace("_", " ").replace("-", " ").strip()
    stem = re.sub(r"^\d+\s+[a-z0-9]{4,}\s+", "", stem, flags=re.IGNORECASE)
    stem = re.sub(r"视频链接", "", stem)
    stem = re.sub(r"\b[\w.-]+\.(?:com|cn|net|org)\b", "", stem, flags=re.IGNORECASE)
    stem = stem.split("#", 1)[0]
    stem = re.sub(r"\s+", " ", stem).strip(" -_")
    return stem[:42] or "参考视频剪辑"


def _creative_title_for_render(source: Path | str, production_options: dict | None = None) -> str:
    title = str((production_options or {}).get("source_title", "")).strip()
    if title:
        return title[:160]
    return creative_title_from_source(source)


def _target_human_edit_duration(source_duration: float) -> float:
    if source_duration < 60:
        return max(6.0, source_duration * 0.72)
    if source_duration >= 360:
        return min(480.0, max(360.0, source_duration * 0.78))
    return min(source_duration, max(30.0, source_duration * 0.9))


def _duration_from_probe(probe: dict) -> float:
    return float(probe.get("format", {}).get("duration") or 0)


def _minimum_creative_longform_duration(source_duration: float) -> float:
    if source_duration < 180:
        return 0.0
    if source_duration < 300:
        return min(source_duration, max(120.0, source_duration * 0.62))
    return min(source_duration, max(300.0, source_duration * 0.8))


def _minimum_creative_output_duration(source_duration: float, plan_path: Path) -> float:
    baseline = _minimum_creative_longform_duration(source_duration)
    if not baseline:
        return 0.0
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8")) if plan_path.exists() else {}
    except (OSError, json.JSONDecodeError):
        return baseline
    strategy = plan.get("creative_strategy", {}) if isinstance(plan.get("creative_strategy", {}), dict) else {}
    try:
        target_duration = float(strategy.get("target_duration", 0.0))
    except (TypeError, ValueError):
        target_duration = 0.0
    if target_duration > 0 and target_duration < baseline:
        return max(30.0, target_duration * 0.85)
    if target_duration > 0:
        return max(baseline, target_duration * 0.86)
    return baseline


def _creative_plan_quality_issues(plan_path: Path) -> list[dict[str, str]]:
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [
            {
                "severity": "blocker",
                "code": "invalid_creative_plan",
                "message": f"creative_plan.json 无法读取或解析：{exc}",
            }
        ]

    segments = plan.get("recommended_variant", {}).get("segments", [])
    issues: list[dict[str, str]] = []
    profile = plan.get("profile", {})
    if not str(profile.get("name", "")).strip():
        issues.append(
            {
                "severity": "blocker",
                "code": "creative_plan_missing_profile",
                "message": "creative_plan.json 缺少 profile.name，无法判断视频类型和剪辑策略。",
            }
        )
    content_provider = plan.get("content_provider", {})
    if not str(content_provider.get("name", "")).strip() or not str(content_provider.get("status", "")).strip():
        issues.append(
            {
                "severity": "blocker",
                "code": "creative_plan_missing_content_provider",
                "message": "creative_plan.json 缺少 content_provider，无法判断内容理解来源。",
            }
        )
    audio_provider = plan.get("audio_provider", {})
    if not str(audio_provider.get("name", "")).strip() or not str(audio_provider.get("status", "")).strip():
        issues.append(
            {
                "severity": "blocker",
                "code": "creative_plan_missing_audio_provider",
                "message": "creative_plan.json 缺少 audio_provider，无法判断声音分析来源。",
            }
        )
    semantic_provider = plan.get("semantic_provider", {})
    if not str(semantic_provider.get("name", "")).strip() or not str(semantic_provider.get("status", "")).strip():
        issues.append(
            {
                "severity": "blocker",
                "code": "creative_plan_missing_semantic_provider",
                "message": "creative_plan.json 缺少 semantic_provider，无法判断章节理解来源。",
            }
        )
    if not segments:
        issues.append(
            {
                "severity": "blocker",
                "code": "empty_creative_plan",
                "message": "creative_plan.json 没有推荐片段，无法驱动创作剪辑。",
            }
        )
        return issues

    for index, segment in enumerate(segments):
        if not str(segment.get("purpose", "")).strip():
            issues.append(
                {
                    "severity": "blocker",
                    "code": "creative_plan_missing_reason",
                    "message": f"创作片段 {index + 1} 缺少选择理由。",
                }
            )
        if not str(segment.get("semantic_role", "")).strip():
            issues.append(
                {
                    "severity": "blocker",
                    "code": "creative_plan_missing_semantic_role",
                    "message": f"创作片段 {index + 1} 缺少 semantic_role，仍停留在泛化抽样。",
                }
            )
        source_type = str(segment.get("source_type", "source_video")).strip() or "source_video"
        if bool(segment.get("synthetic", False)) or source_type != "source_video":
            issues.append(
                {
                    "severity": "blocker",
                    "code": "creative_plan_contains_synthetic_segment",
                    "message": (
                        f"创作片段 {index + 1} 标记为 `{source_type}`，"
                        "发布片必须只由真实源视频片段组成，不能混入合成卡片。"
                    ),
                }
            )
        visual_risk_tags = {str(tag) for tag in (segment.get("visual_risk_tags") or [])}
        if index == 0 and "template_like_source_frame" in visual_risk_tags:
            issues.append(
                {
                    "severity": "blocker",
                    "code": "creative_plan_template_like_hook",
                    "message": "创作片段 1 是模板感源帧，不能作为发布片开场钩子。",
                }
            )
        start = float(segment.get("start", 0.0))
        duration = float(segment.get("duration", 0.0))
        sample_timestamp = segment.get("source_sample_timestamp")
        if index == 0 and sample_timestamp is not None:
            try:
                sample_timestamp_float = float(sample_timestamp)
            except (TypeError, ValueError):
                sample_timestamp_float = start
            preroll = sample_timestamp_float - start
            allowed_preroll = max(0.05, duration * 0.005)
            if preroll > allowed_preroll:
                issues.append(
                    {
                        "severity": "blocker",
                        "code": "creative_plan_hook_preroll_too_long",
                        "message": (
                            f"创作片段 1 从样本点前 {preroll:.1f}s 起剪，"
                            "容易把标题卡或低信息前摇带入片头。"
                        ),
                    }
                )
        if duration <= 0:
            issues.append(
                {
                    "severity": "blocker",
                    "code": "creative_plan_invalid_duration",
                    "message": f"创作片段 {index + 1} 时长无效。",
                }
            )
    template_like_indices = [
        index
        for index, segment in enumerate(segments)
        if "template_like_source_frame" in {str(tag) for tag in (segment.get("visual_risk_tags") or [])}
    ]
    if template_like_indices:
        allowed_template_like = max(1, math.floor(len(segments) * 0.18))
        if len(template_like_indices) > allowed_template_like:
            issues.append(
                {
                    "severity": "blocker",
                    "code": "creative_plan_template_like_overuse",
                    "message": (
                        f"推荐片段里有 {len(template_like_indices)}/{len(segments)} 段是模板感源帧，"
                        f"超过预算 {allowed_template_like} 段；长版创作不能靠标题卡或低信息页堆时长。"
                    ),
                }
            )
        finish_run = 0
        for segment in segments:
            visual_risk_tags = {str(tag) for tag in (segment.get("visual_risk_tags") or [])}
            role = str(segment.get("semantic_role", "")).strip()
            move = str(segment.get("creative_move", "")).strip()
            is_template_like_finish = (
                "template_like_source_frame" in visual_risk_tags
                and (role == "result_validation" or move == "proof_close")
            )
            if is_template_like_finish:
                finish_run += 1
                if finish_run >= 2:
                    issues.append(
                        {
                            "severity": "blocker",
                            "code": "creative_plan_template_like_finish_run",
                            "message": "尾段连续使用模板感源帧，结尾会像片尾卡堆叠而不是真实结果验证。",
                        }
                    )
                    break
            else:
                finish_run = 0

    for left_index, left in enumerate(segments):
        left_start = float(left.get("start", 0.0))
        left_end = left_start + float(left.get("duration", 0.0))
        for right_index, right in enumerate(segments[left_index + 1 :], start=left_index + 2):
            right_start = float(right.get("start", 0.0))
            right_end = right_start + float(right.get("duration", 0.0))
            if ranges_overlap(left_start, left_end, right_start, right_end):
                issues.append(
                    {
                        "severity": "blocker",
                        "code": "creative_plan_overlap",
                        "message": f"创作片段 {left_index + 1} 与 {right_index} 源区间重叠，疑似重复使用素材。",
                    }
                )
    semantic_roles = [str(segment.get("semantic_role", "")).strip() for segment in segments]
    semantic_roles = [role for role in semantic_roles if role]
    low_intent_roles = {"context", "context_bridge"}
    if len(segments) >= 3 and len(set(semantic_roles)) < 3:
        issues.append(
            {
                "severity": "blocker",
                "code": "creative_plan_insufficient_role_variety",
                "message": "推荐片段的语义角色少于 3 类，创作判断不足。",
            }
        )
    if semantic_roles and sum(1 for role in semantic_roles if role in low_intent_roles) > len(semantic_roles) / 2:
        issues.append(
            {
                "severity": "blocker",
                "code": "creative_plan_low_intent_roles",
                "message": "超过一半推荐片段是低意图上下文角色，容易退化成随机抽样。",
            }
        )
    if len(semantic_roles) >= 5:
        dominant_role = max(set(semantic_roles), key=semantic_roles.count)
        dominant_count = semantic_roles.count(dominant_role)
        if dominant_count / len(semantic_roles) > 0.62:
            issues.append(
                {
                    "severity": "blocker",
                    "code": "creative_plan_dominant_semantic_role",
                    "message": (
                        f"`{dominant_role}` 占 {dominant_count}/{len(semantic_roles)}，"
                        "剪辑结构过于单一，不能交付。"
                    ),
                }
            )
    source_duration = float(plan.get("source_duration") or 0.0)
    strategy = plan.get("creative_strategy", {})
    if source_duration >= 300:
        if not str(strategy.get("version", "")).strip() or not str(strategy.get("treatment", "")).strip():
            issues.append(
                {
                    "severity": "blocker",
                    "code": "creative_plan_missing_strategy",
                    "message": "长版创作缺少 creative_strategy，无法证明这是导演策略而不是机械拉长。",
                }
            )
        if semantic_roles:
            current_role = semantic_roles[0]
            current_count = 1
            for role in semantic_roles[1:]:
                if role == current_role:
                    current_count += 1
                    if current_count > 2:
                        issues.append(
                            {
                                "severity": "blocker",
                                "code": "creative_plan_repetitive_chapter_run",
                                "message": (
                                    f"`{role}` 连续出现 {current_count} 段，"
                                    "长版视频疑似靠同类画面堆时长。"
                                ),
                            }
                        )
                        break
                else:
                    current_role = role
                    current_count = 1
        body_starts = [float(segment.get("start", 0.0)) for segment in segments[1:]]
        for left, right in zip(body_starts, body_starts[1:]):
            if right + 0.25 < left:
                issues.append(
                    {
                        "severity": "blocker",
                        "code": "creative_plan_nonchronological_after_hook",
                        "message": (
                            f"长版创作在结果钩子后出现时间线倒退：{left:.1f}s -> {right:.1f}s。"
                            "正文必须沿源视频推进，避免观众感到随机跳剪。"
                        ),
                    }
                )
                break
    if source_duration >= 180 and len(segments) >= 5:
        repeated_roles = {"operation_step", "prep_action", "action_moment"}
        min_gap = min(60.0, max(45.0, source_duration * 0.08))
        for role in repeated_roles:
            starts = sorted(float(segment.get("start", 0.0)) for segment in segments if segment.get("semantic_role") == role)
            for left, right in zip(starts, starts[1:]):
                if right - left < min_gap:
                    issues.append(
                        {
                            "severity": "blocker",
                            "code": "creative_plan_tight_same_role_repetition",
                            "message": (
                                f"`{role}` 片段间隔 {right - left:.1f}s，低于 {min_gap:.1f}s，"
                                "长视频里容易看成重复素材。"
                            ),
                        }
                    )
                    return issues
    content_supported = 0
    for segment in segments:
        tags = segment.get("content_tags") or []
        evidence = str(segment.get("content_evidence", "")).strip()
        if tags or evidence:
            content_supported += 1
    if segments and content_supported < math.ceil(len(segments) / 2):
        issues.append(
            {
                "severity": "blocker",
                "code": "creative_plan_weak_content_evidence",
                "message": (
                    f"只有 {content_supported}/{len(segments)} 个推荐片段带内容证据，"
                    "创作判断仍然过度依赖视觉猜测。"
                ),
            }
        )
    audio_supported = 0
    for segment in segments:
        tags = segment.get("audio_tags") or []
        evidence = str(segment.get("audio_evidence", "")).strip()
        if tags or evidence:
            audio_supported += 1
    if segments and audio_supported < math.ceil(len(segments) / 2):
        issues.append(
            {
                "severity": "blocker",
                "code": "creative_plan_weak_audio_evidence",
                "message": (
                    f"只有 {audio_supported}/{len(segments)} 个推荐片段带音频证据，"
                    "创作判断缺少讲解节奏依据。"
                ),
            }
        )
    semantic_supported = 0
    for segment in segments:
        if (
            str(segment.get("chapter_title", "")).strip()
            or str(segment.get("semantic_topic", "")).strip()
            or str(segment.get("transcript_evidence", "")).strip()
        ):
            semantic_supported += 1
    if segments and semantic_supported < math.ceil(len(segments) / 2):
        issues.append(
            {
                "severity": "blocker",
                "code": "creative_plan_weak_semantic_chapters",
                "message": (
                    f"只有 {semantic_supported}/{len(segments)} 个推荐片段带章节映射，"
                    "创作剪辑没有真正使用语义时间线。"
                ),
            }
        )
    if source_duration >= 300:
        move_supported = 0
        used_moves: set[str] = set()
        for segment in segments:
            move = str(segment.get("creative_move", "")).strip()
            if move:
                move_supported += 1
                used_moves.add(move)
        if segments and move_supported < math.ceil(len(segments) * 0.8):
            issues.append(
                {
                    "severity": "blocker",
                    "code": "creative_plan_weak_director_moves",
                    "message": (
                        f"只有 {move_supported}/{len(segments)} 个推荐片段带 creative_move，"
                        "长版创作缺少导演动作说明。"
                    ),
                }
            )
        expected_moves = {str(move).strip() for move in strategy.get("creative_moves", []) if str(move).strip()}
        missing_moves = sorted(expected_moves - used_moves)
        if expected_moves and missing_moves:
            issues.append(
                {
                    "severity": "blocker",
                    "code": "creative_plan_missing_director_move_coverage",
                    "message": (
                        "长版创作缺少导演动作覆盖："
                        + ", ".join(missing_moves)
                        + "。这通常意味着视频是在重复同类段落。"
                    ),
                }
            )
    return issues


def _even_dimension(value: int) -> int:
    value = max(2, value)
    return value if value % 2 == 0 else value - 1


def _scaled_crop_offset(requested: int, target_dimension: int, base_dimension: int) -> int:
    return int(round(requested * target_dimension / base_dimension))


def _bounded_crop_offset(requested: int, scaled: int, target: int) -> int:
    return max(0, min(requested, scaled - target))


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    return load_font(size, bold)


def _wrap_text_to_pixel_width(text: str, font: ImageFont.FreeTypeFont | ImageFont.ImageFont, max_width: int) -> list[str]:
    lines: list[str] = []
    current = ""
    for char in text:
        if char == "\n":
            if current:
                lines.append(current)
                current = ""
            continue
        candidate = current + char
        if current and font.getlength(candidate) > max_width:
            lines.append(current)
            current = char
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines or [text]


def _text_height(text: str, font: ImageFont.FreeTypeFont | ImageFont.ImageFont) -> int:
    bbox = font.getbbox(text)
    return bbox[3] - bbox[1]


def _format_time(seconds: float) -> str:
    if seconds == int(seconds):
        return str(int(seconds))
    return f"{seconds:.3f}".rstrip("0").rstrip(".")


def _format_duration(seconds: float) -> str:
    whole = int(round(seconds))
    minutes, remainder = divmod(whole, 60)
    return f"{minutes:02d}:{remainder:02d}"


def _slugify(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
    if not ascii_value.strip():
        ascii_value = "-".join(_pinyin_like_parts(value))
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", ascii_value).strip("-").lower()
    return slug or "video"


def _pinyin_like_parts(value: str) -> list[str]:
    fallback = {
        "我": "wo",
        "的": "de",
        "参": "can",
        "考": "kao",
        "视": "shi",
        "频": "pin",
    }
    return [fallback.get(char, "") for char in value if fallback.get(char, "")]


def _progress(progress: ProgressCallback | None, message: str) -> None:
    if progress is not None:
        progress(message)


def _run(command: list[str]) -> None:
    subprocess.run(command, check=True)


if __name__ == "__main__":
    main()
