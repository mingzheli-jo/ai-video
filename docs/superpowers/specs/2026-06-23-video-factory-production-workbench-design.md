# Video Factory Production Workbench Design

## Goal

把现有视频复刻工作台从“单条任务提交器”升级为“可批量、可配置、可验收、可返工、可复用”的本地生产驾驶舱。

## Scope For Today

实现 6 个 P0 能力：

- 批量任务入口：支持多文件上传和多行本地路径。
- 生产参数面板：提供生产预设、质量严格度、创作强度、目标时长策略、音频策略。
- 质量评分：在 `quality_summary` 中输出分数、等级、风险和扣分原因。
- 返工机制：对已有任务创建一版带 `repair_of` 关系的新任务。
- 任务历史：工作台可列出当前会话内的任务，任务记录保存输入、参数、结果、评分和产物链接。
- 生产预设：内置教程长版、真人剪辑、原片增强、美食真实剪辑四个预设，用来快速设置模式和要求。

## Explicit Non-Goals

- 不抓取抖音账号或平台数据。
- 不搬运赛事版权片段。
- 不假装已经接入 Whisper、云 ASR 或素材库。
- 不新增 AI 风包装模板。

## Architecture

继续复用 `video_factory.workbench` 的本地 HTTP 服务。任务层扩展为 batch-aware：一次请求可以创建多个 job，每个 job 仍由现有 `render_replicate()` 渲染。生产参数保存在 job 元数据中，并进入历史、摘要和返工请求。

质量评分不替代现有硬性自检。`quality_report.json` 仍然决定任务是否通过；新增评分层把检查结果翻译成 `score`、`grade`、`risk_level`、`deductions` 和 `repair_suggestions`，让用户能看到“哪里不够好、下一版怎么修”。

## UI

页面保持生产控制台风格，不做营销页。左侧从“创建任务”升级为“创建生产批次”，包含：

- 多文件上传。
- 多行路径输入。
- 生产预设。
- 生产参数。

右侧显示：

- 当前任务状态和验收面板。
- 批次队列。
- 任务历史。
- 返工按钮。

## Data Flow

1. 用户提交一个或多个视频。
2. 后端解析为 batch，逐个创建 job。
3. 每个 job 存储 `input_path`、`mode`、`source_name`、`options`、`repair_of`。
4. 渲染完成后读取 `quality_report.json` 和 `creative_plan.json`。
5. `build_quality_summary()` 输出可读质量摘要和评分。
6. UI 轮询 batch 中的 job，并同步更新队列、历史、验收面板和返工按钮。

## Error Handling

- 空批次返回 `400`。
- 不存在的视频路径逐条返回 `400`，不创建半批次。
- 渲染失败仍保存 `quality_report.json` 摘要，只要报告存在。
- 返工任务若源文件不存在，返回 `400`。

## Testing

- 单元测试覆盖批量路径解析、多文件上传解析、生产预设、质量评分、任务历史、返工任务创建。
- 页面测试覆盖新增 UI 文案、控件和浏览器渲染。
- 全量 `pytest` 必须通过。

## Notes

当前生产参数会先进入任务元数据、历史、质量摘要和返工配置。渲染算法会继续使用现有 `render_replicate()`；后续如需让目标时长和创作强度直接影响剪辑算法，应单独扩展 `video_factory.creative` 的 planner 参数接口。
