---
title: Factor / Research Pipeline DS Adversarial Review
date: 2026-05-20
scope: quant_system_rebuild + eth_trading_bot
status: m1_replay_passed_m2_implemented
---

# Factor / Research Pipeline DS Adversarial Review

## Scope

This note records the adversarial review result and the landing status for the factor/research repair work tracked in:

- `docs/factor_research_pipeline_repair_plan_2026-05-19.md`
- `docs/trigger_ready_small_probe_contract_review_2026-05-20.md`

This document does not authorize live execution. `real_worker` must remain dry-run / no real order submission until the pipeline produces complete, reviewed, and replay-validated candidate execution packages.

## Verdict

The DS review was correct: the earlier progress summary was directionally right, but missed blocking cross-artifact and runtime facts. Those M1 blockers have now been addressed and verified.

M1 is now replay-passed by evidence:

- Factor lookup remains non-empty: `factor_lookup_rows=95`.
- Governance remains non-empty: `factor_governance_rows=95`.
- The decision path is no longer blocked by `factor_lookup_empty`.
- `research_score=0.0` is now handed off with full candidate diagnostics and is not silent.
- Strict-live one-shot cycle produced the complete artifact chain again.
- Offline replay over the required windows passed: `343` replayed cycles, `0` failed cycles.

This is not live-trading readiness. Research remains `fresh_but_unqualified`: the refreshed candidate plan has `candidate_count=42`, `qualified_candidate_count=0`, and dominant failure `wf_trade_share_low`. No candidate execution package is authorized from this result.

M2 has since been implemented and verified as a safety-preserving graduation path. Current runtime shadow evidence remains `reference/watch`; the active-support path is only available for allowlisted validated/paper/real resolved outcome sources that also satisfy unchanged governance thresholds. The M2 adversarial review and post-landing evidence are captured in `docs/factor_research_pipeline_m2_governance_adversarial_review_2026-05-20.md`.

## Evidence Check

| Claim | Evidence | Verdict |
|---|---|---|
| `factor_lookup_rows=95` | `quant_system_rebuild/runtime/analysis/factor_lookup_summary.json` and replay summary | Confirmed |
| `decision_outcomes_total=8` | `lookup_diagnostics.decision_outcomes_total` | Confirmed |
| `resolved_outcome_match_count=158` | `lookup_diagnostics.resolved_outcome_match_count` | Confirmed |
| `factor_lookup_stale=false` | Latest complete strict-live handoff `eth-15m-20260520T111038Z-f63870dc` | Confirmed for latest complete cycle |
| `research_score=0.0` | Latest complete strict-live handoff and replay summary | Confirmed and explained |
| `research_candidate_count=9` in latest complete handoff | `handoff.json` from `eth-15m-20260520T111038Z-f63870dc` | Confirmed |
| `candidate_pool_diagnostics` reaches handoff | `handoff.json` contains full diagnostics under `candidate_pool_diagnostics.datasets.whitelist.candidates` | Confirmed |
| Research refresh found no live-ready candidates | `runtime/fresh_research/research_refresh_result.json` | Confirmed: `fresh_but_unqualified` |
| Strict-live quant can produce complete artifacts again | `runtime/cycles/eth-15m-20260520T111038Z-f63870dc/` | Confirmed |
| Snapshot registry belongs to same cycle dir | `scheduler_status.metadata.artifacts.snapshot_registry` | Confirmed |
| 2026-05-17 through 2026-05-19 replay | `runtime/replay/pipeline_validation_m1/summary.json` | Confirmed: `status=pass` |
| Live order boundary remains closed | Handoff has `execution_allowed=false`; replay has `production_effect=none` | Confirmed |

## Current M1 Status

| # | M1 condition | Status |
|---|---|---|
| 1 | `factor_lookup_summary.factor_lookup_rows > 0` | Done: 95 rows |
| 2 | grouped lookup rows in expected `50-200+` range or explained | Done: 95 rows |
| 3 | `factor_governance_summary.rows.length > 0` | Done: 95 rows |
| 4 | `factor_policy_signal` not blocked by `factor_lookup_empty` | Done: no replayed cycle is blocked by `factor_lookup_empty` |
| 5 | `research_score=0.0` fixed or fully explained per candidate | Done for M1: score remains 0, but is handed off and explained with full pool diagnostics |
| 6 | replay for 2026-05-17 through 2026-05-19 | Done: 343 replayed cycles, 0 failed |
| 7 | remaining no-entry decisions attributable to strategy/risk, not pipeline failure | Done for M1 replay: remaining no-trade family is `research_quality_explained` |

## Landed Gap Status

### Gap 1: Research Score Missing From Handoff

Previous finding:

- `decision.json` had `research_score=0.0`.
- `handoff.json` did not expose `research_score`, `research_candidate_count`, or candidate pool diagnostics.
- Bot/dashboard consumers could not see the research result even if research later produced a positive score.

Landed fix:

- `src/contracts/execution.py` extends the execution handoff contract with:
  - `research_score`
  - `research_candidate_count`
  - `qualified_candidate_count`
  - `research_top_candidate_id`
  - `research_top_candidate_win_rate`
  - `research_top_candidate_total_triggers`
  - `candidate_pool_diagnostics`
- `src/interfaces/runner.py` and `src/interfaces/scheduler.py` merge research metadata into the decision envelope before handoff creation.
- `eth_trading_bot/dashboard/data_sources.py` exposes the handoff research fields, with decision metadata fallback.

Verification:

- Latest complete handoff `eth-15m-20260520T111038Z-f63870dc` contains:
  - `research_score=0.0`
  - `research_candidate_count=9`
  - `qualified_candidate_count=0`
  - `candidate_pool_diagnostics.candidate_count=18`
  - `candidate_pool_diagnostics.datasets.whitelist.candidate_count=9`
  - `candidate_pool_diagnostics.datasets.whitelist.candidates` with 9 entries
- Bot/dashboard tests cover both handoff and decision fallback paths.

Status: fixed for M1.

### Gap 2: Candidate Reject Reasons Were Partial

Previous finding:

- `research.json` had aggregate reject distribution.
- Only top candidate diagnostics were available.
- The repair plan required per-candidate diagnostics for every candidate considered.

Landed fix:

- `src/research/bundle_loader.py` now emits full candidate-pool diagnostics under `candidate_pool_diagnostics.datasets.<dataset>.candidates`.
- Unqualified candidates include `qualified=false`, `score_contribution=0.0`, and candidate-level `reject_reasons`.

Verification:

- Latest complete handoff includes full whitelist diagnostics at `candidate_pool_diagnostics.datasets.whitelist.candidates`.
- Dominant failure in the strict-live cycle is `wf_quality_insufficient`.
- Reason distribution includes `wf_quality_insufficient`, `wf_trade_share_low`, `candidate_authenticity_not_live_ready`, `single_day_signal_cluster`, `signal_count_low`, `wf_trade_count_low`, and related quality blockers.

Status: fixed for M1. Research still rejects every current candidate, but the rejection is now visible and candidate-level.

### Gap 3: Strict-Live Pipeline Had Stopped Producing Complete Cycles

Previous finding:

- The latest strict-live cycle before repair stopped at `incomplete_snapshot_only`.
- It had no complete `handoff.json`, `decision.json`, or `research.json`.
- Bot shadow polling could continue, but quant strict-live was not producing a complete decision chain.

Landed fix:

- `src/interfaces/live_judgement.py` and `scripts/quant_runtime_scheduler.py` now thread the outer `run_id` and `artifact_root` so `snapshot_registry.json` lands in the same cycle directory as the rest of the artifacts.

Verification:

- One-shot strict-live run succeeded with:
  - `status=ok`
  - `run_id=eth-15m-20260520T111038Z-f63870dc`
- The cycle directory contains:
  - `snapshot_registry.json`
  - `live_bundle.json`
  - `research.json`
  - `decision.json`
  - `handoff.json`
  - `scheduler_status.json`
- `scheduler_status.metadata.artifacts.snapshot_registry` points to the same cycle directory.

Operational note:

- This proves the strict-live cycle path can complete after the fix.
- Persistent daemon/service restart status is a separate operational step and is not treated as live execution authorization.

Status: fixed for M1 cycle completeness.

### Gap 4: Resolved Outcomes Are Not Yet Active Governance

Previous finding:

- Lookup diagnostics show `decision_outcomes_total=8` and `resolved_outcome_match_count=158`.
- Governance rows are still `factor_grade=reference` and `factor_lifecycle=watch`.

Current status:

- M2 is implemented and verified.
- Plain shadow outcomes remain forced to `reference/watch`.
- Allowlisted validated/paper/real resolved outcome sources can preserve raw `core` / `enhancer` grade before governance thresholding.
- Default governance thresholds remain unchanged, and current runtime evidence still produces no active support rows.
- This is acceptable because M2 creates a future-safe graduation path without promoting current shadow evidence.

Completed M2 work:

- Defined explicit allowlisted outcome sources for grade passthrough.
- Proved plain shadow remains `reference`.
- Proved allowlisted high-quality synthetic evidence can become `active/support`.
- Rebuilt post-landing lookup/governance artifacts and confirmed current runtime evidence remains `reference/watch`.
- Re-ran strict-live and replay validation without opening the live/no-trade boundary.

Status: fixed for M2. See `docs/factor_research_pipeline_m2_governance_adversarial_review_2026-05-20.md`.

## Research Refresh Result

Research was refreshed after the diagnostic fixes.

Evidence: `quant_system_rebuild/runtime/fresh_research/research_refresh_result.json`

Key fields:

- `status=fresh_but_unqualified`
- `generated_at=2026-05-20T11:03:57Z`
- `candidate_refresh_plan.candidate_count=42`
- `candidate_refresh_plan.qualified_candidate_count=0`
- `candidate_refresh_plan.dominant_failure_reason=wf_trade_share_low`
- blocking metrics include:
  - `wf_trade_share_low`
  - `candidate_authenticity_not_live_ready`
  - `wf_quality_insufficient`
  - `time_alignment_sensitive_factor`
  - `wf_dispersion_high`
  - `factor_value_repetition_high`
  - `signal_count_low`
  - `wf_trade_count_low`

Interpretation:

- The WF/window and diagnostic path has been re-run.
- The current candidate pool is still not live-ready.
- This is a valid no-trade / no-package outcome, not a silent pipeline failure.

## Replay Validation

Evidence:

- `quant_system_rebuild/runtime/replay/pipeline_validation_m1/summary.json`
- `quant_system_rebuild/runtime/replay/pipeline_validation_m1/summary.md`

Command:

```powershell
D:\开发\quant_system_rebuild\.venv_win\Scripts\python.exe -m interfaces.analysis pipeline-replay-validation --cycles-root runtime\cycles --output-root runtime\replay\pipeline_validation_m1 --output runtime\replay\pipeline_validation_m1\summary.json --markdown-output runtime\replay\pipeline_validation_m1\summary.md
```

Result:

- `status=pass`
- `production_effect=none`
- `execution_allowed=false`
- `sampled=false`
- `replayed_cycle_count=343`
- `failed_cycle_count=0`
- `factor_lookup_rows=95`
- `factor_governance_rows=95`

Acceptance:

- `factor_lookup_rows_non_empty=true`
- `factor_governance_rows_non_empty=true`
- `no_factor_lookup_empty_blocks=true`
- `research_zero_not_silent=true`
- `remaining_no_trade_explained=true`

Window breakdown:

| Window | Replayed | Failed | No-trade family |
|---|---:|---:|---|
| `2026-05-17_full_day` | 247 | 0 | `research_quality_explained` |
| `2026-05-18_1400_2200_utc` | 85 | 0 | `research_quality_explained` |
| `2026-05-18_1900_2010_utc` | 11 | 0 | `research_quality_explained` |

Interpretation:

- M1 items 6-7 are now satisfied by offline replay evidence.
- Remaining no-trade decisions are explained by research quality gates, not by `factor_lookup_empty` or missing research diagnostics.
- Replay writes only offline evidence under `runtime/replay/pipeline_validation_m1`; it does not authorize live trading.

## Test Evidence

Quant repo tests run:

```powershell
D:\开发\quant_system_rebuild\.venv_win\Scripts\python.exe -m pytest tests\test_execution_handoff_block_reason.py tests\test_research_bundle_loader.py tests\test_interfaces_research_bundle.py tests\test_interfaces_scheduler.py
```

Result: `143 passed` with a `.pytest_cache` permission warning.

```powershell
D:\开发\quant_system_rebuild\.venv_win\Scripts\python.exe -m pytest tests\test_interfaces_runner.py tests\test_scripts_quant_runtime_scheduler.py
```

Result: `82 passed` with a `.pytest_cache` permission warning.

```powershell
D:\开发\quant_system_rebuild\.venv_win\Scripts\python.exe -m pytest tests\test_scripts_quant_runtime_scheduler.py tests\test_interfaces_scheduler.py tests\test_interfaces_runner.py
```

Result: `123 passed` with a `.pytest_cache` permission warning.

```powershell
D:\开发\quant_system_rebuild\.venv_win\Scripts\python.exe -m pytest tests\test_interfaces_analysis.py -k "pipeline_replay_validation"
```

Result: `2 passed` with a `.pytest_cache` permission warning.

Bot/dashboard tests run:

```powershell
D:\开发\quant_system_rebuild\.venv_win\Scripts\python.exe -m pytest tests\test_dashboard_data_sources.py tests\test_bot_runtime_scheduler_script.py
```

Result: `54 passed`.

## Corrected Progress Statement

The earlier DS priority order has now been executed for M1:

1. Handoff research score propagation fixed and verified.
2. Full candidate pool diagnostics added and verified.
3. Research refresh re-run and shown to be fresh but unqualified.
4. Strict-live one-shot cycle repaired and shown to produce complete artifacts.
5. 2026-05-17 through 2026-05-19 replay run and passed.

Current state:

- M1 is complete by replay evidence.
- M2 is implemented and verified by the dedicated governance adversarial review.
- Research remains unqualified and should continue to block live execution.
- No candidate execution package should be treated as live-ready from this state.
- Current runtime governance remains `reference/watch`; future active support requires allowlisted evidence plus unchanged threshold satisfaction.

## Next Priority

1. Keep live execution disabled while research remains `fresh_but_unqualified`.
2. Keep current shadow evidence as `reference/watch`; do not treat M2 as live readiness.
3. Continue collecting validated/paper/real resolved outcomes before expecting active governance support.
4. Re-run strict-live and replay validation after any new candidate-package or live progression change.

## Non-Goals

Do not loosen live order gates, enable real order submission, increase risk, or treat watch/reference governance as active entry support as part of this M1 repair.
