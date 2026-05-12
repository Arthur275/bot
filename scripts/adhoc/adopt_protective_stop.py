from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from pathlib import Path
from typing import Any

from scripts.path_utils import repo_root_from_script

ACTIVE_ALGO_STATUSES = {"NEW", "PARTIALLY_FILLED", "LIVE", "EFFECTIVE"}
PROTECTIVE_ORDER_TYPES = {"STOP", "STOP_MARKET", "TRAILING_STOP_MARKET", "CONDITIONAL"}

BOT_ROOT = repo_root_from_script(__file__)
DEFAULT_STATE_PATH = str(BOT_ROOT / "runtime" / "shared_state" / "bot_state.json")
DEFAULT_REPORT_ROOT = str(BOT_ROOT / "runtime" / "reports" / "protective_stop_adopt")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Preview or confirm adoption of an existing protective algo stop.")
    parser.add_argument("--state-path", default=DEFAULT_STATE_PATH)
    parser.add_argument("--report-root", default=DEFAULT_REPORT_ROOT)
    parser.add_argument("--proxy-url", default="http://127.0.0.1:7897")
    parser.add_argument("--api-key-env", default="OKX_TRADE_API_KEY")
    parser.add_argument("--api-secret-env", default="OKX_TRADE_API_SECRET")
    parser.add_argument("--api-passphrase-env", default="OKX_TRADE_PASSPHRASE")
    parser.add_argument("--confirm-token", default="")
    parser.add_argument("--preview-file", default="")
    parser.add_argument("--max-preview-age-sec", type=int, default=180)
    parser.add_argument("--snapshot-max-age-sec", type=int, default=30)
    parser.add_argument("--json", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    payload = run(args=args)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(render_panel(payload, args=args))
    return 0


def run(*, args: argparse.Namespace) -> dict[str, Any]:
    from bot.config import BotConfig
    from bot import exchange_adapter
    from bot.exchange_adapter import AdapterCredentials
    from bot.state_store import StateStore

    now = datetime.now().replace(microsecond=0)
    report_root = Path(args.report_root)
    report_root.mkdir(parents=True, exist_ok=True)
    state_path = Path(args.state_path)
    config = BotConfig(
        state_store_path=state_path,
        audit_log_path=report_root / "adopt_audit.jsonl",
        artifacts_root=report_root / "artifacts",
        proxy_url=args.proxy_url or None,
        exchange_venue="binance_usdt_perp" if str(args.api_key_env).startswith("BINANCE_") else "okx_usdt_swap",
        exchange_symbol="ETHUSDT" if str(args.api_key_env).startswith("BINANCE_") else "ETH-USDT-SWAP",
        exchange_api_base_url="https://fapi.binance.com" if str(args.api_key_env).startswith("BINANCE_") else "https://www.okx.com",
    )
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
    store = StateStore(state_path)
    state = store.load()
    mode = "confirm" if args.confirm_token else "preview"
    previous_preview = _load_preview_file(Path(args.preview_file)) if args.preview_file else None

    payload = _build_adopt_payload(
        adapter=adapter,
        state=state,
        mode=mode,
        now=now,
        state_path=state_path,
        report_root=report_root,
        max_snapshot_age_sec=int(args.snapshot_max_age_sec),
        previous_preview=previous_preview,
        confirm_token=str(args.confirm_token or ""),
        max_preview_age_sec=int(args.max_preview_age_sec),
        api_key_env=credentials.api_key_env,
        api_secret_env=credentials.api_secret_env,
        api_passphrase_env=credentials.api_passphrase_env,
    )
    if mode == "confirm" and payload["adopt_ready"]:
        metadata = dict(state.metadata or {})
        metadata["protective_stop"] = payload["adopt_record"]
        state.metadata = metadata
        position = ((payload.get("snapshot") or {}).get("position") or {})
        if position:
            state.observed_position_state = str(position.get("position_state") or state.observed_position_state)
            state.observed_position_direction = str(position.get("direction") or state.observed_position_direction)
            state.observed_position_size_pct = float(position.get("size_pct") or state.observed_position_size_pct)
            state.protective_stop_required = False
            state.recovery_required = False
            state.reconciliation_required = False
        store.save(state)
        payload["state_written"] = True
    _write_report(payload=payload, report_root=report_root)
    return payload


def _build_adopt_payload(
    *,
    adapter: Any,
    state: Any,
    mode: str,
    now: datetime,
    state_path: Path,
    report_root: Path,
    max_snapshot_age_sec: int,
    previous_preview: dict[str, Any] | None,
    confirm_token: str,
    max_preview_age_sec: int,
    api_key_env: str,
    api_secret_env: str,
    api_passphrase_env: str,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "mode": mode,
        "created_at": now.isoformat(),
        "state_path": str(state_path),
        "report_root": str(report_root),
        "adopt_ready": False,
        "blocked_reasons": [],
        "warnings": [],
        "state_written": False,
        "existing_record": (state.metadata or {}).get("protective_stop"),
        "snapshot": {},
        "raw_open_algo_orders": [],
        "candidate_order": None,
        "checks": {},
        "confirm_token": "",
        "confirm_command": "",
        "adopt_record": None,
    }
    if payload["existing_record"]:
        payload["blocked_reasons"].append("already_adopted")
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

    candidate_orders = _filter_protective_algo_orders(raw_orders)
    checks = _validate_snapshot_and_candidate(
        snapshot=snapshot,
        candidate_orders=candidate_orders,
        now=now,
        max_snapshot_age_sec=max_snapshot_age_sec,
    )
    payload["checks"] = checks
    payload["blocked_reasons"].extend(checks["blocked_reasons"])
    candidate = checks.get("candidate_order")
    payload["candidate_order"] = candidate
    if candidate:
        payload["adopt_record"] = _build_adopt_record(
            candidate=candidate,
            snapshot=snapshot,
            preview_created_at=(previous_preview or {}).get("created_at") or now.isoformat(),
            confirmed_at=now.isoformat() if mode == "confirm" else "",
        )
        payload["confirm_token"] = _build_confirm_token(
            state_path=state_path,
            created_at=now.isoformat(),
            candidate=candidate,
            snapshot=snapshot,
        )
        payload["confirm_command"] = _build_confirm_command(
            args_state_path=state_path,
            report_root=report_root,
            token=payload["confirm_token"],
            preview_file=str(report_root / "latest_preview.json"),
            api_key_env=api_key_env,
            api_secret_env=api_secret_env,
            api_passphrase_env=api_passphrase_env,
        )

    if mode == "confirm":
        confirm_checks = _validate_confirm(
            previous_preview=previous_preview,
            confirm_token=confirm_token,
            now=now,
            max_preview_age_sec=max_preview_age_sec,
            current_payload=payload,
            state_path=state_path,
        )
        payload["confirm_checks"] = confirm_checks
        payload["blocked_reasons"].extend(confirm_checks["blocked_reasons"])
    payload["adopt_ready"] = not payload["blocked_reasons"] and (mode == "confirm")
    if mode == "preview" and not payload["blocked_reasons"]:
        payload["adopt_ready"] = False
    return payload


def _order_status(order: dict[str, Any]) -> str:
    return str(order.get("algoStatus") or order.get("state") or order.get("status") or "").upper()


def _order_type(order: dict[str, Any]) -> str:
    return str(order.get("ordType") or order.get("orderType") or order.get("type") or "").upper()


def _order_trigger_price(order: dict[str, Any]) -> Any:
    return order.get("triggerPx") or order.get("triggerPrice") or order.get("stopPrice")


def _order_quantity(order: dict[str, Any]) -> Any:
    return order.get("sz") or order.get("quantity") or order.get("origQty")


def _order_close_position(order: dict[str, Any]) -> bool:
    return _to_bool(order.get("closePosition")) or str(order.get("closeFraction") or "") == "1"


def _order_algo_id(order: dict[str, Any]) -> str:
    return str(order.get("algoId") or order.get("ordId") or order.get("orderId") or "")


def _order_client_algo_id(order: dict[str, Any]) -> str:
    return str(order.get("algoClOrdId") or order.get("clientAlgoId") or order.get("clOrdId") or order.get("clientOrderId") or "")


def _filter_protective_algo_orders(raw_orders: list[dict[str, Any]]) -> list[dict[str, Any]]:
    orders: list[dict[str, Any]] = []
    for item in raw_orders:
        if _order_status(item) not in ACTIVE_ALGO_STATUSES:
            continue
        if _order_type(item) not in PROTECTIVE_ORDER_TYPES:
            continue
        orders.append(item)
    return orders


def _validate_snapshot_and_candidate(
    *,
    snapshot: Any,
    candidate_orders: list[dict[str, Any]],
    now: datetime,
    max_snapshot_age_sec: int,
) -> dict[str, Any]:
    blocked: list[str] = []
    checks: dict[str, Any] = {"blocked_reasons": blocked}
    fetched_at = snapshot.fetched_at
    snapshot_age_sec = (now - fetched_at).total_seconds() if fetched_at else None
    checks["snapshot_age_sec"] = snapshot_age_sec
    checks["snapshot_fresh"] = snapshot_age_sec is not None and snapshot_age_sec <= max_snapshot_age_sec
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
    if len(candidate_orders) == 0:
        blocked.append("no_active_protective_algo_order")
        return checks
    if len(candidate_orders) > 1:
        blocked.append("multiple_active_protective_algo_orders")
        checks["candidate_count"] = len(candidate_orders)
        return checks

    candidate = candidate_orders[0]
    checks["candidate_order"] = candidate
    expected_side = "SELL" if position.direction == "long" else "BUY"
    checks["side_matches"] = str(candidate.get("side") or "").upper() == expected_side
    if not checks["side_matches"]:
        blocked.append("side_mismatch")
    close_position = _order_close_position(candidate)
    checks["close_position"] = close_position
    if close_position:
        checks["quantity_matches"] = True
    else:
        checks["quantity_matches"] = _decimal_equal(_order_quantity(candidate), abs(float(position.position_amt or 0.0)))
        if not checks["quantity_matches"]:
            blocked.append("quantity_mismatch")
    if _to_decimal(_order_trigger_price(candidate)) is None:
        blocked.append("trigger_price_missing")
    if not _order_algo_id(candidate):
        blocked.append("algo_id_missing")
    return checks


def _validate_confirm(
    *,
    previous_preview: dict[str, Any] | None,
    confirm_token: str,
    now: datetime,
    max_preview_age_sec: int,
    current_payload: dict[str, Any],
    state_path: Path,
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
    current = current_payload.get("candidate_order") or {}
    previous = previous_preview.get("candidate_order") or {}
    for key in ("algoId", "algoClOrdId", "clientAlgoId", "side", "ordType", "orderType", "type", "triggerPx", "triggerPrice", "stopPrice", "sz", "quantity", "closePosition", "closeFraction"):
        if _normalize_optional(current.get(key)) != _normalize_optional(previous.get(key)):
            blocked.append(f"candidate_{key}_changed")
    return checks


def _build_adopt_record(*, candidate: dict[str, Any], snapshot: Any, preview_created_at: str, confirmed_at: str) -> dict[str, Any]:
    close_position = _order_close_position(candidate)
    quantity = None if close_position else _to_optional_float(_order_quantity(candidate))
    return {
        "version": 1,
        "venue": "okx_usdt_swap" if str(candidate.get("instId") or "") == "ETH-USDT-SWAP" else "binance_usdt_perp",
        "symbol": str(candidate.get("instId") or candidate.get("symbol") or ""),
        "algo_id": _order_algo_id(candidate),
        "client_algo_id": _order_client_algo_id(candidate),
        "side": str(candidate.get("side") or ""),
        "order_type": _order_type(candidate),
        "algo_status": _order_status(candidate),
        "trigger_price": _to_optional_float(_order_trigger_price(candidate)),
        "close_position": close_position,
        "quantity": quantity,
        "position_amt_at_adopt": snapshot.position.position_amt,
        "position_direction_at_adopt": snapshot.position.direction,
        "entry_price_at_adopt": snapshot.position.entry_price,
        "adopted_from": "exchange_open_algo_orders",
        "preview_created_at": preview_created_at,
        "confirmed_at": confirmed_at,
    }


def _build_confirm_token(*, state_path: Path, created_at: str, candidate: dict[str, Any], snapshot: Any) -> str:
    basis = {
        "state_path": str(state_path),
        "created_at": created_at,
        "algo_id": _order_algo_id(candidate),
        "client_algo_id": _order_client_algo_id(candidate),
        "trigger_price": str(_order_trigger_price(candidate) or ""),
        "position_amt": str(snapshot.position.position_amt or ""),
    }
    serialized = "|".join(f"{key}={basis[key]}" for key in sorted(basis))
    return "ADOPT-" + sha256(serialized.encode("utf-8")).hexdigest()[:12].upper()


def _build_confirm_command(
    *,
    args_state_path: Path,
    report_root: Path,
    token: str,
    preview_file: str,
    api_key_env: str = "",
    api_secret_env: str = "",
    api_passphrase_env: str = "",
) -> str:
    return (
        "python scripts\\adopt_protective_stop.py `\n"
        f"  --state-path {_quote_arg(str(args_state_path))} `\n"
        f"  --report-root {_quote_arg(str(report_root))} `\n"
        f"  --preview-file {_quote_arg(preview_file)} `\n"
        f"  --api-key-env {_quote_arg(api_key_env)} `\n"
        f"  --api-secret-env {_quote_arg(api_secret_env)} `\n"
        f"  --api-passphrase-env {_quote_arg(api_passphrase_env)} `\n"
        f"  --confirm-token {token}"
    )


def render_panel(payload: dict[str, Any], *, args: argparse.Namespace) -> str:
    snapshot = payload.get("snapshot") or {}
    position = snapshot.get("position") or {}
    candidate = payload.get("candidate_order") or {}
    checks = payload.get("checks") or {}
    blocked = payload.get("blocked_reasons") or []
    status = "[READY TO ADOPT]" if not blocked and payload.get("mode") == "preview" else "[ADOPTED]" if payload.get("state_written") else "[BLOCKED: NO CONFIRM COMMAND GENERATED]"
    lines = [
        status,
        "",
        "Protective Stop Adopt Preview",
        f"Created: {payload.get('created_at')}",
        f"State path: {payload.get('state_path')}",
        "",
        "Position",
        f"  State: {position.get('position_state', '')} {position.get('direction', '')}",
        f"  Amount: {_fmt(position.get('position_amt'))} ETH",
        f"  Entry: {_money(position.get('entry_price'))}",
        f"  Mark: {_money(position.get('mark_price'))}",
        f"  Snapshot age: {_fmt(checks.get('snapshot_age_sec'))} sec",
        "",
        "Exchange Algo Stop",
        f"  algoId: {_order_algo_id(candidate)}",
        f"  clientAlgoId: {_order_client_algo_id(candidate)}",
        f"  status: {_order_status(candidate)}",
        f"  side/type: {candidate.get('side', '')} {_order_type(candidate)}",
        f"  trigger: {_money(_order_trigger_price(candidate))}",
        f"  closePosition: {_normalize_optional(candidate.get('closePosition') or candidate.get('closeFraction'))}",
        f"  quantity: {_normalize_optional(_order_quantity(candidate))}",
        "",
        "Checks",
        f"  Active protective order count: {checks.get('candidate_count', 1 if candidate else 0)}",
        f"  Side matches position: {_check(checks.get('side_matches'))}",
        f"  Quantity/closePosition valid: {_check(checks.get('quantity_matches'))}",
        f"  Snapshot fresh: {_check(checks.get('snapshot_fresh'))}",
        "  Re-check before execution: required",
    ]
    if payload.get("existing_record"):
        existing = payload["existing_record"]
        lines.extend(["", f"Already adopted: algo_id={existing.get('algo_id')} confirmed_at={existing.get('confirmed_at')}"])
    if payload.get("open_algo_orders_error"):
        error = payload["open_algo_orders_error"]
        lines.extend(["", "Failed to fetch open algo orders", f"  Error: {error.get('type')} {error.get('message')}", f"  kind/http: {error.get('kind')} {error.get('http_status')}"])
    if blocked:
        lines.extend(["", "Blocked Reasons"])
        lines.extend(f"  - {reason}" for reason in blocked)
    if not blocked and payload.get("mode") == "preview":
        lines.extend(["", "CONFIRM COMMAND", "=" * 72, payload.get("confirm_command") or "", "=" * 72])
    elif payload.get("state_written"):
        lines.extend(["", "Adopt completed. Re-check before replace: run replace preview again."])
    else:
        lines.extend(["", "[BLOCKED: NO CONFIRM COMMAND GENERATED]"])
    return "\n".join(lines)


def _write_report(*, payload: dict[str, Any], report_root: Path) -> None:
    report_root.mkdir(parents=True, exist_ok=True)
    name = "latest_preview.json" if payload.get("mode") == "preview" else "latest_confirm.json"
    (report_root / name).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    timestamp = str(payload.get("created_at") or datetime.now().isoformat()).replace(":", "").replace("-", "")
    (report_root / f"adopt_{payload.get('mode')}_{timestamp}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _load_preview_file(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _to_decimal(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _decimal_equal(left: Any, right: Any) -> bool:
    left_decimal = _to_decimal(left)
    right_decimal = _to_decimal(right)
    if left_decimal is None or right_decimal is None:
        return False
    return abs(left_decimal - right_decimal) <= Decimal("0.00000001")


def _to_optional_float(value: Any) -> float | None:
    parsed = _to_decimal(value)
    return None if parsed is None else float(parsed)


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() == "true"


def _parse_datetime(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except (TypeError, ValueError):
        return None


def _normalize_optional(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _fmt(value: Any) -> str:
    if value in (None, ""):
        return "n/a"
    try:
        return f"{float(value):.4f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        return str(value)


def _money(value: Any) -> str:
    if value in (None, ""):
        return "n/a"
    try:
        return f"${float(value):.2f}"
    except (TypeError, ValueError):
        return str(value)


def _check(value: Any) -> str:
    return "PASS" if value is True else "FAIL" if value is False else "n/a"


def _quote_arg(value: str) -> str:
    escaped = value.replace("'", "''")
    return f"'{escaped}'"


if __name__ == "__main__":
    raise SystemExit(main())
