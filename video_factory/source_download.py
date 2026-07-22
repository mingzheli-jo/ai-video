from __future__ import annotations

import sys
import importlib.util
import json
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence
from urllib.parse import urlparse


Runner = Callable[..., subprocess.CompletedProcess]
SUPPORTED_DOMAINS = {
    "youtube": ("youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"),
    "douyin": ("douyin.com", "www.douyin.com", "v.douyin.com", "iesdouyin.com", "www.iesdouyin.com"),
}


class SourceDownloadError(RuntimeError):
    pass


@dataclass(frozen=True)
class SourceDownloadResult:
    video_path: Path
    report_path: Path
    platform: str
    title: str
    recommended_publish_title: str = ""
    publish_title_candidates: tuple[str, ...] = ()


def parse_source_urls(value: object) -> list[str]:
    if isinstance(value, (list, tuple)):
        items = [str(item).strip() for item in value]
    else:
        items = [line.strip() for line in str(value or "").splitlines()]
    return [item for item in items if item]


def supported_source_url(url: str) -> str:
    parsed = urlparse(str(url).strip())
    host = parsed.netloc.lower()
    if host.startswith("www."):
        host_without_www = host[4:]
    else:
        host_without_www = host
    for platform, domains in SUPPORTED_DOMAINS.items():
        if host in domains or host_without_www in domains or any(host.endswith(f".{domain}") for domain in domains):
            return platform
    return ""


def download_source_video(
    source_url: str,
    output_dir: Path | str,
    progress: Callable[[str], None] | None = None,
    runner: Runner = subprocess.run,
    executable: Sequence[str] | None | object = ...,
    title_context: dict | None = None,
    douyin_downloader: Callable[..., str] | object = ...,
) -> SourceDownloadResult:
    output_dir = Path(output_dir)
    report_path = output_dir / "source_download.json"
    source_dir = output_dir / "source"
    source_dir.mkdir(parents=True, exist_ok=True)
    platform = supported_source_url(source_url)
    if not platform:
        # 可能是"复制打开抖音…https://v.douyin.com/x"这类分享口令（链接不在开头，
        # 整串 urlparse 拿不到域名）：抽出第一个链接再判平台（2026-07-22）。
        from video_factory.douyin_native import extract_first_url

        extracted = extract_first_url(source_url)
        if extracted and extracted != source_url:
            platform = supported_source_url(extracted)
            if platform:
                source_url = extracted
    if not platform:
        message = "暂只支持抖音和 YouTube 的公开视频链接。"
        _write_download_report(report_path, source_url, "", "error", error=message)
        raise SourceDownloadError(message)

    # 抖音优先走原生无水印解析（2026-07-22）：yt-dlp 通用抽取器对抖音不稳，且原生解析
    # 拿的是无水印版本。解析失败（页面结构变/私密/网络）再回落 yt-dlp（下方通用路径）。
    if platform == "douyin":
        native = _download_douyin_native(
            source_url, source_dir, report_path, progress, title_context, douyin_downloader
        )
        if native is not None:
            return native

    command_prefix = _resolve_downloader_executable() if executable is ... else list(executable or [])
    if not command_prefix:
        message = "未找到 yt-dlp。请先安装 yt-dlp，或改用本地视频上传。"
        _write_download_report(report_path, source_url, platform, "error", error=message)
        raise SourceDownloadError(message)

    output_template = source_dir / "source.%(ext)s"
    strategy, command = _download_command(platform, command_prefix, output_template, source_url)
    _progress(progress, "下载视频")
    completed = runner(command, check=False, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900)
    if completed.returncode != 0:
        error = _friendly_download_error(completed.stderr or completed.stdout or "下载失败。")
        _write_download_report(report_path, source_url, platform, "error", error=error, download_strategy=strategy)
        raise SourceDownloadError(error)

    video_path = _find_downloaded_video(source_dir)
    if video_path is None:
        message = "下载完成但没有找到可用视频文件。"
        _write_download_report(report_path, source_url, platform, "error", error=message, download_strategy=strategy)
        raise SourceDownloadError(message)
    target_path = source_dir / "source.mp4"
    if video_path != target_path:
        video_path.replace(target_path)
    info_path = _find_info_json(source_dir)
    title = _title_from_info_json(info_path) or _title_from_stdout(completed.stdout) or target_path.stem
    source_resolution = _resolution_from_info_json(info_path)
    return _finalize_source_download(
        report_path, source_url, platform, target_path, title, strategy,
        source_resolution, title_context, progress,
    )


def _download_douyin_native(
    source_url: str,
    source_dir: Path,
    report_path: Path,
    progress: Callable[[str], None] | None,
    title_context: dict | None,
    douyin_downloader: Callable[..., str] | object,
) -> SourceDownloadResult | None:
    """抖音无水印原生下载；成功返回结果，解析失败返回 None（调用方回落 yt-dlp）。"""
    from video_factory import douyin_native

    downloader = (
        douyin_native.download_no_watermark if douyin_downloader is ... else douyin_downloader
    )
    target_path = source_dir / "source.mp4"
    try:
        desc = downloader(source_url, target_path, progress=progress)
    except douyin_native.DouyinError as exc:
        _progress(progress, f"无水印解析失败，改用 yt-dlp 兜底：{exc}")
        return None
    title = (desc or "").strip() or target_path.stem
    return _finalize_source_download(
        report_path, source_url, "douyin", target_path, title, "douyin_native",
        "", title_context, progress,
    )


def _finalize_source_download(
    report_path: Path,
    source_url: str,
    platform: str,
    target_path: Path,
    title: str,
    strategy: str,
    source_resolution: str,
    title_context: dict | None,
    progress: Callable[[str], None] | None,
) -> SourceDownloadResult:
    """写下载报告 + 生成发布标题候选 + 构造结果（yt-dlp 与原生抖音两条路共用）。"""
    title_candidates = tuple(generate_publish_titles(title, platform=platform, context=title_context or {}))
    recommended_title = title_candidates[0] if title_candidates else ""
    _write_download_report(
        report_path,
        source_url,
        platform,
        "ready",
        source_path=target_path,
        title=title,
        download_strategy=strategy,
        resolution_policy="best_available",
        source_resolution=source_resolution,
        recommended_publish_title=recommended_title,
        publish_title_candidates=title_candidates,
    )
    _progress(progress, "下载完成")
    return SourceDownloadResult(
        video_path=target_path,
        report_path=report_path,
        platform=platform,
        title=title,
        recommended_publish_title=recommended_title,
        publish_title_candidates=title_candidates,
    )


def _download_command(
    platform: str,
    command_prefix: Sequence[str],
    output_template: Path,
    source_url: str,
) -> tuple[str, list[str]]:
    command = list(command_prefix) + [
        "--no-playlist",
        "--merge-output-format",
        "mp4",
        "--write-info-json",
    ]
    strategy = "default"
    if platform == "youtube":
        strategy = "youtube_android"
        command += ["--extractor-args", "youtube:player_client=android"]
    command += [
        "-f",
        "bv*+ba/b",
        "-o",
        str(output_template),
        str(source_url),
    ]
    return strategy, command


def _resolve_downloader_executable() -> list[str]:
    executable = shutil.which("yt-dlp")
    if executable:
        return [executable]
    if importlib.util.find_spec("yt_dlp") is not None:
        return [sys.executable, "-m", "yt_dlp"]
    return []


def _find_downloaded_video(source_dir: Path) -> Path | None:
    candidates = []
    for suffix in (".mp4", ".mkv", ".webm", ".mov", ".m4v"):
        candidates.extend(source_dir.glob(f"source*{suffix}"))
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


def _find_info_json(source_dir: Path) -> Path | None:
    candidates = list(source_dir.glob("source*.info.json"))
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


def _title_from_info_json(path: Path | None) -> str:
    if path is None or not path.exists():
        return ""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    return _clean_title(str(payload.get("title", "")))[:200]


def _resolution_from_info_json(path: Path | None) -> dict:
    if path is None or not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    width = _safe_int(payload.get("width"))
    height = _safe_int(payload.get("height"))
    if not width or not height:
        return {}
    return {"width": width, "height": height}


def _safe_int(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _title_from_stdout(stdout: str) -> str:
    lines = [line.strip() for line in str(stdout or "").splitlines() if line.strip()]
    for line in lines:
        normalized = line.lstrip("\r").strip()
        if normalized.startswith("["):
            continue
        if normalized.startswith("WARNING:") or normalized.startswith("ERROR:"):
            continue
        return normalized[:200]
    return ""


def generate_publish_titles(source_title: str, platform: str = "", context: dict | None = None) -> list[str]:
    context = context or {}
    workflow = str(context.get("workflow", "")).strip()
    topic = _clean_title(
        str(context.get("original_topic") or context.get("topic") or context.get("original_brief") or "").strip()
    )
    source_subject = _subject_from_title(source_title)
    if workflow in {"reference_guided_original", "original", "original-generate"}:
        base = _shorten_title(topic or source_subject or "这条视频", limit=24)
    else:
        base = _shorten_title(source_subject or topic or "这条视频", limit=34)
    if workflow == "reference_guided_original":
        candidates = [
            f"{base}：从参考到原创成片",
            f"用参考视频重做原创内容：{base}实操拆解",
            f"{base}怎么落地？一条视频讲清流程",
            f"从选题到发布：{base}的完整方法",
            f"{base}避坑指南：普通人也能上手",
        ]
    else:
        candidates = [
            f"{base}：关键步骤与避坑",
            f"看懂{base}：核心信息和实用结论",
            f"{base}怎么做？完整流程拆解",
            f"别只看热闹：{base}背后的关键点",
            f"一条视频讲清{base}",
        ]
    source_clean = _clean_title(source_title)
    unique: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        cleaned = _shorten_title(_clean_title(candidate), limit=42)
        if not cleaned or cleaned == source_clean or cleaned in seen:
            continue
        seen.add(cleaned)
        unique.append(cleaned)
    return unique


def _subject_from_title(title: str) -> str:
    cleaned = _clean_title(title)
    if not cleaned or cleaned == "source":
        return ""
    for separator in ("｜", "|", "-", "_", "：", ":", "，", ","):
        if separator in cleaned:
            parts = [part.strip() for part in cleaned.split(separator) if part.strip()]
            if parts:
                cleaned = max(parts, key=len)
                break
    cleaned = _compact_english_subject(cleaned)
    return _shorten_title(cleaned, limit=48)


def _compact_english_subject(value: str) -> str:
    if not re.search(r"[A-Za-z]", value):
        return value
    stop_words = {
        "reaction",
        "reacts",
        "reacting",
        "to",
        "the",
        "a",
        "an",
        "fifa",
        "final",
        "full",
        "video",
        "clip",
        "shorts",
    }
    normalized = value.replace("'s", "").replace("’s", "")
    tokens = re.findall(r"[A-Za-z0-9]+|[\u4e00-\u9fff]+", normalized)
    kept = [token for token in tokens if token.lower() not in stop_words]
    return " ".join(kept) or value


def _clean_title(value: str) -> str:
    cleaned = " ".join(str(value or "").replace("\n", " ").split())
    words = [word for word in cleaned.split(" ") if not word.startswith("#")]
    return " ".join(words).strip(" -_｜|")


def _shorten_title(value: str, limit: int = 42) -> str:
    value = _clean_title(value)
    return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"


def _friendly_download_error(raw_error: str) -> str:
    error = str(raw_error).strip()
    lower = error.lower()
    if "login" in lower or "private" in lower or "cookies" in lower:
        return "该视频需要登录、Cookie 或不是公开视频。首版只支持公开视频链接。"
    if "drm" in lower:
        return "该视频可能受 DRM 或平台限制保护，不能下载。"
    if "unsupported url" in lower:
        return "链接平台暂不支持。首版只支持抖音和 YouTube。"
    error_lines = [line.strip() for line in error.splitlines() if line.strip()]
    for line in reversed(error_lines):
        if line.startswith("ERROR:"):
            return line[:800]
    for line in reversed(error_lines):
        if "http error 403" in line.lower() or "forbidden" in line.lower():
            return line[:800]
    meaningful_lines = [
        line
        for line in error_lines
        if not line.startswith("/Users/")
        and "NotOpenSSLWarning" not in line
        and "warnings.warn" not in line
        and "Deprecated Feature" not in line
    ]
    return "\n".join(meaningful_lines[-4:])[:800] or "下载失败。"


def _write_download_report(
    report_path: Path,
    source_url: str,
    platform: str,
    status: str,
    source_path: Path | None = None,
    title: str = "",
    error: str = "",
    download_strategy: str = "",
    resolution_policy: str = "",
    source_resolution: dict | None = None,
    recommended_publish_title: str = "",
    publish_title_candidates: Sequence[str] = (),
) -> None:
    payload = {
        "version": "source_download_v1",
        "status": status,
        "provider": "yt-dlp",
        "source_url": source_url,
        "platform": platform,
        "title": title,
        "source_title": title,
        "recommended_publish_title": recommended_publish_title,
        "publish_title_candidates": list(publish_title_candidates),
        "source_path": str(source_path or ""),
        "error": error,
        "download_strategy": download_strategy,
        "resolution_policy": resolution_policy,
        "source_resolution": source_resolution or {},
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "auth_policy": "public_only",
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _progress(progress: Callable[[str], None] | None, message: str) -> None:
    if progress is not None:
        progress(message)
