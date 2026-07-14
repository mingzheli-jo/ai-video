# Creative Engine V2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade `creative-edit` so plans include video profile detection, type-specific semantic roles, role-aware story ordering, and stronger quality gates.

**Architecture:** Extend the existing `video_factory.creative` module rather than adding a new subsystem. The planner remains deterministic and local: profile detection uses title hints plus frame statistics, semantic roles are assigned from profile-specific rules, and `video_factory.replicate` keeps rendering and quality enforcement responsibilities.

**Tech Stack:** Python 3.9+, pytest, ffmpeg/ffprobe, Pillow.

---

## File Structure

- Modify `video_factory/creative.py`: add `CreativeProfile`, semantic-role assignment, profile-aware ordering, and expanded plan serialization.
- Modify `video_factory/replicate.py`: pass the cleaned source title into profile detection, write semantic fields through EDL, and enforce V2 quality checks.
- Modify `tests/test_replicate.py`: add red/green tests for profiles, semantic roles, and quality gates.
- Modify `README.md`: document Creative Engine V2 outputs and local deterministic limits.

This workspace is not a git repository, so commit steps are replaced by verification commands.

### Task 1: Profile Detection

**Files:**
- Modify: `tests/test_replicate.py`
- Modify: `video_factory/creative.py`

- [ ] **Step 1: Write failing profile tests**

Append imports and tests to `tests/test_replicate.py`:

```python
from video_factory.creative import classify_creative_profile


def test_creative_profile_uses_food_title_hints():
    samples = [
        CreativeFrameSample(0, 0.7, 0.55, 0.42, 0.62, 0.72, 0.20),
        CreativeFrameSample(1, 4.0, 0.58, 0.48, 0.70, 0.82, 0.42),
    ]

    profile = classify_creative_profile(samples, title="烤羊排下酒太香了")

    assert profile.name == "food_social"
    assert profile.confidence >= 0.6
    assert any("title_hint" in item for item in profile.evidence)


def test_creative_profile_uses_tutorial_title_hints():
    samples = [
        CreativeFrameSample(0, 0.7, 0.82, 0.18, 0.42, 0.14, 0.08),
        CreativeFrameSample(1, 8.0, 0.78, 0.20, 0.38, 0.18, 0.12),
    ]

    profile = classify_creative_profile(samples, title="Codex DeepSeek API 配置教程")

    assert profile.name == "tutorial_screen"
    assert profile.confidence >= 0.6
    assert any("title_hint" in item for item in profile.evidence)
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
python3 -m pytest tests/test_replicate.py::test_creative_profile_uses_food_title_hints tests/test_replicate.py::test_creative_profile_uses_tutorial_title_hints -v
```

Expected: FAIL because `classify_creative_profile` does not exist.

- [ ] **Step 3: Implement profile dataclass and classifier**

Add to `video_factory/creative.py`:

```python
@dataclass(frozen=True)
class CreativeProfile:
    name: str
    confidence: float
    evidence: tuple[str, ...]
```

Add `classify_creative_profile(samples, title)` that checks food/tutorial title hints first, then falls back to sample averages:

- food title hints: `羊排`, `下酒菜`, `吃`, `烤`, `饭`, `菜`, `美食`
- tutorial title hints: `codex`, `deepseek`, `workflow`, `教程`, `配置`, `api`, `安装`
- food visual signal: average colorfulness >= 0.55 and average sharpness >= 0.48
- tutorial visual signal: average colorfulness <= 0.24 and average motion <= 0.22
- fallback: `generic_live`

- [ ] **Step 4: Run profile tests and verify pass**

Run the same pytest command. Expected: PASS.

### Task 2: Semantic Roles in Plans

**Files:**
- Modify: `tests/test_replicate.py`
- Modify: `video_factory/creative.py`

- [ ] **Step 1: Write failing semantic-role tests**

Append these tests to `tests/test_replicate.py`:

```python
def test_food_creative_plan_uses_food_semantic_roles():
    samples = [
        CreativeFrameSample(0, 0.7, 0.50, 0.35, 0.55, 0.70, 0.18),
        CreativeFrameSample(1, 6.0, 0.56, 0.42, 0.60, 0.66, 0.68),
        CreativeFrameSample(2, 12.0, 0.60, 0.50, 0.72, 0.88, 0.42),
        CreativeFrameSample(3, 18.0, 0.62, 0.46, 0.78, 0.92, 0.20),
    ]

    plan = build_creative_plan(24.0, samples, title="烤羊排下酒太香了")
    roles = {segment.semantic_role for segment in plan.recommended_variant.segments}

    assert plan.profile.name == "food_social"
    assert "food_hook" in roles
    assert roles & {"prep_action", "cook_transform", "texture_closeup", "final_payoff"}
    assert all(segment.role_evidence for segment in plan.recommended_variant.segments)


def test_tutorial_creative_plan_uses_tutorial_semantic_roles():
    samples = [
        CreativeFrameSample(0, 0.7, 0.82, 0.18, 0.42, 0.16, 0.08),
        CreativeFrameSample(1, 30.0, 0.80, 0.22, 0.44, 0.18, 0.34),
        CreativeFrameSample(2, 90.0, 0.78, 0.24, 0.50, 0.20, 0.18),
        CreativeFrameSample(3, 150.0, 0.76, 0.26, 0.48, 0.19, 0.12),
    ]

    plan = build_creative_plan(180.0, samples, title="Codex DeepSeek API 配置教程")
    roles = {segment.semantic_role for segment in plan.recommended_variant.segments}

    assert plan.profile.name == "tutorial_screen"
    assert "tutorial_hook" in roles
    assert roles & {"interface_state", "operation_step", "configuration_detail", "result_validation"}
    assert all(segment.role_evidence for segment in plan.recommended_variant.segments)
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
python3 -m pytest tests/test_replicate.py::test_food_creative_plan_uses_food_semantic_roles tests/test_replicate.py::test_tutorial_creative_plan_uses_tutorial_semantic_roles -v
```

Expected: FAIL because `CreativeSegment` has no `semantic_role`, `role_evidence`, or `CreativePlan.profile`.

- [ ] **Step 3: Implement semantic fields and profile-aware roles**

Update dataclasses:

```python
@dataclass(frozen=True)
class CreativeSegment:
    ...
    semantic_role: str
    role_evidence: str

@dataclass(frozen=True)
class CreativePlan:
    ...
    profile: CreativeProfile
```

Update `build_creative_plan` to classify profile once and assign semantic roles through helper functions:

- `food_social`: first selected strong visual becomes `food_hook`; high motion becomes `prep_action`; late/high-color samples become `final_payoff`; high sharpness/color becomes `texture_closeup`; otherwise `cook_transform`.
- `tutorial_screen`: first selected sample becomes `tutorial_hook`; high motion becomes `operation_step`; late sample becomes `result_validation`; high sharpness or low motion stable UI becomes `interface_state`; mid/late stable sample becomes `configuration_detail`.
- `generic_live`: map to `visual_hook`, `action_moment`, `detail_moment`, `context_bridge`, `result_moment`.

- [ ] **Step 4: Run semantic-role tests and verify pass**

Run the same pytest command. Expected: PASS.

### Task 3: Plan Serialization and Candidate EDL

**Files:**
- Modify: `tests/test_replicate.py`
- Modify: `video_factory/creative.py`

- [ ] **Step 1: Write failing serialization test**

Append:

```python
from video_factory.creative import plan_to_dict, write_candidate_edl


def test_creative_plan_serializes_profile_and_semantic_roles(tmp_path):
    plan = build_creative_plan(
        24.0,
        [
            CreativeFrameSample(0, 0.7, 0.50, 0.35, 0.55, 0.70, 0.18),
            CreativeFrameSample(1, 6.0, 0.56, 0.42, 0.60, 0.66, 0.68),
            CreativeFrameSample(2, 12.0, 0.60, 0.50, 0.72, 0.88, 0.42),
            CreativeFrameSample(3, 18.0, 0.62, 0.46, 0.78, 0.92, 0.20),
        ],
        title="美食 烤羊排",
    )

    data = plan_to_dict(plan)
    edl = tmp_path / "candidate_edl.md"
    write_candidate_edl(plan, edl)

    assert data["profile"]["name"] == "food_social"
    assert data["profile_confidence"] == data["profile"]["confidence"]
    assert data["recommended_variant"]["segments"][0]["semantic_role"]
    assert "Semantic Role" in edl.read_text(encoding="utf-8")
```

- [ ] **Step 2: Run test and verify failure**

Run:

```bash
python3 -m pytest tests/test_replicate.py::test_creative_plan_serializes_profile_and_semantic_roles -v
```

Expected: FAIL until serialization includes profile and EDL semantic columns.

- [ ] **Step 3: Implement serialization and EDL changes**

Update `plan_to_dict` to add top-level aliases:

```python
data["profile_confidence"] = plan.profile.confidence
data["profile_evidence"] = list(plan.profile.evidence)
```

Update `write_candidate_edl` table columns to include `Semantic Role`.

- [ ] **Step 4: Run serialization test and verify pass**

Run the same pytest command. Expected: PASS.

### Task 4: Quality Gate V2

**Files:**
- Modify: `tests/test_replicate.py`
- Modify: `video_factory/replicate.py`

- [ ] **Step 1: Write failing quality-gate tests**

Append:

```python
def test_quality_check_rejects_creative_plan_without_semantic_roles(tmp_path):
    paths = build_replicate_paths(tmp_path / "source.mp4", "creative-edit", output_root=tmp_path)
    paths.output_dir.mkdir(parents=True)
    paths.concat.write_text("file '/tmp/body.mp4'\n", encoding="utf-8")
    paths.creative_plan.write_text(
        json.dumps(
            {
                "profile": {"name": "food_social", "confidence": 0.8, "evidence": ["title_hint:美食"]},
                "recommended_variant": {
                    "segments": [
                        {"start": 0.0, "duration": 4.0, "purpose": "保留真实动作。"},
                        {"start": 8.0, "duration": 4.0, "purpose": "保留成品细节。"},
                        {"start": 16.0, "duration": 4.0, "purpose": "收束。"},
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    source_probe = {"streams": [{"codec_type": "video", "width": 1080, "height": 1920}], "format": {"duration": "30"}}
    output_probe = {"streams": [{"codec_type": "video", "width": 1080, "height": 1920}], "format": {"duration": "20"}}

    result = run_quality_checks(source_probe, output_probe, "creative-edit", paths)

    assert result["status"] == "failed"
    assert any(issue["code"] == "creative_plan_missing_semantic_role" for issue in result["issues"])


def test_quality_check_rejects_insufficient_semantic_variety(tmp_path):
    paths = build_replicate_paths(tmp_path / "source.mp4", "creative-edit", output_root=tmp_path)
    paths.output_dir.mkdir(parents=True)
    paths.concat.write_text("file '/tmp/body.mp4'\n", encoding="utf-8")
    paths.creative_plan.write_text(
        json.dumps(
            {
                "profile": {"name": "generic_live", "confidence": 0.5, "evidence": ["fallback"]},
                "recommended_variant": {
                    "segments": [
                        {"start": 0.0, "duration": 4.0, "purpose": "a", "semantic_role": "context_bridge"},
                        {"start": 8.0, "duration": 4.0, "purpose": "b", "semantic_role": "context_bridge"},
                        {"start": 16.0, "duration": 4.0, "purpose": "c", "semantic_role": "context_bridge"},
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    source_probe = {"streams": [{"codec_type": "video", "width": 1080, "height": 1920}], "format": {"duration": "30"}}
    output_probe = {"streams": [{"codec_type": "video", "width": 1080, "height": 1920}], "format": {"duration": "20"}}

    result = run_quality_checks(source_probe, output_probe, "creative-edit", paths)

    assert result["status"] == "failed"
    assert any(issue["code"] == "creative_plan_insufficient_role_variety" for issue in result["issues"])
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
python3 -m pytest tests/test_replicate.py::test_quality_check_rejects_creative_plan_without_semantic_roles tests/test_replicate.py::test_quality_check_rejects_insufficient_semantic_variety -v
```

Expected: FAIL because V2 quality checks are not enforced.

- [ ] **Step 3: Implement quality checks**

Update `_creative_plan_quality_issues`:

- require top-level `profile.name`
- require every recommended segment to have non-empty `semantic_role`
- if source/recommended plan duration is longer than 20 seconds, require at least three distinct semantic roles
- fail if more than half of roles are in `{"context", "context_bridge"}`

Update `run_quality_checks()["checks"]` with:

- `creative_plan_has_profile`
- `creative_plan_has_semantic_roles`
- `creative_plan_has_role_variety`

- [ ] **Step 4: Run quality-gate tests and verify pass**

Run the same pytest command. Expected: PASS.

### Task 5: Compatibility and Sample Verification

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Run focused tests**

Run:

```bash
python3 -m pytest tests/test_replicate.py tests/test_workbench.py -v
```

Expected: PASS.

- [ ] **Step 2: Run full tests**

Run:

```bash
python3 -m pytest
```

Expected: PASS.

- [ ] **Step 3: Render a V2 creative sample**

Run:

```bash
python3 -m video_factory.replicate --input "/Users/king/Downloads/下载 (1).mp4" --mode creative-edit --output /private/tmp/video_factory_creative_engine_v2
```

Expected: render completes and writes `release.mp4`, `creative_plan.json`, `candidate_edl.md`, `cover_candidates.jpg`, and `quality_report.json`.

- [ ] **Step 4: Inspect V2 artifacts**

Run:

```bash
ffprobe -v error -show_entries format=duration,size,bit_rate -show_entries stream=codec_type,width,height,r_frame_rate,duration -of json /private/tmp/video_factory_creative_engine_v2/release.mp4
python3 -c "import json; p=json.load(open('/private/tmp/video_factory_creative_engine_v2/creative_plan.json')); print(p['profile']['name'], p['profile']['confidence']); print([s['semantic_role'] for s in p['recommended_variant']['segments']])"
sed -n '1,160p' /private/tmp/video_factory_creative_engine_v2/quality_report.json
```

Expected: source geometry preserved, quality status passed, semantic roles present, and at least three distinct roles for a video longer than 20 seconds.

- [ ] **Step 5: Update README**

Add a short note that Creative Engine V2 writes profile and semantic-role evidence into `creative_plan.json`, while still staying source-only and local.

