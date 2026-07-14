# Video Factory Production Workbench Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first production-ready workbench layer for batch video replication, production controls, quality scoring, repair runs, history, and presets.

**Architecture:** Keep the existing local HTTP workbench and extend its job model. Batch submission creates multiple ordinary jobs, quality scoring enriches `quality_summary`, and repair/history APIs sit beside the existing job API.

**Tech Stack:** Python stdlib HTTP server, existing `render_replicate()`, pytest, Playwright for local UI verification.

---

## File Structure

- Modify `video_factory/workbench.py`: production presets, batch request parsing, job metadata, history, repair API, quality scoring, UI.
- Modify `tests/test_workbench.py`: red/green tests for presets, batch parsing, scoring, history, repair metadata, UI strings.
- Update `README.md`: document production workbench usage after implementation.

## Task 1: Production Presets And Options

- [ ] Add `PRODUCTION_PRESETS` with four presets: `tutorial_longform`, `human_edit`, `original_enhanced`, `food_real_cut`.
- [ ] Add `build_production_options(payload)` to normalize preset, strictness, creative strength, duration policy, audio policy, and notes.
- [ ] Test invalid values fall back to safe defaults.

## Task 2: Batch Request Parsing

- [ ] Extend `ParsedJobRequest` with `input_paths`, `source_names`, and `options`.
- [ ] Accept newline-separated local paths.
- [ ] Accept multiple multipart file uploads.
- [ ] Return a clear error when no paths/files are provided.

## Task 3: Job Store, History, And Repair Metadata

- [ ] Store `input_path`, `options`, `batch_id`, and `repair_of` on every job.
- [ ] Add `JobStore.list()` to show newest jobs.
- [ ] Add `JobStore.create_repair()` to clone source path and options from an existing job.

## Task 4: Quality Score

- [ ] Extend `build_quality_summary()` with `score`, `grade`, `risk_level`, `deductions`, and `repair_suggestions`.
- [ ] Deduct points for failed checks and issues.
- [ ] Keep `quality_report.json` pass/fail as the hard gate.

## Task 5: Workbench APIs

- [ ] `POST /api/jobs` returns a batch payload containing `batch_id`, `jobs`, and `primary_job_id`.
- [ ] `GET /api/jobs` returns history.
- [ ] `POST /api/jobs/<id>/repair` creates a repair job and starts rendering.

## Task 6: UI Upgrade

- [ ] Replace single path input with batch-aware path textarea while keeping the original path id for compatibility.
- [ ] Add preset and production parameter controls.
- [ ] Add batch queue and task history panes.
- [ ] Add score/grade/risk and repair suggestions to the验收面板.
- [ ] Add a repair button that calls the repair API.

## Task 7: Verification

- [ ] Run `python3 -m pytest tests/test_workbench.py -q`.
- [ ] Run `python3 -m pytest -q`.
- [ ] Restart `python3 -m video_factory.workbench --port 56080`.
- [ ] Verify the UI with Playwright and save a screenshot.

## Self-Review

- The plan covers all six P0 capabilities from the design.
- External ASR, platform scraping, and copyrighted素材 ingestion are explicitly out of scope.
- The first implementation is product-layer complete while leaving deeper creative-planner parameterization for a later focused change.
