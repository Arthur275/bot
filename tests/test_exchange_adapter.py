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
    def __init__(self, *, response: TransportResponse | None = None, exc: Exception | None = None) -> None:
        self._response = response
        self._exc = exc
        self.requests = []

    def send(self, signed_request):
        self.requests.append(signed_request)
        if self._exc is not None:
            raise self._exc
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
            plan_reason="entry_allowed",
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
            plan_reason="reduce_allowed",
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
            plan_reason="entry_allowed",
            place_entry_order=True,
        ),
        handoff={"initial_stop_loss": 0.97},
    )
    assert isinstance(commands[0].payload, EntryOrderPayload)
    assert commands[0].payload.initial_stop_loss == 0.97


def test_exchange_adapter_uses_typed_reconciliation_payload() -> None:
    commands = ExchangeAdapter().build_commands(
        execution_plan=ExecutionPlan(
            requested_action="wait",
            effective_action="wait",
            plan_reason="recovery_reconciliation_required",
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
            plan_reason="entry_allowed",
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
            plan_reason="entry_allowed",
            place_entry_order=True,
        ),
        handoff={
            "generated_at": "2026-04-26T13:00:00",
            "action": "entry_long",
            "direction": "long",
            "initial_stop_loss": 0.97,
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


def test_real_exchange_adapter_maps_real_success() -> None:
    transport = FakeTransport(response=TransportResponse(http_status=200, payload={"orderId": 12345, "status": "NEW"}))
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
            plan_reason="entry_allowed",
            place_entry_order=True,
        ),
        handoff={
            "generated_at": "2026-04-26T13:00:00",
            "action": "entry_long",
            "direction": "long",
            "initial_stop_loss": 0.97,
        },
    )
    results = adapter.execute_commands(commands=commands, runtime_mode=RuntimeMode.REAL)
    assert results[0].status == "accepted"
    assert results[0].accepted is True
    assert results[0].simulated is False
    assert results[0].details["http_status"] == 200
    assert results[0].details["response_payload"] == {"orderId": 12345, "status": "NEW"}
    assert len(transport.requests) == 1


def test_real_exchange_adapter_maps_real_rejection() -> None:
    transport = FakeTransport(exc=BinanceTransportError(kind="http_error", message="HTTP 400", http_status=400, payload={"msg": "bad param"}))
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
            plan_reason="entry_allowed",
            place_entry_order=True,
        ),
        handoff={
            "generated_at": "2026-04-26T13:00:00",
            "action": "entry_long",
            "direction": "long",
            "initial_stop_loss": 0.97,
        },
    )
    results = adapter.execute_commands(commands=commands, runtime_mode=RuntimeMode.REAL)
    assert results[0].status == "rejected"
    assert results[0].accepted is False
    assert results[0].details["http_status"] == 400
    assert results[0].details["response_payload"] == {"msg": "bad param"}


def test_real_exchange_adapter_maps_real_timeout() -> None:
    transport = FakeTransport(exc=BinanceTransportError(kind="timeout", message="timed out"))
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
            plan_reason="entry_allowed",
            place_entry_order=True,
        ),
        handoff={
            "generated_at": "2026-04-26T13:00:00",
            "action": "entry_long",
            "direction": "long",
            "initial_stop_loss": 0.97,
        },
    )
    results = adapter.execute_commands(commands=commands, runtime_mode=RuntimeMode.REAL)
    assert results[0].status == "timeout"
    assert results[0].accepted is False
    assert results[0].simulated is False


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
            plan_reason="reduce_allowed",
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
        "latest_trade_id": "12",
        "latest_order_id": "102",
        "latest_realized_pnl": "2.50",
    }


def test_real_exchange_adapter_summarizes_position_risk_response() -> None:
    transport = FakeTransport(
        response=TransportResponse(
            http_status=200,
            payload=[
                {
                    "positionAmt": "0.015",
                    "entryPrice": "3120.5",
                    "breakEvenPrice": "3118.0",
                    "unRealizedProfit": "4.2",
                }
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
            requested_action="wait",
            effective_action="wait",
            plan_reason="recovery_reconciliation_required",
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
        "position_amt": 0.015,
        "entry_price": "3120.5",
        "break_even_price": "3118.0",
        "unrealized_profit": "4.2",
    }

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
            plan_reason="entry_allowed",
            place_entry_order=True,
        ),
        handoff={
            "generated_at": "2026-04-26T13:00:00",
            "action": "entry_long",
            "direction": "long",
            "initial_stop_loss": 0.97,
        },
    )
    results = adapter.execute_commands(commands=commands, runtime_mode=RuntimeMode.REAL)
    assert results[0].status == "error"
    assert results[0].accepted is False
    assert "BINANCE_API_KEY" in results[0].details["error"]


def test_real_exchange_adapter_falls_back_to_simulation_in_shadow_mode() -> None:
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
            requested_action="wait",
            effective_action="wait",
            plan_reason="recovery_reconciliation_required",
            needs_reconciliation=True,
        ),
        handoff={
            "generated_at": "2026-04-26T13:05:00",
            "action": "wait",
            "direction": "neutral",
        },
    )
    results = adapter.execute_commands(commands=commands, runtime_mode=RuntimeMode.SHADOW)
    assert results[0].status == "simulated"
    assert results[0].simulated is True


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
            plan_reason="reduce_allowed",
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
    assert requests[3].body["trailing_rule"] == "trail_with_trigger"
    assert requests[3].body["direction"] == "short"
    assert requests[4].params == {"symbol": "ETHUSDT", "limit": 20}
    assert requests[5].params == {"symbol": "ETHUSDT"}


def test_exchange_adapter_exposes_capabilities() -> None:
    capabilities = ExchangeAdapter().get_capabilities()
    assert capabilities.supports_real_execution is False
    assert capabilities.supports_recent_fill_sync is True
    assert capabilities.supports_trailing_stop_update is True
    assert capabilities.supports_breakeven_update is True
