from video_factory.cc_switch_deepseek_original_enhanced import (
    DEFAULT_OUTPUT_DIR,
    SOURCE_VIDEO,
    build_original_enhance_command,
    build_original_enhance_paths,
)


def test_original_enhance_uses_full_source_without_visible_ai_packaging(tmp_path):
    output = tmp_path / "release.mp4"
    command = build_original_enhance_command(output)
    joined = " ".join(command)

    assert command[:2] == ["ffmpeg", "-y"]
    assert str(SOURCE_VIDEO) in command
    assert str(output) == command[-1]
    assert "-loop" not in command
    assert "overlay" not in joined
    assert "drawtext" not in joined
    assert "progress" not in joined.lower()
    assert "loudnorm" in joined
    assert "unsharp" in joined
    assert "-t" not in command


def test_original_enhance_paths_are_isolated_from_remix_outputs():
    paths = build_original_enhance_paths(DEFAULT_OUTPUT_DIR)

    assert paths.video.name == "release.mp4"
    assert paths.cover.name == "cover.png"
    assert paths.contact_sheet.name == "contact_sheet.jpg"
    assert paths.report.name == "render_report.json"
    assert "original-enhanced" in str(paths.output_dir)
