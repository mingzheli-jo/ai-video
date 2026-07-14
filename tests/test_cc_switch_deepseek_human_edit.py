from video_factory.cc_switch_deepseek_human_edit import (
    DEFAULT_OUTPUT_DIR,
    SOURCE_VIDEO,
    build_human_edit_paths,
    build_human_edit_scene_command,
    build_human_edit_storyboard,
    human_edit_duration,
)


def test_human_edit_storyboard_is_six_to_eight_minute_real_edit():
    segments = build_human_edit_storyboard()

    assert 360 <= human_edit_duration(segments) <= 480
    assert len(segments) >= 8
    assert all(segment.source == "reference" for segment in segments)
    assert sum(1 for segment in segments if segment.zoom > 1.0) >= 4
    assert segments[0].start == 0


def test_human_edit_segment_command_has_no_ai_template_packaging(tmp_path):
    segment = build_human_edit_storyboard()[1]
    command = build_human_edit_scene_command(segment, tmp_path / "segment.mp4")
    joined = " ".join(command)

    assert command[:2] == ["ffmpeg", "-y"]
    assert str(SOURCE_VIDEO) in command
    assert str(tmp_path / "segment.mp4") == command[-1]
    assert "-loop" not in command
    assert "overlay" not in joined
    assert "drawtext" not in joined
    assert "progress" not in joined.lower()
    assert "crop=1920:1080" in joined
    assert "unsharp" in joined


def test_human_edit_paths_are_separate_from_original_enhanced_output():
    paths = build_human_edit_paths(DEFAULT_OUTPUT_DIR)

    assert paths.video.name == "release.mp4"
    assert paths.edl.name == "edit_decision_list.md"
    assert paths.contact_sheet.name == "contact_sheet.jpg"
    assert "human-edit" in str(paths.output_dir)
