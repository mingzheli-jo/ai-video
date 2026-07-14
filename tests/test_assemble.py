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
    concat_cmd = next(c for c in runner.commands if "concat" in c)
    assert "-safe" in concat_cmd and "0" in concat_cmd


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
