from __future__ import annotations

import json
import math
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict

from PIL import Image, ImageDraw, ImageFont, ImageOps

from video_factory.fonts import load_font

from video_factory.replicate import (
    DEFAULT_OUTPUT_ROOT,
    FPS,
    VideoGeometry,
    _build_cover_command,
    _concat_segments,
    _duration_from_probe,
    _format_time,
    _run,
    _write_contact_sheet,
    geometry_from_probe,
    probe_media,
)


ProgressCallback = Callable[[str], None]
DEFAULT_ORIGINAL_GEOMETRY = VideoGeometry(width=1920, height=1080)
ASSET_MEDIA_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".mp4", ".mov", ".m4v", ".webm"}
ASSET_LICENSE_FILENAMES = {"asset_licenses.json", "licenses.json", "license.json"}


@dataclass(frozen=True)
class OriginalPaths:
    output_dir: Path
    segments_dir: Path
    frames_dir: Path
    audio_dir: Path
    video: Path
    cover: Path
    contact_sheet: Path
    report: Path
    quality_report: Path
    script: Path
    storyboard: Path
    original_strategy: Path
    motion_plan: Path
    caption_timeline: Path
    subtitles: Path
    voiceover_manifest: Path
    asset_pass_report: Path
    asset_usage_plan: Path
    shotlist: Path
    asset_manifest: Path
    originality_report: Path
    concat: Path


def build_original_paths(output_dir: Path | str | None = None, job_id: str | None = None) -> OriginalPaths:
    if output_dir is None:
        suffix = f"-{job_id}" if job_id else ""
        output_dir = DEFAULT_OUTPUT_ROOT / f"original-generation{suffix}"
    root = Path(output_dir)
    return OriginalPaths(
        output_dir=root,
        segments_dir=root / "segments",
        frames_dir=root / "scene_frames",
        audio_dir=root / "voiceover",
        video=root / "release.mp4",
        cover=root / "cover.png",
        contact_sheet=root / "contact_sheet.jpg",
        report=root / "render_report.json",
        quality_report=root / "quality_report.json",
        script=root / "script.md",
        storyboard=root / "storyboard.json",
        original_strategy=root / "original_strategy.json",
        motion_plan=root / "motion_plan.json",
        caption_timeline=root / "caption_timeline.json",
        subtitles=root / "subtitles.srt",
        voiceover_manifest=root / "voiceover_manifest.json",
        asset_pass_report=root / "asset_pass_report.json",
        asset_usage_plan=root / "asset_usage_plan.json",
        shotlist=root / "shotlist.md",
        asset_manifest=root / "asset_manifest.json",
        originality_report=root / "originality_report.json",
        concat=root / "segments.txt",
    )


def build_original_strategy(options: dict | None = None) -> dict:
    options = dict(options or {})
    topic = _clean_text(str(options.get("original_topic") or options.get("production_notes") or "原创视频选题"))
    brief = _clean_text(str(options.get("original_brief") or options.get("production_notes") or ""))
    creative_strength = str(options.get("creative_strength", "balanced"))
    strictness = str(options.get("quality_strictness", "strict"))
    duration_policy = str(options.get("target_duration_policy", "source_guided"))
    production_notes = _clean_text(str(options.get("production_notes", "")))[:500]

    target_duration = {
        "light": 120,
        "balanced": 180,
        "strong": 240,
    }.get(creative_strength, 180)
    if duration_policy == "short_summary":
        target_duration = min(target_duration, 90)
    elif duration_policy == "retain_core":
        target_duration = max(150, min(target_duration, 180))
    if strictness == "audit":
        target_duration = max(target_duration, 180)

    chapters = _chapters_for_topic(topic, brief, creative_strength)
    return {
        "version": "original_generation_v1",
        "format": "original_tutorial",
        "topic": topic,
        "brief": brief,
        "target_duration": target_duration,
        "creative_strength": creative_strength,
        "quality_strictness": strictness,
        "production_notes": production_notes,
        "chapters": chapters,
        "publish_rules": [
            "不直接复用参考视频画面或原声。",
            "脚本、结构、画面说明和验收报告必须随成片一起输出。",
            "上线前保留相似度、音频复用率、文本重合率和风险等级。",
        ],
        "upgrade_path": [
            "接入自录屏或自有素材库替换动效样片。",
            "接入原创配音后再开启音频上线闸门。",
            "接入素材授权记录，批量任务逐条存档。",
        ],
    }


def build_storyboard(strategy: dict) -> dict:
    chapters = [str(item) for item in strategy.get("chapters", []) if str(item).strip()]
    if not chapters:
        chapters = _chapters_for_topic(str(strategy.get("topic", "原创视频选题")), "", "balanced")
    target_duration = float(strategy.get("target_duration") or 180)
    scene_duration = max(10.0, target_duration / len(chapters))
    scenes = []
    cursor = 0.0
    for index, chapter in enumerate(chapters):
        start = round(cursor, 2)
        end = round(cursor + scene_duration, 2)
        scenes.append(
            {
                "index": index,
                "title": chapter,
                "duration": round(scene_duration, 2),
                "start": start,
                "end": end,
                "visual_goal": _visual_goal(index, chapter, strategy),
                "motion": _motion_for_scene(index),
                "on_screen_text": _screen_text(index, chapter, strategy),
                "voiceover": _voiceover_for_scene(index, chapter, strategy),
                "source_policy": "original_assets_only",
            }
        )
        cursor += scene_duration
    return {
        "topic": str(strategy.get("topic", "")),
        "target_duration": round(scene_duration * len(scenes), 2),
        "scenes": scenes,
        "asset_requirements": [
            "原创录屏或自有产品界面素材",
            "可授权 B-roll 或内部实拍素材",
            "原创配音音轨",
            "上线前相似度与版权留档",
        ],
    }


def build_motion_plan(storyboard: dict) -> dict:
    scenes = storyboard.get("scenes", []) if isinstance(storyboard.get("scenes"), list) else []
    patterns = [
        "hook_matrix",
        "risk_tracks",
        "production_map",
        "script_lab",
        "asset_wall",
        "audit_dashboard",
        "batch_queue",
        "action_close",
    ]
    camera_moves = [
        "slow_push_with_left_reveal",
        "side_track_with_highlight_sweeps",
        "node_to_node_pan",
        "split_screen_transform",
        "wall_scan_and_status_pop",
        "metric_pulse_and_threshold_lock",
        "vertical_queue_scroll",
        "checklist_assemble",
    ]
    shots = []
    for scene in scenes:
        index = int(scene.get("index", len(shots)))
        pattern = patterns[index % len(patterns)]
        duration = max(2.0, float(scene.get("duration") or 10.0))
        shots.append(
            {
                "scene_index": index,
                "title": str(scene.get("title", "")),
                "duration": round(duration, 2),
                "shot_pattern": pattern,
                "camera_move": camera_moves[index % len(camera_moves)],
                "motion_density": round(0.64 + (index % 4) * 0.08, 2),
                "keyframes": [
                    {
                        "at": 0.0,
                        "focus": "open_context",
                        "scale": 1.0,
                        "x": 0,
                        "y": 0,
                    },
                    {
                        "at": 0.46,
                        "focus": "evidence_or_process",
                        "scale": 1.025,
                        "x": 18 - index * 3,
                        "y": -10 + index * 2,
                    },
                    {
                        "at": 1.0,
                        "focus": "scene_takeaway",
                        "scale": 1.045,
                        "x": -12 + index * 2,
                        "y": 12 - index,
                    },
                ],
                "overlay_actions": _overlay_actions_for_pattern(pattern),
            }
        )
    return {
        "version": "original_motion_plan_v1",
        "min_distinct_patterns": min(4, len(scenes)),
        "shot_frame_count": 18,
        "shots": shots,
    }


def build_caption_timeline(storyboard: dict) -> dict:
    scenes = storyboard.get("scenes", []) if isinstance(storyboard.get("scenes"), list) else []
    captions = []
    for scene in scenes:
        scene_index = int(scene.get("index", len(captions)))
        start = float(scene.get("start") or 0.0)
        end = float(scene.get("end") or start + float(scene.get("duration") or 10.0))
        chunks = _caption_chunks(str(scene.get("voiceover", "")), scene)
        if not chunks:
            chunks = [str(scene.get("title", "原创段落"))]
        segment_duration = max(0.8, (end - start) / len(chunks))
        current = start
        for chunk_index, chunk in enumerate(chunks):
            caption_end = end if chunk_index == len(chunks) - 1 else min(end, current + segment_duration)
            captions.append(
                {
                    "scene_index": scene_index,
                    "index": len(captions),
                    "start": round(current, 2),
                    "end": round(caption_end, 2),
                    "text": chunk,
                }
            )
            current = caption_end
    return {
        "version": "original_caption_timeline_v1",
        "duration": round(float(storyboard.get("target_duration") or 0.0), 2),
        "captions": captions,
    }


def build_asset_pass_report(asset_library_path: Path | str | None, storyboard: dict) -> dict:
    scene_count = len(storyboard.get("scenes", [])) if isinstance(storyboard.get("scenes"), list) else 0
    if asset_library_path is None or not str(asset_library_path).strip():
        return {
            "version": "original_asset_pass_v1",
            "status": "missing",
            "publish_ready": False,
            "library_path": "",
            "assets_found": 0,
            "scene_count": scene_count,
            "license_manifest_present": False,
            "license_manifest_path": "",
            "assets": [],
            "recommendations": ["添加自录、授权或内部素材库路径，并提供 asset_licenses.json 授权清单。"],
        }

    root = Path(str(asset_library_path).strip()).expanduser()
    if not root.exists() or not root.is_dir():
        return {
            "version": "original_asset_pass_v1",
            "status": "not_found",
            "publish_ready": False,
            "library_path": str(root),
            "assets_found": 0,
            "scene_count": scene_count,
            "license_manifest_present": False,
            "license_manifest_path": "",
            "assets": [],
            "recommendations": ["素材库路径不存在或不是目录，无法进入发布候选。"],
        }

    assets = []
    for item in sorted(root.rglob("*")):
        if not item.is_file() or item.name.startswith("."):
            continue
        suffix = item.suffix.lower()
        if suffix not in ASSET_MEDIA_EXTENSIONS:
            continue
        assets.append(
            {
                "path": str(item),
                "name": item.name,
                "kind": _asset_kind(suffix),
                "extension": suffix,
            }
        )

    license_manifest = next((root / name for name in sorted(ASSET_LICENSE_FILENAMES) if (root / name).exists()), None)
    has_assets = len(assets) > 0
    has_license = license_manifest is not None
    if has_assets and has_license:
        status = "ready"
    elif has_assets:
        status = "assets_need_license"
    else:
        status = "empty"
    return {
        "version": "original_asset_pass_v1",
        "status": status,
        "publish_ready": status == "ready",
        "library_path": str(root),
        "assets_found": len(assets),
        "scene_count": scene_count,
        "license_manifest_present": has_license,
        "license_manifest_path": str(license_manifest) if license_manifest else "",
        "assets": assets[:200],
        "recommendations": _asset_pass_recommendations(status),
    }


def build_asset_usage_plan(storyboard: dict, asset_pass_report: dict) -> dict:
    scenes = storyboard.get("scenes", []) if isinstance(storyboard.get("scenes"), list) else []
    assets = asset_pass_report.get("assets", []) if isinstance(asset_pass_report.get("assets"), list) else []
    if not asset_pass_report.get("publish_ready") or not assets:
        return {
            "version": "original_asset_usage_plan_v1",
            "status": "missing_asset_pass",
            "scene_assets": [],
        }
    scene_assets = []
    for scene in scenes:
        index = int(scene.get("index", len(scene_assets)))
        asset = assets[index % len(assets)]
        scene_assets.append(
            {
                "scene_index": index,
                "scene_title": str(scene.get("title", "")),
                "asset_path": str(asset.get("path", "")),
                "asset_kind": str(asset.get("kind", "")),
                "usage": "primary_cutaway" if asset.get("kind") == "video" else "visual_reference",
            }
        )
    return {
        "version": "original_asset_usage_plan_v1",
        "status": "ready",
        "scene_assets": scene_assets,
    }


def render_original_video(
    output_dir: Path | str,
    options: dict | None = None,
    progress: ProgressCallback | None = None,
) -> Dict[str, Path | str]:
    paths = build_original_paths(output_dir)
    paths.output_dir.mkdir(parents=True, exist_ok=True)
    paths.segments_dir.mkdir(parents=True, exist_ok=True)
    paths.frames_dir.mkdir(parents=True, exist_ok=True)
    paths.audio_dir.mkdir(parents=True, exist_ok=True)

    _progress(progress, "生成原创策略")
    strategy = build_original_strategy(options)
    storyboard = build_storyboard(strategy)
    motion_plan = build_motion_plan(storyboard)
    caption_timeline = build_caption_timeline(storyboard)
    asset_pass_report = build_asset_pass_report((options or {}).get("asset_library_path"), storyboard)
    asset_usage_plan = build_asset_usage_plan(storyboard, asset_pass_report)
    _write_json(paths.original_strategy, strategy)
    _write_json(paths.storyboard, storyboard)
    _write_json(paths.motion_plan, motion_plan)
    _write_json(paths.caption_timeline, caption_timeline)
    _write_json(paths.asset_pass_report, asset_pass_report)
    _write_json(paths.asset_usage_plan, asset_usage_plan)
    write_subtitles(paths.subtitles, caption_timeline)
    _write_script(paths.script, strategy, storyboard)
    _write_shotlist(paths.shotlist, storyboard, motion_plan)
    _write_asset_manifest(paths.asset_manifest, storyboard)

    segment_paths: list[Path] = []
    voiceover_entries: list[dict] = []
    scenes = storyboard.get("scenes", []) if isinstance(storyboard.get("scenes"), list) else []
    shots = motion_plan.get("shots", []) if isinstance(motion_plan.get("shots"), list) else []
    for scene in scenes:
        index = int(scene.get("index", len(segment_paths)))
        shot = shots[index] if index < len(shots) and isinstance(shots[index], dict) else {}
        frame_dir = paths.frames_dir / f"scene_{index:02d}"
        audio_path = paths.audio_dir / f"scene_{index:02d}.aiff"
        segment_path = paths.segments_dir / f"scene_{index:02d}.mp4"
        _progress(progress, f"渲染原创场景 {index + 1}/{len(scenes)}")
        voiceover_entry = _write_scene_voiceover(audio_path, str(scene.get("voiceover", "")))
        voiceover_entries.append(voiceover_entry)
        _write_scene_frames(frame_dir, scene, strategy, shot, DEFAULT_ORIGINAL_GEOMETRY, asset_usage_plan=asset_usage_plan)
        segment_audio_path = audio_path if voiceover_entry.get("status") == "ready" else None
        _run(
            build_original_scene_sequence_command(
                frame_dir / "frame_%03d.png",
                segment_path,
                float(scene.get("duration") or 10.0),
                int(motion_plan.get("shot_frame_count") or 18),
                audio_path=segment_audio_path,
            )
        )
        segment_paths.append(segment_path)

    _write_voiceover_manifest(paths.voiceover_manifest, voiceover_entries)
    _concat_segments(segment_paths, paths.concat, paths.video)
    output_probe = probe_media(paths.video)
    output_duration = _duration_from_probe(output_probe)
    output_geometry = geometry_from_probe(output_probe)
    _run(_build_cover_command(paths.video, paths.cover, output_geometry, 0.0))
    _write_contact_sheet(paths.video, paths.contact_sheet, output_duration)
    write_original_quality_report(paths, strategy, storyboard, motion_plan, caption_timeline, require_video=True)
    write_originality_report(paths.originality_report)
    _write_render_report(paths.report, strategy, storyboard, output_probe)
    _progress(progress, "完成原创生成")

    return {
        "mode": "original-generate",
        "video": paths.video,
        "cover": paths.cover,
        "contact_sheet": paths.contact_sheet,
        "report": paths.report,
        "quality_report": paths.quality_report,
        "script": paths.script,
        "storyboard": paths.storyboard,
        "original_strategy": paths.original_strategy,
        "motion_plan": paths.motion_plan,
        "caption_timeline": paths.caption_timeline,
        "subtitles": paths.subtitles,
        "voiceover_manifest": paths.voiceover_manifest,
        "asset_pass_report": paths.asset_pass_report,
        "asset_usage_plan": paths.asset_usage_plan,
        "shotlist": paths.shotlist,
        "asset_manifest": paths.asset_manifest,
        "originality_report": paths.originality_report,
    }


def build_original_scene_command(
    card_image: Path | str,
    output_video: Path | str,
    duration: float,
    geometry: VideoGeometry = DEFAULT_ORIGINAL_GEOMETRY,
) -> list[str]:
    safe_duration = max(2.0, float(duration))
    video_filter = ",".join(
        [
            f"scale={geometry.width}:{geometry.height}:flags=lanczos",
            "setsar=1",
            (
                "zoompan="
                "z='min(1.035,1+on*0.00045)':"
                "x='iw/2-(iw/zoom/2)':"
                "y='ih/2-(ih/zoom/2)':"
                f"d=1:s={geometry.width}x{geometry.height}:fps={FPS}"
            ),
            f"trim=duration={_format_time(safe_duration)}",
            "setpts=PTS-STARTPTS",
            "format=yuv420p",
        ]
    )
    return [
        "ffmpeg",
        "-y",
        "-loop",
        "1",
        "-framerate",
        str(FPS),
        "-t",
        _format_time(safe_duration),
        "-i",
        str(card_image),
        "-f",
        "lavfi",
        "-t",
        _format_time(safe_duration),
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
        str(output_video),
    ]


def build_original_scene_sequence_command(
    frame_pattern: Path | str,
    output_video: Path | str,
    duration: float,
    frame_count: int,
    audio_path: Path | str | None = None,
    geometry: VideoGeometry = DEFAULT_ORIGINAL_GEOMETRY,
) -> list[str]:
    safe_duration = max(2.0, float(duration))
    safe_frame_count = max(3, int(frame_count))
    input_framerate = safe_frame_count / safe_duration
    video_filter = ",".join(
        [
            f"scale={geometry.width}:{geometry.height}:flags=lanczos",
            "setsar=1",
            f"fps={FPS}",
            f"trim=duration={_format_time(safe_duration)}",
            "setpts=PTS-STARTPTS",
            "format=yuv420p",
        ]
    )
    command = [
        "ffmpeg",
        "-y",
        "-framerate",
        f"{input_framerate:.6f}",
        "-start_number",
        "0",
        "-i",
        str(frame_pattern),
    ]
    if audio_path is None:
        command.extend(
            [
                "-f",
                "lavfi",
                "-t",
                _format_time(safe_duration),
                "-i",
                "anullsrc=channel_layout=stereo:sample_rate=44100",
            ]
        )
    else:
        command.extend(["-i", str(audio_path)])
    command.extend(
        [
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-vf",
        video_filter,
        "-af",
        f"atrim=duration={_format_time(safe_duration)},apad=whole_dur={_format_time(safe_duration)}",
        "-t",
        _format_time(safe_duration),
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
        str(output_video),
        ]
    )
    return command


def write_original_quality_report(
    paths: OriginalPaths,
    strategy: dict,
    storyboard: dict,
    motion_plan: dict | None = None,
    caption_timeline: dict | None = None,
    require_video: bool = False,
) -> None:
    scene_count = len(storyboard.get("scenes", [])) if isinstance(storyboard.get("scenes"), list) else 0
    if motion_plan is None:
        motion_plan = _read_json_if_exists(paths.motion_plan)
    if caption_timeline is None:
        caption_timeline = _read_json_if_exists(paths.caption_timeline)
    voiceover_manifest = _read_json_if_exists(paths.voiceover_manifest)
    asset_pass_report = _read_json_if_exists(paths.asset_pass_report)
    asset_usage_plan = _read_json_if_exists(paths.asset_usage_plan)
    shots = motion_plan.get("shots", []) if isinstance(motion_plan.get("shots"), list) else []
    captions = caption_timeline.get("captions", []) if isinstance(caption_timeline.get("captions"), list) else []
    scene_assets = asset_usage_plan.get("scene_assets", []) if isinstance(asset_usage_plan.get("scene_assets"), list) else []
    distinct_patterns = {str(shot.get("shot_pattern", "")) for shot in shots if isinstance(shot, dict)}
    keyframed_shots = [
        shot
        for shot in shots
        if isinstance(shot, dict)
        and isinstance(shot.get("keyframes"), list)
        and len(shot.get("keyframes", [])) >= 3
    ]
    checks = {
        "original_script_present": bool(strategy.get("topic") and scene_count),
        "storyboard_present": scene_count >= 5,
        "no_source_reuse": True,
        "visual_preview_present": bool(paths.video.exists()) if require_video else scene_count > 0,
        "originality_gate_passed": True,
        "dynamic_shot_plan_present": scene_count > 0 and len(shots) >= scene_count and len(keyframed_shots) >= scene_count,
        "caption_timeline_present": scene_count > 0
        and len(captions) >= scene_count
        and (paths.subtitles.exists() if require_video else True),
        "scene_motion_variety": len(distinct_patterns) >= min(4, scene_count) if scene_count else False,
        "voiceover_audio_present": voiceover_manifest.get("status") == "ready"
        and int(voiceover_manifest.get("ready_count") or 0) >= scene_count,
        "asset_pass_ready": bool(asset_pass_report.get("publish_ready")),
        "asset_visuals_embedded": bool(asset_pass_report.get("publish_ready"))
        and asset_usage_plan.get("status") == "ready"
        and len(scene_assets) >= scene_count,
    }
    issues = []
    if not checks["storyboard_present"]:
        issues.append(
            {
                "severity": "error",
                "code": "storyboard_too_short",
                "message": "原创分镜少于 5 场，难以支撑完整叙事。",
            }
        )
    if not checks["visual_preview_present"]:
        issues.append(
            {
                "severity": "blocker",
                "code": "missing_original_preview",
                "message": "缺少原创样片预览，不能进入验收。",
            }
        )
    if not checks["dynamic_shot_plan_present"]:
        issues.append(
            {
                "severity": "error",
                "code": "missing_dynamic_motion_plan",
                "message": "原创样片缺少动态镜头计划或关键帧，容易退化成图片配音。",
            }
        )
    if not checks["caption_timeline_present"]:
        issues.append(
            {
                "severity": "error",
                "code": "missing_caption_timeline",
                "message": "缺少字幕时间线或字幕文件，无法核对信息节奏。",
            }
        )
    if not checks["scene_motion_variety"]:
        issues.append(
            {
                "severity": "error",
                "code": "low_scene_motion_variety",
                "message": "镜头模式变化不足，成片会显得像模板重复。",
            }
        )
    if not checks["voiceover_audio_present"]:
        issues.append(
            {
                "severity": "error",
                "code": "missing_voiceover_audio",
                "message": "缺少真实旁白音频，当前只能作为视觉样片，不能冒充上线成片。",
            }
        )
    if checks["asset_pass_ready"] and not checks["asset_visuals_embedded"]:
        issues.append(
            {
                "severity": "error",
                "code": "missing_asset_usage_plan",
                "message": "素材通行证已通过，但画面层没有资产使用计划，不能证明自有/授权素材已经进入分镜。",
            }
        )
    publish_tier = "publish_candidate"
    if not checks["asset_pass_ready"]:
        publish_tier = "preview_needs_asset_pass"
        if require_video:
            issues.append(
                {
                    "severity": "warning",
                    "code": "missing_asset_pass",
                    "message": "缺少可发布素材通行证：需要自录/授权素材库和授权清单后，才可升级为发布候选。",
            }
        )
    if checks["asset_pass_ready"] and not checks["asset_visuals_embedded"]:
        publish_tier = "preview_needs_asset_usage"
    if require_video and voiceover_manifest.get("provider") == "macos_say":
        if checks["asset_pass_ready"] and checks["asset_visuals_embedded"]:
            publish_tier = "preview_needs_voiceover_upgrade"
        issues.append(
            {
                "severity": "warning",
                "code": "draft_voiceover_provider",
                "message": "当前使用本机草稿 TTS，可验证链路，但上线前建议替换成品牌级配音或真人录音。",
            }
        )
    if require_video and not checks["asset_pass_ready"]:
        publish_tier = "preview_needs_asset_pass"
        issues.append(
            {
                "severity": "warning",
                "code": "generated_motion_graphics_only",
                "message": "当前视觉层是动态图形样片，适合验证结构；上线版应补充自录屏、授权素材或实拍资产。",
            }
        )
    blocking_issues = [issue for issue in issues if str(issue.get("severity")) != "warning"]
    payload = {
        "status": "passed" if not blocking_issues else "failed",
        "checks": checks,
        "issues": issues,
        "mode": "original-generate",
        "strategy": {
            "topic": strategy.get("topic"),
            "target_duration": strategy.get("target_duration"),
            "chapter_count": scene_count,
            "motion_pattern_count": len(distinct_patterns),
            "caption_count": len(captions),
            "voiceover_status": voiceover_manifest.get("status", "missing"),
            "asset_pass_status": asset_pass_report.get("status", "missing"),
            "asset_count": int(asset_pass_report.get("assets_found") or 0),
            "asset_usage_status": asset_usage_plan.get("status", "missing"),
            "asset_usage_scene_count": len(scene_assets),
            "publish_tier": publish_tier,
        },
    }
    _write_json(paths.quality_report, payload)


def write_originality_report(output_path: Path | str) -> None:
    payload = {
        "risk_level": "low",
        "risk_reason": "原创生成任务没有输入参考视频，未检测到源片画面、原声或文本复用。",
        "similarity_score": 0,
        "metrics": {
            "visual_similarity": 0.0,
            "audio_reuse_ratio": 0.0,
            "text_overlap_ratio": 0.0,
            "source_reuse_ratio": 0.0,
            "duration_retention": 0.0,
        },
        "recommendations": [
            "上线前继续做事实核查、素材授权核查和人工抽检。",
            "如果后续加入参考视频或外部素材，需要重新计算原创风险。",
        ],
        "disclaimer": "本报告只证明当前任务没有复用源片，不替代平台审核或版权审查。",
    }
    _write_json(Path(output_path), payload)


def write_subtitles(output_path: Path | str, caption_timeline: dict) -> None:
    captions = caption_timeline.get("captions", []) if isinstance(caption_timeline.get("captions"), list) else []
    blocks = []
    for index, caption in enumerate(captions, start=1):
        start = _srt_time(float(caption.get("start") or 0.0))
        end = _srt_time(float(caption.get("end") or 0.0))
        text = str(caption.get("text", "")).strip()
        if not text:
            continue
        blocks.append(f"{index}\n{start} --> {end}\n{text}")
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_text("\n\n".join(blocks) + ("\n" if blocks else ""), encoding="utf-8")


def _write_scene_voiceover(output_path: Path, text: str) -> dict:
    cleaned = _clean_text(text)
    say_bin = shutil.which("say")
    if not say_bin:
        return {
            "path": str(output_path),
            "status": "failed",
            "reason": "macOS say command not found",
            "text": cleaned,
        }
    if not cleaned:
        return {
            "path": str(output_path),
            "status": "failed",
            "reason": "empty voiceover text",
            "text": cleaned,
        }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    command = [say_bin, "-o", str(output_path), cleaned]
    try:
        _run(command)
    except Exception as exc:  # pragma: no cover - depends on local TTS availability.
        return {
            "path": str(output_path),
            "status": "failed",
            "reason": str(exc),
            "text": cleaned,
        }
    return {
        "path": str(output_path),
        "status": "ready" if output_path.exists() else "failed",
        "reason": "" if output_path.exists() else "voiceover file was not created",
        "text": cleaned,
    }


def _write_voiceover_manifest(output_path: Path, entries: list[dict]) -> None:
    ready_count = sum(1 for entry in entries if entry.get("status") == "ready")
    payload = {
        "version": "original_voiceover_manifest_v1",
        "status": "ready" if entries and ready_count == len(entries) else "failed",
        "scene_count": len(entries),
        "ready_count": ready_count,
        "provider": "macos_say",
        "entries": entries,
    }
    _write_json(output_path, payload)


def _overlay_actions_for_pattern(pattern: str) -> list[str]:
    actions = {
        "hook_matrix": ["问题卡进入", "痛点高亮", "观点收束"],
        "risk_tracks": ["证据轨道展开", "风险点扫光", "降重策略锁定"],
        "production_map": ["节点逐个点亮", "流程线推进", "质检闸门落位"],
        "script_lab": ["草稿对照", "表达重写", "发布稿生成"],
        "asset_wall": ["素材卡翻入", "授权状态标记", "可替换项分组"],
        "audit_dashboard": ["指标翻牌", "阈值仪表扫描", "风险等级锁定"],
        "batch_queue": ["队列滚动", "失败分支返工", "通过项归档"],
        "action_close": ["清单逐条勾选", "发布前停顿", "人工复核提示"],
    }
    return actions.get(pattern, actions["hook_matrix"])


def _caption_chunks(text: str, scene: dict) -> list[str]:
    cleaned = _clean_text(text)
    if not cleaned:
        cleaned = _clean_text(" ".join(str(item) for item in scene.get("on_screen_text", [])))
    if not cleaned:
        return []
    raw_parts = [part.strip() for part in re.split(r"[，。；;：:,.!?！？]", cleaned) if part.strip()]
    chunks: list[str] = []
    for part in raw_parts:
        if _text_len(part) <= 26:
            chunks.append(part)
            continue
        current = ""
        for char in part:
            current += char
            if _text_len(current) >= 22:
                chunks.append(current)
                current = ""
        if current:
            chunks.append(current)
    if len(chunks) < 2 and scene.get("title"):
        chunks.insert(0, str(scene["title"]))
    return chunks[:4]


def _asset_kind(suffix: str) -> str:
    if suffix.lower() in {".mp4", ".mov", ".m4v", ".webm"}:
        return "video"
    return "image"


def _asset_pass_recommendations(status: str) -> list[str]:
    if status == "ready":
        return ["素材库和授权清单已存在，可进入人工抽检和发布候选评估。"]
    if status == "assets_need_license":
        return ["素材文件已找到，但缺少 asset_licenses.json、licenses.json 或 license.json 授权清单。"]
    if status == "empty":
        return ["素材库目录为空或没有可用图片/视频素材，请添加自录、授权或内部素材。"]
    if status == "not_found":
        return ["素材库路径不存在，请检查本机绝对路径。"]
    return ["添加自录、授权或内部素材库路径，并提供 asset_licenses.json 授权清单。"]


def _chapters_for_topic(topic: str, brief: str, creative_strength: str) -> list[str]:
    topic_label = topic or "原创视频"
    base = [
        f"{topic_label}的真实痛点",
        "为什么简单复刻会失败",
        "原创生产链路全景",
        "脚本结构和信息增量",
        "分镜与素材策略",
        "相似度、音频和文本质检",
        "批量生产与返工机制",
        "结论和下一步行动",
    ]
    if "质检" in brief and "质检" not in "".join(base):
        base.insert(5, "质量闸门如何拦截风险")
    if creative_strength == "light":
        return base[:6]
    return base


def _visual_goal(index: int, chapter: str, strategy: dict) -> str:
    goals = [
        "用真实问题清单建立观看动机。",
        "把复刻风险拆成画面、音频、文本三个证据层。",
        "展示从选题到发布前验收的生产管线。",
        "把脚本写成观点、证据、操作、结论的结构。",
        "列出需要自录、授权或生成的素材类型。",
        "展示原创风控仪表盘和阈值。",
        "展示批量队列、失败返工和人工抽检。",
        "收束为可执行的生产原则。",
    ]
    del strategy
    return goals[index % len(goals)] + f" 章节：{chapter}"


def _motion_for_scene(index: int) -> str:
    motions = [
        "左侧问题卡逐条进入，右侧风险曲线推进。",
        "三条证据轨道横向展开，风险点高亮。",
        "生产流程节点沿时间线点亮。",
        "脚本块从草稿变成发布稿。",
        "素材卡片按授权状态分组。",
        "质检指标从待检变成通过/阻断。",
        "批量任务队列滚动并产生返工分支。",
        "总结卡聚合成上线清单。",
    ]
    return motions[index % len(motions)]


def _screen_text(index: int, chapter: str, strategy: dict) -> list[str]:
    topic = str(strategy.get("topic", "原创视频"))
    text_sets = [
        [topic, "不是换皮，而是重新生产信息价值"],
        ["复刻风险", "画面相似度 / 音频复用 / 文本重合"],
        ["原创链路", "选题 -> 脚本 -> 分镜 -> 素材 -> 质检"],
        ["脚本原则", "新增观点、案例、操作和判断"],
        ["素材原则", "自录、授权、生成素材分开管理"],
        ["上线闸门", "相似度、音频、文本、质量分"],
        ["批量机制", "队列、报告、返工、抽检"],
        ["发布前", "证据完整，再进入平台"],
    ]
    return [chapter] + text_sets[index % len(text_sets)]


def _voiceover_for_scene(index: int, chapter: str, strategy: dict) -> str:
    topic = str(strategy.get("topic", "这个选题"))
    lines = [
        f"这一期先把{topic}的核心问题说清楚：质量不是靠包装堆出来的。",
        "如果只是重剪原片，平台最容易识别的是画面结构、声音和文本的同源性。",
        "原创生成要从生产链路开始，先有脚本和分镜，再决定素材，而不是反过来。",
        "脚本必须加入新的判断、新的例子和新的操作路径，观众才会得到增量。",
        "素材层要区分自录、授权和生成，每一种都要留证据。",
        "质检不是最后补一张表，而是每次生成都要给出能否上线的判断。",
        "批量生产的价值在于稳定返工，不是一次蒙对。",
        f"所以{chapter}的结论是：先做原创结构，再用工具放大产能。",
    ]
    return lines[index % len(lines)]


def _write_scene_frames(
    frame_dir: Path,
    scene: dict,
    strategy: dict,
    shot: dict,
    geometry: VideoGeometry,
    frame_count: int = 18,
    asset_usage_plan: dict | None = None,
) -> None:
    frame_dir.mkdir(parents=True, exist_ok=True)
    safe_count = max(3, frame_count)
    scene_asset = _scene_asset_for_scene(asset_usage_plan or {}, int(scene.get("index", 0)))
    for frame_index in range(safe_count):
        phase = frame_index / (safe_count - 1)
        _write_scene_frame(
            frame_dir / f"frame_{frame_index:03d}.png",
            scene,
            strategy,
            geometry,
            shot=shot,
            phase=phase,
            scene_asset=scene_asset,
        )


def _write_scene_frame(
    path: Path,
    scene: dict,
    strategy: dict,
    geometry: VideoGeometry,
    shot: dict | None = None,
    phase: float = 0.0,
    scene_asset: dict | None = None,
) -> None:
    phase = max(0.0, min(1.0, float(phase)))
    shot = shot or {}
    pattern = str(shot.get("shot_pattern") or "hook_matrix")
    image = Image.new("RGB", (geometry.width, geometry.height), "#f2efe6")
    draw = ImageDraw.Draw(image)
    palette = ["#16201d", "#1f5f70", "#b85c38", "#267553", "#553f75", "#8a6c28", "#b4443f", "#20262b"]
    accent = palette[int(scene.get("index", 0)) % len(palette)]
    muted = "#65706f"
    ink = "#151719"

    grid = 64
    offset = int(phase * grid * 0.5)
    for x in range(-grid + offset, geometry.width + grid, grid):
        draw.line((x, 0, x, geometry.height), fill="#dedbd0", width=1)
    for y in range(-grid + offset, geometry.height + grid, grid):
        draw.line((0, y, geometry.width, y), fill="#dedbd0", width=1)

    margin = 78
    draw.rectangle((margin, margin, geometry.width - margin, geometry.height - margin), outline="#c2bcaf", width=2)
    draw.rectangle((margin, margin, margin + 14, geometry.height - margin), fill=accent)

    eyebrow_font = _font(26, bold=True)
    title_font = _font(64, bold=True)
    body_font = _font(30)
    small_font = _font(23)
    caption_font = _font(34, bold=True)
    label_font = _font(21, bold=True)

    scene_no = f"{int(scene.get('index', 0)) + 1:02d}"
    draw.text((margin + 42, margin + 34), "ORIGINAL GENERATION", fill=accent, font=eyebrow_font)
    draw.text((geometry.width - margin - 210, margin + 34), f"SCENE {scene_no}", fill=ink, font=eyebrow_font)
    progress_x0 = geometry.width - margin - 460
    progress_y = margin + 74
    draw.rectangle((progress_x0, progress_y, geometry.width - margin - 28, progress_y + 10), fill="#d7d4ca")
    draw.rectangle(
        (progress_x0, progress_y, progress_x0 + int((geometry.width - margin - 28 - progress_x0) * phase), progress_y + 10),
        fill=accent,
    )

    title_x = margin + 42 + int(math.sin(phase * math.pi) * 10)
    y = margin + 102
    for line in _wrap(str(scene.get("title", "原创章节")), title_font, 880)[:2]:
        draw.text((title_x, y), line, fill=ink, font=title_font)
        y += _line_height(title_font) + 10
    for line in _wrap(str(scene.get("visual_goal", "")), body_font, 900)[:2]:
        draw.text((title_x, y + 12), line, fill="#303733", font=body_font)
        y += _line_height(body_font) + 8

    visual_box = (margin + 42, 390, geometry.width - margin - 42, geometry.height - 210)
    _draw_pattern_visual(draw, scene, pattern, visual_box, accent, ink, muted, phase, label_font, small_font, body_font)
    _draw_asset_preview(image, draw, scene_asset or {}, visual_box, accent, ink, muted, label_font, small_font)

    caption = _phase_caption(scene, phase)
    caption_y0 = geometry.height - 178
    draw.rectangle((margin + 42, caption_y0, geometry.width - margin - 42, caption_y0 + 88), fill="#151719")
    draw.rectangle((margin + 42, caption_y0, margin + 52, caption_y0 + 88), fill=accent)
    _draw_text_lines(draw, caption, (margin + 74, caption_y0 + 22), caption_font, "#f4f1e8", geometry.width - margin * 2 - 170, 1)

    pills = [str(item) for item in scene.get("on_screen_text", [])[:3]]
    pill_x = margin + 42
    pill_y = geometry.height - 78
    for pill in pills:
        text_w = min(_text_width(pill, small_font), 420)
        draw.rectangle((pill_x, pill_y, pill_x + text_w + 34, pill_y + 42), fill="#ffffff", outline="#c6c0b3", width=1)
        draw.text((pill_x + 16, pill_y + 9), pill[:28], fill=ink, font=small_font)
        pill_x += text_w + 48

    footer = f"{strategy.get('topic', '')} / motion plan / caption timeline / no source reuse"
    draw.text((geometry.width - margin - 680, geometry.height - 70), footer[:90], fill=muted, font=small_font)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)


def _draw_pattern_visual(
    draw: ImageDraw.ImageDraw,
    scene: dict,
    pattern: str,
    box: tuple[int, int, int, int],
    accent: str,
    ink: str,
    muted: str,
    phase: float,
    label_font: ImageFont.ImageFont,
    small_font: ImageFont.ImageFont,
    body_font: ImageFont.ImageFont,
) -> None:
    x0, y0, x1, y1 = box
    width = x1 - x0
    height = y1 - y0
    draw.rectangle(box, fill="#fbfcf8", outline="#c6c0b3", width=2)
    if pattern == "risk_tracks":
        labels = ["画面相似", "音频复用", "文本重合"]
        for idx, label in enumerate(labels):
            y = y0 + 70 + idx * 132
            draw.text((x0 + 46, y - 34), label, fill=ink, font=label_font)
            draw.rectangle((x0 + 46, y, x1 - 60, y + 22), fill="#dfe4dc")
            active_x = x0 + 46 + int((width - 160) * min(1.0, phase + idx * 0.08))
            draw.rectangle((x0 + 46, y, active_x, y + 22), fill=accent)
            draw.ellipse((active_x - 17, y - 16, active_x + 17, y + 38), fill="#151719")
            draw.text((x1 - 260, y + 44), "改写结构 + 原创证据", fill=muted, font=small_font)
    elif pattern == "production_map":
        nodes = ["选题", "脚本", "分镜", "素材", "质检", "发布"]
        step = (width - 150) / (len(nodes) - 1)
        base_y = y0 + height // 2
        for idx, node in enumerate(nodes):
            cx = int(x0 + 75 + idx * step)
            active = phase >= idx / max(1, len(nodes) - 1)
            if idx:
                prev_x = int(x0 + 75 + (idx - 1) * step)
                draw.line((prev_x + 38, base_y, cx - 38, base_y), fill=accent if active else "#d5d1c6", width=6)
            fill = accent if active else "#ffffff"
            text_fill = "#ffffff" if active else ink
            draw.ellipse((cx - 45, base_y - 45, cx + 45, base_y + 45), fill=fill, outline="#c6c0b3", width=2)
            draw.text((cx - _text_width(node, label_font) // 2, base_y - 15), node, fill=text_fill, font=label_font)
        _draw_text_lines(draw, str(scene.get("motion", "")), (x0 + 60, y1 - 96), body_font, ink, width - 120, 2)
    elif pattern == "script_lab":
        mid = x0 + width // 2
        draw.rectangle((x0 + 48, y0 + 56, mid - 28, y1 - 58), fill="#f7eee7", outline="#d6c8ba", width=2)
        draw.rectangle((mid + 28, y0 + 56, x1 - 48, y1 - 58), fill="#eef7f1", outline="#bdd8c7", width=2)
        draw.text((x0 + 72, y0 + 84), "草稿", fill=muted, font=label_font)
        draw.text((mid + 52, y0 + 84), "发布稿", fill=accent, font=label_font)
        draft = ["复述原片", "缺少观点", "同质表达"]
        final = ["新判断", "新例子", "新操作", "新结论"]
        for idx, item in enumerate(draft):
            draw.text((x0 + 86, y0 + 154 + idx * 78), item, fill="#9a4d3b", font=body_font)
        for idx, item in enumerate(final):
            reveal = phase >= idx / max(1, len(final))
            draw.rectangle((mid + 70, y0 + 150 + idx * 62, x1 - 82, y0 + 194 + idx * 62), fill=accent if reveal else "#dfe4dc")
            draw.text((mid + 92, y0 + 157 + idx * 62), item, fill="#ffffff" if reveal else muted, font=small_font)
        arrow_x = mid - 24 + int(math.sin(phase * math.pi) * 48)
        draw.polygon([(arrow_x, y0 + height // 2), (arrow_x - 46, y0 + height // 2 - 34), (arrow_x - 46, y0 + height // 2 + 34)], fill=accent)
    elif pattern == "asset_wall":
        assets = ["自录屏", "授权素材", "原创配音", "数据图", "案例画面", "质检证据"]
        card_w = (width - 150) // 3
        card_h = 118
        for idx, asset in enumerate(assets):
            col = idx % 3
            row = idx // 3
            x = x0 + 50 + col * (card_w + 25)
            y = y0 + 70 + row * (card_h + 48) + int(math.sin((phase + idx * 0.1) * math.pi) * 10)
            active = phase > idx / (len(assets) + 1)
            draw.rectangle((x, y, x + card_w, y + card_h), fill="#ffffff", outline=accent if active else "#c6c0b3", width=3 if active else 1)
            draw.text((x + 24, y + 24), asset, fill=ink, font=body_font)
            draw.text((x + 24, y + 72), "可留证 / 可替换", fill=accent if active else muted, font=small_font)
    elif pattern == "audit_dashboard":
        metrics = [("质量分", "A"), ("相似度", "0%"), ("音频复用", "0%"), ("文本重合", "0%")]
        for idx, (label, value) in enumerate(metrics):
            col = idx % 2
            row = idx // 2
            x = x0 + 58 + col * ((width - 140) // 2 + 28)
            y = y0 + 58 + row * 154
            w = (width - 170) // 2
            draw.rectangle((x, y, x + w, y + 118), fill="#ffffff", outline="#c6c0b3", width=2)
            draw.text((x + 24, y + 22), label, fill=muted, font=small_font)
            draw.text((x + 24, y + 58), value, fill=accent, font=_font(46, bold=True))
        cx = x0 + width // 2
        cy = y1 - 92
        draw.arc((cx - 220, cy - 220, cx + 220, cy + 220), 200, 340, fill="#d6d2c7", width=20)
        draw.arc((cx - 220, cy - 220, cx + 220, cy + 220), 200, int(200 + 140 * phase), fill=accent, width=20)
        draw.text((cx - 90, cy - 70), "上线闸门", fill=ink, font=body_font)
    elif pattern == "batch_queue":
        for idx in range(7):
            y = y0 + 48 + idx * 72 - int(phase * 36)
            if y < y0 + 30 or y > y1 - 70:
                continue
            active = idx in {2, 5}
            draw.rectangle((x0 + 54, y, x1 - 54, y + 54), fill="#ffffff", outline=accent if active else "#c6c0b3", width=2)
            draw.text((x0 + 78, y + 13), f"任务 {idx + 1:02d} / 原创脚本 / 质检", fill=ink, font=small_font)
            state = "返工" if active else "通过"
            draw.text((x1 - 172, y + 13), state, fill="#b4443f" if active else accent, font=small_font)
    elif pattern == "action_close":
        items = ["只用自有或授权素材", "旁白重新组织观点", "字幕时间线可核查", "上线前看风险等级"]
        for idx, item in enumerate(items):
            y = y0 + 72 + idx * 94
            active = phase >= idx / max(1, len(items))
            draw.rectangle((x0 + 70, y, x0 + 122, y + 52), fill=accent if active else "#ffffff", outline="#c6c0b3", width=2)
            if active:
                draw.line((x0 + 82, y + 29, x0 + 94, y + 41, x0 + 112, y + 15), fill="#ffffff", width=5)
            draw.text((x0 + 152, y + 8), item, fill=ink, font=body_font)
        draw.text((x1 - 320, y1 - 94), "READY FOR REVIEW", fill=accent, font=label_font)
    else:
        text_items = [str(item) for item in scene.get("on_screen_text", [])[:4]]
        for idx, item in enumerate(text_items):
            x = x0 + 60 + int((idx % 2) * (width * 0.45)) + int(math.sin((phase + idx * 0.2) * math.pi) * 18)
            y = y0 + 70 + (idx // 2) * 150
            draw.rectangle((x, y, x + int(width * 0.38), y + 108), fill="#ffffff", outline=accent if phase > idx * 0.18 else "#c6c0b3", width=2)
            _draw_text_lines(draw, item, (x + 24, y + 28), body_font, ink, int(width * 0.34), 2)
        curve_points = []
        for i in range(8):
            x = x0 + 70 + i * ((width - 140) // 7)
            y = y1 - 84 - int((math.sin(i * 0.9 + phase * 2.6) + 1) * 44)
            curve_points.append((x, y))
        draw.line(curve_points, fill=accent, width=7)


def _scene_asset_for_scene(asset_usage_plan: dict, scene_index: int) -> dict:
    scene_assets = asset_usage_plan.get("scene_assets", []) if isinstance(asset_usage_plan.get("scene_assets"), list) else []
    for asset in scene_assets:
        if isinstance(asset, dict) and int(asset.get("scene_index", -1)) == scene_index:
            return asset
    return {}


def _draw_asset_preview(
    image: Image.Image,
    draw: ImageDraw.ImageDraw,
    scene_asset: dict,
    box: tuple[int, int, int, int],
    accent: str,
    ink: str,
    muted: str,
    label_font: ImageFont.ImageFont,
    small_font: ImageFont.ImageFont,
) -> None:
    asset_path = Path(str(scene_asset.get("asset_path", ""))).expanduser()
    if not asset_path:
        return
    x0, y0, x1, _ = box
    panel_w = min(460, max(320, (x1 - x0) // 3))
    panel_h = 292
    px1 = x1 - 42
    px0 = px1 - panel_w
    py0 = y0 + 46
    py1 = py0 + panel_h
    draw.rectangle((px0 + 14, py0 + 14, px1 + 14, py1 + 14), fill="#d8d4c8")
    draw.rectangle((px0, py0, px1, py1), fill="#ffffff", outline=accent, width=4)
    draw.text((px0 + 24, py0 + 18), "OWNED ASSET", fill=accent, font=label_font)

    media_box = (px0 + 24, py0 + 62, px1 - 24, py1 - 64)
    kind = str(scene_asset.get("asset_kind") or _asset_kind(asset_path.suffix))
    rendered = False
    if kind == "image" and asset_path.exists():
        rendered = _paste_asset_image(image, asset_path, media_box)
    if not rendered:
        draw.rectangle(media_box, fill="#eef0ea", outline="#c6c0b3", width=2)
        label = "VIDEO ASSET" if kind == "video" else "ASSET PREVIEW"
        draw.text((media_box[0] + 24, media_box[1] + 48), label, fill=ink, font=label_font)
        draw.text((media_box[0] + 24, media_box[1] + 92), asset_path.name[:34], fill=muted, font=small_font)

    usage = str(scene_asset.get("usage") or "visual_reference")
    footer = f"{usage} / {asset_path.name}"[:46]
    draw.text((px0 + 24, py1 - 44), footer, fill=ink, font=small_font)


def _paste_asset_image(image: Image.Image, asset_path: Path, media_box: tuple[int, int, int, int]) -> bool:
    try:
        with Image.open(asset_path) as raw:
            asset = ImageOps.exif_transpose(raw).convert("RGB")
    except (OSError, ValueError):
        return False
    target_w = media_box[2] - media_box[0]
    target_h = media_box[3] - media_box[1]
    asset.thumbnail((target_w, target_h), Image.Resampling.LANCZOS)
    paste_x = media_box[0] + (target_w - asset.width) // 2
    paste_y = media_box[1] + (target_h - asset.height) // 2
    image.paste(asset, (paste_x, paste_y))
    return True


def _draw_text_lines(
    draw: ImageDraw.ImageDraw,
    text: str,
    xy: tuple[int, int],
    font: ImageFont.ImageFont,
    fill: str,
    max_width: int,
    max_lines: int,
) -> None:
    x, y = xy
    for line in _wrap(text, font, max_width)[:max_lines]:
        draw.text((x, y), line, fill=fill, font=font)
        y += _line_height(font) + 8


def _phase_caption(scene: dict, phase: float) -> str:
    chunks = _caption_chunks(str(scene.get("voiceover", "")), scene)
    if not chunks:
        return str(scene.get("title", ""))
    index = min(len(chunks) - 1, int(phase * len(chunks)))
    return chunks[index]


def _write_script(path: Path, strategy: dict, storyboard: dict) -> None:
    lines = [f"# {strategy.get('topic', '原创视频脚本')}", ""]
    if strategy.get("brief"):
        lines.extend(["## 创作说明", str(strategy["brief"]), ""])
    lines.append("## 旁白脚本")
    for scene in storyboard.get("scenes", []):
        lines.append(f"### {int(scene['index']) + 1:02d}. {scene['title']}")
        lines.append(str(scene["voiceover"]))
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _write_shotlist(path: Path, storyboard: dict, motion_plan: dict | None = None) -> None:
    shots = motion_plan.get("shots", []) if isinstance((motion_plan or {}).get("shots"), list) else []
    lines = ["# 原创镜头清单", ""]
    for scene in storyboard.get("scenes", []):
        index = int(scene["index"])
        shot = shots[index] if index < len(shots) and isinstance(shots[index], dict) else {}
        lines.append(f"## {int(scene['index']) + 1:02d}. {scene['title']}")
        lines.append(f"- 时长: {scene['duration']}s")
        lines.append(f"- 画面目标: {scene['visual_goal']}")
        lines.append(f"- 动作: {scene['motion']}")
        if shot:
            lines.append(f"- 镜头模式: {shot.get('shot_pattern')} / {shot.get('camera_move')}")
            lines.append(f"- 关键帧: {len(shot.get('keyframes', []))} 个")
        lines.append("- 素材: 自录/授权/生成素材，禁止直接复用参考视频")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _write_asset_manifest(path: Path, storyboard: dict) -> None:
    payload = {
        "policy": "original_assets_only",
        "requirements": storyboard.get("asset_requirements", []),
        "scenes": [
            {
                "index": scene.get("index"),
                "title": scene.get("title"),
                "required_assets": ["original_screen_recording", "licensed_broll_or_motion_graphics", "original_voiceover"],
            }
            for scene in storyboard.get("scenes", [])
        ],
    }
    _write_json(path, payload)


def _write_render_report(path: Path, strategy: dict, storyboard: dict, output_probe: dict) -> None:
    payload = {
        "mode": "original-generate",
        "topic": strategy.get("topic"),
        "scene_count": len(storyboard.get("scenes", [])),
        "duration": _duration_from_probe(output_probe),
        "geometry": {
            "width": geometry_from_probe(output_probe).width,
            "height": geometry_from_probe(output_probe).height,
        },
        "fps": FPS,
    }
    _write_json(path, payload)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_json_if_exists(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _progress(progress: ProgressCallback | None, message: str) -> None:
    if progress is not None:
        progress(message)


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    return load_font(size, bold)


def _wrap(text: str, font: ImageFont.ImageFont, max_width: int) -> list[str]:
    value = str(text).strip()
    if not value:
        return []
    lines: list[str] = []
    current = ""
    for char in value:
        candidate = current + char
        if current and _text_width(candidate, font) > max_width:
            lines.append(current)
            current = char
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def _text_width(text: str, font: ImageFont.ImageFont) -> int:
    try:
        return math.ceil(font.getlength(text))
    except AttributeError:
        return font.getbbox(text)[2]


def _text_len(text: str) -> int:
    return len(str(text))


def _line_height(font: ImageFont.ImageFont) -> int:
    bbox = font.getbbox("国Ag")
    return bbox[3] - bbox[1]


def _srt_time(seconds: float) -> str:
    safe_seconds = max(0.0, float(seconds))
    hours = int(safe_seconds // 3600)
    minutes = int((safe_seconds % 3600) // 60)
    secs = int(safe_seconds % 60)
    millis = int(round((safe_seconds - math.floor(safe_seconds)) * 1000))
    if millis >= 1000:
        secs += 1
        millis -= 1000
    if secs >= 60:
        minutes += 1
        secs -= 60
    if minutes >= 60:
        hours += 1
        minutes -= 60
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"
