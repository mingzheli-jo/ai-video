# Creative Engine V9 Semantic Timeline Design

## Problem

V8 can produce a source-only longform cut with OCR evidence and a stable release chronology, but the creative layer still reasons mostly at the level of samples and roles. It knows that a segment is an `operation_step` or `configuration_detail`, but it does not expose a human-readable chapter trace such as "install", "provider setup", "API key", "local routing", or "validation".

Without that layer, the system can pass technical checks while still feeling less authored than a human edit.

## Design

V9 adds a local semantic timeline layer:

- Build a `semantic_timeline.json` artifact from `content_analysis.json`, `audio_analysis.json`, the title, and source duration.
- Derive cue topics from OCR text and content tags, using deterministic local rules first.
- Merge adjacent cues with the same topic into readable chapters.
- Give each chapter a title, source time range, sample indices, evidence, and audio emphasis count.
- Attach the matching chapter title/topic back onto each `CreativeSegment`.
- Surface chapter information in `creative_plan.json`, `candidate_edl.md`, `creative_brief.md`, and the workbench artifact list.

This is not presented as true speech recognition. The first provider is `ocr_audio_semantic`, which is honest about its evidence. Later ASR can feed the same timeline interface.

## Acceptance

- Unit tests prove semantic chapters are generated from OCR and audio cues.
- Creative plans serialize `semantic_provider`, `semantic_coverage`, `semantic_chapters`, and per-segment `chapter_title`.
- Candidate EDL includes a Chapter column.
- Creative render outputs `semantic_timeline.json`.
- Existing source-only rendering rules remain unchanged.
