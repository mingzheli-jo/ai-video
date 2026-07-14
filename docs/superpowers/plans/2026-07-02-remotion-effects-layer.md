# Remotion 特效层集成规划

日期：2026-07-02
状态：规划中（未实施）

## 目标

在不改动现有 Python + ffmpeg 主流水线架构的前提下，引入 Remotion（React
逐帧渲染）作为**可选的特效资产 provider**，用于生成片头动画、章节转场、动态字幕卡、
数据图表动画等 ffmpeg 滤镜难以胜任的复杂动效。

特效层产出的是**带 alpha 通道的透明视频片段**，最终由现有流水线用 ffmpeg
`overlay` 合成进 `release.mp4`。主流水线一行架构不用改，Remotion 是旁挂子系统。

## 设计原则（承接项目质量红线）

1. 原片始终是主体，Remotion 只做叠加特效，不替换原始画面。
2. 特效必须是"真正可发布的资产"，不能是占位动画 —— 与 README 里
   "AI 视觉素材必须真正可发布，不能是占位图" 一致。
3. 特效层是**可选**的：Node 未安装或渲染失败时，流水线回退到纯 ffmpeg 路径，
   不阻断成片。
4. 数据驱动：Remotion 组件的 props 来自主流水线已产出的结构化 JSON
   （`semantic_timeline.json`、`creative_plan.json`），不重复造数据。

## 数据契约

复用现有 `semantic_timeline.json`（见 `video_factory/semantic.py`）：

```
chapters[]:
  index      章节序号
  topic      主题
  title      标题（用于章节转场卡）
  start,end  秒级时间码（用于对齐 overlay 时间轴）
  evidence   证据（可选副标题）
```

新增一份 `effects_manifest.json` 作为 Python 与 Node 之间的边界：
Python 写出「需要哪些特效、什么时间、什么文案」，Node 读入渲染。

```jsonc
{
  "version": "effects_manifest_v1",
  "fps": 30,
  "width": 1920, "height": 1080,   // 跟随原片比例，不写死
  "effects": [
    {"type": "intro",        "start": 0.0,  "duration": 2.5, "title": "...", "subtitle": "..."},
    {"type": "chapter_card", "start": 12.0, "duration": 1.5, "index": 1, "title": "..."},
    {"type": "lower_third",  "start": 30.0, "duration": 4.0, "text": "..."}
  ]
}
```

## 架构与数据流

```
Python 主流水线
  └─ 产出 semantic_timeline.json / creative_plan.json
       └─ effects.py（新增）：派生 effects_manifest.json
            └─ subprocess: npx remotion render --props=effects_manifest.json
                 └─ remotion/ 子工程输出 effects_<i>.mov（ProRes 4444，带 alpha）
                      └─ ffmpeg overlay 合成进 release.mp4（回到现有 replicate 流程）
```

## 实施步骤（建议独立分阶段做，不与 Windows/豆包 混提交）

### 阶段 A：Node 子工程骨架
- `remotion/` 目录：`package.json`、`remotion.config.ts`、`src/Root.tsx`、
  `src/compositions/`（Intro / ChapterCard / LowerThird 三个组件起步）。
- props schema 用 zod 校验，与 `effects_manifest_v1` 对齐。
- `.gitignore` 补 `remotion/node_modules/`、`remotion/out/`。

### 阶段 B：Python 桥接层
- 新增 `video_factory/effects.py`：
  - `build_effects_manifest(semantic_timeline, geometry) -> dict`
  - `render_effects(manifest_path, out_dir, runner=subprocess.run) -> list[Path]`
    （用 `shutil.which("npx")` 探测；缺失则返回 `[]` 并记录 skip，不抛错）
- Windows 注意：`npx` 在 Windows 上是 `npx.cmd`，`shutil.which` 能正确解析；
  subprocess 调用照现有约定加 `encoding="utf-8", errors="replace"`。

### 阶段 C：合成接入
- 在 `replicate.py` 的成片阶段，若 effects 资产存在，追加 ffmpeg
  `overlay=...:enable='between(t,start,end)'` 滤镜链。
- 质检（quality_lab）中把特效片段纳入"可发布资产"校验，拒绝空白/占位动画。

### 阶段 D：测试与文档
- `tests/test_effects.py`：manifest 派生逻辑用纯函数测试；`render_effects`
  用 fake runner 测试（不真实调 npx，CI 无 Node 也能过）。
- README 增补 Remotion 可选依赖说明和 Node 版本要求。

## 成本与约束

- 需要 **Node.js 18+**（本机 v24.14.1 ✅、npm 11.11.0 ✅）。
- Remotion 逐帧走 Chromium 截图，**渲染慢**：特效层只渲必要片段，别全片过。
- **许可证**：个人及 ≤3 人团队免费；公司规模超限需商业授权 —— 引入前需确认。
- 简单转场/字幕如果 ffmpeg 的 `xfade`/`drawtext`/`overlay` 能满足，优先用
  ffmpeg，不引入 Remotion。Remotion 的价值在复杂动效（动态图表、精细排版、
  React 组件生态）。

## 决策点（实施前需用户确认）

1. 许可证是否满足使用场景（个人/团队规模）。
2. 首批要做哪些特效类型（建议先 Intro + ChapterCard 两个，验证链路）。
3. 是否接受 `remotion/` 作为项目内子工程（vs 独立仓库）。
