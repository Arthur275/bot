# Daily Market vs System Review - 2026-05-18

> Symbol: ETHUSDT  
> Timezone: UTC  
> Reviewer: codex  
> Generated at: 2026-05-19T04:39:53+00:00

## 0. 输入质量

| Input | Status | Source | Trace |
|---|---|---|---|
| market_data | ok | binance_api | count=96 15m candles, window=2026-05-18T00:00:00+00:00..2026-05-19T00:00:00+00:00 |
| missed_opportunity_audit | ok | existing_local_artifact | source_path=D:\开发\eth_trading_bot\docs\daily_reviews\artifacts\missed_opportunity_audit_20260518.json; start_ts=20260518T000000Z; end_ts=20260519T000000Z |
| live_ready_diagnostics | stale | existing_artifact | source_path=D:\开发\quant_system_rebuild\runtime\analysis\live_ready_blocking_diagnostics.json; decision_cycle_ts=20260505T160027Z |
| scheduler_daily_review | stale | existing_artifact | source_path=D:\开发\quant_system_rebuild\runtime\scheduler\daily_review.json; generated_at=2026-05-04T06:32:30+00:00 |
| shadow_outcomes | ok | existing_local_artifact | source_path=D:\开发\eth_trading_bot\docs\daily_reviews\artifacts\missed_opportunity_shadow_outcomes_20260518.json; audit_path=D:\开发\eth_trading_bot\docs\daily_reviews\artifacts\missed_opportunity_audit_20260518.json |

## 1. 系统健康度诊断

- system_health: unknown
- dominant_blocking_reason: diagnostics_stale; audit_top_reason=decision_artifact_missing
- primary_fault_domain: unknown
- diagnostics_status: stale
- stale_diagnostics_reference: research_gate:research_stale
- research_gate: stale_reference:blocked (research_stale,research_not_ready)
- factor_governance: stale_reference:watch (factor_lookup_stale)
- risk_filter: stale_reference:veto (okx_taker_volume_experimental,overlay_present,overlay_source:okx,overlay_bias:neutral,consensus:long,crowding_warning,high_regime_risk,wf_return_drift_high,truth_candidate_source:all_results_fallback,truth_candidate_unqualified,research_stale,research_issue_present)
- execution_guard: stale_reference:blocked (not_entry_action)
- candidate_package: stale_reference:blocked (valid_for_live_decision_false,qualified_candidate_count_zero,live_candidate_count_zero)

## 2. 市场摘要

- market_data_status: ok
- open/high/low/close: 2131.0/2157.24/2077.23/2130.08
- day_return_pct: -0.0432
- intraday_range_pct: 3.7546
- max_15m_range_pct: 1.6293
- main_regime: volatile
- notable_windows: high@12:45, low@18:15

## 3. 决策时间线

| Time UTC | Market | System | Trigger | Risk | Gate | Cycle Count | 事件级评价 |
|---|---|---|---|---|---|---:|---|
| 00:00-01:44 | volatile@00:00, range_pct=0.94, move=down | action=unknown continued 65 cycles; category=observability_gap | false | unknown | decision_artifact_missing | 65 | needs_review |
| 01:45 | volatile@01:45, range_pct=0.66, move=up | action=wait; category=reasonable_risk_control | false | degraded | not_entry_action | 1 | needs_review |
| 01:45-02:27 | volatile@01:45, range_pct=0.66, move=up | action=unknown continued 28 cycles; category=observability_gap | false | unknown | decision_artifact_missing | 28 | needs_review |
| 02:30 | volatile@02:30, range_pct=0.31, move=down | action=wait; category=reasonable_risk_control | false | degraded | not_entry_action | 1 | needs_review |
| 02:30-02:46 | volatile@02:30, range_pct=0.31, move=down | action=unknown continued 12 cycles; category=observability_gap | false | unknown | decision_artifact_missing | 12 | needs_review |
| 02:47 | volatile@02:45, range_pct=0.36, move=down | action=wait; category=reasonable_risk_control | false | degraded | not_entry_action | 1 | needs_review |
| 02:47-03:11 | volatile@02:45, range_pct=0.36, move=down | action=unknown continued 16 cycles; category=observability_gap | false | unknown | decision_artifact_missing | 16 | needs_review |
| 03:15 | volatile@03:15, range_pct=0.17, move=up | action=wait; category=reasonable_risk_control | false | degraded | not_entry_action | 1 | needs_review |
| 03:15-03:59 | volatile@03:15, range_pct=0.17, move=up | action=unknown continued 29 cycles; category=observability_gap | false | unknown | decision_artifact_missing | 29 | needs_review |
| 04:01 | volatile@04:00, range_pct=0.23, move=up | action=wait; category=reasonable_risk_control | false | degraded | not_entry_action | 1 | needs_review |
| 04:01-04:26 | volatile@04:00, range_pct=0.23, move=up | action=unknown continued 18 cycles; category=observability_gap | false | unknown | decision_artifact_missing | 18 | needs_review |
| 04:29 | volatile@04:15, range_pct=0.37, move=down | action=wait; category=reasonable_risk_control | false | degraded | not_entry_action | 1 | needs_review |
| 04:29 | volatile@04:15, range_pct=0.37, move=down | action=unknown; category=observability_gap | false | unknown | decision_artifact_missing | 1 | needs_review |
| 05:15-06:46 | volatile@05:15, range_pct=0.24, move=up | action=observe_only continued 3 cycles; category=reasonable_risk_control | false | degraded | not_entry_action | 3 | needs_review |
| 07:14 | volatile@07:00, range_pct=0.28, move=down | action=wait; category=reasonable_risk_control | false | degraded | not_entry_action | 1 | needs_review |
| 07:30-08:19 | volatile@07:30, range_pct=0.40, move=down | action=observe_only continued 3 cycles; category=reasonable_risk_control | false | degraded | not_entry_action | 3 | needs_review |
| 08:30 | volatile@08:30, range_pct=0.17, move=down | action=wait; category=reasonable_risk_control | false | degraded | not_entry_action | 1 | needs_review |
| 08:47-09:20 | volatile@08:45, range_pct=0.21, move=up | action=observe_only continued 2 cycles; category=reasonable_risk_control | false | degraded | not_entry_action | 2 | needs_review |
| 09:33-11:04 | volatile@09:30, range_pct=0.19, move=down | action=wait continued 5 cycles; category=reasonable_risk_control | false | degraded | not_entry_action | 5 | needs_review |
| 11:16 | volatile@11:15, range_pct=0.15, move=up | action=observe_only; category=reasonable_risk_control | false | degraded | not_entry_action | 1 | needs_review |
| 14:30-23:00 | volatile@14:30, range_pct=0.99, move=down | action=wait continued 9 cycles; category=reasonable_risk_control | false | degraded | not_entry_action | 9 | needs_review |

## 4. 三层闸门穿透

| Layer | Status | Evidence | Reason |
|---|---|---|---|
| quant | trigger_seen_no_execution | trigger_ready_count=5, execution_ready_count=0 | audit_summary |
| risk_gate | unknown_stale_diagnostics | decision_artifact_missing,scheduler_status:incomplete_snapshot_only,waiting_for_trigger,net_edge_below_cost,below_probe_floor,scheduler_status:blocked | diagnostics_stale_not_used_as_target_day_conclusion |
| bot_order | unknown_stale_diagnostics | decision_artifact_missing,scheduler_status:incomplete_snapshot_only,waiting_for_trigger,net_edge_below_cost,below_probe_floor,scheduler_status:blocked | diagnostics_stale_not_used_as_target_day_conclusion |

## 5. 差异归类

| Category | Count | Meaning |
|---|---:|---|
| logic_gap | 0 | 接近可交易，需要设计审查 |
| strategy_choice | 162 | 策略有意等待 |
| reasonable_risk_control | 31 | 风控合理拦截 |
| observability_gap | 891 | 证据不足，先修观测 |

| 来源 | 结果 | 样本 |
|---|---|---|
| shadow_outcomes | net_favorable_rate=0.000, status=blocked | 0 logic_gap samples evaluated |

## 6. 当天总评

- label: system_blind
- rationale: 市场当天有 3.75% 日内振幅，但审计里 1084 个 cycle 中 891 个是 observability_gap，主因是 decision_artifact_missing；现有证据不足以判断“该下没下”，只能先判定系统当天观测链路不完整。
- evidence: timeline shows repeated `decision_artifact_missing`; category_counts: observability_gap=891, strategy_choice=162, reasonable_risk_control=31, logic_gap=0; live_ready_diagnostics references 20260505T160027Z and is stale_reference only.
- review_confidence: medium

Allowed labels: `hit`, `missed`, `correct_wait`, `wrong_wait`, `saved`, `wrong_entry`, `system_blind`.

## 7. 偏差检查

- hindsight_bias_risk: medium
- confirmation_bias_risk: medium
- which conclusions came from pre-defined strategy rules: logic_gap=0 means current audit did not identify a qualifying missed-entry sample; stale diagnostics are not used as target-day gate conclusions.
- which conclusions came from hindsight price action: market volatility summary only proves opportunity-like movement existed, not that the live strategy should have entered.
- notes:
  - Do not classify this day as `correct_wait`; too much evidence is missing.
  - Do not classify this day as `missed`; there is no audited logic_gap sample and no target-day live-ready diagnostics.
  - Treat the day as observability-first: fix decision artifact completeness before tuning strategy thresholds.

## 8. 下一步

| Type | Action | Owner | Priority |
|---|---|---|---|
| observability_fix | Investigate why scheduler produced snapshot-only cycles without decision artifacts | quant | P0 |
| data_fix | Add target-date live_ready_diagnostics generation or explicit unavailable status | quant | P1 |
| daily_review | Keep current 22-row timeline for now; revisit compression after 1 week of reports | reviewer | P2 |
