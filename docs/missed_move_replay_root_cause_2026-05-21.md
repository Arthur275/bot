---
title: ETH Missed Move Replay Root Cause Decision
date: 2026-05-21
status: accepted
scope: quant_system_rebuild + eth_trading_bot
owner: user
supersedes:
superseded_by:
gbrain_slug: quant-bot-2026-05-21-missed-move-final-review
---

# ETH 错过波段复盘根因裁定 — 2026-05-21

> 结论（一句话）：按当前已修复后的系统回放 2026-05-10 至 2026-05-20 的 21 段 ETH 明显涨跌，系统仍然 0 开仓；显性最大阻塞是 trigger，但安全修复必须先从方向层开始。

## 口径

- 时间区间：2026-05-10 至 2026-05-20。
- 时区：复盘窗口标签使用 CST / Asia-Shanghai；cycle/run_id 仍按系统原始 UTC 命名。
- 品种与周期：ETH，15m cycle 回放。
- 样本范围：16 段约 1% 至 2% 涨跌，5 段超过 2% 涨跌，合计 21 段。
- 判定对象：M1/M2 与 bot-side trigger-ready lifecycle 补丁落地后的当前系统行为。
- 相关 commit：
  - `quant_system_rebuild` `2032545`：M1 pipeline handoff / replay 修复。
  - `quant_system_rebuild` `c1e02b6`：M2 governance allowlist。
  - `quant_system_rebuild` `b57cd6a`：trigger-ready probe handoff（quant 侧）。
  - `eth_trading_bot` `c179c47`：trigger-ready probe lifecycle。
  - `eth_trading_bot` `64bd5cb`、`7c11950`：M2 与 DS 审查文档更新。
- 复盘产物：
  - `C:\Users\左秋三\.codex\memories\move_scan_20260510_20\replay_move_summary.json`
  - `C:\Users\左秋三\.codex\memories\move_scan_20260510_20\replay_over2_move_summary.json`
  - `C:\Users\左秋三\.codex\memories\move_scan_20260510_20\ds_classification_audit.json`
  - `C:\Users\左秋三\.codex\memories\move_scan_20260510_20\current_replay_full_windows_summary.json`
  - `C:\Users\左秋三\.codex\memories\move_scan_20260510_20\current_replay_over2_windows_summary.json`

## 裁定

- 决定：当前复盘结论按“0 开仓”处理；这不是数据缺口，而是当前 repaired system 的实际回放结果。
- 决定：瓶颈要分成两种排序写清楚。
  - 显性阻塞排序：`trigger > research/thesis > direction neutral > direction confident wrong`。
  - 安全修复顺序：`direction -> trigger -> research/thesis -> lifecycle E2E`。
- 决定：先做方向层重构，保留并消费 15m / 1h / 4h 独立结构，不再过早压成单一 `consensus_direction`。
- 决定：方向层完成后，再做 trigger 子归因；只修被证据证明过窄的条件，不全局放松。
- 决定：research / thesis 的 M3 拆分放在方向和 trigger 之后。
- 决定：`trigger_ready_small_probe` lifecycle 补丁落地不等于闭环完成，仍需先实现 invalidate_conditions 消费端，再跑端到端 shadow replay。
- 否决：把“补丁落地”说成“系统 live-ready”。
- 否决：先做 M3 作为第一性修复。M3 只能救“方向对 + trigger 已扣 + 被 research/thesis 拦住”的小子集。
- 否决：直接整体调敏 trigger。方向不稳时调敏 trigger 会放大错方向信号。
- 否决：把当前 OHLCV 斜率方向当预测模型。它是滞后状态标签，不是提前预测。
- 非目标：不启用 live order，不降低 production risk limit，不降低 governance / research 安全阈值，不把当前 shadow evidence 直接毕业成 active support。
- 非目标：本轮不升级方向预测模型（保留 OHLCV 斜率，不做 ML/时序预测）。
- 非目标：本轮不解决 Type 2 confident-wrong 段（4 段自信看反，需要单独模型/规则处理）。

## 分歧与收敛

| 争议点 | DS 初始 | 用户核后 | 收敛 |
|--------|---------|----------|------|
| Type 1 中 `trigger_ready=0` 段数 | 4 段 | 3 段 | 用户对，口径修正 |
| 全 21 段 `trigger_ready=0` 段数 | 未按全样本口径先说清 | 9 段 | 以全 21 段为最终口径 |
| “管道修好了”表述 | 容易被理解成闭环完成 | 补丁落地不等于闭环验证完 | 改口为“工程路径落地，E2E 未完成” |
| 最大瓶颈 | trigger 是最大显性阻塞 | 方向是 trigger 的上游依赖 | 分成“显性阻塞排序”和“安全修复顺序” |
| M3 优先级 | research gate 有价值 | 覆盖范围有限，不是第一性修复 | M3 放在方向与 trigger 之后 |

## 核实痕迹

| 说法 | 结果 | 证据 |
|------|:----:|------|
| 21 段复盘当前系统会开仓 0 段 | ✓ | `replay_move_summary.json` 16 段 `would_open=false`；`replay_over2_move_summary.json` 5 段 `would_open=false` |
| 样本为 16 段约 1% 至 2% + 5 段超过 2% | ✓ | `replay_move_summary.json`；`replay_over2_move_summary.json` |
| Type 1 方向多数正确但没执行为 10 段 | ✓ | `ds_classification_audit.json` 中 `classification=type1_direction_right` |
| Type 2 方向层问题为 11 段 | ✓ | `ds_classification_audit.json` 中 `type2_neutral_dominant=6`、`type2_confident_wrong=4`、`type2_flip_or_mixed=1` |
| 全 21 段 `trigger_ready=0` 为 9 段 | ✓ | `ds_classification_audit.json` 中 `trigger_ready_count=0` 的段 |
| Type 1 中 `trigger_ready=0` 为 3 段 | ✓ | `ds_classification_audit.json` Type 1 子集 |
| Type 1 最高 `trigger_ready` 比例为 22.86% | ✓ | `05-15 18:52 -> 05-15 22:04`，`8 / 35` cycle，实际下跌 `-2.36%` |
| 方向计算是 OHLCV 斜率，不是预测模型 | ✓ | `quant_system_rebuild/src/ingest/market_data_consensus.py` 的方向计算逻辑 |
| 15m / 1h 冲突会导致 source 弃权，严格多数不足会返回 `neutral` | ✓ | `quant_system_rebuild/src/ingest/market_data_consensus.py` 的 `_source_core_direction()` 与 `vote_consensus_direction()` |
| trigger 显性主阻塞是 `setup_ready_waiting_trigger` | ✓ | 复盘 summary 的 `top_reasons` |
| trigger 偶尔通过后仍被 thesis / research 拦住 | ✓ | `entry_thesis_below_floor,entry_research_score_below_floor,setup_ready_waiting_trigger` |
| 当前 research 仍不支持执行 | ✓ | replay summary 中 `research_score=0.0`、`qualified_counts=[0]`、degrade flags 含 `research_veto` |
| `trigger_ready_small_probe` lifecycle 补丁已落地但 E2E 未完成 | ✓ | `docs/trigger_ready_small_probe_contract_review_2026-05-20.md`，仍要求 open -> active -> expire/invalidate -> exit shadow replay |
| 最新已知 strict-live cycle 仍非 live-ready | ✓ | `eth-15m-20260520T125100Z-a1b2c3d4`：`execution_allowed=false`、无 candidate execution package |

## 依赖链（前置条件）

```text
方向层重构 -> trigger 层归因/重构 -> research 层(M3) -> invalidate_conditions 消费端实现 -> lifecycle E2E shadow replay
   ↑ 先做          ↑ 依赖方向结构       ↑ 只覆盖 trigger 已扣子集   ↑ 代码不存在，需先实现              ↑ 验证闭环
```

硬依赖解释：

- trigger 消费方向、regime alignment、breakout/retest/slope 等条件；方向被压成 `neutral` 时，trigger 很难满足。
- M3 只处理 trigger 已经扣了以后被 thesis / research 拦住的情况；trigger 从不扣时，M3 没入口。
- invalidate_conditions 消费端当前不存在（`state_store.py` 存，`position_manager.py` 只消费 `active_probe_expires_at`）；必须先实现代码，再跑 shadow replay 验证退出闭环。

## 验收标准

- P1 方向层重构：
  - 决策层必须保留 15m / 1h / 4h 独立方向。
  - `neutral` 必须有归因：时间框架冲突、source 缺失、投票不足、数据质量问题之一。
  - 不能用盲目强行 long/short 来降低 neutral。
  - 自信看反段必须单独标记，不能被方向重构放大成更激进错单。
- P2 trigger 子归因：
  - 每个 `setup_ready_waiting_trigger` 必须能拆出具体原因。
  - 至少区分 `setup_direction_neutralized`、`regime_alignment_low`、`breakout_support_missing`、`retest_support_missing`、`slope_support_missing`、`short_term_reversal_block`、`thesis_or_confidence_below_probe_floor`、`risk_or_conflict_veto`。
  - 不允许用单个全局阈值调整覆盖所有 trigger failure。
- P3 research / thesis 拆分：
  - full entry 仍要求完整 research 支撑。
  - capped probe 只能放行证据缺口类 reason code：
    - `wf_quality_insufficient`
    - `truth_candidate_unqualified`
    - `research_aging`
    - `wf_trade_share_low`
    - `wf_trade_count_low`
  - 以下硬阻断类继续拦截，不允许用 M3 probe 绕过：
    - `research_issue_present`
    - `wf_dispersion_high`
    - `wf_return_drift_high`
    - `walk_forward_missing`
    - `research_stale`
    - `bundle_missing`
  - research weakness 和 operational fault 必须继续分开；分类口径对齐 `probe_resolver.py` 的 `EVIDENCE_GAP_RESEARCH_CODES` / `HARD_RESEARCH_BLOCK_CODES`、`risk_filter.py` 的 `has_hard_veto_for_research_auxiliary_probe()`，以及 Pattern 8 相关测试。
- P4 lifecycle E2E：
  - 实现 `active_probe_invalidate_conditions` 消费端：bot 侧读取已存储的 invalidate_conditions，与当前 handoff / transition reason 匹配后触发 exit/reduce。
  - shadow replay 必须覆盖 `open trigger_ready_small_probe -> record active probe -> invalidate 或 3 x 15m expire -> exit / reduce handoff consumed`。
  - 必须证明 `invalidate_conditions` 被消费，不只是被记录（`state_store.py:514` 存，`position_manager.py:256` 当前只消费 `active_probe_expires_at`）。
  - 必须证明不会产生孤儿 probe 或无法退出的 shadow position。

## 下一步

| # | 动作 | 依赖 | 负责人 |
|---|------|------|--------|
| 1 | 方向层重构：保留并消费 15m / 1h / 4h 独立方向，建立结构矩阵 | — | 待分配 |
| 2 | 用 21 段错过波段回归方向层输出，核对 neutral 和 confident-wrong 变化 | 方向层重构 | 待分配 |
| 3 | 增加 `trigger_ready=false` 子归因，不先调阈值 | 方向层重构 | 待分配 |
| 4 | 基于子归因定向修 trigger 条件 | trigger 子归因完成 | 待分配 |
| 5 | 做 research / thesis M3 拆分，只覆盖 trigger 已扣子集 | 方向层与 trigger 层完成 | 待分配 |
| 6 | 实现 `active_probe_invalidate_conditions` 消费端 | M3 提供 probe 候选 | 待分配 |
| 7 | 跑 trigger-ready probe lifecycle 端到端 shadow replay | 消费端实现完成 | 待分配 |

## 关联

- 相关 commit：`2032545`、`c1e02b6`、`b57cd6a`、`c179c47`、`64bd5cb`、`7c11950`。
- 上游讨论：2026-05-21 “05-10 至 05-20 ETH 错过波段复盘 / DS 对抗审查 / 最终审查”。
- 前置文档：
  - `D:\开发\eth_trading_bot\docs\trigger_ready_small_probe_contract_review_2026-05-20.md`
  - `D:\开发\eth_trading_bot\docs\factor_research_pipeline_m2_governance_adversarial_review_2026-05-20.md`
  - `D:\开发\eth_trading_bot\docs\factor_research_pipeline_ds_adversarial_review_2026-05-20.md`
- 相关代码：
  - `D:\开发\quant_system_rebuild\src\ingest\market_data_consensus.py`
  - `D:\开发\quant_system_rebuild\src\interfaces\runner.py`
  - `D:\开发\quant_system_rebuild\src\policy\decision_engine.py`
  - `D:\开发\quant_system_rebuild\src\policy\realtime_policy_input.py`
  - `D:\开发\quant_system_rebuild\src\policy\probe_resolver.py`
  - `D:\开发\quant_system_rebuild\src\policy\risk_filter.py`
  - `D:\开发\quant_system_rebuild\src\policy\exit_state_machine.py`
- gbrain slug：`quant-bot-2026-05-21-missed-move-final-review`。

## 遗留

- strict-live pipeline 在 2026-05-19 停止后仍未作为本复盘的一部分重启验证。
- post-M2 的 trigger-ready probe 端到端 shadow replay 仍未完成。
- runtime 仍未毕业：最新已知 cycle 仍是 `execution_allowed=false`，`research_score=0.0`，`qualified_candidate_count=0`，governance 为 `watch`。
- trigger “太钝”的子原因还没有逐 cycle 归因完成，当前只能确定 `setup_ready_waiting_trigger` 是显性主阻塞。
- 方向自信看反的 4 段需要单独机制处理，不能靠降低 neutral 或放松 trigger 顺手解决。
