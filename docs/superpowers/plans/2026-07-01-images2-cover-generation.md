# Images2 Cover Generation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add analysis-driven images2 cover generation to reference-guided original videos.

**Architecture:** Add cover brief, cover prompt pack, and cover asset manifest artifacts after storyboard and visual sourcing. Make the final `cover.png` come from the recommended generated cover instead of a random video frame, and block publish candidates when the cover is preview-only or visually overcomplicated.

**Tech Stack:** Python, pytest, PIL mock image rendering, existing workbench HTML/JS string.

---

### Task 1: Cover Planning Tests

- [ ] Add tests for `build_cover_brief()` and `build_cover_prompt_pack()`.
- [ ] Verify cover copy is concise and prompts forbid complex collage / fake news footage.

### Task 2: Cover Asset Manifest

- [ ] Add tests for `build_cover_asset_manifest()` with `mock_images2` and `mock_image`.
- [ ] Implement candidate cover rendering and recommended `cover.png` selection.

### Task 3: Quality Gate

- [ ] Add quality-report tests for publish-ready and preview-only covers.
- [ ] Integrate cover checks into reference-guided quality report and user delivery.

### Task 4: Workbench Artifacts

- [ ] Add workbench tests for `cover_brief`, `cover_prompt_pack`, and `cover_asset_manifest`.
- [ ] Include cover metrics and checks in the summary board.

### Task 5: Verification

- [ ] Run targeted tests.
- [ ] Run full suite.
- [ ] Restart the workbench and verify HTTP 200.
