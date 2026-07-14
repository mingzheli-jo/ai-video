# Cartoon Host Overlay Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the failed full-frame cartoon filter with usable presenter reconstruction options that preserve source clarity while covering or removing the original presenter area.

**Architecture:** Keep segment rendering source-faithful, then apply a release-level visual transform. `cartoonize` draws a soft mask over the presenter region and overlays a generated transparent PNG cartoon host. `remove_presenter` draws an opaque clean matte over the presenter and original subtitle region when a fake replacement looks worse than removal.

**Tech Stack:** Python, PIL, FFmpeg `filter_complex`, pytest.

---

### Task 1: Lock the Desired Transform with Tests

**Files:**
- Modify: `tests/test_replicate.py`

- [ ] Add a test proving the release transform command uses `filter_complex`, `drawbox`, and `overlay`.
- [ ] Add assertions proving the command does not use `edgedetect` or `negate`.
- [ ] Add a test proving the generated host asset is a transparent PNG with non-empty avatar pixels.
- [ ] Run the focused tests and confirm they fail before implementation.

### Task 2: Implement the Cartoon Host Overlay

**Files:**
- Modify: `video_factory/replicate.py`

- [ ] Add `_write_cartoon_host_asset(path)` using PIL to draw a transparent cartoon presenter.
- [ ] Change `_build_release_visual_transform_command(...)` to accept geometry and avatar path.
- [ ] Replace full-frame line-art filters with a mild clarity-preserving base filter, presenter-region `drawbox`, and PNG `overlay`.
- [ ] Make `_apply_release_visual_transform(...)` generate the avatar asset and pass geometry to the command.
- [ ] Run the focused tests and confirm they pass.

### Task 3: Wire the UX and Render a New Sample

**Files:**
- Modify: `video_factory/workbench.py`
- Generate: `video_factory/output/workbench/<new-job>/release.mp4`

- [ ] Rename the UI label from generic "卡通化" to "卡通主持人".
- [ ] Add a separate "移除讲解人" visual strategy and use it for high-risk repair defaults.
- [ ] Ensure original-enhanced mode also applies the release-level overlay when `visual_transform_policy=cartoonize`.
- [ ] Render a new validation sample from the current uploaded source with audio replacement and either cartoon host overlay or presenter removal.
- [ ] Inspect the contact sheet visually before calling it usable.
- [ ] Run the full test suite.
