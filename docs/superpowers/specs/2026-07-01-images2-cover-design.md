# Images2 Cover Generation Design

## Goal

封面不再从成片里随便截一帧，而是在完整参考拆解、原创内容计划、分镜和标题候选生成后，专门交给 images2 生成一张简洁、有审美、能重复传达视频主旨的封面。

## Flow

1. 读取 `reference_blueprint.json`、`content_plan.json`、`storyboard_v2.json`、`asset_sourcing_plan.json` 和标题信息。
2. 生成 `cover_brief.json`：
   - 一句话主旨
   - 核心矛盾或观众收益
   - 主视觉元素
   - 3 个封面角度
   - 禁止事项：文字过多、复杂拼贴、假新闻感、廉价 AI 光效、冒充真实现场
3. 生成 `cover_prompt_pack.json`：
   - 默认 3 张候选
   - images2 优先
   - 16:9，1920x1080
   - 文字最多 6-10 个中文字
   - 主体单一、留白明确、风格和成片一致
4. 生成 `cover_asset_manifest.json`：
   - 记录候选封面路径、provider、prompt、是否可发布。
   - `mock_image` 只能预览，`mock_images2` 作为测试中的可发布 images2 替身。
5. 推荐封面写入 `cover.png`。
6. `quality_report.json` 增加封面检查：
   - `cover_brief_ready`
   - `cover_prompt_pack_ready`
   - `cover_assets_publish_ready`
   - `cover_not_overcomplicated`
   - `cover_text_concise`

## User Interface

前台继续只展示一个封面和结论。高级产物里显示 `cover_brief`、`cover_prompt_pack`、`cover_asset_manifest`，方便追溯封面为什么这么做。

## Quality Rules

- 没有 images2 或可确认生成图，不能标记为发布候选。
- 封面最多一个主视觉焦点。
- 封面文字最多 10 个中文字。
- 封面不能复杂拼贴，也不能用假新闻/假赛事现场表达。
