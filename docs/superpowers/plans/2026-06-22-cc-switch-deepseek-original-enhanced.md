# CC Switch DeepSeek Original Enhanced Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a full-length, non-template enhancement of `/Users/king/Downloads/下载 (1).mp4` that preserves the original tutorial instead of converting it into an AI-looking short remix.

**Architecture:** Add a focused renderer that performs one clean full-video pass: mild screen-recording sharpening, contrast correction, loudness normalization, cover extraction, contact sheet generation, and a JSON report. It must not generate visible overlays, progress bars, decorative frames, scene cards, or synthetic narration.

**Tech Stack:** Python, FFmpeg, pytest, existing `video_factory` package conventions.

---

### Task 1: Lock the No-Overlay Contract

**Files:**
- Create: `tests/test_cc_switch_deepseek_original_enhanced.py`

- [ ] **Step 1: Write the failing test**

```python
from video_factory.cc_switch_deepseek_original_enhanced import (
    DEFAULT_OUTPUT_DIR,
    SOURCE_VIDEO,
    build_original_enhance_command,
    build_original_enhance_paths,
)


def test_original_enhance_uses_full_source_without_visible_ai_packaging(tmp_path):
    output = tmp_path / "release.mp4"
    command = build_original_enhance_command(output)
    joined = " ".join(command)

    assert command[:2] == ["ffmpeg", "-y"]
    assert str(SOURCE_VIDEO) in command
    assert str(output) == command[-1]
    assert "-loop" not in command
    assert "overlay" not in joined
    assert "drawtext" not in joined
    assert "progress" not in joined.lower()
    assert "loudnorm" in joined
    assert "unsharp" in joined
    assert "-t" not in command


def test_original_enhance_paths_are_isolated_from_remix_outputs():
    paths = build_original_enhance_paths(DEFAULT_OUTPUT_DIR)

    assert paths.video.name == "release.mp4"
    assert paths.cover.name == "cover.png"
    assert paths.contact_sheet.name == "contact_sheet.jpg"
    assert paths.report.name == "render_report.json"
    assert "original-enhanced" in str(paths.output_dir)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_cc_switch_deepseek_original_enhanced.py -q`

Expected: FAIL with `ModuleNotFoundError` because the renderer module does not exist yet.

### Task 2: Implement Clean Full-Length Renderer

**Files:**
- Create: `video_factory/cc_switch_deepseek_original_enhanced.py`
- Create: `tools/render_cc_switch_deepseek_original_enhanced.py`

- [ ] **Step 1: Implement renderer**

Build the FFmpeg command with:
- one source input only
- no image overlays
- no `drawtext`
- no duration clamp
- video filters: `scale`, `setsar`, `fps`, mild `eq`, mild `unsharp`, `format=yuv420p`
- audio filters: `highpass`, `lowpass`, `loudnorm`
- output: H.264 CRF 18, AAC 160k, faststart

- [ ] **Step 2: Implement artifacts**

Create:
- `release.mp4`
- `cover.png`
- `contact_sheet.jpg`
- `render_report.json`

- [ ] **Step 3: Run tests**

Run: `python3 -m pytest tests/test_cc_switch_deepseek_original_enhanced.py -q`

Expected: PASS.

### Task 3: Render and Verify

**Files:**
- Output directory: `video_factory/output/cc-switch-deepseek-original-enhanced`

- [ ] **Step 1: Render**

Run: `python3 tools/render_cc_switch_deepseek_original_enhanced.py`

- [ ] **Step 2: Verify output media**

Run: `ffprobe -v error -select_streams v:0 -show_entries stream=width,height,r_frame_rate,duration,nb_frames -show_entries format=duration,size,bit_rate -of json video_factory/output/cc-switch-deepseek-original-enhanced/release.mp4`

Expected: 1920x1080, 30fps, about 563 seconds.

- [ ] **Step 3: Run full suite**

Run: `python3 -m pytest`

Expected: all tests pass.
