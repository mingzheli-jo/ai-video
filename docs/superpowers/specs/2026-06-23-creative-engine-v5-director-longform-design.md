# Creative Engine V5 Director Longform Design

## Goal

Upgrade `creative-edit` from a two-to-three minute chapter cut into a fuller source-led episode. For a 9-minute tutorial reference, V5 should target roughly 4.5 to 5 minutes while still cutting weak repetition and preserving source-only authenticity.

## Problems To Fix

- V4 is still constrained by 18 frame samples and a 24-segment cap.
- A 563-second source currently lands around 160 seconds, which is useful but still too compressed for a full tutorial.
- The creative plan explains semantic roles, content evidence, and audio evidence, but it does not explicitly describe the creative/director strategy.
- Quality checks only require a low longform duration floor.

## Design

V5 raises creative mode coverage for long videos:

- Sources over 5 minutes target about 52% of source duration, capped around 330 seconds for practical local rendering.
- Long source sampling increases from 18 to 36 frames.
- Recommended segment count can grow to 36, enough to cover more chapters.
- Segment duration for long videos can grow to 10-12 seconds based on source length.

V5 adds a director layer:

- `creative_strategy` at plan level records the V5 policy, target duration, coverage ratio, target segment count, and creative treatment.
- Each selected segment gets a `creative_move`, such as `cold_open`, `reset_context`, `action_chain`, `decision_point`, or `proof_close`.
- `candidate_edl.md` includes the creative move so a human can judge whether the edit has intent.

Quality gates enforce the new baseline:

- Long creative cuts must satisfy a higher duration floor.
- Creative plans must include `creative_strategy`.
- Most selected segments must include `creative_move`.

## Non-Goals

- No generated stock footage or fake scenes.
- No template intro/outro cards.
- No mandatory cloud ASR/OCR.
- No promise that local volume analysis equals real speech transcription.

## Success Criteria

- The 563-second reference produces a plan with at least 28 segments and at least 270 seconds target duration.
- Creative sample extraction supports 36 samples for long sources.
- `creative_plan.json` includes `creative_strategy` and per-segment `creative_move`.
- A new rendered sample preserves 1920x1080, passes quality checks, and is meaningfully longer than V4.
