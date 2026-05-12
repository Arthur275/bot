from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from bot.config import RuntimeMode
from bot.audit_logger import AuditLogger
from bot.exchange_adapter import AdapterRuntimeSnapshot, CommandExecutionResult, ExecutionCommand, OrderSnapshot, PositionSnapshot
from bot.state_store import ExecutionLayerState, StateStore
from scripts import real_order_worker


class FakeRealOrderAdapter:
    def __init__(
        self,
        *,
        position_state: str = "FLAT",
        direction: str = "neutral",
        protective_stop_present: bool = False,
        position_amt: float | None = None,
        entry_price: float = 3000.0,
        stop_failures_before_success: int = 0,
        open_algo_orders: list[dict[str, object]] | None = None,
        cancel_raises: bool = False,
        create_kill_switch_on_submit: Path | None = None,
        create_kill_switch_on_snapshot: tuple[Path, int] | None = None,
        mutate_position_before_submit: tuple[str, str] | None = None,
    ) -> None:
        self.snapshots_fetched = 0
        self.executed_commands: list[ExecutionCommand] = []
        self.canceled_algo_orders: list[dict[str, object]] = []
        self._stop_failures_before_success = stop_failures_before_success
        self._open_algo_orders = list(open_algo_orders or [])
        self._cancel_raises = cancel_raises
        self._create_kill_switch_on_submit = create_kill_switch_on_submit
        self._create_kill_switch_on_snapshot = create_kill_switch_on_snapshot
        self._mutate_position_before_submit = mutate_position_before_submit
        resolved_position_amt = position_amt if position_amt is not None else (0.048 if position_state == "ENTERED" else 0.0)
        self._snapshot = AdapterRuntimeSnapshot(
            fetched_at=datetime(2026, 5, 4, 1, 0, 0),
            position=PositionSnapshot(
                position_state=position_state,
                direction=direction,
                size_pct=0.02 if resolved_position_amt else 0.0,
                position_amt=resolved_position_amt,
                entry_price=entry_price if resolved_position_amt else None,
                mark_price=entry_price,
            ),
            protective_stop_present=protective_stop_present,
            snapshot_valid=True,
        )

    def fetch_runtime_snapshot(self) -> AdapterRuntimeSnapshot:
        self.snapshots_fetched += 1
        if self._create_kill_switch_on_snapshot is not None:
            path, snapshot_number = self._create_kill_switch_on_snapshot
            if self.snapshots_fetched >= snapshot_number:
                path.write_text("1", encoding="utf-8")
                self._create_kill_switch_on_snapshot = None
        if self._mutate_position_before_submit and self.snapshots_fetched >= 4:
            state, direction = self._mutate_position_before_submit
            self._snapshot.position = PositionSnapshot(
                position_state=state,
                direction=direction,
                size_pct=0.02 if state == "ENTERED" else 0.0,
                position_amt=0.048 if state == "ENTERED" else 0.0,
                entry_price=3000.0 if state == "ENTERED" else None,
                mark_price=3000.0,
            )
        return self._snapshot

    def execute_commands(
        self,
        *,
        commands: list[ExecutionCommand],
        runtime_mode: RuntimeMode,
    ) -> list[CommandExecutionResult]:
        assert runtime_mode == RuntimeMode.REAL
        if self._create_kill_switch_on_submit is not None:
            self._create_kill_switch_on_submit.write_text("1", encoding="utf-8")
            self._create_kill_switch_on_submit = None
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
        results: list[CommandExecutionResult] = []
        for command in commands:
            if command.target == "entry_order":
                direction = str(getattr(command.payload, "direction", "") or "long")
                self._snapshot.position = PositionSnapshot(
                    position_state="ENTERED",
                    direction=direction,
                    size_pct=0.02,
                    position_amt=0.048 if direction == "long" else -0.048,
                    entry_price=3000.0,
                    mark_price=3000.0,
                )
            client_id = f"ethbot-ps-{command.idempotency_key.replace(':', '-')}" if command.target == "maintain_protective_stop" else f"client-{command.target}"
            exchange_id = f"algo-{command.idempotency_key.replace(':', '-')}" if command.target == "maintain_protective_stop" else f"exchange-{command.target}"
            details = {}
            if command.target == "maintain_protective_stop":
                direction = str(getattr(command.payload, "direction", "") or self._snapshot.position.direction or "long")
                side = "sell" if direction == "long" else "buy"
                quantity = str(abs(float(self._snapshot.position.position_amt or 0.048)))
                trigger_price = "2910.0"
                details = {
                    "prepared_request": {
                        "body": {
                            "instId": "ETH-USDT-SWAP",
                            "algoClOrdId": client_id,
                            "side": side,
                            "ordType": "conditional",
                            "closeFraction": "1",
                            "sz": quantity,
                            "triggerPx": trigger_price,
                        }
                    },
                    "response_payload": {"data": [{"algoId": exchange_id, "algoClOrdId": client_id, "sCode": "0"}]},
                    "response_summary": {
                        "algoId": exchange_id,
                        "algoClOrdId": client_id,
                        "resolved_stop_price": trigger_price,
                        "resolved_position_amt": quantity,
                    },
                }
                if not any(str(order.get("algoClOrdId") or order.get("clientAlgoId") or "") == client_id for order in self._open_algo_orders):
                    self._open_algo_orders.append(
                        _algo_order(
                            algoId=exchange_id,
                            algoClOrdId=client_id,
                            side=side,
                            quantity=quantity,
                            triggerPrice=trigger_price,
                        )
                    )
                self._snapshot.protective_stop_present = True
            if command.target == "take_profit_order":
                direction = str(getattr(command.payload, "direction", "") or self._snapshot.position.direction or "long")
                side = "SELL" if direction == "long" else "BUY"
                quantity = str(abs(float(self._snapshot.position.position_amt or 0.048)) * float(getattr(command.payload, "reduce_fraction", None) or 1.0))
                price = "3030.0"
                client_id = f"ethbot-tp-{command.idempotency_key.replace(':', '-')}"
                exchange_id = f"order-{command.idempotency_key.replace(':', '-')}"
                details = {
                    "prepared_request": {
                        "body": {
                            "instId": "ETH-USDT-SWAP",
                            "clOrdId": client_id,
                            "side": side,
                            "ordType": "limit",
                            "reduceOnly": "true",
                            "sz": quantity,
                            "px": price,
                        }
                    },
                    "response_payload": {"data": [{"ordId": exchange_id, "clOrdId": client_id, "sCode": "0"}]},
                    "response_summary": {
                        "ordId": exchange_id,
                        "clOrdId": client_id,
                        "resolved_take_profit_price": price,
                        "resolved_reduce_qty": quantity,
                    },
                }
                if not any(str(order.order_id) == client_id for order in self._snapshot.open_orders):
                    self._snapshot.open_orders.append(
                        OrderSnapshot(
                            order_id=exchange_id,
                            client_order_id=client_id,
                            order_type="LIMIT",
                            status="NEW",
                            side=side,
                            reduce_only=True,
                            quantity=float(quantity),
                            price=float(price),
                        )
                    )
            results.append(
                CommandExecutionResult(
                    target=command.target,
                    status="accepted",
                    accepted=True,
                    simulated=False,
                    reason=command.reason,
                    idempotency_key=command.idempotency_key,
                    client_order_id=client_id,
                    exchange_order_id=exchange_id,
                    details=details,
                )
            )
        return results

    def fetch_open_algo_orders_raw(self) -> list[dict[str, object]]:
        return list(self._open_algo_orders)

    def cancel_algo_order_raw(self, *, algo_id: str = "", client_algo_id: str = "") -> dict[str, object]:
        if self._cancel_raises:
            raise RuntimeError("cancel failed")
        payload = {"algoId": algo_id, "algoClOrdId": client_algo_id, "status": "canceled"}
        self.canceled_algo_orders.append(payload)
        self._open_algo_orders = [
            order
            for order in self._open_algo_orders
            if str(order.get("algoId") or "") != algo_id
            and str(order.get("algoClOrdId") or order.get("clientAlgoId") or "") != client_algo_id
        ]
        if not self._open_algo_orders:
            self._snapshot.protective_stop_present = False
        return payload


class InvalidSnapshotAdapter(FakeRealOrderAdapter):
    def __init__(self) -> None:
        super().__init__()
        self._snapshot = AdapterRuntimeSnapshot(
            snapshot_valid=False,
            error_endpoint="/api/v5/account/positions",
            error_kind="timeout",
            error_message="timeout",
        )


class RejectingStopAdapter(FakeRealOrderAdapter):
    def execute_commands(
        self,
        *,
        commands: list[ExecutionCommand],
        runtime_mode: RuntimeMode,
    ) -> list[CommandExecutionResult]:
        if any(command.target == "maintain_protective_stop" for command in commands):
            self.executed_commands.extend(commands)
            return [
                CommandExecutionResult(
                    target=command.target,
                    status="rejected",
                    accepted=False,
                    simulated=False,
                    reason="exchange_rejected",
                    idempotency_key=command.idempotency_key,
                    error_kind="http_error",
                )
                for command in commands
            ]
        return super().execute_commands(commands=commands, runtime_mode=runtime_mode)


class UnconfirmedTakeProfitAdapter(FakeRealOrderAdapter):
    def execute_commands(
        self,
        *,
        commands: list[ExecutionCommand],
        runtime_mode: RuntimeMode,
    ) -> list[CommandExecutionResult]:
        results = super().execute_commands(commands=commands, runtime_mode=runtime_mode)
        if any(command.target == "take_profit_order" for command in commands):
            self._snapshot.open_orders = []
        return results


def _algo_order(
    *,
    algoId: object = "algo-1",
    clientAlgoId: str = "",
    side: str = "sell",
    quantity: object = "0.048",
    triggerPrice: object = "2910.0",
    reduceOnly: object = True,
    symbol: str = "",
    instId: str | None = "ETH-USDT-SWAP",
    algoStatus: str = "",
    state: str | None = "live",
    orderType: str = "",
    ordType: str | None = "conditional",
    algoClOrdId: str | None = "ethbot-ps-existing",
    closeFraction: object | None = None,
) -> dict[str, object]:
    if algoClOrdId == "ethbot-ps-existing" and clientAlgoId:
        algoClOrdId = clientAlgoId
    payload = {
        "symbol": symbol,
        "algoId": algoId,
        "clientAlgoId": clientAlgoId,
        "algoStatus": algoStatus,
        "orderType": orderType,
        "side": side,
        "quantity": quantity,
        "triggerPrice": triggerPrice,
        "reduceOnly": reduceOnly,
    }
    if instId is not None:
        payload["instId"] = instId
    if state is not None:
        payload["state"] = state
    if ordType is not None:
        payload["ordType"] = ordType
    if algoClOrdId is not None:
        payload["algoClOrdId"] = algoClOrdId
    if closeFraction is not None:
        payload["closeFraction"] = closeFraction
    return payload


def _args(tmp_path: Path) -> argparse.Namespace:
    return argparse.Namespace(
        command="run-once",
        package_path=str(tmp_path / "latest_candidate_execution_package.json"),
        audit_log_path=str(tmp_path / "real_order_audit.jsonl"),
        lock_path=str(tmp_path / "real_order_worker.lock"),
        kill_switch_path=str(tmp_path / "disable_real_execution.flag"),
        api_key_env="OKX_API_KEY",
        api_secret_env="OKX_API_SECRET",
        api_passphrase_env="OKX_API_PASSPHRASE",
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
        "symbol": "ETH",
        "exchange_symbol": "ETH-USDT-SWAP",
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

    assert result["status"] == "submitted_all_accepted"
    assert [event["event_type"] for event in events] == [
        "real_order_worker_command_pending",
        "real_order_worker_command_result",
    ]
    assert events[0]["payload"]["commands"][0]["idempotency_key"] == "entry_long:key"
    assert events[1]["payload"]["results"][0]["idempotency_key"] == "entry_long:key"
    assert adapter.snapshots_fetched == 5
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

    assert result["status"] == "submitted_all_accepted"
    assert [command.target for command in adapter.executed_commands] == [
        "entry_order",
        "maintain_protective_stop",
        "maintain_protective_stop",
        "maintain_protective_stop",
    ]


def test_real_order_worker_submits_take_profit_after_entry_stop_confirmation(tmp_path: Path) -> None:
    args = _args(tmp_path)
    args.submit_real_orders = True
    package = _write_package(Path(args.package_path))
    package["execution_commands"].extend(
        [
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
        ]
    )
    package["preflight"].extend(
        [
            {"target": "maintain_protective_stop", "status": "preflight_ready", "error": ""},
            {"target": "take_profit_order", "status": "preflight_ready", "error": ""},
        ]
    )
    Path(args.package_path).write_text(json.dumps(package, ensure_ascii=False), encoding="utf-8")
    adapter = FakeRealOrderAdapter()

    result = real_order_worker.run_once(args=args, adapter_factory=lambda _: adapter)

    assert result["status"] == "submitted_all_accepted"
    assert [command.target for command in adapter.executed_commands] == [
        "entry_order",
        "maintain_protective_stop",
        "take_profit_order",
    ]
    tp_result = result["results"][-1]
    assert tp_result["target"] == "take_profit_order"
    assert tp_result["details"]["take_profit_confirmation"]["matched_order"]["client_order_id"].startswith("ethbot-tp-")


def test_real_order_worker_does_not_submit_take_profit_when_stop_confirmation_fails(tmp_path: Path) -> None:
    args = _args(tmp_path)
    args.submit_real_orders = True
    package = _write_package(Path(args.package_path))
    package["execution_commands"].extend(
        [
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
        ]
    )
    package["preflight"].extend(
        [
            {"target": "maintain_protective_stop", "status": "preflight_ready", "error": ""},
            {"target": "take_profit_order", "status": "preflight_ready", "error": ""},
        ]
    )
    Path(args.package_path).write_text(json.dumps(package, ensure_ascii=False), encoding="utf-8")
    adapter = RejectingStopAdapter()

    result = real_order_worker.run_once(args=args, adapter_factory=lambda _: adapter)

    assert result["status"] == "partial_failed"
    assert [command.target for command in adapter.executed_commands] == [
        "entry_order",
        "maintain_protective_stop",
        "maintain_protective_stop",
        "maintain_protective_stop",
    ]


def test_real_order_worker_marks_take_profit_unconfirmed_for_recovery(tmp_path: Path) -> None:
    args = _args(tmp_path)
    args.submit_real_orders = True
    package = _write_package(Path(args.package_path))
    package["execution_commands"].extend(
        [
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
        ]
    )
    package["preflight"].extend(
        [
            {"target": "maintain_protective_stop", "status": "preflight_ready", "error": ""},
            {"target": "take_profit_order", "status": "preflight_ready", "error": ""},
        ]
    )
    Path(args.package_path).write_text(json.dumps(package, ensure_ascii=False), encoding="utf-8")
    adapter = UnconfirmedTakeProfitAdapter()

    result = real_order_worker.run_once(args=args, adapter_factory=lambda _: adapter)

    assert result["status"] == "partial_failed"
    tp_result = result["results"][-1]
    assert tp_result["target"] == "take_profit_order"
    assert tp_result["accepted"] is False
    assert tp_result["status"] == "timeout"
    assert tp_result["error_kind"] == "timeout"
    assert tp_result["reason"] == "take_profit_order_confirmation_missing"
    assert tp_result["details"]["unconfirmed_submission_result"]["accepted"] is True


def test_real_order_worker_blocks_take_profit_if_kill_switch_appears_after_entry_stop(tmp_path: Path) -> None:
    args = _args(tmp_path)
    args.submit_real_orders = True
    package = _write_package(Path(args.package_path))
    package["execution_commands"].extend(
        [
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
        ]
    )
    package["preflight"].extend(
        [
            {"target": "maintain_protective_stop", "status": "preflight_ready", "error": ""},
            {"target": "take_profit_order", "status": "preflight_ready", "error": ""},
        ]
    )
    Path(args.package_path).write_text(json.dumps(package, ensure_ascii=False), encoding="utf-8")
    adapter = FakeRealOrderAdapter(create_kill_switch_on_submit=Path(args.kill_switch_path))

    result = real_order_worker.run_once(args=args, adapter_factory=lambda _: adapter)

    assert result["status"] == "partial_failed"
    assert [command.target for command in adapter.executed_commands] == ["entry_order", "maintain_protective_stop"]
    tp_result = result["results"][-1]
    assert tp_result["target"] == "take_profit_order"
    assert tp_result["reason"] == "kill_switch_enabled_before_submit"


def test_real_order_worker_cancels_ghost_stop_before_entry(tmp_path: Path) -> None:
    args = _args(tmp_path)
    args.submit_real_orders = True
    _write_package(Path(args.package_path))
    adapter = FakeRealOrderAdapter(
        protective_stop_present=True,
        open_algo_orders=[_algo_order(algoId="123", clientAlgoId="ethbot-ps-ghost")],
    )

    result = real_order_worker.run_once(args=args, adapter_factory=lambda _: adapter)

    assert result["status"] == "submitted_all_accepted"
    assert adapter.canceled_algo_orders == [{"algoId": "123", "algoClOrdId": "ethbot-ps-ghost", "status": "canceled"}]


def test_real_order_worker_blocks_ghost_cleanup_when_external_algo_present(tmp_path: Path) -> None:
    args = _args(tmp_path)
    args.submit_real_orders = True
    _write_package(Path(args.package_path))
    adapter = FakeRealOrderAdapter(
        protective_stop_present=True,
        open_algo_orders=[_algo_order(algoId="manual", clientAlgoId="manual-stop")],
    )

    result = real_order_worker.run_once(args=args, adapter_factory=lambda _: adapter)

    assert result["status"] == "blocked"
    assert result["reason_codes"] == ["external_algo_order_present"]
    assert adapter.canceled_algo_orders == []
    assert adapter.executed_commands == []


def test_real_order_worker_blocks_cleanup_for_bot_stop_with_wrong_side(tmp_path: Path) -> None:
    args = _args(tmp_path)
    args.submit_real_orders = True
    _write_package(Path(args.package_path), action="exit")
    adapter = FakeRealOrderAdapter(
        position_state="ENTERED",
        direction="long",
        protective_stop_present=True,
        open_algo_orders=[_algo_order(algoId="bad-side", clientAlgoId="ethbot-ps-bad-side", side="BUY")],
    )

    result = real_order_worker.run_once(args=args, adapter_factory=lambda _: adapter)

    assert result["status"] == "partial_failed"
    assert result["results"][-1]["target"] == "cleanup_open_algo_orders"
    assert result["results"][-1]["reason"] == "bot_algo_order_semantics_mismatch"
    assert adapter.canceled_algo_orders == []


def test_real_order_worker_blocks_entry_when_ghost_stop_cancel_fails(tmp_path: Path) -> None:
    args = _args(tmp_path)
    args.submit_real_orders = True
    _write_package(Path(args.package_path))
    adapter = FakeRealOrderAdapter(
        protective_stop_present=True,
        open_algo_orders=[_algo_order(algoId="123", clientAlgoId="ethbot-ps-ghost")],
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
        open_algo_orders=[_algo_order(algoId="456", clientAlgoId="ethbot-ps-exit")],
    )

    result = real_order_worker.run_once(args=args, adapter_factory=lambda _: adapter)

    assert result["status"] == "submitted_all_accepted"
    assert adapter.canceled_algo_orders == [{"algoId": "456", "algoClOrdId": "ethbot-ps-exit", "status": "canceled"}]


def test_real_order_worker_blocks_exit_cleanup_if_kill_switch_appears_after_exit_order(tmp_path: Path) -> None:
    args = _args(tmp_path)
    args.submit_real_orders = True
    _write_package(Path(args.package_path), action="exit")
    adapter = FakeRealOrderAdapter(
        position_state="ENTERED",
        direction="long",
        protective_stop_present=True,
        open_algo_orders=[_algo_order(algoId="456", clientAlgoId="ethbot-ps-exit")],
        create_kill_switch_on_submit=Path(args.kill_switch_path),
    )

    result = real_order_worker.run_once(args=args, adapter_factory=lambda _: adapter)

    assert result["status"] == "partial_failed"
    assert [command.target for command in adapter.executed_commands] == ["exit_order"]
    assert adapter.canceled_algo_orders == []
    cleanup_result = result["results"][-1]
    assert cleanup_result["target"] == "cleanup_open_algo_orders"
    assert cleanup_result["reason"] == "kill_switch_enabled_before_submit"


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
        open_algo_orders=[_algo_order(algoId="789", clientAlgoId="ethbot-ps-old")],
    )

    result = real_order_worker.run_once(args=args, adapter_factory=lambda _: adapter)

    assert result["status"] == "submitted_all_accepted"
    assert [command.target for command in adapter.executed_commands] == [
        "reduce_order",
        "maintain_protective_stop",
    ]
    assert adapter.canceled_algo_orders == [{"algoId": "789", "algoClOrdId": "ethbot-ps-old", "status": "canceled"}]


def test_real_order_worker_blocks_reduce_stop_refresh_if_kill_switch_appears_after_reduce(tmp_path: Path) -> None:
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
        open_algo_orders=[_algo_order(algoId="789", clientAlgoId="ethbot-ps-old")],
        create_kill_switch_on_submit=Path(args.kill_switch_path),
    )

    result = real_order_worker.run_once(args=args, adapter_factory=lambda _: adapter)

    assert result["status"] == "partial_failed"
    assert [command.target for command in adapter.executed_commands] == ["reduce_order"]
    assert adapter.canceled_algo_orders == []
    stop_result = result["results"][-1]
    assert stop_result["target"] == "maintain_protective_stop"
    assert stop_result["reason"] == "kill_switch_enabled_before_submit"


def test_real_order_worker_reduce_cleanup_keeps_new_and_external_stops(tmp_path: Path) -> None:
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
        open_algo_orders=[
            _algo_order(algoId="old", clientAlgoId="ethbot-ps-old"),
            _algo_order(algoId="manual", clientAlgoId="manual-stop"),
        ],
    )

    result = real_order_worker.run_once(args=args, adapter_factory=lambda _: adapter)

    assert result["status"] == "submitted_all_accepted"
    assert adapter.canceled_algo_orders == [{"algoId": "old", "algoClOrdId": "ethbot-ps-old", "status": "canceled"}]
    remaining_client_ids = {str(order.get("algoClOrdId") or order.get("clientAlgoId") or "") for order in adapter.fetch_open_algo_orders_raw()}
    assert "manual-stop" in remaining_client_ids
    assert "ethbot-ps-reduce-stop-key" in remaining_client_ids


def test_real_order_worker_repairs_missing_protective_stop_with_retry(tmp_path: Path) -> None:
    args = _args(tmp_path)
    args.submit_real_orders = True
    _write_package(Path(args.package_path), action="protective_stop_repair")
    adapter = FakeRealOrderAdapter(
        position_state="ENTERED",
        direction="long",
        protective_stop_present=False,
        stop_failures_before_success=1,
        open_algo_orders=[],
    )

    result = real_order_worker.run_once(args=args, adapter_factory=lambda _: adapter)

    assert result["status"] == "submitted_all_accepted"
    assert [command.target for command in adapter.executed_commands] == [
        "maintain_protective_stop",
        "maintain_protective_stop",
    ]


def test_real_order_worker_requires_open_algo_confirmation_after_stop_place(tmp_path: Path) -> None:
    args = _args(tmp_path)
    args.submit_real_orders = True
    _write_package(Path(args.package_path), action="protective_stop_repair")
    adapter = RejectingStopAdapter(position_state="ENTERED", direction="long", protective_stop_present=False)

    result = real_order_worker.run_once(args=args, adapter_factory=lambda _: adapter)
    state = StateStore(tmp_path / "state.json").load()

    assert result["status"] == "all_failed"
    assert len(adapter.executed_commands) == 3
    assert state.execution_state is ExecutionLayerState.RECONCILING
    assert state.protective_stop_required is True


def test_real_order_worker_rejects_unrelated_algo_as_stop_confirmation(tmp_path: Path) -> None:
    args = _args(tmp_path)
    args.submit_real_orders = True
    _write_package(Path(args.package_path), action="protective_stop_repair")
    adapter = FakeRealOrderAdapter(
        position_state="ENTERED",
        direction="long",
        protective_stop_present=False,
        open_algo_orders=[_algo_order(algoId="manual", clientAlgoId="manual-stop")],
    )

    result = real_order_worker.run_once(args=args, adapter_factory=lambda _: adapter)

    assert result["status"] == "submitted_all_accepted"
    assert len(adapter.executed_commands) == 1
    matched_order = result["results"][0]["details"]["protective_stop_confirmation"]["matched_order"]
    assert str(matched_order.get("algoClOrdId") or matched_order.get("clientAlgoId") or "").startswith("ethbot-ps-")


def test_find_matching_protective_stop_rejects_wrong_direction_quantity_and_reduce_only() -> None:
    command = ExecutionCommand.model_validate(
        {
            "command_type": "order",
            "operation": "upsert",
            "target": "maintain_protective_stop",
            "idempotency_key": "stop:key",
            "reason": "protective_stop_required",
            "payload": {"direction": "long", "initial_stop_loss": 0.97, "tp_ladder": []},
        }
    )
    result = CommandExecutionResult(
        target="maintain_protective_stop",
        status="accepted",
        accepted=True,
        simulated=False,
        reason="protective_stop_required",
        idempotency_key="stop:key",
        client_order_id="ethbot-ps-stop-key",
        exchange_order_id="algo-stop-key",
        details={
            "prepared_request": {
                "params": {
                    "clientAlgoId": "ethbot-ps-stop-key",
                    "side": "SELL",
                    "quantity": "0.048",
                    "triggerPrice": "2910.0",
                }
            }
        },
    )

    assert real_order_worker.find_matching_protective_stop(
        command=command,
        result=result,
        open_algo_orders=[_algo_order(algoId="algo-stop-key", clientAlgoId="ethbot-ps-stop-key", side="BUY")],
    ) is None
    assert real_order_worker.find_matching_protective_stop(
        command=command,
        result=result,
        open_algo_orders=[_algo_order(algoId="algo-stop-key", clientAlgoId="ethbot-ps-stop-key", quantity="0.001")],
    ) is None
    assert real_order_worker.find_matching_protective_stop(
        command=command,
        result=result,
        open_algo_orders=[_algo_order(algoId="algo-stop-key", clientAlgoId="ethbot-ps-stop-key", reduceOnly=False)],
    ) is None
    assert real_order_worker.find_matching_protective_stop(
        command=command,
        result=result,
        open_algo_orders=[_algo_order(algoId="algo-stop-key", clientAlgoId="ethbot-ps-stop-key")],
    ) is not None


def test_find_matching_protective_stop_accepts_okx_algo_fields() -> None:
    command = ExecutionCommand.model_validate(
        {
            "command_type": "order",
            "operation": "upsert",
            "target": "maintain_protective_stop",
            "idempotency_key": "stop:key",
            "reason": "protective_stop_required",
            "payload": {"direction": "long", "initial_stop_loss": 0.97, "tp_ladder": []},
        }
    )
    result = CommandExecutionResult(
        target="maintain_protective_stop",
        status="accepted",
        accepted=True,
        simulated=False,
        reason="protective_stop_required",
        idempotency_key="stop:key",
        client_order_id="ethbot-ps-stop-key",
        exchange_order_id="algo-stop-key",
        details={
            "prepared_request": {
                "body": {
                    "algoClOrdId": "ethbot-ps-stop-key",
                    "side": "sell",
                    "sz": "0.048",
                    "triggerPx": "2910.0",
                }
            }
        },
    )

    match = real_order_worker.find_matching_protective_stop(
        command=command,
        result=result,
        open_algo_orders=[
            _algo_order(
                algoId="algo-stop-key",
                algoClOrdId="ethbot-ps-stop-key",
                clientAlgoId="",
                instId="ETH-USDT-SWAP",
                state="live",
                ordType="conditional",
                side="sell",
                quantity="0.048",
                triggerPrice="2910.0",
                reduceOnly=False,
                closeFraction="1",
            )
        ],
    )

    assert match is not None
    assert match["algoClOrdId"] == "ethbot-ps-stop-key"


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

    assert result["status"] == "submitted_all_accepted"
    assert state.consecutive_api_failure_count == 0
    assert state.last_api_failure_at == ""


def test_real_order_worker_blocks_entry_if_kill_switch_appears_after_pending(tmp_path: Path) -> None:
    args = _args(tmp_path)
    args.submit_real_orders = True
    _write_package(Path(args.package_path))
    adapter = FakeRealOrderAdapter(create_kill_switch_on_snapshot=(Path(args.kill_switch_path), 4))

    result = real_order_worker.run_once(args=args, adapter_factory=lambda _: adapter)

    assert result["status"] == "all_failed"
    assert result["results"][0]["reason"] == "kill_switch_enabled_before_submit"
    assert adapter.executed_commands == []


def test_real_order_worker_allows_stop_after_entry_when_kill_switch_appears(tmp_path: Path) -> None:
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
    adapter = FakeRealOrderAdapter(create_kill_switch_on_submit=Path(args.kill_switch_path))

    result = real_order_worker.run_once(args=args, adapter_factory=lambda _: adapter)

    assert result["status"] == "submitted_all_accepted"
    assert [command.target for command in adapter.executed_commands] == ["entry_order", "maintain_protective_stop"]
    assert Path(args.kill_switch_path).exists()


def test_real_order_worker_blocks_entry_if_position_changes_after_pending(tmp_path: Path) -> None:
    args = _args(tmp_path)
    args.submit_real_orders = True
    _write_package(Path(args.package_path))
    adapter = FakeRealOrderAdapter(mutate_position_before_submit=("ENTERED", "long"))

    result = real_order_worker.run_once(args=args, adapter_factory=lambda _: adapter)

    assert result["status"] == "all_failed"
    assert result["results"][0]["reason"] == "live_position_not_flat_before_submit"
    assert adapter.executed_commands == []


def test_real_order_worker_blocks_replayed_idempotency_key(tmp_path: Path) -> None:
    args = _args(tmp_path)
    args.submit_real_orders = True
    _write_package(Path(args.package_path))
    first_adapter = FakeRealOrderAdapter()
    second_adapter = FakeRealOrderAdapter()

    first = real_order_worker.run_once(args=args, adapter_factory=lambda _: first_adapter)
    second = real_order_worker.run_once(args=args, adapter_factory=lambda _: second_adapter)

    assert first["status"] == "submitted_all_accepted"
    assert second["status"] == "blocked"
    assert second["reason_codes"] == ["idempotency_key_already_completed"]
    assert second_adapter.executed_commands == []


def test_real_order_worker_blocks_replayed_okx_data_response_idempotency_key(tmp_path: Path) -> None:
    args = _args(tmp_path)
    args.submit_real_orders = True
    _write_package(Path(args.package_path), action="protective_stop_repair")
    AuditLogger(args.audit_log_path).append(
        event_type="real_order_worker_command_result",
        payload={
            "status": "submitted_all_accepted",
            "package_id": "previous-package",
            "commands": [{"target": "maintain_protective_stop", "idempotency_key": "protective_stop_repair:key"}],
            "results": [
                {
                    "target": "maintain_protective_stop",
                    "idempotency_key": "protective_stop_repair:key",
                    "status": "accepted",
                    "accepted": True,
                    "details": {
                        "response_payload": {
                            "code": "0",
                            "data": [{"algoId": "algo-123", "algoClOrdId": "ethbot-ps-123", "sCode": "0"}],
                        }
                    },
                }
            ],
        },
    )
    adapter = FakeRealOrderAdapter(position_state="ENTERED", direction="long", protective_stop_present=False)

    result = real_order_worker.run_once(args=args, adapter_factory=lambda _: adapter)

    assert result["status"] == "blocked"
    assert result["reason_codes"] == ["idempotency_key_already_completed"]
    assert adapter.executed_commands == []


def test_real_order_worker_does_not_complete_failed_idempotency_key(tmp_path: Path) -> None:
    args = _args(tmp_path)
    args.submit_real_orders = True
    _write_package(Path(args.package_path), action="protective_stop_repair")
    first_adapter = RejectingStopAdapter(position_state="ENTERED", direction="long", protective_stop_present=False)
    second_adapter = FakeRealOrderAdapter(position_state="ENTERED", direction="long", protective_stop_present=False)

    first = real_order_worker.run_once(args=args, adapter_factory=lambda _: first_adapter)
    second = real_order_worker.run_once(args=args, adapter_factory=lambda _: second_adapter)

    assert first["status"] == "all_failed"
    assert second["status"] == "submitted_all_accepted"
    assert [command.target for command in second_adapter.executed_commands] == ["maintain_protective_stop"]


def test_real_order_worker_timeout_idempotency_requires_recovery(tmp_path: Path) -> None:
    args = _args(tmp_path)
    args.submit_real_orders = True
    _write_package(Path(args.package_path))
    AuditLogger(args.audit_log_path).append(
        event_type="real_order_worker_command_result",
        payload={
            "status": "all_failed",
            "package_id": "previous-package",
            "commands": [{"target": "entry_order", "idempotency_key": "entry_long:key"}],
            "results": [{"target": "entry_order", "idempotency_key": "entry_long:key", "status": "timeout", "accepted": False, "error_kind": "timeout"}],
        },
    )
    adapter = FakeRealOrderAdapter()

    result = real_order_worker.run_once(args=args, adapter_factory=lambda _: adapter)

    assert result["status"] == "blocked"
    assert result["reason_codes"] == ["pending_idempotency_key_requires_recovery"]
    assert adapter.executed_commands == []


def test_real_order_worker_blocks_pending_idempotency_key_until_recovery(tmp_path: Path) -> None:
    args = _args(tmp_path)
    args.submit_real_orders = True
    _write_package(Path(args.package_path))
    AuditLogger(args.audit_log_path).append(
        event_type="real_order_worker_command_pending",
        payload={
            "status": "pending",
            "package_id": "previous-package",
            "commands": [{"target": "entry_order", "idempotency_key": "entry_long:key"}],
        },
    )
    adapter = FakeRealOrderAdapter()

    result = real_order_worker.run_once(args=args, adapter_factory=lambda _: adapter)

    assert result["status"] == "blocked"
    assert result["reason_codes"] == ["pending_idempotency_key_requires_recovery"]
    assert result["idempotency_keys"] == ["entry_long:key"]
    assert adapter.executed_commands == []


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


def test_real_order_worker_keeps_stale_lock_when_worker_pid_is_live(tmp_path: Path) -> None:
    lock_path = tmp_path / "real_order_worker.lock"
    lock_path.write_text(
        json.dumps(
            {
                "owner": real_order_worker.WORKER_LOCK_OWNER,
                "pid": os.getpid(),
                "process_start_token": real_order_worker._process_start_token(os.getpid()),
            }
        ),
        encoding="utf-8",
    )
    old_time = time.time() - 3600
    os.utime(lock_path, (old_time, old_time))

    with pytest.raises(RuntimeError, match="already running"):
        with real_order_worker.WorkerLock(lock_path=lock_path, stale_after_sec=1):
            pass

    assert lock_path.exists()


def test_real_order_worker_clears_stale_lock_for_dead_worker_pid(tmp_path: Path) -> None:
    lock_path = tmp_path / "real_order_worker.lock"
    lock_path.write_text(
        json.dumps({"owner": real_order_worker.WORKER_LOCK_OWNER, "pid": 999_999_999}),
        encoding="utf-8",
    )
    old_time = time.time() - 3600
    os.utime(lock_path, (old_time, old_time))

    with real_order_worker.WorkerLock(lock_path=lock_path, stale_after_sec=1):
        assert lock_path.exists()

    assert not lock_path.exists()


def test_real_order_worker_clears_stale_non_worker_lock_even_if_pid_is_live(tmp_path: Path) -> None:
    lock_path = tmp_path / "real_order_worker.lock"
    lock_path.write_text(json.dumps({"owner": "external-tool", "pid": os.getpid()}), encoding="utf-8")
    old_time = time.time() - 3600
    os.utime(lock_path, (old_time, old_time))

    with real_order_worker.WorkerLock(lock_path=lock_path, stale_after_sec=1):
        assert lock_path.exists()

    assert not lock_path.exists()
