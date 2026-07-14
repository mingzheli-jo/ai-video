# Creative Engine V3 Content Intelligence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a content-analysis layer to creative video replication so plans and quality reports include content evidence, optional OCR status, and selected-segment content tags.

**Architecture:** Create a focused `video_factory.content` module for content cues and provider status. Keep rendering in `video_factory.replicate`, planning in `video_factory.creative`, and expose the new artifact through the existing workbench artifact mechanism. OCR remains optional and safe to fail; local deterministic cues are always available.

**Tech Stack:** Python 3.9+, Pillow, optional `ocrmac`, ffmpeg/ffprobe, pytest.

---

## File Structure

- Create `video_factory/content.py`: content cue dataclasses, local image cue extraction, optional OCR adapter, serialization.
- Modify `video_factory/creative.py`: accept optional content cues, attach content evidence to segments, serialize content fields, show content in candidate EDL.
- Modify `video_factory/replicate.py`: add `content_analysis` path, build content analysis from sampled frames, pass cues into planner, enforce V3 quality checks.
- Modify `video_factory/workbench.py`: expose `content_analysis` artifact when present.
- Modify `tests/test_replicate.py`: add V3 planner and quality gate tests.
- Modify `tests/test_workbench.py`: assert artifact wiring includes content analysis.
- Modify `README.md`: document Creative Engine V3 content analysis.

This workspace is not a git repository, so commit steps are replaced by verification commands.

### Task 1: Content Module

**Files:**
- Create: `video_factory/content.py`
- Modify: `tests/test_replicate.py`

- [ ] **Step 1: Add failing tests for content cue extraction**

Add tests that create a simple image with subtitle-like and UI-like regions, then call `analyze_content_samples`.

Expected assertions:
- one cue per sample
- provider name is present
- text density is in `[0, 1]`
- interface or subtitle likelihood is positive
- serialization includes `provider`

- [ ] **Step 2: Run focused tests and confirm failure**

Run:

```bash
python3 -m pytest tests/test_replicate.py -k "content_cue or content_analysis" -v
```

Expected: FAIL because `video_factory.content` does not exist.

- [ ] **Step 3: Implement `video_factory.content`**

Define:
- `ContentProviderStatus`
- `ContentCue`
- `ContentAnalysis`
- `analyze_content_samples(sample_paths, title)`
- `analysis_to_dict(analysis)`
- `write_content_analysis_json(analysis, output_path)`

The local fallback should calculate text density, subtitle likelihood, interface likelihood, and title/OCR keyword tags. Optional OCR should be wrapped in `try/except BaseException` and never crash rendering.

- [ ] **Step 4: Run focused tests and confirm pass**

Run the same focused command. Expected: PASS.

### Task 2: Planner Integration

**Files:**
- Modify: `video_factory/creative.py`
- Modify: `tests/test_replicate.py`

- [ ] **Step 1: Add failing planner serialization tests**

Add tests that pass `ContentCue` objects into `build_creative_plan` and assert:
- `plan_to_dict(plan)["content_provider"]` exists
- each recommended segment has `content_tags`
- each recommended segment has `content_evidence`
- `candidate_edl.md` includes `Content`

- [ ] **Step 2: Run planner tests and confirm failure**

Run:

```bash
python3 -m pytest tests/test_replicate.py -k "creative_plan_serializes_content or candidate_edl_content" -v
```

Expected: FAIL until planner accepts content cues.

- [ ] **Step 3: Extend creative dataclasses and serialization**

Add optional fields:
- `CreativeSegment.content_tags`
- `CreativeSegment.content_evidence`
- `CreativePlan.content_provider`
- `CreativePlan.content_coverage`
- `CreativePlan.content_cues`

Update `build_creative_plan(..., content_analysis=None)` and segment construction to attach cue evidence by `source_sample_index`.

- [ ] **Step 4: Run planner tests and confirm pass**

Run the same focused command. Expected: PASS.

### Task 3: Replicate Pipeline and Quality Gate

**Files:**
- Modify: `video_factory/replicate.py`
- Modify: `tests/test_replicate.py`

- [ ] **Step 1: Add failing quality tests**

Add tests for:
- missing `content_analysis.json` fails creative quality
- missing content provider in `creative_plan.json` fails
- too few selected segments with content evidence fails
- fallback provider with content evidence passes

- [ ] **Step 2: Run focused tests and confirm failure**

Run:

```bash
python3 -m pytest tests/test_replicate.py -k "content_analysis_exists or content_provider or content_evidence" -v
```

Expected: FAIL until quality gate is extended.

- [ ] **Step 3: Wire content analysis into rendering**

Update:
- `ReplicatePaths.content_analysis`
- `_paths_for_explicit_output_dir`
- `_render_creative_edit` to call `analyze_content_samples`, write JSON, and pass analysis to `build_creative_plan`
- returned artifacts include `content_analysis`
- `run_quality_checks` includes V3 checks

- [ ] **Step 4: Run focused tests and confirm pass**

Run the same focused command. Expected: PASS.

### Task 4: Workbench and Documentation

**Files:**
- Modify: `video_factory/workbench.py`
- Modify: `tests/test_workbench.py`
- Modify: `README.md`

- [ ] **Step 1: Add failing workbench artifact test**

Assert that `content_analysis` appears in the artifact collection when the file exists.

- [ ] **Step 2: Run workbench tests and confirm failure**

Run:

```bash
python3 -m pytest tests/test_workbench.py -v
```

Expected: FAIL until the artifact is exposed.

- [ ] **Step 3: Expose artifact and document V3**

Add `content_analysis` to workbench artifact output and update README to mention Creative Engine V3.

- [ ] **Step 4: Run workbench tests and confirm pass**

Run the same command. Expected: PASS.

### Task 5: Full Verification and Sample Render

**Files:**
- No new files beyond prior tasks.

- [ ] **Step 1: Run all tests**

Run:

```bash
python3 -m pytest
```

Expected: all tests pass.

- [ ] **Step 2: Render a V3 sample**

Run:

```bash
python3 -m video_factory.replicate --input "/Users/king/Downloads/下载 (1).mp4" --mode creative-edit --output /private/tmp/video_factory_creative_engine_v3
```

Expected: output includes `release.mp4`, `creative_plan.json`, `candidate_edl.md`, `quality_report.json`, and `content_analysis.json`.

- [ ] **Step 3: Inspect final artifacts**

Run:

```bash
python3 -c "import json; p=json.load(open('/private/tmp/video_factory_creative_engine_v3/creative_plan.json')); print(p['content_provider']); print([s['content_tags'] for s in p['recommended_variant']['segments']]); print(p['content_coverage'])"
sed -n '1,220p' /private/tmp/video_factory_creative_engine_v3/quality_report.json
sed -n '1,160p' /private/tmp/video_factory_creative_engine_v3/candidate_edl.md
```

Expected: quality passes, content provider is recorded, and candidate EDL includes content evidence.
