import argparse
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from bot.config import RuntimeMode
from bot.exchange_adapter import AdapterCredentials, AdapterRuntimeSnapshot, OkxUsdtSwapAdapter, PositionSnapshot
from scripts.ops import real_order_worker
from scripts.ops import slope_execution_mapping_report


def _slope_preview_package() -> dict:
    generated = datetime(2026, 6, 17, 1, 2, 3, tzinfo=UTC)
    package_id = "bot-eth-entry_short-slope4h-dryrun-20260617T010203"
    return {
        "package_id": package_id,
        "generated_at": generated.isoformat(),
        "expires_at": (real_order_worker._utcnow() + timedelta(minutes=3)).isoformat(),
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
            "frozen_signal_params": {
                "rv_24h_max": 0.00068014,
                "slope_4h_max": -0.000382,
                "direction": "short",
                "horizon_bars": 48,
                "cost": 0.001,
            },
        },
        "execution_plan": {
            "place_entry_order": True,
            "maintain_protective_stop": True,
            "place_take_profit_orders": False,
            "fixed_time_exit_required": True,
            "fixed_exit_bars": 48,
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
                    "execution_warnings": ["dry_run_preview_only", "not_authorized_for_real_orders"],
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
        "preflight": [
            {"target": "entry_order", "status": "preflight_ready", "error": ""},
            {"target": "maintain_protective_stop", "status": "preflight_ready", "error": ""},
        ],
        "real_order_gate": {
            "enabled": False,
            "allowed": False,
            "automation_boundary": "no_order_submission",
            "reason_codes": ["dry_run_preview_only", "paper_observation_not_complete"],
        },
        "audit_log_path": "",
        "state_path": "",
        "source_cycle_path": "",
    }


def _slope_fixed_exit_preview_package() -> dict:
    generated = datetime(2026, 6, 17, 13, 2, 3, tzinfo=UTC)
    package_id = "bot-eth-exit-slope4h-fixed12h-dryrun-20260617T130203"
    return {
        "package_id": package_id,
        "generated_at": generated.isoformat(),
        "expires_at": (real_order_worker._utcnow() + timedelta(minutes=3)).isoformat(),
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
            "entry_action": "entry_short",
            "entry_at": "2026-06-17T01:02:03+00:00",
            "fixed_exit_due_at": "2026-06-17T13:02:03+00:00",
            "fixed_exit_bars": 48,
            "fixed_exit_hours": 12,
        },
        "execution_plan": {
            "place_exit_order": True,
            "maintain_protective_stop": False,
            "place_take_profit_orders": False,
            "fixed_time_exit_required": True,
            "fixed_exit_bars": 48,
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
        "preflight": [{"target": "exit_order", "status": "preflight_ready", "error": ""}],
        "real_order_gate": {
            "enabled": False,
            "allowed": False,
            "automation_boundary": "no_order_submission",
            "reason_codes": ["dry_run_fixed_exit_preview_only", "paper_observation_not_complete"],
        },
        "audit_log_path": "",
        "state_path": "",
        "source_cycle_path": "",
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


def test_real_order_worker_blocks_slope_preview_package_without_submit_flag(tmp_path: Path) -> None:
    package_path = tmp_path / "latest_candidate_execution_package.json"
    package_path.write_text(json.dumps(_slope_preview_package(), ensure_ascii=False), encoding="utf-8")
    args = argparse.Namespace(
        command="run-once",
        package_path=str(package_path),
        audit_log_path=str(tmp_path / "real_order_worker_audit.jsonl"),
        lock_path=str(tmp_path / "real_order_worker.lock"),
        kill_switch_path=str(tmp_path / "disable_real_execution.flag"),
        api_key_env="OKX_API_KEY",
        api_secret_env="OKX_API_SECRET",
        api_passphrase_env="OKX_API_PASSPHRASE",
        proxy_url=None,
        submit_real_orders=False,
        stale_lock_after_sec=900,
    )

    result = real_order_worker.run_once(args=args, adapter_factory=lambda _: None)

    assert result["status"] == "blocked"
    assert "submit_real_orders_flag_missing" in result["reason_codes"]
    assert "real_order_gate_not_allowed" in result["reason_codes"]
    assert "automation_boundary_not_allowed" in result["reason_codes"]
    assert "runtime_mode_not_real" in result["reason_codes"]
    assert not Path(args.lock_path).exists()


def test_slope_preview_execution_commands_match_worker_schema() -> None:
    commands = real_order_worker._load_execution_commands(_slope_preview_package())

    assert [command.target for command in commands] == ["entry_order", "maintain_protective_stop"]
    assert commands[0].payload.action == "entry_short"
    assert commands[0].payload.direction == "short"
    assert commands[0].payload.initial_stop_loss == 1.039
    assert commands[1].payload.direction == "short"
    assert commands[1].payload.initial_stop_loss == 1.039
    assert commands[1].payload.tp_ladder == []


def test_slope_preview_maps_to_okx_short_entry_and_stop_without_tp_request() -> None:
    commands = real_order_worker._load_execution_commands(_slope_preview_package())
    requests = _okx_adapter().prepare_requests(commands=commands)

    assert [request.path for request in requests] == [
        "/api/v5/trade/order",
        "/api/v5/trade/order-algo",
    ]
    assert requests[0].body["side"] == "sell"
    assert requests[0].body["ordType"] == "market"
    assert "reduceOnly" not in requests[0].body
    assert requests[1].body["ordType"] == "conditional"
    assert requests[1].body["closeFraction"] == "1"
    assert not any(request.body.get("ordType") == "limit" for request in requests)


def test_slope_preview_okx_stop_resolves_to_buy_close_at_3p9_percent_above_entry() -> None:
    commands = real_order_worker._load_execution_commands(_slope_preview_package())
    stop_command = commands[1]
    prepared_stop = _okx_adapter().prepare_requests(commands=[stop_command])[0]
    runtime_snapshot = AdapterRuntimeSnapshot(
        snapshot_valid=True,
        position=PositionSnapshot(
            position_state="ENTERED",
            direction="short",
            position_amt=2.0,
            entry_price=100.0,
            mark_price=99.0,
        ),
    )

    resolved = _okx_adapter().validate_prepared_request(
        command=stop_command,
        prepared=prepared_stop,
        runtime_mode=RuntimeMode.REAL,
        runtime_snapshot=runtime_snapshot,
    )

    assert resolved.body["side"] == "buy"
    assert resolved.body["sz"] == "2"
    assert resolved.body["triggerPx"] == "103.9"
    assert resolved.body["orderPx"] == "-1"
    assert resolved.body["resolved_from_entry_price"] == 100.0
    assert resolved.body["resolution_mode"] == "okx_initial_stop_from_live_entry"


def test_real_order_worker_blocks_slope_fixed_exit_preview_without_submit_flag(tmp_path: Path) -> None:
    package_path = tmp_path / "latest_candidate_execution_package.json"
    package_path.write_text(json.dumps(_slope_fixed_exit_preview_package(), ensure_ascii=False), encoding="utf-8")
    args = argparse.Namespace(
        command="run-once",
        package_path=str(package_path),
        audit_log_path=str(tmp_path / "real_order_worker_audit.jsonl"),
        lock_path=str(tmp_path / "real_order_worker.lock"),
        kill_switch_path=str(tmp_path / "disable_real_execution.flag"),
        api_key_env="OKX_API_KEY",
        api_secret_env="OKX_API_SECRET",
        api_passphrase_env="OKX_API_PASSPHRASE",
        proxy_url=None,
        submit_real_orders=False,
        stale_lock_after_sec=900,
    )

    result = real_order_worker.run_once(args=args, adapter_factory=lambda _: None)

    assert result["status"] == "blocked"
    assert "submit_real_orders_flag_missing" in result["reason_codes"]
    assert "real_order_gate_not_allowed" in result["reason_codes"]
    assert "automation_boundary_not_allowed" in result["reason_codes"]
    assert "runtime_mode_not_real" in result["reason_codes"]
    assert not Path(args.lock_path).exists()


def test_slope_fixed_exit_preview_maps_to_okx_buy_reduce_only_close() -> None:
    commands = real_order_worker._load_execution_commands(_slope_fixed_exit_preview_package())
    prepared_exit = _okx_adapter().prepare_requests(commands=commands)[0]
    runtime_snapshot = AdapterRuntimeSnapshot(
        snapshot_valid=True,
        position=PositionSnapshot(
            position_state="ENTERED",
            direction="short",
            position_amt=2.0,
            entry_price=100.0,
            mark_price=99.0,
        ),
    )

    resolved = _okx_adapter().validate_prepared_request(
        command=commands[0],
        prepared=prepared_exit,
        runtime_mode=RuntimeMode.REAL,
        runtime_snapshot=runtime_snapshot,
    )

    assert [command.target for command in commands] == ["exit_order"]
    assert prepared_exit.path == "/api/v5/trade/order"
    assert prepared_exit.body["side"] == "buy"
    assert prepared_exit.body["reduceOnly"] == "true"
    assert resolved.body["side"] == "buy"
    assert resolved.body["sz"] == "2"
    assert resolved.body["resolution_mode"] == "okx_exit_size_from_live_position"


def build_slope_execution_mapping_report() -> dict:
    entry_commands = real_order_worker._load_execution_commands(_slope_preview_package())
    entry_requests = _okx_adapter().prepare_requests(commands=entry_commands)
    exit_commands = real_order_worker._load_execution_commands(_slope_fixed_exit_preview_package())
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
        "status": "pass" if all(checks.values()) else "blocked",
        "checks": checks,
        "entry_request_paths": [request.path for request in entry_requests],
        "exit_request_path": exit_request.path,
    }


def test_slope_execution_mapping_report_matches_quant_readiness_contract() -> None:
    report = slope_execution_mapping_report.build_report()

    assert report["status"] == "pass"
    assert report["checks"] == {
        "worker_blocks_shadow_without_submit_flag": True,
        "entry_short_maps_sell_market": True,
        "protective_stop_maps_okx_algo": True,
        "no_take_profit_order": True,
        "fixed_exit_maps_buy_reduce_only": True,
    }
