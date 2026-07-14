# Batch Production Guide

批量生产链路：导入原片 → LLM 改造文案（7 种内容风格）→ 素材池自动拼装（4 种画幅 + BGM）→ 豆包配音 → Remotion 特效 → 逐句字幕 → 成片。

五个 CLI 各管一段，产物 JSON 串联，可单独跑、全链跑，或用 `batch` 一张任务清单批量跑。全链均在 Windows + Python 3.14 验证。

## Prerequisites

| 依赖 | 用途 | 检查命令 |
|---|---|---|
| Python 3.9+（本机 venv 在 `.venv/`） | 全部流水线 | `.\.venv\Scripts\python.exe --version` |
| ffmpeg / ffprobe（PATH） | 切片、拼接、探测 | `ffmpeg -version` |
| Node 18+ + npm（PATH，可选） | Remotion 特效层 | `node --version` |
| yt-dlp（PATH，可选） | 原片链接下载 | `yt-dlp --version` |

环境变量（按需设置，只从环境变量读，不落盘）：

```powershell
$env:OPENAI_API_KEY="sk-..."        # 文案改写（--provider openai，默认）
$env:ANTHROPIC_API_KEY="..."        # 文案改写（--provider anthropic）
$env:VOLC_TTS_APPID="..."           # 豆包 TTS
$env:VOLC_TTS_TOKEN="..."           # 豆包 TTS
```

首次使用 Remotion 特效层需安装依赖：

```powershell
cd remotion; npm install; cd ..
```

## Stage 1: Copy Rewrite（文案改造）

输入：**原片视频/音频直接进**（`.mp4`/`.mov`/`.mp3`/`.wav` 等，自动语音转写），或字幕文件（`.srt` / `.vtt`）、转写文本（`.txt`）、transcript JSON。

```powershell
.\.venv\Scripts\python.exe -m video_factory.rewrite `
  --source 别人的视频.mp4 `
  --duration 90 `
  --brief "面向新手，重点讲方法，语气像经验分享" `
  --output video_factory/output/my_video
```

- `--duration`：目标口播秒数（1~2 分钟就是 60~120），字数按 4.3 字/秒换算并在提示词里约束
- `--style`：内容类型模板，七选一——`tutorial`（教程知识）/ `film_recap`（影视解说悬念链）/ `seeding`（带货种草，内置广告法规避）/ `emotion`（情感语录）/ `ranking`（盘点榜单）/ `hot_take`（热点评论）/ `general`（默认通用）；每种输出配套 TTS 音色建议
- `--brief` 与模板冲突时以 `--brief` 为准
- `--provider anthropic --model claude-sonnet-5`：切换改写模型
- `--asr-provider auto|faster_whisper|openai`：转写引擎（默认 auto：装了 faster-whisper 用本地，否则用 OpenAI 云端）
- 原创红线内置于提示词：不抄原句、重构结构、只保留信息点

产物：
- `rewrite.json` — 钩子 + 分节口播 + 每节画面建议 + 3 个发布标题候选
- `voiceover.txt` — 完整配音稿

本地转写（离线、免费）首次需装扩展：`pip install -e ".[asr]"`；也可单独转写：

```powershell
.\.venv\Scripts\python.exe -m video_factory.asr --input 视频.mp4 --prompt "股票、仓位、止盈止损"
```
`--prompt` 传领域术语可显著提高中文识别准确率。

## Stage 2: Assembly（素材拼装 + 配音 + 精确时长）

输入：Stage 1 的 `rewrite.json` + 你的素材视频目录。

```powershell
.\.venv\Scripts\python.exe -m video_factory.assemble `
  --rewrite video_factory/output/my_video/rewrite.json `
  --assets D:\素材库 `
  --tts doubao `
  --output video_factory/output/my_video
```

- `--tts doubao|openai|edge`：现场从 `full_voiceover` 合成配音；或用 `--audio 已有配音.wav` 提供现成音频（两者互斥）
- `--duration 75`：覆盖 rewrite.json 里的目标时长
- `--voice`：指定音色（豆包默认 `zh_female_shuangkuaisisi_moon_bigtts`）
- `--aspect 16:9|9:16|1:1|3:4`：画幅（默认 16:9；抖音/快手/视频号用 `9:16`，小红书用 `3:4`）
- `--fit pad|crop|blur`：素材与画幅不匹配时的填充——`blur` **模糊背景填充**（竖屏发横素材的事实标准，强烈建议 9:16 搭配使用）、`crop` 裁满、`pad` 补边（默认）
- `--bgm 背景乐.mp3`：BGM 混音，自动 ducking（说话时压低、停顿回升），循环适配 + 淡入淡出；`--bgm-volume`（默认 0.2，建议 0.1~0.3）、`--bgm-fade`（默认 2 秒）。注意一稿多发请用免版税音源

时长控制规则：
- 无音频：成片时长精确等于目标时长（每节按字数占比分配，最短 2 秒保护，浮点残差归尾）
- 有音频：**成片时长 = 音频时长（±0.5s）**，视频末帧定格补长、音频截断——配音说完，片子刚好结束
- 素材不足一节时自动串多个片段；同一素材被多节复用时错开起点，避免重复画面

产物：
- `release.mp4` — 1080p30 成片
- `assembly_plan.json` — 每节的素材分配明细（供 Stage 3 和人工复盘）

## Stage 3: Effects（Remotion 特效，可选）

输入：Stage 2 的 `release.mp4` + `assembly_plan.json`。

```powershell
.\.venv\Scripts\python.exe -m video_factory.effects `
  --video video_factory/output/my_video/release.mp4 `
  --plan video_factory/output/my_video/assembly_plan.json `
  --rewrite video_factory/output/my_video/rewrite.json `
  --lower-thirds `
  --output video_factory/output/my_video
```

自动派生并叠加（带 alpha 的 ProRes 4444 中间片）：
- **Intro**：开场标题动画（取 hook / 发布标题）
- **ChapterCard**：每节起点的章节卡（侧滑色块 + 序号）
- **LowerThird**（`--lower-thirds` 开启）：节内底部胶囊条花字

行为约定：
- Node/npx 缺失时**优雅跳过**（写 `effects_skipped.json`），不阻断成片——特效层永远是可选增强
- 单条特效渲染失败会记录 `effects_warnings.json` 并继续
- `--skip-render` 只产出 `effects_manifest.json` 供检查，不实际渲染

## Stage 4: Subtitles（逐句动态字幕，自媒体标配）

输入：成片（有特效则用 `release_with_effects.mp4`）+ `rewrite.json`（文本来源）+ 配音 wav（时间轴来源）。

```powershell
.\.venv\Scripts\python.exe -m video_factory.subtitles `
  --video video_factory/output/my_video/release.mp4 `
  --rewrite video_factory/output/my_video/rewrite.json `
  --audio video_factory/output/my_video/voiceover.wav `
  --output video_factory/output/my_video
```

- **时间轴与文本分离**：faster-whisper 转写配音只取时间戳，屏幕文字用原稿——节奏精准且零错别字
- `--mode auto|align|ratio`：默认 auto（whisper 不可用时自动降级为按字数占比分摊，报告留痕）
- 字号随画幅自适应（竖屏自动加大）；白字黑边、底部居中
- `--audio` 建议显式传纯净配音（视频已混 BGM 会影响转写精度）
- 推荐顺序：rewrite → assemble → effects → **subtitles 最后烧**（不被特效遮挡）

产物：`release_subtitled.mp4` + `subtitles.ass` + `subtitles_report.json`

## Stage 5: Batch（批量驱动器 + 平台预设）

一张任务清单进，N 条成片出。单条失败不断批，产出 `batch_report.json`。

```powershell
.\.venv\Scripts\python.exe -m video_factory.batch --jobs jobs.json            # 全量执行
.\.venv\Scripts\python.exe -m video_factory.batch --jobs jobs.json --dry-run  # 只校验与展开参数
.\.venv\Scripts\python.exe -m video_factory.batch --jobs jobs.json --only douyin_recap_01
```

**平台预设**（job 里写 `"platform": "douyin"` 即自动展开，任何字段可显式覆盖）：

| platform | 画幅 | fit | 时长 | 字幕 | 特效 |
|---|---|---|---|---|---|
| douyin | 9:16 | blur | 60s | ✅ | ✅ |
| kuaishou | 9:16 | blur | 90s | ✅ | ✅ |
| shipinhao | 9:16 | blur | 120s | ✅ | ✅ |
| xiaohongshu | 3:4 | blur | 60s | ✅ | ✅ |
| bilibili | 16:9 | pad | 180s | ✅ | ✅ |

job 可配字段：`source`/`assets`（必填）、`platform`、`style`、`duration`、`brief`、`aspect`、`fit`、`tts`、`voice`、`audio`（复用现成配音，替代 TTS）、`bgm`、`bgm_volume`、`subtitles`、`effects`、`lower_thirds`、`output`。参考项目根的 [jobs.example.json](jobs.example.json)（含抖音影视解说、小红书种草、B站教程三个示例）。

## Full Chain Example（一条龙）

```powershell
$env:OPENAI_API_KEY="sk-..."; $env:VOLC_TTS_APPID="..."; $env:VOLC_TTS_TOKEN="..."
$out = "video_factory/output/batch_001"

# 抖音竖屏影视解说示例
.\.venv\Scripts\python.exe -m video_factory.rewrite   --source 原片.mp4 --duration 60 --style film_recap --output $out
.\.venv\Scripts\python.exe -m video_factory.assemble  --rewrite $out/rewrite.json --assets D:\素材库 --tts doubao --aspect 9:16 --fit blur --bgm bgm.mp3 --output $out
.\.venv\Scripts\python.exe -m video_factory.effects   --video $out/release.mp4 --plan $out/assembly_plan.json --rewrite $out/rewrite.json --output $out
.\.venv\Scripts\python.exe -m video_factory.subtitles --video $out/release_with_effects.mp4 --rewrite $out/rewrite.json --audio $out/voiceover.wav --output $out
```

多条视频用 Stage 5 的 batch 驱动器，每条独立输出目录，产物 JSON 全程可审计。

## Verification

```powershell
# 成片参数与时长
ffprobe -v error -show_entries format=duration -of json $out/release.mp4
# 全量回归
.\.venv\Scripts\python.exe -m pytest -q
```

## Troubleshooting

| 症状 | 处理 |
|---|---|
| `缺少 OPENAI_API_KEY 环境变量` | 设置对应 provider 的 key 后重跑 |
| `Doubao TTS error 3001` 等 | 检查火山引擎语音服务是否开通、appid/token 是否正确 |
| 素材扫描告警某文件跳过 | 该文件 ffprobe 失败（损坏/非视频），不影响其余素材 |
| `effects_skipped.json` 出现 | Node/npx 未装或不在 PATH；装好后重跑 Stage 3 |
| 抖音链接下载失败 | 抖音需要浏览器新鲜 cookie，建议先手动下载原片再走本地文件 |

## Compliance Notes

- 本链路定位是**原创生产**：文案经 LLM 重构（不抄原句），画面来自你自己的素材池，配音是新合成的
- 素材必须是你有权使用的（自有拍摄/授权采购）；原片仅作为文案信息点来源
- 发布前建议跑一次 `python -m video_factory.analysis`（质检）复核成片
