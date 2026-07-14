import json
from pathlib import Path

from PIL import Image

import video_factory.reference_guided_original as reference_guided_original
from video_factory.reference_guided_original import (
    build_asset_sourcing_plan,
    build_caption_timeline_v2,
    build_content_plan,
    build_cover_asset_manifest,
    build_cover_brief,
    build_cover_prompt_pack,
    build_generated_asset_manifest,
    build_reference_blueprint,
    build_reference_guided_paths,
    build_storyboard_v2,
    build_subtitle_burn_command,
    build_subtitle_output_command,
    build_user_delivery,
    build_visual_requirements,
    build_visual_prompt_pack,
    build_voiceover_manifest,
    render_reference_guided_original_video,
    write_subtitles_v2,
    write_reference_guided_quality_report,
)


REFERENCE_PROBE = {
    "format": {"duration": "542.4"},
    "streams": [
        {
            "codec_type": "video",
            "width": 3840,
            "height": 2160,
            "avg_frame_rate": "30/1",
        },
        {"codec_type": "audio", "sample_rate": "48000"},
    ],
}


def test_reference_blueprint_uses_reference_for_learning_not_media_reuse():
    blueprint = build_reference_blueprint(
        Path("/Users/king/Downloads/reference.mp4"),
        REFERENCE_PROBE,
        {"original_topic": "Codex 如何做一键原创视频工厂"},
    )

    assert blueprint["workflow"] == "reference_guided_original"
    assert blueprint["reuse_policy"] == "redraw_by_default"
    assert blueprint["source_media_allowed"] is False
    assert blueprint["source_audio_allowed"] is False
    assert blueprint["default_duration_range_seconds"] == [433.92, 596.64]
    assert blueprint["reference"]["duration_seconds"] == 542.4
    assert blueprint["reference"]["geometry"] == {"width": 3840, "height": 2160}
    assert blueprint["learned_structure"]["chapter_count"] >= 4
    assert blueprint["learned_structure"]["opening_style"]
    assert blueprint["learned_structure"]["visual_types"]


def test_reference_blueprint_duration_range_tracks_source_duration():
    blueprint = build_reference_blueprint(
        Path("/Users/king/Downloads/long-reference.mp4"),
        REFERENCE_PROBE,
        {"target_duration_policy": "source_guided"},
    )

    assert blueprint["reference"]["duration_seconds"] == 542.4
    assert blueprint["default_duration_range_seconds"] == [433.92, 596.64]

    plan = build_content_plan(blueprint, {"source_title": "长视频参考"})
    storyboard = build_storyboard_v2(plan, blueprint)

    assert 433.92 <= plan["target_duration_seconds"] <= 596.64
    assert storyboard["target_duration_seconds"] == plan["target_duration_seconds"]


def test_content_plan_and_storyboard_have_real_chapter_depth():
    blueprint = build_reference_blueprint(
        Path("/Users/king/Downloads/reference.mp4"),
        REFERENCE_PROBE,
        {"original_topic": "普通人如何用 AI 建一个原创视频工厂"},
    )
    plan = build_content_plan(
        blueprint,
        {
            "original_topic": "普通人如何用 AI 建一个原创视频工厂",
            "original_brief": "面向没有专业剪辑能力的创业者，讲清楚从参考拆解到生成交付。",
        },
    )
    storyboard = build_storyboard_v2(plan, blueprint)

    assert plan["topic"] == "普通人如何用 AI 建一个原创视频工厂"
    assert len(plan["chapters"]) >= 4
    assert plan["quality_controls"]["template_phrase_ratio_limit"] <= 0.18
    for chapter in plan["chapters"]:
        assert chapter["viewpoint"]
        assert chapter["evidence_or_example"]
        assert chapter["visual_goal"]
        assert chapter["narration"]
        assert "模板" not in chapter["narration"][:30]

    assert 433.92 <= storyboard["target_duration_seconds"] <= 596.64
    assert len(storyboard["scenes"]) == len(plan["chapters"])
    for scene in storyboard["scenes"]:
        assert scene["source_policy"] == "redraw_by_default"
        assert len(scene["visual_slots"]) >= 2
        assert scene["voiceover"]


def test_reference_guided_original_uses_source_title_when_topic_is_blank():
    source_title = "Lionel Scaloni's Reaction To FIFA World Cup Final Penalty Shootout"
    blueprint = build_reference_blueprint(
        Path("/Users/king/Downloads/reference.mp4"),
        REFERENCE_PROBE,
        {"source_title": source_title, "original_topic": "", "original_brief": ""},
    )
    plan = build_content_plan(blueprint, {"source_title": source_title})

    assert blueprint["topic_hint"] == source_title
    assert plan["topic"] == source_title


def test_sports_topic_generates_sports_specific_plan_not_factory_template():
    topic = "世界盃劇本瘋了！德國出局後劇情徹底失控？哈蘭德VS姆巴佩：今天這三場誰敢保證不爆冷？！"
    blueprint = build_reference_blueprint(
        Path("/Users/king/Downloads/world-cup.mp4"),
        REFERENCE_PROBE,
        {"source_title": topic},
    )
    plan = build_content_plan(blueprint, {"source_title": topic})

    joined = json.dumps(plan, ensure_ascii=False)

    assert any(word in joined for word in ["世界杯", "世界盃", "德国", "德國", "哈兰德", "哈蘭德", "姆巴佩", "爆冷"])
    assert "视频工厂" not in joined
    assert "素材任务" not in joined
    assert "专业配音" not in joined
    assert "JSON" not in joined
    assert "模板" not in joined
    assert any("爆冷" in chapter["title"] or "比赛" in chapter["title"] for chapter in plan["chapters"])


def test_sports_mock_assets_use_field_visual_language(tmp_path):
    topic = "世界盃爆冷：哈蘭德和姆巴佩今天谁能改写比赛"
    blueprint = build_reference_blueprint(Path("/Users/king/Downloads/world-cup.mp4"), REFERENCE_PROBE, {"source_title": topic})
    plan = build_content_plan(blueprint, {"source_title": topic})
    storyboard = build_storyboard_v2(plan, blueprint, {"target_duration_seconds": 48})
    requirements = build_visual_requirements(storyboard, plan, {"visual_asset_strategy": "images2_first"})
    sourcing_plan = build_asset_sourcing_plan(requirements, {"image_provider": "mock_images2"})
    prompt_pack = build_visual_prompt_pack(storyboard, {}, sourcing_plan=sourcing_plan)
    manifest = build_generated_asset_manifest(
        tmp_path / "generated_assets",
        prompt_pack,
        provider="mock_images2",
        sourcing_plan=sourcing_plan,
    )
    first_asset = Path(manifest["scenes"][0]["assets"][0]["path"])

    with Image.open(first_asset) as image:
        red, green, blue = image.convert("RGB").getpixel((260, 360))

    assert green > red
    assert green > blue
    assert storyboard["scenes"][0]["topic_domain"] == "sports"


def test_sports_mock_assets_vary_visual_language_across_chapters(tmp_path):
    topic = "世界盃劇本瘋了！德國出局後哈蘭德和姆巴佩誰更危險"
    blueprint = build_reference_blueprint(Path("/Users/king/Downloads/world-cup.mp4"), REFERENCE_PROBE, {"source_title": topic})
    plan = build_content_plan(blueprint, {"source_title": topic})
    storyboard = build_storyboard_v2(plan, blueprint, {"target_duration_seconds": 70})
    requirements = build_visual_requirements(storyboard, plan, {"visual_asset_strategy": "images2_first"})
    sourcing_plan = build_asset_sourcing_plan(requirements, {"image_provider": "mock_images2"})
    prompt_pack = build_visual_prompt_pack(storyboard, {}, sourcing_plan=sourcing_plan)
    manifest = build_generated_asset_manifest(
        tmp_path / "generated_assets",
        prompt_pack,
        provider="mock_images2",
        sourcing_plan=sourcing_plan,
    )

    sample_pixels = []
    for scene in manifest["scenes"][:5]:
        asset_path = Path(scene["assets"][0]["path"])
        with Image.open(asset_path) as image:
            sample_pixels.append(image.convert("RGB").getpixel((260, 360)))

    assert len(set(sample_pixels)) >= 3


def test_caption_timeline_and_subtitles_are_auto_generated_for_reference_guided_original(tmp_path):
    blueprint = build_reference_blueprint(
        Path("/Users/king/Downloads/reference.mp4"),
        REFERENCE_PROBE,
        {"original_topic": "AI 原创视频质量体系"},
    )
    plan = build_content_plan(blueprint, {"original_topic": "AI 原创视频质量体系"})
    storyboard = build_storyboard_v2(plan, blueprint, {"target_duration_seconds": 48})
    caption_timeline = build_caption_timeline_v2(storyboard)
    subtitles_path = tmp_path / "subtitles.srt"

    write_subtitles_v2(subtitles_path, caption_timeline)

    assert caption_timeline["version"] == "reference_caption_timeline_v1"
    assert caption_timeline["readability"]["max_chars_per_caption"] <= 32
    assert caption_timeline["style"]["placement"] == "bottom_safe_area"
    assert caption_timeline["captions"]
    assert len(caption_timeline["captions"]) >= len(storyboard["scenes"])
    assert caption_timeline["captions"][0]["start"] == 0.0
    for previous, current in zip(caption_timeline["captions"], caption_timeline["captions"][1:]):
        assert current["start"] >= previous["end"]
    for caption in caption_timeline["captions"]:
        assert caption["text"]
        assert len(caption["text"]) <= caption_timeline["readability"]["max_chars_per_caption"]
        assert caption["end"] - caption["start"] >= 0.8
    srt = subtitles_path.read_text(encoding="utf-8")
    assert "00:00:00,000 -->" in srt
    assert "AI 原创视频质量体系" in srt


def test_subtitle_burn_command_uses_generated_srt():
    command = build_subtitle_burn_command("release_raw.mp4", "subtitles.srt", "release.mp4")
    joined = " ".join(command)

    assert command[0] == "ffmpeg"
    assert "subtitles=filename=" in joined
    assert "subtitles.srt" in joined
    assert command[-1] == "release.mp4"


def test_subtitle_output_command_falls_back_to_mp4_text_track_without_burn_filter():
    command = build_subtitle_output_command(
        "release_raw.mp4",
        "subtitles.srt",
        "release.mp4",
        burn_supported=False,
    )

    assert command[0] == "ffmpeg"
    assert "-c:s" in command
    assert "mov_text" in command
    assert "subtitles.srt" in command
    assert command[-1] == "release.mp4"


def test_visual_requirements_are_created_after_storyboard_and_images2_first():
    blueprint = build_reference_blueprint(
        Path("/Users/king/Downloads/reference.mp4"),
        REFERENCE_PROBE,
        {"original_topic": "AI 原创视频质量体系"},
    )
    plan = build_content_plan(blueprint, {"original_topic": "AI 原创视频质量体系"})
    storyboard = build_storyboard_v2(plan, blueprint, {"target_duration_seconds": 48})
    requirements = build_visual_requirements(storyboard, plan, {"visual_asset_strategy": "images2_first"})
    sourcing_plan = build_asset_sourcing_plan(requirements, {"image_provider": "images2"})

    assert requirements["stage"] == "after_storyboard"
    assert requirements["default_source_priority"] == ["images2", "licensed_stock", "user_owned"]
    assert len(requirements["scenes"]) == len(storyboard["scenes"])
    for scene in requirements["scenes"]:
        assert len(scene["requirements"]) >= 2
        for requirement in scene["requirements"]:
            assert requirement["need_id"].startswith(f"scene_{scene['scene_index']:02d}_")
            assert requirement["quality_bar"]["min_width"] == 1920
            assert requirement["quality_bar"]["min_height"] == 1080
            assert requirement["quality_bar"]["avoid_reference_frame_reuse"] is True
            assert requirement["source_priority"][0] == "images2"
            assert requirement["reason_from_storyboard"]

    assert sourcing_plan["strategy"] == "images2_first"
    assert sourcing_plan["reference_frame_allowed"] is False
    assert sourcing_plan["provider_contract"] == "images2.generate(requirement.prompt, size, style)"
    assert len(sourcing_plan["decisions"]) >= len(storyboard["scenes"]) * 2
    assert all(decision["selected_source"] == "ai_generated" for decision in sourcing_plan["decisions"])
    assert all(decision["provider"] == "images2" for decision in sourcing_plan["decisions"])


def test_prompt_pack_and_mock_assets_are_preview_only_per_scene(tmp_path):
    blueprint = build_reference_blueprint(
        Path("/Users/king/Downloads/reference.mp4"),
        REFERENCE_PROBE,
        {"original_topic": "AI 原创视频质量体系"},
    )
    plan = build_content_plan(blueprint, {"original_topic": "AI 原创视频质量体系"})
    storyboard = build_storyboard_v2(plan, blueprint, {"target_duration_seconds": 48})
    requirements = build_visual_requirements(storyboard, plan, {"visual_asset_strategy": "images2_first"})
    sourcing_plan = build_asset_sourcing_plan(requirements, {"image_provider": "mock_images2"})
    prompt_pack = build_visual_prompt_pack(
        storyboard,
        {"visual_style": "documentary_illustration"},
        sourcing_plan=sourcing_plan,
    )
    manifest = build_generated_asset_manifest(
        tmp_path / "generated_assets",
        prompt_pack,
        provider="mock_images2",
        sourcing_plan=sourcing_plan,
    )

    assert prompt_pack["style"] == "documentary_illustration"
    assert prompt_pack["built_from"] == "asset_sourcing_plan"
    assert len(prompt_pack["scenes"]) == len(storyboard["scenes"])
    assert all(len(scene["prompts"]) >= 2 for scene in prompt_pack["scenes"])
    assert manifest["status"] == "ready"
    assert manifest["provider"] == "mock_images2"
    assert manifest["publish_ready"] is False
    assert manifest["sourcing_strategy"] == "images2_first"
    assert manifest["asset_repetition_ratio"] < 0.35
    for scene in manifest["scenes"]:
        assert len(scene["assets"]) >= 2
        for asset in scene["assets"]:
            assert Path(asset["path"]).exists()
            assert asset["license"] == "generated_original"
            assert asset["origin"] == "ai_generated"
            assert asset["publish_ready"] is False


def test_cover_brief_and_prompt_pack_are_generated_after_full_analysis():
    blueprint = build_reference_blueprint(
        Path("/Users/king/Downloads/reference.mp4"),
        REFERENCE_PROBE,
        {"original_topic": "AI 原创视频质量体系"},
    )
    plan = build_content_plan(blueprint, {"original_topic": "AI 原创视频质量体系"})
    storyboard = build_storyboard_v2(plan, blueprint, {"target_duration_seconds": 48})
    requirements = build_visual_requirements(storyboard, plan, {"visual_asset_strategy": "images2_first"})
    sourcing_plan = build_asset_sourcing_plan(requirements, {"image_provider": "images2"})

    brief = build_cover_brief(
        blueprint,
        plan,
        storyboard,
        sourcing_plan,
        {"recommended_publish_title": "AI原创视频质量体系：先质检再发布"},
    )
    prompt_pack = build_cover_prompt_pack(brief, {"image_provider": "images2"})

    assert brief["stage"] == "after_full_video_analysis"
    assert brief["topic"] == "AI 原创视频质量体系"
    assert brief["core_message"]
    assert len(brief["cover_angles"]) == 3
    assert brief["visual_direction"]["focal_point_count"] == 1
    assert len(brief["visual_direction"]["text_overlay"]) <= 10
    assert "复杂拼贴" in brief["forbidden_elements"]
    assert "假新闻感" in brief["forbidden_elements"]
    assert prompt_pack["provider"] == "images2"
    assert prompt_pack["cover_count"] == 3
    assert prompt_pack["style_rules"]["max_text_chars"] == 10
    for prompt in prompt_pack["prompts"]:
        assert prompt["size"] == "1920x1080"
        assert prompt["provider"] == "images2"
        assert len(prompt["text_overlay"]) <= 10
        assert "single clear focal point" in prompt["prompt"]
        assert "complex collage" in prompt["negative_prompt"]
        assert "fake news footage" in prompt["negative_prompt"]


def test_cover_asset_manifest_keeps_mock_candidate_as_preview_only(tmp_path):
    paths = build_reference_guided_paths(tmp_path)
    blueprint = build_reference_blueprint(
        Path("/Users/king/Downloads/reference.mp4"),
        REFERENCE_PROBE,
        {"original_topic": "AI 原创视频质量体系"},
    )
    plan = build_content_plan(blueprint, {"original_topic": "AI 原创视频质量体系"})
    storyboard = build_storyboard_v2(plan, blueprint, {"target_duration_seconds": 48})
    requirements = build_visual_requirements(storyboard, plan, {"visual_asset_strategy": "images2_first"})
    sourcing_plan = build_asset_sourcing_plan(requirements, {"image_provider": "mock_images2"})
    brief = build_cover_brief(blueprint, plan, storyboard, sourcing_plan, {})
    prompt_pack = build_cover_prompt_pack(brief, {"image_provider": "mock_images2"})

    manifest = build_cover_asset_manifest(paths.cover_candidates_dir, paths.cover, prompt_pack, provider="mock_images2")

    assert manifest["status"] == "ready"
    assert manifest["provider"] == "mock_images2"
    assert manifest["publish_ready"] is False
    assert manifest["recommended_cover"]["path"] == str(paths.cover)
    assert manifest["quality"]["not_overcomplicated"] is True
    assert manifest["quality"]["text_concise"] is True
    assert Path(manifest["recommended_cover"]["source_candidate_path"]).exists()
    assert paths.cover.exists()


def test_cover_asset_manifest_blocks_preview_only_mock_cover(tmp_path):
    paths = build_reference_guided_paths(tmp_path)
    blueprint = build_reference_blueprint(
        Path("/Users/king/Downloads/reference.mp4"),
        REFERENCE_PROBE,
        {"original_topic": "AI 原创视频质量体系"},
    )
    plan = build_content_plan(blueprint, {"original_topic": "AI 原创视频质量体系"})
    storyboard = build_storyboard_v2(plan, blueprint, {"target_duration_seconds": 48})
    requirements = build_visual_requirements(storyboard, plan, {"visual_asset_strategy": "images2_first"})
    sourcing_plan = build_asset_sourcing_plan(requirements, {"image_provider": "mock_image"})
    brief = build_cover_brief(blueprint, plan, storyboard, sourcing_plan, {})
    prompt_pack = build_cover_prompt_pack(brief, {"image_provider": "mock_image"})

    manifest = build_cover_asset_manifest(paths.cover_candidates_dir, paths.cover, prompt_pack, provider="mock_image")

    assert manifest["status"] == "ready"
    assert manifest["publish_ready"] is False
    assert manifest["quality"]["text_concise"] is True
    assert paths.cover.exists()


def test_quality_report_blocks_publish_when_assets_or_voice_are_not_publish_ready(tmp_path):
    paths = build_reference_guided_paths(tmp_path)
    blueprint = build_reference_blueprint(
        Path("/Users/king/Downloads/reference.mp4"),
        REFERENCE_PROBE,
        {"original_topic": "AI 原创视频质量体系"},
    )
    plan = build_content_plan(blueprint, {"original_topic": "AI 原创视频质量体系"})
    storyboard = build_storyboard_v2(plan, blueprint, {"target_duration_seconds": 48})
    requirements = build_visual_requirements(storyboard, plan, {"visual_asset_strategy": "images2_first"})
    sourcing_plan = build_asset_sourcing_plan(requirements, {"image_provider": "images2"})
    prompt_pack = build_visual_prompt_pack(storyboard, {}, sourcing_plan=sourcing_plan)
    asset_manifest = {"status": "missing", "scenes": [], "asset_repetition_ratio": 1.0}
    cover_brief = build_cover_brief(blueprint, plan, storyboard, sourcing_plan, {})
    cover_prompt_pack = build_cover_prompt_pack(cover_brief, {"image_provider": "mock_images2"})
    cover_manifest = build_cover_asset_manifest(paths.cover_candidates_dir, paths.cover, cover_prompt_pack, provider="mock_images2")
    voiceover_manifest = build_voiceover_manifest(storyboard, paths.audio_dir, provider="macos_say")
    caption_timeline = build_caption_timeline_v2(storyboard)
    write_subtitles_v2(paths.subtitles, caption_timeline)

    quality = write_reference_guided_quality_report(
        paths,
        blueprint,
        plan,
        storyboard,
        prompt_pack,
        asset_manifest,
        voiceover_manifest,
        requirements,
        sourcing_plan,
        cover_brief,
        cover_prompt_pack,
        cover_manifest,
        caption_timeline,
        require_video=False,
    )
    delivery = build_user_delivery(paths, quality, plan, asset_manifest, voiceover_manifest)

    assert quality["status"] == "failed"
    assert quality["checks"]["generated_assets_ready"] is False
    assert quality["checks"]["voice_provider_publishable"] is False
    assert any(issue["code"] == "missing_generated_assets" for issue in quality["issues"])
    assert any(issue["code"] == "draft_voiceover_provider" for issue in quality["issues"])
    assert delivery["release_decision"]["status"] == "需补素材"
    assert "配音" in " / ".join(delivery["next_actions"])


def test_quality_report_blocks_overcompressed_reference_guided_duration(tmp_path):
    paths = build_reference_guided_paths(tmp_path)
    blueprint = build_reference_blueprint(
        Path("/Users/king/Downloads/reference.mp4"),
        REFERENCE_PROBE,
        {"target_duration_policy": "source_guided"},
    )
    plan = build_content_plan(blueprint, {"source_title": "长参考片"})
    storyboard = build_storyboard_v2(plan, blueprint)
    storyboard["target_duration_seconds"] = 300
    requirements = build_visual_requirements(storyboard, plan, {"visual_asset_strategy": "images2_first"})
    sourcing_plan = build_asset_sourcing_plan(requirements, {"image_provider": "mock_images2"})
    prompt_pack = build_visual_prompt_pack(storyboard, {}, sourcing_plan=sourcing_plan)
    asset_manifest = build_generated_asset_manifest(
        paths.generated_assets_dir,
        prompt_pack,
        provider="mock_images2",
        sourcing_plan=sourcing_plan,
    )
    cover_brief = build_cover_brief(blueprint, plan, storyboard, sourcing_plan, {})
    cover_prompt_pack = build_cover_prompt_pack(cover_brief, {"image_provider": "mock_images2"})
    cover_manifest = build_cover_asset_manifest(paths.cover_candidates_dir, paths.cover, cover_prompt_pack, provider="mock_images2")
    voiceover_manifest = build_voiceover_manifest(storyboard, paths.audio_dir, provider="mock_professional_voice")
    caption_timeline = build_caption_timeline_v2(storyboard)
    write_subtitles_v2(paths.subtitles, caption_timeline)

    quality = write_reference_guided_quality_report(
        paths,
        blueprint,
        plan,
        storyboard,
        prompt_pack,
        asset_manifest,
        voiceover_manifest,
        requirements,
        sourcing_plan,
        cover_brief,
        cover_prompt_pack,
        cover_manifest,
        caption_timeline,
        require_video=False,
    )

    assert quality["status"] == "failed"
    assert quality["checks"]["duration_floor_respected"] is False
    assert quality["strategy"]["minimum_duration_seconds"] == 433.92
    assert any(issue["code"] == "duration_floor_not_met" for issue in quality["issues"])


def test_quality_report_blocks_publish_when_visuals_are_mock_preview_only(tmp_path):
    paths = build_reference_guided_paths(tmp_path)
    blueprint = build_reference_blueprint(
        Path("/Users/king/Downloads/reference.mp4"),
        REFERENCE_PROBE,
        {"original_topic": "AI 原创视频质量体系"},
    )
    plan = build_content_plan(blueprint, {"original_topic": "AI 原创视频质量体系"})
    storyboard = build_storyboard_v2(plan, blueprint, {"target_duration_seconds": 48})
    requirements = build_visual_requirements(storyboard, plan, {"visual_asset_strategy": "images2_first"})
    sourcing_plan = build_asset_sourcing_plan(requirements, {"image_provider": "mock_image"})
    prompt_pack = build_visual_prompt_pack(storyboard, {}, sourcing_plan=sourcing_plan)
    asset_manifest = build_generated_asset_manifest(
        paths.generated_assets_dir,
        prompt_pack,
        provider="mock_image",
        sourcing_plan=sourcing_plan,
    )
    cover_brief = build_cover_brief(blueprint, plan, storyboard, sourcing_plan, {})
    cover_prompt_pack = build_cover_prompt_pack(cover_brief, {"image_provider": "mock_images2"})
    cover_manifest = build_cover_asset_manifest(paths.cover_candidates_dir, paths.cover, cover_prompt_pack, provider="mock_images2")
    voiceover_manifest = build_voiceover_manifest(storyboard, paths.audio_dir, provider="mock_professional_voice")
    caption_timeline = build_caption_timeline_v2(storyboard)
    write_subtitles_v2(paths.subtitles, caption_timeline)

    quality = write_reference_guided_quality_report(
        paths,
        blueprint,
        plan,
        storyboard,
        prompt_pack,
        asset_manifest,
        voiceover_manifest,
        requirements,
        sourcing_plan,
        cover_brief,
        cover_prompt_pack,
        cover_manifest,
        caption_timeline,
        require_video=False,
    )
    delivery = build_user_delivery(paths, quality, plan, asset_manifest, voiceover_manifest)

    assert asset_manifest["status"] == "ready"
    assert asset_manifest["publish_ready"] is False
    assert quality["status"] == "failed"
    assert quality["checks"]["generated_assets_publish_ready"] is False
    assert any(issue["code"] == "preview_only_generated_assets" for issue in quality["issues"])
    assert quality["strategy"]["publish_tier"] == "needs_assets"
    assert delivery["release_decision"]["status"] == "需补素材"


def test_quality_report_blocks_publish_when_cover_is_preview_only(tmp_path):
    paths = build_reference_guided_paths(tmp_path)
    blueprint = build_reference_blueprint(
        Path("/Users/king/Downloads/reference.mp4"),
        REFERENCE_PROBE,
        {"original_topic": "AI 原创视频质量体系"},
    )
    plan = build_content_plan(blueprint, {"original_topic": "AI 原创视频质量体系"})
    storyboard = build_storyboard_v2(plan, blueprint, {"target_duration_seconds": 48})
    requirements = build_visual_requirements(storyboard, plan, {"visual_asset_strategy": "images2_first"})
    sourcing_plan = build_asset_sourcing_plan(requirements, {"image_provider": "mock_images2"})
    prompt_pack = build_visual_prompt_pack(storyboard, {}, sourcing_plan=sourcing_plan)
    asset_manifest = build_generated_asset_manifest(
        paths.generated_assets_dir,
        prompt_pack,
        provider="mock_images2",
        sourcing_plan=sourcing_plan,
    )
    cover_brief = build_cover_brief(blueprint, plan, storyboard, sourcing_plan, {})
    cover_prompt_pack = build_cover_prompt_pack(cover_brief, {"image_provider": "mock_image"})
    cover_manifest = build_cover_asset_manifest(paths.cover_candidates_dir, paths.cover, cover_prompt_pack, provider="mock_image")
    voiceover_manifest = build_voiceover_manifest(storyboard, paths.audio_dir, provider="mock_professional_voice")
    caption_timeline = build_caption_timeline_v2(storyboard)
    write_subtitles_v2(paths.subtitles, caption_timeline)

    quality = write_reference_guided_quality_report(
        paths,
        blueprint,
        plan,
        storyboard,
        prompt_pack,
        asset_manifest,
        voiceover_manifest,
        requirements,
        sourcing_plan,
        cover_brief,
        cover_prompt_pack,
        cover_manifest,
        caption_timeline,
        require_video=False,
    )
    delivery = build_user_delivery(paths, quality, plan, asset_manifest, voiceover_manifest)

    assert quality["status"] == "failed"
    assert quality["checks"]["cover_assets_publish_ready"] is False
    assert any(issue["code"] == "preview_only_cover_asset" for issue in quality["issues"])
    assert quality["strategy"]["publish_tier"] == "needs_assets"
    assert delivery["release_decision"]["status"] == "需补素材"


def test_quality_report_blocks_mock_providers_from_publish_candidate(tmp_path):
    paths = build_reference_guided_paths(tmp_path)
    blueprint = build_reference_blueprint(
        Path("/Users/king/Downloads/reference.mp4"),
        REFERENCE_PROBE,
        {"original_topic": "AI 原创视频质量体系"},
    )
    plan = build_content_plan(blueprint, {"original_topic": "AI 原创视频质量体系"})
    storyboard = build_storyboard_v2(plan, blueprint, {"target_duration_seconds": 48})
    requirements = build_visual_requirements(storyboard, plan, {"visual_asset_strategy": "images2_first"})
    sourcing_plan = build_asset_sourcing_plan(requirements, {"image_provider": "mock_images2"})
    prompt_pack = build_visual_prompt_pack(storyboard, {}, sourcing_plan=sourcing_plan)
    asset_manifest = build_generated_asset_manifest(
        paths.generated_assets_dir,
        prompt_pack,
        provider="mock_images2",
        sourcing_plan=sourcing_plan,
    )
    asset_manifest["publish_ready"] = True
    for scene in asset_manifest["scenes"]:
        for asset in scene["assets"]:
            asset["publish_ready"] = True
    cover_brief = build_cover_brief(blueprint, plan, storyboard, sourcing_plan, {})
    cover_prompt_pack = build_cover_prompt_pack(cover_brief, {"image_provider": "mock_images2"})
    cover_manifest = build_cover_asset_manifest(paths.cover_candidates_dir, paths.cover, cover_prompt_pack, provider="mock_images2")
    cover_manifest["publish_ready"] = True
    voiceover_manifest = build_voiceover_manifest(storyboard, paths.audio_dir, provider="mock_professional_voice")
    paths.video.write_bytes(b"placeholder")
    paths.contact_sheet.write_bytes(b"placeholder")
    caption_timeline = build_caption_timeline_v2(storyboard)
    write_subtitles_v2(paths.subtitles, caption_timeline)

    quality = write_reference_guided_quality_report(
        paths,
        blueprint,
        plan,
        storyboard,
        prompt_pack,
        asset_manifest,
        voiceover_manifest,
        requirements,
        sourcing_plan,
        cover_brief,
        cover_prompt_pack,
        cover_manifest,
        caption_timeline,
        require_video=True,
    )
    delivery = build_user_delivery(paths, quality, plan, asset_manifest, voiceover_manifest)
    paths.user_delivery.write_text(json.dumps(delivery, ensure_ascii=False), encoding="utf-8")

    assert quality["status"] == "failed"
    assert quality["strategy"]["publish_tier"] == "needs_assets"
    assert quality["checks"]["reference_media_not_reused"] is True
    assert quality["checks"]["content_plan_depth"] is True
    assert quality["checks"]["generated_assets_ready"] is True
    assert quality["checks"]["generated_assets_publish_ready"] is False
    assert quality["checks"]["asset_sourcing_plan_images2_first"] is True
    assert quality["checks"]["cover_assets_publish_ready"] is False
    assert quality["checks"]["cover_text_concise"] is True
    assert quality["checks"]["subtitle_timeline_present"] is True
    assert quality["checks"]["voice_provider_publishable"] is True
    assert any(issue["code"] == "preview_only_generated_assets" for issue in quality["issues"])
    assert any(issue["code"] == "preview_only_cover_asset" for issue in quality["issues"])
    assert delivery["release_decision"]["status"] == "需补素材"
    assert json.loads(paths.user_delivery.read_text(encoding="utf-8"))["mode"] == "一键原创视频"


def test_render_reference_guided_original_video_writes_delivery_artifacts(tmp_path, monkeypatch):
    reference_video = tmp_path / "reference.mp4"
    reference_video.write_bytes(b"reference")

    def fake_probe(path):
        if Path(path) == reference_video:
            return REFERENCE_PROBE
        return {
            "format": {"duration": "24.0"},
            "streams": [{"codec_type": "video", "width": 1920, "height": 1080}, {"codec_type": "audio"}],
        }

    run_commands = []

    def fake_run(command):
        run_commands.append(command)
        output = Path(command[-1])
        output.parent.mkdir(parents=True, exist_ok=True)
        if output.suffix.lower() in {".png", ".jpg", ".jpeg"}:
            Image.new("RGB", (320, 180), "#123432").save(output)
        else:
            output.write_bytes(b"video")

    def fake_concat(segment_paths, concat_path, output_video):
        assert len(segment_paths) >= 4
        Path(concat_path).write_text("\n".join(str(path) for path in segment_paths), encoding="utf-8")
        Path(output_video).write_bytes(b"video")

    def fake_contact_sheet(video_path, contact_sheet_path, duration):
        del video_path, duration
        Image.new("RGB", (640, 360), "#20312c").save(contact_sheet_path)

    monkeypatch.setattr(reference_guided_original, "probe_media", fake_probe)
    monkeypatch.setattr(reference_guided_original, "_run", fake_run)
    monkeypatch.setattr(reference_guided_original, "_concat_segments", fake_concat)
    monkeypatch.setattr(reference_guided_original, "_write_contact_sheet", fake_contact_sheet)

    artifacts = render_reference_guided_original_video(
        reference_video,
        tmp_path / "job",
            {
                "original_topic": "普通人如何用 AI 建一个原创视频工厂",
                "target_duration_seconds": 24,
                "image_provider": "mock_images2",
                "voice_provider": "mock_professional_voice",
            },
        )

    for key in [
        "video",
        "cover",
        "contact_sheet",
        "reference_blueprint",
        "content_plan",
            "script_v2",
            "storyboard_v2",
            "visual_requirements",
            "asset_sourcing_plan",
            "cover_brief",
            "cover_prompt_pack",
            "cover_asset_manifest",
            "caption_timeline",
            "subtitles",
            "visual_prompt_pack",
            "generated_asset_manifest",
        "voiceover_manifest",
        "user_delivery",
        "quality_report",
    ]:
        assert Path(artifacts[key]).exists()
    quality = json.loads(Path(artifacts["quality_report"]).read_text(encoding="utf-8"))
    delivery = json.loads(Path(artifacts["user_delivery"]).read_text(encoding="utf-8"))
    assert quality["status"] == "failed"
    assert quality["strategy"]["publish_tier"] == "needs_assets"
    assert delivery["release_decision"]["status"] == "需补素材"
    assert any(
        "subtitles=filename=" in " ".join(command) or "mov_text" in command
        for command in run_commands
    )
