# Codex 自动化 AI 混剪短视频工厂

这个目录提供一个独立的视频生成流水线，不依赖现有股票业务代码。发布版面向“创作者变现 / 流量分账 / 涨粉教程”内容，生成 45 秒、1080x1920、30fps 的抖音竖屏短视频。

## 生成发布版

不需要 OpenAI key 的推荐方式，使用 `edge-tts`：

```bash
python3 -m pip install edge-tts
python3 -m video_factory --tts-provider edge --output video_factory/output/release-edge
```

默认中文声音为 `zh-CN-XiaoxiaoNeural`。也可以指定其他 Edge TTS 声音：

```bash
python3 -m video_factory \
  --tts-provider edge \
  --voice zh-CN-YunxiNeural \
  --output video_factory/output/release-edge
```

Edge TTS 默认使用 `--edge-rate +20%`，让当前 45 秒脚本更接近短视频口播节奏；需要调整时可以传 `--edge-rate +10%` 或 `--edge-rate +30%`。

使用 OpenAI Speech API：

```bash
OPENAI_API_KEY=your_key python3 -m video_factory --output video_factory/output/release
```

默认使用 OpenAI Speech API：

- model: `gpt-4o-mini-tts`
- voice: `marin`
- output: `voiceover.wav`

如果没有 `OPENAI_API_KEY`，发布版会失败，不会伪装成真人口播成片。确实需要非发布级预览时，显式开启兜底音轨：

```bash
python3 -m video_factory --allow-fallback --output video_factory/output/release-preview
```

使用本地配音文件：

```bash
python3 -m video_factory \
  --tts-provider file \
  --audio-file /path/to/voiceover.wav \
  --output video_factory/output/release-with-local-audio
```

只生成脚本、分镜、提示词和字幕：

```bash
python3 -m video_factory --script-only --output video_factory/output/script-pack
```

## 输出文件

- `script.md`：45 秒口播脚本和时间段。
- `storyboard.json`：机器可读分镜。
- `visual_prompts.md`：可交给外部 AI 视频/图片模型升级画面的提示词。
- `subtitles.srt`：字幕时间轴。
- `voiceover.wav`：OpenAI TTS 或本地配音文件转换后的口播音频。
- `frames/`：每个分镜的动画关键帧。
- `cover.png`：首帧封面。
- `render_report.json`：视频规格、TTS 来源、fallback 状态和产物清单。
- `release.mp4`：最终竖屏成片。

## 默认接口

- `topic`: `创作者变现：流量分账收入公开`
- `target_duration`: `45`
- `style`: `ai_realistic_montage`
- `goal`: `retention_growth`
- `reference_title`: `十万粉收入公开！教程：如何快速获得流量分账收入`
- `tts_provider`: `openai`
- `tts_model`: `gpt-4o-mini-tts`
- `voice`: `marin`

## 说明

发布版不复制对标账号画面，只复用“收益公开 + 教程拆解 + 快剪包装”的内容结构。画面采用可重复生成的模板动效：每个分镜会生成多张关键帧，包含转场进度、轻微位移、数据卡变化和字幕节奏包装。
