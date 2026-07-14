# Video Factory Workflow

这份流程把视频工厂从“临时做一条视频”升级成“可重复生产、可复盘、可验证”的项目。

> 批量生产链路（文案改写 `rewrite` → 素材拼装 `assemble` → 特效叠加 `effects`）见
> [BATCH_PRODUCTION_GUIDE.md](BATCH_PRODUCTION_GUIDE.md)。本文档描述的是
> 单条视频的发布增强工作流（workbench / replicate）。

## Production Stages

每条视频都按 7 个阶段执行：

1. Reference Intake
2. Quality Diagnosis
3. Production Mode Selection
4. Edit Decision List
5. Rendering
6. Verification
7. Retrospective

## 1. Reference Intake

拿到参考视频后先做事实确认：

```bash
ffprobe -v error \
  -show_entries format=duration,size,bit_rate \
  -show_entries stream=index,codec_type,codec_name,width,height,r_frame_rate,avg_frame_rate,bit_rate,duration,channels,sample_rate \
  -of json /path/to/reference.mp4
```

必须记录：

- 分辨率。
- 帧率。
- 总时长。
- 视频码率。
- 音频编码和采样率。
- 源视频是否高度压缩。

如果源视频码率很低，要提前说明：只能减少二次损失，不能恢复源文件不存在的细节。

## 2. Quality Diagnosis

先判断视频质量问题属于哪一类：

- 素材问题：画面假、静态图过多、缺少真实视频。
- 剪辑问题：时长不合适、节奏拖、重点不清。
- 包装问题：AI 风 UI 太多、遮挡画面、模板感强。
- 音频问题：声音小、噪声重、切点不连续。
- 方向问题：成片类型和参考片类型不一致。

不要直接开渲染。先说清楚问题在哪里。

## 3. Production Mode Selection

根据用户目标选择模式：

### A. Original Enhanced

适合：

- 用户想保留原教程完整结构。
- 原视频本身主题和叙事可用。
- 主要问题是清晰度、声音或二次压缩。

处理：

- 保留原时长。
- 不做重剪。
- 只做画质、音频、封面、质检图和报告。

### B. Human Edit

适合：

- 用户觉得原片能用，但节奏可优化。
- 需要保留完整教程感。
- 想去掉等待、重复和低信息密度片段。

处理：

- 保留原片约 `65%` 到 `85%` 时长。
- 输出 EDL。
- 做真实切段和自然推近。
- 不新增 AI 模板包装。

### C. Creative Rewrite

适合：

- 用户想做新主题、新脚本、新表达。
- 参考片只提供风格，不要求保留原片主题。

处理：

- 重新写脚本。
- 重新组织分镜。
- 可以引入新素材，但必须真实、合规、可解释。

### D. Short Summary

适合：

- 用户明确要短视频摘要。
- 可以牺牲完整教程感。

处理：

- 强钩子。
- 高压缩信息。
- 必须明确告诉用户这不是同类型完整教程。

## 4. Edit Decision List

Human Edit 和 Creative Rewrite 必须先生成 EDL 或 storyboard。

Human Edit 的 EDL 字段：

- `key`
- `source`
- `start`
- `duration`
- `zoom`
- `crop_x`
- `crop_y`
- `purpose`

示例产物：

- `video_factory/output/cc-switch-deepseek-human-edit/edit_decision_list.md`

EDL 的作用：

- 防止随便剪。
- 让用户能审查剪辑判断。
- 让下一次批量生产可复盘。

## 5. Rendering

渲染器必须输出独立目录，例如：

```text
video_factory/output/<project-slug>/
  release.mp4
  cover.png
  contact_sheet.jpg
  render_report.json
  edit_decision_list.md
  segments/
  segments.txt
```

不同模式的最低产物：

- Original Enhanced: `release.mp4`, `cover.png`, `contact_sheet.jpg`, `render_report.json`
- Human Edit: Original Enhanced 的产物 + `edit_decision_list.md`, `segments/`, `segments.txt`
- Creative Rewrite: Human Edit 的产物 + `script.md` 或 `storyboard.json`
- Creative Edit: Human Edit 的产物 + `creative_plan.json`, `semantic_timeline.json`, `transcript_analysis.json`, `content_analysis.json`, `audio_analysis.json`, `candidate_edl.md`, `cover_candidates.jpg`, `creative_brief.md`

## 6. Verification

交付前必须执行验证。

参数验证：

```bash
ffprobe -v error \
  -select_streams v:0 \
  -show_entries stream=width,height,r_frame_rate,duration,nb_frames \
  -show_entries format=duration,size,bit_rate \
  -of json video_factory/output/<project-slug>/release.mp4
```

音频验证：

```bash
ffprobe -v error \
  -select_streams a:0 \
  -show_entries stream=codec_name,sample_rate,channels,bit_rate,duration \
  -of json video_factory/output/<project-slug>/release.mp4
```

测试验证：

```bash
python3 -m pytest
```

视觉验证：

- 打开 `contact_sheet.jpg`。
- 检查是否有新增 AI 包装。
- 检查关键界面是否被裁掉或遮挡。
- 检查视频是否仍然像真实视频，而不是图片配音。
- 创作增强版要打开 `semantic_timeline.json` 和 `candidate_edl.md`，确认推荐片段能对应原片章节，而不是只按画面好看随机抽样。
- 如果有同名 `.srt`、`.vtt` 或 `.txt` 文稿，创作增强版要打开 `transcript_analysis.json`，确认章节判断使用了 transcript 证据；没有文稿时确认 provider 清楚标记为 OCR 回退。

## 7. Retrospective

每次用户指出质量问题后，必须沉淀为规则。

复盘格式：

```markdown
## Problem

用户看到的问题是什么？

## Root Cause

是素材、剪辑、包装、音频、脚本还是方向问题？

## New Rule

以后如何避免？

## Test Or Artifact

能否写进测试、EDL、报告或质量标准？
```

## Current Baseline Examples

### Original Enhanced Baseline

- Output: `video_factory/output/cc-switch-deepseek-original-enhanced/release.mp4`
- Duration: `9分23秒`
- Rule: 不新增包装，只做画质和声音增强。

### Human Edit Baseline

- Output: `video_factory/output/cc-switch-deepseek-human-edit/release.mp4`
- Duration: `7分17秒`
- Segments: `10`
- Rule: 保留教程主线，删除等待和重复，关键界面自然推近。

## Batch Production Rule

批量视频生产不能直接套视觉模板。

正确批量化的是：

- 分析流程批量化。
- EDL 字段批量化。
- 质量红线批量化。
- 验收命令批量化。
- 抽帧质检批量化。

不能批量化的是：

- 所有视频套同一套顶部栏。
- 所有视频都做同样进度条。
- 所有视频都压成同样时长。
- 所有视频都用静态图配音。

视频工厂的核心资产不是模板外观，而是判断标准和生产流程。
