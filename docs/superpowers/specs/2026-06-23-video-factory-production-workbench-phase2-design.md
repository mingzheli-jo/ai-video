# Video Factory Production Workbench Phase 2 Design

## Goal

把 Production Workbench V1 中“已展示但尚未深入打通”的能力继续向下落实：生产参数进入创作计划、历史可持久化、返工可以根据质量失败原因自动调整。

## Implemented Scope

- `creative_strength` 和 `target_duration_policy` 进入 `build_creative_plan()`。
- 默认不传生产参数时，旧创作计划保持原行为。
- `retain_core` 和 `strong` 会提高目标时长和目标段数。
- `short_summary` 和 `light` 会降低目标时长和目标段数。
- `JobStore` 支持 `history_path`，任务会写入 `job_history.json`。
- 返工任务读取上一版 `quality_summary.deductions`，自动生成 `repair_focus`。
- 模板风险会提升 `creative_strength=strong`。
- 时长风险会设置 `target_duration_policy=retain_core`。
- 任意扣分会提升 `quality_strictness=audit`。
- UI 历史记录可点击回看任务。

## Still Out Of Scope

- 外部 ASR provider。
- 平台趋势抓取。
- 自动素材库版权管理。
- 把每个 profile 的细粒度权重全部参数化。

## Verification

新增测试覆盖：

- 生产参数改变创作目标。
- 历史写盘和重载。
- 智能返工配置。
- 历史任务点击入口存在。

最终需要跑：

```bash
python3 -m pytest -q
```
