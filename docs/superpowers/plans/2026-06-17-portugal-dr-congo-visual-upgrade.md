# Portugal DR Congo Visual Upgrade Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the rough generated look in the Portugal vs DR Congo prediction video with royalty-free football B-roll, cinematic presenter keyframes, and readable code-rendered overlays.

**Architecture:** Keep the current narration, artifact generation, subtitles, and legacy frame renderer. Add a manifest-driven premium edit path that builds one video segment per story section, overlays transparent data panels, concatenates the segments, and attaches the existing voiceover.

**Tech Stack:** Python 3, Pillow, ffmpeg/ffprobe, pytest, local JSON asset manifests, Mixkit free stock video files, generated PNG keyframes.

---

## File Structure

- Create: `video_factory/assets/portugal_dr_congo/asset_manifest.json`
  - Records every downloaded/generated asset, source URL, license URL, license note, local path, and the segment role that consumes it.
- Create: `video_factory/assets/portugal_dr_congo/broll/*.mp4`
  - Stores downloaded Mixkit football clips used as motion backgrounds.
- Create: `video_factory/assets/portugal_dr_congo/keyframes/*.png`
  - Stores generated cinematic presenter/studio stills.
- Modify: `video_factory/pipeline.py`
  - Adds manifest loading, transparent overlay rendering, segment video rendering, segment concat, and report metadata for premium asset edits.
- Modify: `video_factory/cli.py`
  - Passes the Portugal manifest path into `render_video()` when rendering the Portugal template.
- Modify: `tests/test_video_factory.py`
  - Adds tests for manifest validation, overlay rendering, premium ffmpeg command construction, and CLI manifest wiring.

## Task 1: Asset Manifest Contract

**Files:**
- Create: `video_factory/assets/portugal_dr_congo/asset_manifest.json`
- Modify: `tests/test_video_factory.py`

- [ ] **Step 1: Write the failing manifest test**

Add this test to `tests/test_video_factory.py`:

```python
def test_portugal_visual_asset_manifest_records_safe_sources():
    from video_factory.pipeline import load_visual_asset_manifest

    manifest = load_visual_asset_manifest(
        Path("video_factory/assets/portugal_dr_congo/asset_manifest.json")
    )

    assert manifest["license_policy"] == "No official FIFA, broadcaster, federation, or match-highlight footage."
    assert len(manifest["assets"]) >= 8
    assert {asset["segment_role"] for asset in manifest["assets"]} >= {
        "hook",
        "match_setup",
        "portugal_advantage",
        "dr_congo_risk",
        "ai_skill_simulation",
        "final_prediction",
    }
    assert all(asset["license_url"] == "https://mixkit.co/license/#videoFree" or asset["kind"] == "generated_image" for asset in manifest["assets"])
    assert all("official" not in asset["notes"].lower() for asset in manifest["assets"])
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```bash
python3 -m pytest tests/test_video_factory.py::test_portugal_visual_asset_manifest_records_safe_sources -v
```

Expected: FAIL because `load_visual_asset_manifest` and the manifest file do not exist yet.

- [ ] **Step 3: Add the manifest loader**

In `video_factory/pipeline.py`, add:

```python
def load_visual_asset_manifest(path: Path | str) -> Dict[str, object]:
    manifest_path = Path(path)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    required_keys = {"version", "license_policy", "assets"}
    missing = required_keys - set(payload)
    if missing:
        raise ValueError(f"Asset manifest is missing keys: {', '.join(sorted(missing))}")
    for asset in payload["assets"]:
        for key in ("key", "kind", "segment_role", "local_path", "source_url", "license_url", "notes"):
            if key not in asset:
                raise ValueError(f"Asset entry {asset.get('key', '<unknown>')} is missing {key}")
    return payload
```

- [ ] **Step 4: Add the initial manifest**

Create `video_factory/assets/portugal_dr_congo/asset_manifest.json` with these asset entries:

```json
{
  "version": 1,
  "license_policy": "No official FIFA, broadcaster, federation, or match-highlight footage.",
  "assets": [
    {
      "key": "broll_dribble",
      "kind": "video",
      "segment_role": "portugal_advantage",
      "local_path": "video_factory/assets/portugal_dr_congo/broll/mixkit-player-dribbling-43484.mp4",
      "source_url": "https://assets.mixkit.co/videos/43484/43484-1080.mp4",
      "page_url": "https://mixkit.co/free-stock-video/player-dribbling-in-a-one-on-one-in-a-soccer-43484/",
      "license_url": "https://mixkit.co/license/#videoFree",
      "notes": "Generic semi-professional soccer action, no team branding or broadcast graphics."
    },
    {
      "key": "broll_semi_pro_game",
      "kind": "video",
      "segment_role": "dr_congo_risk",
      "local_path": "video_factory/assets/portugal_dr_congo/broll/mixkit-semi-pro-soccer-game-43485.mp4",
      "source_url": "https://assets.mixkit.co/videos/43485/43485-1080.mp4",
      "page_url": "https://mixkit.co/free-stock-video/semi-pro-soccer-game-43485/",
      "license_url": "https://mixkit.co/license/#videoFree",
      "notes": "Generic night soccer match footage, no scorebug or federation logo."
    },
    {
      "key": "broll_penalty",
      "kind": "video",
      "segment_role": "final_prediction",
      "local_path": "video_factory/assets/portugal_dr_congo/broll/mixkit-penalty-goal-43495.mp4",
      "source_url": "https://assets.mixkit.co/videos/43495/43495-1080.mp4",
      "page_url": "https://mixkit.co/free-stock-video/penalty-kick-seen-from-behind-the-goal-nets-43495/",
      "license_url": "https://mixkit.co/license/#videoFree",
      "notes": "Generic penalty scene for prediction tension, no official competition media."
    },
    {
      "key": "broll_fans",
      "kind": "video",
      "segment_role": "match_setup",
      "local_path": "video_factory/assets/portugal_dr_congo/broll/mixkit-fans-celebrating-44602.mp4",
      "source_url": "https://assets.mixkit.co/videos/44602/44602-1080.mp4",
      "page_url": "https://mixkit.co/free-stock-video/friends-from-different-countries-celebrating-a-goal-44602/",
      "license_url": "https://mixkit.co/license/#videoFree",
      "notes": "Generic World Cup viewing party atmosphere, no broadcast footage."
    },
    {
      "key": "keyframe_hook",
      "kind": "generated_image",
      "segment_role": "hook",
      "local_path": "video_factory/assets/portugal_dr_congo/keyframes/hook_presenter.png",
      "source_url": "generated locally with the built-in image generation tool",
      "license_url": "project-generated",
      "notes": "Generic AI sports commentator in a dark studio, not modeled after a real person."
    },
    {
      "key": "keyframe_studio",
      "kind": "generated_image",
      "segment_role": "match_setup",
      "local_path": "video_factory/assets/portugal_dr_congo/keyframes/studio_wide.png",
      "source_url": "generated locally with the built-in image generation tool",
      "license_url": "project-generated",
      "notes": "Wide sports-tech studio still with empty space for overlays."
    },
    {
      "key": "keyframe_monitor_wall",
      "kind": "generated_image",
      "segment_role": "ai_skill_simulation",
      "local_path": "video_factory/assets/portugal_dr_congo/keyframes/monitor_wall.png",
      "source_url": "generated locally with the built-in image generation tool",
      "license_url": "project-generated",
      "notes": "Screen-focused studio image; all readable text is added by code."
    },
    {
      "key": "keyframe_final",
      "kind": "generated_image",
      "segment_role": "final_prediction",
      "local_path": "video_factory/assets/portugal_dr_congo/keyframes/final_presenter.png",
      "source_url": "generated locally with the built-in image generation tool",
      "license_url": "project-generated",
      "notes": "Final presenter still for stable end frame, no real-player likeness."
    }
  ],
  "segment_asset_order": {
    "hook": ["keyframe_hook"],
    "match_setup": ["broll_fans", "keyframe_studio"],
    "portugal_advantage": ["broll_dribble"],
    "dr_congo_risk": ["broll_semi_pro_game"],
    "ai_skill_simulation": ["keyframe_monitor_wall"],
    "final_prediction": ["broll_penalty", "keyframe_final"]
  }
}
```

- [ ] **Step 5: Run the manifest test**

Run:

```bash
python3 -m pytest tests/test_video_factory.py::test_portugal_visual_asset_manifest_records_safe_sources -v
```

Expected: PASS.

## Task 2: Premium Overlay Rendering

**Files:**
- Modify: `video_factory/pipeline.py`
- Modify: `tests/test_video_factory.py`

- [ ] **Step 1: Write the failing overlay test**

Add:

```python
def test_premium_overlay_renderer_outputs_transparent_1080p_panel(tmp_path):
    from PIL import Image
    from video_factory import build_portugal_dr_congo_prediction_plan
    from video_factory.pipeline import _draw_premium_overlay_frame

    plan = build_portugal_dr_congo_prediction_plan()
    overlay = tmp_path / "overlay.png"

    _draw_premium_overlay_frame(plan, plan.segments[0], 0, overlay, progress=0.5)

    with Image.open(overlay) as image:
        assert image.mode == "RGBA"
        assert image.size == (1920, 1080)
        assert image.getpixel((10, 10))[3] == 0
        assert image.getpixel((900, 190))[3] > 180
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```bash
python3 -m pytest tests/test_video_factory.py::test_premium_overlay_renderer_outputs_transparent_1080p_panel -v
```

Expected: FAIL because `_draw_premium_overlay_frame` does not exist.

- [ ] **Step 3: Add transparent overlay drawing**

In `video_factory/pipeline.py`, add a new overlay helper that reuses the existing premium panel functions on an RGBA transparent canvas:

```python
def _draw_premium_overlay_frame(
    plan: VideoPlan,
    segment: Segment,
    index: int,
    output_path: Path,
    progress: float,
) -> None:
    image = Image.new("RGBA", (plan.width, plan.height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    _draw_premium_top_bar(draw, plan, segment, index, progress)
    _draw_premium_prediction_panel(draw, segment, index, progress)
    _draw_premium_side_panel(draw, segment, index, progress)
    _draw_premium_subtitles(draw, segment)
    image.save(output_path)
```

Then update the premium drawing functions so all filled colors are accepted by Pillow on RGBA images. Hex colors may stay as hex strings; Pillow renders them as opaque colors.

- [ ] **Step 4: Run the overlay test**

Run:

```bash
python3 -m pytest tests/test_video_factory.py::test_premium_overlay_renderer_outputs_transparent_1080p_panel -v
```

Expected: PASS.

## Task 3: Segment Video Rendering

**Files:**
- Modify: `video_factory/pipeline.py`
- Modify: `tests/test_video_factory.py`

- [ ] **Step 1: Write command construction tests**

Add:

```python
def test_build_segment_video_command_uses_video_background(tmp_path):
    from video_factory.pipeline import build_segment_video_command

    command = build_segment_video_command(
        background_path=tmp_path / "broll.mp4",
        overlay_path=tmp_path / "overlay.png",
        output_path=tmp_path / "segment.mp4",
        duration=25,
        width=1920,
        height=1080,
        fps=30,
        background_kind="video",
    )

    assert command[:2] == ["ffmpeg", "-y"]
    assert "-stream_loop" in command
    assert "scale=1920:1080:force_original_aspect_ratio=increase" in " ".join(command)
    assert str(tmp_path / "segment.mp4") == command[-1]


def test_build_segment_video_command_uses_image_background(tmp_path):
    from video_factory.pipeline import build_segment_video_command

    command = build_segment_video_command(
        background_path=tmp_path / "keyframe.png",
        overlay_path=tmp_path / "overlay.png",
        output_path=tmp_path / "segment.mp4",
        duration=55,
        width=1920,
        height=1080,
        fps=30,
        background_kind="generated_image",
    )

    assert "-loop" in command
    assert str(tmp_path / "keyframe.png") in command
    assert "-t" in command
    assert "55" in command
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
python3 -m pytest tests/test_video_factory.py::test_build_segment_video_command_uses_video_background tests/test_video_factory.py::test_build_segment_video_command_uses_image_background -v
```

Expected: FAIL because `build_segment_video_command` does not exist.

- [ ] **Step 3: Add segment command construction**

Add:

```python
def build_segment_video_command(
    background_path: Path | str,
    overlay_path: Path | str,
    output_path: Path | str,
    duration: int,
    width: int,
    height: int,
    fps: int,
    background_kind: str,
) -> List[str]:
    background = str(background_path)
    overlay = str(overlay_path)
    output = str(output_path)
    filter_graph = (
        f"[0:v]scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},setsar=1,"
        "eq=contrast=1.08:brightness=-0.08:saturation=0.82,"
        "format=rgba[bg];"
        "[bg][1:v]overlay=0:0:format=auto,format=yuv420p[v]"
    )
    if background_kind == "video":
        inputs = ["-stream_loop", "-1", "-i", background, "-i", overlay]
    else:
        inputs = ["-loop", "1", "-framerate", str(fps), "-i", background, "-i", overlay]
    return [
        "ffmpeg",
        "-y",
        *inputs,
        "-filter_complex",
        filter_graph,
        "-map",
        "[v]",
        "-an",
        "-t",
        str(duration),
        "-r",
        str(fps),
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        output,
    ]
```

- [ ] **Step 4: Add premium segment renderer**

Add `_render_premium_asset_segments(plan, output_path, manifest)`:

```python
def _render_premium_asset_segments(plan: VideoPlan, output_path: Path, manifest: Dict[str, object]) -> List[Path]:
    overlays_dir = output_path / "overlays"
    segments_dir = output_path / "segments"
    overlays_dir.mkdir(exist_ok=True)
    segments_dir.mkdir(exist_ok=True)
    assets_by_key = {asset["key"]: asset for asset in manifest["assets"]}
    rendered_segments = []
    for index, segment in enumerate(plan.segments):
        asset_key = manifest["segment_asset_order"][segment.role][0]
        asset = assets_by_key[asset_key]
        background_path = Path(asset["local_path"])
        if not background_path.exists():
            raise FileNotFoundError(f"Missing visual asset: {background_path}")
        overlay_path = overlays_dir / f"overlay_{index:02d}_{segment.role}.png"
        segment_path = segments_dir / f"segment_{index:02d}_{segment.role}.mp4"
        _draw_premium_overlay_frame(plan, segment, index, overlay_path, progress=1.0)
        subprocess.run(
            build_segment_video_command(
                background_path=background_path,
                overlay_path=overlay_path,
                output_path=segment_path,
                duration=segment.duration,
                width=plan.width,
                height=plan.height,
                fps=plan.fps,
                background_kind=asset["kind"],
            ),
            check=True,
        )
        rendered_segments.append(segment_path)
    return rendered_segments
```

- [ ] **Step 5: Run segment command tests**

Run:

```bash
python3 -m pytest tests/test_video_factory.py::test_build_segment_video_command_uses_video_background tests/test_video_factory.py::test_build_segment_video_command_uses_image_background -v
```

Expected: PASS.

## Task 4: Premium Asset Edit Export Path

**Files:**
- Modify: `video_factory/pipeline.py`
- Modify: `video_factory/cli.py`
- Modify: `tests/test_video_factory.py`

- [ ] **Step 1: Write render wiring test**

Add:

```python
def test_render_video_with_visual_asset_manifest_uses_segment_export(tmp_path, monkeypatch):
    from video_factory import TTSConfig, TTSResult, build_portugal_dr_congo_prediction_plan
    import video_factory.pipeline as pipeline

    plan = build_portugal_dr_congo_prediction_plan()
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "version": 1,
                "license_policy": "No official FIFA, broadcaster, federation, or match-highlight footage.",
                "assets": [
                    {
                        "key": segment.role,
                        "kind": "generated_image",
                        "segment_role": segment.role,
                        "local_path": str(tmp_path / f"{segment.role}.png"),
                        "source_url": "test",
                        "license_url": "project-generated",
                        "notes": "Generic generated test asset with no official media.",
                    }
                    for segment in plan.segments
                ],
                "segment_asset_order": {segment.role: [segment.role] for segment in plan.segments},
            }
        ),
        encoding="utf-8",
    )
    for segment in plan.segments:
        (tmp_path / f"{segment.role}.png").write_bytes(b"asset")

    def fake_synthesize_voiceover(render_plan, voiceover_path, tts_config):
        Path(voiceover_path).write_bytes(_make_test_wav_bytes(duration_seconds=340))
        return TTSResult(Path(voiceover_path), "edge", "test", "edge-tts", False, "test")

    monkeypatch.setattr(pipeline, "synthesize_voiceover", fake_synthesize_voiceover)
    monkeypatch.setattr(pipeline, "_render_premium_asset_segments", lambda render_plan, out, data: [out / f"{i}.mp4" for i in range(6)])
    monkeypatch.setattr(pipeline, "_concat_segment_videos_with_audio", lambda segments, audio, output, duration: output.write_bytes(b"video"))
    monkeypatch.setattr(pipeline.shutil, "copyfile", lambda source, dest: Path(dest).write_bytes(b"cover"))

    result = pipeline.render_video(
        plan,
        tmp_path / "out",
        TTSConfig(provider="edge"),
        release=True,
        visual_asset_manifest=manifest,
    )

    assert result.video.name == "release.mp4"
    assert result.video.exists()
    report = json.loads(result.report.read_text(encoding="utf-8"))
    assert report["artifacts"]["render_mode"] == "premium_asset_edit"
    assert report["artifacts"]["asset_manifest"].endswith("manifest.json")
```

- [ ] **Step 2: Run the render wiring test to verify it fails**

Run:

```bash
python3 -m pytest tests/test_video_factory.py::test_render_video_with_visual_asset_manifest_uses_segment_export -v
```

Expected: FAIL because `render_video` has no `visual_asset_manifest` argument.

- [ ] **Step 3: Extend `render_video`**

Change the signature:

```python
def render_video(
    plan: VideoPlan,
    output_dir: Path | str,
    tts_config: TTSConfig | None = None,
    release: bool = False,
    visual_asset_manifest: Path | str | None = None,
) -> RenderResult:
```

After voiceover synthesis, branch when `visual_asset_manifest` is supplied:

```python
    if visual_asset_manifest is not None:
        manifest = load_visual_asset_manifest(visual_asset_manifest)
        segment_videos = _render_premium_asset_segments(plan, output_path, manifest)
        concat_path = output_path / "segments.txt"
        _write_segment_concat_file(segment_videos, concat_path)
        video_path = output_path / ("release.mp4" if release else f"{plan.config.output_slug}.mp4")
        _concat_segment_videos_with_audio(segment_videos, voiceover_path, video_path, plan.config.target_duration)
        cover_path = output_path / "cover.png"
        _extract_cover_frame(segment_videos[0], cover_path)
        report_path = output_path / "render_report.json"
        _write_render_report(
            plan,
            voiceover_result,
            video_path,
            cover_path,
            report_path,
            segment_videos,
            render_mode="premium_asset_edit",
            asset_manifest=Path(visual_asset_manifest),
        )
        return RenderResult(...)
```

Update `_write_render_report` to accept `render_mode: str = "frame_concat"` and `asset_manifest: Path | None = None`, then write:

```python
"artifacts": {
    "cover": str(cover_path),
    "frames_count": len(frames),
    "segments_count": len(plan.segments),
    "render_mode": render_mode,
    "asset_manifest": str(asset_manifest) if asset_manifest else "",
}
```

- [ ] **Step 4: Add segment concat helpers**

Add:

```python
def _write_segment_concat_file(segment_videos: Sequence[Path], concat_path: Path) -> None:
    lines = [f"file '{_escape_concat_path(path)}'" for path in segment_videos]
    concat_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _concat_segment_videos_with_audio(
    segment_videos: Sequence[Path],
    audio_path: Path,
    output_path: Path,
    duration: int,
) -> None:
    concat_path = output_path.with_name("segments.txt")
    _write_segment_concat_file(segment_videos, concat_path)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_path),
            "-i",
            str(audio_path),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-shortest",
            "-t",
            str(duration),
            str(output_path),
        ],
        check=True,
    )


def _extract_cover_frame(video_path: Path, cover_path: Path) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(video_path), "-frames:v", "1", str(cover_path)],
        check=True,
    )
```

- [ ] **Step 5: Wire CLI manifest**

In `video_factory/cli.py`, add:

```python
def default_portugal_asset_manifest() -> Path:
    return Path(__file__).resolve().parent / "assets" / "portugal_dr_congo" / "asset_manifest.json"
```

Add an optional CLI argument:

```python
parser.add_argument("--visual-asset-manifest", type=Path, default=None)
```

When rendering:

```python
visual_asset_manifest = args.visual_asset_manifest
if visual_asset_manifest is None and args.template == "portugal-dr-congo":
    candidate = default_portugal_asset_manifest()
    visual_asset_manifest = candidate if candidate.exists() else None
result = render_video(..., visual_asset_manifest=visual_asset_manifest)
```

- [ ] **Step 6: Run render wiring test and existing premium wiring test**

Run:

```bash
python3 -m pytest tests/test_video_factory.py::test_render_video_with_visual_asset_manifest_uses_segment_export tests/test_video_factory.py::test_render_video_with_premium_plan_wires_horizontal_export -v
```

Expected: PASS. The old premium test still uses frame concat because it does not pass a visual asset manifest.

## Task 5: Download And Generate Assets

**Files:**
- Create: `video_factory/assets/portugal_dr_congo/broll/*.mp4`
- Create: `video_factory/assets/portugal_dr_congo/keyframes/*.png`

- [ ] **Step 1: Create asset folders**

Run:

```bash
mkdir -p video_factory/assets/portugal_dr_congo/broll video_factory/assets/portugal_dr_congo/keyframes
```

Expected: both directories exist.

- [ ] **Step 2: Download Mixkit B-roll**

Run:

```bash
curl -L https://assets.mixkit.co/videos/43484/43484-1080.mp4 -o video_factory/assets/portugal_dr_congo/broll/mixkit-player-dribbling-43484.mp4
curl -L https://assets.mixkit.co/videos/43485/43485-1080.mp4 -o video_factory/assets/portugal_dr_congo/broll/mixkit-semi-pro-soccer-game-43485.mp4
curl -L https://assets.mixkit.co/videos/43495/43495-1080.mp4 -o video_factory/assets/portugal_dr_congo/broll/mixkit-penalty-goal-43495.mp4
curl -L https://assets.mixkit.co/videos/44602/44602-1080.mp4 -o video_factory/assets/portugal_dr_congo/broll/mixkit-fans-celebrating-44602.mp4
```

Expected: four MP4 files exist and each file is larger than 1 MB.

- [ ] **Step 3: Generate presenter keyframes**

Use the built-in image generation tool to generate four project-bound PNGs:

```text
hook_presenter.png: Cinematic sports-tech commentator in a dark studio, half body, monitor glow, green data light, red accent rim light, empty right side for overlays, photorealistic, no readable text, no logos, not resembling any real person.
studio_wide.png: Wide dark football analytics studio, presenter silhouette at left, large blurred monitor wall, stadium-light atmosphere, empty center/right for overlay panels, photorealistic, no readable text, no logos.
monitor_wall.png: Close studio view of AI analytics screens and a commentator seen from behind, dark glass desk, green scan light, red/yellow risk accents, no readable text, no logos.
final_presenter.png: Calm final prediction studio frame, presenter at left, deep dark background, premium sports broadcast lighting, empty right side for final score panel, photorealistic, no readable text, no logos.
```

Expected: four PNGs are copied into `video_factory/assets/portugal_dr_congo/keyframes/`.

- [ ] **Step 4: Probe asset files**

Run:

```bash
ffprobe -v error -select_streams v:0 -show_entries stream=width,height,duration -of json video_factory/assets/portugal_dr_congo/broll/mixkit-player-dribbling-43484.mp4
```

Expected: JSON includes a video stream and dimensions at or near 1920x1080.

## Task 6: Full Verification Render

**Files:**
- Output: `video_factory/output/portugal-dr-congo-release/`

- [ ] **Step 1: Run focused tests**

Run:

```bash
python3 -m pytest tests/test_video_factory.py::test_portugal_visual_asset_manifest_records_safe_sources tests/test_video_factory.py::test_premium_overlay_renderer_outputs_transparent_1080p_panel tests/test_video_factory.py::test_build_segment_video_command_uses_video_background tests/test_video_factory.py::test_build_segment_video_command_uses_image_background tests/test_video_factory.py::test_render_video_with_visual_asset_manifest_uses_segment_export -v
```

Expected: PASS.

- [ ] **Step 2: Run full test suite**

Run:

```bash
python3 -m pytest
```

Expected: all tests pass.

- [ ] **Step 3: Render release video**

Run:

```bash
python3 -m video_factory --template portugal-dr-congo --tts-provider edge --voice zh-CN-YunxiNeural --edge-rate +8% --output video_factory/output/portugal-dr-congo-release
```

Expected: `release.mp4`, `script.md`, `storyboard.json`, `subtitles.srt`, `cover.png`, and `render_report.json` exist.

- [ ] **Step 4: Probe final MP4**

Run:

```bash
ffprobe -v error -select_streams v:0 -show_entries stream=width,height,r_frame_rate -show_entries format=duration -of json video_factory/output/portugal-dr-congo-release/release.mp4
```

Expected:

```json
{
  "streams": [{"width": 1920, "height": 1080, "r_frame_rate": "30/1"}],
  "format": {"duration": "340.000000"}
}
```

- [ ] **Step 5: Generate contact sheet**

Run:

```bash
ffmpeg -hide_banner -y -i video_factory/output/portugal-dr-congo-release/release.mp4 -vf "fps=1/42,scale=320:-1,tile=4x2" -frames:v 1 /private/tmp/portugal_dr_congo_visual_upgrade_contact_sheet.jpg
```

Expected: contact sheet exists and shows real football B-roll or cinematic keyframes in at least four sections, with readable overlays and no broadcast scorebugs/watermarks.

## Self-Review

- Spec coverage: Task 1 covers license manifest and copyright boundary. Task 2 covers code-rendered text overlays. Task 3 and Task 4 cover layered B-roll/keyframe segment rendering. Task 5 covers downloading free football B-roll and generating presenter images. Task 6 covers tests, render, ffprobe, and visual contact sheet.
- Placeholder scan: This plan avoids unfinished markers and gives concrete filenames, URLs, snippets, commands, and expected outputs.
- Type consistency: `visual_asset_manifest`, `load_visual_asset_manifest`, `build_segment_video_command`, `_draw_premium_overlay_frame`, `_render_premium_asset_segments`, `_concat_segment_videos_with_audio`, and report fields are named consistently across tasks.
