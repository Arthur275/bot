from datetime import datetime

import pytest

from bot.binance_transport import BinanceRequestSigner, BinanceTransportError, TransportResponse
from bot.config import RuntimeMode
from bot.exchange_adapter import (
    AdapterCredentials,
    AdapterRuntimeSnapshot,
    BinancePerpAdapter,
    BreakevenPayload,
    EntryOrderPayload,
    ExchangeAdapter,
    ExitOrderPayload,
    PositionSnapshot,
    ProtectiveStopPayload,
    RealExchangeAdapter,
    RecentFillsPayload,
    ReconciliationPayload,
    ReduceOrderPayload,
    TrailingStopPayload,
)
from bot.position_manager import ExecutionPlan


class FakeTransport:
    def __init__(self, *, response: TransportResponse | None = None, responses: list[TransportResponse] | None = None, exc: Exception | None = None) -> None:
        self._response = response
        self._responses = list(responses or [])
        self._exc = exc
        self.requests = []

    def send(self, signed_request):
        self.requests.append(signed_request)
        if self._exc is not None:
            raise self._exc
        if self._responses:
            response = self._responses.pop(0)
            if isinstance(response, Exception):
                raise response
            return response
        assert self._response is not None
        return self._response


def _credentials() -> AdapterCredentials:
    return AdapterCredentials(
        venue="binance_usdt_perp",
        api_key_env="BINANCE_API_KEY",
        api_secret_env="BINANCE_API_SECRET",
        recv_window_ms=5000,
        timeout_sec=15.0,
        proxy_url="http://127.0.0.1:7897",
        api_base_url="https://fapi.binance.com",
    )


def test_exchange_adapter_builds_entry_and_stop_actions() -> None:
    actions = ExchangeAdapter().plan_actions(
        execution_plan=ExecutionPlan(
            requested_action="entry_long",
            effective_action="entry_long",
            plan_reason="quant_action_passthrough",
            place_entry_order=True,
            maintain_protective_stop=True,
        ),
        handoff={
            "initial_stop_loss": 0.97,
            "breakeven_trigger": 1.01,
            "trailing_rule": "trail_with_trigger",
        },
    )
    assert [action.action_type for action in actions] == [
        "entry_order",
        "maintain_protective_stop",
    ]
    assert actions[0].reason == "effective_action:entry_long"
    assert actions[1].reason == "protective_stop_required"
    assert actions[0].payload["command_type"] == "order"
    assert actions[0].payload["operation"] == "place"


def test_exchange_adapter_builds_reconcile_action() -> None:
    actions = ExchangeAdapter().plan_actions(
        execution_plan=ExecutionPlan(
            requested_action="wait",
            effective_action="wait",
            plan_reason="entry_disallowed_by_guard",
            needs_reconciliation=True,
            recovery_action="reconcile_before_reentry",
        ),
        handoff=None,
    )
    assert [action.action_type for action in actions] == ["reconcile_position_and_orders"]
    assert actions[0].payload == {
        "command_type": "sync",
        "operation": "query",
        "idempotency_key": "reconcile_position_and_orders::wait:neutral",
        "recovery_action": "reconcile_before_reentry",
    }


def test_exchange_adapter_builds_execution_commands() -> None:
    commands = ExchangeAdapter().build_commands(
        execution_plan=ExecutionPlan(
            requested_action="reduce",
            effective_action="reduce",
            plan_reason="quant_action_passthrough",
            place_reduce_order=True,
            maintain_protective_stop=True,
            advance_breakeven=True,
            advance_trailing_stop=True,
            sync_recent_fills=True,
        ),
        handoff={
            "generated_at": "2026-04-26T12:15:00",
            "action": "reduce",
            "direction": "short",
            "reduce_conditions": ["crowding_warning"],
            "initial_stop_loss": 0.97,
            "breakeven_trigger": 1.01,
            "trailing_rule": "trail_with_trigger",
            "tp_ladder": [0.99, 0.98],
        },
    )
    assert [(command.command_type, command.operation, command.target) for command in commands] == [
        ("order", "place", "reduce_order"),
        ("order", "upsert", "maintain_protective_stop"),
        ("risk", "tighten", "advance_breakeven_stop"),
        ("risk", "tighten", "advance_trailing_stop"),
        ("sync", "query", "sync_recent_fills"),
    ]
    assert isinstance(commands[0].payload, ReduceOrderPayload)
    assert commands[0].reason == "effective_action:reduce"
    assert commands[1].reason == "protective_stop_required"
    assert commands[2].reason == "breakeven_ready"
    assert commands[3].reason == "trailing_ready"
    assert commands[4].reason == "recent_fill_sync_required"
    assert isinstance(commands[1].payload, ProtectiveStopPayload)
    assert isinstance(commands[2].payload, BreakevenPayload)
    assert isinstance(commands[3].payload, TrailingStopPayload)
    assert isinstance(commands[4].payload, RecentFillsPayload)
    assert all(command.idempotency_key for command in commands)


def test_exchange_adapter_uses_typed_entry_payload() -> None:
    commands = ExchangeAdapter().build_commands(
        execution_plan=ExecutionPlan(
            requested_action="entry_long",
            effective_action="entry_long",
            plan_reason="quant_action_passthrough",
            place_entry_order=True,
        ),
        handoff={"initial_stop_loss": 0.97, "position_size_pct": 0.15},
    )
    assert isinstance(commands[0].payload, EntryOrderPayload)
    assert commands[0].reason == "effective_action:entry_long"
    assert commands[0].payload.initial_stop_loss == 0.97
    assert commands[0].payload.position_size_pct == 0.15


def test_exchange_adapter_uses_typed_exit_payload() -> None:
    commands = ExchangeAdapter().build_commands(
        execution_plan=ExecutionPlan(
            requested_action="exit",
            effective_action="exit",
            plan_reason="quant_action_passthrough",
            place_exit_order=True,
        ),
        handoff={"generated_at": "2026-04-26T12:16:00", "action": "exit", "direction": "long"},
    )
    assert isinstance(commands[0].payload, ExitOrderPayload)
    assert commands[0].reason == "effective_action:exit"
    assert commands[0].payload.direction == "long"


def test_exchange_adapter_uses_typed_reduce_payload() -> None:
    commands = ExchangeAdapter().build_commands(
        execution_plan=ExecutionPlan(
            requested_action="reduce",
            effective_action="reduce",
            plan_reason="quant_action_passthrough",
            place_reduce_order=True,
        ),
        handoff={
            "generated_at": "2026-04-26T12:16:30",
            "action": "reduce",
            "direction": "short",
            "reduce_conditions": ["crowding_warning"],
        },
    )
    assert isinstance(commands[0].payload, ReduceOrderPayload)
    assert commands[0].reason == "effective_action:reduce"
    assert commands[0].payload.direction == "short"
    assert commands[0].payload.reduce_conditions == ["crowding_warning"]


def test_exchange_adapter_uses_typed_reconciliation_payload() -> None:
    commands = ExchangeAdapter().build_commands(
        execution_plan=ExecutionPlan(
            requested_action="wait",
            effective_action="wait",
            plan_reason="quant_action_passthrough",
            needs_reconciliation=True,
            recovery_action="reconcile_runtime_state",
        ),
        handoff=None,
    )
    assert isinstance(commands[0].payload, ReconciliationPayload)
    assert commands[0].payload.recovery_action == "reconcile_runtime_state"


def test_exchange_adapter_executes_commands_in_shadow_mode() -> None:
    commands = ExchangeAdapter().build_commands(
        execution_plan=ExecutionPlan(
            requested_action="entry_long",
            effective_action="entry_long",
            plan_reason="quant_action_passthrough",
            place_entry_order=True,
            maintain_protective_stop=True,
        ),
        handoff={
            "initial_stop_loss": 0.97,
            "breakeven_trigger": 1.01,
            "trailing_rule": "trail_with_trigger",
        },
    )
    results = ExchangeAdapter().execute_commands(commands=commands)
    assert [result.status for result in results] == ["simulated", "simulated"]
    assert all(result.simulated for result in results)
    assert results[0].details["operation"] == "place"


def test_exchange_adapter_treats_invalid_runtime_snapshot_as_unavailable() -> None:
    result = ExchangeAdapter().assess_reconciliation(
        runtime_snapshot=AdapterRuntimeSnapshot(snapshot_valid=False),
        expected_position_state="ENTERED",
        expected_direction="long",
        expected_size_pct=0.3,
    )
    assert result.in_sync is True
    assert result.needs_position_sync is False
    assert result.needs_order_sync is False
    assert result.reason_codes == []



def test_real_capable_exchange_adapter_reports_runtime_snapshot_unavailable() -> None:
    adapter = RealExchangeAdapter(_credentials())
    result = adapter.assess_reconciliation(
        runtime_snapshot=AdapterRuntimeSnapshot(snapshot_valid=False),
        expected_position_state="ENTERED",
        expected_direction="long",
        expected_size_pct=0.3,
    )
    assert result.in_sync is False
    assert result.needs_position_sync is True
    assert result.needs_order_sync is False
    assert result.reason_codes == ["runtime_snapshot_unavailable"]


def test_exchange_adapter_detects_reconciliation_gaps() -> None:
    result = ExchangeAdapter().assess_reconciliation(
        runtime_snapshot=AdapterRuntimeSnapshot(
            position=PositionSnapshot(
                position_state="ENTERED",
                direction="long",
                size_pct=0.2,
            ),
            protective_stop_present=False,
        ),
        expected_position_state="ENTERED",
        expected_direction="long",
        expected_size_pct=0.3,
    )
    assert result.in_sync is False
    assert result.needs_position_sync is True
    assert result.needs_order_sync is True
    assert "position_size_mismatch" in result.reason_codes
    assert "protective_stop_missing" in result.reason_codes


def test_exchange_adapter_accepts_synced_runtime_snapshot() -> None:
    result = ExchangeAdapter().assess_reconciliation(
        runtime_snapshot=AdapterRuntimeSnapshot(
            position=PositionSnapshot(
                position_state="ENTERED",
                direction="short",
                size_pct=0.3,
            ),
            protective_stop_present=True,
        ),
        expected_position_state="ENTERED",
        expected_direction="short",
        expected_size_pct=0.3,
    )
    assert result.in_sync is True
    assert result.reason_codes == []


def test_exchange_adapter_builds_stable_idempotency_key() -> None:
    adapter = ExchangeAdapter()
    handoff = {
        "generated_at": "2026-04-26T12:20:00",
        "action": "entry_short",
        "direction": "short",
    }
    left = adapter._build_idempotency_key(target="entry_order", handoff=handoff)
    right = adapter._build_idempotency_key(target="entry_order", handoff=handoff)
    assert left == right
    assert left == "entry_order:2026-04-26T12:20:00:entry_short:short"



def test_exchange_adapter_uses_current_position_direction_as_direction_fallback() -> None:
    commands = ExchangeAdapter().build_commands(
        execution_plan=ExecutionPlan(
            requested_action="reduce",
            effective_action="reduce",
            plan_reason="quant_action_passthrough",
            place_reduce_order=True,
            maintain_protective_stop=True,
            advance_breakeven=True,
            advance_trailing_stop=True,
        ),
        handoff={
            "generated_at": "2026-04-26T12:15:00",
            "action": "reduce",
            "current_position_direction": "long",
            "reduce_conditions": ["crowding_warning"],
            "initial_stop_loss": 0.97,
            "breakeven_trigger": 1.01,
            "trailing_rule": "trail_with_trigger",
            "trailing_activation_ratio": 1.01,
            "trailing_callback_rate_pct": 0.5,
        },
    )
    assert isinstance(commands[0].payload, ReduceOrderPayload)
    assert commands[0].payload.direction == "long"
    assert isinstance(commands[1].payload, ProtectiveStopPayload)
    assert commands[1].payload.direction == "long"
    assert isinstance(commands[2].payload, BreakevenPayload)
    assert commands[2].payload.direction == "long"
    assert isinstance(commands[3].payload, TrailingStopPayload)
    assert commands[3].payload.direction == "long"



def test_exchange_adapter_uses_current_position_direction_in_idempotency_key() -> None:
    key = ExchangeAdapter()._build_idempotency_key(
        target="reduce_order",
        handoff={
            "generated_at": "2026-04-26T12:17:30",
            "action": "reduce",
            "current_position_direction": "long",
        },
    )
    assert key == "reduce_order:2026-04-26T12:17:30:reduce:long"



def test_exchange_adapter_prefers_current_position_direction_over_neutral_direction() -> None:
    commands = ExchangeAdapter().build_commands(
        execution_plan=ExecutionPlan(
            requested_action="reduce",
            effective_action="reduce",
            plan_reason="quant_action_passthrough",
            place_reduce_order=True,
            maintain_protective_stop=True,
            advance_breakeven=True,
            advance_trailing_stop=True,
        ),
        handoff={
            "generated_at": "2026-04-26T12:18:00",
            "action": "reduce",
            "direction": "neutral",
            "current_position_direction": "long",
            "reduce_conditions": ["crowding_warning"],
            "initial_stop_loss": 0.97,
            "breakeven_trigger": 1.01,
            "trailing_rule": "trail_with_trigger",
            "trailing_activation_ratio": 1.01,
            "trailing_callback_rate_pct": 0.5,
        },
    )
    assert commands[0].payload.direction == "long"
    assert commands[1].payload.direction == "long"
    assert commands[2].payload.direction == "long"
    assert commands[3].payload.direction == "long"
    assert commands[0].idempotency_key == "reduce_order:2026-04-26T12:18:00:reduce:long"


def test_real_exchange_adapter_executes_simulated_real_without_dispatch() -> None:
    transport = FakeTransport(response=TransportResponse(http_status=200, payload={"orderId": 1}))
    signer = BinanceRequestSigner(
        _credentials(),
        env_getter=lambda key: {"BINANCE_API_KEY": "key123", "BINANCE_API_SECRET": "secret456"}.get(key),
        clock=lambda: 1714132800000,
    )
    adapter = BinancePerpAdapter(_credentials(), signer=signer, transport=transport)
    commands = adapter.build_commands(
        execution_plan=ExecutionPlan(
            requested_action="entry_long",
            effective_action="entry_long",
            plan_reason="quant_action_passthrough",
            place_entry_order=True,
        ),
        handoff={
            "generated_at": "2026-04-26T13:00:00",
            "action": "entry_long",
            "direction": "long",
            "initial_stop_loss": 0.97,
            "position_size_pct": 0.15,
        },
    )
    results = adapter.execute_commands(commands=commands, runtime_mode=RuntimeMode.SIMULATED_REAL)
    assert results[0].status == "simulated"
    assert results[0].accepted is True
    assert results[0].simulated is True
    assert results[0].details["venue"] == "binance_usdt_perp"
    assert results[0].details["prepared_request"]["path"] == "/fapi/v1/order"
    assert results[0].details["signed_request"]["headers"] == {"X-MBX-APIKEY": "key123"}
    assert transport.requests == []


def test_real_exchange_adapter_preflight_entry_order_resolves_request_without_dispatch() -> None:
    transport = FakeTransport(
        responses=[
            TransportResponse(
                http_status=200,
                payload=[
                    {
                        "positionAmt": "0",
                        "entryPrice": "0",
                        "markPrice": "3120.5",
                        "leverage": "10",
                    }
                ],
            ),
            TransportResponse(http_status=200, payload=[]),
            TransportResponse(http_status=200, payload={"totalWalletBalance": "100.0"}),
            TransportResponse(
                http_status=200,
                payload={
                    "symbols": [
                        {
                            "symbol": "ETHUSDT",
                            "filters": [
                                {"filterType": "LOT_SIZE", "minQty": "0.001", "stepSize": "0.001"}
                            ],
                        }
                    ]
                },
            ),
            TransportResponse(http_status=200, payload={"symbol": "ETHUSDT", "markPrice": "3120.5"}),
        ]
    )
    signer = BinanceRequestSigner(
        _credentials(),
        env_getter=lambda key: {"BINANCE_API_KEY": "key123", "BINANCE_API_SECRET": "secret456"}.get(key),
        clock=lambda: 1714132800000,
    )
    refreshed = {"called": False}

    def refresh() -> None:
        refreshed["called"] = True

    signer.refresh_timestamp_offset = refresh  # type: ignore[method-assign]
    adapter = BinancePerpAdapter(_credentials(), signer=signer, transport=transport)
    commands = adapter.build_commands(
        execution_plan=ExecutionPlan(
            requested_action="entry_long",
            effective_action="entry_long",
            plan_reason="quant_action_passthrough",
            place_entry_order=True,
        ),
        handoff={
            "generated_at": "2026-04-26T13:30:00",
            "action": "entry_long",
            "direction": "long",
            "position_size_pct": 0.15,
        },
    )

    results = adapter.preflight_commands(commands=commands)

    assert refreshed["called"] is False
    assert results[0].status == "preflight_ready"
    assert results[0].accepted is True
    assert results[0].simulated is True
    assert results[0].details["preflight"] is True
    assert results[0].details["prepared_request"]["params"]["quantity"] == "0.048"
    assert [request.path for request in transport.requests] == [
        "/fapi/v2/positionRisk",
        "/fapi/v1/openOrders",
        "/fapi/v2/account",
        "/fapi/v1/exchangeInfo",
        "/fapi/v1/premiumIndex",
    ]


def test_real_exchange_adapter_executes_real_entry_order() -> None:
    transport = FakeTransport(
        responses=[
            TransportResponse(
                http_status=200,
                payload=[
                    {
                        "positionAmt": "0",
                        "entryPrice": "0",
                        "markPrice": "3120.5",
                        "leverage": "10",
                    }
                ],
            ),
            TransportResponse(http_status=200, payload=[]),
            TransportResponse(http_status=200, payload={"totalWalletBalance": "100.0"}),
            TransportResponse(
                http_status=200,
                payload={
                    "symbols": [
                        {
                            "symbol": "ETHUSDT",
                            "filters": [
                                {"filterType": "LOT_SIZE", "minQty": "0.001", "stepSize": "0.001"}
                            ],
                        }
                    ]
                },
            ),
            TransportResponse(http_status=200, payload={"symbol": "ETHUSDT", "markPrice": "3120.5"}),
            TransportResponse(http_status=200, payload={"orderId": 12345, "status": "NEW"}),
        ]
    )
    signer = BinanceRequestSigner(
        _credentials(),
        env_getter=lambda key: {"BINANCE_API_KEY": "key123", "BINANCE_API_SECRET": "secret456"}.get(key),
        clock=lambda: 1714132800000,
    )
    adapter = BinancePerpAdapter(_credentials(), signer=signer, transport=transport)
    commands = adapter.build_commands(
        execution_plan=ExecutionPlan(
            requested_action="entry_long",
            effective_action="entry_long",
            plan_reason="quant_action_passthrough",
            place_entry_order=True,
        ),
        handoff={
            "generated_at": "2026-04-26T13:00:00",
            "action": "entry_long",
            "direction": "long",
            "initial_stop_loss": 0.97,
            "position_size_pct": 0.15,
        },
    )
    results = adapter.execute_commands(commands=commands, runtime_mode=RuntimeMode.REAL)
    assert results[0].status == "accepted"
    assert results[0].accepted is True
    assert results[0].simulated is False
    assert results[0].details["http_status"] == 200
    assert results[0].details["response_payload"] == {"orderId": 12345, "status": "NEW"}
    assert results[0].details["prepared_request"]["params"]["quantity"] == "0.048"
    assert results[0].details["prepared_request"]["body"]["resolution_mode"] == "entry_quantity_from_size_pct"
    assert [request.path for request in transport.requests] == ["/fapi/v2/positionRisk", "/fapi/v1/openOrders", "/fapi/v2/account", "/fapi/v1/exchangeInfo", "/fapi/v1/premiumIndex", "/fapi/v1/order"]


def test_real_exchange_adapter_executes_real_exit_order_from_live_position() -> None:
    transport = FakeTransport(
        responses=[
            TransportResponse(
                http_status=200,
                payload=[
                    {
                        "positionAmt": "0.048",
                        "entryPrice": "3120.5",
                        "markPrice": "3122.0",
                        "leverage": "10",
                        "notional": "149.856",
                    }
                ],
            ),
            TransportResponse(http_status=200, payload=[]),
            TransportResponse(http_status=200, payload={"totalWalletBalance": "100.0"}),
            TransportResponse(http_status=200, payload={"orderId": 22222, "status": "FILLED"}),
        ]
    )
    signer = BinanceRequestSigner(
        _credentials(),
        env_getter=lambda key: {"BINANCE_API_KEY": "key123", "BINANCE_API_SECRET": "secret456"}.get(key),
        clock=lambda: 1714132800000,
    )
    adapter = BinancePerpAdapter(_credentials(), signer=signer, transport=transport)
    commands = adapter.build_commands(
        execution_plan=ExecutionPlan(
            requested_action="exit",
            effective_action="exit",
            plan_reason="quant_action_passthrough",
            place_exit_order=True,
        ),
        handoff={
            "generated_at": "2026-04-26T13:05:00",
            "action": "exit",
            "direction": "long",
        },
    )

    results = adapter.execute_commands(commands=commands, runtime_mode=RuntimeMode.REAL)

    assert results[0].target == "exit_order"
    assert results[0].status == "accepted"
    assert results[0].accepted is True
    assert results[0].details["prepared_request"]["params"]["side"] == "SELL"
    assert results[0].details["prepared_request"]["params"]["quantity"] == "0.048"
    assert results[0].details["prepared_request"]["params"]["reduceOnly"] == "true"
    assert "closePosition" not in results[0].details["prepared_request"]["params"]
    assert results[0].details["prepared_request"]["body"]["resolved_position_amt"] == "0.048"
    assert results[0].details["prepared_request"]["body"]["resolution_mode"] == "exit_quantity_from_live_position"
    assert [request.path for request in transport.requests] == ["/fapi/v2/positionRisk", "/fapi/v1/openOrders", "/fapi/v2/account", "/fapi/v1/order"]



def test_real_exchange_adapter_executes_real_exit_order_from_live_position_without_handoff_direction() -> None:
    transport = FakeTransport(
        responses=[
            TransportResponse(
                http_status=200,
                payload=[
                    {
                        "positionAmt": "0.048",
                        "entryPrice": "3120.5",
                        "markPrice": "3122.0",
                        "leverage": "10",
                        "notional": "149.856",
                    }
                ],
            ),
            TransportResponse(http_status=200, payload=[]),
            TransportResponse(http_status=200, payload={"totalWalletBalance": "100.0"}),
            TransportResponse(http_status=200, payload={"orderId": 22222, "status": "FILLED"}),
        ]
    )
    signer = BinanceRequestSigner(
        _credentials(),
        env_getter=lambda key: {"BINANCE_API_KEY": "key123", "BINANCE_API_SECRET": "secret456"}.get(key),
        clock=lambda: 1714132800000,
    )
    adapter = BinancePerpAdapter(_credentials(), signer=signer, transport=transport)
    commands = adapter.build_commands(
        execution_plan=ExecutionPlan(
            requested_action="exit",
            effective_action="exit",
            plan_reason="quant_action_passthrough",
            place_exit_order=True,
        ),
        handoff={
            "generated_at": "2026-04-26T13:05:00",
            "action": "exit",
        },
    )

    results = adapter.execute_commands(commands=commands, runtime_mode=RuntimeMode.REAL)

    assert results[0].target == "exit_order"
    assert results[0].status == "accepted"
    assert results[0].accepted is True
    assert results[0].details["prepared_request"]["params"]["side"] == "SELL"
    assert results[0].details["prepared_request"]["params"]["quantity"] == "0.048"


def test_real_exchange_adapter_blocks_reduce_order_without_explicit_quantity_contract() -> None:
    transport = FakeTransport(
        responses=[
            TransportResponse(
                http_status=200,
                payload=[
                    {
                        "positionAmt": "-0.048",
                        "entryPrice": "3120.5",
                        "markPrice": "3122.0",
                        "leverage": "10",
                        "notional": "-149.856",
                    }
                ],
            ),
            TransportResponse(http_status=200, payload=[]),
            TransportResponse(http_status=200, payload={"totalWalletBalance": "100.0"}),
        ]
    )
    signer = BinanceRequestSigner(
        _credentials(),
        env_getter=lambda key: {"BINANCE_API_KEY": "key123", "BINANCE_API_SECRET": "secret456"}.get(key),
        clock=lambda: 1714132800000,
    )
    adapter = BinancePerpAdapter(_credentials(), signer=signer, transport=transport)
    commands = adapter.build_commands(
        execution_plan=ExecutionPlan(
            requested_action="reduce",
            effective_action="reduce",
            plan_reason="quant_action_passthrough",
            place_reduce_order=True,
        ),
        handoff={
            "generated_at": "2026-04-26T13:05:30",
            "action": "reduce",
            "direction": "short",
            "reduce_conditions": ["crowding_warning"],
        },
    )

    results = adapter.execute_commands(commands=commands, runtime_mode=RuntimeMode.REAL)

    assert results[0].target == "reduce_order"
    assert results[0].status == "error"
    assert results[0].accepted is False
    assert results[0].reason == "unsafe_request_mapping"
    assert "explicit reduce quantity contract" in results[0].details["error"]
    assert [request.path for request in transport.requests] == ["/fapi/v2/positionRisk", "/fapi/v1/openOrders", "/fapi/v2/account"]


def test_real_exchange_adapter_blocks_exit_order_without_live_position() -> None:
    transport = FakeTransport(
        responses=[
            TransportResponse(http_status=200, payload=[{"positionAmt": "0", "entryPrice": "0", "markPrice": "3120.5", "leverage": "10"}]),
            TransportResponse(http_status=200, payload=[]),
        ]
    )
    signer = BinanceRequestSigner(
        _credentials(),
        env_getter=lambda key: {"BINANCE_API_KEY": "key123", "BINANCE_API_SECRET": "secret456"}.get(key),
        clock=lambda: 1714132800000,
    )
    adapter = BinancePerpAdapter(_credentials(), signer=signer, transport=transport)
    commands = adapter.build_commands(
        execution_plan=ExecutionPlan(
            requested_action="exit",
            effective_action="exit",
            plan_reason="quant_action_passthrough",
            place_exit_order=True,
        ),
        handoff={
            "generated_at": "2026-04-26T13:06:00",
            "action": "exit",
            "direction": "long",
        },
    )

    results = adapter.execute_commands(commands=commands, runtime_mode=RuntimeMode.REAL)

    assert results[0].target == "exit_order"
    assert results[0].status == "error"
    assert results[0].accepted is False
    assert results[0].reason == "unsafe_request_mapping"
    assert "existing entered position" in results[0].details["error"]
    assert [request.path for request in transport.requests] == ["/fapi/v2/positionRisk", "/fapi/v1/openOrders"]


def test_real_exchange_adapter_blocks_exit_order_with_invalid_runtime_snapshot() -> None:
    transport = FakeTransport(exc=BinanceTransportError(kind="transport_error", message="network down"))
    signer = BinanceRequestSigner(
        _credentials(),
        env_getter=lambda key: {"BINANCE_API_KEY": "key123", "BINANCE_API_SECRET": "secret456"}.get(key),
        clock=lambda: 1714132800000,
    )
    adapter = BinancePerpAdapter(_credentials(), signer=signer, transport=transport)
    commands = adapter.build_commands(
        execution_plan=ExecutionPlan(
            requested_action="exit",
            effective_action="exit",
            plan_reason="quant_action_passthrough",
            place_exit_order=True,
        ),
        handoff={
            "generated_at": "2026-04-26T13:06:30",
            "action": "exit",
            "direction": "long",
        },
    )

    results = adapter.execute_commands(commands=commands, runtime_mode=RuntimeMode.REAL)

    assert results[0].target == "exit_order"
    assert results[0].status == "error"
    assert results[0].accepted is False
    assert results[0].reason == "unsafe_request_mapping"
    assert "valid runtime snapshot" in results[0].details["error"]
    assert [request.path for request in transport.requests] == ["/fapi/v2/positionRisk"]


    transport = FakeTransport(
        responses=[
            TransportResponse(
                http_status=200,
                payload=[
                    {
                        "positionAmt": "0",
                        "entryPrice": "0",
                        "markPrice": "3120.5",
                        "leverage": "10",
                    }
                ],
            ),
            TransportResponse(http_status=200, payload=[]),
            TransportResponse(http_status=200, payload={"totalWalletBalance": "100.0"}),
            TransportResponse(
                http_status=200,
                payload={
                    "symbols": [
                        {
                            "symbol": "ETHUSDT",
                            "filters": [
                                {"filterType": "LOT_SIZE", "minQty": "0.001", "stepSize": "0.001"}
                            ],
                        }
                    ]
                },
            ),
            TransportResponse(http_status=200, payload={"symbol": "ETHUSDT", "markPrice": "3120.5"}),
            BinanceTransportError(kind="http_error", message="HTTP 400", http_status=400, payload={"msg": "bad param"}),
        ],
    )
    signer = BinanceRequestSigner(
        _credentials(),
        env_getter=lambda key: {"BINANCE_API_KEY": "key123", "BINANCE_API_SECRET": "secret456"}.get(key),
        clock=lambda: 1714132800000,
    )
    adapter = BinancePerpAdapter(_credentials(), signer=signer, transport=transport)
    commands = adapter.build_commands(
        execution_plan=ExecutionPlan(
            requested_action="entry_long",
            effective_action="entry_long",
            plan_reason="quant_action_passthrough",
            place_entry_order=True,
        ),
        handoff={
            "generated_at": "2026-04-26T13:00:00",
            "action": "entry_long",
            "direction": "long",
            "initial_stop_loss": 0.97,
            "position_size_pct": 0.15,
        },
    )
    results = adapter.execute_commands(commands=commands, runtime_mode=RuntimeMode.REAL)
    assert results[0].status == "rejected"
    assert results[0].accepted is False
    assert results[0].details["http_status"] == 400
    assert results[0].details["response_payload"] == {"msg": "bad param"}
    assert results[0].details["prepared_request"]["params"]["quantity"] == "0.048"


def test_real_exchange_adapter_maps_real_timeout() -> None:
    transport = FakeTransport(
        responses=[
            TransportResponse(
                http_status=200,
                payload=[
                    {
                        "positionAmt": "0",
                        "entryPrice": "0",
                        "markPrice": "3120.5",
                        "leverage": "10",
                    }
                ],
            ),
            TransportResponse(http_status=200, payload=[]),
            TransportResponse(http_status=200, payload={"totalWalletBalance": "100.0"}),
            TransportResponse(
                http_status=200,
                payload={
                    "symbols": [
                        {
                            "symbol": "ETHUSDT",
                            "filters": [
                                {"filterType": "LOT_SIZE", "minQty": "0.001", "stepSize": "0.001"}
                            ],
                        }
                    ]
                },
            ),
            TransportResponse(http_status=200, payload={"symbol": "ETHUSDT", "markPrice": "3120.5"}),
            BinanceTransportError(kind="timeout", message="timed out"),
        ]
    )
    signer = BinanceRequestSigner(
        _credentials(),
        env_getter=lambda key: {"BINANCE_API_KEY": "key123", "BINANCE_API_SECRET": "secret456"}.get(key),
        clock=lambda: 1714132800000,
    )
    adapter = BinancePerpAdapter(_credentials(), signer=signer, transport=transport)
    commands = adapter.build_commands(
        execution_plan=ExecutionPlan(
            requested_action="entry_long",
            effective_action="entry_long",
            plan_reason="quant_action_passthrough",
            place_entry_order=True,
        ),
        handoff={
            "generated_at": "2026-04-26T13:00:00",
            "action": "entry_long",
            "direction": "long",
            "initial_stop_loss": 0.97,
            "position_size_pct": 0.15,
        },
    )
    results = adapter.execute_commands(commands=commands, runtime_mode=RuntimeMode.REAL)
    assert results[0].status == "timeout"
    assert results[0].accepted is False
    assert results[0].simulated is False


def test_real_exchange_adapter_blocks_entry_order_without_flat_live_position() -> None:
    transport = FakeTransport(
        responses=[
            TransportResponse(
                http_status=200,
                payload=[
                    {
                        "positionAmt": "0.015",
                        "entryPrice": "3120.5",
                        "markPrice": "3125.0",
                        "leverage": "10",
                        "notional": "46.8",
                    }
                ],
            ),
            TransportResponse(http_status=200, payload=[]),
            TransportResponse(http_status=200, payload={"totalWalletBalance": "100.0"}),
        ]
    )
    signer = BinanceRequestSigner(
        _credentials(),
        env_getter=lambda key: {"BINANCE_API_KEY": "key123", "BINANCE_API_SECRET": "secret456"}.get(key),
        clock=lambda: 1714132800000,
    )
    adapter = BinancePerpAdapter(_credentials(), signer=signer, transport=transport)
    commands = adapter.build_commands(
        execution_plan=ExecutionPlan(
            requested_action="entry_long",
            effective_action="entry_long",
            plan_reason="quant_action_passthrough",
            place_entry_order=True,
        ),
        handoff={
            "generated_at": "2026-04-26T13:20:00",
            "action": "entry_long",
            "direction": "long",
            "position_size_pct": 0.15,
        },
    )
    results = adapter.execute_commands(commands=commands, runtime_mode=RuntimeMode.REAL)
    assert results[0].target == "entry_order"
    assert results[0].status == "error"
    assert results[0].accepted is False
    assert results[0].reason == "unsafe_request_mapping"
    assert "flat live position" in results[0].details["error"]
    assert [request.path for request in transport.requests] == ["/fapi/v2/positionRisk", "/fapi/v1/openOrders", "/fapi/v2/account"]


def test_real_exchange_adapter_blocks_entry_order_with_invalid_runtime_snapshot() -> None:
    transport = FakeTransport(exc=BinanceTransportError(kind="transport_error", message="network down"))
    signer = BinanceRequestSigner(
        _credentials(),
        env_getter=lambda key: {"BINANCE_API_KEY": "key123", "BINANCE_API_SECRET": "secret456"}.get(key),
        clock=lambda: 1714132800000,
    )
    adapter = BinancePerpAdapter(_credentials(), signer=signer, transport=transport)
    commands = adapter.build_commands(
        execution_plan=ExecutionPlan(
            requested_action="entry_long",
            effective_action="entry_long",
            plan_reason="quant_action_passthrough",
            place_entry_order=True,
        ),
        handoff={
            "generated_at": "2026-04-26T13:20:30",
            "action": "entry_long",
            "direction": "long",
            "position_size_pct": 0.15,
        },
    )

    results = adapter.execute_commands(commands=commands, runtime_mode=RuntimeMode.REAL)

    assert results[0].target == "entry_order"
    assert results[0].status == "error"
    assert results[0].accepted is False
    assert results[0].reason == "unsafe_request_mapping"
    assert "valid runtime snapshot" in results[0].details["error"]
    assert [request.path for request in transport.requests] == ["/fapi/v2/positionRisk"]


    transport = FakeTransport(
        responses=[
            TransportResponse(
                http_status=200,
                payload=[
                    {
                        "positionAmt": "0",
                        "entryPrice": "0",
                        "markPrice": "3120.5",
                        "leverage": "10",
                    }
                ],
            ),
            TransportResponse(http_status=200, payload=[]),
        ]
    )
    signer = BinanceRequestSigner(
        _credentials(),
        env_getter=lambda key: {"BINANCE_API_KEY": "key123", "BINANCE_API_SECRET": "secret456"}.get(key),
        clock=lambda: 1714132800000,
    )
    adapter = BinancePerpAdapter(_credentials(), signer=signer, transport=transport)
    commands = adapter.build_commands(
        execution_plan=ExecutionPlan(
            requested_action="entry_long",
            effective_action="entry_long",
            plan_reason="quant_action_passthrough",
            place_entry_order=True,
        ),
        handoff={
            "generated_at": "2026-04-26T13:21:00",
            "action": "entry_long",
            "direction": "long",
            "position_size_pct": 0.0,
        },
    )
    results = adapter.execute_commands(commands=commands, runtime_mode=RuntimeMode.REAL)
    assert results[0].target == "entry_order"
    assert results[0].status == "error"
    assert results[0].accepted is False
    assert results[0].reason == "unsafe_request_mapping"
    assert "positive position_size_pct" in results[0].details["error"]
    assert [request.path for request in transport.requests] == ["/fapi/v2/positionRisk", "/fapi/v1/openOrders"]


def test_real_exchange_adapter_blocks_entry_order_below_binance_min_qty() -> None:
    transport = FakeTransport(
        responses=[
            TransportResponse(
                http_status=200,
                payload=[
                    {
                        "positionAmt": "0",
                        "entryPrice": "0",
                        "markPrice": "3120.5",
                        "leverage": "10",
                    }
                ],
            ),
            TransportResponse(http_status=200, payload=[]),
            TransportResponse(http_status=200, payload={"totalWalletBalance": "10.0"}),
            TransportResponse(
                http_status=200,
                payload={
                    "symbols": [
                        {
                            "symbol": "ETHUSDT",
                            "filters": [
                                {"filterType": "LOT_SIZE", "minQty": "0.01", "stepSize": "0.001"}
                            ],
                        }
                    ]
                },
            ),
            TransportResponse(http_status=200, payload={"symbol": "ETHUSDT", "markPrice": "3120.5"}),
        ]
    )
    signer = BinanceRequestSigner(
        _credentials(),
        env_getter=lambda key: {"BINANCE_API_KEY": "key123", "BINANCE_API_SECRET": "secret456"}.get(key),
        clock=lambda: 1714132800000,
    )
    adapter = BinancePerpAdapter(_credentials(), signer=signer, transport=transport)
    commands = adapter.build_commands(
        execution_plan=ExecutionPlan(
            requested_action="entry_long",
            effective_action="entry_long",
            plan_reason="quant_action_passthrough",
            place_entry_order=True,
        ),
        handoff={
            "generated_at": "2026-04-26T13:22:00",
            "action": "entry_long",
            "direction": "long",
            "position_size_pct": 0.01,
        },
    )
    results = adapter.execute_commands(commands=commands, runtime_mode=RuntimeMode.REAL)
    assert results[0].target == "entry_order"
    assert results[0].status == "error"
    assert results[0].accepted is False
    assert results[0].reason == "unsafe_request_mapping"
    assert "below Binance minQty" in results[0].details["error"]
    assert [request.path for request in transport.requests] == ["/fapi/v2/positionRisk", "/fapi/v1/openOrders", "/fapi/v2/account", "/fapi/v1/exchangeInfo", "/fapi/v1/premiumIndex"]


def test_real_exchange_adapter_summarizes_recent_fills_response() -> None:
    transport = FakeTransport(
        response=TransportResponse(
            http_status=200,
            payload=[
                {"id": 11, "orderId": 101, "realizedPnl": "1.25"},
                {"id": 12, "orderId": 102, "realizedPnl": "2.50"},
            ],
        )
    )
    signer = BinanceRequestSigner(
        _credentials(),
        env_getter=lambda key: {"BINANCE_API_KEY": "key123", "BINANCE_API_SECRET": "secret456"}.get(key),
        clock=lambda: 1714132800000,
    )
    adapter = BinancePerpAdapter(_credentials(), signer=signer, transport=transport)
    commands = adapter.build_commands(
        execution_plan=ExecutionPlan(
            requested_action="reduce",
            effective_action="reduce",
            plan_reason="quant_action_passthrough",
            sync_recent_fills=True,
        ),
        handoff={
            "generated_at": "2026-04-26T13:00:00",
            "action": "reduce",
            "direction": "long",
        },
    )
    results = adapter.execute_commands(commands=commands, runtime_mode=RuntimeMode.REAL)
    assert results[0].target == "sync_recent_fills"
    assert results[0].details["response_summary"] == {
        "fill_count": 2,
        "distinct_order_count": 2,
        "latest_trade_id": "12",
        "latest_order_id": "102",
        "latest_side": "",
        "latest_price": "",
        "latest_qty": "",
        "latest_quote_qty": "",
        "latest_time": None,
        "latest_realized_pnl": "2.50",
    }


def test_real_exchange_adapter_summarizes_position_risk_response() -> None:
    transport = FakeTransport(
        responses=[
            TransportResponse(
                http_status=200,
                payload=[
                    {
                        "positionAmt": "0.015",
                        "entryPrice": "3120.5",
                        "breakEvenPrice": "3118.0",
                        "unRealizedProfit": "4.2",
                        "notional": "140.4",
                        "leverage": "10",
                    }
                ],
            ),
            TransportResponse(
                http_status=200,
                payload={"totalWalletBalance": "300.0"},
            ),
        ]
    )
    signer = BinanceRequestSigner(
        _credentials(),
        env_getter=lambda key: {"BINANCE_API_KEY": "key123", "BINANCE_API_SECRET": "secret456"}.get(key),
        clock=lambda: 1714132800000,
    )
    adapter = BinancePerpAdapter(_credentials(), signer=signer, transport=transport)
    commands = adapter.build_commands(
        execution_plan=ExecutionPlan(
            requested_action="wait",
            effective_action="wait",
            plan_reason="quant_action_passthrough",
            needs_reconciliation=True,
            recovery_action="reconcile_runtime_state",
        ),
        handoff={
            "generated_at": "2026-04-26T13:00:00",
            "action": "wait",
            "direction": "long",
        },
    )
    results = adapter.execute_commands(commands=commands, runtime_mode=RuntimeMode.REAL)
    assert results[0].target == "reconcile_position_and_orders"
    assert results[0].details["response_summary"] == {
        "position_state": "ENTERED",
        "direction": "long",
        "size_pct": 0.0468,
        "position_amt": 0.015,
        "entry_price": "3120.5",
        "break_even_price": "3118.0",
        "mark_price": None,
        "notional": "140.4",
        "unrealized_profit": "4.2",
        "leverage": 10,
        "account_equity": 300.0,
        "account_equity_source": "totalWalletBalance",
    }
    assert [request.path for request in transport.requests] == ["/fapi/v2/positionRisk", "/fapi/v2/account"]


def test_real_exchange_adapter_maps_signing_failure_to_error() -> None:
    adapter = BinancePerpAdapter(
        _credentials(),
        signer=BinanceRequestSigner(_credentials(), env_getter=lambda key: None),
        transport=FakeTransport(response=TransportResponse(http_status=200, payload={"orderId": 1})),
    )
    commands = adapter.build_commands(
        execution_plan=ExecutionPlan(
            requested_action="entry_long",
            effective_action="entry_long",
            plan_reason="quant_action_passthrough",
            place_entry_order=True,
        ),
        handoff={
            "generated_at": "2026-04-26T13:00:00",
            "action": "entry_long",
            "direction": "long",
            "initial_stop_loss": 0.97,
            "position_size_pct": 0.15,
        },
    )
    results = adapter.execute_commands(commands=commands, runtime_mode=RuntimeMode.REAL)
    assert results[0].status == "error"
    assert results[0].accepted is False
    assert "BINANCE_API_KEY" in results[0].details["error"]


def test_real_exchange_adapter_maps_protective_stop_for_live_position() -> None:
    transport = FakeTransport(
        responses=[
            TransportResponse(
                http_status=200,
                payload=[
                    {
                        "positionAmt": "0.015",
                        "entryPrice": "3120.5",
                        "breakEvenPrice": "3118.0",
                        "unRealizedProfit": "4.2",
                        "leverage": "10",
                        "notional": "46.8075",
                    }
                ],
            ),
            TransportResponse(http_status=200, payload=[]),
            TransportResponse(http_status=200, payload={"totalWalletBalance": "100.0"}),
            TransportResponse(http_status=200, payload={"orderId": 1, "status": "NEW"}),
        ]
    )
    signer = BinanceRequestSigner(
        _credentials(),
        env_getter=lambda key: {"BINANCE_API_KEY": "key123", "BINANCE_API_SECRET": "secret456"}.get(key),
        clock=lambda: 1714132800000,
    )
    adapter = BinancePerpAdapter(_credentials(), signer=signer, transport=transport)
    commands = adapter.build_commands(
        execution_plan=ExecutionPlan(
            requested_action="wait",
            effective_action="wait",
            plan_reason="quant_action_passthrough",
            maintain_protective_stop=True,
        ),
        handoff={
            "generated_at": "2026-04-26T13:00:00",
            "action": "wait",
            "direction": "long",
            "initial_stop_loss": 0.9706,
        },
    )

    results = adapter.execute_commands(commands=commands, runtime_mode=RuntimeMode.REAL)

    assert results[0].target == "maintain_protective_stop"
    assert results[0].status == "accepted"
    assert results[0].accepted is True
    assert results[0].details["prepared_request"]["params"]["side"] == "SELL"
    assert results[0].details["prepared_request"]["params"]["stopPrice"] == "3028.8"
    assert results[0].details["prepared_request"]["params"]["closePosition"] == "true"
    assert results[0].details["prepared_request"]["body"]["resolved_from_entry_price"] == 3120.5
    assert results[0].details["prepared_request"]["body"]["resolved_stop_price"] == "3028.8"
    assert [request.path for request in transport.requests] == ["/fapi/v2/positionRisk", "/fapi/v1/openOrders", "/fapi/v2/account", "/fapi/v1/order"]



def test_real_exchange_adapter_blocks_protective_stop_with_invalid_runtime_snapshot() -> None:
    transport = FakeTransport(exc=BinanceTransportError(kind="transport_error", message="network down"))
    signer = BinanceRequestSigner(
        _credentials(),
        env_getter=lambda key: {"BINANCE_API_KEY": "key123", "BINANCE_API_SECRET": "secret456"}.get(key),
        clock=lambda: 1714132800000,
    )
    adapter = BinancePerpAdapter(_credentials(), signer=signer, transport=transport)
    commands = adapter.build_commands(
        execution_plan=ExecutionPlan(
            requested_action="wait",
            effective_action="wait",
            plan_reason="quant_action_passthrough",
            maintain_protective_stop=True,
        ),
        handoff={
            "generated_at": "2026-04-26T13:00:30",
            "action": "wait",
            "direction": "long",
            "initial_stop_loss": 0.9706,
        },
    )

    results = adapter.execute_commands(commands=commands, runtime_mode=RuntimeMode.REAL)

    assert results[0].target == "maintain_protective_stop"
    assert results[0].status == "error"
    assert results[0].accepted is False
    assert results[0].reason == "unsafe_request_mapping"
    assert "valid runtime snapshot" in results[0].details["error"]
    assert [request.path for request in transport.requests] == ["/fapi/v2/positionRisk"]


    transport = FakeTransport(
        responses=[
            TransportResponse(
                http_status=200,
                payload=[
                    {
                        "positionAmt": "0.015",
                        "entryPrice": "3120.5",
                        "breakEvenPrice": "3118.0",
                        "unRealizedProfit": "4.2",
                        "leverage": "10",
                        "notional": "46.8075",
                    }
                ],
            ),
            TransportResponse(http_status=200, payload=[]),
            TransportResponse(http_status=200, payload={"totalWalletBalance": "100.0"}),
            TransportResponse(http_status=200, payload={"orderId": 1, "status": "NEW"}),
        ]
    )
    signer = BinanceRequestSigner(
        _credentials(),
        env_getter=lambda key: {"BINANCE_API_KEY": "key123", "BINANCE_API_SECRET": "secret456"}.get(key),
        clock=lambda: 1714132800000,
    )
    adapter = BinancePerpAdapter(_credentials(), signer=signer, transport=transport)
    commands = adapter.build_commands(
        execution_plan=ExecutionPlan(
            requested_action="wait",
            effective_action="wait",
            plan_reason="quant_action_passthrough",
            maintain_protective_stop=True,
        ),
        handoff={
            "generated_at": "2026-04-26T13:00:00",
            "action": "wait",
            "initial_stop_loss": 0.9706,
        },
    )

    results = adapter.execute_commands(commands=commands, runtime_mode=RuntimeMode.REAL)

    assert results[0].target == "maintain_protective_stop"
    assert results[0].status == "accepted"
    assert results[0].accepted is True
    assert results[0].details["prepared_request"]["params"]["side"] == "SELL"
    assert results[0].details["prepared_request"]["params"]["stopPrice"] == "3028.8"


def test_real_exchange_adapter_refreshes_runtime_snapshot_after_real_entry_before_protective_stop() -> None:
    transport = FakeTransport(
        responses=[
            TransportResponse(
                http_status=200,
                payload=[
                    {
                        "positionAmt": "0",
                        "entryPrice": "0",
                        "markPrice": "3120.5",
                        "leverage": "10",
                    }
                ],
            ),
            TransportResponse(http_status=200, payload=[]),
            TransportResponse(http_status=200, payload={"totalWalletBalance": "100.0"}),
            TransportResponse(
                http_status=200,
                payload={
                    "symbols": [
                        {
                            "symbol": "ETHUSDT",
                            "filters": [
                                {"filterType": "LOT_SIZE", "minQty": "0.001", "stepSize": "0.001"}
                            ],
                        }
                    ]
                },
            ),
            TransportResponse(http_status=200, payload={"symbol": "ETHUSDT", "markPrice": "3120.5"}),
            TransportResponse(http_status=200, payload={"orderId": 12345, "status": "NEW"}),
            TransportResponse(
                http_status=200,
                payload=[
                    {
                        "positionAmt": "0.048",
                        "entryPrice": "3120.5",
                        "markPrice": "3122.0",
                        "breakEvenPrice": "3120.5",
                        "unRealizedProfit": "0.0",
                        "leverage": "10",
                        "notional": "149.784",
                    }
                ],
            ),
            TransportResponse(http_status=200, payload=[]),
            TransportResponse(http_status=200, payload={"totalWalletBalance": "100.0"}),
            TransportResponse(http_status=200, payload={"orderId": 54321, "status": "NEW"}),
        ]
    )
    signer = BinanceRequestSigner(
        _credentials(),
        env_getter=lambda key: {"BINANCE_API_KEY": "key123", "BINANCE_API_SECRET": "secret456"}.get(key),
        clock=lambda: 1714132800000,
    )
    adapter = BinancePerpAdapter(_credentials(), signer=signer, transport=transport)
    commands = adapter.build_commands(
        execution_plan=ExecutionPlan(
            requested_action="entry_long",
            effective_action="entry_long",
            plan_reason="quant_action_passthrough",
            place_entry_order=True,
            maintain_protective_stop=True,
        ),
        handoff={
            "generated_at": "2026-04-26T13:40:00",
            "action": "entry_long",
            "direction": "long",
            "initial_stop_loss": 0.9706,
            "position_size_pct": 0.15,
        },
    )

    results = adapter.execute_commands(commands=commands, runtime_mode=RuntimeMode.REAL)

    assert [result.target for result in results] == ["entry_order", "maintain_protective_stop"]
    assert [result.status for result in results] == ["accepted", "accepted"]
    assert results[0].details["prepared_request"]["params"]["quantity"] == "0.048"
    assert results[1].details["prepared_request"]["params"]["stopPrice"] == "3028.8"
    assert results[1].details["prepared_request"]["body"]["resolved_from_entry_price"] == 3120.5
    assert [request.path for request in transport.requests] == [
        "/fapi/v2/positionRisk",
        "/fapi/v1/openOrders",
        "/fapi/v2/account",
        "/fapi/v1/exchangeInfo",
        "/fapi/v1/premiumIndex",
        "/fapi/v1/order",
        "/fapi/v2/positionRisk",
        "/fapi/v1/openOrders",
        "/fapi/v2/account",
        "/fapi/v1/order",
    ]


def test_real_exchange_adapter_blocks_same_batch_protective_stop_when_entry_fails() -> None:
    transport = FakeTransport(
        responses=[
            TransportResponse(
                http_status=200,
                payload=[
                    {
                        "positionAmt": "0",
                        "entryPrice": "0",
                        "markPrice": "3120.5",
                        "leverage": "10",
                    }
                ],
            ),
            TransportResponse(http_status=200, payload=[]),
            TransportResponse(http_status=200, payload={"totalWalletBalance": "100.0"}),
            TransportResponse(
                http_status=200,
                payload={
                    "symbols": [
                        {
                            "symbol": "ETHUSDT",
                            "filters": [
                                {"filterType": "LOT_SIZE", "minQty": "0.001", "stepSize": "0.001"}
                            ],
                        }
                    ]
                },
            ),
            TransportResponse(http_status=200, payload={"symbol": "ETHUSDT", "markPrice": "3120.5"}),
            BinanceTransportError(kind="http_error", message="HTTP 400", http_status=400, payload={"msg": "bad param"}),
            TransportResponse(
                http_status=200,
                payload=[
                    {
                        "positionAmt": "0",
                        "entryPrice": "0",
                        "markPrice": "3120.5",
                        "leverage": "10",
                    }
                ],
            ),
            TransportResponse(http_status=200, payload=[]),
        ]
    )
    signer = BinanceRequestSigner(
        _credentials(),
        env_getter=lambda key: {"BINANCE_API_KEY": "key123", "BINANCE_API_SECRET": "secret456"}.get(key),
        clock=lambda: 1714132800000,
    )
    adapter = BinancePerpAdapter(_credentials(), signer=signer, transport=transport)
    commands = adapter.build_commands(
        execution_plan=ExecutionPlan(
            requested_action="entry_long",
            effective_action="entry_long",
            plan_reason="quant_action_passthrough",
            place_entry_order=True,
            maintain_protective_stop=True,
        ),
        handoff={
            "generated_at": "2026-04-26T13:41:00",
            "action": "entry_long",
            "direction": "long",
            "initial_stop_loss": 0.9706,
            "position_size_pct": 0.15,
        },
    )

    results = adapter.execute_commands(commands=commands, runtime_mode=RuntimeMode.REAL)

    assert [result.target for result in results] == ["entry_order", "maintain_protective_stop"]
    assert results[0].status == "rejected"
    assert results[1].status == "error"
    assert results[1].reason == "unsafe_request_mapping"
    assert "existing entered position" in results[1].details["error"]



def test_real_exchange_adapter_blocks_unresolved_stop_semantics_in_real_mode() -> None:
    transport = FakeTransport(
        responses=[
            TransportResponse(
                http_status=200,
                payload=[
                    {
                        "positionAmt": "0.015",
                        "entryPrice": "3120.5",
                        "breakEvenPrice": "3118.0",
                        "unRealizedProfit": "4.2",
                        "leverage": "10",
                        "notional": "46.8075",
                    }
                ],
            ),
            TransportResponse(http_status=200, payload=[]),
            TransportResponse(http_status=200, payload={"totalWalletBalance": "100.0"}),
        ]
    )
    signer = BinanceRequestSigner(
        _credentials(),
        env_getter=lambda key: {"BINANCE_API_KEY": "key123", "BINANCE_API_SECRET": "secret456"}.get(key),
        clock=lambda: 1714132800000,
    )
    adapter = BinancePerpAdapter(_credentials(), signer=signer, transport=transport)
    commands = adapter.build_commands(
        execution_plan=ExecutionPlan(
            requested_action="entry_long",
            effective_action="entry_long",
            plan_reason="quant_action_passthrough",
            maintain_protective_stop=True,
        ),
        handoff={
            "generated_at": "2026-04-26T13:00:00",
            "action": "entry_long",
            "direction": "long",
            "initial_stop_loss": 0.9706,
            "breakeven_trigger": 1.01,
            "trailing_rule": "trail_with_trigger",
            "tp_ladder": [1.01, 1.02],
        },
    )

    results = adapter.execute_commands(commands=commands, runtime_mode=RuntimeMode.REAL)

    assert results[0].target == "maintain_protective_stop"
    assert results[0].status == "error"
    assert results[0].accepted is False
    assert results[0].reason == "unsafe_request_mapping"
    assert "strategy-relative" in results[0].details["error"]
    assert [request.path for request in transport.requests] == ["/fapi/v2/positionRisk", "/fapi/v1/openOrders", "/fapi/v2/account"]




def test_real_exchange_adapter_maps_breakeven_stop_to_live_entry_price_in_real_mode() -> None:
    transport = FakeTransport(
        responses=[
            TransportResponse(
                http_status=200,
                payload=[
                    {
                        "positionAmt": "0.015",
                        "entryPrice": "3120.5",
                        "breakEvenPrice": "3118.0",
                        "unRealizedProfit": "4.2",
                        "leverage": "10",
                        "notional": "46.8075",
                    }
                ],
            ),
            TransportResponse(http_status=200, payload=[]),
            TransportResponse(http_status=200, payload={"totalWalletBalance": "100.0"}),
            TransportResponse(http_status=200, payload={"orderId": 1, "status": "NEW"}),
        ]
    )
    signer = BinanceRequestSigner(
        _credentials(),
        env_getter=lambda key: {"BINANCE_API_KEY": "key123", "BINANCE_API_SECRET": "secret456"}.get(key),
        clock=lambda: 1714132800000,
    )
    adapter = BinancePerpAdapter(_credentials(), signer=signer, transport=transport)
    commands = adapter.build_commands(
        execution_plan=ExecutionPlan(
            requested_action="wait",
            effective_action="wait",
            plan_reason="quant_action_passthrough",
            advance_breakeven=True,
        ),
        handoff={
            "generated_at": "2026-04-26T13:10:00",
            "action": "wait",
            "direction": "long",
            "breakeven_trigger": 1.01,
            "trailing_rule": "trail_with_trigger",
        },
    )

    results = adapter.execute_commands(commands=commands, runtime_mode=RuntimeMode.REAL)

    assert results[0].target == "advance_breakeven_stop"
    assert results[0].status == "accepted"
    assert results[0].accepted is True
    assert results[0].details["prepared_request"]["params"]["side"] == "SELL"
    assert results[0].details["prepared_request"]["params"]["stopPrice"] == "3120.5"
    assert results[0].details["prepared_request"]["params"]["closePosition"] == "true"
    assert results[0].details["prepared_request"]["body"]["resolved_from_entry_price"] == 3120.5
    assert results[0].details["prepared_request"]["body"]["resolved_stop_price"] == "3120.5"
    assert results[0].details["prepared_request"]["body"]["resolution_mode"] == "breakeven_from_live_entry"
    assert [request.path for request in transport.requests] == ["/fapi/v2/positionRisk", "/fapi/v1/openOrders", "/fapi/v2/account", "/fapi/v1/order"]



def test_real_exchange_adapter_maps_breakeven_stop_without_handoff_direction() -> None:
    transport = FakeTransport(
        responses=[
            TransportResponse(
                http_status=200,
                payload=[
                    {
                        "positionAmt": "0.015",
                        "entryPrice": "3120.5",
                        "breakEvenPrice": "3118.0",
                        "unRealizedProfit": "4.2",
                        "leverage": "10",
                        "notional": "46.8075",
                    }
                ],
            ),
            TransportResponse(http_status=200, payload=[]),
            TransportResponse(http_status=200, payload={"totalWalletBalance": "100.0"}),
            TransportResponse(http_status=200, payload={"orderId": 1, "status": "NEW"}),
        ]
    )
    signer = BinanceRequestSigner(
        _credentials(),
        env_getter=lambda key: {"BINANCE_API_KEY": "key123", "BINANCE_API_SECRET": "secret456"}.get(key),
        clock=lambda: 1714132800000,
    )
    adapter = BinancePerpAdapter(_credentials(), signer=signer, transport=transport)
    commands = adapter.build_commands(
        execution_plan=ExecutionPlan(
            requested_action="wait",
            effective_action="wait",
            plan_reason="quant_action_passthrough",
            advance_breakeven=True,
        ),
        handoff={
            "generated_at": "2026-04-26T13:10:00",
            "action": "wait",
            "breakeven_trigger": 1.01,
            "trailing_rule": "trail_with_trigger",
        },
    )

    results = adapter.execute_commands(commands=commands, runtime_mode=RuntimeMode.REAL)

    assert results[0].target == "advance_breakeven_stop"
    assert results[0].status == "accepted"
    assert results[0].accepted is True
    assert results[0].details["prepared_request"]["params"]["side"] == "SELL"
    assert results[0].details["prepared_request"]["params"]["stopPrice"] == "3120.5"


def test_real_exchange_adapter_blocks_breakeven_stop_without_live_position_in_real_mode() -> None:
    transport = FakeTransport(
        responses=[
            TransportResponse(http_status=200, payload=[{"positionAmt": "0", "entryPrice": "0", "leverage": "10"}]),
            TransportResponse(http_status=200, payload=[]),
        ]
    )
    signer = BinanceRequestSigner(
        _credentials(),
        env_getter=lambda key: {"BINANCE_API_KEY": "key123", "BINANCE_API_SECRET": "secret456"}.get(key),
        clock=lambda: 1714132800000,
    )
    adapter = BinancePerpAdapter(_credentials(), signer=signer, transport=transport)
    commands = adapter.build_commands(
        execution_plan=ExecutionPlan(
            requested_action="wait",
            effective_action="wait",
            plan_reason="quant_action_passthrough",
            advance_breakeven=True,
        ),
        handoff={
            "generated_at": "2026-04-26T13:11:00",
            "action": "wait",
            "direction": "long",
            "breakeven_trigger": 1.01,
        },
    )

    results = adapter.execute_commands(commands=commands, runtime_mode=RuntimeMode.REAL)

    assert results[0].target == "advance_breakeven_stop"
    assert results[0].status == "error"
    assert results[0].accepted is False
    assert results[0].reason == "unsafe_request_mapping"
    assert "existing entered position" in results[0].details["error"]
    assert [request.path for request in transport.requests] == ["/fapi/v2/positionRisk", "/fapi/v1/openOrders"]


def test_real_exchange_adapter_blocks_breakeven_stop_when_direction_mismatches_live_position() -> None:
    transport = FakeTransport(
        responses=[
            TransportResponse(
                http_status=200,
                payload=[
                    {
                        "positionAmt": "0.015",
                        "entryPrice": "3120.5",
                        "leverage": "10",
                        "notional": "46.8075",
                    }
                ],
            ),
            TransportResponse(http_status=200, payload=[]),
            TransportResponse(http_status=200, payload={"totalWalletBalance": "100.0"}),
        ]
    )
    signer = BinanceRequestSigner(
        _credentials(),
        env_getter=lambda key: {"BINANCE_API_KEY": "key123", "BINANCE_API_SECRET": "secret456"}.get(key),
        clock=lambda: 1714132800000,
    )
    adapter = BinancePerpAdapter(_credentials(), signer=signer, transport=transport)
    commands = adapter.build_commands(
        execution_plan=ExecutionPlan(
            requested_action="wait",
            effective_action="wait",
            plan_reason="quant_action_passthrough",
            advance_breakeven=True,
        ),
        handoff={
            "generated_at": "2026-04-26T13:12:00",
            "action": "wait",
            "direction": "short",
            "breakeven_trigger": 1.01,
        },
    )

    results = adapter.execute_commands(commands=commands, runtime_mode=RuntimeMode.REAL)

    assert results[0].target == "advance_breakeven_stop"
    assert results[0].status == "error"
    assert results[0].accepted is False
    assert results[0].reason == "unsafe_request_mapping"
    assert "direction does not match live position direction" in results[0].details["error"]
    assert [request.path for request in transport.requests] == ["/fapi/v2/positionRisk", "/fapi/v1/openOrders", "/fapi/v2/account"]


def test_real_exchange_adapter_blocks_breakeven_stop_without_trigger_in_real_mode() -> None:
    transport = FakeTransport(
        responses=[
            TransportResponse(
                http_status=200,
                payload=[
                    {
                        "positionAmt": "0.015",
                        "entryPrice": "3120.5",
                        "leverage": "10",
                        "notional": "46.8075",
                    }
                ],
            ),
            TransportResponse(http_status=200, payload=[]),
            TransportResponse(http_status=200, payload={"totalWalletBalance": "100.0"}),
        ]
    )
    signer = BinanceRequestSigner(
        _credentials(),
        env_getter=lambda key: {"BINANCE_API_KEY": "key123", "BINANCE_API_SECRET": "secret456"}.get(key),
        clock=lambda: 1714132800000,
    )
    adapter = BinancePerpAdapter(_credentials(), signer=signer, transport=transport)
    commands = adapter.build_commands(
        execution_plan=ExecutionPlan(
            requested_action="wait",
            effective_action="wait",
            plan_reason="quant_action_passthrough",
            advance_breakeven=True,
        ),
        handoff={
            "generated_at": "2026-04-26T13:13:00",
            "action": "wait",
            "direction": "long",
        },
    )

    results = adapter.execute_commands(commands=commands, runtime_mode=RuntimeMode.REAL)

    assert results[0].target == "advance_breakeven_stop"
    assert results[0].status == "error"
    assert results[0].accepted is False
    assert results[0].reason == "unsafe_request_mapping"
    assert "requires breakeven_trigger" in results[0].details["error"]
    assert [request.path for request in transport.requests] == ["/fapi/v2/positionRisk", "/fapi/v1/openOrders", "/fapi/v2/account"]


def test_real_exchange_adapter_maps_trailing_stop_in_real_mode() -> None:
    transport = FakeTransport(
        responses=[
            TransportResponse(
                http_status=200,
                payload=[
                    {
                        "positionAmt": "0.015",
                        "entryPrice": "3120.5",
                        "leverage": "10",
                        "notional": "46.8075",
                    }
                ],
            ),
            TransportResponse(http_status=200, payload=[]),
            TransportResponse(http_status=200, payload={"totalWalletBalance": "100.0"}),
            TransportResponse(http_status=200, payload={"orderId": 1, "status": "NEW"}),
        ]
    )
    signer = BinanceRequestSigner(
        _credentials(),
        env_getter=lambda key: {"BINANCE_API_KEY": "key123", "BINANCE_API_SECRET": "secret456"}.get(key),
        clock=lambda: 1714132800000,
    )
    adapter = BinancePerpAdapter(_credentials(), signer=signer, transport=transport)
    commands = adapter.build_commands(
        execution_plan=ExecutionPlan(
            requested_action="wait",
            effective_action="wait",
            plan_reason="quant_action_passthrough",
            advance_trailing_stop=True,
        ),
        handoff={
            "generated_at": "2026-04-26T13:15:00",
            "action": "wait",
            "direction": "long",
            "trailing_rule": "trail_with_trigger",
            "trailing_activation_ratio": 1.01,
            "trailing_callback_rate_pct": 0.5,
            "tp_ladder": [1.01, 1.02],
        },
    )

    results = adapter.execute_commands(commands=commands, runtime_mode=RuntimeMode.REAL)

    assert results[0].target == "advance_trailing_stop"
    assert results[0].status == "accepted"
    assert results[0].accepted is True
    assert results[0].details["prepared_request"]["params"]["side"] == "SELL"
    assert results[0].details["prepared_request"]["params"]["type"] == "TRAILING_STOP_MARKET"
    assert results[0].details["prepared_request"]["params"]["activationPrice"] == "3151.7"
    assert results[0].details["prepared_request"]["params"]["callbackRate"] == "0.5"
    assert results[0].details["prepared_request"]["body"]["resolved_activation_price"] == "3151.7"
    assert results[0].details["prepared_request"]["body"]["resolved_callback_rate"] == "0.5"
    assert results[0].details["prepared_request"]["body"]["resolution_mode"] == "trailing_from_quant_contract"
    assert [request.path for request in transport.requests] == ["/fapi/v2/positionRisk", "/fapi/v1/openOrders", "/fapi/v2/account", "/fapi/v1/order"]



def test_real_exchange_adapter_maps_trailing_stop_without_handoff_direction() -> None:
    transport = FakeTransport(
        responses=[
            TransportResponse(
                http_status=200,
                payload=[
                    {
                        "positionAmt": "0.015",
                        "entryPrice": "3120.5",
                        "leverage": "10",
                        "notional": "46.8075",
                    }
                ],
            ),
            TransportResponse(http_status=200, payload=[]),
            TransportResponse(http_status=200, payload={"totalWalletBalance": "100.0"}),
            TransportResponse(http_status=200, payload={"orderId": 1, "status": "NEW"}),
        ]
    )
    signer = BinanceRequestSigner(
        _credentials(),
        env_getter=lambda key: {"BINANCE_API_KEY": "key123", "BINANCE_API_SECRET": "secret456"}.get(key),
        clock=lambda: 1714132800000,
    )
    adapter = BinancePerpAdapter(_credentials(), signer=signer, transport=transport)
    commands = adapter.build_commands(
        execution_plan=ExecutionPlan(
            requested_action="wait",
            effective_action="wait",
            plan_reason="quant_action_passthrough",
            advance_trailing_stop=True,
        ),
        handoff={
            "generated_at": "2026-04-26T13:15:00",
            "action": "wait",
            "trailing_rule": "trail_with_trigger",
            "trailing_activation_ratio": 1.01,
            "trailing_callback_rate_pct": 0.5,
            "tp_ladder": [1.01, 1.02],
        },
    )

    results = adapter.execute_commands(commands=commands, runtime_mode=RuntimeMode.REAL)

    assert results[0].target == "advance_trailing_stop"
    assert results[0].status == "accepted"
    assert results[0].accepted is True
    assert results[0].details["prepared_request"]["params"]["side"] == "SELL"
    assert results[0].details["prepared_request"]["params"]["activationPrice"] == "3151.7"


def test_real_exchange_adapter_blocks_trailing_stop_without_activation_ratio_in_real_mode() -> None:
    transport = FakeTransport(
        responses=[
            TransportResponse(
                http_status=200,
                payload=[
                    {
                        "positionAmt": "0.015",
                        "entryPrice": "3120.5",
                        "leverage": "10",
                        "notional": "46.8075",
                    }
                ],
            ),
            TransportResponse(http_status=200, payload=[]),
            TransportResponse(http_status=200, payload={"totalWalletBalance": "100.0"}),
        ]
    )
    signer = BinanceRequestSigner(
        _credentials(),
        env_getter=lambda key: {"BINANCE_API_KEY": "key123", "BINANCE_API_SECRET": "secret456"}.get(key),
        clock=lambda: 1714132800000,
    )
    adapter = BinancePerpAdapter(_credentials(), signer=signer, transport=transport)
    commands = adapter.build_commands(
        execution_plan=ExecutionPlan(
            requested_action="wait",
            effective_action="wait",
            plan_reason="quant_action_passthrough",
            advance_trailing_stop=True,
        ),
        handoff={
            "generated_at": "2026-04-26T13:15:00",
            "action": "wait",
            "direction": "long",
            "trailing_rule": "trail_with_trigger",
            "trailing_callback_rate_pct": 0.5,
            "tp_ladder": [1.01, 1.02],
        },
    )

    results = adapter.execute_commands(commands=commands, runtime_mode=RuntimeMode.REAL)

    assert results[0].target == "advance_trailing_stop"
    assert results[0].status == "error"
    assert results[0].accepted is False
    assert results[0].reason == "unsafe_request_mapping"
    assert "requires trailing_activation_ratio" in results[0].details["error"]
    assert [request.path for request in transport.requests] == ["/fapi/v2/positionRisk", "/fapi/v1/openOrders", "/fapi/v2/account"]


def test_real_exchange_adapter_blocks_trailing_stop_without_callback_rate_in_real_mode() -> None:
    transport = FakeTransport(
        responses=[
            TransportResponse(
                http_status=200,
                payload=[
                    {
                        "positionAmt": "0.015",
                        "entryPrice": "3120.5",
                        "leverage": "10",
                        "notional": "46.8075",
                    }
                ],
            ),
            TransportResponse(http_status=200, payload=[]),
            TransportResponse(http_status=200, payload={"totalWalletBalance": "100.0"}),
        ]
    )
    signer = BinanceRequestSigner(
        _credentials(),
        env_getter=lambda key: {"BINANCE_API_KEY": "key123", "BINANCE_API_SECRET": "secret456"}.get(key),
        clock=lambda: 1714132800000,
    )
    adapter = BinancePerpAdapter(_credentials(), signer=signer, transport=transport)
    commands = adapter.build_commands(
        execution_plan=ExecutionPlan(
            requested_action="wait",
            effective_action="wait",
            plan_reason="quant_action_passthrough",
            advance_trailing_stop=True,
        ),
        handoff={
            "generated_at": "2026-04-26T13:15:00",
            "action": "wait",
            "direction": "long",
            "trailing_rule": "trail_with_trigger",
            "trailing_activation_ratio": 1.01,
            "tp_ladder": [1.01, 1.02],
        },
    )

    results = adapter.execute_commands(commands=commands, runtime_mode=RuntimeMode.REAL)

    assert results[0].target == "advance_trailing_stop"
    assert results[0].status == "error"
    assert results[0].accepted is False
    assert results[0].reason == "unsafe_request_mapping"
    assert "requires trailing_callback_rate_pct" in results[0].details["error"]
    assert [request.path for request in transport.requests] == ["/fapi/v2/positionRisk", "/fapi/v1/openOrders", "/fapi/v2/account"]


def test_binance_perp_adapter_fetches_runtime_snapshot() -> None:
    transport = FakeTransport(
        responses=[
            TransportResponse(
                http_status=200,
                payload=[
                    {
                        "positionAmt": "0.015",
                        "entryPrice": "3120.5",
                        "breakEvenPrice": "3118.0",
                        "unRealizedProfit": "4.2",
                        "leverage": "10",
                        "notional": "46.8075",
                    }
                ],
            ),
            TransportResponse(
                http_status=200,
                payload=[
                    {
                        "orderId": 2001,
                        "type": "STOP_MARKET",
                        "status": "NEW",
                        "side": "SELL",
                        "reduceOnly": True,
                        "stopPrice": "3050.0",
                    },
                    {
                        "orderId": 2002,
                        "type": "LIMIT",
                        "status": "NEW",
                        "side": "SELL",
                        "price": "3250.0",
                    },
                ],
            ),
            TransportResponse(
                http_status=200,
                payload={
                    "totalWalletBalance": "100.0",
                },
            ),
        ]
    )
    signer = BinanceRequestSigner(
        _credentials(),
        env_getter=lambda key: {"BINANCE_API_KEY": "key123", "BINANCE_API_SECRET": "secret456"}.get(key),
        clock=lambda: 1714132800000,
    )
    adapter = BinancePerpAdapter(_credentials(), signer=signer, transport=transport)

    snapshot = adapter.fetch_runtime_snapshot()

    assert snapshot.position.position_state == "ENTERED"
    assert snapshot.position.direction == "long"
    assert snapshot.position.size_pct == 0.0468
    assert snapshot.position.entry_price == 3120.5
    assert snapshot.position.leverage == 10
    assert snapshot.protective_stop_present is True
    assert [order.order_id for order in snapshot.open_orders] == ["2001", "2002"]
    assert snapshot.open_orders[0].trigger_price == 3050.0
    assert [request.path for request in transport.requests] == ["/fapi/v2/positionRisk", "/fapi/v1/openOrders", "/fapi/v2/account"]


def test_binance_perp_adapter_keeps_zero_size_pct_when_account_equity_unavailable() -> None:
    transport = FakeTransport(
        responses=[
            TransportResponse(
                http_status=200,
                payload=[
                    {
                        "positionAmt": "0.015",
                        "entryPrice": "3120.5",
                        "leverage": "10",
                        "notional": "46.8075",
                    }
                ],
            ),
            TransportResponse(http_status=200, payload=[]),
            TransportResponse(http_status=200, payload={}),
        ]
    )
    signer = BinanceRequestSigner(
        _credentials(),
        env_getter=lambda key: {"BINANCE_API_KEY": "key123", "BINANCE_API_SECRET": "secret456"}.get(key),
        clock=lambda: 1714132800000,
    )
    adapter = BinancePerpAdapter(_credentials(), signer=signer, transport=transport)

    snapshot = adapter.fetch_runtime_snapshot()

    assert snapshot.position.position_state == "ENTERED"
    assert snapshot.position.size_pct == 0.0


def test_binance_perp_adapter_returns_empty_runtime_snapshot_on_transport_error() -> None:
    signer = BinanceRequestSigner(
        _credentials(),
        env_getter=lambda key: {"BINANCE_API_KEY": "key123", "BINANCE_API_SECRET": "secret456"}.get(key),
        clock=lambda: 1714132800000,
    )
    adapter = BinancePerpAdapter(
        _credentials(),
        signer=signer,
        transport=FakeTransport(exc=BinanceTransportError(kind="timeout", message="timed out")),
    )

    snapshot = adapter.fetch_runtime_snapshot()

    assert snapshot.position.position_state == "FLAT"
    assert snapshot.open_orders == []
    assert snapshot.protective_stop_present is False


def test_binance_perp_adapter_prepares_requests_for_supported_targets() -> None:
    adapter = BinancePerpAdapter(
        AdapterCredentials(
            venue="binance_usdt_perp",
            api_key_env="BINANCE_API_KEY",
            api_secret_env="BINANCE_API_SECRET",
            recv_window_ms=5000,
        )
    )
    commands = adapter.build_commands(
        execution_plan=ExecutionPlan(
            requested_action="reduce",
            effective_action="reduce",
            plan_reason="quant_action_passthrough",
            place_reduce_order=True,
            maintain_protective_stop=True,
            advance_breakeven=True,
            advance_trailing_stop=True,
            sync_recent_fills=True,
            needs_reconciliation=True,
            recovery_action="reconcile_runtime_state",
        ),
        handoff={
            "generated_at": "2026-04-26T13:10:00",
            "action": "reduce",
            "direction": "short",
            "reduce_conditions": ["crowding_warning"],
            "initial_stop_loss": 0.97,
            "breakeven_trigger": 1.01,
            "trailing_rule": "trail_with_trigger",
            "trailing_activation_ratio": 0.99,
            "trailing_callback_rate_pct": 0.5,
            "tp_ladder": [0.99, 0.98],
        },
    )
    requests = adapter.prepare_requests(commands=commands)
    assert [(request.method, request.path) for request in requests] == [
        ("POST", "/fapi/v1/order"),
        ("POST", "/fapi/v1/order"),
        ("POST", "/fapi/v1/order"),
        ("POST", "/fapi/v1/order"),
        ("GET", "/fapi/v1/userTrades"),
        ("GET", "/fapi/v2/positionRisk"),
    ]
    assert requests[0].params["symbol"] == "ETHUSDT"
    assert requests[0].params["side"] == "BUY"
    assert requests[0].params["newOrderRespType"] == "RESULT"
    assert requests[0].params["reduceOnly"] == "true"
    assert requests[0].params["side"] == "BUY"
    assert requests[1].params["type"] == "STOP_MARKET"
    assert requests[1].params["side"] == "BUY"
    assert requests[1].params["reduceOnly"] == "true"
    assert requests[1].body["initial_stop_loss"] == 0.97
    assert requests[1].body["direction"] == "short"
    assert requests[2].body["breakeven_trigger"] == 1.01
    assert requests[2].body["direction"] == "short"
    assert requests[3].params["type"] == "TRAILING_STOP_MARKET"
    assert requests[3].body["trailing_rule"] == "trail_with_trigger"
    assert requests[3].body["trailing_activation_ratio"] == 0.99
    assert requests[3].body["trailing_callback_rate_pct"] == 0.5
    assert requests[3].body["direction"] == "short"
    assert requests[4].params == {"symbol": "ETHUSDT", "limit": 20}
    assert requests[5].params == {"symbol": "ETHUSDT"}



def test_binance_perp_adapter_prepares_requests_with_current_position_direction_fallback() -> None:
    adapter = BinancePerpAdapter(
        AdapterCredentials(
            venue="binance_usdt_perp",
            api_key_env="BINANCE_API_KEY",
            api_secret_env="BINANCE_API_SECRET",
            recv_window_ms=5000,
        )
    )
    commands = adapter.build_commands(
        execution_plan=ExecutionPlan(
            requested_action="reduce",
            effective_action="reduce",
            plan_reason="quant_action_passthrough",
            place_reduce_order=True,
            maintain_protective_stop=True,
            advance_breakeven=True,
            advance_trailing_stop=True,
        ),
        handoff={
            "generated_at": "2026-04-26T13:10:00",
            "action": "reduce",
            "current_position_direction": "long",
            "reduce_conditions": ["crowding_warning"],
            "initial_stop_loss": 0.97,
            "breakeven_trigger": 1.01,
            "trailing_rule": "trail_with_trigger",
            "trailing_activation_ratio": 1.01,
            "trailing_callback_rate_pct": 0.5,
        },
    )
    requests = adapter.prepare_requests(commands=commands)
    assert requests[0].params["side"] == "SELL"
    assert requests[1].params["side"] == "SELL"
    assert requests[1].body["direction"] == "long"
    assert requests[2].params["side"] == "SELL"
    assert requests[2].body["direction"] == "long"
    assert requests[3].params["side"] == "SELL"
    assert requests[3].body["direction"] == "long"



def test_binance_perp_adapter_prefers_current_position_direction_over_neutral_direction() -> None:
    adapter = BinancePerpAdapter(
        AdapterCredentials(
            venue="binance_usdt_perp",
            api_key_env="BINANCE_API_KEY",
            api_secret_env="BINANCE_API_SECRET",
            recv_window_ms=5000,
        )
    )
    commands = adapter.build_commands(
        execution_plan=ExecutionPlan(
            requested_action="reduce",
            effective_action="reduce",
            plan_reason="quant_action_passthrough",
            place_reduce_order=True,
            maintain_protective_stop=True,
            advance_breakeven=True,
            advance_trailing_stop=True,
        ),
        handoff={
            "generated_at": "2026-04-26T13:11:00",
            "action": "reduce",
            "direction": "neutral",
            "current_position_direction": "long",
            "reduce_conditions": ["crowding_warning"],
            "initial_stop_loss": 0.97,
            "breakeven_trigger": 1.01,
            "trailing_rule": "trail_with_trigger",
            "trailing_activation_ratio": 1.01,
            "trailing_callback_rate_pct": 0.5,
        },
    )
    requests = adapter.prepare_requests(commands=commands)
    assert requests[0].params["side"] == "SELL"
    assert requests[1].params["side"] == "SELL"
    assert requests[1].body["direction"] == "long"
    assert requests[2].params["side"] == "SELL"
    assert requests[2].body["direction"] == "long"
    assert requests[3].params["side"] == "SELL"
    assert requests[3].body["direction"] == "long"


def test_exchange_adapter_exposes_capabilities() -> None:
    capabilities = ExchangeAdapter().get_capabilities()
    assert capabilities.supports_real_execution is False
    assert capabilities.supports_recent_fill_sync is True
    assert capabilities.supports_trailing_stop_update is True
    assert capabilities.supports_breakeven_update is True
