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

    # 双封面：两个 composition 各渲一张
    assert set(kit["covers"]) == {"16x9", "9x16"}
    comps = [c[c.index("still") + 2] for c in runner.commands if "still" in c]
    assert comps == ["Cover16x9", "Cover9x16"]
    # props：标题取 publish_titles[0]、底图 data URI 来自 gen_assets 首图
    props = json.loads((job_dir / "publish" / "cover.props.json").read_text(encoding="utf-8"))
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
