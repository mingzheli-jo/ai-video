import json
import subprocess
from pathlib import Path

import pytest

from video_factory.asset_pool import AssetClip
from video_factory.assemble import (
    AssemblyError,
    build_assembly_plan,
    render_assembly,
    main,
    DURATION_TOLERANCE,
)


REWRITE = {
    "version": "rewrite_v1",
    "hook": "三秒钩子",
    "sections": [
        {"index": 0, "title": "第一节", "narration": "第一节口播文案内容一二三", "visual_hint": "画面"},
        {"index": 1, "title": "第二节", "narration": "第二节口播", "visual_hint": "画面"},
    ],
    "full_voiceover": "三秒钩子\n第一节口播文案内容一二三\n第二节口播",
    "target_duration_seconds": 90,
}


def _clips():
    return (
        AssetClip(path=Path("a.mp4"), duration=1000.0, width=1920, height=1080),
        AssetClip(path=Path("b.mp4"), duration=1000.0, width=1280, height=720),
    )


class _Recorder:
    """记录 ffmpeg 命令、创建产物文件、并为 ffprobe 返回可控时长。"""

    def __init__(self, probe_duration=90.0):
        self.commands = []
        self.probe_duration = probe_duration

    def __call__(self, command, **kwargs):
        self.commands.append(command)
        tool = Path(command[0]).stem.lower()
        if tool == "ffprobe":
            payload = json.dumps({"format": {"duration": str(self.probe_duration)}})
            return subprocess.CompletedProcess(command, 0, stdout=payload, stderr="")
        # ffmpeg：最后一个参数是输出路径，落一个占位文件。
        output = Path(command[-1])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"video")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")


# --- plan ---------------------------------------------------------------


def test_build_assembly_plan_counts_hook_as_section():
    plan = build_assembly_plan(REWRITE, _clips(), target_duration=None)
    assert plan.target_duration == 90.0
    assert plan.section_titles[0] == "hook"
    assert len(plan.allocations) == 3  # hook + 2 sections
    assert sum(a.duration for a in plan.allocations) == pytest.approx(90.0, abs=1e-6)


def test_build_assembly_plan_duration_override():
    plan = build_assembly_plan(REWRITE, _clips(), target_duration=45.0)
    assert plan.target_duration == 45.0
    assert sum(a.duration for a in plan.allocations) == pytest.approx(45.0, abs=1e-6)


def test_build_assembly_plan_rejects_empty_rewrite():
    with pytest.raises(AssemblyError, match="没有可用的文案小节"):
        build_assembly_plan({"hook": "", "sections": []}, _clips(), target_duration=30.0)


# --- render commands ----------------------------------------------------


def test_render_segment_command_has_scale_pad_fps(tmp_path):
    plan = build_assembly_plan(REWRITE, _clips(), target_duration=30.0)
    runner = _Recorder(probe_duration=30.0)
    render_assembly(plan, tmp_path, audio_path=None, runner=runner)

    segment_cmds = [c for c in runner.commands if Path(c[0]).stem == "ffmpeg" and "-vf" in c]
    vf = segment_cmds[0][segment_cmds[0].index("-vf") + 1]
    assert "scale=1920:1080:force_original_aspect_ratio=decrease" in vf
    assert "pad=1920:1080:(ow-iw)/2:(oh-ih)/2" in vf
    assert "fps=30" in vf
    assert "-an" in segment_cmds[0]
    assert "libx264" in segment_cmds[0]
    assert "18" in segment_cmds[0]


def test_render_writes_concat_file_with_posix_paths(tmp_path):
    plan = build_assembly_plan(REWRITE, _clips(), target_duration=30.0)
    runner = _Recorder(probe_duration=30.0)
    outputs = render_assembly(plan, tmp_path, audio_path=None, runner=runner)

    concat_text = outputs["concat"].read_text(encoding="utf-8")
    assert concat_text.startswith("file '")
    assert "segment_00.mp4" in concat_text
    assert "\\" not in concat_text  # 用 as_posix，concat 里不能有反斜杠
    # ≥2 段默认走 xfade 转场（不再是 concat demuxer 硬切）；concat 清单仍写出，作 xfade
    # 失败时的回退输入。合并命令应是带 xfade 的 filter_complex，不含 -f concat。
    merge_cmd = next(
        c for c in runner.commands
        if "-filter_complex" in c and "xfade" in c[c.index("-filter_complex") + 1]
    )
    assert "concat" not in merge_cmd


def test_render_escapes_single_quotes_in_concat_paths(tmp_path):
    output_dir = tmp_path / "O'Brien Editor's Cut"  # 含单引号的真实 Windows 路径形态
    plan = build_assembly_plan(REWRITE, _clips(), target_duration=30.0)
    runner = _Recorder(probe_duration=30.0)
    outputs = render_assembly(plan, output_dir, audio_path=None, runner=runner)

    concat_text = outputs["concat"].read_text(encoding="utf-8")
    # concat demuxer 语法：路径内单引号必须转义为 '\''，否则 ffmpeg 在引号处截断
    assert "O'\\''Brien Editor'\\''s Cut" in concat_text
    for line in concat_text.strip().splitlines():
        assert line.startswith("file '") and line.endswith("'")


def test_render_without_audio_copies_release(tmp_path):
    plan = build_assembly_plan(REWRITE, _clips(), target_duration=30.0)
    runner = _Recorder(probe_duration=30.0)
    outputs = render_assembly(plan, tmp_path, audio_path=None, runner=runner)

    assert outputs["release"].exists()
    manifest = json.loads(outputs["assembly_plan"].read_text(encoding="utf-8"))
    assert manifest["audio_path"] == ""
    assert manifest["audio_duration_seconds"] is None
    assert manifest["fps"] == 30 and manifest["width"] == 1920 and manifest["height"] == 1080


def test_render_with_audio_muxes_and_reports_alignment(tmp_path):
    audio = tmp_path / "voiceover.wav"
    audio.write_bytes(b"audio")
    plan = build_assembly_plan(REWRITE, _clips(), target_duration=90.0)
    # 音频 60s，最终成片探测也回 60s → 对齐。
    runner = _Recorder(probe_duration=60.0)
    outputs = render_assembly(plan, tmp_path, audio_path=audio, runner=runner)

    mux_cmd = next(c for c in runner.commands if "-shortest" in c)
    assert "1:a" in mux_cmd
    assert "[vpad]" in mux_cmd
    assert "aac" in mux_cmd
    assert "192k" in mux_cmd
    # -t 强制截到配音时长（真实 ffmpeg 下 tpad+-shortest 会过冲，靠 -t 精确对齐）
    assert "-t" in mux_cmd
    assert mux_cmd[mux_cmd.index("-t") + 1] == "60.000"
    manifest = json.loads(outputs["assembly_plan"].read_text(encoding="utf-8"))
    assert manifest["audio_duration_seconds"] == 60.0
    assert manifest["final_duration_seconds"] == 60.0
    assert manifest["duration_aligned"] is True


def test_render_flags_misaligned_duration(tmp_path):
    audio = tmp_path / "voiceover.wav"
    audio.write_bytes(b"audio")
    plan = build_assembly_plan(REWRITE, _clips(), target_duration=90.0)

    class DriftRecorder(_Recorder):
        def __call__(self, command, **kwargs):
            tool = Path(command[0]).stem.lower()
            if tool == "ffprobe":
                # 音频 60s，成片 65s（>容差）→ 记为未对齐。
                is_audio = command[-1].endswith("voiceover.wav")
                payload = json.dumps({"format": {"duration": "60.0" if is_audio else "65.0"}})
                self.commands.append(command)
                return subprocess.CompletedProcess(command, 0, stdout=payload, stderr="")
            return super().__call__(command, **kwargs)

    runner = DriftRecorder()
    outputs = render_assembly(plan, tmp_path, audio_path=audio, runner=runner)
    manifest = json.loads(outputs["assembly_plan"].read_text(encoding="utf-8"))
    assert abs(manifest["final_duration_seconds"] - manifest["audio_duration_seconds"]) > DURATION_TOLERANCE
    assert manifest["duration_aligned"] is False


# --- BGM ducking --------------------------------------------------------


def _bgm_mux_cmd(runner):
    """取带 BGM 的合成命令（三输入：含 -filter_complex 且用了 [aout] 映射）。"""
    return next(
        c for c in runner.commands
        if Path(c[0]).stem == "ffmpeg" and "-filter_complex" in c and "[aout]" in c
    )


def _filter_complex(cmd):
    return cmd[cmd.index("-filter_complex") + 1]


def _make_bgm(tmp_path):
    bgm = tmp_path / "bgm.mp3"
    bgm.write_bytes(b"bgm")
    audio = tmp_path / "voiceover.wav"
    audio.write_bytes(b"audio")
    return audio, bgm


def test_render_with_bgm_builds_sidechain_ducking_command(tmp_path):
    audio, bgm = _make_bgm(tmp_path)
    plan = build_assembly_plan(REWRITE, _clips(), target_duration=90.0)
    runner = _Recorder(probe_duration=60.0)
    render_assembly(
        plan, tmp_path, audio_path=audio, runner=runner,
        bgm_path=bgm, bgm_volume=0.2, bgm_fade=2.0,
    )

    cmd = _bgm_mux_cmd(runner)
    fc = _filter_complex(cmd)
    assert "sidechaincompress=threshold=0.03:ratio=8:attack=20:release=400" in fc
    assert "amix=inputs=2:duration=first:dropout_transition=0:normalize=0" in fc
    assert fc.count("aformat=sample_rates=48000:channel_layouts=stereo") == 2
    assert "volume=0.2" in fc
    assert "afade=t=in:st=0:d=2.0" in fc
    assert "[vpad]" in cmd and "[aout]" in cmd
    assert "-shortest" in cmd and "aac" in cmd and "192k" in cmd


def test_render_with_bgm_stream_loops_before_bgm_input(tmp_path):
    audio, bgm = _make_bgm(tmp_path)
    plan = build_assembly_plan(REWRITE, _clips(), target_duration=90.0)
    runner = _Recorder(probe_duration=60.0)
    render_assembly(
        plan, tmp_path, audio_path=audio, runner=runner, bgm_path=bgm,
    )

    cmd = _bgm_mux_cmd(runner)
    assert "-stream_loop" in cmd and "-1" in cmd
    loop_idx = cmd.index("-stream_loop")
    bgm_i_idx = next(
        i for i in range(len(cmd) - 1) if cmd[i] == "-i" and cmd[i + 1] == str(bgm)
    )
    # -stream_loop -1 必须紧贴在 -i bgm 之前才对该输入生效。
    assert loop_idx < bgm_i_idx
    assert cmd[loop_idx + 1] == "-1"


def test_render_with_bgm_adds_fadeout_when_audio_long_enough(tmp_path):
    audio, bgm = _make_bgm(tmp_path)
    plan = build_assembly_plan(REWRITE, _clips(), target_duration=90.0)
    # 配音 60s，fade 2s → 60 > 2*2，应拼淡出，st = 60 - 2 = 58。
    runner = _Recorder(probe_duration=60.0)
    render_assembly(
        plan, tmp_path, audio_path=audio, runner=runner, bgm_path=bgm, bgm_fade=2.0,
    )

    fc = _filter_complex(_bgm_mux_cmd(runner))
    assert "afade=t=out:st=58.0:d=2.0" in fc


def test_render_with_bgm_omits_fadeout_when_audio_too_short(tmp_path):
    audio, bgm = _make_bgm(tmp_path)
    plan = build_assembly_plan(REWRITE, _clips(), target_duration=90.0)
    # 配音 3s，fade 2s → 3 <= 2*2，省略淡出（绝不能拼出负 st）。
    runner = _Recorder(probe_duration=3.0)
    render_assembly(
        plan, tmp_path, audio_path=audio, runner=runner, bgm_path=bgm, bgm_fade=2.0,
    )

    fc = _filter_complex(_bgm_mux_cmd(runner))
    assert "afade=t=out" not in fc
    assert "afade=t=in:st=0:d=2.0" in fc  # 淡入仍在


def test_render_bgm_without_audio_raises(tmp_path):
    _, bgm = _make_bgm(tmp_path)
    plan = build_assembly_plan(REWRITE, _clips(), target_duration=30.0)
    runner = _Recorder(probe_duration=30.0)
    with pytest.raises(AssemblyError, match="ducking 基准"):
        render_assembly(plan, tmp_path, audio_path=None, runner=runner, bgm_path=bgm)


def test_render_bgm_missing_file_raises(tmp_path):
    audio = tmp_path / "voiceover.wav"
    audio.write_bytes(b"audio")
    plan = build_assembly_plan(REWRITE, _clips(), target_duration=30.0)
    runner = _Recorder(probe_duration=30.0)
    with pytest.raises(AssemblyError, match="BGM 文件不存在"):
        render_assembly(
            plan, tmp_path, audio_path=audio, runner=runner,
            bgm_path=tmp_path / "nope.mp3",
        )


def test_render_with_bgm_records_manifest_fields(tmp_path):
    audio, bgm = _make_bgm(tmp_path)
    plan = build_assembly_plan(REWRITE, _clips(), target_duration=90.0)
    runner = _Recorder(probe_duration=60.0)
    outputs = render_assembly(
        plan, tmp_path, audio_path=audio, runner=runner,
        bgm_path=bgm, bgm_volume=0.25, bgm_fade=1.5,
    )

    manifest = json.loads(outputs["assembly_plan"].read_text(encoding="utf-8"))
    assert manifest["bgm_path"] == str(bgm)
    assert manifest["bgm_volume"] == 0.25
    assert manifest["bgm_fade"] == 1.5


def test_render_without_bgm_manifest_bgm_path_empty(tmp_path):
    audio = tmp_path / "voiceover.wav"
    audio.write_bytes(b"audio")
    plan = build_assembly_plan(REWRITE, _clips(), target_duration=90.0)
    runner = _Recorder(probe_duration=60.0)
    outputs = render_assembly(plan, tmp_path, audio_path=audio, runner=runner)

    manifest = json.loads(outputs["assembly_plan"].read_text(encoding="utf-8"))
    assert manifest["bgm_path"] == ""


def test_mux_command_byte_identical_when_no_bgm(tmp_path):
    """bgm=None 时合成命令的完整形态（含 -t 精确对齐 + -shortest 兜底）。"""
    audio = tmp_path / "voiceover.wav"
    audio.write_bytes(b"audio")
    plan = build_assembly_plan(REWRITE, _clips(), target_duration=90.0)
    runner = _Recorder(probe_duration=60.0)
    render_assembly(plan, tmp_path, audio_path=audio, runner=runner)

    mux_cmd = next(c for c in runner.commands if "-shortest" in c)
    silent = str(tmp_path / "assembly_silent.mp4")
    release = str(tmp_path / "release.mp4")
    assert mux_cmd == [
        "ffmpeg", "-y",
        "-hide_banner", "-loglevel", "error",
        "-i", silent,
        "-i", str(audio),
        "-filter_complex", "[0:v]tpad=stop_mode=clone:stop_duration=3600[vpad]",
        "-map", "[vpad]",
        "-map", "1:a",
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "18",
        "-c:a", "aac",
        "-b:a", "192k",
        "-t", "60.000",
        "-shortest",
        "-movflags", "+faststart",
        release,
    ]


def test_cli_bgm_without_audio_returns_chinese_error(tmp_path, capsys):
    rewrite_path = tmp_path / "rewrite.json"
    rewrite_path.write_text(json.dumps(REWRITE, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "a.mp4").write_bytes(b"x")
    (tmp_path / "bgm.mp3").write_bytes(b"bgm")

    code = main([
        "--rewrite", str(rewrite_path),
        "--assets", str(tmp_path),
        "--bgm", str(tmp_path / "bgm.mp3"),
    ])
    assert code == 1
    out = capsys.readouterr().out
    assert "拼装失败" in out and "ducking 基准" in out


def test_cli_bgm_ducking_runs(tmp_path, monkeypatch):
    from video_factory import assemble as assemble_mod
    from video_factory import asset_pool as asset_pool_mod

    rewrite_path = tmp_path / "rewrite.json"
    rewrite_path.write_text(json.dumps(REWRITE, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "a.mp4").write_bytes(b"x")
    audio = tmp_path / "voiceover.wav"
    audio.write_bytes(b"audio")
    bgm = tmp_path / "bgm.mp3"
    bgm.write_bytes(b"bgm")

    runner = _Recorder(probe_duration=60.0)
    real_scan = asset_pool_mod.scan_asset_pool
    real_render = assemble_mod.render_assembly
    monkeypatch.setattr(
        assemble_mod, "scan_asset_pool", lambda directory: real_scan(directory, runner=runner)
    )
    monkeypatch.setattr(
        assemble_mod,
        "render_assembly",
        lambda plan, output_dir, **kwargs: real_render(
            plan, output_dir, runner=runner, **kwargs
        ),
    )

    code = main([
        "--rewrite", str(rewrite_path),
        "--assets", str(tmp_path),
        "--duration", "90",
        "--audio", str(audio),
        "--bgm", str(bgm),
        "--bgm-volume", "0.15",
        "--bgm-fade", "2.0",
    ])
    assert code == 0
    cmd = _bgm_mux_cmd(runner)
    fc = _filter_complex(cmd)
    assert "sidechaincompress=" in fc
    assert "volume=0.15" in fc
    assert "-stream_loop" in cmd


def _segment_vf(runner):
    segment_cmds = [c for c in runner.commands if Path(c[0]).stem == "ffmpeg" and "-vf" in c]
    return segment_cmds[0][segment_cmds[0].index("-vf") + 1]


def test_render_vertical_9_16_scale_and_pad(tmp_path):
    plan = build_assembly_plan(REWRITE, _clips(), target_duration=30.0, width=1080, height=1920)
    runner = _Recorder(probe_duration=30.0)
    render_assembly(plan, tmp_path, audio_path=None, runner=runner)

    vf = _segment_vf(runner)
    assert "scale=1080:1920:force_original_aspect_ratio=decrease" in vf
    assert "pad=1080:1920:(ow-iw)/2:(oh-ih)/2" in vf
    assert vf.endswith("fps=30")


def test_render_square_1_1_geometry(tmp_path):
    plan = build_assembly_plan(REWRITE, _clips(), target_duration=30.0, width=1080, height=1080)
    runner = _Recorder(probe_duration=30.0)
    render_assembly(plan, tmp_path, audio_path=None, runner=runner)

    vf = _segment_vf(runner)
    assert "scale=1080:1080:force_original_aspect_ratio=decrease" in vf
    assert "pad=1080:1080:(ow-iw)/2:(oh-ih)/2" in vf


def test_render_portrait_3_4_geometry(tmp_path):
    plan = build_assembly_plan(REWRITE, _clips(), target_duration=30.0, width=1080, height=1440)
    runner = _Recorder(probe_duration=30.0)
    render_assembly(plan, tmp_path, audio_path=None, runner=runner)

    vf = _segment_vf(runner)
    assert "scale=1080:1440:force_original_aspect_ratio=decrease" in vf
    assert "pad=1080:1440:(ow-iw)/2:(oh-ih)/2" in vf


def test_render_fit_crop_uses_increase_and_crop(tmp_path):
    plan = build_assembly_plan(
        REWRITE, _clips(), target_duration=30.0, width=1080, height=1920, fit="crop"
    )
    runner = _Recorder(probe_duration=30.0)
    render_assembly(plan, tmp_path, audio_path=None, runner=runner)

    vf = _segment_vf(runner)
    assert "scale=1080:1920:force_original_aspect_ratio=increase" in vf
    assert "crop=1080:1920" in vf
    assert "pad=" not in vf
    assert vf.endswith("fps=30")


def test_render_fit_blur_uses_split_boxblur_overlay(tmp_path):
    plan = build_assembly_plan(
        REWRITE, _clips(), target_duration=30.0, width=1080, height=1920, fit="blur"
    )
    runner = _Recorder(probe_duration=30.0)
    render_assembly(plan, tmp_path, audio_path=None, runner=runner)

    vf = _segment_vf(runner)
    assert "split[bg][fg]" in vf
    assert "boxblur=20:2" in vf
    assert "overlay=(W-w)/2:(H-h)/2" in vf
    assert vf.endswith("fps=30")


def test_build_assembly_plan_rejects_unknown_fit():
    with pytest.raises(AssemblyError, match="未知的画幅填充模式"):
        build_assembly_plan(REWRITE, _clips(), target_duration=30.0, fit="stretch")


def test_render_vertical_manifest_records_aspect_and_fit(tmp_path):
    plan = build_assembly_plan(
        REWRITE, _clips(), target_duration=30.0, width=1080, height=1920, fit="blur"
    )
    runner = _Recorder(probe_duration=30.0)
    outputs = render_assembly(plan, tmp_path, audio_path=None, runner=runner)

    manifest = json.loads(outputs["assembly_plan"].read_text(encoding="utf-8"))
    assert manifest["width"] == 1080 and manifest["height"] == 1920
    assert manifest["aspect"] == "9:16"
    assert manifest["fit"] == "blur"


def test_render_custom_geometry_manifest_aspect_is_custom(tmp_path):
    plan = build_assembly_plan(REWRITE, _clips(), target_duration=30.0, width=800, height=600)
    runner = _Recorder(probe_duration=30.0)
    outputs = render_assembly(plan, tmp_path, audio_path=None, runner=runner)

    manifest = json.loads(outputs["assembly_plan"].read_text(encoding="utf-8"))
    assert manifest["aspect"] == "custom"


def test_default_command_is_byte_identical_to_legacy(tmp_path):
    plan = build_assembly_plan(REWRITE, _clips(), target_duration=30.0)
    runner = _Recorder(probe_duration=30.0)
    render_assembly(plan, tmp_path, audio_path=None, runner=runner)

    vf = _segment_vf(runner)
    assert vf == (
        "scale=1920:1080:force_original_aspect_ratio=decrease,"
        "pad=1920:1080:(ow-iw)/2:(oh-ih)/2,"
        "fps=30"
    )


def test_cli_aspect_9_16_fit_blur_runs(tmp_path, monkeypatch):
    from video_factory import assemble as assemble_mod
    from video_factory import asset_pool as asset_pool_mod

    rewrite_path = tmp_path / "rewrite.json"
    rewrite_path.write_text(json.dumps(REWRITE, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "a.mp4").write_bytes(b"x")

    runner = _Recorder(probe_duration=30.0)
    # scan_asset_pool / render_assembly 的 runner 是 def 时绑定的默认参数，
    # monkeypatch 模块 subprocess 无效，直接替身这两个引用把假 runner 注入。
    real_scan = asset_pool_mod.scan_asset_pool
    real_render = assemble_mod.render_assembly
    monkeypatch.setattr(
        assemble_mod, "scan_asset_pool", lambda directory: real_scan(directory, runner=runner)
    )
    monkeypatch.setattr(
        assemble_mod,
        "render_assembly",
        lambda plan, output_dir, audio_path=None: real_render(
            plan, output_dir, audio_path=audio_path, runner=runner
        ),
    )

    code = main([
        "--rewrite", str(rewrite_path),
        "--assets", str(tmp_path),
        "--duration", "30",
        "--aspect", "9:16",
        "--fit", "blur",
        "--output", str(tmp_path / "out"),
    ])
    assert code == 0
    vf = _segment_vf(runner)
    assert "scale=1080:1920:force_original_aspect_ratio=increase" in vf
    assert "boxblur=20:2" in vf
    assert "overlay=(W-w)/2:(H-h)/2" in vf
    manifest = json.loads((tmp_path / "out" / "assembly_plan.json").read_text(encoding="utf-8"))
    assert manifest["aspect"] == "9:16" and manifest["fit"] == "blur"


def test_render_raises_assembly_error_on_ffmpeg_failure(tmp_path):
    plan = build_assembly_plan(REWRITE, _clips(), target_duration=30.0)

    def failing_runner(command, **kwargs):
        if Path(command[0]).stem == "ffmpeg":
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="Invalid argument xyz" * 40)
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps({"format": {"duration": "1"}}), stderr="")

    with pytest.raises(AssemblyError) as exc:
        render_assembly(plan, tmp_path, audio_path=None, runner=failing_runner)
    assert "Invalid argument" in str(exc.value)
    assert len(str(exc.value)) < 400  # stderr 摘要截断


# --- CLI ----------------------------------------------------------------


def test_cli_audio_and_tts_are_mutually_exclusive(tmp_path, capsys):
    rewrite_path = tmp_path / "rewrite.json"
    rewrite_path.write_text(json.dumps(REWRITE, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(SystemExit):
        main([
            "--rewrite", str(rewrite_path),
            "--assets", str(tmp_path),
            "--audio", "x.wav",
            "--tts", "doubao",
        ])
    err = capsys.readouterr().err
    assert "not allowed with argument" in err or "互斥" in err


def test_cli_missing_rewrite_returns_chinese_error_and_exit_1(tmp_path, capsys):
    code = main([
        "--rewrite", str(tmp_path / "missing.json"),
        "--assets", str(tmp_path),
    ])
    assert code == 1
    out = capsys.readouterr().out
    assert "拼装失败" in out


def test_cli_missing_audio_file_returns_chinese_error(tmp_path, capsys):
    rewrite_path = tmp_path / "rewrite.json"
    rewrite_path.write_text(json.dumps(REWRITE, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "a.mp4").write_bytes(b"x")

    code = main([
        "--rewrite", str(rewrite_path),
        "--assets", str(tmp_path),
        "--audio", str(tmp_path / "nope.wav"),
    ])
    assert code == 1
    out = capsys.readouterr().out
    assert "拼装失败" in out and "配音文件不存在" in out


# --- pipeline text-level TTS entry --------------------------------------


def test_synthesize_voiceover_text_dispatches_to_doubao(tmp_path, monkeypatch):
    import base64

    from video_factory import synthesize_voiceover_text
    from video_factory.pipeline import TTSConfig

    output = tmp_path / "voiceover.wav"
    monkeypatch.setenv("VOLC_TTS_APPID", "appid-123")
    monkeypatch.setenv("VOLC_TTS_TOKEN", "token-456")
    requests = []

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            data = base64.b64encode(b"fake-mp3-bytes").decode("ascii")
            return json.dumps({"code": 3000, "data": data}).encode("utf-8")

    def fake_urlopen(request, timeout):
        requests.append(request)
        return FakeResponse()

    def fake_run(command, **kwargs):
        if Path(command[0]).stem.lower() == "ffmpeg":
            output.write_bytes(b"wav")

    monkeypatch.setattr("video_factory.pipeline.urlopen", fake_urlopen)
    monkeypatch.setattr("video_factory.pipeline.subprocess.run", fake_run)
    monkeypatch.setattr("video_factory.pipeline._probe_duration_seconds", lambda path: 42)

    result = synthesize_voiceover_text(
        "三秒钩子 第一节口播 第二节口播",
        output,
        TTSConfig(provider="doubao", voice="zh_male_yuanboxiaoshu_moon_bigtts"),
    )

    assert result.provider == "doubao"
    assert result.used_fallback is False
    body = json.loads(requests[0].data.decode("utf-8"))
    assert body["request"]["text"] == "三秒钩子 第一节口播 第二节口播"
    assert body["audio"]["voice_type"] == "zh_male_yuanboxiaoshu_moon_bigtts"


def test_synthesize_voiceover_text_rejects_tone_and_fallback(tmp_path, monkeypatch):
    from video_factory import synthesize_voiceover_text
    from video_factory.pipeline import TTSConfig, TTSProviderError

    output = tmp_path / "voiceover.wav"
    with pytest.raises(TTSProviderError, match="tone"):
        synthesize_voiceover_text("口播文本", output, TTSConfig(provider="tone"))

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    # 缺凭证 + allow_fallback 也不能兜底成 tone，必须抛错。
    with pytest.raises(TTSProviderError):
        synthesize_voiceover_text(
            "口播文本", output, TTSConfig(provider="openai", allow_fallback=True)
        )


def test_synthesize_voiceover_text_rejects_empty_narration(tmp_path):
    from video_factory import synthesize_voiceover_text
    from video_factory.pipeline import TTSProviderError

    with pytest.raises(TTSProviderError, match="口播文案为空"):
        synthesize_voiceover_text("   ", tmp_path / "v.wav")


# ---------- 时长闭环第二级：atempo 末级微调 ----------

class _FitFakeRunner:
    """假 runner：ffprobe 回注入的时长 JSON，ffmpeg 落地输出文件并记录命令。"""

    def __init__(self, duration: float, ffmpeg_ok: bool = True):
        self.duration = duration
        self.ffmpeg_ok = ffmpeg_ok
        self.commands: list[list[str]] = []

    def __call__(self, command, **kwargs):
        self.commands.append(command)
        stem = Path(command[0]).stem.lower()
        if stem == "ffprobe":
            return subprocess.CompletedProcess(
                command, 0, stdout=json.dumps({"format": {"duration": str(self.duration)}}), stderr=""
            )
        if stem == "ffmpeg":
            if self.ffmpeg_ok:
                Path(command[-1]).write_bytes(b"fitted")
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="fake fail")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")


def _fit_setup(tmp_path):
    audio = tmp_path / "voiceover.wav"
    audio.write_bytes(b"wav")
    return audio


def test_fit_audio_within_tolerance_untouched(tmp_path):
    from video_factory.assemble import _fit_audio_to_target

    audio = _fit_setup(tmp_path)
    runner = _FitFakeRunner(duration=116.0)  # 116/120 ≈ -3.3%，在 ±5% 容差内
    path, note = _fit_audio_to_target(audio, 120.0, tmp_path, runner)
    assert path == audio and note is None
    assert not any(Path(c[0]).stem.lower() == "ffmpeg" for c in runner.commands)


def test_fit_audio_too_short_slows_down_clamped(tmp_path):
    from video_factory.assemble import _fit_audio_to_target

    audio = _fit_setup(tmp_path)
    runner = _FitFakeRunner(duration=60.0)  # 60/120=-50% → tempo 钳到 0.9（放慢上限）
    path, note = _fit_audio_to_target(audio, 120.0, tmp_path, runner)
    assert path.name == "voiceover_fitted.wav" and path.exists()
    ffmpeg_cmd = next(c for c in runner.commands if Path(c[0]).stem.lower() == "ffmpeg")
    assert "atempo=0.9000" in " ".join(ffmpeg_cmd)
    assert note and "上限" in note  # 钳到上限必须提示残差要靠文案解决


def test_fit_audio_moderate_overshoot_speeds_up_exact(tmp_path):
    from video_factory.assemble import _fit_audio_to_target

    audio = _fit_setup(tmp_path)
    runner = _FitFakeRunner(duration=100.0)  # 100/93≈1.0753：超5%容差、未到1.1钳位→精确修
    path, note = _fit_audio_to_target(audio, 93.0, tmp_path, runner)
    ffmpeg_cmd = next(c for c in runner.commands if Path(c[0]).stem.lower() == "ffmpeg")
    assert "atempo=1.0753" in " ".join(ffmpeg_cmd)
    assert note and "上限" not in note


def test_fit_audio_passthrough_on_probe_or_ffmpeg_failure(tmp_path):
    from video_factory.assemble import _fit_audio_to_target

    audio = _fit_setup(tmp_path)
    # 无目标 → 原样放行
    path, note = _fit_audio_to_target(audio, 0.0, tmp_path, _FitFakeRunner(60.0))
    assert path == audio and note is None
    # ffmpeg 失败 → 放行原音频，不阻断成片
    runner = _FitFakeRunner(duration=60.0, ffmpeg_ok=False)
    path, note = _fit_audio_to_target(audio, 120.0, tmp_path, runner)
    assert path == audio and note is None


# ---------- 图片素材：Ken Burns 运镜 ----------

def test_build_segment_command_image_uses_zoompan(tmp_path):
    from video_factory.assemble import _build_segment_command
    from video_factory.asset_pool import ClipSlice

    img = ClipSlice(path=Path("素材库/图片/物品/img_abc.png"), start=0.0, duration=4.0)
    cmd = _build_segment_command(img, tmp_path / "seg.mp4", 1080, 1920, 30, "blur")
    joined = " ".join(cmd)
    assert "zoompan=" in joined            # 图片走运镜
    assert "-frames:v 120" in joined       # 4s*30fps
    assert "-ss" not in cmd                # 图片无时间起点
    assert "s=1080x1920" in joined         # 输出目标分辨率
    assert "scale=2160:3840" in joined     # 2倍超采样减抖

    vid = ClipSlice(path=Path("a.mp4"), start=3.0, duration=4.0)
    cmd2 = _build_segment_command(vid, tmp_path / "seg2.mp4", 1080, 1920, 30, "blur")
    assert "zoompan=" not in " ".join(cmd2)  # 视频路径不受影响
    assert "-ss" in cmd2


def test_kenburns_variant_deterministic():
    from video_factory.assemble import _build_image_segment_command
    from video_factory.asset_pool import ClipSlice

    s = ClipSlice(path=Path("img_x.png"), start=0.0, duration=3.0)
    c1 = _build_image_segment_command(s, Path("o.mp4"), 1080, 1920, 30, "pad")
    c2 = _build_image_segment_command(s, Path("o.mp4"), 1080, 1920, 30, "pad")
    assert c1 == c2  # 同图同起点 → 动效确定（可复现）


# ---------- 图片素材：Ken Burns 新增 4 款运镜（variant 4~7，共 8 款） ----------

def _image_vf(path_name, width=1080, height=1920, fps=30, fit="pad", duration=3.0):
    """按文件名取该图运镜的 -vf 滤镜串（文件名决定命中哪一款 variant）。"""
    from video_factory.assemble import _build_image_segment_command
    from video_factory.asset_pool import ClipSlice

    s = ClipSlice(path=Path(path_name), start=0.0, duration=duration)
    cmd = _build_image_segment_command(s, Path("o.mp4"), width, height, fps, fit)
    return cmd[cmd.index("-vf") + 1]


def test_kenburns_variant_diagonal_push():  # p0.png → variant 4（对角推近）
    vf = _image_vf("p0.png")
    assert "zoompan=" in vf
    # x 与 y 都随 on 变化 → 斜向运动，配合 zoom 推近
    assert "x='(iw-iw/zoom)*on/90'" in vf
    assert "y='(ih-ih/zoom)*on/90'" in vf
    assert "s=1080x1920" in vf


def test_kenburns_variant_rotate_drift():  # p14.png → variant 5（旋转漂移）
    vf = _image_vf("p14.png")
    assert "rotate=" in vf            # 旋转漂移专属 rotate 滤镜
    assert "crop=1080:1920" in vf     # 中心裁回目标
    assert "scale=2160:3840" in vf    # 2 倍超采样
    assert "zoompan=" in vf           # 仍用 zoompan 生成多帧 + 缓推
    assert "s=1350x2400" in vf        # 输出到留 25% 余量的画布（黑角被中心裁切吃掉）
    assert "fillcolor=black@0" in vf  # 兜底填色（正常裁切下看不到）


def test_kenburns_variant_punch_in():  # p5.png → variant 6（特写冲击）
    vf = _image_vf("p5.png")
    assert "if(lte(on,18)" in vf  # 前 20%（90*0.2=18 帧）分段冲击
    assert "1.25" in vf            # 冲到 1.25 再回落
    assert "zoompan=" in vf
    assert "s=1080x1920" in vf


def test_kenburns_variant_breathing():  # p2.png → variant 7（缓慢呼吸）
    vf = _image_vf("p2.png")
    assert "cos(2*PI*on/90)" in vf  # 余弦单周期往返（1.0~1.08）
    assert "s=1080x1920" in vf


def test_kenburns_variant_covers_eight():
    # 8 个文件名应能命中全部 8 款 variant（分布覆盖，非退化到少数几款）。
    import hashlib

    from video_factory.assemble import _KENBURNS_VARIANTS

    variants = set()
    for i in range(400):
        key = f"cover{i}.png:0.0"
        variants.add(int(hashlib.sha1(key.encode("utf-8")).hexdigest(), 16) % _KENBURNS_VARIANTS)
    assert variants == set(range(_KENBURNS_VARIANTS))


# ---------- 片段转场（xfade）+ 转场点 ----------

def _xfade_cmd(runner):
    return next(
        c for c in runner.commands
        if "-filter_complex" in c and "xfade" in c[c.index("-filter_complex") + 1]
    )


def _make_segments(tmp_path, count):
    segs = []
    for i in range(count):
        p = tmp_path / f"segment_{i:02d}.mp4"
        p.write_bytes(b"v")
        segs.append(p)
    return segs


def test_concat_uses_xfade_for_multiple_segments(tmp_path):
    from video_factory.assemble import _concat_segments, _XFADE_TRANSITIONS

    segs = _make_segments(tmp_path, 3)
    runner = _Recorder(probe_duration=30.0)
    points = _concat_segments(
        segs, tmp_path / "segments.txt", tmp_path / "silent.mp4", 30, runner,
        segment_durations=[10.0, 8.0, 6.0],
    )
    cmd = _xfade_cmd(runner)
    fc = cmd[cmd.index("-filter_complex") + 1]
    # 未给 transition_flags → 每段自成一块；块尾段渲染期已 +0.4 补偿，恰抵消 xfade 重叠，
    # 故 offset = 原始累计内容时长：offset1 = 10；offset2 = 10+8 = 18（不再减 i*0.4）。
    assert "offset=10.000" in fc
    assert "offset=18.000" in fc
    assert "duration=0.4" in fc
    assert "[vout]" in cmd and "-an" in cmd
    import re as _re
    used = _re.findall(r"transition=(\w+)", fc)
    assert used and all(t in _XFADE_TRANSITIONS for t in used)
    assert points == [10.0, 18.0]  # 返回的转场时刻 = 各 offset = 原始累计内容时长


def test_concat_xfade_offset_precise_for_many_segments(tmp_path):
    from video_factory.assemble import _concat_segments

    durs = [5.0, 4.0, 3.0, 6.0]
    segs = _make_segments(tmp_path, len(durs))
    runner = _Recorder(probe_duration=30.0)
    points = _concat_segments(
        segs, tmp_path / "s.txt", tmp_path / "out.mp4", 30, runner, segment_durations=durs,
    )
    # offset_i = Σ(前 i 段原始时长)，逐点核对（错一点就黑帧/画面超前）：块尾段 +0.4 补偿
    # 恰抵消 xfade 每次重叠的 0.4s，故直接累计原始时长、不再减 i*T。
    expected, cum = [], 0.0
    for i in range(1, len(durs)):
        cum += durs[i - 1]
        expected.append(round(cum, 3))
    assert points == expected  # [5.0, 9.0, 12.0]


def test_concat_single_segment_uses_demux(tmp_path):
    from video_factory.assemble import _concat_segments

    segs = _make_segments(tmp_path, 1)
    runner = _Recorder(probe_duration=30.0)
    points = _concat_segments(
        segs, tmp_path / "s.txt", tmp_path / "out.mp4", 30, runner, segment_durations=[10.0],
    )
    assert points == []
    demux = next(c for c in runner.commands if "-f" in c and "concat" in c)
    assert "-safe" in demux and "0" in demux
    assert not any("xfade" in " ".join(c) for c in runner.commands)


def test_concat_short_segment_falls_back_to_demux(tmp_path):
    from video_factory.assemble import _concat_segments

    segs = _make_segments(tmp_path, 2)
    runner = _Recorder(probe_duration=30.0)
    # 第二段 0.3s < 转场时长 0.4s → 不满足 xfade 条件（offset 会算成负），硬切回退
    points = _concat_segments(
        segs, tmp_path / "s.txt", tmp_path / "out.mp4", 30, runner, segment_durations=[5.0, 0.3],
    )
    assert points == []
    assert not any("xfade" in " ".join(c) for c in runner.commands)
    assert any("-f" in c and "concat" in c for c in runner.commands)


def test_concat_xfade_failure_falls_back_to_demux(tmp_path):
    from video_factory.assemble import _concat_segments

    segs = _make_segments(tmp_path, 2)

    def _is_xfade(command):
        # 精确判定 xfade 命令：看 -filter_complex 参数里是否含 xfade（不能用整条命令的
        # 子串匹配——本用例的 tmp 路径本身就含 "xfade"，会误伤回退的 demux 命令）。
        return "-filter_complex" in command and "xfade" in command[
            command.index("-filter_complex") + 1
        ]

    class XfadeFailRunner(_Recorder):
        def __call__(self, command, **kwargs):
            if _is_xfade(command):
                self.commands.append(command)
                return subprocess.CompletedProcess(command, 1, stdout="", stderr="xfade boom")
            return super().__call__(command, **kwargs)

    runner = XfadeFailRunner(probe_duration=30.0)
    points = _concat_segments(
        segs, tmp_path / "s.txt", tmp_path / "out.mp4", 30, runner, segment_durations=[5.0, 4.0],
    )
    # xfade 失败 → 降级硬切，不抛错、转场点清空（降级不阻断成片）
    assert points == []
    assert any(_is_xfade(c) for c in runner.commands)  # 试过 xfade
    assert any("-f" in c and "concat" in c for c in runner.commands)  # 回退到 demux


def test_render_xfade_records_transition_points_and_stays_aligned(tmp_path):
    audio = tmp_path / "voiceover.wav"
    audio.write_bytes(b"audio")
    plan = build_assembly_plan(REWRITE, _clips(), target_duration=90.0)
    runner = _Recorder(probe_duration=60.0)
    outputs = render_assembly(plan, tmp_path, audio_path=audio, runner=runner)

    manifest = json.loads(outputs["assembly_plan"].read_text(encoding="utf-8"))
    # 3 段 → 2 个转场点写进计划，供特效层配音
    assert len(manifest["transition_points"]) == 2
    assert all(p > 0 for p in manifest["transition_points"])
    # 关键闭环：xfade 吃掉的时长由下游 tpad + -t（截到配音时长）兜底，duration_aligned 仍成立
    assert manifest["duration_aligned"] is True


# ---------- 内容时间轴守恒：块尾段 +0.4s 补偿 + 转场密度（章节边界 + 节内 15s） ----------

def test_segment_command_compensates_duration_before_transition():
    """后接转场的段渲染时 +0.4s（视频靠 -t、图片靠帧数）；不接转场的段用原始时长。"""
    from video_factory.assemble import _build_segment_command, _XFADE_DURATION
    from video_factory.asset_pool import ClipSlice

    vid = ClipSlice(path=Path("a.mp4"), start=2.0, duration=5.0)
    # 后接转场 → -t 补偿 +0.4（多切 0.4s 供 xfade 重叠吃掉），起点 -ss 不动
    cmd = _build_segment_command(
        vid, Path("o.mp4"), 1920, 1080, 30, "pad", extra_duration=_XFADE_DURATION
    )
    assert cmd[cmd.index("-t") + 1] == "5.4"
    assert cmd[cmd.index("-ss") + 1] == "2"
    # 不接转场（默认 extra=0）→ 原始时长
    cmd0 = _build_segment_command(vid, Path("o.mp4"), 1920, 1080, 30, "pad")
    assert cmd0[cmd0.index("-t") + 1] == "5"
    # 图片切片：帧数按补偿后时长算 round((4+0.4)*30)=132
    img = ClipSlice(path=Path("p.png"), start=0.0, duration=4.0)
    cimg = _build_segment_command(
        img, Path("o.mp4"), 1080, 1920, 30, "pad", extra_duration=_XFADE_DURATION
    )
    assert "-frames:v 132" in " ".join(cimg)


def test_transition_flags_chapter_boundary_and_15s_rule():
    """密度规则：①跨节必转场；②同节内累计 ≥15s 转一次并清零；③其余硬切；末段恒 False。"""
    from video_factory.assemble import (
        _transition_flags,
        INTRA_SECTION_TRANSITION_INTERVAL,
    )

    assert INTRA_SECTION_TRANSITION_INTERVAL == 15.0
    # sec0 四段各 5s（20s），sec1 两段各 5s（10s）
    section_indices = [0, 0, 0, 0, 1, 1]
    durations = [5.0, 5.0, 5.0, 5.0, 5.0, 5.0]
    # i=2：sec0 内累计 15s → 转；i=3：跨节 sec0→sec1 → 必转；其余硬切
    assert _transition_flags(section_indices, durations) == [
        False, False, True, True, False, False,
    ]
    # 纯短节（无内部满 15s、无跨节）→ 全硬切
    assert _transition_flags([0, 0, 0], [4.0, 4.0, 4.0]) == [False, False, False]


def test_concat_groups_hard_cut_segments_into_block(tmp_path):
    """连续无转场的段先 concat demuxer 硬切成中间块，再与其他块做 xfade（子组 concat）。"""
    from video_factory.assemble import _concat_segments

    segs = _make_segments(tmp_path, 3)
    runner = _Recorder(probe_duration=30.0)
    # 仅段1后转场 → 块[0,1]（硬切合并）+ 块[2]（单段）
    points = _concat_segments(
        segs, tmp_path / "s.txt", tmp_path / "silent.mp4", 30, runner,
        segment_durations=[6.0, 6.0, 6.0],
        transition_flags=[False, True, False],
    )
    # 一次转场：块[0,1] 内容 12s → offset 12（成片时间轴上的节内边界）
    assert points == [12.0]
    # 块内硬切：写出 block_00.txt 清单并对块 0 跑 concat demuxer
    block_manifest = tmp_path / "block_00.txt"
    assert block_manifest.exists()
    txt = block_manifest.read_text(encoding="utf-8")
    assert "segment_00.mp4" in txt and "segment_01.mp4" in txt
    # 块级 xfade：offset=12
    xfade = next(
        c for c in runner.commands
        if "-filter_complex" in c and "xfade" in c[c.index("-filter_complex") + 1]
    )
    assert "offset=12.000" in xfade[xfade.index("-filter_complex") + 1]


def test_user_scenario_52_segments_density_and_transition_points(tmp_path):
    """用户真实成片回归：6 节 52 片、两长节 110s/107s。验证转场次数由旧方案 51 次降到
    18 次（章节边界 5 + sec0 内部 7 + sec2 内部 6），且每个转场点都落在内容时间轴的正确
    位置（节边界 + 节内 15s 刻度）——特效音因此自动归位、画面不再逐渐超前口播。"""
    from video_factory.assemble import _transition_flags, _concat_segments

    # 逐节构造（节索引, 段时长）：时长取整便于 15s 整除；总 279s、共 52 段、两长节 110/107。
    layout = [
        (0, [5.0] * 22),          # sec0：110s（22 段）
        (1, [4.0] * 3),           # sec1：12s（3 段）
        (2, [5.0] * 20 + [7.0]),  # sec2：107s（21 段）
        (3, [9.0] * 2),           # sec3：18s
        (4, [8.0] * 2),           # sec4：16s
        (5, [8.0] * 2),           # sec5：16s
    ]
    section_indices, durations = [], []
    for sec, durs in layout:
        for d in durs:
            section_indices.append(sec)
            durations.append(d)
    assert len(durations) == 52
    assert sum(durations) == pytest.approx(279.0)

    flags = _transition_flags(section_indices, durations)
    assert sum(flags) == 18  # 旧方案 51 次 → 新方案 18 次（合理密度）

    segs = _make_segments(tmp_path, 52)
    runner = _Recorder(probe_duration=279.0)
    points = _concat_segments(
        segs, tmp_path / "s.txt", tmp_path / "silent.mp4", 30, runner,
        segment_durations=durations, transition_flags=flags,
    )
    expected = [
        15.0, 30.0, 45.0, 60.0, 75.0, 90.0, 105.0,  # sec0 内部每 15s（7 次）
        110.0,                                       # sec0→sec1 章节边界
        122.0,                                       # sec1→sec2 章节边界
        137.0, 152.0, 167.0, 182.0, 197.0, 212.0,    # sec2 内部每 15s（6 次）
        229.0,                                       # sec2→sec3 章节边界
        247.0,                                       # sec3→sec4 章节边界
        263.0,                                       # sec4→sec5 章节边界
    ]
    assert points == expected
    assert len(points) == 18
    # 5 个章节边界（各节累计时长）必须都在转场点里，节切换处的特效音才归位
    for boundary in (110.0, 122.0, 229.0, 247.0, 263.0):
        assert boundary in points
    # 转场点递增、且严格落在总时长之内（不越界）
    assert points == sorted(points)
    assert points[-1] < sum(durations)


# ---------- 配音语速（P12：语速显式时跳过 atempo、分镜跟随实测配音时长） ----------

def test_cli_voice_speed_routes_to_tts_and_follows_audio(tmp_path, monkeypatch):
    from video_factory import assemble as assemble_mod
    from video_factory import asset_pool as asset_pool_mod

    rewrite_path = tmp_path / "rewrite.json"
    rewrite_path.write_text(json.dumps(REWRITE, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "a.mp4").write_bytes(b"x")
    out = tmp_path / "out"

    captured = {}

    def fake_synth(narration, path, config):
        captured["speed"] = config.speed
        Path(path).write_bytes(b"wav")

        class _R:
            pass

        result = _R()
        result.path = Path(path)
        return result

    runner = _Recorder(probe_duration=50.0)  # 实测配音 50s（语速加快后变短）
    real_scan = asset_pool_mod.scan_asset_pool
    real_render = assemble_mod.render_assembly
    monkeypatch.setattr(assemble_mod, "synthesize_voiceover_text", fake_synth)
    monkeypatch.setattr(assemble_mod, "_probe_duration", lambda p, r: 50.0)
    monkeypatch.setattr(
        assemble_mod, "scan_asset_pool", lambda directory: real_scan(directory, runner=runner)
    )
    monkeypatch.setattr(
        assemble_mod, "render_assembly",
        lambda plan, output_dir, **kwargs: real_render(plan, output_dir, runner=runner, **kwargs),
    )

    code = main([
        "--rewrite", str(rewrite_path), "--assets", str(tmp_path),
        "--tts", "doubao", "--voice-speed", "1.3",
        "--duration", "90", "--output", str(out),
    ])
    assert code == 0
    assert captured["speed"] == 1.3                       # 语速进 TTSConfig
    plan = json.loads((out / "assembly_plan.json").read_text(encoding="utf-8"))
    assert plan["target_duration_seconds"] == 50.0        # 分配计划以实测配音时长为轴
    assert not (out / "voiceover_fitted.wav").exists()    # atempo 微调已让位


# ---------- 主时间轴（P16）：assemble 产出时机 = _fit_audio_to_target 之后 ----------

def test_cli_produces_timeline_after_fit(tmp_path, monkeypatch):
    """现场 TTS + atempo 微调后，produce_timeline 收到的是 fitted 音轨（对齐最终那条）。"""
    from video_factory import assemble as assemble_mod
    from video_factory import asset_pool as asset_pool_mod
    from video_factory import timeline as timeline_mod

    rewrite_path = tmp_path / "rewrite.json"
    rewrite_path.write_text(json.dumps(REWRITE, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "a.mp4").write_bytes(b"x")
    out = tmp_path / "out"

    def fake_synth(narration, path, config):
        Path(path).write_bytes(b"wav")

        class _R:
            pass

        result = _R()
        result.path = Path(path)
        return result

    captured = {}

    def fake_produce(audio_path, full_text, output_dir, runner=None):
        captured["audio_path"] = Path(audio_path)
        captured["full_text"] = full_text
        return Path(output_dir) / "timeline.json"

    def fake_fit(audio_path, target, output_dir, runner):
        # 模拟 atempo 微调：产出 fitted 音轨并返回它（真实 subprocess.run 在假 wav 上会失败）。
        fitted = Path(output_dir) / "voiceover_fitted.wav"
        fitted.write_bytes(b"fitted")
        return fitted, "已微调"

    runner = _Recorder(probe_duration=50.0)
    real_scan = asset_pool_mod.scan_asset_pool
    real_render = assemble_mod.render_assembly
    monkeypatch.setattr(assemble_mod, "synthesize_voiceover_text", fake_synth)
    monkeypatch.setattr(assemble_mod, "_probe_duration", lambda p, r: 50.0)
    monkeypatch.setattr(assemble_mod, "_fit_audio_to_target", fake_fit)
    monkeypatch.setattr(timeline_mod, "produce_timeline", fake_produce)
    monkeypatch.setattr(
        assemble_mod, "scan_asset_pool", lambda directory: real_scan(directory, runner=runner)
    )
    monkeypatch.setattr(
        assemble_mod, "render_assembly",
        lambda plan, output_dir, **kwargs: real_render(plan, output_dir, runner=runner, **kwargs),
    )

    code = main([
        "--rewrite", str(rewrite_path), "--assets", str(tmp_path),
        "--tts", "doubao", "--duration", "90", "--output", str(out),
    ])
    assert code == 0
    # 关键：对齐的是变速后的 voiceover_fitted.wav，而非原始 voiceover.wav。
    assert captured["audio_path"] == out / "voiceover_fitted.wav"
    assert (out / "voiceover_fitted.wav").exists()
    assert captured["full_text"] == REWRITE["full_voiceover"]


def test_cli_produces_timeline_for_user_audio(tmp_path, monkeypatch):
    """用户自带 --audio 分支同样产出时间轴（收到用户那条音轨，不变速）。"""
    from video_factory import assemble as assemble_mod
    from video_factory import asset_pool as asset_pool_mod
    from video_factory import timeline as timeline_mod

    rewrite_path = tmp_path / "rewrite.json"
    rewrite_path.write_text(json.dumps(REWRITE, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "a.mp4").write_bytes(b"x")
    user_audio = tmp_path / "my_voice.wav"
    user_audio.write_bytes(b"wav")
    out = tmp_path / "out"

    captured = {}

    def fake_produce(audio_path, full_text, output_dir, runner=None):
        captured["audio_path"] = Path(audio_path)
        return Path(output_dir) / "timeline.json"

    runner = _Recorder(probe_duration=90.0)
    real_scan = asset_pool_mod.scan_asset_pool
    real_render = assemble_mod.render_assembly
    monkeypatch.setattr(timeline_mod, "produce_timeline", fake_produce)
    monkeypatch.setattr(
        assemble_mod, "scan_asset_pool", lambda directory: real_scan(directory, runner=runner)
    )
    monkeypatch.setattr(
        assemble_mod, "render_assembly",
        lambda plan, output_dir, **kwargs: real_render(plan, output_dir, runner=runner, **kwargs),
    )

    code = main([
        "--rewrite", str(rewrite_path), "--assets", str(tmp_path),
        "--audio", str(user_audio), "--duration", "90", "--output", str(out),
    ])
    assert code == 0
    assert captured["audio_path"] == user_audio  # 用户自带音轨原样传入，不变速


# ---------- 拍级配图：--ordered-assets 顺序分配（P13 任务B） ----------

def test_build_ordered_assembly_plan_uses_images_in_beat_order(tmp_path):
    from video_factory.assemble import build_ordered_assembly_plan

    imgs = []
    for i in range(3):
        p = tmp_path / f"img_{i:02d}.png"
        p.write_bytes(b"png")
        imgs.append(p)
    plan = build_ordered_assembly_plan(REWRITE, imgs, target_duration=20.0)
    slices = [s for a in plan.allocations for s in a.slices]
    assert len(slices) >= 3  # 20s/5s ≈ 4 拍上下（按节字数分配有波动）
    assert slices[0].path == imgs[0]  # 第 0 拍用第 0 张
    assert slices[1].path == imgs[1]  # 第 1 拍用第 1 张
    # 总时长守恒（拍时长求和 = 目标）
    assert sum(s.duration for s in slices) == pytest.approx(20.0, abs=0.1)


def test_build_ordered_assembly_plan_tail_loops_when_images_short(tmp_path):
    from video_factory.assemble import build_ordered_assembly_plan

    only = tmp_path / "img_00.png"
    only.write_bytes(b"png")
    plan = build_ordered_assembly_plan(REWRITE, [only], target_duration=20.0)
    slices = [s for a in plan.allocations for s in a.slices]
    assert len(slices) >= 2
    assert all(s.path == only for s in slices)  # 图不足 → 尾部循环用最后一张


def test_build_ordered_assembly_plan_groups_beats_into_real_sections(tmp_path):
    """拍归组回真实节：节数=真实节数（hook+正题），标题无「-拍N」，切片仍按拍序。"""
    from video_factory.assemble import build_ordered_assembly_plan

    imgs = []
    for i in range(20):
        p = tmp_path / f"img_{i:02d}.png"
        p.write_bytes(b"png")
        imgs.append(p)
    plan = build_ordered_assembly_plan(REWRITE, imgs, target_duration=20.0)

    # 真实节：hook + 第一节 + 第二节 = 3 个 allocation（不再是每拍一个 SectionAllocation）
    assert len(plan.allocations) == 3
    assert list(plan.section_titles) == ["hook", "第一节", "第二节"]
    # 内部「-拍N」标签绝不泄漏进节标题（项目红线）
    assert all("-拍" not in t for t in plan.section_titles)
    # index 与节序一一对应（供 _write_plan_json 取 section_titles[index]）
    assert [a.index for a in plan.allocations] == [0, 1, 2]
    # 每节至少 1 拍；摊平后第 k 拍仍用第 k 图（分段渲染顺序与图序不变）
    assert all(len(a.slices) >= 1 for a in plan.allocations)
    slices = [s for a in plan.allocations for s in a.slices]
    for k, s in enumerate(slices):
        assert s.path == imgs[k]
    # 每节时长 = 该节各拍时长之和；全片总时长守恒
    for a in plan.allocations:
        assert a.duration == pytest.approx(sum(s.duration for s in a.slices), abs=1e-6)
    assert sum(a.duration for a in plan.allocations) == pytest.approx(20.0, abs=0.1)


# ---------- 变长拍（卡话切）：build_ordered_assembly_plan + timeline_sentences（P16 二期） ----------

def _pipe_rewrite():
    """按 | 精确控制每节句数的 rewrite（配合 mock split_sentences）。"""
    return {
        "hook": "A|B",  # 2 句
        "sections": [{"title": "第一节", "narration": "C|D"}],  # 2 句
        "target_duration_seconds": 90,
    }


def _mock_split_on_pipe(monkeypatch):
    monkeypatch.setattr(
        "video_factory.subtitles.split_sentences",
        lambda text: [p for p in str(text).split("|") if p],
    )


def _pipe_timeline():
    """4 句 timeline：hook(A=3s,B=1s)、第一节(C=3s,D=1s)；各节残拍并入 → 每节 1 拍 4s。"""
    return [
        {"text": "甲", "start": 0.0, "end": 3.0},
        {"text": "乙", "start": 3.0, "end": 4.0},
        {"text": "丙", "start": 4.0, "end": 7.0},
        {"text": "丁", "start": 7.0, "end": 8.0},
    ]


def test_build_ordered_plan_uses_timeline_beat_durations(tmp_path, monkeypatch):
    """给了 timeline：切片时长=拍真实时长，目标时长=Σ拍时长（真实音频跨度），不再是 5s 均分。"""
    from video_factory.assemble import build_ordered_assembly_plan

    _mock_split_on_pipe(monkeypatch)
    imgs = []
    for i in range(2):
        p = tmp_path / f"img_{i:02d}.png"
        p.write_bytes(b"png")
        imgs.append(p)
    plan = build_ordered_assembly_plan(
        _pipe_rewrite(), imgs, target_duration=90.0, timeline_sentences=_pipe_timeline()
    )
    # 目标时长 = Σ拍时长 = 8.0（真实音频跨度），而非 CLI 传的 90
    assert plan.target_duration == pytest.approx(8.0, abs=1e-6)
    # 2 真实节（hook + 第一节），各 1 拍，拍时长 = 句群真实时长 4.0（3.0 封拍 + 1.0 残拍并入）
    assert len(plan.allocations) == 2
    slices = [s for a in plan.allocations for s in a.slices]
    assert [round(s.duration, 3) for s in slices] == [4.0, 4.0]
    # 第 k 拍用第 k 图
    assert slices[0].path == imgs[0] and slices[1].path == imgs[1]


def test_build_ordered_plan_beat_count_matches_image_gen(tmp_path, monkeypatch):
    """拍的唯一权威函数：assemble 拼装侧的切片数与 image_gen 生图侧的拍数逐拍一致。"""
    from video_factory.assemble import build_ordered_assembly_plan
    from video_factory.image_gen import plan_beats_from_timeline

    _mock_split_on_pipe(monkeypatch)
    rewrite, sents = _pipe_rewrite(), _pipe_timeline()
    imgs = []
    for i in range(4):
        p = tmp_path / f"img_{i:02d}.png"
        p.write_bytes(b"png")
        imgs.append(p)

    beats = plan_beats_from_timeline(rewrite, sents)  # 生图侧的拍
    plan = build_ordered_assembly_plan(rewrite, imgs, target_duration=90.0, timeline_sentences=sents)
    slices = [s for a in plan.allocations for s in a.slices]
    assert len(slices) == len(beats)  # 拍数=切片数=图片消费数，天然一致
    assert [round(s.duration, 3) for s in slices] == [round(b.duration, 3) for b in beats]


def test_build_ordered_plan_without_timeline_falls_back_to_5s(tmp_path):
    """无 timeline（timeline_sentences=None）：回落 5s 均分，目标时长=CLI 传值，与今天一致。"""
    from video_factory.assemble import build_ordered_assembly_plan

    imgs = []
    for i in range(6):
        p = tmp_path / f"img_{i:02d}.png"
        p.write_bytes(b"png")
        imgs.append(p)
    plan = build_ordered_assembly_plan(REWRITE, imgs, target_duration=20.0, timeline_sentences=None)
    assert plan.target_duration == pytest.approx(20.0, abs=1e-6)  # 用 CLI 目标，非 Σ拍


def test_build_ordered_plan_timeline_count_mismatch_falls_back(tmp_path, monkeypatch):
    """timeline 句数与 rewrite 每节句数之和对不上 → plan_beats_from_timeline 回 None → 回落 5s。"""
    from video_factory.assemble import build_ordered_assembly_plan

    _mock_split_on_pipe(monkeypatch)
    imgs = []
    for i in range(6):
        p = tmp_path / f"img_{i:02d}.png"
        p.write_bytes(b"png")
        imgs.append(p)
    # rewrite 期望 4 句，却喂 3 句 timeline → 计数不符
    bad_timeline = _pipe_timeline()[:3]
    plan = build_ordered_assembly_plan(
        _pipe_rewrite(), imgs, target_duration=20.0, timeline_sentences=bad_timeline
    )
    assert plan.target_duration == pytest.approx(20.0, abs=1e-6)  # 回落 CLI 目标，非 Σ拍(8)
