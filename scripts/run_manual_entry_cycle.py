from __future__ import annotations

import argparse
import json
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

try:
    from .run_shadow_preflight_cycle import DEFAULT_QUANT_ROOT, _add_src_paths, _load_latest_audit_event, _summarize_preflight_result
except ImportError:
    from run_shadow_preflight_cycle import DEFAULT_QUANT_ROOT, _add_src_paths, _load_latest_audit_event, _summarize_preflight_result


def _default_output_root() -> str:
    return str(Path.home() / ".codex" / "memories" / "eth_bot_manual_entry")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Preview or manually confirm one strict-live ETH entry cycle."
    )
    parser.add_argument("--quant-root", default=DEFAULT_QUANT_ROOT)
    parser.add_argument("--output-root", default=_default_output_root())
    parser.add_argument("--proxy-url", default="http://127.0.0.1:7897")
    parser.add_argument("--include-okx-overlay", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--include-coinglass-overlay", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--api-key-env", default="OKX_TRADE_API_KEY")
    parser.add_argument("--api-secret-env", default="OKX_TRADE_API_SECRET")
    parser.add_argument("--api-passphrase-env", default="OKX_TRADE_PASSPHRASE")
    parser.add_argument("--confirm-token", default="")
    parser.add_argument("--json", action="store_true", help="Emit the full structured payload instead of the human confirmation panel.")
    return parser


def _load_real_adapter(venue: str) -> Any:
    from bot.exchange_adapter import BinancePerpAdapter, OkxUsdtSwapAdapter

    if venue == "okx_usdt_swap":
        return OkxUsdtSwapAdapter
    if venue == "binance_usdt_perp":
        return BinancePerpAdapter
    raise ValueError(f"Unsupported real adapter venue: {venue}")


def _load_binance_perp_adapter() -> Any:
    from bot.exchange_adapter import BinancePerpAdapter

    return BinancePerpAdapter


def main() -> int:
    args = build_parser().parse_args()
    payload = run_cycle(args=args, bot_root=Path(__file__).resolve().parents[1])
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(render_confirmation_panel(payload, args=args))
    return 0


def run_cycle(*, args: argparse.Namespace, bot_root: Path) -> dict[str, Any]:
    quant_root = Path(args.quant_root)
    _add_src_paths(bot_root=bot_root, quant_root=quant_root)

    from bot.config import BotConfig, RuntimeMode
    from bot.engine_client import EngineClient
    from bot.exchange_adapter import AdapterCredentials, ExchangeAdapter
    from bot.orchestrator import ShadowOrchestrator
    from bot.position_manager import ExecutionPlan
    from contracts.execution import DecisionEnvelope
    from interfaces.live_judgement import run_live_judgement
    from interfaces.runner import build_execution_handoff

    output_root = Path(args.output_root)
    if not output_root.is_absolute():
        output_root = bot_root / output_root
    output_root.mkdir(parents=True, exist_ok=True)

    config = BotConfig(
        runtime_mode=RuntimeMode.REAL,
        manual_entry_confirmation_token=str(args.confirm_token or ""),
        audit_log_path=output_root / "audit.jsonl",
        state_store_path=output_root / "state.json",
        artifacts_root=output_root / "artifacts",
        proxy_url=args.proxy_url or None,
        include_okx_overlay=bool(args.include_okx_overlay),
        include_coinglass_overlay=args.include_coinglass_overlay,
    )
    credentials = AdapterCredentials(
        venue=config.exchange_venue,
        api_key_env=args.api_key_env,
        api_secret_env=args.api_secret_env,
        api_passphrase_env=getattr(args, "api_passphrase_env", None) or config.exchange_api_passphrase_env,
        recv_window_ms=config.recv_window_ms,
        timeout_sec=config.timeout_sec,
        proxy_url=config.proxy_url,
        api_base_url=config.exchange_api_base_url,
    )
    real_adapter = _load_real_adapter(config.exchange_venue)(credentials)
    client = EngineClient(
        config,
        run_live_judgement_fn=run_live_judgement,
        build_execution_handoff_fn=build_execution_handoff,
        decision_envelope_factory=DecisionEnvelope.model_validate,
    )
    report = ShadowOrchestrator(config, engine_client=client, exchange_adapter=real_adapter).run_cycle(
        generated_at=datetime.now().replace(microsecond=0)
    )

    audit_event = _load_latest_audit_event(config.audit_log_path)
    audit_payload = audit_event.get("payload") or {}
    execution_plan = audit_payload.get("execution_plan") or {}
    handoff = audit_payload.get("handoff") or {}
    runtime_snapshot = audit_payload.get("runtime_snapshot") or {}
    execution_results = audit_payload.get("execution_results") or []
    expected_token = _first_expected_confirmation_token(execution_results)
    confirmation_matched = bool(args.confirm_token) and not expected_token and "blocked" not in report.command_result_statuses

    preflight_results = []
    preflight_error = ""
    if expected_token:
        execution_plan_model = ExecutionPlan.model_validate(execution_plan)
        commands = ExchangeAdapter().build_commands(execution_plan=execution_plan_model, handoff=handoff)
        try:
            preflight_results = real_adapter.preflight_commands(commands=commands)
        except Exception as exc:
            preflight_error = f"{exc.__class__.__name__}: {exc}"

    preflight = [_summarize_preflight_result(result) for result in preflight_results]
    execution_summary = [_summarize_execution_result(result) for result in execution_results]
    entry_preflight = _first_preflight(preflight, "entry_order") or _first_preflight(execution_summary, "entry_order")
    risk_preview = _build_risk_preview(
        entry_preflight=entry_preflight,
        execution_plan=execution_plan,
        handoff=handoff,
        runtime_snapshot=runtime_snapshot,
    )
    payload = {
        "mode": "confirmed" if args.confirm_token else "preview",
        "runtime_mode": report.runtime_mode,
        "requested_action": report.requested_action,
        "effective_action": report.effective_action,
        "plan_reason": report.plan_reason,
        "command_types": report.command_types,
        "command_result_statuses": report.command_result_statuses,
        "execution_overview": report.execution_overview,
        "runtime_snapshot": runtime_snapshot,
        "risk_preview": risk_preview,
        "manual_entry_confirmation": {
            "required": bool(expected_token),
            "expected_token": expected_token,
            "provided": bool(args.confirm_token),
            "matched": confirmation_matched,
        },
        "preflight_statuses": [result.status for result in preflight_results],
        "preflight_error": preflight_error,
        "preflight": preflight,
        "execution_results": execution_summary,
        "audit_log_path": str(config.audit_log_path),
        "state_path": str(config.state_store_path),
    }
    payload["confirm_ready"] = _is_confirm_ready(payload)
    payload["confirm_command"] = _build_confirm_command(args=args, token=expected_token) if payload["confirm_ready"] else ""
    return payload


def render_confirmation_panel(payload: dict[str, Any], *, args: argparse.Namespace) -> str:
    confirmation = payload.get("manual_entry_confirmation") or {}
    risk = payload.get("risk_preview") or {}
    runtime = payload.get("runtime_snapshot") or {}
    position = runtime.get("position") or {}
    entry = _first_preflight(payload.get("preflight") or [], "entry_order")
    if not entry:
        entry = _first_preflight(payload.get("execution_results") or [], "entry_order")
    stop = _first_preflight(payload.get("preflight") or [], "maintain_protective_stop")

    header = _status_header(payload)
    lines = [
        f"{header}",
        "=" * 72,
        f"Mode: {payload.get('mode')} | Runtime: {payload.get('runtime_mode')} | Action: {payload.get('effective_action')}",
        f"Decision: {payload.get('plan_reason')}",
        "",
        "Runtime Snapshot",
        f"- Snapshot: {_ok(runtime.get('snapshot_valid'))} | Equity: {_money(runtime.get('account_equity'))} | Leverage: {_value(position.get('leverage'))}x",
        f"- Position: {_value(position.get('position_state'))} {_value(position.get('direction'))} size={_value(position.get('size_pct'))}",
        f"- Protective stop present: {_ok(runtime.get('protective_stop_present'))}",
        "",
        "Entry Order",
        f"- Side: {_value(entry.get('side'))} | Type: {_value(entry.get('type'))} | Quantity: {_value(entry.get('quantity') or entry.get('signed_quantity'))} ETH",
        f"- Mark price: {_money(risk.get('mark_price'))} | Estimated stop price: {_money(risk.get('estimated_stop_price'))}",
        f"- Notional: {_money(risk.get('notional_usd'))} | Estimated loss at stop: {_money(risk.get('estimated_loss_usd'))}",
        f"- Loss / equity: {_pct(risk.get('loss_equity_ratio'))} | Stop distance: {_pct(risk.get('stop_distance_pct'))}",
        "",
        "Checks",
        f"- Entry preflight: {_preflight_status(entry)}",
        f"- Protective stop preflight: {_preflight_status(stop)}",
        f"- Runtime snapshot error: {_runtime_error(runtime)}",
        f"- Preflight error: {_none(payload.get('preflight_error'))}",
        "",
        "Artifacts",
        f"- Audit: {payload.get('audit_log_path')}",
        f"- State: {payload.get('state_path')}",
    ]
    command = payload.get("confirm_command") or ""
    if command:
        lines.extend(
            [
                "",
                "=" * 72,
                "CONFIRM COMMAND",
                "=" * 72,
                command,
                "=" * 72,
            ]
        )
    elif confirmation.get("matched"):
        lines.extend(["", "=" * 72, "CONFIRMATION ACCEPTED", "=" * 72])
    elif confirmation.get("required"):
        lines.extend(["", "=" * 72, "CONFIRM COMMAND SUPPRESSED: PREFLIGHT NOT READY", "=" * 72])
    return "\n".join(lines)


def _status_header(payload: dict[str, Any]) -> str:
    confirmation = payload.get("manual_entry_confirmation") or {}
    if confirmation.get("matched"):
        return "[READY TO EXECUTE] Confirm token supplied. Real order may be submitted."
    if confirmation.get("required"):
        return "[BLOCKED: MANUAL CONFIRMATION REQUIRED] Entry is ready, waiting for token."
    return "[PRE-FLIGHT CHECK] No real order will be submitted."


def _build_risk_preview(
    *,
    entry_preflight: dict[str, Any],
    execution_plan: dict[str, Any],
    handoff: dict[str, Any],
    runtime_snapshot: dict[str, Any],
) -> dict[str, Any]:
    quantity = _to_decimal(entry_preflight.get("quantity") or entry_preflight.get("signed_quantity"))
    mark_price = _to_decimal(entry_preflight.get("resolved_mark_price"))
    equity = _to_decimal(entry_preflight.get("resolved_account_equity") or runtime_snapshot.get("account_equity"))
    stop_distance = _to_decimal(execution_plan.get("stop_distance_pct") or handoff.get("stop_distance_pct"))
    direction = str(handoff.get("direction") or entry_preflight.get("side") or "").lower()
    notional = quantity * mark_price if quantity is not None and mark_price is not None else None
    estimated_loss = notional * stop_distance if notional is not None and stop_distance is not None else None
    loss_equity_ratio = estimated_loss / equity if estimated_loss is not None and equity and equity > 0 else None
    stop_price = None
    if mark_price is not None and stop_distance is not None:
        stop_price = mark_price * (Decimal("1") + stop_distance) if "short" in direction else mark_price * (Decimal("1") - stop_distance)
    return {
        "quantity": _decimal_to_float(quantity),
        "mark_price": _decimal_to_float(mark_price),
        "account_equity": _decimal_to_float(equity),
        "stop_distance_pct": _decimal_to_float(stop_distance),
        "estimated_stop_price": _decimal_to_float(stop_price),
        "notional_usd": _decimal_to_float(notional),
        "estimated_loss_usd": _decimal_to_float(estimated_loss),
        "loss_equity_ratio": _decimal_to_float(loss_equity_ratio),
    }


def _build_confirm_command(*, args: argparse.Namespace, token: str) -> str:
    if not token:
        return ""
    parts = [
        "python scripts\\run_manual_entry_cycle.py",
        f"--quant-root {_quote_arg(args.quant_root)}",
        f"--output-root {_quote_arg(args.output_root)}",
        f"--proxy-url {_quote_arg(args.proxy_url)}",
        "--include-okx-overlay" if args.include_okx_overlay else "--no-include-okx-overlay",
        "--include-coinglass-overlay" if args.include_coinglass_overlay is not False else "--no-include-coinglass-overlay",
        f"--api-key-env {_quote_arg(args.api_key_env)}",
        f"--api-secret-env {_quote_arg(args.api_secret_env)}",
        f"--api-passphrase-env {_quote_arg(getattr(args, 'api_passphrase_env', ''))}",
        f"--confirm-token {token}",
    ]
    return " `\n  ".join(parts)


def _is_confirm_ready(payload: dict[str, Any]) -> bool:
    confirmation = payload.get("manual_entry_confirmation") or {}
    if not confirmation.get("required") or not confirmation.get("expected_token"):
        return False
    if payload.get("preflight_error"):
        return False
    runtime = payload.get("runtime_snapshot") or {}
    if runtime.get("snapshot_valid") is not True:
        return False
    if runtime.get("error_endpoint") or runtime.get("error_kind") or runtime.get("error_message"):
        return False
    entry = _first_preflight(payload.get("preflight") or [], "entry_order")
    if entry.get("status") != "preflight_ready" or entry.get("error"):
        return False
    risk = payload.get("risk_preview") or {}
    required_risk_fields = ("mark_price", "estimated_stop_price", "notional_usd", "estimated_loss_usd", "loss_equity_ratio")
    return all(risk.get(field) is not None for field in required_risk_fields)


def _summarize_execution_result(result: dict[str, Any]) -> dict[str, Any]:
    details = result.get("details") or {}
    prepared = details.get("prepared_request") or {}
    signed = details.get("signed_request") or {}
    prepared_params = prepared.get("params") or {}
    signed_params = signed.get("params") or {}
    body = prepared.get("body") or {}
    return {
        "target": result.get("target"),
        "status": result.get("status"),
        "accepted": result.get("accepted"),
        "simulated": result.get("simulated"),
        "reason": result.get("reason"),
        "method": prepared.get("method"),
        "path": prepared.get("path"),
        "side": prepared_params.get("side") or body.get("side"),
        "type": prepared_params.get("type") or body.get("ordType"),
        "quantity": prepared_params.get("quantity") or body.get("sz"),
        "stopPrice": prepared_params.get("stopPrice") or body.get("triggerPx"),
        "closePosition": prepared_params.get("closePosition"),
        "newClientOrderId": prepared_params.get("newClientOrderId") or body.get("clOrdId") or body.get("algoClOrdId"),
        "signed_quantity": signed_params.get("quantity") or (signed.get("body") or {}).get("sz"),
        "signed_stopPrice": signed_params.get("stopPrice") or (signed.get("body") or {}).get("triggerPx"),
        "resolution_mode": body.get("resolution_mode"),
        "resolved_account_equity": body.get("resolved_account_equity"),
        "resolved_leverage": body.get("resolved_leverage"),
        "resolved_mark_price": body.get("resolved_mark_price"),
        "resolved_stop_price": body.get("resolved_stop_price"),
        "error": details.get("error"),
        "http_status": details.get("http_status"),
        "runtime_snapshot": details.get("runtime_snapshot"),
    }


def _first_expected_confirmation_token(execution_results: list[dict[str, Any]]) -> str:
    for result in execution_results:
        details = result.get("details") or {}
        token = str(details.get("expected_confirmation_token") or "")
        if token:
            return token
    return ""


def _first_preflight(preflight: list[dict[str, Any]], target: str) -> dict[str, Any]:
    return next((item for item in preflight if item.get("target") == target), {})


def _to_decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _decimal_to_float(value: Decimal | None) -> float | None:
    return float(value) if value is not None else None


def _money(value: Any) -> str:
    parsed = _to_decimal(value)
    if parsed is None:
        return "<missing>"
    return f"${parsed.quantize(Decimal('0.01'))}"


def _pct(value: Any) -> str:
    parsed = _to_decimal(value)
    if parsed is None:
        return "<missing>"
    return f"{(parsed * Decimal('100')).quantize(Decimal('0.01'))}%"


def _value(value: Any) -> str:
    if value in (None, ""):
        return "<missing>"
    return str(value)


def _none(value: Any) -> str:
    if value in (None, ""):
        return "<none>"
    return str(value)


def _ok(value: Any) -> str:
    if value is True:
        return "OK"
    if value is False:
        return "NO"
    return _value(value)


def _preflight_status(item: dict[str, Any]) -> str:
    if not item:
        return "<missing>"
    status = str(item.get("status") or "")
    error = str(item.get("error") or "")
    if error:
        return f"{status} ({error})"
    return status or "<missing>"


def _runtime_error(runtime: dict[str, Any]) -> str:
    parts = [
        str(runtime.get("error_endpoint") or ""),
        str(runtime.get("error_kind") or ""),
        str(runtime.get("error_message") or ""),
    ]
    message = " | ".join(part for part in parts if part)
    return message or "<none>"


def _quote_arg(value: Any) -> str:
    text = str(value or "")
    if not text:
        return "''"
    if any(char.isspace() for char in text):
        return f"'{text}'"
    return text


if __name__ == "__main__":
    raise SystemExit(main())
