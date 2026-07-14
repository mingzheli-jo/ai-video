import json
import subprocess
from pathlib import Path

import pytest

from video_factory.effects import (
    DEFAULT_FPS,
    EffectsError,
    _resolve_video_dimensions,
    build_effects_manifest,
    main,
    overlay_effects,
    render_effects,
)


# assembly_plan.json 的最小可用形态：sections 各带 title/duration_seconds。
PLAN = {
    "version": "assembly_v1",
    "fps": 30,
    "width": 1920,
    "height": 1080,
    "sections": [
        {"index": 0, "title": "hook", "duration_seconds": 6.0, "slices": []},
        {"index": 1, "title": "第一节", "duration_seconds": 20.0, "slices": []},
        {"index": 2, "title": "第二节", "duration_seconds": 10.0, "slices": []},
    ],
}

REWRITE = {
    "hook": "三秒钩子这是一段很长的开场白文字",
    "publish_titles": ["候选标题一", "候选标题二"],
}


# --- manifest 派生 ------------------------------------------------------


def test_manifest_intro_derived_from_first_section_and_hook():
    manifest = build_effects_manifest(PLAN, REWRITE)
    intro = manifest["effects"][0]
    assert intro["type"] == "intro"
    assert intro["start"] == 0.0
    # min(2.5, 6.0*0.8=4.8) = 2.5
    assert intro["duration"] == 2.5
    # hook 前 12 字
    assert intro["title"] == "三秒钩子这是一段很长的开"
    assert len(intro["title"]) == 12
    assert intro["subtitle"] == ""


def test_manifest_intro_falls_back_to_publish_title_without_hook():
    manifest = build_effects_manifest(PLAN, {"publish_titles": ["候选标题一"]})
    assert manifest["effects"][0]["title"] == "候选标题一"


def test_manifest_intro_duration_capped_by_short_first_section():
    plan = {"sections": [{"title": "hook", "duration_seconds": 2.0}]}
    manifest = build_effects_manifest(plan, None)
    # min(2.5, 2.0*0.8=1.6) = 1.6
    assert manifest["effects"][0]["duration"] == 1.6


def test_manifest_chapter_cards_skip_first_section_and_start_at_cumulative():
    manifest = build_effects_manifest(PLAN, REWRITE)
    chapters = [e for e in manifest["effects"] if e["type"] == "chapter_card"]
    assert len(chapters) == 2  # 3 节，首节除外
    # 第二节起点 = 6.0；第三节起点 = 6.0 + 20.0 = 26.0
    assert chapters[0]["start"] == 6.0
    assert chapters[0]["index"] == 1
    assert chapters[0]["title"] == "第一节"
    assert chapters[0]["duration"] == 1.5
    assert chapters[1]["start"] == 26.0
    assert chapters[1]["title"] == "第二节"


def test_manifest_lower_thirds_optional_and_derived():
    manifest = build_effects_manifest(PLAN, REWRITE, include_lower_thirds=True)
    lowers = [e for e in manifest["effects"] if e["type"] == "lower_third"]
    assert len(lowers) == 2
    # 第一节：start = 6.0 + 1.0 = 7.0，duration = min(4, 20-1)=4
    assert lowers[0]["start"] == 7.0
    assert lowers[0]["duration"] == 4.0
    assert lowers[0]["text"] == "第一节"


def test_manifest_no_lower_thirds_by_default():
    manifest = build_effects_manifest(PLAN, REWRITE)
    assert all(e["type"] != "lower_third" for e in manifest["effects"])


def test_manifest_lower_third_omitted_for_short_section():
    # 节时长 ≤1s 时 duration = min(4, 1.0-1.0)=0，lower_third 必须被跳过而非产出 0 时长特效。
    plan = {
        "sections": [
            {"title": "hook", "duration_seconds": 5.0},
            {"title": "极短节", "duration_seconds": 1.0},
            {"title": "正常节", "duration_seconds": 10.0},
        ]
    }
    manifest = build_effects_manifest(plan, None, include_lower_thirds=True)
    lowers = [e for e in manifest["effects"] if e["type"] == "lower_third"]
    assert len(lowers) == 1  # 只有正常节
    assert lowers[0]["text"] == "正常节"
    assert all(e["duration"] > 0 for e in manifest["effects"])


def test_manifest_frame_rounding():
    # duration 3.0*0.8 = 2.4 -> 帧对齐后仍为整帧倍数。
    plan = {"sections": [{"title": "a", "duration_seconds": 3.0}, {"title": "b", "duration_seconds": 7.333}]}
    manifest = build_effects_manifest(plan, None)
    for e in manifest["effects"]:
        frames = e["start"] * DEFAULT_FPS
        assert abs(frames - round(frames)) < 1e-6
        dframes = e["duration"] * DEFAULT_FPS
        assert abs(dframes - round(dframes)) < 1e-6


def test_manifest_single_section_only_intro():
    plan = {"sections": [{"title": "唯一节", "duration_seconds": 5.0}]}
    manifest = build_effects_manifest(plan, None)
    assert len(manifest["effects"]) == 1
    assert manifest["effects"][0]["type"] == "intro"


def test_manifest_rejects_empty_sections():
    with pytest.raises(EffectsError, match="没有可用的分节"):
        build_effects_manifest({"sections": []}, None)


def test_manifest_skips_zero_duration_sections():
    plan = {
        "sections": [
            {"title": "hook", "duration_seconds": 5.0},
            {"title": "空节", "duration_seconds": 0.0},
            {"title": "真节", "duration_seconds": 8.0},
        ]
    }
    manifest = build_effects_manifest(plan, None)
    chapters = [e for e in manifest["effects"] if e["type"] == "chapter_card"]
    # 空节被过滤，只有 hook + 真节 → 真节作为第 1 章
    assert len(chapters) == 1
    assert chapters[0]["title"] == "真节"


def test_manifest_top_level_shape():
    manifest = build_effects_manifest(PLAN, REWRITE)
    assert manifest["version"] == "effects_manifest_v1"
    assert manifest["fps"] == 30
    assert manifest["width"] == 1920
    assert manifest["height"] == 1080


# --- render_effects -----------------------------------------------------


class _Recorder:
    """记录 npx 渲染命令并落占位 .mov 文件。"""

    def __init__(self, returncode=0):
        self.commands = []
        self.returncode = returncode

    def __call__(self, command, **kwargs):
        self.commands.append(command)
        out = Path(command[command.index("render") + 3]) if "render" in command else None
        if out is not None and self.returncode == 0:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(b"mov")
        return subprocess.CompletedProcess(command, self.returncode, stdout="", stderr="boom" * 100)


def test_render_effects_skips_gracefully_when_npx_missing(tmp_path, monkeypatch):
    monkeypatch.setattr("video_factory.effects.shutil.which", lambda _: None)
    manifest = build_effects_manifest(PLAN, REWRITE)
    runner = _Recorder()

    result = render_effects(manifest, tmp_path, runner=runner)

    assert result == []
    assert runner.commands == []  # 没有真跑 npx
    skipped = json.loads((tmp_path / "effects_skipped.json").read_text(encoding="utf-8"))
    assert skipped["skipped"] is True
    assert "npx" in skipped["reason"]
    # manifest 仍然写出。
    assert (tmp_path / "effects_manifest.json").exists()


def test_render_effects_builds_correct_remotion_command(tmp_path, monkeypatch):
    monkeypatch.setattr("video_factory.effects.shutil.which", lambda _: "/usr/bin/npx")
    manifest = build_effects_manifest(PLAN, REWRITE)
    runner = _Recorder()

    result = render_effects(manifest, tmp_path, runner=runner)

    assert len(result) == len(manifest["effects"])
    # 返回值带 manifest 原始索引
    assert [index for index, _ in result] == list(range(len(manifest["effects"])))
    first = runner.commands[0]
    assert first[0] == "/usr/bin/npx"
    assert first[1] == "remotion" and first[2] == "render"
    assert "Intro" in first  # 第一条是 intro composition
    assert any(a == "--codec=prores" for a in first)
    assert any(a == "--prores-profile=4444" for a in first)
    # 默认 manifest 是 1920x1080，render 命令必须带 --width/--height（CLI 覆盖画幅）
    assert "--width=1920" in first
    assert "--height=1080" in first
    # props 落文件传路径（防 npx.cmd → cmd.exe 重新解释元字符），文件里带 title，
    # 不带 type/start/duration
    props_arg = next(a for a in first if a.startswith("--props="))
    props_path = Path(props_arg[len("--props=") :])
    assert props_path.name == "effect_00.props.json"
    props = json.loads(props_path.read_text(encoding="utf-8"))
    assert "title" in props
    assert "type" not in props and "start" not in props and "duration" not in props
    # 输出文件命名 effect_00.mov
    assert (tmp_path / "effect_00.mov").exists()


def test_render_effects_props_file_keeps_cmd_metacharacters_safe(tmp_path, monkeypatch):
    monkeypatch.setattr("video_factory.effects.shutil.which", lambda _: "/usr/bin/npx")
    # 标题带 cmd.exe 元字符（真实标题形态："A&B联名"、"涨停|复盘"）
    plan = {"sections": [{"title": "A&B联名|复盘", "duration_seconds": 6.0}]}
    manifest = build_effects_manifest(plan, {"hook": "A&B联名|复盘要点"})
    runner = _Recorder()

    render_effects(manifest, tmp_path, runner=runner)

    command = runner.commands[0]
    # 命令行参数里不允许出现内联 JSON（元字符会被 cmd.exe 重新解释）
    props_arg = next(a for a in command if a.startswith("--props="))
    assert "&" not in props_arg and "|" not in props_arg and "{" not in props_arg
    # 元字符原样保存在 props 文件里
    props = json.loads(Path(props_arg[len("--props=") :]).read_text(encoding="utf-8"))
    assert props["title"] == "A&B联名|复盘要点"[:12]


def test_render_effects_passes_portrait_width_height_from_manifest(tmp_path, monkeypatch):
    monkeypatch.setattr("video_factory.effects.shutil.which", lambda _: "/usr/bin/npx")
    # 竖屏底片派生的 manifest（width/height 由 build_effects_manifest 落进 manifest）
    manifest = build_effects_manifest(PLAN, REWRITE, width=1080, height=1920)
    runner = _Recorder()

    render_effects(manifest, tmp_path, runner=runner)

    first = runner.commands[0]
    # render 命令必须把底片竖屏尺寸透传给 CLI，否则 Remotion 按默认 16:9 静默裁剪
    assert "--width=1080" in first
    assert "--height=1920" in first


class _FailAtRecorder(_Recorder):
    """只让指定序号的渲染失败，其余正常落盘。"""

    def __init__(self, fail_name: str):
        super().__init__(returncode=0)
        self.fail_name = fail_name

    def __call__(self, command, **kwargs):
        if any(self.fail_name in str(part) for part in command):
            self.commands.append(command)
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="boom")
        return super().__call__(command, **kwargs)


def test_render_effects_partial_failure_keeps_original_indices(tmp_path, monkeypatch):
    monkeypatch.setattr("video_factory.effects.shutil.which", lambda _: "/usr/bin/npx")
    manifest = build_effects_manifest(PLAN, REWRITE)
    # intro + 2 章节卡 + 开屏要点卡 + 金句卡（丰富化特效 2026-07 起默认全开）
    assert len(manifest["effects"]) == 5
    runner = _FailAtRecorder(fail_name="effect_01.mov")

    result = render_effects(manifest, tmp_path, runner=runner)

    # 中段失败后，成功项必须保留原始 manifest 索引（跳过 1），不能塌缩重排，
    # 否则叠加阶段会把后续特效错位到前一条的时间点。
    assert [index for index, _ in result] == [0, 2, 3, 4]
    assert result[0][1].name == "effect_00.mov"
    assert result[1][1].name == "effect_02.mov"
    warnings = json.loads((tmp_path / "effects_warnings.json").read_text(encoding="utf-8"))
    assert len(warnings["warnings"]) == 1 and "第 1 条" in warnings["warnings"][0]


def test_render_effects_output_path_is_absolute_for_relative_output_dir(tmp_path, monkeypatch):
    # 回归：npx remotion render 以 remotion/ 为 cwd 运行，输出路径必须是绝对路径，
    # 否则相对路径下 .mov 会被写到 remotion/ 下的错误位置、overlay 找不到。
    monkeypatch.setattr("video_factory.effects.shutil.which", lambda _: "/usr/bin/npx")
    monkeypatch.chdir(tmp_path)
    manifest = build_effects_manifest(PLAN, REWRITE)
    runner = _Recorder()

    result = render_effects(manifest, "out_rel", runner=runner)  # 相对输出目录

    assert result, "应有渲染产出"
    out_arg = runner.commands[0][5]  # 命令里 composition 后是输出 .mov 路径
    assert Path(out_arg).is_absolute(), f"输出路径必须绝对，实际：{out_arg}"
    # 返回给 overlay 的路径也应是绝对路径
    assert result[0][1].is_absolute()


def test_render_effects_collects_warnings_on_failure_and_continues(tmp_path, monkeypatch):
    monkeypatch.setattr("video_factory.effects.shutil.which", lambda _: "/usr/bin/npx")
    manifest = build_effects_manifest(PLAN, REWRITE)
    runner = _Recorder(returncode=1)

    result = render_effects(manifest, tmp_path, runner=runner)

    assert result == []  # 全失败
    warnings = json.loads((tmp_path / "effects_warnings.json").read_text(encoding="utf-8"))
    assert len(warnings["warnings"]) == len(manifest["effects"])
    assert "渲染失败" in warnings["warnings"][0]


# --- overlay_effects ----------------------------------------------------


class _OverlayRecorder:
    """伪 ffprobe/ffmpeg 跑法。

    ffprobe 现在有两种查询：format=duration（叠加窗口）与 stream=width,height（分辨率校验）。
    按 -show_entries 参数区分。分辨率默认让底片与每条特效一致（不触发跳过），
    个别用例可用 resolutions 映射（按路径 stem）指定不一致尺寸来测跳过。
    """

    def __init__(self, probe_duration=1.5, resolution=(1920, 1080), resolutions=None, channels=2):
        self.commands = []
        self.probe_duration = probe_duration
        self.resolution = resolution
        self.resolutions = resolutions or {}
        self.channels = channels  # 底片音轨声道数（0=无音轨，特效音会跳过）

    def _resolution_for(self, command):
        # 命令末位是被探测文件路径。
        stem = Path(command[-1]).stem
        return self.resolutions.get(stem, self.resolution)

    def __call__(self, command, **kwargs):
        self.commands.append(command)
        tool = Path(command[0]).stem.lower()
        if tool == "ffprobe":
            entries = ""
            if "-show_entries" in command:
                entries = command[command.index("-show_entries") + 1]
            if "stream=width,height" in entries:
                w, h = self._resolution_for(command)
                if w <= 0 or h <= 0:
                    return subprocess.CompletedProcess(
                        command, 0, stdout=json.dumps({"streams": []}), stderr=""
                    )
                payload = json.dumps({"streams": [{"width": w, "height": h}]})
                return subprocess.CompletedProcess(command, 0, stdout=payload, stderr="")
            if "stream=channels" in entries:
                streams = [{"channels": self.channels}] if self.channels > 0 else []
                return subprocess.CompletedProcess(
                    command, 0, stdout=json.dumps({"streams": streams}), stderr=""
                )
            payload = json.dumps({"format": {"duration": str(self.probe_duration)}})
            return subprocess.CompletedProcess(command, 0, stdout=payload, stderr="")
        Path(command[-1]).write_bytes(b"video")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")


def test_overlay_builds_cascaded_filter_with_enable_windows(tmp_path):
    base = tmp_path / "release.mp4"
    base.write_bytes(b"base")
    e0 = tmp_path / "effect_00.mov"
    e0.write_bytes(b"a")
    e1 = tmp_path / "effect_01.mov"
    e1.write_bytes(b"b")
    runner = _OverlayRecorder(probe_duration=2.0)

    out = overlay_effects(
        base,
        [{"path": e0, "start": 0.0}, {"path": e1, "start": 6.0}],
        tmp_path / "out.mp4",
        runner=runner,
    )

    assert out.exists()
    overlay_cmd = next(c for c in runner.commands if "-filter_complex" in c)
    fc = overlay_cmd[overlay_cmd.index("-filter_complex") + 1]
    # 每条特效先 setpts 平移到落点，再叠加（规避短片段 EOF repeat 淡出残影）。
    assert "[1:v]setpts=PTS+0.000/TB[e0]" in fc
    assert "[2:v]setpts=PTS+6.000/TB[e1]" in fc
    # 第一层 base + 平移后的 e0，enable 窗口 0..2，eof_action=pass 播完直通
    assert "[0:v][e0]overlay=0:0:enable='between(t,0.000,2.000)':eof_action=pass" in fc
    # 第二层级联 l0 + e1，窗口 6..8，末层输出 [out]
    assert "between(t,6.000,8.000)" in fc
    assert ":eof_action=pass[out]" in fc
    # 音轨 copy 直通
    assert "copy" in overlay_cmd
    assert "0:a?" in overlay_cmd


def test_overlay_probe_failure_records_warning(tmp_path):
    base = tmp_path / "release.mp4"
    base.write_bytes(b"base")
    clip = tmp_path / "effect_00.mov"
    clip.write_bytes(b"a")
    runner = _OverlayRecorder(probe_duration=0.0)  # 探测失败 → 时长 0

    overlay_effects(base, [{"path": clip, "start": 3.0}], tmp_path / "out.mp4", runner=runner)

    # 退化为长窗口的同时必须留痕，避免损坏片段"常驻画面"却无线索可查
    warnings = json.loads((tmp_path / "effects_warnings.json").read_text(encoding="utf-8"))
    assert any("探测失败" in w for w in warnings["warnings"])
    overlay_cmd = next(c for c in runner.commands if "-filter_complex" in c)
    fc = overlay_cmd[overlay_cmd.index("-filter_complex") + 1]
    assert "between(t,3.000,3603.000)" in fc  # start + 3600 长窗口


def test_overlay_skips_resolution_mismatched_effect_and_records_warning(tmp_path):
    # 底片 1080x1920（竖屏），effect_00 同尺寸放行，effect_01 是 1920x1080 必须跳过。
    base = tmp_path / "release.mp4"
    base.write_bytes(b"base")
    e0 = tmp_path / "effect_00.mov"
    e0.write_bytes(b"a")
    e1 = tmp_path / "effect_01.mov"
    e1.write_bytes(b"b")
    runner = _OverlayRecorder(
        probe_duration=2.0,
        resolution=(1080, 1920),
        resolutions={"effect_01": (1920, 1080)},  # 尺寸不匹配
    )

    overlay_effects(
        base,
        [{"path": e0, "start": 0.0}, {"path": e1, "start": 6.0}],
        tmp_path / "out.mp4",
        runner=runner,
    )

    # 只叠加了 effect_00：ffmpeg 只有 base + 1 个特效输入，filter 里没有第二层。
    overlay_cmd = next(c for c in runner.commands if "-filter_complex" in c)
    fc = overlay_cmd[overlay_cmd.index("-filter_complex") + 1]
    assert "[1:v]setpts=PTS+0.000/TB[e0]" in fc
    assert "[0:v][e0]overlay=0:0:enable='between(t,0.000,2.000)':eof_action=pass[out]" in fc
    assert "between(t,6.000" not in fc  # 第二条被跳过，不进 filter
    assert overlay_cmd.count("-i") == 2  # base + 1 特效
    # 跳过必须留痕（overlay 对尺寸不匹配零日志，无留痕就查不到）
    warnings = json.loads((tmp_path / "effects_warnings.json").read_text(encoding="utf-8"))
    assert any("不一致" in w and "已跳过" in w for w in warnings["warnings"])


def test_overlay_keeps_effect_when_resolution_probe_fails(tmp_path):
    # 底片尺寸能探到，但特效片段分辨率探测失败（0,0）→ 不阻断只留痕，仍叠加。
    base = tmp_path / "release.mp4"
    base.write_bytes(b"base")
    clip = tmp_path / "effect_00.mov"
    clip.write_bytes(b"a")
    runner = _OverlayRecorder(
        probe_duration=2.0,
        resolution=(1080, 1920),
        resolutions={"effect_00": (0, 0)},  # 探测失败
    )

    overlay_effects(base, [{"path": clip, "start": 0.0}], tmp_path / "out.mp4", runner=runner)

    overlay_cmd = next(c for c in runner.commands if "-filter_complex" in c)
    fc = overlay_cmd[overlay_cmd.index("-filter_complex") + 1]
    assert "[0:v][e0]overlay" in fc  # 仍叠加（平移后的 e0）
    warnings = json.loads((tmp_path / "effects_warnings.json").read_text(encoding="utf-8"))
    assert any("分辨率探测失败" in w for w in warnings["warnings"])


# ---------- 特效音混音 ----------

def _make_sfx_dir(tmp_path):
    d = tmp_path / "sfx"
    d.mkdir()
    for f in ("whoosh.wav", "pop.wav", "swoosh.wav"):
        (d / f).write_bytes(b"RIFFxxxxWAVE")
    return d


def test_overlay_mixes_sfx_at_effect_starts_when_enabled(tmp_path):
    base = tmp_path / "release.mp4"
    base.write_bytes(b"base")
    e0 = tmp_path / "effect_00.mov"
    e0.write_bytes(b"a")
    e1 = tmp_path / "effect_01.mov"
    e1.write_bytes(b"b")
    sfx_dir = _make_sfx_dir(tmp_path)
    runner = _OverlayRecorder(probe_duration=2.0, channels=2)

    overlay_effects(
        base,
        [
            {"path": e0, "start": 0.0, "type": "intro"},
            {"path": e1, "start": 6.0, "type": "chapter_card"},
        ],
        tmp_path / "out.mp4",
        runner=runner,
        sfx_enabled=True,
        sfx_volume=0.4,
        sfx_dir=sfx_dir,
    )

    cmd = next(c for c in runner.commands if "-filter_complex" in c)
    fc = cmd[cmd.index("-filter_complex") + 1]
    # 每个音效平移到各自特效的 start；normalize=0 保住人声、duration=first 锁定成片时长
    assert "adelay=0:all=1" in fc
    assert "adelay=6000:all=1" in fc
    assert "amix=inputs=3:duration=first:normalize=0" in fc
    assert "volume=0.400" in fc
    # 音轨重编码为 aac 并映射混音输出，不再直通 copy
    assert "aac" in cmd and "[aout]" in cmd
    assert "copy" not in cmd
    # 对应音效作为额外输入（intro→whoosh、chapter_card→pop）
    assert str(sfx_dir / "whoosh.wav") in cmd
    assert str(sfx_dir / "pop.wav") in cmd


def test_overlay_copies_audio_when_sfx_disabled(tmp_path):
    base = tmp_path / "release.mp4"
    base.write_bytes(b"base")
    e0 = tmp_path / "effect_00.mov"
    e0.write_bytes(b"a")
    runner = _OverlayRecorder(probe_duration=2.0)

    overlay_effects(
        base,
        [{"path": e0, "start": 0.0, "type": "intro"}],
        tmp_path / "out.mp4",
        runner=runner,
        sfx_enabled=False,
    )

    cmd = next(c for c in runner.commands if "-filter_complex" in c)
    assert "copy" in cmd
    fc = cmd[cmd.index("-filter_complex") + 1]
    assert "amix" not in fc


def test_overlay_skips_sfx_when_base_has_no_audio(tmp_path):
    base = tmp_path / "release.mp4"
    base.write_bytes(b"base")
    e0 = tmp_path / "effect_00.mov"
    e0.write_bytes(b"a")
    sfx_dir = _make_sfx_dir(tmp_path)
    runner = _OverlayRecorder(probe_duration=2.0, channels=0)  # 底片无音轨

    overlay_effects(
        base,
        [{"path": e0, "start": 0.0, "type": "intro"}],
        tmp_path / "out.mp4",
        runner=runner,
        sfx_enabled=True,
        sfx_dir=sfx_dir,
    )

    cmd = next(c for c in runner.commands if "-filter_complex" in c)
    assert "copy" in cmd  # 回退直通，不炸成片
    warnings = json.loads((tmp_path / "effects_warnings.json").read_text(encoding="utf-8"))
    assert any("没有音轨" in w for w in warnings["warnings"])


def test_overlay_skips_sfx_when_pack_missing(tmp_path):
    base = tmp_path / "release.mp4"
    base.write_bytes(b"base")
    e0 = tmp_path / "effect_00.mov"
    e0.write_bytes(b"a")
    empty = tmp_path / "empty_sfx"
    empty.mkdir()
    runner = _OverlayRecorder(probe_duration=2.0, channels=2)

    overlay_effects(
        base,
        [{"path": e0, "start": 0.0, "type": "intro"}],
        tmp_path / "out.mp4",
        runner=runner,
        sfx_enabled=True,
        sfx_dir=empty,
    )

    cmd = next(c for c in runner.commands if "-filter_complex" in c)
    assert "copy" in cmd
    warnings = json.loads((tmp_path / "effects_warnings.json").read_text(encoding="utf-8"))
    assert any("未找到任何音效文件" in w for w in warnings["warnings"])


# --- 底片分辨率探测（喂给 manifest） ------------------------------------


def test_resolve_video_dimensions_reads_portrait_resolution(tmp_path):
    video = tmp_path / "release.mp4"
    video.write_bytes(b"v")
    runner = _OverlayRecorder(resolution=(1080, 1920))

    width, height, warnings = _resolve_video_dimensions(video, runner=runner)

    assert (width, height) == (1080, 1920)
    assert warnings == []


def test_resolve_video_dimensions_falls_back_when_probe_fails(tmp_path):
    video = tmp_path / "release.mp4"
    video.write_bytes(b"v")
    runner = _OverlayRecorder(resolution=(0, 0))  # 探测失败

    width, height, warnings = _resolve_video_dimensions(video, runner=runner)

    assert (width, height) == (1920, 1080)  # 回落
    assert any("回落" in w and "探测失败" in w for w in warnings)


def test_resolve_video_dimensions_falls_back_when_video_missing(tmp_path):
    runner = _OverlayRecorder(resolution=(1080, 1920))

    width, height, warnings = _resolve_video_dimensions(tmp_path / "nope.mp4", runner=runner)

    assert (width, height) == (1920, 1080)  # 文件缺失也回落
    assert any("底片不存在" in w for w in warnings)


def test_manifest_carries_probed_dimensions_into_render(tmp_path, monkeypatch):
    # 端到端：底片竖屏 → main 探测 → manifest 竖屏尺寸 → render 命令带竖屏 --width/--height。
    monkeypatch.setattr("video_factory.effects.shutil.which", lambda _: "/usr/bin/npx")
    plan_path = tmp_path / "assembly_plan.json"
    plan_path.write_text(json.dumps(PLAN, ensure_ascii=False), encoding="utf-8")
    video = tmp_path / "release.mp4"
    video.write_bytes(b"v")
    monkeypatch.setattr(
        "video_factory.effects._probe_resolution", lambda p, r: (1080, 1920)
    )

    code = main([
        "--video", str(video),
        "--plan", str(plan_path),
        "--output", str(tmp_path / "out"),
        "--skip-render",
    ])

    assert code == 0
    manifest = json.loads(
        (tmp_path / "out" / "effects_manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["width"] == 1080 and manifest["height"] == 1920


def test_main_records_warning_when_video_resolution_probe_fails(tmp_path, monkeypatch):
    plan_path = tmp_path / "assembly_plan.json"
    plan_path.write_text(json.dumps(PLAN, ensure_ascii=False), encoding="utf-8")
    video = tmp_path / "release.mp4"
    video.write_bytes(b"v")
    monkeypatch.setattr("video_factory.effects._probe_resolution", lambda p, r: (0, 0))

    code = main([
        "--video", str(video),
        "--plan", str(plan_path),
        "--output", str(tmp_path / "out"),
        "--skip-render",
    ])

    assert code == 0
    manifest = json.loads(
        (tmp_path / "out" / "effects_manifest.json").read_text(encoding="utf-8")
    )
    # 探测失败回落 1920x1080
    assert manifest["width"] == 1920 and manifest["height"] == 1080
    warnings = json.loads(
        (tmp_path / "out" / "effects_warnings.json").read_text(encoding="utf-8")
    )
    assert any("探测失败" in w and "回落" in w for w in warnings["warnings"])


def test_overlay_rejects_missing_base(tmp_path):
    with pytest.raises(EffectsError, match="原片不存在"):
        overlay_effects(tmp_path / "nope.mp4", [{"path": tmp_path / "x.mov", "start": 0}], tmp_path / "o.mp4", runner=_OverlayRecorder())


def test_overlay_rejects_empty_effects(tmp_path):
    base = tmp_path / "release.mp4"
    base.write_bytes(b"base")
    with pytest.raises(EffectsError, match="没有可叠加"):
        overlay_effects(base, [], tmp_path / "o.mp4", runner=_OverlayRecorder())


# --- CLI ----------------------------------------------------------------


def test_cli_skip_render_only_writes_manifest(tmp_path, capsys):
    plan_path = tmp_path / "assembly_plan.json"
    plan_path.write_text(json.dumps(PLAN, ensure_ascii=False), encoding="utf-8")
    video = tmp_path / "release.mp4"
    video.write_bytes(b"v")

    code = main([
        "--video", str(video),
        "--plan", str(plan_path),
        "--output", str(tmp_path / "out"),
        "--skip-render",
    ])

    assert code == 0
    out = capsys.readouterr().out
    assert "特效清单已生成" in out
    assert (tmp_path / "out" / "effects_manifest.json").exists()


def test_cli_missing_plan_returns_chinese_error_and_exit_1(tmp_path, capsys):
    video = tmp_path / "release.mp4"
    video.write_bytes(b"v")
    code = main([
        "--video", str(video),
        "--plan", str(tmp_path / "missing.json"),
        "--skip-render",
    ])
    assert code == 1
    out = capsys.readouterr().out
    assert "特效层失败" in out and "assembly_plan.json不存在" in out


def test_cli_requires_video_and_plan(capsys):
    with pytest.raises(SystemExit):
        main(["--plan", "x.json"])  # 缺 --video
    err = capsys.readouterr().err
    assert "--video" in err


def test_cli_missing_video_returns_chinese_error(tmp_path, capsys, monkeypatch):
    plan_path = tmp_path / "assembly_plan.json"
    plan_path.write_text(json.dumps(PLAN, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr("video_factory.effects.shutil.which", lambda _: None)

    code = main([
        "--video", str(tmp_path / "nope.mp4"),
        "--plan", str(plan_path),
        "--output", str(tmp_path / "out"),
    ])
    assert code == 1
    out = capsys.readouterr().out
    assert "特效层失败" in out and "原片不存在" in out


# ---------- 丰富化特效：开屏要点卡 / 金句卡 / 数字强调 ----------

def test_manifest_key_points_lists_section_titles_after_intro():
    manifest = build_effects_manifest(PLAN, REWRITE)
    kp = next(e for e in manifest["effects"] if e["type"] == "key_points")
    # 第 0 节是 hook（片头已覆盖），要点行从正题标题取起
    assert kp["lines"] == ["第一节", "第二节"]
    intro = manifest["effects"][0]
    assert kp["start"] == pytest.approx(intro["duration"], abs=1 / 30)
    assert kp["duration"] <= 3.5


def test_manifest_quote_card_uses_first_publish_title_and_avoids_chapter_cards():
    manifest = build_effects_manifest(PLAN, REWRITE)
    quote = next(e for e in manifest["effects"] if e["type"] == "quote_card")
    assert quote["text"] == "候选标题一"
    total = 36.0
    # 不撞任何章节卡窗口：与每张卡的 [start, start+1.5] 无重叠
    cards = [e for e in manifest["effects"] if e["type"] == "chapter_card"]
    for card in cards:
        assert not (card["start"] - quote["duration"] < quote["start"] < card["start"] + 1.5)
    assert quote["start"] + quote["duration"] < total


def test_manifest_number_pop_extracts_key_number_max_two():
    plan = {
        "sections": [
            {"index": 0, "title": "hook", "duration_seconds": 6.0},
            {"index": 1, "title": "一", "duration_seconds": 20.0},
            {"index": 2, "title": "二", "duration_seconds": 20.0},
            {"index": 3, "title": "三", "duration_seconds": 20.0},
        ]
    }
    # 计划把 hook 计为第 0 节：rewrite 第 i-1 节对应计划第 i 节。
    rewrite = {
        "hook": "开场",
        "sections": [
            {"narration": "只需3步就能救回来"},
            {"narration": "读写速度会掉50%左右"},
            {"narration": "再来10个也不怕"},  # 第 3 个数字：超过上限不再取
        ],
    }
    manifest = build_effects_manifest(plan, rewrite)
    pops = [e for e in manifest["effects"] if e["type"] == "number_pop"]
    assert [p["value"] for p in pops] == ["3步", "50%"]  # 最多 2 个、顺序取
    assert pops[0]["start"] == pytest.approx(6.0 + 1.0, abs=1 / 30)  # 首个正题节起点 + 1s


def test_manifest_number_pop_reads_rewrite_sections_not_plan(monkeypatch):
    # rewrite 缺失 → 无 number_pop / quote_card（数字与金句都来自 rewrite）
    manifest = build_effects_manifest(PLAN, None)
    types = [e["type"] for e in manifest["effects"]]
    assert "number_pop" not in types
    assert "quote_card" not in types
    assert "key_points" in types  # 要点卡只依赖 plan 的节标题，仍在


def test_composition_map_covers_new_types():
    from video_factory.effects import _COMPOSITION_BY_TYPE

    assert _COMPOSITION_BY_TYPE["key_points"] == "KeyPoints"
    assert _COMPOSITION_BY_TYPE["quote_card"] == "QuoteCard"
    assert _COMPOSITION_BY_TYPE["number_pop"] == "NumberPop"


def test_sfx_mapping_covers_new_types():
    from video_factory.sfx import SFX_BY_TYPE

    for t in ("key_points", "quote_card", "number_pop"):
        assert t in SFX_BY_TYPE
