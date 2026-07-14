# Portugal DR Congo Real Footage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the synthetic presenter/studio look with a real-footage football analysis montage.

**Architecture:** Keep the existing Portugal DR Congo video plan, script, TTS, and segment rendering pipeline. Add a manifest-level `cinematic_broll` overlay style that renders compact broadcast-like UI over full-screen royalty-free football footage.

**Tech Stack:** Python, Pillow, FFmpeg, pytest, Mixkit free stock video assets.

---

### Task 1: Lock the Real-Footage Asset Contract

**Files:**
- Modify: `tests/test_video_factory.py`
- Modify: `video_factory/assets/portugal_dr_congo/asset_manifest.json`

- [ ] **Step 1: Write failing tests**

Add tests that require all scheduled Portugal DR Congo assets to be videos and require the manifest overlay style to be `cinematic_broll`.

- [ ] **Step 2: Run focused tests to verify failure**

Run: `python3 -m pytest tests/test_video_factory.py::test_portugal_visual_asset_manifest_schedules_only_real_video_footage -q`

Expected: fail because the current manifest schedules generated images.

- [ ] **Step 3: Update the manifest**

Add downloaded Mixkit football B-roll entries and set every `segment_asset_order` value to video asset keys only.

- [ ] **Step 4: Run the focused test**

Run: `python3 -m pytest tests/test_video_factory.py::test_portugal_visual_asset_manifest_schedules_only_real_video_footage -q`

Expected: pass.

### Task 2: Add Compact Cinematic Overlay Rendering

**Files:**
- Modify: `tests/test_video_factory.py`
- Modify: `video_factory/pipeline.py`

- [ ] **Step 1: Write failing overlay test**

Add a test for `_draw_cinematic_broll_overlay_frame` that checks the image is 1920x1080 RGBA, mostly transparent, and only has strong alpha in compact UI/subtitle regions.

- [ ] **Step 2: Run focused test to verify failure**

Run: `python3 -m pytest tests/test_video_factory.py::test_cinematic_broll_overlay_renderer_stays_lightweight -q`

Expected: fail because the renderer does not exist yet.

- [ ] **Step 3: Implement renderer and dispatch**

Add `_draw_cinematic_broll_overlay_frame`, helper drawing functions, and update `_render_premium_asset_segments` to choose it when `manifest["overlay_style"] == "cinematic_broll"`.

- [ ] **Step 4: Run focused tests**

Run: `python3 -m pytest tests/test_video_factory.py::test_cinematic_broll_overlay_renderer_stays_lightweight tests/test_video_factory.py::test_render_video_with_visual_asset_manifest_uses_segment_export -q`

Expected: pass.

### Task 3: Render and Verify the New Release

**Files:**
- Output: `video_factory/output/portugal-dr-congo-release/release.mp4`
- Output: `/private/tmp/portugal_dr_congo_real_footage_contact_sheet.jpg`

- [ ] **Step 1: Run all tests**

Run: `python3 -m pytest`

Expected: all tests pass.

- [ ] **Step 2: Render the release**

Run: `python3 -m video_factory --template portugal-dr-congo --tts-provider edge --voice zh-CN-YunxiNeural --edge-rate +8% --output video_factory/output/portugal-dr-congo-release`

Expected: creates `release.mp4` using the real-footage manifest.

- [ ] **Step 3: Verify media parameters**

Run: `ffprobe -v error -select_streams v:0 -show_entries stream=width,height,r_frame_rate -show_entries format=duration -of json video_factory/output/portugal-dr-congo-release/release.mp4`

Expected: width 1920, height 1080, frame rate 30/1, duration 340.

- [ ] **Step 4: Create and inspect contact sheet**

Run: `ffmpeg -hide_banner -y -i video_factory/output/portugal-dr-congo-release/release.mp4 -vf fps=1/42,scale=320:-1,tile=4x2 -frames:v 1 /private/tmp/portugal_dr_congo_real_footage_contact_sheet.jpg`

Expected: sampled frames show real football footage, compact overlays, readable subtitles, and no fake presenter/studio imagery.
