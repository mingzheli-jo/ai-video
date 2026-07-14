import json

from PIL import Image

import video_factory.original as original
from video_factory.original import (
    build_original_paths,
    build_original_scene_command,
    build_original_scene_sequence_command,
    build_original_strategy,
    build_storyboard,
    write_originality_report,
    write_original_quality_report,
)


def test_build_original_strategy_turns_topic_into_publishable_plan():
    strategy = build_original_strategy(
        {
            "original_topic": "Codex 批量视频工厂",
            "original_brief": "讲清楚如何从选题、脚本、分镜到质检批量生产。",
            "creative_strength": "strong",
            "quality_strictness": "audit",
            "production_notes": "不要引用原视频，做成可发布教程。",
        }
    )

    assert strategy["topic"] == "Codex 批量视频工厂"
    assert strategy["format"] == "original_tutorial"
    assert strategy["target_duration"] >= 150
    assert len(strategy["chapters"]) >= 6
    assert any("质检" in chapter for chapter in strategy["chapters"])
    assert "不要引用原视频" in strategy["production_notes"]


def test_build_storyboard_contains_motion_and_asset_instructions():
    strategy = build_original_strategy(
        {
            "original_topic": "视频工厂质量闸门",
            "original_brief": "解释相似度、音频复用率、文本重合率如何拦截风险。",
            "creative_strength": "balanced",
        }
    )

    storyboard = build_storyboard(strategy)

    assert len(storyboard["scenes"]) == len(strategy["chapters"])
    assert storyboard["scenes"][0]["duration"] > 0
    assert storyboard["scenes"][0]["motion"]
    assert storyboard["scenes"][0]["visual_goal"]
    assert storyboard["scenes"][0]["voiceover"]
    assert storyboard["asset_requirements"]


def test_original_scene_command_pins_image_input_to_project_fps():
    command = build_original_scene_command("card.png", "scene.mp4", 15.0)

    assert "-framerate" in command
    assert command[command.index("-framerate") + 1] == "30"
    assert command.index("-framerate") < command.index("-i")


def test_original_scene_sequence_command_uses_voiceover_audio_file():
    command = build_original_scene_sequence_command("frames/frame_%03d.png", "scene.mp4", 15.0, 18, audio_path="voice.aiff")

    assert "voice.aiff" in command
    assert "anullsrc=channel_layout=stereo:sample_rate=44100" not in command
    assert "-af" in command
    assert "apad" in command[command.index("-af") + 1]


def test_build_motion_plan_adds_distinct_patterns_and_keyframes():
    assert hasattr(original, "build_motion_plan")
    strategy = build_original_strategy(
        {
            "original_topic": "原创生成系统",
            "creative_strength": "strong",
        }
    )
    storyboard = build_storyboard(strategy)

    motion_plan = original.build_motion_plan(storyboard)

    shots = motion_plan["shots"]
    assert len(shots) == len(storyboard["scenes"])
    assert len({shot["shot_pattern"] for shot in shots}) >= 4
    assert all(len(shot["keyframes"]) >= 3 for shot in shots)
    assert all(shot["motion_density"] >= 0.6 for shot in shots)


def test_build_caption_timeline_is_monotonic_and_scene_bounded():
    assert hasattr(original, "build_caption_timeline")
    strategy = build_original_strategy(
        {
            "original_topic": "Codex 批量视频工厂",
            "creative_strength": "balanced",
        }
    )
    storyboard = build_storyboard(strategy)

    caption_timeline = original.build_caption_timeline(storyboard)

    captions = caption_timeline["captions"]
    assert captions
    assert captions[0]["start"] == 0.0
    for previous, current in zip(captions, captions[1:]):
        assert current["start"] >= previous["end"]
    for caption in captions:
        scene = storyboard["scenes"][caption["scene_index"]]
        assert caption["start"] >= scene["start"]
        assert caption["end"] <= scene["end"]
        assert caption["text"]
    assert captions[-1]["end"] <= storyboard["target_duration"]


def test_asset_pass_report_marks_missing_library_as_not_ready(tmp_path):
    assert hasattr(original, "build_asset_pass_report")
    strategy = build_original_strategy({"original_topic": "素材通行证"})
    storyboard = build_storyboard(strategy)

    report = original.build_asset_pass_report(None, storyboard)

    assert report["status"] == "missing"
    assert report["publish_ready"] is False
    assert report["assets_found"] == 0
    assert report["license_manifest_present"] is False


def test_asset_pass_report_accepts_owned_assets_with_license_manifest(tmp_path):
    assert hasattr(original, "build_asset_pass_report")
    asset_dir = tmp_path / "owned_assets"
    asset_dir.mkdir()
    (asset_dir / "scene-a.png").write_bytes(b"fake-image")
    (asset_dir / "scene-b.mp4").write_bytes(b"fake-video")
    (asset_dir / "asset_licenses.json").write_text(
        json.dumps({"license": "owned_or_authorized", "owner": "internal"}),
        encoding="utf-8",
    )
    strategy = build_original_strategy({"original_topic": "素材通行证"})
    storyboard = build_storyboard(strategy)

    report = original.build_asset_pass_report(asset_dir, storyboard)
    usage_plan = original.build_asset_usage_plan(storyboard, report)

    assert report["status"] == "ready"
    assert report["publish_ready"] is True
    assert report["assets_found"] == 2
    assert report["license_manifest_present"] is True
    assert usage_plan["status"] == "ready"
    assert len(usage_plan["scene_assets"]) == len(storyboard["scenes"])


def test_write_scene_frames_embeds_owned_image_asset(tmp_path):
    asset_dir = tmp_path / "owned_assets"
    asset_dir.mkdir()
    asset_path = asset_dir / "scene-a.png"
    Image.new("RGB", (320, 180), "#d22f27").save(asset_path)
    (asset_dir / "asset_licenses.json").write_text(
        json.dumps({"license": "owned_or_authorized", "owner": "internal"}),
        encoding="utf-8",
    )
    strategy = build_original_strategy({"original_topic": "素材入镜"})
    storyboard = build_storyboard(strategy)
    motion_plan = original.build_motion_plan(storyboard)
    asset_pass = original.build_asset_pass_report(asset_dir, storyboard)
    usage_plan = original.build_asset_usage_plan(storyboard, asset_pass)
    scene = storyboard["scenes"][0]
    shot = motion_plan["shots"][0]

    original._write_scene_frames(  # noqa: SLF001 - intentional regression coverage for rendered frame output.
        tmp_path / "frames",
        scene,
        strategy,
        shot,
        original.DEFAULT_ORIGINAL_GEOMETRY,
        frame_count=1,
        asset_usage_plan=usage_plan,
    )

    frame = Image.open(tmp_path / "frames" / "frame_000.png").convert("RGB")
    asset_crop = frame.crop((1300, 455, 1650, 660))
    red_pixels = sum(1 for r, g, b in asset_crop.getdata() if r > 170 and g < 90 and b < 90)
    assert red_pixels > 1000


def test_original_reports_mark_no_source_reuse(tmp_path):
    paths = build_original_paths(tmp_path)
    strategy = build_original_strategy({"original_topic": "原创生成系统"})
    storyboard = build_storyboard(strategy)
    assert hasattr(original, "build_motion_plan")
    assert hasattr(original, "build_caption_timeline")
    motion_plan = original.build_motion_plan(storyboard)
    caption_timeline = original.build_caption_timeline(storyboard)

    paths.motion_plan.write_text(json.dumps(motion_plan), encoding="utf-8")
    paths.caption_timeline.write_text(json.dumps(caption_timeline), encoding="utf-8")
    paths.subtitles.write_text("1\n00:00:00,000 --> 00:00:01,000\n原创字幕\n", encoding="utf-8")
    paths.asset_pass_report.write_text(
        json.dumps({"status": "ready", "publish_ready": True, "assets_found": len(storyboard["scenes"])}),
        encoding="utf-8",
    )
    paths.asset_usage_plan.write_text(
        json.dumps(
            {
                "status": "ready",
                "scene_assets": [{"scene_index": scene["index"], "asset_path": f"asset-{scene['index']}.png"} for scene in storyboard["scenes"]],
            }
        ),
        encoding="utf-8",
    )
    paths.voiceover_manifest.write_text(
        json.dumps(
            {
                "status": "ready",
                "scene_count": len(storyboard["scenes"]),
                "ready_count": len(storyboard["scenes"]),
                "provider": "professional_voice",
            }
        ),
        encoding="utf-8",
    )
    write_original_quality_report(paths, strategy, storyboard, motion_plan, caption_timeline)
    write_originality_report(paths.originality_report)

    quality = json.loads(paths.quality_report.read_text(encoding="utf-8"))
    originality = json.loads(paths.originality_report.read_text(encoding="utf-8"))

    assert quality["status"] == "passed"
    assert quality["checks"]["original_script_present"] is True
    assert quality["checks"]["no_source_reuse"] is True
    assert quality["checks"]["dynamic_shot_plan_present"] is True
    assert quality["checks"]["caption_timeline_present"] is True
    assert quality["checks"]["scene_motion_variety"] is True
    assert quality["checks"]["voiceover_audio_present"] is True
    assert quality["checks"]["asset_pass_ready"] is True
    assert quality["checks"]["asset_visuals_embedded"] is True
    assert quality["strategy"]["publish_tier"] == "publish_candidate"
    assert originality["risk_level"] == "low"
    assert originality["metrics"]["source_reuse_ratio"] == 0.0


def test_original_quality_report_blocks_missing_voiceover_audio(tmp_path):
    paths = build_original_paths(tmp_path)
    strategy = build_original_strategy({"original_topic": "原创生成系统"})
    storyboard = build_storyboard(strategy)
    motion_plan = original.build_motion_plan(storyboard)
    caption_timeline = original.build_caption_timeline(storyboard)

    write_original_quality_report(paths, strategy, storyboard, motion_plan, caption_timeline)

    quality = json.loads(paths.quality_report.read_text(encoding="utf-8"))
    assert quality["status"] == "failed"
    assert quality["checks"]["voiceover_audio_present"] is False
    assert any(issue["code"] == "missing_voiceover_audio" for issue in quality["issues"])


def test_original_quality_report_warns_when_rendered_preview_uses_draft_assets(tmp_path):
    paths = build_original_paths(tmp_path)
    strategy = build_original_strategy({"original_topic": "原创生成系统"})
    storyboard = build_storyboard(strategy)
    motion_plan = original.build_motion_plan(storyboard)
    caption_timeline = original.build_caption_timeline(storyboard)
    paths.video.write_bytes(b"placeholder")
    paths.subtitles.write_text("1\n00:00:00,000 --> 00:00:01,000\n原创字幕\n", encoding="utf-8")
    paths.voiceover_manifest.write_text(
        json.dumps(
            {
                "status": "ready",
                "scene_count": len(storyboard["scenes"]),
                "ready_count": len(storyboard["scenes"]),
                "provider": "macos_say",
            }
        ),
        encoding="utf-8",
    )

    write_original_quality_report(paths, strategy, storyboard, motion_plan, caption_timeline, require_video=True)

    quality = json.loads(paths.quality_report.read_text(encoding="utf-8"))
    assert quality["status"] == "passed"
    assert quality["strategy"]["publish_tier"] == "preview_needs_asset_pass"
    assert any(issue["code"] == "draft_voiceover_provider" for issue in quality["issues"])
    assert any(issue["code"] == "missing_asset_pass" for issue in quality["issues"])
    assert any(issue["code"] == "generated_motion_graphics_only" for issue in quality["issues"])


def test_original_quality_report_marks_draft_voiceover_when_assets_are_ready(tmp_path):
    paths = build_original_paths(tmp_path)
    strategy = build_original_strategy({"original_topic": "原创生成系统"})
    storyboard = build_storyboard(strategy)
    motion_plan = original.build_motion_plan(storyboard)
    caption_timeline = original.build_caption_timeline(storyboard)
    paths.video.write_bytes(b"placeholder")
    paths.subtitles.write_text("1\n00:00:00,000 --> 00:00:01,000\n原创字幕\n", encoding="utf-8")
    paths.asset_pass_report.write_text(
        json.dumps({"status": "ready", "publish_ready": True, "assets_found": len(storyboard["scenes"])}),
        encoding="utf-8",
    )
    paths.asset_usage_plan.write_text(
        json.dumps(
            {
                "status": "ready",
                "scene_assets": [{"scene_index": scene["index"], "asset_path": f"asset-{scene['index']}.png"} for scene in storyboard["scenes"]],
            }
        ),
        encoding="utf-8",
    )
    paths.voiceover_manifest.write_text(
        json.dumps(
            {
                "status": "ready",
                "scene_count": len(storyboard["scenes"]),
                "ready_count": len(storyboard["scenes"]),
                "provider": "macos_say",
            }
        ),
        encoding="utf-8",
    )

    write_original_quality_report(paths, strategy, storyboard, motion_plan, caption_timeline, require_video=True)

    quality = json.loads(paths.quality_report.read_text(encoding="utf-8"))
    assert quality["status"] == "passed"
    assert quality["checks"]["asset_pass_ready"] is True
    assert quality["checks"]["asset_visuals_embedded"] is True
    assert quality["strategy"]["publish_tier"] == "preview_needs_voiceover_upgrade"
    assert any(issue["code"] == "draft_voiceover_provider" for issue in quality["issues"])
    assert not any(issue["code"] == "missing_asset_pass" for issue in quality["issues"])


def test_original_quality_report_blocks_ready_assets_without_usage_plan(tmp_path):
    paths = build_original_paths(tmp_path)
    strategy = build_original_strategy({"original_topic": "原创生成系统"})
    storyboard = build_storyboard(strategy)
    motion_plan = original.build_motion_plan(storyboard)
    caption_timeline = original.build_caption_timeline(storyboard)
    paths.video.write_bytes(b"placeholder")
    paths.subtitles.write_text("1\n00:00:00,000 --> 00:00:01,000\n原创字幕\n", encoding="utf-8")
    paths.asset_pass_report.write_text(
        json.dumps({"status": "ready", "publish_ready": True, "assets_found": len(storyboard["scenes"])}),
        encoding="utf-8",
    )
    paths.voiceover_manifest.write_text(
        json.dumps(
            {
                "status": "ready",
                "scene_count": len(storyboard["scenes"]),
                "ready_count": len(storyboard["scenes"]),
                "provider": "professional_voice",
            }
        ),
        encoding="utf-8",
    )

    write_original_quality_report(paths, strategy, storyboard, motion_plan, caption_timeline, require_video=True)

    quality = json.loads(paths.quality_report.read_text(encoding="utf-8"))
    assert quality["status"] == "failed"
    assert quality["checks"]["asset_pass_ready"] is True
    assert quality["checks"]["asset_visuals_embedded"] is False
    assert any(issue["code"] == "missing_asset_usage_plan" for issue in quality["issues"])
