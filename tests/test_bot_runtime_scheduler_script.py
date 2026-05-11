from __future__ import annotations

import argparse
import json
import os
import time
import pytest
from pathlib import Path

from bot.audit_logger import AuditLogger
from scripts import bot_runtime_scheduler


def _args(tmp_path: Path) -> argparse.Namespace:
    return argparse.Namespace(
        command="run-once",
        quant_root=str(tmp_path / "quant"),
        runtime_root=str(tmp_path / "runtime"),
        cycle_output_root=str(tmp_path / "cycles"),
        proxy_url="",
        include_okx_overlay=False,
        include_coinglass_overlay=None,
        consensus_request_timeout_sec=10.0,
        research_sync_request_path=None,
        research_dispatch_request_path=str(tmp_path / "quant" / "runtime" / "fresh_research" / "dispatch_request.json"),
        api_key_env=None,
        api_secret_env=None,
        api_passphrase_env=None,
        analysis_db_path=None,
        skip_analysis_ingest=True,
        enable_real_orders=False,
        kill_switch_path=str(tmp_path / "disable_real_execution.flag"),
        interval_sec=1,
        cycles=1,
        max_consecutive_failures=1,
        degraded_heartbeat_interval_sec=1,
    )


def test_bot_runtime_scheduler_run_once_records_shadow_preflight_boundary(tmp_path: Path) -> None:
    captured = {}

    def fake_cycle_runner(*, args, bot_root):
        assert args.api_key_env is None
        assert args.api_secret_env is None
        assert args.api_passphrase_env is None
        assert args.consensus_request_timeout_sec == 10.0
        assert args.research_dispatch_request_path.endswith("dispatch_request.json")
        captured["include_coinglass_overlay"] = args.include_coinglass_overlay
        return {
            "requested_action": "small_probe",
            "effective_action": "small_probe",
            "plan_reason": "effective_action:small_probe",
            "handoff": {
                "direction": "long",
                "execution_allowed": True,
                "position_size_pct": 0.02,
                "initial_stop_loss": 0.98,
                "tp_ladder": [1.01],
                "reduce_conditions": ["crowding_warning"],
                "invalidate_conditions": ["setup_invalidated"],
                "trailing_rule": "trail_with_trigger",
            },
            "execution_plan": {
                "executable_size_pct": 0.02,
                "stop_distance_pct": 0.02,
                "account_risk_pct": 0.001,
            },
            "command_targets": ["entry_order", "maintain_protective_stop"],
            "exchange_venue": "okx_usdt_swap",
            "exchange_symbol": "ETH-USDT-SWAP",
            "runtime_snapshot": {
                "snapshot_valid": True,
                "account_equity": 101.25,
                "account_equity_source": "totalEq",
                "position": {
                    "position_state": "ENTERED",
                    "unrealized_pnl_usd": 2.25,
                    "unrealized_pnl_pct_on_margin": 0.03,
                    "price_vs_entry_pct": 0.004,
                },
            },
            "preflight_statuses": ["preflight_ready"],
            "preflight": [{"target": "entry_order", "status": "preflight_ready", "error": ""}],
            "reason_codes": [],
            "judgement": {"status": "ok"},
            "audit_log_path": str(tmp_path / "audit.jsonl"),
            "state_path": str(tmp_path / "state.json"),
        }

    payload = bot_runtime_scheduler.run_once(
        args=_args(tmp_path),
        bot_root=Path(__file__).resolve().parents[1],
        cycle_runner=fake_cycle_runner,
    )

    runtime_root = tmp_path / "runtime"
    latest = json.loads((runtime_root / "latest_cycle.json").read_text(encoding="utf-8"))
    heartbeat = json.loads((runtime_root / "heartbeat.json").read_text(encoding="utf-8"))
    samples = (runtime_root / "samples.jsonl").read_text(encoding="utf-8").splitlines()

    assert payload["status"] == "ok"
    assert captured["include_coinglass_overlay"] is None
    assert payload["mode"] == "shadow_preflight_only"
    assert payload["automation_boundary"] == "no_order_submission"
    assert payload["candidate_execution_package"]["status"] == "skipped"
    assert payload["real_order_gate"]["enabled"] is False
    assert payload["real_order_gate"]["allowed"] is False
    assert latest["command_targets"] == ["entry_order", "maintain_protective_stop"]
    assert latest["exchange_venue"] == "okx_usdt_swap"
    assert latest["exchange_symbol"] == "ETH-USDT-SWAP"
    assert latest["runtime_snapshot"]["account_equity"] == 101.25
    assert latest["runtime_account_equity"] == 101.25
    assert latest["runtime_account_equity_source"] == "totalEq"
    assert latest["runtime_unrealized_pnl_usd"] == 2.25
    assert latest["runtime_unrealized_pnl_pct"] == 0.03
    assert latest["price_vs_entry_pct"] == 0.004
    assert latest["tp_ladder"] == [1.01]
    assert heartbeat["status"] == "ok"
    assert heartbeat["automation_boundary"] == "no_order_submission"
    assert heartbeat["candidate_execution_package_path"] == ""
    assert len(samples) == 1


def test_bot_runtime_scheduler_write_json_is_atomic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "runtime" / "latest_cycle.json"
    path.parent.mkdir(parents=True)
    path.write_text('{"status": "old"}', encoding="utf-8")

    def fail_replace(_src, _dst):
        raise OSError("replace failed")

    monkeypatch.setattr(bot_runtime_scheduler.os, "replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        bot_runtime_scheduler._write_json(path, {"status": "new"})

    assert json.loads(path.read_text(encoding="utf-8")) == {"status": "old"}
    assert list(path.parent.glob(".*.tmp")) == []


def test_bot_runtime_scheduler_enable_real_orders_still_blocks_shadow_payload(tmp_path: Path) -> None:
    args = _args(tmp_path)
    args.enable_real_orders = True

    payload = bot_runtime_scheduler.run_once(
        args=args,
        bot_root=Path(__file__).resolve().parents[1],
        cycle_runner=lambda **_: {
            "runtime_mode": "shadow",
            "requested_action": "entry_long",
            "effective_action": "entry_long",
            "handoff": {"execution_allowed": True, "risk_filter_status": "pass", "initial_stop_loss": 0.97},
            "execution_plan": {"place_entry_order": True, "maintain_protective_stop": True},
            "preflight": [
                {"target": "entry_order", "status": "preflight_ready", "error": ""},
                {"target": "maintain_protective_stop", "status": "preflight_ready", "error": ""},
            ],
            "audit_log_path": "",
        },
    )

    assert payload["real_order_gate"]["enabled"] is True
    assert payload["real_order_gate"]["allowed"] is False
    assert payload["automation_boundary"] == "real_order_submission_blocked"
    assert "runtime_mode_not_real" in payload["real_order_gate"]["reason_codes"]
    assert payload["candidate_execution_package"]["status"] == "skipped"


def test_bot_runtime_scheduler_skips_candidate_package_when_handoff_execution_not_allowed(tmp_path: Path) -> None:
    args = _args(tmp_path)
    args.enable_real_orders = True

    payload = bot_runtime_scheduler.run_once(
        args=args,
        bot_root=Path(__file__).resolve().parents[1],
        cycle_runner=lambda **_: {
            "runtime_mode": "real",
            "engine_mode": "strict-live",
            "requested_action": "entry_long",
            "effective_action": "entry_long",
            "handoff": {
                "action": "entry_long",
                "direction": "long",
                "execution_allowed": False,
                "execution_block_reason": "runtime_veto",
                "risk_filter_status": "veto",
                "runtime_vetoes": ["research_not_ready"],
                "initial_stop_loss": 0.97,
            },
            "execution_plan": {"place_entry_order": True, "maintain_protective_stop": True},
            "runtime_snapshot": {"snapshot_valid": True, "position": {"position_state": "FLAT"}},
            "preflight": [
                {"target": "entry_order", "status": "preflight_ready", "error": ""},
                {"target": "maintain_protective_stop", "status": "preflight_ready", "error": ""},
            ],
            "audit_log_path": "",
        },
    )

    assert payload["real_order_gate"]["enabled"] is True
    assert payload["real_order_gate"]["allowed"] is False
    assert payload["automation_boundary"] == "real_order_submission_blocked"
    assert "execution_not_allowed" in payload["real_order_gate"]["reason_codes"]
    assert "risk_filter_not_pass" in payload["real_order_gate"]["reason_codes"]
    assert payload["candidate_execution_package"] == {
        "status": "skipped",
        "reason": "candidate_execution_package_not_allowed",
    }
    assert not (Path(args.runtime_root) / "latest_candidate_execution_package.json").exists()


def test_bot_runtime_scheduler_kill_switch_blocks_real_order_gate(tmp_path: Path) -> None:
    args = _args(tmp_path)
    args.enable_real_orders = True
    kill_switch_path = tmp_path / "disable_real_execution.flag"
    kill_switch_path.write_text("1", encoding="utf-8")

    payload = bot_runtime_scheduler.run_once(
        args=args,
        bot_root=Path(__file__).resolve().parents[1],
        cycle_runner=lambda **_: {
            "runtime_mode": "real",
            "engine_mode": "strict-live",
            "requested_action": "entry_long",
            "effective_action": "entry_long",
            "audit_log_path": "",
        },
    )

    assert payload["real_order_gate"]["allowed"] is False
    assert payload["real_order_gate"]["reason_codes"] == ["kill_switch_enabled"]
    assert payload["candidate_execution_package"]["status"] == "skipped"


def test_bot_runtime_scheduler_writes_candidate_execution_package_when_gate_allows(tmp_path: Path) -> None:
    args = _args(tmp_path)
    args.enable_real_orders = True

    payload = bot_runtime_scheduler.run_once(
        args=args,
        bot_root=Path(__file__).resolve().parents[1],
        cycle_runner=lambda **_: {
            "runtime_mode": "real",
            "engine_mode": "strict-live",
            "symbol": "ETH",
            "exchange_symbol": "ETH-USDT-SWAP",
            "requested_action": "entry_long",
            "effective_action": "entry_long",
            "handoff": {
                "action": "entry_long",
                "direction": "long",
                "execution_allowed": True,
                "risk_filter_status": "pass",
                "initial_stop_loss": 0.97,
                "tp_ladder": [],
            },
            "execution_plan": {"place_entry_order": True, "maintain_protective_stop": True},
            "execution_commands": [
                {
                    "command_type": "order",
                    "operation": "place",
                    "target": "entry_order",
                    "idempotency_key": "entry:key",
                    "reason": "effective_action:entry_long",
                    "payload": {"action": "entry_long", "direction": "long", "position_size_pct": 0.02},
                }
            ],
            "preflight": [
                {"target": "entry_order", "status": "preflight_ready", "error": ""},
                {"target": "maintain_protective_stop", "status": "preflight_ready", "error": ""},
            ],
            "audit_log_path": str(tmp_path / "audit.jsonl"),
            "state_path": str(tmp_path / "state.json"),
            "runtime_snapshot": {"snapshot_valid": True, "position": {"position_state": "FLAT"}},
        },
    )

    package_summary = payload["candidate_execution_package"]
    latest_path = Path(package_summary["latest_path"])
    package = json.loads(latest_path.read_text(encoding="utf-8"))
    generated_at = bot_runtime_scheduler.datetime.fromisoformat(package["generated_at"])
    expires_at = bot_runtime_scheduler.datetime.fromisoformat(package["expires_at"])

    assert package_summary["status"] == "written"
    assert latest_path.exists()
    assert package["action"] == "entry_long"
    assert package["direction"] == "long"
    assert package["real_order_gate"]["allowed"] is True
    assert package["execution_commands"][0]["target"] == "entry_order"
    assert (expires_at - generated_at).total_seconds() == 180


def test_bot_runtime_scheduler_blocks_candidate_when_runtime_snapshot_missing(tmp_path: Path) -> None:
    args = _args(tmp_path)
    args.enable_real_orders = True

    payload = bot_runtime_scheduler.run_once(
        args=args,
        bot_root=Path(__file__).resolve().parents[1],
        cycle_runner=lambda **_: {
            "runtime_mode": "real",
            "engine_mode": "strict-live",
            "requested_action": "entry_long",
            "effective_action": "entry_long",
            "handoff": {
                "execution_allowed": True,
                "risk_filter_status": "pass",
                "direction": "long",
                "initial_stop_loss": 0.97,
            },
            "execution_plan": {"place_entry_order": True, "maintain_protective_stop": True},
            "execution_commands": [
                {"target": "entry_order", "idempotency_key": "entry:key", "payload": {"direction": "long"}},
                {"target": "maintain_protective_stop", "idempotency_key": "stop:key", "payload": {"direction": "long"}},
            ],
            "preflight": [
                {"target": "entry_order", "status": "preflight_ready", "error": ""},
                {"target": "maintain_protective_stop", "status": "preflight_ready", "error": ""},
            ],
        },
    )

    assert payload["real_order_gate"]["allowed"] is False
    assert "runtime_snapshot_invalid" in payload["real_order_gate"]["reason_codes"]
    assert payload["candidate_execution_package"]["status"] == "skipped"
    assert not (Path(args.runtime_root) / "latest_candidate_execution_package.json").exists()


def test_bot_runtime_scheduler_writes_repair_candidate_package_when_gate_allows(tmp_path: Path) -> None:
    args = _args(tmp_path)
    args.enable_real_orders = True

    payload = bot_runtime_scheduler.run_once(
        args=args,
        bot_root=Path(__file__).resolve().parents[1],
        cycle_runner=lambda **_: {
            "runtime_mode": "real",
            "engine_mode": "strict-live",
            "requested_action": "protective_stop_repair",
            "effective_action": "protective_stop_repair",
            "handoff": {"action": "protective_stop_repair", "direction": "long", "initial_stop_loss": 0.97},
            "execution_plan": {"maintain_protective_stop": True},
            "execution_commands": [
                {
                    "command_type": "order",
                    "operation": "upsert",
                    "target": "maintain_protective_stop",
                    "idempotency_key": "repair:key",
                    "reason": "protective_stop_required",
                    "payload": {"direction": "long", "initial_stop_loss": 0.97, "tp_ladder": []},
                }
            ],
            "preflight": [
                {"target": "maintain_protective_stop", "status": "preflight_ready", "error": ""},
            ],
            "runtime_snapshot": {
                "snapshot_valid": True,
                "protective_stop_present": False,
                "position": {"position_state": "ENTERED", "direction": "long"},
            },
            "audit_log_path": str(tmp_path / "audit.jsonl"),
            "state_path": str(tmp_path / "state.json"),
        },
    )

    package = json.loads(Path(payload["candidate_execution_package"]["latest_path"]).read_text(encoding="utf-8"))

    assert payload["candidate_execution_package"]["status"] == "written"
    assert package["action"] == "protective_stop_repair"
    assert package["execution_commands"][0]["target"] == "maintain_protective_stop"


def test_bot_runtime_scheduler_blocks_candidate_when_tp_ladder_has_no_tp_order(tmp_path: Path) -> None:
    args = _args(tmp_path)
    args.enable_real_orders = True

    payload = bot_runtime_scheduler.run_once(
        args=args,
        bot_root=Path(__file__).resolve().parents[1],
        cycle_runner=lambda **_: {
            "runtime_mode": "real",
            "engine_mode": "strict-live",
            "symbol": "ETH",
            "exchange_symbol": "ETH-USDT-SWAP",
            "requested_action": "entry_long",
            "effective_action": "entry_long",
            "handoff": {
                "action": "entry_long",
                "direction": "long",
                "execution_allowed": True,
                "risk_filter_status": "pass",
                "initial_stop_loss": 0.97,
                "tp_ladder": [1.01, 1.02],
            },
            "execution_plan": {"place_entry_order": True, "maintain_protective_stop": True},
            "command_targets": ["entry_order", "maintain_protective_stop"],
            "execution_commands": [
                {
                    "command_type": "order",
                    "operation": "place",
                    "target": "entry_order",
                    "idempotency_key": "entry:key",
                    "reason": "effective_action:entry_long",
                    "payload": {"action": "entry_long", "direction": "long", "position_size_pct": 0.02},
                },
                {
                    "command_type": "order",
                    "operation": "upsert",
                    "target": "maintain_protective_stop",
                    "idempotency_key": "stop:key",
                    "reason": "protective_stop_required",
                    "payload": {"direction": "long", "initial_stop_loss": 0.97, "tp_ladder": [1.01, 1.02]},
                },
            ],
            "preflight": [
                {"target": "entry_order", "status": "preflight_ready", "error": ""},
                {"target": "maintain_protective_stop", "status": "preflight_ready", "error": ""},
            ],
            "audit_log_path": str(tmp_path / "audit.jsonl"),
            "state_path": str(tmp_path / "state.json"),
            "runtime_snapshot": {"snapshot_valid": True, "position": {"position_state": "FLAT"}},
        },
    )

    assert payload["real_order_gate"]["allowed"] is False
    assert "take_profit_orders_not_planned" in payload["real_order_gate"]["reason_codes"]
    assert payload["candidate_execution_package"]["status"] == "skipped"


def test_bot_runtime_scheduler_writes_candidate_when_take_profit_order_is_planned(tmp_path: Path) -> None:
    args = _args(tmp_path)
    args.enable_real_orders = True

    payload = bot_runtime_scheduler.run_once(
        args=args,
        bot_root=Path(__file__).resolve().parents[1],
        cycle_runner=lambda **_: {
            "runtime_mode": "real",
            "engine_mode": "strict-live",
            "symbol": "ETH",
            "exchange_symbol": "ETH-USDT-SWAP",
            "requested_action": "entry_long",
            "effective_action": "entry_long",
            "handoff": {
                "action": "entry_long",
                "direction": "long",
                "execution_allowed": True,
                "risk_filter_status": "pass",
                "initial_stop_loss": 0.97,
                "tp_ladder": [1.01],
                "tp_reduce_fractions": [0.5],
            },
            "execution_plan": {"place_entry_order": True, "maintain_protective_stop": True, "place_take_profit_orders": True},
            "command_targets": ["entry_order", "maintain_protective_stop", "take_profit_order"],
            "execution_commands": [
                {
                    "command_type": "order",
                    "operation": "place",
                    "target": "entry_order",
                    "idempotency_key": "entry:key",
                    "reason": "effective_action:entry_long",
                    "payload": {"action": "entry_long", "direction": "long", "position_size_pct": 0.02},
                },
                {
                    "command_type": "order",
                    "operation": "upsert",
                    "target": "maintain_protective_stop",
                    "idempotency_key": "stop:key",
                    "reason": "protective_stop_required",
                    "payload": {"direction": "long", "initial_stop_loss": 0.97, "tp_ladder": [1.01]},
                },
                {
                    "command_type": "order",
                    "operation": "place",
                    "target": "take_profit_order",
                    "idempotency_key": "tp:key",
                    "reason": "take_profit_level:1",
                    "payload": {"direction": "long", "price_ratio": 1.01, "reduce_fraction": 0.5, "level": 1},
                },
            ],
            "preflight": [
                {"target": "entry_order", "status": "preflight_ready", "error": ""},
                {"target": "maintain_protective_stop", "status": "preflight_ready", "error": ""},
                {"target": "take_profit_order", "status": "preflight_ready", "error": ""},
            ],
            "audit_log_path": str(tmp_path / "audit.jsonl"),
            "state_path": str(tmp_path / "state.json"),
            "runtime_snapshot": {"snapshot_valid": True, "position": {"position_state": "FLAT"}},
        },
    )

    package = json.loads(Path(payload["candidate_execution_package"]["latest_path"]).read_text(encoding="utf-8"))

    assert payload["real_order_gate"]["allowed"] is True
    assert package["execution_commands"][-1]["target"] == "take_profit_order"
    assert package["execution_commands"][-1]["payload"]["reduce_fraction"] == 0.5


def test_bot_runtime_scheduler_records_error_and_degraded_heartbeat(tmp_path: Path, monkeypatch) -> None:
    def failing_cycle_runner(*, args, bot_root):
        raise RuntimeError("quant strict-live unavailable")

    sleeps: list[int] = []
    monkeypatch.setattr(bot_runtime_scheduler.time, "sleep", lambda seconds: sleeps.append(seconds))

    args = _args(tmp_path)
    args.command = "loop"
    args.cycles = 1
    exit_code = bot_runtime_scheduler.run_loop(
        args=args,
        bot_root=Path(__file__).resolve().parents[1],
        cycle_runner=failing_cycle_runner,
    )

    runtime_root = tmp_path / "runtime"
    latest = json.loads((runtime_root / "latest_cycle.json").read_text(encoding="utf-8"))
    heartbeat = json.loads((runtime_root / "heartbeat.json").read_text(encoding="utf-8"))

    assert exit_code == 0
    assert latest["status"] == "error"
    assert "quant strict-live unavailable" in latest["error"]
    assert latest["automation_boundary"] == "no_order_submission"
    assert heartbeat["status"] == "degraded"
    assert heartbeat["consecutive_failures"] == 1
    assert sleeps == []


def test_bot_runtime_scheduler_lock_blocks_duplicate_loop(tmp_path: Path) -> None:
    args = _args(tmp_path)
    args.command = "loop"
    lock_path = Path(args.runtime_root) / "scheduler.lock"
    lock_path.parent.mkdir(parents=True)
    lock_path.write_text("existing", encoding="utf-8")

    with pytest.raises(RuntimeError, match="already running"):
        bot_runtime_scheduler.run_loop(
            args=args,
            bot_root=Path(__file__).resolve().parents[1],
            cycle_runner=lambda **_: {},
        )


def test_bot_runtime_scheduler_clears_stale_lock(tmp_path: Path) -> None:
    args = _args(tmp_path)
    args.command = "loop"
    lock_path = Path(args.runtime_root) / "scheduler.lock"
    lock_path.parent.mkdir(parents=True)
    lock_path.write_text("stale", encoding="utf-8")
    old_time = time.time() - 3600
    os.utime(lock_path, (old_time, old_time))

    exit_code = bot_runtime_scheduler.run_loop(
        args=args,
        bot_root=Path(__file__).resolve().parents[1],
        cycle_runner=lambda **_: {"audit_log_path": ""},
    )

    assert exit_code == 0
    assert not lock_path.exists()


def test_bot_runtime_scheduler_clears_dead_pid_lock_even_when_fresh(tmp_path: Path, monkeypatch) -> None:
    args = _args(tmp_path)
    args.command = "loop"
    lock_path = Path(args.runtime_root) / "scheduler.lock"
    lock_path.parent.mkdir(parents=True)
    lock_path.write_text(json.dumps({"pid": 424242, "created_at": "2026-05-06T18:12:03"}), encoding="utf-8")
    monkeypatch.setattr(bot_runtime_scheduler, "_process_exists", lambda pid: False)

    exit_code = bot_runtime_scheduler.run_loop(
        args=args,
        bot_root=Path(__file__).resolve().parents[1],
        cycle_runner=lambda **_: {"audit_log_path": ""},
    )

    assert exit_code == 0
    assert not lock_path.exists()


def test_bot_runtime_scheduler_console_output_is_ascii_safe_for_windows_redirect() -> None:
    script = Path(bot_runtime_scheduler.__file__).read_text(encoding="utf-8")

    assert "print(json.dumps(payload, ensure_ascii=True, indent=2))" in script
    assert "print(json.dumps(payload, ensure_ascii=False, indent=2))" not in script


def test_bot_runtime_scheduler_releases_lock_after_loop(tmp_path: Path) -> None:
    args = _args(tmp_path)
    args.command = "loop"
    args.cycles = 1

    exit_code = bot_runtime_scheduler.run_loop(
        args=args,
        bot_root=Path(__file__).resolve().parents[1],
        cycle_runner=lambda **_: {"audit_log_path": ""},
    )

    assert exit_code == 0
    assert not (Path(args.runtime_root) / "scheduler.lock").exists()


def test_bot_runtime_scheduler_ingests_audit_log_when_enabled(tmp_path: Path) -> None:
    pytest.importorskip("duckdb")
    audit_path = tmp_path / "audit.jsonl"
    _write_audit_cycle(audit_path)
    args = _args(tmp_path)
    args.skip_analysis_ingest = False
    args.analysis_db_path = str(tmp_path / "analysis.duckdb")

    payload = bot_runtime_scheduler.run_once(
        args=args,
        bot_root=Path(__file__).resolve().parents[1],
        cycle_runner=lambda **_: {"audit_log_path": str(audit_path)},
    )

    summary_path = Path(args.runtime_root) / "analysis" / "bot_runtime_summary.json"
    assert payload["analysis_ingest"]["status"] == "ok"
    assert payload["analysis_ingest"]["cycles_upserted"] == 1
    assert json.loads(summary_path.read_text(encoding="utf-8"))["total_cycles"] == 1


def _write_audit_cycle(path: Path) -> None:
    AuditLogger(path).append(
        event_type="shadow_cycle",
        payload={
            "runtime_mode": "shadow",
            "engine_mode": "strict-live",
            "handoff": {"action": "wait"},
            "effective_action": "wait",
            "action_summary": {"blocked": False, "degraded": False},
            "state": {"execution_state": "idle", "automation_state": "observing"},
            "automation_state": "observing",
            "runtime_snapshot_before": {"position": {"position_state": "FLAT", "direction": "neutral", "size_pct": 0.0}},
            "runtime_snapshot_after": {"position": {"position_state": "FLAT", "direction": "neutral", "size_pct": 0.0}},
            "execution_commands": [],
            "execution_results": [],
        },
    )
