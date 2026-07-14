from PIL import Image

from video_factory.motion_quality_lab import (
    HEIGHT,
    WIDTH,
    build_motion_quality_lab_storyboard,
    build_motion_scene_command,
    draw_motion_overlay,
    motion_quality_lab_duration,
)


def test_motion_quality_lab_storyboard_uses_real_video_sources():
    scenes = build_motion_quality_lab_storyboard()

    assert motion_quality_lab_duration(scenes) == 80
    assert scenes[0].source == "split"
    assert any(scene.source == "reference" for scene in scenes)
    assert any(scene.source == "failed" for scene in scenes)
    assert all(scene.duration >= 9 for scene in scenes)


def test_motion_scene_command_trims_video_and_overlays_png(tmp_path):
    scene = build_motion_quality_lab_storyboard()[1]
    command = build_motion_scene_command(scene, tmp_path / "overlay.png", tmp_path / "segment.mp4")

    assert command[:2] == ["ffmpeg", "-y"]
    assert "-ss" in command
    assert "-filter_complex" in command
    assert "overlay=0:0" in " ".join(command)
    assert "-loop" in command
    assert str(tmp_path / "segment.mp4") == command[-1]


def test_split_scene_command_uses_two_video_inputs(tmp_path):
    scene = build_motion_quality_lab_storyboard()[0]
    command = build_motion_scene_command(scene, tmp_path / "overlay.png", tmp_path / "segment.mp4")
    joined = " ".join(command)

    assert "hstack=inputs=2" in joined
    assert joined.count("-i") == 3
    assert str(tmp_path / "segment.mp4") == command[-1]


def test_motion_overlay_is_transparent_1080p_caption_layer(tmp_path):
    scene = build_motion_quality_lab_storyboard()[0]
    overlay_path = draw_motion_overlay(scene, 0, 7, tmp_path / "overlay.png")

    with Image.open(overlay_path) as image:
        assert image.mode == "RGBA"
        assert image.size == (WIDTH, HEIGHT)
        assert image.getpixel((720, 420))[3] < 80
        assert image.getpixel((120, 720))[3] > 120
