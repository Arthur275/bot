# Post-Entry Dynamic Exit Governance

## Purpose

Post-Entry Dynamic Exit Governance is the secondary decision engine after a position has been opened. It is not a raw price trigger and it must not become a second strategy brain inside the bot.

The design goal is:

- quant evaluates post-entry exit intent
- bot writes exchange/runtime facts
- bot enforces execution safety
- protective stop is never absent during an active position
- all dynamic exits only reduce risk

This document is the implementation design for the first version.

## Non-Goals

The first version does not:

- let the bot decide strategy intent
- let quant call exchange APIs directly
- introduce a new scheduler or cron
- replace the existing `ExecutionHandoff` contract for entry/reduce/exit
- automatically convert `trail_profit` into a Binance trailing stop
- cancel an existing protective stop before a new protective stop is confirmed active
- use DuckDB as live cooldown state

## Core Principles

### Protective Stop Is the Hard Floor

After entry, an active position must have a protective stop.

If the protective stop is missing, stale, directionally wrong, or quantity-mismatched, the system prioritizes protective stop repair and reconciliation above any profit-taking logic.

Protective stops may only tighten:

- long: new stop must be greater than or equal to current stop, and below mark price
- short: new stop must be less than or equal to current stop, and above mark price

No automated path may loosen the stop.

### Exit Is Not a Single-Price Trigger

Exit or reduction should not be triggered by one 15m bar, one order book shock, or one funding-rate tick.

Actions should be based on a combination of:

- price behavior
- factor state changes
- runtime risk state
- protective stop state
- cooldown state

Strong hard vetoes may bypass multi-bar confirmation, but they still cannot bypass execution safety.

### Review Intent Is Separate From Execution

The review layer can produce rich post-entry intent:

- `hold`
- `tighten_stop`
- `move_to_breakeven`
- `trail_profit`
- `partial_reduce`
- `full_exit`

The execution layer maps approved intent to existing safe bot commands. The bot must not infer strategy meaning from raw market data.

## Architecture

```text
scheduler cycle
  -> quant strict-live judgement
  -> decision / handoff artifacts
  -> bot writes bot_position_snapshot.json
  -> quant builds post_entry_exit_review.json when position is active
  -> bot consumes review intent
  -> bot applies gates and execution safety checks
  -> bot executes only risk-reducing actions
  -> bot writes cooldown/state/audit facts
```

The design keeps the existing quant/bot boundary:

- quant owns strategy and factor governance
- bot owns exchange facts, runtime state, order safety, and reconciliation

## Data Flow

### Bot Position Snapshot

The bot writes a lightweight runtime snapshot every cycle when exchange state can be fetched.

Recommended path:

```text
runtime/bot_position_snapshot.json
```

Minimum schema:

```json
{
  "generated_at": "2026-05-06T14:30:00Z",
  "position_active": true,
  "symbol": "ETHUSDT",
  "direction": "long",
  "entry_price": 3120.5,
  "mark_price": 3150.2,
  "quantity": 0.03,
  "unrealized_pnl_pct": 0.0095,
  "protective_stop_present": true,
  "protective_stop_trigger": 3090.0,
  "open_algo_order_count": 1,
  "entry_bar_utc": "2026-05-06T12:00:00Z"
}
```

The bot may include additional diagnostic fields, but quant should only depend on documented fields.

### Post-Entry Exit Review

Quant reads:

- latest live bundle
- latest decision/handoff artifacts
- `runtime/bot_position_snapshot.json`
- `runtime/exit_cooldown_state.json`

Recommended output path:

```text
runtime/post_entry_exit_review.json
```

Minimum schema:

```json
{
  "generated_at": "2026-05-06T14:30:05Z",
  "position_state": "entered",
  "symbol": "ETHUSDT",
  "direction": "long",
  "entry_age_bars": 12,
  "unrealized_pnl_pct": 0.018,
  "max_favorable_excursion_pct": 0.026,
  "max_adverse_excursion_pct": -0.006,
  "factor_state": {
    "support_score": 0.72,
    "opposition_score": 0.28,
    "veto_count": 0,
    "regime_alignment": 0.81
  },
  "exit_intent": "trail_profit",
  "confidence": 0.74,
  "proposed_stop_trigger": 3138.0,
  "reduce_fraction": null,
  "reason_codes": [
    "profit_buffer_confirmed",
    "trend_support_intact",
    "trail_activation_reached"
  ]
}
```

This review does not place orders. It is a governed strategy conclusion.

### Cooldown State

The bot writes cooldown state after confirmed execution. Quant reads it before proposing repeated actions.

Recommended path:

```text
runtime/exit_cooldown_state.json
```

Minimum schema:

```json
{
  "updated_at": "2026-05-06T14:30:10Z",
  "last_confirmed_action": "move_to_breakeven",
  "last_confirmed_action_at": "2026-05-06T14:30:10Z",
  "actions": {
    "move_to_breakeven": {
      "last_confirmed_at": "2026-05-06T14:30:10Z"
    },
    "trail_profit": {
      "last_confirmed_at": null
    },
    "partial_reduce": {
      "last_confirmed_at": null
    },
    "full_exit": {
      "last_confirmed_at": null
    }
  }
}
```

Cooldown is a live execution-state concern, not a research dataset. DuckDB can ingest it later for analysis, but it should not be the live coordination mechanism.

## Intent Mapping

The first version keeps review intent separate from `ExecutionHandoff`.

| Review intent | Bot execution mapping | Notes |
| --- | --- | --- |
| `hold` | no command | No order action. Continue monitoring. |
| `tighten_stop` | `maintain_protective_stop` with tighter fixed stop | Must pass monotonic stop check. |
| `move_to_breakeven` | `maintain_protective_stop` near entry plus fee/slippage buffer | Must pass monotonic stop check and distance-to-mark check. |
| `trail_profit` | fixed protective stop tightening | First version does not automatically use exchange trailing stop. |
| `partial_reduce` | existing reduce path, then rebuild protective stop for remaining quantity | Must confirm reduced position and protective stop quantity. |
| `full_exit` | existing exit path, then clear residual protective stop only after flat confirmation | Must not cancel protective stop while position remains open. |

Do not encode stop updates as `action=hold` inside a normal `ExecutionHandoff`. A normal hold means no strategy action. Stop updates should be represented by post-entry review intent and bot-side protective stop commands.

## Gates

### Hard Protection Gate

Runs before any post-entry action.

Block or repair when:

- position is active and protective stop is missing
- protective stop direction does not match position
- protective stop quantity does not match protected position quantity
- protective stop is too far from current risk budget
- exchange open order state is uncertain
- state is `RECONCILING`, `DEGRADED`, or `BLOCKED`

When this gate fails, allowed actions are limited to:

- protective stop repair
- reduce
- full exit
- reconciliation

No new entry or add-to-position action is allowed.

### Stop Monotonicity Gate

For long positions:

```text
new_stop >= current_stop
new_stop < mark_price
```

For short positions:

```text
new_stop <= current_stop
new_stop > mark_price
```

If there is no confirmed current protective stop, the system must treat this as protective stop repair, not as normal tightening.

### Cooldown Gate

Each dynamic action class has a cooldown.

The first version should include cooldowns for:

- `move_to_breakeven`
- `trail_profit`
- `partial_reduce`
- `full_exit`

Cooldown starts only after bot-confirmed execution. A quant review that was blocked or failed must not update cooldown as if it executed.

### Confirmation Gate

An action needs at least one of:

- multi-bar confirmation
- strong hard veto
- sufficient profit buffer
- structural drawdown threshold breach
- protective stop failure or uncertainty

The exact thresholds belong in quant policy, but the bot still enforces runtime safety.

## P0: Protective Stop Replacement Safety

Binance algo order replacement must not create a protection gap.

The safe sequence is:

```text
1. PLACE new protective stop
2. CONFIRM new protective stop is active on exchange
3. CANCEL old protective stop
```

Step 2 is mandatory. If confirmation fails:

- keep the old protective stop
- mark state as `RECONCILING`
- set recovery/protective-stop-required flags
- emit audit reason code

If the exchange does not allow two same-side protective algo stops to coexist, automated replacement is blocked unless a verified atomic replace API is available.

In that case, the system must:

- not cancel the old stop
- not perform trailing takeover
- enter reconciliation or manual-review path

## Trailing Takeover Policy

First version behavior:

```text
trail_profit = tighten fixed protective stop
```

Do not automatically convert `trail_profit` to a Binance `TRAILING_STOP_MARKET`.

Trailing takeover may be enabled only after all of the following are implemented and tested:

- existing fixed protective stop is active
- activation distance is at least 0.5% from mark
- high-risk lock stage is sufficient
- new trailing order parameters are explicit and preflighted
- new protection is confirmed active before old protection is cancelled
- failure keeps old stop active
- replay and worker tests prove no protection gap

Until then, `trail_profit` is a governance intent for fixed stop tightening.

## First Version Implementation Order

1. Add bot writer for `runtime/bot_position_snapshot.json`.
2. Add quant builder for `runtime/post_entry_exit_review.json`.
3. Add bot writer/reader contract for `runtime/exit_cooldown_state.json`.
4. Add bot hard gate for protective stop monotonic tightening.
5. Add protective stop replacement safety checks with place-confirm-cancel semantics or explicit block.
6. Map post-entry intents to existing bot commands.
7. Add audit payloads for review, gate decision, execution result, cooldown update.
8. Add tests for all P0 protection-gap cases.

## Test Checklist

- active position without protective stop triggers repair before profit logic
- long stop cannot be lowered
- short stop cannot be raised
- stop cannot be tightened through mark price
- `move_to_breakeven` respects cooldown
- `trail_profit` maps to fixed stop tightening in first version
- `trail_profit` does not cancel fixed stop before new protection is confirmed
- failed new stop confirmation leaves old stop active
- reduce rebuilds protective stop for remaining quantity
- full exit does not cancel protective stop until flat position is confirmed
- uncertain exchange open order state blocks dynamic stop actions
- quant review cannot update cooldown unless bot confirms execution

## Red Lines

The implementation is not live-safe if any of the following is true:

- protective stop can be absent while a position remains open
- stop can be automatically loosened
- bot makes independent strategy decisions from raw factors or price
- quant directly calls exchange APIs
- `trail_profit` silently becomes exchange trailing stop before takeover safety is proven
- cooldown is updated from an unexecuted review
- old protective stop is cancelled before new protection is confirmed active
