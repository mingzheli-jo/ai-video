# Creative Engine V4 Longform Audio Design

## Goal

Make `creative-edit` usable for full video production instead of short highlight reels. Long source videos must produce a chaptered creative cut with enough duration, clear semantic coverage, audio evidence, and quality gates that reject thin 60-second outputs for 9-minute references.

## Problems To Fix

- Long references currently cap at 8 selected segments, so a 9-minute tutorial becomes about 64 seconds.
- Creative decisions only use frame samples and optional OCR. When OCR is empty, the planner lacks speech/pace evidence.
- Quality checks do not require a meaningful output duration for long creative cuts.
- The workbench does not explain that creative mode now produces a chaptered longform cut.

## Design

V4 adds a local `audio_analysis.json` artifact and feeds audio cues into the creative planner. The first implementation does not depend on cloud transcription. It uses ffmpeg `astats` over evenly spaced windows to estimate audio energy, speech-like continuity, peaks, quietness, and likely emphasis. Optional ASR can be added later without changing the planner interface.

The creative planner changes from a fixed 8-segment cap to a chapter coverage policy. Long tutorials should use more segments and longer total duration while still cutting aggressively. For sources over 5 minutes, the recommended creative variant should target roughly 22-32% of the source duration, capped so sample renders remain practical. The 563-second reference should therefore land around two to three minutes, not one minute.

Each segment gains optional `chapter_role`, `audio_tags`, and `audio_evidence`. Tutorial cuts must cover hook/setup/operation/configuration/validation roles with spaced source ranges. The planner still avoids overlapping ranges and still preserves source geometry.

Quality checks gain longform rules for `creative-edit`:

- `audio_analysis.json` must exist.
- `creative_plan.json` must include `audio_provider` and audio evidence on enough selected segments.
- For long sources, output duration must meet a minimum creative duration ratio unless the source is shorter than the policy threshold.
- Recommended segments must include enough chapter/semantic variety.

## Non-Goals

- No generated fake intro/outro cards.
- No stock footage insertion.
- No mandatory paid OCR/ASR service.
- No copied third-party platform footage.

## Success Criteria

- A 563-second tutorial reference produces a creative plan with more than 8 segments and a target duration over 120 seconds.
- `audio_analysis.json` is generated and referenced by `creative_plan.json`.
- Full tests pass.
- A new sample render produces a playable video, preserves the original 1920x1080 geometry, and passes quality checks.
