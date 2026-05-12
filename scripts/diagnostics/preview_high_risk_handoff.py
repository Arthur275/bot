from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from scripts.path_utils import repo_root_from_script

try:
    from scripts.adhoc.adopt_protective_stop import ACTIVE_ALGO_STATUSES, PROTECTIVE_ORDER_TYPES
except ImportError:
    from scripts.adhoc.adopt_protective_stop import ACTIVE_ALGO_STATUSES, PROTECTIVE_ORDER_TYPES


BOT_ROOT = repo_root_from_script(__file__)
DEFAULT_STATE_PATH = str(BOT_ROOT / "runtime" / "shared_state" / "bot_state.json")
DEFAULT_REPORT_ROOT = str(BOT_ROOT / "runtime" / "reports" / "high_risk_handoff_preview")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Preview high-risk trailing/reduce/exit handoff without executing orders.")
    parser.add_argument("--handoff-file", required=True)
    parser.add_argument("--state-path", default=DEFAULT_STATE_PATH)
    parser.add_argument("--report-root", default=DEFAULT_REPORT_ROOT)
    parser.add_argument("--proxy-url", default="http://127.0.0.1:7897")
    parser.add_argument("--api-key-env", default="OKX_TRADE_API_KEY")
    parser.add_argument("--api-secret-env", default="OKX_TRADE_API_SECRET")
    parser.add_argument("--api-passphrase-env", default="OKX_TRADE_PASSPHRASE")
    parser.add_argument("--kill-switch-path", default=str(BOT_ROOT / "runtime" / "controls" / "disable_real_execution.flag"))
    parser.add_argument("--lock-path", default=str(BOT_ROOT / "runtime" / "reports" / "protective_stop_replace_watch" / "auto_replace.lock"))
    parser.add_argument("--json", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    payload = run(args=args)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(render_panel(payload))
    return 0


def run(*, args: argparse.Namespace, adapter: Any | None = None, state_store: Any | None = None, network_decision: Any | None = None) -> dict[str, Any]:
    from bot.config import BotConfig
    from bot import exchange_adapter
    from bot.exchange_adapter import AdapterCredentials
    from bot.high_risk_gate import HighRiskGate
    from bot.network_guard import GuardDecision
    from bot.state_store import StateStore

    now = datetime.now().replace(microsecond=0)
    report_root = Path(args.report_root)
    report_root.mkdir(parents=True, exist_ok=True)
    state_path = Path(args.state_path)
    handoff = json.loads(Path(args.handoff_file).read_text(encoding="utf-8"))
    config = BotConfig(
        state_store_path=state_path,
        audit_log_path=report_root / "high_risk_preview_audit.jsonl",
        artifacts_root=report_root / "artifacts",
        proxy_url=args.proxy_url or None,
        exchange_venue="binance_usdt_perp" if str(args.api_key_env).startswith("BINANCE_") else "okx_usdt_swap",
        exchange_symbol="ETHUSDT" if str(args.api_key_env).startswith("BINANCE_") else "ETH-USDT-SWAP",
        exchange_api_base_url="https://fapi.binance.com" if str(args.api_key_env).startswith("BINANCE_") else "https://www.okx.com",
    )
    if adapter is None:
        credentials = AdapterCredentials(
            venue=config.exchange_venue,
            api_key_env=args.api_key_env,
            api_secret_env=args.api_secret_env,
            api_passphrase_env=getattr(args, "api_passphrase_env", None) or getattr(config, "exchange_api_passphrase_env", ""),
            recv_window_ms=config.recv_window_ms,
            timeout_sec=config.timeout_sec,
            proxy_url=config.proxy_url,
            api_base_url=config.exchange_api_base_url,
        )
        adapter = (
            exchange_adapter.OkxUsdtSwapAdapter(credentials)
            if config.exchange_venue == "okx_usdt_swap"
            else exchange_adapter.BinancePerpAdapter(credentials)
        )
    store = state_store or StateStore(state_path)
    state = store.load()
    snapshot = adapter.fetch_runtime_snapshot()
    raw_orders = _fetch_open_algo_orders(adapter)
    protective_stop_payload = _extract_unique_exchange_protective_stop(raw_orders=raw_orders, handoff=handoff)
    protective_stop = protective_stop_payload.get("exchange_protective_stop")
    blocked_reasons = list(protective_stop_payload.get("blocked_reasons") or [])
    network = network_decision or GuardDecision(judgement_status="ok", ready=True)
    gate = HighRiskGate(
        kill_switch_path=args.kill_switch_path,
        lock_path=args.lock_path,
        exchange_min_order_qty=config.exchange_min_order_qty,
        now_fn=lambda: now,
    )
    decision = gate.evaluate(
        raw_handoff=handoff,
        network_decision=network,
        runtime_snapshot=snapshot,
        state_metadata=state.metadata,
        exchange_protective_stop=protective_stop,
    )
    blocked_reasons.extend(decision.blocked_reasons)
    blocked_reasons = list(dict.fromkeys(blocked_reasons))
    expected_state = _build_expected_state(
        handoff=handoff,
        snapshot=snapshot,
        state_metadata=state.metadata,
        exchange_protective_stop=protective_stop,
        blocked=bool(blocked_reasons),
    )
    payload = {
        "mode": "preview",
        "created_at": now.isoformat(),
        "handoff_file": str(args.handoff_file),
        "state_path": str(state_path),
        "report_root": str(report_root),
        "data_times": {
            "handoff_generated_at": handoff.get("generated_at"),
            "handoff_expires_at": handoff.get("expires_at"),
            "exchange_snapshot_fetched_at": snapshot.fetched_at.isoformat() if snapshot.fetched_at else "",
            "open_algo_orders_fetched_at": now.isoformat(),
            "gate_evaluated_at": now.isoformat(),
        },
        "handoff": handoff,
        "network_decision": network.model_dump(mode="json") if hasattr(network, "model_dump") else dict(network),
        "snapshot": snapshot.model_dump(mode="json"),
        "raw_open_algo_orders": raw_orders,
        "exchange_protective_stop": protective_stop,
        "gate": decision.model_dump(mode="json"),
        "blocked_reasons": blocked_reasons,
        "warnings": decision.warnings,
        "expected_after_execution": expected_state,
        "execution_enabled": False,
        "execution_note": "preview_only_no_real_trailing_reduce_exit_execution",
    }
    _write_report(payload=payload, report_root=report_root)
    return payload


def _fetch_open_algo_orders(adapter: Any) -> list[dict[str, Any]]:
    try:
        return adapter.fetch_open_algo_orders_raw()
    except Exception as exc:
        return [{"_fetch_error": {"type": exc.__class__.__name__, "message": str(exc)}}]


def _extract_unique_exchange_protective_stop(*, raw_orders: list[dict[str, Any]], handoff: dict[str, Any]) -> dict[str, Any]:
    if raw_orders and isinstance(raw_orders[0], dict) and raw_orders[0].get("_fetch_error"):
        return {"exchange_protective_stop": None, "blocked_reasons": ["open_algo_orders_unavailable"]}
    candidates = []
    for item in raw_orders:
        status = str(item.get("algoStatus") or item.get("state") or item.get("status") or "").upper()
        order_type = str(item.get("ordType") or item.get("orderType") or item.get("type") or "").upper()
        if status not in ACTIVE_ALGO_STATUSES:
            continue
        if order_type not in PROTECTIVE_ORDER_TYPES:
            continue
        candidates.append(item)
    if len(candidates) == 0:
        return {"exchange_protective_stop": None, "blocked_reasons": ["protective_stop_missing_for_high_risk_preview"]}
    if len(candidates) > 1:
        return {
            "exchange_protective_stop": None,
            "blocked_reasons": ["multiple_protective_stops_manual_required"],
            "candidate_count": len(candidates),
        }
    candidate = candidates[0]
    trigger_price = _to_float(candidate.get("triggerPx") or candidate.get("triggerPrice") or candidate.get("stopPrice"))
    if trigger_price is None:
        return {"exchange_protective_stop": None, "blocked_reasons": ["exchange_protective_stop_trigger_missing"]}
    side = str(candidate.get("side") or "").upper()
    expected_side = "SELL" if str(handoff.get("direction") or "") == "long" else "BUY"
    if side and side != expected_side:
        return {"exchange_protective_stop": None, "blocked_reasons": ["exchange_protective_stop_side_mismatch"]}
    return {
        "exchange_protective_stop": {
            "trigger_price": trigger_price,
            "side": side or expected_side,
            "order_type": str(candidate.get("ordType") or candidate.get("orderType") or candidate.get("type") or ""),
            "algo_id": str(candidate.get("algoId") or candidate.get("ordId") or ""),
            "client_algo_id": str(candidate.get("algoClOrdId") or candidate.get("clientAlgoId") or candidate.get("clOrdId") or ""),
        },
        "blocked_reasons": [],
        "candidate_count": 1,
    }


def _build_expected_state(
    *,
    handoff: dict[str, Any],
    snapshot: Any,
    state_metadata: dict[str, Any],
    exchange_protective_stop: dict[str, Any] | None,
    blocked: bool,
) -> dict[str, Any]:
    position = snapshot.position
    amount = abs(float(position.position_amt or 0.0))
    direction = str(position.direction or "")
    action = str(handoff.get("action") or "")
    if blocked:
        return {"status": "not_computed_when_blocked"}
    if action == "reduce":
        reduce_qty = _resolve_reduce_qty(handoff=handoff, position_amount=amount)
        remaining = max(0.0, amount - reduce_qty)
        stop_price = (state_metadata.get("protective_stop") or {}).get("trigger_price")
        return {
            "position_after": {"state": "ENTERED", "direction": direction, "position_amt": remaining},
            "protective_stop_after": {
                "action": "rebuild_required",
                "trigger_price": stop_price,
                "quantity": remaining,
                "source": "current_protective_stop_price_new_position_qty",
            },
            "algo_orders_after": "1 active STOP_MARKET protective order expected after rebuild",
            "state_after": {
                "protective_stop_required": False,
                "reconciliation_required": False,
                "recovery_required": False,
            },
            "required_followup_actions": [
                "reduce verify",
                "cancel old protective stop",
                "verify removed",
                "place protective stop with remaining qty",
                "verify active",
                "state write",
            ],
        }
    if action == "exit":
        return {
            "position_after": {"state": "FLAT", "direction": "neutral", "position_amt": 0.0},
            "protective_stop_after": {"action": "cancel_residual_protective_orders", "orders_to_cancel": [exchange_protective_stop] if exchange_protective_stop else []},
            "algo_orders_after": "0 protective algo orders expected",
            "state_after": {
                "protective_stop_required": False,
                "reconciliation_required": False,
                "recovery_required": False,
            },
            "required_followup_actions": ["exit verify", "cancel residual protective stops", "verify no protective algo orders", "state write"],
        }
    if action == "trailing":
        trailing = handoff.get("trailing_rule") or {}
        return {
            "position_after": {"state": "ENTERED", "direction": direction, "position_amt": amount},
            "protective_stop_after": {
                "action": "preview_only_trailing_takeover_blocked_for_real_execution",
                "activation_price": trailing.get("activation_price"),
                "callback_rate": trailing.get("callback_rate"),
                "fixed_stop_cancel_allowed": False,
                "reason": "trailing_activation_not_yet_verified",
            },
            "transition_coverage": {
                "coverage_plan_status": "preview_only",
                "fixed_stop_cancel_allowed": False,
                "activation_distance_check": "see gate blocked_reasons",
            },
            "required_followup_actions": ["manual exchange validation before real trailing takeover"],
        }
    return {"status": "unknown_action"}


def _resolve_reduce_qty(*, handoff: dict[str, Any], position_amount: float) -> float:
    if handoff.get("reduce_qty") is not None:
        return float(handoff["reduce_qty"])
    return position_amount * float(handoff.get("reduce_fraction") or 0.0)


def _to_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def render_panel(payload: dict[str, Any]) -> str:
    status = "[READY: PREVIEW ONLY]" if not payload.get("blocked_reasons") else "[BLOCKED]"
    lines = [
        status,
        f"Action: {(payload.get('handoff') or {}).get('action', '')}",
        f"Handoff: {(payload.get('handoff') or {}).get('handoff_id', '')}",
        f"Created: {payload.get('created_at', '')}",
        "",
        "Blocked reasons:",
        *(f"- {item}" for item in (payload.get("blocked_reasons") or ["none"])),
        "",
        "Warnings:",
        *(f"- {item}" for item in (payload.get("warnings") or ["none"])),
        "",
        "Expected After Execution:",
        json.dumps(payload.get("expected_after_execution") or {}, ensure_ascii=False, indent=2),
        "",
        "Execution: disabled (preview only)",
    ]
    return "\n".join(lines)


def _write_report(*, payload: dict[str, Any], report_root: Path) -> None:
    report_root.mkdir(parents=True, exist_ok=True)
    (report_root / "latest_preview.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    (report_root / f"high_risk_preview_{timestamp}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
