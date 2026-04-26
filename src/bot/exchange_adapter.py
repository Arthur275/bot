from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from .binance_transport import BinanceRequestConfigError, BinanceRequestSigner, BinanceTransport, BinanceTransportError
from .config import RuntimeMode
from .position_manager import ExecutionPlan


class PositionSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    position_state: str = "FLAT"
    direction: str = "neutral"
    size_pct: float = 0.0
    entry_price: float | None = None
    leverage: int | None = None


class OrderSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    order_id: str = ""
    order_type: str
    status: str = "open"
    side: str = ""
    reduce_only: bool = False
    price: float | None = None
    trigger_price: float | None = None


class AdapterRuntimeSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fetched_at: datetime | None = None
    position: PositionSnapshot = Field(default_factory=PositionSnapshot)
    open_orders: list[OrderSnapshot] = Field(default_factory=list)
    protective_stop_present: bool = False


class ReconciliationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    in_sync: bool = True
    protective_stop_present: bool = False
    needs_position_sync: bool = False
    needs_order_sync: bool = False
    reason_codes: list[str] = Field(default_factory=list)


class ExchangeAdapterProtocol(Protocol):
    def plan_actions(self, *, execution_plan: ExecutionPlan, handoff: dict[str, Any] | None) -> list["AdapterAction"]: ...

    def build_commands(self, *, execution_plan: ExecutionPlan, handoff: dict[str, Any] | None) -> list["ExecutionCommand"]: ...

    def execute_commands(self, *, commands: list["ExecutionCommand"], runtime_mode: RuntimeMode) -> list["CommandExecutionResult"]: ...

    def fetch_runtime_snapshot(self) -> AdapterRuntimeSnapshot: ...

    def assess_reconciliation(
        self,
        *,
        runtime_snapshot: AdapterRuntimeSnapshot,
        expected_position_state: str,
        expected_direction: str,
        expected_size_pct: float,
    ) -> ReconciliationResult: ...

    def get_capabilities(self) -> "AdapterCapabilities": ...


class AdapterAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_type: str
    accepted: bool = True
    reason: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)


class EntryOrderPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: str
    initial_stop_loss: float | None = None


class ReduceOrderPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: str
    direction: str = ""
    reduce_conditions: list[str] = Field(default_factory=list)


class ExitOrderPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: str
    direction: str = ""


class ProtectiveStopPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    direction: str = ""
    initial_stop_loss: float | None = None
    breakeven_trigger: float | None = None
    trailing_rule: str = ""
    tp_ladder: list[float] = Field(default_factory=list)


class ReconciliationPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recovery_action: str = ""


class BreakevenPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    direction: str = ""
    breakeven_trigger: float | None = None
    trailing_rule: str = ""


class TrailingStopPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    direction: str = ""
    trailing_rule: str = ""
    tp_ladder: list[float] = Field(default_factory=list)


class RecentFillsPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: str = "sync_recent_fills"


class ExecutionCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command_type: str
    operation: str
    target: str
    idempotency_key: str = ""
    reason: str = ""
    payload: EntryOrderPayload | ReduceOrderPayload | ExitOrderPayload | ProtectiveStopPayload | ReconciliationPayload | BreakevenPayload | TrailingStopPayload | RecentFillsPayload


class CommandExecutionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: str
    status: str
    accepted: bool = True
    simulated: bool = True
    reason: str = ""
    details: dict[str, Any] = Field(default_factory=dict)


class AdapterCredentials(BaseModel):
    model_config = ConfigDict(extra="forbid")

    venue: str
    api_key_env: str
    api_secret_env: str
    recv_window_ms: int = Field(default=5000, gt=0)
    timeout_sec: float = Field(default=15.0, gt=0.0)
    proxy_url: str | None = None
    api_base_url: str = "https://fapi.binance.com"


class AdapterCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid")

    supports_real_execution: bool = False
    supports_recent_fill_sync: bool = False
    supports_trailing_stop_update: bool = False
    supports_breakeven_update: bool = False


class PreparedAdapterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    method: str
    path: str
    requires_auth: bool = True
    params: dict[str, Any] = Field(default_factory=dict)
    body: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str = ""


class BaseExchangeAdapter:
    def plan_actions(self, *, execution_plan: ExecutionPlan, handoff: dict[str, Any] | None) -> list[AdapterAction]:
        actions: list[AdapterAction] = []
        for command in self.build_commands(execution_plan=execution_plan, handoff=handoff):
            actions.append(
                AdapterAction(
                    action_type=command.target,
                    reason=command.reason,
                    payload={
                        "command_type": command.command_type,
                        "operation": command.operation,
                        "idempotency_key": command.idempotency_key,
                        **command.payload.model_dump(mode="json"),
                    },
                )
            )
        return actions

    def build_commands(self, *, execution_plan: ExecutionPlan, handoff: dict[str, Any] | None) -> list[ExecutionCommand]:
        commands: list[ExecutionCommand] = []
        if execution_plan.place_entry_order:
            commands.append(
                ExecutionCommand(
                    command_type="order",
                    operation="place",
                    target="entry_order",
                    idempotency_key=self._build_idempotency_key(target="entry_order", handoff=handoff),
                    reason=execution_plan.plan_reason,
                    payload=EntryOrderPayload(
                        action=execution_plan.effective_action,
                        initial_stop_loss=(handoff or {}).get("initial_stop_loss"),
                    ),
                )
            )
        if execution_plan.place_reduce_order:
            commands.append(
                ExecutionCommand(
                    command_type="order",
                    operation="place",
                    target="reduce_order",
                    idempotency_key=self._build_idempotency_key(target="reduce_order", handoff=handoff),
                    reason=execution_plan.plan_reason,
                    payload=ReduceOrderPayload(
                        action=execution_plan.effective_action,
                        direction=str((handoff or {}).get("direction") or ""),
                        reduce_conditions=list((handoff or {}).get("reduce_conditions", [])),
                    ),
                )
            )
        if execution_plan.place_exit_order:
            commands.append(
                ExecutionCommand(
                    command_type="order",
                    operation="place",
                    target="exit_order",
                    idempotency_key=self._build_idempotency_key(target="exit_order", handoff=handoff),
                    reason=execution_plan.plan_reason,
                    payload=ExitOrderPayload(
                        action=execution_plan.effective_action,
                        direction=str((handoff or {}).get("direction") or ""),
                    ),
                )
            )
        if execution_plan.maintain_protective_stop:
            commands.append(
                ExecutionCommand(
                    command_type="order",
                    operation="upsert",
                    target="maintain_protective_stop",
                    idempotency_key=self._build_idempotency_key(target="maintain_protective_stop", handoff=handoff),
                    reason="protective_stop_required",
                    payload=ProtectiveStopPayload(
                        direction=str((handoff or {}).get("direction") or ""),
                        initial_stop_loss=(handoff or {}).get("initial_stop_loss"),
                        breakeven_trigger=(handoff or {}).get("breakeven_trigger"),
                        trailing_rule=(handoff or {}).get("trailing_rule") or "",
                        tp_ladder=list((handoff or {}).get("tp_ladder", [])),
                    ),
                )
            )
        if execution_plan.advance_breakeven:
            commands.append(
                ExecutionCommand(
                    command_type="risk",
                    operation="tighten",
                    target="advance_breakeven_stop",
                    idempotency_key=self._build_idempotency_key(target="advance_breakeven_stop", handoff=handoff),
                    reason="breakeven_ready",
                    payload=BreakevenPayload(
                        direction=str((handoff or {}).get("direction") or ""),
                        breakeven_trigger=(handoff or {}).get("breakeven_trigger"),
                        trailing_rule=(handoff or {}).get("trailing_rule") or "",
                    ),
                )
            )
        if execution_plan.advance_trailing_stop:
            commands.append(
                ExecutionCommand(
                    command_type="risk",
                    operation="tighten",
                    target="advance_trailing_stop",
                    idempotency_key=self._build_idempotency_key(target="advance_trailing_stop", handoff=handoff),
                    reason="trailing_ready",
                    payload=TrailingStopPayload(
                        direction=str((handoff or {}).get("direction") or ""),
                        trailing_rule=(handoff or {}).get("trailing_rule") or "",
                        tp_ladder=list((handoff or {}).get("tp_ladder", [])),
                    ),
                )
            )
        if execution_plan.sync_recent_fills:
            commands.append(
                ExecutionCommand(
                    command_type="sync",
                    operation="query",
                    target="sync_recent_fills",
                    idempotency_key=self._build_idempotency_key(target="sync_recent_fills", handoff=handoff),
                    reason="recent_fill_sync_required",
                    payload=RecentFillsPayload(),
                )
            )
        if execution_plan.needs_reconciliation:
            commands.append(
                ExecutionCommand(
                    command_type="sync",
                    operation="query",
                    target="reconcile_position_and_orders",
                    idempotency_key=self._build_idempotency_key(target="reconcile_position_and_orders", handoff=handoff),
                    reason="reconciliation_required",
                    payload=ReconciliationPayload(recovery_action=execution_plan.recovery_action),
                )
            )
        return commands

    def fetch_runtime_snapshot(self) -> AdapterRuntimeSnapshot:
        return AdapterRuntimeSnapshot(fetched_at=datetime.now().replace(microsecond=0))

    def assess_reconciliation(
        self,
        *,
        runtime_snapshot: AdapterRuntimeSnapshot,
        expected_position_state: str,
        expected_direction: str,
        expected_size_pct: float,
    ) -> ReconciliationResult:
        reason_codes: list[str] = []
        needs_position_sync = False
        needs_order_sync = False

        position = runtime_snapshot.position
        if position.position_state != expected_position_state:
            needs_position_sync = True
            reason_codes.append("position_state_mismatch")
        if position.direction != expected_direction:
            needs_position_sync = True
            reason_codes.append("position_direction_mismatch")
        if abs(float(position.size_pct) - float(expected_size_pct)) > 1e-9:
            needs_position_sync = True
            reason_codes.append("position_size_mismatch")
        if expected_size_pct > 0.0 and not runtime_snapshot.protective_stop_present:
            needs_order_sync = True
            reason_codes.append("protective_stop_missing")

        return ReconciliationResult(
            in_sync=not (needs_position_sync or needs_order_sync),
            protective_stop_present=runtime_snapshot.protective_stop_present,
            needs_position_sync=needs_position_sync,
            needs_order_sync=needs_order_sync,
            reason_codes=reason_codes,
        )

    def get_capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities()

    @staticmethod
    def _build_idempotency_key(*, target: str, handoff: dict[str, Any] | None) -> str:
        generated_at = str((handoff or {}).get("generated_at") or "")
        action = str((handoff or {}).get("action") or "wait")
        direction = str((handoff or {}).get("direction") or "neutral")
        return f"{target}:{generated_at}:{action}:{direction}"


class ExchangeAdapter(BaseExchangeAdapter):
    def execute_commands(
        self,
        *,
        commands: list[ExecutionCommand],
        runtime_mode: RuntimeMode = RuntimeMode.SHADOW,
    ) -> list[CommandExecutionResult]:
        simulated = runtime_mode != RuntimeMode.REAL
        status = "simulated" if simulated else "accepted"
        results: list[CommandExecutionResult] = []
        for command in commands:
            results.append(
                CommandExecutionResult(
                    target=command.target,
                    status=status,
                    accepted=True,
                    simulated=simulated,
                    reason=command.reason,
                    details={
                        "command_type": command.command_type,
                        "operation": command.operation,
                        "payload": command.payload.model_dump(mode="json"),
                        "idempotency_key": command.idempotency_key,
                        "runtime_mode": runtime_mode.value,
                    },
                )
            )
        return results

    def get_capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            supports_real_execution=False,
            supports_recent_fill_sync=True,
            supports_trailing_stop_update=True,
            supports_breakeven_update=True,
        )


class RealExchangeAdapter(BaseExchangeAdapter):
    def __init__(
        self,
        credentials: AdapterCredentials,
        *,
        signer: BinanceRequestSigner | None = None,
        transport: BinanceTransport | None = None,
    ) -> None:
        self._credentials = credentials
        self._signer = signer or BinanceRequestSigner(credentials)
        self._transport = transport or BinanceTransport(credentials)

    @property
    def credentials(self) -> AdapterCredentials:
        return self._credentials

    def prepare_requests(self, *, commands: list[ExecutionCommand]) -> list[PreparedAdapterRequest]:
        raise NotImplementedError

    def execute_commands(
        self,
        *,
        commands: list[ExecutionCommand],
        runtime_mode: RuntimeMode = RuntimeMode.REAL,
    ) -> list[CommandExecutionResult]:
        if runtime_mode == RuntimeMode.SHADOW:
            return ExchangeAdapter().execute_commands(commands=commands, runtime_mode=runtime_mode)
        results: list[CommandExecutionResult] = []
        prepared_requests = self.prepare_requests(commands=commands)
        for command, prepared in zip(commands, prepared_requests, strict=False):
            try:
                signed_request = self._signer.sign(prepared)
            except BinanceRequestConfigError as exc:
                results.append(
                    self._build_execution_result(
                        command=command,
                        runtime_mode=runtime_mode,
                        prepared=prepared,
                        status="error",
                        accepted=False,
                        simulated=runtime_mode != RuntimeMode.REAL,
                        reason="request_signing_failed",
                        extra_details={"error": str(exc)},
                    )
                )
                continue

            if runtime_mode == RuntimeMode.SIMULATED_REAL:
                results.append(
                    self._build_execution_result(
                        command=command,
                        runtime_mode=runtime_mode,
                        prepared=prepared,
                        status="simulated",
                        accepted=True,
                        simulated=True,
                        reason=command.reason,
                        extra_details={
                            "signed_request": signed_request.model_dump(mode="json"),
                        },
                    )
                )
                continue

            try:
                response = self._transport.send(signed_request)
            except BinanceTransportError as exc:
                status = "timeout" if exc.kind == "timeout" else "rejected" if exc.kind == "http_error" else "error"
                reason = "transport_timeout" if exc.kind == "timeout" else "exchange_rejected" if exc.kind == "http_error" else "transport_error"
                results.append(
                    self._build_execution_result(
                        command=command,
                        runtime_mode=runtime_mode,
                        prepared=prepared,
                        status=status,
                        accepted=False,
                        simulated=False,
                        reason=reason,
                        extra_details={
                            "signed_request": signed_request.model_dump(mode="json"),
                            "http_status": exc.http_status,
                            "response_payload": exc.payload,
                            "error": str(exc),
                        },
                    )
                )
                continue

            results.append(
                self._build_execution_result(
                    command=command,
                    runtime_mode=runtime_mode,
                    prepared=prepared,
                    status="accepted",
                    accepted=True,
                    simulated=False,
                    reason=command.reason,
                    extra_details={
                        "signed_request": signed_request.model_dump(mode="json"),
                        "http_status": response.http_status,
                        "response_payload": response.payload,
                        "response_summary": self._summarize_response_payload(command=command, payload=response.payload),
                    },
                )
            )
        return results

    def _build_execution_result(
        self,
        *,
        command: ExecutionCommand,
        runtime_mode: RuntimeMode,
        prepared: PreparedAdapterRequest,
        status: str,
        accepted: bool,
        simulated: bool,
        reason: str,
        extra_details: dict[str, Any] | None = None,
    ) -> CommandExecutionResult:
        details = {
            "command_type": command.command_type,
            "operation": command.operation,
            "payload": command.payload.model_dump(mode="json"),
            "idempotency_key": command.idempotency_key,
            "runtime_mode": runtime_mode.value,
            "venue": self._credentials.venue,
            "prepared_request": prepared.model_dump(mode="json"),
        }
        if extra_details:
            details.update(extra_details)
        return CommandExecutionResult(
            target=command.target,
            status=status,
            accepted=accepted,
            simulated=simulated,
            reason=reason,
            details=details,
        )

    @staticmethod
    def _summarize_response_payload(*, command: ExecutionCommand, payload: Any) -> dict[str, Any]:
        if command.target == "sync_recent_fills" and isinstance(payload, list):
            latest = payload[-1] if payload else {}
            return {
                "fill_count": len(payload),
                "latest_trade_id": str((latest or {}).get("id") or ""),
                "latest_order_id": str((latest or {}).get("orderId") or ""),
                "latest_realized_pnl": str((latest or {}).get("realizedPnl") or ""),
            }
        if command.target == "reconcile_position_and_orders" and isinstance(payload, list):
            current = payload[0] if payload else {}
            amt_raw = (current or {}).get("positionAmt")
            try:
                position_amt = float(amt_raw or 0.0)
            except (TypeError, ValueError):
                position_amt = 0.0
            direction = "neutral"
            if position_amt > 0:
                direction = "long"
            elif position_amt < 0:
                direction = "short"
            return {
                "position_state": "ENTERED" if position_amt != 0.0 else "FLAT",
                "direction": direction,
                "position_amt": position_amt,
                "entry_price": (current or {}).get("entryPrice"),
                "break_even_price": (current or {}).get("breakEvenPrice"),
                "unrealized_profit": (current or {}).get("unRealizedProfit"),
            }
        if isinstance(payload, dict):
            summary: dict[str, Any] = {}
            for key in ("orderId", "status", "symbol", "side", "type", "clientOrderId"):
                if key in payload:
                    summary[key] = payload.get(key)
            return summary
        return {}

    def get_capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            supports_real_execution=True,
            supports_recent_fill_sync=True,
            supports_trailing_stop_update=True,
            supports_breakeven_update=True,
        )


class BinancePerpAdapter(RealExchangeAdapter):
    def prepare_requests(self, *, commands: list[ExecutionCommand]) -> list[PreparedAdapterRequest]:
        requests: list[PreparedAdapterRequest] = []
        for command in commands:
            requests.append(self._map_command_to_request(command))
        return requests

    def _map_command_to_request(self, command: ExecutionCommand) -> PreparedAdapterRequest:
        if command.target == "entry_order":
            side = "BUY" if getattr(command.payload, "action", "") == "entry_long" else "SELL"
            return PreparedAdapterRequest(
                method="POST",
                path="/fapi/v1/order",
                params={
                    "symbol": "ETHUSDT",
                    "side": side,
                    "type": "MARKET",
                    "newClientOrderId": command.idempotency_key,
                    "newOrderRespType": "RESULT",
                },
                idempotency_key=command.idempotency_key,
            )
        if command.target == "reduce_order":
            side = "SELL" if getattr(command.payload, "direction", "") == "long" else "BUY"
            return PreparedAdapterRequest(
                method="POST",
                path="/fapi/v1/order",
                params={
                    "symbol": "ETHUSDT",
                    "side": side,
                    "reduceOnly": "true",
                    "type": "MARKET",
                    "newClientOrderId": command.idempotency_key,
                    "newOrderRespType": "RESULT",
                },
                idempotency_key=command.idempotency_key,
            )
        if command.target == "exit_order":
            side = "SELL" if getattr(command.payload, "direction", "") == "long" else "BUY"
            return PreparedAdapterRequest(
                method="POST",
                path="/fapi/v1/order",
                params={
                    "symbol": "ETHUSDT",
                    "side": side,
                    "reduceOnly": "true",
                    "closePosition": "true",
                    "type": "MARKET",
                    "newClientOrderId": command.idempotency_key,
                    "newOrderRespType": "RESULT",
                },
                idempotency_key=command.idempotency_key,
            )
        if command.target == "maintain_protective_stop":
            side = "SELL" if getattr(command.payload, "direction", "") == "long" else "BUY"
            return PreparedAdapterRequest(
                method="POST",
                path="/fapi/v1/order",
                params={
                    "symbol": "ETHUSDT",
                    "side": side,
                    "type": "STOP_MARKET",
                    "workingType": "MARK_PRICE",
                    "reduceOnly": "true",
                    "newClientOrderId": command.idempotency_key,
                },
                body=command.payload.model_dump(mode="json"),
                idempotency_key=command.idempotency_key,
            )
        if command.target in {"advance_breakeven_stop", "advance_trailing_stop"}:
            side = "SELL" if getattr(command.payload, "direction", "") == "long" else "BUY"
            return PreparedAdapterRequest(
                method="POST",
                path="/fapi/v1/order",
                params={
                    "symbol": "ETHUSDT",
                    "side": side,
                    "type": "STOP_MARKET",
                    "workingType": "MARK_PRICE",
                    "reduceOnly": "true",
                    "newClientOrderId": command.idempotency_key,
                },
                body=command.payload.model_dump(mode="json"),
                idempotency_key=command.idempotency_key,
            )
        if command.target == "sync_recent_fills":
            return PreparedAdapterRequest(
                method="GET",
                path="/fapi/v1/userTrades",
                params={"symbol": "ETHUSDT", "limit": 20},
                idempotency_key=command.idempotency_key,
            )
        if command.target == "reconcile_position_and_orders":
            return PreparedAdapterRequest(
                method="GET",
                path="/fapi/v2/positionRisk",
                params={"symbol": "ETHUSDT"},
                idempotency_key=command.idempotency_key,
            )
        return PreparedAdapterRequest(
            method="GET",
            path="/fapi/v1/ping",
            requires_auth=False,
            idempotency_key=command.idempotency_key,
        )
