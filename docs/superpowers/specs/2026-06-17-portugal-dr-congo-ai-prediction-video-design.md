# Portugal vs DR Congo AI Prediction Video Design

## Objective

Create a 5-6 minute horizontal explainer video that imitates the structure and production feel of the user's reference video without copying its presenter footage or exact UI. The new video focuses on the real 2026 FIFA World Cup match Portugal vs DR Congo and frames the story as an AI Skill pre-match prediction.

The target viewer experience is: a dark studio sports-tech commentary video with an AI/silhouette presenter, glowing prediction dashboards, score cards, bilingual subtitles, and a clear model-based conclusion.

## Confirmed User Choices

- Content direction: use the original video's topic family, AI/Skill football prediction.
- Output scope: full 5-6 minute version.
- Presenter treatment: do not reuse the reference presenter; use an AI/silhouette presenter and custom data panels.
- Match focus: Portugal vs DR Congo.
- Narrative approach: explosive prediction angle, centered on Cristiano Ronaldo's sixth World Cup storyline and whether Portugal can avoid a dangerous opener.

## Source Facts And Boundaries

Use current match facts as factual setup, then clearly label prediction content as model simulation.

- Match: Portugal vs DR Congo.
- Competition context: 2026 FIFA World Cup, Group K.
- Staging: Houston-area stadium listing in public schedule coverage.
- Local match date: June 17, 2026.
- Beijing time framing: June 18, 2026 around 01:00, if the final checked schedule still aligns at implementation time.
- Sources to verify during implementation:
  - FIFA official fixtures page: https://www.fifa.com/en/tournaments/mens/worldcup/canadamexicousa2026/scores-fixtures
  - Houston Chronicle match coverage for Portugal vs DR Congo.
  - A secondary schedule table such as SB Nation or another current sports fixture page.

The video must not present a prediction as a guaranteed result. Use language such as "model projection", "simulation", "pre-match estimate", and "not a certainty".

## Narrative Structure

Target duration: approximately 5:40.

1. Hook, 0:00-0:25
   - Open on the projected score: Portugal 2:1 DR Congo.
   - Core line: this is not a simple strong-vs-weak match; Portugal is favored, but not comfortably.
   - Visuals: large green score panel, AI Skill label, fast scoreboard flash.

2. Match setup, 0:25-1:20
   - Establish the fixture, date, group, and stakes.
   - Mention Ronaldo's sixth World Cup storyline as the public hook.
   - Position DR Congo as a real opponent, not a filler team.

3. Portugal advantage, 1:20-2:35
   - Explain that the projection is not only about Ronaldo.
   - Emphasize squad depth, chance creation, midfield control, fullback width, and tournament experience.
   - Visuals: Portugal player-card wall, control map, green advantage bars.

4. DR Congo upset window, 2:35-3:45
   - Give DR Congo credible threat factors: physical transitions, direct counterattacks, set pieces, emotional return to the World Cup.
   - Include player/role cards where reliable current information is available.
   - Visuals: red/yellow warning panel, counterattack route lines, upset probability meter.

5. AI Skill simulation, 3:45-4:55
   - Show the prediction workflow: inputs, factor weights, scenario branches, probability output.
   - Factors: attack quality, midfield control, defensive risk, transition defense, set pieces, venue/travel context, early-goal sensitivity.
   - Visuals: custom green dashboard, code/input box, probability bars, scenario table.

6. Final prediction, 4:55-5:40
   - Prediction: Portugal 2:1 DR Congo.
   - Reasoning: Portugal has more ways to create chances; DR Congo has enough transition threat to make the game unsafe.
   - Closing caution: if Portugal fails to score in the first 30 minutes, the match becomes more volatile.

## Visual Design

The visual system should imitate the reference video's language while using original assets.

- Canvas: horizontal 16:9, recommended 1920x1080, 30fps.
- Palette: deep cyan-black base, neon green UI, controlled magenta/blue side light, yellow highlights for key numbers.
- Presenter: left-biased AI/silhouette half-body presenter in a dark studio. Avoid a specific real face and avoid copying the reference speaker.
- UI anchor: right or center-right glowing prediction panel with Portugal vs DR Congo, projected score, win probability, and risk flags.
- Subtitle treatment: large Chinese subtitle with smaller English subtitle below, black translucent backing, keyword highlights.
- Motion: slow push-ins, panel slides, scan lines, probability bars filling, scoreboard flashes, subtle handheld/camera drift.
- Transitions: keep transitions smooth and editorial; avoid jump cuts between major sections.

## Components

The implementation should extend the existing `video_factory` pipeline with a new long-form horizontal template rather than replacing the current vertical short-video template.

Recommended units:

- `premium_studio_tutorial` plan builder
  - Produces long-form segments, 16:9 dimensions, and full video metadata.
- Segment data model extension
  - Supports Chinese narration, optional English subtitle text, UI panel type, and key data points.
- Long-form frame renderer
  - Draws studio background, presenter silhouette, UI panels, score cards, subtitles, progress bars, and section labels.
- Horizontal ffmpeg export path
  - Uses the plan dimensions instead of the existing hard-coded `scale=1080:1920`.
- Script and artifact writer
  - Writes script, storyboard JSON, visual prompts, subtitles, cover, and render report.

## Data Flow

1. User choices become a `VideoConfig` for a long-form match-prediction template.
2. The plan builder creates timed segments and narration.
3. Artifact writers output script, storyboard, prompts, and subtitles.
4. Frame renderer creates enough keyframes for each segment to support visual movement.
5. TTS provider creates voiceover audio, preferably Edge TTS for local generation unless a higher-quality voice file is supplied.
6. ffmpeg combines the frame sequence, audio, subtitles baked into frames, and export settings into `release.mp4`.
7. Report writer records duration, resolution, fps, TTS source, match facts, and generated file paths.

## Error Handling

- If external schedule facts cannot be reverified during implementation, use conservative wording and cite the previously checked sources in the script notes.
- If Edge TTS fails, surface the failure; only use tone fallback when explicitly marked as preview/non-release.
- If rendering duration drifts from the intended 5-6 minute range, adjust segment timings before final render.
- If text overflows the frame or subtitle area, wrap and reduce line length rather than shrinking everything globally.
- If a player or squad detail cannot be verified, describe the role-level threat instead of naming uncertain details.

## Testing And Acceptance

Minimum verification before delivery:

- Unit tests for the new long-form plan builder and horizontal ffmpeg command.
- Generate script-only artifacts and inspect segment timing totals.
- Render a short draft or frame set to verify layout, subtitle readability, and cover composition.
- Render the full video at 1920x1080, 30fps.
- Probe final MP4 with ffprobe to confirm duration, resolution, fps, video codec, and audio track.
- Manually inspect representative frames from hook, Portugal analysis, DR Congo risk, AI simulation, and final prediction.

Acceptance criteria:

- Output includes `release.mp4`, `script.md`, `storyboard.json`, `subtitles.srt`, `cover.png`, and `render_report.json`.
- Video is horizontal 16:9, approximately 5-6 minutes, and visually reads as dark studio sports-tech commentary.
- No reference presenter footage is reused.
- Prediction is clearly labeled as model simulation rather than fact.
- The final stated prediction is Portugal 2:1 DR Congo unless the user changes it.

## Implementation Note

This workspace is not currently a Git repository, so the design document cannot be committed here. If the workspace is later initialized as a repo, this spec should be added as the first planning artifact for the implementation branch.
