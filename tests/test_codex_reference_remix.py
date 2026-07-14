from PIL import Image

from video_factory.codex_reference_remix import (
    HEIGHT,
    SOURCE_VIDEO,
    WIDTH,
    build_codex_reference_remix_storyboard,
    build_remix_scene_command,
    codex_reference_remix_duration,
    draw_remix_overlay,
)


def test_codex_reference_remix_storyboard_keeps_original_theme():
    scenes = build_codex_reference_remix_storyboard()

    assert codex_reference_remix_duration(scenes) == 80
    assert all(scene.source == "reference" for scene in scenes)
    assert all("足球" not in scene.title for scene in scenes)
    assert any("agent" in scene.title.lower() for scene in scenes)
    assert any("知识库" in scene.title for scene in scenes)


def test_remix_scene_command_uses_only_reference_video_and_overlay(tmp_path):
    scene = build_codex_reference_remix_storyboard()[0]
    command = build_remix_scene_command(scene, tmp_path / "overlay.png", tmp_path / "segment.mp4")
    joined = " ".join(command)

    assert command[:2] == ["ffmpeg", "-y"]
    assert str(SOURCE_VIDEO) in command
    assert joined.count("-i") == 2
    assert "overlay=0:0" in joined
    assert "scale=1920:1080" in joined
    assert str(tmp_path / "segment.mp4") == command[-1]


def test_remix_overlay_is_transparent_and_keeps_center_video_visible(tmp_path):
    scene = build_codex_reference_remix_storyboard()[0]
    overlay_path = draw_remix_overlay(scene, 0, 7, tmp_path / "overlay.png")

    with Image.open(overlay_path) as image:
        assert image.mode == "RGBA"
        assert image.size == (WIDTH, HEIGHT)
        assert image.getpixel((960, 420))[3] < 80
        assert image.getpixel((92, 96))[3] > 120
        assert image.getpixel((120, 920))[3] > 120
