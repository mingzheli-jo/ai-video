import json
import math
from dataclasses import replace
from pathlib import Path

from PIL import Image, ImageDraw

from video_factory.audio import (
    AudioAnalysis,
    AudioCue,
    AudioProviderStatus,
    audio_analysis_to_dict,
    write_audio_analysis_json,
)
from video_factory.content import (
    ContentAnalysis,
    ContentCue,
    ContentProviderStatus,
    analyze_content_samples,
    analysis_to_dict,
    build_ocr_candidate_images,
    write_content_analysis_json,
)
from video_factory.creative import (
    CreativeFrameSample,
    build_creative_plan,
    build_sample_schedule,
    classify_creative_profile,
    plan_to_dict,
    ranges_overlap,
    write_candidate_edl,
)
from video_factory.semantic import build_semantic_timeline
from video_factory.replicate import (
    FaceOverlayRegion,
    VideoGeometry,
    build_caption_timeline_for_release,
    build_creative_sample_extract_command,
    build_creative_card,
    build_generated_visual_manifest,
    build_human_edit_scene_command,
    build_human_edit_storyboard,
    build_images2_prompt_pack,
    build_original_enhance_command,
    build_replicate_paths,
    build_visual_insert_plan,
    choose_mode,
    contact_sheet_timestamps,
    creative_sample_count_for_duration,
    creative_release_timeline,
    geometry_from_probe,
    human_edit_duration,
    cover_seek_seconds,
    cover_seek_seconds_for_mode,
    creative_title_from_source,
    creative_segments_from_plan,
    run_quality_checks,
    write_subtitles_for_release,
    _creative_title_for_render,
    _build_caption_overlay_burn_command,
    _build_face_only_transform_command,
    _build_presenter_remove_transform_command,
    _compose_release_cover,
    _publishable_release_visual_inserts,
    _release_cover_title_from_paths,
    _write_caption_overlay_images,
    _estimate_face_overlay_region_from_image,
    _write_contact_sheet,
    _write_report,
    _write_virtual_face_asset,
    _visual_transform_policy,
)


def _write_passing_content_files(paths):
    paths.content_analysis.write_text(
        json.dumps(
            {
                "provider": {"name": "vision_lite", "status": "fallback", "message": "local cues"},
                "coverage": {"cue_count": 3, "tagged_count": 3, "recognized_text_count": 0},
                "cues": [
                    {"sample_index": 0, "timestamp": 0.7, "content_tags": ["subtitle"], "evidence": ["subtitle_band"]},
                    {"sample_index": 1, "timestamp": 8.0, "content_tags": ["interface"], "evidence": ["interface_layout"]},
                    {"sample_index": 2, "timestamp": 16.0, "content_tags": ["validation"], "evidence": ["result_screen"]},
                ],
            }
        ),
        encoding="utf-8",
    )


def _write_passing_audio_files(paths, cue_count=4):
    paths.audio_analysis.write_text(
        json.dumps(
            {
                "provider": {"name": "ffmpeg_volumedetect", "status": "available", "message": "local audio cues"},
                "coverage": {"cue_count": cue_count, "tagged_count": cue_count, "speech_like_count": cue_count},
                "cues": [
                    {
                        "sample_index": index,
                        "timestamp": index * 30.0,
                        "mean_volume_db": -24.0,
                        "max_volume_db": -8.0,
                        "energy": 0.72,
                        "speech_likelihood": 0.68,
                        "audio_tags": ["speech_like", "emphasis"],
                        "evidence": ["mean_volume:-24.0dB", "max_volume:-8.0dB"],
                    }
                    for index in range(cue_count)
                ],
            }
        ),
        encoding="utf-8",
    )


def _write_passing_semantic_files(paths, chapter_count=3):
    paths.semantic_timeline.write_text(
        json.dumps(
            {
                "provider": {
                    "name": "ocr_audio_semantic",
                    "status": "available",
                    "message": "derived semantic chapters from OCR/content cues",
                },
                "coverage": {
                    "chapter_count": chapter_count,
                    "sample_count": chapter_count,
                    "titled_count": chapter_count,
                    "audio_emphasis_count": chapter_count,
                },
                "chapters": [
                    {
                        "index": index,
                        "topic": topic,
                        "title": title,
                        "start": index * 8.0,
                        "end": index * 8.0 + 6.0,
                        "sample_indices": [index],
                        "evidence": [title],
                        "audio_emphasis_count": 1,
                    }
                    for index, (topic, title) in enumerate(
                        [
                            ("install", "安装入口"),
                            ("api_key", "DeepSeek API Key"),
                            ("local_route", "本地路由与验证"),
                        ][:chapter_count]
                    )
                ],
            }
        ),
        encoding="utf-8",
    )


def _write_passing_transcript_files(paths, cue_count=3):
    paths.transcript_analysis.write_text(
        json.dumps(
            {
                "provider": {
                    "name": "ocr_transcript_proxy",
                    "status": "fallback",
                    "message": "derived transcript cues from OCR/content text",
                },
                "coverage": {
                    "cue_count": cue_count,
                    "text_cue_count": cue_count,
                    "sidecar_cue_count": 0,
                    "ocr_proxy_cue_count": cue_count,
                },
                "cues": [
                    {
                        "sample_index": index,
                        "start": index * 8.0,
                        "end": index * 8.0 + 4.0,
                        "text": text,
                        "source": "ocr_proxy",
                        "confidence": 0.62,
                        "evidence": [f"ocr_proxy:{text}"],
                    }
                    for index, text in enumerate(
                        ["安装 Codex", "DeepSeek API Key", "本地路由服务启动"][:cue_count]
                    )
                ],
            }
        ),
        encoding="utf-8",
    )


def _longest_run(values):
    longest = 0
    current_value = object()
    current_count = 0
    for value in values:
        if value == current_value:
            current_count += 1
        else:
            current_value = value
            current_count = 1
        longest = max(longest, current_count)
    return longest


def test_replicate_paths_are_based_on_input_name_and_mode(tmp_path):
    input_video = tmp_path / "我的 参考视频.mp4"
    paths = build_replicate_paths(input_video, "human-edit", output_root=tmp_path / "out")

    assert paths.output_dir.name == "wo-de-can-kao-shi-pin-human-edit"
    assert paths.video == paths.output_dir / "release.mp4"
    assert paths.contact_sheet == paths.output_dir / "contact_sheet.jpg"
    assert paths.edl == paths.output_dir / "edit_decision_list.md"
    assert paths.report == paths.output_dir / "render_report.json"
    assert paths.content_analysis == paths.output_dir / "content_analysis.json"
    assert paths.audio_analysis == paths.output_dir / "audio_analysis.json"
    assert paths.semantic_timeline == paths.output_dir / "semantic_timeline.json"
    assert paths.transcript_analysis == paths.output_dir / "transcript_analysis.json"
    assert paths.visual_insert_plan == paths.output_dir / "visual_insert_plan.json"
    assert paths.images2_prompt_pack == paths.output_dir / "images2_prompt_pack.json"
    assert paths.generated_visual_manifest == paths.output_dir / "generated_visual_manifest.json"
    assert paths.caption_timeline == paths.output_dir / "caption_timeline.json"
    assert paths.subtitles == paths.output_dir / "subtitles.srt"
    assert paths.cover_brief == paths.output_dir / "cover_brief.json"


def test_images2_contextual_insert_plan_is_relevant_limited_and_non_consecutive():
    samples = [
        CreativeFrameSample(index=index, timestamp=20.0 + index * 55.0, brightness=0.55, contrast=0.62, sharpness=0.7, colorfulness=0.5, motion=0.42)
        for index in range(8)
    ]
    plan = build_creative_plan(
        420.0,
        samples,
        title="德国队赛后情绪失控完整复盘",
        production_options={
            "visual_asset_strategy": "images2_contextual_inserts",
            "target_duration_policy": "source_guided",
        },
    )

    visual_plan = build_visual_insert_plan(plan, {"visual_asset_strategy": "images2_contextual_inserts"})

    assert visual_plan["version"] == "visual_insert_plan_v1"
    assert visual_plan["strategy"] == "images2_contextual_inserts"
    assert visual_plan["source_video_is_primary"] is True
    assert visual_plan["max_ai_insert_ratio"] == 0.08
    assert visual_plan["total_ai_insert_duration"] <= visual_plan["release_duration_seconds"] * 0.08
    assert visual_plan["inserts"]
    previous_after_index = -10
    for insert in visual_plan["inserts"]:
        assert insert["insert_type"] in {"explanation_visual", "detail_cutaway", "comparison_visual", "chapter_transition"}
        assert insert["source_evidence"]
        assert insert["context_binding"]["chapter"] or insert["context_binding"]["semantic_role"]
        assert "no PPT card" in insert["visual_style"]
        assert int(insert["after_index"]) > previous_after_index
        assert int(insert["after_index"]) - previous_after_index > 0
        previous_after_index = int(insert["after_index"])


def test_images2_prompt_pack_and_mock_manifest_are_preview_only(tmp_path):
    visual_plan = {
        "version": "visual_insert_plan_v1",
        "strategy": "images2_contextual_inserts",
        "source_title": "德国队赛后情绪失控完整复盘",
        "release_duration_seconds": 240.0,
        "max_ai_insert_ratio": 0.08,
        "inserts": [
            {
                "insert_id": "ai_visual_00",
                "insert_type": "explanation_visual",
                "duration": 3.0,
                "after_index": 1,
                "source_evidence": ["chapter:赛后反应", "audio:情绪峰值"],
                "prompt_goal": "解释主教练表情变化和现场气氛",
                "context_binding": {"chapter": "赛后反应", "semantic_role": "decision_moment"},
                "visual_style": "documentary contextual still, no text card",
            }
        ],
    }

    prompt_pack = build_images2_prompt_pack(visual_plan, {"image_provider": "mock_images2"})
    manifest = build_generated_visual_manifest(tmp_path / "generated_visuals", prompt_pack, provider="mock_images2")

    assert prompt_pack["version"] == "images2_prompt_pack_v1"
    assert prompt_pack["provider"] == "mock_images2"
    assert prompt_pack["prompts"][0]["insert_id"] == "ai_visual_00"
    assert "do not create a PPT slide" in prompt_pack["prompts"][0]["negative_prompt"]
    assert manifest["status"] == "ready"
    assert manifest["publish_ready"] is False
    assert manifest["provider"] == "mock_images2"
    assert Path(manifest["visuals"][0]["path"]).exists()
    assert manifest["visuals"][0]["origin"] == "ai_generated"
    assert manifest["visuals"][0]["usage"] == "contextual_insert"


def test_mock_images2_visuals_are_not_inserted_into_release(tmp_path):
    visual_plan = {
        "inserts": [
            {
                "insert_id": "ai_visual_00",
                "source_type": "ai_contextual_visual",
                "duration": 3.0,
                "after_index": 1,
                "source_evidence": ["chapter:赛后反应"],
            }
        ]
    }
    prompt_pack = build_images2_prompt_pack(
        {"source_title": "德国队赛后复盘", "inserts": visual_plan["inserts"]},
        {"image_provider": "mock_images2"},
    )
    manifest = build_generated_visual_manifest(tmp_path / "generated_visuals", prompt_pack, provider="mock_images2")

    assert _publishable_release_visual_inserts(visual_plan["inserts"], manifest) == []


def test_publishable_images2_visuals_can_be_inserted_into_release(tmp_path):
    visual_plan = {
        "inserts": [
            {
                "insert_id": "ai_visual_00",
                "source_type": "ai_contextual_visual",
                "duration": 3.0,
                "after_index": 1,
                "source_evidence": ["chapter:赛后反应"],
            }
        ]
    }
    prompt_pack = build_images2_prompt_pack(
        {"source_title": "德国队赛后复盘", "inserts": visual_plan["inserts"]},
        {"image_provider": "images2"},
    )
    manifest = build_generated_visual_manifest(tmp_path / "generated_visuals", prompt_pack, provider="images2")

    assert _publishable_release_visual_inserts(visual_plan["inserts"], manifest) == visual_plan["inserts"]


def test_caption_timeline_and_subtitles_are_generated_from_release_plan(tmp_path):
    samples = [
        CreativeFrameSample(index=index, timestamp=5.0 + index * 15.0, brightness=0.5, contrast=0.55, sharpness=0.66, colorfulness=0.44, motion=0.38)
        for index in range(5)
    ]
    plan = build_creative_plan(96.0, samples, title="AI 视频发布增强流程", production_options={"visual_asset_strategy": "images2_contextual_inserts"})
    segment = replace(plan.recommended_variant.segments[0], transcript_evidence="srt:AI 视频发布增强流程")
    variant = replace(plan.recommended_variant, segments=(segment,))
    plan = replace(plan, variants=(variant,), recommended_variant_name=variant.name)
    caption_timeline = build_caption_timeline_for_release(plan)
    subtitles = tmp_path / "subtitles.srt"

    write_subtitles_for_release(subtitles, caption_timeline)

    assert caption_timeline["version"] == "release_caption_timeline_v1"
    assert caption_timeline["readability"]["max_chars_per_caption"] <= 32
    assert caption_timeline["captions"]
    for previous, current in zip(caption_timeline["captions"], caption_timeline["captions"][1:]):
        assert current["start"] >= previous["end"]
    assert "00:00:00,000 -->" in subtitles.read_text(encoding="utf-8")


def test_caption_timeline_does_not_leak_internal_chapter_labels():
    samples = [
        CreativeFrameSample(index=index, timestamp=5.0 + index * 15.0, brightness=0.5, contrast=0.55, sharpness=0.66, colorfulness=0.44, motion=0.38)
        for index in range(5)
    ]
    plan = build_creative_plan(96.0, samples, title="世界盃劇本瘋了")
    segment = replace(
        plan.recommended_variant.segments[0],
        chapter_title="上下文铺垫",
        semantic_topic="context",
        transcript_evidence="上下文铺垫",
        content_evidence="text_density:0.30; subtitle_likelihood:0.75; interface_likelihood:0.18",
    )
    variant = replace(plan.recommended_variant, segments=(segment,))
    plan = replace(plan, variants=(variant,), recommended_variant_name=variant.name)

    caption_timeline = build_caption_timeline_for_release(plan)

    assert caption_timeline["captions"] == []


def test_release_subtitle_burn_command_creates_visible_subtitles():
    overlays = [{"path": Path("/tmp/caption_001.png"), "start": 0.0, "end": 2.5}]
    command = _build_caption_overlay_burn_command("release_no_subtitles.mp4", overlays, "release.mp4")
    joined = " ".join(command)

    assert command[0] == "ffmpeg"
    assert "overlay=0:0" in joined
    assert "between(t\\,0.000\\,2.500)" in joined
    assert "-c:s" not in command
    assert "mov_text" not in command
    assert command[-1] == "release.mp4"


def test_caption_overlay_images_are_generated_for_visible_subtitles(tmp_path):
    overlays = _write_caption_overlay_images(
        [{"start": 0.0, "end": 2.0, "text": "自动字幕可见"}],
        tmp_path,
        VideoGeometry(width=640, height=360),
    )

    assert len(overlays) == 1
    assert Path(overlays[0]["path"]).exists()


def test_audio_analysis_json_round_trip(tmp_path):
    analysis = AudioAnalysis(
        provider=AudioProviderStatus(name="ffmpeg_volumedetect", status="available", message="local audio cues"),
        cues=(
            AudioCue(
                sample_index=0,
                timestamp=0.7,
                mean_volume_db=-23.5,
                max_volume_db=-5.0,
                energy=0.73,
                speech_likelihood=0.66,
                audio_tags=("speech_like", "emphasis"),
                evidence=("mean_volume:-23.5dB", "max_volume:-5.0dB"),
            ),
        ),
    )
    output = tmp_path / "audio_analysis.json"

    write_audio_analysis_json(analysis, output)
    data = json.loads(output.read_text(encoding="utf-8"))

    assert data == audio_analysis_to_dict(analysis)
    assert data["provider"]["status"] == "available"
    assert data["coverage"]["speech_like_count"] == 1
    assert data["cues"][0]["audio_tags"] == ["speech_like", "emphasis"]


def test_content_analysis_extracts_local_cues_and_serializes(tmp_path):
    image_path = tmp_path / "sample.jpg"
    image = Image.new("RGB", (640, 360), "#f5f5f1")
    draw = ImageDraw.Draw(image)
    draw.rectangle((40, 40, 600, 180), fill="#ffffff", outline="#333333")
    draw.text((70, 80), "Codex API Key", fill="#111111")
    draw.rectangle((0, 300, 640, 360), fill="#111111")
    draw.text((180, 318), "配置 API key 后验证", fill="#ffffff")
    image.save(image_path)

    analysis = analyze_content_samples([(0.7, image_path)], title="Codex DeepSeek API 配置教程")
    data = analysis_to_dict(analysis)

    assert analysis.provider.name
    assert len(analysis.cues) == 1
    assert 0 <= analysis.cues[0].text_density <= 1
    assert analysis.cues[0].subtitle_likelihood > 0
    assert analysis.cues[0].interface_likelihood > 0
    assert {"api_key", "configuration"} & set(analysis.cues[0].content_tags)
    assert data["provider"]["name"] == analysis.provider.name


def test_ocr_candidate_images_include_upscaled_subtitle_and_interface_regions(tmp_path):
    image_path = tmp_path / "sample.jpg"
    image = Image.new("RGB", (1280, 720), "#f5f5f1")
    draw = ImageDraw.Draw(image)
    draw.rectangle((120, 90, 1160, 520), fill="#ffffff", outline="#333333")
    draw.text((180, 160), "Codex API Key", fill="#111111")
    draw.rectangle((0, 590, 1280, 720), fill="#111111")
    draw.text((420, 635), "配置 API key 后验证", fill="#ffffff")
    image.save(image_path)

    candidates = build_ocr_candidate_images(image_path)
    roles = {candidate.role for candidate in candidates}
    sizes = {candidate.role: Image.open(candidate.path).size for candidate in candidates}

    assert {"full_frame", "subtitle_band", "interface_panel", "header_band"}.issubset(roles)
    assert sizes["subtitle_band"][0] >= 1280
    assert sizes["subtitle_band"][1] > 130
    assert sizes["interface_panel"][0] >= 1000
    assert all(candidate.path.exists() for candidate in candidates)


def test_content_analysis_json_round_trip(tmp_path):
    analysis = ContentAnalysis(
        provider=ContentProviderStatus(name="vision_lite", status="fallback", message="local cues"),
        cues=(
            ContentCue(
                sample_index=0,
                timestamp=0.7,
                text_density=0.12,
                subtitle_likelihood=0.8,
                interface_likelihood=0.5,
                recognized_text="Codex API",
                content_tags=("codex", "api_key"),
                evidence=("ocr:Codex API", "subtitle_band:0.80"),
            ),
        ),
    )
    output = tmp_path / "content_analysis.json"

    write_content_analysis_json(analysis, output)
    data = json.loads(output.read_text(encoding="utf-8"))

    assert data["provider"]["status"] == "fallback"
    assert data["coverage"]["cue_count"] == 1
    assert data["cues"][0]["content_tags"] == ["codex", "api_key"]


def test_choose_mode_prefers_human_edit_for_long_tutorials(tmp_path):
    video = tmp_path / "tutorial.mp4"

    assert choose_mode(video, source_duration=563.4) == "human-edit"
    assert choose_mode(video, source_duration=80.0) == "original-enhanced"


def test_human_edit_storyboard_builds_real_cut_plan():
    segments = build_human_edit_storyboard(563.4)

    assert 360 <= human_edit_duration(segments) <= 480
    assert len(segments) >= 8
    assert segments[0].start == 0
    assert all(segment.duration > 0 for segment in segments)
    assert all(segment.start + segment.duration <= 563.4 for segment in segments)
    assert sum(1 for segment in segments if segment.zoom > 1.0) >= 4


def test_short_storyboard_never_inflates_or_overlaps_source():
    source_duration = 22.314
    segments = build_human_edit_storyboard(source_duration)

    assert human_edit_duration(segments) <= source_duration
    for previous, current in zip(segments, segments[1:]):
        assert current.start >= previous.start + previous.duration


def test_human_edit_command_has_no_ai_template_packaging(tmp_path):
    source = tmp_path / "source.mp4"
    segment = build_human_edit_storyboard(563.4)[1]
    command = build_human_edit_scene_command(source, segment, tmp_path / "segment.mp4")
    joined = " ".join(command)

    assert command[:2] == ["ffmpeg", "-y"]
    assert str(source) in command
    assert str(tmp_path / "segment.mp4") == command[-1]
    assert "-loop" not in command
    assert "overlay" not in joined
    assert "drawtext" not in joined
    assert "progress" not in joined.lower()
    assert "crop=1920:1080" in joined
    assert "unsharp" in joined


def test_original_enhance_command_preserves_portrait_geometry(tmp_path):
    source = tmp_path / "source.mp4"
    geometry = VideoGeometry(width=1080, height=1920)
    command = build_original_enhance_command(source, tmp_path / "release.mp4", geometry=geometry)
    joined = " ".join(command)

    assert "scale=1080:1920:flags=lanczos" in joined
    assert "scale=1920:1080" not in joined


def test_human_edit_command_uses_source_geometry_without_stretching(tmp_path):
    source = tmp_path / "portrait.mp4"
    segment = build_human_edit_storyboard(563.4)[1]
    geometry = VideoGeometry(width=1080, height=1920)
    command = build_human_edit_scene_command(source, segment, tmp_path / "segment.mp4", geometry=geometry)
    joined = " ".join(command)

    assert "scale=1188:2112:flags=lanczos" in joined
    assert "crop=1080:1920:54:53" in joined
    assert "crop=1920:1080" not in joined


def test_deprecated_cartoonize_policy_is_ignored_while_replace_audio_still_works(tmp_path):
    source = tmp_path / "portrait.mp4"
    segment = build_human_edit_storyboard(120.0)[1]
    command = build_human_edit_scene_command(
        source,
        segment,
        tmp_path / "segment.mp4",
        production_options={"visual_transform_policy": "cartoonize", "audio_policy": "replace_later"},
    )
    joined = " ".join(command)

    assert _visual_transform_policy({"visual_transform_policy": "cartoonize"}) == "none"
    assert "edgedetect" not in joined
    assert "negate" not in joined
    assert "scale=iw/2:ih/2:flags=fast_bilinear" not in joined
    assert "scale=iw*2:ih*2:flags=fast_bilinear" not in joined
    assert "eq=contrast=1.04" in joined
    assert "volume=0" in joined
    assert "loudnorm" not in joined


def test_presenter_remove_transform_uses_opaque_clean_matte_without_avatar(tmp_path):
    command = _build_presenter_remove_transform_command(
        tmp_path / "release.mp4",
        tmp_path / "presenter_removed.mp4",
        geometry=VideoGeometry(width=3840, height=2160),
    )
    joined = " ".join(command)

    assert command[:2] == ["ffmpeg", "-y"]
    assert "-filter_complex" in command
    assert "-i" in command
    assert joined.count(" -i ") == 1
    assert "-map 0:a:0?" in joined
    assert "-c:a copy" in joined
    assert "drawbox=x=iw*0.28" in joined
    assert "w=iw*0.42" in joined
    assert "color=0x071018@1.0" in joined
    assert "drawbox=x=0:y=ih*0.82:w=iw:h=ih*0.18" in joined
    assert "color=0x05080c@1.0" in joined
    assert "overlay" not in joined
    assert "edgedetect" not in joined
    assert "negate" not in joined


def test_face_only_transform_repairs_picture_in_picture_region_without_fake_asset(tmp_path):
    face_asset = tmp_path / "virtual_face.png"
    region = FaceOverlayRegion(x=1790, y=250, width=360, height=360, confidence=0.86, source="unit")
    command = _build_face_only_transform_command(
        tmp_path / "release.mp4",
        tmp_path / "face_only.mp4",
        geometry=VideoGeometry(width=3840, height=2160),
        face_path=face_asset,
        face_region=region,
    )
    joined = " ".join(command)

    assert command[:2] == ["ffmpeg", "-y"]
    assert str(face_asset) not in command
    assert "-map 0:a:0?" in joined
    assert "-c:a copy" in joined
    assert "delogo=x=1629:y=89:w=682:h=682:show=0" in joined
    assert "boxblur=" not in joined
    assert "alphamerge" not in joined
    assert "overlay=" not in joined
    assert "[1:v]" not in joined
    assert "drawbox=x=iw*0.28" not in joined
    assert "drawbox=x=0:y=ih*0.82" not in joined
    assert "overlay=x=main_w*0.33" not in joined
    assert "edgedetect" not in joined
    assert "negate" not in joined
    assert str(tmp_path / "face_only.mp4") == command[-1]


def test_face_only_transform_can_start_overlay_after_face_appears(tmp_path):
    region = FaceOverlayRegion(
        x=1674,
        y=18,
        width=240,
        height=240,
        confidence=0.79,
        source="skin_tone_median",
        start=4.212,
    )
    command = _build_face_only_transform_command(
        tmp_path / "release.mp4",
        tmp_path / "face_only.mp4",
        geometry=VideoGeometry(width=1920, height=1080),
        face_path=tmp_path / "virtual_face.png",
        face_region=region,
    )

    joined = " ".join(command)

    assert "delogo=x=1465:y=1:w=454:h=454:show=0:enable='gte(t,4.212)'" in joined


def test_virtual_face_asset_is_privacy_mask_without_drawn_facial_features(tmp_path):
    asset_path = tmp_path / "virtual_face.png"
    _write_virtual_face_asset(asset_path)

    image = Image.open(asset_path).convert("RGBA")
    alpha = image.getchannel("A")
    visible_pixels = sum(1 for value in alpha.getdata() if value > 0)
    dark_opaque_pixels = sum(
        1
        for red, green, blue, opacity in image.getdata()
        if opacity > 180 and red < 90 and green < 90 and blue < 90
    )

    assert image.size == (720, 720)
    assert alpha.getextrema() == (0, 255)
    assert alpha.getpixel((360, 360)) == 255
    assert visible_pixels < image.width * image.height * 0.68
    assert alpha.getpixel((0, 0)) == 0
    assert dark_opaque_pixels / max(1, visible_pixels) < 0.015


def test_face_region_estimator_detects_skin_oval_for_face_only_overlay(tmp_path):
    frame_path = tmp_path / "face_frame.jpg"
    image = Image.new("RGB", (1000, 600), "#182026")
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 500, 1000, 600), fill="#111111")
    draw.ellipse((424, 88, 566, 238), fill="#e1aa82")
    draw.pieslice((400, 52, 590, 184), start=185, end=360, fill="#222733")
    draw.rectangle((468, 222, 522, 292), fill="#d79b76")
    image.save(frame_path)

    region = _estimate_face_overlay_region_from_image(frame_path, VideoGeometry(width=1000, height=600))

    assert region.source == "skin_tone"
    assert region.confidence >= 0.45
    assert 385 <= region.x <= 450
    assert 55 <= region.y <= 115
    assert 145 <= region.width <= 230
    assert region.width == region.height


def test_geometry_from_probe_reads_first_video_stream():
    probe = {
        "streams": [
            {"codec_type": "audio", "channels": 2},
            {"codec_type": "video", "width": 1080, "height": 1920},
        ]
    }

    assert geometry_from_probe(probe) == VideoGeometry(width=1080, height=1920)


def test_creative_card_uses_source_geometry(tmp_path):
    output = tmp_path / "intro.png"

    build_creative_card("测试标题", "自动创作片头", VideoGeometry(width=720, height=1280), output)

    from PIL import Image

    with Image.open(output) as image:
        assert image.size == (720, 1280)


def test_cover_seek_seconds_stays_inside_short_videos():
    assert cover_seek_seconds(2.0) == 0.7
    assert cover_seek_seconds(60.0) == 8.0


def test_creative_cover_seek_uses_first_director_sample(tmp_path):
    paths = build_replicate_paths(tmp_path / "source.mp4", "creative-edit", output_root=tmp_path)
    paths.output_dir.mkdir(parents=True)
    paths.creative_plan.write_text(
        json.dumps(
            {
                "recommended_variant": {
                    "segments": [
                        {
                            "start": 122.673,
                            "duration": 10.705,
                            "source_sample_timestamp": 127.49,
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    assert cover_seek_seconds_for_mode(300.0, "creative-edit", paths) == 4.817
    assert cover_seek_seconds_for_mode(300.0, "human-edit", paths) == 8.0


def test_release_cover_title_prefers_source_download_metadata(tmp_path):
    paths = build_replicate_paths(tmp_path / "source.mp4", "creative-edit", output_root=tmp_path)
    paths.output_dir.mkdir(parents=True)
    paths.creative_plan.write_text(json.dumps({"title": "source"}), encoding="utf-8")
    (paths.output_dir / "source_download.json").write_text(
        json.dumps({"source_title": "世界盃劇本瘋了！德國出局後劇情徹底失控"}),
        encoding="utf-8",
    )

    assert _release_cover_title_from_paths(paths).startswith("世界盃劇本瘋了")


def test_release_cover_composition_crops_burned_subtitle_band(tmp_path):
    source = tmp_path / "frame.jpg"
    cover = tmp_path / "cover.jpg"
    image = Image.new("RGB", (640, 360), "#2b7a39")
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 300, 640, 360), fill="#ff0000")
    image.save(source)

    _compose_release_cover(source, cover, VideoGeometry(width=640, height=360), "世界盃劇本瘋了")

    with Image.open(cover) as output:
        assert output.size == (640, 360)
        assert output.getpixel((16, 344))[0] < 170


def test_contact_sheet_timestamps_start_with_first_frame():
    timestamps = contact_sheet_timestamps(300.0)

    assert timestamps[0] == 0.0
    assert len(timestamps) == 12
    assert timestamps[-1] == 298.0


def test_contact_sheet_timestamps_leave_seek_margin_for_short_outputs():
    timestamps = contact_sheet_timestamps(8.1)

    assert timestamps[0] == 0.0
    assert timestamps[-1] <= 6.1
    assert all(0.0 <= timestamp <= 6.1 for timestamp in timestamps)


def test_write_contact_sheet_pastes_first_frame_first(monkeypatch, tmp_path):
    colors = [
        (255, 0, 0),
        (0, 255, 0),
        (0, 0, 255),
        (255, 255, 0),
        (255, 0, 255),
        (0, 255, 255),
        (128, 0, 0),
        (0, 128, 0),
        (0, 0, 128),
        (128, 128, 0),
        (128, 0, 128),
        (0, 128, 128),
    ]

    def fake_run(command):
        frame_path = Path(command[-1])
        index = int(frame_path.stem.split("_")[1])
        Image.new("RGB", (480, 270), colors[index]).save(frame_path)

    monkeypatch.setattr("video_factory.replicate._run", fake_run)
    output = tmp_path / "contact_sheet.jpg"

    _write_contact_sheet(tmp_path / "release.mp4", output, 300.0)

    with Image.open(output) as sheet:
        assert sheet.size == (1920, 810)
        red, green, blue = sheet.getpixel((10, 10))
        assert red > 240
        assert green < 10
        assert blue < 10


def test_creative_title_from_source_removes_upload_and_link_noise():
    title = creative_title_from_source("1782114833-fc38ec-视频链接 xtonner.com 烤羊排下酒太香了#下酒菜.mp4")

    assert title == "烤羊排下酒太香了"


def test_quality_check_rejects_generated_intro_outro_in_release(tmp_path):
    paths = build_replicate_paths(tmp_path / "source.mp4", "creative-edit", output_root=tmp_path)
    paths.output_dir.mkdir(parents=True)
    paths.concat.write_text(
        "file '/tmp/creative_intro.mp4'\nfile '/tmp/body.mp4'\nfile '/tmp/creative_outro.mp4'\n",
        encoding="utf-8",
    )
    source_probe = {"streams": [{"codec_type": "video", "width": 1080, "height": 1920}], "format": {"duration": "30"}}
    output_probe = {"streams": [{"codec_type": "video", "width": 1080, "height": 1920}], "format": {"duration": "30"}}

    result = run_quality_checks(source_probe, output_probe, "creative-edit", paths)

    assert result["status"] == "failed"
    assert any(issue["code"] == "generated_card_in_release" for issue in result["issues"])


def test_quality_check_rejects_ai_visual_insert_budget_overuse(tmp_path):
    paths = build_replicate_paths(tmp_path / "source.mp4", "creative-edit", output_root=tmp_path)
    paths.output_dir.mkdir(parents=True)
    paths.concat.write_text("file '/tmp/body.mp4'\n", encoding="utf-8")
    _write_passing_content_files(paths)
    _write_passing_audio_files(paths)
    _write_passing_semantic_files(paths)
    _write_passing_transcript_files(paths)
    paths.creative_plan.write_text(
        json.dumps(
            {
                "profile": {"name": "generic_live", "confidence": 0.8, "evidence": ["unit"]},
                "source_duration": 240.0,
                "content_provider": {"name": "vision_lite", "status": "fallback", "message": "local cues"},
                "audio_provider": {"name": "ffmpeg_volumedetect", "status": "available", "message": "local cues"},
                "semantic_provider": {"name": "ocr_audio_semantic", "status": "available", "message": "semantic"},
                "recommended_variant": {
                    "segments": [
                        {
                            "start": index * 30.0,
                            "duration": 10.0,
                            "purpose": "source moment",
                            "semantic_role": role,
                            "content_tags": ["action"],
                            "audio_tags": ["speech_like"],
                            "chapter_title": f"章节 {index + 1}",
                            "creative_move": "evidence_cut",
                            "source_type": "source_video",
                            "synthetic": False,
                        }
                        for index, role in enumerate(["visual_hook", "context_bridge", "action_moment", "result_validation"])
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    paths.visual_insert_plan.write_text(
        json.dumps(
            {
                "strategy": "images2_contextual_inserts",
                "release_duration_seconds": 100.0,
                "max_ai_insert_ratio": 0.08,
                "inserts": [
                    {"insert_id": "ai_00", "after_index": 0, "duration": 5.0, "source_evidence": ["chapter"]},
                    {"insert_id": "ai_01", "after_index": 2, "duration": 5.0, "source_evidence": ["chapter"]},
                ],
            }
        ),
        encoding="utf-8",
    )
    paths.caption_timeline.write_text(json.dumps({"captions": [{"start": 0, "end": 1, "text": "字幕"}]}), encoding="utf-8")
    paths.subtitles.write_text("1\n00:00:00,000 --> 00:00:01,000\n字幕\n", encoding="utf-8")
    source_probe = {"streams": [{"codec_type": "video", "width": 1080, "height": 1920}], "format": {"duration": "240"}}
    output_probe = {"streams": [{"codec_type": "video", "width": 1080, "height": 1920}], "format": {"duration": "100"}}

    result = run_quality_checks(source_probe, output_probe, "creative-edit", paths)

    assert result["status"] == "failed"
    assert any(issue["code"] == "ai_visual_insert_budget_exceeded" for issue in result["issues"])


def test_quality_check_rejects_consecutive_ai_visual_inserts(tmp_path):
    paths = build_replicate_paths(tmp_path / "source.mp4", "creative-edit", output_root=tmp_path)
    paths.output_dir.mkdir(parents=True)
    paths.concat.write_text("file '/tmp/body.mp4'\n", encoding="utf-8")
    _write_passing_content_files(paths)
    _write_passing_audio_files(paths)
    _write_passing_semantic_files(paths)
    _write_passing_transcript_files(paths)
    paths.creative_plan.write_text(
        json.dumps(
            {
                "profile": {"name": "generic_live", "confidence": 0.8, "evidence": ["unit"]},
                "source_duration": 240.0,
                "content_provider": {"name": "vision_lite", "status": "fallback", "message": "local cues"},
                "audio_provider": {"name": "ffmpeg_volumedetect", "status": "available", "message": "local cues"},
                "semantic_provider": {"name": "ocr_audio_semantic", "status": "available", "message": "semantic"},
                "recommended_variant": {
                    "segments": [
                        {
                            "start": index * 30.0,
                            "duration": 10.0,
                            "purpose": "source moment",
                            "semantic_role": role,
                            "content_tags": ["action"],
                            "audio_tags": ["speech_like"],
                            "chapter_title": f"章节 {index + 1}",
                            "creative_move": "evidence_cut",
                            "source_type": "source_video",
                            "synthetic": False,
                        }
                        for index, role in enumerate(["visual_hook", "context_bridge", "action_moment", "result_validation"])
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    paths.visual_insert_plan.write_text(
        json.dumps(
            {
                "strategy": "images2_contextual_inserts",
                "release_duration_seconds": 100.0,
                "max_ai_insert_ratio": 0.08,
                "inserts": [
                    {"insert_id": "ai_00", "after_index": 1, "duration": 2.0, "source_evidence": ["chapter"]},
                    {"insert_id": "ai_01", "after_index": 1, "duration": 2.0, "source_evidence": ["chapter"]},
                ],
            }
        ),
        encoding="utf-8",
    )
    paths.caption_timeline.write_text(json.dumps({"captions": [{"start": 0, "end": 1, "text": "字幕"}]}), encoding="utf-8")
    paths.subtitles.write_text("1\n00:00:00,000 --> 00:00:01,000\n字幕\n", encoding="utf-8")
    source_probe = {"streams": [{"codec_type": "video", "width": 1080, "height": 1920}], "format": {"duration": "240"}}
    output_probe = {"streams": [{"codec_type": "video", "width": 1080, "height": 1920}], "format": {"duration": "100"}}

    result = run_quality_checks(source_probe, output_probe, "creative-edit", paths)

    assert result["status"] == "failed"
    assert any(issue["code"] == "ai_visual_insert_consecutive" for issue in result["issues"])


def test_quality_check_rejects_synthetic_creative_plan_segments(tmp_path):
    paths = build_replicate_paths(tmp_path / "source.mp4", "creative-edit", output_root=tmp_path)
    paths.output_dir.mkdir(parents=True)
    paths.concat.write_text("file '/tmp/body.mp4'\n", encoding="utf-8")
    _write_passing_content_files(paths)
    _write_passing_audio_files(paths)
    _write_passing_semantic_files(paths)
    _write_passing_transcript_files(paths)
    paths.creative_plan.write_text(
        json.dumps(
            {
                "profile": {"name": "tutorial_screen", "confidence": 0.8, "evidence": ["title_hint:教程"]},
                "source_duration": 30.0,
                "content_provider": {"name": "vision_lite", "status": "fallback", "message": "local cues"},
                "audio_provider": {"name": "ffmpeg_volumedetect", "status": "available", "message": "local cues"},
                "semantic_provider": {"name": "ocr_audio_semantic", "status": "available", "message": "semantic"},
                "recommended_variant": {
                    "segments": [
                        {
                            "start": 0.0,
                            "duration": 4.0,
                            "purpose": "synthetic title card",
                            "semantic_role": "tutorial_hook",
                            "content_tags": ["subtitle"],
                            "audio_tags": ["speech_like"],
                            "chapter_title": "安装入口",
                            "creative_move": "cold_open",
                            "source_type": "generated_card",
                            "synthetic": True,
                        },
                        {
                            "start": 8.0,
                            "duration": 4.0,
                            "purpose": "source step",
                            "semantic_role": "operation_step",
                            "content_tags": ["interface"],
                            "audio_tags": ["speech_like"],
                            "chapter_title": "DeepSeek API Key",
                            "creative_move": "action_chain",
                            "source_type": "source_video",
                            "synthetic": False,
                        },
                        {
                            "start": 16.0,
                            "duration": 4.0,
                            "purpose": "source proof",
                            "semantic_role": "result_validation",
                            "content_tags": ["validation"],
                            "audio_tags": ["speech_like"],
                            "chapter_title": "本地路由与验证",
                            "creative_move": "proof_close",
                            "source_type": "source_video",
                            "synthetic": False,
                        },
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    source_probe = {"streams": [{"codec_type": "video", "width": 1080, "height": 1920}], "format": {"duration": "30"}}
    output_probe = {"streams": [{"codec_type": "video", "width": 1080, "height": 1920}], "format": {"duration": "20"}}

    result = run_quality_checks(source_probe, output_probe, "creative-edit", paths)

    assert result["status"] == "failed"
    assert any(issue["code"] == "creative_plan_contains_synthetic_segment" for issue in result["issues"])


def test_quality_check_rejects_template_like_source_frame_as_hook(tmp_path):
    paths = build_replicate_paths(tmp_path / "source.mp4", "creative-edit", output_root=tmp_path)
    paths.output_dir.mkdir(parents=True)
    paths.concat.write_text("file '/tmp/body.mp4'\n", encoding="utf-8")
    _write_passing_content_files(paths)
    _write_passing_audio_files(paths)
    _write_passing_semantic_files(paths)
    _write_passing_transcript_files(paths)
    paths.creative_plan.write_text(
        json.dumps(
            {
                "profile": {"name": "tutorial_screen", "confidence": 0.8, "evidence": ["title_hint:教程"]},
                "source_duration": 30.0,
                "content_provider": {"name": "vision_lite", "status": "fallback", "message": "local cues"},
                "audio_provider": {"name": "ffmpeg_volumedetect", "status": "available", "message": "local cues"},
                "semantic_provider": {"name": "ocr_audio_semantic", "status": "available", "message": "semantic"},
                "recommended_variant": {
                    "segments": [
                        {
                            "start": 0.0,
                            "duration": 4.0,
                            "purpose": "source title page",
                            "semantic_role": "tutorial_hook",
                            "content_tags": ["subtitle"],
                            "audio_tags": ["speech_like"],
                            "chapter_title": "安装入口",
                            "creative_move": "cold_open",
                            "source_type": "source_video",
                            "synthetic": False,
                            "visual_risk_tags": ["template_like_source_frame"],
                        },
                        {
                            "start": 8.0,
                            "duration": 4.0,
                            "purpose": "source step",
                            "semantic_role": "operation_step",
                            "content_tags": ["interface"],
                            "audio_tags": ["speech_like"],
                            "chapter_title": "DeepSeek API Key",
                            "creative_move": "action_chain",
                        },
                        {
                            "start": 16.0,
                            "duration": 4.0,
                            "purpose": "source proof",
                            "semantic_role": "result_validation",
                            "content_tags": ["validation"],
                            "audio_tags": ["speech_like"],
                            "chapter_title": "本地路由与验证",
                            "creative_move": "proof_close",
                        },
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    source_probe = {"streams": [{"codec_type": "video", "width": 1080, "height": 1920}], "format": {"duration": "30"}}
    output_probe = {"streams": [{"codec_type": "video", "width": 1080, "height": 1920}], "format": {"duration": "20"}}

    result = run_quality_checks(source_probe, output_probe, "creative-edit", paths)

    assert result["status"] == "failed"
    assert any(issue["code"] == "creative_plan_template_like_hook" for issue in result["issues"])


def test_quality_check_rejects_template_like_source_frame_overuse(tmp_path):
    paths = build_replicate_paths(tmp_path / "source.mp4", "creative-edit", output_root=tmp_path)
    paths.output_dir.mkdir(parents=True)
    paths.concat.write_text("file '/tmp/body.mp4'\n", encoding="utf-8")
    _write_passing_content_files(paths)
    _write_passing_audio_files(paths, cue_count=10)
    _write_passing_semantic_files(paths)
    _write_passing_transcript_files(paths)
    roles = ["tutorial_hook", "interface_state", "operation_step", "configuration_detail", "result_validation"]
    moves = ["cold_open", "reset_context", "action_chain", "decision_point", "proof_close"]
    paths.creative_plan.write_text(
        json.dumps(
            {
                "profile": {"name": "tutorial_screen", "confidence": 0.8, "evidence": ["title_hint:教程"]},
                "source_duration": 220.0,
                "content_provider": {"name": "vision_lite", "status": "fallback", "message": "local cues"},
                "audio_provider": {"name": "ffmpeg_volumedetect", "status": "available", "message": "local cues"},
                "semantic_provider": {"name": "ocr_audio_semantic", "status": "available", "message": "semantic"},
                "recommended_variant": {
                    "segments": [
                        {
                            "start": index * 18.0,
                            "duration": 8.0,
                            "purpose": "source segment",
                            "semantic_role": roles[index % len(roles)],
                            "content_tags": ["interface"],
                            "content_evidence": "interface_layout",
                            "audio_tags": ["speech_like"],
                            "audio_evidence": "mean_volume:-18.0dB",
                            "chapter_title": f"章节 {index}",
                            "creative_move": moves[index % len(moves)],
                            "source_type": "source_video",
                            "synthetic": False,
                            "visual_risk_tags": ["template_like_source_frame"] if index in {2, 3, 5, 7} else [],
                        }
                        for index in range(10)
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    source_probe = {"streams": [{"codec_type": "video", "width": 1920, "height": 1080}], "format": {"duration": "220"}}
    output_probe = {"streams": [{"codec_type": "video", "width": 1920, "height": 1080}], "format": {"duration": "80"}}

    result = run_quality_checks(source_probe, output_probe, "creative-edit", paths)

    assert result["status"] == "failed"
    assert any(issue["code"] == "creative_plan_template_like_overuse" for issue in result["issues"])
    assert result["checks"]["creative_plan_template_like_budget"] is False


def test_quality_check_rejects_template_like_finish_run(tmp_path):
    paths = build_replicate_paths(tmp_path / "source.mp4", "creative-edit", output_root=tmp_path)
    paths.output_dir.mkdir(parents=True)
    paths.concat.write_text("file '/tmp/body.mp4'\n", encoding="utf-8")
    _write_passing_content_files(paths)
    _write_passing_audio_files(paths, cue_count=8)
    _write_passing_semantic_files(paths)
    _write_passing_transcript_files(paths)
    segments = []
    roles = ["tutorial_hook", "interface_state", "operation_step", "configuration_detail"]
    moves = ["cold_open", "reset_context", "action_chain", "decision_point"]
    for index in range(5):
        segments.append(
            {
                "start": index * 18.0,
                "duration": 8.0,
                "purpose": "source segment",
                "semantic_role": roles[index % len(roles)],
                "content_tags": ["interface"],
                "content_evidence": "interface_layout",
                "audio_tags": ["speech_like"],
                "audio_evidence": "mean_volume:-18.0dB",
                "chapter_title": f"章节 {index}",
                "creative_move": moves[index % len(moves)],
                "source_type": "source_video",
                "synthetic": False,
                "visual_risk_tags": [],
            }
        )
    segments.extend(
        [
            {
                "start": 120.0 + offset * 18.0,
                "duration": 8.0,
                "purpose": "template-like finish card",
                "semantic_role": "result_validation",
                "content_tags": ["subtitle"],
                "content_evidence": "subtitle_band",
                "audio_tags": ["speech_like"],
                "audio_evidence": "mean_volume:-18.0dB",
                "chapter_title": "结果验证",
                "creative_move": "proof_close",
                "source_type": "source_video",
                "synthetic": False,
                "visual_risk_tags": ["template_like_source_frame"],
            }
            for offset in range(3)
        ]
    )
    paths.creative_plan.write_text(
        json.dumps(
            {
                "profile": {"name": "tutorial_screen", "confidence": 0.8, "evidence": ["title_hint:教程"]},
                "source_duration": 240.0,
                "content_provider": {"name": "vision_lite", "status": "fallback", "message": "local cues"},
                "audio_provider": {"name": "ffmpeg_volumedetect", "status": "available", "message": "local cues"},
                "semantic_provider": {"name": "ocr_audio_semantic", "status": "available", "message": "semantic"},
                "recommended_variant": {"segments": segments},
            }
        ),
        encoding="utf-8",
    )
    source_probe = {"streams": [{"codec_type": "video", "width": 1920, "height": 1080}], "format": {"duration": "240"}}
    output_probe = {"streams": [{"codec_type": "video", "width": 1920, "height": 1080}], "format": {"duration": "64"}}

    result = run_quality_checks(source_probe, output_probe, "creative-edit", paths)

    assert result["status"] == "failed"
    assert any(issue["code"] == "creative_plan_template_like_finish_run" for issue in result["issues"])


def test_quality_check_rejects_cold_open_with_long_preroll(tmp_path):
    paths = build_replicate_paths(tmp_path / "source.mp4", "creative-edit", output_root=tmp_path)
    paths.output_dir.mkdir(parents=True)
    paths.concat.write_text("file '/tmp/body.mp4'\n", encoding="utf-8")
    _write_passing_content_files(paths)
    _write_passing_audio_files(paths)
    _write_passing_semantic_files(paths)
    _write_passing_transcript_files(paths)
    paths.creative_plan.write_text(
        json.dumps(
            {
                "profile": {"name": "tutorial_screen", "confidence": 0.8, "evidence": ["title_hint:教程"]},
                "source_duration": 180.0,
                "content_provider": {"name": "vision_lite", "status": "fallback", "message": "local cues"},
                "audio_provider": {"name": "ffmpeg_volumedetect", "status": "available", "message": "local cues"},
                "semantic_provider": {"name": "ocr_audio_semantic", "status": "available", "message": "semantic"},
                "recommended_variant": {
                    "segments": [
                        {
                            "start": 122.673,
                            "duration": 10.705,
                            "purpose": "source operation",
                            "semantic_role": "tutorial_hook",
                            "content_tags": ["install", "subtitle", "text_dense"],
                            "audio_tags": ["speech_like"],
                            "chapter_title": "安装入口",
                            "creative_move": "cold_open",
                            "source_sample_index": 8,
                            "source_sample_timestamp": 127.49,
                            "source_type": "source_video",
                            "synthetic": False,
                            "visual_risk_tags": [],
                        },
                        {
                            "start": 140.0,
                            "duration": 4.0,
                            "purpose": "source step",
                            "semantic_role": "operation_step",
                            "content_tags": ["interface"],
                            "audio_tags": ["speech_like"],
                            "chapter_title": "DeepSeek API Key",
                            "creative_move": "action_chain",
                        },
                        {
                            "start": 150.0,
                            "duration": 4.0,
                            "purpose": "source proof",
                            "semantic_role": "result_validation",
                            "content_tags": ["validation"],
                            "audio_tags": ["speech_like"],
                            "chapter_title": "本地路由与验证",
                            "creative_move": "proof_close",
                        },
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    source_probe = {"streams": [{"codec_type": "video", "width": 1080, "height": 1920}], "format": {"duration": "180"}}
    output_probe = {"streams": [{"codec_type": "video", "width": 1080, "height": 1920}], "format": {"duration": "20"}}

    result = run_quality_checks(source_probe, output_probe, "creative-edit", paths)

    assert result["status"] == "failed"
    assert any(issue["code"] == "creative_plan_hook_preroll_too_long" for issue in result["issues"])
    assert result["checks"]["creative_plan_hook_starts_near_sample"] is False


def test_quality_check_accepts_source_only_creative_cut(tmp_path):
    paths = build_replicate_paths(tmp_path / "source.mp4", "creative-edit", output_root=tmp_path)
    paths.output_dir.mkdir(parents=True)
    paths.concat.write_text("file '/tmp/body.mp4'\n", encoding="utf-8")
    _write_passing_content_files(paths)
    _write_passing_audio_files(paths, cue_count=3)
    _write_passing_semantic_files(paths)
    _write_passing_transcript_files(paths)
    paths.creative_plan.write_text(
        json.dumps(
            {
                "profile": {"name": "food_social", "confidence": 0.8, "evidence": ["title_hint:美食"]},
                "content_provider": {"name": "vision_lite", "status": "fallback", "message": "local cues"},
                "content_coverage": {"cue_count": 3, "tagged_count": 3, "recognized_text_count": 0},
                "audio_provider": {"name": "ffmpeg_volumedetect", "status": "available", "message": "local audio cues"},
                "audio_coverage": {"cue_count": 3, "tagged_count": 3, "speech_like_count": 3},
                "semantic_provider": {
                    "name": "ocr_audio_semantic",
                    "status": "available",
                    "message": "derived semantic chapters",
                },
                "recommended_variant": {
                    "segments": [
                        {
                            "start": 0.0,
                            "duration": 4.0,
                            "purpose": "保留真实动作。",
                            "semantic_role": "food_hook",
                            "chapter_title": "安装入口",
                            "content_tags": ["subtitle"],
                            "content_evidence": "subtitle_band",
                            "audio_tags": ["speech_like"],
                            "audio_evidence": "mean_volume:-24.0dB",
                        },
                        {
                            "start": 8.0,
                            "duration": 4.0,
                            "purpose": "保留成品细节。",
                            "semantic_role": "prep_action",
                            "chapter_title": "DeepSeek API Key",
                            "content_tags": ["interface"],
                            "content_evidence": "interface_layout",
                            "audio_tags": ["speech_like"],
                            "audio_evidence": "mean_volume:-24.0dB",
                        },
                        {
                            "start": 16.0,
                            "duration": 4.0,
                            "purpose": "保留收束。",
                            "semantic_role": "final_payoff",
                            "chapter_title": "本地路由与验证",
                            "content_tags": ["validation"],
                            "content_evidence": "result_screen",
                            "audio_tags": ["speech_like"],
                            "audio_evidence": "mean_volume:-24.0dB",
                        },
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    source_probe = {"streams": [{"codec_type": "video", "width": 1080, "height": 1920}], "format": {"duration": "30"}}
    output_probe = {"streams": [{"codec_type": "video", "width": 1080, "height": 1920}], "format": {"duration": "30"}}

    result = run_quality_checks(source_probe, output_probe, "creative-edit", paths)

    assert result["status"] == "passed"
    assert result["issues"] == []


def test_quality_check_requires_transcript_analysis_for_creative_edit(tmp_path):
    paths = build_replicate_paths(tmp_path / "source.mp4", "creative-edit", output_root=tmp_path)
    paths.output_dir.mkdir(parents=True)
    paths.concat.write_text("file '/tmp/body.mp4'\n", encoding="utf-8")
    _write_passing_content_files(paths)
    _write_passing_audio_files(paths, cue_count=3)
    _write_passing_semantic_files(paths)
    paths.creative_plan.write_text(
        json.dumps(
            {
                "profile": {"name": "tutorial_screen", "confidence": 0.8, "evidence": ["visual_screen_signal"]},
                "content_provider": {"name": "vision_lite", "status": "fallback", "message": "local cues"},
                "audio_provider": {"name": "ffmpeg_volumedetect", "status": "available", "message": "local cues"},
                "semantic_provider": {
                    "name": "ocr_audio_transcript_semantic",
                    "status": "available",
                    "message": "derived semantic chapters",
                },
                "recommended_variant": {
                    "segments": [
                        {
                            "start": 0.0,
                            "duration": 4.0,
                            "purpose": "hook",
                            "semantic_role": "tutorial_hook",
                            "chapter_title": "安装入口",
                            "content_tags": ["subtitle"],
                            "content_evidence": "subtitle_band",
                            "audio_tags": ["speech_like"],
                            "audio_evidence": "mean_volume:-24.0dB",
                        },
                        {
                            "start": 8.0,
                            "duration": 4.0,
                            "purpose": "setup",
                            "semantic_role": "interface_state",
                            "chapter_title": "DeepSeek API Key",
                            "content_tags": ["interface"],
                            "content_evidence": "interface_layout",
                            "audio_tags": ["speech_like"],
                            "audio_evidence": "mean_volume:-24.0dB",
                        },
                        {
                            "start": 16.0,
                            "duration": 4.0,
                            "purpose": "result",
                            "semantic_role": "result_validation",
                            "chapter_title": "本地路由与验证",
                            "content_tags": ["validation"],
                            "content_evidence": "result_screen",
                            "audio_tags": ["speech_like"],
                            "audio_evidence": "mean_volume:-24.0dB",
                        },
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    source_probe = {"streams": [{"codec_type": "video", "width": 1080, "height": 1920}], "format": {"duration": "30"}}
    output_probe = {"streams": [{"codec_type": "video", "width": 1080, "height": 1920}], "format": {"duration": "24"}}

    result = run_quality_checks(source_probe, output_probe, "creative-edit", paths)

    assert result["status"] == "failed"
    assert any(issue["code"] == "missing_transcript_analysis" for issue in result["issues"])


def test_quality_check_requires_semantic_timeline_for_creative_edit(tmp_path):
    paths = build_replicate_paths(tmp_path / "source.mp4", "creative-edit", output_root=tmp_path)
    paths.output_dir.mkdir(parents=True)
    paths.concat.write_text("file '/tmp/body.mp4'\n", encoding="utf-8")
    _write_passing_content_files(paths)
    _write_passing_audio_files(paths, cue_count=3)
    paths.creative_plan.write_text(
        json.dumps(
            {
                "profile": {"name": "tutorial_screen", "confidence": 0.8, "evidence": ["visual_screen_signal"]},
                "content_provider": {"name": "vision_lite", "status": "fallback", "message": "local cues"},
                "audio_provider": {"name": "ffmpeg_volumedetect", "status": "available", "message": "local cues"},
                "semantic_provider": {"name": "ocr_audio_semantic", "status": "available", "message": "local"},
                "recommended_variant": {
                    "segments": [
                        {
                            "start": 0.0,
                            "duration": 4.0,
                            "purpose": "hook",
                            "semantic_role": "tutorial_hook",
                            "chapter_title": "安装入口",
                            "content_tags": ["subtitle"],
                            "content_evidence": "subtitle_band",
                            "audio_tags": ["speech_like"],
                            "audio_evidence": "mean_volume:-24.0dB",
                        },
                        {
                            "start": 8.0,
                            "duration": 4.0,
                            "purpose": "setup",
                            "semantic_role": "interface_state",
                            "chapter_title": "DeepSeek API Key",
                            "content_tags": ["interface"],
                            "content_evidence": "interface_layout",
                            "audio_tags": ["speech_like"],
                            "audio_evidence": "mean_volume:-24.0dB",
                        },
                        {
                            "start": 16.0,
                            "duration": 4.0,
                            "purpose": "result",
                            "semantic_role": "result_validation",
                            "chapter_title": "本地路由与验证",
                            "content_tags": ["validation"],
                            "content_evidence": "result_screen",
                            "audio_tags": ["speech_like"],
                            "audio_evidence": "mean_volume:-24.0dB",
                        },
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    source_probe = {"streams": [{"codec_type": "video", "width": 1080, "height": 1920}], "format": {"duration": "30"}}
    output_probe = {"streams": [{"codec_type": "video", "width": 1080, "height": 1920}], "format": {"duration": "24"}}

    result = run_quality_checks(source_probe, output_probe, "creative-edit", paths)

    assert result["status"] == "failed"
    assert any(issue["code"] == "missing_semantic_timeline" for issue in result["issues"])


def test_quality_check_rejects_creative_plan_without_semantic_provider(tmp_path):
    paths = build_replicate_paths(tmp_path / "source.mp4", "creative-edit", output_root=tmp_path)
    paths.output_dir.mkdir(parents=True)
    paths.concat.write_text("file '/tmp/body.mp4'\n", encoding="utf-8")
    _write_passing_content_files(paths)
    _write_passing_audio_files(paths, cue_count=3)
    _write_passing_semantic_files(paths)
    paths.creative_plan.write_text(
        json.dumps(
            {
                "profile": {"name": "tutorial_screen", "confidence": 0.8, "evidence": ["visual_screen_signal"]},
                "content_provider": {"name": "vision_lite", "status": "fallback", "message": "local cues"},
                "audio_provider": {"name": "ffmpeg_volumedetect", "status": "available", "message": "local cues"},
                "recommended_variant": {
                    "segments": [
                        {
                            "start": 0.0,
                            "duration": 4.0,
                            "purpose": "hook",
                            "semantic_role": "tutorial_hook",
                            "chapter_title": "安装入口",
                            "content_tags": ["subtitle"],
                            "content_evidence": "subtitle_band",
                            "audio_tags": ["speech_like"],
                            "audio_evidence": "mean_volume:-24.0dB",
                        },
                        {
                            "start": 8.0,
                            "duration": 4.0,
                            "purpose": "setup",
                            "semantic_role": "interface_state",
                            "chapter_title": "DeepSeek API Key",
                            "content_tags": ["interface"],
                            "content_evidence": "interface_layout",
                            "audio_tags": ["speech_like"],
                            "audio_evidence": "mean_volume:-24.0dB",
                        },
                        {
                            "start": 16.0,
                            "duration": 4.0,
                            "purpose": "result",
                            "semantic_role": "result_validation",
                            "chapter_title": "本地路由与验证",
                            "content_tags": ["validation"],
                            "content_evidence": "result_screen",
                            "audio_tags": ["speech_like"],
                            "audio_evidence": "mean_volume:-24.0dB",
                        },
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    source_probe = {"streams": [{"codec_type": "video", "width": 1080, "height": 1920}], "format": {"duration": "30"}}
    output_probe = {"streams": [{"codec_type": "video", "width": 1080, "height": 1920}], "format": {"duration": "24"}}

    result = run_quality_checks(source_probe, output_probe, "creative-edit", paths)

    assert result["status"] == "failed"
    assert any(issue["code"] == "creative_plan_missing_semantic_provider" for issue in result["issues"])


def test_quality_check_rejects_creative_plan_without_chapter_mapping(tmp_path):
    paths = build_replicate_paths(tmp_path / "source.mp4", "creative-edit", output_root=tmp_path)
    paths.output_dir.mkdir(parents=True)
    paths.concat.write_text("file '/tmp/body.mp4'\n", encoding="utf-8")
    _write_passing_content_files(paths)
    _write_passing_audio_files(paths, cue_count=3)
    _write_passing_semantic_files(paths)
    paths.creative_plan.write_text(
        json.dumps(
            {
                "profile": {"name": "tutorial_screen", "confidence": 0.8, "evidence": ["visual_screen_signal"]},
                "content_provider": {"name": "vision_lite", "status": "fallback", "message": "local cues"},
                "audio_provider": {"name": "ffmpeg_volumedetect", "status": "available", "message": "local cues"},
                "semantic_provider": {
                    "name": "ocr_audio_semantic",
                    "status": "available",
                    "message": "derived semantic chapters",
                },
                "recommended_variant": {
                    "segments": [
                        {
                            "start": 0.0,
                            "duration": 4.0,
                            "purpose": "hook",
                            "semantic_role": "tutorial_hook",
                            "chapter_title": "安装入口",
                            "content_tags": ["subtitle"],
                            "content_evidence": "subtitle_band",
                            "audio_tags": ["speech_like"],
                            "audio_evidence": "mean_volume:-24.0dB",
                        },
                        {
                            "start": 8.0,
                            "duration": 4.0,
                            "purpose": "setup",
                            "semantic_role": "interface_state",
                            "content_tags": ["interface"],
                            "content_evidence": "interface_layout",
                            "audio_tags": ["speech_like"],
                            "audio_evidence": "mean_volume:-24.0dB",
                        },
                        {
                            "start": 16.0,
                            "duration": 4.0,
                            "purpose": "result",
                            "semantic_role": "result_validation",
                            "content_tags": ["validation"],
                            "content_evidence": "result_screen",
                            "audio_tags": ["speech_like"],
                            "audio_evidence": "mean_volume:-24.0dB",
                        },
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    source_probe = {"streams": [{"codec_type": "video", "width": 1080, "height": 1920}], "format": {"duration": "30"}}
    output_probe = {"streams": [{"codec_type": "video", "width": 1080, "height": 1920}], "format": {"duration": "24"}}

    result = run_quality_checks(source_probe, output_probe, "creative-edit", paths)

    assert result["status"] == "failed"
    assert any(issue["code"] == "creative_plan_weak_semantic_chapters" for issue in result["issues"])


def test_quality_check_requires_content_analysis_for_creative_edit(tmp_path):
    paths = build_replicate_paths(tmp_path / "source.mp4", "creative-edit", output_root=tmp_path)
    paths.output_dir.mkdir(parents=True)
    paths.concat.write_text("file '/tmp/body.mp4'\n", encoding="utf-8")
    paths.creative_plan.write_text(
        json.dumps(
            {
                "profile": {"name": "tutorial_screen", "confidence": 0.8, "evidence": ["visual_screen_signal"]},
                "content_provider": {"name": "vision_lite", "status": "fallback", "message": "local cues"},
                "recommended_variant": {
                    "segments": [
                        {
                            "start": 0.0,
                            "duration": 4.0,
                            "purpose": "hook",
                            "semantic_role": "tutorial_hook",
                            "content_tags": ["subtitle"],
                            "content_evidence": "subtitle_band",
                        },
                        {
                            "start": 8.0,
                            "duration": 4.0,
                            "purpose": "setup",
                            "semantic_role": "interface_state",
                            "content_tags": ["interface"],
                            "content_evidence": "interface_layout",
                        },
                        {
                            "start": 16.0,
                            "duration": 4.0,
                            "purpose": "result",
                            "semantic_role": "result_validation",
                            "content_tags": ["validation"],
                            "content_evidence": "result_screen",
                        },
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    source_probe = {"streams": [{"codec_type": "video", "width": 1080, "height": 1920}], "format": {"duration": "30"}}
    output_probe = {"streams": [{"codec_type": "video", "width": 1080, "height": 1920}], "format": {"duration": "24"}}

    result = run_quality_checks(source_probe, output_probe, "creative-edit", paths)

    assert result["status"] == "failed"
    assert any(issue["code"] == "missing_content_analysis" for issue in result["issues"])


def test_quality_check_requires_audio_analysis_for_creative_edit(tmp_path):
    paths = build_replicate_paths(tmp_path / "source.mp4", "creative-edit", output_root=tmp_path)
    paths.output_dir.mkdir(parents=True)
    paths.concat.write_text("file '/tmp/body.mp4'\n", encoding="utf-8")
    _write_passing_content_files(paths)
    paths.creative_plan.write_text(
        json.dumps(
            {
                "profile": {"name": "tutorial_screen", "confidence": 0.8, "evidence": ["visual_screen_signal"]},
                "content_provider": {"name": "vision_lite", "status": "fallback", "message": "local cues"},
                "audio_provider": {"name": "ffmpeg_volumedetect", "status": "available"},
                "recommended_variant": {
                    "segments": [
                        {
                            "start": 0.0,
                            "duration": 4.0,
                            "purpose": "hook",
                            "semantic_role": "tutorial_hook",
                            "content_tags": ["subtitle"],
                            "content_evidence": "subtitle_band",
                            "audio_tags": ["speech_like"],
                            "audio_evidence": "mean_volume:-24.0dB",
                        },
                        {
                            "start": 8.0,
                            "duration": 4.0,
                            "purpose": "setup",
                            "semantic_role": "interface_state",
                            "content_tags": ["interface"],
                            "content_evidence": "interface_layout",
                            "audio_tags": ["speech_like"],
                            "audio_evidence": "mean_volume:-24.0dB",
                        },
                        {
                            "start": 16.0,
                            "duration": 4.0,
                            "purpose": "result",
                            "semantic_role": "result_validation",
                            "content_tags": ["validation"],
                            "content_evidence": "result_screen",
                            "audio_tags": ["speech_like"],
                            "audio_evidence": "mean_volume:-24.0dB",
                        },
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    source_probe = {"streams": [{"codec_type": "video", "width": 1080, "height": 1920}], "format": {"duration": "30"}}
    output_probe = {"streams": [{"codec_type": "video", "width": 1080, "height": 1920}], "format": {"duration": "24"}}

    result = run_quality_checks(source_probe, output_probe, "creative-edit", paths)

    assert result["status"] == "failed"
    assert any(issue["code"] == "missing_audio_analysis" for issue in result["issues"])


def test_quality_check_rejects_creative_plan_without_content_provider(tmp_path):
    paths = build_replicate_paths(tmp_path / "source.mp4", "creative-edit", output_root=tmp_path)
    paths.output_dir.mkdir(parents=True)
    paths.concat.write_text("file '/tmp/body.mp4'\n", encoding="utf-8")
    _write_passing_content_files(paths)
    paths.creative_plan.write_text(
        json.dumps(
            {
                "profile": {"name": "tutorial_screen", "confidence": 0.8, "evidence": ["visual_screen_signal"]},
                "recommended_variant": {
                    "segments": [
                        {
                            "start": 0.0,
                            "duration": 4.0,
                            "purpose": "hook",
                            "semantic_role": "tutorial_hook",
                            "content_tags": ["subtitle"],
                            "content_evidence": "subtitle_band",
                        },
                        {
                            "start": 8.0,
                            "duration": 4.0,
                            "purpose": "setup",
                            "semantic_role": "interface_state",
                            "content_tags": ["interface"],
                            "content_evidence": "interface_layout",
                        },
                        {
                            "start": 16.0,
                            "duration": 4.0,
                            "purpose": "result",
                            "semantic_role": "result_validation",
                            "content_tags": ["validation"],
                            "content_evidence": "result_screen",
                        },
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    source_probe = {"streams": [{"codec_type": "video", "width": 1080, "height": 1920}], "format": {"duration": "30"}}
    output_probe = {"streams": [{"codec_type": "video", "width": 1080, "height": 1920}], "format": {"duration": "24"}}

    result = run_quality_checks(source_probe, output_probe, "creative-edit", paths)

    assert result["status"] == "failed"
    assert any(issue["code"] == "creative_plan_missing_content_provider" for issue in result["issues"])


def test_quality_check_rejects_weak_content_evidence(tmp_path):
    paths = build_replicate_paths(tmp_path / "source.mp4", "creative-edit", output_root=tmp_path)
    paths.output_dir.mkdir(parents=True)
    paths.concat.write_text("file '/tmp/body.mp4'\n", encoding="utf-8")
    _write_passing_content_files(paths)
    paths.creative_plan.write_text(
        json.dumps(
            {
                "profile": {"name": "tutorial_screen", "confidence": 0.8, "evidence": ["visual_screen_signal"]},
                "content_provider": {"name": "vision_lite", "status": "fallback", "message": "local cues"},
                "recommended_variant": {
                    "segments": [
                        {
                            "start": 0.0,
                            "duration": 4.0,
                            "purpose": "hook",
                            "semantic_role": "tutorial_hook",
                            "content_tags": ["subtitle"],
                            "content_evidence": "subtitle_band",
                        },
                        {"start": 8.0, "duration": 4.0, "purpose": "setup", "semantic_role": "interface_state"},
                        {"start": 16.0, "duration": 4.0, "purpose": "step", "semantic_role": "operation_step"},
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    source_probe = {"streams": [{"codec_type": "video", "width": 1080, "height": 1920}], "format": {"duration": "30"}}
    output_probe = {"streams": [{"codec_type": "video", "width": 1080, "height": 1920}], "format": {"duration": "24"}}

    result = run_quality_checks(source_probe, output_probe, "creative-edit", paths)

    assert result["status"] == "failed"
    assert any(issue["code"] == "creative_plan_weak_content_evidence" for issue in result["issues"])


def test_quality_check_rejects_weak_audio_evidence(tmp_path):
    paths = build_replicate_paths(tmp_path / "source.mp4", "creative-edit", output_root=tmp_path)
    paths.output_dir.mkdir(parents=True)
    paths.concat.write_text("file '/tmp/body.mp4'\n", encoding="utf-8")
    _write_passing_content_files(paths)
    _write_passing_audio_files(paths)
    paths.creative_plan.write_text(
        json.dumps(
            {
                "profile": {"name": "tutorial_screen", "confidence": 0.8, "evidence": ["visual_screen_signal"]},
                "content_provider": {"name": "vision_lite", "status": "fallback", "message": "local cues"},
                "audio_provider": {"name": "ffmpeg_volumedetect", "status": "available", "message": "local cues"},
                "recommended_variant": {
                    "segments": [
                        {
                            "start": 0.0,
                            "duration": 4.0,
                            "purpose": "hook",
                            "semantic_role": "tutorial_hook",
                            "content_tags": ["subtitle"],
                            "content_evidence": "subtitle_band",
                        },
                        {
                            "start": 8.0,
                            "duration": 4.0,
                            "purpose": "setup",
                            "semantic_role": "interface_state",
                            "content_tags": ["interface"],
                            "content_evidence": "interface_layout",
                        },
                        {
                            "start": 16.0,
                            "duration": 4.0,
                            "purpose": "result",
                            "semantic_role": "result_validation",
                            "content_tags": ["validation"],
                            "content_evidence": "result_screen",
                        },
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    source_probe = {"streams": [{"codec_type": "video", "width": 1080, "height": 1920}], "format": {"duration": "30"}}
    output_probe = {"streams": [{"codec_type": "video", "width": 1080, "height": 1920}], "format": {"duration": "24"}}

    result = run_quality_checks(source_probe, output_probe, "creative-edit", paths)

    assert result["status"] == "failed"
    assert any(issue["code"] == "creative_plan_weak_audio_evidence" for issue in result["issues"])


def test_quality_check_rejects_longform_creative_cut_that_is_too_short(tmp_path):
    paths = build_replicate_paths(tmp_path / "source.mp4", "creative-edit", output_root=tmp_path)
    paths.output_dir.mkdir(parents=True)
    paths.concat.write_text("file '/tmp/body.mp4'\n", encoding="utf-8")
    _write_passing_content_files(paths)
    _write_passing_audio_files(paths, cue_count=10)
    paths.creative_plan.write_text(
        json.dumps(
            {
                "source_duration": 563.41,
                "profile": {"name": "tutorial_screen", "confidence": 0.8, "evidence": ["visual_screen_signal"]},
                "content_provider": {"name": "vision_lite", "status": "fallback", "message": "local cues"},
                "audio_provider": {"name": "ffmpeg_volumedetect", "status": "available", "message": "local cues"},
                "recommended_variant": {
                    "segments": [
                        {
                            "start": index * 58.0,
                            "duration": 8.0,
                            "purpose": "chapter",
                            "semantic_role": role,
                            "content_tags": ["subtitle"],
                            "content_evidence": "subtitle_band",
                            "audio_tags": ["speech_like"],
                            "audio_evidence": "mean_volume:-24.0dB",
                        }
                        for index, role in enumerate(
                            [
                                "tutorial_hook",
                                "interface_state",
                                "operation_step",
                                "configuration_detail",
                                "operation_step",
                                "result_validation",
                            ]
                        )
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    source_probe = {"streams": [{"codec_type": "video", "width": 1920, "height": 1080}], "format": {"duration": "563.41"}}
    output_probe = {"streams": [{"codec_type": "video", "width": 1920, "height": 1080}], "format": {"duration": "64.13"}}

    result = run_quality_checks(source_probe, output_probe, "creative-edit", paths)

    assert result["status"] == "failed"
    assert any(issue["code"] == "creative_output_too_short_for_longform" for issue in result["issues"])


def test_quality_check_rejects_v4_duration_for_director_longform(tmp_path):
    paths = build_replicate_paths(tmp_path / "source.mp4", "creative-edit", output_root=tmp_path)
    paths.output_dir.mkdir(parents=True)
    paths.concat.write_text("file '/tmp/body.mp4'\n", encoding="utf-8")
    _write_passing_content_files(paths)
    _write_passing_audio_files(paths, cue_count=30)
    roles = ["tutorial_hook", "interface_state", "operation_step", "configuration_detail", "result_validation"]
    moves = ["cold_open", "reset_context", "action_chain", "decision_point", "proof_close"]
    paths.creative_plan.write_text(
        json.dumps(
            {
                "source_duration": 563.41,
                "creative_strategy": {
                    "version": "v5_director_longform",
                    "target_duration": 293.0,
                    "coverage_ratio": 0.52,
                    "target_segment_count": 30,
                    "treatment": "director_longform_chapter_cut",
                    "creative_moves": moves,
                },
                "profile": {"name": "tutorial_screen", "confidence": 0.8, "evidence": ["visual_screen_signal"]},
                "content_provider": {"name": "vision_lite", "status": "fallback", "message": "local cues"},
                "audio_provider": {"name": "ffmpeg_volumedetect", "status": "available", "message": "local cues"},
                "recommended_variant": {
                    "segments": [
                        {
                            "start": index * 16.0,
                            "duration": 9.0,
                            "purpose": "chapter",
                            "semantic_role": roles[index % len(roles)],
                            "content_tags": ["subtitle"],
                            "content_evidence": "subtitle_band",
                            "audio_tags": ["speech_like"],
                            "audio_evidence": "mean_volume:-18.0dB",
                            "creative_move": moves[index % len(moves)],
                        }
                        for index in range(30)
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    source_probe = {"streams": [{"codec_type": "video", "width": 1920, "height": 1080}], "format": {"duration": "563.41"}}
    output_probe = {"streams": [{"codec_type": "video", "width": 1920, "height": 1080}], "format": {"duration": "160.27"}}

    result = run_quality_checks(source_probe, output_probe, "creative-edit", paths)

    assert result["status"] == "failed"
    assert any(issue["code"] == "creative_output_too_short_for_longform" for issue in result["issues"])


def test_quality_check_allows_declared_short_summary_strategy_for_long_source(tmp_path):
    paths = build_replicate_paths(tmp_path / "source.mp4", "creative-edit", output_root=tmp_path)
    paths.output_dir.mkdir(parents=True)
    paths.concat.write_text("file '/tmp/body.mp4'\n", encoding="utf-8")
    _write_passing_content_files(paths)
    _write_passing_audio_files(paths, cue_count=11)
    _write_passing_semantic_files(paths, chapter_count=5)
    _write_passing_transcript_files(paths, cue_count=11)
    roles = [
        "tutorial_hook",
        "interface_state",
        "configuration_detail",
        "operation_step",
        "interface_state",
        "configuration_detail",
        "operation_step",
        "interface_state",
        "configuration_detail",
        "operation_step",
        "result_validation",
    ]
    moves = ["cold_open", "reset_context", "action_chain", "decision_point", "proof_close"]
    starts = [0.0, 16.0, 34.0, 58.0, 82.0, 106.0, 130.0, 154.0, 178.0, 202.0, 226.0]
    paths.creative_plan.write_text(
        json.dumps(
            {
                "source_duration": 355.109,
                "creative_strategy": {
                    "version": "v5_director_longform",
                    "target_duration": 87.499,
                    "coverage_ratio": 0.2464,
                    "target_segment_count": 11,
                    "treatment": "director_longform_chapter_cut",
                    "creative_moves": moves,
                },
                "profile": {"name": "tutorial_screen", "confidence": 0.8, "evidence": ["visual_screen_signal"]},
                "content_provider": {"name": "vision_lite", "status": "fallback", "message": "local cues"},
                "audio_provider": {"name": "ffmpeg_volumedetect", "status": "available", "message": "local cues"},
                "semantic_provider": {"name": "semantic_timeline", "status": "available", "message": "local cues"},
                "recommended_variant": {
                    "segments": [
                        {
                            "start": starts[index],
                            "duration": 12.0,
                            "purpose": "short summary chapter",
                            "semantic_role": role,
                            "content_tags": ["subtitle"],
                            "content_evidence": "subtitle_band",
                            "audio_tags": ["speech_like"],
                            "audio_evidence": "mean_volume:-18.0dB",
                            "chapter_title": f"章节 {index + 1}",
                            "semantic_topic": "tutorial",
                            "transcript_evidence": "ocr transcript proxy",
                            "creative_move": moves[index % len(moves)],
                        }
                        for index, role in enumerate(roles)
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    source_probe = {"streams": [{"codec_type": "video", "width": 3840, "height": 2160}], "format": {"duration": "355.109"}}
    output_probe = {"streams": [{"codec_type": "video", "width": 3840, "height": 2160}], "format": {"duration": "124.087"}}

    result = run_quality_checks(source_probe, output_probe, "creative-edit", paths)

    assert result["status"] == "passed"
    assert not any(issue["code"] == "creative_output_too_short_for_longform" for issue in result["issues"])


def test_quality_check_requires_director_strategy_for_longform(tmp_path):
    paths = build_replicate_paths(tmp_path / "source.mp4", "creative-edit", output_root=tmp_path)
    paths.output_dir.mkdir(parents=True)
    paths.concat.write_text("file '/tmp/body.mp4'\n", encoding="utf-8")
    _write_passing_content_files(paths)
    _write_passing_audio_files(paths, cue_count=30)
    paths.creative_plan.write_text(
        json.dumps(
            {
                "source_duration": 563.41,
                "profile": {"name": "tutorial_screen", "confidence": 0.8, "evidence": ["visual_screen_signal"]},
                "content_provider": {"name": "vision_lite", "status": "fallback", "message": "local cues"},
                "audio_provider": {"name": "ffmpeg_volumedetect", "status": "available", "message": "local cues"},
                "recommended_variant": {
                    "segments": [
                        {
                            "start": index * 16.0,
                            "duration": 9.0,
                            "purpose": "chapter",
                            "semantic_role": ["tutorial_hook", "interface_state", "operation_step", "configuration_detail", "result_validation"][index % 5],
                            "content_tags": ["subtitle"],
                            "content_evidence": "subtitle_band",
                            "audio_tags": ["speech_like"],
                            "audio_evidence": "mean_volume:-18.0dB",
                            "creative_move": ["cold_open", "reset_context", "action_chain", "decision_point", "proof_close"][index % 5],
                        }
                        for index in range(30)
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    source_probe = {"streams": [{"codec_type": "video", "width": 1920, "height": 1080}], "format": {"duration": "563.41"}}
    output_probe = {"streams": [{"codec_type": "video", "width": 1920, "height": 1080}], "format": {"duration": "280"}}

    result = run_quality_checks(source_probe, output_probe, "creative-edit", paths)

    assert result["status"] == "failed"
    assert any(issue["code"] == "creative_plan_missing_strategy" for issue in result["issues"])


def test_quality_check_rejects_weak_director_moves_for_longform(tmp_path):
    paths = build_replicate_paths(tmp_path / "source.mp4", "creative-edit", output_root=tmp_path)
    paths.output_dir.mkdir(parents=True)
    paths.concat.write_text("file '/tmp/body.mp4'\n", encoding="utf-8")
    _write_passing_content_files(paths)
    _write_passing_audio_files(paths, cue_count=30)
    roles = ["tutorial_hook", "interface_state", "operation_step", "configuration_detail", "result_validation"]
    paths.creative_plan.write_text(
        json.dumps(
            {
                "source_duration": 563.41,
                "creative_strategy": {
                    "version": "v5_director_longform",
                    "target_duration": 293.0,
                    "coverage_ratio": 0.52,
                    "target_segment_count": 30,
                    "treatment": "director_longform_chapter_cut",
                    "creative_moves": ["cold_open", "reset_context", "action_chain", "decision_point", "proof_close"],
                },
                "profile": {"name": "tutorial_screen", "confidence": 0.8, "evidence": ["visual_screen_signal"]},
                "content_provider": {"name": "vision_lite", "status": "fallback", "message": "local cues"},
                "audio_provider": {"name": "ffmpeg_volumedetect", "status": "available", "message": "local cues"},
                "recommended_variant": {
                    "segments": [
                        {
                            "start": index * 16.0,
                            "duration": 9.0,
                            "purpose": "chapter",
                            "semantic_role": roles[index % len(roles)],
                            "content_tags": ["subtitle"],
                            "content_evidence": "subtitle_band",
                            "audio_tags": ["speech_like"],
                            "audio_evidence": "mean_volume:-18.0dB",
                        }
                        for index in range(30)
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    source_probe = {"streams": [{"codec_type": "video", "width": 1920, "height": 1080}], "format": {"duration": "563.41"}}
    output_probe = {"streams": [{"codec_type": "video", "width": 1920, "height": 1080}], "format": {"duration": "280"}}

    result = run_quality_checks(source_probe, output_probe, "creative-edit", paths)

    assert result["status"] == "failed"
    assert any(issue["code"] == "creative_plan_weak_director_moves" for issue in result["issues"])


def test_quality_check_rejects_repetitive_chapter_runs_for_longform(tmp_path):
    paths = build_replicate_paths(tmp_path / "source.mp4", "creative-edit", output_root=tmp_path)
    paths.output_dir.mkdir(parents=True)
    paths.concat.write_text("file '/tmp/body.mp4'\n", encoding="utf-8")
    _write_passing_content_files(paths)
    _write_passing_audio_files(paths, cue_count=8)
    paths.creative_plan.write_text(
        json.dumps(
            {
                "source_duration": 563.41,
                "profile": {"name": "tutorial_screen", "confidence": 0.82, "evidence": ["title_hint:codex"]},
                "content_provider": {"name": "vision_lite", "status": "fallback", "message": "local cues"},
                "audio_provider": {"name": "ffmpeg_volumedetect", "status": "available", "message": "local audio cues"},
                "creative_strategy": {
                    "version": "v5_director_longform",
                    "target_duration": 292.972,
                    "coverage_ratio": 0.52,
                    "target_segment_count": 8,
                    "treatment": "director_longform_chapter_cut",
                    "creative_moves": [
                        "cold_open",
                        "reset_context",
                        "action_chain",
                        "decision_point",
                        "proof_close",
                    ],
                },
                "recommended_variant": {
                    "segments": [
                        {
                            "start": index * 28.0,
                            "duration": 10.0,
                            "purpose": "chapter",
                            "semantic_role": role,
                            "creative_move": move,
                            "content_tags": ["interface"],
                            "audio_tags": ["speech_like"],
                        }
                        for index, (role, move) in enumerate(
                            [
                                ("tutorial_hook", "cold_open"),
                                ("interface_state", "reset_context"),
                                ("configuration_detail", "decision_point"),
                                ("configuration_detail", "decision_point"),
                                ("configuration_detail", "decision_point"),
                                ("operation_step", "action_chain"),
                                ("result_validation", "proof_close"),
                                ("result_validation", "proof_close"),
                            ]
                        )
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    source_probe = {"streams": [{"codec_type": "video", "width": 1920, "height": 1080}], "format": {"duration": "563.41"}}
    output_probe = {"streams": [{"codec_type": "video", "width": 1920, "height": 1080}], "format": {"duration": "301"}}

    result = run_quality_checks(source_probe, output_probe, "creative-edit", paths)

    assert result["status"] == "failed"
    assert any(issue["code"] == "creative_plan_repetitive_chapter_run" for issue in result["issues"])


def test_quality_check_requires_director_move_coverage_for_longform(tmp_path):
    paths = build_replicate_paths(tmp_path / "source.mp4", "creative-edit", output_root=tmp_path)
    paths.output_dir.mkdir(parents=True)
    paths.concat.write_text("file '/tmp/body.mp4'\n", encoding="utf-8")
    _write_passing_content_files(paths)
    _write_passing_audio_files(paths, cue_count=8)
    paths.creative_plan.write_text(
        json.dumps(
            {
                "source_duration": 563.41,
                "profile": {"name": "tutorial_screen", "confidence": 0.82, "evidence": ["title_hint:codex"]},
                "content_provider": {"name": "vision_lite", "status": "fallback", "message": "local cues"},
                "audio_provider": {"name": "ffmpeg_volumedetect", "status": "available", "message": "local audio cues"},
                "creative_strategy": {
                    "version": "v5_director_longform",
                    "target_duration": 292.972,
                    "coverage_ratio": 0.52,
                    "target_segment_count": 8,
                    "treatment": "director_longform_chapter_cut",
                    "creative_moves": [
                        "cold_open",
                        "reset_context",
                        "action_chain",
                        "decision_point",
                        "proof_close",
                    ],
                },
                "recommended_variant": {
                    "segments": [
                        {
                            "start": index * 28.0,
                            "duration": 10.0,
                            "purpose": "chapter",
                            "semantic_role": role,
                            "creative_move": move,
                            "content_tags": ["interface"],
                            "audio_tags": ["speech_like"],
                        }
                        for index, (role, move) in enumerate(
                            [
                                ("tutorial_hook", "cold_open"),
                                ("interface_state", "reset_context"),
                                ("configuration_detail", "decision_point"),
                                ("interface_state", "reset_context"),
                                ("configuration_detail", "decision_point"),
                                ("interface_state", "reset_context"),
                                ("configuration_detail", "decision_point"),
                                ("result_validation", "proof_close"),
                            ]
                        )
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    source_probe = {"streams": [{"codec_type": "video", "width": 1920, "height": 1080}], "format": {"duration": "563.41"}}
    output_probe = {"streams": [{"codec_type": "video", "width": 1920, "height": 1080}], "format": {"duration": "301"}}

    result = run_quality_checks(source_probe, output_probe, "creative-edit", paths)

    assert result["status"] == "failed"
    assert any(issue["code"] == "creative_plan_missing_director_move_coverage" for issue in result["issues"])


def test_quality_check_rejects_nonchronological_longform_after_hook(tmp_path):
    paths = build_replicate_paths(tmp_path / "source.mp4", "creative-edit", output_root=tmp_path)
    paths.output_dir.mkdir(parents=True)
    paths.concat.write_text("file '/tmp/body.mp4'\n", encoding="utf-8")
    _write_passing_content_files(paths)
    _write_passing_audio_files(paths, cue_count=8)
    roles = [
        "tutorial_hook",
        "interface_state",
        "configuration_detail",
        "operation_step",
        "interface_state",
        "configuration_detail",
        "operation_step",
        "result_validation",
    ]
    moves = [
        "cold_open",
        "reset_context",
        "decision_point",
        "action_chain",
        "reset_context",
        "decision_point",
        "action_chain",
        "proof_close",
    ]
    starts = [550.0, 43.0, 20.0, 80.0, 160.0, 240.0, 320.0, 500.0]
    paths.creative_plan.write_text(
        json.dumps(
            {
                "source_duration": 563.41,
                "profile": {"name": "tutorial_screen", "confidence": 0.82, "evidence": ["title_hint:codex"]},
                "content_provider": {"name": "vision_lite", "status": "fallback", "message": "local cues"},
                "audio_provider": {"name": "ffmpeg_volumedetect", "status": "available", "message": "local audio cues"},
                "creative_strategy": {
                    "version": "v5_director_longform",
                    "target_duration": 292.972,
                    "coverage_ratio": 0.52,
                    "target_segment_count": 8,
                    "treatment": "director_longform_chapter_cut",
                    "creative_moves": [
                        "cold_open",
                        "reset_context",
                        "action_chain",
                        "decision_point",
                        "proof_close",
                    ],
                },
                "recommended_variant": {
                    "segments": [
                        {
                            "start": start,
                            "duration": 10.0,
                            "purpose": "chapter",
                            "semantic_role": role,
                            "creative_move": move,
                            "content_tags": ["interface"],
                            "content_evidence": "OCR: evidence",
                            "audio_tags": ["speech_like"],
                            "audio_evidence": "mean_volume:-18.0dB",
                        }
                        for start, role, move in zip(starts, roles, moves)
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    source_probe = {"streams": [{"codec_type": "video", "width": 1920, "height": 1080}], "format": {"duration": "563.41"}}
    output_probe = {"streams": [{"codec_type": "video", "width": 1920, "height": 1080}], "format": {"duration": "301"}}

    result = run_quality_checks(source_probe, output_probe, "creative-edit", paths)

    assert result["status"] == "failed"
    assert any(issue["code"] == "creative_plan_nonchronological_after_hook" for issue in result["issues"])


def test_quality_check_rejects_duration_inflation_for_creative_cut(tmp_path):
    paths = build_replicate_paths(tmp_path / "source.mp4", "creative-edit", output_root=tmp_path)
    paths.output_dir.mkdir(parents=True)
    paths.concat.write_text("file '/tmp/body.mp4'\n", encoding="utf-8")
    source_probe = {"streams": [{"codec_type": "video", "width": 1080, "height": 1920}], "format": {"duration": "22.314"}}
    output_probe = {"streams": [{"codec_type": "video", "width": 1080, "height": 1920}], "format": {"duration": "30.093"}}

    result = run_quality_checks(source_probe, output_probe, "creative-edit", paths)

    assert result["status"] == "failed"
    assert any(issue["code"] == "duration_inflation" for issue in result["issues"])


def test_quality_check_rejects_overexposed_line_art_contact_sheet(tmp_path):
    paths = build_replicate_paths(tmp_path / "source.mp4", "creative-edit", output_root=tmp_path)
    paths.output_dir.mkdir(parents=True)
    paths.concat.write_text("file '/tmp/body.mp4'\n", encoding="utf-8")
    Image.new("RGB", (1920, 810), (250, 250, 250)).save(paths.contact_sheet)
    draw = ImageDraw.Draw(Image.open(paths.contact_sheet).convert("RGB"))
    del draw
    with Image.open(paths.contact_sheet).convert("RGB") as image:
        overlay = ImageDraw.Draw(image)
        for offset in range(0, image.width, 140):
            overlay.line((offset, 0, offset + 80, image.height), fill=(218, 218, 218), width=2)
        image.save(paths.contact_sheet)
    _write_passing_content_files(paths)
    _write_passing_audio_files(paths)
    _write_passing_semantic_files(paths)
    _write_passing_transcript_files(paths)
    paths.creative_plan.write_text(
        json.dumps(
            {
                "profile": {"name": "tutorial_screen", "confidence": 0.8, "evidence": ["title_hint:教程"]},
                "content_provider": {"name": "vision_lite", "status": "fallback", "message": "local cues"},
                "audio_provider": {"name": "ffmpeg_volumedetect", "status": "available", "message": "local cues"},
                "semantic_provider": {"name": "ocr_audio_semantic", "status": "available", "message": "semantic"},
                "recommended_variant": {
                    "segments": [
                        {
                            "start": 0.0,
                            "duration": 4.0,
                            "purpose": "source hook",
                            "semantic_role": "tutorial_hook",
                            "content_tags": ["interface"],
                            "content_evidence": "OCR: evidence",
                            "audio_tags": ["speech_like"],
                            "audio_evidence": "mean_volume:-18.0dB",
                            "chapter_title": "开场",
                            "creative_move": "cold_open",
                        },
                        {
                            "start": 8.0,
                            "duration": 4.0,
                            "purpose": "source step",
                            "semantic_role": "operation_step",
                            "content_tags": ["interface"],
                            "content_evidence": "OCR: evidence",
                            "audio_tags": ["speech_like"],
                            "audio_evidence": "mean_volume:-18.0dB",
                            "chapter_title": "步骤",
                            "creative_move": "action_chain",
                        },
                        {
                            "start": 16.0,
                            "duration": 4.0,
                            "purpose": "source close",
                            "semantic_role": "result_validation",
                            "content_tags": ["validation"],
                            "content_evidence": "OCR: evidence",
                            "audio_tags": ["speech_like"],
                            "audio_evidence": "mean_volume:-18.0dB",
                            "chapter_title": "结果",
                            "creative_move": "proof_close",
                        },
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    source_probe = {"streams": [{"codec_type": "video", "width": 1920, "height": 1080}], "format": {"duration": "30"}}
    output_probe = {"streams": [{"codec_type": "video", "width": 1920, "height": 1080}], "format": {"duration": "20"}}

    result = run_quality_checks(source_probe, output_probe, "creative-edit", paths)

    assert result["status"] == "failed"
    assert any(issue["code"] == "visual_line_art_artifact" for issue in result["issues"])


def test_quality_check_requires_creative_plan_for_creative_edit(tmp_path):
    paths = build_replicate_paths(tmp_path / "source.mp4", "creative-edit", output_root=tmp_path)
    paths.output_dir.mkdir(parents=True)
    paths.concat.write_text("file '/tmp/body.mp4'\n", encoding="utf-8")
    source_probe = {"streams": [{"codec_type": "video", "width": 1080, "height": 1920}], "format": {"duration": "30"}}
    output_probe = {"streams": [{"codec_type": "video", "width": 1080, "height": 1920}], "format": {"duration": "20"}}

    result = run_quality_checks(source_probe, output_probe, "creative-edit", paths)

    assert result["status"] == "failed"
    assert any(issue["code"] == "missing_creative_plan" for issue in result["issues"])


def test_quality_check_rejects_overlapping_creative_plan_ranges(tmp_path):
    paths = build_replicate_paths(tmp_path / "source.mp4", "creative-edit", output_root=tmp_path)
    paths.output_dir.mkdir(parents=True)
    paths.concat.write_text("file '/tmp/body.mp4'\n", encoding="utf-8")
    paths.creative_plan.write_text(
        json.dumps(
            {
                "recommended_variant": {
                    "segments": [
                        {"start": 0.0, "duration": 5.0, "purpose": "first"},
                        {"start": 4.5, "duration": 4.0, "purpose": "overlap"},
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    source_probe = {"streams": [{"codec_type": "video", "width": 1080, "height": 1920}], "format": {"duration": "30"}}
    output_probe = {"streams": [{"codec_type": "video", "width": 1080, "height": 1920}], "format": {"duration": "9"}}

    result = run_quality_checks(source_probe, output_probe, "creative-edit", paths)

    assert result["status"] == "failed"
    assert any(issue["code"] == "creative_plan_overlap" for issue in result["issues"])


def test_quality_check_rejects_creative_plan_without_semantic_roles(tmp_path):
    paths = build_replicate_paths(tmp_path / "source.mp4", "creative-edit", output_root=tmp_path)
    paths.output_dir.mkdir(parents=True)
    paths.concat.write_text("file '/tmp/body.mp4'\n", encoding="utf-8")
    paths.creative_plan.write_text(
        json.dumps(
            {
                "profile": {"name": "food_social", "confidence": 0.8, "evidence": ["title_hint:美食"]},
                "recommended_variant": {
                    "segments": [
                        {"start": 0.0, "duration": 4.0, "purpose": "保留真实动作。"},
                        {"start": 8.0, "duration": 4.0, "purpose": "保留成品细节。"},
                        {"start": 16.0, "duration": 4.0, "purpose": "收束。"},
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    source_probe = {"streams": [{"codec_type": "video", "width": 1080, "height": 1920}], "format": {"duration": "30"}}
    output_probe = {"streams": [{"codec_type": "video", "width": 1080, "height": 1920}], "format": {"duration": "20"}}

    result = run_quality_checks(source_probe, output_probe, "creative-edit", paths)

    assert result["status"] == "failed"
    assert any(issue["code"] == "creative_plan_missing_semantic_role" for issue in result["issues"])


def test_quality_check_rejects_insufficient_semantic_variety(tmp_path):
    paths = build_replicate_paths(tmp_path / "source.mp4", "creative-edit", output_root=tmp_path)
    paths.output_dir.mkdir(parents=True)
    paths.concat.write_text("file '/tmp/body.mp4'\n", encoding="utf-8")
    paths.creative_plan.write_text(
        json.dumps(
            {
                "profile": {"name": "generic_live", "confidence": 0.5, "evidence": ["fallback"]},
                "recommended_variant": {
                    "segments": [
                        {"start": 0.0, "duration": 4.0, "purpose": "a", "semantic_role": "context_bridge"},
                        {"start": 8.0, "duration": 4.0, "purpose": "b", "semantic_role": "context_bridge"},
                        {"start": 16.0, "duration": 4.0, "purpose": "c", "semantic_role": "context_bridge"},
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    source_probe = {"streams": [{"codec_type": "video", "width": 1080, "height": 1920}], "format": {"duration": "30"}}
    output_probe = {"streams": [{"codec_type": "video", "width": 1080, "height": 1920}], "format": {"duration": "20"}}

    result = run_quality_checks(source_probe, output_probe, "creative-edit", paths)

    assert result["status"] == "failed"
    assert any(issue["code"] == "creative_plan_insufficient_role_variety" for issue in result["issues"])


def test_quality_check_rejects_dominant_semantic_role(tmp_path):
    paths = build_replicate_paths(tmp_path / "source.mp4", "creative-edit", output_root=tmp_path)
    paths.output_dir.mkdir(parents=True)
    paths.concat.write_text("file '/tmp/body.mp4'\n", encoding="utf-8")
    paths.creative_plan.write_text(
        json.dumps(
            {
                "profile": {"name": "tutorial_screen", "confidence": 0.66, "evidence": ["visual_screen_signal"]},
                "recommended_variant": {
                    "segments": [
                        {"start": 0.0, "duration": 4.0, "purpose": "hook", "semantic_role": "tutorial_hook"},
                        {"start": 8.0, "duration": 4.0, "purpose": "setup", "semantic_role": "interface_state"},
                        {"start": 16.0, "duration": 4.0, "purpose": "step", "semantic_role": "operation_step"},
                        {"start": 24.0, "duration": 4.0, "purpose": "step", "semantic_role": "operation_step"},
                        {"start": 32.0, "duration": 4.0, "purpose": "step", "semantic_role": "operation_step"},
                        {"start": 40.0, "duration": 4.0, "purpose": "step", "semantic_role": "operation_step"},
                        {"start": 48.0, "duration": 4.0, "purpose": "step", "semantic_role": "operation_step"},
                        {"start": 56.0, "duration": 4.0, "purpose": "step", "semantic_role": "operation_step"},
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    source_probe = {"streams": [{"codec_type": "video", "width": 1080, "height": 1920}], "format": {"duration": "90"}}
    output_probe = {"streams": [{"codec_type": "video", "width": 1080, "height": 1920}], "format": {"duration": "64"}}

    result = run_quality_checks(source_probe, output_probe, "creative-edit", paths)

    assert result["status"] == "failed"
    assert any(issue["code"] == "creative_plan_dominant_semantic_role" for issue in result["issues"])


def test_quality_check_rejects_tight_same_role_repetition(tmp_path):
    paths = build_replicate_paths(tmp_path / "source.mp4", "creative-edit", output_root=tmp_path)
    paths.output_dir.mkdir(parents=True)
    paths.concat.write_text("file '/tmp/body.mp4'\n", encoding="utf-8")
    paths.creative_plan.write_text(
        json.dumps(
            {
                "source_duration": 560.0,
                "profile": {"name": "tutorial_screen", "confidence": 0.66, "evidence": ["visual_screen_signal"]},
                "recommended_variant": {
                    "segments": [
                        {"start": 0.0, "duration": 8.0, "purpose": "hook", "semantic_role": "tutorial_hook"},
                        {"start": 80.0, "duration": 8.0, "purpose": "setup", "semantic_role": "interface_state"},
                        {"start": 120.0, "duration": 8.0, "purpose": "step", "semantic_role": "operation_step"},
                        {"start": 150.0, "duration": 8.0, "purpose": "near repeat", "semantic_role": "operation_step"},
                        {"start": 260.0, "duration": 8.0, "purpose": "config", "semantic_role": "configuration_detail"},
                        {"start": 420.0, "duration": 8.0, "purpose": "result", "semantic_role": "result_validation"},
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    source_probe = {"streams": [{"codec_type": "video", "width": 1080, "height": 1920}], "format": {"duration": "560"}}
    output_probe = {"streams": [{"codec_type": "video", "width": 1080, "height": 1920}], "format": {"duration": "48"}}

    result = run_quality_checks(source_probe, output_probe, "creative-edit", paths)

    assert result["status"] == "failed"
    assert any(issue["code"] == "creative_plan_tight_same_role_repetition" for issue in result["issues"])


def test_report_records_creative_artifacts_without_generated_intro_outro(tmp_path):
    paths = build_replicate_paths(tmp_path / "source.mp4", "creative-edit", output_root=tmp_path)
    paths.output_dir.mkdir(parents=True)
    probe = {"streams": [{"codec_type": "video", "width": 1080, "height": 1920}], "format": {"duration": "30"}}

    _write_report(tmp_path / "source.mp4", probe, probe, "creative-edit", paths, VideoGeometry(width=1080, height=1920))
    report = json.loads(paths.report.read_text(encoding="utf-8"))

    assert report["editing_rules"]["source_only"] is True
    assert report["editing_rules"]["generated_intro_outro"] is False
    assert report["editing_rules"]["creative_decision_artifacts"] is True


def test_creative_sample_schedule_stays_inside_source_duration():
    assert build_sample_schedule(22.314, sample_count=6) == [0.7, 4.383, 8.066, 11.749, 15.432, 19.115]


def test_creative_sample_count_expands_for_director_longform():
    assert creative_sample_count_for_duration(22.314) == 11
    assert creative_sample_count_for_duration(160.0) == 18
    assert creative_sample_count_for_duration(563.41) == 36


def test_creative_sample_extract_command_uses_ocr_ready_resolution(tmp_path):
    command = build_creative_sample_extract_command(
        source=tmp_path / "source.mp4",
        timestamp=12.345,
        frame_path=tmp_path / "sample.jpg",
    )

    assert "-vf" in command
    assert "scale=1280:-1:flags=lanczos" in command
    assert "12.345" in command


def test_creative_title_for_render_prefers_downloaded_source_title(tmp_path):
    source = tmp_path / "source.mp4"

    assert _creative_title_for_render(
        source,
        {"source_title": "Lionel Scaloni's Reaction To FIFA World Cup Final Penalty Shootout"},
    ) == "Lionel Scaloni's Reaction To FIFA World Cup Final Penalty Shootout"


def test_creative_profile_uses_food_title_hints():
    samples = [
        CreativeFrameSample(0, 0.7, 0.55, 0.42, 0.62, 0.72, 0.20),
        CreativeFrameSample(1, 4.0, 0.58, 0.48, 0.70, 0.82, 0.42),
    ]

    profile = classify_creative_profile(samples, title="烤羊排下酒太香了")

    assert profile.name == "food_social"
    assert profile.confidence >= 0.6
    assert any("title_hint" in item for item in profile.evidence)


def test_creative_profile_uses_tutorial_title_hints():
    samples = [
        CreativeFrameSample(0, 0.7, 0.82, 0.18, 0.42, 0.14, 0.08),
        CreativeFrameSample(1, 8.0, 0.78, 0.20, 0.38, 0.18, 0.12),
    ]

    profile = classify_creative_profile(samples, title="Codex DeepSeek API 配置教程")

    assert profile.name == "tutorial_screen"
    assert profile.confidence >= 0.6
    assert any("title_hint" in item for item in profile.evidence)


def test_creative_profile_detects_low_color_screen_recording_without_title_hints():
    samples = [
        CreativeFrameSample(0, 0.7, 0.70, 0.24, 0.38, 0.07, 0.10),
        CreativeFrameSample(1, 30.0, 0.74, 0.28, 0.42, 0.10, 0.72),
        CreativeFrameSample(2, 60.0, 0.68, 0.22, 0.40, 0.09, 0.88),
    ]

    profile = classify_creative_profile(samples, title="下载 (1)")

    assert profile.name == "tutorial_screen"
    assert any("visual_screen_signal" in item for item in profile.evidence)


def test_creative_plan_opens_with_strong_late_visual_without_overlap():
    samples = [
        CreativeFrameSample(0, 0.7, brightness=0.30, contrast=0.25, sharpness=0.30, colorfulness=0.35, motion=0.10),
        CreativeFrameSample(1, 5.0, brightness=0.45, contrast=0.38, sharpness=0.45, colorfulness=0.55, motion=0.60),
        CreativeFrameSample(2, 10.0, brightness=0.42, contrast=0.36, sharpness=0.44, colorfulness=0.52, motion=0.58),
        CreativeFrameSample(3, 16.0, brightness=0.58, contrast=0.50, sharpness=0.70, colorfulness=0.86, motion=0.40),
        CreativeFrameSample(4, 20.0, brightness=0.60, contrast=0.56, sharpness=0.72, colorfulness=0.92, motion=0.32),
    ]

    plan = build_creative_plan(22.314, samples, title="烤羊排下酒太香了")
    recommended = plan.recommended_variant

    assert recommended.name == "cover_first_story"
    assert recommended.segments[0].purpose.startswith("先用高吸引力成品")
    assert recommended.total_duration <= 22.314
    for previous, current in zip(recommended.segments, recommended.segments[1:]):
        assert not ranges_overlap(
            previous.start,
            previous.start + previous.duration,
            current.start,
            current.start + current.duration,
        )
    assert any(candidate.role == "cover_candidate" for candidate in plan.cover_candidates)


def test_strong_creative_edit_adds_original_explainers_and_lowers_source_reuse():
    samples = [
        CreativeFrameSample(index, 0.7 + index * 13.1, 0.48, 0.46, 0.62, 0.54, 0.72)
        for index in range(24)
    ]
    plan = build_creative_plan(
        470.181,
        samples,
        title="Lionel Scaloni's Reaction To FIFA World Cup Final Penalty Shootout",
        production_options={
            "quality_strictness": "audit",
            "creative_strength": "strong",
            "audio_policy": "replace_later",
        },
    )

    source_segments, original_inserts = creative_release_timeline(
        plan,
        production_options={
            "quality_strictness": "audit",
            "creative_strength": "strong",
            "audio_policy": "replace_later",
        },
    )

    source_duration = sum(segment.duration for segment in source_segments)
    insert_duration = sum(insert["duration"] for insert in original_inserts)
    assert original_inserts
    assert insert_duration >= 60
    assert source_duration / (source_duration + insert_duration) <= 0.64
    assert all(insert["source_type"] == "original_explainer" for insert in original_inserts)
    assert any("点球" in insert["subtitle"] or "World Cup" in insert["subtitle"] for insert in original_inserts)


def test_source_guided_longform_targets_near_original_duration():
    samples = [
        CreativeFrameSample(
            index,
            0.7 + index * 24.0,
            brightness=0.55,
            contrast=0.42,
            sharpness=0.58,
            colorfulness=0.35,
            motion=0.48,
        )
        for index in range(24)
    ]

    plan = build_creative_plan(
        420.0,
        samples,
        title="七分钟参考视频",
        production_options={"target_duration_policy": "source_guided", "creative_strength": "strong"},
    )
    source_segments, original_inserts = creative_release_timeline(
        plan,
        production_options={
            "target_duration_policy": "source_guided",
            "creative_strength": "strong",
            "audio_policy": "replace_later",
        },
    )
    release_duration = sum(segment.duration for segment in source_segments) + sum(
        float(insert["duration"]) for insert in original_inserts
    )

    assert plan.creative_strategy.target_duration >= 360.0
    assert release_duration >= 360.0


def test_images2_source_guided_longform_plan_keeps_real_source_duration():
    source_duration = 780.0
    samples = [
        CreativeFrameSample(
            index,
            timestamp,
            brightness=0.52 + (index % 4) * 0.03,
            contrast=0.46 + (index % 5) * 0.04,
            sharpness=0.54 + (index % 3) * 0.05,
            colorfulness=0.32,
            motion=0.44 + (index % 6) * 0.03,
        )
        for index, timestamp in enumerate(
            build_sample_schedule(source_duration, creative_sample_count_for_duration(source_duration))
        )
    ]

    plan = build_creative_plan(
        source_duration,
        samples,
        title="世界杯淘汰赛完整复盘",
        production_options={
            "target_duration_policy": "source_guided",
            "creative_strength": "strong",
            "visual_asset_strategy": "images2_contextual_inserts",
        },
    )
    visual_plan = build_visual_insert_plan(plan, {"visual_asset_strategy": "images2_contextual_inserts"})
    source_total = sum(segment.duration for segment in creative_segments_from_plan(plan))
    release_total = source_total + float(visual_plan["total_ai_insert_duration"])

    assert plan.creative_strategy.target_duration >= source_duration * 0.85
    assert source_total >= plan.creative_strategy.target_duration * 0.86
    assert release_total >= source_duration * 0.85
    for left_index, left in enumerate(plan.recommended_variant.segments):
        for right in plan.recommended_variant.segments[left_index + 1 :]:
            assert not ranges_overlap(
                left.start,
                left.start + left.duration,
                right.start,
                right.start + right.duration,
            )


def test_source_guided_longform_keeps_duration_for_repeated_subtitle_content():
    source_duration = 781.584
    samples = [
        CreativeFrameSample(
            index,
            timestamp,
            brightness=0.52,
            contrast=0.55 + (index % 4) * 0.04,
            sharpness=0.50,
            colorfulness=0.24,
            motion=0.32 + (index % 5) * 0.07,
        )
        for index, timestamp in enumerate(
            build_sample_schedule(source_duration, creative_sample_count_for_duration(source_duration))
        )
    ]
    content_analysis = ContentAnalysis(
        provider=ContentProviderStatus(name="vision_lite", status="fallback", message="local cues"),
        cues=tuple(
            ContentCue(
                sample_index=sample.index,
                timestamp=sample.timestamp,
                text_density=0.28,
                subtitle_likelihood=0.72,
                interface_likelihood=0.09,
                recognized_text="",
                content_tags=("subtitle", "text_dense"),
                evidence=("subtitle_band",),
            )
            for sample in samples
        ),
    )

    plan = build_creative_plan(
        source_duration,
        samples,
        title="体育解说长视频",
        content_analysis=content_analysis,
        production_options={
            "target_duration_policy": "source_guided",
            "creative_strength": "strong",
            "visual_asset_strategy": "images2_contextual_inserts",
        },
    )

    assert len(plan.recommended_variant.segments) >= 28
    assert plan.recommended_variant.total_duration >= plan.creative_strategy.target_duration * 0.86
    for left_index, left in enumerate(plan.recommended_variant.segments):
        for right in plan.recommended_variant.segments[left_index + 1 :]:
            assert not ranges_overlap(
                left.start,
                left.start + left.duration,
                right.start,
                right.start + right.duration,
            )


def test_creative_plan_production_options_adjust_strategy():
    samples = [
        CreativeFrameSample(
            index,
            0.7 + index * 24.0,
            brightness=0.62 if index % 3 else 0.78,
            contrast=0.40 + (index % 4) * 0.08,
            sharpness=0.45 + (index % 5) * 0.04,
            colorfulness=0.15,
            motion=0.25 + (index % 6) * 0.08,
        )
        for index in range(16)
    ]

    baseline = build_creative_plan(420.0, samples, title="Codex DeepSeek API 配置教程")
    strong = build_creative_plan(
        420.0,
        samples,
        title="Codex DeepSeek API 配置教程",
        production_options={"creative_strength": "strong", "target_duration_policy": "retain_core"},
    )
    short = build_creative_plan(
        420.0,
        samples,
        title="Codex DeepSeek API 配置教程",
        production_options={"creative_strength": "light", "target_duration_policy": "short_summary"},
    )

    assert baseline.creative_strategy.target_duration >= strong.creative_strategy.target_duration
    assert strong.creative_strategy.target_duration > short.creative_strategy.target_duration
    assert strong.creative_strategy.target_segment_count >= baseline.creative_strategy.target_segment_count
    assert short.creative_strategy.target_duration < baseline.creative_strategy.target_duration
    assert len(short.recommended_variant.segments) <= len(baseline.recommended_variant.segments)


def test_creative_plan_records_production_notes_in_strategy():
    plan = build_creative_plan(
        120.0,
        [
            CreativeFrameSample(0, 4.0, 0.52, 0.42, 0.5, 0.2, 0.2),
            CreativeFrameSample(1, 42.0, 0.54, 0.48, 0.62, 0.24, 0.5),
            CreativeFrameSample(2, 96.0, 0.56, 0.52, 0.58, 0.22, 0.3),
        ],
        title="Codex DeepSeek API 配置教程",
        production_options={"production_notes": "不要使用卡通化、线稿滤镜或假脸贴片；提高原创内容比例。"},
    )

    data = plan_to_dict(plan)

    assert data["creative_strategy"]["production_notes"] == "不要使用卡通化、线稿滤镜或假脸贴片；提高原创内容比例。"


def test_creative_plan_short_summary_uses_final_segment_duration_for_spacing():
    timestamps = build_sample_schedule(355.109, sample_count=36)
    samples = [
        CreativeFrameSample(
            index=index,
            timestamp=timestamp,
            brightness=0.62 if index % 3 else 0.78,
            contrast=0.40 + (index % 4) * 0.08,
            sharpness=0.45 + (index % 5) * 0.04,
            colorfulness=0.15,
            motion=0.25 + (index % 6) * 0.08,
        )
        for index, timestamp in enumerate(timestamps)
    ]

    plan = build_creative_plan(
        355.109,
        samples,
        title="Codex DeepSeek API 配置教程",
        production_options={"creative_strength": "light", "target_duration_policy": "short_summary"},
    )
    segments = plan.recommended_variant.segments

    for left_index, left in enumerate(segments):
        for right in segments[left_index + 1 :]:
            assert not ranges_overlap(left.start, left.start + left.duration, right.start, right.start + right.duration)


def test_creative_plan_marks_release_segments_as_source_video_only():
    plan = build_creative_plan(
        22.314,
        [
            CreativeFrameSample(0, 0.7, 0.3, 0.2, 0.3, 0.2, 0.1),
            CreativeFrameSample(1, 8.0, 0.5, 0.6, 0.6, 0.7, 0.6),
            CreativeFrameSample(2, 18.0, 0.7, 0.8, 0.8, 0.9, 0.4),
        ],
        title="烤羊排",
    )

    data = plan_to_dict(plan)
    segments = data["recommended_variant"]["segments"]

    assert all(segment["source_type"] == "source_video" for segment in segments)
    assert all(segment["synthetic"] is False for segment in segments)


def test_creative_plan_avoids_template_like_source_frame_as_hook():
    samples = [
        CreativeFrameSample(0, 0.7, brightness=0.72, contrast=0.32, sharpness=0.46, colorfulness=0.13, motion=0.08),
        CreativeFrameSample(1, 11.0, brightness=0.64, contrast=0.50, sharpness=0.66, colorfulness=0.18, motion=0.54),
        CreativeFrameSample(2, 22.0, brightness=0.16, contrast=0.33, sharpness=0.12, colorfulness=0.02, motion=1.0),
    ]
    content_analysis = ContentAnalysis(
        provider=ContentProviderStatus(name="vision_lite", status="fallback", message="local cues"),
        cues=(
            ContentCue(
                sample_index=0,
                timestamp=0.7,
                text_density=0.12,
                subtitle_likelihood=0.10,
                interface_likelihood=0.42,
                recognized_text="",
                content_tags=("interface",),
                evidence=("interface_likelihood:0.42",),
            ),
            ContentCue(
                sample_index=1,
                timestamp=11.0,
                text_density=0.18,
                subtitle_likelihood=0.12,
                interface_likelihood=0.50,
                recognized_text="",
                content_tags=("interface",),
                evidence=("interface_likelihood:0.50",),
            ),
            ContentCue(
                sample_index=2,
                timestamp=22.0,
                text_density=0.11,
                subtitle_likelihood=0.29,
                interface_likelihood=0.05,
                recognized_text="",
                content_tags=("subtitle",),
                evidence=("subtitle_likelihood:0.29", "interface_likelihood:0.05"),
            ),
        ),
    )

    plan = build_creative_plan(
        30.0,
        samples,
        title="Codex DeepSeek 安装教程",
        content_analysis=content_analysis,
    )
    first_segment = plan.recommended_variant.segments[0]

    assert first_segment.source_sample_index != 2
    assert "template_like_source_frame" not in first_segment.visual_risk_tags
    assert "template_like_source_frame" in plan.recommended_variant.segments[-1].visual_risk_tags


def test_creative_plan_avoids_bright_template_like_source_frame_as_hook():
    samples = [
        CreativeFrameSample(0, 0.7, brightness=0.72, contrast=0.32, sharpness=0.46, colorfulness=0.13, motion=0.08),
        CreativeFrameSample(1, 11.0, brightness=0.66, contrast=0.58, sharpness=0.60, colorfulness=0.19, motion=0.50),
        CreativeFrameSample(2, 22.0, brightness=0.94, contrast=0.27, sharpness=0.12, colorfulness=0.06, motion=1.0),
    ]
    content_analysis = ContentAnalysis(
        provider=ContentProviderStatus(name="vision_lite", status="fallback", message="local cues"),
        cues=(
            ContentCue(
                sample_index=0,
                timestamp=0.7,
                text_density=0.12,
                subtitle_likelihood=0.10,
                interface_likelihood=0.42,
                recognized_text="",
                content_tags=("interface",),
                evidence=("interface_likelihood:0.42",),
            ),
            ContentCue(
                sample_index=1,
                timestamp=11.0,
                text_density=0.18,
                subtitle_likelihood=0.12,
                interface_likelihood=0.50,
                recognized_text="",
                content_tags=("interface",),
                evidence=("interface_likelihood:0.50",),
            ),
            ContentCue(
                sample_index=2,
                timestamp=22.0,
                text_density=0.19,
                subtitle_likelihood=0.43,
                interface_likelihood=0.42,
                recognized_text="",
                content_tags=("subtitle", "interface", "text_dense"),
                evidence=("subtitle_likelihood:0.43", "interface_likelihood:0.42"),
            ),
        ),
    )

    plan = build_creative_plan(
        30.0,
        samples,
        title="Codex DeepSeek 安装教程",
        content_analysis=content_analysis,
    )
    first_segment = plan.recommended_variant.segments[0]

    assert first_segment.source_sample_index != 2
    assert "template_like_source_frame" not in first_segment.visual_risk_tags
    assert "template_like_source_frame" in plan.recommended_variant.segments[-1].visual_risk_tags


def test_creative_plan_limits_template_like_source_frame_budget():
    source_duration = 563.41
    timestamps = build_sample_schedule(source_duration, sample_count=36)
    risky_indices = {5, 7, 9, 11, 13, 15, 17, 19, 21, 23, 25, 27, 29}
    samples = []
    cues = []
    for index, timestamp in enumerate(timestamps):
        if index in risky_indices:
            samples.append(
                CreativeFrameSample(
                    index=index,
                    timestamp=timestamp,
                    brightness=0.17,
                    contrast=0.78,
                    sharpness=0.12,
                    colorfulness=0.03,
                    motion=1.0,
                )
            )
            cues.append(
                ContentCue(
                    sample_index=index,
                    timestamp=timestamp,
                    text_density=0.09,
                    subtitle_likelihood=0.38,
                    interface_likelihood=0.04,
                    recognized_text="",
                    content_tags=("subtitle",),
                    evidence=("subtitle_likelihood:0.38", "interface_likelihood:0.04"),
                )
            )
        else:
            samples.append(
                CreativeFrameSample(
                    index=index,
                    timestamp=timestamp,
                    brightness=0.62,
                    contrast=0.54,
                    sharpness=0.42,
                    colorfulness=0.20,
                    motion=0.56 if index % 3 == 0 else 0.36,
                )
            )
            cues.append(
                ContentCue(
                    sample_index=index,
                    timestamp=timestamp,
                    text_density=0.26,
                    subtitle_likelihood=0.08,
                    interface_likelihood=0.55,
                    recognized_text=f"screen {index}",
                    content_tags=("interface",),
                    evidence=("interface_likelihood:0.55",),
                )
            )
    content_analysis = ContentAnalysis(
        provider=ContentProviderStatus(name="vision_lite", status="fallback", message="local cues"),
        cues=tuple(cues),
    )

    plan = build_creative_plan(
        source_duration,
        samples,
        title="Codex DeepSeek API 配置教程",
        content_analysis=content_analysis,
    )
    segments = plan.recommended_variant.segments
    template_like_count = sum(
        1 for segment in segments if "template_like_source_frame" in segment.visual_risk_tags
    )
    allowed_template_like = max(1, math.floor(len(segments) * 0.18))

    assert template_like_count <= allowed_template_like
    assert "template_like_source_frame" not in segments[0].visual_risk_tags


def test_creative_plan_rebalances_template_like_frames_without_losing_longform_floor():
    source_duration = 563.41
    sample_rows = (
        (0, 0.7, 0.2123, 0.56, 0.2209, 0.0633, 0.0),
        (1, 16.549, 0.1792, 0.3721, 0.1207, 0.0194, 0.4764),
        (2, 32.398, 0.1877, 0.4814, 0.1918, 0.0343, 0.2592),
        (3, 48.246, 0.2721, 0.8534, 0.1906, 0.0343, 0.5123),
        (4, 64.095, 0.376, 1.0, 0.2204, 0.0389, 0.7403),
        (5, 79.944, 0.1622, 0.289, 0.1184, 0.0272, 1.0),
        (6, 95.793, 0.264, 0.0953, 0.0245, 0.4959, 0.8737),
        (7, 111.641, 0.7868, 0.868, 0.2153, 0.2209, 1.0),
        (8, 127.49, 0.5065, 0.872, 0.1514, 0.1276, 1.0),
        (9, 143.339, 0.7956, 0.8521, 0.2013, 0.2153, 1.0),
        (10, 159.188, 0.1714, 0.3895, 0.1341, 0.0251, 1.0),
        (11, 175.036, 0.903, 0.3291, 0.126, 0.0714, 1.0),
        (12, 190.885, 0.6591, 1.0, 0.1866, 0.0215, 1.0),
        (13, 206.734, 0.832, 0.916, 0.1851, 0.13, 1.0),
        (14, 222.583, 0.8294, 0.918, 0.187, 0.1299, 0.0208),
        (15, 238.432, 0.8328, 0.9199, 0.1796, 0.1312, 0.1259),
        (16, 254.28, 0.9531, 0.248, 0.1308, 0.0162, 0.8489),
        (17, 270.129, 0.9685, 0.2358, 0.0923, 0.0169, 0.2292),
        (18, 285.978, 0.9686, 0.2422, 0.0895, 0.0194, 0.1898),
        (19, 301.827, 0.9422, 0.341, 0.1735, 0.0425, 0.1962),
        (20, 317.675, 0.9626, 0.2679, 0.1019, 0.0196, 0.1462),
        (21, 333.524, 0.8403, 0.9164, 0.1625, 0.127, 0.7448),
        (22, 349.373, 0.8356, 0.9163, 0.1766, 0.1268, 0.0395),
        (23, 365.222, 0.7615, 0.2752, 0.0882, 0.0149, 1.0),
        (24, 381.071, 0.9467, 0.2837, 0.1083, 0.0573, 0.9138),
        (25, 396.919, 0.1807, 0.4905, 0.185, 0.0209, 1.0),
        (26, 412.768, 0.9446, 0.2676, 0.1176, 0.0656, 1.0),
        (27, 428.617, 0.952, 0.2475, 0.1039, 0.057, 0.0492),
        (28, 444.466, 0.935, 0.3581, 0.1449, 0.0836, 0.1562),
        (29, 460.314, 0.9465, 0.2624, 0.1136, 0.0595, 0.153),
        (30, 476.163, 0.9455, 0.2464, 0.1137, 0.0652, 0.0586),
        (31, 492.012, 0.9526, 0.2266, 0.0962, 0.0586, 0.0847),
        (32, 507.861, 0.9226, 0.4302, 0.1473, 0.0667, 0.2715),
        (33, 523.709, 0.9232, 0.4392, 0.1316, 0.066, 0.0814),
        (34, 539.558, 0.9259, 0.4297, 0.1295, 0.0661, 0.0226),
        (35, 555.407, 0.1641, 0.3306, 0.1176, 0.0256, 1.0),
    )
    cue_rows = (
        (0, 0.2597, 0.4723, 0.1532, ("install", "subtitle", "text_dense")),
        (1, 0.1331, 0.1634, 0.0702, ("install",)),
        (2, 0.2295, 0.452, 0.1369, ("install", "subtitle", "text_dense")),
        (3, 0.2424, 0.3707, 0.1127, ("install", "subtitle", "text_dense")),
        (4, 0.3107, 0.3991, 0.1561, ("install", "subtitle", "text_dense")),
        (5, 0.1378, 0.3829, 0.0808, ("install", "subtitle")),
        (6, 0.0782, 0.3593, 0.0503, ("install", "subtitle")),
        (7, 0.3596, 0.4952, 0.4095, ("install", "subtitle", "interface", "text_dense")),
        (8, 0.2931, 0.3915, 0.1871, ("install", "subtitle", "text_dense")),
        (9, 0.3575, 0.5694, 0.4144, ("install", "subtitle", "interface", "text_dense")),
        (10, 0.1448, 0.3306, 0.0772, ("install", "subtitle")),
        (11, 0.2236, 0.3853, 0.4193, ("install", "subtitle", "interface", "text_dense")),
        (12, 0.2824, 0.4031, 0.2586, ("install", "subtitle", "text_dense")),
        (13, 0.3622, 0.5411, 0.4343, ("install", "subtitle", "interface", "text_dense")),
        (14, 0.3724, 0.5782, 0.4409, ("install", "subtitle", "interface", "text_dense")),
        (15, 0.3731, 0.4598, 0.4436, ("install", "subtitle", "interface", "text_dense")),
        (16, 0.2584, 0.4435, 0.4859, ("install", "subtitle", "interface", "text_dense")),
        (17, 0.1784, 0.4774, 0.4312, ("install", "subtitle", "interface")),
        (18, 0.1581, 0.2723, 0.4149, ("install", "subtitle", "interface")),
        (19, 0.3023, 0.4983, 0.5067, ("install", "subtitle", "interface", "text_dense")),
        (20, 0.191, 0.4735, 0.4352, ("install", "subtitle", "interface", "text_dense")),
        (21, 0.3102, 0.4701, 0.3968, ("install", "subtitle", "interface", "text_dense")),
        (22, 0.3347, 0.5648, 0.4139, ("install", "subtitle", "interface", "text_dense")),
        (23, 0.1656, 0.1961, 0.2939, ("install",)),
        (24, 0.1837, 0.5253, 0.4184, ("install", "subtitle", "interface", "text_dense")),
        (25, 0.2207, 0.283, 0.1278, ("install", "subtitle", "text_dense")),
        (26, 0.1925, 0.4298, 0.426, ("install", "subtitle", "interface", "text_dense")),
        (27, 0.1759, 0.391, 0.4191, ("install", "subtitle", "interface")),
        (28, 0.2277, 0.4488, 0.4401, ("install", "subtitle", "interface", "text_dense")),
        (29, 0.161, 0.3703, 0.402, ("install", "subtitle", "interface")),
        (30, 0.1481, 0.276, 0.3927, ("install", "subtitle", "interface")),
        (31, 0.125, 0.2671, 0.3801, ("install", "subtitle", "interface")),
        (32, 0.2421, 0.4027, 0.4377, ("install", "subtitle", "interface", "text_dense")),
        (33, 0.2346, 0.5704, 0.4311, ("install", "subtitle", "interface", "text_dense")),
        (34, 0.2194, 0.5039, 0.4215, ("install", "subtitle", "interface", "text_dense")),
        (35, 0.1087, 0.285, 0.0542, ("install", "subtitle")),
    )
    samples = [CreativeFrameSample(*row) for row in sample_rows]
    cue_by_index = {index: (text_density, subtitle, interface, tags) for index, text_density, subtitle, interface, tags in cue_rows}
    content_analysis = ContentAnalysis(
        provider=ContentProviderStatus(name="vision_lite", status="fallback", message="local cues"),
        cues=tuple(
            ContentCue(
                sample_index=sample.index,
                timestamp=sample.timestamp,
                text_density=cue_by_index[sample.index][0],
                subtitle_likelihood=cue_by_index[sample.index][1],
                interface_likelihood=cue_by_index[sample.index][2],
                recognized_text="",
                content_tags=cue_by_index[sample.index][3],
                evidence=("v19 regression",),
            )
            for sample in samples
        ),
    )

    plan = build_creative_plan(
        source_duration,
        samples,
        title="Codex DeepSeek API 配置教程",
        content_analysis=content_analysis,
    )
    segments = plan.recommended_variant.segments
    minimum_longform_duration = min(300.0, max(240.0, source_duration * 0.45))
    template_like_count = sum(
        1 for segment in segments if "template_like_source_frame" in segment.visual_risk_tags
    )
    allowed_template_like = max(1, math.floor(len(segments) * 0.18))

    assert template_like_count <= allowed_template_like
    assert plan.recommended_variant.total_duration >= minimum_longform_duration


def test_creative_plan_cold_open_starts_near_selected_sample_timestamp():
    samples = [
        CreativeFrameSample(0, 0.7, brightness=0.24, contrast=0.42, sharpness=0.16, colorfulness=0.05, motion=0.06),
        CreativeFrameSample(1, 127.49, brightness=0.51, contrast=0.87, sharpness=0.15, colorfulness=0.13, motion=1.0),
        CreativeFrameSample(2, 143.339, brightness=0.38, contrast=0.50, sharpness=0.20, colorfulness=0.06, motion=0.20),
    ]
    content_analysis = ContentAnalysis(
        provider=ContentProviderStatus(name="vision_lite", status="fallback", message="local cues"),
        cues=(
            ContentCue(
                sample_index=0,
                timestamp=0.7,
                text_density=0.10,
                subtitle_likelihood=0.05,
                interface_likelihood=0.25,
                recognized_text="",
                content_tags=("interface",),
                evidence=("interface_likelihood:0.25",),
            ),
            ContentCue(
                sample_index=1,
                timestamp=127.49,
                text_density=0.29,
                subtitle_likelihood=0.39,
                interface_likelihood=0.19,
                recognized_text="",
                content_tags=("install", "subtitle", "text_dense"),
                evidence=("subtitle_likelihood:0.39", "interface_likelihood:0.19"),
            ),
            ContentCue(
                sample_index=2,
                timestamp=143.339,
                text_density=0.11,
                subtitle_likelihood=0.12,
                interface_likelihood=0.35,
                recognized_text="",
                content_tags=("interface",),
                evidence=("interface_likelihood:0.35",),
            ),
        ),
    )

    plan = build_creative_plan(
        180.0,
        samples,
        title="Codex DeepSeek 安装教程",
        content_analysis=content_analysis,
    )
    first_segment = plan.recommended_variant.segments[0]

    assert first_segment.creative_move == "cold_open"
    assert first_segment.source_sample_index == 1
    assert first_segment.start >= first_segment.source_sample_timestamp - 0.01
    assert first_segment.duration <= 4.0


def test_creative_segments_from_plan_preserves_plan_order_and_reasons():
    plan = build_creative_plan(
        22.314,
        [
            CreativeFrameSample(0, 0.7, 0.3, 0.2, 0.3, 0.2, 0.1),
            CreativeFrameSample(1, 8.0, 0.5, 0.6, 0.6, 0.7, 0.6),
            CreativeFrameSample(2, 18.0, 0.7, 0.8, 0.8, 0.9, 0.4),
        ],
        title="烤羊排",
    )

    segments = creative_segments_from_plan(plan)

    assert [segment.key for segment in segments][0].startswith("creative_")
    assert segments[0].purpose == plan.recommended_variant.segments[0].purpose
    assert sum(segment.duration for segment in segments) == plan.recommended_variant.total_duration


def test_food_creative_plan_uses_food_semantic_roles():
    samples = [
        CreativeFrameSample(0, 0.7, 0.50, 0.35, 0.55, 0.70, 0.18),
        CreativeFrameSample(1, 6.0, 0.56, 0.42, 0.60, 0.66, 0.68),
        CreativeFrameSample(2, 12.0, 0.60, 0.50, 0.72, 0.88, 0.42),
        CreativeFrameSample(3, 18.0, 0.62, 0.46, 0.78, 0.92, 0.20),
    ]

    plan = build_creative_plan(24.0, samples, title="烤羊排下酒太香了")
    roles = {segment.semantic_role for segment in plan.recommended_variant.segments}

    assert plan.profile.name == "food_social"
    assert "food_hook" in roles
    assert roles & {"prep_action", "cook_transform", "texture_closeup", "final_payoff"}
    assert all(segment.role_evidence for segment in plan.recommended_variant.segments)


def test_tutorial_creative_plan_uses_tutorial_semantic_roles():
    samples = [
        CreativeFrameSample(0, 0.7, 0.82, 0.18, 0.42, 0.16, 0.08),
        CreativeFrameSample(1, 30.0, 0.80, 0.22, 0.44, 0.18, 0.34),
        CreativeFrameSample(2, 90.0, 0.78, 0.24, 0.50, 0.20, 0.18),
        CreativeFrameSample(3, 150.0, 0.76, 0.26, 0.48, 0.19, 0.12),
    ]

    plan = build_creative_plan(180.0, samples, title="Codex DeepSeek API 配置教程")
    roles = {segment.semantic_role for segment in plan.recommended_variant.segments}

    assert plan.profile.name == "tutorial_screen"
    assert "tutorial_hook" in roles
    assert roles & {"interface_state", "operation_step", "configuration_detail", "result_validation"}
    assert all(segment.role_evidence for segment in plan.recommended_variant.segments)


def test_tutorial_story_arc_balances_operation_and_validation_roles():
    samples = [
        CreativeFrameSample(0, 0.7, 0.2116, 0.5561, 0.2184, 0.0602, 0.0),
        CreativeFrameSample(1, 33.33, 0.1874, 0.4791, 0.1913, 0.0318, 0.4747),
        CreativeFrameSample(2, 65.96, 0.1915, 0.5532, 0.1748, 0.0239, 0.3233),
        CreativeFrameSample(3, 98.589, 0.6368, 0.7254, 0.1784, 0.4038, 1.0),
        CreativeFrameSample(4, 131.219, 0.5058, 0.8712, 0.155, 0.1258, 1.0),
        CreativeFrameSample(5, 163.849, 0.2076, 0.6391, 0.1438, 0.0343, 1.0),
        CreativeFrameSample(6, 196.479, 0.6591, 1.0, 0.1821, 0.0179, 1.0),
        CreativeFrameSample(7, 229.109, 0.8353, 0.9131, 0.1835, 0.1256, 1.0),
        CreativeFrameSample(8, 261.739, 0.7617, 0.9499, 0.2062, 0.1256, 0.4657),
        CreativeFrameSample(9, 294.368, 0.9599, 0.2657, 0.1036, 0.0311, 1.0),
        CreativeFrameSample(10, 326.998, 0.8026, 0.8732, 0.1922, 0.1744, 0.8667),
        CreativeFrameSample(11, 359.628, 0.7624, 0.2785, 0.0916, 0.0132, 1.0),
        CreativeFrameSample(12, 392.258, 0.1674, 0.426, 0.1191, 0.0169, 1.0),
        CreativeFrameSample(13, 424.888, 0.9405, 0.2894, 0.124, 0.0643, 1.0),
        CreativeFrameSample(14, 457.518, 0.944, 0.278, 0.1163, 0.0581, 0.1522),
        CreativeFrameSample(15, 490.147, 0.9504, 0.2431, 0.1004, 0.0574, 0.0922),
        CreativeFrameSample(16, 522.777, 0.9229, 0.4397, 0.13, 0.0657, 0.2614),
        CreativeFrameSample(17, 555.407, 0.1641, 0.3307, 0.1177, 0.0231, 1.0),
    ]

    plan = build_creative_plan(563.41, samples, title="下载 (1)")
    roles = [segment.semantic_role for segment in plan.recommended_variant.segments]

    assert plan.profile.name == "tutorial_screen"
    assert roles.count("operation_step") <= 4
    assert "configuration_detail" in roles
    assert "result_validation" in roles
    assert roles[-1] == "result_validation"
    operation_starts = sorted(
        segment.start for segment in plan.recommended_variant.segments if segment.semantic_role == "operation_step"
    )
    assert all(right - left >= 45 for left, right in zip(operation_starts, operation_starts[1:]))


def test_text_dense_download_video_uses_tutorial_arc_instead_of_generic_role_piles():
    timestamps = build_sample_schedule(355.109, sample_count=36)
    samples = [
        CreativeFrameSample(
            index=index,
            timestamp=timestamp,
            brightness=0.24 + (index % 3) * 0.03,
            contrast=0.50 + (index % 4) * 0.04,
            sharpness=0.18 + (index % 5) * 0.02,
            colorfulness=0.07 + (index % 4) * 0.01,
            motion=0.62 if index in {2, 6, 10, 14, 18, 22, 26, 30} else 0.38,
        )
        for index, timestamp in enumerate(timestamps)
    ]
    content_analysis = ContentAnalysis(
        provider=ContentProviderStatus(name="vision_lite", status="fallback", message="local cues"),
        cues=tuple(
            ContentCue(
                sample_index=sample.index,
                timestamp=sample.timestamp,
                text_density=0.24,
                subtitle_likelihood=0.54,
                interface_likelihood=0.14,
                recognized_text="",
                content_tags=("install", "subtitle", "text_dense"),
                evidence=("text_density:0.24", "subtitle_likelihood:0.54", "visual_tags:subtitle,text_dense"),
            )
            for sample in samples
        ),
    )

    plan = build_creative_plan(
        355.109,
        samples,
        title="下载",
        content_analysis=content_analysis,
        production_options={"target_duration_policy": "short_summary", "creative_strength": "light"},
    )
    roles = [segment.semantic_role for segment in plan.recommended_variant.segments]
    operation_starts = sorted(
        segment.start for segment in plan.recommended_variant.segments if segment.semantic_role == "operation_step"
    )

    assert plan.profile.name == "tutorial_screen"
    assert _longest_run(roles) <= 2
    assert "configuration_detail" in roles
    assert "result_validation" in roles
    assert all(right - left >= 45 for left, right in zip(operation_starts, operation_starts[1:]))


def test_longform_tutorial_plan_expands_duration_and_serializes_audio_evidence():
    samples = [
        CreativeFrameSample(0, 0.7, 0.2116, 0.5561, 0.2184, 0.0602, 0.0),
        CreativeFrameSample(1, 33.33, 0.1874, 0.4791, 0.1913, 0.0318, 0.4747),
        CreativeFrameSample(2, 65.96, 0.1915, 0.5532, 0.1748, 0.0239, 0.3233),
        CreativeFrameSample(3, 98.589, 0.6368, 0.7254, 0.1784, 0.4038, 1.0),
        CreativeFrameSample(4, 131.219, 0.5058, 0.8712, 0.155, 0.1258, 1.0),
        CreativeFrameSample(5, 163.849, 0.2076, 0.6391, 0.1438, 0.0343, 1.0),
        CreativeFrameSample(6, 196.479, 0.6591, 1.0, 0.1821, 0.0179, 1.0),
        CreativeFrameSample(7, 229.109, 0.8353, 0.9131, 0.1835, 0.1256, 1.0),
        CreativeFrameSample(8, 261.739, 0.7617, 0.9499, 0.2062, 0.1256, 0.4657),
        CreativeFrameSample(9, 294.368, 0.9599, 0.2657, 0.1036, 0.0311, 1.0),
        CreativeFrameSample(10, 326.998, 0.8026, 0.8732, 0.1922, 0.1744, 0.8667),
        CreativeFrameSample(11, 359.628, 0.7624, 0.2785, 0.0916, 0.0132, 1.0),
        CreativeFrameSample(12, 392.258, 0.1674, 0.426, 0.1191, 0.0169, 1.0),
        CreativeFrameSample(13, 424.888, 0.9405, 0.2894, 0.124, 0.0643, 1.0),
        CreativeFrameSample(14, 457.518, 0.944, 0.278, 0.1163, 0.0581, 0.1522),
        CreativeFrameSample(15, 490.147, 0.9504, 0.2431, 0.1004, 0.0574, 0.0922),
        CreativeFrameSample(16, 522.777, 0.9229, 0.4397, 0.13, 0.0657, 0.2614),
        CreativeFrameSample(17, 555.407, 0.1641, 0.3307, 0.1177, 0.0231, 1.0),
    ]
    audio_analysis = AudioAnalysis(
        provider=AudioProviderStatus(name="ffmpeg_volumedetect", status="available", message="local audio cues"),
        cues=tuple(
            AudioCue(
                sample_index=sample.index,
                timestamp=sample.timestamp,
                mean_volume_db=-24.0,
                max_volume_db=-8.0,
                energy=0.72,
                speech_likelihood=0.68,
                audio_tags=("speech_like", "emphasis"),
                evidence=("mean_volume:-24.0dB", "max_volume:-8.0dB"),
            )
            for sample in samples
        ),
    )

    plan = build_creative_plan(563.41, samples, title="下载 (1)", audio_analysis=audio_analysis)
    data = plan_to_dict(plan)
    segments = data["recommended_variant"]["segments"]

    assert len(segments) > 8
    assert data["recommended_variant"]["total_duration"] >= 120
    assert data["audio_provider"]["name"] == "ffmpeg_volumedetect"
    assert data["audio_coverage"]["speech_like_count"] == len(samples)
    assert all(segment["audio_tags"] for segment in segments)
    assert all(segment["audio_evidence"] for segment in segments)


def test_director_longform_plan_has_strategy_moves_and_fuller_duration():
    timestamps = build_sample_schedule(563.41, sample_count=36)
    samples = [
        CreativeFrameSample(
            index=index,
            timestamp=timestamp,
            brightness=0.2 + (index % 5) * 0.14,
            contrast=0.35 + (index % 4) * 0.14,
            sharpness=0.16 + (index % 3) * 0.08,
            colorfulness=0.04 + (index % 6) * 0.04,
            motion=0.72 if index % 4 in {1, 2} else 0.22,
        )
        for index, timestamp in enumerate(timestamps)
    ]
    audio_analysis = AudioAnalysis(
        provider=AudioProviderStatus(name="ffmpeg_volumedetect", status="available", message="local audio cues"),
        cues=tuple(
            AudioCue(
                sample_index=sample.index,
                timestamp=sample.timestamp,
                mean_volume_db=-18.0,
                max_volume_db=-1.0,
                energy=0.74,
                speech_likelihood=0.82,
                audio_tags=("speech_like", "emphasis", "continuous_audio"),
                evidence=("mean_volume:-18.0dB", "max_volume:-1.0dB", "speech_likelihood:0.82"),
            )
            for sample in samples
        ),
    )

    plan = build_creative_plan(563.41, samples, title="Codex DeepSeek API 配置教程", audio_analysis=audio_analysis)
    data = plan_to_dict(plan)
    segments = data["recommended_variant"]["segments"]

    assert len(segments) >= 28
    assert data["recommended_variant"]["total_duration"] >= 270
    assert data["creative_strategy"]["version"] == "v5_director_longform"
    assert data["creative_strategy"]["coverage_ratio"] >= 0.45
    assert all(segment["creative_move"] for segment in segments)


def test_director_longform_plan_uses_chapter_arc_instead_of_role_piles():
    timestamps = build_sample_schedule(563.41, sample_count=36)
    samples = [
        CreativeFrameSample(
            index=index,
            timestamp=timestamp,
            brightness=0.2 + (index % 5) * 0.14,
            contrast=0.35 + (index % 4) * 0.14,
            sharpness=0.16 + (index % 3) * 0.08,
            colorfulness=0.04 + (index % 6) * 0.04,
            motion=0.72 if index % 4 in {1, 2} else 0.22,
        )
        for index, timestamp in enumerate(timestamps)
    ]

    plan = build_creative_plan(563.41, samples, title="Codex DeepSeek API 配置教程")
    data = plan_to_dict(plan)
    segments = data["recommended_variant"]["segments"]
    roles = [segment["semantic_role"] for segment in segments]
    moves = [segment["creative_move"] for segment in segments]

    assert _longest_run(roles) <= 2
    assert set(data["creative_strategy"]["creative_moves"]).issubset(set(moves))
    assert roles[0] == "tutorial_hook"
    assert roles[-1] == "result_validation"


def test_generic_longform_plan_uses_role_arc_instead_of_action_pile():
    timestamps = build_sample_schedule(470.181, sample_count=36)
    samples = [
        CreativeFrameSample(
            index=index,
            timestamp=timestamp,
            brightness=0.30 + (index % 5) * 0.07,
            contrast=0.40 + (index % 4) * 0.10,
            sharpness=0.18 + (index % 4) * 0.04,
            colorfulness=0.18 + (index % 3) * 0.03,
            motion=0.72 if index % 5 != 0 else 0.36,
        )
        for index, timestamp in enumerate(timestamps)
    ]

    plan = build_creative_plan(470.181, samples, title="Lionel Scaloni Reaction")
    data = plan_to_dict(plan)
    segments = data["recommended_variant"]["segments"]
    roles = [segment["semantic_role"] for segment in segments]
    moves = [segment["creative_move"] for segment in segments]

    assert data["profile"]["name"] == "generic_live"
    assert _longest_run(roles) <= 2
    assert roles.count("action_moment") / len(roles) <= 0.4
    assert "decision_point" in moves
    assert "proof_close" in moves
    assert roles[-1] == "result_moment"


def test_director_longform_plan_keeps_chronology_after_hook():
    timestamps = build_sample_schedule(563.41, sample_count=36)
    samples = [
        CreativeFrameSample(
            index=index,
            timestamp=timestamp,
            brightness=0.2 + (index % 5) * 0.14,
            contrast=0.35 + (index % 4) * 0.14,
            sharpness=0.16 + (index % 3) * 0.08,
            colorfulness=0.04 + (index % 6) * 0.04,
            motion=0.72 if index % 4 in {1, 2} else 0.22,
        )
        for index, timestamp in enumerate(timestamps)
    ]

    plan = build_creative_plan(563.41, samples, title="Codex DeepSeek API 配置教程")
    starts = [segment.start for segment in plan.recommended_variant.segments]

    assert starts[1:] == sorted(starts[1:])
    assert starts[1] < 20
    assert starts[-1] > 500


def test_director_longform_plan_limits_repeated_ocr_pages():
    timestamps = build_sample_schedule(563.41, sample_count=36)
    samples = [
        CreativeFrameSample(
            index=index,
            timestamp=timestamp,
            brightness=0.2 + (index % 5) * 0.14,
            contrast=0.35 + (index % 4) * 0.14,
            sharpness=0.16 + (index % 3) * 0.08,
            colorfulness=0.04 + (index % 6) * 0.04,
            motion=0.72 if index % 4 in {1, 2} else 0.22,
        )
        for index, timestamp in enumerate(timestamps)
    ]
    content_analysis = ContentAnalysis(
        provider=ContentProviderStatus(name="ocrmac", status="available", message="test OCR"),
        cues=tuple(
            ContentCue(
                sample_index=sample.index,
                timestamp=sample.timestamp,
                text_density=0.2,
                subtitle_likelihood=0.3,
                interface_likelihood=0.7,
                recognized_text=(
                    "CC Switch 首页 供应商列表"
                    if sample.index < 16
                    else f"步骤 {sample.index} DeepSeek API Key 路由验证"
                ),
                content_tags=(
                    ("cc_switch", "provider", "interface")
                    if sample.index < 16
                    else ("deepseek", "api_key", "validation", "interface")
                ),
                evidence=("ocr:test",),
            )
            for sample in samples
        ),
    )

    plan = build_creative_plan(
        563.41,
        samples,
        title="Codex DeepSeek API 配置教程",
        content_analysis=content_analysis,
    )
    repeated_home_page_segments = [
        segment for segment in plan.recommended_variant.segments if segment.source_sample_index < 16
    ]

    assert len(repeated_home_page_segments) <= 8


def test_creative_plan_serializes_profile_and_semantic_roles(tmp_path):
    plan = build_creative_plan(
        24.0,
        [
            CreativeFrameSample(0, 0.7, 0.50, 0.35, 0.55, 0.70, 0.18),
            CreativeFrameSample(1, 6.0, 0.56, 0.42, 0.60, 0.66, 0.68),
            CreativeFrameSample(2, 12.0, 0.60, 0.50, 0.72, 0.88, 0.42),
            CreativeFrameSample(3, 18.0, 0.62, 0.46, 0.78, 0.92, 0.20),
        ],
        title="美食 烤羊排",
    )

    data = plan_to_dict(plan)
    edl = tmp_path / "candidate_edl.md"
    write_candidate_edl(plan, edl)

    assert data["profile"]["name"] == "food_social"
    assert data["profile_confidence"] == data["profile"]["confidence"]
    assert data["recommended_variant"]["segments"][0]["semantic_role"]
    assert "Semantic Role" in edl.read_text(encoding="utf-8")


def test_creative_plan_serializes_content_evidence(tmp_path):
    content_analysis = ContentAnalysis(
        provider=ContentProviderStatus(name="vision_lite", status="fallback", message="local cues"),
        cues=(
            ContentCue(
                sample_index=0,
                timestamp=0.7,
                text_density=0.18,
                subtitle_likelihood=0.75,
                interface_likelihood=0.55,
                recognized_text="Codex API key",
                content_tags=("codex", "api_key", "subtitle"),
                evidence=("ocr:Codex API key", "subtitle_band:0.75"),
            ),
            ContentCue(
                sample_index=1,
                timestamp=8.0,
                text_density=0.12,
                subtitle_likelihood=0.20,
                interface_likelihood=0.70,
                recognized_text="DeepSeek model",
                content_tags=("deepseek", "model", "interface"),
                evidence=("ocr:DeepSeek model", "interface_layout:0.70"),
            ),
            ContentCue(
                sample_index=2,
                timestamp=18.0,
                text_density=0.10,
                subtitle_likelihood=0.18,
                interface_likelihood=0.60,
                recognized_text="验证成功",
                content_tags=("validation", "interface"),
                evidence=("ocr:验证成功", "interface_layout:0.60"),
            ),
        ),
    )
    plan = build_creative_plan(
        24.0,
        [
            CreativeFrameSample(0, 0.7, 0.50, 0.35, 0.55, 0.10, 0.18),
            CreativeFrameSample(1, 8.0, 0.56, 0.42, 0.60, 0.12, 0.68),
            CreativeFrameSample(2, 18.0, 0.62, 0.46, 0.78, 0.14, 0.20),
        ],
        title="Codex DeepSeek API 配置教程",
        content_analysis=content_analysis,
    )

    data = plan_to_dict(plan)
    edl = tmp_path / "candidate_edl.md"
    write_candidate_edl(plan, edl)
    segments = data["recommended_variant"]["segments"]

    assert data["content_provider"]["name"] == "vision_lite"
    assert data["content_coverage"]["cue_count"] == 3
    assert all(segment["content_tags"] for segment in segments)
    assert all(segment["content_evidence"] for segment in segments)
    assert "Content" in edl.read_text(encoding="utf-8")


def test_creative_plan_serializes_semantic_chapters(tmp_path):
    content_analysis = ContentAnalysis(
        provider=ContentProviderStatus(name="ocrmac", status="available", message="test OCR"),
        cues=(
            ContentCue(
                sample_index=0,
                timestamp=0.7,
                text_density=0.18,
                subtitle_likelihood=0.75,
                interface_likelihood=0.55,
                recognized_text="Codex 保姆级安装教程",
                content_tags=("codex", "install", "subtitle"),
                evidence=("ocr:Codex 保姆级安装教程",),
            ),
            ContentCue(
                sample_index=1,
                timestamp=8.0,
                text_density=0.12,
                subtitle_likelihood=0.20,
                interface_likelihood=0.70,
                recognized_text="添加供应商 DeepSeek API Key",
                content_tags=("deepseek", "api_key", "provider", "interface"),
                evidence=("ocr:DeepSeek API Key",),
            ),
            ContentCue(
                sample_index=2,
                timestamp=18.0,
                text_density=0.10,
                subtitle_likelihood=0.18,
                interface_likelihood=0.60,
                recognized_text="本地路由服务已启动",
                content_tags=("validation", "interface"),
                evidence=("ocr:本地路由服务已启动",),
            ),
        ),
    )
    semantic_timeline = build_semantic_timeline(
        content_analysis=content_analysis,
        audio_analysis=None,
        title="Codex DeepSeek API 配置教程",
        source_duration=24.0,
    )

    plan = build_creative_plan(
        24.0,
        [
            CreativeFrameSample(0, 0.7, 0.50, 0.35, 0.55, 0.10, 0.18),
            CreativeFrameSample(1, 8.0, 0.56, 0.42, 0.60, 0.12, 0.68),
            CreativeFrameSample(2, 18.0, 0.62, 0.46, 0.78, 0.14, 0.20),
        ],
        title="Codex DeepSeek API 配置教程",
        content_analysis=content_analysis,
        semantic_timeline=semantic_timeline,
    )

    data = plan_to_dict(plan)
    edl = tmp_path / "candidate_edl.md"
    write_candidate_edl(plan, edl)
    segments = data["recommended_variant"]["segments"]

    assert data["semantic_provider"]["name"] == "ocr_audio_semantic"
    assert data["semantic_coverage"]["chapter_count"] >= 3
    assert all(segment["chapter_title"] for segment in segments)
    assert "Chapter" in edl.read_text(encoding="utf-8")
