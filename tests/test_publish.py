"""发布物料（publish）阶段测试：全部离线（fake runner + 无凭据降级）。"""

import json
import subprocess
from pathlib import Path

import pytest

from video_factory.publish import (
    KIT_FILENAME,
    TXT_FILENAME,
    build_description_and_tags,
    generate_publish_kit,
    pick_cover_background,
)


@pytest.fixture(autouse=True)
def _no_llm_credentials(monkeypatch):
    """默认无凭据：简介走模板降级分支（LLM 路径单独用 monkeypatch 测）。"""
    for name in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY"):
        monkeypatch.delenv(name, raising=False)


REWRITE = {
    "hook": "你被情绪劫持了吗，三招破局？",
    "publish_titles": ["王阳明：此心不动，万事从容", "心学三步破内耗"],
    "sections": [
        {"title": "一、聚会的刺痛", "narration": "口播一",
         "emphasis": [{"text": "心中之贼", "kind": "keyword"}]},
        {"title": "二、心外无物", "narration": "口播二",
         "emphasis": [{"text": "知行合一", "kind": "golden"}]},
    ],
}


class _StillRecorder:
    """记录 npx remotion still 命令并落占位 jpg。"""

    def __init__(self, returncode=0):
        self.commands = []
        self.returncode = returncode

    def __call__(self, command, **kwargs):
        self.commands.append(command)
        if "still" in command and self.returncode == 0:
            Path(command[command.index("still") + 3]).write_bytes(b"jpg")
        return subprocess.CompletedProcess(command, self.returncode, stdout="", stderr="boom")


def _write_rewrite(job_dir: Path) -> Path:
    job_dir.mkdir(parents=True, exist_ok=True)
    path = job_dir / "rewrite.json"
    path.write_text(json.dumps(REWRITE, ensure_ascii=False), encoding="utf-8")
    return path


def test_generate_publish_kit_end_to_end_offline(tmp_path, monkeypatch):
    monkeypatch.setattr("video_factory.publish.shutil.which", lambda _: "/usr/bin/npx")
    job_dir = tmp_path / "job"
    rewrite_path = _write_rewrite(job_dir)
    (job_dir / "gen_assets").mkdir()
    (job_dir / "gen_assets" / "beat_01.png").write_bytes(b"\x89PNG fake")
    runner = _StillRecorder()

    kit = generate_publish_kit(rewrite_path, None, job_dir, tag="心学频道", runner=runner)

    # 三封面：16x9（B站/西瓜）、9x16（竖屏）、3x4（抖音横版专用——主页作品位
    # 是竖向 ~1080×1464，16:9 会被裁掉左右，2026-07-16 用户实测+查证）
    assert set(kit["covers"]) == {"16x9", "9x16", "3x4"}
    comps = [c[c.index("still") + 2] for c in runner.commands if "still" in c]
    assert comps == ["Cover16x9", "Cover9x16", "Cover3x4"]
    # props：标题取 publish_titles[0]、底图 data URI 来自 gen_assets 首图
    # 2026-07-18 起 props 逐封面独立（画幅匹配底图防裁人），抽 16x9 那份验证
    props = json.loads((job_dir / "publish" / "cover.props.16x9.json").read_text(encoding="utf-8"))
    assert props["title"].startswith("王阳明")
    assert props["tag"] == "心学频道"
    assert props["bg"].startswith("data:image/png;base64,")
    # 标题候选原样保留；无凭据 → 简介模板降级、标签取自 emphasis
    assert kit["titles"] == REWRITE["publish_titles"]
    assert "本期讲透" in kit["description"]
    assert "心中之贼" in kit["tags"] and "知行合一" in kit["tags"]
    # 留档双文件
    assert (job_dir / "publish" / KIT_FILENAME).exists()
    txt = (job_dir / "publish" / TXT_FILENAME).read_text(encoding="utf-8")
    assert "【标题候选】" in txt and "【简介】" in txt and "#心中之贼" in txt


def test_generate_publish_kit_without_npx_still_writes_kit(tmp_path, monkeypatch):
    monkeypatch.setattr("video_factory.publish.shutil.which", lambda _: None)
    job_dir = tmp_path / "job"
    rewrite_path = _write_rewrite(job_dir)

    kit = generate_publish_kit(rewrite_path, None, job_dir)

    assert kit["covers"] == {}
    assert any("npx" in w for w in kit["warnings"])
    assert (job_dir / "publish" / KIT_FILENAME).exists()  # 物料照常留档


def test_pick_cover_background_prefers_gen_assets_then_frame(tmp_path):
    job_dir = tmp_path / "job"
    (job_dir / "gen_assets").mkdir(parents=True)
    first = job_dir / "gen_assets" / "a.png"
    first.write_bytes(b"png")

    bg, warnings = pick_cover_background(job_dir, None)
    assert bg == first and warnings == []

    # 无生图素材 → ffmpeg 从成片抽帧
    job_dir2 = tmp_path / "job2"
    job_dir2.mkdir()
    video = job_dir2 / "release.mp4"
    video.write_bytes(b"mp4")
    recorded = []

    def runner(command, **kwargs):
        recorded.append(command)
        Path(command[-1]).parent.mkdir(parents=True, exist_ok=True)
        Path(command[-1]).write_bytes(b"jpg")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    bg2, warnings2 = pick_cover_background(job_dir2, video, runner)
    assert bg2 is not None and bg2.name == "cover_bg.jpg"
    assert "-frames:v" in recorded[0]


def test_description_uses_llm_when_available(monkeypatch):
    monkeypatch.setattr("video_factory.publish.resolve_llm_provider", lambda _: "deepseek")
    monkeypatch.setattr(
        "video_factory.publish.chat_completion",
        lambda s, u, c: '{"description": "一条 LLM 写的简介。", "tags": ["#王阳明", "心学"]}',
    )
    description, tags, warnings = build_description_and_tags(REWRITE)
    assert description == "一条 LLM 写的简介。"
    assert tags == ["王阳明", "心学"]  # # 号剥掉
    assert warnings == []


def test_build_publish_argv_picks_final_video(tmp_path):
    from video_factory.batch import build_publish_argv, resolve_job

    job = resolve_job({"source": "s.mp4", "assets": "a"}, 0)
    (tmp_path / "release.mp4").write_bytes(b"v")
    (tmp_path / "release_subtitled.mp4").write_bytes(b"v")
    argv = build_publish_argv(job, tmp_path)
    assert argv[argv.index("--video") + 1].endswith("release_subtitled.mp4")

    empty = tmp_path / "empty"
    empty.mkdir()
    assert "--video" not in build_publish_argv(job, empty)


# ---- 成片归档（2026-07-16 用户定案：视频+封面+文案一站式文件夹） ----


class _StillAndProbeRecorder(_StillRecorder):
    """still 落占位 jpg；ffprobe 按路径回分辨率（9x16 目录→竖版，其余→横版）。"""

    def __call__(self, command, **kwargs):
        if command and command[0] == "ffprobe":
            path = str(command[-1])
            payload = (
                {"streams": [{"width": 1080, "height": 1920}]}
                if "9x16" in path
                else {"streams": [{"width": 1920, "height": 1080}]}
            )
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload), stderr="")
        return super().__call__(command, **kwargs)


def test_publish_archives_dual_videos_into_publish_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("video_factory.publish.shutil.which", lambda _: "/usr/bin/npx")
    job_dir = tmp_path / "job"
    rewrite_path = _write_rewrite(job_dir)
    for sub in ("9x16", "16x9"):
        (job_dir / sub).mkdir()
        (job_dir / sub / "release_subtitled.mp4").write_bytes(b"v" * 32)
    runner = _StillAndProbeRecorder()

    kit = generate_publish_kit(rewrite_path, None, job_dir, runner=runner)

    # 双画幅成片按横竖命名归档进 publish/（硬链接或复制），kit 留档路径
    assert set(kit["videos"]) == {"竖版_9x16", "横版_16x9"}
    assert (job_dir / "publish" / "视频_竖版_9x16.mp4").exists()
    assert (job_dir / "publish" / "视频_横版_16x9.mp4").exists()
    txt = (job_dir / "publish" / TXT_FILENAME).read_text(encoding="utf-8")
    assert "【成片】" in txt
    assert "3x4 这张" in txt  # 抖音横版封面提示写进物料


def test_publish_archives_single_root_video_without_npx(tmp_path, monkeypatch):
    # 无 npx（封面跳过）也照常归档根目录单画幅成片。
    monkeypatch.setattr("video_factory.publish.shutil.which", lambda _: None)
    job_dir = tmp_path / "job"
    rewrite_path = _write_rewrite(job_dir)
    (job_dir / "release_subtitled.mp4").write_bytes(b"v")
    runner = _StillAndProbeRecorder()

    kit = generate_publish_kit(rewrite_path, None, job_dir, runner=runner)

    assert set(kit["videos"]) == {"横版_16x9"}
    assert (job_dir / "publish" / "视频_横版_16x9.mp4").exists()


def test_pick_cover_backgrounds_matches_aspect_per_cover(tmp_path):
    # 2026-07-18 防裁人：16x9 封面配横图、9x16/3x4 封面配竖图；无对应向回落任意首图。
    from PIL import Image

    from video_factory.publish import pick_cover_backgrounds

    job_dir = tmp_path / "job"
    gen = job_dir / "gen_assets"
    gen.mkdir(parents=True)
    Image.new("RGB", (90, 160)).save(gen / "a_portrait.png")
    Image.new("RGB", (160, 90)).save(gen / "b_landscape.png")

    bgs, warnings = pick_cover_backgrounds(job_dir, None)
    assert bgs["16x9"].name == "b_landscape.png"
    assert bgs["9x16"].name == "a_portrait.png"
    assert bgs["3x4"].name == "a_portrait.png"
    assert warnings == []

    # 只有竖图 → 16x9 回落首图（CoverCard contain 渲染保人物完整）
    job2 = tmp_path / "job2"
    gen2 = job2 / "gen_assets"
    gen2.mkdir(parents=True)
    Image.new("RGB", (90, 160)).save(gen2 / "only_portrait.png")
    bgs2, _ = pick_cover_backgrounds(job2, None)
    assert bgs2["16x9"].name == "only_portrait.png"
