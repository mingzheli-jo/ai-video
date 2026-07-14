# AI Video Factory / AI 视频工厂

[简体中文](#简体中文) | [English](#english)

## 简体中文

AI Video Factory 是一个本地优先的视频发布增强工作台。它不是“自动搬运工具”，也不是“规避平台检测工具”；它的目标是帮助创作者把自己提供的参考视频、公开视频链接或本地视频，整理成更适合发布的成片包。

项目会尽量保持真实原片作为主体，围绕画质、比例、字幕、封面、剪辑节奏、质量检查和原创风险报告做增强。所有产物都保存在本地，方便复查和二次编辑。

## 项目定位

适合这些场景：

- 你有一条视频，想做发布前增强、清理、字幕和封面。
- 你有 YouTube 或抖音公开视频链接，想先下载到本地再处理。
- 你想保留原片主体，不想让成片变成明显的 AI 图片拼接视频。
- 你需要输出质量报告、原创风险报告、剪辑计划和素材使用记录。
- 你希望普通用户通过浏览器工作台使用，而不是直接面对一堆命令行参数。

不适合这些场景：

- 不适合用来规避版权、平台审核或相似度检测。
- 不适合处理无授权、私密、登录后可见、DRM 或受限制的视频。
- 目前不是完整的专业剪辑软件，也不是成熟的全自动原创视频生成系统。

## 核心能力

- 支持粘贴 YouTube / 抖音公开视频链接，并通过 `yt-dlp` 下载到本地任务目录。
- 支持本地视频路径输入。
- 默认保持原视频比例，避免把横屏、竖屏或特殊比例强行拉伸。
- 围绕真实原片做发布增强：剪辑、去重复、修复节奏、音频标准化、字幕和封面。
- 自动生成字幕，但只有在真实 transcript / OCR / SRT 证据存在时才会进入成片。
- 自动生成封面，优先使用原片真实画面和视频元信息，避免粗糙模板封面。
- 阻止 mock / 占位 AI 图片进入最终 `release.mp4`。
- 输出质量报告、相似度和复用风险估计、联系图、剪辑计划和语义时间线。
- 提供本地浏览器工作台，方便非技术用户操作。

## 当前状态

这是早期开源版本。它已经可以作为本地视频增强和发布前 QA 工具使用，但仍然处在快速迭代阶段。

已重点修正的问题：

- 生成视频被强行压缩得太短。
- 原视频比例被拉伸。
- AI 占位图、模板卡、内部章节标签进入成片。
- 字幕来源不可靠。
- 封面过于粗糙。
- README 和启动流程不清楚。

仍然需要继续提升：

- 更强的真实字幕 / ASR / OCR 能力。
- 更好的 images2 或其他图片生成 provider 接入。
- 更稳定的视频下载质量控制。
- 更好的封面审美和多版本候选。
- 更细的视频质量自检和可视化报告。

## 环境要求

- Python 3.9+
- `ffmpeg` 和 `ffprobe`
- macOS 或 Linux
- 可选：`yt-dlp`，用于下载公开视频
- 可选：`edge-tts`，用于草稿配音流程
- 可选：OCR / ASR provider，用于更强字幕识别

macOS 安装 ffmpeg：

```bash
brew install ffmpeg
```

## 快速启动

进入项目目录：

```bash
cd /path/to/video_factory_project
```

创建虚拟环境并安装依赖：

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -U pip
python3 -m pip install -e ".[dev,tts]"
```

启动本地工作台：

```bash
python3 -m video_factory.workbench --port 56080
```

浏览器打开：

```text
http://127.0.0.1:56080/
```

推荐使用浏览器工作台。你可以粘贴 YouTube / 抖音公开视频链接，也可以填写本地视频路径，然后点击生成按钮。

任务输出目录：

```text
video_factory/output/workbench/<job_id>/
```

## 命令行用法

增强本地视频：

```bash
python3 -m video_factory.replicate \
  --input "/absolute/path/to/source.mp4" \
  --mode creative-edit
```

更保守的真人剪辑模式：

```bash
python3 -m video_factory.replicate \
  --input "/absolute/path/to/source.mp4" \
  --mode human-edit
```

只生成脚本包：

```bash
python3 -m video_factory --script-only --output video_factory/output/script-pack
```

运行测试：

```bash
python3 -m pytest
```

## 主要产物

每个任务可能生成：

- `release.mp4`：最终成片
- `cover.png`：封面图
- `contact_sheet.jpg`：质检联系图
- `quality_report.json`：质量检查报告
- `originality_report.json`：原创风险和复用估计
- `creative_plan.json`：剪辑策略和片段选择
- `semantic_timeline.json`：内容结构和语义时间线
- `transcript_analysis.json`：字幕、OCR、SRT 等证据分析
- `caption_timeline.json` / `subtitles.srt`：字幕产物
- `source_download.json`：下载来源、平台、标题、缓存路径和下载状态

生成产物默认不会提交到 Git，见 `.gitignore`。

## 项目结构

```text
video_factory/
  workbench.py                 本地浏览器工作台
  replicate.py                 视频发布增强主流程
  source_download.py           YouTube / 抖音公开视频下载层
  creative.py                  基于原片的剪辑和片段规划
  originality.py               相似度和复用风险估计
  content.py                   视频帧和内容分析
  audio.py                     音频分析
  semantic.py                  语义时间线
  transcript.py                字幕 / SRT / OCR 证据层
  analysis/                    参考视频分析工具

tests/                         回归测试
tools/                         手动渲染和实验脚本
docs/superpowers/              设计记录和实施计划
references/                    小型参考材料
```

## 质量原则

当前产品方向会刻意收窄：

1. 用户提供或下载的真实视频是主体。
2. 系统负责修复、重排、剪辑、字幕、封面和质检。
3. AI 视觉素材必须是真正可发布的资产，不能是占位图。
4. mock 图片、模板卡和内部标签不能进入 `release.mp4`。
5. 字幕必须来自可靠证据，不能把章节角色或内部说明烧进视频。
6. 成片必须保持原视频比例，避免拉伸和假包装。
7. 长视频默认不能被无理由压缩成短摘要。

更多说明：

- [`VIDEO_QUALITY_STANDARD.md`](VIDEO_QUALITY_STANDARD.md)
- [`HUMAN_EDITING_PLAYBOOK.md`](HUMAN_EDITING_PLAYBOOK.md)
- [`VIDEO_FACTORY_WORKFLOW.md`](VIDEO_FACTORY_WORKFLOW.md)

## 合规说明

请只处理你拥有版权、获得授权、或有权参考和改编的视频。本项目的视频下载能力只面向公开可访问视频，不绕过登录、私密访问、DRM、地区限制或平台限制。

原创风险和相似度报告只是本地工程估计，目的是帮助你减少明显复用风险、提高内容质量，不保证任何平台一定通过审核。

## 开发

安装开发依赖：

```bash
python3 -m pip install -e ".[dev,tts]"
```

运行完整测试：

```bash
python3 -m pytest
```

发布改动前建议确认：

- 生成文件都在 `video_factory/output/` 下。
- 没有提交私密视频、Cookie、API Key 或账号数据。
- 没有提交大型源视频文件。
- 测试仍然通过。

## License

MIT License. See [`LICENSE`](LICENSE).

---

## English

AI Video Factory is a local-first video publishing enhancement workbench. It helps creators turn a reference video, public video URL, or local media file into a cleaner release package: edited video, cover image, subtitles, quality report, originality risk report, and review artifacts.

This project does **not** try to bypass platform detection. The goal is to improve video quality, preserve aspect ratio, document sources, avoid fake AI-looking inserts, and make release decisions more auditable.

## What It Does

- Downloads public reference videos from YouTube or Douyin with `yt-dlp`.
- Keeps the source aspect ratio instead of stretching every video into one fixed canvas.
- Builds source-guided edits for publishing enhancement.
- Normalizes audio and keeps the real source video as the main visual body.
- Generates subtitles only when real transcript/OCR/SRT evidence exists.
- Generates a simple cover from real frames and source metadata.
- Blocks mock or placeholder AI visuals from entering `release.mp4`.
- Produces quality reports, contact sheets, EDL notes, creative plans, and originality risk reports.
- Provides a local browser workbench for non-technical users.

## Quick Start

Install requirements:

```bash
brew install ffmpeg
```

Create a virtual environment and install the project:

```bash
cd /path/to/video_factory_project
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -U pip
python3 -m pip install -e ".[dev,tts]"
```

Start the local workbench:

```bash
python3 -m video_factory.workbench --port 56080
```

Open:

```text
http://127.0.0.1:56080/
```

The workbench is the recommended interface. Paste a YouTube/Douyin public video link or provide a local video path, then click the production button. Output files are written under:

```text
video_factory/output/workbench/<job_id>/
```

## Command Line Usage

Enhance a local video:

```bash
python3 -m video_factory.replicate \
  --input "/absolute/path/to/source.mp4" \
  --mode creative-edit
```

Use a more conservative edit:

```bash
python3 -m video_factory.replicate \
  --input "/absolute/path/to/source.mp4" \
  --mode human-edit
```

Run tests:

```bash
python3 -m pytest
```

## Legal Notes

Use this project only with content you own, licensed content, or content you have permission to reference or transform. The URL downloader only targets public videos and does not bypass login, private access, DRM, or platform restrictions.

The originality and similarity reports are engineering heuristics. They are meant to help creators reduce obvious reuse risk and improve quality, not to guarantee approval by any platform.
