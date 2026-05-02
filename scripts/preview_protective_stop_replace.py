from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from hashlib import sha256
from pathlib import Path
from typing import Any

try:
    from .adopt_protective_stop import (
        ACTIVE_ALGO_STATUSES,
        PROTECTIVE_ORDER_TYPES,
        _decimal_equal,
        _fmt,
        _money,
        _normalize_optional,
        _to_bool,
        _to_decimal,
    )
except ImportError:
    from adopt_protective_stop import (
        ACTIVE_ALGO_STATUSES,
        PROTECTIVE_ORDER_TYPES,
        _decimal_equal,
        _fmt,
        _money,
        _normalize_optional,
        _to_bool,
        _to_decimal,
    )


BOT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STATE_PATH = str(BOT_ROOT / "runtime" / "shared_state" / "bot_state.json")
DEFAULT_REPORT_ROOT = str(BOT_ROOT / "runtime" / "reports" / "protective_stop_replace")


RATCHET_LOCK_STAGES: tuple[tuple[int, Decimal, Decimal], ...] = (
    (1, Decimal("0.005"), Decimal("0.003")),
    (2, Decimal("0.009"), Decimal("0.006")),
    (3, Decimal("0.013"), Decimal("0.009")),
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Preview a safe Binance protective algo stop cancel/place replacement.")
    parser.add_argument("--state-path", default=DEFAULT_STATE_PATH)
    parser.add_argument("--report-root", default=DEFAULT_REPORT_ROOT)
    parser.add_argument("--proxy-url", default="http://127.0.0.1:7897")
    parser.add_argument("--api-key-env", default="BINANCE_TRADE_API_KEY")
    parser.add_argument("--api-secret-env", default="BINANCE_TRADE_API_SECRET")
    parser.add_argument("--target-mode", choices=["breakeven", "ratchet"], default="ratchet")
    parser.add_argument("--min-profit-lock-pct", type=float, default=0.003)
    parser.add_argument("--min-mark-buffer-pct", type=float, default=0.005)
    parser.add_argument("--snapshot-max-age-sec", type=int, default=30)
    parser.add_argument("--preview-file", default="")
    parser.add_argument("--confirm-token", default="")
    parser.add_argument("--max-preview-age-sec", type=int, default=180)
    parser.add_argument("--accept-gap-risk", action="store_true")
    parser.add_argument("--allow-missing-repair", action="store_true")
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


def run(*, args: argparse.Namespace) -> dict[str, Any]:
    from bot.config import BotConfig
    from bot.exchange_adapter import AdapterCredentials, BinancePerpAdapter
    from bot.state_store import StateStore

    now = datetime.now().replace(microsecond=0)
    report_root = Path(args.report_root)
    report_root.mkdir(parents=True, exist_ok=True)
    state_path = Path(args.state_path)
    config = BotConfig(
        state_store_path=state_path,
        audit_log_path=report_root / "replace_preview_audit.jsonl",
        artifacts_root=report_root / "artifacts",
        proxy_url=args.proxy_url or None,
    )
    adapter = BinancePerpAdapter(
        AdapterCredentials(
            venue=config.exchange_venue,
            api_key_env=args.api_key_env,
            api_secret_env=args.api_secret_env,
            recv_window_ms=config.recv_window_ms,
            timeout_sec=config.timeout_sec,
            proxy_url=config.proxy_url,
            api_base_url=config.exchange_api_base_url,
        )
    )
    state = StateStore(state_path).load()
    mode = "confirm" if args.confirm_token else "preview"
    previous_preview = _load_preview_file(Path(args.preview_file)) if args.preview_file else None
    payload = _build_replace_preview(
        adapter=adapter,
        state=state,
        mode=mode,
        now=now,
        state_path=state_path,
        report_root=report_root,
        target_mode=str(args.target_mode),
        min_profit_lock_pct=float(args.min_profit_lock_pct),
        min_mark_buffer_pct=float(args.min_mark_buffer_pct),
        snapshot_max_age_sec=int(args.snapshot_max_age_sec),
        previous_preview=previous_preview,
        confirm_token=str(args.confirm_token or ""),
        max_preview_age_sec=int(args.max_preview_age_sec),
        accept_gap_risk=bool(args.accept_gap_risk),
        allow_missing_repair=bool(args.allow_missing_repair),
    )
    if mode == "confirm" and payload["replace_ready"]:
        _execute_replace(adapter=adapter, payload=payload)
        state = StateStore(state_path).load()
        if payload.get("new_protective_stop_record") and not payload.get("blocked_reasons"):
            metadata = dict(state.metadata or {})
            metadata["protective_stop"] = payload["new_protective_stop_record"]
            state.metadata = metadata
            state.protective_stop_required = False
            state.recovery_required = False
            state.reconciliation_required = False
            StateStore(state_path).save(state)
            payload["state_written"] = True
        elif payload.get("requires_recovery_state"):
            state.recovery_required = True
            state.reconciliation_required = True
            state.protective_stop_required = bool(payload.get("requires_protective_stop_recovery"))
            StateStore(state_path).save(state)
    _write_report(payload=payload, report_root=report_root)
    return payload


def _build_replace_preview(
    *,
    adapter: Any,
    state: Any,
    mode: str,
    now: datetime,
    state_path: Path,
    report_root: Path,
    target_mode: str,
    min_profit_lock_pct: float,
    min_mark_buffer_pct: float,
    snapshot_max_age_sec: int,
    previous_preview: dict[str, Any] | None,
    confirm_token: str,
    max_preview_age_sec: int,
    accept_gap_risk: bool,
    allow_missing_repair: bool,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "mode": mode,
        "created_at": now.isoformat(),
        "state_path": str(state_path),
        "report_root": str(report_root),
        "target_mode": target_mode,
        "blocked_reasons": [],
        "warnings": [],
        "replace_ready": False,
        "recorded_protective_stop": (state.metadata or {}).get("protective_stop"),
        "snapshot": {},
        "raw_open_algo_orders": [],
        "candidate_order": None,
        "checks": {},
        "pnl_state": {},
        "risk_change": {},
        "request_preview": {},
        "confirm_token": "",
        "confirm_command": "",
        "confirm_checks": {},
        "accept_gap_risk": accept_gap_risk,
        "state_written": False,
        "cancel_response": None,
        "cancel_verify": {},
        "place_response": None,
        "place_verify": {},
        "requires_recovery_state": False,
        "requires_protective_stop_recovery": False,
        "new_protective_stop_record": None,
        "repair_missing": False,
    }
    record = payload["recorded_protective_stop"]
    if not isinstance(record, dict):
        payload["blocked_reasons"].append("recorded_protective_stop_missing")
        return payload

    snapshot = adapter.fetch_runtime_snapshot()
    payload["snapshot"] = snapshot.model_dump(mode="json")
    try:
        raw_orders = adapter.fetch_open_algo_orders_raw()
    except Exception as exc:
        payload["blocked_reasons"].append("open_algo_orders_unavailable")
        payload["open_algo_orders_error"] = {
            "type": exc.__class__.__name__,
            "message": str(exc),
            "kind": getattr(exc, "kind", ""),
            "http_status": getattr(exc, "http_status", None),
            "payload": getattr(exc, "payload", None),
        }
        return payload
    payload["raw_open_algo_orders"] = raw_orders
    candidates = _filter_protective_algo_orders(raw_orders)
    checks = _validate_recorded_order(
        snapshot=snapshot,
        recorded=record,
        candidates=candidates,
        now=now,
        snapshot_max_age_sec=snapshot_max_age_sec,
    )
    payload["checks"] = checks
    payload["blocked_reasons"].extend(checks["blocked_reasons"])
    candidate = checks.get("candidate_order")
    payload["candidate_order"] = candidate
    if not candidate:
        if not allow_missing_repair or payload["blocked_reasons"] != ["no_active_protective_algo_order"]:
            return payload
        payload["repair_missing"] = True
        payload["blocked_reasons"] = []

    pnl_state = _build_pnl_state(snapshot=snapshot)
    payload["pnl_state"] = pnl_state
    risk_change = _build_risk_change(
        snapshot=snapshot,
        candidate=candidate,
        recorded=record,
        target_mode=target_mode,
        min_profit_lock_pct=min_profit_lock_pct,
        min_mark_buffer_pct=min_mark_buffer_pct,
    )
    payload["risk_change"] = risk_change
    payload["blocked_reasons"].extend(risk_change["blocked_reasons"])
    payload["warnings"].extend(risk_change["warnings"])
    payload["request_preview"] = _build_request_preview(
        recorded=record,
        candidate=candidate,
        snapshot=snapshot,
        new_stop_price=risk_change.get("target_stop_price"),
        repair_missing=bool(payload["repair_missing"]),
        previous_preview=previous_preview if mode == "confirm" else None,
    )
    if not payload["blocked_reasons"]:
        payload["confirm_token"] = _build_confirm_token(
            state_path=state_path,
            created_at=now.isoformat(),
            request_preview=payload["request_preview"],
            risk_change=risk_change,
        )
        payload["confirm_command"] = _build_confirm_command(
            state_path=state_path,
            report_root=report_root,
            preview_file=str(report_root / "latest_preview.json"),
            token=payload["confirm_token"],
            allow_missing_repair=bool(payload["repair_missing"]),
        )
    if mode == "confirm":
        confirm_checks = _validate_confirm(
            previous_preview=previous_preview,
            current_payload=payload,
            confirm_token=confirm_token,
            now=now,
            max_preview_age_sec=max_preview_age_sec,
            state_path=state_path,
            accept_gap_risk=accept_gap_risk,
        )
        payload["confirm_checks"] = confirm_checks
        payload["blocked_reasons"].extend(confirm_checks["blocked_reasons"])
    payload["replace_ready"] = not payload["blocked_reasons"]
    return payload


def _filter_protective_algo_orders(raw_orders: list[dict[str, Any]]) -> list[dict[str, Any]]:
    orders: list[dict[str, Any]] = []
    for item in raw_orders:
        status = str(item.get("algoStatus") or item.get("status") or "").upper()
        order_type = str(item.get("orderType") or item.get("type") or "").upper()
        if status not in ACTIVE_ALGO_STATUSES:
            continue
        if order_type not in PROTECTIVE_ORDER_TYPES:
            continue
        orders.append(item)
    return orders


def _validate_recorded_order(
    *,
    snapshot: Any,
    recorded: dict[str, Any],
    candidates: list[dict[str, Any]],
    now: datetime,
    snapshot_max_age_sec: int,
) -> dict[str, Any]:
    blocked: list[str] = []
    checks: dict[str, Any] = {"blocked_reasons": blocked}
    fetched_at = snapshot.fetched_at
    snapshot_age_sec = (now - fetched_at).total_seconds() if fetched_at else None
    checks["snapshot_age_sec"] = snapshot_age_sec
    checks["snapshot_fresh"] = snapshot_age_sec is not None and snapshot_age_sec <= snapshot_max_age_sec
    if not snapshot.snapshot_valid:
        blocked.append("snapshot_invalid")
    if not checks["snapshot_fresh"]:
        blocked.append("snapshot_stale")
    position = snapshot.position
    if position.position_state != "ENTERED":
        blocked.append("position_not_entered")
    if position.direction not in {"long", "short"}:
        blocked.append("position_direction_unknown")
    if not position.position_amt:
        blocked.append("position_amount_missing")
    if len(candidates) == 0:
        blocked.append("no_active_protective_algo_order")
        return checks
    if len(candidates) > 1:
        blocked.append("multiple_active_protective_algo_orders")
        checks["candidate_count"] = len(candidates)
        return checks
    candidate = candidates[0]
    checks["candidate_order"] = candidate
    checks["candidate_count"] = 1
    recorded_algo_id = str(recorded.get("algo_id") or "")
    exchange_algo_id = str(candidate.get("algoId") or "")
    checks["algo_id_matches_record"] = bool(recorded_algo_id) and recorded_algo_id == exchange_algo_id
    if not checks["algo_id_matches_record"]:
        blocked.append("algo_id_mismatch")
    expected_side = "SELL" if position.direction == "long" else "BUY"
    checks["side_matches_position"] = str(candidate.get("side") or "").upper() == expected_side
    if not checks["side_matches_position"]:
        blocked.append("side_mismatch")
    close_position = _to_bool(candidate.get("closePosition"))
    checks["close_position"] = close_position
    if close_position:
        checks["quantity_matches_position"] = True
    else:
        checks["quantity_matches_position"] = _decimal_equal(candidate.get("quantity"), abs(float(position.position_amt or 0.0)))
        if not checks["quantity_matches_position"]:
            blocked.append("quantity_mismatch")
    checks["trigger_matches_record"] = _decimal_equal(
        candidate.get("triggerPrice") or candidate.get("stopPrice"),
        recorded.get("trigger_price"),
    )
    if not checks["trigger_matches_record"]:
        blocked.append("trigger_price_mismatch")
    return checks


def _build_pnl_state(*, snapshot: Any) -> dict[str, Any]:
    position = snapshot.position
    entry = _to_decimal(position.entry_price)
    mark = _to_decimal(position.mark_price)
    amount = _to_decimal(position.position_amt)
    if entry is None or mark is None or amount is None:
        return {}
    pnl = (mark - entry) * amount
    if position.direction == "short":
        pnl = (entry - mark) * abs(amount)
    notional_margin = abs(amount) * entry / Decimal(str(position.leverage or 10))
    pnl_pct = (pnl / notional_margin) if notional_margin > 0 else Decimal("0")
    price_vs_entry_pct = ((mark - entry) / entry) if entry > 0 else Decimal("0")
    if position.direction == "short":
        price_vs_entry_pct = ((entry - mark) / entry) if entry > 0 else Decimal("0")
    return {
        "unrealized_pnl_usd": float(pnl),
        "unrealized_pnl_pct_on_margin": float(pnl_pct),
        "price_vs_entry_pct": float(price_vs_entry_pct),
    }


def _build_risk_change(
    *,
    snapshot: Any,
    candidate: dict[str, Any] | None,
    recorded: dict[str, Any],
    target_mode: str,
    min_profit_lock_pct: float,
    min_mark_buffer_pct: float,
) -> dict[str, Any]:
    blocked: list[str] = []
    warnings: list[str] = []
    position = snapshot.position
    entry = _require_decimal(position.entry_price, "entry_price_missing")
    mark = _require_decimal(position.mark_price, "mark_price_missing")
    amount = abs(_require_decimal(position.position_amt, "position_amount_missing"))
    old_stop_source = (candidate or {}).get("triggerPrice") or (candidate or {}).get("stopPrice") or recorded.get("trigger_price")
    old_stop = _require_decimal(old_stop_source, "old_stop_missing")
    if position.direction == "long":
        mark_buffer = ((mark - entry) / entry) if entry > 0 else Decimal("0")
    else:
        mark_buffer = ((entry - mark) / entry) if entry > 0 else Decimal("0")
    if target_mode == "ratchet":
        stage_state = _build_ratchet_stage_state(recorded=recorded, mark_buffer=mark_buffer)
        current_stage = int(stage_state["current_stage"])
        target_stage = int(stage_state["target_stage"])
        lock_pct = Decimal(str(stage_state["target_lock_pct"]))
        min_buffer = Decimal(str(stage_state["target_buffer_pct"]))
        if target_stage <= current_stage:
            blocked.append("ratchet_stage_not_advanced")
        if target_stage > current_stage + 1:
            blocked.append("ratchet_stage_jump_not_allowed")
    else:
        current_stage = int(recorded.get("lock_stage") or 0)
        target_stage = max(1, current_stage)
        lock_pct = Decimal(str(min_profit_lock_pct))
        min_buffer = Decimal(str(min_mark_buffer_pct))
        stage_state = {
            "current_stage": current_stage,
            "target_stage": target_stage,
            "target_buffer_pct": float(min_buffer),
            "target_lock_pct": float(lock_pct),
            "max_stage": max(stage for stage, _buffer, _lock in RATCHET_LOCK_STAGES),
        }
    if target_mode == "ratchet" and target_stage <= current_stage:
        target = _require_decimal(recorded.get("lock_target_price") or recorded.get("trigger_price"), "lock_target_price_missing")
    elif position.direction == "long":
        target = (entry * (Decimal("1") + lock_pct)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    else:
        target = (entry * (Decimal("1") - lock_pct)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    if position.direction == "long":
        safer = target > old_stop
        target_lock = ((target - entry) / entry) if entry > 0 else Decimal("0")
    else:
        safer = target < old_stop
        target_lock = ((entry - target) / entry) if entry > 0 else Decimal("0")
    old_loss = _loss_at_stop(direction=position.direction, entry=entry, stop=old_stop, amount=amount)
    new_loss = _loss_at_stop(direction=position.direction, entry=entry, stop=target, amount=amount)
    if not safer:
        blocked.append("target_stop_not_safer")
    if mark_buffer < min_buffer:
        blocked.append("min_mark_buffer_not_met")
    if target_lock < lock_pct:
        blocked.append("min_profit_lock_not_met")
    if target_lock < Decimal("0.005"):
        warnings.append("target_stop_tight_to_entry")
    return {
        "old_stop_price": float(old_stop),
        "target_stop_price": float(target),
        "stop_change_pct": float(((target - old_stop) / old_stop) if old_stop > 0 else Decimal("0")),
        "direction_label": "SAFER" if safer else "MORE_RISK",
        "old_loss_usd": float(old_loss),
        "new_loss_usd": float(new_loss),
        "risk_delta_usd": float(new_loss - old_loss),
        "mark_buffer_pct": float(mark_buffer),
        "target_lock_pct": float(target_lock),
        "min_profit_lock_pct": float(lock_pct),
        "min_mark_buffer_pct": float(min_buffer),
        "current_lock_stage": current_stage,
        "target_lock_stage": target_stage,
        "ratchet": stage_state,
        "blocked_reasons": blocked,
        "warnings": warnings,
    }


def _build_ratchet_stage_state(*, recorded: dict[str, Any], mark_buffer: Decimal) -> dict[str, Any]:
    current_stage = int(recorded.get("lock_stage") or 0)
    max_stage = max(stage for stage, _buffer, _lock in RATCHET_LOCK_STAGES)
    next_stage = min(current_stage + 1, max_stage)
    target_stage = current_stage
    target_buffer = Decimal("999")
    target_lock = Decimal(str(recorded.get("lock_target_pct") or "0"))
    for stage, buffer_pct, lock_pct in RATCHET_LOCK_STAGES:
        if stage == next_stage:
            target_buffer = buffer_pct
            target_lock = lock_pct
            if mark_buffer >= buffer_pct:
                target_stage = stage
            break
    if current_stage >= max_stage:
        target_buffer = RATCHET_LOCK_STAGES[-1][1]
        target_lock = RATCHET_LOCK_STAGES[-1][2]
        target_stage = current_stage
    return {
        "current_stage": current_stage,
        "target_stage": target_stage,
        "target_buffer_pct": float(target_buffer),
        "target_lock_pct": float(target_lock),
        "max_stage": max_stage,
        "ratchet_holds_on_pullback": True,
    }


def _loss_at_stop(*, direction: str, entry: Decimal, stop: Decimal, amount: Decimal) -> Decimal:
    if direction == "long":
        return (entry - stop) * amount
    return (stop - entry) * amount


def _build_request_preview(
    *,
    recorded: dict[str, Any],
    candidate: dict[str, Any] | None,
    snapshot: Any,
    new_stop_price: Any,
    repair_missing: bool = False,
    previous_preview: dict[str, Any] | None = None,
) -> dict[str, Any]:
    position = snapshot.position
    side = "SELL" if position.direction == "long" else "BUY"
    quantity = str((candidate or {}).get("quantity") or recorded.get("quantity") or abs(float(position.position_amt or 0.0)))
    previous_place = ((previous_preview or {}).get("request_preview") or {}).get("place") or {}
    previous_params = previous_place.get("params") or {}
    client_algo_id = str(previous_params.get("clientAlgoId") or f"ethbotpsreplace{datetime.now().strftime('%Y%m%d%H%M%S')}")
    payload: dict[str, Any] = {
        "place": {
            "method": "POST",
            "path": "/fapi/v1/algoOrder",
            "params": {
                "symbol": "ETHUSDT",
                "algoType": "CONDITIONAL",
                "side": side,
                "type": "STOP_MARKET",
                "quantity": quantity,
                "triggerPrice": _format_price(new_stop_price),
                "workingType": "MARK_PRICE",
                "reduceOnly": "true",
                "clientAlgoId": client_algo_id[:36],
            },
        },
    }
    if repair_missing:
        payload["gap_risk"] = {
            "estimated_latency": "place-only",
            "message": "No active protective stop exists. Repair places a new protective stop without cancel.",
            "execution_requires": ["manual confirmation token", "--accept-gap-risk"],
        }
        return payload
    payload["cancel"] = {
            "method": "DELETE",
            "path": "/fapi/v1/algoOrder",
            "params": {
                "algoId": str(recorded.get("algo_id") or (candidate or {}).get("algoId") or ""),
            },
    }
    payload["gap_risk"] = {
        "estimated_latency": "200-500ms",
        "message": "During cancel -> place, the position has no protective stop.",
        "execution_requires": ["manual confirmation token", "--accept-gap-risk"],
    }
    return payload


def _build_confirm_token(
    *,
    state_path: Path,
    created_at: str,
    request_preview: dict[str, Any],
    risk_change: dict[str, Any],
) -> str:
    cancel = request_preview.get("cancel") or {}
    place = request_preview.get("place") or {}
    basis = {
        "state_path": str(state_path),
        "created_at": created_at,
        "cancel": json.dumps(cancel.get("params") or {}, sort_keys=True),
        "place": json.dumps(place.get("params") or {}, sort_keys=True),
        "target_stop": str(risk_change.get("target_stop_price") or ""),
    }
    serialized = "|".join(f"{key}={basis[key]}" for key in sorted(basis))
    return "REPLACE-" + sha256(serialized.encode("utf-8")).hexdigest()[:12].upper()


def _build_confirm_command(*, state_path: Path, report_root: Path, preview_file: str, token: str, allow_missing_repair: bool = False) -> str:
    command = (
        "python scripts\\preview_protective_stop_replace.py `\n"
        f"  --state-path {_quote_arg(str(state_path))} `\n"
        f"  --report-root {_quote_arg(str(report_root))} `\n"
        f"  --preview-file {_quote_arg(preview_file)} `\n"
        f"  --confirm-token {token} `\n"
        "  --accept-gap-risk"
    )
    if allow_missing_repair:
        command += " `\n  --allow-missing-repair"
    return command


def _validate_confirm(
    *,
    previous_preview: dict[str, Any] | None,
    current_payload: dict[str, Any],
    confirm_token: str,
    now: datetime,
    max_preview_age_sec: int,
    state_path: Path,
    accept_gap_risk: bool,
) -> dict[str, Any]:
    blocked: list[str] = []
    checks: dict[str, Any] = {"blocked_reasons": blocked}
    if not previous_preview:
        blocked.append("preview_file_required")
        return checks
    preview_created_at = _parse_datetime(previous_preview.get("created_at"))
    elapsed = (now - preview_created_at).total_seconds() if preview_created_at else None
    checks["preview_age_sec"] = elapsed
    checks["preview_age_ok"] = elapsed is not None and elapsed <= max_preview_age_sec
    if not checks["preview_age_ok"]:
        blocked.append("preview_expired")
    expected_token = previous_preview.get("confirm_token") or ""
    checks["token_matches"] = bool(confirm_token) and confirm_token == expected_token
    if not checks["token_matches"]:
        blocked.append("confirm_token_mismatch")
    checks["state_path_matches"] = str(previous_preview.get("state_path") or "") == str(state_path)
    if not checks["state_path_matches"]:
        blocked.append("state_path_mismatch")
    checks["gap_risk_accepted"] = bool(accept_gap_risk)
    if not checks["gap_risk_accepted"]:
        blocked.append("gap_risk_not_accepted")
    for key in ("candidate_order", "request_preview", "risk_change"):
        if _stable_json(previous_preview.get(key)) != _stable_json(current_payload.get(key)):
            blocked.append(f"{key}_changed")
    return checks


def _execute_replace(
    *,
    adapter: Any,
    payload: dict[str, Any],
    sleep_fn: Any = time.sleep,
    verify_attempts: int = 2,
    verify_interval_sec: float = 0.2,
) -> None:
    request_preview = payload["request_preview"]
    place_params = request_preview["place"]["params"]
    if payload.get("repair_missing"):
        _execute_missing_repair(adapter=adapter, payload=payload, place_params=place_params)
        return
    cancel_params = request_preview["cancel"]["params"]
    old_algo_id = str(cancel_params.get("algoId") or "")
    try:
        cancel_response = adapter.cancel_algo_order_raw(algo_id=old_algo_id)
    except Exception as exc:
        payload["cancel_response"] = None
        _block_replace(
            payload=payload,
            reason="cancel_failed",
            detail={"type": exc.__class__.__name__, "message": str(exc)},
            protective_stop_uncertain=True,
        )
        return
    payload["cancel_response"] = cancel_response
    if _response_has_exchange_error(cancel_response):
        _block_replace(payload=payload, reason="cancel_failed", detail={"response": cancel_response})
        return

    cancel_verify = _verify_old_stop_removed(
        adapter=adapter,
        old_algo_id=old_algo_id,
        previous_snapshot=payload.get("snapshot") or {},
        sleep_fn=sleep_fn,
        attempts=verify_attempts,
        interval_sec=verify_interval_sec,
    )
    payload["cancel_verify"] = cancel_verify
    if not cancel_verify.get("verified"):
        reason = str(cancel_verify.get("reason") or "cancel_unverified")
        protective_stop_uncertain = reason != "old_stop_still_active"
        _block_replace(
            payload=payload,
            reason=reason,
            detail=cancel_verify,
            protective_stop_uncertain=protective_stop_uncertain,
        )
        return
    try:
        place_response = adapter.place_algo_order_raw(params=place_params)
    except Exception as exc:
        payload["place_response"] = None
        _block_replace(
            payload=payload,
            reason="place_failed",
            detail={"type": exc.__class__.__name__, "message": str(exc)},
            protective_stop_missing=True,
        )
        return
    payload["place_response"] = place_response
    if _response_has_exchange_error(place_response):
        _block_replace(payload=payload, reason="place_failed", detail={"response": place_response}, protective_stop_missing=True)
        return
    place_verify = _verify_new_stop_active(adapter=adapter, old_algo_id=old_algo_id, place_params=place_params)
    payload["place_verify"] = place_verify
    if not place_verify.get("verified"):
        _block_replace(payload=payload, reason=str(place_verify.get("reason") or "place_unverified"), detail=place_verify, protective_stop_missing=True)
        return
    payload["new_protective_stop_record"] = _build_new_protective_stop_record(
        previous_record=payload["recorded_protective_stop"],
        place_params=place_params,
        place_response=place_verify.get("candidate_order") or place_response,
        snapshot=payload["snapshot"],
        risk_change=payload.get("risk_change") or {},
        created_at=payload["created_at"],
        adopted_from="replace_cancel_place",
    )


def _execute_missing_repair(*, adapter: Any, payload: dict[str, Any], place_params: dict[str, Any]) -> None:
    try:
        place_response = adapter.place_algo_order_raw(params=place_params)
    except Exception as exc:
        payload["place_response"] = None
        _block_replace(
            payload=payload,
            reason="place_failed",
            detail={"type": exc.__class__.__name__, "message": str(exc)},
            protective_stop_missing=True,
        )
        return
    payload["place_response"] = place_response
    if _response_has_exchange_error(place_response):
        _block_replace(payload=payload, reason="place_failed", detail={"response": place_response}, protective_stop_missing=True)
        return
    place_verify = _verify_new_stop_active(adapter=adapter, old_algo_id="", place_params=place_params)
    payload["place_verify"] = place_verify
    if not place_verify.get("verified"):
        _block_replace(payload=payload, reason=str(place_verify.get("reason") or "place_unverified"), detail=place_verify, protective_stop_missing=True)
        return
    payload["new_protective_stop_record"] = _build_new_protective_stop_record(
        previous_record=payload["recorded_protective_stop"],
        place_params=place_params,
        place_response=place_verify.get("candidate_order") or place_response,
        snapshot=payload["snapshot"],
        risk_change=payload.get("risk_change") or {},
        created_at=payload["created_at"],
        adopted_from="missing_repair_place_only",
    )


def _verify_old_stop_removed(
    *,
    adapter: Any,
    old_algo_id: str,
    previous_snapshot: dict[str, Any],
    sleep_fn: Any,
    attempts: int,
    interval_sec: float,
) -> dict[str, Any]:
    for attempt in range(1, max(1, attempts) + 1):
        sleep_fn(interval_sec)
        try:
            raw_orders = adapter.fetch_open_algo_orders_raw()
        except Exception as exc:
            return {
                "verified": False,
                "reason": "cancel_unverified",
                "attempt": attempt,
                "error": {"type": exc.__class__.__name__, "message": str(exc)},
            }
        candidates = _filter_protective_algo_orders(raw_orders)
        if any(str(order.get("algoId") or "") == old_algo_id for order in candidates):
            if attempt >= max(1, attempts):
                return {
                    "verified": False,
                    "reason": "old_stop_still_active",
                    "attempt": attempt,
                    "active_protective_orders": candidates,
                }
            continue
        try:
            snapshot = adapter.fetch_runtime_snapshot()
        except Exception as exc:
            return {
                "verified": False,
                "reason": "cancel_position_unverified",
                "attempt": attempt,
                "active_protective_orders": candidates,
                "error": {"type": exc.__class__.__name__, "message": str(exc)},
            }
        position = snapshot.position
        if not snapshot.snapshot_valid:
            return {
                "verified": False,
                "reason": "cancel_position_unverified",
                "attempt": attempt,
                "active_protective_orders": candidates,
                "snapshot": snapshot.model_dump(mode="json"),
            }
        if position.position_state == "FLAT":
            return {
                "verified": False,
                "reason": "position_closed_after_cancel",
                "attempt": attempt,
                "active_protective_orders": candidates,
                "snapshot": snapshot.model_dump(mode="json"),
            }
        if position.position_state != "ENTERED":
            return {
                "verified": False,
                "reason": "cancel_position_unverified",
                "attempt": attempt,
                "active_protective_orders": candidates,
                "snapshot": snapshot.model_dump(mode="json"),
            }
        if not _position_still_matches(previous_snapshot=previous_snapshot, current_snapshot=snapshot.model_dump(mode="json")):
            return {
                "verified": False,
                "reason": "position_changed_after_cancel",
                "attempt": attempt,
                "active_protective_orders": candidates,
                "snapshot": snapshot.model_dump(mode="json"),
            }
        return {
            "verified": True,
            "attempt": attempt,
            "active_protective_orders": candidates,
            "snapshot": snapshot.model_dump(mode="json"),
        }
    return {"verified": False, "reason": "cancel_unverified"}


def _verify_new_stop_active(*, adapter: Any, old_algo_id: str, place_params: dict[str, Any]) -> dict[str, Any]:
    try:
        raw_orders = adapter.fetch_open_algo_orders_raw()
    except Exception as exc:
        return {
            "verified": False,
            "reason": "place_unverified",
            "error": {"type": exc.__class__.__name__, "message": str(exc)},
        }
    candidates = _filter_protective_algo_orders(raw_orders)
    if len(candidates) != 1:
        return {
            "verified": False,
            "reason": "place_unverified",
            "active_protective_orders": candidates,
        }
    candidate = candidates[0]
    if old_algo_id and str(candidate.get("algoId") or "") == old_algo_id:
        return {
            "verified": False,
            "reason": "place_unverified",
            "active_protective_orders": candidates,
        }
    mismatches: list[str] = []
    if str(candidate.get("side") or "").upper() != str(place_params.get("side") or "").upper():
        mismatches.append("side")
    if str(candidate.get("orderType") or candidate.get("type") or "").upper() != "STOP_MARKET":
        mismatches.append("order_type")
    if not _decimal_equal(candidate.get("quantity"), place_params.get("quantity")):
        mismatches.append("quantity")
    if not _decimal_equal(candidate.get("triggerPrice") or candidate.get("stopPrice"), place_params.get("triggerPrice")):
        mismatches.append("trigger_price")
    if mismatches:
        return {
            "verified": False,
            "reason": "place_unverified",
            "mismatches": mismatches,
            "active_protective_orders": candidates,
        }
    return {
        "verified": True,
        "candidate_order": candidate,
        "active_protective_orders": candidates,
    }


def _position_still_matches(*, previous_snapshot: dict[str, Any], current_snapshot: dict[str, Any]) -> bool:
    previous = previous_snapshot.get("position") or {}
    current = current_snapshot.get("position") or {}
    if str(previous.get("direction") or "") != str(current.get("direction") or ""):
        return False
    return _decimal_equal(previous.get("position_amt"), current.get("position_amt"))


def _response_has_exchange_error(response: Any) -> bool:
    if not isinstance(response, dict):
        return False
    code = response.get("code")
    if code not in (None, "", 0, "0"):
        return True
    status = str(response.get("status") or response.get("error") or "").lower()
    return status in {"error", "rejected", "failed"}


def _block_replace(
    *,
    payload: dict[str, Any],
    reason: str,
    detail: dict[str, Any],
    protective_stop_missing: bool = False,
    protective_stop_uncertain: bool = False,
) -> None:
    payload["replace_ready"] = False
    if reason not in payload["blocked_reasons"]:
        payload["blocked_reasons"].append(reason)
    payload.setdefault("execution_errors", []).append({"reason": reason, "detail": detail})
    if protective_stop_missing or protective_stop_uncertain:
        payload["requires_recovery_state"] = True
        payload["requires_protective_stop_recovery"] = True


def _build_new_protective_stop_record(
    *,
    previous_record: dict[str, Any],
    place_params: dict[str, Any],
    place_response: dict[str, Any],
    snapshot: dict[str, Any],
    risk_change: dict[str, Any],
    created_at: str,
    adopted_from: str,
) -> dict[str, Any]:
    position = snapshot.get("position") or {}
    target_stage = int(risk_change.get("target_lock_stage") or previous_record.get("lock_stage") or 0)
    return {
        "version": 1,
        "venue": "binance_usdt_perp",
        "symbol": "ETHUSDT",
        "algo_id": str(place_response.get("algoId") or ""),
        "client_algo_id": str(place_response.get("clientAlgoId") or place_params.get("clientAlgoId") or ""),
        "side": str(place_params.get("side") or ""),
        "order_type": str(place_params.get("type") or ""),
        "algo_status": str(place_response.get("algoStatus") or place_response.get("status") or ""),
        "trigger_price": float(place_params.get("triggerPrice")),
        "close_position": False,
        "quantity": float(place_params.get("quantity")),
        "position_amt_at_adopt": position.get("position_amt"),
        "position_direction_at_adopt": position.get("direction"),
        "entry_price_at_adopt": position.get("entry_price"),
        "adopted_from": adopted_from,
        "previous_algo_id": str(previous_record.get("algo_id") or ""),
        "lock_stage": target_stage,
        "lock_target_price": float(place_params.get("triggerPrice")),
        "lock_target_pct": risk_change.get("target_lock_pct"),
        "source_entry_price": position.get("entry_price"),
        "preview_created_at": created_at,
        "confirmed_at": created_at,
    }


def render_panel(payload: dict[str, Any]) -> str:
    snapshot = payload.get("snapshot") or {}
    position = snapshot.get("position") or {}
    candidate = payload.get("candidate_order") or {}
    record = payload.get("recorded_protective_stop") or {}
    pnl = payload.get("pnl_state") or {}
    risk = payload.get("risk_change") or {}
    checks = payload.get("checks") or {}
    request_preview = payload.get("request_preview") or {}
    blocked = payload.get("blocked_reasons") or []
    status = "[REPLACE CONFIRMED]" if payload.get("state_written") else "[REPLACE PREVIEW READY]" if payload.get("replace_ready") else "[BLOCKED: NO REPLACE COMMAND GENERATED]"
    lines = [
        status,
        "",
        "Protective Stop Replace Preview",
        f"Created: {payload.get('created_at')}",
        f"State path: {payload.get('state_path')}",
        "",
        "Position / PnL",
        f"  Position: {position.get('direction', '')} {_fmt(position.get('position_amt'))} ETH",
        f"  Entry price: {_money(position.get('entry_price'))}",
        f"  Mark price: {_money(position.get('mark_price'))}",
        f"  Unrealized PnL: {_money(pnl.get('unrealized_pnl_usd'))} ({_pct(pnl.get('unrealized_pnl_pct_on_margin'))})",
        f"  Price vs entry: {_pct(pnl.get('price_vs_entry_pct'))}",
        "",
        "Recorded vs Exchange Stop",
        f"  Recorded algo_id: {record.get('algo_id', '')}",
        f"  Exchange algo_id: {candidate.get('algoId', '')}",
        f"  Current stop: {_money(candidate.get('triggerPrice') or candidate.get('stopPrice'))}",
        f"  Side/type: {candidate.get('side', '')} {candidate.get('orderType') or candidate.get('type') or ''}",
        f"  Quantity: {_normalize_optional(candidate.get('quantity'))}",
        "",
        "Proposed Change",
        f"  Target stop: {_money(risk.get('target_stop_price'))}",
        f"  Direction: {risk.get('direction_label', 'n/a')}",
        f"  Stop change: {_pct(risk.get('stop_change_pct'))}",
        f"  Old risk at stop: {_money(risk.get('old_loss_usd'))}",
        f"  New risk at stop: {_money(risk.get('new_loss_usd'))}",
        f"  Risk delta: {_money(risk.get('risk_delta_usd'))}",
        f"  Mark buffer: {_pct(risk.get('mark_buffer_pct'))} (min {_pct(risk.get('min_mark_buffer_pct'))})",
        f"  Profit lock: {_pct(risk.get('target_lock_pct'))} (min {_pct(risk.get('min_profit_lock_pct'))})",
        "",
        "Checks",
        f"  Active protective order count: {checks.get('candidate_count', 0)}",
        f"  Algo id matches record: {_check(checks.get('algo_id_matches_record'))}",
        f"  Side matches position: {_check(checks.get('side_matches_position'))}",
        f"  Quantity/closePosition valid: {_check(checks.get('quantity_matches_position'))}",
        f"  Trigger matches record: {_check(checks.get('trigger_matches_record'))}",
        f"  Snapshot fresh: {_check(checks.get('snapshot_fresh'))}",
    ]
    if request_preview:
        place = request_preview["place"]
        lines.extend(["", "Dry-run REST Requests"])
        cancel = request_preview.get("cancel")
        if cancel:
            lines.extend([
                f"  Cancel: {cancel['method']} {cancel['path']}",
                f"    params: {json.dumps(cancel['params'], ensure_ascii=False)}",
            ])
        else:
            lines.append("  Cancel: skipped (missing protective stop repair)")
        lines.extend([
            f"  Place: {place['method']} {place['path']}",
            f"    params: {json.dumps(place['params'], ensure_ascii=False)}",
            "",
            "Cancel -> Place Gap Risk" if cancel else "Protection Gap Risk",
            "  Replacement uses cancel -> place; missing repair uses place-only.",
            "  Execution requires manual confirmation token and --accept-gap-risk.",
        ])
    if payload.get("warnings"):
        lines.extend(["", "Warnings"])
        lines.extend(f"  - {warning}" for warning in payload["warnings"])
    if blocked:
        lines.extend(["", "Blocked Reasons"])
        lines.extend(f"  - {reason}" for reason in blocked)
        lines.extend(["", "[BLOCKED: NO REPLACE COMMAND GENERATED]"])
    elif payload.get("mode") == "preview":
        lines.extend(["", "CONFIRM COMMAND", "=" * 72, payload.get("confirm_command") or "", "=" * 72])
    elif payload.get("state_written"):
        lines.extend(["", "Replace completed and shared state updated."])
    else:
        lines.extend(["", "Confirm mode validated but no state write occurred."])
    return "\n".join(lines)


def _write_report(*, payload: dict[str, Any], report_root: Path) -> None:
    report_root.mkdir(parents=True, exist_ok=True)
    (report_root / "latest_preview.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if payload.get("mode") == "confirm":
        (report_root / "latest_confirm.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    timestamp = str(payload.get("created_at") or datetime.now().isoformat()).replace(":", "").replace("-", "")
    (report_root / f"replace_preview_{timestamp}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_preview_file(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _require_decimal(value: Any, reason: str) -> Decimal:
    parsed = _to_decimal(value)
    if parsed is None:
        raise ValueError(reason)
    return parsed


def _format_price(value: Any) -> str:
    parsed = _to_decimal(value)
    if parsed is None:
        return ""
    return format(parsed, "f")


def _pct(value: Any) -> str:
    if value in (None, ""):
        return "n/a"
    try:
        return f"{float(value) * 100:.2f}%"
    except (TypeError, ValueError):
        return str(value)


def _check(value: Any) -> str:
    return "PASS" if value is True else "FAIL" if value is False else "n/a"


def _parse_datetime(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except (TypeError, ValueError):
        return None


def _stable_json(value: Any) -> str:
    return json.dumps(value or {}, sort_keys=True, ensure_ascii=False)


def _quote_arg(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


if __name__ == "__main__":
    raise SystemExit(main())
