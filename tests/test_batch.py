"""batch.py 单测（P9）。

全程 mock STAGE_RUNNERS，绝不真跑任何阶段模块（rewrite/assemble/effects/subtitles）。
覆盖：预设展开优先级、非法 job 记 invalid 且继续、缺 source/assets、单阶段失败不中断整批、
subtitles/effects 开关决定阶段链、字幕 --video 在有/无特效时指向正确文件、dry-run 不调 runner、
--only 过滤、报告汇总数字、各阶段 argv 组装拼写。
"""

import json

import pytest

from video_factory import batch
from video_factory.batch import (
    ResolvedJob,
    build_assemble_argv,
    build_effects_argv,
    build_rewrite_argv,
    build_subtitles_argv,
    build_publish_argv,
    build_voice_argv,
    build_report,
    load_jobs,
    main,
    resolve_all,
    resolve_job,
    run_batch,
    run_job,
    validate_job,
)


# ---------- 测试脚手架：录制式阶段执行器 ----------

class RunnerRecorder:
    """替换 STAGE_RUNNERS 的录制器：记录每个阶段被调用及其 argv，可预设失败阶段。"""

    def __init__(self, fail_stage: str | None = None, fail_code: int = 1, raise_stage: str | None = None):
        self.calls: list[tuple[str, list[str]]] = []
        self.fail_stage = fail_stage
        self.fail_code = fail_code
        self.raise_stage = raise_stage

    def make(self, stage: str, argv_builder):
        def runner(job: ResolvedJob, job_dir):
            self.calls.append((stage, argv_builder(job, job_dir)))
            if stage == self.raise_stage:
                raise RuntimeError("boom")
            if stage == self.fail_stage:
                return self.fail_code
            return 0
        return runner

    @property
    def stages(self) -> list[str]:
        return [stage for stage, _ in self.calls]


@pytest.fixture
def patch_runners(monkeypatch):
    """把 STAGE_RUNNERS 全体替换成录制器，返回 recorder 供断言 argv/阶段链。"""
    def _apply(recorder: RunnerRecorder):
        builders = {
            "rewrite": build_rewrite_argv,
            "voice": build_voice_argv,
            "assemble": build_assemble_argv,
            "effects": build_effects_argv,
            "subtitles": build_subtitles_argv,
            "publish": build_publish_argv,
        }
        patched = {stage: recorder.make(stage, builders[stage]) for stage in builders}
        monkeypatch.setattr(batch, "STAGE_RUNNERS", patched)
        return recorder
    return _apply


def _make_valid_paths(tmp_path):
    """建立一个真实存在的 source 文件与 assets 目录，返回 (source, assets) 字符串。"""
    source = tmp_path / "source.txt"
    source.write_text("原始文案", encoding="utf-8")
    assets = tmp_path / "assets"
    assets.mkdir()
    return str(source), str(assets)


# ---------- 预设展开优先级 ----------

def test_resolve_job_applies_platform_preset(tmp_path):
    job = resolve_job({"source": "s", "assets": "a", "platform": "douyin"}, 0)
    assert job.aspect == "9:16"
    assert job.fit == "blur"
    assert job.duration == 60
    assert job.subtitles is True
    assert job.effects is True
    assert job.name == "job_01"


def test_resolve_job_falls_back_to_global_defaults_without_platform(tmp_path):
    job = resolve_job({"source": "s", "assets": "a"}, 2)
    assert job.aspect == batch.DEFAULT_ASPECT == "16:9"
    assert job.fit == "pad"
    assert job.duration == 90
    # 2026-07-16 用户定案：字幕/氛围粒子与特效一样全默认开
    assert job.subtitles is True
    assert job.effects is True
    assert job.ambient_particles is True
    assert job.name == "job_03"  # index 2 → job_03


def test_resolve_job_explicit_field_overrides_platform_preset(tmp_path):
    job = resolve_job(
        {"source": "s", "assets": "a", "platform": "douyin", "aspect": "1:1", "duration": 15},
        0,
    )
    # job 显式 aspect/duration 覆盖 douyin 预设的 9:16/60
    assert job.aspect == "1:1"
    assert job.duration == 15
    # 未覆盖字段仍随预设
    assert job.fit == "blur"
    assert job.subtitles is True


def test_resolve_job_explicit_false_overrides_preset_true(tmp_path):
    # subtitles=False 是显式覆盖（哪怕 falsy），应压过 douyin 预设的 True
    job = resolve_job(
        {"source": "s", "assets": "a", "platform": "douyin", "subtitles": False, "effects": False},
        0,
    )
    assert job.subtitles is False
    assert job.effects is False


# ---------- 非法 job：记 invalid 且继续 ----------

def test_validate_job_flags_invalid_platform(tmp_path):
    source, assets = _make_valid_paths(tmp_path)
    job = resolve_job({"source": source, "assets": assets, "platform": "youtube"}, 0)
    errors = validate_job(job)
    assert any("platform 非法" in e for e in errors)
    assert any("douyin" in e for e in errors)  # 列出可选值


def test_validate_job_flags_invalid_style(tmp_path):
    source, assets = _make_valid_paths(tmp_path)
    job = resolve_job({"source": source, "assets": assets, "style": "不存在"}, 0)
    errors = validate_job(job)
    assert any("style 非法" in e for e in errors)


def test_validate_job_flags_invalid_aspect(tmp_path):
    source, assets = _make_valid_paths(tmp_path)
    job = resolve_job({"source": source, "assets": assets, "aspect": "21:9"}, 0)
    errors = validate_job(job)
    assert any("aspect 非法" in e for e in errors)


def test_validate_job_missing_source_and_assets(tmp_path):
    job = resolve_job({"name": "j1"}, 0)
    errors = validate_job(job)
    assert any("缺少 source" in e for e in errors)
    assert any("缺少 assets" in e for e in errors)


def test_validate_job_source_path_not_exist(tmp_path):
    assets = tmp_path / "assets"
    assets.mkdir()
    job = resolve_job({"source": str(tmp_path / "nope.txt"), "assets": str(assets)}, 0)
    errors = validate_job(job)
    assert any("source 不存在" in e for e in errors)


def test_run_batch_invalid_job_does_not_stop_others(tmp_path, patch_runners):
    recorder = patch_runners(RunnerRecorder())
    source, assets = _make_valid_paths(tmp_path)
    raw = [
        {"name": "bad", "platform": "youtube", "source": source, "assets": assets},
        # good 显式关特效关字幕，阶段链固定为 rewrite→assemble，便于断言
        {"name": "good", "source": source, "assets": assets, "effects": False, "subtitles": False},
    ]
    reports = run_batch(resolve_all(raw))
    status = {r.name: r.status for r in reports}
    assert status["bad"] == "invalid"
    assert status["good"] == "ok"
    # 非法 job 不进 runner，只有 good 的阶段被调用
    assert recorder.stages == ["rewrite", "voice", "assemble", "publish"]


# ---------- 阶段失败不中断整批 ----------

def test_single_stage_failure_records_stage_failed_and_continues(tmp_path, patch_runners):
    recorder = patch_runners(RunnerRecorder(fail_stage="assemble"))
    source, assets = _make_valid_paths(tmp_path)
    raw = [
        {"name": "j1", "source": source, "assets": assets},
        {"name": "j2", "source": source, "assets": assets},
    ]
    reports = run_batch(resolve_all(raw))
    j1 = next(r for r in reports if r.name == "j1")
    j2 = next(r for r in reports if r.name == "j2")
    assert j1.status == "failed"
    assert j1.stage_failed == "assemble"
    assert "非 0" in j1.error
    # j2 依然被执行
    assert j2.status == "failed"  # 同样 mock 下 assemble 失败


def test_stage_exception_records_failed(tmp_path, patch_runners):
    recorder = patch_runners(RunnerRecorder(raise_stage="rewrite"))
    source, assets = _make_valid_paths(tmp_path)
    job = resolve_job({"name": "j", "source": source, "assets": assets}, 0)
    report = run_job(job)
    assert report.status == "failed"
    assert report.stage_failed == "rewrite"
    assert "异常" in report.error


def test_failure_stops_later_stages(tmp_path, patch_runners):
    recorder = patch_runners(RunnerRecorder(fail_stage="rewrite"))
    source, assets = _make_valid_paths(tmp_path)
    job = resolve_job({"name": "j", "source": source, "assets": assets, "subtitles": True}, 0)
    run_job(job)
    # rewrite 失败后不应再调 assemble/effects/subtitles
    assert recorder.stages == ["rewrite"]


# ---------- subtitles/effects 开关决定阶段链 ----------

def test_stage_chain_effects_off_subtitles_off(tmp_path, patch_runners):
    recorder = patch_runners(RunnerRecorder())
    source, assets = _make_valid_paths(tmp_path)
    job = resolve_job(
        {"name": "j", "source": source, "assets": assets, "effects": False, "subtitles": False},
        0,
    )
    run_job(job)
    assert recorder.stages == ["rewrite", "voice", "assemble", "publish"]


def test_stage_chain_effects_on_subtitles_on(tmp_path, patch_runners):
    recorder = patch_runners(RunnerRecorder())
    source, assets = _make_valid_paths(tmp_path)
    job = resolve_job(
        {"name": "j", "source": source, "assets": assets, "effects": True, "subtitles": True},
        0,
    )
    run_job(job)
    assert recorder.stages == ["rewrite", "voice", "assemble", "effects", "subtitles", "publish"]


def test_stage_chain_effects_on_subtitles_off(tmp_path, patch_runners):
    recorder = patch_runners(RunnerRecorder())
    source, assets = _make_valid_paths(tmp_path)
    job = resolve_job(
        {"name": "j", "source": source, "assets": assets, "effects": True, "subtitles": False},
        0,
    )
    run_job(job)
    assert recorder.stages == ["rewrite", "voice", "assemble", "effects", "publish"]


# ---------- 特效音开关 / 音量 ----------

def test_resolve_job_sfx_defaults_on():
    job = resolve_job({"name": "j", "source": "s", "assets": "a"}, 0)
    assert job.sfx is True
    assert job.sfx_volume is None


def test_build_effects_argv_default_keeps_sfx_on():
    job = resolve_job({"name": "j", "source": "s", "assets": "a"}, 0)
    argv = build_effects_argv(job, job.output)
    assert "--no-sfx" not in argv  # 默认开，不传关闭标志
    assert "--sfx-volume" not in argv  # 未指定音量则不传，effects 用默认 0.35


def test_build_effects_argv_disables_sfx():
    job = resolve_job({"name": "j", "source": "s", "assets": "a", "sfx": False}, 0)
    argv = build_effects_argv(job, job.output)
    assert "--no-sfx" in argv


def test_build_effects_argv_passes_sfx_volume():
    job = resolve_job({"name": "j", "source": "s", "assets": "a", "sfx_volume": 0.5}, 0)
    argv = build_effects_argv(job, job.output)
    assert "--no-sfx" not in argv
    assert argv[argv.index("--sfx-volume") + 1] == "0.5"


def test_build_effects_argv_passes_timeline_unconditionally(tmp_path):
    """P16：effects argv 无条件带 --timeline 指向 job_dir/timeline.json（不存在时消费方自然回落）。"""
    job = resolve_job({"name": "j", "source": "s", "assets": "a"}, 0)
    job_dir = tmp_path / "jobout"
    argv = build_effects_argv(job, job_dir)
    assert "--timeline" in argv
    assert argv[argv.index("--timeline") + 1] == str(job_dir / "timeline.json")


def test_build_subtitles_argv_passes_timeline_unconditionally(tmp_path):
    """P16：subtitles argv 无条件带 --timeline 指向 job_dir/timeline.json。"""
    job = resolve_job({"name": "j", "source": "s", "assets": "a"}, 0)
    job_dir = tmp_path / "jobout"
    job_dir.mkdir()
    argv = build_subtitles_argv(job, job_dir)
    assert "--timeline" in argv
    assert argv[argv.index("--timeline") + 1] == str(job_dir / "timeline.json")


# ---------- 字幕 --video 指向：有/无特效 ----------

def test_subtitles_video_points_to_effects_output_when_effects_on(tmp_path):
    job = resolve_job(
        {"name": "j", "source": "s", "assets": "a", "effects": True, "subtitles": True},
        0,
    )
    job_dir = job.output
    argv = build_subtitles_argv(job, job_dir)
    video_idx = argv.index("--video")
    assert argv[video_idx + 1].endswith("release_with_effects.mp4")


def test_subtitles_video_points_to_release_when_effects_off(tmp_path):
    job = resolve_job(
        {"name": "j", "source": "s", "assets": "a", "effects": False, "subtitles": True},
        0,
    )
    argv = build_subtitles_argv(job, job.output)
    video_idx = argv.index("--video")
    assert argv[video_idx + 1].endswith("release.mp4")
    assert not argv[video_idx + 1].endswith("release_with_effects.mp4")


def test_subtitles_audio_added_only_when_voiceover_exists(tmp_path):
    job = resolve_job({"name": "j", "source": "s", "assets": "a"}, 0)
    job_dir = tmp_path / "jobout"
    job_dir.mkdir()
    # 无 voiceover.wav → 不带 --audio
    argv_no = build_subtitles_argv(job, job_dir)
    assert "--audio" not in argv_no
    # 有 voiceover.wav → 带 --audio 指向它
    (job_dir / "voiceover.wav").write_text("x", encoding="utf-8")
    argv_yes = build_subtitles_argv(job, job_dir)
    assert "--audio" in argv_yes
    assert argv_yes[argv_yes.index("--audio") + 1].endswith("voiceover.wav")


def test_subtitles_audio_prefers_plan_recorded_fitted_audio(tmp_path):
    """字幕时间轴必须用实际混入成片的音频。assemble 触发 atempo 变速微调后，成片用的是
    voiceover_fitted.wav；build_subtitles_argv 应读 assembly_plan.json 的 audio_path 指向
    fitted 版，而不是硬猜 voiceover.wav（否则字幕与配音系统性错位）。"""
    job = resolve_job({"name": "j", "source": "s", "assets": "a"}, 0)
    job_dir = tmp_path / "jobout"
    job_dir.mkdir()
    # 两份都在磁盘：原始 voiceover.wav（变速前）与真正混入成片的 fitted 版。
    (job_dir / "voiceover.wav").write_text("raw", encoding="utf-8")
    fitted = job_dir / "voiceover_fitted.wav"
    fitted.write_text("fitted", encoding="utf-8")
    (job_dir / "assembly_plan.json").write_text(
        json.dumps({"audio_path": str(fitted)}, ensure_ascii=False), encoding="utf-8"
    )
    argv = build_subtitles_argv(job, job_dir)
    picked = argv[argv.index("--audio") + 1]
    assert picked.endswith("voiceover_fitted.wav")  # 取到 fitted 而非裸 voiceover.wav


def test_subtitles_audio_falls_back_when_plan_missing(tmp_path):
    """无 assembly_plan.json（如只跑到 rewrite 就中断重跑）时，回落到 voiceover.wav 不炸。"""
    job = resolve_job({"name": "j", "source": "s", "assets": "a"}, 0)
    job_dir = tmp_path / "jobout"
    job_dir.mkdir()
    (job_dir / "voiceover.wav").write_text("raw", encoding="utf-8")
    argv = build_subtitles_argv(job, job_dir)
    assert argv[argv.index("--audio") + 1].endswith("voiceover.wav")


# ---------- dry-run 不调 runner ----------

def test_dry_run_does_not_invoke_runners(tmp_path, patch_runners, capsys):
    recorder = patch_runners(RunnerRecorder())
    source, assets = _make_valid_paths(tmp_path)
    jobs_file = tmp_path / "jobs.json"
    jobs_file.write_text(
        json.dumps({"jobs": [{"name": "j", "source": source, "assets": assets}]}),
        encoding="utf-8",
    )
    code = main(["--jobs", str(jobs_file), "--dry-run"])
    assert code == 0
    assert recorder.calls == []  # 一个 runner 都没调
    out = capsys.readouterr().out
    assert "rewrite:" in out
    assert "assemble:" in out


# ---------- --only 过滤 ----------

def test_only_filter_runs_single_job(tmp_path, patch_runners):
    recorder = patch_runners(RunnerRecorder())
    source, assets = _make_valid_paths(tmp_path)
    raw = [
        {"name": "alpha", "source": source, "assets": assets},
        {"name": "beta", "source": source, "assets": assets},
    ]
    reports = run_batch(resolve_all(raw), only="beta")
    assert [r.name for r in reports] == ["beta"]


# ---------- 报告汇总数字 ----------

def test_report_summary_counts(tmp_path, patch_runners):
    recorder = patch_runners(RunnerRecorder(fail_stage="assemble"))
    source, assets = _make_valid_paths(tmp_path)
    raw = [
        {"name": "ok_job", "source": source, "assets": assets, "effects": False},
        {"name": "bad_platform", "platform": "youtube", "source": source, "assets": assets},
    ]
    # ok_job 会因 assemble fail 记 failed；bad_platform 记 invalid
    reports = run_batch(resolve_all(raw))
    report = build_report(reports)
    summary = report["summary"]
    assert summary["total"] == 2
    assert summary["failed"] == 1
    assert summary["invalid"] == 1
    assert summary["ok"] == 0


def test_report_all_ok_summary(tmp_path, patch_runners):
    recorder = patch_runners(RunnerRecorder())
    source, assets = _make_valid_paths(tmp_path)
    raw = [{"name": f"j{i}", "source": source, "assets": assets, "effects": False} for i in range(3)]
    reports = run_batch(resolve_all(raw))
    summary = build_report(reports)["summary"]
    assert summary == {"total": 3, "ok": 3, "failed": 0, "invalid": 0}


# ---------- argv 组装拼写正确性 ----------

def test_rewrite_argv_spelling(tmp_path):
    job = resolve_job(
        {"name": "j", "source": "src.srt", "assets": "a", "duration": 45, "brief": "面向新手", "style": "tutorial"},
        0,
    )
    argv = build_rewrite_argv(job, job.output)
    assert argv[:4] == ["--source", "src.srt", "--duration", "45"]
    assert "--brief" in argv and argv[argv.index("--brief") + 1] == "面向新手"
    assert "--style" in argv and argv[argv.index("--style") + 1] == "tutorial"
    assert "--output" in argv


def test_assemble_argv_spelling_and_paths(tmp_path):
    job = resolve_job(
        {"name": "j", "source": "s", "assets": "assets_dir", "platform": "douyin",
         "tts": "openai", "voice": "shimmer", "bgm": "bgm.mp3", "bgm_volume": 0.15},
        0,
    )
    argv = build_assemble_argv(job, job.output)
    assert "--rewrite" in argv and argv[argv.index("--rewrite") + 1].endswith("rewrite.json")
    assert argv[argv.index("--assets") + 1] == "assets_dir"
    assert argv[argv.index("--aspect") + 1] == "9:16"  # 来自 douyin 预设
    assert argv[argv.index("--fit") + 1] == "blur"
    assert argv[argv.index("--tts") + 1] == "openai"
    assert argv[argv.index("--voice") + 1] == "shimmer"
    assert argv[argv.index("--bgm") + 1] == "bgm.mp3"
    assert argv[argv.index("--bgm-volume") + 1] == "0.15"
    assert "--duration" in argv


def test_assemble_argv_omits_bgm_volume_without_bgm(tmp_path):
    job = resolve_job({"name": "j", "source": "s", "assets": "a", "bgm_volume": 0.2}, 0)
    argv = build_assemble_argv(job, job.output)
    # 没给 bgm，就不应出现 --bgm 或 --bgm-volume
    assert "--bgm" not in argv
    assert "--bgm-volume" not in argv


def test_effects_argv_spelling(tmp_path):
    job = resolve_job({"name": "j", "source": "s", "assets": "a", "lower_thirds": True}, 0)
    argv = build_effects_argv(job, job.output)
    assert argv[argv.index("--video") + 1].endswith("release.mp4")
    assert argv[argv.index("--plan") + 1].endswith("assembly_plan.json")
    assert argv[argv.index("--rewrite") + 1].endswith("rewrite.json")
    assert "--lower-thirds" in argv
    assert "--output" in argv


def test_effects_argv_omits_lower_thirds_when_off(tmp_path):
    job = resolve_job({"name": "j", "source": "s", "assets": "a"}, 0)
    argv = build_effects_argv(job, job.output)
    assert "--lower-thirds" not in argv


# ---------- load_jobs 校验 ----------

def test_load_jobs_missing_file_raises(tmp_path):
    with pytest.raises(batch.BatchError, match="不存在"):
        load_jobs(tmp_path / "nope.json")


def test_load_jobs_rejects_non_object_top(tmp_path):
    f = tmp_path / "jobs.json"
    f.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    with pytest.raises(batch.BatchError):
        load_jobs(f)


def test_load_jobs_rejects_empty_jobs(tmp_path):
    f = tmp_path / "jobs.json"
    f.write_text(json.dumps({"jobs": []}), encoding="utf-8")
    with pytest.raises(batch.BatchError, match="非空"):
        load_jobs(f)


def test_load_jobs_reads_valid(tmp_path):
    f = tmp_path / "jobs.json"
    f.write_text(json.dumps({"jobs": [{"source": "s", "assets": "a"}]}), encoding="utf-8")
    jobs = load_jobs(f)
    assert len(jobs) == 1


# ---------- main 端到端（mock runner）：写报告、汇总输出 ----------

def test_main_end_to_end_writes_report(tmp_path, patch_runners, capsys):
    patch_runners(RunnerRecorder())
    source, assets = _make_valid_paths(tmp_path)
    jobs_file = tmp_path / "jobs.json"
    jobs_file.write_text(
        json.dumps({"jobs": [{"name": "j", "source": source, "assets": assets, "effects": False}]}),
        encoding="utf-8",
    )
    report_dir = tmp_path / "out"
    code = main(["--jobs", str(jobs_file), "--report-dir", str(report_dir)])
    assert code == 0
    report_path = report_dir / "batch_report.json"
    assert report_path.exists()
    body = json.loads(report_path.read_text(encoding="utf-8"))
    assert body["summary"]["ok"] == 1
    assert body["jobs"][0]["name"] == "j"


def test_main_missing_jobs_file_returns_1(tmp_path, capsys):
    code = main(["--jobs", str(tmp_path / "nope.json")])
    assert code == 1
    assert "批量任务失败" in capsys.readouterr().out


# ---------- 审查回归：SystemExit 隔离 / 解析隔离 / --only / audio ----------

def test_stage_systemexit_recorded_as_failed_and_batch_continues(tmp_path, patch_runners, monkeypatch):
    patch_runners(RunnerRecorder())

    def argparse_explodes(job, job_dir):
        raise SystemExit(2)  # argparse 参数错误的真实形态（不继承 Exception）

    monkeypatch.setitem(batch.STAGE_RUNNERS, "rewrite", argparse_explodes)
    source, assets = _make_valid_paths(tmp_path)
    raw = [
        {"name": "j1", "source": source, "assets": assets, "output": str(tmp_path / "o1"),
         "effects": False, "subtitles": False},
        {"name": "j2", "source": source, "assets": assets, "output": str(tmp_path / "o2"),
         "effects": False, "subtitles": False},
    ]
    reports = run_batch(resolve_all(raw))  # 必须能返回，不许 SystemExit 击穿整批
    assert [r.status for r in reports] == ["failed", "failed"]
    assert all(r.stage_failed == "rewrite" for r in reports)
    assert "SystemExit 2" in reports[0].error


def test_stage_systemexit_zero_treated_as_success(tmp_path, patch_runners, monkeypatch):
    patch_runners(RunnerRecorder())

    def clean_exit(job, job_dir):
        raise SystemExit(0)  # 下游用 sys.exit(0) 正常收尾的形态

    monkeypatch.setitem(batch.STAGE_RUNNERS, "rewrite", clean_exit)
    source, assets = _make_valid_paths(tmp_path)
    job = resolve_job(
        {"name": "j", "source": source, "assets": assets, "output": str(tmp_path / "o"),
         "effects": False, "subtitles": False}, 0)
    assert run_job(job).status == "ok"


def test_resolve_all_isolates_unparseable_duration(tmp_path, patch_runners):
    patch_runners(RunnerRecorder())
    source, assets = _make_valid_paths(tmp_path)
    raw = [
        {"name": "bad", "source": source, "assets": assets, "duration": "oops"},
        {"name": "good", "source": source, "assets": assets, "output": str(tmp_path / "o"),
         "effects": False, "subtitles": False},
    ]
    reports = run_batch(resolve_all(raw))  # 单个 job 字段类型错误不许击穿整批
    status = {r.name: r.status for r in reports}
    assert status["bad"] == "invalid"
    assert "无法解析" in next(r for r in reports if r.name == "bad").error
    assert status["good"] == "ok"


def test_validate_job_rejects_nonpositive_duration(tmp_path):
    source, assets = _make_valid_paths(tmp_path)
    job = resolve_job({"source": source, "assets": assets, "duration": 0}, 0)
    assert any("duration" in e for e in validate_job(job))


def test_only_without_match_raises_batch_error(tmp_path, patch_runners):
    patch_runners(RunnerRecorder())
    source, assets = _make_valid_paths(tmp_path)
    jobs = resolve_all([{"name": "real", "source": source, "assets": assets}])
    with pytest.raises(batch.BatchError, match="未匹配到任何 job"):
        run_batch(jobs, only="typo")


def test_audio_field_routes_to_assemble_and_subtitles(tmp_path):
    source, assets = _make_valid_paths(tmp_path)
    audio = tmp_path / "my_voice.wav"
    audio.write_bytes(b"wav")
    job = resolve_job(
        {"source": source, "assets": assets, "audio": str(audio), "output": str(tmp_path / "o")}, 0)
    assert validate_job(job) == []
    asm = build_assemble_argv(job, tmp_path / "o")
    assert "--audio" in asm and str(audio) in asm
    assert "--tts" not in asm  # audio 与 tts 互斥，audio 优先
    subs = build_subtitles_argv(job, tmp_path / "o")
    assert "--audio" in subs and str(audio) in subs


def test_audio_missing_file_marks_invalid(tmp_path):
    source, assets = _make_valid_paths(tmp_path)
    job = resolve_job({"source": source, "assets": assets, "audio": str(tmp_path / "nope.wav")}, 0)
    assert any("audio 不存在" in e for e in validate_job(job))


def test_llm_field_routes_provider_into_rewrite_argv(tmp_path):
    source, assets = _make_valid_paths(tmp_path)
    job = resolve_job({"source": source, "assets": assets, "llm": "deepseek"}, 0)
    assert validate_job(job) == []
    argv = build_rewrite_argv(job, tmp_path)
    assert "--provider" in argv and "deepseek" in argv
    # 空/auto 时不传 --provider，交给 rewrite 的 auto 凭据探测
    job_auto = resolve_job({"source": source, "assets": assets}, 0)
    assert "--provider" not in build_rewrite_argv(job_auto, tmp_path)


def test_llm_field_invalid_value_flagged(tmp_path):
    source, assets = _make_valid_paths(tmp_path)
    job = resolve_job({"source": source, "assets": assets, "llm": "gemini"}, 0)
    assert any("llm 非法" in e for e in validate_job(job))


# ---------- run_job on_stage 回调（P10 供 studio 用） ----------

def test_on_stage_callback_receives_stages_in_order(tmp_path, patch_runners):
    patch_runners(RunnerRecorder())
    source, assets = _make_valid_paths(tmp_path)
    job = resolve_job(
        {"name": "j", "source": source, "assets": assets, "output": str(tmp_path / "o"),
         "effects": True, "subtitles": True}, 0)
    seen: list[str] = []
    report = run_job(job, on_stage=seen.append)
    assert report.status == "ok"
    # 回调在每个阶段开始执行前触发，顺序与阶段链一致
    assert seen == ["rewrite", "voice", "assemble", "effects", "subtitles", "publish"]


def test_on_stage_none_preserves_current_behavior(tmp_path, patch_runners):
    recorder = patch_runners(RunnerRecorder())
    source, assets = _make_valid_paths(tmp_path)
    job = resolve_job(
        {"name": "j", "source": source, "assets": assets, "output": str(tmp_path / "o"),
         "effects": False, "subtitles": False}, 0)
    # 默认 None：不传 on_stage，行为与历史完全一致
    report = run_job(job)
    assert report.status == "ok"
    assert recorder.stages == ["rewrite", "voice", "assemble", "publish"]


def test_on_stage_callback_exception_does_not_abort_job(tmp_path, patch_runners):
    recorder = patch_runners(RunnerRecorder())
    source, assets = _make_valid_paths(tmp_path)
    job = resolve_job(
        {"name": "j", "source": source, "assets": assets, "output": str(tmp_path / "o"),
         "effects": False, "subtitles": False}, 0)

    def bad_callback(stage):
        raise RuntimeError("callback boom")

    # 进度回调是旁路观察者：抛异常绝不能打断任务执行
    report = run_job(job, on_stage=bad_callback)
    assert report.status == "ok"
    assert recorder.stages == ["rewrite", "voice", "assemble", "publish"]


# ---------- 视觉来源：视频素材 / AI 生图 ----------

def test_resolve_job_visual_source_defaults_video():
    job = resolve_job({"name": "j", "source": "s", "assets": "a"}, 0)
    assert job.visual_source == "video"


def test_resolve_job_visual_source_ai_image_and_normalizes():
    assert resolve_job({"source": "s", "visual_source": "ai_image"}, 0).visual_source == "ai_image"
    # 非法值回落 video（前端只会传两个合法值，后端仍兜底）
    assert resolve_job({"source": "s", "visual_source": "乱填"}, 0).visual_source == "video"


def test_validate_job_ai_image_does_not_require_assets(tmp_path):
    source = tmp_path / "s.txt"
    source.write_text("x", encoding="utf-8")
    job = resolve_job({"source": str(source), "visual_source": "ai_image"}, 0)  # 无 assets
    assert validate_job(job) == []


def test_validate_job_video_still_requires_assets(tmp_path):
    source = tmp_path / "s.txt"
    source.write_text("x", encoding="utf-8")
    job = resolve_job({"source": str(source)}, 0)  # video 默认、无 assets
    assert any("assets" in e for e in validate_job(job))


def test_stages_include_image_gen_only_for_ai_image():
    from video_factory.batch import _stages_for

    video = resolve_job({"source": "s", "assets": "a"}, 0)
    ai = resolve_job({"source": "s", "visual_source": "ai_image"}, 0)
    assert "image_gen" not in _stages_for(video)
    # voice（配音+主时间轴）前移：恒在 rewrite 之后、image_gen 之前。
    assert _stages_for(ai)[:4] == ["rewrite", "voice", "image_gen", "assemble"]
    assert _stages_for(video)[:3] == ["rewrite", "voice", "assemble"]


def test_build_assemble_argv_uses_gen_assets_for_ai_image(tmp_path):
    job = resolve_job({"name": "j", "source": "s", "visual_source": "ai_image"}, 0)
    argv = build_assemble_argv(job, tmp_path)
    assets = argv[argv.index("--assets") + 1]
    assert assets.endswith("gen_assets") and str(tmp_path) in assets


# ---------- 配音阶段前移（P16 二期）：voice argv / assemble 按盘选音轨 ----------

def test_build_voice_argv_tts_path(tmp_path):
    """默认 doubao：voice argv 带 --tts/--rewrite/--duration/--output，无 --audio。"""
    job = resolve_job({"name": "j", "source": "s", "assets": "a", "voice": "shimmer"}, 0)
    argv = batch.build_voice_argv(job, tmp_path)
    assert argv[argv.index("--rewrite") + 1].endswith("rewrite.json")
    assert argv[argv.index("--tts") + 1] == "doubao"
    assert argv[argv.index("--voice") + 1] == "shimmer"
    assert "--audio" not in argv
    assert argv[argv.index("--output") + 1] == str(tmp_path)


def test_build_voice_argv_user_audio_path(tmp_path):
    """自带 --audio：voice argv 用 --audio、不带 --tts（互斥，audio 优先）。"""
    audio = tmp_path / "v.wav"
    audio.write_bytes(b"wav")
    job = resolve_job({"name": "j", "source": "s", "assets": "a", "audio": str(audio)}, 0)
    argv = batch.build_voice_argv(job, tmp_path)
    assert argv[argv.index("--audio") + 1] == str(audio)
    assert "--tts" not in argv


def test_build_voice_argv_passes_voice_speed(tmp_path):
    job = resolve_job({"name": "j", "source": "s", "assets": "a", "voice_speed": "1.2"}, 0)
    argv = batch.build_voice_argv(job, tmp_path)
    assert argv[argv.index("--voice-speed") + 1] == "1.2"


def test_build_assemble_argv_prefers_fitted_over_raw(tmp_path):
    """voice 阶段产出后：assemble 按盘优先取 voiceover_fitted.wav（微调过），不再传 --tts。"""
    job = resolve_job({"name": "j", "source": "s", "assets": "a"}, 0)  # 默认 tts=doubao
    (tmp_path / "voiceover.wav").write_bytes(b"raw")
    (tmp_path / "voiceover_fitted.wav").write_bytes(b"fitted")
    argv = build_assemble_argv(job, tmp_path)
    assert argv[argv.index("--audio") + 1] == str(tmp_path / "voiceover_fitted.wav")
    assert "--tts" not in argv  # 已有配音，不再内嵌合成


def test_build_assemble_argv_falls_back_to_raw_when_no_fitted(tmp_path):
    """只有 voiceover.wav（本轮未微调）→ 取 raw。"""
    job = resolve_job({"name": "j", "source": "s", "assets": "a"}, 0)
    (tmp_path / "voiceover.wav").write_bytes(b"raw")
    argv = build_assemble_argv(job, tmp_path)
    assert argv[argv.index("--audio") + 1] == str(tmp_path / "voiceover.wav")
    assert "--tts" not in argv


def test_build_assemble_argv_falls_back_to_tts_when_no_voice_output(tmp_path):
    """voice 阶段失败/老任务无配音产物 → 维持现有 --tts/--voice-speed 组装（assemble 内嵌兜底）。"""
    job = resolve_job({"name": "j", "source": "s", "assets": "a", "voice_speed": "1.1"}, 0)
    argv = build_assemble_argv(job, tmp_path)  # tmp_path 内无任何 voiceover 文件
    assert "--audio" not in argv
    assert argv[argv.index("--tts") + 1] == "doubao"
    assert argv[argv.index("--voice-speed") + 1] == "1.1"


def test_build_assemble_argv_user_audio_unaffected_by_disk_scan(tmp_path):
    """用户自带 --audio：始终走 job.audio，不受 job_dir 内 voiceover 产物影响。"""
    audio = tmp_path / "mine.wav"
    audio.write_bytes(b"wav")
    (tmp_path / "voiceover_fitted.wav").write_bytes(b"fitted")  # 即便盘上有 fitted
    job = resolve_job({"name": "j", "source": "s", "assets": "a", "audio": str(audio)}, 0)
    argv = build_assemble_argv(job, tmp_path)
    assert argv[argv.index("--audio") + 1] == str(audio)  # 仍用用户那条


def test_dry_run_includes_voice_stage(tmp_path):
    """dry-run 展开含 voice 阶段（rewrite→voice→assemble…），不 KeyError。"""
    job = resolve_job(
        {"name": "g", "source": "s", "assets": "a", "output": str(tmp_path / "o")}, 0)
    argv_by_stage = batch._dry_run_argv(job)
    assert list(argv_by_stage)[:3] == ["rewrite", "voice", "assemble"]
    assert "--rewrite" in argv_by_stage["voice"]


def test_output_artifacts_clears_timeline_not_audio():
    """(重)跑前清 timeline.json（防陈旧时钟），但绝不清配音（重跑复用配音是特性）。"""
    assert "timeline.json" in batch._OUTPUT_ARTIFACTS
    assert "voiceover.wav" not in batch._OUTPUT_ARTIFACTS
    assert "voiceover_fitted.wav" not in batch._OUTPUT_ARTIFACTS


def test_build_assemble_argv_uses_user_assets_for_video(tmp_path):
    job = resolve_job({"name": "j", "source": "s", "assets": "D:/mats"}, 0)
    argv = build_assemble_argv(job, tmp_path)
    assert argv[argv.index("--assets") + 1] == "D:/mats"


def test_run_image_gen_copies_images_in_section_order(tmp_path, monkeypatch):
    from video_factory import image_gen

    job_dir = tmp_path / "job"
    job_dir.mkdir()
    (job_dir / "rewrite.json").write_text(
        json.dumps({"sections": [{"narration": "a"}, {"narration": "b"}]}), encoding="utf-8"
    )
    img_a = tmp_path / "A.png"; img_a.write_bytes(b"AAA")
    img_b = tmp_path / "B.png"; img_b.write_bytes(b"BBB")
    # 报告故意乱序（section 1 在前），验证按 section 重排后命名
    monkeypatch.setattr(image_gen, "ensure_section_images", lambda *a, **k: {
        "images": [{"section": 1, "path": str(img_b)}, {"section": 0, "path": str(img_a)}],
        "generated": 2, "reused": 0, "warnings": [],
    })
    job = resolve_job({"name": "j", "source": "s", "visual_source": "ai_image", "aspect": "9:16"}, 0)
    assert batch._run_image_gen(job, job_dir) == 0
    gen = job_dir / "gen_assets"
    assert sorted(p.name for p in gen.glob("img_*")) == ["img_00.png", "img_01.png"]
    assert (gen / "img_00.png").read_bytes() == b"AAA"  # section0
    assert (gen / "img_01.png").read_bytes() == b"BBB"  # section1


def test_run_image_gen_returns_1_when_no_images(tmp_path, monkeypatch):
    from video_factory import image_gen

    job_dir = tmp_path / "job"
    job_dir.mkdir()
    (job_dir / "rewrite.json").write_text(json.dumps({"sections": [{"narration": "a"}]}), encoding="utf-8")
    monkeypatch.setattr(image_gen, "ensure_section_images", lambda *a, **k: {
        "images": [], "generated": 0, "reused": 0, "warnings": ["all failed"],
    })
    job = resolve_job({"name": "j", "source": "s", "visual_source": "ai_image"}, 0)
    assert batch._run_image_gen(job, job_dir) == 1


def test_run_image_gen_returns_1_on_error(tmp_path, monkeypatch):
    from video_factory import image_gen

    job_dir = tmp_path / "job"
    job_dir.mkdir()
    (job_dir / "rewrite.json").write_text(json.dumps({"sections": [{"narration": "a"}]}), encoding="utf-8")

    def boom(*a, **k):
        raise image_gen.ImageGenError("未配置 ARK_API_KEY")

    monkeypatch.setattr(image_gen, "ensure_section_images", boom)
    job = resolve_job({"name": "j", "source": "s", "visual_source": "ai_image"}, 0)
    assert batch._run_image_gen(job, job_dir) == 1


def test_gen_sizes_within_seedream_bounds():
    # 回归：所有生图尺寸宽高必须 ∈[1280,4096]——Seedream 4.0 自定义尺寸下限 1280，
    # 旧的 1080x1920/1080x1080 宽或高不足会被方舟 API 拒（2026-07-14 核对官方文档修正）。
    from video_factory.batch import _GEN_SIZE_BY_ASPECT
    from video_factory.image_gen import DEFAULT_SIZE

    for s in list(_GEN_SIZE_BY_ASPECT.values()) + [DEFAULT_SIZE]:
        w, h = (int(x) for x in s.split("x"))
        assert 1280 <= w <= 4096, f"{s} 宽越界"
        assert 1280 <= h <= 4096, f"{s} 高越界"


def test_dry_run_handles_ai_image_stage(tmp_path):
    """回归：ai_image 任务 dry-run 不再 KeyError（_dry_run_argv 的 builders 原本缺 image_gen 键）。"""
    job = resolve_job(
        {"name": "g", "source": "s", "assets": "a", "visual_source": "ai_image",
         "output": str(tmp_path / "o")},
        0,
    )
    argv_by_stage = batch._dry_run_argv(job)  # 原会抛 KeyError
    assert "image_gen" in argv_by_stage
    assert "gen_assets" in " ".join(argv_by_stage["image_gen"])


def test_run_job_clears_stale_final_before_running(tmp_path, patch_runners):
    """回归：(重)跑前清掉上一轮遗留成片。本轮在 rewrite 阶段就失败时，final 不应指向旧 release。"""
    patch_runners(RunnerRecorder(fail_stage="rewrite"))
    out = tmp_path / "job"
    out.mkdir()
    (out / "release_subtitled.mp4").write_text("stale", encoding="utf-8")  # 上一轮遗留成片
    job = resolve_job({"name": "j", "source": "s", "assets": "a", "output": str(out)}, 0)
    report = run_job(job)
    assert report.status == "failed" and report.stage_failed == "rewrite"
    assert "subtitled" not in report.outputs  # 旧成片已清
    assert report.to_dict()["final"] == ""  # final 不再指向陈旧文件
    assert not (out / "release_subtitled.mp4").exists()


def test_run_job_surfaces_stage_error_detail(tmp_path, monkeypatch):
    """回归（2026-07-14 事故）：阶段失败落盘的 <stage>_error.txt 必须带进 JobReport.error，
    任务看板才能显示根因（此前只有「返回非 0 退出码：1」，排障全靠猜）。"""
    from video_factory import stage_report

    def failing_rewrite(job, job_dir):
        stage_report.write_stage_error(job_dir, "rewrite", "改写失败：未配置任何 LLM 凭据。")
        return 1

    monkeypatch.setattr(batch, "STAGE_RUNNERS", {**batch.STAGE_RUNNERS, "rewrite": failing_rewrite})
    job = resolve_job({"name": "j", "source": "s", "assets": "a", "output": str(tmp_path / "o")}, 0)
    report = run_job(job)
    assert report.status == "failed"
    assert "未配置任何 LLM 凭据" in (report.error or "")  # 根因进报告


def test_run_job_clears_stale_stage_errors(tmp_path, patch_runners):
    """回归：上一轮的 <stage>_error.txt 不许串进本轮报告（重跑前必须清）。"""
    patch_runners(RunnerRecorder())  # 本轮全部成功
    out = tmp_path / "job"
    out.mkdir()
    (out / "rewrite_error.txt").write_text("上一轮的旧错误", encoding="utf-8")
    job = resolve_job({"name": "j", "source": "s", "assets": "a", "output": str(out)}, 0)
    report = run_job(job)
    assert report.status == "ok" and report.error is None
    assert not (out / "rewrite_error.txt").exists()  # 旧错误已清


# ---------- 配音语速（P12） ----------

def test_voice_speed_resolves_validates_and_routes(tmp_path):
    source, assets = _make_valid_paths(tmp_path)
    # 未设 → 默认 1.1（2026-07-16 用户定案），照常进 argv
    plain = resolve_job({"source": source, "assets": assets}, 0)
    assert plain.voice_speed == pytest.approx(1.1)
    asm_plain = build_assemble_argv(plain, tmp_path / "o")
    assert asm_plain[asm_plain.index("--voice-speed") + 1] == "1.1"
    # 设了 → float 化、过校验、进 assemble argv
    job = resolve_job({"source": source, "assets": assets, "voice_speed": "1.2"}, 0)
    assert job.voice_speed == 1.2
    assert validate_job(job) == []
    asm = build_assemble_argv(job, tmp_path / "o")
    assert asm[asm.index("--voice-speed") + 1] == "1.2"


def test_voice_speed_out_of_range_invalid(tmp_path):
    source, assets = _make_valid_paths(tmp_path)
    job = resolve_job({"source": source, "assets": assets, "voice_speed": 3.0}, 0)
    errors = validate_job(job)
    assert any("voice_speed" in e for e in errors)


def test_voice_speed_ignored_with_existing_audio(tmp_path):
    """复用现成配音（--audio）时语速不传：变速会毁用户的成品音频。"""
    source, assets = _make_valid_paths(tmp_path)
    audio = tmp_path / "v.wav"
    audio.write_bytes(b"wav")
    job = resolve_job(
        {"source": source, "assets": assets, "audio": str(audio), "voice_speed": 1.5}, 0)
    assert "--voice-speed" not in build_assemble_argv(job, tmp_path / "o")


def test_voice_speed_garbage_marks_invalid(tmp_path):
    """voice_speed 写成非数字不许击穿整批：resolve_all 记 invalid 继续。"""
    jobs = resolve_all([{"name": "bad", "source": "s", "assets": "a", "voice_speed": "fast"}])
    assert validate_job(jobs[0]) != []


# ---------- 拍级生图：风格拼接 + 入库（2026-07-15 修复回归） ----------

def test_beat_image_gen_appends_style_and_ingests_to_library(tmp_path, monkeypatch):
    """回归两个真实事故：①拍级生成漏拼风格提示词（成片画风跑偏）；
    ②新图只进 gen_assets 不入库（库养不肥、每单全额重新生图）。"""
    from video_factory import image_gen

    job_dir = tmp_path / "job"
    job_dir.mkdir()
    (job_dir / "rewrite.json").write_text(json.dumps({
        "hook": "钩子", "sections": [{"title": "一", "narration": "内容" * 20, "visual_hint": ""}],
        "target_duration_seconds": 10,
    }, ensure_ascii=False), encoding="utf-8")
    job = resolve_job({"name": "g", "source": "s", "visual_source": "ai_image",
                       "duration": 10, "output": str(job_dir)}, 0)

    matches = [{"beat_index": 0, "action": "generate", "file": None, "prompt": "城市夜景",
                "category": "场景", "tags": ["城市"]}]
    gen_prompts, ingested = [], []
    monkeypatch.setattr(image_gen, "match_beats_to_library", lambda *a, **k: matches)
    monkeypatch.setattr(image_gen, "get_style_prompt", lambda: "测试美漫风")
    monkeypatch.setattr(image_gen, "generate_image",
                        lambda prompt, size: gen_prompts.append(prompt) or b"png-bytes")
    monkeypatch.setattr(image_gen, "ingest_generated_image",
                        lambda img, **kw: ingested.append(kw) or (tmp_path / "lib.png"))

    assert batch._run_image_gen(job, job_dir) == 0
    assert len(gen_prompts) == 1
    assert gen_prompts[0].startswith("城市夜景。测试美漫风")   # 风格已拼接
    assert "不得出现任何文字" in gen_prompts[0]              # 禁文字保险已钉进最终提示词
    assert len(ingested) == 1                              # 新图已入库
    assert ingested[0]["category"] == "场景" and ingested[0]["tags"] == ["城市"]
    assert (job_dir / "gen_assets" / "img_00.png").exists()  # gen_assets 照常产出


def test_run_image_gen_uses_timeline_beats_when_present(tmp_path, monkeypatch):
    """P16 二期：job_dir 有 timeline.json 时，生图按变长拍规划（卡话切），拍数=timeline 拍数。

    验证生图侧与拼装侧用同一权威函数 plan_beats_from_timeline：拍数由时间轴决定，
    而非旧的 5s 均分——保证生图张数与 assemble --ordered-assets 逐拍消费一致。
    """
    from video_factory import image_gen

    monkeypatch.setattr("video_factory.subtitles.split_sentences",
                        lambda text: [p for p in str(text).split("|") if p])
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    (job_dir / "rewrite.json").write_text(json.dumps({
        "hook": "A|B", "sections": [{"title": "一", "narration": "C|D", "visual_hint": ""}],
        "full_voiceover": "x", "target_duration_seconds": 90,
    }, ensure_ascii=False), encoding="utf-8")
    # 4 句 timeline → hook(3s封拍+1s残并)=1 拍、第一节(3s+1s)=1 拍 → 共 2 拍
    (job_dir / "timeline.json").write_text(json.dumps({
        "version": "timeline_v1",
        "sentences": [
            {"text": "甲", "start": 0.0, "end": 3.0},
            {"text": "乙", "start": 3.0, "end": 4.0},
            {"text": "丙", "start": 4.0, "end": 7.0},
            {"text": "丁", "start": 7.0, "end": 8.0},
        ],
    }, ensure_ascii=False), encoding="utf-8")
    job = resolve_job({"name": "g", "source": "s", "visual_source": "ai_image",
                       "duration": 90, "output": str(job_dir)}, 0)

    captured = {}

    def fake_match(beats, index, root):
        captured["n_beats"] = len(beats)
        # 每拍一条 generate 匹配，img_NN 用全局拍序命名，避免同节 beat_index 冲突。
        return [{"beat_index": b.global_index, "action": "generate", "file": None,
                 "prompt": "p", "category": "场景", "tags": []} for b in beats]

    monkeypatch.setattr(image_gen, "match_beats_to_library", fake_match)
    monkeypatch.setattr(image_gen, "load_index", lambda root: [])
    monkeypatch.setattr(image_gen, "get_style_prompt", lambda: "风")
    monkeypatch.setattr(image_gen, "generate_image", lambda prompt, size: b"png")
    monkeypatch.setattr(image_gen, "ingest_generated_image", lambda img, **kw: tmp_path / "l.png")

    assert batch._run_image_gen(job, job_dir) == 0
    assert captured["n_beats"] == 2  # 来自 timeline 的变长拍，非 5s 均分
    assert (job_dir / "gen_assets" / "img_00.png").exists()
    assert (job_dir / "gen_assets" / "img_01.png").exists()


# ---- P3b 氛围粒子开关（2026-07-16）----


def test_build_effects_argv_ambient_particles_flag(tmp_path):
    from video_factory.batch import build_effects_argv, resolve_job

    # 2026-07-16 二次定案：氛围粒子默认开（缺省即带 flag），显式 False 才关。
    on = resolve_job({"source": "s.mp4", "assets": "a"}, 0)
    assert "--ambient-particles" in build_effects_argv(on, tmp_path)

    off = resolve_job({"source": "s.mp4", "assets": "a", "ambient_particles": False}, 0)
    assert "--ambient-particles" not in build_effects_argv(off, tmp_path)


def test_validate_job_rejects_zero_byte_source(tmp_path):
    # 0 字节 source = 上传中断残骸（2026-07-16 实锤事故）：validate 就地拦下，
    # 不让它混到 rewrite 阶段才以 ffmpeg Invalid data 的面目炸出来。
    source = tmp_path / "broken.mp4"
    source.write_bytes(b"")
    assets = tmp_path / "assets"
    assets.mkdir()
    job = resolve_job({"source": str(source), "assets": str(assets)}, 0)
    errors = validate_job(job)
    assert any("空文件" in e and "重新上传" in e for e in errors)


# ---------- 双画幅（2026-07-16 用户定案 A 方案：勾选才双出） ----------

def test_dual_aspect_default_off_and_resolve(tmp_path):
    source, assets = _make_valid_paths(tmp_path)
    plain = resolve_job({"source": source, "assets": assets}, 0)
    assert plain.dual_aspect is False
    dual = resolve_job({"source": source, "assets": assets, "dual_aspect": True}, 0)
    assert dual.dual_aspect is True


def test_dual_aspect_stage_flow_runs_per_aspect_then_publish_once(tmp_path, patch_runners):
    recorder = patch_runners(RunnerRecorder())
    source, assets = _make_valid_paths(tmp_path)
    job = resolve_job({
        "name": "dual", "source": source, "assets": assets,
        "aspect": "9:16", "dual_aspect": True,
        "output": str(tmp_path / "out"),
    }, 0)
    report = run_job(job)

    assert report.status == "ok"
    # rewrite/voice 共享一次；assemble/effects/subtitles 按画幅各一遍；publish 收尾一次
    assert recorder.stages == [
        "rewrite", "voice",
        "assemble", "effects", "subtitles",
        "assemble", "effects", "subtitles",
        "publish",
    ]
    # 两轮 assemble 的画幅：主画幅 9:16 先行，另一画幅 16:9 随后；产物落各自子目录
    asm_argvs = [argv for stage, argv in recorder.calls if stage == "assemble"]
    assert asm_argvs[0][asm_argvs[0].index("--aspect") + 1] == "9:16"
    assert asm_argvs[1][asm_argvs[1].index("--aspect") + 1] == "16:9"
    assert "9x16" in asm_argvs[0][asm_argvs[0].index("--rewrite") + 1]
    assert "16x9" in asm_argvs[1][asm_argvs[1].index("--rewrite") + 1]


def test_dual_aspect_seeds_shared_artifacts_into_subdirs(tmp_path, monkeypatch):
    source, assets = _make_valid_paths(tmp_path)
    out = tmp_path / "out"

    def make_runner(stage):
        def runner(job, job_dir):
            if stage == "voice":
                # voice 在根目录落共享产物
                (job_dir / "rewrite.json").write_text("{}", encoding="utf-8")
                (job_dir / "voiceover.wav").write_bytes(b"wav")
                (job_dir / "timeline.json").write_text("{}", encoding="utf-8")
            if stage == "assemble":
                # 画幅子目录里必须已被播种共享产物（argv builder 全靠 job_dir 组路径）
                assert (job_dir / "voiceover.wav").exists(), f"{job_dir} 缺 voiceover"
                assert (job_dir / "timeline.json").exists()
            return 0
        return runner

    monkeypatch.setattr(batch, "STAGE_RUNNERS", {
        s: make_runner(s)
        for s in ("rewrite", "voice", "image_gen", "assemble", "effects", "subtitles", "publish")
    })
    job = resolve_job({
        "name": "seed", "source": source, "assets": assets,
        "aspect": "9:16", "dual_aspect": True, "output": str(out),
    }, 0)
    report = run_job(job)
    assert report.status == "ok"
    assert (out / "9x16" / "voiceover.wav").exists()
    assert (out / "16x9" / "voiceover.wav").exists()


def test_dual_aspect_outputs_suffixed_and_final_prefers_primary(tmp_path):
    from video_factory.batch import _collect_outputs, _final_output

    source, assets = _make_valid_paths(tmp_path)
    job = resolve_job({"source": source, "assets": assets, "dual_aspect": True}, 0)
    job_dir = tmp_path / "out"
    for d in ("9x16", "16x9"):
        (job_dir / d).mkdir(parents=True)
        (job_dir / d / "release_subtitled.mp4").write_bytes(b"v")
    outputs = _collect_outputs(job, job_dir)
    assert "subtitled_9x16" in outputs and "subtitled_16x9" in outputs
    assert _final_output(outputs) == outputs["subtitled_9x16"]  # 竖屏优先


def test_build_publish_argv_dual_video_from_primary_subdir(tmp_path):
    source, assets = _make_valid_paths(tmp_path)
    job = resolve_job({
        "source": source, "assets": assets,
        "aspect": "9:16", "dual_aspect": True,
    }, 0)
    job_dir = tmp_path / "out"
    (job_dir / "9x16").mkdir(parents=True)
    (job_dir / "9x16" / "release_subtitled.mp4").write_bytes(b"v")
    argv = build_publish_argv(job, job_dir)
    video = argv[argv.index("--video") + 1]
    assert "9x16" in video and video.endswith("release_subtitled.mp4")
