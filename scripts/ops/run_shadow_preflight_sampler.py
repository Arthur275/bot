from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from scripts.path_utils import repo_root_from_script
from bot.time_utils import utc_now

try:
    from .run_shadow_preflight_cycle import DEFAULT_QUANT_ROOT, ParsedArgs, default_output_root, run_cycle
    from .shadow_preflight_diagnostics import SAMPLE_HANDOFF_FIELDS
except ImportError:
    from scripts.ops.run_shadow_preflight_cycle import DEFAULT_QUANT_ROOT, ParsedArgs, default_output_root, run_cycle
    from scripts.ops.shadow_preflight_diagnostics import SAMPLE_HANDOFF_FIELDS


def _default_sample_root() -> str:
    return str(Path.home() / ".codex" / "memories" / "eth_bot_shadow_preflight_samples")


def _build_cycle_args(args: argparse.Namespace, output_root: Path) -> ParsedArgs:
    cycle_args = ParsedArgs()
    cycle_args.quant_root = args.quant_root
    cycle_args.output_root = str(output_root)
    cycle_args.proxy_url = args.proxy_url
    cycle_args.include_okx_overlay = bool(args.include_okx_overlay)
    cycle_args.include_coinglass_overlay = args.include_coinglass_overlay
    cycle_args.api_key_env = args.api_key_env
    cycle_args.api_secret_env = args.api_secret_env
    cycle_args.api_passphrase_env = getattr(args, "api_passphrase_env", None)
    return cycle_args


def _summarize_sample(*, sample_id: int, payload: dict[str, Any], started_at: datetime, status: str, error: str = "") -> dict[str, Any]:
    handoff = payload.get("handoff") or {}
    execution_plan = payload.get("execution_plan") or {}
    runtime_snapshot = payload.get("runtime_snapshot") or {}
    runtime_position = runtime_snapshot.get("position") if isinstance(runtime_snapshot, dict) else {}
    runtime_position = runtime_position if isinstance(runtime_position, dict) else {}
    summary = {
        "sample_id": sample_id,
        "started_at": started_at.isoformat(timespec="seconds"),
        "finished_at": utc_now().isoformat(),
        "status": status,
        "error": error,
        "runtime_mode": payload.get("runtime_mode"),
        "planning_runtime_mode": payload.get("planning_runtime_mode"),
        "real_order_submission_intent": payload.get("real_order_submission_intent"),
        "engine_mode": payload.get("engine_mode"),
        "requested_action": payload.get("requested_action"),
        "effective_action": payload.get("effective_action"),
        "plan_reason": payload.get("plan_reason"),
        "direction": handoff.get("direction"),
        "execution_allowed": handoff.get("execution_allowed"),
        "execution_block_reason": handoff.get("execution_block_reason"),
        "execution_warnings": handoff.get("execution_warnings") or [],
        "position_size_pct": handoff.get("position_size_pct"),
        "executable_size_pct": execution_plan.get("executable_size_pct") or handoff.get("executable_size_pct"),
        "stop_distance_pct": execution_plan.get("stop_distance_pct") or handoff.get("stop_distance_pct"),
        "account_risk_pct": execution_plan.get("account_risk_pct"),
        "initial_stop_loss": handoff.get("initial_stop_loss"),
        "estimated_cost_pct": handoff.get("estimated_cost_pct"),
        "net_edge_pct": handoff.get("net_edge_pct"),
        "command_targets": payload.get("command_targets") or [],
        "preflight_statuses": payload.get("preflight_statuses") or [],
        "preflight": payload.get("preflight") or [],
        "preflight_error": payload.get("preflight_error") or "",
        "reason_codes": payload.get("reason_codes") or [],
        "judgement": payload.get("judgement") or {},
        "exchange_venue": payload.get("exchange_venue") or "",
        "exchange_symbol": payload.get("exchange_symbol") or "",
        "runtime_snapshot": runtime_snapshot,
        "runtime_account_equity": runtime_snapshot.get("account_equity") if isinstance(runtime_snapshot, dict) else None,
        "runtime_account_equity_source": runtime_snapshot.get("account_equity_source") if isinstance(runtime_snapshot, dict) else "",
        "runtime_unrealized_pnl_usd": runtime_position.get("unrealized_pnl_usd"),
        "runtime_unrealized_pnl_pct": runtime_position.get("unrealized_pnl_pct_on_margin"),
        "price_vs_entry_pct": runtime_position.get("price_vs_entry_pct"),
        "audit_log_path": payload.get("audit_log_path"),
        "state_path": payload.get("state_path"),
    }
    for field in SAMPLE_HANDOFF_FIELDS:
        if field in handoff:
            summary[field] = handoff.get(field)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Sample quant strict-live -> bot shadow -> OKX preflight repeatedly.")
    parser.add_argument("--quant-root", default=DEFAULT_QUANT_ROOT)
    parser.add_argument("--sample-root", default=_default_sample_root())
    parser.add_argument("--cycle-output-root", default=default_output_root())
    parser.add_argument("--proxy-url", default="http://127.0.0.1:7897")
    parser.add_argument("--include-okx-overlay", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--include-coinglass-overlay", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--api-key-env", default=None)
    parser.add_argument("--api-secret-env", default=None)
    parser.add_argument("--api-passphrase-env", default=None)
    parser.add_argument("--interval-sec", type=int, default=900)
    parser.add_argument("--samples", type=int, default=96)
    args = parser.parse_args()

    sample_root = Path(args.sample_root)
    sample_root.mkdir(parents=True, exist_ok=True)
    summary_path = sample_root / "samples.jsonl"
    bot_root = repo_root_from_script(__file__)

    for sample_id in range(1, max(1, args.samples) + 1):
        started_at = utc_now()
        cycle_output_root = Path(args.cycle_output_root) / f"sample_{sample_id:04d}"
        try:
            payload = run_cycle(args=_build_cycle_args(args, cycle_output_root), bot_root=bot_root)
            summary = _summarize_sample(sample_id=sample_id, payload=payload, started_at=started_at, status="ok")
        except Exception as exc:
            summary = _summarize_sample(sample_id=sample_id, payload={}, started_at=started_at, status="error", error=f"{exc.__class__.__name__}: {exc}")

        with summary_path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(summary, ensure_ascii=False, sort_keys=True) + "\n")
        print(json.dumps(summary, ensure_ascii=False, indent=2))

        if sample_id < args.samples:
            time.sleep(max(1, args.interval_sec))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
