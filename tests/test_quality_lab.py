import wave
from pathlib import Path

from PIL import Image

from video_factory.quality_lab import (
    HEIGHT,
    WIDTH,
    QualityLabEvidence,
    build_quality_lab_ffmpeg_command,
    build_quality_lab_storyboard,
    build_quality_lab_tone_track,
    quality_lab_duration,
    render_quality_lab_frames,
)


def test_quality_lab_storyboard_matches_sample_contract():
    scenes = build_quality_lab_storyboard()

    assert quality_lab_duration(scenes) == 78
    assert scenes[0].key == "hook"
    assert scenes[-1].key == "close"
    assert {scene.evidence for scene in scenes} >= {"split", "failed", "scorecard", "workflow", "final"}
    assert all(scene.duration > 0 for scene in scenes)
    assert all(scene.bullets for scene in scenes)


def test_quality_lab_ffmpeg_command_targets_horizontal_sample(tmp_path):
    command = build_quality_lab_ffmpeg_command(
        tmp_path / "frames.txt",
        tmp_path / "voiceover.wav",
        tmp_path / "release.mp4",
        duration=78,
    )

    assert command[0] == "ffmpeg"
    assert "scale=1920:1080" in command
    assert "-t" in command
    assert "78" in command
    assert str(tmp_path / "release.mp4") == command[-1]


def test_quality_lab_renderer_creates_nonblank_1080p_frame(tmp_path):
    reference_contact = _write_color_image(tmp_path / "reference.jpg", "#1f7a55")
    failed_contact = _write_color_image(tmp_path / "failed.jpg", "#7a1f2a")
    reference_frame = _write_color_image(tmp_path / "frame.jpg", "#274d7a")
    evidence = QualityLabEvidence(
        reference_contact_sheet=reference_contact,
        failed_contact_sheet=failed_contact,
        reference_frames=[reference_frame],
    )

    frames = render_quality_lab_frames(build_quality_lab_storyboard()[:1], tmp_path / "frames", evidence)

    assert len(frames) == 1
    with Image.open(frames[0]) as image:
        assert image.size == (WIDTH, HEIGHT)
        assert image.getpixel((120, 170)) != image.getpixel((1780, 560))


def test_quality_lab_tone_track_matches_storyboard_duration(tmp_path):
    scenes = build_quality_lab_storyboard()
    audio_path = build_quality_lab_tone_track(scenes, tmp_path / "voiceover.wav")

    with wave.open(str(audio_path), "rb") as wav_file:
        duration = wav_file.getnframes() / wav_file.getframerate()

    assert int(duration) == quality_lab_duration(scenes)
    assert audio_path.stat().st_size > 1000


def _write_color_image(path: Path, color: str) -> Path:
    image = Image.new("RGB", (1280, 720), color)
    image.save(path)
    return path
