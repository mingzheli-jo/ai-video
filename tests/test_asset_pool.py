import json
import subprocess
from pathlib import Path

import pytest

from video_factory.asset_pool import (
    AssetClip,
    AssetPoolError,
    allocate_sections_to_clips,
    scan_asset_pool,
    MIN_SECTION_SECONDS,
)


def _ffprobe_json(duration, width=1920, height=1080):
    return json.dumps(
        {
            "format": {"duration": str(duration)},
            "streams": [
                {"codec_type": "video", "width": width, "height": height, "duration": str(duration)}
            ],
        }
    )


def _make_clip(name, duration, width=1920, height=1080):
    return AssetClip(path=Path(name), duration=duration, width=width, height=height)


# --- scan ---------------------------------------------------------------


def test_scan_asset_pool_parses_ffprobe_output(tmp_path):
    (tmp_path / "a.mp4").write_bytes(b"x")
    (tmp_path / "b.mov").write_bytes(b"x")
    (tmp_path / "notes.txt").write_text("ignore", encoding="utf-8")

    def fake_runner(command, **kwargs):
        name = command[-1]
        duration = 12.5 if name.endswith("a.mp4") else 8.0
        return subprocess.CompletedProcess(command, 0, stdout=_ffprobe_json(duration), stderr="")

    result = scan_asset_pool(tmp_path, runner=fake_runner)

    assert [clip.path.name for clip in result.clips] == ["a.mp4", "b.mov"]
    assert result.clips[0].duration == 12.5
    assert result.clips[0].width == 1920 and result.clips[0].height == 1080
    assert result.warnings == ()


def test_scan_asset_pool_skips_bad_files_without_aborting(tmp_path):
    (tmp_path / "good.mp4").write_bytes(b"x")
    (tmp_path / "bad.mp4").write_bytes(b"x")

    def fake_runner(command, **kwargs):
        name = command[-1]
        if name.endswith("bad.mp4"):
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="moov atom not found")
        return subprocess.CompletedProcess(command, 0, stdout=_ffprobe_json(9.0), stderr="")

    result = scan_asset_pool(tmp_path, runner=fake_runner)

    assert [clip.path.name for clip in result.clips] == ["good.mp4"]
    assert len(result.warnings) == 1
    assert "bad.mp4" in result.warnings[0]


def test_scan_asset_pool_raises_for_missing_directory(tmp_path):
    with pytest.raises(AssetPoolError, match="素材目录不存在"):
        scan_asset_pool(tmp_path / "nope")


def test_scan_asset_pool_raises_when_no_usable_assets(tmp_path):
    (tmp_path / "readme.md").write_text("x", encoding="utf-8")
    with pytest.raises(AssetPoolError, match="没有可用的视频/图片素材"):
        scan_asset_pool(tmp_path, runner=lambda *a, **k: None)


def test_scan_asset_pool_skips_zero_duration(tmp_path):
    (tmp_path / "empty.mp4").write_bytes(b"x")

    def fake_runner(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, stdout=_ffprobe_json(0), stderr="")

    with pytest.raises(AssetPoolError, match="没有可用的视频/图片素材"):
        scan_asset_pool(tmp_path, runner=fake_runner)


# --- allocation ---------------------------------------------------------


def test_allocation_total_equals_target_and_respects_char_share():
    clips = [_make_clip("c.mp4", duration=1000.0)]
    allocations = allocate_sections_to_clips([10, 30], clips, total_duration=100.0)

    total = sum(a.duration for a in allocations)
    assert total == pytest.approx(100.0, abs=1e-6)
    # 第二节字数是第一节 3 倍，扣掉每节 2s 保护后 96s 按 1:3 分。
    assert allocations[0].duration == pytest.approx(2.0 + 96.0 * 0.25, abs=0.01)
    assert allocations[1].duration == pytest.approx(2.0 + 96.0 * 0.75, abs=0.01)


def test_allocation_handles_float_residual_into_last_section():
    clips = [_make_clip("c.mp4", duration=1000.0)]
    allocations = allocate_sections_to_clips([1, 1, 1], clips, total_duration=100.0)

    total = sum(a.duration for a in allocations)
    assert total == pytest.approx(100.0, abs=1e-9)


def test_allocation_enforces_min_two_seconds():
    clips = [_make_clip("c.mp4", duration=1000.0)]
    allocations = allocate_sections_to_clips([1, 1000], clips, total_duration=50.0)

    assert allocations[0].duration >= MIN_SECTION_SECONDS


def test_allocation_rejects_target_too_short_for_min_protection():
    clips = [_make_clip("c.mp4", duration=1000.0)]
    with pytest.raises(AssetPoolError, match="太短"):
        allocate_sections_to_clips([1, 1, 1], clips, total_duration=5.0)


def test_allocation_rotates_clips_and_offsets_reuse():
    clips = [_make_clip("a.mp4", duration=10.0), _make_clip("b.mp4", duration=10.0)]
    # 4 节各 5s（等字数），共 20s；素材各 10s。
    allocations = allocate_sections_to_clips([1, 1, 1, 1], clips, total_duration=20.0)

    # 轮转：节0取 a[0:5]，节1取 b[0:5]，节2取 a[5:10]（错开起点），节3取 b[5:10]。
    assert allocations[0].slices[0].path.name == "a.mp4"
    assert allocations[0].slices[0].start == 0.0
    assert allocations[1].slices[0].path.name == "b.mp4"
    assert allocations[2].slices[0].path.name == "a.mp4"
    assert allocations[2].slices[0].start == pytest.approx(5.0, abs=1e-6)
    assert allocations[3].slices[0].path.name == "b.mp4"
    assert allocations[3].slices[0].start == pytest.approx(5.0, abs=1e-6)


def test_allocation_wraps_when_clip_exhausted_within_section():
    clips = [_make_clip("a.mp4", duration=3.0)]
    # 单素材 3s，一节要 8s → 内部串多段：3 + 3 + 2，最后回绕。
    allocations = allocate_sections_to_clips([1], clips, total_duration=8.0)

    slices = allocations[0].slices
    assert len(slices) >= 3
    assert sum(s.duration for s in slices) == pytest.approx(8.0, abs=1e-6)
    assert slices[0].start == 0.0
    # 用完回绕后再从头取。
    assert slices[-1].start == 0.0


def test_allocation_rejects_empty_clip_pool():
    with pytest.raises(AssetPoolError, match="没有可用素材"):
        allocate_sections_to_clips([1], [], total_duration=10.0)


# ---------- 图片素材扫描 ----------

def test_scan_asset_pool_accepts_images_with_virtual_duration(tmp_path):
    import json as _json
    import subprocess as _subprocess
    from video_factory.asset_pool import IMAGE_VIRTUAL_DURATION, scan_asset_pool

    (tmp_path / "photo.png").write_bytes(b"png")

    def fake_runner(command, **kwargs):
        payload = {"streams": [{"codec_type": "video", "width": 1080, "height": 1920}]}
        return _subprocess.CompletedProcess(command, 0, stdout=_json.dumps(payload), stderr="")

    result = scan_asset_pool(tmp_path, runner=fake_runner)
    assert len(result.clips) == 1
    clip = result.clips[0]
    assert clip.is_image is True
    assert clip.duration == IMAGE_VIRTUAL_DURATION
    assert (clip.width, clip.height) == (1080, 1920)


def test_scan_asset_pool_skips_image_without_resolution(tmp_path):
    import subprocess as _subprocess
    from video_factory.asset_pool import AssetPoolError, scan_asset_pool
    import pytest as _pytest

    (tmp_path / "broken.jpg").write_bytes(b"x")

    def fake_runner(command, **kwargs):
        return _subprocess.CompletedProcess(command, 0, stdout="{}", stderr="")

    with _pytest.raises(AssetPoolError):  # 只有一张坏图 → 全池为空报错
        scan_asset_pool(tmp_path, runner=fake_runner)
