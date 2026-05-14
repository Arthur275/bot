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
    TakeProfitOrderPayload,
    PositionSnapshot,
    ProtectiveStopPayload,
    RealExchangeAdapter,
    RecentFillsPayload,
    ReconciliationPayload,
    ReduceOrderPayload,
    OkxUsdtSwapAdapter,
    TrailingStopPayload,
)
from bot.okx_transport import OkxRequestConfigError, OkxRequestSigner, OkxTransportError
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
        if signed_request.path == "/fapi/v1/openAlgoOrders":
            if self._responses and self._looks_like_open_algo_response(self._responses[0]):
                response = self._responses.pop(0)
                if isinstance(response, Exception):
                    raise response
                return response
            return TransportResponse(http_status=200, payload=[])
        if self._responses:
            response = self._responses.pop(0)
            if isinstance(response, Exception):
                raise response
            return response
        assert self._response is not None
        return self._response

    @staticmethod
    def _looks_like_open_algo_response(response) -> bool:
        if isinstance(response, Exception):
            return True
        payload = getattr(response, "payload", None)
        if not isinstance(payload, list):
            return False
        return any(isinstance(item, dict) and ("algoId" in item or "algoStatus" in item or "clientAlgoId" in item) for item in payload)


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


def _okx_credentials() -> AdapterCredentials:
    return AdapterCredentials(
        venue="okx_usdt_swap",
        api_key_env="OKX_API_KEY",
        api_secret_env="OKX_API_SECRET",
        api_passphrase_env="OKX_API_PASSPHRASE",
        recv_window_ms=5000,
        timeout_sec=15.0,
        proxy_url="http://127.0.0.1:7897",
        api_base_url="https://www.okx.com",
    )


class MismatchedPreparedRequestAdapter(RealExchangeAdapter):
    def fetch_runtime_snapshot(self) -> AdapterRuntimeSnapshot:
        return AdapterRuntimeSnapshot(snapshot_valid=True)

    def prepare_requests(self, *, commands):
        return []

    def _requires_runtime_snapshot(self, command) -> bool:
        return False


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


def test_real_exchange_adapter_rejects_command_prepared_request_length_mismatch() -> None:
    adapter = MismatchedPreparedRequestAdapter(_credentials())
    commands = ExchangeAdapter().build_commands(
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

    with pytest.raises(ValueError, match="zip\\(\\) argument 2 is shorter"):
        adapter.execute_commands(commands=commands, runtime_mode=RuntimeMode.SIMULATED_REAL)

    with pytest.raises(ValueError, match="zip\\(\\) argument 2 is shorter"):
        adapter.preflight_commands(commands=commands)


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


def test_exchange_adapter_builds_take_profit_commands_from_explicit_fractions() -> None:
    commands = ExchangeAdapter().build_commands(
        execution_plan=ExecutionPlan(
            requested_action="entry_long",
            effective_action="entry_long",
            plan_reason="quant_action_passthrough",
            place_take_profit_orders=True,
        ),
        handoff={
            "generated_at": "2026-04-26T12:15:00",
            "action": "entry_long",
            "direction": "long",
            "tp_ladder": [1.01, 1.02],
            "tp_reduce_fractions": [0.5, 0.5],
        },
    )

    assert [(command.target, command.reason) for command in commands] == [
        ("take_profit_order", "take_profit_level:1"),
        ("take_profit_order", "take_profit_level:2"),
    ]
    assert isinstance(commands[0].payload, TakeProfitOrderPayload)
    assert commands[0].payload.price_ratio == 1.01
    assert commands[0].payload.reduce_fraction == 0.5
    assert commands[0].payload.level == 1
    assert commands[1].idempotency_key.startswith("take_profit_order:2:")


def test_exchange_adapter_does_not_build_ambiguous_take_profit_ladder_contract() -> None:
    commands = ExchangeAdapter().build_commands(
        execution_plan=ExecutionPlan(
            requested_action="entry_long",
            effective_action="entry_long",
            plan_reason="quant_action_passthrough",
            place_take_profit_orders=True,
        ),
        handoff={
            "generated_at": "2026-04-26T12:15:00",
            "action": "entry_long",
            "direction": "long",
            "tp_ladder": [1.01, 1.02],
            "tp_reduce_fractions": [0.5, 0.5],
            "tp_reduce_qtys": [0.01, 0.01],
        },
    )

    assert commands == []


def test_exchange_adapter_uses_typed_entry_payload() -> None:
    commands = ExchangeAdapter().build_commands(
        execution_plan=ExecutionPlan(
            requested_action="entry_long",
            effective_action="entry_long",
            plan_reason="quant_action_passthrough",
            place_entry_order=True,
            executable_size_pct=0.05,
        ),
        handoff={"initial_stop_loss": 0.97, "position_size_pct": 0.15},
    )
    assert isinstance(commands[0].payload, EntryOrderPayload)
    assert commands[0].reason == "effective_action:entry_long"
    assert commands[0].payload.initial_stop_loss == 0.97
    assert commands[0].payload.position_size_pct == 0.05


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



def test_real_capable_exchange_adapter_requires_runtime_snapshot_override() -> None:
    adapter = RealExchangeAdapter(_credentials())
    with pytest.raises(NotImplementedError):
        adapter.fetch_runtime_snapshot()


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


def test_exchange_adapter_includes_source_run_id_in_new_idempotency_keys() -> None:
    key = ExchangeAdapter()._build_idempotency_key(
        target="entry_order",
        handoff={
            "source_run_id": "eth-15m-20260418T200900Z-a1b2c3d4",
            "generated_at": "2026-04-26T12:17:30",
            "action": "entry_long",
            "direction": "long",
        },
    )

    assert key == "entry_order:eth-15m-20260418T200900Z-a1b2c3d4:2026-04-26T12:17:30:entry_long:long"


def test_exchange_adapter_uses_handoff_id_for_idempotency_when_source_run_id_missing() -> None:
    key = ExchangeAdapter()._build_idempotency_key(
        target="advance_trailing_stop",
        handoff={
            "handoff_id": "hr-eth-trailing-20260426121730",
            "generated_at": "2026-04-26T12:17:30",
            "action": "wait",
            "current_position_direction": "short",
        },
    )

    assert key == "advance_trailing_stop:hr-eth-trailing-20260426121730:2026-04-26T12:17:30:wait:short"


def test_binance_entry_side_uses_direction_for_small_probe() -> None:
    command = ExchangeAdapter().build_commands(
        execution_plan=ExecutionPlan(
            requested_action="small_probe",
            effective_action="small_probe",
            plan_reason="quant_action_passthrough",
            place_entry_order=True,
            executable_size_pct=0.012821,
        ),
        handoff={
            "generated_at": "2026-05-02T00:36:58",
            "action": "small_probe",
            "direction": "long",
            "initial_stop_loss": 0.9844,
            "position_size_pct": 0.1,
            "executable_size_pct": 0.012821,
        },
    )[0]

    request = BinancePerpAdapter(_credentials())._map_command_to_request(command)

    assert isinstance(command.payload, EntryOrderPayload)
    assert command.payload.direction == "long"
    assert request.params["side"] == "BUY"



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
    assert results[0].idempotency_key == "entry_order:2026-04-26T13:00:00:entry_long:long"
    assert results[0].client_order_id.startswith("ethbot-eo-")
    assert results[0].details["venue"] == "binance_usdt_perp"
    assert results[0].details["prepared_request"]["path"] == "/fapi/v1/order"
    assert results[0].details["prepared_request"]["params"]["quantity"] == "0.048"
    assert results[0].details["signed_request"]["headers"] == {"X-MBX-APIKEY": "key123"}
    assert [request.path for request in transport.requests] == [
        "/fapi/v2/positionRisk",
        "/fapi/v1/openOrders",
        "/fapi/v2/account",
        "/fapi/v1/exchangeInfo",
        "/fapi/v1/premiumIndex",
    ]


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
    assert results[0].idempotency_key == "entry_order:2026-04-26T13:30:00:entry_long:long"
    assert results[0].client_order_id.startswith("ethbot-eo-")
    assert results[0].details["preflight"] is True
    assert results[0].details["prepared_request"]["params"]["quantity"] == "0.048"
    assert "balance_checked" not in results[0].details
    assert "margin_checked" not in results[0].details
    assert "prepared_request" in results[0].details
    assert "signed_request" in results[0].details
    assert [request.path for request in transport.requests] == [
        "/fapi/v2/positionRisk",
        "/fapi/v1/openOrders",
        "/fapi/v2/account",
        "/fapi/v1/exchangeInfo",
        "/fapi/v1/premiumIndex",
    ]


def test_real_exchange_adapter_preflight_entry_order_uses_fresh_runtime_mark_price(monkeypatch) -> None:
    transport = FakeTransport(
        responses=[
            TransportResponse(
                http_status=200,
                payload=[
                    {
                        "positionAmt": "0",
                        "entryPrice": "0",
                        "markPrice": "3000.0",
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
            TransportResponse(http_status=200, payload={"symbol": "ETHUSDT", "markPrice": "2500.0"}),
        ]
    )
    signer = BinanceRequestSigner(
        _credentials(),
        env_getter=lambda key: {"BINANCE_API_KEY": "key123", "BINANCE_API_SECRET": "secret456"}.get(key),
        clock=lambda: 1714132800000,
    )
    monkeypatch.setattr(BinancePerpAdapter, "_mark_price_age_sec", staticmethod(lambda _: 5.0))
    adapter = BinancePerpAdapter(_credentials(), signer=signer, transport=transport)
    commands = adapter.build_commands(
        execution_plan=ExecutionPlan(
            requested_action="entry_long",
            effective_action="entry_long",
            plan_reason="quant_action_passthrough",
            place_entry_order=True,
        ),
        handoff={
            "generated_at": "2026-04-26T13:30:15",
            "action": "entry_long",
            "direction": "long",
            "position_size_pct": 0.15,
        },
    )

    results = adapter.preflight_commands(commands=commands)

    body = results[0].details["prepared_request"]["body"]
    assert results[0].status == "preflight_ready"
    assert results[0].details["prepared_request"]["params"]["quantity"] == "0.050"
    assert body["resolved_mark_price"] == "3000.0"
    assert body["mark_price_source"] == "runtime_snapshot"
    assert body["mark_price_age_sec"] == 5.0


def test_real_exchange_adapter_preflight_entry_order_rejects_stale_runtime_mark_price(monkeypatch) -> None:
    transport = FakeTransport(
        responses=[
            TransportResponse(
                http_status=200,
                payload=[
                    {
                        "positionAmt": "0",
                        "entryPrice": "0",
                        "markPrice": "3000.0",
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
            TransportResponse(http_status=200, payload={"symbol": "ETHUSDT", "markPrice": "2500.0"}),
        ]
    )
    signer = BinanceRequestSigner(
        _credentials(),
        env_getter=lambda key: {"BINANCE_API_KEY": "key123", "BINANCE_API_SECRET": "secret456"}.get(key),
        clock=lambda: 1714132800000,
    )
    monkeypatch.setattr(BinancePerpAdapter, "_mark_price_age_sec", staticmethod(lambda _: 30.0))
    adapter = BinancePerpAdapter(_credentials(), signer=signer, transport=transport)
    commands = adapter.build_commands(
        execution_plan=ExecutionPlan(
            requested_action="entry_long",
            effective_action="entry_long",
            plan_reason="quant_action_passthrough",
            place_entry_order=True,
        ),
        handoff={
            "generated_at": "2026-04-26T13:30:30",
            "action": "entry_long",
            "direction": "long",
            "position_size_pct": 0.15,
        },
    )

    results = adapter.preflight_commands(commands=commands)

    body = results[0].details["prepared_request"]["body"]
    assert results[0].status == "preflight_ready"
    assert results[0].details["prepared_request"]["params"]["quantity"] == "0.060"
    assert body["resolved_mark_price"] == "2500.0"
    assert body["mark_price_source"] == "premium_index"
    assert body["mark_price_age_sec"] == 30.0


def test_real_exchange_adapter_preflight_entry_order_uses_executable_size_contract() -> None:
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
    adapter = BinancePerpAdapter(_credentials(), signer=signer, transport=transport)
    commands = adapter.build_commands(
        execution_plan=ExecutionPlan(
            requested_action="entry_long",
            effective_action="entry_long",
            plan_reason="quant_action_passthrough",
            place_entry_order=True,
            executable_size_pct=0.05,
        ),
        handoff={
            "generated_at": "2026-04-26T13:30:30",
            "action": "entry_long",
            "direction": "long",
            "position_size_pct": 0.15,
            "execution_warnings": ["route_c_missing"],
        },
    )

    results = adapter.preflight_commands(commands=commands)

    assert results[0].status == "preflight_ready"
    assert results[0].accepted is True
    assert results[0].simulated is True
    assert results[0].details["prepared_request"]["params"]["quantity"] == "0.016"
    client_order_id = results[0].details["prepared_request"]["params"]["newClientOrderId"]
    assert results[0].idempotency_key == "entry_order:2026-04-26T13:30:30:entry_long:long"
    assert results[0].client_order_id == client_order_id
    assert client_order_id.startswith("ethbot-eo-")
    assert len(client_order_id) <= 36
    assert results[0].details["prepared_request"]["idempotency_key"] == "entry_order:2026-04-26T13:30:30:entry_long:long"
    assert results[0].details["prepared_request"]["body"]["resolved_account_equity"] == "100.0"
    assert results[0].details["prepared_request"]["body"]["resolved_leverage"] == 10
    assert results[0].details["payload"]["execution_warnings"] == ["route_c_missing"]
    assert results[0].details["signed_request"]["params"]["quantity"] == "0.016"
    assert results[0].details["signed_request"]["params"]["newClientOrderId"] == client_order_id
    assert [request.path for request in transport.requests] == [
        "/fapi/v2/positionRisk",
        "/fapi/v1/openOrders",
        "/fapi/v2/account",
        "/fapi/v1/exchangeInfo",
        "/fapi/v1/premiumIndex",
    ]


def test_real_exchange_adapter_preflight_entry_order_does_not_interpret_sizing_tier() -> None:
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
    adapter = BinancePerpAdapter(_credentials(), signer=signer, transport=transport)
    commands = adapter.build_commands(
        execution_plan=ExecutionPlan(
            requested_action="entry_long",
            effective_action="entry_long",
            plan_reason="quant_action_passthrough",
            place_entry_order=True,
            executable_size_pct=0.05,
        ),
        handoff={
            "generated_at": "2026-04-26T13:30:30",
            "action": "entry_long",
            "direction": "long",
            "position_size_pct": 0.15,
            "sizing_tier": "full",
            "sizing_bias": "constructive",
        },
    )

    results = adapter.preflight_commands(commands=commands)

    assert results[0].status == "preflight_ready"
    assert commands[0].payload.position_size_pct == 0.05
    assert results[0].details["prepared_request"]["params"]["quantity"] == "0.016"
    assert results[0].details["prepared_request"]["body"]["resolution_mode"] == "entry_quantity_from_size_pct"


def test_real_exchange_adapter_preflight_refreshes_timestamp_offset_on_recv_window_error() -> None:
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
            BinanceTransportError(
                kind="http_error",
                message="HTTP 400",
                http_status=400,
                payload={"code": -1021, "msg": "Timestamp ahead of server time"},
            ),
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
    refreshed = {"count": 0}

    def refresh() -> None:
        refreshed["count"] += 1

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
            "generated_at": "2026-04-26T13:31:00",
            "action": "entry_long",
            "direction": "long",
            "position_size_pct": 0.15,
        },
    )

    results = adapter.preflight_commands(commands=commands)

    assert refreshed["count"] == 1
    assert results[0].status == "preflight_ready"
    assert results[0].accepted is True
    assert results[0].details["prepared_request"]["params"]["quantity"] == "0.048"
    assert [request.path for request in transport.requests] == [
        "/fapi/v2/positionRisk",
        "/fapi/v1/openOrders",
        "/fapi/v2/account",
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
    assert results[0].idempotency_key == "entry_order:2026-04-26T13:00:00:entry_long:long"
    assert results[0].client_order_id.startswith("ethbot-eo-")
    assert results[0].exchange_order_id == "12345"
    assert results[0].error_kind == ""
    assert results[0].details["http_status"] == 200
    assert results[0].details["response_payload"] == {"orderId": 12345, "status": "NEW"}
    assert results[0].details["prepared_request"]["params"]["quantity"] == "0.048"
    assert results[0].details["prepared_request"]["body"]["resolution_mode"] == "entry_quantity_from_size_pct"
    assert [request.path for request in transport.requests] == ["/fapi/v2/positionRisk", "/fapi/v1/openOrders", "/fapi/v2/account", "/fapi/v1/exchangeInfo", "/fapi/v1/premiumIndex", "/fapi/v1/order"]


def test_real_exchange_adapter_blocks_real_entry_order_when_route_c_missing_warning() -> None:
    transport = FakeTransport(response=TransportResponse(http_status=200, payload={"orderId": 12345}))
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
            "generated_at": "2026-04-26T13:00:15",
            "action": "entry_long",
            "direction": "long",
            "initial_stop_loss": 0.97,
            "position_size_pct": 0.15,
            "execution_warnings": ["route_c_missing"],
        },
    )

    results = adapter.execute_commands(commands=commands, runtime_mode=RuntimeMode.REAL)

    assert results[0].status == "error"
    assert results[0].accepted is False
    assert results[0].simulated is False
    assert results[0].reason == "unsafe_request_mapping"
    assert results[0].error_kind == "unsafe_request_mapping"
    assert results[0].details["reason_code"] == "route_c_missing"
    assert "Route C/orderbook" in results[0].details["error"]
    assert transport.requests == []


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
    assert [request.path for request in transport.requests] == ["/fapi/v2/positionRisk", "/fapi/v1/openOrders", "/fapi/v2/account", "/fapi/v1/openAlgoOrders", "/fapi/v1/order"]



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
    assert [request.path for request in transport.requests] == ["/fapi/v2/positionRisk", "/fapi/v1/openOrders", "/fapi/v2/account", "/fapi/v1/openAlgoOrders"]


def test_real_exchange_adapter_blocks_exit_order_without_live_position() -> None:
    transport = FakeTransport(
        responses=[
            TransportResponse(http_status=200, payload=[{"positionAmt": "0", "entryPrice": "0", "markPrice": "3120.5", "leverage": "10"}]),
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
    assert [request.path for request in transport.requests] == ["/fapi/v2/positionRisk", "/fapi/v1/openOrders", "/fapi/v2/account"]


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
    assert [request.path for request in transport.requests] == ["/fapi/v2/positionRisk", "/fapi/v1/openOrders", "/fapi/v2/account", "/fapi/v1/openAlgoOrders"]


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
    assert [request.path for request in transport.requests] == ["/fapi/v2/positionRisk", "/fapi/v1/openOrders", "/fapi/v2/account"]


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
    assert results[0].details["prepared_request"]["params"]["triggerPrice"] == "3028.8"
    assert results[0].details["prepared_request"]["params"]["quantity"] == "0.015"
    assert results[0].details["prepared_request"]["params"]["reduceOnly"] == "true"
    assert "closePosition" not in results[0].details["prepared_request"]["params"]
    assert results[0].details["prepared_request"]["params"]["algoType"] == "CONDITIONAL"
    assert results[0].details["prepared_request"]["path"] == "/fapi/v1/algoOrder"
    assert results[0].details["prepared_request"]["body"]["resolved_from_entry_price"] == 3120.5
    assert results[0].details["prepared_request"]["body"]["resolved_stop_price"] == "3028.8"
    assert [request.path for request in transport.requests] == ["/fapi/v2/positionRisk", "/fapi/v1/openOrders", "/fapi/v2/account", "/fapi/v1/openAlgoOrders", "/fapi/v1/algoOrder"]



def test_real_exchange_adapter_preflight_protective_stop_resolves_request_without_dispatch() -> None:
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
            "generated_at": "2026-04-26T13:01:00",
            "action": "wait",
            "direction": "long",
            "initial_stop_loss": 0.9706,
        },
    )

    results = adapter.preflight_commands(commands=commands)

    assert results[0].target == "maintain_protective_stop"
    assert results[0].status == "preflight_ready"
    assert results[0].accepted is True
    assert results[0].simulated is True
    assert results[0].details["prepared_request"]["params"]["side"] == "SELL"
    assert results[0].details["prepared_request"]["params"]["triggerPrice"] == "3028.8"
    assert results[0].details["prepared_request"]["params"]["quantity"] == "0.048"
    assert "closePosition" not in results[0].details["prepared_request"]["params"]
    client_order_id = results[0].details["prepared_request"]["params"]["clientAlgoId"]
    assert client_order_id.startswith("ethbot-ps-")
    assert len(client_order_id) <= 36
    assert results[0].details["prepared_request"]["idempotency_key"] == "maintain_protective_stop:2026-04-26T13:01:00:wait:long"
    assert results[0].details["prepared_request"]["body"]["resolved_from_entry_price"] == 3120.5
    assert results[0].details["prepared_request"]["body"]["resolved_stop_price"] == "3028.8"
    assert results[0].details["signed_request"]["params"]["triggerPrice"] == "3028.8"
    assert results[0].details["signed_request"]["params"]["quantity"] == "0.048"
    assert [request.path for request in transport.requests] == ["/fapi/v2/positionRisk", "/fapi/v1/openOrders", "/fapi/v2/account", "/fapi/v1/openAlgoOrders"]


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
    assert results[0].details["prepared_request"]["params"]["triggerPrice"] == "3028.8"


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
    assert results[1].details["prepared_request"]["params"]["triggerPrice"] == "3028.8"
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
        "/fapi/v1/openAlgoOrders",
        "/fapi/v1/algoOrder",
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



def test_real_exchange_adapter_ignores_strategy_metadata_for_initial_protective_stop() -> None:
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
    assert results[0].status == "accepted"
    assert results[0].accepted is True
    assert results[0].details["prepared_request"]["params"]["side"] == "SELL"
    assert results[0].details["prepared_request"]["params"]["triggerPrice"] == "3028.8"
    assert results[0].details["prepared_request"]["body"]["resolution_mode"] == "initial_stop_from_live_entry"
    assert results[0].details["prepared_request"]["body"]["trailing_rule"] == "trail_with_trigger"
    assert results[0].details["prepared_request"]["body"]["breakeven_trigger"] == 1.01
    assert results[0].details["prepared_request"]["body"]["tp_ladder"] == [1.01, 1.02]
    assert [request.path for request in transport.requests] == ["/fapi/v2/positionRisk", "/fapi/v1/openOrders", "/fapi/v2/account", "/fapi/v1/openAlgoOrders", "/fapi/v1/algoOrder"]




def test_real_exchange_adapter_blocks_breakeven_stop_until_algo_replace_is_supported() -> None:
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
    assert results[0].status == "error"
    assert results[0].accepted is False
    assert results[0].reason == "unsafe_request_mapping"
    assert "Algo stop cancel/replace" in results[0].details["error"]
    assert results[0].details["prepared_request"]["params"]["side"] == "SELL"
    assert results[0].details["prepared_request"]["params"]["type"] == "STOP_MARKET"
    assert "stopPrice" not in results[0].details["prepared_request"]["params"]
    assert [request.path for request in transport.requests] == ["/fapi/v2/positionRisk", "/fapi/v1/openOrders", "/fapi/v2/account", "/fapi/v1/openAlgoOrders"]


def test_simulated_real_exchange_adapter_uses_real_validation_for_breakeven_stop() -> None:
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
            "generated_at": "2026-04-26T13:10:00",
            "action": "wait",
            "direction": "long",
            "breakeven_trigger": 1.01,
        },
    )

    results = adapter.execute_commands(commands=commands, runtime_mode=RuntimeMode.SIMULATED_REAL)

    assert results[0].target == "advance_breakeven_stop"
    assert results[0].status == "error"
    assert results[0].accepted is False
    assert results[0].simulated is True
    assert results[0].reason == "unsafe_request_mapping"
    assert "Algo stop cancel/replace" in results[0].details["error"]
    assert [request.path for request in transport.requests] == ["/fapi/v2/positionRisk", "/fapi/v1/openOrders", "/fapi/v2/account", "/fapi/v1/openAlgoOrders"]



def test_real_exchange_adapter_blocks_breakeven_stop_without_handoff_direction_until_algo_replace_is_supported() -> None:
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
    assert results[0].status == "error"
    assert results[0].accepted is False
    assert results[0].reason == "unsafe_request_mapping"
    assert "Algo stop cancel/replace" in results[0].details["error"]
    assert "stopPrice" not in results[0].details["prepared_request"]["params"]


def test_real_exchange_adapter_blocks_breakeven_stop_without_live_position_in_real_mode() -> None:
    transport = FakeTransport(
        responses=[
            TransportResponse(http_status=200, payload=[{"positionAmt": "0", "entryPrice": "0", "leverage": "10"}]),
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
    assert [request.path for request in transport.requests] == ["/fapi/v2/positionRisk", "/fapi/v1/openOrders", "/fapi/v2/account"]


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
    assert [request.path for request in transport.requests] == ["/fapi/v2/positionRisk", "/fapi/v1/openOrders", "/fapi/v2/account", "/fapi/v1/openAlgoOrders"]


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
    assert [request.path for request in transport.requests] == ["/fapi/v2/positionRisk", "/fapi/v1/openOrders", "/fapi/v2/account", "/fapi/v1/openAlgoOrders"]


def test_real_exchange_adapter_blocks_trailing_stop_until_algo_replace_is_supported() -> None:
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
            "trailing_callback_rate_pct": 0.5,
            "tp_ladder": [1.01, 1.02],
        },
    )

    results = adapter.execute_commands(commands=commands, runtime_mode=RuntimeMode.REAL)

    assert results[0].target == "advance_trailing_stop"
    assert results[0].status == "error"
    assert results[0].accepted is False
    assert results[0].reason == "unsafe_request_mapping"
    assert "Algo stop cancel/replace" in results[0].details["error"]
    assert results[0].details["prepared_request"]["params"]["side"] == "SELL"
    assert results[0].details["prepared_request"]["params"]["type"] == "TRAILING_STOP_MARKET"
    assert "activationPrice" not in results[0].details["prepared_request"]["params"]
    assert "callbackRate" not in results[0].details["prepared_request"]["params"]
    assert [request.path for request in transport.requests] == ["/fapi/v2/positionRisk", "/fapi/v1/openOrders", "/fapi/v2/account", "/fapi/v1/openAlgoOrders"]



def test_real_exchange_adapter_blocks_trailing_stop_without_handoff_direction_until_algo_replace_is_supported() -> None:
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
            "trailing_rule": "trail_with_trigger",
            "trailing_activation_ratio": 1.01,
            "trailing_callback_rate_pct": 0.5,
            "tp_ladder": [1.01, 1.02],
        },
    )

    results = adapter.execute_commands(commands=commands, runtime_mode=RuntimeMode.REAL)

    assert results[0].target == "advance_trailing_stop"
    assert results[0].status == "error"
    assert results[0].accepted is False
    assert results[0].reason == "unsafe_request_mapping"
    assert "Algo stop cancel/replace" in results[0].details["error"]
    assert "activationPrice" not in results[0].details["prepared_request"]["params"]


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
    assert [request.path for request in transport.requests] == ["/fapi/v2/positionRisk", "/fapi/v1/openOrders", "/fapi/v2/account", "/fapi/v1/openAlgoOrders"]


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
    assert [request.path for request in transport.requests] == ["/fapi/v2/positionRisk", "/fapi/v1/openOrders", "/fapi/v2/account", "/fapi/v1/openAlgoOrders"]


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


def test_binance_perp_adapter_detects_protective_stop_from_open_algo_orders() -> None:
    transport = FakeTransport(
        responses=[
            TransportResponse(
                http_status=200,
                payload=[
                    {
                        "positionAmt": "0.043",
                        "entryPrice": "2300.26",
                        "markPrice": "2298.99",
                        "leverage": "10",
                        "notional": "98.85657",
                    }
                ],
            ),
            TransportResponse(http_status=200, payload=[]),
            TransportResponse(http_status=200, payload={"totalWalletBalance": "11.135"}),
            TransportResponse(
                http_status=200,
                payload=[
                    {
                        "algoId": 1000001522632139,
                        "clientAlgoId": "ethbotps20260502142000",
                        "algoStatus": "NEW",
                        "orderType": "STOP_MARKET",
                        "side": "SELL",
                        "quantity": "0.043",
                        "triggerPrice": "2264.6",
                        "reduceOnly": True,
                    }
                ],
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

    assert snapshot.protective_stop_present is True
    assert [order.order_id for order in snapshot.open_orders] == ["algo:1000001522632139"]
    assert snapshot.open_orders[0].order_type == "STOP_MARKET"
    assert snapshot.open_orders[0].trigger_price == 2264.6
    assert [request.path for request in transport.requests] == [
        "/fapi/v2/positionRisk",
        "/fapi/v1/openOrders",
        "/fapi/v2/account",
        "/fapi/v1/openAlgoOrders",
    ]


def test_binance_perp_adapter_fetches_open_algo_orders_raw() -> None:
    transport = FakeTransport(
        responses=[
            TransportResponse(
                http_status=200,
                payload=[
                    {
                        "algoId": 1000001522632139,
                        "clientAlgoId": "ethbotps20260502142000",
                        "algoStatus": "NEW",
                        "orderType": "STOP_MARKET",
                        "side": "SELL",
                        "quantity": "0.043",
                        "triggerPrice": "2264.6",
                        "reduceOnly": True,
                        "closePosition": False,
                    }
                ],
            )
        ]
    )
    signer = BinanceRequestSigner(
        _credentials(),
        env_getter=lambda key: {"BINANCE_API_KEY": "key123", "BINANCE_API_SECRET": "secret456"}.get(key),
        clock=lambda: 1714132800000,
    )
    adapter = BinancePerpAdapter(_credentials(), signer=signer, transport=transport)

    payload = adapter.fetch_open_algo_orders_raw()

    assert payload == [
        {
            "algoId": 1000001522632139,
            "clientAlgoId": "ethbotps20260502142000",
            "algoStatus": "NEW",
            "orderType": "STOP_MARKET",
            "side": "SELL",
            "quantity": "0.043",
            "triggerPrice": "2264.6",
            "reduceOnly": True,
            "closePosition": False,
        }
    ]
    assert [request.path for request in transport.requests] == ["/fapi/v1/openAlgoOrders"]
    assert transport.requests[0].params["algoType"] == "CONDITIONAL"


def test_binance_perp_adapter_rejects_malformed_open_algo_orders_raw_payload() -> None:
    class MalformedTransport:
        def __init__(self) -> None:
            self.requests = []

        def send(self, signed_request):
            self.requests.append(signed_request)
            return TransportResponse(http_status=200, payload={"unexpected": []})

    transport = MalformedTransport()
    signer = BinanceRequestSigner(
        _credentials(),
        env_getter=lambda key: {"BINANCE_API_KEY": "key123", "BINANCE_API_SECRET": "secret456"}.get(key),
        clock=lambda: 1714132800000,
    )
    adapter = BinancePerpAdapter(_credentials(), signer=signer, transport=transport)

    with pytest.raises(BinanceTransportError, match="Malformed open algo orders response"):
        adapter.fetch_open_algo_orders_raw()


def test_binance_perp_adapter_refreshes_timestamp_offset_for_open_algo_orders_raw() -> None:
    transport = FakeTransport(
        responses=[
            BinanceTransportError(
                kind="http_error",
                message="HTTP 400",
                http_status=400,
                payload={"code": -1021, "msg": "Timestamp ahead of server time"},
            ),
            TransportResponse(http_status=200, payload=[]),
        ]
    )
    signer = BinanceRequestSigner(
        _credentials(),
        env_getter=lambda key: {"BINANCE_API_KEY": "key123", "BINANCE_API_SECRET": "secret456"}.get(key),
        clock=lambda: 1714132800000,
    )
    refreshed = {"count": 0}

    def refresh() -> None:
        refreshed["count"] += 1

    signer.refresh_timestamp_offset = refresh  # type: ignore[method-assign]
    adapter = BinancePerpAdapter(_credentials(), signer=signer, transport=transport)

    assert adapter.fetch_open_algo_orders_raw() == []
    assert refreshed["count"] == 1
    assert [request.path for request in transport.requests] == [
        "/fapi/v1/openAlgoOrders",
        "/fapi/v1/openAlgoOrders",
    ]


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


def test_binance_perp_adapter_float_helpers_preserve_zero_only_for_diagnostics() -> None:
    assert BinancePerpAdapter._to_optional_float_preserve_zero("0") == 0.0
    assert BinancePerpAdapter._to_optional_float_preserve_zero("-1.25") == -1.25
    assert BinancePerpAdapter._to_optional_positive_float("0") is None
    assert BinancePerpAdapter._to_optional_positive_float("-1.25") is None
    assert BinancePerpAdapter._to_optional_positive_float("3120.5") == 3120.5


def test_okx_usdt_swap_adapter_fetches_runtime_snapshot() -> None:
    transport = FakeTransport(
        responses=[
            TransportResponse(
                http_status=200,
                payload={
                    "code": "0",
                    "data": [
                        {
                            "instId": "ETH-USDT-SWAP",
                            "pos": "2",
                            "posSide": "net",
                            "avgPx": "3100.0",
                            "markPx": "3120.5",
                            "lever": "10",
                            "notionalUsd": "62.41",
                            "upl": "1.23",
                            "uplRatio": "0.041",
                        }
                    ],
                },
            ),
            TransportResponse(http_status=200, payload={"code": "0", "data": []}),
            TransportResponse(http_status=200, payload={"code": "0", "data": [{"totalEq": "100.0", "details": [{"ccy": "USDT", "eq": "100.0"}]}]}),
            TransportResponse(
                http_status=200,
                payload={
                    "code": "0",
                    "data": [
                        {
                            "instId": "ETH-USDT-SWAP",
                            "algoId": "algo-1",
                            "algoClOrdId": "ethbot-ps-existing",
                            "ordType": "conditional",
                            "state": "live",
                            "side": "sell",
                            "triggerPx": "3007.0",
                            "orderPx": "-1",
                            "closeFraction": "1",
                        }
                    ],
                },
            ),
        ]
    )
    signer = OkxRequestSigner(
        _okx_credentials(),
        env_getter=lambda key: {"OKX_API_KEY": "key", "OKX_API_SECRET": "secret", "OKX_API_PASSPHRASE": "pass"}.get(key),
    )
    adapter = OkxUsdtSwapAdapter(_okx_credentials(), signer=signer, transport=transport)

    snapshot = adapter.fetch_runtime_snapshot()

    assert snapshot.position.position_state == "ENTERED"
    assert snapshot.position.direction == "long"
    assert snapshot.position.position_amt == 2.0
    assert snapshot.position.entry_price == 3100.0
    assert snapshot.position.mark_price == 3120.5
    assert snapshot.position.leverage == 10
    assert snapshot.position.unrealized_pnl_usd == 1.23
    assert snapshot.position.unrealized_pnl_pct_on_margin == 0.041
    assert snapshot.position.price_vs_entry_pct == pytest.approx((3120.5 - 3100.0) / 3100.0)
    assert snapshot.account_equity == 100.0
    assert snapshot.account_equity_source == "totalEq"
    assert snapshot.protective_stop_present is True
    assert snapshot.open_orders[0].order_id == "algo:algo-1"
    assert snapshot.open_orders[0].order_type == "CONDITIONAL"
    assert snapshot.open_orders[0].trigger_price == 3007.0
    assert [request.path for request in transport.requests] == [
        "/api/v5/account/positions",
        "/api/v5/trade/orders-pending",
        "/api/v5/account/balance",
        "/api/v5/trade/orders-algo-pending",
    ]


def test_okx_usdt_swap_adapter_marks_snapshot_invalid_when_algo_orders_unreadable() -> None:
    transport = FakeTransport(
        responses=[
            TransportResponse(
                http_status=200,
                payload={"code": "0", "data": [{"instId": "ETH-USDT-SWAP", "pos": "0", "avgPx": "0", "markPx": "3120.5"}]},
            ),
            TransportResponse(http_status=200, payload={"code": "0", "data": []}),
            TransportResponse(http_status=200, payload={"code": "0", "data": [{"totalEq": "100.0"}]}),
            OkxTransportError(kind="timeout", message="timed out"),
        ]
    )
    signer = OkxRequestSigner(
        _okx_credentials(),
        env_getter=lambda key: {"OKX_API_KEY": "key", "OKX_API_SECRET": "secret", "OKX_API_PASSPHRASE": "pass"}.get(key),
    )
    adapter = OkxUsdtSwapAdapter(_okx_credentials(), signer=signer, transport=transport)

    snapshot = adapter.fetch_runtime_snapshot()

    assert snapshot.snapshot_valid is False
    assert snapshot.error_endpoint == "/api/v5/trade/orders-algo-pending"
    assert snapshot.error_kind == "timeout"
    assert snapshot.protective_stop_present is False


def test_okx_usdt_swap_adapter_preflight_entry_order_resolves_body_without_dispatch() -> None:
    transport = FakeTransport(
        responses=[
            TransportResponse(
                http_status=200,
                payload={"code": "0", "data": [{"instId": "ETH-USDT-SWAP", "pos": "0", "avgPx": "0", "markPx": "3120.5", "lever": "10"}]},
            ),
            TransportResponse(http_status=200, payload={"code": "0", "data": []}),
            TransportResponse(http_status=200, payload={"code": "0", "data": [{"totalEq": "100.0", "details": [{"ccy": "USDT", "eq": "100.0"}]}]}),
            TransportResponse(http_status=200, payload={"code": "0", "data": []}),
            TransportResponse(
                http_status=200,
                payload={"code": "0", "data": [{"instId": "ETH-USDT-SWAP", "lotSz": "1", "minSz": "1", "ctVal": "0.01"}]},
            ),
            TransportResponse(http_status=200, payload={"code": "0", "data": [{"instId": "ETH-USDT-SWAP", "last": "3120.5"}]}),
        ]
    )
    signer = OkxRequestSigner(
        _okx_credentials(),
        env_getter=lambda key: {"OKX_API_KEY": "key", "OKX_API_SECRET": "secret", "OKX_API_PASSPHRASE": "pass"}.get(key),
    )
    adapter = OkxUsdtSwapAdapter(_okx_credentials(), signer=signer, transport=transport)
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
            "position_size_pct": 0.1,
        },
    )

    results = adapter.preflight_commands(commands=commands)

    assert results[0].status == "preflight_ready"
    body = results[0].details["prepared_request"]["body"]
    signed_body = results[0].details["signed_request"]["body"]
    assert results[0].details["prepared_request"]["path"] == "/api/v5/trade/order"
    assert body["instId"] == "ETH-USDT-SWAP"
    assert body["tdMode"] == "cross"
    assert body["side"] == "buy"
    assert body["ordType"] == "market"
    assert body["sz"] == "3"
    assert body["resolved_account_equity"] == "100.0"
    assert body["resolved_contract_value"] == "0.01"
    assert results[0].client_order_id.startswith("ethbot-eo-")
    assert signed_body["sz"] == "3"
    assert "OK-ACCESS-PASSPHRASE" in results[0].details["signed_request"]["headers"]
    assert [request.path for request in transport.requests] == [
        "/api/v5/account/positions",
        "/api/v5/trade/orders-pending",
        "/api/v5/account/balance",
        "/api/v5/trade/orders-algo-pending",
        "/api/v5/public/instruments",
        "/api/v5/market/ticker",
    ]


def test_okx_usdt_swap_adapter_preflight_protective_stop_resolves_body() -> None:
    transport = FakeTransport(
        responses=[
            TransportResponse(
                http_status=200,
                payload={
                    "code": "0",
                    "data": [
                        {
                            "instId": "ETH-USDT-SWAP",
                            "pos": "2",
                            "posSide": "net",
                            "avgPx": "3100.0",
                            "markPx": "3120.5",
                            "lever": "10",
                            "notionalUsd": "62.41",
                        }
                    ],
                },
            ),
            TransportResponse(http_status=200, payload={"code": "0", "data": []}),
            TransportResponse(http_status=200, payload={"code": "0", "data": [{"totalEq": "100.0", "details": [{"ccy": "USDT", "eq": "100.0"}]}]}),
            TransportResponse(http_status=200, payload={"code": "0", "data": []}),
        ]
    )
    signer = OkxRequestSigner(
        _okx_credentials(),
        env_getter=lambda key: {"OKX_API_KEY": "key", "OKX_API_SECRET": "secret", "OKX_API_PASSPHRASE": "pass"}.get(key),
    )
    adapter = OkxUsdtSwapAdapter(_okx_credentials(), signer=signer, transport=transport)
    commands = adapter.build_commands(
        execution_plan=ExecutionPlan(
            requested_action="entry_long",
            effective_action="entry_long",
            plan_reason="quant_action_passthrough",
            maintain_protective_stop=True,
        ),
        handoff={
            "generated_at": "2026-04-26T13:01:00",
            "action": "entry_long",
            "direction": "long",
            "initial_stop_loss": 0.97,
            "tp_ladder": [],
        },
    )

    results = adapter.preflight_commands(commands=commands)

    assert results[0].status == "preflight_ready"
    body = results[0].details["prepared_request"]["body"]
    assert results[0].details["prepared_request"]["path"] == "/api/v5/trade/order-algo"
    assert body["instId"] == "ETH-USDT-SWAP"
    assert body["tdMode"] == "cross"
    assert body["ordType"] == "conditional"
    assert body["side"] == "sell"
    assert body["sz"] == "2"
    assert float(body["triggerPx"]) == 3007.0
    assert body["orderPx"] == "-1"
    assert body["closeFraction"] == "1"
    assert body["algoClOrdId"].startswith("ethbot-ps-")
    assert results[0].client_order_id.startswith("ethbot-ps-")


def test_okx_usdt_swap_adapter_preflight_take_profit_order_resolves_limit_reduce_only() -> None:
    transport = FakeTransport(
        responses=[
            TransportResponse(
                http_status=200,
                payload={
                    "code": "0",
                    "data": [
                        {
                            "instId": "ETH-USDT-SWAP",
                            "pos": "2",
                            "posSide": "net",
                            "avgPx": "3100.0",
                            "markPx": "3120.5",
                            "lever": "10",
                            "notionalUsd": "62.41",
                        }
                    ],
                },
            ),
            TransportResponse(http_status=200, payload={"code": "0", "data": []}),
            TransportResponse(http_status=200, payload={"code": "0", "data": [{"totalEq": "100.0"}]}),
            TransportResponse(http_status=200, payload={"code": "0", "data": []}),
        ]
    )
    signer = OkxRequestSigner(
        _okx_credentials(),
        env_getter=lambda key: {"OKX_API_KEY": "key", "OKX_API_SECRET": "secret", "OKX_API_PASSPHRASE": "pass"}.get(key),
    )
    adapter = OkxUsdtSwapAdapter(_okx_credentials(), signer=signer, transport=transport)
    commands = adapter.build_commands(
        execution_plan=ExecutionPlan(
            requested_action="entry_long",
            effective_action="entry_long",
            plan_reason="quant_action_passthrough",
            place_take_profit_orders=True,
        ),
        handoff={
            "generated_at": "2026-04-26T13:01:30",
            "action": "entry_long",
            "direction": "long",
            "tp_ladder": [1.01],
            "tp_reduce_fractions": [0.5],
        },
    )

    results = adapter.preflight_commands(commands=commands)

    assert results[0].target == "take_profit_order"
    assert results[0].status == "preflight_ready"
    body = results[0].details["prepared_request"]["body"]
    assert body["instId"] == "ETH-USDT-SWAP"
    assert body["side"] == "sell"
    assert body["ordType"] == "limit"
    assert body["reduceOnly"] == "true"
    assert body["sz"] == "1"
    assert body["px"] == "3131.0"
    assert body["resolution_mode"] == "okx_take_profit_from_live_entry"
    assert results[0].client_order_id.startswith("ethbot-tp-")


def test_okx_usdt_swap_adapter_missing_passphrase_yields_request_signing_failed() -> None:
    transport = FakeTransport(
        responses=[
            TransportResponse(
                http_status=200,
                payload={"code": "0", "data": [{"instId": "ETH-USDT-SWAP", "pos": "0", "avgPx": "0", "markPx": "3120.5", "lever": "10"}]},
            ),
            TransportResponse(http_status=200, payload={"code": "0", "data": []}),
            TransportResponse(http_status=200, payload={"code": "0", "data": [{"totalEq": "100.0"}]}),
            TransportResponse(http_status=200, payload={"code": "0", "data": []}),
            TransportResponse(http_status=200, payload={"code": "0", "data": [{"instId": "ETH-USDT-SWAP", "lotSz": "1", "minSz": "1", "ctVal": "0.01"}]}),
            TransportResponse(http_status=200, payload={"code": "0", "data": [{"instId": "ETH-USDT-SWAP", "last": "3120.5"}]}),
        ]
    )
    signer = OkxRequestSigner(
        _okx_credentials(),
        env_getter=lambda key: {"OKX_API_KEY": "key", "OKX_API_SECRET": "secret"}.get(key),
    )
    adapter = OkxUsdtSwapAdapter(_okx_credentials(), signer=signer, transport=transport)
    commands = adapter.build_commands(
        execution_plan=ExecutionPlan(
            requested_action="entry_long",
            effective_action="entry_long",
            plan_reason="quant_action_passthrough",
            place_entry_order=True,
        ),
        handoff={
            "generated_at": "2026-04-26T13:02:00",
            "action": "entry_long",
            "direction": "long",
            "position_size_pct": 0.1,
        },
    )

    results = adapter.preflight_commands(commands=commands)

    assert results[0].status == "error"
    assert results[0].accepted is False
    assert results[0].reason == "request_signing_failed"
    assert results[0].error_kind == "request_config_error"
    assert "OKX_API_PASSPHRASE" in results[0].details["error"]


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
    assert snapshot.snapshot_valid is False
    assert snapshot.error_endpoint == "/fapi/v2/positionRisk"
    assert snapshot.error_kind == "timeout"
    assert snapshot.error_message == "timed out"


def test_binance_perp_adapter_marks_open_orders_runtime_snapshot_error() -> None:
    transport = FakeTransport(
        responses=[
            TransportResponse(
                http_status=200,
                payload=[
                    {
                        "positionAmt": "0",
                        "entryPrice": "0",
                        "leverage": "10",
                        "notional": "0",
                    }
                ],
            ),
            BinanceTransportError(kind="http_error", message="HTTP 401", http_status=401, payload={"code": -2015}),
        ]
    )
    signer = BinanceRequestSigner(
        _credentials(),
        env_getter=lambda key: {"BINANCE_API_KEY": "key123", "BINANCE_API_SECRET": "secret456"}.get(key),
        clock=lambda: 1714132800000,
    )
    adapter = BinancePerpAdapter(_credentials(), signer=signer, transport=transport)

    snapshot = adapter.fetch_runtime_snapshot()

    assert snapshot.snapshot_valid is False
    assert snapshot.error_endpoint == "/fapi/v1/openOrders"
    assert snapshot.error_kind == "http_error"
    assert snapshot.error_http_status == 401
    assert snapshot.error_payload == {"code": -2015}
    assert [request.path for request in transport.requests] == ["/fapi/v2/positionRisk", "/fapi/v1/openOrders"]


def test_binance_perp_adapter_refreshes_timestamp_offset_for_open_orders_snapshot() -> None:
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
            BinanceTransportError(
                kind="http_error",
                message="HTTP 400",
                http_status=400,
                payload={"code": -1021, "msg": "Timestamp ahead of server time"},
            ),
            TransportResponse(http_status=200, payload=[]),
            TransportResponse(http_status=200, payload={"totalWalletBalance": "11.0"}),
        ]
    )
    signer = BinanceRequestSigner(
        _credentials(),
        env_getter=lambda key: {"BINANCE_API_KEY": "key123", "BINANCE_API_SECRET": "secret456"}.get(key),
        clock=lambda: 1714132800000,
    )
    refreshed = {"count": 0}

    def refresh() -> None:
        refreshed["count"] += 1

    signer.refresh_timestamp_offset = refresh  # type: ignore[method-assign]
    adapter = BinancePerpAdapter(_credentials(), signer=signer, transport=transport)

    snapshot = adapter.fetch_runtime_snapshot()

    assert snapshot.snapshot_valid is True
    assert refreshed["count"] == 1
    assert [request.path for request in transport.requests] == [
        "/fapi/v2/positionRisk",
        "/fapi/v1/openOrders",
        "/fapi/v1/openOrders",
        "/fapi/v2/account",
    ]


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
        ("POST", "/fapi/v1/algoOrder"),
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
    assert capabilities.supports_take_profit_orders is True


def test_real_exchange_adapter_capabilities_match_blocked_post_entry_features() -> None:
    capabilities = RealExchangeAdapter(_credentials()).get_capabilities()
    assert capabilities.supports_real_execution is True
    assert capabilities.supports_recent_fill_sync is True
    assert capabilities.supports_trailing_stop_update is False
    assert capabilities.supports_breakeven_update is False
    assert capabilities.supports_take_profit_orders is True
