# Codex Same Theme Remix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a 60-90 second same-theme Codex/AI Skill remix video from the user-provided reference video without changing the topic.

**Architecture:** Use the original MP4 as the only visual/audio source. Trim real motion clips, add restrained transparent subtitle/branding overlays, concatenate segments into a playable horizontal MP4, and verify dimensions, duration, frames, and tests.

**Tech Stack:** Python 3.9, Pillow for transparent overlays, ffmpeg/ffprobe for video trimming and assembly, pytest for renderer contract tests.

---

### Task 1: Renderer Contract

**Files:**
- Create: `video_factory/codex_reference_remix.py`
- Create: `tools/render_codex_reference_remix.py`
- Create: `tests/test_codex_reference_remix.py`

- [x] **Step 1: Define the target behavior**

The renderer must:
- use `/Users/king/Downloads/下载.mp4` as the only source video,
- keep the Codex/AI Skill/agent/knowledge-base theme,
- trim multiple real motion clips rather than rendering still frames,
- add transparent overlays that leave the center video visible,
- export `video_factory/output/codex-reference-remix/release.mp4`.

- [x] **Step 2: Write tests**

Run: `python3 -m pytest tests/test_codex_reference_remix.py -q`

Expected before implementation: tests fail because the module does not exist.

- [x] **Step 3: Implement renderer**

Create a focused module that defines the storyboard, overlay drawing, ffmpeg commands, segment concatenation, script, and report writing.

- [x] **Step 4: Render sample**

Run: `python3 tools/render_codex_reference_remix.py`

Expected: `release.mp4`, `cover.png`, `contact_sheet.jpg`, `script.md`, and `render_report.json` exist in `video_factory/output/codex-reference-remix/`.

- [x] **Step 5: Verify**

Run:
- `ffprobe -v error -select_streams v:0 -show_entries stream=width,height,r_frame_rate,duration,nb_frames -show_entries format=duration,size -of json video_factory/output/codex-reference-remix/release.mp4`
- `python3 -m pytest`

Expected:
- 1920x1080 video,
- 30fps,
- 60-90 seconds,
- nonzero frame count,
- all tests pass.
