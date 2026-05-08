# OKX Migration Logic Fix Plan

## Current Findings

The execution default has moved to OKX, but the live quant and runtime snapshot paths still have Binance-era hard dependencies.

Observed issues:

- Quant strict-live can still block before policy on Binance HTTP 451.
- OKX runtime snapshot is currently invalid in `latest_cycle.json`.
- Dashboard can consume OKX PnL, but only after bot scheduler writes a valid OKX runtime snapshot.
- Quant heartbeat and latest cycle status are not fully aligned.
- Stack status reports `command_mismatch` for wrapper-managed processes.

## Priority Order

### P0: Remove Binance Hard Dependencies From Quant Live Path

Goal: strict-live should continue when Binance is restricted, as long as OKX or consensus data is available.

Scope:

- `quant_system_rebuild/src/interfaces/live_snapshots.py`
- `quant_system_rebuild/src/interfaces/scheduler.py`
- `quant_system_rebuild/src/interfaces/runner.py`
- `quant_system_rebuild/src/ingest/market_data_consensus.py`
- `quant_system_rebuild/src/policy/execution_cost.py`
- Related tests under `quant_system_rebuild/tests/`

Required changes:

1. Treat Binance HTTP 451 as `restricted_location`, not a hard live-chain failure.
2. Use OKX and consensus sources for live price features before Binance fallback.
   - In `live_snapshots.py`, do not leave the current Binance-only URL builders as the only required path.
   - Add explicit OKX request builders for ETH swap ticker and klines, for example `_okx_futures_ticker_url()` and `_okx_futures_klines_url()`, or convert the current request builders to accept an exchange/source parameter.
   - The first implementation should prefer explicit OKX functions over a broad generic abstraction unless the existing local code already has a clear generic request pattern.
   - When building live `FeatureRow.values`, populate `_price_source`, `_feature_source`, `_source_asof`, and `_source_quality`.
3. Block only when all primary replacement ticker sources fail:
   - OKX ticker fails
   - Bitget ticker fails
   - Gate ticker fails
4. Keep partial consensus degraded but usable:
   - OKX only is currently `ConsensusQuality.UNRELIABLE` in `score_consensus_quality()` and will be hard-vetoed through `data_health_score=0.30`.
   - P0 must change `score_consensus_quality()` so OKX-only can become `ConsensusQuality.DEGRADED` only when Binance is unavailable because `binance_source_failure_reason=restricted_location`.
   - Do not make all single-source cases degraded; transient Binance timeout/server errors should remain `UNRELIABLE`.
   - OKX + Bitget: `consensus_quality=restricted_two_source` or `degraded`
5. Ensure two-source degraded consensus is not later rejected by strong three-source consensus checks.
6. Keep Binance as optional diagnostic/fallback only, not as a required strict-live input.

### P0 Edge Chain: Move Edge Estimation Off Stale Binance Features

Goal: strict-live must not produce a decision that is immediately neutralized by stale Binance-derived edge features.

Current risk:

- `src/policy/execution_cost.py::_estimate_gross_edge_pct()` reads ATR/range from `FeatureMatrix.rows[-1].values`.
- If that row still comes from stale Binance inputs, `net_edge_pct` can become missing or below cost.
- This can trigger `net_edge_below_cost` and collapse `position_cap` to zero even after strict-live no longer blocks.

Required changes:

1. Ensure feature matrix rows used by live decisions are built from OKX or consensus price data.
2. Add source metadata to live feature rows where possible:
   - Put these keys inside `FeatureRow.values`, not on the `FeatureRow` model itself.
   - `FeatureRow` uses `extra="forbid"`, so adding model-level fields will fail validation.
   - Use underscored keys in `values` to avoid colliding with normal factor names:
     - `_price_source`
     - `_feature_source`
     - `_source_asof`
     - `_source_quality`
3. Make `_estimate_gross_edge_pct()` refuse stale or restricted Binance-only ATR/range inputs without changing its call signature.
   - Keep `_estimate_gross_edge_pct(values: Mapping[str, Any])`.
   - Read source metadata from `values`.
   - Reuse existing `DataStatus` vocabulary for `_source_quality`: `available`, `stale`, `unavailable`.
   - Treat `_source_quality in {"stale", "unavailable"}` as unusable for ATR/range edge.
   - Treat `_feature_source` or `_price_source` values that identify restricted Binance-only data as unusable.
4. Missing edge should remain `edge_estimate_missing`, not silently become zero edge.
5. Define `edge_source` as the output-level source from `build_execution_cost_risk_input()`.
   - This is separate from feature-row `_feature_source`.
   - Suggested values:
     - `atr_15m_okx`
     - `range_15m_okx`
     - `atr_15m_consensus`
     - `range_15m_consensus`
     - `missing`
     - `stale_binance_atr`
     - `stale_binance_range`
6. Add regression tests proving Binance 451 + OKX/consensus features can produce a non-blocked decision without forced `position_cap=0`.

P0 acceptance criteria:

- Binance 451 does not block strict-live if OKX ticker is available.
- `score_consensus_quality()` returns `DEGRADED` for OKX-only only when Binance failure reason is `restricted_location`.
- `score_consensus_quality()` still returns `UNRELIABLE` for single-source fallback when Binance failures are transient timeout/server errors.
- Two-source consensus can produce a degraded but usable decision.
- `net_edge_below_cost` is not emitted solely because stale Binance ATR/range was used.
- `edge_source` clearly identifies OKX/consensus or `missing`.
- Tests cover Binance 451 with OKX success.

## P1: Diagnose And Fix OKX Runtime Snapshot Invalid

Goal: bot runtime should fetch valid OKX account equity and unrealized PnL.

Current observation:

- `latest_cycle.json` has:
  - `exchange_venue=okx_usdt_swap`
  - `runtime_snapshot.snapshot_valid=false`
  - `runtime_account_equity=null`
  - `runtime_unrealized_pnl_usd=null`

Likely root cause:

- `manage_runtime_stack.ps1` starts bot scheduler without OKX API env arguments, so `run_shadow_preflight_cycle.py` does not construct a real OKX adapter.

Required changes:

1. Pass OKX env names into bot scheduler startup:
   - `--api-key-env OKX_TRADE_API_KEY`
   - `--api-secret-env OKX_TRADE_API_SECRET`
   - `--api-passphrase-env OKX_TRADE_PASSPHRASE`
2. Apply the same check to `launch_runtime_stack.ps1`.
3. Verify quant and bot processes do not accidentally use different exchange env families.
   - Check bot stack scripts:
     - From the bot repo root: `rg -n "OKX_TRADE_API|BINANCE_API|BINANCE_TRADE|api-key-env|api-secret-env|api-passphrase-env" scripts/manage_runtime_stack.ps1 scripts/launch_runtime_stack.ps1`
   - Check quant stack/scripts:
     - From the quant repo root: `rg -n "BINANCE_|OKX_|exchange.*binance|exchange.*okx" scripts src/interfaces -g "*.py" -g "*.ps1"`
   - Confirm quant live judgement does not depend on Binance env variables after P0.
   - Confirm bot scheduler and real worker use the same OKX env family.
4. Add startup/preflight diagnostics for missing OKX passphrase.
5. Run an isolated OKX snapshot probe using:
   - `bot.exchange_adapter.AdapterCredentials`
   - `bot.exchange_adapter.OkxUsdtSwapAdapter`
6. If the isolated probe succeeds but scheduler snapshot stays invalid, fix env propagation.
7. If the isolated probe fails, fix OKX adapter parsing/request logic.

P1 acceptance criteria:

- `latest_cycle.runtime_snapshot.snapshot_valid=true`
- `latest_cycle.runtime_account_equity` is populated.
- If a position exists, `runtime_unrealized_pnl_usd` is populated from OKX `upl`.
- Dashboard total profit reads OKX runtime data.

## P2: Fix Scheduler And Dashboard State Consistency

Goal: status surfaces should reflect the actual latest quant cycle.

Observed issue:

- `runtime/cycles` is still updating.
- `runtime/scheduler/heartbeat.json` can be stale.
- Some latest cycle directories may contain only `snapshot_registry.json`.

Required changes:

1. `quant_runtime_scheduler.py` should write `runtime/scheduler/heartbeat.json` on every loop.
2. Blocked cycles should still write `scheduler_status.json`.
3. Dashboard/status should distinguish:
   - latest complete decision cycle
   - latest blocked cycle
   - latest incomplete snapshot-only cycle
4. Avoid silently showing old decisions as current when the newest cycle is blocked.

P2 acceptance criteria:

- `manage_runtime_stack.cmd status` reports fresh quant age from the latest scheduler status.
- Dashboard can show blocked/incomplete quant cycles explicitly.
- Old decisions are not mistaken for fresh active decisions.

## P3: Fix Stack Process Detection And Resource Cleanup

Goal: reduce false alarms and long-running runtime noise.

Required changes:

1. Fix wrapper-aware process matching in `manage_runtime_stack.ps1`.
2. `bot_scheduler` should match `bot_scheduler_loop.ps1`, not only `bot_runtime_scheduler.py`.
3. `real_worker` should match the wrapper command and child worker command.
4. Close async ccxt exchange instances explicitly.

P3 acceptance criteria:

- `bot_scheduler` and `real_worker` no longer show `command_mismatch` when healthy.
- stderr no longer emits `requires to release all resources with an explicit call to .close()`.

## Test Plan

P0 tests:

- Binance 451 + OKX ticker success -> strict-live not blocked.
- Binance `restricted_location` + OKX-only source -> degraded decision, not blocked.
- Binance timeout/server error + OKX-only source -> unreliable, still blocked or vetoed.
- OKX + Bitget -> restricted two-source or degraded decision, not blocked.
- All OKX/Bitget/Gate ticker sources fail -> blocked.
- Edge estimation uses OKX/consensus feature row, not stale Binance row.
- Stale Binance-only ATR/range -> `edge_estimate_missing`.

P1 tests:

- Stack args include OKX API key, secret, and passphrase env names.
- Missing passphrase produces explicit diagnostic.
- OKX adapter maps:
  - `totalEq` -> `account_equity`
  - `upl` -> `unrealized_pnl_usd`
  - `uplRatio` -> `unrealized_pnl_pct_on_margin`
- Dashboard displays OKX total profit and ignores Binance runtime/preview data.

P2 tests:

- Successful cycle writes heartbeat.
- Blocked cycle writes heartbeat and `scheduler_status.json`.
- Dashboard prefers latest blocked status over stale old decision.

P3 tests:

- Stack status recognizes wrapper-managed bot scheduler.
- Stack status recognizes wrapper-managed real worker.
- CCXT collectors close exchange instances on success and failure.

## Execution Sequence

1. Implement P1 env propagation first because it is small and unlocks OKX snapshot verification.
2. Implement P0 strict-live fallback and P0 edge-source fixes in the same PR/change set.
   - Do not merge a state where Binance 451 no longer blocks but stale/missing edge immediately collapses `position_cap` to zero.
   - The P0 acceptance test must prove both: decision is not blocked and edge handling does not force a zero-sized outcome solely due to stale Binance data.
3. Add P0 tests before or with the implementation.
4. Run quant scheduler/interface tests and bot dashboard/scheduler tests.
5. Fix P2 heartbeat/status consistency.
6. Fix P3 process detection and ccxt cleanup.

## Non-Goals

- Do not remove Binance adapters entirely; keep them as explicit legacy fallback.
- Do not allow real order submission without existing gates, kill switch checks, and confirmation flow.
- Do not treat degraded two-source consensus as full-quality data.
- Do not hide missing edge estimates by coercing them to zero.
