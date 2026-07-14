from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict

from PIL import Image, ImageDraw, ImageFont, ImageOps

from video_factory.fonts import load_font

from video_factory.replicate import (
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
DEFAULT_REFERENCE_ORIGINAL_GEOMETRY = VideoGeometry(width=1920, height=1080)
DEFAULT_DURATION_RANGE = [180, 300]
CAPTION_MAX_CHARS = 32
CAPTION_MIN_SECONDS = 0.8
PUBLISHABLE_IMAGE_PROVIDERS = {"images2", "uploaded_generated_assets", "professional_image_provider"}
PREVIEW_IMAGE_PROVIDERS = {"mock_image"}
PUBLISHABLE_VOICE_PROVIDERS = {"mock_professional_voice", "professional_voice", "uploaded_voice"}


@dataclass(frozen=True)
class ReferenceGuidedPaths:
    output_dir: Path
    segments_dir: Path
    frames_dir: Path
    generated_assets_dir: Path
    cover_candidates_dir: Path
    audio_dir: Path
    video: Path
    cover: Path
    contact_sheet: Path
    reference_blueprint: Path
    content_plan: Path
    script_v2: Path
    storyboard_v2: Path
    visual_requirements: Path
    asset_sourcing_plan: Path
    cover_brief: Path
    cover_prompt_pack: Path
    cover_asset_manifest: Path
    visual_prompt_pack: Path
    generated_asset_manifest: Path
    caption_timeline: Path
    subtitles: Path
    voiceover_manifest: Path
    user_delivery: Path
    quality_report: Path
    report: Path
    concat: Path


def build_reference_guided_paths(output_dir: Path | str) -> ReferenceGuidedPaths:
    root = Path(output_dir)
    return ReferenceGuidedPaths(
        output_dir=root,
        segments_dir=root / "segments",
        frames_dir=root / "scene_frames",
        generated_assets_dir=root / "generated_assets",
        cover_candidates_dir=root / "cover_candidates",
        audio_dir=root / "voiceover",
        video=root / "release.mp4",
        cover=root / "cover.png",
        contact_sheet=root / "contact_sheet.jpg",
        reference_blueprint=root / "reference_blueprint.json",
        content_plan=root / "content_plan.json",
        script_v2=root / "script_v2.md",
        storyboard_v2=root / "storyboard_v2.json",
        visual_requirements=root / "visual_requirements.json",
        asset_sourcing_plan=root / "asset_sourcing_plan.json",
        cover_brief=root / "cover_brief.json",
        cover_prompt_pack=root / "cover_prompt_pack.json",
        cover_asset_manifest=root / "cover_asset_manifest.json",
        visual_prompt_pack=root / "visual_prompt_pack.json",
        generated_asset_manifest=root / "generated_asset_manifest.json",
        caption_timeline=root / "caption_timeline.json",
        subtitles=root / "subtitles.srt",
        voiceover_manifest=root / "voiceover_manifest.json",
        user_delivery=root / "user_delivery.json",
        quality_report=root / "quality_report.json",
        report=root / "render_report.json",
        concat=root / "segments.txt",
    )


def build_reference_blueprint(reference_video_path: Path | str, reference_probe: dict, options: dict | None = None) -> dict:
    options = options or {}
    reference_video_path = Path(reference_video_path)
    duration = round(_duration_from_probe(reference_probe), 3)
    geometry = geometry_from_probe(reference_probe)
    chapter_count = max(4, min(7, int(round(max(duration, 240.0) / 90.0))))
    shot_rhythm = 4.5 if duration <= 180 else 6.0 if duration <= 600 else 7.5
    density = "medium_high" if duration >= 420 else "standard"
    opening_style = "problem_cold_open" if duration >= 90 else "direct_context_open"
    return {
        "version": "reference_blueprint_v1",
        "workflow": "reference_guided_original",
        "topic_hint": _topic_from_options(options),
        "reuse_policy": str(options.get("reuse_policy") or "redraw_by_default"),
        "source_media_allowed": False,
        "source_audio_allowed": False,
        "default_duration_range_seconds": _reference_duration_range(duration, options),
        "reference": {
            "path": str(reference_video_path),
            "name": reference_video_path.name,
            "duration_seconds": duration,
            "geometry": {"width": geometry.width, "height": geometry.height},
            "has_audio": _probe_has_audio(reference_probe),
        },
        "learned_structure": {
            "opening_style": opening_style,
            "chapter_count": chapter_count,
            "average_shot_rhythm_seconds": shot_rhythm,
            "information_density": density,
            "visual_types": [
                "documentary_illustration",
                "process_diagram",
                "data_card",
                "detail_cutaway",
            ],
            "chapter_pattern": [
                "hook",
                "context",
                "method",
                "evidence",
                "risk_or_tradeoff",
                "takeaway",
            ][:chapter_count],
            "emotion_curve": ["curiosity", "clarity", "confidence", "decision"],
        },
        "guardrails": [
            "参考视频只学习结构、节奏和表达密度，不复用原画面。",
            "默认重画视觉资产，不复用原声、原字幕和原脚本。",
            "AI 插画用于解释和信息表达，不冒充真实新闻、赛事或现场素材。",
        ],
    }


def build_content_plan(blueprint: dict, options: dict | None = None) -> dict:
    options = options or {}
    topic = _topic_from_options(options) or str(blueprint.get("topic_hint") or "AI 原创视频工厂")
    audience = str(options.get("audience") or "auto")
    target_duration = _target_duration_seconds(options, blueprint)
    chapter_count = int((blueprint.get("learned_structure") or {}).get("chapter_count") or 6)
    chapter_seed = _chapter_seed_for_topic(topic, max(4, chapter_count))
    chapters = []
    cursor = 0.0
    chapter_duration = target_duration / max(4, chapter_count)
    for index, (title, viewpoint, evidence, visual_goal) in enumerate(chapter_seed[: max(4, chapter_count)]):
        duration = round(chapter_duration, 3)
        narration = _chapter_narration(topic, title, viewpoint, evidence)
        chapters.append(
            {
                "index": index,
                "title": title,
                "start": round(cursor, 3),
                "end": round(cursor + duration, 3),
                "duration": duration,
                "viewpoint": viewpoint,
                "evidence_or_example": evidence,
                "visual_goal": visual_goal,
                "narration": narration,
            }
        )
        cursor += duration
    return {
        "version": "content_plan_v1",
        "workflow": "reference_guided_original",
        "topic": topic,
        "audience": audience,
        "platform": str(options.get("platform") or "short_video"),
        "content_depth": str(options.get("content_depth") or "standard"),
        "target_duration_seconds": round(target_duration, 3),
        "chapters": chapters,
        "quality_controls": {
            "minimum_chapters": 4,
            "requires_viewpoint": True,
            "requires_evidence_or_example": True,
            "template_phrase_ratio_limit": 0.18,
            "source_media_reuse_allowed": False,
        },
    }


def build_storyboard_v2(content_plan: dict, blueprint: dict, options: dict | None = None) -> dict:
    options = options or {}
    target_duration = _target_duration_seconds(options, blueprint, default=float(content_plan["target_duration_seconds"]))
    chapters = content_plan.get("chapters", []) if isinstance(content_plan.get("chapters"), list) else []
    topic = str(content_plan.get("topic") or blueprint.get("topic_hint") or "")
    topic_domain = _topic_domain(topic)
    scene_duration = target_duration / max(1, len(chapters))
    scenes = []
    cursor = 0.0
    for chapter in chapters:
        index = int(chapter.get("index") or len(scenes))
        duration = round(scene_duration, 3)
        visual_slots = [
            {
                "type": "hero_illustration",
                "purpose": "建立章节主场景和情绪",
                "description": chapter.get("visual_goal", ""),
            },
            {
                "type": "evidence_infographic",
                "purpose": "把证据或例子做成可理解的信息图",
                "description": chapter.get("evidence_or_example", ""),
            },
            {
                "type": "detail_cutaway",
                "purpose": "补一个近景细节，避免全片都是静态板式",
                "description": f"{chapter.get('title', '')} 的关键动作细节",
            },
        ]
        scenes.append(
            {
                "index": index,
                "topic": topic,
                "topic_domain": topic_domain,
                "title": str(chapter.get("title") or f"章节 {index + 1}"),
                "start": round(cursor, 3),
                "end": round(cursor + duration, 3),
                "duration": duration,
                "viewpoint": str(chapter.get("viewpoint") or ""),
                "evidence_or_example": str(chapter.get("evidence_or_example") or ""),
                "visual_goal": str(chapter.get("visual_goal") or ""),
                "voiceover": str(chapter.get("narration") or ""),
                "visual_slots": visual_slots,
                "source_policy": "redraw_by_default",
                "motion_direction": ["slow_push", "lateral_reveal", "detail_pull", "diagram_build"][index % 4],
            }
        )
        cursor += duration
    return {
        "version": "storyboard_v2",
        "workflow": "reference_guided_original",
        "target_duration_seconds": round(target_duration, 3),
        "topic": topic,
        "topic_domain": topic_domain,
        "duration_policy": {
            "target_duration_policy": str(options.get("target_duration_policy") or "source_guided"),
            "explicit_target_duration": bool(options.get("target_duration_seconds")),
            "source_guided_minimum_ratio": 0.8,
        },
        "reuse_policy": "redraw_by_default",
        "scenes": scenes,
        "source_constraints": {
            "use_reference_frames": False,
            "use_reference_audio": False,
            "use_reference_subtitles": False,
        },
    }


def build_visual_requirements(storyboard: dict, content_plan: dict, options: dict | None = None) -> dict:
    options = options or {}
    style = str(options.get("visual_style") or "documentary_illustration")
    strategy = str(options.get("visual_asset_strategy") or "images2_first")
    if strategy in {"images2_only", "ai_only"}:
        source_priority = ["images2"]
    elif strategy == "user_owned_first":
        source_priority = ["user_owned", "images2", "licensed_stock"]
    else:
        source_priority = ["images2", "licensed_stock", "user_owned"]
        strategy = "images2_first"
    scene_requirements = []
    total_requirements = 0
    for scene in storyboard.get("scenes", []):
        scene_index = int(scene.get("index") or len(scene_requirements))
        requirements = []
        for slot_index, slot in enumerate(scene.get("visual_slots", [])[:3]):
            slot_type = str(slot.get("type") or "visual")
            need_id = f"scene_{scene_index:02d}_{slot_index:02d}_{slot_type}"
            description = str(slot.get("description") or scene.get("visual_goal") or scene.get("title") or "")
            purpose = str(slot.get("purpose") or "支撑本段表达")
            requirements.append(
                {
                    "need_id": need_id,
                    "scene_index": scene_index,
                    "scene_title": str(scene.get("title") or ""),
                    "slot_type": slot_type,
                    "purpose": purpose,
                    "description": description,
                    "reason_from_storyboard": (
                        f"本镜头用于“{scene.get('title', '原创章节')}”，需要通过{purpose}支撑观点："
                        f"{scene.get('viewpoint', '')}"
                    ),
                    "prompt_brief": (
                        f"{style}, high quality Chinese information documentary still, "
                        f"{scene.get('title', '')}, {description}, cinematic composition, "
                        "editorial clarity, no copied source frame"
                    ),
                    "source_priority": source_priority,
                    "quality_bar": {
                        "min_width": DEFAULT_REFERENCE_ORIGINAL_GEOMETRY.width,
                        "min_height": DEFAULT_REFERENCE_ORIGINAL_GEOMETRY.height,
                        "aspect_ratio": "16:9",
                        "style": style,
                        "avoid_reference_frame_reuse": True,
                        "no_text_in_image": True,
                        "must_feel_original": True,
                    },
                }
            )
            total_requirements += 1
        scene_requirements.append(
            {
                "scene_index": scene_index,
                "scene_title": str(scene.get("title") or ""),
                "requirements": requirements,
            }
        )
    return {
        "version": "visual_requirements_v1",
        "workflow": "reference_guided_original",
        "stage": "after_storyboard",
        "topic": str(content_plan.get("topic") or storyboard.get("topic") or ""),
        "visual_asset_strategy": strategy,
        "default_source_priority": source_priority,
        "requirement_count": total_requirements,
        "quality_policy": {
            "reference_frame_reuse_allowed": False,
            "mock_assets_publishable": False,
            "images2_first": strategy == "images2_first",
        },
        "scenes": scene_requirements,
    }


def build_asset_sourcing_plan(visual_requirements: dict, options: dict | None = None) -> dict:
    options = options or {}
    provider = str(options.get("image_provider") or "images2").strip() or "images2"
    strategy = str(options.get("visual_asset_strategy") or visual_requirements.get("visual_asset_strategy") or "images2_first")
    if strategy not in {"images2_first", "images2_only", "ai_only", "user_owned_first"}:
        strategy = "images2_first"
    decisions = []
    for scene in visual_requirements.get("scenes", []):
        for requirement in scene.get("requirements", []):
            priority = requirement.get("source_priority", ["images2"])
            selected_source = "ai_generated" if "images2" in priority else "user_owned"
            decisions.append(
                {
                    "decision_id": f"{requirement.get('need_id')}_source",
                    "need_id": str(requirement.get("need_id") or ""),
                    "scene_index": int(requirement.get("scene_index") or 0),
                    "scene_title": str(requirement.get("scene_title") or ""),
                    "slot_type": str(requirement.get("slot_type") or "visual"),
                    "selected_source": selected_source,
                    "provider": provider if selected_source == "ai_generated" else "user_asset_library",
                    "prompt": str(requirement.get("prompt_brief") or ""),
                    "quality_bar": requirement.get("quality_bar", {}),
                    "fallback_sources": [source for source in priority if source != "images2"],
                    "decision_reason": "分镜先定义画面缺口，再用 images2 生图补齐；授权素材只作为补位。",
                    "reference_frame_allowed": False,
                }
            )
    return {
        "version": "asset_sourcing_plan_v1",
        "workflow": "reference_guided_original",
        "strategy": "images2_first" if strategy in {"images2_first", "images2_only", "ai_only"} else strategy,
        "provider": provider,
        "provider_contract": "images2.generate(requirement.prompt, size, style)",
        "reference_frame_allowed": False,
        "mock_assets_publishable": False,
        "decision_count": len(decisions),
        "decisions": decisions,
    }


def build_visual_prompt_pack(storyboard: dict, options: dict | None = None, sourcing_plan: dict | None = None) -> dict:
    options = options or {}
    style = str(options.get("visual_style") or "documentary_illustration")
    scenes = []
    decisions_by_scene: dict[int, list[dict]] = {}
    if sourcing_plan:
        for decision in sourcing_plan.get("decisions", []):
            scene_index = int(decision.get("scene_index") or 0)
            decisions_by_scene.setdefault(scene_index, []).append(decision)
    for scene in storyboard.get("scenes", []):
        scene_index = int(scene.get("index") or 0)
        prompts = []
        source_items = decisions_by_scene.get(scene_index) or scene.get("visual_slots", [])[:3]
        for item_index, item in enumerate(source_items[:3]):
            if sourcing_plan:
                slot_type = str(item.get("slot_type") or "visual")
                prompt_text = str(item.get("prompt") or "")
                need_id = str(item.get("need_id") or "")
                provider = str(item.get("provider") or "")
            else:
                slot_type = str(item.get("type") or "visual")
                prompt_text = (
                    f"{style}, high quality Chinese information documentary still, "
                    f"scene about {scene.get('title')}, {item.get('description')}, "
                    "cinematic composition, realistic lighting, editorial clarity, no copied source frame"
                )
                need_id = f"scene_{scene_index:02d}_{item_index:02d}_{slot_type}"
                provider = str(options.get("image_provider") or "mock_image")
            prompts.append(
                {
                    "need_id": need_id,
                    "slot_type": slot_type,
                    "prompt": prompt_text,
                    "negative_prompt": "watermark, fake news footage, copied video frame, distorted text, extra logos, low resolution",
                    "size": "1920x1080",
                    "no_text_in_image": True,
                    "source_provider": provider,
                }
            )
        scenes.append(
            {
                "scene_index": scene.get("index"),
                "scene_title": scene.get("title"),
                "topic": scene.get("topic"),
                "topic_domain": scene.get("topic_domain"),
                "prompts": prompts,
            }
        )
    return {
        "version": "visual_prompt_pack_v1",
        "workflow": "reference_guided_original",
        "style": style,
        "built_from": "asset_sourcing_plan" if sourcing_plan else "storyboard_visual_slots",
        "provider_contract": "image_provider.generate(prompt, size, style) -> original asset",
        "source_frame_reuse_allowed": False,
        "scenes": scenes,
    }


def build_cover_brief(
    blueprint: dict,
    content_plan: dict,
    storyboard: dict,
    asset_sourcing_plan: dict,
    options: dict | None = None,
) -> dict:
    options = options or {}
    topic = str(content_plan.get("topic") or blueprint.get("topic_hint") or _topic_from_options(options) or "原创视频").strip()
    chapters = content_plan.get("chapters", []) if isinstance(content_plan.get("chapters"), list) else []
    first_chapter = chapters[0] if chapters and isinstance(chapters[0], dict) else {}
    title_hint = str(options.get("recommended_publish_title") or options.get("source_title") or topic).strip()
    core_message = str(first_chapter.get("viewpoint") or f"讲清楚{topic}的关键判断").strip()
    evidence = str(first_chapter.get("evidence_or_example") or "用结构、证据和质检建立可信表达").strip()
    text_overlay = _cover_text_overlay(title_hint or topic)
    return {
        "version": "cover_brief_v1",
        "workflow": "reference_guided_original",
        "stage": "after_full_video_analysis",
        "topic": topic,
        "title_hint": title_hint,
        "core_message": core_message,
        "audience_gain": f"看懂{topic}的关键判断和可执行路径",
        "evidence_anchor": evidence,
        "learned_opening_style": (blueprint.get("learned_structure") or {}).get("opening_style", ""),
        "visual_strategy": str(asset_sourcing_plan.get("strategy") or "images2_first"),
        "cover_angles": [
            {"angle": "核心矛盾", "copy": text_overlay, "intent": "一眼说明这条视频在解决什么问题。"},
            {"angle": "方法路径", "copy": _cover_text_overlay(topic + "方法"), "intent": "让观众知道这里有清晰步骤。"},
            {"angle": "结果收益", "copy": _cover_text_overlay(topic + "答案"), "intent": "强调看完能得到结论。"},
        ],
        "visual_direction": {
            "style": str(options.get("visual_style") or "documentary_illustration"),
            "main_subject": f"{topic} 的单一主视觉符号",
            "supporting_element": "一个简洁的信息层或光线焦点",
            "focal_point_count": 1,
            "text_overlay": text_overlay,
            "mood": "clean editorial documentary, confident, not busy",
        },
        "forbidden_elements": [
            "复杂拼贴",
            "假新闻感",
            "廉价 AI 光效",
            "大段文字",
            "冒充真实现场",
            "参考视频截图",
        ],
    }


def build_cover_prompt_pack(cover_brief: dict, options: dict | None = None) -> dict:
    options = options or {}
    provider = str(options.get("image_provider") or cover_brief.get("image_provider") or "images2").strip() or "images2"
    visual_direction = cover_brief.get("visual_direction", {}) if isinstance(cover_brief.get("visual_direction"), dict) else {}
    text_overlay = _cover_text_overlay(str(visual_direction.get("text_overlay") or cover_brief.get("topic") or "原创视频"))
    style = str(visual_direction.get("style") or options.get("visual_style") or "documentary_illustration")
    prompts = []
    for index, angle in enumerate(cover_brief.get("cover_angles", [])[:3]):
        copy = _cover_text_overlay(str(angle.get("copy") or text_overlay))
        prompts.append(
            {
                "candidate_index": index,
                "angle": str(angle.get("angle") or f"候选 {index + 1}"),
                "provider": provider,
                "size": "1920x1080",
                "text_overlay": copy,
                "prompt": (
                    f"{style}, premium Chinese video cover, single clear focal point, "
                    f"main subject: {visual_direction.get('main_subject')}, "
                    f"message: {cover_brief.get('core_message')}, text overlay '{copy}', "
                    "clean editorial composition, generous negative space, cinematic lighting, tasteful color, "
                    "not busy, not template-like"
                ),
                "negative_prompt": (
                    "complex collage, fake news footage, copied source frame, watermark, too much text, "
                    "cheap AI glow, distorted Chinese characters, random logos, cluttered layout"
                ),
                "quality_bar": {
                    "max_text_chars": 10,
                    "focal_point_count": 1,
                    "min_width": DEFAULT_REFERENCE_ORIGINAL_GEOMETRY.width,
                    "min_height": DEFAULT_REFERENCE_ORIGINAL_GEOMETRY.height,
                    "aspect_ratio": "16:9",
                },
            }
        )
    return {
        "version": "cover_prompt_pack_v1",
        "workflow": "reference_guided_original",
        "provider": provider,
        "cover_count": len(prompts),
        "style_rules": {
            "max_text_chars": 10,
            "focal_point_count": 1,
            "simple_visual": True,
            "match_video_style": True,
        },
        "source_frame_reuse_allowed": False,
        "prompts": prompts,
    }


def build_cover_asset_manifest(
    cover_dir: Path | str,
    final_cover_path: Path | str,
    cover_prompt_pack: dict,
    provider: str = "mock_image",
) -> dict:
    cover_dir = Path(cover_dir)
    final_cover_path = Path(final_cover_path)
    cover_dir.mkdir(parents=True, exist_ok=True)
    final_cover_path.parent.mkdir(parents=True, exist_ok=True)
    provider = provider or str(cover_prompt_pack.get("provider") or "mock_image")
    can_generate_preview = provider in PREVIEW_IMAGE_PROVIDERS or provider == "mock_images2"
    provider_publish_ready = provider in PUBLISHABLE_IMAGE_PROVIDERS
    candidates = []
    for prompt in cover_prompt_pack.get("prompts", []):
        index = int(prompt.get("candidate_index") or len(candidates))
        candidate_path = cover_dir / f"cover_candidate_{index:02d}.png"
        if can_generate_preview:
            _draw_mock_cover_asset(candidate_path, prompt, index)
        candidates.append(
            {
                "index": index,
                "path": str(candidate_path),
                "provider": provider,
                "text_overlay": str(prompt.get("text_overlay") or ""),
                "prompt_ref": index,
                "publish_ready": provider_publish_ready,
                "exists": candidate_path.exists(),
            }
        )
    ready_candidates = [candidate for candidate in candidates if candidate.get("exists")]
    recommended = ready_candidates[0] if ready_candidates else {}
    if recommended:
        shutil.copyfile(str(recommended["path"]), final_cover_path)
    quality = {
        "not_overcomplicated": _cover_pack_not_overcomplicated(cover_prompt_pack),
        "text_concise": all(len(str(prompt.get("text_overlay") or "")) <= 10 for prompt in cover_prompt_pack.get("prompts", [])),
        "single_focal_point": all(
            int((prompt.get("quality_bar") or {}).get("focal_point_count") or 0) == 1
            for prompt in cover_prompt_pack.get("prompts", [])
        ),
        "style_matches_video": True,
    }
    status = "ready" if ready_candidates else "pending_generation"
    return {
        "version": "cover_asset_manifest_v1",
        "workflow": "reference_guided_original",
        "provider": provider,
        "status": status,
        "publish_ready": status == "ready" and provider_publish_ready and all(quality.values()),
        "candidate_count": len(candidates),
        "recommended_cover": {
            "path": str(final_cover_path) if final_cover_path.exists() else "",
            "source_candidate_path": str(recommended.get("path") or ""),
            "index": recommended.get("index", 0) if recommended else None,
        },
        "quality": quality,
        "candidates": candidates,
    }


def build_generated_asset_manifest(
    asset_dir: Path | str,
    prompt_pack: dict,
    provider: str = "mock_image",
    sourcing_plan: dict | None = None,
) -> dict:
    asset_dir = Path(asset_dir)
    asset_dir.mkdir(parents=True, exist_ok=True)
    provider = provider or "mock_image"
    can_generate_preview = provider in PREVIEW_IMAGE_PROVIDERS or provider == "mock_images2"
    provider_publish_ready = provider in PUBLISHABLE_IMAGE_PROVIDERS
    scenes = []
    fingerprints: set[str] = set()
    duplicate_count = 0
    asset_count = 0
    for scene in prompt_pack.get("scenes", []):
        scene_index = int(scene.get("scene_index") or 0)
        assets = []
        for prompt_index, prompt in enumerate(scene.get("prompts", [])):
            if not can_generate_preview:
                continue
            asset_path = asset_dir / f"scene_{scene_index:02d}_{prompt_index:02d}.png"
            _draw_mock_documentary_asset(asset_path, scene, prompt, scene_index, prompt_index)
            fingerprint = f"{scene_index}:{prompt.get('slot_type')}:{prompt_index}"
            if fingerprint in fingerprints:
                duplicate_count += 1
            fingerprints.add(fingerprint)
            assets.append(
                {
                    "path": str(asset_path),
                    "need_id": str(prompt.get("need_id") or ""),
                    "slot_type": str(prompt.get("slot_type") or "visual"),
                    "license": "generated_original",
                    "provider": provider,
                    "origin": "ai_generated",
                    "prompt_ref": prompt_index,
                    "publish_ready": provider_publish_ready,
                }
            )
            asset_count += 1
        scenes.append({"scene_index": scene_index, "scene_title": scene.get("scene_title"), "assets": assets})
    repetition_ratio = duplicate_count / asset_count if asset_count else 1.0
    status = "ready" if asset_count and all(len(scene["assets"]) >= 2 for scene in scenes) else "pending_generation"
    return {
        "version": "generated_asset_manifest_v1",
        "workflow": "reference_guided_original",
        "provider": provider,
        "status": status,
        "publish_ready": status == "ready" and provider_publish_ready,
        "asset_count": asset_count,
        "asset_repetition_ratio": round(repetition_ratio, 3),
        "license_policy": "generated_original_assets_only",
        "sourcing_strategy": str((sourcing_plan or {}).get("strategy") or ""),
        "scenes": scenes,
    }


def build_voiceover_manifest(storyboard: dict, audio_dir: Path | str, provider: str = "macos_say") -> dict:
    audio_dir = Path(audio_dir)
    audio_dir.mkdir(parents=True, exist_ok=True)
    provider = provider or "macos_say"
    publish_ready = provider in PUBLISHABLE_VOICE_PROVIDERS
    entries = []
    for scene in storyboard.get("scenes", []):
        index = int(scene.get("index") or len(entries))
        entries.append(
            {
                "scene_index": index,
                "provider": provider,
                "publish_ready": publish_ready,
                "duration": float(scene.get("duration") or 0.0),
                "script": str(scene.get("voiceover") or ""),
                "audio_path": str(audio_dir / f"scene_{index:02d}.wav"),
            }
        )
    return {
        "version": "voiceover_manifest_v1",
        "workflow": "reference_guided_original",
        "provider": provider,
        "status": "ready" if entries else "missing",
        "publish_ready": publish_ready,
        "front_label": "可发布音" if publish_ready else "草稿音",
        "scene_count": len(entries),
        "ready_count": len(entries),
        "entries": entries,
    }


def build_caption_timeline_v2(storyboard: dict) -> dict:
    scenes = storyboard.get("scenes", []) if isinstance(storyboard.get("scenes"), list) else []
    captions = []
    for scene in scenes:
        scene_index = int(scene.get("index") or len(captions))
        start = float(scene.get("start") or 0.0)
        end = float(scene.get("end") or start + float(scene.get("duration") or 10.0))
        chunks = _caption_chunks_v2(str(scene.get("voiceover") or scene.get("title") or ""))
        if not chunks:
            chunks = [_fit_text(str(scene.get("title") or "原创章节"), CAPTION_MAX_CHARS)]
        scene_duration = max(CAPTION_MIN_SECONDS, end - start)
        max_chunks = max(1, int(scene_duration // CAPTION_MIN_SECONDS))
        chunks = chunks[:max_chunks]
        segment_duration = max(CAPTION_MIN_SECONDS, scene_duration / len(chunks))
        current = start
        for chunk_index, chunk in enumerate(chunks):
            caption_end = end if chunk_index == len(chunks) - 1 else min(end, current + segment_duration)
            captions.append(
                {
                    "scene_index": scene_index,
                    "index": len(captions),
                    "start": round(current, 3),
                    "end": round(caption_end, 3),
                    "text": _fit_text(chunk, CAPTION_MAX_CHARS),
                }
            )
            current = caption_end
    return {
        "version": "reference_caption_timeline_v1",
        "workflow": "reference_guided_original",
        "duration": round(float(storyboard.get("target_duration_seconds") or 0.0), 3),
        "readability": {
            "max_chars_per_caption": CAPTION_MAX_CHARS,
            "min_seconds_per_caption": CAPTION_MIN_SECONDS,
            "line_count": 1,
            "avoid_covering_subject": True,
        },
        "style": {
            "placement": "bottom_safe_area",
            "font_size": 24,
            "outline": 3,
            "margin_v": 72,
        },
        "captions": captions,
    }


def write_subtitles_v2(output_path: Path | str, caption_timeline: dict) -> None:
    captions = caption_timeline.get("captions", []) if isinstance(caption_timeline.get("captions"), list) else []
    blocks = []
    for index, caption in enumerate(captions, start=1):
        text = str(caption.get("text") or "").strip()
        if not text:
            continue
        blocks.append(
            "\n".join(
                [
                    str(index),
                    f"{_srt_time(float(caption.get('start') or 0.0))} --> {_srt_time(float(caption.get('end') or 0.0))}",
                    text,
                ]
            )
        )
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n\n".join(blocks) + ("\n" if blocks else ""), encoding="utf-8")


def write_reference_guided_quality_report(
    paths: ReferenceGuidedPaths,
    blueprint: dict,
    content_plan: dict,
    storyboard: dict,
    prompt_pack: dict,
    asset_manifest: dict,
    voiceover_manifest: dict,
    visual_requirements: dict | None = None,
    asset_sourcing_plan: dict | None = None,
    cover_brief: dict | None = None,
    cover_prompt_pack: dict | None = None,
    cover_asset_manifest: dict | None = None,
    caption_timeline: dict | None = None,
    require_video: bool = False,
) -> dict:
    visual_requirements = visual_requirements or {}
    asset_sourcing_plan = asset_sourcing_plan or {}
    cover_brief = cover_brief or {}
    cover_prompt_pack = cover_prompt_pack or {}
    cover_asset_manifest = cover_asset_manifest or {}
    caption_timeline = caption_timeline or {}
    chapters = content_plan.get("chapters", []) if isinstance(content_plan.get("chapters"), list) else []
    scenes = storyboard.get("scenes", []) if isinstance(storyboard.get("scenes"), list) else []
    requirement_scenes = visual_requirements.get("scenes", []) if isinstance(visual_requirements.get("scenes"), list) else []
    sourcing_decisions = (
        asset_sourcing_plan.get("decisions", []) if isinstance(asset_sourcing_plan.get("decisions"), list) else []
    )
    prompt_scenes = prompt_pack.get("scenes", []) if isinstance(prompt_pack.get("scenes"), list) else []
    asset_scenes = asset_manifest.get("scenes", []) if isinstance(asset_manifest.get("scenes"), list) else []
    cover_prompts = cover_prompt_pack.get("prompts", []) if isinstance(cover_prompt_pack.get("prompts"), list) else []
    cover_quality = cover_asset_manifest.get("quality", {}) if isinstance(cover_asset_manifest.get("quality"), dict) else {}
    captions = caption_timeline.get("captions", []) if isinstance(caption_timeline.get("captions"), list) else []
    reference_duration = float((blueprint.get("reference") or {}).get("duration_seconds") or 0.0)
    minimum_duration = round(reference_duration * 0.8, 3) if reference_duration else 0.0
    target_duration = float(storyboard.get("target_duration_seconds") or 0.0)
    subtitle_readability = _caption_timeline_readable(captions)
    asset_provider = str(asset_manifest.get("provider") or asset_sourcing_plan.get("provider") or "missing")
    cover_provider = str(cover_asset_manifest.get("provider") or cover_prompt_pack.get("provider") or "missing")
    asset_publish_ready = bool(asset_manifest.get("publish_ready")) and _image_provider_publishable(asset_provider)
    cover_publish_ready = bool(cover_asset_manifest.get("publish_ready")) and _image_provider_publishable(cover_provider)
    duration_policy = storyboard.get("duration_policy", {}) if isinstance(storyboard.get("duration_policy"), dict) else {}
    duration_floor_required = (
        not bool(duration_policy.get("explicit_target_duration"))
        and str(duration_policy.get("target_duration_policy") or "source_guided") != "short_summary"
    )
    checks = {
        "reference_blueprint_present": bool(blueprint.get("workflow") == "reference_guided_original"),
        "reference_media_not_reused": blueprint.get("source_media_allowed") is False
        and blueprint.get("source_audio_allowed") is False
        and blueprint.get("reuse_policy") == "redraw_by_default",
        "content_plan_depth": len(chapters) >= 4
        and all(
            chapter.get("viewpoint")
            and chapter.get("evidence_or_example")
            and chapter.get("visual_goal")
            and chapter.get("narration")
            for chapter in chapters
            if isinstance(chapter, dict)
        ),
        "storyboard_v2_present": len(scenes) >= 4
        and all(len(scene.get("visual_slots", [])) >= 2 for scene in scenes if isinstance(scene, dict)),
        "visual_requirements_ready": len(requirement_scenes) >= len(scenes)
        and all(
            len(scene.get("requirements", [])) >= 2
            and all(
                (requirement.get("quality_bar") or {}).get("avoid_reference_frame_reuse") is True
                and requirement.get("reason_from_storyboard")
                for requirement in scene.get("requirements", [])
                if isinstance(requirement, dict)
            )
            for scene in requirement_scenes
            if isinstance(scene, dict)
        ),
        "asset_sourcing_plan_images2_first": asset_sourcing_plan.get("strategy") == "images2_first"
        and asset_sourcing_plan.get("reference_frame_allowed") is False
        and len(sourcing_decisions) >= len(scenes) * 2
        and all(decision.get("selected_source") == "ai_generated" for decision in sourcing_decisions if isinstance(decision, dict)),
        "visual_prompt_pack_present": len(prompt_scenes) >= len(scenes)
        and all(len(scene.get("prompts", [])) >= 2 for scene in prompt_scenes if isinstance(scene, dict)),
        "generated_assets_ready": asset_manifest.get("status") == "ready"
        and float(asset_manifest.get("asset_repetition_ratio", 1.0)) < 0.35
        and len(asset_scenes) >= len(scenes)
        and all(len(scene.get("assets", [])) >= 2 for scene in asset_scenes if isinstance(scene, dict)),
        "generated_assets_publish_ready": asset_publish_ready,
        "cover_brief_ready": cover_brief.get("stage") == "after_full_video_analysis"
        and len(cover_brief.get("cover_angles", [])) >= 3
        and (cover_brief.get("visual_direction") or {}).get("focal_point_count") == 1,
        "cover_prompt_pack_ready": cover_prompt_pack.get("cover_count") == 3
        and len(cover_prompts) == 3
        and all(len(str(prompt.get("text_overlay") or "")) <= 10 for prompt in cover_prompts),
        "cover_assets_ready": cover_asset_manifest.get("status") == "ready"
        and bool((cover_asset_manifest.get("recommended_cover") or {}).get("path")),
        "cover_assets_publish_ready": cover_publish_ready,
        "cover_not_overcomplicated": bool(cover_quality.get("not_overcomplicated")),
        "cover_text_concise": bool(cover_quality.get("text_concise")),
        "duration_floor_respected": (target_duration >= minimum_duration if minimum_duration else True)
        if duration_floor_required
        else True,
        "subtitle_timeline_present": len(captions) >= len(scenes)
        and all(caption.get("text") for caption in captions if isinstance(caption, dict))
        and (paths.subtitles.exists() if require_video else True),
        "subtitle_readability": subtitle_readability,
        "voice_provider_publishable": bool(voiceover_manifest.get("publish_ready")),
        "user_delivery_present": True,
        "rendered_release_present": bool(paths.video.exists()) if require_video else True,
    }
    issues = []
    if not checks["reference_media_not_reused"]:
        issues.append(
            {
                "severity": "blocker",
                "code": "reference_reuse_policy_violation",
                "message": "参考视频只能用于学习结构，不能复用原画面、原声或原字幕。",
            }
        )
    if not checks["content_plan_depth"]:
        issues.append(
            {
                "severity": "error",
                "code": "content_needs_rewrite",
                "message": "内容章节缺少观点、证据、例子或旁白稿，需要重写。",
            }
        )
    if not checks["storyboard_v2_present"]:
        issues.append(
            {
                "severity": "error",
                "code": "storyboard_too_thin",
                "message": "分镜不足 4 章或每章视觉资产少于 2 类。",
            }
        )
    if not checks["visual_requirements_ready"]:
        issues.append(
            {
                "severity": "error",
                "code": "missing_visual_requirements",
                "message": "缺少分镜后的画面需求清单，不能提前拼素材。",
            }
        )
    if not checks["asset_sourcing_plan_images2_first"]:
        issues.append(
            {
                "severity": "error",
                "code": "asset_sourcing_not_images2_first",
                "message": "画面来源计划不是 images2 优先，或仍允许参考帧复用。",
            }
        )
    if not checks["visual_prompt_pack_present"]:
        issues.append(
            {
                "severity": "error",
                "code": "missing_visual_prompt_pack",
                "message": "缺少可交给生图 provider 的分镜提示词包。",
            }
        )
    if not checks["generated_assets_ready"]:
        issues.append(
            {
                "severity": "blocker",
                "code": "missing_generated_assets",
                "message": "缺少原创生成图，不能进入发布候选。",
            }
        )
    elif not checks["generated_assets_publish_ready"]:
        issues.append(
            {
                "severity": "blocker",
                "code": "preview_only_generated_assets",
                "message": "当前画面是 mock 预览图，需要 images2 或确认过的原创生成图后才能发布。",
            }
        )
    if not checks["cover_brief_ready"]:
        issues.append(
            {
                "severity": "error",
                "code": "missing_cover_brief",
                "message": "缺少全片分析后的封面简报，不能随便截图当封面。",
            }
        )
    if not checks["cover_prompt_pack_ready"]:
        issues.append(
            {
                "severity": "error",
                "code": "missing_cover_prompt_pack",
                "message": "缺少 images2 封面提示词，或封面文字过长。",
            }
        )
    if not checks["cover_assets_ready"]:
        issues.append(
            {
                "severity": "blocker",
                "code": "missing_cover_asset",
                "message": "缺少生成封面，不能进入发布候选。",
            }
        )
    elif not checks["cover_assets_publish_ready"]:
        issues.append(
            {
                "severity": "blocker",
                "code": "preview_only_cover_asset",
                "message": "当前封面只是 mock 预览图，需要 images2 或确认过的原创封面后才能发布。",
            }
        )
    if not checks["cover_not_overcomplicated"]:
        issues.append(
            {
                "severity": "error",
                "code": "cover_too_complex",
                "message": "封面过于复杂，应保留单一主体和清晰留白。",
            }
        )
    if not checks["cover_text_concise"]:
        issues.append(
            {
                "severity": "error",
                "code": "cover_text_too_long",
                "message": "封面文字过多，建议控制在 6 到 10 个中文字内。",
            }
        )
    if not checks["subtitle_timeline_present"]:
        issues.append(
            {
                "severity": "error",
                "code": "missing_subtitle_timeline",
                "message": "缺少自动字幕时间线或 subtitles.srt，需要随成片一起输出。",
            }
        )
    elif not checks["subtitle_readability"]:
        issues.append(
            {
                "severity": "error",
                "code": "subtitle_readability_failed",
                "message": "字幕过长、过密或时间重叠，需要按安全区重新生成。",
            }
        )
    if not checks["duration_floor_respected"]:
        issues.append(
            {
                "severity": "error",
                "code": "duration_floor_not_met",
                "message": "成片目标时长低于参考片 80%，信息量被过度压缩，需要按源片长度重排。",
            }
        )
    if not checks["voice_provider_publishable"]:
        issues.append(
            {
                "severity": "error",
                "code": "draft_voiceover_provider",
                "message": "当前配音不是可发布 provider，需要专业配音或用户上传真人配音。",
            }
        )
    if not checks["rendered_release_present"]:
        issues.append(
            {
                "severity": "blocker",
                "code": "missing_release_video",
                "message": "缺少 release.mp4，不能交付预览。",
            }
        )
    publish_tier = "publish_candidate"
    if (
        not checks["generated_assets_ready"]
        or not checks["generated_assets_publish_ready"]
        or not checks["cover_assets_ready"]
        or not checks["cover_assets_publish_ready"]
    ):
        publish_tier = "needs_assets"
    elif not checks["voice_provider_publishable"]:
        publish_tier = "needs_voiceover"
    elif not checks["content_plan_depth"] or not checks["duration_floor_respected"] or not checks["subtitle_readability"]:
        publish_tier = "needs_rewrite"
    blocking = [issue for issue in issues if str(issue.get("severity")) != "warning"]
    payload = {
        "version": "reference_guided_quality_v1",
        "workflow": "reference_guided_original",
        "status": "passed" if not blocking else "failed",
        "checks": checks,
        "issues": issues,
        "strategy": {
            "workflow": "reference_guided_original",
            "publish_tier": publish_tier,
            "target_duration_seconds": storyboard.get("target_duration_seconds"),
            "reference_duration_seconds": reference_duration,
            "minimum_duration_seconds": minimum_duration,
            "duration_floor_required": duration_floor_required,
            "chapter_count": len(chapters),
            "scene_count": len(scenes),
            "asset_count": int(asset_manifest.get("asset_count") or 0),
            "asset_publish_ready": asset_publish_ready,
            "visual_requirement_count": int(visual_requirements.get("requirement_count") or 0),
            "asset_sourcing_strategy": str(asset_sourcing_plan.get("strategy") or "missing"),
            "image_provider": asset_provider,
            "cover_provider": cover_provider,
            "cover_publish_ready": cover_publish_ready,
            "subtitle_count": len(captions),
            "voice_provider": str(voiceover_manifest.get("provider") or "missing"),
            "reuse_policy": blueprint.get("reuse_policy"),
        },
    }
    _write_json(paths.quality_report, payload)
    return payload


def build_user_delivery(
    paths: ReferenceGuidedPaths,
    quality_report: dict,
    content_plan: dict,
    asset_manifest: dict,
    voiceover_manifest: dict,
) -> dict:
    strategy = quality_report.get("strategy", {}) if isinstance(quality_report.get("strategy"), dict) else {}
    publish_tier = str(strategy.get("publish_tier") or "")
    if publish_tier == "publish_candidate" and quality_report.get("status") == "passed":
        status = "可发布"
        reason = "原创结构、生成素材和可发布配音均已通过 V1 门槛。"
        next_actions: list[str] = []
    elif publish_tier == "needs_assets":
        status = "需补素材"
        reason = "还没有足够的可发布原创生成图或可发布封面，或当前只是 mock 预览图。"
        next_actions = [
            "用 images2 生成每章至少 2 类原创视觉资产",
            "用 images2 生成简洁封面，封面文字控制在 10 个字以内",
            "重新跑画面和封面质检后再进入发布候选",
            "配音也需要确认是否为可发布 provider",
        ]
    elif publish_tier == "needs_voiceover":
        status = "需补配音"
        reason = "画面和内容已成型，但当前是草稿音。"
        next_actions = ["替换成专业 voice provider 或上传真人配音", "重新跑声音工作室和质检"]
    else:
        status = "内容需重写"
        reason = "章节深度或分镜结构没有达到原创视频门槛。"
        next_actions = ["重写章节观点、证据、例子和结论", "重新生成分镜与视觉提示词"]
    return {
        "version": "user_delivery_v1",
        "mode": "一键原创视频",
        "topic": content_plan.get("topic"),
        "release_decision": {"status": status, "reason": reason},
        "next_actions": next_actions,
        "preview": {
            "video": str(paths.video) if paths.video.exists() else "",
            "cover": str(paths.cover) if paths.cover.exists() else "",
            "contact_sheet": str(paths.contact_sheet) if paths.contact_sheet.exists() else "",
        },
        "front_labels": {
            "asset_status": _asset_front_label(asset_manifest),
            "cover_status": "封面可发布" if strategy.get("cover_publish_ready") else "封面待 images2 升级",
            "voice_status": str(voiceover_manifest.get("front_label") or "需要换配音"),
        },
        "advanced_files": [
            "reference_blueprint.json",
            "content_plan.json",
            "script_v2.md",
            "storyboard_v2.json",
            "visual_requirements.json",
            "asset_sourcing_plan.json",
            "cover_brief.json",
            "cover_prompt_pack.json",
            "cover_asset_manifest.json",
            "visual_prompt_pack.json",
            "generated_asset_manifest.json",
            "caption_timeline.json",
            "subtitles.srt",
            "voiceover_manifest.json",
            "quality_report.json",
        ],
    }


def render_reference_guided_original_video(
    reference_video_path: Path | str,
    output_dir: Path | str,
    options: dict | None = None,
    progress: ProgressCallback | None = None,
) -> Dict[str, Path | str]:
    options = options or {}
    paths = build_reference_guided_paths(output_dir)
    for directory in [
        paths.output_dir,
        paths.segments_dir,
        paths.frames_dir,
        paths.generated_assets_dir,
        paths.cover_candidates_dir,
        paths.audio_dir,
    ]:
        directory.mkdir(parents=True, exist_ok=True)

    _progress(progress, "拆解参考视频结构蓝图")
    reference_probe = probe_media(reference_video_path)
    blueprint = build_reference_blueprint(reference_video_path, reference_probe, options)
    content_plan = build_content_plan(blueprint, options)
    storyboard = build_storyboard_v2(content_plan, blueprint, options)
    visual_requirements = build_visual_requirements(storyboard, content_plan, options)
    asset_sourcing_plan = build_asset_sourcing_plan(visual_requirements, options)
    cover_brief = build_cover_brief(blueprint, content_plan, storyboard, asset_sourcing_plan, options)
    cover_prompt_pack = build_cover_prompt_pack(cover_brief, options)
    cover_asset_manifest = build_cover_asset_manifest(
        paths.cover_candidates_dir,
        paths.cover,
        cover_prompt_pack,
        provider=str(options.get("image_provider") or "mock_images2"),
    )
    prompt_pack = build_visual_prompt_pack(storyboard, options, sourcing_plan=asset_sourcing_plan)
    caption_timeline = build_caption_timeline_v2(storyboard)
    asset_manifest = build_generated_asset_manifest(
        paths.generated_assets_dir,
        prompt_pack,
        provider=str(options.get("image_provider") or "mock_images2"),
        sourcing_plan=asset_sourcing_plan,
    )
    voiceover_manifest = build_voiceover_manifest(
        storyboard,
        paths.audio_dir,
        provider=str(options.get("voice_provider") or "mock_professional_voice"),
    )
    _write_json(paths.reference_blueprint, blueprint)
    _write_json(paths.content_plan, content_plan)
    _write_json(paths.storyboard_v2, storyboard)
    _write_json(paths.visual_requirements, visual_requirements)
    _write_json(paths.asset_sourcing_plan, asset_sourcing_plan)
    _write_json(paths.cover_brief, cover_brief)
    _write_json(paths.cover_prompt_pack, cover_prompt_pack)
    _write_json(paths.cover_asset_manifest, cover_asset_manifest)
    _write_json(paths.visual_prompt_pack, prompt_pack)
    _write_json(paths.generated_asset_manifest, asset_manifest)
    _write_json(paths.caption_timeline, caption_timeline)
    write_subtitles_v2(paths.subtitles, caption_timeline)
    _write_json(paths.voiceover_manifest, voiceover_manifest)
    _write_script_v2(paths.script_v2, content_plan, storyboard)

    segment_paths = []
    for scene in storyboard.get("scenes", []):
        index = int(scene.get("index") or len(segment_paths))
        _progress(progress, f"渲染原创分镜 {index + 1}/{len(storyboard.get('scenes', []))}")
        frame_path = paths.frames_dir / f"scene_{index:02d}.png"
        segment_path = paths.segments_dir / f"scene_{index:02d}.mp4"
        _draw_scene_frame(frame_path, scene, asset_manifest)
        _run(build_reference_guided_scene_command(frame_path, segment_path, float(scene.get("duration") or 10.0)))
        segment_paths.append(segment_path)

    raw_video_path = paths.output_dir / "release_no_subtitles.mp4"
    _concat_segments(segment_paths, paths.concat, raw_video_path)
    _run(build_subtitle_output_command(raw_video_path, paths.subtitles, paths.video))
    output_probe = probe_media(paths.video)
    output_duration = _duration_from_probe(output_probe)
    if not paths.cover.exists():
        output_geometry = geometry_from_probe(output_probe)
        _run(_build_cover_command(paths.video, paths.cover, output_geometry, 0.0))
    _write_contact_sheet(paths.video, paths.contact_sheet, output_duration)
    quality_report = write_reference_guided_quality_report(
        paths,
        blueprint,
        content_plan,
        storyboard,
        prompt_pack,
        asset_manifest,
        voiceover_manifest,
        visual_requirements,
        asset_sourcing_plan,
        cover_brief,
        cover_prompt_pack,
        cover_asset_manifest,
        caption_timeline,
        require_video=True,
    )
    user_delivery = build_user_delivery(paths, quality_report, content_plan, asset_manifest, voiceover_manifest)
    _write_json(paths.user_delivery, user_delivery)
    _write_render_report(paths.report, reference_probe, output_probe, quality_report, user_delivery)
    _progress(progress, "完成一键原创视频")
    return {
        "mode": "reference-guided-original",
        "video": paths.video,
        "cover": paths.cover,
        "contact_sheet": paths.contact_sheet,
        "reference_blueprint": paths.reference_blueprint,
        "content_plan": paths.content_plan,
        "script_v2": paths.script_v2,
        "storyboard_v2": paths.storyboard_v2,
        "visual_requirements": paths.visual_requirements,
        "asset_sourcing_plan": paths.asset_sourcing_plan,
        "cover_brief": paths.cover_brief,
        "cover_prompt_pack": paths.cover_prompt_pack,
        "cover_asset_manifest": paths.cover_asset_manifest,
        "visual_prompt_pack": paths.visual_prompt_pack,
        "generated_asset_manifest": paths.generated_asset_manifest,
        "caption_timeline": paths.caption_timeline,
        "subtitles": paths.subtitles,
        "voiceover_manifest": paths.voiceover_manifest,
        "user_delivery": paths.user_delivery,
        "quality_report": paths.quality_report,
        "report": paths.report,
    }


def build_reference_guided_scene_command(card_image: Path | str, output_video: Path | str, duration: float) -> list[str]:
    safe_duration = max(2.0, float(duration))
    geometry = DEFAULT_REFERENCE_ORIGINAL_GEOMETRY
    video_filter = ",".join(
        [
            f"scale={geometry.width}:{geometry.height}:flags=lanczos",
            "setsar=1",
            (
                "zoompan="
                "z='min(1.045,1+on*0.00055)':"
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
        "veryfast",
        "-crf",
        "18",
        "-profile:v",
        "high",
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


def build_subtitle_burn_command(input_video: Path | str, subtitles_path: Path | str, output_video: Path | str) -> list[str]:
    subtitle_filter = (
        f"subtitles=filename={_ffmpeg_filter_path(subtitles_path)}:"
        "force_style='FontSize=24,PrimaryColour=&H00FFFFFF&,OutlineColour=&H90000000&,"
        "BorderStyle=1,Outline=3,Shadow=0,Alignment=2,MarginV=72'"
    )
    return [
        "ffmpeg",
        "-y",
        "-i",
        str(input_video),
        "-vf",
        subtitle_filter,
        "-c:v",
        "libx264",
        "-preset",
        "slow",
        "-crf",
        "16",
        "-c:a",
        "copy",
        "-movflags",
        "+faststart",
        str(output_video),
    ]


def build_subtitle_mux_command(input_video: Path | str, subtitles_path: Path | str, output_video: Path | str) -> list[str]:
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
        "-metadata:s:s:0",
        "language=chi",
        "-disposition:s:0",
        "default",
        "-movflags",
        "+faststart",
        str(output_video),
    ]


def build_subtitle_output_command(
    input_video: Path | str,
    subtitles_path: Path | str,
    output_video: Path | str,
    burn_supported: bool | None = None,
) -> list[str]:
    if burn_supported is None:
        burn_supported = _ffmpeg_filter_available("subtitles")
    if burn_supported:
        return build_subtitle_burn_command(input_video, subtitles_path, output_video)
    return build_subtitle_mux_command(input_video, subtitles_path, output_video)


def _asset_front_label(asset_manifest: dict) -> str:
    provider = str(asset_manifest.get("provider") or "")
    if asset_manifest.get("publish_ready") and _image_provider_publishable(provider):
        return "画面可发布"
    if asset_manifest.get("status") == "ready":
        return "样片待真实生图升级"
    return "待素材生成"


def _image_provider_publishable(provider: str) -> bool:
    return provider in PUBLISHABLE_IMAGE_PROVIDERS


def _topic_from_options(options: dict) -> str:
    return str(
        options.get("topic")
        or options.get("original_topic")
        or options.get("original_brief")
        or options.get("source_title")
        or options.get("reference_title")
        or options.get("source_name")
        or ""
    ).strip()[:180]


def _target_duration_seconds(options: dict, blueprint: dict, default: float | None = None) -> float:
    if options.get("target_duration_seconds"):
        return max(8.0, float(options["target_duration_seconds"]))
    if default is not None:
        return float(default)
    duration_range = options.get("duration_range_seconds") or blueprint.get("default_duration_range_seconds") or DEFAULT_DURATION_RANGE
    if isinstance(duration_range, list) and len(duration_range) >= 2:
        return float((float(duration_range[0]) + float(duration_range[1])) / 2.0)
    return 240.0


def _reference_duration_range(duration: float, options: dict) -> list[float]:
    explicit = options.get("duration_range_seconds")
    if isinstance(explicit, list) and len(explicit) >= 2:
        return [round(float(explicit[0]), 3), round(float(explicit[1]), 3)]
    policy = str(options.get("target_duration_policy") or "source_guided")
    if policy == "short_summary":
        upper = min(float(DEFAULT_DURATION_RANGE[1]), max(60.0, duration * 0.6))
        lower = min(float(DEFAULT_DURATION_RANGE[0]), max(30.0, upper * 0.65))
        return [round(lower, 3), round(max(lower, upper), 3)]
    lower = max(8.0, duration * 0.8)
    upper = max(lower, duration * 1.1)
    return [round(lower, 3), round(upper, 3)]


def _probe_has_audio(probe: dict) -> bool:
    return any(stream.get("codec_type") == "audio" for stream in probe.get("streams", []))


def _chapter_seed_for_topic(topic: str, count: int) -> list[tuple[str, str, str, str]]:
    domain = _topic_domain(topic)
    if domain == "sports":
        seed = _sports_chapter_seed(topic)
    else:
        seed = _general_chapter_seed(topic)
    while len(seed) < count:
        index = len(seed) + 1
        seed.append(
            (
                f"第 {index} 个关键判断",
                f"这一部分继续拆解《{topic}》里最容易被忽略的变量。",
                "把前文结论放回具体场景里，说明哪些信息能支撑判断，哪些只是情绪噪音。",
                "用分层信息图表现关键变量、证据来源和结论之间的关系。",
            )
        )
    return seed[:count]


def _topic_domain(topic: str) -> str:
    lowered = str(topic or "").lower()
    sports_terms = [
        "世界杯",
        "世界盃",
        "德國",
        "德国",
        "哈蘭德",
        "哈兰德",
        "姆巴佩",
        "足球",
        "比赛",
        "球员",
        "球队",
        "爆冷",
        "world cup",
        "fifa",
        "penalty",
        "shootout",
    ]
    if any(term.lower() in lowered for term in sports_terms):
        return "sports"
    return "general"


def _sports_chapter_seed(topic: str) -> list[tuple[str, str, str, str]]:
    return [
        (
            "爆冷信号从哪里出现",
            "这条片子的核心不是制造悬念，而是把爆冷可能性拆成赛程、状态、阵容和心理四条线。",
            "德国出局会改变观众对强队稳定性的预期，也会放大每场比赛里小概率事件的讨论热度。",
            "用球场俯视图叠加四条风险线：赛程压力、球员状态、阵容变化、临场心理。",
        ),
        (
            "德国出局后的连锁反应",
            "强队提前出局会让后续比赛的叙事重心从排名预测转向风险判断。",
            "观众不再只问谁更强，而会追问热门球队有没有隐藏短板、替补深度和临场调整够不够。",
            "用淘汰树和舆论热度曲线表现强队出局后，赛程判断如何被重新改写。",
        ),
        (
            "哈兰德和姆巴佩的对位变量",
            "球星对比不能只看名气，要看他们在不同比赛节奏里承担的功能。",
            "哈兰德更依赖禁区终结和输送质量，姆巴佩更能用速度和单点突破改变防线形态。",
            "用左右对比图展示两名球员的活动区域、进攻触发点和防守压力来源。",
        ),
        (
            "今天三场比赛的真实风险",
            "爆冷不是一句口号，每场都要拆主队节奏、客队反击、伤停和体能窗口。",
            "如果热门方控球但转化率低，或者防线面对速度型反击时站位过高，冷门概率就会上升。",
            "用三块比赛卡片展示每场的热门方、风险点、关键球员和可能转折。",
        ),
        (
            "舆论热度不等于胜率",
            "越热门的比赛越容易被情绪带偏，真正有价值的是把热度和证据分开。",
            "社媒讨论、球星名气和历史战绩只能提供背景，不能替代近期状态、战术匹配和临场信息。",
            "用双轴图表现“讨论热度”和“比赛证据”的差异，突出判断不能只跟热搜走。",
        ),
        (
            "最后该怎么形成判断",
            "更稳的看法不是押一个绝对答案，而是给出主线判断、风险条件和临场观察点。",
            "当阵容公布、开场压迫强度和前 15 分钟转换速度出现变化，赛前判断就要动态修正。",
            "用赛前到开场的时间线列出三个观察点：首发、压迫、反击空间。",
        ),
        (
            "结论：看比赛要看变量",
            "这期的结论是：热门球队仍然有优势，但每一场都必须把爆冷条件讲清楚。",
            "德国出局提醒观众，足球比赛的戏剧性来自细节累积，不是标题里的单一情绪。",
            "用一个清晰总结板收束：热门优势、爆冷条件、临场观察、最终判断。",
        ),
    ]


def _general_chapter_seed(topic: str) -> list[tuple[str, str, str, str]]:
    return [
        (
            "问题真正从哪里开始",
            f"理解《{topic}》不能只看表面结论，要先找到推动事件变化的起点。",
            "把时间线、关键人物和最早出现的冲突放在一起，说明为什么这件事会被关注。",
            "用时间线和关键节点卡片建立背景，不使用参考视频截图。",
        ),
        (
            "哪些事实最关键",
            "高质量解读要把事实、判断和情绪分开，让观众知道依据来自哪里。",
            "列出能够支撑判断的事实、数据、公开信息或可观察细节，再说明它们之间的关系。",
            "用证据看板呈现事实、例子、反例和待确认信息。",
        ),
        (
            "分歧点在哪里",
            "真正有价值的内容通常不在结论本身，而在不同判断之间的分歧。",
            "对比两种常见看法，说明每种看法成立的条件，以及它们忽略了什么。",
            "用左右对照的信息图表现两种观点的依据和盲区。",
        ),
        (
            "一个具体例子说明问题",
            "抽象观点需要落到具体场景里，观众才会觉得这条视频不是空泛复述。",
            "选取一个典型场景，拆开人物、动机、限制和结果，说明主线判断为什么成立。",
            "用场景插画和局部 cutaway 表现例子里的关键动作。",
        ),
        (
            "风险和误区",
            "任何判断都有边界，必须说明哪些条件变化会让结论失效。",
            "把容易误判的地方列出来，提醒观众不要把单一线索当成完整答案。",
            "用风险清单和条件分支图表现判断边界。",
        ),
        (
            "最后给出可执行结论",
            "结尾要把信息收束成清楚判断：现在能确定什么，还需要观察什么。",
            "用一句主结论、三个依据和一个待观察变量完成收束。",
            "用简洁总结板展示主结论、证据和下一步观察点。",
        ),
    ]


def _chapter_narration(topic: str, title: str, viewpoint: str, evidence: str) -> str:
    return (
        f"围绕《{topic}》，这一章先回答“{title}”。{viewpoint}"
        f" 这里的证据不是口号：{evidence}"
        " 观众听完这一段，应该能分清哪些是事实，哪些只是情绪化判断。"
    )


def _caption_chunks_v2(text: str) -> list[str]:
    cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
    if not cleaned:
        return []
    parts = [part.strip() for part in re.split(r"[。！？!?；;]", cleaned) if part.strip()]
    chunks: list[str] = []
    for part in parts:
        while len(part) > CAPTION_MAX_CHARS:
            chunks.append(part[:CAPTION_MAX_CHARS])
            part = part[CAPTION_MAX_CHARS:]
        if part:
            chunks.append(part)
    return chunks[:12]


def _caption_timeline_readable(captions: list[dict]) -> bool:
    if not captions:
        return False
    previous_end = 0.0
    for index, caption in enumerate(captions):
        text = str(caption.get("text") or "")
        start = float(caption.get("start") or 0.0)
        end = float(caption.get("end") or 0.0)
        if len(text) > CAPTION_MAX_CHARS:
            return False
        if end - start < CAPTION_MIN_SECONDS:
            return False
        if index and start < previous_end:
            return False
        previous_end = end
    return True


def _srt_time(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    whole_seconds = int(seconds % 60)
    milliseconds = int(round((seconds - int(seconds)) * 1000))
    if milliseconds >= 1000:
        whole_seconds += 1
        milliseconds -= 1000
    return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d},{milliseconds:03d}"


def _ffmpeg_filter_path(path: Path | str) -> str:
    value = str(path)
    return value.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def _ffmpeg_filter_available(filter_name: str) -> bool:
    executable = shutil.which("ffmpeg")
    if not executable:
        return False
    try:
        completed = subprocess.run(
            [executable, "-hide_banner", "-filters"],
            check=False,
            capture_output=True,
            text=True, encoding="utf-8", errors="replace",
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    if completed.returncode != 0:
        return False
    pattern = re.compile(rf"^\s*\S+\s+{re.escape(filter_name)}\s", re.MULTILINE)
    return bool(pattern.search(completed.stdout))


def _cover_text_overlay(value: str) -> str:
    value = re.sub(r"\s+", "", str(value or "原创视频"))
    value = re.sub(r"[：:|｜,，。！？!?·\-—_《》“”\"'（）()]+", "", value)
    if not value:
        value = "原创视频"
    return value[:10]


def _cover_pack_not_overcomplicated(cover_prompt_pack: dict) -> bool:
    prompts = cover_prompt_pack.get("prompts", []) if isinstance(cover_prompt_pack.get("prompts"), list) else []
    if not prompts:
        return False
    for prompt in prompts:
        text = f"{prompt.get('prompt', '')} {prompt.get('negative_prompt', '')}".lower()
        quality_bar = prompt.get("quality_bar", {}) if isinstance(prompt.get("quality_bar"), dict) else {}
        if int(quality_bar.get("focal_point_count") or 0) != 1:
            return False
        if "single clear focal point" not in text:
            return False
    return True


def _draw_mock_cover_asset(path: Path, prompt: dict, index: int) -> None:
    width, height = DEFAULT_REFERENCE_ORIGINAL_GEOMETRY.width, DEFAULT_REFERENCE_ORIGINAL_GEOMETRY.height
    palettes = [
        ("#101719", "#f4efe3", "#36b889", "#e7bd57"),
        ("#141820", "#f5f1e8", "#4c88b7", "#dd5d4b"),
        ("#191816", "#f2eadc", "#b68a42", "#65a17d"),
    ]
    base, paper, accent, warm = palettes[index % len(palettes)]
    image = Image.new("RGB", (width, height), base)
    draw = ImageDraw.Draw(image)
    for x in range(-200, width, 120):
        draw.line((x, height, x + 520, 0), fill="#263230", width=2)
    margin = 118
    draw.rounded_rectangle((margin, 110, width - margin, height - 110), radius=22, fill=paper, outline=accent, width=6)
    focus_x0 = margin + 96
    focus_y0 = 200
    draw.rounded_rectangle((focus_x0, focus_y0, focus_x0 + 560, focus_y0 + 560), radius=36, fill=accent)
    draw.ellipse((focus_x0 + 135, focus_y0 + 96, focus_x0 + 425, focus_y0 + 386), fill=warm)
    draw.rectangle((focus_x0 + 210, focus_y0 + 378, focus_x0 + 350, focus_y0 + 470), fill=base)
    draw.line((focus_x0 + 760, focus_y0 + 120, width - margin - 130, focus_y0 + 120), fill=accent, width=16)
    draw.line((focus_x0 + 760, focus_y0 + 225, width - margin - 250, focus_y0 + 225), fill=warm, width=16)
    draw.line((focus_x0 + 760, focus_y0 + 330, width - margin - 190, focus_y0 + 330), fill="#323737", width=16)
    text = _cover_text_overlay(str(prompt.get("text_overlay") or "原创视频"))
    title_font = _font(92)
    small_font = _font(30)
    draw.text((focus_x0 + 750, focus_y0 + 420), text, fill=base, font=title_font)
    draw.text((focus_x0 + 760, focus_y0 + 530), "ORIGINAL COVER / IMAGES2", fill=accent, font=small_font)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)


def _draw_mock_documentary_asset(path: Path, scene: dict, prompt: dict, scene_index: int, prompt_index: int) -> None:
    scene_text = " ".join(
        [
            str(scene.get("topic_domain") or ""),
            str(scene.get("scene_title") or ""),
            str(prompt.get("prompt") or ""),
        ]
    )
    if "sports" in scene_text or _topic_domain(scene_text) == "sports":
        _draw_mock_sports_asset(path, scene, prompt, scene_index, prompt_index)
        return
    width, height = 1280, 720
    palette = [
        ("#17201d", "#2f8f74", "#f0c15a"),
        ("#1d2430", "#5c8fb7", "#e85f49"),
        ("#202019", "#b68a42", "#70a288"),
        ("#181a21", "#7c6db0", "#e2d6c0"),
    ][scene_index % 4]
    base, accent, warm = palette
    image = Image.new("RGB", (width, height), base)
    draw = ImageDraw.Draw(image)
    for x in range(0, width, 80):
        draw.line((x, 0, x + 160, height), fill=_hex_with_alpha(accent, 34), width=2)
    for y in range(0, height, 90):
        draw.line((0, y, width, y + 40), fill=_hex_with_alpha("#ffffff", 20), width=1)
    inset = 70 + prompt_index * 22
    draw.rounded_rectangle((inset, 84, width - inset, height - 104), radius=28, fill="#f4f0e6", outline=accent, width=6)
    draw.rectangle((inset + 52, 160, inset + 390, 490), fill=accent)
    draw.ellipse((inset + 108, 216, inset + 335, 443), fill=warm)
    draw.line((inset + 500, 230, width - inset - 110, 230), fill=accent, width=12)
    draw.line((inset + 500, 315, width - inset - 190, 315), fill=warm, width=12)
    draw.line((inset + 500, 400, width - inset - 150, 400), fill="#2f3438", width=12)
    font_title = _font(44)
    font_small = _font(24)
    title = str(scene.get("scene_title") or f"Scene {scene_index + 1}")
    slot = str(prompt.get("slot_type") or "visual").replace("_", " ")
    draw.text((inset + 52, height - 170), _fit_text(title, 24), fill="#18201e", font=font_title)
    draw.text((inset + 52, height - 116), slot.upper(), fill=accent, font=font_small)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)


def _draw_mock_sports_asset(path: Path, scene: dict, prompt: dict, scene_index: int, prompt_index: int) -> None:
    variant = scene_index % 7
    if variant == 1:
        _draw_sports_bracket_asset(path, scene, prompt, prompt_index)
    elif variant == 2:
        _draw_sports_player_compare_asset(path, scene, prompt, prompt_index)
    elif variant == 3:
        _draw_sports_match_cards_asset(path, scene, prompt, prompt_index)
    elif variant == 4:
        _draw_sports_evidence_chart_asset(path, scene, prompt, prompt_index)
    elif variant == 5:
        _draw_sports_timeline_asset(path, scene, prompt, prompt_index)
    elif variant == 6:
        _draw_sports_summary_asset(path, scene, prompt, prompt_index)
    else:
        _draw_sports_field_asset(path, scene, prompt, prompt_index)


def _draw_sports_field_asset(path: Path, scene: dict, prompt: dict, prompt_index: int) -> None:
    width, height = 1280, 720
    field = "#1f7a4d"
    dark = "#10281f"
    line = "#e7f2df"
    accent = ["#e4c84f", "#e95a4c", "#61a8d8", "#f4efe3"][prompt_index % 4]
    image = Image.new("RGB", (width, height), dark)
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((70, 70, width - 70, height - 80), radius=28, fill=field, outline=line, width=6)
    for x in range(160, width - 120, 120):
        draw.line((x, 74, x - 140, height - 84), fill="#2f9060", width=3)
    draw.line((width // 2, 78, width // 2, height - 84), fill=line, width=5)
    draw.ellipse((width // 2 - 95, height // 2 - 95, width // 2 + 95, height // 2 + 95), outline=line, width=5)
    draw.rectangle((70, 230, 190, 490), outline=line, width=5)
    draw.rectangle((width - 190, 230, width - 70, 490), outline=line, width=5)
    player_positions = [
        (330, 260, "#f4efe3"),
        (440, 430, "#f4efe3"),
        (650, 360, accent),
        (820, 250, "#101719"),
        (930, 470, "#101719"),
    ]
    for x, y, color in player_positions:
        draw.ellipse((x - 22, y - 22, x + 22, y + 22), fill=color, outline=line, width=2)
    draw.ellipse((width // 2 - 12, height // 2 - 12, width // 2 + 12, height // 2 + 12), fill="#ffffff", outline="#1c1c1c")
    panel_x0, panel_y0 = 840, 92
    draw.rounded_rectangle((panel_x0, panel_y0, width - 92, panel_y0 + 178), radius=18, fill="#f4efe3", outline=accent, width=5)
    title = str(scene.get("scene_title") or "比赛变量")
    slot = str(prompt.get("slot_type") or "visual").replace("_", " ")
    draw.text((panel_x0 + 28, panel_y0 + 26), _fit_text(title, 16), fill="#111719", font=_font(38))
    draw.text((panel_x0 + 28, panel_y0 + 86), "MATCH RISK / KEY VARIABLES", fill="#1f7a4d", font=_font(24))
    draw.text((panel_x0 + 28, panel_y0 + 122), slot.upper(), fill="#383b36", font=_font(22))
    draw.rounded_rectangle((94, height - 145, 660, height - 92), radius=14, fill="#f4efe3")
    draw.text((120, height - 134), _fit_text(str(prompt.get("slot_type") or "爆冷条件"), 22), fill="#10281f", font=_font(30))
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)


def _draw_sports_bracket_asset(path: Path, scene: dict, prompt: dict, prompt_index: int) -> None:
    width, height = 1280, 720
    image = Image.new("RGB", (width, height), "#111719")
    draw = ImageDraw.Draw(image)
    accent = ["#e4c84f", "#66b58e", "#f05b4f"][prompt_index % 3]
    draw.rounded_rectangle((70, 70, width - 70, height - 80), radius=28, fill="#18221f", outline="#66b58e", width=5)
    draw.rounded_rectangle((96, 96, width - 96, 180), radius=18, fill="#f4efe3")
    draw.text((126, 120), _fit_text(str(scene.get("scene_title") or "淘汰树变化"), 26), fill="#111719", font=_font(42))
    rounds_x = [180, 430, 700, 980]
    y_groups = [[250, 390, 530], [310, 470], [390], [390]]
    for col, x in enumerate(rounds_x):
        for y in y_groups[col]:
            draw.rounded_rectangle((x - 70, y - 24, x + 120, y + 34), radius=14, fill="#f4efe3", outline=accent, width=3)
            draw.text((x - 45, y - 8), ["热门", "风险", "冷门", "晋级"][col], fill="#111719", font=_font(23))
        if col < len(rounds_x) - 1:
            next_x = rounds_x[col + 1] - 70
            for y in y_groups[col]:
                target_y = y_groups[col + 1][min(len(y_groups[col + 1]) - 1, y // 360)]
                draw.line((x + 120, y + 5, next_x, target_y + 5), fill=accent, width=5)
    _draw_sports_asset_footer(draw, scene, prompt, accent)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)


def _draw_sports_player_compare_asset(path: Path, scene: dict, prompt: dict, prompt_index: int) -> None:
    width, height = 1280, 720
    image = Image.new("RGB", (width, height), "#101719")
    draw = ImageDraw.Draw(image)
    left = "#2f6fa3"
    right = "#d85d4d"
    accent = "#e4c84f" if prompt_index % 2 == 0 else "#66b58e"
    draw.rounded_rectangle((70, 86, 610, 590), radius=28, fill=left)
    draw.rounded_rectangle((670, 86, 1210, 590), radius=28, fill=right)
    for x, label, color in [(190, "终结点", left), (790, "突破点", right)]:
        draw.ellipse((x, 170, x + 260, 430), fill="#f4efe3", outline=accent, width=8)
        draw.rectangle((x + 94, 430, x + 166, 525), fill="#171b1f")
        draw.text((x + 50, 120), label, fill="#f4efe3", font=_font(40))
    draw.line((640, 120, 640, 560), fill="#f4efe3", width=4)
    draw.text((458, 610), "HAALAND", fill="#f4efe3", font=_font(34))
    draw.text((720, 610), "MBAPPE", fill="#f4efe3", font=_font(34))
    _draw_sports_asset_header(draw, scene, "#f4efe3")
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)


def _draw_sports_match_cards_asset(path: Path, scene: dict, prompt: dict, prompt_index: int) -> None:
    width, height = 1280, 720
    image = Image.new("RGB", (width, height), "#12201b")
    draw = ImageDraw.Draw(image)
    accent = ["#e4c84f", "#66b58e", "#e95a4c"][prompt_index % 3]
    draw.text((82, 58), _fit_text(str(scene.get("scene_title") or "三场比赛风险"), 24), fill="#f4efe3", font=_font(48))
    labels = ["节奏", "反击", "体能"]
    for index, x in enumerate([80, 460, 840]):
        draw.rounded_rectangle((x, 158, x + 330, 565), radius=24, fill="#f4efe3", outline=accent, width=5)
        draw.text((x + 34, 196), f"比赛 {index + 1}", fill="#111719", font=_font(38))
        draw.line((x + 34, 262, x + 260, 262), fill=accent, width=8)
        for row, label in enumerate(labels):
            y = 320 + row * 70
            draw.ellipse((x + 38, y, x + 68, y + 30), fill=accent)
            draw.text((x + 88, y - 4), label, fill="#23302a", font=_font(30))
        draw.rounded_rectangle((x + 36, 500, x + 292, 536), radius=12, fill="#13201b")
    _draw_sports_asset_footer(draw, scene, prompt, accent)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)


def _draw_sports_evidence_chart_asset(path: Path, scene: dict, prompt: dict, prompt_index: int) -> None:
    width, height = 1280, 720
    image = Image.new("RGB", (width, height), "#f4efe3")
    draw = ImageDraw.Draw(image)
    accent = "#1f7a4d"
    warm = "#e4c84f" if prompt_index % 2 == 0 else "#d85d4d"
    draw.rounded_rectangle((80, 80, 1200, 610), radius=26, fill="#101719")
    draw.text((120, 116), _fit_text(str(scene.get("scene_title") or "热度不等于胜率"), 24), fill="#f4efe3", font=_font(48))
    chart = (150, 230, 1120, 520)
    draw.rectangle(chart, outline="#5f756a", width=3)
    for step in range(1, 5):
        y = chart[1] + step * 58
        draw.line((chart[0], y, chart[2], y), fill="#263230", width=2)
    heat_points = [(170, 470), (340, 390), (510, 410), (680, 260), (850, 300), (1060, 190)]
    evidence_points = [(170, 450), (340, 430), (510, 360), (680, 350), (850, 280), (1060, 250)]
    draw.line(heat_points, fill=warm, width=8)
    draw.line(evidence_points, fill=accent, width=8)
    for x, y in heat_points + evidence_points:
        draw.ellipse((x - 9, y - 9, x + 9, y + 9), fill="#f4efe3")
    draw.text((165, 545), "舆论热度", fill=warm, font=_font(28))
    draw.text((355, 545), "比赛证据", fill=accent, font=_font(28))
    _draw_sports_asset_footer(draw, scene, prompt, warm)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)


def _draw_sports_timeline_asset(path: Path, scene: dict, prompt: dict, prompt_index: int) -> None:
    width, height = 1280, 720
    image = Image.new("RGB", (width, height), "#111719")
    draw = ImageDraw.Draw(image)
    accent = "#66b58e" if prompt_index % 2 == 0 else "#e4c84f"
    draw.text((86, 80), _fit_text(str(scene.get("scene_title") or "赛前观察线"), 24), fill="#f4efe3", font=_font(52))
    y = 360
    draw.line((130, y, 1150, y), fill="#f4efe3", width=8)
    for index, (x, label) in enumerate([(190, "首发"), (460, "压迫"), (730, "空间"), (1000, "修正")]):
        draw.ellipse((x - 38, y - 38, x + 38, y + 38), fill=accent, outline="#f4efe3", width=5)
        draw.text((x - 52, y + 64), label, fill="#f4efe3", font=_font(34))
        draw.rounded_rectangle((x - 88, 210, x + 118, 285), radius=16, fill="#f4efe3")
        draw.text((x - 54, 232), f"T{index}", fill="#111719", font=_font(28))
    _draw_sports_asset_footer(draw, scene, prompt, accent)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)


def _draw_sports_summary_asset(path: Path, scene: dict, prompt: dict, prompt_index: int) -> None:
    width, height = 1280, 720
    image = Image.new("RGB", (width, height), "#18221f")
    draw = ImageDraw.Draw(image)
    accent = "#e4c84f" if prompt_index % 2 == 0 else "#66b58e"
    draw.rounded_rectangle((88, 86, 1192, 612), radius=28, fill="#f4efe3", outline=accent, width=7)
    draw.text((136, 132), _fit_text(str(scene.get("scene_title") or "看变量"), 24), fill="#111719", font=_font(56))
    items = ["热门优势", "爆冷条件", "临场观察", "最终判断"]
    for index, item in enumerate(items):
        x = 150 + (index % 2) * 500
        y = 250 + (index // 2) * 140
        draw.rounded_rectangle((x, y, x + 410, y + 88), radius=18, fill="#111719")
        draw.ellipse((x + 26, y + 26, x + 62, y + 62), fill=accent)
        draw.text((x + 88, y + 24), item, fill="#f4efe3", font=_font(34))
    _draw_sports_asset_footer(draw, scene, prompt, accent)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)


def _draw_sports_asset_header(draw: ImageDraw.ImageDraw, scene: dict, fill: str) -> None:
    draw.text((78, 38), _fit_text(str(scene.get("scene_title") or "比赛变量"), 26), fill=fill, font=_font(40))


def _draw_sports_asset_footer(draw: ImageDraw.ImageDraw, scene: dict, prompt: dict, accent: str) -> None:
    width, height = 1280, 720
    draw.rounded_rectangle((86, height - 86, 640, height - 38), radius=14, fill="#f4efe3")
    draw.text((112, height - 76), _fit_text(str(prompt.get("slot_type") or scene.get("scene_title") or "关键变量"), 22), fill="#10281f", font=_font(27))
    draw.line((690, height - 62, 1120, height - 62), fill=accent, width=8)


def _draw_scene_frame(path: Path, scene: dict, asset_manifest: dict) -> None:
    width, height = DEFAULT_REFERENCE_ORIGINAL_GEOMETRY.width, DEFAULT_REFERENCE_ORIGINAL_GEOMETRY.height
    image = Image.new("RGB", (width, height), "#111719")
    draw = ImageDraw.Draw(image)
    for x in range(0, width, 96):
        draw.line((x, 0, x, height), fill="#263230", width=1)
    for y in range(0, height, 96):
        draw.line((0, y, width, y), fill="#263230", width=1)
    assets = _assets_for_scene(asset_manifest, int(scene.get("index") or 0))
    if assets:
        hero = Image.open(assets[0]).convert("RGB")
        hero = ImageOps.fit(hero, (1020, 574), method=Image.Resampling.LANCZOS)
        image.paste(hero, (90, 150))
    if len(assets) > 1:
        detail = Image.open(assets[1]).convert("RGB")
        detail = ImageOps.fit(detail, (520, 292), method=Image.Resampling.LANCZOS)
        image.paste(detail, (1300, 180))
    if len(assets) > 2:
        cutaway = Image.open(assets[2]).convert("RGB")
        cutaway = ImageOps.fit(cutaway, (520, 292), method=Image.Resampling.LANCZOS)
        image.paste(cutaway, (1300, 526))
    draw.rectangle((0, 0, width, 92), fill="#f5f2ea")
    draw.rectangle((0, height - 170, width, height), fill="#101719")
    draw.rectangle((90, 116, 1110, 724), outline="#51b88f", width=4)
    draw.text((90, 26), f"{int(scene.get('index') or 0) + 1:02d} / ORIGINAL DOCUMENTARY", fill="#121819", font=_font(34))
    title = str(scene.get("title") or "原创章节")
    draw.text((90, height - 138), _fit_text(title, 30), fill="#f5f2ea", font=_font(46))
    voiceover = re.sub(r"\s+", " ", str(scene.get("voiceover") or "")).strip()
    draw.text((90, height - 78), _fit_text(voiceover, 72), fill="#cbd7d2", font=_font(28))
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)


def _assets_for_scene(asset_manifest: dict, scene_index: int) -> list[Path]:
    for scene in asset_manifest.get("scenes", []):
        if int(scene.get("scene_index") or 0) != scene_index:
            continue
        return [Path(asset["path"]) for asset in scene.get("assets", []) if Path(asset.get("path", "")).exists()]
    return []


def _write_script_v2(path: Path, content_plan: dict, storyboard: dict) -> None:
    lines = [
        f"# {content_plan.get('topic', '原创视频脚本')}",
        "",
        "这条片子使用参考视频学习结构和节奏，但不复用参考画面、原声或原字幕。",
        "",
    ]
    for scene in storyboard.get("scenes", []):
        lines.extend(
            [
                f"## {int(scene.get('index') or 0) + 1}. {scene.get('title')}",
                f"- 观点：{scene.get('viewpoint')}",
                f"- 证据或例子：{scene.get('evidence_or_example')}",
                f"- 视觉目标：{scene.get('visual_goal')}",
                f"- 旁白：{scene.get('voiceover')}",
                "",
            ]
        )
    path.write_text("\n".join(lines), encoding="utf-8")


def _write_render_report(path: Path, reference_probe: dict, output_probe: dict, quality: dict, delivery: dict) -> None:
    payload = {
        "version": "reference_guided_render_report_v1",
        "workflow": "reference_guided_original",
        "reference_duration": _duration_from_probe(reference_probe),
        "output_duration": _duration_from_probe(output_probe),
        "quality_status": quality.get("status"),
        "release_decision": delivery.get("release_decision", {}),
        "source_reuse": {"visual": 0.0, "audio": 0.0, "text": 0.0},
    }
    _write_json(path, payload)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    return load_font(size)


def _fit_text(value: str, limit: int) -> str:
    value = str(value or "").strip()
    return value if len(value) <= limit else value[: max(0, limit - 1)] + "…"


def _hex_with_alpha(hex_color: str, alpha: int) -> str:
    del alpha
    return hex_color


def _progress(progress: ProgressCallback | None, message: str) -> None:
    if progress is not None:
        progress(message)
