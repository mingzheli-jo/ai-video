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
            "assemble": build_assemble_argv,
            "effects": build_effects_argv,
            "subtitles": build_subtitles_argv,
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
    assert job.subtitles is False
    assert job.effects is True
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
    assert recorder.stages == ["rewrite", "assemble"]


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
    assert recorder.stages == ["rewrite", "assemble"]


def test_stage_chain_effects_on_subtitles_on(tmp_path, patch_runners):
    recorder = patch_runners(RunnerRecorder())
    source, assets = _make_valid_paths(tmp_path)
    job = resolve_job(
        {"name": "j", "source": source, "assets": assets, "effects": True, "subtitles": True},
        0,
    )
    run_job(job)
    assert recorder.stages == ["rewrite", "assemble", "effects", "subtitles"]


def test_stage_chain_effects_on_subtitles_off(tmp_path, patch_runners):
    recorder = patch_runners(RunnerRecorder())
    source, assets = _make_valid_paths(tmp_path)
    job = resolve_job(
        {"name": "j", "source": source, "assets": assets, "effects": True, "subtitles": False},
        0,
    )
    run_job(job)
    assert recorder.stages == ["rewrite", "assemble", "effects"]


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
    assert seen == ["rewrite", "assemble", "effects", "subtitles"]


def test_on_stage_none_preserves_current_behavior(tmp_path, patch_runners):
    recorder = patch_runners(RunnerRecorder())
    source, assets = _make_valid_paths(tmp_path)
    job = resolve_job(
        {"name": "j", "source": source, "assets": assets, "output": str(tmp_path / "o"),
         "effects": False, "subtitles": False}, 0)
    # 默认 None：不传 on_stage，行为与历史完全一致
    report = run_job(job)
    assert report.status == "ok"
    assert recorder.stages == ["rewrite", "assemble"]


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
    assert recorder.stages == ["rewrite", "assemble"]


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
    assert _stages_for(ai)[:3] == ["rewrite", "image_gen", "assemble"]


def test_build_assemble_argv_uses_gen_assets_for_ai_image(tmp_path):
    job = resolve_job({"name": "j", "source": "s", "visual_source": "ai_image"}, 0)
    argv = build_assemble_argv(job, tmp_path)
    assets = argv[argv.index("--assets") + 1]
    assert assets.endswith("gen_assets") and str(tmp_path) in assets


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
