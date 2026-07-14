# Portugal DR Congo Real Footage Visual Redesign

## Goal

Rebuild the Portugal vs DR Congo prediction video so the picture reads as a realistic football analysis montage, not an AI presenter or synthetic studio video.

## Visual Direction

- Use real royalty-free football B-roll as the full-screen base layer for every segment.
- Do not use generated human faces, generated presenters, generated studio stills, official match footage, broadcaster footage, federation footage, or highlight clips.
- Keep overlays small and broadcast-like: top progress ticker, compact prediction chip, lower-third subtitles, and small key-variable tags.
- Let real football motion carry the video. UI should support the analysis, not dominate the image.

## Asset Policy

The manifest will only schedule `video` assets for the Portugal DR Congo template. Generated images may remain on disk for archival purposes, but they must not appear in `segment_asset_order`.

All newly scheduled footage must include source URL, page URL when available, and license URL. Current allowed source family is Mixkit free stock video under the Mixkit free video license.

## Rendering Behavior

Add a manifest-level overlay style named `cinematic_broll`.

When that style is active:

- render full-screen B-roll with mild cinematic color treatment;
- draw a compact top match strip and timeline;
- draw a small score/probability card in the upper right;
- draw key variables as small tags above the subtitle band;
- draw Chinese and English subtitles in the lower safe area;
- avoid large centered panels, fake presenter portraits, and fake studio elements.

## Verification

- Tests must confirm scheduled assets are video-only.
- Tests must confirm cinematic overlay output is transparent outside the compact UI regions.
- Rendered output must be 1920x1080, 30fps, 340 seconds.
- A contact sheet and at least one late-frame still must be visually inspected for realistic footage, readable overlays, and no official broadcast graphics.
