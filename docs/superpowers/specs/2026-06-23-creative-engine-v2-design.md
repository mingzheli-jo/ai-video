# Creative Engine V2 Design

## Goal

Creative Engine V2 upgrades `creative-edit` from visual-feature scoring to profile-aware semantic scoring. The engine should understand the broad type of source video, choose segments with type-specific editing intent, and reject outputs whose creative plan is still mostly generic context.

This version still uses only real source video. It does not generate fake footage, AI intro/outro cards, decorative progress bars, synthetic scenes, or template overlays.

## Problem

V1 can produce a non-overlapping creative plan, candidate cover sheet, EDL, and self-check report. That is a real foundation, but its judgment is still shallow:

- It mostly scores brightness, contrast, sharpness, color, and motion.
- A tutorial video can end up with too many `context` segments.
- A food/social video and a software tutorial are not yet edited by different rules.
- `creative_plan.json` explains why a frame was visually usable, but not enough about what role it plays in the story.

V2 should make the plan more useful even before watching the rendered video.

## Scope

V2 will add a local deterministic semantic layer. It will not call external AI APIs in this iteration.

Included:

- Add a `CreativeProfile` classification for the source.
- Add type-specific semantic roles.
- Use role-specific story ordering.
- Write semantic profile and role evidence into `creative_plan.json`.
- Extend the quality gate so creative plans with insufficient semantic variety fail.
- Keep all existing V1 artifacts and workbench behavior.

Excluded:

- OCR text extraction from frames.
- Speech-to-text transcription.
- External multimodal model scoring.
- New generated video assets.
- Music, voiceover, subtitles, or platform-specific publish packaging.

## Profiles

The engine will classify source samples into one of three profiles:

| Profile | Intended Inputs | Editing Strategy |
|---|---|---|
| `tutorial_screen` | Software demos, browser/app recordings, workflow explanations | Preserve key interface states and result/validation moments. Avoid compressing into a misleading short ad. |
| `food_social` | Food, cooking, eating, product/process social shorts | Open with the strongest result or detail, then show action/process, texture, and final payoff. |
| `generic_live` | Footage that does not clearly fit the first two | Use visual density and motion, but still require semantic labels beyond generic context. |

Initial classification will use local frame statistics and filename/title hints:

- `tutorial_screen` signals: lower colorfulness, large flat UI-like regions, repeated interface-like frames, title words such as `Codex`, `DeepSeek`, `workflow`, `教程`, `配置`, `API`, `安装`.
- `food_social` signals: higher colorfulness, warmer tones, high texture/detail, title words such as `羊排`, `下酒菜`, `吃`, `烤`, `饭`, `菜`, `美食`.
- Otherwise use `generic_live`.

## Semantic Roles

### Tutorial Roles

- `tutorial_hook`: opening premise, result preview, or concise promise.
- `interface_state`: important app/page/screen state.
- `operation_step`: visible action or transition between states.
- `configuration_detail`: settings, API key, model/provider, install path, or parameter area.
- `result_validation`: success, final result, comparison, or verification.

### Food Roles

- `food_hook`: strongest finished dish, bite, pour, flame, steam, or texture shot.
- `prep_action`: chopping, arranging, seasoning, pouring, heating, mixing.
- `cook_transform`: visible color/texture/state change.
- `texture_closeup`: crispness, sauce, meat fibers, steam, oil, plating detail.
- `final_payoff`: finished dish, eating moment, or table reveal.

### Generic Roles

- `visual_hook`: visually strong opening.
- `action_moment`: clear motion or event.
- `detail_moment`: close/detail/texture.
- `context_bridge`: necessary connective tissue.
- `result_moment`: ending, reveal, or conclusion.

## Story Ordering

V2 should build the recommended variant according to profile:

- `tutorial_screen`: `tutorial_hook -> interface_state -> operation_step -> configuration_detail -> result_validation`.
- `food_social`: `food_hook -> prep_action -> cook_transform -> texture_closeup -> final_payoff`.
- `generic_live`: `visual_hook -> action_moment -> detail_moment -> context_bridge -> result_moment`.

The engine can skip roles when no credible sample exists, but the recommended variant must include at least three distinct semantic roles for videos longer than 20 seconds.

## Creative Plan Output

`creative_plan.json` must include:

- `profile`
- `profile_confidence`
- `profile_evidence`
- `recommended_variant`
- per-segment `semantic_role`
- per-segment `role_evidence`
- per-segment `purpose`
- per-segment `source_sample_index`

The existing `role` field may remain for compatibility, but `semantic_role` becomes the primary creative judgment field.

## Quality Gate

For `creative-edit`, `quality_report.json` must fail if:

- `creative_plan.json` is missing.
- Recommended segments overlap.
- Any recommended segment lacks a non-empty `purpose`.
- Any recommended segment lacks `semantic_role`.
- Videos longer than 20 seconds have fewer than three distinct semantic roles.
- More than half of recommended segments are generic `context`, `context_bridge`, or equivalent low-intent roles.

The report should add checks:

- `creative_plan_has_profile`
- `creative_plan_has_semantic_roles`
- `creative_plan_has_role_variety`

## Workbench

The workbench already exposes `creative_plan`, `candidate_edl`, and `cover_candidates`. V2 does not need a new UI surface yet. The immediate improvement is that the opened artifacts become more useful:

- `candidate_edl.md` should show semantic role names.
- `creative_plan.json` should show profile and evidence.
- `quality_report.json` should make semantic failures obvious.

## Testing

Tests should cover:

- Food filename/title hints classify as `food_social`.
- Tutorial filename/title hints classify as `tutorial_screen`.
- Food plans include food-specific semantic roles.
- Tutorial plans include tutorial-specific semantic roles.
- Quality checks reject creative plans without semantic roles.
- Quality checks reject plans with insufficient semantic variety.
- Existing V1 behavior still passes.

## Success Criteria

V2 is successful when a user can open `creative_plan.json` and see a credible explanation of:

- what kind of video this is,
- why each selected segment belongs in the story,
- what role each segment plays,
- and why the output is not just repeated or randomly sampled footage.

