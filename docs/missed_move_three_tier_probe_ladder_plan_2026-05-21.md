---
title: ETH Missed Move Three Tier Probe Ladder Plan
date: 2026-05-21
status: accepted
scope: quant_system_rebuild + eth_trading_bot
owner: user
gbrain_slug: quant-bot-2026-05-21-three-tier-probe-ladder-plan
---

# ETH 三级仓位梯子方案裁定 — 2026-05-21

> 结论（一句话）：三级仓位梯子的方向是对的，但不能只改 `direction_alignment_pattern`；必须把“方向冲突分类”同时接入 trigger、probe admission 和 sizing，否则会变成日志分级而执行层仍不开仓。

## 裁定

- 接受“三级仓位梯子”的总体方向：不同 15m / 1h / 4h 结构对应不同风险标签、不同 probe admission 条件、不同仓位上限。
- 修正“不改阈值只加逻辑”的表述：`MIN_PROBE_THESIS_SCORE=0.53 -> 0.48 / 0.50 / 0.55` 是动态阈值，不是完全不改阈值。
- 否决只在 `multi_timeframe_policy.py` 增加 `direction_alignment_pattern` 名称；该字段主要用于解释，不足以改变 `trigger_ready` 或 sizing。
- 新增字段应独立于现有 pattern，避免破坏已有 `all_aligned_long` / `all_aligned_short` 语义：
  - `direction_conflict_class`
  - `probe_risk_tier`
  - `probe_size_cap`
  - `probe_thesis_floor`
- 接受 retest 开门，但必须按风险标签分层；`retest_support=true` 不能全局跳过 slope / breakout / regime alignment。
- `15m==4h!=1h` 不能再被简单 `regime_alignment=0` 硬拦；应标为 `early_rejoin_regime` / `1h_lagging` 候选，并走中风险 probe 路径。
- full entry 路径不动：research gate、governance 阈值、hard veto、staleness、conflict 仍保持原有约束。
- 所有改动必须先跑反事实 replay，再决定是否写入生产逻辑。

## 分歧与收敛

| 争议点 | 初始方案 | 复查后 | 收敛 |
|--------|----------|--------|------|
| 是否“不改阈值” | 声称只加逻辑 | thesis floor 分级本质是动态阈值 | 改口为“full alignment 阈值不动，probe 阈值按风险标签动态化” |
| pattern 字段是否足够 | 新增 `direction_alignment_pattern` 三个值 | pattern 主要解释，不直接驱动执行 | 新增独立 risk/probe 字段 |
| retest 是否可直接开门 | `retest_support=true` 直接进 trigger_ready | 仍会被 `regime_alignment < 0.55` 卡住 | retest 开门必须结合 conflict class |
| 仓位梯子落地位置 | 表格定义仓位 | 代码还需 sizing/runner 消费 | 仓位上限写入 probe context 并接 sizing |
| neutral 处理 | 冲突不再 neutral | 不能所有冲突直接 long/short | 先做方向冲突分类，再走不同 probe 路径 |

## 核实痕迹

| 说法 | 结果 | 证据 |
|------|:----:|------|
| 当前 `direction_alignment_pattern` 主要是解释字段 | ✓ | `quant_system_rebuild/src/policy/multi_timeframe_policy.py` |
| 当前真正构造方向与 trigger 的入口在 `realtime_policy_input.py` | ✓ | `_resolve_setup_direction()`、`_resolve_trigger_ready()`、`_resolve_regime_alignment()` |
| 当前 `trigger_ready` 会先检查 `regime_alignment < 0.55` | ✓ | `realtime_policy_input.py::_resolve_trigger_ready()` |
| 当前 `15m==4h!=1h` 可能被 1h lagging 导致 `regime_alignment=0` | ✓ | `_resolve_regime_alignment(regime_direction, confirm_direction)` |
| 当前 `trigger_ready_small_probe` 最大仓位固定为 10% | ✓ | `probe_resolver.py::MAX_TRIGGER_READY_PROBE_POSITION_SIZE = 0.10` |
| runner 会检查 `probe_context.source == trigger_ready_small_probe` | ✓ | `interfaces/runner.py::_is_trigger_ready_small_probe_allowed()` |
| 1%-4% replay 当前 13 段仍 0 开仓 | ✓ | `current_replay_1to4_windows_summary.json` |
| 520 个 neutral / observe_only 是方向冲突被压平，不是单纯没数据 | ✓ | `replay_1to4_direction_pattern_summary.json` |
| 45 个 trigger-ready 没开主要卡在 thesis floor | ✓ | `replay_1to4_miss_explanation.json` |

## 依赖链（前置条件）

```text
方向冲突分类
  -> 分层 trigger_ready
  -> 分层 probe admission
  -> 分层 sizing / runner 放行
  -> 反事实 replay
  -> 代码落地与横向测试
```

硬依赖解释：

- 只做方向分类不接 trigger，执行层仍可能保持 `observe_only` 或 `wait`。
- 只做 retest 开门不处理 `regime_alignment`，`early_rejoin_regime` 仍可能被 1h lagging 卡死。
- 只做 thesis floor 分级不接 sizing，会放出统一仓位的 probe，失去风险梯子的意义。
- 不跑反事实 replay，无法知道新增 100-200 个 probe 是改善收益还是放大噪声。

## 目标设计

### 方向结构分类

| 原始信号 | 标签 | 解释 | 风险 | 默认仓位上限 |
|----------|------|------|:---:|:---:|
| `15m==1h==4h` | `full_alignment` | 三框同向 | 低 | 原 full entry / 原 probe 逻辑 |
| `15m==4h!=1h` | `early_rejoin_regime` | 15m 回到 4h，大概率 1h 滞后 | 中 | 10% probe |
| `15m!=1h==4h` | `pullback_or_failed_reversal` | 15m 逆 1h/4h，可能是回调或失败反转 | 中高 | 5% probe |
| `15m==1h!=4h` | `counter_regime_momentum` | 15m/1h 动量逆 4h | 高 | 3% probe |

### Trigger 分层

| 标签 | trigger 规则 |
|------|--------------|
| `full_alignment` | 原逻辑不动 |
| `early_rejoin_regime` | 允许 `retest_support=true` 开门；允许 `1h_lagging`，不因 `regime_alignment=0` 直接硬拦 |
| `pullback_or_failed_reversal` | 必须 `retest_support=true`，且不能有 hard veto / staleness / conflict |
| `counter_regime_momentum` | 必须 `retest_support=true`，且走更高 thesis floor 与更低仓位 |

### Probe Admission 分层

| 标签 | thesis floor | 仓位上限 | 说明 |
|------|:---:|:---:|------|
| `full_alignment` | 0.53 | 原逻辑 | 不改全仓候选路径 |
| `early_rejoin_regime` | 0.48 | 10% | 顺 4h，大概率 1h 滞后 |
| `pullback_or_failed_reversal` | 0.50 | 5% | 中高风险，只允许小仓 |
| `counter_regime_momentum` | 0.55 | 3% | 逆 4h，高门槛低仓位 |

### 保持不动

- full entry 仍要求 research 支持。
- governance 毕业阈值不动。
- `has_hard_veto` 不动。
- staleness / conflict veto 不动。
- operational data quality block 不动。
- full alignment 的全仓候选路径不动。

## 下一步

| # | 动作 | 依赖 | 负责人 |
|---|------|------|--------|
| 1 | 写反事实脚本，基于 replay artifact 给每个 cycle 打 `direction_conflict_class` | 已有 1%-4% replay artifact | 待分配 |
| 2 | 统计四类 conflict class 的后验收益、MFE、MAE、最大回撤 | #1 | 待分配 |
| 3 | 模拟分层 `trigger_ready`，验证 retest 开门会新增多少候选 | #1 | 待分配 |
| 4 | 模拟分层 thesis floor 与仓位上限，得到候选 probe 列表 | #3 | 待分配 |
| 5 | 对候选 probe 跑退出规则与 lifecycle shadow replay | #4 | 待分配 |
| 6 | 反事实通过后再改代码：方向分类、trigger 分层、probe admission、sizing/runner | #5 | 待分配 |
| 7 | 每完成一层跑窄测与横向测试，最后跑 1%-4% / >2% replay 回归 | #6 | 待分配 |

## 关联

- 上游裁定: `docs/missed_move_1to4_replay_gate_review_2026-05-21.md`
- 相关 commit: `2032545`, `c1e02b6`, `b57cd6a`, `c179c47`, `64bd5cb`, `7c11950`
- 上游讨论: 2026-05-21 ETH missed move replay / DS 对抗审查 / 三级仓位梯子方案复查
- 复盘产物:
  - `C:\Users\左秋三\.codex\memories\move_scan_20260510_20\current_replay_1to4_windows_summary.json`
  - `C:\Users\左秋三\.codex\memories\move_scan_20260510_20\replay_1to4_miss_explanation.json`
  - `C:\Users\左秋三\.codex\memories\move_scan_20260510_20\replay_1to4_direction_pattern_summary.json`
- 相关代码:
  - `D:\开发\quant_system_rebuild\src\policy\realtime_policy_input.py`
  - `D:\开发\quant_system_rebuild\src\policy\multi_timeframe_policy.py`
  - `D:\开发\quant_system_rebuild\src\policy\decision_engine.py`
  - `D:\开发\quant_system_rebuild\src\policy\probe_resolver.py`
  - `D:\开发\quant_system_rebuild\src\policy\risk_filter.py`
  - `D:\开发\quant_system_rebuild\src\interfaces\runner.py`

## 遗留

- 本文只固化方案裁定，不直接修改交易逻辑。
- `0.48 / 0.50 / 0.55` 是待验证参数，不是已批准生产阈值。
- 预期 `100-200 small_probe` 只是目标假设，必须由反事实 replay 给出真实数字。
- confident-wrong 段仍未解决；仓位梯子只能降低风险暴露，不能提升方向预测质量。
- 若反事实显示某一类 conflict class 后验收益为负，该类必须保持 observe_only 或更低仓位，不允许为了提高开仓数强行放行。
