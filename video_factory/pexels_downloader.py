"""Pexels 视频素材下载器。

从 Pexels 视频 API 按类型批量拉取免费素材，供二次重组使用。
仅依赖标准库 urllib；凭据只从环境变量 PEXELS_API_KEY 读取。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


SEARCH_URL = "https://api.pexels.com/videos/search"
MAX_PER_PAGE = 80
CHUNK_SIZE = 1024 * 1024
DEFAULT_TIMEOUT = 60
# Pexels 前置 Cloudflare 会拦截默认的 Python-urllib UA（返回 403 error code 1010），
# 必须带一个正常的浏览器 UA 才放行。搜索与下载请求都要带。
_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) KingAIVideo/1.0"
DEFAULT_COUNT = 20
DEFAULT_OUT_DIR = "素材库"
MAX_PREFERRED_HEIGHT = 1080

# 10 个中文类型 → 英文搜索词（Pexels 搜英文效果更好，多词轮询取更丰富的素材）。
CATEGORY_QUERIES: dict[str, tuple[str, ...]] = {
    "科技": ("technology", "computer", "artificial intelligence", "data center"),
    "历史": ("history", "ancient ruins", "castle", "museum"),
    "科普": ("science", "laboratory", "space", "microscope"),
    "人文": ("people culture", "portrait", "crowd", "street life"),
    "职场": ("business meeting", "office work", "teamwork", "handshake"),
    "自然": ("nature landscape", "forest", "ocean", "mountains"),
    "城市": ("city skyline", "urban street", "traffic", "night city"),
    "财经": ("finance", "stock market", "money", "business chart"),
    "美食": ("food cooking", "restaurant", "coffee", "fresh ingredients"),
    "健康": ("fitness workout", "yoga", "running", "healthy lifestyle"),
}

Opener = Callable[..., object]
ProgressFn = Callable[[str, int, int], None]


class PexelsError(RuntimeError):
    pass


@dataclass(frozen=True)
class PexelsVideo:
    id: int
    width: int
    height: int
    duration: float
    download_url: str


def _pick_best_file(video_files: list[dict]) -> dict | None:
    """按分辨率选体积适中的 mp4：取高度<=1080 中最高的一档；全部超 1080 时取最矮的
    （避开 4K 巨文件）。**不依赖 quality 字段**——Pexels 新数据里 quality 常为 None，
    只按分辨率判断才稳。无有效高度时退回第一个 mp4。"""
    mp4_files = [f for f in video_files if str(f.get("file_type")) == "video/mp4" and f.get("link")]
    if not mp4_files:
        return None
    with_height = [(f, _safe_int(f.get("height"))) for f in mp4_files]
    with_height = [(f, h) for f, h in with_height if h > 0]
    if not with_height:
        return mp4_files[0]
    within = [(f, h) for f, h in with_height if h <= MAX_PREFERRED_HEIGHT]
    if within:
        return max(within, key=lambda fh: fh[1])[0]  # <=1080 中最高清
    return min(with_height, key=lambda fh: fh[1])[0]  # 全超 1080 → 取最矮避免 4K


def _safe_int(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _safe_float(value: object) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _parse_video(raw: dict) -> PexelsVideo | None:
    best = _pick_best_file(list(raw.get("video_files") or []))
    if best is None:
        return None
    return PexelsVideo(
        id=_safe_int(raw.get("id")),
        width=_safe_int(raw.get("width")),
        height=_safe_int(raw.get("height")),
        duration=_safe_float(raw.get("duration")),
        download_url=str(best.get("link")),
    )


def _search_page(query: str, per_page: int, page: int, api_key: str, orientation: str, opener: Opener) -> dict:
    from urllib.parse import urlencode

    params = urlencode({"query": query, "per_page": per_page, "page": page, "orientation": orientation})
    request = Request(
        f"{SEARCH_URL}?{params}",
        headers={"Authorization": api_key, "User-Agent": _USER_AGENT},
    )
    try:
        with opener(request, timeout=DEFAULT_TIMEOUT) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        _raise_for_http_error(exc)
    except URLError as exc:
        raise PexelsError(f"连接 Pexels 失败：{exc.reason}。请检查网络或代理设置。") from exc
    except json.JSONDecodeError as exc:
        raise PexelsError("Pexels 返回了非 JSON 响应。") from exc


def _raise_for_http_error(exc: HTTPError) -> None:
    if exc.code in (401, 403):
        raise PexelsError("PEXELS_API_KEY 无效或未授权。") from exc
    if exc.code == 429:
        raise PexelsError("触发 Pexels 限流，请稍后再试。") from exc
    raise PexelsError(f"Pexels 请求失败：HTTP {exc.code} {exc.reason}") from exc


def search_videos(
    query: str,
    count: int,
    api_key: str,
    orientation: str = "landscape",
    opener: Opener = urlopen,
) -> list[PexelsVideo]:
    """分页搜索，直到凑够 count 个可下载视频。"""
    per_page = min(MAX_PER_PAGE, max(1, count))
    videos: list[PexelsVideo] = []
    page = 1
    while len(videos) < count:
        payload = _search_page(query, per_page, page, api_key, orientation, opener)
        raw_videos = list(payload.get("videos") or [])
        if not raw_videos:
            break
        for raw in raw_videos:
            parsed = _parse_video(raw)
            if parsed is not None:
                videos.append(parsed)
            if len(videos) >= count:
                break
        if len(raw_videos) < per_page:
            break
        page += 1
    return videos[:count]


def download_video(video: PexelsVideo, dest_path: Path | str, opener: Opener = urlopen) -> Path:
    """流式下载到 dest_path；已存在同名非空文件则跳过（幂等）。"""
    dest_path = Path(dest_path)
    if dest_path.exists() and dest_path.stat().st_size > 0:
        return dest_path
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    part_path = dest_path.with_suffix(dest_path.suffix + ".part")
    request = Request(video.download_url, headers={"User-Agent": _USER_AGENT})
    try:
        with opener(request, timeout=DEFAULT_TIMEOUT) as response, part_path.open("wb") as handle:
            while True:
                chunk = response.read(CHUNK_SIZE)
                if not chunk:
                    break
                handle.write(chunk)
    except (HTTPError, URLError, OSError) as exc:
        _cleanup_part(part_path)
        raise PexelsError(f"下载视频失败（id={video.id}）：{exc}") from exc
    part_path.replace(dest_path)
    return dest_path


def _cleanup_part(part_path: Path) -> None:
    try:
        if part_path.exists():
            part_path.unlink()
    except OSError:
        pass


def download_category(
    category: str,
    count: int,
    out_dir: Path | str,
    api_key: str,
    orientation: str = "landscape",
    opener: Opener = urlopen,
    on_progress: ProgressFn | None = None,
) -> dict:
    """按类型的多个搜索词合并去重，逐个下载，单个失败不中断整类。"""
    if category not in CATEGORY_QUERIES:
        raise PexelsError(f"未知类型「{category}」。可选：{'、'.join(CATEGORY_QUERIES)}")
    out_dir = Path(out_dir)
    videos = _collect_videos(category, count, api_key, orientation, opener)
    downloaded = 0
    skipped = 0
    failed: list[dict] = []
    for index, video in enumerate(videos, start=1):
        if on_progress is not None:
            on_progress(category, index, len(videos))
        dest = out_dir / f"pexels_{video.id}.mp4"
        already = dest.exists() and dest.stat().st_size > 0
        try:
            download_video(video, dest, opener=opener)
        except PexelsError as exc:
            failed.append({"id": video.id, "error": str(exc)})
            continue
        if already:
            skipped += 1
        else:
            downloaded += 1
    return {
        "category": category,
        "requested": count,
        "downloaded": downloaded,
        "skipped": skipped,
        "failed": failed,
    }


def _collect_videos(
    category: str,
    count: int,
    api_key: str,
    orientation: str,
    opener: Opener,
) -> list[PexelsVideo]:
    collected: list[PexelsVideo] = []
    seen_ids: set[int] = set()
    for query in CATEGORY_QUERIES[category]:
        if len(collected) >= count:
            break
        for video in search_videos(query, count, api_key, orientation=orientation, opener=opener):
            if video.id in seen_ids:
                continue
            seen_ids.add(video.id)
            collected.append(video)
            if len(collected) >= count:
                break
    return collected


def download_all(
    base_dir: Path | str,
    count_per_category: int,
    api_key: str,
    categories: list[str] | None = None,
    orientation: str = "landscape",
    on_progress: ProgressFn | None = None,
    opener: Opener = urlopen,
) -> list[dict]:
    """对每个类型建 base_dir/<类型> 目录并 download_category，返回逐类结果。"""
    base_dir = Path(base_dir)
    selected = categories if categories else list(CATEGORY_QUERIES)
    results: list[dict] = []
    for category in selected:
        category_dir = base_dir / category
        category_dir.mkdir(parents=True, exist_ok=True)
        try:
            results.append(
                download_category(
                    category,
                    count_per_category,
                    category_dir,
                    api_key,
                    orientation=orientation,
                    opener=opener,
                    on_progress=on_progress,
                )
            )
        except PexelsError as exc:
            # 某一类搜索出错（常见是限流）不该拖垮整批：记录后继续下一类，
            # 已下好的其它类保留。已下的文件幂等跳过，稍后重跑即可补齐本类。
            results.append({
                "category": category, "requested": count_per_category,
                "downloaded": 0, "skipped": 0, "failed": [f"整类失败：{exc}"],
            })
    return results


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m video_factory.pexels_downloader",
        description="Pexels 视频素材下载器：按类型批量下载免费视频素材。",
    )
    parser.add_argument(
        "--category",
        action="append",
        help="中文类型（可重复）；缺省下载全部 10 类。可选：" + "、".join(CATEGORY_QUERIES),
    )
    parser.add_argument("--count", type=int, default=DEFAULT_COUNT, help="每类下载数量，默认 20。")
    parser.add_argument("--out", default=DEFAULT_OUT_DIR, help="素材库根目录，默认「素材库」。")
    parser.add_argument(
        "--orientation",
        default="landscape",
        choices=("landscape", "portrait", "square"),
        help="视频方向，默认 landscape。",
    )
    return parser


def _validate_categories(categories: list[str] | None) -> list[str] | None:
    if not categories:
        return None
    invalid = [c for c in categories if c not in CATEGORY_QUERIES]
    if invalid:
        raise PexelsError(
            f"非法类型：{'、'.join(invalid)}。可选：{'、'.join(CATEGORY_QUERIES)}"
        )
    return categories


def _print_progress(category: str, index: int, total: int) -> None:
    print(f"正在下载「{category}」{index}/{total} …")


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    api_key = os.environ.get("PEXELS_API_KEY", "").strip()
    if not api_key:
        print(
            "未设置 PEXELS_API_KEY。请到 https://www.pexels.com/api 免费注册获取 API Key，"
            "并填入凭据（环境变量 PEXELS_API_KEY）。",
            file=sys.stderr,
        )
        return 1
    try:
        categories = _validate_categories(args.category)
        results = download_all(
            args.out,
            args.count,
            api_key,
            categories=categories,
            orientation=args.orientation,
            on_progress=_print_progress,
        )
    except PexelsError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    _print_summary(results)
    return 0


def _print_summary(results: list[dict]) -> None:
    for result in results:
        print(
            f"类型「{result['category']}」：成功 {result['downloaded']}，"
            f"跳过 {result['skipped']}，失败 {len(result['failed'])}"
        )


if __name__ == "__main__":
    raise SystemExit(main())
