---
title: ETH 1%-4% Move Replay Gate Review
date: 2026-05-21
status: accepted
scope: quant_system_rebuild + eth_trading_bot
owner: user
gbrain_slug: quant-bot-2026-05-21-current-1to4-move-replay
---

# ETH 1%-4% 行情复盘门槛裁定 — 2026-05-21

> 结论（一句话）：当前 P1-P4 修复后的系统在 13 段 ETH 1%-4% 行情中仍然 0 开仓；系统有 45 次看见 `trigger_ready` 但被 probe thesis floor 拦住，另有 520 次 neutral 冲突被压成 `observe_only`，方向冲突不能再直接塌缩成无动作。

## 裁定

- 当前 1%-4% replay 按 0 开仓处理：13 段、1396 个 cycle，`entry_count=0`，`execution_allowed=true` 为 0。
- `trigger_ready=true` 不等于开仓：45 个 trigger-ready cycle 全部保持 `action=wait`，`probe_source` 为空，没有生成 `trigger_ready_small_probe`。
- 05-19 03:11 -> 08:22 CST 的 +1.73% 行情属于“看到了但没开”：方向为 long，`trigger_ready=true`，但 `thesis_score=0.510958 < MIN_PROBE_THESIS_SCORE=0.53`。
- research 在 trigger-ready probe 降级层不是独立硬拦：`research_score=0.0` 触发 full entry 降级和 `entry_research_score_below_floor`，但 probe contract 的直接阻挡者是 thesis floor。
- 下一刀不应同时砍 research gate；应先做 `MIN_PROBE_THESIS_SCORE=0.53 -> 0.50` 的反事实测试，确认会放出多少 `trigger_ready_small_probe` 以及放出的收益/回撤。
- `neutral` 不是系统没看见，而是看见时间框架冲突后塌缩成 `observe_only`；这类冲突必须分类消费，不应统一变成无动作。
- 否决“把所有 neutral 直接改 long/short”：15m/1h/4h 的冲突含义不同，必须按结构分类。
- 否决“直接整体放松 trigger”：directional-wait 的主因是 `slope/breakout/retest` 缺失，必须先做反事实归因，不能全局降阈值。

## 分歧与收敛

| 争议点 | DS 初始 | 用户核后 | 收敛 |
|--------|---------|----------|------|
| probe 没开是否是 research + thesis 双拦 | research 与 thesis 并列为阻挡 | research 只触发 full entry 降级，probe 直接卡在 thesis floor | 用户/DS 修正成立：probe 层只先砍 thesis floor |
| 05-19 03:11 那波能否靠 thesis floor 修复 | 未明确 | `0.511 < 0.53`，降到 0.50 大概率出 probe | 成立，但需 replay 反事实验证 |
| neutral 数量 | 848 | 当前 1%-4% replay 是 520 | 以本轮 13 段 artifact 为准 |
| neutral 是否全是同一模式 | 全部 15m=short,1h=long,4h=short | 主模式集中，但不是全部同一模式 | 改为冲突结构分类 |
| 15m 与 4h 同向、1h 反向 | 可视为早期反转 | 只能标为早期重回大周期/1h 滞后候选 | 不直接开仓，先分类再验证 |

## 核实痕迹

| 说法 | 结果 | 证据 |
|------|:----:|------|
| 13 段 1%-4% 行情当前系统 0 开仓 | ✓ | `current_replay_1to4_windows_summary.json`，1396 cycles |
| `trigger_ready=true` 共有 45 个 cycle | ✓ | `inspect_replay_1to4_artifacts.py` 直接扫 `handoff.json` / `decision.json` |
| 45 个 trigger-ready 全部未生成 probe | ✓ | `probe_sources=[]`，`actions=[wait]` |
| 未看见/未 ready 的 cycle 为 1351 个 | ✓ | `replay_1to4_miss_explanation.json` |
| 未 ready 主因是 slope/breakout/retest 缺失 | ✓ | `slope_support_missing=1351`，`breakout_support_missing=1340`，`retest_support_missing=889` |
| neutral / observe_only 为 520 个 | ✓ | `replay_1to4_direction_pattern_summary.json` |
| directional wait 为 876 个 | ✓ | `action_counts: wait=876, observe_only=520` |
| 05-19 03:11 样本 thesis 低于 probe floor | ✓ | `eth-15m-20260518T213320Z-9a474a99/handoff.json`，`thesis_score=0.510958` |
| 05-19 03:11 样本无 runtime/staleness/conflict veto | ✓ | 同上，`runtime_vetoes=[]`，`staleness_veto=false`，`conflict_veto=false` |
| 05-19 03:11 样本 stop 与 edge 不构成阻挡 | ✓ | 同上，`stop_distance_pct=0.0156`，`edge_status=confirmed_positive`，`net_edge>0` |
| research 不在 trigger-ready probe contract 中独立拦截 | ✓ | `quant_system_rebuild/src/policy/probe_resolver.py` 的 `trigger_ready_small_probe_contract_block_reason_codes()` |
| runner 对 `trigger_ready_small_probe` 不把 `research_veto` degrade flag 当硬拦 | ✓ | `quant_system_rebuild/src/interfaces/runner.py` 的 `_is_trigger_ready_small_probe_allowed()` |

## 依赖链（前置条件）

```text
冲突方向结构分类
  -> trigger 反事实归因
  -> probe thesis floor 反事实
  -> 小仓 probe replay 验证
  -> lifecycle E2E shadow replay
```

硬依赖解释：

- 不先做冲突结构分类，neutral 会继续把 15m/1h/4h 的有效分歧压成 `observe_only`。
- 不先做 trigger 反事实，直接放松 slope/breakout/retest 会把无效方向也放出去。
- 不先做 thesis floor 反事实，直接把 `0.53` 改成 `0.50` 只是在 05-19 个案上成立，不能证明全局收益为正。
- lifecycle E2E 只能验证开仓后的退出闭环，不能替代入场门槛验证。

## 下一步

| # | 动作 | 依赖 | 负责人 |
|---|------|------|--------|
| 1 | 聚合 neutral 原始方向三元组，按 `15m/1h/4h` 结构标注 conflict class | 已有 replay artifact | 待分配 |
| 2 | 实现方向冲突分类，不把所有冲突统一压成 neutral | #1 | 待分配 |
| 3 | 对 1351 个 non-ready cycle 做 trigger 反事实，分别测试 slope / breakout / retest 条件贡献 | #1 | 待分配 |
| 4 | 对 45 个 trigger-ready cycle 做 `MIN_PROBE_THESIS_SCORE=0.53 -> 0.50` 反事实 | 已有 replay artifact | 待分配 |
| 5 | 对反事实放出的 probe 做收益、回撤、MFE/MAE、退出闭环统计 | #3, #4 | 待分配 |
| 6 | 只有反事实通过后，才落地参数或规则改动并跑横向测试 | #5 | 待分配 |

## 关联

- 相关 commit: `2032545`, `c1e02b6`, `b57cd6a`, `c179c47`, `64bd5cb`, `7c11950`
- 上游讨论: 2026-05-21 ETH 05-10 至 05-20 missed move replay / DS 对抗审查 / 1%-4% 当前系统回放
- 复盘产物:
  - `C:\Users\左秋三\.codex\memories\move_scan_20260510_20\current_replay_1to4_windows_summary.json`
  - `C:\Users\左秋三\.codex\memories\move_scan_20260510_20\replay_1to4_miss_explanation.json`
  - `C:\Users\左秋三\.codex\memories\move_scan_20260510_20\replay_1to4_direction_pattern_summary.json`
  - `C:\Users\左秋三\.codex\memories\move_scan_20260510_20\replay_1to4_artifact_probe_audit.json`
- 相关代码:
  - `D:\开发\quant_system_rebuild\src\ingest\market_data_consensus.py`
  - `D:\开发\quant_system_rebuild\src\policy\multi_timeframe_policy.py`
  - `D:\开发\quant_system_rebuild\src\policy\decision_engine.py`
  - `D:\开发\quant_system_rebuild\src\policy\probe_resolver.py`
  - `D:\开发\quant_system_rebuild\src\interfaces\runner.py`
- gbrain:
  - `quant-bot-2026-05-21-current-1to4-move-replay`
  - `quant-bot-2026-05-21-p1-p4-refactor-verified`

## 遗留

- 本文不修改代码，只固化裁定与下一步验证顺序。
- 本文不宣称系统 live-ready；当前仍是 replay / shadow 结论。
- 本文不解决 confident-wrong 段；自信看反需要单独方向质量机制。
- 本文不直接降低生产风险阈值；`0.53 -> 0.50` 必须先通过反事实 replay。
- 45 个 trigger-ready 中至少 1 个带 `net_edge_below_cost`，不能用 05-19 个案推导全部可开。
