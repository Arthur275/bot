# 2026-05-18 missed entry/probe incident

> Scope: diagnose why ETH dropped sharply but the system produced no `entry` or `small_probe`. This document is read-only incident analysis and does not authorize live orders, risk-limit changes, or probe bypasses.

## Current Finding

The issue is not in the final exchange submission layer. The real worker had no executable candidate package to consume. The upstream quant/policy chain did not produce any `entry` or `small_probe` intent during the inspected window.

Observed facts from runtime artifacts:

- Handoff windows differed by counting method, but both counts showed `entry/small_probe = 0`.
- One count using file `LastWriteTime` saw 248 handoffs: `wait=189`, `observe_only=59`.
- Another count using `generated_at` / directory filtering saw 172 handoffs.
- Both counts agreed there were no executable entry/probe handoffs and no `execution_allowed=true`.
- Trigger-ready windows existed, but they still resolved to `action=wait`.

## Corrected Interpretation

The fine-grained reasons are present in the artifacts, but not in the execution-facing block fields.

For trigger-ready handoffs:

- `execution_block_reason = not_entry_action`
- `execution_block_reason_codes = []`
- `transition_reason_codes` includes:
  - `entry_thesis_below_floor`
  - `entry_research_score_below_floor`
  - `setup_ready_waiting_trigger`

Therefore the issue is not that the artifacts lack the detailed reason. The issue is that dashboard/status paths that only consume `execution_block_reason` or `execution_block_reason_codes` will show only `not_entry_action` and miss the actual policy reasons.

## Root-Cause Direction

The strongest current hypothesis is a multi-layer upstream decision failure:

1. `thesis_score` and `confidence` appear frozen in trigger-ready windows.
   - Repeated trigger-ready handoffs showed the same values across hours and directions:
     - `confidence = 0.5924861611009046`
     - `thesis_score = 0.5483766712290262`
   - This is unlikely to be normal live scoring behavior. It suggests a stale input, cached/fallback score, or fixed degradation template.

2. Research scoring is not cleanly represented in handoff.
   - `research_health.json` contains `research_score = 0.0`, `qualified_candidate_count = 0`, and fresh-but-unqualified state.
   - The handoff field for `research_score` is empty/null in the inspected trigger-ready artifacts.
   - This means research output exists, but the execution handoff is not carrying a clear numeric research score for diagnosis and policy reasoning.

3. Hard research blocks close the probe paths.
   - Current research reason codes include hard blockers such as:
     - `wf_dispersion_high`
     - `wf_return_drift_high`
     - `truth_candidate_unqualified`
   - Probe resolvers treat hard research block states as non-probeable in most paths.
   - As a result, `small_probe` is not acting as a safety valve under this research state.

4. Observability is insufficient.
   - The real reason for `not_entry_action` is in `transition_reason_codes`.
   - Execution-facing fields and dashboards can hide this and make the behavior look like a generic non-entry decision.

## Factor Lookup Staleness Finding

The latest checked quant handoff showed a stale factor lookup being reported as fresh:

- Handoff path: `D:\开发\quant_system_rebuild\runtime\cycles\eth-15m-20260518T020203Z-677a8ec4\handoff.json`
- `generated_at = 2026-05-18T02:02:03Z`
- `factor_lookup_generated_at = 2026-05-16T19:06:55`
- observed lookup age at that handoff: about `30.92h`
- `factor_lookup_stale = False`

This confirms a real freshness-detection problem. The implementation issue is more specific than "threshold is infinite":

- `FactorGovernanceThresholds.max_lookup_age_sec` defaults to `86400` seconds, so the built-in threshold is 24 hours.
- `evaluate_factor_governance()` reports `factor_lookup_empty` when lookup rows are empty, but because the stale branch is `elif lookup_stale`, an empty stale lookup does not also emit `factor_lookup_stale`.
- `build_execution_handoff_payload()` resolves `factor_lookup_stale` from explicit metadata or from governance reason codes. It does not independently recompute staleness from `factor_lookup_generated_at`.
- Result: an old empty lookup can be serialized into handoff as `factor_lookup_stale=False`, even though its timestamp is already beyond the governance threshold.

The practical root cause is therefore:

1. stale lookup reason is swallowed by the empty-lookup branch;
2. handoff serialization does not independently recompute lookup age;
3. dashboard/status can then display a stale scoring input as normal.

## Required Freshness Guardrails

Fixing only one stale flag is not enough. The next design should use three independent layers.

Safety principle:

- Self-healing is allowed only when the recovery action is deterministic, bounded, idempotent, and verifiable.
- If the system cannot determine the correct recovery action, it must alert and fail closed for live `entry` / `small_probe`.
- Unknown freshness failures must not trigger blind rebuilds, old-artifact reuse, synthetic scores, or automatic gate relaxation.

Decision tree:

```text
factor_lookup over-threshold
  ├─ process-level failure: builder did not run / was not scheduled
  │    -> self-heal: trigger one bounded rebuild, then re-validate artifact freshness
  ├─ data-level failure: rebuild fails, source DB is stale/missing, research remains unqualified,
  │   or wf stays dispersion/drift blocked
  │    -> no self-heal: alert, write stale=True, fail closed for entry/probe
  └─ scoring-chain freeze detector trips
       -> no self-heal: alert, emit scoring_chain_frozen, fail closed for entry/probe
```

### Layer 1: factor lookup self-healing

Scheduler must check factor lookup age before producing each handoff.

Required behavior:

- Compute `factor_lookup_age_hours` from `factor_lookup_generated_at` using UTC.
- Treat missing, unparsable, far-future, empty, or over-threshold lookup artifacts as unsafe for live entry/probe.
- Use a live threshold that is explicit in config; the incident recommendation is `3h` for runtime decision freshness, even if governance keeps a looser historical default.
- If lookup age exceeds the threshold because the builder process did not run or was not scheduled, scheduler may self-heal by triggering one rebuild.
- If the root cause is unknown, the upstream data source is stale, the source database is missing, the latest scan output is unqualified/empty, research cannot produce qualified candidates, or wf stays blocked by `wf_dispersion_high` / `wf_return_drift_high`, scheduler must not blindly rebuild and continue.
- Any recovery attempt must be single-cycle bounded, idempotent, and followed by a re-read of the artifact.
- After recovery, scheduler must verify that the lookup is non-empty, fresh, governance-evaluable, and generated from fresh source inputs before allowing live entry/probe.
- If rebuild fails or still produces an empty/stale lookup, scheduler must fail closed for `entry` and `small_probe`, write `factor_lookup_stale=True`, write `factor_lookup_age_hours`, and include reason codes such as:
  - `factor_lookup_stale`
  - `factor_lookup_empty`
  - `factor_lookup_rebuild_failed`
  - `factor_lookup_rebuild_still_stale`
- If recovery is skipped because the correct action is not known, scheduler must write:
  - `factor_lookup_recovery_attempted=false`
  - `factor_lookup_recovery_skipped_reason`
  - `requires_human_intervention=true`
- Empty and stale must be additive states. A lookup can be both `factor_lookup_empty` and `factor_lookup_stale`.

Implementation note:

- The governance evaluator should not use `if empty ... elif stale ...` for top-level reason codes. It should append both conditions when both are true.
- Handoff serialization should not rely only on governance reason codes. It should recompute age from `factor_lookup_generated_at` and expose the computed value.
- Current implementation:
  - `quant_system_rebuild/src/policy/factor_governance.py` emits `factor_lookup_empty` and `factor_lookup_stale` additively.
  - `quant_system_rebuild/src/interfaces/runner.py` recomputes `factor_lookup_age_seconds` during handoff serialization and treats missing/unparsable/future/over-threshold timestamps as stale.
  - `quant_system_rebuild/scripts/quant_runtime_scheduler.py run-cycle` checks `factor_lookup_summary.json` before producing handoff; if it is missing, empty, stale, or time-invalid, it triggers one bounded `build_factor_lookup()` rebuild and re-reads the artifact.
  - If rebuild still produces empty/stale/invalid lookup, the scheduler records `factor_lookup_recovery.status=unhealthy_after_rebuild`; it does not mark bad data as usable.
  - Runtime freshness threshold defaults to `3h` and can be overridden with `FACTOR_LOOKUP_MAX_AGE_SEC` or `QUANT_FACTOR_LOOKUP_MAX_AGE_SEC`.
  - Bot consumers fail closed with the same `3h` default; `BOT_FACTOR_LOOKUP_MAX_AGE_SEC` / `DASHBOARD_FACTOR_LOOKUP_MAX_AGE_SEC` can override consumer-specific thresholds.

### Layer 2: thesis/confidence freeze detection

Decision engine needs a scoring-chain freeze detector as a backstop.

Required behavior:

- Track a score signature across recent cycles, not just a single value.
- Suggested signature fields:
  - `thesis_score`
  - `confidence`
  - `factor_lookup_version`
  - `factor_lookup_generated_at`
  - `research_score`
  - `research_health_status`
  - key source-data timestamps, where available
- If the signature is exactly unchanged for `N` consecutive live cycles while new handoffs are being produced, emit `scoring_chain_frozen`.
- A frozen scoring chain should not be rendered as normal `wait` / `not_entry_action`.
- Dashboard/status should show it as a red operational fault, not as a strategy decision.

This is a fallback for cases where factor lookup freshness checks fail again. Exact float equality across many live cycles is suspicious, especially when trigger-ready windows exist but the scoring values do not move.

Current implementation:

- `quant_system_rebuild/scripts/quant_runtime_scheduler.py run-cycle` accepts `--scoring-freeze-window` with default `6`.
- The scheduler compares the current handoff with recent handoffs using:
  - `thesis_score`
  - `confidence`
  - `factor_lookup_version`
  - `factor_lookup_generated_at`
  - `research_gate_status`
  - `research_gate_reasons`
- When the signature is unchanged across the configured live window, the handoff writes `scoring_chain_frozen=true` and appends `scoring_chain_frozen` to `transition_reason_codes` and `execution_warnings`.

Post-review correction:

- A DS review found a P0 bug in the first implementation: scheduler-level freeze detection was applied after `build_execution_handoff_payload()` had already computed `execution_allowed` and `execution_block_reason`.
- That meant a handoff could theoretically contain `scoring_chain_frozen=true` while still keeping an earlier `execution_allowed=true`.
- The fix is now explicit:
  - when scheduler freeze detection fires, quant sets `execution_allowed=false`;
  - quant sets `execution_block_reason=scoring_chain_frozen`;
  - quant still appends `scoring_chain_frozen` to `transition_reason_codes` and `execution_warnings`;
  - bot `ExecutionRiskGate` also blocks any entry/probe handoff with `scoring_chain_frozen=true`.
- Verification:
  - `quant_system_rebuild`: `tests/test_execution_handoff_block_reason.py tests/test_policy_engine.py tests/test_scripts_quant_runtime_scheduler.py` -> `153 passed`.
  - `eth_trading_bot`: `tests/test_execution_risk_gate.py tests/test_network_guard.py tests/test_automation_gate.py` -> `43 passed`.

### Layer 3: independent freshness monitor

Add a monitor that does not depend on scheduler or decision-engine logic.

Required behavior:

- Run every 15 minutes.
- Read the latest handoff directly from disk.
- Parse `factor_lookup_generated_at` and compute age.
- Alert if age exceeds the configured threshold.
- Also alert on:
  - latest handoff itself being stale or missing;
  - `research_health.valid_for_live_decision=false`;
  - repeated `trigger_ready=true` with zero `entry/small_probe`;
  - `factor_lookup_stale=False` while computed age is over threshold.

This monitor is the final line of defense: even if scheduler and decision engine both misclassify freshness, a pure file-based check should still alarm.

Current implementation:

- Read-only monitor script: `eth_trading_bot/scripts/diagnostics/monitor_handoff_freshness.py`.
- It reads the latest handoff directly from `quant_system_rebuild/runtime/cycles`, recomputes `factor_lookup_age_seconds`, and returns JSON.
- It exits `0` for `ok` and `2` for `alert`, so Windows Task Scheduler / cron can alert on non-zero status.
- It alerts on missing/unreadable handoff, missing/unparsable/future/over-threshold `factor_lookup_generated_at`, stale flag conflicts, producer stale flag, and `scoring_chain_frozen=true`.
- Threshold defaults to `3h` and can be overridden with `HANDOFF_MONITOR_FACTOR_LOOKUP_MAX_AGE_SEC` or `FACTOR_LOOKUP_MAX_AGE_SEC`.
- Task Scheduler / cron registration is intentionally not installed by this change; it must be deployed explicitly in ops.

### Observability requirement

Dashboard/status must surface the diagnostic fields directly:

- `factor_lookup_generated_at`
- `factor_lookup_age_hours`
- `factor_lookup_stale`
- `factor_lookup_empty`
- `factor_lookup_rebuild_status`
- `transition_reason_codes`
- `scoring_chain_frozen`

The UI must not collapse these into only `not_entry_action`.

## What This Is Not

- Not primarily a real worker failure: worker had no candidate package.
- Not primarily a candidate package write failure: scheduler did not receive an executable `entry/small_probe` intent.
- Not proof that the bot should have automatically entered a full position.
- Not permission to weaken research gates or bypass manual confirmation.

## Strategy Gate Finding: Trigger Seen, Probe Not Emitted

This section records the corrected strategy-level finding from the 2026-05-17 to 2026-05-18 missed short opportunity review.

The problem was not only stale/frozen data. The system also has a strategy-gate design issue: `small_probe` is not an independent low-risk response to a live trigger. In the current decision path it is mostly a narrow downgrade from a blocked `entry`, and it reuses entry-level research constraints.

### Full-runtime count

Read-only scan over `quant_system_rebuild/runtime/cycles/*/handoff.json` found:

- total handoffs scanned: `2524`
- `small_probe`: `26`
- `entry`: `0`
- `wait`: `1724`
- `observe_only`: `774`
- `trigger_ready=true` + directional `long/short` + final `action=wait`: `38`
- among those 38:
  - confidence `>= 0.53`: `38`
  - confidence `>= 0.55`: `31`
  - thesis `>= 0.53`: `31`
  - confidence `>= 0.53` and thesis `>= 0.53`: `31`
  - short direction: `27`
  - long direction: `11`

All 38 had the same transition reason pattern:

- `entry_thesis_below_floor`
- `entry_research_score_below_floor`
- `setup_ready_waiting_trigger`

This is not an isolated missed event. The runtime has repeatedly produced "trigger seen, direction known, but still wait" decisions.

### Representative missed short cycle

Cycle:

- `D:\开发\quant_system_rebuild\runtime\cycles\eth-15m-20260517T230855Z-f9e4f416`
- `generated_at = 2026-05-17T23:08:55Z`
- BJT: `2026-05-18 07:08:55`

Observed fields:

- `action = wait`
- `direction = short`
- `trigger_ready = true`
- `entry_timing_score = 0.9001`
- `confidence = 0.5924861611009046`
- `thesis_score = 0.5483766712290262`
- `research_score = 0.0` in `decision.json` metadata
- `research_candidate_count = 22`
- `research_gate_status = open`
- `risk_filter_status = degraded`
- `execution_block_reason = not_entry_action`
- `transition_reason_codes = entry_thesis_below_floor, entry_research_score_below_floor, setup_ready_waiting_trigger`

The handoff-level `research_score` field was empty/null in some views, but the source `decision.json` metadata showed `research_score=0.0` and `research_candidate_count=22`. Therefore the low-research chain is real; the handoff display was less complete than the decision artifact.

### Corrected blocking chain

```text
trigger_ready=true
direction=short
confidence=0.593
thesis=0.548
research_score=0.0
research_candidate_count=22
risk_filter_status=degraded

  -> entry gate:
       thesis(0.548) < entry floor(0.55)
         -> entry_thesis_below_floor
       research(0.0) < MIN_ENTRY_RESEARCH_SCORE(0.6)
         -> entry_research_score_below_floor
       raw entry blocked

  -> small_probe downgrade:
       confidence(0.593) >= probe floor(0.53)
       thesis(0.548) >= probe floor(0.53)
       low_research=True because 0.0 < 0.6
         -> _eligible_for_small_probe returns False
       downgrade blocked

  -> independent probe channels:
       trigger_ready=true causes several probe resolvers to return WAIT early
       no alternate "trigger-ready small probe" path catches the setup

  -> execution layer:
       degraded risk/research/factor states would still be checked before execution
```

### Code locations

Key policy locations in `quant_system_rebuild`:

- `src/policy/decision_engine.py`
  - `MIN_ENTRY_THESIS_SCORE = 0.55`
  - `MIN_ENTRY_RESEARCH_SCORE = 0.6`
  - `_apply_entry_score_gate()` adds `entry_thesis_below_floor` and `entry_research_score_below_floor`.
  - `_eligible_for_small_probe()` checks probe confidence/thesis but also blocks on `low_research`.
- `src/policy/probe_resolver.py`
  - `resolve_trigger_edge_probe_action()` returns `WAIT` when `trigger_ready=true`.
  - `resolve_trend_continuation_probe_action()` returns `WAIT` when `trigger_ready=true`.
  - `resolve_strong_consensus_probe_action()` returns `WAIT` when `trigger_ready=true`.
  - `resolve_strong_momentum_probe_action()` returns `WAIT` when `trigger_ready=true`.
  - `resolve_restricted_binance_two_source_probe_action()` returns `WAIT` when `trigger_ready=true`.
- `src/interfaces/runner.py`
  - execution-handoff permission checks apply a second layer of risk/research validation if a probe is ever produced.

### Correct interpretation

The issue is not that confidence/thesis were too weak for a probe.

For the representative cycle:

- probe confidence floor was satisfied;
- probe thesis floor was satisfied;
- full-entry thesis floor was missed by about `0.0016`;
- research score was `0.0`, with candidates present but unqualified;
- the small-probe downgrade reused entry-level research logic and was blocked.

The most precise conclusion is:

> The critical strategy break is the shared research gate. `small_probe` does not have its own explicitly capped, lower-risk research standard. A research state that should block full entry also blocks the small-probe downgrade, even when trigger, direction, confidence, and probe-level thesis are present.

### Why this matters

This behavior means the system is not only filtering noise. It can also classify real but imperfect early opportunities as noise.

A full entry should remain blocked when research is weak. That part is reasonable. The questionable part is treating weak research as an unconditional block on a capped probe instead of using it to reduce size, shorten expiry, require a hard stop, or demand faster follow-through.

### Not yet authorized

This finding does not authorize an automatic live-probe change by itself.

Any future code change must define a separate probe contract first, including:

- fresh market data required;
- no factor/staleness/scoring-freeze fault;
- no hard exchange, kill-switch, or account-risk veto;
- trigger-ready directional agreement required;
- executable stop required;
- strict max probe size;
- user target for "small position" is `10%`, so the current `2%` implementation must not be treated as the final intended size;
- short probe expiry / no-follow-through exit;
- explicit treatment of research states:
  - weak research blocks full entry;
  - weak but non-operational research may cap or label a probe;
  - hard data-quality faults still block.

### Implementation note

`quant_system_rebuild` now implements this as a separate `trigger_ready_small_probe` path:

- policy layer opens it only when trigger/direction/regime/setup agree, confidence and thesis meet probe floors, and entry timing is strong;
- weak or unstable research can cap/label the probe instead of blocking it outright;
- operational data faults still block it, including stale/empty factor lookup, factor governance unavailable, research unavailable/stale, and `scoring_chain_frozen`;
- sizing caps this probe at `0.02`;
- probe context carries a 3 x 15m expiry and no-follow-through invalidation;
- execution handoff rechecks stop distance, max size, factor freshness, scoring freeze, runtime vetoes, and account/exchange vetoes before allowing execution.

Post-review sizing correction:

- The current implementation caps `trigger_ready_small_probe` at `0.02`.
- The user's intended "small position" target is `10%`, not `1%-2%`.
- Therefore the current `2%` cap is conservative and not final.
- This should be fixed as a separate sizing-contract change, not mixed with the P0 frozen-chain safety fix.
- The change must keep quant and bot aligned:
  - quant trigger-ready probe cap;
  - handoff strict-cap validation;
  - bot `max_probe_size_pct`;
  - demo small-account fixed-margin behavior.
- The bot currently has `demo_small_account_mode=true` with a fixed `10U` margin budget. On small equity, that can override the handoff's requested percentage and produce a larger executable percentage than the quant probe cap. This is a P1 sizing semantics issue before degraded probes are ever allowed to become real auto-submit candidates.

Post-review strategy-shape note:

- `trigger_ready_small_probe` still requires full direction agreement across regime, confirmation, setup, and trigger, plus `regime_alignment >= 1.0`.
- This would have covered the representative 2026-05-17 trigger-ready aligned cycles.
- It will still miss earlier fast-move situations where 15m has triggered but 1h or 4h has not fully aligned.
- That is a strategy design choice, not a syntax bug, but it should be reviewed separately from the safety fixes.

## Bot Real-Order Gate Alignment Review

This section records the 2026-05-18 code review of the DS finding: quant now distinguishes weak research from operational faults for `trigger_ready_small_probe`, but bot real-order gating still treats `degraded` as a hard real-entry block.

### Review verdict

DS is correct.

The current end-to-end chain is:

```text
quant:
  action=small_probe
  probe_source=trigger_ready_small_probe
  execution_allowed=true
  risk_filter_status=degraded

bot:
  payload.degraded=true
    -> cycle_blocked_or_degraded
  risk_filter_allows_real_entry("degraded") == false
    -> risk_filter_not_pass

result:
  real_order_gate.allowed=false
  candidate_execution_package.status=skipped
  real worker has no package to submit
```

So this is an actual execution-chain block, not only a dashboard wording issue.

### Code evidence

Bot cycle-level gate:

- File: `eth_trading_bot/src/bot/automation_gate.py`
- Current behavior:

```python
if bool(payload.get("blocked", False)) or bool(payload.get("degraded", False)):
    reason_codes.append("cycle_blocked_or_degraded")
```

Bot handoff-level gate:

- File: `eth_trading_bot/src/bot/automation_gate.py`
- Current behavior:

```python
if not risk_filter_allows_real_entry(handoff.get("risk_filter_status")):
    reason_codes.append("risk_filter_not_pass")
```

Risk-filter contract:

- File: `eth_trading_bot/src/bot/risk_filter_contract.py`
- Current behavior:

```python
def risk_filter_allows_real_entry(value):
    return classify_risk_filter_status(value) == "pass"
```

Scheduler candidate package writer:

- File: `eth_trading_bot/scripts/ops/bot_runtime_scheduler.py`
- Current behavior:

```python
if real_order_gate.get("allowed") is not True:
    return False
```

Therefore any `trigger_ready_small_probe` that arrives as `risk_filter_status=degraded` will not produce `latest_candidate_execution_package.json`.

### Minimal reproduction

The following payload shape was tested against `evaluate_real_order_gate()`:

```text
runtime_mode=real
engine_mode=strict-live
effective_action=small_probe
blocked=false
degraded=true
handoff.execution_allowed=true
handoff.risk_filter_status=degraded
handoff.probe_source=trigger_ready_small_probe
handoff.position_size_pct=0.10
runtime_snapshot.snapshot_valid=true
position_state=FLAT
entry_order preflight=ready
maintain_protective_stop preflight=ready
```

Observed result:

```text
allowed=false
automation_boundary=real_order_submission_blocked
reason_codes=[
  cycle_blocked_or_degraded,
  risk_filter_not_pass
]
```

### Why this matters

Quant and bot currently disagree on the meaning of `degraded`.

Quant-side intended meaning for this specific path:

- operational data faults still block;
- weak or unstable research may block full entry;
- weak or unstable research may still allow a capped `trigger_ready_small_probe`;
- `execution_allowed=true` means the quant contract is intentionally open for that probe.

Bot-side current meaning:

- any cycle-level `degraded=true` blocks real-order submission;
- any handoff-level `risk_filter_status=degraded` blocks real entry;
- bot does not yet recognize the special `trigger_ready_small_probe` contract.

Plain-language conclusion:

> Quant says "10% trigger-ready probe is allowed under weak research." Bot says "degraded means no real order." Bot wins, so no order package is written.

### Required fix shape

Do not globally treat `degraded` as real-entry allowed.

Only this exact contract should get the exception:

```text
effective_action=small_probe
handoff.probe_source=trigger_ready_small_probe
handoff.execution_allowed=true
handoff.position_size_pct <= 0.10
risk_filter_status=degraded
payload.degraded=true
payload.blocked=false
```

For that exact case:

- do not add `cycle_blocked_or_degraded` solely because `payload.degraded=true`;
- do not add `risk_filter_not_pass` solely because `risk_filter_status=degraded`;
- continue writing all diagnostic reason codes for observability.

Everything below must remain hard-blocking:

- kill switch enabled;
- `runtime_mode != real`;
- `engine_mode != strict-live`;
- `payload.blocked=true`;
- `handoff.execution_allowed is not true`;
- `scoring_chain_frozen=true`;
- factor lookup faults:
  - `factor_lookup_stale`
  - `factor_lookup_empty`
  - `factor_lookup_missing`
  - `factor_lookup_rebuild_failed`
  - `factor_lookup_rebuild_still_stale`
- research/data availability faults:
  - `research_stale`
  - `research_unavailable`
  - `research_missing`
  - `research_not_ready`
  - `bundle_missing`
  - `data_health_veto`
  - `factor_governance_unavailable`
- runtime vetoes, staleness veto, conflict veto;
- invalid runtime snapshot;
- live position not flat;
- missing entry order plan;
- missing protective stop plan;
- missing initial stop;
- entry or stop preflight not ready;
- strategy TP ladder present but no TP order planned;
- probe size over `10%`.

### Test cases required before restart

Add or update bot tests:

1. `automation_gate` allows real-order gate for:
   - `small_probe`;
   - `probe_source=trigger_ready_small_probe`;
   - `risk_filter_status=degraded`;
   - `payload.degraded=true`;
   - `execution_allowed=true`;
   - valid snapshot, flat position, ready entry and stop preflight.

2. `automation_gate` still blocks the same probe when:
   - `scoring_chain_frozen=true`;
   - `factor_lookup_stale=true`;
   - `factor_lookup_empty=true`;
   - `research_stale` / `research_unavailable` / `research_missing` / `research_not_ready`;
   - `payload.blocked=true`;
   - size is above `0.10`.

3. Normal `entry_long` / `entry_short` remains blocked when:
   - `risk_filter_status=degraded`.

4. `bot_runtime_scheduler` writes `latest_candidate_execution_package.json` only when the fixed real-order gate allows the exact trigger-ready probe and all preflight checks are ready.

5. `bot_runtime_scheduler` skips candidate package for the same probe if any hard operational fault is present.

### Runtime status during review

Runtime stack was checked after real-order mode had been enabled:

```text
real_worker: running, mode=submit_enabled
kill_switch: off
candidate_package: missing
```

No order was submitted because the real worker had no candidate package to consume.

### Status

This document records a confirmed bot-side alignment bug and the implemented bot-side fix.

The fix is not "let degraded trade." The fix is:

> For `trigger_ready_small_probe` only, bot must mirror quant's three-level contract: operational fault blocks, full-entry research weakness blocks full entry, but research weakness alone may allow the explicitly capped 10% probe.

Current implementation:

- `eth_trading_bot/src/bot/automation_gate.py` now recognizes the narrow contract:
  - `effective_action=small_probe`;
  - `handoff.probe_source=trigger_ready_small_probe`;
  - `handoff.execution_allowed=true`;
  - `risk_filter_status=degraded`;
  - `position_size_pct/executable_size_pct/command position_size_pct <= 0.10`;
  - `payload.blocked=false`.
- For that exact contract, bot no longer adds:
  - `cycle_blocked_or_degraded` solely from `payload.degraded=true`;
  - `risk_filter_not_pass` solely from `risk_filter_status=degraded`.
- The same gate still blocks operational hard faults:
  - `scoring_chain_frozen`;
  - stale/missing/empty/rebuild-failed factor lookup;
  - research stale/unavailable/missing/not-ready;
  - `bundle_missing`;
  - `data_health_veto`;
  - `factor_governance_unavailable`;
  - runtime/staleness/conflict vetoes;
  - invalid snapshot, non-flat position, missing entry/stop plan, missing stop, missing preflight, TP ladder without TP order, or size over `10%`.
- `eth_trading_bot/scripts/ops/shadow_preflight_diagnostics.py` now preserves factor lookup freshness and scoring-freeze fields in the summarized handoff so scheduler/gate consumers do not lose hard-fault context.

Verification:

- `tests/test_automation_gate.py tests/test_bot_runtime_scheduler_script.py tests/test_run_shadow_preflight_cycle_script.py` -> `51 passed`.
- Broader bot gate/worker set:
  - `tests/test_automation_gate.py`
  - `tests/test_bot_runtime_scheduler_script.py`
  - `tests/test_execution_risk_gate.py`
  - `tests/test_network_guard.py`
  - `tests/test_position_manager.py`
  - `tests/test_shadow_orchestrator.py`
  - `tests/test_real_order_worker_script.py`
  - Result: `180 passed`.
- Minimal reproduction now returns:
  - `allowed=true`;
  - `automation_boundary=real_order_submission_allowed`;
  - `reason_codes=[]`.

## Concrete Checks To Run Next

Prioritize tracing one trigger-ready cycle end to end:

1. Pick one trigger-ready handoff, for example a cycle with:
   - `trigger_ready=true`
   - `action=wait`
   - `transition_reason_codes` containing `entry_thesis_below_floor`

2. Trace score sources:
   - `policy_input`
   - `factor_policy_signal`
   - `research_score`
   - `DecisionEngine._resolve_confidence`
   - thesis score construction
   - final handoff serialization

3. Determine why `confidence` and `thesis_score` are identical across trigger-ready cycles:
   - stale source data
   - cached policy input
   - fixed fallback values
   - factor multiplier clamp
   - degraded research template

4. Determine why `research_health.research_score=0.0` does not appear as a numeric handoff field.

5. Audit probe paths under hard research blocks:
   - confirm current behavior is intentional
   - decide whether a separate explicitly capped "diagnostic probe" policy should exist
   - keep default as no auto-probe until the risk contract is explicit

6. Improve observability:
   - surface `transition_reason_codes` in dashboard/status for `not_entry_action`
   - include `confidence`, `thesis_score`, `research_score`, and probe block reasons in the decision summary

## Immediate Priority

Do not start by changing the real-order worker. The immediate root-cause work is in:

- quant policy scoring freshness
- research score handoff
- probe resolver hard-block behavior
- dashboard/status reason mapping

Only after the upstream decision path can produce a justified executable intent should the real-order candidate/manual-confirmation work matter for live submission.
