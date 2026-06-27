# Factor / Research Pipeline Repair Plan

> Date: 2026-05-19  
> Scope: repair the `quant_system_rebuild` factor/research pipeline that keeps producing `factor_lookup_empty`, `research_score=0.0`, and `execution_allowed=false`.  
> Boundary: this plan does not loosen live risk limits, enable trading, or change strategy gates before the upstream data fault is fixed.

## Executive Summary

The current primary blocker is not the 15m / 1h / 4h strategy gate. The producer pipeline is stuck earlier:

- `factor_values` and `factor_samples` exist in the analysis database.
- `decision_outcomes` has effectively no matching resolved outcomes for factor lookup.
- `live_governance` lookup has a SQL filter conflict:
  - the unresolved path excludes `decision` and `handoff`;
  - the live scope then excludes `scan_artifact`;
  - the resolved path has no matching outcomes;
  - therefore `filtered_lookup_source` becomes zero rows.
- The minimum operational fix is to remove the `scan_artifact` exclusion from the `live_governance` scope while keeping the evidence-role whitelist and treating those rows as watch/reference evidence.
- `factor_governance.rows=[]`, `factor_lookup_empty`, and `factor_policy_signal=blocked` follow from that.
- `research_candidate_count=22` but `research_score=0.0` is a second P0 pipeline quality issue.

So the next fix should target the factor/research pipeline, not real-order worker behavior and not a fast V-reversal probe bypass.

## Verified Runtime Symptoms

Bulk producer artifact review for `eth-15m` cycles from 2026-05-17 through 2026-05-19 showed:

| Metric | Result |
|---|---:|
| Checked `decision.json` files | 583 |
| Files containing `factor_lookup_empty` | 583 / 583 |
| `factor_governance.rows=[]` | 583 / 583 |
| `research_score=0.0` | 583 / 583 |
| `research_candidate_count=22` | 583 / 583 |
| Matching `handoff.execution_allowed=true` | 0 |
| Producer-side `small_probe` action | 0 |

Latest analysis artifacts also show:

- `runtime/analysis/factor_lookup_summary.json`
  - `lookup_scope=live_governance`
  - `factor_lookup_rows=0`
  - `lookup_rows=[]`
- `runtime/analysis/factor_governance_summary.json`
  - `status=blocked`
  - `rows=[]`
  - `reason_codes=["factor_lookup_empty"]`

## Precise Factor Lookup Root Cause

Relevant code: `D:\开发\quant_system_rebuild\src\analysis\factor_dataset.py`

- `_fetch_factor_lookup_rows(...)`, lines around 753-874
- `_factor_lookup_scope_filter(...)`, lines around 877-890
- `_factor_lookup_unresolved_source_filter(...)`, lines around 893-897

The lookup query builds two sources:

```text
resolved_outcome_values:
  factor_values JOIN decision_outcomes
  WHERE decision_outcomes.status = 'resolved'

unresolved_factor_values:
  factor_values
  WHERE NOT EXISTS matching decision_outcomes
  AND unresolved_source_filter
```

For `live_governance`, the filters are currently incompatible in a no-outcome environment:

| Stage | Filter | Result |
|---|---|---|
| Resolved path | `JOIN decision_outcomes ... status='resolved'` | 0 rows if outcomes do not match factor run IDs |
| Unresolved path | `sample_source NOT IN ('decision','handoff')` | leaves scan-style sources |
| Scope filter | `sample_source <> 'scan_artifact'` | removes the scan-style rows |
| Final lookup | `HAVING COUNT(*) >= min_sample_count` | receives zero rows |

This creates a deterministic empty lookup, not a random data freshness problem.

## Chicken-And-Egg Deadlock

The system is stuck in a circular dependency:

```text
live_governance factor lookup wants resolved decision outcomes
  -> resolved outcomes require trades or resolved paper outcomes
  -> trades require factor lookup to be non-empty
  -> factor lookup stays empty
```

This is why simply waiting for more cycles does not fix the issue. The pipeline can run forever and still produce `rows=[]`.

## Decision Outcomes Gap

The repair plan must explicitly decide how `decision_outcomes` should be populated.

Two viable routes:

1. Backfill `decision_outcomes` from historical paper/shadow decisions.
   - Pros: preserves the original resolved-outcome model.
   - Cons: slower, requires correct outcome labeling, and may still leave live lookup empty until enough outcomes exist.

2. Break the deadlock by allowing unresolved evidence into a watch/reference grade lookup.
   - Pros: restores non-empty governance immediately without pretending evidence is fully validated.
   - Cons: must never be treated as strong support for full-size live entries.

Recommended approach:

- Implement route 2 first for operational recovery.
- Keep route 1 as a parallel backfill task so the system can eventually graduate from watch/reference evidence to resolved evidence.

## P0 Fix Plan

### 1. Add A Factor Lookup Pipeline Diagnostic

The diagnostic should report each SQL stage count, not just `factor_lookup_empty`.

Required fields:

```json
{
  "factor_values_total": 154971,
  "factor_samples_total": 8474,
  "decision_outcomes_total": 1,
  "resolved_outcome_match_count": 0,
  "unresolved_after_source_filter_count": 11492,
  "after_live_governance_scope_filter_count": 0,
  "expected_after_scope_fix_raw_rows": "7000+",
  "expected_grouped_lookup_rows": "50-200",
  "min_sample_count": 1,
  "detected_issue": "live_governance_source_filters_are_mutually_exclusive_without_resolved_outcomes"
}
```

Acceptance:

- A zero-row lookup must explain exactly which stage dropped rows to zero.
- If resolved outcomes are missing, the diagnostic must say so directly.
- If the unresolved path retains only `scan_artifact` and the scope removes `scan_artifact`, the diagnostic must flag the filter conflict.

### 2. Break The Live Governance Deadlock

Preferred minimum fix:

Change only the `live_governance` scope filter first:

```python
# Before
if scope == "live_governance":
    return (
        "COALESCE(sample_source, '') <> 'scan_artifact' "
        "AND COALESCE(evidence_role, '') IN ('entry_support', 'risk', 'veto', 'supporting', 'opposing')"
    )

# Minimum operational fix
if scope == "live_governance":
    return (
        "COALESCE(evidence_role, '') IN ('entry_support', 'risk', 'veto', 'supporting', 'opposing')"
    )
```

Expected effect:

- The unresolved path keeps roughly `11,492` `scan_artifact` rows instead of losing everything after scope filtering.
- The evidence-role whitelist should still filter out roles not intended for live governance.
- After evidence-role filtering, expect roughly `7,000+` raw rows to reach grouping.
- After grouping and `HAVING COUNT(*) >= min_sample_count`, expect at least `50-200` lookup rows. If the grouped result is below `10`, treat it as another filter bug, not a successful repair.

Alternative if the team wants a stricter separation:

- Add a new `live_watch` lookup scope and wire runner/scheduler to use it when resolved governance rows are unavailable.
- This is cleaner architecturally but larger than the minimum unblock.

Guardrails:

- `scan_artifact` / unresolved evidence must not become strong execution support by default.
- It can unblock governance from `factor_lookup_empty`, but sizing and full entry should still require stronger evidence.
- Handoff metadata should distinguish:
  - `resolved_governance`
  - `watch_governance`
  - `scan_reference_governance`

Acceptance:

- `runtime/analysis/factor_lookup_summary.json.factor_lookup_rows > 0`
- expected grouped lookup rows are in the `50-200+` range, not just one accidental row
- `runtime/analysis/factor_governance_summary.json.rows.length > 0`
- governance no longer blocks solely with `factor_lookup_empty`
- if evidence is weak, governance status may be `watch`, but rows must not be empty

### 3. Define Decision Outcome Backfill Ownership

This is the durability milestone, not a prerequisite for the same-day deadlock break. Add or document one owner path for filling `decision_outcomes`.

Questions the implementation must answer:

- Which component writes `decision_outcomes`?
- Are outcomes based on real trades, paper trades, shadow decisions, or replay labels?
- What is the minimum holding horizon before an outcome can be marked `resolved`?
- How are `decision_run_id` and `factor_values.run_id` guaranteed to match?

Acceptance:

- `decision_outcomes` has more than manual or ad hoc rows.
- At least one resolved outcome joins to `factor_values.run_id`.
- The lookup diagnostic reports non-zero `resolved_outcome_match_count` after backfill.

### 4. Diagnose Research Candidates Scoring To Zero

The research path has a separate P0 symptom:

```text
research_candidate_count=22
research_score=0.0
```

Add candidate-level diagnostics:

```json
{
  "candidate_id": "...",
  "qualified": false,
  "score_contribution": 0.0,
  "reject_reasons": [],
  "required_fields_missing": [],
  "sample_count": 0,
  "win_rate": null,
  "net_edge_pct": null,
  "freshness": ""
}
```

Acceptance:

- If 22 candidates exist, the report must explain all 22 zero-score outcomes.
- `research_score=0.0` must not appear without candidate-level reject reasons.
- After repair, either `research_score > 0.0` or `qualified_candidate_count=0` with explicit reasons.

### 5. Fix Negative Factor Lookup Age

Observed producer handoffs include negative `factor_lookup_age_seconds` values such as `-1`, `-3`, `-18`, and `-25`.

This is not the primary blocker, but it can pollute freshness diagnostics.

Likely cause:

- lookup `generated_at` can be a few seconds after the handoff timestamp used for age calculation, or timestamps are rounded/truncated inconsistently.

Acceptance:

- `factor_lookup_age_seconds` should be clamped to `0.0` for small negative clock skew.
- Larger negative values should emit a diagnostic reason such as `factor_lookup_generated_at_in_future`.
- Dashboard should not show negative age as a normal state.

### 6. Improve Dashboard / Bot Projection

Producer canonical truth for the missed window was:

- `factor_lookup_stale=false`
- `factor_lookup_age_seconds=0.0`
- `factor_lookup_generated_at` populated
- `factor_lookup_empty=true`

But bot/dashboard projections can show stale-like fields. The UI must distinguish fresh-but-empty from stale.

Required fields:

- `factor_lookup_rows`
- `factor_governance_status`
- `factor_governance_reason_codes`
- `factor_policy_signal_status`
- `factor_policy_signal_reason_codes`
- `research_score`
- `research_candidate_count`
- `qualified_candidate_count`
- `candidate_execution_package.status`
- `candidate_execution_package.reason`

Acceptance:

- Dashboard explicitly shows `fresh but empty`.
- `factor_lookup_empty` is surfaced before generic `not_entry_action`.
- `candidate_execution_package_missing` links to upstream non-generation reasons.

## P1 Replay Validation

After P0 changes, replay:

| Window | Purpose |
|---|---|
| 2026-05-17 full day | verify cross-day recovery |
| 2026-05-18 14:00-22:00 UTC | verify recent blocked interval |
| 2026-05-18 19:00-20:10 UTC | verify the 2026-05-19 03:00 BJT missed long window |

Replay acceptance:

- factor lookup rows are non-empty
- governance rows are non-empty
- research score is not silently fixed at 0.0
- `factor_policy_signal` is not blocked by `factor_lookup_empty`
- if still no entry occurs, the reason is strategy/risk, not empty factor/research data

## P2 Strategy Gate Review

Only after P0/P1 passes should we review whether 15m / 1h / 4h is too conservative.

Do not use windows with `factor_lookup_empty` and `research_score=0.0` as proof that the strategy gate is too strict. Those windows are contaminated by upstream pipeline failure.

Questions for P2:

- Should 1h/4h control size rather than hard-block every 15m opportunity?
- Should a watch-grade factor lookup allow a smaller probe but not full entry?
- Does `trigger_ready=false` still miss fast V-reversals after factor/research recovery?

## Test Plan

Run tests from Windows PowerShell using the Windows venv. Do not rely on WSL unless `duckdb` and path encoding are confirmed.

### quant_system_rebuild

```powershell
D:\开发\quant_system_rebuild\.venv_win\Scripts\python.exe -m pytest tests\test_factor_governance.py
D:\开发\quant_system_rebuild\.venv_win\Scripts\python.exe -m pytest tests\test_scripts_quant_runtime_scheduler.py
D:\开发\quant_system_rebuild\.venv_win\Scripts\python.exe -m pytest tests\test_research_bundle_loader.py
D:\开发\quant_system_rebuild\.venv_win\Scripts\python.exe -m pytest tests\test_research_health.py
D:\开发\quant_system_rebuild\.venv_win\Scripts\python.exe -m pytest tests\test_interfaces_research_bundle.py
D:\开发\quant_system_rebuild\.venv_win\Scripts\python.exe -m pytest tests\test_scripts_research_refresh_worker.py
```

New tests to add:

- `live_governance` with no resolved outcomes should not deterministically return zero rows when scan/watch evidence exists.
- factor lookup diagnostic reports `resolved_outcome_match_count`, unresolved row count, post-scope row count, and the detected filter conflict.
- decision outcome backfill creates at least one row that joins to `factor_values.run_id`.
- negative factor lookup age is clamped or diagnosed.
- research candidates with `research_score=0.0` expose per-candidate reject reasons.

### eth_trading_bot

```powershell
D:\开发\eth_trading_bot\.venv_win\Scripts\python.exe -m pytest tests\test_dashboard_data_sources.py
D:\开发\eth_trading_bot\.venv_win\Scripts\python.exe -m pytest tests\test_execution_risk_gate.py
D:\开发\eth_trading_bot\.venv_win\Scripts\python.exe -m pytest tests\test_bot_runtime_scheduler_script.py
```

New tests to add:

- dashboard distinguishes producer `factor_lookup_stale=false` from bot-side projection stale.
- dashboard displays `factor_lookup_empty` before generic `not_entry_action`.
- real-order worker skipped state links to upstream candidate package non-generation reason.

## Completion Definition

Split completion into two milestones so the operational unblock is not held hostage by long-running outcome backfill.

### M1: Same-Day Deadlock Break

M1 is complete when:

1. `factor_lookup_summary.factor_lookup_rows > 0`.
2. Grouped lookup rows are in the expected `50-200+` range, or any lower count has a clear diagnostic explanation.
3. `factor_governance_summary.rows.length > 0`.
4. `factor_policy_signal` is not blocked by `factor_lookup_empty`.
5. `research_score=0.0` is either fixed or fully explained per candidate.
6. Replay for 2026-05-17 to 2026-05-19 proves the pipeline is no longer empty.
7. Any remaining no-trade decision is attributable to strategy/risk logic, not lookup deadlock.

### M2: Resolved Outcome Backfill

M2 is complete when:

1. `decision_outcomes` ownership/backfill is documented.
2. `decision_outcomes` has more than manual or ad hoc rows.
3. At least one resolved outcome joins to `factor_values.run_id`.
4. The lookup diagnostic reports non-zero `resolved_outcome_match_count`.
5. The system can eventually graduate from scan/watch governance to resolved-outcome governance.
