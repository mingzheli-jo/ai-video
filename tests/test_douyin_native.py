"""抖音原生无水印解析器单测（全部离线：mock urllib.urlopen，绝不真联网）。"""

import io
import json

import pytest

from video_factory import douyin_native
from video_factory.douyin_native import (
    DouyinError,
    download_no_watermark,
    extract_first_url,
    parse_video_info,
    resolve_video_id,
    sanitize_filename,
    to_no_watermark,
)


# --- 纯函数 ---

def test_extract_first_url_from_share_text():
    # 「复制打开抖音」口令里抽第一个链接
    text = "7.53 复制打开抖音，看看【作者的作品】https://v.douyin.com/iAbCdEf/ 大家都在看"
    assert extract_first_url(text) == "https://v.douyin.com/iAbCdEf/"
    # 裸链接原样返回
    assert extract_first_url("https://www.douyin.com/video/123") == "https://www.douyin.com/video/123"
    # 尾部标点剥掉
    assert extract_first_url("看这个 https://v.douyin.com/x)。") == "https://v.douyin.com/x"
    assert extract_first_url("") == ""


def test_to_no_watermark_swaps_playwm():
    assert to_no_watermark("https://a.com/aweme/v1/playwm/?video_id=1") == "https://a.com/aweme/v1/play/?video_id=1"
    assert to_no_watermark("https://a.com/playwm/x") == "https://a.com/play/x"
    assert to_no_watermark("https://a.com/play/x") == "https://a.com/play/x"  # 已无水印不变


def test_sanitize_filename_strips_illegal_and_truncates():
    assert sanitize_filename('标题/含:非法*字符?"<>|', "fb") == "标题 含 非法 字符"
    assert sanitize_filename("  \n\t  ", "回落名") == "回落名"
    assert len(sanitize_filename("长" * 200, "fb")) == 80


# --- 网络路径（mock urlopen）---

class _FakeResp:
    def __init__(self, body=b"", final_url="https://final.example/"):
        self._body = body
        self._url = final_url
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False
    def read(self, n=-1):
        if n is None or n < 0:
            data, self._body = self._body, b""
            return data
        data, self._body = self._body[:n], self._body[n:]
        return data
    def geturl(self):
        return self._url


def _router_html(video_id="123456", desc="停车场老板躺着收钱", play_url="https://v.com/aweme/playwm/?id=1"):
    payload = {
        "loaderData": {
            "video_(id)/page": {
                "videoInfoRes": {
                    "item_list": [{
                        "desc": desc,
                        "video": {"play_addr": {"url_list": [play_url]}},
                    }]
                }
            }
        }
    }
    return (
        "<html><script>window._ROUTER_DATA = "
        + json.dumps(payload, ensure_ascii=False)
        + ";</script></html>"
    ).encode("utf-8")


def test_resolve_video_id_from_direct_url():
    assert resolve_video_id("https://www.douyin.com/video/7234567890") == "7234567890"
    assert resolve_video_id("https://www.douyin.com/?modal_id=555") == "555"


def test_resolve_video_id_follows_short_link_redirect(monkeypatch):
    # v.douyin.com 短链 → urlopen 跟随重定向到含 video_id 的最终 URL
    monkeypatch.setattr(
        douyin_native, "urlopen",
        lambda req, timeout=None: _FakeResp(b"", final_url="https://www.iesdouyin.com/share/video/998877/"),
    )
    assert resolve_video_id("https://v.douyin.com/abcXYZ/") == "998877"


def test_parse_video_info_extracts_desc_and_no_watermark_url(monkeypatch):
    monkeypatch.setattr(
        douyin_native, "urlopen",
        lambda req, timeout=None: _FakeResp(_router_html(play_url="https://v.com/playwm/?id=9")),
    )
    info = parse_video_info("123456")
    assert info["desc"] == "停车场老板躺着收钱"
    assert info["play_url"] == "https://v.com/play/?id=9"  # 已去水印


def test_parse_video_info_raises_when_no_router_data(monkeypatch):
    monkeypatch.setattr(
        douyin_native, "urlopen",
        lambda req, timeout=None: _FakeResp(b"<html>no data</html>"),
    )
    with pytest.raises(DouyinError, match="未返回视频数据"):
        parse_video_info("123456")


def test_download_no_watermark_end_to_end(monkeypatch, tmp_path):
    calls = {"n": 0}

    def fake_urlopen(req, timeout=None):
        calls["n"] += 1
        url = req.full_url if hasattr(req, "full_url") else str(req)
        if "share/video" in url:
            return _FakeResp(_router_html(desc="资源比努力更值钱"))
        # 视频流
        return _FakeResp(b"VIDEOBYTES" * 500)  # >1KB

    monkeypatch.setattr(douyin_native, "urlopen", fake_urlopen)
    dest = tmp_path / "source" / "source.mp4"
    progress = []
    title = download_no_watermark("https://www.douyin.com/video/123456", dest, progress=progress.append)

    assert title == "资源比努力更值钱"
    assert dest.exists() and dest.read_bytes().startswith(b"VIDEOBYTES")
    assert "下载无水印视频" in progress


def test_download_no_watermark_rejects_empty_stream(monkeypatch, tmp_path):
    def fake_urlopen(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        if "share/video" in url:
            return _FakeResp(_router_html())
        return _FakeResp(b"tiny")  # <1KB → 空文件
    monkeypatch.setattr(douyin_native, "urlopen", fake_urlopen)
    dest = tmp_path / "source.mp4"
    with pytest.raises(DouyinError, match="文件为空"):
        download_no_watermark("https://www.douyin.com/video/123456", dest)
    assert not dest.exists()  # 空文件已清理
