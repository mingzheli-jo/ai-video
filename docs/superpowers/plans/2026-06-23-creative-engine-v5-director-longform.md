# Creative Engine V5 Director Longform Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend creative-edit into a fuller director-guided longform mode with higher duration coverage and explicit creative intent.

**Architecture:** Update the planner's target duration and sample limits, add creative strategy metadata, add per-segment creative moves, and enforce the new standards in quality checks.

**Tech Stack:** Python 3.9, pytest, ffmpeg, existing Video Factory modules.

---

### Task 1: TDD For V5 Duration And Sampling

**Files:**
- Modify: `tests/test_replicate.py`

- [ ] Add a failing test requiring 36 samples for a 563-second creative source.
- [ ] Add a failing test requiring at least 28 planned segments and at least 270 seconds target duration.

### Task 2: Planner Strategy

**Files:**
- Modify: `video_factory/creative.py`
- Test: `tests/test_replicate.py`

- [ ] Add `CreativeStrategy`.
- [ ] Add `creative_strategy` to `CreativePlan`.
- [ ] Add `creative_move` to `CreativeSegment`.
- [ ] Include creative moves in `candidate_edl.md`.

### Task 3: Pipeline Limits

**Files:**
- Modify: `video_factory/replicate.py`
- Test: `tests/test_replicate.py`

- [ ] Raise long source sample count to 36.
- [ ] Raise longform duration floor in quality checks.
- [ ] Add quality checks for missing `creative_strategy` and weak `creative_move` coverage.

### Task 4: Workbench And Docs

**Files:**
- Modify: `video_factory/workbench.py`
- Modify: `README.md`
- Test: `tests/test_workbench.py`

- [ ] Surface director longform wording in workbench.
- [ ] Document V5 duration and creative strategy behavior.

### Task 5: Verification And Sample

**Files:**
- Output: `/private/tmp/video_factory_creative_engine_v5`

- [ ] Run focused tests.
- [ ] Run full test suite.
- [ ] Render V5 sample from `/Users/king/Downloads/下载 (1).mp4`.
- [ ] Inspect ffprobe, quality report, creative plan, audio analysis, and contact sheet.
