# Creative Engine V10 Transcript Layer Design

## Problem

V9 adds `semantic_timeline.json` from OCR/content cues and audio emphasis. That improves the edit decision layer, but it still cannot use spoken narration directly. For tutorial videos, the spoken explanation often names the real step before the UI text becomes readable, so relying only on OCR can mislabel chapters or miss intent.

## Design

V10 adds a transcript analysis layer without claiming unavailable ASR quality:

- Create `transcript_analysis.json` for every `creative-edit` render.
- Support sidecar transcripts first: `.srt`, `.vtt`, and plain `.txt` files next to the source video, or an explicit path later.
- Map transcript text onto the same sample indices used by content and audio analysis.
- When no transcript sidecar is available, produce an honest `ocr_transcript_proxy` fallback from recognized OCR text so the downstream interface is still populated.
- Let `build_semantic_timeline` accept optional transcript analysis and prefer transcript text when deriving topics and chapter evidence.
- Surface `transcript_analysis` in returned artifacts and the workbench artifact list.
- Add quality checks so `creative-edit` cannot pass without `transcript_analysis.json`.

The transcript layer is an interface and evidence artifact, not a visual package. It must not add generated intro cards, captions, overlays, voiceover, or any visible template elements to the rendered video.

## Data Model

`TranscriptAnalysis`:

- `provider`: name, status, message.
- `cues`: sample index, timestamp range, text, source, confidence, evidence.
- `coverage`: cue count, text cue count, sidecar cue count, OCR proxy cue count.

Provider names:

- `sidecar_transcript`: parsed local `.srt`, `.vtt`, or `.txt`.
- `ocr_transcript_proxy`: fallback built from OCR/content text.
- `none`: no text evidence available.

## Acceptance

- Unit tests parse SRT/VTT/plain text into sample-aligned transcript cues.
- Unit tests prove OCR fallback writes `transcript_analysis.json`.
- Semantic timeline tests prove transcript text can classify chapters when OCR text is blank.
- Creative render paths include `transcript_analysis.json`.
- Workbench exposes `transcript_analysis`.
- Quality report exposes `transcript_analysis_exists`.
- Existing source-only rendering rules remain unchanged.
