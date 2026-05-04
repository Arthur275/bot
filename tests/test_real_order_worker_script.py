from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from bot.config import RuntimeMode
from bot.exchange_adapter import AdapterRuntimeSnapshot, CommandExecutionResult, ExecutionCommand, PositionSnapshot
from bot.state_store import ExecutionLayerState, StateStore
from scripts import real_order_worker


class FakeRealOrderAdapter:
    def __init__(
        self,
        *,
        position_state: str = "FLAT",
        direction: str = "neutral",
        protective_stop_present: bool = False,
        stop_failures_before_success: int = 0,
        open_algo_orders: list[dict[str, object]] | None = None,
        cancel_raises: bool = False,
    ) -> None:
        self.snapshots_fetched = 0
        self.executed_commands: list[ExecutionCommand] = []
        self.canceled_algo_orders: list[dict[str, object]] = []
        self._stop_failures_before_success = stop_failures_before_success
        self._open_algo_orders = list(open_algo_orders or [])
        self._cancel_raises = cancel_raises
        self._snapshot = AdapterRuntimeSnapshot(
            fetched_at=datetime(2026, 5, 4, 1, 0, 0),
            position=PositionSnapshot(position_state=position_state, direction=direction, size_pct=0.0),
            protective_stop_present=protective_stop_present,
            snapshot_valid=True,
        )

    def fetch_runtime_snapshot(self) -> AdapterRuntimeSnapshot:
        self.snapshots_fetched += 1
        return self._snapshot

    def execute_commands(
        self,
        *,
        commands: list[ExecutionCommand],
        runtime_mode: RuntimeMode,
    ) -> list[CommandExecutionResult]:
        assert runtime_mode == RuntimeMode.REAL
        self.executed_commands.extend(commands)
        if any(command.target == "maintain_protective_stop" for command in commands) and self._stop_failures_before_success > 0:
            self._stop_failures_before_success -= 1
            return [
                CommandExecutionResult(
                    target=command.target,
                    status="error",
                    accepted=False,
                    simulated=False,
                    reason="protective_stop_required",
                    idempotency_key=command.idempotency_key,
                    error_kind="transport_error",
                )
                for command in commands
            ]
        return [
            CommandExecutionResult(
                target=command.target,
                status="accepted",
                accepted=True,
                simulated=False,
                reason=command.reason,
                idempotency_key=command.idempotency_key,
                client_order_id=f"client-{command.target}",
                exchange_order_id=f"exchange-{command.target}",
            )
            for command in commands
        ]

    def fetch_open_algo_orders_raw(self) -> list[dict[str, object]]:
        return list(self._open_algo_orders)

    def cancel_algo_order_raw(self, *, algo_id: str = "", client_algo_id: str = "") -> dict[str, object]:
        if self._cancel_raises:
            raise RuntimeError("cancel failed")
        payload = {"algoId": algo_id, "clientAlgoId": client_algo_id, "status": "canceled"}
        self.canceled_algo_orders.append(payload)
        self._open_algo_orders = [
            order
            for order in self._open_algo_orders
            if str(order.get("algoId") or "") != algo_id and str(order.get("clientAlgoId") or "") != client_algo_id
        ]
        if not self._open_algo_orders:
            self._snapshot.protective_stop_present = False
        return payload


class InvalidSnapshotAdapter(FakeRealOrderAdapter):
    def __init__(self) -> None:
        super().__init__()
        self._snapshot = AdapterRuntimeSnapshot(
            snapshot_valid=False,
            error_endpoint="/fapi/v2/positionRisk",
            error_kind="timeout",
            error_message="timeout",
        )


def _args(tmp_path: Path) -> argparse.Namespace:
    return argparse.Namespace(
        command="run-once",
        package_path=str(tmp_path / "latest_candidate_execution_package.json"),
        audit_log_path=str(tmp_path / "real_order_audit.jsonl"),
        lock_path=str(tmp_path / "real_order_worker.lock"),
        kill_switch_path=str(tmp_path / "disable_real_execution.flag"),
        api_key_env="BINANCE_API_KEY",
        api_secret_env="BINANCE_API_SECRET",
        proxy_url=None,
        submit_real_orders=False,
        stale_lock_after_sec=900,
    )


def _write_package(path: Path, *, expires_at: datetime | None = None, action: str = "entry_long") -> dict[str, object]:
    command_target = (
        "entry_order"
        if action in {"entry_long", "entry_short", "small_probe"}
        else "maintain_protective_stop"
        if action in {"protect", "protective_stop_repair", "maintain_protective_stop"}
        else f"{action}_order"
    )
    package = {
        "package_id": "bot-eth-entry_long-20260504T010000",
        "generated_at": "2026-05-04T01:00:00",
        "expires_at": (expires_at or (real_order_worker._utcnow() + timedelta(minutes=3))).isoformat(),
        "runtime_mode": "real",
        "engine_mode": "strict-live",
        "symbol": "ETHUSDT",
        "exchange_symbol": "ETHUSDT",
        "action": action,
        "direction": "long",
        "handoff": {"action": "entry_long", "direction": "long"},
        "execution_plan": {"place_entry_order": True, "maintain_protective_stop": True},
        "execution_commands": [
            {
                "command_type": "order",
                "operation": "place",
                "target": command_target,
                "idempotency_key": f"{action}:key",
                "reason": f"effective_action:{action}",
                "payload": {
                    "direction": "long",
                    "initial_stop_loss": 0.97,
                    **({"action": action, "position_size_pct": 0.02, "execution_warnings": []} if command_target == "entry_order" else {}),
                    **({"tp_ladder": []} if command_target == "maintain_protective_stop" else {}),
                },
            }
        ],
        "preflight": [{"target": command_target, "status": "preflight_ready", "error": ""}],
        "real_order_gate": {
            "enabled": True,
            "allowed": True,
            "automation_boundary": "real_order_submission_allowed",
            "reason_codes": [],
        },
        "audit_log_path": str(path.parent / "shadow_audit.jsonl"),
        "state_path": str(path.parent / "state.json"),
        "source_cycle_path": str(path.parent / "latest_cycle.json"),
    }
    path.write_text(json.dumps(package, ensure_ascii=False), encoding="utf-8")
    return package


def test_real_order_worker_blocks_without_submit_flag(tmp_path: Path) -> None:
    args = _args(tmp_path)
    _write_package(Path(args.package_path))

    result = real_order_worker.run_once(args=args, adapter_factory=lambda _: FakeRealOrderAdapter())

    assert result["status"] == "blocked"
    assert "submit_real_orders_flag_missing" in result["reason_codes"]
    assert not Path(args.lock_path).exists()


def test_real_order_worker_blocks_expired_package(tmp_path: Path) -> None:
    args = _args(tmp_path)
    args.submit_real_orders = True
    _write_package(Path(args.package_path), expires_at=real_order_worker._utcnow() - timedelta(seconds=1))

    result = real_order_worker.run_once(args=args, adapter_factory=lambda _: FakeRealOrderAdapter())

    assert result["status"] == "blocked"
    assert "execution_package_expired" in result["reason_codes"]


def test_real_order_worker_kill_switch_blocks_before_submit(tmp_path: Path) -> None:
    args = _args(tmp_path)
    args.submit_real_orders = True
    _write_package(Path(args.package_path))
    Path(args.kill_switch_path).write_text("1", encoding="utf-8")

    result = real_order_worker.run_once(args=args, adapter_factory=lambda _: FakeRealOrderAdapter())

    assert result["status"] == "blocked"
    assert "kill_switch_enabled" in result["reason_codes"]


def test_real_order_worker_writes_pending_before_submit_and_result(tmp_path: Path) -> None:
    args = _args(tmp_path)
    args.submit_real_orders = True
    _write_package(Path(args.package_path))
    adapter = FakeRealOrderAdapter()

    result = real_order_worker.run_once(args=args, adapter_factory=lambda _: adapter)
    events = [
        json.loads(line)
        for line in Path(args.audit_log_path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    assert result["status"] == "submitted"
    assert [event["event_type"] for event in events] == [
        "real_order_worker_command_pending",
        "real_order_worker_command_result",
    ]
    assert events[0]["payload"]["commands"][0]["idempotency_key"] == "entry_long:key"
    assert events[1]["payload"]["results"][0]["idempotency_key"] == "entry_long:key"
    assert adapter.snapshots_fetched == 3
    assert [command.target for command in adapter.executed_commands] == ["entry_order"]
    assert not Path(args.lock_path).exists()


def test_real_order_worker_retries_protective_stop_after_entry(tmp_path: Path) -> None:
    args = _args(tmp_path)
    args.submit_real_orders = True
    package = _write_package(Path(args.package_path))
    package["execution_commands"].append(
        {
            "command_type": "order",
            "operation": "upsert",
            "target": "maintain_protective_stop",
            "idempotency_key": "stop:key",
            "reason": "protective_stop_required",
            "payload": {"direction": "long", "initial_stop_loss": 0.97, "tp_ladder": []},
        }
    )
    package["preflight"].append({"target": "maintain_protective_stop", "status": "preflight_ready", "error": ""})
    Path(args.package_path).write_text(json.dumps(package, ensure_ascii=False), encoding="utf-8")
    adapter = FakeRealOrderAdapter(stop_failures_before_success=2)

    result = real_order_worker.run_once(args=args, adapter_factory=lambda _: adapter)

    assert result["status"] == "submitted"
    assert [command.target for command in adapter.executed_commands] == [
        "entry_order",
        "maintain_protective_stop",
        "maintain_protective_stop",
        "maintain_protective_stop",
    ]


def test_real_order_worker_cancels_ghost_stop_before_entry(tmp_path: Path) -> None:
    args = _args(tmp_path)
    args.submit_real_orders = True
    _write_package(Path(args.package_path))
    adapter = FakeRealOrderAdapter(
        protective_stop_present=True,
        open_algo_orders=[{"algoId": "123", "clientAlgoId": "ghost-stop"}],
    )

    result = real_order_worker.run_once(args=args, adapter_factory=lambda _: adapter)

    assert result["status"] == "submitted"
    assert adapter.canceled_algo_orders == [{"algoId": "123", "clientAlgoId": "ghost-stop", "status": "canceled"}]


def test_real_order_worker_blocks_entry_when_ghost_stop_cancel_fails(tmp_path: Path) -> None:
    args = _args(tmp_path)
    args.submit_real_orders = True
    _write_package(Path(args.package_path))
    adapter = FakeRealOrderAdapter(
        protective_stop_present=True,
        open_algo_orders=[{"algoId": "123", "clientAlgoId": "ghost-stop"}],
        cancel_raises=True,
    )

    result = real_order_worker.run_once(args=args, adapter_factory=lambda _: adapter)

    assert result["status"] == "blocked"
    assert "ghost_stop_cancel_failed" in result["reason_codes"]
    assert adapter.executed_commands == []


def test_real_order_worker_cleans_algo_orders_after_exit(tmp_path: Path) -> None:
    args = _args(tmp_path)
    args.submit_real_orders = True
    _write_package(Path(args.package_path), action="exit")
    adapter = FakeRealOrderAdapter(
        position_state="ENTERED",
        direction="long",
        protective_stop_present=True,
        open_algo_orders=[{"algoId": "456", "clientAlgoId": "exit-cleanup"}],
    )

    result = real_order_worker.run_once(args=args, adapter_factory=lambda _: adapter)

    assert result["status"] == "submitted"
    assert adapter.canceled_algo_orders == [{"algoId": "456", "clientAlgoId": "exit-cleanup", "status": "canceled"}]


def test_real_order_worker_refreshes_stop_after_reduce_before_cleanup(tmp_path: Path) -> None:
    args = _args(tmp_path)
    args.submit_real_orders = True
    package = _write_package(Path(args.package_path), action="reduce")
    package["execution_commands"].append(
        {
            "command_type": "order",
            "operation": "upsert",
            "target": "maintain_protective_stop",
            "idempotency_key": "reduce-stop:key",
            "reason": "protective_stop_required",
            "payload": {"direction": "long", "initial_stop_loss": 0.97, "tp_ladder": []},
        }
    )
    package["preflight"].append({"target": "maintain_protective_stop", "status": "preflight_ready", "error": ""})
    Path(args.package_path).write_text(json.dumps(package, ensure_ascii=False), encoding="utf-8")
    adapter = FakeRealOrderAdapter(
        position_state="ENTERED",
        direction="long",
        protective_stop_present=True,
        open_algo_orders=[{"algoId": "789", "clientAlgoId": "old-stop"}],
    )

    result = real_order_worker.run_once(args=args, adapter_factory=lambda _: adapter)

    assert result["status"] == "submitted"
    assert [command.target for command in adapter.executed_commands] == [
        "reduce_order",
        "maintain_protective_stop",
    ]
    assert adapter.canceled_algo_orders == [{"algoId": "789", "clientAlgoId": "old-stop", "status": "canceled"}]


def test_real_order_worker_repairs_missing_protective_stop_with_retry(tmp_path: Path) -> None:
    args = _args(tmp_path)
    args.submit_real_orders = True
    _write_package(Path(args.package_path), action="protective_stop_repair")
    adapter = FakeRealOrderAdapter(
        position_state="ENTERED",
        direction="long",
        protective_stop_present=False,
        stop_failures_before_success=1,
        open_algo_orders=[{"algoId": "repair-stop", "clientAlgoId": "repair-stop"}],
    )

    result = real_order_worker.run_once(args=args, adapter_factory=lambda _: adapter)

    assert result["status"] == "submitted"
    assert [command.target for command in adapter.executed_commands] == [
        "maintain_protective_stop",
        "maintain_protective_stop",
    ]


def test_real_order_worker_requires_open_algo_confirmation_after_stop_place(tmp_path: Path) -> None:
    args = _args(tmp_path)
    args.submit_real_orders = True
    _write_package(Path(args.package_path), action="protective_stop_repair")
    adapter = FakeRealOrderAdapter(
        position_state="ENTERED",
        direction="long",
        protective_stop_present=False,
        open_algo_orders=[],
    )

    result = real_order_worker.run_once(args=args, adapter_factory=lambda _: adapter)
    state = StateStore(tmp_path / "state.json").load()

    assert result["status"] == "submitted"
    assert len(adapter.executed_commands) == 3
    assert state.execution_state is ExecutionLayerState.RECONCILING
    assert state.protective_stop_required is True


def test_real_order_worker_blocks_repair_when_stop_already_present(tmp_path: Path) -> None:
    args = _args(tmp_path)
    args.submit_real_orders = True
    _write_package(Path(args.package_path), action="protective_stop_repair")
    adapter = FakeRealOrderAdapter(
        position_state="ENTERED",
        direction="long",
        protective_stop_present=True,
    )

    result = real_order_worker.run_once(args=args, adapter_factory=lambda _: adapter)

    assert result["status"] == "blocked"
    assert "protective_stop_already_present" in result["reason_codes"]
    assert adapter.executed_commands == []


def test_real_order_worker_records_invalid_snapshot_failure_in_state_store(tmp_path: Path) -> None:
    args = _args(tmp_path)
    args.submit_real_orders = True
    _write_package(Path(args.package_path))

    result = real_order_worker.run_once(args=args, adapter_factory=lambda _: InvalidSnapshotAdapter())
    state = StateStore(tmp_path / "state.json").load()

    assert result["status"] == "blocked"
    assert "runtime_snapshot_invalid" in result["reason_codes"]
    assert state.consecutive_api_failure_count == 1
    assert "pre_submit_position_check:timeout" in state.last_reason_codes


def test_real_order_worker_enters_degraded_after_three_invalid_snapshots(tmp_path: Path) -> None:
    args = _args(tmp_path)
    args.submit_real_orders = True
    _write_package(Path(args.package_path))

    for _ in range(3):
        real_order_worker.run_once(args=args, adapter_factory=lambda _: InvalidSnapshotAdapter())

    state = StateStore(tmp_path / "state.json").load()
    assert state.consecutive_api_failure_count == 3
    assert state.execution_state is ExecutionLayerState.DEGRADED


def test_real_order_worker_success_clears_previous_api_failure_count(tmp_path: Path) -> None:
    args = _args(tmp_path)
    args.submit_real_orders = True
    _write_package(Path(args.package_path))
    StateStore(tmp_path / "state.json").record_api_failure(reason_code="fetch_position_timeout")

    result = real_order_worker.run_once(args=args, adapter_factory=lambda _: FakeRealOrderAdapter())
    state = StateStore(tmp_path / "state.json").load()

    assert result["status"] == "submitted"
    assert state.consecutive_api_failure_count == 0
    assert state.last_api_failure_at == ""


def test_real_order_worker_blocks_replayed_idempotency_key(tmp_path: Path) -> None:
    args = _args(tmp_path)
    args.submit_real_orders = True
    _write_package(Path(args.package_path))
    first_adapter = FakeRealOrderAdapter()
    second_adapter = FakeRealOrderAdapter()

    first = real_order_worker.run_once(args=args, adapter_factory=lambda _: first_adapter)
    second = real_order_worker.run_once(args=args, adapter_factory=lambda _: second_adapter)

    assert first["status"] == "submitted"
    assert second["status"] == "blocked"
    assert second["reason_codes"] == ["idempotency_key_already_completed"]
    assert second_adapter.executed_commands == []


def test_real_order_worker_blocks_fresh_duplicate_lock(tmp_path: Path) -> None:
    lock_path = tmp_path / "real_order_worker.lock"
    lock_path.write_text("fresh", encoding="utf-8")

    with pytest.raises(RuntimeError, match="already running"):
        with real_order_worker.WorkerLock(lock_path=lock_path, stale_after_sec=900):
            pass


def test_real_order_worker_clears_stale_lock(tmp_path: Path) -> None:
    lock_path = tmp_path / "real_order_worker.lock"
    lock_path.write_text("stale", encoding="utf-8")
    old_time = time.time() - 3600
    os.utime(lock_path, (old_time, old_time))

    with real_order_worker.WorkerLock(lock_path=lock_path, stale_after_sec=1):
        assert lock_path.exists()

    assert not lock_path.exists()
