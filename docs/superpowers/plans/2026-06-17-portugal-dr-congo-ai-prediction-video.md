# Portugal DR Congo AI Prediction Video Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a 5-6 minute horizontal sports-tech prediction video template for Portugal vs DR Congo and export a complete `release.mp4` with script, storyboard, subtitles, cover, and report.

**Architecture:** Keep the existing 45-second vertical template intact. Add a separate long-form plan builder and route rendering through existing artifact/TTS/export functions, with small extensions for bilingual subtitles, horizontal dimensions, and premium studio frames.

**Tech Stack:** Python 3.9+, Pillow, ffmpeg/ffprobe, pytest, optional edge-tts.

---

## File Structure

- Modify `video_factory/pipeline.py`
  - Extend `Segment` with optional English subtitle and panel metadata.
  - Add `build_portugal_dr_congo_prediction_plan`.
  - Make script, visual prompt, SRT, frame rendering, and ffmpeg export dimension-aware.
  - Add premium studio frame drawing helpers.
- Modify `video_factory/cli.py`
  - Add template selection and a plan factory function.
  - Keep the current default behavior unchanged.
- Modify `video_factory/__init__.py`
  - Export the new long-form plan builder.
- Modify `tests/test_video_factory.py`
  - Add focused tests for the new builder, artifacts, horizontal ffmpeg command, frame layout, CLI selection, and render wiring.
- Output during manual verification
  - `video_factory/output/portugal-dr-congo-release/`

The current `video_factory/pipeline.py` is already large, so implementation should keep additions grouped and named. Do not restructure unrelated vertical-video code.

---

### Task 1: Add Long-Form Plan Builder

**Files:**
- Modify: `video_factory/pipeline.py`
- Modify: `video_factory/__init__.py`
- Test: `tests/test_video_factory.py`

- [ ] **Step 1: Write the failing test**

Append this test after `test_default_creator_monetization_plan_matches_requested_contract`:

```python
def test_portugal_dr_congo_plan_matches_long_form_contract():
    from video_factory import build_portugal_dr_congo_prediction_plan

    plan = build_portugal_dr_congo_prediction_plan()

    assert plan.width == 1920
    assert plan.height == 1080
    assert plan.fps == 30
    assert plan.config.target_duration == 340
    assert plan.config.style == "premium_studio_tutorial"
    assert plan.config.goal == "sports_prediction_retention"
    assert "Portugal vs DR Congo" in plan.config.topic
    assert [segment.role for segment in plan.segments] == [
        "hook",
        "match_setup",
        "portugal_advantage",
        "dr_congo_risk",
        "ai_skill_simulation",
        "final_prediction",
    ]
    assert plan.segments[0].start == 0
    assert plan.segments[-1].end == 340
    assert plan.segments[0].english_subtitle.startswith("This is not")
    assert plan.segments[4].panel_type == "simulation"
    assert "Portugal 2:1 DR Congo" in plan.segments[-1].screen_text
    assert all(segment.key_points for segment in plan.segments)
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
python3 -m pytest tests/test_video_factory.py::test_portugal_dr_congo_plan_matches_long_form_contract -v
```

Expected: FAIL with an import error for `build_portugal_dr_congo_prediction_plan`.

- [ ] **Step 3: Extend the segment model**

In `video_factory/pipeline.py`, change the `Segment` dataclass to:

```python
@dataclass(frozen=True)
class Segment:
    role: str
    start: int
    end: int
    screen_text: str
    narration: str
    visual_prompt: str
    english_subtitle: str = ""
    panel_type: str = "creator_dashboard"
    key_points: Sequence[str] = ()

    @property
    def duration(self) -> int:
        return self.end - self.start
```

`Sequence` is already imported in `pipeline.py`, so no import change is required for this field.

- [ ] **Step 4: Add the long-form builder**

Add this function immediately after `build_video_plan` in `video_factory/pipeline.py`:

```python
def build_portugal_dr_congo_prediction_plan(config: VideoConfig | None = None) -> VideoPlan:
    base_config = config or VideoConfig(
        topic="Portugal vs DR Congo：AI Skill 世界杯赛前预测",
        target_duration=340,
        style="premium_studio_tutorial",
        goal="sports_prediction_retention",
        reference_title="C罗第六届世界杯首战，AI预测葡萄牙会不会翻车",
        output_slug="portugal-dr-congo-release",
        tts_provider="edge",
        voice="zh-CN-YunxiNeural",
        voice_instructions="用中文体育解说口吻，节奏稳，重点数字和比分要咬字清楚，像在做赛前战术分析。",
    )
    if base_config.target_duration != 340:
        raise ValueError("The Portugal vs DR Congo long-form template is fixed at 340 seconds.")

    visual_style = (
        "dark sports-tech studio, AI silhouette presenter, neon green prediction dashboard, "
        "Portugal vs DR Congo World Cup 2026 analysis, bilingual subtitles, original UI only"
    )
    segments = [
        Segment(
            role="hook",
            start=0,
            end=25,
            screen_text="AI预测\n葡萄牙 2:1 DR Congo",
            narration=(
                "这场葡萄牙对刚果民主共和国，表面看是强弱局，但我的AI Skill给出的结论不是大胜，"
                "而是葡萄牙二比一小胜。真正危险的地方，在前三十分钟。"
            ),
            english_subtitle="This is not a simple mismatch. The model projects Portugal 2-1 DR Congo.",
            panel_type="score_prediction",
            key_points=("Portugal 2:1 DR Congo", "not a guaranteed result", "early goal sensitivity"),
            visual_prompt=f"{visual_style}; opening score card, large 2-1 projection, urgent hook.",
        ),
        Segment(
            role="match_setup",
            start=25,
            end=80,
            screen_text="Group K首战\n北京时间 6月18日 01:00",
            narration=(
                "先把背景摆清楚。这是二零二六世界杯K组的一场焦点战，休斯敦当地六月十七日中午开球，"
                "换到北京时间大约是六月十八日凌晨一点。C罗第六届世界杯当然是流量入口，"
                "但这场球不能只看一个名字。"
            ),
            english_subtitle="Group K opener in Houston, with Beijing time around 01:00 on June 18.",
            panel_type="fixture_card",
            key_points=("Group K", "June 17 local time", "June 18 Beijing time", "Ronaldo storyline"),
            visual_prompt=f"{visual_style}; fixture card, Houston stadium map, Group K label.",
        ),
        Segment(
            role="portugal_advantage",
            start=80,
            end=155,
            screen_text="葡萄牙优势\n不只是C罗",
            narration=(
                "模型把葡萄牙放在优势方，核心原因不是情怀，而是阵容厚度。"
                "他们有更多能制造最后一传的人，有更稳定的中场控球，也有边路把防线拉开的能力。"
                "如果葡萄牙早早进球，比赛会被拖进他们最舒服的控球节奏。"
            ),
            english_subtitle="Portugal are favored because of depth, control, chance creation, and width.",
            panel_type="advantage_bars",
            key_points=("squad depth", "midfield control", "chance creation", "wide overloads"),
            visual_prompt=f"{visual_style}; Portugal advantage bars, player-card wall, control map.",
        ),
        Segment(
            role="dr_congo_risk",
            start=155,
            end=225,
            screen_text="爆冷窗口\n反击和定位球",
            narration=(
                "但刚果民主共和国不是来当背景板的。模型给他们的窗口，主要在身体对抗、直接反击和定位球。"
                "如果葡萄牙压得太靠前，丢球后的第一脚处理慢半拍，比赛就会从技术局变成冲刺局。"
            ),
            english_subtitle="DR Congo's upset window is physical duels, counters, and set pieces.",
            panel_type="risk_map",
            key_points=("physical duels", "direct counters", "set pieces", "Portugal transition defense"),
            visual_prompt=f"{visual_style}; yellow warning board, counterattack route lines, upset meter.",
        ),
        Segment(
            role="ai_skill_simulation",
            start=225,
            end=295,
            screen_text="AI Skill推演\n胜率不是稳胆",
            narration=(
                "我的Skill会把进攻质量、中场控制、防守转换、定位球风险和赛地因素分开打分，"
                "再跑三种剧本。葡萄牙先领先，胜率明显抬高；零比零拖到半场，冷门概率会上升；"
                "如果刚果民主共和国先进球，这场就会变成压力测试。"
            ),
            english_subtitle="The Skill separates attack, control, transition defense, set pieces, and venue context.",
            panel_type="simulation",
            key_points=("attack quality", "control score", "transition risk", "three scenarios"),
            visual_prompt=f"{visual_style}; code input box, probability table, scenario branches.",
        ),
        Segment(
            role="final_prediction",
            start=295,
            end=340,
            screen_text="最终预测\nPortugal 2:1 DR Congo",
            narration=(
                "所以最终预测，我会给葡萄牙二比一。葡萄牙赢面更大，但不是稳胆。"
                "关键观察点只有一个：前三十分钟能不能先进球。这个结论是赛前模型推演，"
                "不是确定结果，真正的答案还是要等比赛自己给。"
            ),
            english_subtitle="Final projection: Portugal 2-1 DR Congo. It is a model estimate, not a guarantee.",
            panel_type="final_score",
            key_points=("Portugal 2:1 DR Congo", "favored but volatile", "model estimate only"),
            visual_prompt=f"{visual_style}; final score panel, caution badge, closing presenter frame.",
        ),
    ]
    return VideoPlan(config=base_config, width=1920, height=1080, fps=30, segments=segments)
```

- [ ] **Step 5: Export the new builder**

In `video_factory/__init__.py`, add `build_portugal_dr_congo_prediction_plan` to the import list and `__all__`:

```python
from .pipeline import (
    Segment,
    TTSConfig,
    TTSProviderError,
    TTSResult,
    VideoConfig,
    VideoPlan,
    build_ffmpeg_command,
    build_openai_speech_payload,
    build_portugal_dr_congo_prediction_plan,
    build_tone_track,
    build_video_plan,
    render_video,
    synthesize_voiceover,
    wrap_video_text,
    write_artifacts,
)

__all__ = [
    "Segment",
    "TTSConfig",
    "TTSProviderError",
    "TTSResult",
    "VideoConfig",
    "VideoPlan",
    "build_ffmpeg_command",
    "build_openai_speech_payload",
    "build_portugal_dr_congo_prediction_plan",
    "build_tone_track",
    "build_video_plan",
    "render_video",
    "synthesize_voiceover",
    "wrap_video_text",
    "write_artifacts",
]
```

- [ ] **Step 6: Run test to verify it passes**

Run:

```bash
python3 -m pytest tests/test_video_factory.py::test_portugal_dr_congo_plan_matches_long_form_contract -v
```

Expected: PASS.

- [ ] **Step 7: Commit boundary**

This workspace is not currently a Git repository. Run:

```bash
git status --short
```

Expected in the current workspace: `fatal: not a git repository`. If executing inside a Git repository, commit:

```bash
git add video_factory/pipeline.py video_factory/__init__.py tests/test_video_factory.py
git commit -m "feat: add Portugal DR Congo long-form plan"
```

---

### Task 2: Add Bilingual Long-Form Artifacts

**Files:**
- Modify: `video_factory/pipeline.py`
- Test: `tests/test_video_factory.py`

- [ ] **Step 1: Write the failing test**

Append this test after `test_write_artifacts_creates_script_storyboard_prompts_and_srt`:

```python
def test_portugal_dr_congo_artifacts_include_bilingual_subtitles_and_sources(tmp_path):
    from video_factory import build_portugal_dr_congo_prediction_plan

    plan = build_portugal_dr_congo_prediction_plan()
    artifact_paths = write_artifacts(plan, tmp_path)

    script = artifact_paths["script"].read_text(encoding="utf-8")
    assert "Portugal vs DR Congo" in script
    assert "premium_studio_tutorial" in script
    assert "FIFA official fixtures page" in script
    assert "model simulation" in script
    assert "English Subtitle" in script

    subtitles = artifact_paths["subtitles"].read_text(encoding="utf-8")
    assert "葡萄牙对刚果民主共和国" in subtitles
    assert "This is not a simple mismatch" in subtitles

    prompts = artifact_paths["prompts"].read_text(encoding="utf-8")
    assert "16:9 horizontal composition" in prompts
    assert "AI silhouette presenter" in prompts

    storyboard = json.loads(artifact_paths["storyboard"].read_text(encoding="utf-8"))
    assert storyboard["segments"][0]["english_subtitle"].startswith("This is not")
    assert storyboard["segments"][4]["panel_type"] == "simulation"
    assert storyboard["segments"][5]["key_points"][-1] == "model estimate only"
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
python3 -m pytest tests/test_video_factory.py::test_portugal_dr_congo_artifacts_include_bilingual_subtitles_and_sources -v
```

Expected: FAIL because the script table, SRT, and visual prompts do not yet include the long-form bilingual metadata.

- [ ] **Step 3: Update script markdown**

Replace `_build_script_markdown` with:

```python
def _build_script_markdown(plan: VideoPlan) -> str:
    lines = [
        "# Codex 自动化 AI 混剪短视频脚本",
        "",
        f"- 主题：{plan.config.topic}",
        f"- 对标标题：{plan.config.reference_title}",
        f"- 风格：{plan.config.style}",
        f"- 目标：{plan.config.goal}",
        f"- 规格：{plan.width}x{plan.height}, {plan.fps}fps, {plan.config.target_duration}s",
    ]
    if plan.config.style == "premium_studio_tutorial":
        lines.extend(
            [
                "- 事实来源：FIFA official fixtures page, Houston Chronicle match coverage, secondary schedule table",
                "- 边界声明：Prediction content is a model simulation, not a guaranteed match result.",
            ]
        )
    lines.extend(
        [
            "",
            "| 时间 | 段落 | 屏幕大字 | 口播 | English Subtitle | Key Points |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
    )
    for segment in plan.segments:
        screen_text = segment.screen_text.replace("\n", " / ")
        key_points = " / ".join(segment.key_points)
        lines.append(
            f"| {segment.start}-{segment.end}s | {segment.role} | {screen_text} | "
            f"{segment.narration} | {segment.english_subtitle} | {key_points} |"
        )
    lines.append("")
    return "\n".join(lines)
```

- [ ] **Step 4: Update visual prompt markdown**

Replace `_build_visual_prompts` with:

```python
def _build_visual_prompts(plan: VideoPlan) -> str:
    aspect = "16:9 horizontal composition" if plan.width > plan.height else "9:16 vertical composition"
    if plan.config.style == "premium_studio_tutorial":
        shared = (
            "Shared constraints: dark sports-tech studio, AI silhouette presenter, neon green dashboards, "
            f"{aspect}, bilingual subtitles, original UI only, no copied presenter footage, no watermark."
        )
    else:
        shared = (
            f"Shared constraints: AI realistic montage, {aspect}, no copied Douyin footage, "
            "no watermark, no real private data."
        )
    lines = ["# Visual Prompts", "", shared, ""]
    for index, segment in enumerate(plan.segments, start=1):
        lines.extend(
            [
                f"## {index}. {segment.role} ({segment.start}-{segment.end}s)",
                f"Panel type: {segment.panel_type}",
                f"Key points: {'; '.join(segment.key_points)}",
                segment.visual_prompt,
                "",
            ]
        )
    return "\n".join(lines)
```

- [ ] **Step 5: Update SRT generation**

Replace `_build_srt` with:

```python
def _build_srt(plan: VideoPlan) -> str:
    entries = []
    for index, segment in enumerate(plan.segments, start=1):
        subtitle_lines = [segment.narration]
        if segment.english_subtitle:
            subtitle_lines.append(segment.english_subtitle)
        entries.extend(
            [
                str(index),
                f"{_format_srt_time(segment.start)} --> {_format_srt_time(segment.end)}",
                "\n".join(subtitle_lines),
                "",
            ]
        )
    return "\n".join(entries)
```

- [ ] **Step 6: Run focused artifact tests**

Run:

```bash
python3 -m pytest tests/test_video_factory.py::test_write_artifacts_creates_script_storyboard_prompts_and_srt tests/test_video_factory.py::test_portugal_dr_congo_artifacts_include_bilingual_subtitles_and_sources -v
```

Expected: PASS. The existing vertical artifact test still passes because it checks durable content, not the exact table columns.

- [ ] **Step 7: Commit boundary**

Run:

```bash
git status --short
```

Expected in the current workspace: `fatal: not a git repository`. If executing inside a Git repository, commit:

```bash
git add video_factory/pipeline.py tests/test_video_factory.py
git commit -m "feat: add bilingual long-form artifacts"
```

---

### Task 3: Make FFmpeg Export Dimension-Aware

**Files:**
- Modify: `video_factory/pipeline.py`
- Test: `tests/test_video_factory.py`

- [ ] **Step 1: Write the failing test**

Append this test after `test_build_ffmpeg_command_targets_vertical_douyin_export`:

```python
def test_build_ffmpeg_command_targets_horizontal_premium_export(tmp_path):
    frames = tmp_path / "frames.txt"
    audio = tmp_path / "voice.wav"
    output = tmp_path / "premium.mp4"

    command = build_ffmpeg_command(
        frames,
        audio,
        output,
        duration=340,
        fps=30,
        width=1920,
        height=1080,
    )

    assert command[0] == "ffmpeg"
    assert str(frames) in command
    assert str(audio) in command
    assert "scale=1920:1080" in command
    assert "-t" in command
    assert "340" in command
    assert str(output) == command[-1]
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
python3 -m pytest tests/test_video_factory.py::test_build_ffmpeg_command_targets_horizontal_premium_export -v
```

Expected: FAIL with `TypeError` because `width` and `height` are not accepted.

- [ ] **Step 3: Extend ffmpeg command signature**

Replace `build_ffmpeg_command` with:

```python
def build_ffmpeg_command(
    frames_file: Path | str,
    audio_file: Path | str,
    output_file: Path | str,
    duration: int = 45,
    fps: int = 30,
    width: int = 1080,
    height: int = 1920,
) -> List[str]:
    return [
        "ffmpeg",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(frames_file),
        "-i",
        str(audio_file),
        "-t",
        str(duration),
        "-vf",
        f"scale={width}:{height}",
        "-r",
        str(fps),
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-af",
        "apad",
        "-c:a",
        "aac",
        "-b:a",
        "160k",
        "-movflags",
        "+faststart",
        str(output_file),
    ]
```

- [ ] **Step 4: Pass plan dimensions from render**

In `render_video`, change the command construction to:

```python
    command = build_ffmpeg_command(
        concat_path,
        voiceover_path,
        video_path,
        duration=plan.config.target_duration,
        fps=plan.fps,
        width=plan.width,
        height=plan.height,
    )
```

- [ ] **Step 5: Run ffmpeg command tests**

Run:

```bash
python3 -m pytest tests/test_video_factory.py::test_build_ffmpeg_command_targets_vertical_douyin_export tests/test_video_factory.py::test_build_ffmpeg_command_targets_horizontal_premium_export -v
```

Expected: PASS. The vertical test passes because the new defaults preserve `scale=1080:1920`.

- [ ] **Step 6: Commit boundary**

Run:

```bash
git status --short
```

Expected in the current workspace: `fatal: not a git repository`. If executing inside a Git repository, commit:

```bash
git add video_factory/pipeline.py tests/test_video_factory.py
git commit -m "feat: support horizontal ffmpeg export"
```

---

### Task 4: Add Premium Studio Frame Rendering

**Files:**
- Modify: `video_factory/pipeline.py`
- Test: `tests/test_video_factory.py`

- [ ] **Step 1: Write the failing test**

Append this test after `test_build_tone_track_creates_audible_fallback_audio`:

```python
def test_premium_studio_frame_renderer_creates_horizontal_dark_tech_frame(tmp_path):
    from PIL import Image
    from video_factory import build_portugal_dr_congo_prediction_plan
    from video_factory.pipeline import _render_frames

    plan = build_portugal_dr_congo_prediction_plan()
    frames = _render_frames(plan, tmp_path, frames_per_segment=1)

    assert len(frames) == len(plan.segments)
    with Image.open(frames[0]) as image:
        assert image.size == (1920, 1080)
        assert image.getpixel((30, 30))[1] > image.getpixel((30, 30))[0]
        assert image.getpixel((1520, 210))[1] > 80
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
python3 -m pytest tests/test_video_factory.py::test_premium_studio_frame_renderer_creates_horizontal_dark_tech_frame -v
```

Expected: FAIL because the current generic renderer is vertical-template oriented and does not produce the expected premium frame composition.

- [ ] **Step 3: Route premium plans to a dedicated renderer**

At the top of `_draw_segment_frame`, add:

```python
    if plan.config.style == "premium_studio_tutorial":
        _draw_premium_studio_frame(plan, segment, index, output_path, progress)
        return
```

The resulting function begins as:

```python
def _draw_segment_frame(
    plan: VideoPlan,
    segment: Segment,
    index: int,
    output_path: Path,
    progress: float = 0.0,
) -> None:
    if plan.config.style == "premium_studio_tutorial":
        _draw_premium_studio_frame(plan, segment, index, output_path, progress)
        return
    image = Image.new("RGB", (plan.width, plan.height), "#111827")
    draw = ImageDraw.Draw(image)
    _draw_gradient(draw, plan.width, plan.height, index, progress)
    _draw_synthetic_presenter(draw, plan.width, plan.height, index, progress)
    _draw_dashboard(draw, plan.width, plan.height, index, progress)
    _draw_text_layers(draw, plan, segment, index, progress)
    image.save(output_path)
```

- [ ] **Step 4: Add premium studio drawing helpers**

Append these helpers near the existing drawing helpers in `video_factory/pipeline.py`:

```python
def _draw_premium_studio_frame(
    plan: VideoPlan,
    segment: Segment,
    index: int,
    output_path: Path,
    progress: float,
) -> None:
    image = Image.new("RGB", (plan.width, plan.height), "#06100d")
    draw = ImageDraw.Draw(image)
    _draw_premium_background(draw, plan.width, plan.height, index, progress)
    _draw_premium_presenter(draw, index, progress)
    _draw_premium_top_bar(draw, plan, segment, index, progress)
    _draw_premium_prediction_panel(draw, segment, index, progress)
    _draw_premium_side_panel(draw, segment, index, progress)
    _draw_premium_subtitles(draw, segment)
    image.save(output_path)


def _draw_premium_background(
    draw: ImageDraw.ImageDraw, width: int, height: int, index: int, progress: float
) -> None:
    top_rgb = _hex_to_rgb("#071512")
    bottom_rgb = _hex_to_rgb("#020403")
    for y in range(height):
        ratio = y / max(1, height - 1)
        color = tuple(int(top_rgb[c] * (1 - ratio) + bottom_rgb[c] * ratio) for c in range(3))
        draw.line([(0, y), (width, y)], fill=color)
    green_x = 1390 + int(math.sin(progress * math.pi) * 24)
    magenta_x = 360 - int(math.sin(progress * math.pi) * 18)
    for radius, alpha_color in ((420, "#0f5f45"), (300, "#123d35"), (190, "#1b8061")):
        draw.ellipse(
            (green_x - radius, 120 - radius, green_x + radius, 120 + radius),
            outline=alpha_color,
            width=10,
        )
    for radius, color in ((340, "#4a1530"), (230, "#6f1b43")):
        draw.ellipse(
            (magenta_x - radius, 510 - radius, magenta_x + radius, 510 + radius),
            outline=color,
            width=8,
        )
    for x in range(0, width, 96):
        draw.line((x, 0, x - 220, height), fill="#0a211b", width=1)
    draw.rectangle((0, height - 86, width, height), fill="#020403")


def _draw_premium_presenter(draw: ImageDraw.ImageDraw, index: int, progress: float) -> None:
    center_x = 420 + int(math.sin(progress * math.pi) * 12)
    shoulder_y = 820
    draw.ellipse((center_x - 82, 285, center_x + 82, 449), fill="#1d2a28")
    draw.ellipse((center_x - 72, 294, center_x + 72, 438), outline="#35d88e", width=3)
    draw.rounded_rectangle((center_x - 178, 438, center_x + 178, shoulder_y), radius=92, fill="#101817")
    draw.polygon(
        [(center_x - 68, 454), (center_x + 68, 454), (center_x + 30, 690), (center_x - 30, 690)],
        fill="#dfe7e2",
    )
    draw.line((center_x - 155, 520, center_x - 260, 670), fill="#23302d", width=42)
    draw.line((center_x + 155, 520, center_x + 270, 682), fill="#23302d", width=42)
    draw.ellipse((center_x - 38, 348, center_x - 20, 366), fill="#96ffd0")
    draw.ellipse((center_x + 20, 348, center_x + 38, 366), fill="#96ffd0")
    draw.arc((center_x - 34, 382, center_x + 34, 420), 10, 170, fill="#96ffd0", width=4)
    draw.rounded_rectangle((180, 780, 660, 842), radius=18, fill="#071512", outline="#35d88e", width=2)
    draw.text((210, 795), "AI SKILL COMMENTARY", fill="#96ffd0", font=_font(30, bold=True))


def _draw_premium_top_bar(
    draw: ImageDraw.ImageDraw,
    plan: VideoPlan,
    segment: Segment,
    index: int,
    progress: float,
) -> None:
    draw.rounded_rectangle((46, 34, 780, 96), radius=18, fill="#06100d", outline="#35d88e", width=2)
    draw.text((74, 49), "FIFA WORLD CUP 26  |  GROUP K", fill="#96ffd0", font=_font(28, bold=True))
    draw.rounded_rectangle((1325, 34, 1876, 96), radius=18, fill="#06100d", outline="#243b35", width=2)
    draw.text((1354, 49), f"{segment.start:03d}-{segment.end:03d}s  {segment.role}", fill="#d9fff0", font=_font(26, bold=True))
    draw.rounded_rectangle((46, 112, 1876, 126), radius=7, fill="#123d35")
    draw.rounded_rectangle((46, 112, 46 + int(1830 * progress), 126), radius=7, fill="#f5e84c")


def _draw_premium_prediction_panel(
    draw: ImageDraw.ImageDraw, segment: Segment, index: int, progress: float
) -> None:
    x0, y0, x1, y1 = 810, 158, 1818, 694
    draw.rounded_rectangle((x0, y0, x1, y1), radius=24, fill="#082119", outline="#35d88e", width=4)
    draw.rounded_rectangle((x0 + 24, y0 + 24, x1 - 24, y0 + 82), radius=16, fill="#10372a")
    draw.text((x0 + 50, y0 + 38), "PORTUGAL  VS  DR CONGO", fill="#d9fff0", font=_font(38, bold=True))
    draw.text((x0 + 58, y0 + 128), "2", fill="#ffffff", font=_font(150, bold=True))
    draw.text((x0 + 210, y0 + 174), ":", fill="#f5e84c", font=_font(92, bold=True))
    draw.text((x0 + 292, y0 + 128), "1", fill="#ffffff", font=_font(150, bold=True))
    draw.text((x0 + 470, y0 + 158), "MODEL PROJECTION", fill="#96ffd0", font=_font(34, bold=True))
    bars = [
        ("Portugal win", 0.62, "#35d88e"),
        ("Draw", 0.22, "#f5e84c"),
        ("DR Congo win", 0.16, "#ff6b7a"),
    ]
    for row, (label, value, color) in enumerate(bars):
        y = y0 + 310 + row * 62
        draw.text((x0 + 58, y), label, fill="#d9fff0", font=_font(28, bold=True))
        draw.rounded_rectangle((x0 + 300, y + 8, x1 - 72, y + 34), radius=13, fill="#14342b")
        draw.rounded_rectangle(
            (x0 + 300, y + 8, x0 + 300 + int((x1 - x0 - 372) * value * (0.84 + 0.16 * progress)), y + 34),
            radius=13,
            fill=color,
        )
    if segment.panel_type == "simulation":
        draw.rounded_rectangle((x0 + 58, y1 - 96, x1 - 58, y1 - 42), radius=14, fill="#02110d")
        draw.text((x0 + 82, y1 - 82), "scenario: early goal / 0-0 halftime / DR Congo first goal", fill="#96ffd0", font=_font(25))


def _draw_premium_side_panel(
    draw: ImageDraw.ImageDraw, segment: Segment, index: int, progress: float
) -> None:
    x0, y0, x1, y1 = 84, 160, 720, 262
    title_lines = _wrap_by_chars(segment.screen_text, 13)
    y = y0
    for line in title_lines[:3]:
        draw.text((x0, y), line, fill="#ffffff", font=_font(58, bold=True))
        y += 72
    card_y = 650
    draw.rounded_rectangle((760, 732, 1818, 896), radius=22, fill="#06100d", outline="#243b35", width=2)
    draw.text((790, 754), "KEY VARIABLES", fill="#f5e84c", font=_font(30, bold=True))
    for point_index, point in enumerate(segment.key_points[:4]):
        px = 800 + point_index * 250
        draw.rounded_rectangle((px, 806, px + 210, 852), radius=12, fill="#10372a")
        draw.text((px + 14, 818), point[:22], fill="#d9fff0", font=_font(20, bold=True))
    draw.rounded_rectangle((84, card_y, 684, 735), radius=20, fill="#06100d", outline="#35d88e", width=2)
    draw.text((112, card_y + 24), "Prediction is a model estimate, not a certainty.", fill="#d9fff0", font=_font(24))


def _draw_premium_subtitles(draw: ImageDraw.ImageDraw, segment: Segment) -> None:
    subtitle_y = 912
    draw.rounded_rectangle((180, subtitle_y - 18, 1740, 1062), radius=20, fill="#020403", outline="#14251f", width=2)
    chinese_lines = wrap_video_text(segment.narration, 31)[:2]
    y = subtitle_y
    for line in chinese_lines:
        bbox = draw.textbbox((0, 0), line, font=_font(36, bold=True))
        draw.text(((1920 - (bbox[2] - bbox[0])) / 2, y), line, fill="#ffffff", font=_font(36, bold=True))
        y += 46
    if segment.english_subtitle:
        english = segment.english_subtitle
        bbox = draw.textbbox((0, 0), english, font=_font(25))
        x = max(210, (1920 - (bbox[2] - bbox[0])) / 2)
        draw.text((x, 1018), english, fill="#96ffd0", font=_font(25))
```

- [ ] **Step 5: Run the frame renderer test**

Run:

```bash
python3 -m pytest tests/test_video_factory.py::test_premium_studio_frame_renderer_creates_horizontal_dark_tech_frame -v
```

Expected: PASS.

- [ ] **Step 6: Run a quick visual frame generation command**

Run:

```bash
python3 -m video_factory --template portugal-dr-congo --script-only --output video_factory/output/portugal-dr-congo-script
```

Expected: script-only artifacts are written. Frame rendering is covered by the test in this task; full media generation comes after CLI wiring.

- [ ] **Step 7: Commit boundary**

Run:

```bash
git status --short
```

Expected in the current workspace: `fatal: not a git repository`. If executing inside a Git repository, commit:

```bash
git add video_factory/pipeline.py tests/test_video_factory.py
git commit -m "feat: draw premium studio frames"
```

---

### Task 5: Add CLI Template Selection

**Files:**
- Modify: `video_factory/cli.py`
- Test: `tests/test_video_factory.py`

- [ ] **Step 1: Write the failing tests**

Append these tests after `test_cli_defaults_edge_voice_for_edge_provider`:

```python
def test_cli_parses_portugal_template_options():
    args = parse_args(["--template", "portugal-dr-congo", "--tts-provider", "edge"])

    assert args.template == "portugal-dr-congo"
    assert args.tts_provider == "edge"


def test_cli_builds_portugal_template_plan():
    from video_factory.cli import build_plan_from_args

    args = parse_args(["--template", "portugal-dr-congo", "--tts-provider", "edge"])
    plan = build_plan_from_args(args)

    assert plan.config.target_duration == 340
    assert plan.width == 1920
    assert plan.height == 1080
    assert plan.config.style == "premium_studio_tutorial"
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
python3 -m pytest tests/test_video_factory.py::test_cli_parses_portugal_template_options tests/test_video_factory.py::test_cli_builds_portugal_template_plan -v
```

Expected: FAIL because `--template` and `build_plan_from_args` do not exist.

- [ ] **Step 3: Update CLI imports**

In `video_factory/cli.py`, change the import to:

```python
from .pipeline import (
    TTSConfig,
    VideoConfig,
    build_portugal_dr_congo_prediction_plan,
    build_video_plan,
    render_video,
    write_artifacts,
)
```

- [ ] **Step 4: Add the template argument**

In `parse_args`, add this argument before `--topic`:

```python
    parser.add_argument(
        "--template",
        choices=["creator-monetization", "portugal-dr-congo"],
        default="creator-monetization",
    )
```

- [ ] **Step 5: Add a plan factory**

Add this function after `build_tts_config`:

```python
def build_plan_from_args(args: argparse.Namespace):
    if args.template == "portugal-dr-congo":
        config = VideoConfig(
            topic="Portugal vs DR Congo：AI Skill 世界杯赛前预测",
            target_duration=340,
            style="premium_studio_tutorial",
            goal="sports_prediction_retention",
            reference_title="C罗第六届世界杯首战，AI预测葡萄牙会不会翻车",
            output_slug=args.output.name,
            tts_provider=args.tts_provider,
            voice=build_tts_config(args).voice,
            voice_instructions=args.voice_instructions,
        )
        return build_portugal_dr_congo_prediction_plan(config)

    config = VideoConfig(
        topic=args.topic,
        target_duration=args.duration,
        style=args.style,
        goal=args.goal,
        reference_title=args.reference_title,
        output_slug=args.output.name,
        tts_provider=args.tts_provider,
        voice=build_tts_config(args).voice,
        voice_instructions=args.voice_instructions,
    )
    return build_video_plan(config)
```

- [ ] **Step 6: Use the plan factory in `main`**

Replace the `config = ...` and `plan = build_video_plan(config)` block in `main` with:

```python
    plan = build_plan_from_args(args)
```

The rest of `main` stays the same.

- [ ] **Step 7: Run CLI tests**

Run:

```bash
python3 -m pytest tests/test_video_factory.py::test_cli_parses_release_tts_options tests/test_video_factory.py::test_cli_defaults_edge_voice_for_edge_provider tests/test_video_factory.py::test_cli_parses_portugal_template_options tests/test_video_factory.py::test_cli_builds_portugal_template_plan -v
```

Expected: PASS.

- [ ] **Step 8: Generate script-only artifacts from the CLI**

Run:

```bash
python3 -m video_factory --template portugal-dr-congo --tts-provider edge --script-only --output video_factory/output/portugal-dr-congo-script
```

Expected output includes:

```text
script: video_factory/output/portugal-dr-congo-script/script.md
storyboard: video_factory/output/portugal-dr-congo-script/storyboard.json
prompts: video_factory/output/portugal-dr-congo-script/visual_prompts.md
subtitles: video_factory/output/portugal-dr-congo-script/subtitles.srt
```

- [ ] **Step 9: Commit boundary**

Run:

```bash
git status --short
```

Expected in the current workspace: `fatal: not a git repository`. If executing inside a Git repository, commit:

```bash
git add video_factory/cli.py tests/test_video_factory.py
git commit -m "feat: add Portugal DR Congo CLI template"
```

---

### Task 6: Render Wiring And Full Verification

**Files:**
- Modify: `tests/test_video_factory.py`
- Verify outputs under: `video_factory/output/portugal-dr-congo-release/`

- [ ] **Step 1: Write the render wiring test**

Append this test before `_make_test_wav_bytes`:

```python
def test_render_video_with_premium_plan_wires_horizontal_export(tmp_path, monkeypatch):
    from video_factory import TTSResult, build_portugal_dr_congo_prediction_plan
    import video_factory.pipeline as pipeline

    plan = build_portugal_dr_congo_prediction_plan()
    commands = []

    def fake_render_frames(render_plan, frames_dir):
        frames_dir.mkdir(exist_ok=True)
        frames = []
        for index, segment in enumerate(render_plan.segments):
            frame = frames_dir / f"frame_{index:02d}_{segment.role}.png"
            frame.write_bytes(b"fake-png")
            frames.append(frame)
        return frames

    def fake_synthesize_voiceover(render_plan, voiceover_path, tts_config):
        Path(voiceover_path).write_bytes(_make_test_wav_bytes(duration_seconds=340))
        return TTSResult(
            path=Path(voiceover_path),
            provider="edge",
            voice="zh-CN-YunxiNeural",
            model="edge-tts",
            used_fallback=False,
            notes="test voiceover",
        )

    def fake_run(command, check):
        commands.append(command)
        output = Path(command[-1])
        output.write_bytes(b"fake-video")

    monkeypatch.setattr(pipeline, "_render_frames", fake_render_frames)
    monkeypatch.setattr(pipeline, "synthesize_voiceover", fake_synthesize_voiceover)
    monkeypatch.setattr(pipeline.subprocess, "run", fake_run)

    result = render_video(plan, tmp_path, TTSConfig(provider="edge", voice="zh-CN-YunxiNeural"), release=True)

    assert result.video.name == "release.mp4"
    assert result.video.exists()
    assert result.cover.exists()
    assert any("scale=1920:1080" in command for command in commands)
    report = json.loads(result.report.read_text(encoding="utf-8"))
    assert report["video"]["width"] == 1920
    assert report["video"]["height"] == 1080
    assert report["video"]["duration"] == 340
    assert report["tts"]["provider"] == "edge"
```

- [ ] **Step 2: Run test to verify it passes**

Run:

```bash
python3 -m pytest tests/test_video_factory.py::test_render_video_with_premium_plan_wires_horizontal_export -v
```

Expected: PASS after Tasks 1-5 are complete.

- [ ] **Step 3: Run the full unit suite**

Run:

```bash
python3 -m pytest
```

Expected: all tests pass.

- [ ] **Step 4: Render the full release with Edge TTS**

Run:

```bash
python3 -m video_factory --template portugal-dr-congo --tts-provider edge --voice zh-CN-YunxiNeural --edge-rate +8% --output video_factory/output/portugal-dr-congo-release
```

Expected output includes:

```text
script: video_factory/output/portugal-dr-congo-release/script.md
storyboard: video_factory/output/portugal-dr-congo-release/storyboard.json
prompts: video_factory/output/portugal-dr-congo-release/visual_prompts.md
subtitles: video_factory/output/portugal-dr-congo-release/subtitles.srt
voiceover: video_factory/output/portugal-dr-congo-release/voiceover.wav
cover: video_factory/output/portugal-dr-congo-release/cover.png
report: video_factory/output/portugal-dr-congo-release/render_report.json
video: video_factory/output/portugal-dr-congo-release/release.mp4
```

If Edge TTS fails because the package is missing, install the already-declared optional dependency:

```bash
python3 -m pip install -e ".[tts]"
```

Then rerun the render command.

- [ ] **Step 5: Probe final video**

Run:

```bash
ffprobe -v error -select_streams v:0 -show_entries stream=width,height,r_frame_rate -show_entries format=duration -of json video_factory/output/portugal-dr-congo-release/release.mp4
```

Expected JSON contains width `1920`, height `1080`, frame rate `30/1`, and duration close to `340`.

- [ ] **Step 6: Create a verification contact sheet**

Run:

```bash
ffmpeg -hide_banner -y -i video_factory/output/portugal-dr-congo-release/release.mp4 -vf "fps=1/45,scale=320:-1,tile=4x2" -frames:v 1 /private/tmp/portugal_dr_congo_contact_sheet.jpg
```

Expected: `/private/tmp/portugal_dr_congo_contact_sheet.jpg` exists and shows hook, match setup, Portugal advantage, DR Congo risk, AI simulation, and final prediction frames.

- [ ] **Step 7: Inspect generated artifacts**

Run:

```bash
python3 - <<'PY'
import json
from pathlib import Path
base = Path("video_factory/output/portugal-dr-congo-release")
report = json.loads((base / "render_report.json").read_text(encoding="utf-8"))
print(report["video"])
print(report["tts"])
print((base / "script.md").read_text(encoding="utf-8").splitlines()[:12])
PY
```

Expected output shows a 1920x1080, 340-second video report, Edge TTS metadata, and script header lines containing `Portugal vs DR Congo`.

- [ ] **Step 8: Commit boundary**

Run:

```bash
git status --short
```

Expected in the current workspace: `fatal: not a git repository`. If executing inside a Git repository, commit:

```bash
git add tests/test_video_factory.py
git commit -m "test: verify premium video render wiring"
```

---

## Final Acceptance Checklist

- [ ] `python3 -m pytest` passes.
- [ ] `video_factory/output/portugal-dr-congo-release/release.mp4` exists.
- [ ] `ffprobe` confirms 1920x1080, 30fps, approximately 340 seconds.
- [ ] `script.md` includes factual-source notes and model-simulation boundary language.
- [ ] `subtitles.srt` contains Chinese narration and English subtitle lines.
- [ ] `cover.png` is a dark sports-tech frame with the Portugal vs DR Congo prediction panel.
- [ ] Contact sheet shows all six story sections.
- [ ] Final answer reports all generated file paths and any verification command that could not be run.

## Self-Review

- Spec coverage: Tasks 1 and 5 cover the confirmed video scope and CLI entry. Task 2 covers bilingual script, storyboard, prompts, subtitles, source notes, and model-simulation boundary. Task 3 covers horizontal 1920x1080 export. Task 4 covers original dark studio visuals without copied presenter footage. Task 6 covers full render and verification.
- Placeholder scan: no incomplete markers are used in the plan.
- Type consistency: `english_subtitle`, `panel_type`, and `key_points` are added to `Segment` in Task 1 and reused with the same names in artifact, frame, and test tasks.
