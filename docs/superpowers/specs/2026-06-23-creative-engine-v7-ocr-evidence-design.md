# Creative Engine V7 OCR Evidence Design

## Problem

V6 can create a 5-minute directed longform cut, but content evidence is still weak when OCR returns no text. The current extraction path saves creative samples at about 360px wide, which is enough for visual scoring but too small for software tutorial text, subtitles, settings pages, and API key labels.

## Design

V7 upgrades the content understanding layer without changing the source-only rendering rule:

- Extract creative sample frames at a larger width for content analysis.
- Before OCR, create several image candidates from each sample:
  - full frame
  - bottom subtitle band
  - central interface panel
  - upper interface/header band
- Upscale cropped candidates before OCR so small UI text has a better chance of being recognized.
- Merge OCR words from all candidates into one recognized text string per sample.
- Keep the existing `vision_lite` metrics as fallback when OCR is unavailable or empty.

## Quality Intent

The output should not look more "AI". The video remains a real source-video cut. The improvement is in the invisible decision layer: `content_analysis.json`, `creative_plan.json`, and `candidate_edl.md` should have stronger evidence for why each segment was selected.

## Acceptance

- Creative sample extraction uses high-resolution frames.
- OCR attempts multiple candidates per sampled frame.
- Recognized text is deduplicated before entering `content_analysis.json`.
- Existing visual fallback behavior still works when OCR is missing.
- Tests cover extraction command shape and OCR candidate generation.
