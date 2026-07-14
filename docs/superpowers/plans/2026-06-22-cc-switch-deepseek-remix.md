# CC Switch DeepSeek Remix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a high-quality 80-90 second same-theme remix from `/Users/king/Downloads/下载 (1).mp4` focused on CC Switch, Codex, DeepSeek, API keys, and model configuration.

**Architecture:** Use the provided MP4 as the only audiovisual source, trim real motion clips, add restrained transparent chapter/caption overlays, concatenate clips into a horizontal MP4, and verify with ffprobe, contact sheet review, and pytest.

**Tech Stack:** Python 3.9, Pillow, ffmpeg/ffprobe, pytest.

---

### Task 1: Renderer Contract

**Files:**
- Create: `tests/test_cc_switch_deepseek_remix.py`
- Create: `video_factory/cc_switch_deepseek_remix.py`
- Create: `tools/render_cc_switch_deepseek_remix.py`

- [ ] **Step 1: Write failing tests**

Run: `python3 -m pytest tests/test_cc_switch_deepseek_remix.py -q`

Expected: failure because `video_factory.cc_switch_deepseek_remix` does not exist.

- [ ] **Step 2: Implement renderer**

Implement a focused renderer that:
- uses only `/Users/king/Downloads/下载 (1).mp4`,
- builds a 90 second storyboard from real motion clips,
- keeps topic terms `CC Switch`, `Codex`, `DeepSeek`, `API Key`, and `模型配置`,
- draws transparent overlays that leave the center of the screen visible,
- exports to `video_factory/output/cc-switch-deepseek-remix/release.mp4`.

- [ ] **Step 3: Run targeted tests**

Run: `python3 -m pytest tests/test_cc_switch_deepseek_remix.py -q`

Expected: all tests pass.

- [ ] **Step 4: Render sample**

Run: `python3 tools/render_cc_switch_deepseek_remix.py`

Expected output files:
- `video_factory/output/cc-switch-deepseek-remix/release.mp4`
- `video_factory/output/cc-switch-deepseek-remix/cover.png`
- `video_factory/output/cc-switch-deepseek-remix/contact_sheet.jpg`
- `video_factory/output/cc-switch-deepseek-remix/script.md`
- `video_factory/output/cc-switch-deepseek-remix/render_report.json`

- [ ] **Step 5: Verify**

Run:
- `ffprobe -v error -select_streams v:0 -show_entries stream=width,height,r_frame_rate,duration,nb_frames -show_entries format=duration,size,bit_rate -of json video_factory/output/cc-switch-deepseek-remix/release.mp4`
- `python3 -m pytest`

Expected:
- 1920x1080
- 30fps
- 90 seconds
- 2700 video frames
- all tests pass.
