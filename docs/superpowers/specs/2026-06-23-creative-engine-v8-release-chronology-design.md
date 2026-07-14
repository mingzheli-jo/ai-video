# Creative Engine V8 Release Chronology Design

## Problem

V7 can recognize source-video text reliably and can produce a 5-minute longform cut, but a plan can still feel unlike a human edit when it opens with a result hook and then jumps around the body timeline. A sequence such as `550s -> 43s -> 28s -> 59s -> 12s` is technically non-overlapping, but viewers experience it as random.

## Design

V8 keeps the source-only rendering rule and improves the edit decision layer:

- Longform tutorials may still start with a high-value result or payoff hook.
- After the hook, the first body segment is anchored near the source opening.
- Remaining body segments are released in source-time order.
- OCR text signatures are counted during selection so one repeated page cannot dominate the episode when enough alternate content exists.
- Quality self-checks now reject longform creative plans whose body timeline goes backward after the hook.

## Acceptance

- `creative_plan.json` keeps `starts[1:]` sorted for longform tutorial cuts.
- A repeated OCR page cannot occupy more than the allowed longform signature budget when enough unique samples are available.
- `quality_report.json` exposes `creative_plan_has_release_chronology`.
- A 9-minute tutorial reference still renders a 4.5-5 minute source-only cut with content and audio evidence.

## Verified Sample

Reference: `/Users/king/Downloads/下载 (1).mp4`

Output: `/private/tmp/video_factory_creative_engine_v8/release.mp4`

Observed:

- Duration: `300.967s`
- Resolution: `1920x1080`
- Segments: `28`
- OCR: `36/36` sampled frames recognized from `144` candidates
- Release order after hook: chronological
- Quality report: `passed`
