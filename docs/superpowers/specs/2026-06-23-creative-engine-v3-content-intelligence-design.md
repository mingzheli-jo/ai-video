# Creative Engine V3 Content Intelligence Design

## Goal

Creative Engine V3 adds a content-intelligence layer to `creative-edit` so the system can explain and judge clips using visible content signals, not only visual motion and brightness. The output should make it clear what each selected segment appears to contain, how confident the system is, and whether the plan has enough content evidence to be trusted.

This version still renders source-only video. It must not insert generated intro cards, fake footage, decorative overlays, synthetic voiceover, or template progress UI into the release video.

## Problem

Creative Engine V2 can classify broad video profiles and assign semantic roles. That fixed the worst random-sampling behavior, but it still has a blind spot:

- It can infer that a screen recording is a tutorial, but not what screen text is present.
- It can label a segment as `configuration_detail`, but cannot yet show concrete content evidence.
- Optional OCR/ASR may be available on one machine and unavailable on another, so the pipeline needs a stable fallback.
- Quality reports should distinguish "content understood" from "content guessed from visual layout".

## Scope

Included:

- Add a content-analysis module that produces `content_analysis.json`.
- Add per-sample content cues: text density, subtitle-likelihood, interface-likelihood, visual content tags, and optional OCR text.
- Add optional OCR provider support that is safe to fail and records provider status.
- Feed content cues into creative planning and candidate EDL output.
- Add quality checks for content-analysis presence, provider status, and content-evidence coverage.
- Expose `content_analysis` through the workbench artifact list.
- Document the V3 behavior and its local limits.

Excluded:

- Mandatory external AI APIs.
- Mandatory cloud OCR or cloud ASR.
- Generated B-roll or fake replacement visuals.
- Automatic publishing, captions, cover copy generation, or platform upload.
- Hard failure when optional OCR is unavailable, unless future settings explicitly require OCR.

## Content Analysis

V3 adds a new module, `video_factory.content`, with these concepts:

- `ContentCue`: a timestamped cue attached to one sampled frame.
- `ContentAnalysis`: a collection of cues plus provider metadata.
- `ContentProviderStatus`: information about which provider ran and whether it returned text.

Each `ContentCue` should include:

- `sample_index`
- `timestamp`
- `text_density`
- `subtitle_likelihood`
- `interface_likelihood`
- `recognized_text`
- `content_tags`
- `evidence`

The local fallback should use image analysis rather than pretending to OCR text. It may detect dense UI text areas, likely subtitle bands, and screen-layout structure. If optional OCR returns text, the cue should include the recognized text and keyword-derived tags.

## Optional OCR Provider

The OCR provider must be best-effort:

- If an OCR package exists and returns text, record provider `status: available`.
- If the package is missing, crashes, times out, or returns no text, record `status: unavailable` or `empty`.
- The creative render must continue unless the user later configures strict OCR mode.
- The quality report must show whether `content_analysis.json` exists and whether selected segments have content evidence.

V3 can support `ocrmac`/macOS Vision as the first optional provider, but the core code must not require it.

## Planning Integration

`build_creative_plan` should accept optional content cues. The planner should:

- Preserve all V2 profile and semantic-role behavior.
- Attach content cue summaries to each selected segment.
- Prefer content-rich candidates when choosing between otherwise similar tutorial segments.
- Use content tags to strengthen role evidence, for example:
  - `install`, `download`, `provider`, `api_key`, `model`, `validation`
  - `subtitle`, `interface`, `dialog`, `settings`
- Avoid claiming exact OCR text when only fallback visual cues are available.

`creative_plan.json` should include:

- top-level `content_provider`
- top-level `content_coverage`
- top-level `content_cues`
- per-segment `content_tags`
- per-segment `content_evidence`

`candidate_edl.md` should include content tags or a short evidence summary for each row.

## Quality Gate

For `creative-edit`, `quality_report.json` should fail if:

- `content_analysis.json` is missing.
- `creative_plan.json` has no content provider metadata.
- fewer than half of selected segments have either content tags or content evidence.

The gate should not fail only because optional OCR is unavailable. Instead, it should expose provider state so the user can see whether the plan came from true OCR or local fallback cues.

Add checks:

- `content_analysis_exists`
- `creative_plan_has_content_provider`
- `creative_plan_has_content_evidence`

## Workbench

The workbench should expose `content_analysis` as an artifact when present. The first UI pass does not need a new visual panel; artifact visibility is enough because the current workflow already opens JSON/Markdown outputs for inspection.

## Testing

Tests should cover:

- Local content cue extraction from image samples.
- Keyword tagging from recognized text.
- Content analysis serialization.
- Creative plan serialization of content provider and per-segment content evidence.
- Candidate EDL content columns.
- Quality checks reject missing content analysis.
- Quality checks reject creative plans with weak content evidence coverage.
- Quality checks pass when OCR is unavailable but fallback content cues exist.
- Workbench artifact list includes `content_analysis`.

## Success Criteria

V3 is successful when a user can open the output directory and see:

- `release.mp4`
- `creative_plan.json`
- `candidate_edl.md`
- `quality_report.json`
- `content_analysis.json`

Together, these files should explain not only where the clips came from, but also what content evidence the system used to choose them and how much of that evidence came from optional OCR versus deterministic local fallback analysis.
