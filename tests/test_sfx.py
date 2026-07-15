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
    (d / "whoosh.wav").write_bytes(b"x")
    assert resolve_sfx_path("intro", d) == d / "whoosh.wav"


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

    # 只补缺失：whoosh 已存在不动，pop/swoosh/transition 三个缺失的都补上。
    assert {p.name for p in generated} == {"pop.wav", "swoosh.wav", "transition.wav"}
    assert (d / "whoosh.wav").read_bytes() == b"already"  # 尊重用户替换，不覆盖


def test_ensure_default_pack_raises_without_ffmpeg(tmp_path, monkeypatch):
    monkeypatch.setattr("video_factory.sfx.shutil.which", lambda name: None)
    with pytest.raises(SfxError, match="ffmpeg"):
        ensure_default_pack(tmp_path / "sfx")


def test_transition_type_maps_to_transition_wav(tmp_path):
    # 转场 whoosh：新增的 transition 类型映射到独立的 transition.wav。
    assert SFX_BY_TYPE["transition"] == "transition.wav"
    d = tmp_path / "sfx"
    d.mkdir()
    (d / "transition.wav").write_bytes(b"x")
    assert resolve_sfx_path("transition", d) == d / "transition.wav"


def test_ensure_default_pack_synthesizes_transition(tmp_path, monkeypatch):
    # 空目录 → 应把 transition.wav 也合成出来（转场特效音开箱即用）。
    def fake_run(command, **kwargs):
        Path(command[-1]).write_bytes(b"generated")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("video_factory.sfx.shutil.which", lambda name: "/usr/bin/ffmpeg")
    generated = ensure_default_pack(tmp_path / "sfx", runner=fake_run)
    assert "transition.wav" in {p.name for p in generated}
