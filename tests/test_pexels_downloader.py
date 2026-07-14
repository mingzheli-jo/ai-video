import json
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse

import pytest

from video_factory.pexels_downloader import (
    CATEGORY_QUERIES,
    PexelsError,
    PexelsVideo,
    _build_parser,
    _pick_best_file,
    download_all,
    download_category,
    download_video,
    main,
    search_videos,
)


# --------------------------------------------------------------------------- #
# 测试替身（全部离线，绝不真连网）
# --------------------------------------------------------------------------- #
class FakeResponse:
    def __init__(self, data: bytes):
        self._data = data
        self._pos = 0

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            chunk = self._data[self._pos :]
            self._pos = len(self._data)
            return chunk
        chunk = self._data[self._pos : self._pos + size]
        self._pos += len(chunk)
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def hd_file(vid: int, height: int = 1080) -> dict:
    return {
        "quality": "hd",
        "width": height * 16 // 9,
        "height": height,
        "file_type": "video/mp4",
        "link": f"https://dl.pexels.test/{vid}_hd_{height}.mp4",
    }


def sd_file(vid: int) -> dict:
    return {
        "quality": "sd",
        "width": 640,
        "height": 360,
        "file_type": "video/mp4",
        "link": f"https://dl.pexels.test/{vid}_sd.mp4",
    }


def raw_video(vid: int, files: list[dict] | None = None) -> dict:
    return {
        "id": vid,
        "width": 1920,
        "height": 1080,
        "duration": 12.0,
        "video_files": files if files is not None else [hd_file(vid)],
    }


def search_bytes(videos: list[dict], page: int = 1, per_page: int = 15) -> bytes:
    payload = {"videos": videos, "page": page, "per_page": per_page, "total_results": len(videos)}
    return json.dumps(payload).encode("utf-8")


class FakeOpener:
    """按 URL 路由：搜索请求返回 JSON，下载请求返回视频字节。"""

    def __init__(self, pages: dict[tuple[str, int], list[dict]] | None = None, fail_urls: set[str] | None = None):
        self.pages = pages or {}
        self.fail_urls = fail_urls or set()
        self.search_calls: list[tuple[str, int]] = []
        self.download_calls: list[str] = []

    def __call__(self, request, timeout=None):
        url = request.full_url
        # Cloudflare 1010 回归：搜索与下载都必须带正常 UA，否则被 403 拦。
        self.last_user_agent = request.get_header("User-agent")
        assert self.last_user_agent, "请求缺少 User-Agent，会被 Pexels 的 Cloudflare 403 拦截"
        if url.startswith("https://api.pexels.com/videos/search"):
            qs = parse_qs(urlparse(url).query)
            query = qs["query"][0]
            page = int(qs["page"][0])
            self.search_calls.append((query, page))
            videos = self.pages.get((query, page), [])
            return FakeResponse(search_bytes(videos, page=page))
        self.download_calls.append(url)
        if url in self.fail_urls:
            raise URLError("boom")
        return FakeResponse(b"FAKEVIDEOBYTES")


# --------------------------------------------------------------------------- #
# CATEGORY_QUERIES
# --------------------------------------------------------------------------- #
def test_category_queries_has_ten_types_each_with_enough_queries():
    assert len(CATEGORY_QUERIES) == 10
    for category, queries in CATEGORY_QUERIES.items():
        assert len(queries) >= 3, category
        assert all(isinstance(q, str) and q for q in queries)


# --------------------------------------------------------------------------- #
# _pick_best_file
# --------------------------------------------------------------------------- #
def test_pick_best_file_prefers_hd_up_to_1080_and_avoids_4k():
    files = [hd_file(1, height=2160), hd_file(1, height=1080), sd_file(1)]
    chosen = _pick_best_file(files)
    assert chosen is not None
    assert chosen["height"] == 1080
    assert chosen["quality"] == "hd"


def test_pick_best_file_falls_back_to_sd_when_only_4k_hd():
    files = [hd_file(1, height=2160), sd_file(1)]
    chosen = _pick_best_file(files)
    assert chosen["quality"] == "sd"


def test_pick_best_file_falls_back_to_first_mp4_when_no_hd_or_sd():
    uhd = {"quality": "uhd", "width": 3840, "height": 2160, "file_type": "video/mp4", "link": "https://x/u.mp4"}
    webm = {"quality": "hd", "width": 1920, "height": 1080, "file_type": "video/webm", "link": "https://x/w.webm"}
    chosen = _pick_best_file([webm, uhd])
    assert chosen is uhd


def test_pick_best_file_returns_none_without_mp4():
    webm = {"quality": "hd", "width": 1920, "height": 1080, "file_type": "video/webm", "link": "https://x/w.webm"}
    assert _pick_best_file([webm]) is None


def _mp4(height: int, quality=None) -> dict:
    # Pexels 新数据 quality 常为 None，只按分辨率选片。
    return {"quality": quality, "width": height * 16 // 9, "height": height,
            "file_type": "video/mp4", "link": f"https://x/{height}.mp4"}


def test_pick_best_file_selects_by_resolution_when_quality_is_none():
    # 回归：真实 Pexels 返回 quality=None + 多分辨率，必须选 1080 而不是列表首个 360。
    files = [_mp4(360), _mp4(540), _mp4(720), _mp4(1080), _mp4(1440), _mp4(2160)]
    chosen = _pick_best_file(files)
    assert chosen["height"] == 1080  # <=1080 中最高清，不是列表第一个 360


def test_pick_best_file_picks_smallest_when_all_above_1080():
    # 全部超 1080（quality=None）：取最矮的一档，避免 4K 巨文件。
    files = [_mp4(2160), _mp4(1440), _mp4(1280)]
    assert _pick_best_file(files)["height"] == 1280


# --------------------------------------------------------------------------- #
# search_videos
# --------------------------------------------------------------------------- #
def test_search_videos_paginates_until_count_reached():
    # 第一页 5 条原始数据，其中 2 条没有 mp4（会被跳过），触发翻页到第二页补足。
    no_mp4 = raw_video(0, files=[{"quality": "hd", "height": 1080, "file_type": "video/webm", "link": "x"}])
    page1 = [raw_video(1), raw_video(2), no_mp4, no_mp4, raw_video(3)]
    page2 = [raw_video(4), raw_video(5), raw_video(6), raw_video(7), raw_video(8)]
    opener = FakeOpener(pages={("technology", 1): page1, ("technology", 2): page2})

    videos = search_videos("technology", 5, "KEY", opener=opener)

    assert [v.id for v in videos] == [1, 2, 3, 4, 5]
    assert opener.search_calls == [("technology", 1), ("technology", 2)]
    assert all(isinstance(v, PexelsVideo) for v in videos)
    assert videos[0].download_url.endswith("1_hd_1080.mp4")


def test_search_videos_stops_when_page_shorter_than_per_page():
    opener = FakeOpener(pages={("science", 1): [raw_video(1), raw_video(2)]})
    videos = search_videos("science", 10, "KEY", opener=opener)
    assert [v.id for v in videos] == [1, 2]
    assert opener.search_calls == [("science", 1)]


def test_search_videos_maps_401_to_chinese_error():
    def opener(request, timeout=None):
        raise HTTPError(request.full_url, 401, "Unauthorized", None, None)

    with pytest.raises(PexelsError) as exc:
        search_videos("technology", 5, "BAD", opener=opener)
    assert "PEXELS_API_KEY 无效或未授权" in str(exc.value)


def test_search_videos_maps_429_to_rate_limit_error():
    def opener(request, timeout=None):
        raise HTTPError(request.full_url, 429, "Too Many Requests", None, None)

    with pytest.raises(PexelsError) as exc:
        search_videos("technology", 5, "KEY", opener=opener)
    assert "限流" in str(exc.value)


# --------------------------------------------------------------------------- #
# download_video
# --------------------------------------------------------------------------- #
def test_download_video_writes_file_and_cleans_part(tmp_path):
    video = PexelsVideo(1, 1920, 1080, 12.0, "https://dl.pexels.test/1.mp4")
    opener = FakeOpener()
    dest = tmp_path / "pexels_1.mp4"

    result = download_video(video, dest, opener=opener)

    assert result == dest
    assert dest.read_bytes() == b"FAKEVIDEOBYTES"
    assert not (tmp_path / "pexels_1.mp4.part").exists()


def test_download_video_is_idempotent_when_file_exists(tmp_path):
    dest = tmp_path / "pexels_1.mp4"
    dest.write_bytes(b"EXISTING")

    def opener(request, timeout=None):
        raise AssertionError("已存在文件不应重新下载")

    video = PexelsVideo(1, 1920, 1080, 12.0, "https://dl.pexels.test/1.mp4")
    result = download_video(video, dest, opener=opener)

    assert result == dest
    assert dest.read_bytes() == b"EXISTING"


def test_download_video_cleans_part_on_failure(tmp_path):
    video = PexelsVideo(9, 1920, 1080, 12.0, "https://dl.pexels.test/9.mp4")
    opener = FakeOpener(fail_urls={"https://dl.pexels.test/9.mp4"})
    dest = tmp_path / "pexels_9.mp4"

    with pytest.raises(PexelsError):
        download_video(video, dest, opener=opener)

    assert not dest.exists()
    assert not (tmp_path / "pexels_9.mp4.part").exists()


# --------------------------------------------------------------------------- #
# download_category
# --------------------------------------------------------------------------- #
def test_download_category_merges_and_dedupes_by_id(tmp_path):
    queries = CATEGORY_QUERIES["科技"]
    opener = FakeOpener(
        pages={
            (queries[0], 1): [raw_video(1), raw_video(2)],
            (queries[1], 1): [raw_video(2), raw_video(3)],
            (queries[2], 1): [raw_video(4)],
        }
    )

    result = download_category("科技", 3, tmp_path, "KEY", opener=opener)

    assert result["category"] == "科技"
    assert result["downloaded"] == 3
    assert result["skipped"] == 0
    assert result["failed"] == []
    files = sorted(p.name for p in tmp_path.glob("pexels_*.mp4"))
    assert files == ["pexels_1.mp4", "pexels_2.mp4", "pexels_3.mp4"]
    # id=2 只下载一次（去重）
    assert opener.download_calls.count("https://dl.pexels.test/2_hd_1080.mp4") == 1


def test_download_category_collects_single_failure_and_continues(tmp_path):
    queries = CATEGORY_QUERIES["科技"]
    opener = FakeOpener(
        pages={(queries[0], 1): [raw_video(1), raw_video(2), raw_video(3)]},
        fail_urls={"https://dl.pexels.test/2_hd_1080.mp4"},
    )

    result = download_category("科技", 3, tmp_path, "KEY", opener=opener)

    assert result["downloaded"] == 2
    assert len(result["failed"]) == 1
    assert result["failed"][0]["id"] == 2
    assert (tmp_path / "pexels_1.mp4").exists()
    assert (tmp_path / "pexels_3.mp4").exists()
    assert not (tmp_path / "pexels_2.mp4").exists()


def test_download_category_reports_progress(tmp_path):
    queries = CATEGORY_QUERIES["科技"]
    opener = FakeOpener(pages={(queries[0], 1): [raw_video(1), raw_video(2)]})
    events: list[tuple[str, int, int]] = []

    download_category(
        "科技", 2, tmp_path, "KEY", opener=opener, on_progress=lambda c, i, t: events.append((c, i, t))
    )

    assert events == [("科技", 1, 2), ("科技", 2, 2)]


# --------------------------------------------------------------------------- #
# download_all
# --------------------------------------------------------------------------- #
def test_download_all_builds_directory_tree_and_returns_per_category(tmp_path):
    opener = FakeOpener(
        pages={
            (CATEGORY_QUERIES["科技"][0], 1): [raw_video(1)],
            (CATEGORY_QUERIES["自然"][0], 1): [raw_video(2)],
        }
    )

    results = download_all(tmp_path, 1, "KEY", categories=["科技", "自然"], opener=opener)

    assert [r["category"] for r in results] == ["科技", "自然"]
    assert (tmp_path / "科技" / "pexels_1.mp4").exists()
    assert (tmp_path / "自然" / "pexels_2.mp4").exists()
    assert all(r["downloaded"] == 1 for r in results)


def test_download_all_one_category_rate_limited_does_not_abort_others(tmp_path):
    from urllib.error import HTTPError

    class RateLimitOnFirst:
        """第一类搜索触发 429（限流），其余类正常。"""
        def __init__(self):
            self.first_query = CATEGORY_QUERIES["科技"][0]

        def __call__(self, request, timeout=None):
            url = request.full_url
            if "/videos/search" in url:
                from urllib.parse import parse_qs, urlparse
                if parse_qs(urlparse(url).query)["query"][0] == self.first_query:
                    raise HTTPError(url, 429, "Too Many Requests", {}, None)
                return FakeResponse(search_bytes([raw_video(2)], page=1))
            return FakeResponse(b"BYTES")

    results = download_all(tmp_path, 1, "KEY", categories=["科技", "自然"], opener=RateLimitOnFirst())

    # 科技 整类失败但不中断，自然 照常下成
    assert results[0]["category"] == "科技" and results[0]["downloaded"] == 0
    assert any("限流" in f or "整类失败" in f for f in results[0]["failed"])
    assert results[1]["category"] == "自然" and results[1]["downloaded"] == 1
    assert (tmp_path / "自然" / "pexels_2.mp4").exists()


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def test_cli_missing_api_key_exits_1(monkeypatch, capsys):
    monkeypatch.delenv("PEXELS_API_KEY", raising=False)
    code = main([])
    assert code == 1
    assert "PEXELS_API_KEY" in capsys.readouterr().err


def test_cli_invalid_category_lists_valid_options(monkeypatch, capsys):
    monkeypatch.setenv("PEXELS_API_KEY", "KEY")
    code = main(["--category", "不存在"])
    assert code == 1
    err = capsys.readouterr().err
    assert "非法类型" in err
    assert "科技" in err


def test_cli_parses_count_out_and_category():
    args = _build_parser().parse_args(["--count", "5", "--out", "库", "--category", "科技", "--category", "自然"])
    assert args.count == 5
    assert args.out == "库"
    assert args.category == ["科技", "自然"]
    assert args.orientation == "landscape"
