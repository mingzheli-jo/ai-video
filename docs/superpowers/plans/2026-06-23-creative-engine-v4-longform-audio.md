# Creative Engine V4 Longform Audio Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade creative-edit into a chaptered longform video creator with local audio evidence and stronger quality gates.

**Architecture:** Add a focused audio analysis module, extend creative plan dataclasses with audio fields, adjust segment count/duration policy for long sources, then enforce the new artifacts in quality checks and the workbench.

**Tech Stack:** Python 3.9, pytest, ffmpeg/ffprobe, PIL, existing Video Factory modules.

---

### Task 1: Audio Analysis Artifact

**Files:**
- Create: `video_factory/audio.py`
- Modify: `video_factory/replicate.py`
- Test: `tests/test_replicate.py`

- [ ] Write tests for serializing audio cues and provider status.
- [ ] Implement `AudioProviderStatus`, `AudioCue`, `AudioAnalysis`, and JSON helpers.
- [ ] Add a local ffmpeg-backed analyzer that produces deterministic energy tags and a safe fallback.

### Task 2: Planner Integration

**Files:**
- Modify: `video_factory/creative.py`
- Test: `tests/test_replicate.py`

- [ ] Write failing tests proving long sources produce more than 8 segments and more than 120 seconds for a 563-second tutorial.
- [ ] Feed audio cues into `build_creative_plan`.
- [ ] Add `audio_provider`, `audio_coverage`, `audio_tags`, and `audio_evidence` to plan JSON.
- [ ] Replace the fixed long-source 8-segment cap with a chapter duration policy.

### Task 3: Pipeline And Quality Gates

**Files:**
- Modify: `video_factory/replicate.py`
- Test: `tests/test_replicate.py`

- [ ] Generate `audio_analysis.json` in creative-edit mode.
- [ ] Add quality checks for audio artifact, audio provider, audio evidence, and longform minimum duration.
- [ ] Include `audio_analysis` in returned artifacts and reports.

### Task 4: Workbench And Docs

**Files:**
- Modify: `video_factory/workbench.py`
- Modify: `README.md`
- Test: `tests/test_workbench.py`

- [ ] Surface `audio_analysis.json` in the workbench copy and artifact links.
- [ ] Explain Creative Engine V4 in README.
- [ ] Verify the local workbench page renders.

### Task 5: Verification And Sample

**Files:**
- Output only under `/private/tmp/video_factory_creative_engine_v4`

- [ ] Run focused tests.
- [ ] Run `python3 -m pytest`.
- [ ] Render a creative-edit sample from `/Users/king/Downloads/下载 (1).mp4`.
- [ ] Inspect ffprobe, quality report, creative plan, audio analysis, and contact sheet.
