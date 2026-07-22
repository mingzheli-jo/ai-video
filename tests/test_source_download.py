import json
import subprocess
from pathlib import Path

import pytest

from video_factory.source_download import (
    SourceDownloadError,
    download_source_video,
    generate_publish_titles,
    parse_source_urls,
    supported_source_url,
)


def test_parse_source_urls_accepts_multiline_and_list_values():
    assert parse_source_urls(" https://youtu.be/a \n\nhttps://v.douyin.com/b ") == [
        "https://youtu.be/a",
        "https://v.douyin.com/b",
    ]
    assert parse_source_urls(["https://youtube.com/watch?v=1", " ", "https://www.douyin.com/video/2"]) == [
        "https://youtube.com/watch?v=1",
        "https://www.douyin.com/video/2",
    ]


def test_supported_source_url_limits_v1_to_douyin_and_youtube():
    assert supported_source_url("https://www.youtube.com/watch?v=abc") == "youtube"
    assert supported_source_url("https://youtu.be/abc") == "youtube"
    assert supported_source_url("https://v.douyin.com/abc") == "douyin"
    assert supported_source_url("https://www.douyin.com/video/123") == "douyin"
    assert supported_source_url("https://example.com/video") == ""


def test_download_source_video_uses_runner_and_writes_manifest(tmp_path):
    commands = []

    def fake_runner(command, **kwargs):
        commands.append(command)
        output_template = Path(command[command.index("-o") + 1])
        output_path = Path(str(output_template).replace("%(ext)s", "mp4"))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"video")
        info_path = output_path.with_suffix(".info.json")
        info_path.write_text(
            json.dumps({"title": "原视频：AI 视频工厂怎么做？", "width": 3840, "height": 2160}),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, stdout="Demo title\n", stderr="")

    result = download_source_video(
        "https://www.youtube.com/watch?v=abc",
        tmp_path,
        runner=fake_runner,
        executable=["yt-dlp"],
        title_context={"workflow": "reference_guided_original", "original_topic": "普通人做 AI 原创视频工厂"},
    )

    assert result.video_path == tmp_path / "source" / "source.mp4"
    assert result.report_path == tmp_path / "source_download.json"
    assert result.platform == "youtube"
    assert result.title == "原视频：AI 视频工厂怎么做？"
    assert result.recommended_publish_title == "普通人做 AI 原创视频工厂：从参考到原创成片"
    assert result.publish_title_candidates
    assert commands[0][0] == "yt-dlp"
    assert "--extractor-args" in commands[0]
    assert "youtube:player_client=android" in commands[0]
    assert "--write-info-json" in commands[0]
    joined_command = " ".join(commands[0])
    assert "height<=1080" not in joined_command
    assert "bv*+ba/b" in joined_command
    manifest = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert manifest["status"] == "ready"
    assert manifest["source_url"] == "https://www.youtube.com/watch?v=abc"
    assert manifest["source_path"].replace("\\", "/").endswith("/source/source.mp4")
    assert manifest["download_strategy"] == "youtube_android"
    assert manifest["resolution_policy"] == "best_available"
    assert manifest["source_resolution"] == {"width": 3840, "height": 2160}
    assert manifest["source_title"] == "原视频：AI 视频工厂怎么做？"
    assert manifest["recommended_publish_title"] == "普通人做 AI 原创视频工厂：从参考到原创成片"
    assert len(manifest["publish_title_candidates"]) >= 3


def test_douyin_falls_back_to_ytdlp_without_youtube_android_client(tmp_path):
    """原生无水印解析失败 → 回落 yt-dlp；yt-dlp 抖音命令不带 youtube android client。"""
    from video_factory.douyin_native import DouyinError

    commands = []

    def fake_runner(command, **kwargs):
        commands.append(command)
        output_template = Path(command[command.index("-o") + 1])
        output_path = Path(str(output_template).replace("%(ext)s", "mp4"))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"video")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    def failing_native(url, dest, progress=None):
        raise DouyinError("测试：强制回落 yt-dlp")

    download_source_video(
        "https://v.douyin.com/abc",
        tmp_path,
        runner=fake_runner,
        executable=["yt-dlp"],
        douyin_downloader=failing_native,
    )

    assert commands and "youtube:player_client=android" not in commands[0]


def test_douyin_native_no_watermark_is_primary_path(tmp_path):
    """抖音优先走原生无水印：成功时不调 yt-dlp，报告标注 douyin_native 策略。"""
    def native_ok(url, dest, progress=None):
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(b"NOWATERMARK" * 200)
        if progress:
            progress("下载无水印视频")
        return "停车场老板躺着收钱"

    def runner_must_not_run(command, **kwargs):  # yt-dlp 不该被调
        raise AssertionError("原生成功时不应回落 yt-dlp")

    result = download_source_video(
        "7.53 复制打开抖音 https://v.douyin.com/abc/ 作品",
        tmp_path,
        runner=runner_must_not_run,
        executable=["yt-dlp"],
        douyin_downloader=native_ok,
    )

    assert result.platform == "douyin"
    assert result.title == "停车场老板躺着收钱"
    assert result.video_path == tmp_path / "source" / "source.mp4"
    assert result.video_path.read_bytes().startswith(b"NOWATERMARK")
    manifest = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert manifest["status"] == "ready"
    assert manifest["download_strategy"] == "douyin_native"
    assert manifest["source_title"] == "停车场老板躺着收钱"


def test_download_source_video_reports_real_error_after_warnings(tmp_path):
    def fake_runner(command, **kwargs):
        return subprocess.CompletedProcess(
            command,
            1,
            stdout="",
            stderr=(
                "NotOpenSSLWarning: urllib3 warning\n"
                "Deprecated Feature: Support for Python version 3.9 has been deprecated.\n"
                "WARNING: [youtube] Some web client formats have been skipped.\n"
                "ERROR: unable to download video data: HTTP Error 403: Forbidden\n"
            ),
        )

    with pytest.raises(SourceDownloadError) as exc:
        download_source_video(
            "https://www.youtube.com/shorts/EmYt3tFeWI8",
            tmp_path,
            runner=fake_runner,
            executable=["yt-dlp"],
        )

    assert "403" in str(exc.value)
    assert "Forbidden" in str(exc.value)
    assert "NotOpenSSLWarning" not in str(exc.value)
    manifest = json.loads((tmp_path / "source_download.json").read_text(encoding="utf-8"))
    assert "403" in manifest["error"]


def test_download_source_video_ignores_progress_lines_as_title(tmp_path):
    def fake_runner(command, **kwargs):
        output_template = Path(command[command.index("-o") + 1])
        output_path = Path(str(output_template).replace("%(ext)s", "mp4"))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"video")
        return subprocess.CompletedProcess(
            command,
            0,
            stdout="[download] 100% of 2.89MiB\n",
            stderr="",
        )

    result = download_source_video(
        "https://www.youtube.com/watch?v=abc",
        tmp_path,
        runner=fake_runner,
        executable=["yt-dlp"],
    )

    assert result.title == "source"


def test_generate_publish_titles_rewrites_reference_guided_original_title():
    titles = generate_publish_titles(
        "原视频：AI 视频工厂怎么做？",
        platform="youtube",
        context={"workflow": "reference_guided_original", "original_topic": "普通人做 AI 原创视频工厂"},
    )

    assert titles[0] == "普通人做 AI 原创视频工厂：从参考到原创成片"
    assert "原视频：AI 视频工厂怎么做？" not in titles
    assert len(titles) >= 4


def test_generate_publish_titles_prefers_source_title_for_replicate_jobs():
    titles = generate_publish_titles(
        "Lionel Scaloni's Reaction To FIFA World Cup Final Penalty Shootout",
        platform="youtube",
        context={"workflow": "replicate", "original_topic": "Codex 原创视频资产入镜验证"},
    )

    assert "Codex" not in titles[0]
    assert "Scaloni" in titles[0] or "World Cup" in titles[0]
    assert any("Penalty" in title or "点球" in title for title in titles)


def test_download_source_video_writes_failure_manifest_for_unsupported_url(tmp_path):
    with pytest.raises(SourceDownloadError) as exc:
        download_source_video("https://example.com/video", tmp_path, executable=["yt-dlp"])

    assert "暂只支持抖音和 YouTube" in str(exc.value)
    manifest = json.loads((tmp_path / "source_download.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "error"
    assert manifest["source_url"] == "https://example.com/video"


def test_download_source_video_reports_missing_provider(tmp_path):
    with pytest.raises(SourceDownloadError) as exc:
        download_source_video("https://youtu.be/abc", tmp_path, executable=None)

    assert "yt-dlp" in str(exc.value)
    manifest = json.loads((tmp_path / "source_download.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "error"
    assert manifest["platform"] == "youtube"
