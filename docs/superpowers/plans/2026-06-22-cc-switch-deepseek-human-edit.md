# CC Switch DeepSeek Human Edit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce a 6-8 minute human-style edit of `/Users/king/Downloads/下载 (1).mp4` that keeps the tutorial story intact while removing obvious waiting/repetition and avoiding AI-template packaging.

**Architecture:** Add a dedicated edit-decision-list renderer. The renderer trims the source into real timeline segments, applies mild quality cleanup plus occasional natural digital punch-ins, concatenates the segments, and emits cover/contact-sheet/report/EDL artifacts. It must not draw titles, progress bars, decorative panels, synthetic avatars, or new narration.

**Tech Stack:** Python, FFmpeg, pytest, existing `video_factory` package conventions.

---

### Task 1: Test the Human-Edit Contract

**Files:**
- Create: `tests/test_cc_switch_deepseek_human_edit.py`

- [ ] **Step 1: Write the failing test**

```python
from video_factory.cc_switch_deepseek_human_edit import (
    DEFAULT_OUTPUT_DIR,
    SOURCE_VIDEO,
    build_human_edit_scene_command,
    build_human_edit_storyboard,
    build_human_edit_paths,
    human_edit_duration,
)


def test_human_edit_storyboard_is_six_to_eight_minute_real_edit():
    segments = build_human_edit_storyboard()

    assert 360 <= human_edit_duration(segments) <= 480
    assert len(segments) >= 8
    assert all(segment.source == "reference" for segment in segments)
    assert sum(1 for segment in segments if segment.zoom > 1.0) >= 4
    assert segments[0].start == 0


def test_human_edit_segment_command_has_no_ai_template_packaging(tmp_path):
    segment = build_human_edit_storyboard()[1]
    command = build_human_edit_scene_command(segment, tmp_path / "segment.mp4")
    joined = " ".join(command)

    assert command[:2] == ["ffmpeg", "-y"]
    assert str(SOURCE_VIDEO) in command
    assert str(tmp_path / "segment.mp4") == command[-1]
    assert "-loop" not in command
    assert "overlay" not in joined
    assert "drawtext" not in joined
    assert "progress" not in joined.lower()
    assert "crop=1920:1080" in joined
    assert "unsharp" in joined


def test_human_edit_paths_are_separate_from_original_enhanced_output():
    paths = build_human_edit_paths(DEFAULT_OUTPUT_DIR)

    assert paths.video.name == "release.mp4"
    assert paths.edl.name == "edit_decision_list.md"
    assert paths.contact_sheet.name == "contact_sheet.jpg"
    assert "human-edit" in str(paths.output_dir)
```

- [ ] **Step 2: Run the test**

Run: `python3 -m pytest tests/test_cc_switch_deepseek_human_edit.py -q`

Expected: FAIL with `ModuleNotFoundError`.

### Task 2: Implement the Renderer

**Files:**
- Create: `video_factory/cc_switch_deepseek_human_edit.py`
- Create: `tools/render_cc_switch_deepseek_human_edit.py`

- [ ] **Step 1: Build a 6-8 minute edit-decision list**

Use source-only segments that preserve the flow:
- opening premise
- Codex context
- CC Switch overview
- release/update page
- provider setup
- DeepSeek/API key
- local configuration
- model selection
- validation/close

- [ ] **Step 2: Render each segment**

Use one source video input per segment. Apply mild screen-recording enhancement and segment-specific digital punch-ins with `scale` + `crop`. Do not use overlays, generated captions, or image layers.

- [ ] **Step 3: Concatenate and emit artifacts**

Create:
- `release.mp4`
- `cover.png`
- `contact_sheet.jpg`
- `edit_decision_list.md`
- `render_report.json`

### Task 3: Render and Verify

- [ ] **Step 1: Render**

Run: `python3 tools/render_cc_switch_deepseek_human_edit.py`

- [ ] **Step 2: Verify output media**

Run: `ffprobe -v error -select_streams v:0 -show_entries stream=width,height,r_frame_rate,duration,nb_frames -show_entries format=duration,size,bit_rate -of json video_factory/output/cc-switch-deepseek-human-edit/release.mp4`

Expected: 1920x1080, 30fps, 360-480 seconds.

- [ ] **Step 3: Run tests**

Run: `python3 -m pytest`

Expected: all tests pass.
