# AI Video Factory / AI 视频工厂

面向自媒体的**批量原创短视频生产流水线**：一条参考内容进，多平台可发布的成片出。

[简体中文](#简体中文) · [English](#english)

---

## 简体中文

### 这是什么

AI Video Factory 把「参考内容 → 原创短视频」这条链路自动化：你提供一段参考素材（视频/音频/字幕/文本）和一个自己的素材库，系统用 LLM 重写文案、合成配音、拼装画面、叠加特效与逐句字幕，产出抖音 / 快手 / 视频号 / 小红书 / B站规格的成片。核心是**批量**（一张任务清单出 N 条）和**原创化**（文案经 LLM 重构、画面来自你的素材、配音重新合成）。

**适合**：做多平台自媒体、需要一套系统覆盖多种内容类型（教程 / 影视解说 / 种草 / 情感 / 盘点 / 热点 / 通用）、希望非技术同学也能用浏览器工作台操作。

**不适合**：搬运他人成片、规避平台审核或相似度检测、处理无授权 / 私密 / DRM 视频。素材必须是你有权使用的（自拍 / 授权采购）；参考原片仅作为文案信息点来源。

### 系统架构：五段链路

每段一个独立 CLI，产物 JSON 串联，可单段跑、全链跑，或用 `batch` 一张清单批量跑。

| 段 | 模块 | 职责 | 关键产物 |
|---|---|---|---|
| 1 | `rewrite` | LLM 原创改写文案（7 种内容风格）；视频/音频可直接进（自动 whisper 转写） | `rewrite.json` |
| 2 | `assemble` | 素材池拼装 + TTS 配音 + 精确时长对齐（4 画幅 / pad·crop·blur 填充 / BGM ducking） | `release.mp4`、`assembly_plan.json` |
| 3 | `effects` | Remotion 特效（片头 / 章节卡 / 花字 / 要点卡 / 金句 / 数字强调 + 转场音效），可选、优雅降级 | `release_with_effects.mp4` |
| 4 | `subtitles` | 逐句字幕：whisper 取时间轴 + 原稿取文本，白字描边、中英双语、竖屏单行、libass 烧录 | `release_subtitled.mp4` |
| 5 | `batch` | 批量驱动器 + 平台预设；单条失败不断批 | `batch_report.json` |

> 另有 AI 生图链路：无视频素材时可用豆包 Seedream 为每节生图（`image_gen`），在批量链路里夹在 rewrite 与 assemble 之间。

### 环境要求

- Python 3.9+（本机为 3.14；虚拟环境在 `.venv/`）
- `ffmpeg` / `ffprobe` 在 PATH（切片、拼接、混音、探测）
- Node 18+ + npm（可选，Remotion 特效层）
- `yt-dlp`（可选，公开视频链接下载）

首次安装：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -U pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev,tts]"   # 可选再加 asr：".[dev,tts,asr]"
cd remotion; npm install; cd ..                              # 需要特效层时
```

凭据只从环境变量读、不硬编码（工作台会持久化到已 gitignore 的 `credentials.yaml`）：

```powershell
$env:OPENAI_API_KEY="sk-..."      # 或 ANTHROPIC_API_KEY / DEEPSEEK_API_KEY（改写，--provider 默认 auto 按序自动选）
$env:VOLC_TTS_APPID="..."         # 豆包 TTS（旧版）
$env:VOLC_TTS_TOKEN="..."
# 或 $env:VOLC_TTS_APIKEY="..."    # 豆包 TTS（新版快捷 API，单 key）
$env:ARK_API_KEY="..."            # 可选：AI 生图（方舟 Seedream）
```

### 怎么用

**① 创作工作台（推荐，图形化）**

双击项目根目录的 **`启动创作工作台.bat`**（自动开浏览器，关窗口即停服）。等价命令：

```powershell
.\.venv\Scripts\python.exe -m video_factory.studio --port 56090
```

只绑 `127.0.0.1`，本机访问。可填表单选平台 / 风格 / 配音、上传素材、配置凭据与生图风格，一键出片。

**② 批量出片（一张清单出 N 条）**

```powershell
.\.venv\Scripts\python.exe -m video_factory.batch --jobs jobs.json --dry-run   # 先校验参数展开
.\.venv\Scripts\python.exe -m video_factory.batch --jobs jobs.json             # 正式跑
.\.venv\Scripts\python.exe -m video_factory.batch --jobs jobs.json --only douyin_recap_01
```

job 里写 `"platform": "douyin"` 即自动套用下表预设，任何字段可显式覆盖。参考 [`jobs.example.json`](jobs.example.json)。

| platform | 画幅 | fit | 时长 | 字幕 | 特效 |
|---|---|---|---|---|---|
| douyin | 9:16 | blur | 60s | ✅ | ✅ |
| kuaishou | 9:16 | blur | 90s | ✅ | ✅ |
| shipinhao | 9:16 | blur | 120s | ✅ | ✅ |
| xiaohongshu | 3:4 | blur | 60s | ✅ | ✅ |
| bilibili | 16:9 | pad | 180s | ✅ | ✅ |

**③ 单段手工跑（调试 / 精修）**

```powershell
$out = "video_factory/output/demo"
.\.venv\Scripts\python.exe -m video_factory.rewrite   --source 原片.mp4 --duration 60 --style film_recap --output $out
.\.venv\Scripts\python.exe -m video_factory.assemble  --rewrite $out/rewrite.json --assets D:\素材库 --tts doubao --aspect 9:16 --fit blur --output $out
.\.venv\Scripts\python.exe -m video_factory.effects   --video $out/release.mp4 --plan $out/assembly_plan.json --rewrite $out/rewrite.json --output $out
.\.venv\Scripts\python.exe -m video_factory.subtitles --video $out/release_with_effects.mp4 --rewrite $out/rewrite.json --audio $out/voiceover.wav --output $out
```

推荐顺序 `rewrite → assemble → effects → subtitles`（字幕最后烧，不被特效遮挡）。

### 主要产物

每个任务目录下可能生成：`rewrite.json`（文案）、`release.mp4`（拼装成片）、`assembly_plan.json`（分配明细）、`release_with_effects.mp4`（带特效）、`release_subtitled.mp4`（带字幕，最终成片）、`voiceover.wav`（配音）、`batch_report.json`（批量报告）。产物默认不入库（见 `.gitignore`）。

### 测试

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

### 更多文档

- [`BATCH_PRODUCTION_GUIDE.md`](BATCH_PRODUCTION_GUIDE.md) —— 五段链路完整参数与排障
- [`VIDEO_QUALITY_STANDARD.md`](VIDEO_QUALITY_STANDARD.md) —— 成片质量红线
- [`HUMAN_EDITING_PLAYBOOK.md`](HUMAN_EDITING_PLAYBOOK.md) —— 人工精修参考

### 合规

只处理你拥有版权、获授权、或有权参考改编的内容。链接下载能力只面向公开可访问视频，不绕过登录 / 私密 / DRM / 平台限制。原创性只是工程约束（LLM 重构文案、自有素材、新合成配音），不构成对任何平台审核结果的保证。

### License

MIT License，见 [`LICENSE`](LICENSE)。

---

## English

AI Video Factory is a **batch original short-video production pipeline** for creators. Feed it one reference input plus your own asset library; it rewrites the script with an LLM, synthesizes voiceover, assembles footage, and overlays effects and per-line subtitles — producing platform-ready clips for Douyin / Kuaishou / Bilibili / Xiaohongshu / Shipinhao.

It does **not** try to bypass platform review or similarity detection. Use only content you own or are licensed to adapt; your footage must be yours to use, and the reference clip serves only as an information-point source for the rewritten script.

**Five-stage pipeline** (each an independent CLI, chained via JSON): `rewrite` (LLM script) → `assemble` (footage + TTS + exact duration) → `effects` (Remotion, optional) → `subtitles` (per-line, bilingual) → `batch` (platform presets, one job list → N clips).

**Quick start** (Windows, Python 3.9+):

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev,tts]"
.\.venv\Scripts\python.exe -m video_factory.studio --port 56090   # local workbench (127.0.0.1)
```

Requires `ffmpeg`/`ffprobe` on PATH; Node 18+ for the optional effects layer. Credentials are read from environment variables only. See [`BATCH_PRODUCTION_GUIDE.md`](BATCH_PRODUCTION_GUIDE.md) for full CLI usage. MIT licensed.
