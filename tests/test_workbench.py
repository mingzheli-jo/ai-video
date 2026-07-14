import io
import json
from http import HTTPStatus
from pathlib import Path

import video_factory.workbench as workbench
from video_factory.workbench import (
    INDEX_HTML,
    PRODUCTION_PRESETS,
    JobStore,
    build_production_options,
    build_quality_summary,
    parse_job_request,
)


def test_index_html_contains_workbench_controls():
    assert "视频发布增强工作台" in INDEX_HTML
    assert "workflow-original" in INDEX_HTML
    assert "视频修复 / 增强" in INDEX_HTML
    assert "发布增强" in INDEX_HTML
    assert "workflow-reference-guided-original" in INDEX_HTML
    assert "一键原创视频" not in INDEX_HTML
    assert "生成原创视频" not in INDEX_HTML
    assert "只看结果" not in INDEX_HTML
    assert "开始发布增强" in INDEX_HTML
    assert "高级详情" in INDEX_HTML
    assert "originalTopic" in INDEX_HTML
    assert "originalBrief" in INDEX_HTML
    assert "发布目标（可选）" in INDEX_HTML
    assert "补充要求（可选）" in INDEX_HTML
    assert "不填也可以" in INDEX_HTML
    assert "inputPath" in INDEX_HTML
    assert "sourceUrls" in INDEX_HTML
    assert "视频链接" in INDEX_HTML
    assert "参考视频缓存" in INDEX_HTML
    assert "下载状态" in INDEX_HTML
    assert "source_download" in INDEX_HTML
    assert "fileInput" in INDEX_HTML
    assert "mode-human-edit" in INDEX_HTML
    assert "mode-creative" in INDEX_HTML
    assert "startButton" in INDEX_HTML
    assert "artifactList" in INDEX_HTML
    assert "previewModal" in INDEX_HTML
    assert "expandPreviewButton" in INDEX_HTML
    assert "quality_report" in INDEX_HTML
    assert "auto_repair_report" in INDEX_HTML
    assert "creative_plan" in INDEX_HTML
    assert "candidate_edl" in INDEX_HTML
    assert "cover_candidates" in INDEX_HTML
    assert "content_analysis" in INDEX_HTML
    assert "audio_analysis" in INDEX_HTML
    assert "semantic_timeline" in INDEX_HTML
    assert "transcript_analysis" in INDEX_HTML
    assert "motion_plan" in INDEX_HTML
    assert "caption_timeline" in INDEX_HTML
    assert "subtitles" in INDEX_HTML
    assert "voiceover_manifest" in INDEX_HTML
    assert "assetLibraryPath" in INDEX_HTML
    assert "asset_pass_report" in INDEX_HTML
    assert "asset_usage_plan" in INDEX_HTML
    assert "visual_requirements" in INDEX_HTML
    assert "asset_sourcing_plan" in INDEX_HTML
    assert "visual_insert_plan" in INDEX_HTML
    assert "images2_prompt_pack" in INDEX_HTML
    assert "generated_visual_manifest" in INDEX_HTML
    assert "cover_brief" in INDEX_HTML
    assert "cover_prompt_pack" in INDEX_HTML
    assert "cover_asset_manifest" in INDEX_HTML
    assert "visualAssetStrategy" in INDEX_HTML
    assert "AI 补充镜头策略" in INDEX_HTML
    assert "images2 按需补充" in INDEX_HTML
    assert "解释镜头、对比画面或过渡画面" in INDEX_HTML
    assert "data-poster" in INDEX_HTML
    assert "poster=" in INDEX_HTML
    assert "导演长版" in INDEX_HTML
    assert "creative_strategy" in INDEX_HTML
    assert "动态镜头" in INDEX_HTML
    assert "字幕时间线" in INDEX_HTML
    assert "旁白" in INDEX_HTML
    assert "资产入镜" in INDEX_HTML
    assert "能力雷达" not in INDEX_HTML
    assert "模式驾驶舱" in INDEX_HTML
    assert "验收面板" in INDEX_HTML
    assert "summaryBoard" in INDEX_HTML
    assert "比例保持" in INDEX_HTML
    assert "模板预算" in INDEX_HTML
    assert "时长底线" in INDEX_HTML
    assert "生产预设" in INDEX_HTML
    assert "批量路径" in INDEX_HTML
    assert "批次队列" in INDEX_HTML
    assert "任务历史" in INDEX_HTML
    assert "自动优化重做" in INDEX_HTML
    assert "质量分" in INDEX_HTML
    assert "原创风险" in INDEX_HTML
    assert "音频复用" in INDEX_HTML
    assert "文本重合" in INDEX_HTML
    assert "originality_report" in INDEX_HTML
    assert "视觉重构" in INDEX_HTML
    assert "人像窗口修补" in INDEX_HTML
    assert "移除讲解人" in INDEX_HTML
    assert "卡通主持人" not in INDEX_HTML
    assert "data-job-id" in INDEX_HTML
    assert "historyList.addEventListener" in INDEX_HTML
    assert "自动加轻片头片尾" not in INDEX_HTML


def test_public_workbench_focuses_on_release_enhancement_only():
    workflow_grid = INDEX_HTML[
        INDEX_HTML.index('<div class="workflow-grid">') : INDEX_HTML.index("</div>", INDEX_HTML.index('<div class="workflow-grid">'))
    ]

    assert "<strong>一键原创视频</strong>" not in workflow_grid
    assert "<strong>视频修复 / 增强</strong>" in workflow_grid
    assert "<strong>原创生成</strong>" not in workflow_grid
    assert workflow_grid.count('type="radio" name="workflow"') == 1
    assert 'id="workflow-reference-guided-original" type="radio" name="workflow" value="reference_guided_original" hidden' in INDEX_HTML
    assert 'id="workflow-original" type="radio" name="workflow" value="original" hidden' in INDEX_HTML


def test_primary_actions_are_visible_before_long_form_sections():
    assert 'id="advancedOptions"' in INDEX_HTML
    assert INDEX_HTML.index('id="sourceSection"') < INDEX_HTML.index('id="advancedOptions"')
    assert INDEX_HTML.index('id="originalTopic"') < INDEX_HTML.index('id="advancedOptions"')
    assert INDEX_HTML.index('id="visualAssetStrategy"') < INDEX_HTML.index('id="advancedOptions"')
    assert INDEX_HTML.index('id="durationPolicy"') < INDEX_HTML.index('id="advancedOptions"')
    assert INDEX_HTML.index('id="startButton"') < INDEX_HTML.index('id="advancedOptions"')
    assert INDEX_HTML.index('id="advancedOptions"') < INDEX_HTML.index('id="assetLibraryPath"')
    assert INDEX_HTML.index('id="advancedOptions"') < INDEX_HTML.index('class="workflow-grid"')
    assert INDEX_HTML.index('class="workflow-grid"') < INDEX_HTML.index('id="presetSelect"')


def test_default_front_door_is_release_enhancement_and_expert_modes_are_folded():
    assert 'id="workflow-replicate" type="radio" name="workflow" value="replicate" checked' in INDEX_HTML
    assert 'id="workflow-reference-guided-original" type="radio" name="workflow" value="reference_guided_original" checked' not in INDEX_HTML
    assert '<option value="tutorial_longform" selected>发布增强</option>' in INDEX_HTML
    assert '<option value="foolproof_original"' not in INDEX_HTML
    assert '<option value="original_tutorial"' not in INDEX_HTML
    assert INDEX_HTML.index('id="advancedOptions"') < INDEX_HTML.index('id="replicateModeGrid"')
    assert '<details class="queue-board" id="batchBoard">' in INDEX_HTML
    assert '<details class="history-board" id="historyBoard">' in INDEX_HTML


def test_latest_job_does_not_prefill_new_task_form():
    assert "productionNotes.value = job.options.production_notes" not in INDEX_HTML
    assert "originalTopic.value = job.options.original_topic" not in INDEX_HTML
    assert "originalBrief.value = job.options.original_brief" not in INDEX_HTML
    assert "assetLibraryPath.value = job.options.asset_library_path" not in INDEX_HTML
    assert "visualAssetStrategy.value = job.options.visual_asset_strategy" not in INDEX_HTML
    assert "if (!currentJobId && jobs[0])" not in INDEX_HTML


def test_frontend_sanitizes_legacy_original_logs():
    assert "function sanitizeLegacyLog" in INDEX_HTML
    assert "旧版参考学习实验" in INDEX_HTML
    assert "生成旧版实验样片" in INDEX_HTML
    assert "jobLog.textContent = sanitizeLegacyLog(lines.join(" in INDEX_HTML
    assert "一键原创视频" not in INDEX_HTML
    assert "生成原创视频" not in INDEX_HTML


def test_tutorial_longform_defaults_to_low_reuse_creative_settings():
    options = build_production_options({"workflow": "replicate", "preset_id": "tutorial_longform"})

    assert options["mode"] == "creative-edit"
    assert options["quality_strictness"] == "audit"
    assert options["creative_strength"] == "strong"
    assert options["audio_policy"] == "normalize_only"
    assert options["visual_asset_strategy"] == "images2_contextual_inserts"
    assert options["original_insert_policy"] == "chapter_explainers"
    assert PRODUCTION_PRESETS["tutorial_longform"]["audio_policy"] == "normalize_only"
    assert PRODUCTION_PRESETS["tutorial_longform"]["visual_asset_strategy"] == "images2_contextual_inserts"
    assert "tutorial_longform: { quality: 'audit', creative: 'strong'" in INDEX_HTML


def test_artifact_links_are_collapsed_as_advanced_outputs():
    assert 'id="artifactDetails"' in INDEX_HTML
    assert 'id="artifactCount"' in INDEX_HTML
    assert "高级产物" in INDEX_HTML
    assert INDEX_HTML.index('id="artifactDetails"') < INDEX_HTML.index('id="artifactList"')
    assert INDEX_HTML.index('id="artifactList"') < INDEX_HTML.index('id="previewPane"')


def test_video_file_path_is_visible_near_preview():
    assert 'id="filePathPanel"' in INDEX_HTML
    assert 'id="videoPathText"' in INDEX_HTML
    assert 'id="videoFolderText"' in INDEX_HTML
    assert 'id="sourceCacheText"' in INDEX_HTML
    assert 'id="downloadStatusText"' in INDEX_HTML
    assert 'id="sourceTitleText"' in INDEX_HTML
    assert 'id="publishTitleText"' in INDEX_HTML
    assert 'id="titleCandidateList"' in INDEX_HTML
    assert 'data-copy-target="videoPathText"' in INDEX_HTML
    assert 'data-copy-target="videoFolderText"' in INDEX_HTML
    assert 'data-copy-target="sourceCacheText"' in INDEX_HTML
    assert 'data-copy-target="publishTitleText"' in INDEX_HTML
    assert INDEX_HTML.index('id="previewPane"') < INDEX_HTML.index('id="filePathPanel"')
    assert "视频文件" in INDEX_HTML
    assert "所在目录" in INDEX_HTML
    assert "原视频标题" in INDEX_HTML
    assert "发布标题" in INDEX_HTML


def test_quality_summary_board_is_collapsible():
    assert '<details class="summary-board" id="summaryBoard">' in INDEX_HTML
    assert '<summary class="summary-top">' in INDEX_HTML
    assert '<div class="metric-grid" id="metricGrid">' in INDEX_HTML
    assert INDEX_HTML.index('id="summaryStatus"') < INDEX_HTML.index('id="metricGrid"')


def test_job_store_creates_and_updates_jobs():
    store = JobStore()
    job = store.create(
        mode="human-edit",
        source_name="source.mp4",
        input_path="/tmp/source.mp4",
        batch_id="batch-1",
        options={"preset_id": "human_edit"},
    )

    assert job["status"] == "queued"
    assert job["mode"] == "human-edit"
    assert job["quality_summary"] == {}
    assert job["input_path"] == "/tmp/source.mp4"
    assert job["batch_id"] == "batch-1"
    assert job["options"]["preset_id"] == "human_edit"

    store.log(job["id"], "开始分析")
    store.update(job["id"], status="done", artifacts={"video": "/tmp/release.mp4"})
    updated = store.get(job["id"])

    assert updated["status"] == "done"
    assert updated["logs"] == ["开始分析"]
    assert updated["artifacts"]["video"] == "/tmp/release.mp4"
    assert store.list()[0]["id"] == job["id"]


def test_job_store_creates_repair_job_from_existing_job():
    store = JobStore()
    original = store.create(
        mode="creative-edit",
        source_name="source.mp4",
        input_path="/tmp/source.mp4",
        batch_id="batch-1",
        options={"preset_id": "tutorial_longform", "quality_strictness": "strict"},
    )
    store.update(
        original["id"],
        quality_summary={
            "deductions": [
                {"code": "creative_plan_template_like_overuse"},
                {"code": "longform_duration_floor"},
            ],
            "repair_suggestions": ["降低模板感画面占比。", "提高长版覆盖线。"],
        },
    )

    repair = store.create_repair(original["id"])

    assert repair is not None
    assert repair["repair_of"] == original["id"]
    assert repair["mode"] == "creative-edit"
    assert repair["input_path"] == "/tmp/source.mp4"
    assert repair["options"]["quality_strictness"] == "audit"
    assert repair["options"]["creative_strength"] == "strong"
    assert repair["options"]["target_duration_policy"] == "retain_core"
    assert repair["options"]["repair_focus"] == ["template", "duration"]
    assert "降低模板感画面占比" in repair["options"]["production_notes"]


def test_repair_job_uses_originality_advice_as_generation_strategy():
    store = JobStore()
    original = store.create(
        mode="human-edit",
        source_name="source.mp4",
        input_path="/tmp/source.mp4",
        batch_id="batch-1",
        options={"preset_id": "tutorial_longform", "mode": "creative-edit"},
    )
    store.update(
        original["id"],
        quality_summary={
            "deductions": [],
            "repair_suggestions": [],
            "originality": {
                "risk_level": "high",
                "recommendations": [
                    "不要以重剪版直接发布，先提高原创内容比例。",
                    "使用原创解说或现场声音，不要整段保留原片音频。",
                ],
            },
        },
    )

    repair = store.create_repair(original["id"])

    assert repair is not None
    assert repair["mode"] == "creative-edit"
    assert repair["options"]["mode"] == "creative-edit"
    assert repair["options"]["quality_strictness"] == "audit"
    assert repair["options"]["creative_strength"] == "strong"
    assert repair["options"]["audio_policy"] == "replace_later"
    assert repair["options"]["visual_transform_policy"] == "none"
    assert repair["options"]["repair_focus"] == ["originality"]
    assert "不要以重剪版直接发布" in repair["options"]["production_notes"]
    assert "不要使用卡通化、线稿滤镜或假脸贴片" in repair["options"]["production_notes"]


def test_job_store_persists_history_to_disk(tmp_path):
    history_path = tmp_path / "job_history.json"
    store = JobStore(history_path=history_path)
    job = store.create(
        mode="human-edit",
        source_name="source.mp4",
        input_path="/tmp/source.mp4",
        batch_id="batch-1",
        options={"preset_id": "human_edit"},
    )
    store.update(job["id"], status="done", quality_summary={"score": 94})

    reloaded = JobStore(history_path=history_path)

    restored = reloaded.get(job["id"])
    assert restored is not None
    assert restored["status"] == "done"
    assert restored["quality_summary"]["score"] == 94
    assert reloaded.list()[0]["id"] == job["id"]


def test_job_store_regrades_persisted_history_with_current_rules(tmp_path):
    history_path = tmp_path / "job_history.json"
    quality_report = tmp_path / "quality_report.json"
    quality_report.write_text(
        json.dumps(
            {
                "status": "passed",
                "checks": {
                    "preserve_source_geometry": True,
                    "no_generated_cards_in_release": True,
                },
                "issues": [],
                "strategy": {"publish_tier": "preview_needs_voiceover_upgrade"},
            }
        ),
        encoding="utf-8",
    )
    history_path.write_text(
        json.dumps(
            {
                "jobs": [
                    {
                        "id": "old-cartoon-job",
                        "mode": "creative-edit",
                        "source_name": "old.mp4",
                        "input_path": "/tmp/old.mp4",
                        "batch_id": "batch-old",
                        "options": {"visual_transform_policy": "cartoonize"},
                        "status": "done",
                        "created_at": 1,
                        "logs": [],
                        "artifacts": {"quality_report": str(quality_report)},
                        "artifact_urls": {},
                        "quality_summary": {"score": 100, "grade": "A", "risk_level": "low"},
                        "error": "",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    reloaded = JobStore(history_path=history_path)
    restored = reloaded.get("old-cartoon-job")

    assert restored is not None
    assert restored["quality_summary"]["score"] < 80
    assert restored["quality_summary"]["risk_level"] == "high"
    assert any(
        item["code"] == "deprecated_cartoonize_output"
        for item in restored["quality_summary"]["deductions"]
    )


def test_production_presets_and_options_are_normalized():
    assert PRODUCTION_PRESETS["tutorial_longform"]["mode"] == "creative-edit"

    options = build_production_options(
        {
            "workflow": "wild",
            "preset_id": "unknown",
            "quality_strictness": "wild",
            "creative_strength": "maximum",
            "target_duration_policy": "forever",
            "audio_policy": "loud",
            "visual_transform_policy": "wild",
            "original_topic": "  Codex 视频工厂  ",
            "original_brief": "  做一条讲清楚批量生产的视频。  ",
            "asset_library_path": "  /Users/king/Videos/owned-assets  ",
            "production_notes": "  keep it real  ",
        }
    )

    assert options["workflow"] == "replicate"
    assert options["preset_id"] == "tutorial_longform"
    assert options["quality_strictness"] == "audit"
    assert options["creative_strength"] == "strong"
    assert options["target_duration_policy"] == "source_guided"
    assert options["audio_policy"] == "normalize_only"
    assert options["original_insert_policy"] == "chapter_explainers"
    assert options["visual_transform_policy"] == "none"
    assert options["visual_asset_strategy"] == "images2_contextual_inserts"
    assert options["original_topic"] == "Codex 视频工厂"
    assert options["original_brief"] == "做一条讲清楚批量生产的视频。"
    assert options["asset_library_path"] == "/Users/king/Videos/owned-assets"
    assert options["production_notes"] == "keep it real"


def test_reference_guided_defaults_to_local_images2_and_professional_voice():
    options = build_production_options({"workflow": "reference_guided_original"})

    assert options["workflow"] == "reference_guided_original"
    assert options["image_provider"] == "mock_images2"
    assert options["voice_provider"] == "mock_professional_voice"
    assert options["target_duration_policy"] == "source_guided"
    assert options["visual_asset_strategy"] == "images2_first"
    assert "duration_range_seconds" not in options


def test_auto_repair_options_upgrade_assets_voice_and_duration():
    summary = {
        "status": "failed",
        "score": 55,
        "issues": [
            {"code": "preview_only_generated_assets", "severity": "blocker"},
            {"code": "draft_voiceover_provider", "severity": "error"},
            {"code": "duration_floor_not_met", "severity": "error"},
        ],
    }
    options = {
        "workflow": "reference_guided_original",
        "image_provider": "mock_image",
        "voice_provider": "macos_say",
        "target_duration_policy": "short_summary",
    }

    assert workbench._quality_summary_needs_auto_repair(summary, options) is True

    repaired = workbench._auto_repair_options(options, summary)

    assert repaired["auto_repair_attempted"] is True
    assert repaired["image_provider"] == "mock_images2"
    assert repaired["voice_provider"] == "mock_professional_voice"
    assert repaired["target_duration_policy"] == "source_guided"
    assert "target_duration_seconds" not in repaired


def test_auto_repair_is_single_attempt_only():
    summary = {"status": "failed", "score": 40, "issues": [{"code": "missing_generated_assets"}]}
    options = {"workflow": "reference_guided_original", "auto_repair_attempted": True}

    assert workbench._quality_summary_needs_auto_repair(summary, options) is False


def test_run_job_auto_repairs_reference_guided_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(workbench, "WORKBENCH_ROOT", tmp_path)
    source = tmp_path / "reference.mp4"
    source.write_bytes(b"source")
    store = JobStore(history_path=tmp_path / "history.json")
    job = store.create(
        mode="reference-guided-original",
        source_name=source.name,
        input_path=str(source),
        options={
            "workflow": "reference_guided_original",
            "image_provider": "mock_image",
            "voice_provider": "macos_say",
        },
    )
    calls = []

    def fake_render_reference_guided_original_video(reference_video_path, output_dir, options, progress):
        calls.append(dict(options))
        progress("fake render")
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        video = output_dir / "release.mp4"
        video.write_bytes(b"video")
        quality = output_dir / "quality_report.json"
        if len(calls) == 1:
            quality.write_text(
                json.dumps(
                    {
                        "status": "failed",
                        "checks": {
                            "generated_assets_publish_ready": False,
                            "voice_provider_publishable": False,
                        },
                        "issues": [
                            {"code": "preview_only_generated_assets", "severity": "blocker"},
                            {"code": "draft_voiceover_provider", "severity": "error"},
                        ],
                    }
                ),
                encoding="utf-8",
            )
        else:
            quality.write_text(json.dumps({"status": "passed", "checks": {}, "issues": []}), encoding="utf-8")
        return {
            "mode": "reference-guided-original",
            "video": video,
            "quality_report": quality,
        }

    def fake_originality_report(source_path, output_path, report_path, edl_path=None):
        del source_path, output_path, edl_path
        Path(report_path).write_text(
            json.dumps(
                {
                    "risk_level": "low",
                    "similarity_score": 0,
                    "metrics": {"visual_similarity": 0, "audio_reuse_ratio": 0, "text_overlap_ratio": 0},
                }
            ),
            encoding="utf-8",
        )

    monkeypatch.setattr(workbench, "render_reference_guided_original_video", fake_render_reference_guided_original_video)
    monkeypatch.setattr(workbench, "build_originality_report", fake_originality_report)

    workbench._run_job(store, job["id"], source, "reference-guided-original")

    updated = store.get(job["id"])
    assert updated["status"] == "done"
    assert len(calls) == 2
    assert calls[1]["auto_repair_attempted"] is True
    assert calls[1]["image_provider"] == "mock_images2"
    assert calls[1]["voice_provider"] == "mock_professional_voice"
    assert "auto_repair_report" in updated["artifacts"]
    assert Path(updated["artifacts"]["auto_repair_report"]).exists()


def test_parse_job_request_accepts_original_generation_without_video():
    payload = {
        "workflow": "original",
        "original_topic": "Codex 批量视频工厂",
        "original_brief": "讲清楚如何从选题、脚本、分镜到质检批量生产。",
        "quality_strictness": "audit",
        "creative_strength": "strong",
        "asset_library_path": "/Users/king/Videos/owned-assets",
    }
    body = io.BytesIO(json.dumps(payload).encode("utf-8"))

    result = parse_job_request("application/json", body, content_length=len(body.getvalue()))

    assert result.status == HTTPStatus.OK
    assert result.mode == "original-generate"
    assert result.input_path is None
    assert result.input_paths == ()
    assert result.source_name == "Codex 批量视频工厂"
    assert result.options["workflow"] == "original"
    assert result.options["original_topic"] == "Codex 批量视频工厂"
    assert result.options["original_brief"].startswith("讲清楚")
    assert result.options["asset_library_path"] == "/Users/king/Videos/owned-assets"


def test_parse_job_request_rejects_original_generation_without_topic():
    payload = {"workflow": "original", "original_topic": " ", "original_brief": " "}
    body = io.BytesIO(json.dumps(payload).encode("utf-8"))

    result = parse_job_request("application/json", body, content_length=len(body.getvalue()))

    assert result.status == HTTPStatus.BAD_REQUEST
    assert "原创选题" in result.error


def test_parse_job_request_accepts_reference_guided_original_with_video_and_topic(tmp_path):
    video = tmp_path / "reference.mp4"
    payload = {
        "workflow": "reference_guided_original",
        "input_path": str(video),
        "original_topic": "普通人如何用 AI 建一个原创视频工厂",
    }
    body = io.BytesIO(json.dumps(payload).encode("utf-8"))

    result = parse_job_request("application/json", body, content_length=len(body.getvalue()))

    assert result.status == HTTPStatus.OK
    assert result.mode == "reference-guided-original"
    assert result.input_path == video
    assert result.source_name == "reference.mp4"
    assert result.options["workflow"] == "reference_guided_original"
    assert result.options["reuse_policy"] == "redraw_by_default"
    assert "duration_range_seconds" not in result.options
    assert result.options["visual_style"] == "documentary_illustration"
    assert result.options["visual_asset_strategy"] == "images2_first"
    assert result.options["platform"] == "short_video"


def test_parse_job_request_accepts_reference_guided_original_topic_alias(tmp_path):
    video = tmp_path / "reference.mp4"
    payload = {
        "workflow": "reference_guided_original",
        "input_path": str(video),
        "topic": "只用一句话主题生成原创视频",
    }
    body = io.BytesIO(json.dumps(payload).encode("utf-8"))

    result = parse_job_request("application/json", body, content_length=len(body.getvalue()))

    assert result.status == HTTPStatus.OK
    assert result.mode == "reference-guided-original"
    assert result.options["original_topic"] == "只用一句话主题生成原创视频"


def test_parse_job_request_rejects_reference_guided_original_without_video():
    payload = {
        "workflow": "reference_guided_original",
        "original_topic": "普通人如何用 AI 建一个原创视频工厂",
    }
    body = io.BytesIO(json.dumps(payload).encode("utf-8"))

    result = parse_job_request("application/json", body, content_length=len(body.getvalue()))

    assert result.status == HTTPStatus.BAD_REQUEST
    assert "参考视频" in result.error


def test_parse_job_request_accepts_reference_guided_original_without_topic_when_video_exists(tmp_path):
    video = tmp_path / "reference.mp4"
    payload = {"workflow": "reference_guided_original", "input_path": str(video), "original_topic": " "}
    body = io.BytesIO(json.dumps(payload).encode("utf-8"))

    result = parse_job_request("application/json", body, content_length=len(body.getvalue()))

    assert result.status == HTTPStatus.OK
    assert result.mode == "reference-guided-original"
    assert result.input_path == video
    assert result.options["original_topic"] == ""


def test_parse_job_request_accepts_reference_guided_original_with_source_url():
    payload = {
        "workflow": "reference_guided_original",
        "source_urls": "https://www.youtube.com/watch?v=abc",
        "topic": "用链接做原创视频",
    }
    body = io.BytesIO(json.dumps(payload).encode("utf-8"))

    result = parse_job_request("application/json", body, content_length=len(body.getvalue()))

    assert result.status == HTTPStatus.OK
    assert result.mode == "reference-guided-original"
    assert result.input_path is None
    assert result.input_paths == ()
    assert result.source_urls == ("https://www.youtube.com/watch?v=abc",)
    assert result.source_names == ("www.youtube.com",)
    assert result.options["workflow"] == "reference_guided_original"


def test_parse_job_request_accepts_reference_guided_original_with_source_url_and_no_topic():
    payload = {
        "workflow": "reference_guided_original",
        "source_urls": "https://www.youtube.com/watch?v=abc",
    }
    body = io.BytesIO(json.dumps(payload).encode("utf-8"))

    result = parse_job_request("application/json", body, content_length=len(body.getvalue()))

    assert result.status == HTTPStatus.OK
    assert result.mode == "reference-guided-original"
    assert result.source_urls == ("https://www.youtube.com/watch?v=abc",)
    assert result.options["original_topic"] == ""


def test_parse_job_request_accepts_multiple_json_source_urls():
    payload = {
        "mode": "creative-edit",
        "source_urls": ["https://youtu.be/abc", "https://v.douyin.com/xyz"],
    }
    body = io.BytesIO(json.dumps(payload).encode("utf-8"))

    result = parse_job_request("application/json", body, content_length=len(body.getvalue()))

    assert result.status == HTTPStatus.OK
    assert result.mode == "creative-edit"
    assert result.input_paths == ()
    assert result.source_urls == ("https://youtu.be/abc", "https://v.douyin.com/xyz")
    assert result.source_names == ("youtu.be", "v.douyin.com")


def test_production_options_reject_cartoonize_visual_rewrite():
    options = build_production_options(
        {
            "preset_id": "tutorial_longform",
            "visual_transform_policy": "cartoonize",
        }
    )

    assert options["visual_transform_policy"] == "none"


def test_production_options_accept_face_only_visual_rewrite():
    options = build_production_options(
        {
            "preset_id": "tutorial_longform",
            "visual_transform_policy": "face_only",
        }
    )

    assert options["visual_transform_policy"] == "face_only"


def test_parse_job_request_accepts_multiple_json_paths(tmp_path):
    first = tmp_path / "first.mp4"
    second = tmp_path / "second.mp4"
    payload = {
        "mode": "creative-edit",
        "input_path": f"{first}\n{second}",
        "preset_id": "tutorial_longform",
        "quality_strictness": "strict",
    }
    body = io.BytesIO(json.dumps(payload).encode("utf-8"))

    result = parse_job_request("application/json", body, content_length=len(body.getvalue()))

    assert result.status == HTTPStatus.OK
    assert result.mode == "creative-edit"
    assert result.input_paths == (first, second)
    assert result.source_names == ("first.mp4", "second.mp4")
    assert result.options["preset_id"] == "tutorial_longform"
    assert result.options["quality_strictness"] == "strict"


def test_build_quality_summary_reads_reports(tmp_path):
    quality_report = tmp_path / "quality_report.json"
    creative_plan = tmp_path / "creative_plan.json"
    originality_report = tmp_path / "originality_report.json"
    quality_report.write_text(
        json.dumps(
            {
                "status": "passed",
                "checks": {
                    "preserve_source_geometry": True,
                    "no_generated_cards_in_release": True,
                    "creative_plan_template_like_budget": True,
                    "longform_duration_floor": True,
                    "creative_plan_has_director_moves": True,
                    "creative_plan_has_release_chronology": True,
                },
                "issues": [],
                "output_geometry": {"width": 1920, "height": 1080},
                "source_geometry": {"width": 1920, "height": 1080},
            }
        ),
        encoding="utf-8",
    )
    creative_plan.write_text(
        json.dumps(
            {
                "recommended_variant": {
                    "total_duration": 256.2,
                    "segments": [
                        {"template_like": False},
                        {"template_like": False},
                        {"template_like": True},
                        {"template_like": False},
                        {"template_like": False},
                        {"template_like": False},
                    ],
                },
                "creative_strategy": {
                    "version": "v5_director_longform",
                    "treatment": "director_longform_chapter_cut",
                    "target_duration": 292.9,
                    "creative_moves": ["cold_open", "action_chain"],
                },
            }
        ),
        encoding="utf-8",
    )
    originality_report.write_text(
        json.dumps(
            {
                "risk_level": "high",
                "similarity_score": 87,
                "metrics": {
                    "visual_similarity": 0.87,
                    "audio_reuse_ratio": 0.94,
                    "text_overlap_ratio": 0.88,
                    "source_reuse_ratio": 0.90,
                },
                "recommendations": ["不要以重剪版直接发布，先提高原创内容比例。"],
            }
        ),
        encoding="utf-8",
    )

    summary = build_quality_summary(
        {
            "quality_report": str(quality_report),
            "creative_plan": str(creative_plan),
            "originality_report": str(originality_report),
        }
    )

    assert summary["status"] == "passed"
    assert summary["score"] < 80
    assert summary["grade"] in {"C", "D"}
    assert summary["risk_level"] == "high"
    assert {"label": "模板预算", "passed": True, "key": "creative_plan_template_like_budget"} in summary["checks"]
    assert {"label": "计划时长", "value": "4:16"} in summary["metrics"]
    assert {"label": "模板风险", "value": "1/1"} in summary["metrics"]
    assert {"label": "原创风险", "value": "high / 87"} in summary["metrics"]
    assert summary["strategy"]["moves"] == ["cold_open", "action_chain"]
    assert summary["originality"]["risk_level"] == "high"
    assert "不要以重剪版直接发布" in summary["originality"]["recommendations"][0]
    assert any(item["code"] == "originality_high_risk" for item in summary["deductions"])


def test_build_quality_summary_scores_failed_reports(tmp_path):
    quality_report = tmp_path / "quality_report.json"
    quality_report.write_text(
        json.dumps(
            {
                "status": "failed",
                "checks": {
                    "preserve_source_geometry": False,
                    "no_generated_cards_in_release": True,
                    "creative_plan_template_like_budget": False,
                },
                "issues": [
                    {
                        "code": "bad_geometry",
                        "severity": "error",
                        "message": "输出比例错误。",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    summary = build_quality_summary({"quality_report": str(quality_report)})

    assert summary["status"] == "failed"
    assert summary["score"] < 80
    assert summary["grade"] in {"C", "D"}
    assert summary["risk_level"] in {"medium", "high"}
    assert any(item["code"] == "preserve_source_geometry" for item in summary["deductions"])
    assert any("比例" in item for item in summary["repair_suggestions"])


def test_build_quality_summary_treats_visual_artifact_blocker_as_high_risk(tmp_path):
    quality_report = tmp_path / "quality_report.json"
    quality_report.write_text(
        json.dumps(
            {
                "status": "failed",
                "checks": {
                    "preserve_source_geometry": True,
                    "visual_artifact_free": False,
                },
                "issues": [
                    {
                        "code": "visual_line_art_artifact",
                        "severity": "blocker",
                        "message": "质检图呈现过曝线稿/滤镜化特征。",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    summary = build_quality_summary({"quality_report": str(quality_report)}, options={"quality_strictness": "audit"})

    assert summary["status"] == "failed"
    assert summary["grade"] == "D"
    assert summary["risk_level"] == "high"
    assert {"label": "真实画面", "passed": False, "key": "visual_artifact_free"} in summary["checks"]
    assert any(item["code"] == "visual_line_art_artifact" for item in summary["deductions"])


def test_build_quality_summary_reads_original_generation_artifacts(tmp_path):
    quality_report = tmp_path / "quality_report.json"
    original_strategy = tmp_path / "original_strategy.json"
    storyboard = tmp_path / "storyboard.json"
    originality_report = tmp_path / "originality_report.json"
    quality_report.write_text(
        json.dumps(
            {
                "status": "passed",
                "checks": {
                    "original_script_present": True,
                    "storyboard_present": True,
                    "no_source_reuse": True,
                    "visual_preview_present": True,
                    "originality_gate_passed": True,
                    "dynamic_shot_plan_present": True,
                    "caption_timeline_present": True,
                    "scene_motion_variety": True,
                    "voiceover_audio_present": True,
                    "asset_pass_ready": True,
                    "asset_visuals_embedded": True,
                },
                "issues": [],
                "strategy": {
                    "publish_tier": "preview_needs_voiceover_upgrade",
                    "asset_usage_scene_count": 3,
                },
            }
        ),
        encoding="utf-8",
    )
    original_strategy.write_text(
        json.dumps(
            {
                "topic": "Codex 批量视频工厂",
                "target_duration": 180,
                "format": "tutorial",
                "chapters": ["痛点", "流程", "质检"],
            }
        ),
        encoding="utf-8",
    )
    storyboard.write_text(
        json.dumps({"scenes": [{"title": "痛点"}, {"title": "流程"}, {"title": "质检"}]}),
        encoding="utf-8",
    )
    originality_report.write_text(
        json.dumps(
            {
                "risk_level": "low",
                "similarity_score": 0,
                "metrics": {"visual_similarity": 0.0, "audio_reuse_ratio": 0.0, "text_overlap_ratio": 0.0},
                "recommendations": ["没有源片复用，进入人工选题事实核查。"],
            }
        ),
        encoding="utf-8",
    )

    summary = build_quality_summary(
        {
            "quality_report": str(quality_report),
            "original_strategy": str(original_strategy),
            "storyboard": str(storyboard),
            "originality_report": str(originality_report),
        }
    )

    assert summary["status"] == "passed"
    assert {"label": "原创脚本", "passed": True, "key": "original_script_present"} in summary["checks"]
    assert {"label": "无源片复用", "passed": True, "key": "no_source_reuse"} in summary["checks"]
    assert {"label": "动态镜头", "passed": True, "key": "dynamic_shot_plan_present"} in summary["checks"]
    assert {"label": "字幕时间线", "passed": True, "key": "caption_timeline_present"} in summary["checks"]
    assert {"label": "镜头变化", "passed": True, "key": "scene_motion_variety"} in summary["checks"]
    assert {"label": "旁白音频", "passed": True, "key": "voiceover_audio_present"} in summary["checks"]
    assert {"label": "资产通行证", "passed": True, "key": "asset_pass_ready"} in summary["checks"]
    assert {"label": "资产入镜", "passed": True, "key": "asset_visuals_embedded"} in summary["checks"]
    assert {"label": "生产类型", "value": "无参考原创"} in summary["metrics"]
    assert {"label": "发布分级", "value": "样片待配音升级"} in summary["metrics"]
    assert {"label": "资产入镜", "value": "3 场"} in summary["metrics"]
    assert {"label": "原创场景", "value": "3 场"} in summary["metrics"]
    assert {"label": "原创风险", "value": "low / 0"} in summary["metrics"]


def test_build_quality_summary_reads_reference_guided_delivery_artifacts(tmp_path):
    quality_report = tmp_path / "quality_report.json"
    user_delivery = tmp_path / "user_delivery.json"
    reference_blueprint = tmp_path / "reference_blueprint.json"
    content_plan = tmp_path / "content_plan.json"
    storyboard_v2 = tmp_path / "storyboard_v2.json"
    generated_asset_manifest = tmp_path / "generated_asset_manifest.json"
    cover_asset_manifest = tmp_path / "cover_asset_manifest.json"
    voiceover_manifest = tmp_path / "voiceover_manifest.json"
    quality_report.write_text(
        json.dumps(
            {
                "status": "passed",
                "checks": {
                    "reference_blueprint_present": True,
                    "reference_media_not_reused": True,
                    "content_plan_depth": True,
                    "storyboard_v2_present": True,
                    "visual_prompt_pack_present": True,
                    "generated_assets_ready": True,
                    "cover_brief_ready": True,
                    "cover_prompt_pack_ready": True,
                    "cover_assets_ready": True,
                    "cover_assets_publish_ready": True,
                    "cover_not_overcomplicated": True,
                    "cover_text_concise": True,
                    "subtitle_timeline_present": True,
                    "voice_provider_publishable": True,
                    "user_delivery_present": True,
                },
                "issues": [],
                "strategy": {
                    "publish_tier": "publish_candidate",
                    "workflow": "reference_guided_original",
                    "target_duration_seconds": 240,
                    "asset_count": 10,
                    "cover_provider": "mock_images2",
                    "cover_publish_ready": True,
                    "voice_provider": "mock_professional_voice",
                },
            }
        ),
        encoding="utf-8",
    )
    user_delivery.write_text(
        json.dumps(
            {
                "mode": "一键原创视频",
                "release_decision": {"status": "可发布", "reason": "素材和配音已满足样片发布门槛。"},
                "front_labels": {"cover_status": "封面可发布"},
                "next_actions": [],
            }
        ),
        encoding="utf-8",
    )
    reference_blueprint.write_text(
        json.dumps({"workflow": "reference_guided_original", "reuse_policy": "redraw_by_default"}),
        encoding="utf-8",
    )
    content_plan.write_text(
        json.dumps({"topic": "AI 原创视频工厂", "chapters": [{"title": "起点"}, {"title": "蓝图"}, {"title": "素材"}, {"title": "发布"}]}),
        encoding="utf-8",
    )
    storyboard_v2.write_text(json.dumps({"scenes": [{}, {}, {}, {}]}), encoding="utf-8")
    generated_asset_manifest.write_text(json.dumps({"status": "ready", "asset_count": 8}), encoding="utf-8")
    cover_asset_manifest.write_text(
        json.dumps({"status": "ready", "publish_ready": True, "provider": "mock_images2"}),
        encoding="utf-8",
    )
    voiceover_manifest.write_text(json.dumps({"status": "ready", "publish_ready": True}), encoding="utf-8")

    summary = build_quality_summary(
        {
            "quality_report": str(quality_report),
            "user_delivery": str(user_delivery),
            "reference_blueprint": str(reference_blueprint),
            "content_plan": str(content_plan),
            "storyboard_v2": str(storyboard_v2),
            "generated_asset_manifest": str(generated_asset_manifest),
            "cover_asset_manifest": str(cover_asset_manifest),
            "voiceover_manifest": str(voiceover_manifest),
        }
    )

    assert summary["status"] == "passed"
    assert {"label": "交付结论", "value": "可发布"} in summary["metrics"]
    assert {"label": "生产类型", "value": "一键原创视频"} in summary["metrics"]
    assert {"label": "参考用途", "value": "学习结构，不复用画面"} in summary["metrics"]
    assert {"label": "内容章节", "value": "4 章"} in summary["metrics"]
    assert {"label": "生成素材", "value": "8 个"} in summary["metrics"]
    assert {"label": "封面状态", "value": "封面可发布"} in summary["metrics"]
    assert {"label": "封面门禁", "value": "可发布"} in summary["metrics"]
    assert {"label": "参考蓝图", "passed": True, "key": "reference_blueprint_present"} in summary["checks"]
    assert {"label": "素材生成", "passed": True, "key": "generated_assets_ready"} in summary["checks"]
    assert {"label": "封面生成", "passed": True, "key": "cover_assets_ready"} in summary["checks"]
    assert {"label": "封面简洁", "passed": True, "key": "cover_not_overcomplicated"} in summary["checks"]
    assert {"label": "自动字幕", "passed": True, "key": "subtitle_timeline_present"} in summary["checks"]
    assert {"label": "可发布配音", "passed": True, "key": "voice_provider_publishable"} in summary["checks"]


def test_parse_job_request_accepts_creative_edit_mode(tmp_path):
    video = tmp_path / "source.mp4"
    payload = {"mode": "creative-edit", "input_path": str(video)}
    body = io.BytesIO(json.dumps(payload).encode("utf-8"))

    result = parse_job_request("application/json", body, content_length=len(body.getvalue()))

    assert result.status == HTTPStatus.OK
    assert result.mode == "creative-edit"


def test_run_job_downloads_source_url_before_reference_generation(tmp_path, monkeypatch):
    store = JobStore()
    job = store.create(
        mode="reference-guided-original",
        source_name="www.youtube.com",
        input_path="",
        options={"workflow": "reference_guided_original", "source_url": "https://www.youtube.com/watch?v=abc"},
    )
    downloaded_video = tmp_path / "source" / "source.mp4"
    download_report = tmp_path / "source_download.json"
    downloaded_video.parent.mkdir(parents=True)
    downloaded_video.write_bytes(b"video")
    download_report.write_text(json.dumps({"status": "ready"}), encoding="utf-8")

    class FakeDownloadResult:
        video_path = downloaded_video
        report_path = download_report
        platform = "youtube"
        title = "Demo"
        recommended_publish_title = "Demo：从参考到原创成片"
        publish_title_candidates = ("Demo：从参考到原创成片", "Demo 的关键流程拆解")

    def fake_download(url, output_dir, progress=None, title_context=None):
        assert url == "https://www.youtube.com/watch?v=abc"
        assert output_dir.name == job["id"]
        assert title_context["workflow"] == "reference_guided_original"
        if progress:
            progress("下载完成")
        return FakeDownloadResult()

    def fake_render(reference_video_path, output_dir, options, progress=None):
        assert Path(reference_video_path) == downloaded_video
        assert options["source_title"] == "Demo"
        release = Path(output_dir) / "release.mp4"
        release.write_bytes(b"release")
        quality = Path(output_dir) / "quality_report.json"
        quality.write_text(json.dumps({"status": "passed", "checks": {}, "issues": []}), encoding="utf-8")
        return {"mode": "reference-guided-original", "video": release, "quality_report": quality}

    monkeypatch.setattr(workbench, "WORKBENCH_ROOT", tmp_path)
    monkeypatch.setattr(workbench, "download_source_video", fake_download)
    monkeypatch.setattr(workbench, "render_reference_guided_original_video", fake_render)
    monkeypatch.setattr(workbench, "build_originality_report", lambda *args, **kwargs: None)

    workbench._run_job(store, job["id"], None, "reference-guided-original")

    updated = store.get(job["id"])
    assert updated["status"] == "done"
    assert updated["input_path"] == str(downloaded_video)
    assert updated["artifacts"]["source_video"] == str(downloaded_video)
    assert updated["artifacts"]["source_download"] == str(download_report)
    assert updated["source_metadata"]["source_title"] == "Demo"
    assert updated["source_metadata"]["recommended_publish_title"] == "Demo：从参考到原创成片"
    assert "Demo 的关键流程拆解" in updated["source_metadata"]["publish_title_candidates"]
    assert "下载完成" in updated["logs"]


def test_parse_job_request_rejects_empty_json_body():
    body = io.BytesIO(json.dumps({}).encode("utf-8"))
    result = parse_job_request("application/json", body, content_length=2)

    assert result.status == HTTPStatus.BAD_REQUEST
    assert result.error == "请提供视频文件、本地视频路径或视频链接。"
