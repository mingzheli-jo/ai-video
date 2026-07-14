# Creative Engine V1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local creative-edit engine that scores source footage, creates non-overlapping story-driven cut plans, writes explainable creative artifacts, and blocks low-quality repetition.

**Architecture:** Add a focused `video_factory.creative` module for sample schedules, frame feature scoring, segment selection, variant planning, and artifact serialization. Keep `video_factory.replicate` responsible for ffmpeg extraction/rendering and quality gates, wiring creative plans into the existing workbench artifacts.

**Tech Stack:** Python 3.9+, ffmpeg/ffprobe, Pillow, pytest.

---

## File Structure

- Create `video_factory/creative.py`: pure creative planning logic, dataclasses, frame analysis, non-overlap checks, JSON/Markdown serialization helpers.
- Modify `video_factory/replicate.py`: extract source samples, call creative planner, render the recommended variant, write `creative_plan.json`, `candidate_edl.md`, and `cover_candidates.jpg`, extend quality checks.
- Modify `video_factory/workbench.py`: expose new creative artifacts in the browser UI.
- Modify `tests/test_replicate.py`: add unit tests for creative planning and quality gates.
- Modify `tests/test_workbench.py`: verify the new artifacts are advertised and served.
- Modify `README.md`: document Creative Engine V1 behavior and outputs.

## Task 1: Creative Planner Pure Functions

**Files:**
- Create: `video_factory/creative.py`
- Test: `tests/test_replicate.py`

- [ ] **Step 1: Write failing planner tests**

Append these tests to `tests/test_replicate.py`:

```python
from video_factory.creative import (
    CreativeFrameSample,
    build_creative_plan,
    build_sample_schedule,
    ranges_overlap,
)


def test_creative_sample_schedule_stays_inside_source_duration():
    assert build_sample_schedule(22.314, sample_count=6) == [0.7, 4.383, 8.066, 11.749, 15.432, 19.115]


def test_creative_plan_opens_with_strong_late_visual_without_overlap():
    samples = [
        CreativeFrameSample(0, 0.7, brightness=0.30, contrast=0.25, sharpness=0.30, colorfulness=0.35, motion=0.10),
        CreativeFrameSample(1, 5.0, brightness=0.45, contrast=0.38, sharpness=0.45, colorfulness=0.55, motion=0.60),
        CreativeFrameSample(2, 10.0, brightness=0.42, contrast=0.36, sharpness=0.44, colorfulness=0.52, motion=0.58),
        CreativeFrameSample(3, 16.0, brightness=0.58, contrast=0.50, sharpness=0.70, colorfulness=0.86, motion=0.40),
        CreativeFrameSample(4, 20.0, brightness=0.60, contrast=0.56, sharpness=0.72, colorfulness=0.92, motion=0.32),
    ]

    plan = build_creative_plan(22.314, samples, title="烤羊排下酒太香了")
    recommended = plan.recommended_variant

    assert recommended.name == "cover_first_story"
    assert recommended.segments[0].purpose.startswith("先用高吸引力成品")
    assert recommended.total_duration <= 22.314
    for previous, current in zip(recommended.segments, recommended.segments[1:]):
        assert not ranges_overlap(
            previous.start,
            previous.start + previous.duration,
            current.start,
            current.start + current.duration,
        )
    assert any(candidate.role == "cover_candidate" for candidate in plan.cover_candidates)
```

- [ ] **Step 2: Run planner tests and verify they fail**

Run:

```bash
python3 -m pytest tests/test_replicate.py::test_creative_sample_schedule_stays_inside_source_duration tests/test_replicate.py::test_creative_plan_opens_with_strong_late_visual_without_overlap -v
```

Expected: FAIL because `video_factory.creative` does not exist yet.

- [ ] **Step 3: Implement minimal planner**

Create `video_factory/creative.py` with dataclasses `CreativeFrameSample`, `CreativeMoment`, `CreativeSegment`, `CreativeVariant`, `CreativePlan`; functions `build_sample_schedule`, `score_sample`, `build_creative_plan`, `ranges_overlap`, `plan_to_dict`, `write_creative_plan_json`, and `write_candidate_edl`.

- [ ] **Step 4: Run planner tests and verify they pass**

Run the same pytest command. Expected: PASS.

## Task 2: Creative Quality Gate

**Files:**
- Modify: `video_factory/replicate.py`
- Test: `tests/test_replicate.py`

- [ ] **Step 1: Write failing quality tests**

Append tests that create `creative_plan.json` and verify `run_quality_checks`:

```python
def test_quality_check_requires_creative_plan_for_creative_edit(tmp_path):
    paths = build_replicate_paths(tmp_path / "source.mp4", "creative-edit", output_root=tmp_path)
    paths.output_dir.mkdir(parents=True)
    paths.concat.write_text("file '/tmp/body.mp4'\n", encoding="utf-8")
    source_probe = {"streams": [{"codec_type": "video", "width": 1080, "height": 1920}], "format": {"duration": "30"}}
    output_probe = {"streams": [{"codec_type": "video", "width": 1080, "height": 1920}], "format": {"duration": "20"}}

    result = run_quality_checks(source_probe, output_probe, "creative-edit", paths)

    assert result["status"] == "failed"
    assert any(issue["code"] == "missing_creative_plan" for issue in result["issues"])


def test_quality_check_rejects_overlapping_creative_plan_ranges(tmp_path):
    paths = build_replicate_paths(tmp_path / "source.mp4", "creative-edit", output_root=tmp_path)
    paths.output_dir.mkdir(parents=True)
    paths.concat.write_text("file '/tmp/body.mp4'\n", encoding="utf-8")
    paths.creative_plan.write_text(
        json.dumps(
            {
                "recommended_variant": {
                    "segments": [
                        {"start": 0.0, "duration": 5.0, "purpose": "first"},
                        {"start": 4.5, "duration": 4.0, "purpose": "overlap"},
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    source_probe = {"streams": [{"codec_type": "video", "width": 1080, "height": 1920}], "format": {"duration": "30"}}
    output_probe = {"streams": [{"codec_type": "video", "width": 1080, "height": 1920}], "format": {"duration": "9"}}

    result = run_quality_checks(source_probe, output_probe, "creative-edit", paths)

    assert result["status"] == "failed"
    assert any(issue["code"] == "creative_plan_overlap" for issue in result["issues"])
```

- [ ] **Step 2: Run quality tests and verify they fail**

Run:

```bash
python3 -m pytest tests/test_replicate.py::test_quality_check_requires_creative_plan_for_creative_edit tests/test_replicate.py::test_quality_check_rejects_overlapping_creative_plan_ranges -v
```

Expected: FAIL because paths and checks do not include `creative_plan`.

- [ ] **Step 3: Extend paths and quality checks**

Add `creative_plan`, `candidate_edl`, and `cover_candidates` fields to `ReplicatePaths`, update path builders, import `ranges_overlap`, and make `run_quality_checks` require a valid non-overlapping creative plan for `creative-edit`.

- [ ] **Step 4: Run quality tests and verify they pass**

Run the same pytest command. Expected: PASS.

## Task 3: Render Integration

**Files:**
- Modify: `video_factory/replicate.py`
- Test: `tests/test_replicate.py`

- [ ] **Step 1: Write failing render-adapter tests**

Add tests for adapter behavior without calling ffmpeg:

```python
from video_factory.creative import CreativeFrameSample
from video_factory.replicate import creative_segments_from_plan


def test_creative_segments_from_plan_preserves_plan_order_and_reasons():
    plan = build_creative_plan(
        22.314,
        [
            CreativeFrameSample(0, 0.7, 0.3, 0.2, 0.3, 0.2, 0.1),
            CreativeFrameSample(1, 8.0, 0.5, 0.6, 0.6, 0.7, 0.6),
            CreativeFrameSample(2, 18.0, 0.7, 0.8, 0.8, 0.9, 0.4),
        ],
        title="烤羊排",
    )

    segments = creative_segments_from_plan(plan)

    assert [segment.key for segment in segments][0].startswith("creative_")
    assert segments[0].purpose == plan.recommended_variant.segments[0].purpose
    assert sum(segment.duration for segment in segments) == plan.recommended_variant.total_duration
```

- [ ] **Step 2: Run adapter test and verify it fails**

Run:

```bash
python3 -m pytest tests/test_replicate.py::test_creative_segments_from_plan_preserves_plan_order_and_reasons -v
```

Expected: FAIL because `creative_segments_from_plan` does not exist.

- [ ] **Step 3: Implement render adapter and sample extraction**

Update `_render_creative_edit` so it extracts frame samples, analyzes them, builds the creative plan, writes artifacts, renders only the recommended non-overlapping source segments, and writes EDL from the plan.

- [ ] **Step 4: Run adapter test and verify it passes**

Run the same pytest command. Expected: PASS.

## Task 4: Workbench and Docs

**Files:**
- Modify: `video_factory/workbench.py`
- Modify: `README.md`
- Test: `tests/test_workbench.py`

- [ ] **Step 1: Write failing workbench test**

Update `test_index_html_contains_workbench_controls` to assert:

```python
assert "creative_plan" in INDEX_HTML
assert "candidate_edl" in INDEX_HTML
assert "cover_candidates" in INDEX_HTML
```

- [ ] **Step 2: Run workbench test and verify it fails**

Run:

```bash
python3 -m pytest tests/test_workbench.py::test_index_html_contains_workbench_controls -v
```

Expected: FAIL because those artifacts are not yet exposed.

- [ ] **Step 3: Expose artifacts and document workflow**

Add creative artifact names to the workbench allowlist and update UI/help copy plus README.

- [ ] **Step 4: Run workbench test and verify it passes**

Run the same pytest command. Expected: PASS.

## Task 5: Full Verification and Sample

**Files:**
- No new code files unless verification exposes defects.

- [ ] **Step 1: Run full test suite**

Run:

```bash
python3 -m pytest
```

Expected: PASS.

- [ ] **Step 2: Render a creative sample from the user-provided food video**

Run:

```bash
python3 -m video_factory.replicate --input "/Users/king/Downloads/下载 (1).mp4" --mode creative-edit --output /private/tmp/video_factory_creative_engine_v1
```

Expected artifacts:

- `/private/tmp/video_factory_creative_engine_v1/release.mp4`
- `/private/tmp/video_factory_creative_engine_v1/creative_plan.json`
- `/private/tmp/video_factory_creative_engine_v1/candidate_edl.md`
- `/private/tmp/video_factory_creative_engine_v1/cover_candidates.jpg`
- `/private/tmp/video_factory_creative_engine_v1/quality_report.json`

- [ ] **Step 3: Verify rendered media facts**

Run ffprobe on release:

```bash
ffprobe -v error -show_entries format=duration -show_entries stream=codec_type,width,height,r_frame_rate -of json /private/tmp/video_factory_creative_engine_v1/release.mp4
```

Expected: source geometry preserved, output duration not longer than source, audio/video streams present.

- [ ] **Step 4: Inspect creative plan**

Read `creative_plan.json` and confirm the recommended segments are non-overlapping, have reasons, and are not plain equal time slicing.

