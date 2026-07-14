# Video Factory Originality Risk Gate

## Goal

在视频上线前给出一份可解释的原创度风险报告，帮助判断成片是否过度复用参考视频，并把风险转化为合规的返工策略。

## Non-Goals

- 不模拟、规避或承诺通过任何平台的真实相似度检测。
- 不提供绕过审核的技术建议。
- 不把“重剪同一条视频”包装成原创内容。

## Metrics

`originality_report.json` 输出以下指标：

- `visual_similarity`：源视频与成片的抽帧感知哈希相似度。
- `audio_reuse_ratio`：优先使用低采样率音频能量指纹估算；无法估算时才回退到 EDL 源片复用比例。
- `text_overlap_ratio`：优先比较同名 `.txt`、`.srt`、`.vtt` 文本；没有文本时使用音频复用作为保守代理。
- `source_reuse_ratio`：根据 `edit_decision_list.md` 估算直接来自源片的成片占比。
- `duration_retention`：成片时长与源片时长比例。

这些指标会汇总成 `similarity_score` 和 `risk_level`，风险等级为 `low`、`medium`、`high` 或 `unknown`。

## Workflow

1. `render_replicate()` 生成成片、质量报告、EDL 和创作计划。
2. 工作台调用 `build_originality_report()` 生成 `originality_report.json`。
3. `build_quality_summary()` 把原创风险、相似度、音频复用和文本重合展示到验收面板。
4. 如果原创风险为中高，一键返工会进入 `创作增强`、审计严格度、强创作、移除原声待原创配音、卡通化视觉重构和保留主线策略，并把原创改造建议写回生产备注。

## Legitimate Fixes

高风险时的解决方向是增加真实原创增量：

- 增加自有画面、重新录屏或补拍 B-roll。
- 使用原创解说、现场声或重新配音。
- 重写脚本结构和表达顺序。
- 把参考视频变成短引用或证据来源，而不是主体素材。
- 增加清晰评论、分析、教学、对比或实测结论。
- 对人物或原片画面做合规视觉重构，例如授权卡通角色、原创 avatar 或视频到视频风格化；本地卡通化只能作为快速重构，不等同于平台审核保证。

## UI Contract

工作台首页必须可见：

- `原创风险`
- `相似度`
- `音频复用`
- `文本重合`
- `originality_report`

这些信息必须出现在任务摘要和产物列表里，不能只藏在日志中。
