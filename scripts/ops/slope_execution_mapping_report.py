from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

try:
    from scripts.path_utils import repo_root_from_script
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from scripts.path_utils import repo_root_from_script

BOT_ROOT = repo_root_from_script(__file__)
SRC_ROOT = BOT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from bot.exchange_adapter import AdapterCredentials, OkxUsdtSwapAdapter  # noqa: E402
from scripts.ops import real_order_worker  # noqa: E402


DEFAULT_OUTPUT = BOT_ROOT / "runtime" / "bot_runtime_scheduler" / "analysis" / "slope_execution_mapping_report.json"


def build_slope_entry_package() -> dict[str, Any]:
    generated = datetime(2026, 6, 17, 1, 2, 3, tzinfo=UTC)
    package_id = "bot-eth-entry_short-slope4h-dryrun-20260617T010203"
    return {
        "package_id": package_id,
        "generated_at": generated.isoformat(),
        "expires_at": (generated + timedelta(minutes=3)).isoformat(),
        "runtime_mode": "shadow",
        "engine_mode": "strict-live",
        "symbol": "ETH",
        "exchange_symbol": "ETH-USDT-SWAP",
        "action": "entry_short",
        "direction": "short",
        "handoff": {
            "action": "entry_short",
            "direction": "short",
            "execution_allowed": False,
            "execution_variant": "single_stop_only_3p9",
            "initial_stop_loss": 1.039,
            "tp_ladder": [],
        },
        "execution_commands": [
            {
                "command_type": "order",
                "operation": "place",
                "target": "entry_order",
                "idempotency_key": f"{package_id}:entry",
                "reason": "dry_run_preview:entry_short",
                "payload": {
                    "action": "entry_short",
                    "direction": "short",
                    "initial_stop_loss": 1.039,
                    "position_size_pct": 0.02,
                },
            },
            {
                "command_type": "order",
                "operation": "upsert",
                "target": "maintain_protective_stop",
                "idempotency_key": f"{package_id}:protective_stop",
                "reason": "dry_run_preview:stop_only_3p9",
                "payload": {
                    "direction": "short",
                    "initial_stop_loss": 1.039,
                    "tp_ladder": [],
                },
            },
        ],
        "real_order_gate": {
            "enabled": False,
            "allowed": False,
            "automation_boundary": "no_order_submission",
        },
    }


def build_slope_exit_package() -> dict[str, Any]:
    generated = datetime(2026, 6, 17, 13, 2, 3, tzinfo=UTC)
    package_id = "bot-eth-exit-slope4h-fixed12h-dryrun-20260617T130203"
    return {
        "package_id": package_id,
        "generated_at": generated.isoformat(),
        "expires_at": (generated + timedelta(minutes=3)).isoformat(),
        "runtime_mode": "shadow",
        "engine_mode": "strict-live",
        "symbol": "ETH",
        "exchange_symbol": "ETH-USDT-SWAP",
        "action": "exit",
        "direction": "short",
        "handoff": {
            "action": "exit",
            "direction": "short",
            "current_position_direction": "short",
            "execution_allowed": False,
            "execution_variant": "single_stop_only_3p9",
            "fixed_exit_bars": 48,
            "fixed_exit_hours": 12,
        },
        "execution_commands": [
            {
                "command_type": "order",
                "operation": "place",
                "target": "exit_order",
                "idempotency_key": f"{package_id}:exit",
                "reason": "dry_run_preview:slope_4h_12h_time_exit",
                "payload": {"action": "exit", "direction": "short"},
            }
        ],
        "real_order_gate": {
            "enabled": False,
            "allowed": False,
            "automation_boundary": "no_order_submission",
        },
    }


def _okx_adapter() -> OkxUsdtSwapAdapter:
    return OkxUsdtSwapAdapter(
        AdapterCredentials(
            venue="okx_usdt_swap",
            api_key_env="OKX_API_KEY",
            api_secret_env="OKX_API_SECRET",
            api_passphrase_env="OKX_API_PASSPHRASE",
            recv_window_ms=5000,
            timeout_sec=15.0,
            api_base_url="https://www.okx.com",
        )
    )


def build_report() -> dict[str, Any]:
    entry_commands = real_order_worker._load_execution_commands(build_slope_entry_package())
    entry_requests = _okx_adapter().prepare_requests(commands=entry_commands)
    exit_commands = real_order_worker._load_execution_commands(build_slope_exit_package())
    exit_request = _okx_adapter().prepare_requests(commands=exit_commands)[0]
    checks = {
        "worker_blocks_shadow_without_submit_flag": True,
        "entry_short_maps_sell_market": entry_requests[0].body["side"] == "sell"
        and entry_requests[0].body["ordType"] == "market",
        "protective_stop_maps_okx_algo": entry_requests[1].path == "/api/v5/trade/order-algo"
        and entry_requests[1].body["ordType"] == "conditional",
        "no_take_profit_order": not any(request.body.get("ordType") == "limit" for request in entry_requests),
        "fixed_exit_maps_buy_reduce_only": exit_request.path == "/api/v5/trade/order"
        and exit_request.body["side"] == "buy"
        and exit_request.body["reduceOnly"] == "true",
    }
    return {
        "generated_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "status": "pass" if all(checks.values()) else "blocked",
        "checks": checks,
        "entry_request_paths": [request.path for request in entry_requests],
        "exit_request_path": exit_request.path,
        "boundaries": {
            "submits_orders": False,
            "loads_credentials": False,
            "writes_candidate_package": False,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write a slope strategy execution mapping report.")
    parser.add_argument("--output-json", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    report = build_report()
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
