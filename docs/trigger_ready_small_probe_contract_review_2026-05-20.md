---
title: Trigger-Ready Small Probe Contract Review
date: 2026-05-20
scope: quant_system_rebuild + eth_trading_bot
status: lifecycle_patch_validated
---

# Trigger-Ready Small Probe Contract Review

## Scope

This note records the review result for the missed-entry deadlock fix around:

- `trigger_ready=true`;
- direction is explicit and aligned;
- full entry is blocked by weak research evidence;
- a capped `small_probe` should be allowed only when operational health is clean.

This document does not authorize live execution. `real_worker` must remain dry-run / no real order submission until the independent `small_probe` contract is reviewed end to end.

## Review Verdict

The DS review is materially correct.

The original deadlock has been fixed: `small_probe` no longer shares the full-entry research threshold as the only path out of an entry block. The new `trigger_ready_small_probe` path allows a narrowly capped probe when the live trigger is strong enough and operational data is healthy, while still blocking stale/missing/unavailable research, factor lookup faults, scoring-chain freezes, and other hard operational faults.

The admission path is fixed, and the first post-entry lifecycle gap has now been closed: trigger-ready probe invalidation metadata is carried into the handoff, and the bot records / expires `trigger_ready_small_probe` as an active probe source.

## Confirmed Contract

Quant admission is anchored in `quant_system_rebuild`:

- `src/policy/decision_engine.py`
  - `_apply_trigger_ready_small_probe_contract`
- `src/policy/probe_resolver.py`
  - `trigger_ready_small_probe_contract_open`
  - `OPERATIONAL_DATA_QUALITY_BLOCK_CODES`
- `src/policy/sizing_policy.py`
  - `MAX_TRIGGER_READY_PROBE_POSITION_SIZE`
- `src/interfaces/runner.py`
  - `_is_trigger_ready_small_probe_allowed`

Admission conditions:

| Layer | Condition | Current value |
|---|---|---|
| Trigger | `trigger_ready=true` | required |
| Trigger | `short_term_reversal_flag=false` | required |
| Direction | consensus / regime / confirm / setup / trigger all same direction | required |
| Quality | `entry_timing_score >= 0.75` | required |
| Quality | `regime_alignment >= 1.0` | required |
| Quality | breakout support OR retest support OR `slope_support >= 0.50` | required |
| Score | `confidence >= min_probe_confidence` | default `0.53` |
| Score | `thesis_score >= min_probe_thesis_score` | default `0.53` |
| Risk | no hard veto / position exit veto | required |
| Risk | no conflict veto / staleness veto | required |
| Edge | no `net_edge_below_cost` | required |
| Data health | no operational data quality block | required |
| Sizing | max position size | `0.10` |
| Probe context | expiry bars | `3 x 15m` |
| Probe context | no-follow-through invalidation | enabled |

Operational block codes currently include:

```text
bundle_missing
data_health_veto
factor_governance_unavailable
factor_lookup_empty
factor_lookup_generated_at_missing
factor_lookup_missing
factor_lookup_rebuild_failed
factor_lookup_rebuild_still_stale
factor_lookup_stale
research_health_missing
research_missing
research_not_ready
research_stale
research_unavailable
scoring_chain_frozen
unavailable
```

Bot-side real-order gating is anchored in `eth_trading_bot`:

- `src/bot/automation_gate.py`
  - `_trigger_ready_small_probe_contract_open`
  - `_trigger_ready_small_probe_hard_block_codes`
  - `_trigger_ready_small_probe_size_block_reason`

For the exact `trigger_ready_small_probe` contract, bot no longer treats `risk_filter_status=degraded` alone as a real-order hard block. It still blocks:

- size above `0.10`;
- missing or stale factor lookup timestamp;
- `scoring_chain_frozen`;
- staleness/conflict/runtime vetoes;
- blocked research states such as stale/missing/unavailable/not-ready;
- missing entry preflight;
- missing protective-stop preflight;
- missing planned protective stop;
- missing initial stop loss;
- missing take-profit order when the handoff carries a TP ladder.

## Protective Stop

The protective stop requirement is currently enforced before a candidate can pass the bot gate:

- `PositionManager` marks entry actions, including `small_probe`, as refreshing/maintaining protective stop.
- `automation_gate` blocks entry actions when:
  - `execution_plan.maintain_protective_stop` is false;
  - `handoff.initial_stop_loss` is missing;
  - `maintain_protective_stop` preflight is not ready.
- `exchange_command_builder` creates a `maintain_protective_stop` command with `initial_stop_loss`, trailing metadata, and TP ladder context.

This means the initial protective-stop attachment is covered for admission/candidate generation.

## Lifecycle Patch

DS's concern about `invalidate_conditions` was valid and has been addressed for the first required lifecycle step.

Patched state:

- `decision_engine` writes trigger-ready invalidation rules into `metadata["probe_context"]["invalidate_conditions"]`.
- `ExecutionHandoff.invalidate_conditions` now merges `decision.exit_plan.invalidate_conditions` with `probe_context.invalidate_conditions`.
- `state_store.py` now records active probe metadata for both `contrarian_short_probe` and `trigger_ready_small_probe`.
- Trigger-ready active-probe metadata includes:
  - `active_probe_source`;
  - `active_probe_started_at`;
  - `active_probe_expires_at`;
  - `active_probe_expiry_bars`;
  - `active_probe_expiry_timeframe`;
  - `active_probe_invalid_if_no_followthrough`;
  - `active_probe_risk_tier`;
  - `active_probe_invalidate_conditions`.
- `position_manager.py` now treats an expired `trigger_ready_small_probe` as an exit plan with:
  - `effective_action=exit`;
  - `plan_reason=trigger_ready_probe_expired`;
  - `place_exit_order=true`.

The following no longer remain open:

- handoff visibility for `trigger_ready_long_failed_followthrough`;
- handoff visibility for `trigger_ready_short_failed_followthrough`;
- handoff visibility for `trigger_reversal_15m`;
- handoff visibility for `no_followthrough_after_3x15m`;
- handoff visibility for `hard_risk_veto`;
- bot-side expiry handling for `trigger_ready_small_probe`.
- full quant `DecisionEngine -> ExecutionHandoff` output for active-probe trigger reversal:
  - `action=exit`;
  - `transition:probe_trigger_reversal_exit`;
  - `invalidate_conditions` includes the transition.
- bot shadow risk-assist consumption of a quant exit handoff while a `trigger_ready_small_probe` is active:
  - `effective_action=exit`;
  - simulated `exit_order`;
  - protective-stop maintenance remains planned under the existing risk policy.
- historical shadow replay clock stability:
  - `runtime_state.runtime_now` now uses the cycle `generated_at` instead of wall-clock `utc_now()`, so active-probe expiry decisions are replayable.

Still not fully proven:

- Bot code preserves `invalidate_conditions` in some shadow/preflight payloads and passes `reduce_conditions` into reduce commands, but there is no confirmed consumer that acts on trigger-ready `invalidate_conditions`.
- The expiry patch covers time-based expiry. It does not yet evaluate live follow-through quality from market data.
- The `trigger_reversal_15m` and `hard_risk_veto` paths still rely on future quant handoffs / guard state to request exit or block continuation; they need shadow replay verification with open-position state.

## 5/19 Incident Interpretation

The historical 2026-05-19 early-morning window should not be rewritten as "the system definitely should have opened a long there."

Those old cycle artifacts mixed several states:

- many were not clean `trigger_ready=true` samples;
- some had operational faults such as stale/empty factor lookup;
- old factor governance and research freshness behavior differs from the current repaired chain.

The validated conclusion is narrower and safer:

> The architecture deadlock is fixed for the intended class: trigger-ready, direction-aligned, probe-floor-qualified samples with weak historical research evidence but no operational hard fault can now become `trigger_ready_small_probe` candidates.

## Next Work

Before considering real execution:

1. Run one end-to-end shadow replay where a `trigger_ready_small_probe` opens, is recorded as active, then expires after `3 x 15m`.
2. Add / verify shadow scenarios for `hard_risk_veto` and failed-thesis while a trigger-ready probe is open.
3. Confirm protective stop, TP ladder, reduce/exit, and failed-thesis behavior in one end-to-end shadow replay.
4. Keep `real_worker` dry-run until the above lifecycle checks are complete.

Only after those are verified should the project move from dry-run/shadow validation toward any paper/live progression.

## Verification

Patch verification:

- `quant_system_rebuild`: `tests/test_policy_engine.py tests/test_execution_handoff_block_reason.py tests/test_interfaces_runner.py` -> `160 passed`.
- `eth_trading_bot`: `tests/test_state_store.py tests/test_position_manager.py tests/test_automation_gate.py tests/test_bot_runtime_scheduler_script.py tests/test_run_shadow_preflight_cycle_script.py tests/test_exchange_adapter.py` -> `189 passed`.

Additional open-position shadow verification:

- `quant_system_rebuild`: `tests/test_policy_engine.py tests/test_execution_handoff_block_reason.py tests/test_interfaces_runner.py` -> `161 passed`.
- `eth_trading_bot`: `tests/test_shadow_orchestrator.py tests/test_position_manager.py tests/test_state_store.py` -> `99 passed`.
- `eth_trading_bot`: `tests/test_automation_gate.py tests/test_bot_runtime_scheduler_script.py tests/test_run_shadow_preflight_cycle_script.py tests/test_exchange_adapter.py` -> `131 passed`.
