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
    # REWRITE 有 publish_titles → 冷开场卡排在最前，intro 紧接其结束硬切（无交叠淡入）。
    assert manifest["effects"][0]["type"] == "opening_card"
    intro = next(e for e in manifest["effects"] if e["type"] == "intro")
    assert intro["start"] == 1.2
    # min(2.5, 6.0*0.8=4.8) = 2.5
    assert intro["duration"] == 2.5
    # hook 前 12 字
    assert intro["title"] == "三秒钩子这是一段很长的开"
    assert len(intro["title"]) == 12
    assert intro["subtitle"] == ""


def test_manifest_intro_falls_back_to_publish_title_without_hook():
    manifest = build_effects_manifest(PLAN, {"publish_titles": ["候选标题一"]})
    intro = next(e for e in manifest["effects"] if e["type"] == "intro")
    assert intro["title"] == "候选标题一"


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
    assert "OpeningCard" in first  # 第一条是冷开场卡 composition（排在 intro 之前）
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
    # 冷开场卡 + intro + 2 章节卡 + 开屏要点卡 + 金句卡 + 2 关键词弹出 = 8
    assert len(manifest["effects"]) == 8
    runner = _FailAtRecorder(fail_name="effect_01.mov")

    result = render_effects(manifest, tmp_path, runner=runner)

    # 中段失败后，成功项必须保留原始 manifest 索引（跳过 1），不能塌缩重排，
    # 否则叠加阶段会把后续特效错位到前一条的时间点。
    expected = [i for i in range(len(manifest["effects"])) if i != 1]
    assert [index for index, _ in result] == expected
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
    for f in ("whoosh.wav", "pop.wav", "swoosh.wav", "impact.wav"):  # transition 已移除
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
    # 要点卡紧接 intro 结束落点（intro 后移到 1.0 后，要点卡也随之顺延到 intro 终点）。
    intro = next(e for e in manifest["effects"] if e["type"] == "intro")
    assert kp["start"] == pytest.approx(intro["start"] + intro["duration"], abs=1 / 30)
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


# ---------- 片头标题截断（防悬挂残字，2026-07-14 红框反馈） ----------

def test_clip_title_cuts_at_sentence_end_keeps_punct():
    from video_factory.effects import _clip_title

    # 用户实际案例：[:12] 硬截会得到「王阳明心学到底有多牛？一」——句尾挂着"一句话"的首字
    hook = "王阳明心学到底有多牛？一句话就能让你摆脱内耗，今天给你讲透。"
    assert _clip_title(hook, 12) == "王阳明心学到底有多牛？"  # 句末？保留、残字丢弃


def test_clip_title_cuts_before_clause_punct():
    from video_factory.effects import _clip_title

    assert _clip_title("低杠杆保现金流，多元化收入才是出路", 9) == "低杠杆保现金流"  # 逗号本身丢弃


def test_clip_title_short_text_unchanged_and_hard_cut_fallback():
    from video_factory.effects import _clip_title

    assert _clip_title("心外无物", 12) == "心外无物"          # 预算内原样
    assert _clip_title("零标点连续十四个字的超长标题啊", 8) == "零标点连续十四个"  # 无标点硬截


def test_intro_title_uses_clipped_hook():
    from video_factory.effects import _intro_title

    rewrite = {"hook": "王阳明心学到底有多牛？一句话就能让你摆脱内耗"}
    assert _intro_title(rewrite) == "王阳明心学到底有多牛？"


# ---------- 冷开场卡 / 关键词弹出（Task C） ----------

def test_manifest_opening_card_first_and_shifts_intro():
    manifest = build_effects_manifest(PLAN, REWRITE)
    opening = manifest["effects"][0]
    assert opening["type"] == "opening_card"
    assert opening["start"] == 0.0
    assert opening["duration"] == 1.2
    assert opening["title"] == "候选标题一"          # publish_titles[0] 截 8 字
    assert opening["points"] == ["第一节", "第二节"]  # 第 1、2 节标题
    intro = next(e for e in manifest["effects"] if e["type"] == "intro")
    assert intro["start"] == 1.2                     # 开场卡结束即硬切进 intro


def test_manifest_no_opening_card_without_publish_titles():
    # 无 publish_titles[0] 主题词 → 不出开场卡，intro 仍从 0 起（底片时间轴不变）。
    manifest = build_effects_manifest(PLAN, {"hook": "只有钩子没有标题"})
    assert all(e["type"] != "opening_card" for e in manifest["effects"])
    intro = next(e for e in manifest["effects"] if e["type"] == "intro")
    assert intro["start"] == 0.0


def test_manifest_opening_card_title_clipped_to_eight():
    rewrite = dict(REWRITE, publish_titles=["王阳明心学到底有多牛一句话讲透"])
    manifest = build_effects_manifest(PLAN, rewrite)
    opening = manifest["effects"][0]
    assert opening["type"] == "opening_card"
    assert len(opening["title"]) <= 8


def test_manifest_keyword_pop_per_section_skips_first():
    plan = {
        "sections": [
            {"index": 0, "title": "hook", "duration_seconds": 6.0},
            {"index": 1, "title": "第一节", "duration_seconds": 10.0},
            {"index": 2, "title": "第二节", "duration_seconds": 10.0},
        ]
    }
    rewrite = {
        "publish_titles": ["主题词候选"],
        "sections": [
            {"narration": "重点是「反脆弱」思维"},
            {"narration": "再讲一个数字3步法"},
        ],
    }
    manifest = build_effects_manifest(plan, rewrite)
    kws = [e for e in manifest["effects"] if e["type"] == "keyword_pop"]
    # 第 0 节 hook 跳过；第 1 节引号词 > 第 2 节数字短语
    assert [k["keyword"] for k in kws] == ["反脆弱", "3步"]
    # 第 1 节起点 6.0 + 40%*10 = 10.0
    assert kws[0]["start"] == pytest.approx(6.0 + 10.0 * 0.4, abs=1 / 30)
    assert kws[0]["duration"] == 1.6


def test_manifest_keyword_pop_falls_back_to_title():
    # 无引号/数字 → 取节标题头 6 字兜底；无 rewrite 也能从 plan 标题产出关键词。
    plan = {
        "sections": [
            {"index": 0, "title": "hook", "duration_seconds": 6.0},
            {"index": 1, "title": "低杠杆保现金流才是出路", "duration_seconds": 10.0},
        ]
    }
    manifest = build_effects_manifest(plan, None)
    kws = [e for e in manifest["effects"] if e["type"] == "keyword_pop"]
    assert [k["keyword"] for k in kws] == ["低杠杆保现金"]


def test_extract_keyword_priority():
    from video_factory.effects import _extract_keyword

    assert _extract_keyword("他说「断舍离」是关键", "标题") == "断舍离"          # 引号优先
    assert _extract_keyword("涨了50%不止", "标题") == "50%"                     # 数字次之
    assert _extract_keyword("没有引号也没数字", "低杠杆保现金流") == "低杠杆保现金"  # 头 6 字兜底
    assert _extract_keyword("", "") == ""                                       # 全空 → 空串（跳过）


def test_composition_map_covers_keyword_opening_and_golden():
    from video_factory.effects import _COMPOSITION_BY_TYPE

    assert _COMPOSITION_BY_TYPE["keyword_pop"] == "KeywordPop"
    assert _COMPOSITION_BY_TYPE["opening_card"] == "OpeningCard"
    assert _COMPOSITION_BY_TYPE["golden_card"] == "GoldenCard"


def test_sfx_mapping_covers_keyword_opening_golden():
    from video_factory.sfx import SFX_BY_TYPE

    # keyword_pop、开场卡、金句卡均有 SFX 映射。
    for t in ("keyword_pop", "opening_card", "golden_card"):
        assert t in SFX_BY_TYPE
    # 转场音效已取消（2026-07-15 用户点名），不再注入。
    assert "transition" not in SFX_BY_TYPE


# ---------- 转场特效音（Task B）：转场点混入 whoosh ----------

def test_overlay_does_not_mix_transition_sfx(tmp_path):
    # 转场音效已取消（2026-07-15 用户点名）：overlay_effects 不再有 transition_points 参数，
    # 即使 sfx_enabled=True 也不会把 transition.wav 混入音轨——不再注入。
    import inspect
    sig = inspect.signature(overlay_effects)
    assert "transition_points" not in sig.parameters  # 参数已删除，不再接受

    base = tmp_path / "release.mp4"
    base.write_bytes(b"base")
    e0 = tmp_path / "effect_00.mov"
    e0.write_bytes(b"a")
    sfx_dir = _make_sfx_dir(tmp_path)
    runner = _OverlayRecorder(probe_duration=2.0, channels=2)

    overlay_effects(
        base,
        [{"path": e0, "start": 0.0, "type": "intro"}],
        tmp_path / "out.mp4",
        runner=runner,
        sfx_enabled=True,
        sfx_dir=sfx_dir,
    )

    cmd = next(c for c in runner.commands if "-filter_complex" in c)
    fc = cmd[cmd.index("-filter_complex") + 1]
    # 只有 intro 特效音（底片 + intro = inputs=2），无转场 whoosh。
    assert "amix=inputs=2:duration=first:normalize=0" in fc
    # transition.wav 不再出现在命令行中——不再注入。
    assert str(sfx_dir / "transition.wav") not in " ".join(str(x) for x in cmd)


def test_overlay_sfx_without_transition_even_with_plan_points(tmp_path):
    # assembly_plan 中有 transition_points 字段也与 overlay_effects 无关；
    # 特效音仅按各特效类型映射音效，不再为转场点注入 transition.wav。
    base = tmp_path / "release.mp4"
    base.write_bytes(b"base")
    e0 = tmp_path / "effect_00.mov"
    e0.write_bytes(b"a")
    sfx_dir = _make_sfx_dir(tmp_path)
    runner = _OverlayRecorder(probe_duration=2.0, channels=2)

    overlay_effects(
        base,
        [{"path": e0, "start": 0.0, "type": "intro"}],
        tmp_path / "out.mp4",
        runner=runner,
        sfx_enabled=True,
        sfx_dir=sfx_dir,
    )

    cmd = next(c for c in runner.commands if "-filter_complex" in c)
    fc = cmd[cmd.index("-filter_complex") + 1]
    # 只有 intro 特效音（底片 + intro = inputs=2），无转场音注入。
    assert "amix=inputs=2:duration=first:normalize=0" in fc
    assert str(sfx_dir / "transition.wav") not in " ".join(str(x) for x in cmd)


# ============================================================
# 任务A：LLM 强调计划 - emphasis 贯穿动效 + 密度控制 + 三色轮换
# ============================================================

from video_factory.effects import (  # noqa: E402
    DENSITY_MIN_GAP_S,
    DENSITY_VACUUM_S,
    GOLDEN_CARD_DURATION,
    GOLDEN_CARD_MIN_GAP_S,
    KEYWORD_POP_COLORS,
    _apply_density_control,
    _apply_golden_density_control,
    _derive_golden_events,
    _derive_keyword_events,
)


# ---- 辅助工具 ----

def _make_section(title: str, duration_seconds: float) -> dict:
    return {"title": title, "duration_seconds": duration_seconds}


def _make_rewrite_section(narration: str, emphasis=None) -> dict:
    result = {"title": "标题", "narration": narration, "visual_hint": ""}
    if emphasis is not None:
        result["emphasis"] = emphasis
    return result


def _starts(durations: list[float]) -> list[float]:
    out, cursor = [], 0.0
    for d in durations:
        out.append(cursor)
        cursor += d
    return out


# ---- _derive_keyword_events ----

def test_derive_keyword_events_uses_emphasis_when_present():
    """有 emphasis 时按均匀分布取落点（不走规则抽取）；golden 类条目不进 keyword_pop。"""
    sections = [
        _make_section("hook", 10.0),
        _make_section("第一节", 30.0),
    ]
    rw_sections = [
        _make_rewrite_section(
            "这节口播文案",
            emphasis=[
                {"text": "核心词A", "kind": "keyword"},
                {"text": "50%收益", "kind": "number"},
                {"text": "持续就是力量", "kind": "golden"},  # golden 不进 keyword_pop
            ],
        )
    ]
    starts = _starts([10.0, 30.0])

    events = _derive_keyword_events(sections, rw_sections, starts)

    # golden 被过滤，只剩 keyword + number 两条
    assert len(events) == 2
    texts = [e[1] for e in events]
    assert texts == ["核心词A", "50%收益"]
    # 均匀分布（n=2）：1/(2+1)*30=10, 2/(2+1)*30=20，加 section_start=10
    times = [e[0] for e in events]
    assert abs(times[0] - (10 + 10.0)) < 0.01
    assert abs(times[1] - (10 + 20.0)) < 0.01


def test_derive_keyword_events_falls_back_to_rule_without_emphasis():
    """无 emphasis 时回落规则抽取：节内 40% 处，行为与改版前完全一致。"""
    sections = [
        _make_section("hook", 6.0),
        _make_section("第一节", 20.0),
    ]
    # rewrite 有对应节但无 emphasis
    rw_sections = [_make_rewrite_section("带「核心操作」的口播")]
    starts = _starts([6.0, 20.0])

    events = _derive_keyword_events(sections, rw_sections, starts)

    assert len(events) == 1
    assert events[0][1] == "核心操作"  # 「」引号内词
    # 落点：6 + 20*0.4 = 14
    assert abs(events[0][0] - 14.0) < 0.01


def test_derive_keyword_events_skips_hook_section():
    """第 0 节（hook）始终跳过，不生成 keyword_pop。"""
    sections = [_make_section("hook", 10.0)]
    events = _derive_keyword_events(sections, [], _starts([10.0]))
    assert events == []


def test_derive_keyword_events_uses_title_fallback_for_empty_narration():
    """空口播 + 无 emphasis 时，_section_title 兜底标题作关键词（永不返回空）。"""
    sections = [
        _make_section("hook", 5.0),
        _make_section("核心节", 10.0),
    ]
    rw_sections = [_make_rewrite_section("")]  # 空口播，无引号/数字
    events = _derive_keyword_events(sections, rw_sections, _starts([5.0, 10.0]))
    # 空口播 → 回落节标题 "核心节"[:6]
    assert len(events) == 1
    assert events[0][1] == "核心节"


# ---- _apply_density_control ----

def test_density_thinning_removes_events_closer_than_min_gap():
    """两个事件间隔 < DENSITY_MIN_GAP_S 时，后出现的被丢弃（抽稀）。"""
    gap = DENSITY_MIN_GAP_S - 1.0  # 比最小间隔小 1s
    events = [(10.0, "词A"), (10.0 + gap, "词B"), (30.0, "词C")]
    sections = [_make_section("hook", 5.0), _make_section("节", 35.0)]
    starts = _starts([5.0, 35.0])

    result = _apply_density_control(events, sections, [], starts)

    times = [e[0] for e in result]
    # 词B 太近被丢，词A 和 词C 保留
    assert 10.0 in times
    assert 30.0 in times
    assert 10.0 + gap not in times


def test_density_thinning_keeps_events_at_exactly_min_gap():
    """间隔恰好等于 DENSITY_MIN_GAP_S 时，两者都保留（边界：>= 不是 >）。"""
    events = [(10.0, "词A"), (10.0 + DENSITY_MIN_GAP_S, "词B")]
    sections = [_make_section("hook", 5.0), _make_section("节", 25.0)]
    starts = _starts([5.0, 25.0])

    result = _apply_density_control(events, sections, [], starts)

    assert len(result) == 2


def test_density_vacuum_fill_adds_event_in_large_gap():
    """相邻两事件间隔 > DENSITY_VACUUM_S 时，在中点插入规则抽取的 keyword_pop。"""
    # 第一节事件在 10s，第二节事件在 10 + DENSITY_VACUUM_S + 5 = 35s
    gap_end = 10.0 + DENSITY_VACUUM_S + 5.0
    events = [(10.0, "词A"), (gap_end, "词B")]
    # 三节：hook 5s，节1 20s，节2 15s
    sections = [
        _make_section("hook", 5.0),
        _make_section("第一节", 20.0),
        _make_section("第二节", 15.0),
    ]
    rw_sections = [
        _make_rewrite_section("「关键词X」的实操方法"),
        _make_rewrite_section("「关键词Y」的进阶技巧"),
    ]
    starts = _starts([5.0, 20.0, 15.0])

    result = _apply_density_control(events, sections, rw_sections, starts)

    # 应有补填事件（总数 > 2）
    assert len(result) > 2
    # 补填事件时间在 (10, gap_end) 区间内
    filled_times = [e[0] for e in result if e[0] not in (10.0, gap_end)]
    assert len(filled_times) >= 1
    assert all(10.0 < t < gap_end for t in filled_times)


def test_density_no_fill_for_gap_at_or_below_threshold():
    """间隔恰好等于 DENSITY_VACUUM_S 时不触发补填（> 不是 >=）。"""
    events = [(5.0, "词A"), (5.0 + DENSITY_VACUUM_S, "词B")]
    sections = [_make_section("hook", 3.0), _make_section("节", 30.0)]
    starts = _starts([3.0, 30.0])

    result = _apply_density_control(events, sections, [], starts)

    assert len(result) == 2  # 无补填


def test_density_control_returns_empty_for_empty_input():
    """空输入直接返回空（不崩溃）。"""
    sections = [_make_section("hook", 5.0)]
    result = _apply_density_control([], sections, [], _starts([5.0]))
    assert result == []


# ---- 三色轮换 ----

def test_keyword_pop_color_cycles_red_yellow_white():
    """keyword_pop 三色按全片动效序号轮换：红→黄→白→红→……"""
    # 3节（hook + 2内容节），无 emphasis → 规则抽取每节一个 keyword_pop
    plan = {
        "sections": [
            {"index": 0, "title": "hook", "duration_seconds": 5.0, "slices": []},
            {"index": 1, "title": "节一", "duration_seconds": 30.0, "slices": []},
            {"index": 2, "title": "节二", "duration_seconds": 30.0, "slices": []},
            {"index": 3, "title": "节三", "duration_seconds": 30.0, "slices": []},
        ]
    }
    manifest = build_effects_manifest(plan, None)
    kw_pops = [e for e in manifest["effects"] if e["type"] == "keyword_pop"]

    # 每节规则抽取一个 keyword_pop（3 节，跳过 hook）
    assert len(kw_pops) == 3
    assert kw_pops[0]["color"] == KEYWORD_POP_COLORS[0]  # 红
    assert kw_pops[1]["color"] == KEYWORD_POP_COLORS[1]  # 黄
    assert kw_pops[2]["color"] == KEYWORD_POP_COLORS[2]  # 白


def test_keyword_pop_color_wraps_around_at_3():
    """4 个 keyword_pop 时第 4 个颜色回到红（轮换周期=3）。"""
    plan = {
        "sections": [
            {"index": 0, "title": "hook", "duration_seconds": 5.0, "slices": []},
            {"index": 1, "title": "节一", "duration_seconds": 30.0, "slices": []},
            {"index": 2, "title": "节二", "duration_seconds": 30.0, "slices": []},
            {"index": 3, "title": "节三", "duration_seconds": 30.0, "slices": []},
            {"index": 4, "title": "节四", "duration_seconds": 30.0, "slices": []},
        ]
    }
    manifest = build_effects_manifest(plan, None)
    kw_pops = [e for e in manifest["effects"] if e["type"] == "keyword_pop"]

    assert len(kw_pops) == 4
    # 第 4 个（index=3）= 3 % 3 = 0 → 红
    assert kw_pops[3]["color"] == KEYWORD_POP_COLORS[0]


# ---- 向后兼容：无 emphasis 时 manifest 总条数不变 ----

def test_manifest_count_unchanged_without_emphasis():
    """无 emphasis 时 manifest 总条数与重构前完全一致（8 条）。"""
    # 与 test_render_effects_partial_failure_keeps_original_indices 用同一 PLAN+REWRITE
    manifest = build_effects_manifest(PLAN, REWRITE)
    kw_pops = [e for e in manifest["effects"] if e["type"] == "keyword_pop"]
    # 2 节内容节（hook 跳过）→ 2 个 keyword_pop
    assert len(kw_pops) == 2
    # 总条数不变
    assert len(manifest["effects"]) == 8


def test_manifest_keyword_pop_with_emphasis_uses_distributed_positions():
    """有 emphasis 时 keyword_pop 落点来自 emphasis 均匀分布（不走 40% 规则）；
    golden 类条目不进 keyword_pop，单独派生 golden_card。
    两类 emphasis 放在不同节以避免时刻重叠导致 keyword_pop 被过滤。"""
    plan = {
        "sections": [
            {"index": 0, "title": "hook", "duration_seconds": 5.0, "slices": []},
            {"index": 1, "title": "节一", "duration_seconds": 40.0, "slices": []},  # 5-45s
            {"index": 2, "title": "节二", "duration_seconds": 40.0, "slices": []},  # 45-85s
        ]
    }
    rewrite_with_emphasis = {
        "hook": "钩子",
        "sections": [
            {
                "title": "节一",
                "narration": "这节口播文案",
                "visual_hint": "",
                "emphasis": [
                    {"text": "关键词X", "kind": "keyword"},  # keyword_pop 在节一
                ],
            },
            {
                "title": "节二",
                "narration": "",
                "visual_hint": "",
                "emphasis": [
                    {"text": "关键词Y", "kind": "golden"},   # golden_card 在节二（间距>20s）
                ],
            }
        ],
        "publish_titles": [],
        "notes": "",
    }
    manifest = build_effects_manifest(plan, rewrite_with_emphasis)
    kw_pops = [e for e in manifest["effects"] if e["type"] == "keyword_pop"]
    golden_cards = [e for e in manifest["effects"] if e["type"] == "golden_card"]

    # keyword 类（节一）应存在；golden_card 由节二的 "关键词Y" 派生
    assert len(kw_pops) >= 1
    assert any(k["keyword"] == "关键词X" for k in kw_pops)
    # golden_card 包含 "关键词Y"
    assert len(golden_cards) == 1
    assert golden_cards[0]["text"] == "关键词Y"


# ============================================================
# 任务A：golden_card —— 派生、密度控制、撞窗、keyword_pop 过滤
# ============================================================

# ---- _derive_golden_events ----

def test_derive_golden_events_collects_golden_emphasis():
    """kind=golden 的 emphasis 按均匀分布产生 golden_card 落点。"""
    sections = [
        _make_section("hook", 10.0),
        _make_section("第一节", 30.0),
    ]
    rw_sections = [
        _make_rewrite_section(
            "这节口播",
            emphasis=[
                {"text": "核心词A", "kind": "keyword"},   # 不进 golden
                {"text": "持续就是力量", "kind": "golden"},
            ],
        )
    ]
    starts = _starts([10.0, 30.0])

    events = _derive_golden_events(sections, rw_sections, starts)

    assert len(events) == 1
    assert events[0][1] == "持续就是力量"
    # n=1: offset = 1/(1+1)*30 = 15, time = 10+15 = 25
    assert abs(events[0][0] - 25.0) < 0.01


def test_derive_golden_events_skips_hook_section():
    """第 0 节（hook）跳过，与 keyword_events 同逻辑。"""
    sections = [_make_section("hook", 10.0)]
    rw_sections = [_make_rewrite_section("hook文案", emphasis=[{"text": "金句", "kind": "golden"}])]
    events = _derive_golden_events(sections, rw_sections, _starts([10.0]))
    assert events == []


def test_derive_golden_events_ignores_keyword_number():
    """keyword/number 不进 golden events。"""
    sections = [
        _make_section("hook", 5.0),
        _make_section("节1", 20.0),
    ]
    rw_sections = [
        _make_rewrite_section("", emphasis=[
            {"text": "词A", "kind": "keyword"},
            {"text": "50%", "kind": "number"},
        ])
    ]
    events = _derive_golden_events(sections, rw_sections, _starts([5.0, 20.0]))
    assert events == []


def test_derive_golden_events_multiple_golden_per_section():
    """单节内多条 golden 均匀分布，最多取 3 条。"""
    sections = [
        _make_section("hook", 5.0),
        _make_section("节1", 60.0),
    ]
    rw_sections = [
        _make_rewrite_section("", emphasis=[
            {"text": "金句A", "kind": "golden"},
            {"text": "金句B", "kind": "golden"},
        ])
    ]
    events = _derive_golden_events(sections, rw_sections, _starts([5.0, 60.0]))
    assert len(events) == 2
    # n=2: 1/(2+1)*60=20→time=25, 2/(2+1)*60=40→time=45
    assert abs(events[0][0] - 25.0) < 0.01
    assert abs(events[1][0] - 45.0) < 0.01


# ---- _apply_golden_density_control ----

def test_apply_golden_density_min_gap_drops_close_event():
    """相邻 golden_card 间隔 < GOLDEN_CARD_MIN_GAP_S 时丢弃后出现的事件。"""
    events = [(10.0, "A"), (15.0, "B"), (40.0, "C")]
    result = _apply_golden_density_control(events, [], 100.0)
    # 10→A 保留；15-10=5s < 20s → 丢弃 B；40-10=30s >= 20s → 保留 C
    assert len(result) == 2
    assert result[0][1] == "A"
    assert result[1][1] == "C"


def test_apply_golden_density_exact_min_gap_kept():
    """恰好等于 GOLDEN_CARD_MIN_GAP_S 的间隔应保留（>=，非 >）。"""
    events = [(10.0, "A"), (10.0 + GOLDEN_CARD_MIN_GAP_S, "B")]
    result = _apply_golden_density_control(events, [], 100.0)
    assert len(result) == 2


def test_apply_golden_density_window_collision_still_collides_discards():
    """撞保护窗口 → 顺延 0.5s 后仍撞则丢弃。"""
    # 保护窗口 [10.0, 13.0]；候选时刻 11.0
    # golden_card [11.0, 13.4] 与 [10.0, 13.0] 重叠 → 顺延到 11.5
    # [11.5, 13.9] 仍与 [10.0, 13.0] 重叠 → 丢弃
    events = [(11.0, "金句")]
    result = _apply_golden_density_control(events, [(10.0, 13.0)], 100.0)
    assert result == []


def test_apply_golden_density_window_collision_delay_succeeds():
    """撞保护窗口 → 顺延 0.5s 后不再撞则保留，起始时刻更新。"""
    # 保护窗口 [10.0, 11.0]；候选时刻 10.5
    # golden_card [10.5, 12.9] 与 [10.0, 11.0] 重叠 → 顺延到 11.0
    # golden_card [11.0, 13.4]：11.0 < 11.0 为 False → 不重叠 → 保留
    events = [(10.5, "金句")]
    result = _apply_golden_density_control(events, [(10.0, 11.0)], 100.0)
    assert len(result) == 1
    assert abs(result[0][0] - 11.0) < 0.01
    assert result[0][1] == "金句"


def test_apply_golden_density_empty_input():
    """空输入直接返回空（不崩溃）。"""
    assert _apply_golden_density_control([], [], 100.0) == []


# ---- build_effects_manifest golden_card 集成 ----

def test_build_manifest_golden_goes_to_golden_card_not_keyword_pop():
    """build_effects_manifest：golden emphasis 生成 golden_card，不进 keyword_pop。"""
    plan = {
        "sections": [
            {"index": 0, "title": "hook", "duration_seconds": 10.0},
            {"index": 1, "title": "节1", "duration_seconds": 60.0},
        ]
    }
    rewrite = {
        "hook": "开场",
        "sections": [{
            "narration": "这节口播",
            "emphasis": [
                {"text": "要抓住机会", "kind": "golden"},
                {"text": "核心词", "kind": "keyword"},
            ]
        }]
    }
    manifest = build_effects_manifest(plan, rewrite)
    types = [e["type"] for e in manifest["effects"]]
    assert "golden_card" in types
    golden = [e for e in manifest["effects"] if e["type"] == "golden_card"]
    assert golden[0]["text"] == "要抓住机会"
    # keyword_pop 里不含 golden 文字
    kw_keywords = [e.get("keyword", "") for e in manifest["effects"] if e["type"] == "keyword_pop"]
    assert "要抓住机会" not in kw_keywords


def test_keyword_pop_filtered_near_golden_window():
    """keyword_pop 落在 golden_card 时间窗 +-1s 内时被过滤掉。"""
    plan = {
        "sections": [
            {"index": 0, "title": "hook", "duration_seconds": 5.0},
            {"index": 1, "title": "节1", "duration_seconds": 120.0},
        ]
    }
    # 一个 golden + 一个 keyword：均匀分布后各自均为 n=1，
    # 两者落在同一节的相同比例位置 → 时刻相同 → keyword 与 golden_card 窗口重叠 → 被过滤。
    rewrite = {
        "hook": "开场",
        "sections": [{
            "narration": "",
            "emphasis": [
                {"text": "金句", "kind": "golden"},
                {"text": "关键词", "kind": "keyword"},
            ]
        }]
    }
    manifest = build_effects_manifest(plan, rewrite)
    golden_specs = [e for e in manifest["effects"] if e["type"] == "golden_card"]
    kw_specs = [e for e in manifest["effects"] if e["type"] == "keyword_pop"]

    assert len(golden_specs) >= 1
    golden_start = golden_specs[0]["start"]
    golden_end = golden_start + GOLDEN_CARD_DURATION
    # 所有 keyword_pop 都不应落在 golden_card 的 +-1s 窗口内
    for ks in [e["start"] for e in kw_specs]:
        assert not (golden_start - 1.0 <= ks <= golden_end + 1.0), (
            f"keyword_pop at {ks}s 未被 golden_card [{golden_start}, {golden_end}] 过滤"
        )


def test_golden_card_duration_from_manifest():
    """manifest 中 golden_card 的 duration 近似等于 GOLDEN_CARD_DURATION（帧对齐误差 < 1帧）。"""
    plan = {
        "sections": [
            {"index": 0, "title": "hook", "duration_seconds": 5.0},
            {"index": 1, "title": "节1", "duration_seconds": 60.0},
        ]
    }
    rewrite = {
        "hook": "开场",
        "sections": [{"narration": "", "emphasis": [{"text": "金句A", "kind": "golden"}]}]
    }
    manifest = build_effects_manifest(plan, rewrite)
    golden = [e for e in manifest["effects"] if e["type"] == "golden_card"]
    assert len(golden) == 1
    assert abs(golden[0]["duration"] - GOLDEN_CARD_DURATION) < 0.1


def test_golden_card_min_gap_in_full_manifest():
    """全片多节均含 golden emphasis 时，golden_card 间距 >= GOLDEN_CARD_MIN_GAP_S。"""
    plan = {
        "sections": [
            {"index": 0, "title": "hook", "duration_seconds": 5.0},
            {"index": 1, "title": "节1", "duration_seconds": 15.0},   # 5-20s
            {"index": 2, "title": "节2", "duration_seconds": 15.0},   # 20-35s
            {"index": 3, "title": "节3", "duration_seconds": 30.0},   # 35-65s
        ]
    }
    rewrite = {
        "hook": "开场",
        "sections": [
            {"narration": "", "emphasis": [{"text": "金句A", "kind": "golden"}]},
            {"narration": "", "emphasis": [{"text": "金句B", "kind": "golden"}]},
            {"narration": "", "emphasis": [{"text": "金句C", "kind": "golden"}]},
        ]
    }
    manifest = build_effects_manifest(plan, rewrite)
    golden = sorted(
        [e for e in manifest["effects"] if e["type"] == "golden_card"],
        key=lambda e: e["start"],
    )
    for i in range(1, len(golden)):
        gap = golden[i]["start"] - golden[i - 1]["start"]
        assert gap >= GOLDEN_CARD_MIN_GAP_S - 0.1, (
            f"golden_card 间隔 {gap:.1f}s 小于 {GOLDEN_CARD_MIN_GAP_S}s"
        )
