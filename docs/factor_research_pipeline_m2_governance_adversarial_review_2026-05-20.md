---
title: Factor / Research Pipeline M2 Governance Adversarial Review
date: 2026-05-20
scope: quant_system_rebuild + eth_trading_bot
status: m2_implemented_verified
---

# Factor / Research Pipeline M2 Governance Adversarial Review

## Scope

This note records the adversarial review for M2 of the factor/research repair path: graduating resolved outcome evidence from `reference/watch` into active factor governance only when the evidence source and governance thresholds both permit it.

This document does not authorize live execution. It also does not authorize lowering governance thresholds, relaxing research gates, enabling real order submission, or treating current shadow evidence as active entry support.

Related documents:

- `docs/factor_research_pipeline_ds_adversarial_review_2026-05-20.md`
- `docs/factor_research_pipeline_repair_plan_2026-05-19.md`

## Verdict

M2 can proceed, but it must be treated as path engineering, not data fitting.

The current absence of active governance is not a single bug. It is the result of two independent safety locks:

1. SQL grade lock: shadow outcome sources are forced to `reference`.
2. Governance threshold lock: even if raw grades were allowed through, the currently joined resolved outcome rows do not meet `min_sample_count`, `min_win_rate`, or positive expectancy requirements.

Therefore the correct M2 goal is not "make current governance rows active." The correct goal is to build and test a safe graduation path that remains inactive for current shadow data, but can activate later when validated/paper/real resolved evidence is sufficient.

## Implementation Status

Status: implemented and verified on 2026-05-20.

Landed changes:

- `quant_system_rebuild/src/analysis/decision_outcomes.py`
  - Added explicit outcome source constants:
    - `VALIDATED_SHADOW_OUTCOME_SOURCE = "validated_shadow_cycle_forward_return"`
    - `PAPER_RESOLVED_OUTCOME_SOURCE = "paper_resolved"`
    - `REAL_FILL_OUTCOME_SOURCE = "real_fill"`
  - Added `GOVERNANCE_GRADE_PASSTHROUGH_OUTCOME_SOURCES`.
  - Kept `SHADOW_OUTCOME_SOURCE = "shadow_cycle_forward_return"` as the default shadow backfill source.
- `quant_system_rebuild/src/analysis/factor_dataset.py`
  - Updated resolved-outcome grade SQL so allowlisted sources pass through raw `factor_grade` before the `LIKE 'shadow_%'` fallback.
  - Kept plain shadow outcomes and scan artifacts downgraded to `reference`.
- `quant_system_rebuild/src/analysis/__init__.py`
  - Exported the source constants for producer/test use.
- `quant_system_rebuild/tests/test_interfaces_analysis.py`
  - Added temp-DuckDB tests proving:
    - plain `shadow_cycle_forward_return` remains `reference`;
    - `validated_shadow_cycle_forward_return` can preserve raw `core`;
    - low-sample allowlisted evidence remains non-active under default governance;
    - synthetic high-quality allowlisted evidence can become `active/support`.

Verification:

```powershell
D:\开发\quant_system_rebuild\.venv_win\Scripts\python.exe -m pytest tests\test_interfaces_analysis.py -k "shadow_outcomes_to_reference or validated_shadow"
# 3 passed

D:\开发\quant_system_rebuild\.venv_win\Scripts\python.exe -m pytest tests\test_interfaces_analysis.py -k "factor_lookup or decision_outcomes or shadow"
# 28 passed

D:\开发\quant_system_rebuild\.venv_win\Scripts\python.exe -m pytest tests\test_factor_governance.py tests\test_interfaces_analysis.py -k "factor_governance or factor_lookup or decision_outcomes or shadow"
# 42 passed
```

Governance thresholds were not changed. This implementation does not enable live order submission, does not create candidate execution packages, and does not promote current shadow evidence into active support.

## Post-Landing Verification

Status: passed on 2026-05-20.

### Offline Runtime Artifact Rebuild

Artifacts:

- `quant_system_rebuild/runtime/analysis/factor_lookup_m2_postlanding_20260520.json`
- `quant_system_rebuild/runtime/analysis/factor_lookup_m2_postlanding_20260520.md`
- `quant_system_rebuild/runtime/analysis/factor_governance_m2_postlanding_20260520.json`
- `quant_system_rebuild/runtime/analysis/factor_bucket_config_m2_postlanding_20260520.json`

Command:

```powershell
.venv_win\Scripts\python.exe -m interfaces.analysis build-factor-lookup `
  --db-path runtime\analysis\quant_analysis.duckdb `
  --symbol ETH `
  --timeframe 15m `
  --lookup-version m2_postlanding_20260520 `
  --lookup-scope live_governance `
  --min-sample-count 1 `
  --bucket-config-output runtime\analysis\factor_bucket_config_m2_postlanding_20260520.json `
  --output runtime\analysis\factor_lookup_m2_postlanding_20260520.json `
  --markdown-output runtime\analysis\factor_lookup_m2_postlanding_20260520.md `
  --governance-output runtime\analysis\factor_governance_m2_postlanding_20260520.json
```

Observed evidence:

| Check | Result |
|---|---:|
| `factor_lookup_rows` | 95 |
| `decision_outcomes_total` | 8 |
| `resolved_outcome_match_count` | 158 |
| lookup grade counts | `reference=95` |
| governance status | `watch` |
| governance lifecycle counts | `watch=95` |
| active support rows | 0 |

DB source verification:

| Source check | Result |
|---|---:|
| `shadow_cycle_forward_return` outcomes | 7 |
| empty / NULL source outcomes | 1 |
| joined rows from `shadow_cycle_forward_return` | 158 |
| joined runs from `shadow_cycle_forward_return` | 7 |
| allowlisted joined rows | 0 |

This confirms the M2 allowlist did not promote current runtime shadow evidence. Current production-like evidence remains `reference/watch`.

### Strict-Live One-Shot

Valid strict-live run:

- Cycle: `quant_system_rebuild/runtime/cycles/eth-15m-20260520T125100Z-a1b2c3d4`
- Scheduler status: `ok`
- Scheduler issues: none
- Files present:
  - `live_bundle.json`
  - `research.json`
  - `decision.json`
  - `snapshot_registry.json`
  - `handoff.json`
  - `scheduler_status.json`

Key assertions:

| Check | Result |
|---|---:|
| `snapshot_registry.run_id` | `eth-15m-20260520T125100Z-a1b2c3d4` |
| snapshot registry records | 6 |
| `handoff.execution_allowed` | `false` |
| `handoff.current_position_direction` | `neutral` |
| `handoff.position_size_pct` | 0.0 |
| `handoff.position_state` | `ARMED` |
| candidate execution package exists | `false` |
| `handoff.research_score` | 0.0 |
| `handoff.research_candidate_count` | 9 |
| `handoff.qualified_candidate_count` | 0 |
| handoff has `candidate_pool_diagnostics` | `true` |
| `decision.action` | `wait` |
| `execution_block_reason` | `not_entry_action` |

This confirms the one-shot strict-live path no longer stops at `incomplete_snapshot_only`; the snapshot registry, decision, research bundle, and handoff are all produced in the same cycle.

Note: one earlier manual attempt used invalid run id `eth-15m-20260520T125000Z-m2post` and correctly blocked with `invalid_run_id_format`. That cycle is not M2 evidence and was excluded from the clean replay.

### Replay / Smoke Validation

Artifacts:

- `quant_system_rebuild/runtime/replay/pipeline_validation_m2_postlanding_clean/summary.json`
- `quant_system_rebuild/runtime/replay/pipeline_validation_m2_postlanding_clean/summary.md`

Command:

```powershell
.venv_win\Scripts\python.exe -m interfaces.analysis pipeline-replay-validation `
  --cycles-root runtime\cycles `
  --output-root runtime\replay\pipeline_validation_m2_postlanding_clean `
  --symbol ETH `
  --timeframe 15m `
  --window m2_clean_1110:20260520T111000Z:20260520T112000Z `
  --window m2_clean_1251:20260520T125100Z:20260520T125200Z `
  --max-cycles-per-window 20 `
  --factor-governance-path runtime\analysis\factor_governance_summary.json `
  --factor-lookup-path runtime\analysis\factor_lookup_summary.json `
  --whitelist-path runtime\fresh_research\whitelist.json `
  --all-results-path runtime\fresh_research\all_results.json `
  --output runtime\replay\pipeline_validation_m2_postlanding_clean\summary.json `
  --markdown-output runtime\replay\pipeline_validation_m2_postlanding_clean\summary.md
```

Replay result:

| Check | Result |
|---|---:|
| status | `pass` |
| production effect | `none` |
| execution allowed | `false` |
| window count | 2 |
| window pass count | 2 |
| replayed cycle count | 2 |
| failed cycle count | 0 |
| factor lookup rows | 95 |
| factor governance rows | 95 |
| `no_factor_lookup_empty_blocks` | `true` |
| `research_zero_not_silent` | `true` |
| `remaining_no_trade_explained` | `true` |

Replay items:

| Cycle | Status | Action | Execution | Governance | No-trade family |
|---|---|---|---|---|---|
| `eth-15m-20260520T111038Z-f63870dc` | pass | wait | false | watch | `research_quality_explained` |
| `eth-15m-20260520T125100Z-a1b2c3d4` | pass | wait | false | watch | `research_quality_explained` |

This confirms M2 did not change the no-trade boundary: current evidence remains watch-only, research zero is explained, and no live execution path or candidate execution package is enabled.

## Root Cause

### Lock 1: SQL Source Lock

Location:

- `quant_system_rebuild/src/analysis/factor_dataset.py:1049`

Current behavior:

```python
"WHEN COALESCE(o.outcome_source, '') LIKE 'shadow_%' THEN 'reference' "
```

Effect:

- Any resolved outcome with `outcome_source` beginning with `shadow_` is downgraded to `reference` before governance evaluation.
- Current source `shadow_cycle_forward_return` is intentionally not allowed to promote raw `core` / `enhancer` grade.

This is a safety rule, not a bug. It prevents unvalidated shadow-forward-return observations from becoming active governance support.

### Lock 2: Governance Threshold Lock

Location:

- `quant_system_rebuild/src/policy/factor_governance.py:14-15`
- `quant_system_rebuild/src/policy/factor_governance.py:147-155`

Current thresholds:

```python
min_sample_count: int = 30
min_win_rate: float = 0.53
```

Active support also requires:

- `factor_grade in {"core", "enhancer"}`
- `net_expectancy_pct > 0`
- `stop_hit_rate <= max_stop_hit_rate`
- lookup not stale

Effect:

- Even if the SQL source lock is bypassed, current resolved rows do not qualify for active support.
- Lowering these thresholds to fit the current shadow data would turn low-quality noise into active governance, which is explicitly out of scope.

## Current Evidence

### Decision Outcomes

Current `decision_outcomes` distribution in `quant_system_rebuild/runtime/analysis/quant_analysis.duckdb`:

| Source | Outcome count | Outcomes with factor values |
|---|---:|---:|
| `shadow_cycle_forward_return` | 7 | 7 |
| `NULL` | 1 | 0 |

The `NULL` row does not join to `factor_values` because its `decision_run_id` does not match any factor value run id.

Resolved join evidence:

| Source | Joined rows | Joined runs |
|---|---:|---:|
| `shadow_cycle_forward_return` | 158 | 7 |

Therefore:

- `resolved_outcome_match_count=158` comes entirely from `shadow_cycle_forward_return`.
- No current joined resolved row escapes the SQL source lock.

### Raw Grade Before SQL Downgrade

Direct DB inspection of the resolved join shows:

| Raw grade | Effective grade | Source | Joined rows | Grouped keys |
|---|---|---|---:|---:|
| `core` | `reference` | `shadow_cycle_forward_return` | 10 | 2 |
| `enhancer` | `reference` | `shadow_cycle_forward_return` | 69 | 21 |
| `reference` | `reference` | `shadow_cycle_forward_return` | 51 | 15 |

The current direct `quant_analysis.duckdb` resolved live-governance join has max grouped sample count `5` for raw `core` / `enhancer` rows, win rate `0.2`, and negative net expectancy. Earlier M2 artifacts showed similarly failing data under a different grouping snapshot, with no path reaching `30` samples, `0.53` win rate, and positive expectancy.

The exact max per snapshot is less important than the invariant: current resolved shadow evidence fails both locks.

## Correct M2 Design

### Source Policy

Use an explicit allowlist, not a naming hack.

Default policy:

| `outcome_source` | Grade policy |
|---|---|
| `shadow_cycle_forward_return` | Force `reference` |
| `validated_shadow_cycle_forward_return` | Allow raw `factor_grade` to pass to governance |
| `paper_resolved` | Allow raw `factor_grade` to pass to governance |
| `real_fill` | Allow raw `factor_grade` to pass to governance |
| empty / unknown / unlisted | Keep conservative default, no active promotion |

Important naming detail:

- A source named `shadow_cycle_forward_return_validated` still matches `LIKE 'shadow_%'`.
- If SQL keeps the current shadow deny rule, validated sources must either be checked by allowlist before the shadow rule or use a non-`shadow_` prefix.
- Preferred implementation is explicit allowlist before the shadow fallback because the intent is clear and testable.

Expected SQL shape:

```sql
CASE
  WHEN COALESCE(o.outcome_source, '') IN (
    'validated_shadow_cycle_forward_return',
    'paper_resolved',
    'real_fill'
  ) THEN fv.factor_grade
  WHEN COALESCE(o.outcome_source, '') LIKE 'shadow_%' THEN 'reference'
  WHEN COALESCE(fv.sample_source, '') = 'scan_artifact' THEN 'reference'
  ELSE fv.factor_grade
END
```

### Source Definition Side

Do not leave source names as SQL-only magic strings.

Implementation should also cover:

- `quant_system_rebuild/src/analysis/decision_outcomes.py:32`
  - Current constant: `SHADOW_OUTCOME_SOURCE = "shadow_cycle_forward_return"`
  - Add constants for allowlisted sources, such as:
    - `VALIDATED_SHADOW_OUTCOME_SOURCE = "validated_shadow_cycle_forward_return"`
    - `PAPER_RESOLVED_OUTCOME_SOURCE = "paper_resolved"`
    - `REAL_FILL_OUTCOME_SOURCE = "real_fill"`
- `quant_system_rebuild/src/analysis/duckdb_store.py:211`
  - The schema already has `outcome_source VARCHAR`; no table migration is required for the source string itself.
  - Add documentation/contract coverage if this module remains the schema source of truth.
- `DecisionOutcome` / upsert paths
  - Ensure validated/paper/real sources can be written intentionally.
  - Keep shadow backfill defaulting to `SHADOW_OUTCOME_SOURCE`.

This source definition work prevents a cross-layer gap where SQL has an allowlist that no producer or test path can safely use.

## Required Tests

M2 tests should use temporary DuckDB fixtures, not production DB mutation.

Minimum coverage:

1. Existing shadow stays reference.
   - Insert resolved outcome with `outcome_source="shadow_cycle_forward_return"`.
   - Build factor lookup.
   - Assert resolved rows are `factor_grade="reference"`.

2. Allowlisted source permits raw grade through.
   - Insert resolved outcome with an allowlisted source.
   - Insert matching `factor_values` with raw `factor_grade="core"` or `enhancer`.
   - Build factor lookup.
   - Assert lookup row preserves raw active grade before governance thresholding.

3. Low-quality allowlisted evidence still does not become active.
   - Use allowlisted source but below `min_sample_count=30`, below `min_win_rate=0.53`, or non-positive `net_expectancy_pct`.
   - Assert governance remains `watch` or appropriate negative/veto behavior.

4. High-quality allowlisted evidence can become active.
   - Construct synthetic test rows with:
     - sample count `>=30`
     - win rate `>=0.53`
     - positive net expectancy
     - acceptable stop hit rate
     - raw grade `core` or `enhancer`
   - Run `build_factor_lookup` and `evaluate_factor_governance`.
   - Assert `factor_lifecycle="active"` and `factor_effect="support"`.

5. Thresholds are not lowered.
   - Tests should use default governance thresholds unless explicitly testing threshold behavior.
   - Do not set `min_sample_count=1` for the active-support proof.

## Implementation Plan

1. Define source constants and source policy.
   - Add allowlisted source constants in `decision_outcomes.py`.
   - Keep `SHADOW_OUTCOME_SOURCE` unchanged for shadow backfill.

2. Add grade resolution helper.
   - Centralize the allowlist and downgrade logic near `_factor_lookup_resolved_grade_sql`.
   - Allowlist must run before the `LIKE 'shadow_%'` rule.

3. Add temp-DuckDB M2 tests.
   - Prove current shadow stays `reference`.
   - Prove allowlisted high-quality resolved evidence can become active support.
   - Prove allowlisted but weak evidence remains non-active.

4. Rebuild M2 artifacts offline.
   - Generate lookup/governance summaries into analysis artifacts.
   - Confirm current production-like data remains `reference/watch`.

5. Re-run strict-live and replay validation.
   - Verify no accidental live execution enablement.
   - Verify no weak current shadow data is promoted.

## Non-Goals

Do not:

- Lower `min_sample_count=30`.
- Lower `min_win_rate=0.53`.
- Treat `shadow_cycle_forward_return` as active support.
- Treat `scan_artifact` rows as active governance.
- Promote current `reference/watch` rows to active by migration.
- Enable live order submission or candidate execution packages from this M2 work.

## Acceptance Criteria

M2 is accepted only when all of the following are true:

- Plain shadow resolved outcomes still produce `reference` lookup grade.
- Allowlisted validated/paper/real outcomes can preserve raw `core` / `enhancer` grade.
- Governance thresholds remain intact.
- A synthetic high-quality resolved outcome path produces `active/support` in tests.
- Current runtime evidence remains non-active unless it naturally satisfies source policy and thresholds.
- Documentation states that M2 creates a future-safe graduation path, not current live readiness.

## Final Statement

M2 is approved as a safety-preserving graduation path. It must not be implemented as a shortcut to make the current 7 shadow-resolved runs active.

The current evidence says: resolved outcomes are joining, but all joined resolved rows come from shadow data and fail the governance wall. That is the desired conservative behavior until validated, paper, or real outcomes accumulate enough positive evidence.
