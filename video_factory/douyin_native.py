"""抖音分享链接原生解析 + 无水印下载（仅标准库 urllib，无 requests 依赖）。

从 video-upload/douyin.py 移植（2026-07-22）：yt-dlp 的通用抽取器对抖音不稳，原生
解析分享页更可靠、且能拿**无水印**版本，故作主路径、yt-dlp 兜底。

链路：分享口令里抽链接 → 定位 video_id（含 v.douyin.com 短链跟随重定向）→ 读
iesdouyin 分享页的 window._ROUTER_DATA → 取播放地址 → /playwm/→/play/ 去水印 →
流式落盘。任一步失败抛 DouyinError，调用方据此回落 yt-dlp。
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Callable
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# 抖音按 UA 分发内容：必须用移动端 UA 才能拿到分享页的 _ROUTER_DATA。
MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1"
)
SHARE_PAGE = "https://www.iesdouyin.com/share/video/{video_id}/"
_URL_RE = re.compile(r"https?://[^\s'\"<>]+")
_VIDEO_ID_RE = re.compile(r"/video/(\d+)")
_MODAL_ID_RE = re.compile(r"[?&]modal_id=(\d+)")
_ROUTER_DATA_RE = re.compile(r"window\._ROUTER_DATA\s*=\s*(\{.*?\});?</script>", re.S)

REQUEST_TIMEOUT = 30
MAX_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 2.0
_STREAM_CHUNK = 262144
_MIN_VALID_BYTES = 1024


class DouyinError(RuntimeError):
    """抖音链接无法解析或下载时抛出（调用方据此回落 yt-dlp）。"""


# URL 尾部要剥的标点：半角 + 全角/中文（口令里 URL 常紧跟「）。，」等）。
_TRAILING_PUNCT = ").,;!?]）。，、；！？】》"


def extract_first_url(raw_text: str) -> str:
    """从「7.53 复制打开抖音，看看【…】https://v.douyin.com/xxx/」这类口令里抽第一个链接。"""
    text = (raw_text or "").strip()
    if not text:
        return ""
    match = _URL_RE.search(text)
    if match:
        return match.group(0).rstrip(_TRAILING_PUNCT)
    return text


def to_no_watermark(url: str) -> str:
    """播放地址去水印：抖音 /playwm/ 是带水印版，/play/ 是无水印版。"""
    return url.replace("/playwm/", "/play/").replace("/playwm?", "/play?")


def sanitize_filename(name: str, fallback: str) -> str:
    """视频标题转安全文件名（剥非法字符、压空白、截 80 字）。"""
    cleaned = re.sub(r'[\\/:*?"<>|\r\n\t]+', " ", name).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = cleaned[:80].strip()
    return cleaned or fallback


def _headers() -> dict[str, str]:
    return {
        "User-Agent": MOBILE_UA,
        "Referer": "https://www.douyin.com/",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }


def _get(url: str, *, timeout: float = REQUEST_TIMEOUT) -> tuple[str, bytes]:
    """GET 带瞬时故障重试；返回 (最终URL, 响应体)。HTTP 明确状态码不重试。"""
    last_error: Exception | None = None
    for attempt in range(MAX_ATTEMPTS):
        if attempt:
            time.sleep(RETRY_BACKOFF_SECONDS * attempt)
        try:
            with urlopen(Request(url, headers=_headers()), timeout=timeout) as resp:
                return resp.geturl(), resp.read()
        except HTTPError as exc:  # 明确状态码：重试无意义
            raise DouyinError(f"抖音返回 HTTP {exc.code}。") from exc
        except (URLError, HTTPException, OSError) as exc:  # 瞬时故障：重试
            last_error = exc
    raise DouyinError(f"多次重试仍无法连接抖音：{last_error}") from last_error


def resolve_video_id(raw_text: str) -> str:
    """从链接/口令定位 video_id：直链正则命中；v.douyin.com 短链跟随重定向后再取。"""
    url = extract_first_url(raw_text)
    if not url:
        raise DouyinError("请填写抖音视频链接或分享口令。")
    direct = _VIDEO_ID_RE.search(url) or _MODAL_ID_RE.search(url)
    if direct:
        return direct.group(1)
    final_url, _ = _get(url)  # 短链 → 跟随重定向到含 video_id 的页面
    match = _VIDEO_ID_RE.search(final_url) or _MODAL_ID_RE.search(final_url)
    if match:
        return match.group(1)
    raise DouyinError("这个抖音链接里找不到视频 id。")


def _first_play_url(video: dict) -> str | None:
    play_addr = video.get("play_addr") or {}
    for candidate in play_addr.get("url_list") or []:
        if candidate:
            return str(candidate)
    return None


def parse_video_info(video_id: str) -> dict[str, str]:
    """读分享页 _ROUTER_DATA，取 desc（标题）与无水印播放地址。"""
    _, body = _get(SHARE_PAGE.format(video_id=video_id))
    match = _ROUTER_DATA_RE.search(body.decode("utf-8", "replace"))
    if not match:
        raise DouyinError("抖音未返回视频数据（可能私密、已删除或地区限制）。")
    try:
        data = json.loads(match.group(1))
        page = data["loaderData"]["video_(id)/page"]
        item = (page["videoInfoRes"]["item_list"] or [])[0]
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise DouyinError("抖音视频数据解析失败（页面结构可能已变）。") from exc
    play_url = _first_play_url(item.get("video") or {})
    if not play_url:
        raise DouyinError("找不到可播放的视频地址。")
    return {
        "video_id": video_id,
        "desc": str(item.get("desc") or "").strip(),
        "play_url": to_no_watermark(play_url),
    }


def download_no_watermark(
    raw_text: str,
    dest_path: Path | str,
    progress: Callable[[str], None] | None = None,
) -> str:
    """解析并流式下载无水印 MP4 到 dest_path；返回视频标题（desc）。

    失败抛 DouyinError（调用方据此回落 yt-dlp）。落盘不足 1KB 视为空文件（链接过期）。
    """
    dest_path = Path(dest_path)
    video_id = resolve_video_id(raw_text)
    info = parse_video_info(video_id)
    if progress:
        progress("下载无水印视频")
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    try:
        with urlopen(Request(info["play_url"], headers=_headers()), timeout=REQUEST_TIMEOUT) as resp, \
                open(dest_path, "wb") as fh:
            while True:
                chunk = resp.read(_STREAM_CHUNK)
                if not chunk:
                    break
                fh.write(chunk)
                total += len(chunk)
    except HTTPError as exc:
        dest_path.unlink(missing_ok=True)
        raise DouyinError(f"视频流返回 HTTP {exc.code}。") from exc
    except (URLError, HTTPException, OSError) as exc:
        dest_path.unlink(missing_ok=True)
        raise DouyinError(f"视频流下载失败：{exc}") from exc
    if total < _MIN_VALID_BYTES:
        dest_path.unlink(missing_ok=True)
        raise DouyinError("下载到的文件为空，链接可能已过期。")
    return info["desc"]
