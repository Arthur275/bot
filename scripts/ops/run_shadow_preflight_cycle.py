from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

try:
    from scripts.path_utils import repo_root_from_script
    from scripts.quant_runtime_bridge import ensure_runtime_src_paths, load_quant_runtime_contracts
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from scripts.path_utils import repo_root_from_script
    from scripts.quant_runtime_bridge import ensure_runtime_src_paths, load_quant_runtime_contracts

try:
    from .shadow_preflight_diagnostics import summarize_handoff, summarize_judgement
except ImportError:
    from scripts.ops.shadow_preflight_diagnostics import summarize_handoff, summarize_judgement


BOT_ROOT = repo_root_from_script(__file__)
DEFAULT_QUANT_ROOT = str(Path(os.environ.get("QUANT_ROOT") or BOT_ROOT.parent / "quant_system_rebuild"))


class ParsedArgs(argparse.Namespace):
    quant_root: str
    output_root: str
    proxy_url: str
    include_okx_overlay: bool
    include_coinglass_overlay: bool
    consensus_request_timeout_sec: float
    research_sync_request_path: str | None
    research_dispatch_request_path: str | None
    api_key_env: str | None
    api_secret_env: str | None
    api_passphrase_env: str | None
    enable_real_orders: bool


def default_output_root() -> str:
    return str(Path.home() / ".codex" / "memories" / "eth_bot_shadow_preflight")


def _add_src_paths(*, bot_root: Path, quant_root: Path) -> None:
    ensure_runtime_src_paths(bot_root=bot_root, quant_root=quant_root)


def _load_latest_audit_event(audit_path: Path) -> dict[str, Any]:
    if not audit_path.exists():
        return {}
    lines = [line for line in audit_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not lines:
        return {}
    return json.loads(lines[-1])


def _summarize_preflight_result(result: Any) -> dict[str, Any]:
    details = result.details or {}
    prepared = details.get("prepared_request") or {}
    signed = details.get("signed_request") or {}
    prepared_params = prepared.get("params") or {}
    signed_params = signed.get("params") or {}
    body = prepared.get("body") or {}
    return {
        "target": result.target,
        "status": result.status,
        "accepted": result.accepted,
        "simulated": result.simulated,
        "reason": result.reason,
        "method": prepared.get("method"),
        "path": prepared.get("path"),
        "side": prepared_params.get("side") or body.get("side"),
        "type": prepared_params.get("type") or body.get("ordType"),
        "quantity": prepared_params.get("quantity") or body.get("sz"),
        "stopPrice": prepared_params.get("stopPrice") or body.get("triggerPx"),
        "closePosition": prepared_params.get("closePosition"),
        "newClientOrderId": prepared_params.get("newClientOrderId") or body.get("clOrdId") or body.get("algoClOrdId"),
        "signed_quantity": signed_params.get("quantity") or (signed.get("body") or {}).get("sz"),
        "signed_stopPrice": signed_params.get("stopPrice"),
        "resolution_mode": body.get("resolution_mode"),
        "resolved_account_equity": body.get("resolved_account_equity"),
        "resolved_leverage": body.get("resolved_leverage"),
        "resolved_mark_price": body.get("resolved_mark_price"),
        "resolved_stop_price": body.get("resolved_stop_price"),
        "error": details.get("error"),
        "http_status": details.get("http_status"),
        "runtime_snapshot": details.get("runtime_snapshot"),
    }


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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run one quant strict-live -> bot shadow -> OKX preflight cycle without submitting orders."
    )
    parser.add_argument("--quant-root", default=DEFAULT_QUANT_ROOT)
    parser.add_argument("--output-root", default=default_output_root())
    parser.add_argument("--proxy-url", default="http://127.0.0.1:7897")
    parser.add_argument("--include-okx-overlay", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--include-coinglass-overlay", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--consensus-request-timeout-sec", type=float, default=15.0)
    parser.add_argument("--research-sync-request", dest="research_sync_request_path", default=None)
    parser.add_argument("--research-dispatch-request", dest="research_dispatch_request_path", default=None)
    parser.add_argument("--api-key-env", default=None)
    parser.add_argument("--api-secret-env", default=None)
    parser.add_argument("--api-passphrase-env", default=None)
    parser.add_argument(
        "--enable-real-orders",
        action="store_true",
        default=False,
        help="Emit a real-order candidate payload after simulated-real validation; this script still does not submit orders.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args(namespace=ParsedArgs())
    payload = run_cycle(args=args, bot_root=BOT_ROOT)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def run_cycle(*, args: ParsedArgs, bot_root: Path) -> dict[str, Any]:
    quant_root = Path(args.quant_root)
    contracts = load_quant_runtime_contracts(bot_root=bot_root, quant_root=quant_root)

    from bot.config import BotConfig, RuntimeMode
    from bot.engine_client import EngineClient
    from bot.exchange_adapter import AdapterCredentials, ExchangeAdapter
    from bot.orchestrator import ShadowOrchestrator
    from bot.position_manager import ExecutionPlan
    from bot.time_utils import utc_now

    output_root = Path(args.output_root)
    if not output_root.is_absolute():
        output_root = bot_root / output_root
    output_root.mkdir(parents=True, exist_ok=True)

    real_order_submission_intent = bool(getattr(args, "enable_real_orders", False))
    planning_runtime_mode = RuntimeMode.SIMULATED_REAL if real_order_submission_intent else RuntimeMode.SHADOW
    config = BotConfig(
        runtime_mode=planning_runtime_mode,
        audit_log_path=output_root / "audit.jsonl",
        state_store_path=output_root / "state.json",
        artifacts_root=output_root / "artifacts",
        proxy_url=args.proxy_url or None,
        include_okx_overlay=bool(args.include_okx_overlay),
        include_coinglass_overlay=args.include_coinglass_overlay,
        consensus_request_timeout_sec=float(getattr(args, "consensus_request_timeout_sec", 15.0) or 15.0),
        research_sync_request_path=Path(sync_path) if (sync_path := getattr(args, "research_sync_request_path", None)) else None,
        research_dispatch_request_path=Path(dispatch_path) if (dispatch_path := getattr(args, "research_dispatch_request_path", None)) else None,
    )
    missing_api_envs: list[str] = []
    credentials = None
    real_adapter = None
    if args.api_key_env and args.api_secret_env:
        requested_api_envs = [
            args.api_key_env,
            args.api_secret_env,
            getattr(args, "api_passphrase_env", None) or getattr(config, "exchange_api_passphrase_env", ""),
        ]
        missing_api_envs = [
            str(env_name)
            for env_name in requested_api_envs
            if env_name and not os.getenv(str(env_name))
        ]
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
        real_adapter = _load_real_adapter(config.exchange_venue)(credentials)
    client = EngineClient(
        config,
        run_live_judgement_fn=contracts.run_live_judgement,
        build_execution_handoff_fn=contracts.build_execution_handoff,
        decision_envelope_factory=contracts.decision_envelope_factory,
    )
    report = ShadowOrchestrator(config, engine_client=client, exchange_adapter=real_adapter).run_cycle(
        generated_at=utc_now()
    )

    audit_event = _load_latest_audit_event(config.audit_log_path)
    audit_payload = audit_event.get("payload") or {}
    execution_plan = audit_payload.get("execution_plan") or {}
    handoff = audit_payload.get("handoff") or {}

    execution_plan_model = ExecutionPlan.model_validate(execution_plan)
    commands = ExchangeAdapter().build_commands(execution_plan=execution_plan_model, handoff=handoff)
    preflight_error = ""
    preflight_results = []
    if commands and real_adapter is not None:
        try:
            preflight_results = real_adapter.preflight_commands(commands=commands)
        except Exception as exc:
            preflight_error = f"{exc.__class__.__name__}: {exc}"
    elif commands:
        preflight_error = "preflight_skipped_missing_api_env"

    payload = {
        "runtime_mode": RuntimeMode.REAL.value if real_order_submission_intent else report.runtime_mode,
        "planning_runtime_mode": report.runtime_mode,
        "real_order_submission_intent": real_order_submission_intent,
        "exchange_venue": config.exchange_venue,
        "exchange_symbol": config.exchange_symbol,
        "engine_mode": config.engine_mode.value,
        "adapter_capabilities": (real_adapter or ExchangeAdapter()).get_capabilities().model_dump(mode="json"),
        "requested_action": report.requested_action,
        "effective_action": report.effective_action,
        "plan_reason": report.plan_reason,
        "blocked": report.blocked,
        "degraded": report.degraded,
        "reason_codes": report.reason_codes,
        "execution_overview": report.execution_overview,
        "execution_plan": {
            "requested_action": execution_plan.get("requested_action"),
            "effective_action": execution_plan.get("effective_action"),
            "plan_reason": execution_plan.get("plan_reason"),
            "place_entry_order": execution_plan.get("place_entry_order"),
            "maintain_protective_stop": execution_plan.get("maintain_protective_stop"),
            "place_take_profit_orders": execution_plan.get("place_take_profit_orders"),
            "executable_size_pct": execution_plan.get("executable_size_pct"),
            "stop_distance_pct": execution_plan.get("stop_distance_pct"),
            "account_risk_pct": execution_plan.get("account_risk_pct"),
            "notes": execution_plan.get("notes"),
        },
        "judgement": summarize_judgement(audit_payload.get("judgement") or {}),
        "handoff": summarize_handoff(handoff),
        "command_targets": [command.target for command in commands],
        "execution_commands": [command.model_dump(mode="json") for command in commands],
        "preflight_statuses": [result.status for result in preflight_results],
        "preflight_error": preflight_error,
        "preflight_diagnostics": [f"missing_api_env:{env_name}" for env_name in missing_api_envs],
        "preflight": [_summarize_preflight_result(result) for result in preflight_results],
        "runtime_snapshot": audit_payload.get("runtime_snapshot") or {},
        "audit_log_path": str(config.audit_log_path),
        "state_path": str(config.state_store_path),
    }
    return payload


if __name__ == "__main__":
    raise SystemExit(main())
