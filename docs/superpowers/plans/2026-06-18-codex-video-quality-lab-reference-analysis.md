# Codex Video Quality Lab Reference Analysis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a first-version local reference-video analysis tool that extracts media facts and review frames, then writes a structured quality report, production template, and scorecard for Codex-assisted expert review.

**Architecture:** Add a focused `video_factory.analysis` package instead of extending the current render-heavy `pipeline.py`. The package will separate media probing, frame sampling, report writing, and CLI orchestration so later automated metrics can be added without disturbing video rendering.

**Tech Stack:** Python 3.9+, stdlib `argparse`, `dataclasses`, `json`, `pathlib`, `subprocess`, existing `ffmpeg`/`ffprobe`, pytest.

---

## File Structure

- Create `video_factory/analysis/__init__.py`: public exports for the analysis package.
- Create `video_factory/analysis/models.py`: dataclasses for `MediaInfo`, `SampleFrame`, `AnalysisPaths`, and `AnalysisResult`.
- Create `video_factory/analysis/media.py`: slug generation, ffprobe command execution, and media-info parsing.
- Create `video_factory/analysis/frames.py`: sample timestamp selection and ffmpeg command builders/runners for frames and contact sheets.
- Create `video_factory/analysis/reports.py`: markdown generation for timeline seed, quality report scaffold, production template, and scorecard.
- Create `video_factory/analysis/runner.py`: end-to-end orchestration that writes the output directory.
- Create `video_factory/analysis/__main__.py`: command-line entrypoint for `python3 -m video_factory.analysis`.
- Modify `README.md`: document the first-version reference analysis workflow.
- Create `tests/test_analysis.py`: unit tests for parsing, sampling, report sections, runner behavior, and CLI argument handling.

The first version does not call an LLM from code. It creates a strong evidence pack and structured report files that Codex can fill through expert review after reading frames and media facts.

---

### Task 1: Analysis Models

**Files:**
- Create: `video_factory/analysis/__init__.py`
- Create: `video_factory/analysis/models.py`
- Test: `tests/test_analysis.py`

- [ ] **Step 1: Write failing model tests**

Add this to `tests/test_analysis.py`:

```python
from pathlib import Path

from video_factory.analysis import AnalysisPaths, MediaInfo, SampleFrame


def test_media_info_dataclass_exposes_core_video_facts():
    media = MediaInfo(
        source_path=Path("/tmp/reference.mp4"),
        duration=355.11,
        width=3840,
        height=2160,
        fps=60.0,
        video_codec="h264",
        audio_codec="aac",
        audio_sample_rate=48000,
        bit_rate=13277000,
    )

    assert media.aspect_ratio == "16:9"
    assert media.orientation == "landscape"
    assert media.to_json_dict()["duration"] == 355.11
    assert media.to_json_dict()["width"] == 3840


def test_analysis_paths_collects_expected_outputs(tmp_path):
    paths = AnalysisPaths.for_output_dir(tmp_path)

    assert paths.media_info == tmp_path / "media_info.json"
    assert paths.contact_sheet == tmp_path / "contact_sheet.jpg"
    assert paths.sample_frames_dir == tmp_path / "sample_frames"
    assert paths.timeline == tmp_path / "timeline.md"
    assert paths.quality_report == tmp_path / "quality_report.md"
    assert paths.production_template == tmp_path / "production_template.md"
    assert paths.scorecard == tmp_path / "scorecard.md"


def test_sample_frame_uses_stable_filename():
    frame = SampleFrame(timestamp=12.5, path=Path("sample_frames/frame_012_50.jpg"))

    assert frame.label == "00:12.50"
```

- [ ] **Step 2: Run model tests and verify they fail**

Run:

```bash
python3 -m pytest tests/test_analysis.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'video_factory.analysis'`.

- [ ] **Step 3: Create model implementation**

Create `video_factory/analysis/models.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class MediaInfo:
    source_path: Path
    duration: float
    width: int
    height: int
    fps: float
    video_codec: str
    audio_codec: str
    audio_sample_rate: int
    bit_rate: int

    @property
    def orientation(self) -> str:
        if self.height > self.width:
            return "portrait"
        if self.width > self.height:
            return "landscape"
        return "square"

    @property
    def aspect_ratio(self) -> str:
        if self.width == 0 or self.height == 0:
            return "unknown"
        divisor = _gcd(self.width, self.height)
        return f"{self.width // divisor}:{self.height // divisor}"

    def to_json_dict(self) -> dict[str, object]:
        return {
            "source_path": str(self.source_path),
            "duration": round(self.duration, 3),
            "width": self.width,
            "height": self.height,
            "fps": round(self.fps, 3),
            "video_codec": self.video_codec,
            "audio_codec": self.audio_codec,
            "audio_sample_rate": self.audio_sample_rate,
            "bit_rate": self.bit_rate,
            "orientation": self.orientation,
            "aspect_ratio": self.aspect_ratio,
        }


@dataclass(frozen=True)
class SampleFrame:
    timestamp: float
    path: Path

    @property
    def label(self) -> str:
        minutes = int(self.timestamp // 60)
        seconds = self.timestamp - minutes * 60
        return f"{minutes:02d}:{seconds:05.2f}"


@dataclass(frozen=True)
class AnalysisPaths:
    output_dir: Path
    media_info: Path
    contact_sheet: Path
    sample_frames_dir: Path
    timeline: Path
    quality_report: Path
    production_template: Path
    scorecard: Path

    @classmethod
    def for_output_dir(cls, output_dir: Path) -> "AnalysisPaths":
        return cls(
            output_dir=output_dir,
            media_info=output_dir / "media_info.json",
            contact_sheet=output_dir / "contact_sheet.jpg",
            sample_frames_dir=output_dir / "sample_frames",
            timeline=output_dir / "timeline.md",
            quality_report=output_dir / "quality_report.md",
            production_template=output_dir / "production_template.md",
            scorecard=output_dir / "scorecard.md",
        )


@dataclass(frozen=True)
class AnalysisResult:
    media: MediaInfo
    paths: AnalysisPaths
    sample_frames: list[SampleFrame]


def _gcd(left: int, right: int) -> int:
    while right:
        left, right = right, left % right
    return max(1, abs(left))
```

Create `video_factory/analysis/__init__.py`:

```python
from .models import AnalysisPaths, AnalysisResult, MediaInfo, SampleFrame

__all__ = [
    "AnalysisPaths",
    "AnalysisResult",
    "MediaInfo",
    "SampleFrame",
]
```

- [ ] **Step 4: Run model tests and verify they pass**

Run:

```bash
python3 -m pytest tests/test_analysis.py -v
```

Expected: PASS for the three model tests.

- [ ] **Step 5: Commit**

This workspace is not currently a git repository. If implementation is later run inside a git repo, commit with:

```bash
git add video_factory/analysis/__init__.py video_factory/analysis/models.py tests/test_analysis.py
git commit -m "feat: add reference analysis models"
```

---

### Task 2: Media Probing

**Files:**
- Create: `video_factory/analysis/media.py`
- Modify: `video_factory/analysis/__init__.py`
- Test: `tests/test_analysis.py`

- [ ] **Step 1: Write failing media tests**

Append to `tests/test_analysis.py`:

```python
import json
import subprocess

import pytest

from video_factory.analysis import build_output_slug, probe_media


def test_build_output_slug_keeps_ascii_and_chinese_safe():
    assert build_output_slug(Path("/tmp/My Video!.mp4")) == "my-video"
    assert build_output_slug(Path("/tmp/下载.mp4")) == "video"


def test_probe_media_parses_ffprobe_json(monkeypatch, tmp_path):
    source = tmp_path / "reference.mp4"
    source.write_bytes(b"not a real video")
    payload = {
        "format": {"duration": "355.114", "bit_rate": "13277000"},
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "h264",
                "width": 3840,
                "height": 2160,
                "r_frame_rate": "60/1",
            },
            {
                "codec_type": "audio",
                "codec_name": "aac",
                "sample_rate": "48000",
            },
        ],
    }

    def fake_run(command, capture_output, text, check):
        assert command[:2] == ["ffprobe", "-v"]
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr("video_factory.analysis.media.subprocess.run", fake_run)

    media = probe_media(source)

    assert media.duration == 355.114
    assert media.width == 3840
    assert media.height == 2160
    assert media.fps == 60.0
    assert media.video_codec == "h264"
    assert media.audio_codec == "aac"
    assert media.audio_sample_rate == 48000
    assert media.bit_rate == 13277000


def test_probe_media_requires_existing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        probe_media(tmp_path / "missing.mp4")
```

- [ ] **Step 2: Run media tests and verify they fail**

Run:

```bash
python3 -m pytest tests/test_analysis.py -v
```

Expected: FAIL with import errors for `build_output_slug` and `probe_media`.

- [ ] **Step 3: Implement media probing**

Create `video_factory/analysis/media.py`:

```python
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from .models import MediaInfo


def build_output_slug(source_path: Path) -> str:
    stem = source_path.stem.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", stem).strip("-")
    return slug or "video"


def probe_media(source_path: Path) -> MediaInfo:
    if not source_path.exists():
        raise FileNotFoundError(f"Reference video does not exist: {source_path}")

    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration,bit_rate",
        "-show_streams",
        "-of",
        "json",
        str(source_path),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=True)
    payload = json.loads(completed.stdout)
    streams = payload.get("streams", [])
    video_stream = _first_stream(streams, "video")
    audio_stream = _first_stream(streams, "audio")
    format_info = payload.get("format", {})

    return MediaInfo(
        source_path=source_path,
        duration=_float_value(format_info.get("duration")),
        width=_int_value(video_stream.get("width")),
        height=_int_value(video_stream.get("height")),
        fps=_parse_rate(str(video_stream.get("r_frame_rate", "0/1"))),
        video_codec=str(video_stream.get("codec_name", "")),
        audio_codec=str(audio_stream.get("codec_name", "")),
        audio_sample_rate=_int_value(audio_stream.get("sample_rate")),
        bit_rate=_int_value(format_info.get("bit_rate")),
    )


def _first_stream(streams: list[dict[str, object]], codec_type: str) -> dict[str, object]:
    for stream in streams:
        if stream.get("codec_type") == codec_type:
            return stream
    return {}


def _parse_rate(rate: str) -> float:
    if "/" not in rate:
        return _float_value(rate)
    numerator_text, denominator_text = rate.split("/", 1)
    numerator = _float_value(numerator_text)
    denominator = _float_value(denominator_text)
    if denominator == 0:
        return 0.0
    return numerator / denominator


def _int_value(value: object) -> int:
    try:
        return int(float(str(value)))
    except (TypeError, ValueError):
        return 0


def _float_value(value: object) -> float:
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return 0.0
```

Modify `video_factory/analysis/__init__.py`:

```python
from .media import build_output_slug, probe_media
from .models import AnalysisPaths, AnalysisResult, MediaInfo, SampleFrame

__all__ = [
    "AnalysisPaths",
    "AnalysisResult",
    "MediaInfo",
    "SampleFrame",
    "build_output_slug",
    "probe_media",
]
```

- [ ] **Step 4: Run media tests and verify they pass**

Run:

```bash
python3 -m pytest tests/test_analysis.py -v
```

Expected: PASS for model and media tests.

- [ ] **Step 5: Commit**

If inside a git repo:

```bash
git add video_factory/analysis/__init__.py video_factory/analysis/media.py tests/test_analysis.py
git commit -m "feat: add media probing for reference analysis"
```

---

### Task 3: Frame Sampling Commands

**Files:**
- Create: `video_factory/analysis/frames.py`
- Modify: `video_factory/analysis/__init__.py`
- Test: `tests/test_analysis.py`

- [ ] **Step 1: Write failing frame-sampling tests**

Append to `tests/test_analysis.py`:

```python
from video_factory.analysis import (
    build_contact_sheet_command,
    build_sample_frame_command,
    choose_sample_timestamps,
)


def test_choose_sample_timestamps_covers_start_middle_and_end():
    timestamps = choose_sample_timestamps(duration=100.0, count=5)

    assert timestamps == [3.0, 26.5, 50.0, 73.5, 97.0]


def test_choose_sample_timestamps_handles_short_video():
    timestamps = choose_sample_timestamps(duration=12.0, count=5)

    assert timestamps == [1.0, 3.5, 6.0, 8.5, 11.0]


def test_build_contact_sheet_command_uses_even_sampling(tmp_path):
    source = tmp_path / "reference.mp4"
    output = tmp_path / "contact_sheet.jpg"

    command = build_contact_sheet_command(source, output, duration=240.0)

    joined = " ".join(command)
    assert command[:3] == ["ffmpeg", "-hide_banner", "-y"]
    assert str(source) in command
    assert str(output) == command[-1]
    assert "fps=1/30" in joined
    assert "tile=4x2" in joined


def test_build_sample_frame_command_seeks_to_timestamp(tmp_path):
    source = tmp_path / "reference.mp4"
    output = tmp_path / "frame.jpg"

    command = build_sample_frame_command(source, output, timestamp=12.5)

    assert command[:3] == ["ffmpeg", "-hide_banner", "-y"]
    assert "-ss" in command
    assert "12.500" in command
    assert str(output) == command[-1]
```

- [ ] **Step 2: Run frame tests and verify they fail**

Run:

```bash
python3 -m pytest tests/test_analysis.py -v
```

Expected: FAIL with import errors for frame helper functions.

- [ ] **Step 3: Implement frame sampling helpers**

Create `video_factory/analysis/frames.py`:

```python
from __future__ import annotations

import subprocess
from pathlib import Path

from .models import MediaInfo, SampleFrame


def choose_sample_timestamps(duration: float, count: int = 8) -> list[float]:
    if count <= 0:
        return []
    if duration <= 2:
        return [0.0]
    start = round(min(3.0, max(0.0, duration * 0.08)), 1)
    end = round(max(start, duration - min(3.0, max(1.0, duration * 0.08))), 1)
    if count == 1:
        return [round(duration / 2, 1)]
    step = (end - start) / (count - 1)
    return [round(start + step * index, 1) for index in range(count)]


def build_contact_sheet_command(source_path: Path, output_path: Path, duration: float) -> list[str]:
    interval = max(1, int(round(duration / 8)))
    return [
        "ffmpeg",
        "-hide_banner",
        "-y",
        "-i",
        str(source_path),
        "-vf",
        f"fps=1/{interval},scale=320:-1,tile=4x2",
        "-frames:v",
        "1",
        str(output_path),
    ]


def build_sample_frame_command(source_path: Path, output_path: Path, timestamp: float) -> list[str]:
    return [
        "ffmpeg",
        "-hide_banner",
        "-y",
        "-ss",
        f"{timestamp:.3f}",
        "-i",
        str(source_path),
        "-frames:v",
        "1",
        str(output_path),
    ]


def render_contact_sheet(media: MediaInfo, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(build_contact_sheet_command(media.source_path, output_path, media.duration), check=True)


def render_sample_frames(media: MediaInfo, output_dir: Path, count: int = 8) -> list[SampleFrame]:
    output_dir.mkdir(parents=True, exist_ok=True)
    frames: list[SampleFrame] = []
    for timestamp in choose_sample_timestamps(media.duration, count=count):
        name = f"frame_{int(timestamp):03d}_{int(round((timestamp % 1) * 100)):02d}.jpg"
        path = output_dir / name
        subprocess.run(build_sample_frame_command(media.source_path, path, timestamp), check=True)
        frames.append(SampleFrame(timestamp=timestamp, path=path))
    return frames
```

Modify `video_factory/analysis/__init__.py`:

```python
from .frames import (
    build_contact_sheet_command,
    build_sample_frame_command,
    choose_sample_timestamps,
    render_contact_sheet,
    render_sample_frames,
)
from .media import build_output_slug, probe_media
from .models import AnalysisPaths, AnalysisResult, MediaInfo, SampleFrame

__all__ = [
    "AnalysisPaths",
    "AnalysisResult",
    "MediaInfo",
    "SampleFrame",
    "build_contact_sheet_command",
    "build_output_slug",
    "build_sample_frame_command",
    "choose_sample_timestamps",
    "probe_media",
    "render_contact_sheet",
    "render_sample_frames",
]
```

- [ ] **Step 4: Run frame tests and verify they pass**

Run:

```bash
python3 -m pytest tests/test_analysis.py -v
```

Expected: PASS for model, media, and frame tests.

- [ ] **Step 5: Commit**

If inside a git repo:

```bash
git add video_factory/analysis/__init__.py video_factory/analysis/frames.py tests/test_analysis.py
git commit -m "feat: add reference frame sampling"
```

---

### Task 4: Report Writers

**Files:**
- Create: `video_factory/analysis/reports.py`
- Modify: `video_factory/analysis/__init__.py`
- Test: `tests/test_analysis.py`

- [ ] **Step 1: Write failing report tests**

Append to `tests/test_analysis.py`:

```python
from video_factory.analysis import (
    AnalysisPaths,
    MediaInfo,
    SampleFrame,
    build_production_template_markdown,
    build_quality_report_markdown,
    build_scorecard_markdown,
    build_timeline_markdown,
    write_report_artifacts,
)


def _test_media(tmp_path):
    return MediaInfo(
        source_path=tmp_path / "reference.mp4",
        duration=120.0,
        width=1920,
        height=1080,
        fps=30.0,
        video_codec="h264",
        audio_codec="aac",
        audio_sample_rate=48000,
        bit_rate=5000000,
    )


def test_build_timeline_markdown_lists_review_frames(tmp_path):
    media = _test_media(tmp_path)
    frames = [
        SampleFrame(timestamp=3.0, path=tmp_path / "sample_frames/frame_003_00.jpg"),
        SampleFrame(timestamp=60.0, path=tmp_path / "sample_frames/frame_060_00.jpg"),
    ]

    markdown = build_timeline_markdown(media, frames)

    assert "# Reference Video Timeline Seed" in markdown
    assert "| 00:03.00 |" in markdown
    assert "sample_frames/frame_003_00.jpg" in markdown
    assert "Segment function" in markdown


def test_quality_report_contains_required_analysis_sections(tmp_path):
    media = _test_media(tmp_path)
    markdown = build_quality_report_markdown(media)

    required = [
        "基础信息",
        "时间线拆解",
        "镜头与画面系统",
        "字幕系统",
        "声音与口播",
        "剪辑节奏",
        "真实感来源",
        "可复刻规则",
        "失败样片对照",
    ]
    for heading in required:
        assert heading in markdown
    assert "本报告需要结合抽帧由 Codex 进行专家判断" in markdown


def test_template_and_scorecard_have_actionable_sections(tmp_path):
    media = _test_media(tmp_path)
    template = build_production_template_markdown(media)
    scorecard = build_scorecard_markdown(media)

    assert "开头结构规则" in template
    assert "素材规则" in template
    assert "口播规则" in template
    assert "| 维度 | 满分 | 不合格表现 |" in scorecard
    assert "语义一致性" in scorecard


def test_write_report_artifacts_creates_markdown_files(tmp_path):
    media = _test_media(tmp_path)
    frames = [SampleFrame(timestamp=3.0, path=tmp_path / "sample_frames/frame_003_00.jpg")]
    paths = AnalysisPaths.for_output_dir(tmp_path)

    write_report_artifacts(media, frames, paths)

    assert paths.timeline.exists()
    assert paths.quality_report.exists()
    assert paths.production_template.exists()
    assert paths.scorecard.exists()
```

- [ ] **Step 2: Run report tests and verify they fail**

Run:

```bash
python3 -m pytest tests/test_analysis.py -v
```

Expected: FAIL with import errors for report functions.

- [ ] **Step 3: Implement report writers**

Create `video_factory/analysis/reports.py`:

```python
from __future__ import annotations

from pathlib import Path

from .models import AnalysisPaths, MediaInfo, SampleFrame


def build_timeline_markdown(media: MediaInfo, frames: list[SampleFrame]) -> str:
    lines = [
        "# Reference Video Timeline Seed",
        "",
        "This timeline is an evidence scaffold. Codex should replace the review notes with expert observations after inspecting the frames.",
        "",
        "| Time | Frame | Segment function | Visual evidence | Audio/subtitle notes |",
        "| --- | --- | --- | --- | --- |",
    ]
    for frame in frames:
        relative_path = _relative_to_parent(frame.path, media.source_path.parent)
        lines.append(
            f"| {frame.label} | `{relative_path}` | Segment function | Describe visible shot/UI/subtitle state | Describe narration or subtitle rhythm |"
        )
    lines.append("")
    return "\n".join(lines)


def build_quality_report_markdown(media: MediaInfo) -> str:
    return "\n".join(
        [
            "# Reference Video Quality Report",
            "",
            "本报告需要结合抽帧由 Codex 进行专家判断。自动媒体信息只提供事实，不替代审美判断。",
            "",
            "## 基础信息",
            "",
            f"- Source: `{media.source_path}`",
            f"- Duration: {media.duration:.2f}s",
            f"- Resolution: {media.width}x{media.height}",
            f"- Aspect ratio: {media.aspect_ratio}",
            f"- Orientation: {media.orientation}",
            f"- FPS: {media.fps:.2f}",
            f"- Video codec: {media.video_codec}",
            f"- Audio codec: {media.audio_codec}",
            "",
            "## 时间线拆解",
            "",
            "按 3 到 10 秒粒度描述每段功能：开头钩子、观点提出、证据展示、转折、演示、结论或互动引导。",
            "",
            "## 镜头与画面系统",
            "",
            "分析真人出镜、B-roll、屏幕录制、图表、字幕覆盖、主视觉统一性，以及画面是否服务口播。",
            "",
            "## 字幕系统",
            "",
            "分析字体、位置、颜色、双语结构、重点词高亮、字幕出现节奏，以及字幕是否和画面冲突。",
            "",
            "## 声音与口播",
            "",
            "分析语速、停顿、情绪、句子长度、真人口语感、背景音乐和音效。",
            "",
            "## 剪辑节奏",
            "",
            "分析镜头切换、画面变化点、字幕变化点、信息密度、拖沓区和节奏峰值。",
            "",
            "## 真实感来源",
            "",
            "指出视频为什么不像 AI：动作同步、素材可信、语言自然、界面统一、信息具体、细节真实。",
            "",
            "## 可复刻规则",
            "",
            "总结脚本、素材、字幕、声音、剪辑、包装和质检规则。",
            "",
            "## 失败样片对照",
            "",
            "对照当前失败样片，指出 AI 味来自哪一层：语义、素材、字幕、声音、包装、节奏或质检缺失。",
            "",
        ]
    )


def build_production_template_markdown(media: MediaInfo) -> str:
    return "\n".join(
        [
            "# Production Template Draft",
            "",
            f"Reference: `{media.source_path}`",
            "",
            "## 开头结构规则",
            "",
            "记录前 3 到 5 秒如何建立冲突、利益点或悬念。",
            "",
            "## 叙事结构规则",
            "",
            "记录观点、证据、转折、结论和互动引导的顺序。",
            "",
            "## 素材规则",
            "",
            "记录需要哪些素材类型、素材与口播如何同步、哪些素材不能替代。",
            "",
            "## 字幕规则",
            "",
            "记录字幕位置、字号、颜色、层级、双语规则和避让区域。",
            "",
            "## 口播规则",
            "",
            "记录语速、句长、停顿、重音、口语化程度和情绪变化。",
            "",
            "## 剪辑规则",
            "",
            "记录镜头变化频率、转场方式、信息密度和拖沓风险。",
            "",
            "## 禁止规则",
            "",
            "记录会显著增加 AI 味或廉价感的做法。",
            "",
        ]
    )


def build_scorecard_markdown(media: MediaInfo) -> str:
    return "\n".join(
        [
            "# Reference-Derived Quality Scorecard",
            "",
            f"Reference: `{media.source_path}`",
            "",
            "| 维度 | 满分 | 不合格表现 |",
            "| --- | ---: | --- |",
            "| 语义一致性 | 20 | 口播主题和画面主题不一致，或硬改旧视频语义 |",
            "| 素材可信度 | 15 | 素材假、丑、风格混乱，或无法支撑口播 |",
            "| 口播自然度 | 15 | 句子像模型作文，缺少停顿、重点和真人判断 |",
            "| 字幕干净度 | 15 | 字幕重叠、遮挡、旧字幕残留明显，或层级混乱 |",
            "| 剪辑节奏 | 15 | 长时间无信息变化，或画面变化与口播不同步 |",
            "| 包装统一性 | 10 | 两套 UI 系统互相抢占注意力 |",
            "| 整体真实感 | 10 | 观众能明显感到模板化、拼贴感或 AI 痕迹 |",
            "",
            "总分低于 80 的样片不得进入发布候选。",
            "",
        ]
    )


def write_report_artifacts(media: MediaInfo, frames: list[SampleFrame], paths: AnalysisPaths) -> None:
    paths.output_dir.mkdir(parents=True, exist_ok=True)
    paths.timeline.write_text(build_timeline_markdown(media, frames), encoding="utf-8")
    paths.quality_report.write_text(build_quality_report_markdown(media), encoding="utf-8")
    paths.production_template.write_text(build_production_template_markdown(media), encoding="utf-8")
    paths.scorecard.write_text(build_scorecard_markdown(media), encoding="utf-8")


def _relative_to_parent(path: Path, parent: Path) -> Path:
    try:
        return path.relative_to(parent)
    except ValueError:
        return path
```

Modify `video_factory/analysis/__init__.py`:

```python
from .frames import (
    build_contact_sheet_command,
    build_sample_frame_command,
    choose_sample_timestamps,
    render_contact_sheet,
    render_sample_frames,
)
from .media import build_output_slug, probe_media
from .models import AnalysisPaths, AnalysisResult, MediaInfo, SampleFrame
from .reports import (
    build_production_template_markdown,
    build_quality_report_markdown,
    build_scorecard_markdown,
    build_timeline_markdown,
    write_report_artifacts,
)

__all__ = [
    "AnalysisPaths",
    "AnalysisResult",
    "MediaInfo",
    "SampleFrame",
    "build_contact_sheet_command",
    "build_output_slug",
    "build_production_template_markdown",
    "build_quality_report_markdown",
    "build_sample_frame_command",
    "build_scorecard_markdown",
    "build_timeline_markdown",
    "choose_sample_timestamps",
    "probe_media",
    "render_contact_sheet",
    "render_sample_frames",
    "write_report_artifacts",
]
```

- [ ] **Step 4: Run report tests and verify they pass**

Run:

```bash
python3 -m pytest tests/test_analysis.py -v
```

Expected: PASS for all analysis tests so far.

- [ ] **Step 5: Commit**

If inside a git repo:

```bash
git add video_factory/analysis/__init__.py video_factory/analysis/reports.py tests/test_analysis.py
git commit -m "feat: add reference analysis report writers"
```

---

### Task 5: Analysis Runner

**Files:**
- Create: `video_factory/analysis/runner.py`
- Modify: `video_factory/analysis/__init__.py`
- Test: `tests/test_analysis.py`

- [ ] **Step 1: Write failing runner test**

Append to `tests/test_analysis.py`:

```python
from video_factory.analysis import analyze_reference_video


def test_analyze_reference_video_writes_expected_artifacts(monkeypatch, tmp_path):
    source = tmp_path / "reference.mp4"
    source.write_bytes(b"not a real video")
    output = tmp_path / "analysis"
    media = MediaInfo(
        source_path=source,
        duration=60.0,
        width=1920,
        height=1080,
        fps=30.0,
        video_codec="h264",
        audio_codec="aac",
        audio_sample_rate=48000,
        bit_rate=5000000,
    )
    calls = []

    def fake_probe(path):
        calls.append(("probe", path))
        return media

    def fake_contact_sheet(probed_media, output_path):
        calls.append(("contact", output_path))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"jpg")

    def fake_frames(probed_media, output_dir, count):
        calls.append(("frames", output_dir, count))
        output_dir.mkdir(parents=True, exist_ok=True)
        frame = output_dir / "frame_003_00.jpg"
        frame.write_bytes(b"jpg")
        return [SampleFrame(timestamp=3.0, path=frame)]

    monkeypatch.setattr("video_factory.analysis.runner.probe_media", fake_probe)
    monkeypatch.setattr("video_factory.analysis.runner.render_contact_sheet", fake_contact_sheet)
    monkeypatch.setattr("video_factory.analysis.runner.render_sample_frames", fake_frames)

    result = analyze_reference_video(source, output_dir=output, sample_count=1)

    assert result.media == media
    assert result.paths.media_info.exists()
    assert result.paths.contact_sheet.exists()
    assert result.paths.quality_report.exists()
    assert result.sample_frames[0].timestamp == 3.0
    assert calls == [
        ("probe", source),
        ("contact", output / "contact_sheet.jpg"),
        ("frames", output / "sample_frames", 1),
    ]
```

- [ ] **Step 2: Run runner test and verify it fails**

Run:

```bash
python3 -m pytest tests/test_analysis.py -v
```

Expected: FAIL with import error for `analyze_reference_video`.

- [ ] **Step 3: Implement runner**

Create `video_factory/analysis/runner.py`:

```python
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from .frames import render_contact_sheet, render_sample_frames
from .media import build_output_slug, probe_media
from .models import AnalysisPaths, AnalysisResult
from .reports import write_report_artifacts


def analyze_reference_video(
    source_path: Path,
    output_dir: Optional[Path] = None,
    sample_count: int = 8,
) -> AnalysisResult:
    media = probe_media(source_path)
    resolved_output = output_dir or Path("video_factory/output/analysis") / build_output_slug(source_path)
    paths = AnalysisPaths.for_output_dir(resolved_output)
    paths.output_dir.mkdir(parents=True, exist_ok=True)
    paths.sample_frames_dir.mkdir(parents=True, exist_ok=True)

    paths.media_info.write_text(
        json.dumps(media.to_json_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    render_contact_sheet(media, paths.contact_sheet)
    frames = render_sample_frames(media, paths.sample_frames_dir, count=sample_count)
    write_report_artifacts(media, frames, paths)
    return AnalysisResult(media=media, paths=paths, sample_frames=frames)
```

Modify `video_factory/analysis/__init__.py`:

```python
from .frames import (
    build_contact_sheet_command,
    build_sample_frame_command,
    choose_sample_timestamps,
    render_contact_sheet,
    render_sample_frames,
)
from .media import build_output_slug, probe_media
from .models import AnalysisPaths, AnalysisResult, MediaInfo, SampleFrame
from .reports import (
    build_production_template_markdown,
    build_quality_report_markdown,
    build_scorecard_markdown,
    build_timeline_markdown,
    write_report_artifacts,
)
from .runner import analyze_reference_video

__all__ = [
    "AnalysisPaths",
    "AnalysisResult",
    "MediaInfo",
    "SampleFrame",
    "analyze_reference_video",
    "build_contact_sheet_command",
    "build_output_slug",
    "build_production_template_markdown",
    "build_quality_report_markdown",
    "build_sample_frame_command",
    "build_scorecard_markdown",
    "build_timeline_markdown",
    "choose_sample_timestamps",
    "probe_media",
    "render_contact_sheet",
    "render_sample_frames",
    "write_report_artifacts",
]
```

- [ ] **Step 4: Run runner tests and verify they pass**

Run:

```bash
python3 -m pytest tests/test_analysis.py -v
```

Expected: PASS for all analysis tests.

- [ ] **Step 5: Commit**

If inside a git repo:

```bash
git add video_factory/analysis/__init__.py video_factory/analysis/runner.py tests/test_analysis.py
git commit -m "feat: orchestrate reference video analysis"
```

---

### Task 6: CLI Entrypoint

**Files:**
- Create: `video_factory/analysis/__main__.py`
- Test: `tests/test_analysis.py`

- [ ] **Step 1: Write failing CLI tests**

Append to `tests/test_analysis.py`:

```python
from video_factory.analysis.__main__ import parse_args


def test_analysis_cli_parse_args(tmp_path):
    source = tmp_path / "reference.mp4"
    output = tmp_path / "out"

    args = parse_args(["--input", str(source), "--output", str(output), "--sample-count", "4"])

    assert args.input == source
    assert args.output == output
    assert args.sample_count == 4
```

- [ ] **Step 2: Run CLI tests and verify they fail**

Run:

```bash
python3 -m pytest tests/test_analysis.py -v
```

Expected: FAIL with `ModuleNotFoundError` for `video_factory.analysis.__main__`.

- [ ] **Step 3: Implement CLI entrypoint**

Create `video_factory/analysis/__main__.py`:

```python
from __future__ import annotations

import argparse
from pathlib import Path

from .runner import analyze_reference_video


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze a local reference video for production quality.")
    parser.add_argument("--input", type=Path, required=True, help="Local reference video file.")
    parser.add_argument("--output", type=Path, default=None, help="Analysis output directory.")
    parser.add_argument("--sample-count", type=int, default=8, help="Number of review frames to extract.")
    return parser.parse_args(argv)


def main() -> None:
    args = parse_args()
    result = analyze_reference_video(args.input, output_dir=args.output, sample_count=args.sample_count)
    print(f"media_info: {result.paths.media_info}")
    print(f"contact_sheet: {result.paths.contact_sheet}")
    print(f"timeline: {result.paths.timeline}")
    print(f"quality_report: {result.paths.quality_report}")
    print(f"production_template: {result.paths.production_template}")
    print(f"scorecard: {result.paths.scorecard}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run CLI tests and verify they pass**

Run:

```bash
python3 -m pytest tests/test_analysis.py -v
```

Expected: PASS for all analysis tests.

- [ ] **Step 5: Smoke test the CLI against the real reference file**

Run:

```bash
python3 -m video_factory.analysis --input /Users/king/Downloads/下载.mp4 --output video_factory/output/analysis/download-reference --sample-count 8
```

Expected output includes:

```text
media_info: video_factory/output/analysis/download-reference/media_info.json
contact_sheet: video_factory/output/analysis/download-reference/contact_sheet.jpg
quality_report: video_factory/output/analysis/download-reference/quality_report.md
```

Expected files exist:

```text
video_factory/output/analysis/download-reference/media_info.json
video_factory/output/analysis/download-reference/contact_sheet.jpg
video_factory/output/analysis/download-reference/sample_frames/
video_factory/output/analysis/download-reference/timeline.md
video_factory/output/analysis/download-reference/quality_report.md
video_factory/output/analysis/download-reference/production_template.md
video_factory/output/analysis/download-reference/scorecard.md
```

- [ ] **Step 6: Commit**

If inside a git repo:

```bash
git add video_factory/analysis/__main__.py tests/test_analysis.py
git commit -m "feat: add reference analysis cli"
```

---

### Task 7: Documentation

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Update README with analysis workflow**

Add this section before `## Next Direction` in `README.md`:

````markdown
## Reference Quality Analysis

The first Video Quality Lab workflow analyzes a local reference video before producing new videos. It extracts media facts, a contact sheet, sample frames, and markdown files for Codex-assisted expert review.

Run:

```bash
python3 -m video_factory.analysis \
  --input /Users/king/Downloads/下载.mp4 \
  --output video_factory/output/analysis/download-reference \
  --sample-count 8
```

Outputs:

- `media_info.json`: ffprobe-derived media facts.
- `contact_sheet.jpg`: eight-frame overview of the reference.
- `sample_frames/`: individual review frames.
- `timeline.md`: timeline scaffold for expert review.
- `quality_report.md`: quality analysis scaffold.
- `production_template.md`: production rules scaffold.
- `scorecard.md`: quality scoring scaffold.

The CLI does not claim to fully understand creative quality by itself. Codex should inspect the frames and fill the report with expert observations before using the template to make new videos.
````

- [ ] **Step 2: Run full tests**

Run:

```bash
python3 -m pytest
```

Expected: all tests pass.

- [ ] **Step 3: Commit**

If inside a git repo:

```bash
git add README.md
git commit -m "docs: document reference quality analysis workflow"
```

---

### Task 8: First Reference Expert Report

**Files:**
- Modify after CLI smoke test creates them:
  - `video_factory/output/analysis/download-reference/timeline.md`
  - `video_factory/output/analysis/download-reference/quality_report.md`
  - `video_factory/output/analysis/download-reference/production_template.md`
  - `video_factory/output/analysis/download-reference/scorecard.md`

- [ ] **Step 1: Inspect generated evidence**

Open:

```text
video_factory/output/analysis/download-reference/contact_sheet.jpg
video_factory/output/analysis/download-reference/sample_frames/
video_factory/output/analysis/download-reference/media_info.json
```

Expected: the contact sheet and sample frames show the reference video's changing UI, subtitles, presenter framing, and screen-recording sections.

- [ ] **Step 2: Fill `timeline.md` with expert observations**

Replace each row's generic notes with direct observations. Example format:

```markdown
| Time | Frame | Segment function | Visual evidence | Audio/subtitle notes |
| --- | --- | --- | --- | --- |
| 00:03.00 | `sample_frames/frame_003_00.jpg` | Hook/options setup | Presenter is centered-right; three-option UI anchors the premise; dark room and neon UI establish tech tone. | Large Chinese subtitle carries the spoken punchline; English subtitle supports but stays secondary. |
```

- [ ] **Step 3: Fill `quality_report.md`**

The report must explicitly answer these points:

```markdown
## 原视频高级感来源

- UI、字幕、真人画面、屏幕录制都围绕同一主题服务，没有第二套视觉语言抢戏。
- 真人动作、口播、字幕变化在同一节奏里，保留真实拍摄的不完美细节。
- 双语字幕有清晰主次，中文负责信息推进，英文负责补充，不互相争抢。

## 之前失败样片为什么 AI 味重

- 新主题和原画面语义不一致，把 AI Skill/流量主题的母片硬改成足球预测。
- 在原片已有完整 UI 系统上叠加世界杯预测模板，导致两套包装冲突。
- TTS 文案太像模型解释，没有真人博主的取舍、犹豫、重音和态度。
- 字幕遮罩只能压暗旧字幕，不能让旧内容语义真正消失。
```

- [ ] **Step 4: Fill `production_template.md`**

Add rules specific enough to guide the next sample:

```markdown
## 必须遵守

- 先确定母片/素材和脚本属于同一主题，再剪辑。
- 如果参考视频已有完整 UI 系统，新视频只能复用其结构，不能再叠另一套大 UI。
- 每 3 到 8 秒必须有一次信息状态变化：镜头、字幕重点、屏幕内容或图表状态。
- 口播每段必须有明确观点句，避免连续抽象解释。

## 禁止

- 禁止用与主题无关的旧视频硬改新话题。
- 禁止用低质量生成图假装赛事素材。
- 禁止让字幕、英文翻译、旧字幕三层同时出现。
```

- [ ] **Step 5: Fill `scorecard.md` with reference-specific failure examples**

Keep the 100-point table and add this section:

```markdown
## 当前失败样片扣分示例

- 语义一致性：母片讲 AI Skill/流量，新口播讲足球预测，严重不一致。
- 包装统一性：原 UI 与世界杯 UI 并存，观众会感到模板叠模板。
- 口播自然度：过多“模型、变量、分支”表达，缺少真人观点和情绪。
```

- [ ] **Step 6: Verify report usefulness**

Read the four markdown files and confirm they can answer:

```text
1. 原视频为什么好？
2. 失败样片为什么差？
3. 下一条样片制作前必须遵守什么规则？
4. 下一条样片怎么评分？
```

Expected: each question has concrete answers, not only abstract words like “高级”“真实”“节奏好”.

- [ ] **Step 7: Commit**

If inside a git repo:

```bash
git add video_factory/output/analysis/download-reference
git commit -m "docs: add first reference quality analysis"
```

---

## Final Verification

- [ ] Run unit tests:

```bash
python3 -m pytest tests/test_analysis.py -v
```

Expected: all analysis tests pass.

- [ ] Run full test suite:

```bash
python3 -m pytest
```

Expected: all tests pass.

- [ ] Run real reference analysis:

```bash
python3 -m video_factory.analysis --input /Users/king/Downloads/下载.mp4 --output video_factory/output/analysis/download-reference --sample-count 8
```

Expected: command exits successfully and writes all output files.

- [ ] Visually inspect:

```text
video_factory/output/analysis/download-reference/contact_sheet.jpg
video_factory/output/analysis/download-reference/sample_frames/
```

Expected: frames are readable enough for expert review.

- [ ] Read:

```text
video_factory/output/analysis/download-reference/quality_report.md
video_factory/output/analysis/download-reference/production_template.md
video_factory/output/analysis/download-reference/scorecard.md
```

Expected: the documents identify the reference video's quality sources and explain the previous failed sample's AI-heavy feel.

---

## Self-Review Notes

Spec coverage:

- Local-video first version is covered by Tasks 2, 3, 5, and 6.
- Media information, contact sheet, sample frames, timeline, quality report, production template, and scorecard outputs are covered by Tasks 1 through 5.
- Expert-analysis workflow is covered by Task 8.
- The non-goals are preserved: no Douyin scraping, no platform account access, no batch video generation, no automatic publication.

Type consistency:

- Public API uses `MediaInfo`, `SampleFrame`, `AnalysisPaths`, `AnalysisResult`, and `analyze_reference_video`.
- CLI calls the same runner used by tests.

Red-flag scan:

- No implementation step relies on undefined functions after its task.
- Report scaffold text intentionally marks expert-review sections as review instructions, not unfinished implementation.
