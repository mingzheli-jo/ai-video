import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from video_factory.analysis import (
    AnalysisPaths,
    MediaInfo,
    SampleFrame,
    analyze_reference_video,
    build_contact_sheet_command,
    build_output_slug,
    build_production_template_markdown,
    build_quality_report_markdown,
    build_scorecard_markdown,
    build_sample_frame_command,
    build_timeline_markdown,
    choose_sample_timestamps,
    probe_media,
    render_contact_sheet,
    render_sample_frames,
    write_report_artifacts,
)
from video_factory.analysis.__main__ import main, parse_args


def test_media_info_dataclass_exposes_core_video_facts():
    media = MediaInfo(
        source_path=Path("/tmp/reference.mp4"),
        duration=355.11,
        width=3840,
        height=2160,
        fps=60.0,
        video_codec="h264",
        audio_codec="aac",
        audio_sample_rate=48000,
        bit_rate=13277000,
    )

    assert media.aspect_ratio == "16:9"
    assert media.orientation == "landscape"
    assert media.to_json_dict()["duration"] == 355.11
    assert media.to_json_dict()["width"] == 3840


def test_analysis_paths_collects_expected_outputs(tmp_path):
    paths = AnalysisPaths.for_output_dir(tmp_path)

    assert paths.output_dir == tmp_path
    assert paths.media_info == tmp_path / "media_info.json"
    assert paths.contact_sheet == tmp_path / "contact_sheet.jpg"
    assert paths.sample_frames_dir == tmp_path / "sample_frames"
    assert paths.timeline == tmp_path / "timeline.md"
    assert paths.quality_report == tmp_path / "quality_report.md"
    assert paths.production_template == tmp_path / "production_template.md"
    assert paths.scorecard == tmp_path / "scorecard.md"


def test_sample_frame_uses_stable_filename():
    frame = SampleFrame(timestamp=12.5, path=Path("sample_frames/frame_012_50.jpg"))

    assert frame.label == "00:12.50"


def test_build_output_slug_keeps_ascii_and_chinese_safe():
    assert build_output_slug(Path("/tmp/My Video!.mp4")) == "my-video"
    assert build_output_slug(Path("/tmp/下载.mp4")) == "video"
    assert build_output_slug(Path("/tmp/!!!.mp4")) == "video"


def test_choose_sample_timestamps_covers_start_middle_and_end():
    timestamps = choose_sample_timestamps(duration=100.0, count=5)

    assert timestamps == [3.0, 26.5, 50.0, 73.5, 97.0]


def test_choose_sample_timestamps_handles_short_video():
    timestamps = choose_sample_timestamps(duration=12.0, count=5)

    assert timestamps == [1.0, 3.5, 6.0, 8.5, 11.0]


@pytest.mark.parametrize("count", [0, -1])
def test_choose_sample_timestamps_returns_empty_when_count_is_not_positive(count):
    assert choose_sample_timestamps(duration=100.0, count=count) == []


def test_choose_sample_timestamps_uses_midpoint_when_count_is_one():
    assert choose_sample_timestamps(duration=12.0, count=1) == [6.0]


@pytest.mark.parametrize("duration", [0.0, 2.0])
def test_choose_sample_timestamps_uses_start_for_very_short_video(duration):
    assert choose_sample_timestamps(duration=duration, count=5) == [0.0]


def test_build_contact_sheet_command_uses_even_sampling(tmp_path):
    source = tmp_path / "reference.mp4"
    output = tmp_path / "contact_sheet.jpg"

    command = build_contact_sheet_command(source, output, duration=240.0)

    joined = " ".join(command)
    assert command[:3] == ["ffmpeg", "-hide_banner", "-y"]
    assert str(source) in command
    assert str(output) == command[-1]
    assert "fps=1/30" in joined
    assert "tile=4x2" in joined


def test_build_sample_frame_command_seeks_to_timestamp(tmp_path):
    source = tmp_path / "reference.mp4"
    output = tmp_path / "frame.jpg"

    command = build_sample_frame_command(source, output, timestamp=12.5)

    assert command[:3] == ["ffmpeg", "-hide_banner", "-y"]
    assert "-ss" in command
    assert "12.500" in command
    assert str(output) == command[-1]


def _media(source_path: Path, duration: float) -> MediaInfo:
    return MediaInfo(
        source_path=source_path,
        duration=duration,
        width=1920,
        height=1080,
        fps=30.0,
        video_codec="h264",
        audio_codec="aac",
        audio_sample_rate=48000,
        bit_rate=1000000,
    )


def test_render_contact_sheet_creates_parent_dir_and_runs_checked_command(
    monkeypatch, tmp_path
):
    media = _media(tmp_path / "reference.mp4", duration=240.0)
    output = tmp_path / "nested" / "contact_sheet.jpg"
    calls = []

    def fake_run(command, check):
        calls.append((command, check))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr("video_factory.analysis.frames.subprocess.run", fake_run)

    render_contact_sheet(media, output)

    assert output.parent.exists()
    assert len(calls) == 1
    command, check = calls[0]
    assert check is True
    assert command[-1] == str(output)


def test_render_sample_frames_creates_unique_paths_for_repeated_timestamps(
    monkeypatch, tmp_path
):
    media = _media(tmp_path / "short.mp4", duration=3.0)
    output_dir = tmp_path / "sample_frames"
    calls = []

    def fake_run(command, check):
        calls.append((command, check))
        Path(command[-1]).write_bytes(b"frame")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr("video_factory.analysis.frames.subprocess.run", fake_run)

    frames = render_sample_frames(media, output_dir, count=20)

    assert output_dir.exists()
    assert len(frames) == 20
    assert len({frame.path for frame in frames}) == 20
    assert len(calls) == 20
    for frame, (command, check) in zip(frames, calls):
        assert check is True
        assert command[-1] == str(frame.path)


def _probe_with_payload(monkeypatch, source: Path, payload: dict) -> MediaInfo:
    source.write_bytes(b"not a real video")

    def fake_run(command, capture_output, text, check, **kwargs):
        assert command[:2] == ["ffprobe", "-v"]
        assert capture_output is True
        assert text is True
        assert check is True
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr("video_factory.analysis.media.subprocess.run", fake_run)

    return probe_media(source)


def test_probe_media_parses_ffprobe_json(monkeypatch, tmp_path):
    source = tmp_path / "reference.mp4"
    payload = {
        "format": {"duration": "355.114", "bit_rate": "13277000"},
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "h264",
                "width": 3840,
                "height": 2160,
                "r_frame_rate": "60/1",
            },
            {
                "codec_type": "audio",
                "codec_name": "aac",
                "sample_rate": "48000",
            },
        ],
    }

    media = _probe_with_payload(monkeypatch, source, payload)

    assert media.duration == 355.114
    assert media.width == 3840
    assert media.height == 2160
    assert media.fps == 60.0
    assert media.video_codec == "h264"
    assert media.audio_codec == "aac"
    assert media.audio_sample_rate == 48000
    assert media.bit_rate == 13277000


def test_probe_media_defaults_audio_fields_when_no_audio_stream(monkeypatch, tmp_path):
    payload = {
        "format": {"duration": "10.0"},
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "h264",
                "width": 1920,
                "height": 1080,
                "r_frame_rate": "30/1",
            },
        ],
    }

    media = _probe_with_payload(monkeypatch, tmp_path / "reference.mp4", payload)

    assert media.audio_codec == ""
    assert media.audio_sample_rate == 0


@pytest.mark.parametrize(
    "streams",
    [
        [{"codec_type": "audio", "codec_name": "aac", "sample_rate": "48000"}],
        [{"codec_type": "video"}],
    ],
    ids=["no-video-stream", "missing-video-fields"],
)
def test_probe_media_defaults_video_fields_when_video_data_is_missing(
    monkeypatch, tmp_path, streams
):
    payload = {"format": {"duration": "10.0"}, "streams": streams}

    media = _probe_with_payload(monkeypatch, tmp_path / "reference.mp4", payload)

    assert media.width == 0
    assert media.height == 0
    assert media.fps == 0.0
    assert media.video_codec == ""


def test_probe_media_zero_division_frame_rate_defaults_to_zero(monkeypatch, tmp_path):
    payload = {
        "format": {"duration": "10.0"},
        "streams": [{"codec_type": "video", "r_frame_rate": "0/0"}],
    }

    media = _probe_with_payload(monkeypatch, tmp_path / "reference.mp4", payload)

    assert media.fps == 0.0


def test_probe_media_decimal_frame_rate_parses_as_float(monkeypatch, tmp_path):
    payload = {
        "format": {"duration": "10.0"},
        "streams": [{"codec_type": "video", "r_frame_rate": "29.97"}],
    }

    media = _probe_with_payload(monkeypatch, tmp_path / "reference.mp4", payload)

    assert media.fps == 29.97


def test_probe_media_propagates_ffprobe_failure(monkeypatch, tmp_path):
    source = tmp_path / "reference.mp4"
    source.write_bytes(b"not a real video")

    def fake_run(command, capture_output, text, check, **kwargs):
        raise subprocess.CalledProcessError(returncode=1, cmd=command, stderr="boom")

    monkeypatch.setattr("video_factory.analysis.media.subprocess.run", fake_run)

    with pytest.raises(subprocess.CalledProcessError):
        probe_media(source)


def test_probe_media_requires_existing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        probe_media(tmp_path / "missing.mp4")


def _test_media(tmp_path):
    return MediaInfo(
        source_path=tmp_path / "reference.mp4",
        duration=120.0,
        width=1920,
        height=1080,
        fps=30.0,
        video_codec="h264",
        audio_codec="aac",
        audio_sample_rate=48000,
        bit_rate=5000000,
    )


def test_build_timeline_markdown_lists_review_frames(tmp_path):
    media = _test_media(tmp_path)
    frames = [
        SampleFrame(timestamp=3.0, path=tmp_path / "sample_frames/frame_003_00.jpg"),
        SampleFrame(timestamp=60.0, path=tmp_path / "sample_frames/frame_060_00.jpg"),
    ]

    markdown = build_timeline_markdown(media, frames)

    assert "# Reference Video Timeline Seed" in markdown
    assert "| 00:03.00 |" in markdown
    assert "sample_frames/frame_003_00.jpg" in markdown
    assert "Segment function" in markdown


def test_build_timeline_markdown_uses_safe_relative_frame_paths(tmp_path):
    media = _media(tmp_path / "source" / "reference.mp4", duration=120.0)
    frames = [
        SampleFrame(
            timestamp=3.0,
            path=tmp_path / "analysis" / "sample frames" / "frame|003`bad.jpg",
        )
    ]

    markdown = build_timeline_markdown(
        media, frames, base_dir=tmp_path / "analysis"
    )

    assert "sample frames/" in markdown
    assert str(tmp_path) not in markdown
    assert "`sample frames/frame\\|003'bad.jpg`" in markdown


def test_quality_report_contains_required_analysis_sections(tmp_path):
    media = _test_media(tmp_path)
    markdown = build_quality_report_markdown(media)

    required = [
        "基础信息",
        "时间线拆解",
        "镜头与画面系统",
        "字幕系统",
        "声音与口播",
        "剪辑节奏",
        "真实感来源",
        "可复刻规则",
        "失败样片对照",
    ]
    for heading in required:
        assert heading in markdown
    assert "本报告需要结合抽帧由 Codex 进行专家判断" in markdown


def test_template_and_scorecard_have_actionable_sections(tmp_path):
    media = _test_media(tmp_path)
    template = build_production_template_markdown(media)
    scorecard = build_scorecard_markdown(media)

    assert "开头结构规则" in template
    assert "素材规则" in template
    assert "口播规则" in template
    assert "| 维度 | 满分 | 不合格表现 |" in scorecard
    assert "语义一致性" in scorecard


def test_write_report_artifacts_creates_markdown_files(tmp_path):
    media = _test_media(tmp_path)
    frames = [SampleFrame(timestamp=3.0, path=tmp_path / "sample_frames/frame_003_00.jpg")]
    paths = AnalysisPaths.for_output_dir(tmp_path)

    write_report_artifacts(media, frames, paths)

    timeline = paths.timeline.read_text(encoding="utf-8")
    quality_report = paths.quality_report.read_text(encoding="utf-8")
    production_template = paths.production_template.read_text(encoding="utf-8")
    scorecard = paths.scorecard.read_text(encoding="utf-8")

    assert "# Reference Video Timeline Seed" in timeline
    assert "| 00:03.00 |" in timeline
    assert "sample_frames/frame_003_00.jpg" in timeline
    assert "本报告需要结合抽帧由 Codex 进行专家判断" in quality_report
    assert "# Production Template Draft" in production_template
    assert "# Reference-Derived Quality Scorecard" in scorecard
    assert "总分说明：低于 80 分不得进入发布候选。" in scorecard


def test_write_report_artifacts_preserves_existing_expert_reports_by_default(tmp_path):
    media = _test_media(tmp_path)
    frames = [SampleFrame(timestamp=3.0, path=tmp_path / "sample_frames/frame_003_00.jpg")]
    paths = AnalysisPaths.for_output_dir(tmp_path)
    paths.output_dir.mkdir(parents=True, exist_ok=True)
    paths.quality_report.write_text("manual expert report\n", encoding="utf-8")

    write_report_artifacts(media, frames, paths)

    assert paths.quality_report.read_text(encoding="utf-8") == "manual expert report\n"
    assert paths.timeline.exists()


def test_write_report_artifacts_can_overwrite_existing_reports_when_requested(tmp_path):
    media = _test_media(tmp_path)
    frames = [SampleFrame(timestamp=3.0, path=tmp_path / "sample_frames/frame_003_00.jpg")]
    paths = AnalysisPaths.for_output_dir(tmp_path)
    paths.output_dir.mkdir(parents=True, exist_ok=True)
    paths.quality_report.write_text("manual expert report\n", encoding="utf-8")

    write_report_artifacts(media, frames, paths, overwrite=True)

    quality_report = paths.quality_report.read_text(encoding="utf-8")
    assert "# Reference Video Quality Report" in quality_report
    assert "manual expert report" not in quality_report


def test_analyze_reference_video_writes_expected_artifacts(monkeypatch, tmp_path):
    source = tmp_path / "参考视频.mp4"
    source.write_bytes(b"not a real video")
    output = tmp_path / "analysis"
    media = MediaInfo(
        source_path=source,
        duration=60.12345,
        width=1920,
        height=1080,
        fps=29.97003,
        video_codec="h264",
        audio_codec="aac",
        audio_sample_rate=48000,
        bit_rate=5000000,
    )
    calls = []

    def fake_probe(path):
        calls.append(("probe", path))
        return media

    def fake_contact_sheet(probed_media, output_path):
        calls.append(("contact", output_path))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"jpg")

    def fake_frames(probed_media, output_dir, count):
        calls.append(("frames", output_dir, count))
        output_dir.mkdir(parents=True, exist_ok=True)
        frame = output_dir / "frame_003_00.jpg"
        frame.write_bytes(b"jpg")
        return [SampleFrame(timestamp=3.0, path=frame)]

    def fake_reports(probed_media, frames, paths, overwrite=False):
        calls.append(("reports", overwrite))
        paths.quality_report.write_text("report\n", encoding="utf-8")

    monkeypatch.setattr("video_factory.analysis.runner.probe_media", fake_probe)
    monkeypatch.setattr(
        "video_factory.analysis.runner.render_contact_sheet", fake_contact_sheet
    )
    monkeypatch.setattr("video_factory.analysis.runner.render_sample_frames", fake_frames)
    monkeypatch.setattr("video_factory.analysis.runner.write_report_artifacts", fake_reports)

    result = analyze_reference_video(
        source,
        output_dir=output,
        sample_count=1,
        overwrite_reports=True,
    )

    assert result.media == media
    assert result.paths.media_info.exists()
    media_info = json.loads(result.paths.media_info.read_bytes().decode("utf-8"))
    assert media_info["duration"] == 60.123
    assert media_info["fps"] == 29.97
    assert "参考视频.mp4" in media_info["source_path"]
    assert media_info["orientation"] == "landscape"
    assert media_info["aspect_ratio"] == "16:9"
    assert result.paths.contact_sheet.exists()
    assert result.paths.quality_report.exists()
    assert result.sample_frames[0].timestamp == 3.0
    assert calls == [
        ("probe", source),
        ("contact", output / "contact_sheet.jpg"),
        ("frames", output / "sample_frames", 1),
        ("reports", True),
    ]


def test_analyze_reference_video_uses_default_slugged_output_dir(
    monkeypatch, tmp_path
):
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "Reference Video!.mp4"
    source.write_bytes(b"not a real video")
    media = MediaInfo(
        source_path=source,
        duration=12.0,
        width=1920,
        height=1080,
        fps=30.0,
        video_codec="h264",
        audio_codec="aac",
        audio_sample_rate=48000,
        bit_rate=5000000,
    )

    def fake_probe(path):
        return media

    def fake_contact_sheet(probed_media, output_path):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"jpg")

    def fake_frames(probed_media, output_dir, count):
        output_dir.mkdir(parents=True, exist_ok=True)
        frame = output_dir / "frame_006_00.jpg"
        frame.write_bytes(b"jpg")
        return [SampleFrame(timestamp=6.0, path=frame)]

    monkeypatch.setattr("video_factory.analysis.runner.probe_media", fake_probe)
    monkeypatch.setattr(
        "video_factory.analysis.runner.render_contact_sheet", fake_contact_sheet
    )
    monkeypatch.setattr("video_factory.analysis.runner.render_sample_frames", fake_frames)

    result = analyze_reference_video(source, sample_count=1)

    assert result.paths.output_dir == Path(
        "video_factory/output/analysis/reference-video"
    )
    assert result.paths.media_info.exists()
    assert result.paths.contact_sheet == Path(
        "video_factory/output/analysis/reference-video/contact_sheet.jpg"
    )
    assert result.paths.sample_frames_dir == Path(
        "video_factory/output/analysis/reference-video/sample_frames"
    )
    assert result.paths.quality_report.exists()


def test_analysis_cli_parse_args(tmp_path):
    source = tmp_path / "reference.mp4"
    output = tmp_path / "out"

    args = parse_args(
        ["--input", str(source), "--output", str(output), "--sample-count", "4"]
    )

    assert args.input == source
    assert args.output == output
    assert args.sample_count == 4
    assert args.overwrite_reports is False

    overwrite_args = parse_args(
        [
            "--input",
            str(source),
            "--output",
            str(output),
            "--sample-count",
            "4",
            "--overwrite-reports",
        ]
    )

    assert overwrite_args.overwrite_reports is True


def test_analysis_cli_main_calls_runner_and_prints_paths(monkeypatch, capsys, tmp_path):
    source = tmp_path / "reference.mp4"
    output = tmp_path / "out"
    calls = []

    def fake_analyze_reference_video(
        source_path,
        output_dir=None,
        sample_count=8,
        overwrite_reports=False,
    ):
        calls.append((source_path, output_dir, sample_count, overwrite_reports))
        return SimpleNamespace(paths=AnalysisPaths.for_output_dir(output))

    monkeypatch.setattr(
        "video_factory.analysis.__main__.analyze_reference_video",
        fake_analyze_reference_video,
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "video_factory.analysis",
            "--input",
            str(source),
            "--output",
            str(output),
            "--sample-count",
            "4",
            "--overwrite-reports",
        ],
    )

    main()

    assert calls == [(source, output, 4, True)]
    stdout = capsys.readouterr().out
    expected_paths = AnalysisPaths.for_output_dir(output)
    assert "media_info:" in stdout
    assert str(expected_paths.media_info) in stdout
    assert "contact_sheet:" in stdout
    assert str(expected_paths.contact_sheet) in stdout
    assert "timeline:" in stdout
    assert str(expected_paths.timeline) in stdout
    assert "quality_report:" in stdout
    assert str(expected_paths.quality_report) in stdout
    assert "production_template:" in stdout
    assert str(expected_paths.production_template) in stdout
    assert "scorecard:" in stdout
    assert str(expected_paths.scorecard) in stdout
