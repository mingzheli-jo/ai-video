from PIL import Image

from video_factory.cc_switch_deepseek_remix import (
    HEIGHT,
    SOURCE_VIDEO,
    WIDTH,
    build_cc_switch_deepseek_scene_command,
    build_cc_switch_deepseek_storyboard,
    cc_switch_deepseek_duration,
    draw_cc_switch_deepseek_overlay,
)


def test_cc_switch_deepseek_storyboard_matches_same_topic_contract():
    scenes = build_cc_switch_deepseek_storyboard()
    combined_text = " ".join(scene.title + " " + scene.body for scene in scenes)

    assert cc_switch_deepseek_duration(scenes) == 90
    assert len(scenes) == 8
    assert all(scene.source == "reference" for scene in scenes)
    assert "CC Switch" in combined_text
    assert "Codex" in combined_text
    assert "DeepSeek" in combined_text
    assert "足球" not in combined_text


def test_cc_switch_deepseek_scene_command_uses_real_video_clip(tmp_path):
    scene = build_cc_switch_deepseek_storyboard()[0]
    command = build_cc_switch_deepseek_scene_command(
        scene,
        tmp_path / "overlay.png",
        tmp_path / "segment.mp4",
    )
    joined = " ".join(command)

    assert command[:2] == ["ffmpeg", "-y"]
    assert str(SOURCE_VIDEO) in command
    assert joined.count("-i") == 2
    assert "overlay=0:0" in joined
    assert "unsharp" in joined
    assert str(tmp_path / "segment.mp4") == command[-1]


def test_cc_switch_deepseek_overlay_keeps_center_visible(tmp_path):
    scene = build_cc_switch_deepseek_storyboard()[0]
    overlay_path = draw_cc_switch_deepseek_overlay(scene, 0, 8, tmp_path / "overlay.png")

    with Image.open(overlay_path) as image:
        assert image.mode == "RGBA"
        assert image.size == (WIDTH, HEIGHT)
        assert image.getpixel((960, 430))[3] < 80
        assert image.getpixel((96, 84))[3] > 120
        assert image.getpixel((128, 902))[3] > 120
