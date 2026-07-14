# Quality-First Visual Sourcing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a quality-first visual sourcing layer for reference-guided original videos.

**Architecture:** Insert a visual requirements stage after storyboard creation and an asset sourcing plan before prompt generation. Quality checks consume those artifacts and block publish candidates when assets are mock-only, missing, or not images2-first.

**Tech Stack:** Python, pytest, PIL-based local preview renderer, existing `video_factory.workbench` HTML/JS string.

---

### Task 1: Visual Requirements And Sourcing Plan

**Files:**
- Modify: `video_factory/reference_guided_original.py`
- Test: `tests/test_reference_guided_original.py`

- [ ] Write failing tests for `build_visual_requirements()` and `build_asset_sourcing_plan()`.
- [ ] Implement the functions with images2-first source priority and no reference-frame reuse.
- [ ] Run the targeted reference-guided tests.

### Task 2: Prompt Pack And Asset Manifest Contract

**Files:**
- Modify: `video_factory/reference_guided_original.py`
- Test: `tests/test_reference_guided_original.py`

- [ ] Write failing tests proving prompt packs are generated from sourcing decisions.
- [ ] Add publish readiness metadata to generated asset manifests.
- [ ] Treat `mock_image` as preview-only and `mock_images2` as a test double for publishable images2.

### Task 3: Quality Gate Integration

**Files:**
- Modify: `video_factory/reference_guided_original.py`
- Test: `tests/test_reference_guided_original.py`

- [ ] Write failing tests proving mock-only assets cannot be marked publish candidate.
- [ ] Update `write_reference_guided_quality_report()` and `build_user_delivery()`.
- [ ] Return new artifacts from `render_reference_guided_original_video()`.

### Task 4: Workbench Interface

**Files:**
- Modify: `video_factory/workbench.py`
- Test: `tests/test_workbench.py`

- [ ] Write failing tests for the new `visual_asset_strategy` option and UI text.
- [ ] Add a simple “画面生产策略” selector before expert options.
- [ ] Move `assetLibraryPath` into expert settings.
- [ ] Show visual sourcing artifacts in advanced outputs and quality summary.

### Task 5: Verification And Service Restart

**Files:**
- Test: `tests/test_reference_guided_original.py`, `tests/test_workbench.py`, full suite.

- [ ] Run targeted tests.
- [ ] Run full pytest suite.
- [ ] Restart `python3 -m video_factory.workbench --port 56080`.
- [ ] Verify the local workbench returns HTTP 200.
