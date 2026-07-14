# Quality-First Visual Sourcing Design

## Goal

把“傻瓜式原创”从先找素材改成先分析参考视频和原创分镜，再按镜头缺口决定是否用 images2 生图、授权素材或用户自有素材补位。系统默认不把 mock 图、素材拼贴或参考视频截图标记为可发布。

## User Flow

1. 用户提供参考视频和一句话主题。
2. 系统拆解参考视频结构，但不复用画面、原声或字幕。
3. 系统生成原创内容计划和分镜。
4. 系统从分镜生成 `visual_requirements.json`，逐镜头说明需要什么画面、为什么需要、最低质量标准和禁止事项。
5. 系统生成 `asset_sourcing_plan.json`，默认 images2 优先；授权素材只做补位，用户素材只在专家设置中使用。
6. 系统生成 `visual_prompt_pack.json` 和资产 manifest。
7. 质量门禁检查真实生成图、来源决策、分辨率/比例、重复率、配音和原创风险。mock 图只能作为预览，不能进入发布候选。

## Architecture

- `reference_guided_original.py` 增加两个正式产物：
  - `visual_requirements.json`: 分镜后的画面需求清单。
  - `asset_sourcing_plan.json`: 每个画面需求的来源决策，默认 `images2_first`。
- `build_visual_prompt_pack()` 改为从素材来源计划生成 prompt，避免绕过分镜需求直接拼素材。
- `build_generated_asset_manifest()` 标记 provider 是否 publish ready；`mock_image` 只允许预览，`mock_images2` 用于测试模拟可发布 provider。
- `write_reference_guided_quality_report()` 增加视觉需求和来源计划检查，未通过时前台显示“需补素材”。
- 工作台主界面新增“画面生产策略”，默认“images2 生图优先”。本地素材库路径保留在专家设置里，避免普通用户误以为要提前准备素材。

## Quality Gates

- 每个场景至少两个画面需求。
- 来源计划必须 images2 优先，且禁止参考帧复用。
- mock 图不能进入发布候选。
- 缺真实生成图时只能输出预览，交付结论为“需补素材”。
- 质量报告必须展示画面需求、生图策略、生成素材数量和发布分级。

## Out of Scope

- 本次不接真实外部素材搜索 API。
- 本次不在本地服务里直接调用 Codex 会话的 image generation tool；只定义 provider 合同和门禁。
- 本次不做固定虚拟角色、口型同步或真实人物换脸。
