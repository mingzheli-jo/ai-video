"""特效音模块（video_factory.sfx）单元测试。"""

import subprocess
from pathlib import Path

import pytest

from video_factory.sfx import (
    DEFAULT_SFX_DIR,
    SFX_BY_TYPE,
    SfxError,
    ensure_default_pack,
    resolve_sfx_path,
)


def test_resolve_sfx_path_maps_type_to_file(tmp_path):
    d = tmp_path / "sfx"
    d.mkdir()
    # 2026-07-16 音效语言统一：intro 也改"刷"（whoosh 只留给首屏首声）。
    (d / "swoosh.wav").write_bytes(b"x")
    assert resolve_sfx_path("intro", d) == d / "swoosh.wav"


def test_resolve_sfx_path_none_for_unknown_type(tmp_path):
    d = tmp_path / "sfx"
    d.mkdir()
    (d / "whoosh.wav").write_bytes(b"x")
    assert resolve_sfx_path("nonexistent_type", d) is None


def test_resolve_sfx_path_none_when_file_missing(tmp_path):
    d = tmp_path / "sfx"
    d.mkdir()  # 空目录：类型合法但文件不在 → None（调用方跳过这一声）
    assert resolve_sfx_path("chapter_card", d) is None


def test_builtin_pack_is_shipped():
    # 内置音效包必须随仓库发布，三个音效都在（开箱即用）。
    for name in SFX_BY_TYPE.values():
        assert (DEFAULT_SFX_DIR / name).exists(), f"缺内置音效 {name}"


def test_ensure_default_pack_skips_existing_generates_missing(tmp_path, monkeypatch):
    d = tmp_path / "sfx"
    d.mkdir()
    (d / "whoosh.wav").write_bytes(b"already")  # 用户已放，不该被覆盖

    def fake_run(command, **kwargs):
        Path(command[-1]).write_bytes(b"generated")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("video_factory.sfx.shutil.which", lambda name: "/usr/bin/ffmpeg")
    generated = ensure_default_pack(d, runner=fake_run)

    # 只补缺失：whoosh 已存在不动，pop/swoosh/impact 三个缺失的都补上（transition 已移除）。
    assert {p.name for p in generated} == {"pop.wav", "swoosh.wav", "impact.wav"}
    assert (d / "whoosh.wav").read_bytes() == b"already"  # 尊重用户替换，不覆盖


def test_ensure_default_pack_raises_without_ffmpeg(tmp_path, monkeypatch):
    monkeypatch.setattr("video_factory.sfx.shutil.which", lambda name: None)
    with pytest.raises(SfxError, match="ffmpeg"):
        ensure_default_pack(tmp_path / "sfx")


def test_transition_type_removed_from_sfx_mapping(tmp_path):
    # 转场音效已取消（2026-07-15 用户点名）：transition 不再映射任何音效文件。
    assert "transition" not in SFX_BY_TYPE
    # resolve_sfx_path 对 transition 类型应返回 None（不再注入）。
    d = tmp_path / "sfx"
    d.mkdir()
    (d / "transition.wav").write_bytes(b"x")  # 即使文件存在，也不应被引用
    assert resolve_sfx_path("transition", d) is None


def test_ensure_default_pack_does_not_synthesize_transition(tmp_path, monkeypatch):
    # 转场音效已取消：ensure_default_pack 不再合成 transition.wav；
    # 新增 impact.wav（金句卡冲击音）应在空目录下被合成。
    def fake_run(command, **kwargs):
        Path(command[-1]).write_bytes(b"generated")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("video_factory.sfx.shutil.which", lambda name: "/usr/bin/ffmpeg")
    generated = ensure_default_pack(tmp_path / "sfx", runner=fake_run)
    names = {p.name for p in generated}
    assert "transition.wav" not in names  # 不再注入转场音
    assert "impact.wav" in names          # 金句卡冲击音应生成
