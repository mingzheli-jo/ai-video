# Video Replication Workbench Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Provide a local browser workbench so the user can replicate videos by choosing or dropping a source video, selecting a mode, and watching the resulting artifacts appear.

**Architecture:** Add a generic replication engine that accepts any source video path and renders `original-enhanced`, `human-edit`, or `auto` outputs. Add a standard-library HTTP workbench on port 56080 with a static HTML/CSS/JS interface, JSON job API, background rendering thread, and artifact links.

**Tech Stack:** Python standard library, FFmpeg, pytest, plain HTML/CSS/JavaScript.

---

### Task 1: Generic Replication Engine

**Files:**
- Create: `video_factory/replicate.py`
- Test: `tests/test_replicate.py`

- [ ] **Step 1: Write failing tests**

Cover:
- mode selection for `auto`
- safe output path creation from an arbitrary input filename
- human-edit storyboard duration
- segment command must not contain overlay/drawtext/progress UI

- [ ] **Step 2: Implement engine**

Expose:
- `build_replicate_paths(input_video, mode, output_root=None, job_id=None)`
- `choose_mode(input_video, source_duration)`
- `build_human_edit_storyboard(source_duration)`
- `build_human_edit_scene_command(source_video, segment, output_path)`
- `render_replicate(input_video, mode="auto", output_dir=None, progress=None)`

### Task 2: Local Workbench Server

**Files:**
- Create: `video_factory/workbench.py`
- Test: `tests/test_workbench.py`

- [ ] **Step 1: Write failing tests**

Cover:
- index HTML contains upload, path input, mode selector, start action, job status, artifact area
- API rejects empty requests
- job store can create and update a job without rendering

- [ ] **Step 2: Implement server**

Expose:
- `run_server(port=56080)`
- `make_app_handler(job_store)`
- `JobStore`

Routes:
- `GET /`
- `POST /api/jobs`
- `GET /api/jobs/<id>`
- `GET /artifact/<id>/<name>`

### Task 3: Verification

- [ ] Run focused tests:

```bash
python3 -m pytest tests/test_replicate.py tests/test_workbench.py -q
```

- [ ] Start the workbench:

```bash
python3 -m video_factory.workbench --port 56080
```

- [ ] Verify the page loads in browser and shows the expected controls.

- [ ] Run full tests:

```bash
python3 -m pytest
```
