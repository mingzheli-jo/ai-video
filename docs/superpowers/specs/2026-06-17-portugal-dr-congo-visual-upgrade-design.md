# Portugal vs DR Congo Visual Upgrade Design

## Objective

Upgrade the current generated video from a simple programmatic drawing style to a more realistic sports commentary style. The new visual system should combine royalty-free football B-roll, cinematic AI presenter keyframes, and code-rendered data overlays.

The existing story, prediction, voiceover, and 5:40 horizontal format remain the base. The upgrade focuses on replacing ugly synthetic presenter drawings and flat panels with better source material.

## Confirmed Direction

- Visual direction: cinematic AI presenter, realistic dark studio background, high-end data panels.
- Production method: generate six cinematic keyframes, then edit them into the existing video timeline.
- Sports realism layer: add royalty-free football B-roll for movement, stadium atmosphere, training, fans, pitch details, boots, ball, floodlights, and tactical moments.
- Copyright boundary: do not use official FIFA, broadcaster, federation, or match-highlight footage unless the user provides licensed material.

## Asset Strategy

Use three asset layers:

1. Royalty-free football B-roll
   - Search current free/commercial-use sources such as Pexels, Pixabay, Mixkit, Videvo free-license items, and Wikimedia Commons when license is clear.
   - Prefer generic football visuals: training drills, stadium lights, crowd atmosphere, boots on grass, ball movement, tactical board, tunnel walk, players as silhouettes.
   - Avoid recognizable official match footage, broadcast scorebugs, federation logos, watermarked media, and clips with unclear reuse rights.

2. AI cinematic presenter keyframes
   - Six generated stills matching the six video sections.
   - Dark studio, monitor glow, RGB side lighting, credible sports-tech commentator setup.
   - Presenter should not resemble the reference video's real person.
   - Leave negative space for code-rendered overlays.

3. Code-rendered overlays
   - All readable text, scorelines, percentages, labels, subtitles, and timeline markers are drawn by the pipeline.
   - AI-generated images and B-roll should not be trusted for text.
   - Current bilingual subtitle and prediction text behavior remains, with pixel-width wrapping.

## Timeline Treatment

The upgraded 5:40 video should alternate between presenter/keyframe scenes and B-roll movement:

1. Hook, 0:00-0:25
   - Presenter keyframe as primary visual.
   - Large Portugal vs DR Congo prediction overlay.
   - Optional quick B-roll flash: stadium lights or ball rolling on grass.

2. Match setup, 0:25-1:20
   - Football stadium/fan/travel B-roll, mixed with a wide dark-studio presenter frame.
   - Overlay Group K, Houston timing, Beijing time, and model-simulation disclaimer.

3. Portugal advantage, 1:20-2:35
   - Training/attacking-football B-roll with green advantage bars.
   - AI keyframe: presenter watching a monitor wall.
   - Avoid Portugal logos or official kit replicas unless legally safe.

4. DR Congo risk, 2:35-3:45
   - More physical, tense B-roll: tackles, boots, floodlights, fast pitch movement, tactical routes.
   - Yellow/red risk overlay and transition-risk map.

5. AI Skill simulation, 3:45-4:55
   - Screen-focused AI keyframe plus code-rendered dashboard.
   - Use subtle screen recording motion, scan lines, probability bars, scenario table.

6. Final prediction, 4:55-5:40
   - Return to presenter keyframe and final score panel.
   - End with model-estimate disclaimer and stable final frame.

## Implementation Shape

Extend the existing renderer rather than replacing the whole pipeline.

- Add an asset manifest for the Portugal vs DR Congo template.
- Download or copy approved B-roll clips into the output or asset directory.
- Generate or place six keyframe images in an assets directory.
- Update rendering to use layered media:
  - Background still or B-roll frame.
  - Dark grade/vignette.
  - Code-rendered data panel.
  - Code-rendered Chinese/English subtitles.
- Preserve the current script, TTS, timing, and artifact outputs.

## Quality Requirements

- The video must no longer look like a rough temporary UI drawing.
- No smiling icon face, stick-figure presenter, or flat cartoon dashboard as the main material.
- At least four sections should include real B-roll movement or photo-realistic keyframes.
- All important text must be readable and not generated inside image assets.
- Final output remains 1920x1080, 30fps, 340 seconds.
- Keep `release.mp4`, `script.md`, `storyboard.json`, `subtitles.srt`, `cover.png`, and `render_report.json`.

## Verification

Before delivery:

- Verify asset license notes are recorded in a manifest.
- Run the full test suite.
- Render the full video.
- Probe final MP4 for 1920x1080, 30fps, 340 seconds.
- Generate a contact sheet and visually confirm:
  - realistic presenter/studio visuals,
  - B-roll appears in multiple sections,
  - overlays are readable,
  - no official broadcast footage or watermarks appear.

## Constraints

The current workspace is not a Git repository, so this design cannot be committed here. If a repository is initialized later, add this spec before implementation changes.
