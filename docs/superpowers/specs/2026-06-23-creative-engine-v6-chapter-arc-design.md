# Creative Engine V6 Chapter Arc Design

## Problem

V5 can render a fuller 4.5-5 minute longform cut, but it can still satisfy duration by selecting many visually similar tutorial frames. The output is technically valid yet can feel like mechanical extension instead of a directed edit.

## Design

V6 adds a longform tutorial chapter arc:

- Start with one high-value `tutorial_hook`.
- Interleave `interface_state`, `configuration_detail`, and `operation_step` through the body.
- End with one or two `result_validation` segments.
- Match candidates against source timestamps and visual roles so the episode covers the source instead of repeatedly sampling one area.

Each chapter role maps to a `creative_move`:

- `tutorial_hook` -> `cold_open`
- `interface_state` -> `reset_context`
- `configuration_detail` -> `decision_point`
- `operation_step` -> `action_chain`
- `result_validation` -> `proof_close`

## Quality Gates

Longform creative plans now fail when:

- A semantic role appears more than two times consecutively.
- The plan does not cover every move listed in `creative_strategy.creative_moves`.

These checks make duration alone insufficient. A 5-minute render must also demonstrate director intent and chapter variety.

## Verified Sample

Reference: `/Users/king/Downloads/下载 (1).mp4`

Output: `/private/tmp/video_factory_creative_engine_v6/release.mp4`

Observed:

- Duration: `301.000s`
- Resolution: `1920x1080`
- Segments: `28`
- Longest same-role run: `2`
- Director moves covered: `action_chain`, `cold_open`, `decision_point`, `proof_close`, `reset_context`
