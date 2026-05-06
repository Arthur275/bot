from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_DOWN, ROUND_HALF_UP
from hashlib import sha256
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from .binance_transport import (
    BinanceRequestConfigError,
    BinanceRequestSigner,
    BinanceTransport,
    BinanceTransportError,
    TransportResponse,
)
from .config import RuntimeMode
from .position_manager import ExecutionPlan


class PositionSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    position_state: str = "FLAT"
    direction: str = "neutral"
    size_pct: float = 0.0
    position_amt: float | None = None
    entry_price: float | None = None
    mark_price: float | None = None
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
    snapshot_valid: bool = True
    account_equity: float | None = None
    account_equity_source: str = ""
    error_endpoint: str = ""
    error_kind: str = ""
    error_message: str = ""
    error_http_status: int | None = None
    error_payload: Any = None


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
    direction: str = ""
    initial_stop_loss: float | None = None
    position_size_pct: float = Field(ge=0.0, le=1.0, default=0.0)
    execution_warnings: list[str] = Field(default_factory=list)


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
    trailing_activation_ratio: float | None = None
    trailing_callback_rate_pct: float | None = None
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
    idempotency_key: str = ""
    client_order_id: str = ""
    exchange_order_id: str = ""
    error_kind: str = ""


class AdapterCredentials(BaseModel):
    model_config = ConfigDict(extra="forbid")

    venue: str
    api_key_env: str
    api_secret_env: str
    recv_window_ms: int = Field(default=60000, gt=0)
    timeout_sec: float = Field(default=15.0, gt=0.0)
    proxy_url: str | None = None
    api_base_url: str = "https://fapi.binance.com"


class AdapterCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid")

    supports_real_execution: bool = False
    supports_recent_fill_sync: bool = False
    supports_trailing_stop_update: bool = False
    supports_breakeven_update: bool = False


class BinanceRequestMappingError(RuntimeError):
    pass


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
        resolved_direction = self._resolve_handoff_direction(handoff)
        if execution_plan.place_entry_order:
            commands.append(
                ExecutionCommand(
                    command_type="order",
                    operation="place",
                    target="entry_order",
                    idempotency_key=self._build_idempotency_key(target="entry_order", handoff=handoff),
                    reason=self._resolve_primary_command_reason(execution_plan),
                    payload=EntryOrderPayload(
                        action=execution_plan.effective_action,
                        direction=resolved_direction,
                        initial_stop_loss=(handoff or {}).get("initial_stop_loss"),
                        position_size_pct=self._resolve_entry_size_pct(execution_plan=execution_plan, handoff=handoff),
                        execution_warnings=self._resolve_execution_warnings(handoff),
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
                    reason=self._resolve_primary_command_reason(execution_plan),
                    payload=ReduceOrderPayload(
                        action=execution_plan.effective_action,
                        direction=resolved_direction,
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
                    reason=self._resolve_primary_command_reason(execution_plan),
                    payload=ExitOrderPayload(
                        action=execution_plan.effective_action,
                        direction=resolved_direction,
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
                        direction=resolved_direction,
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
                        direction=resolved_direction,
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
                        direction=resolved_direction,
                        trailing_rule=(handoff or {}).get("trailing_rule") or "",
                        trailing_activation_ratio=(handoff or {}).get("trailing_activation_ratio"),
                        trailing_callback_rate_pct=(handoff or {}).get("trailing_callback_rate_pct"),
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

    @staticmethod
    def _resolve_primary_command_reason(execution_plan: ExecutionPlan) -> str:
        action = execution_plan.effective_action or execution_plan.requested_action or "wait"
        return f"effective_action:{action}"

    @staticmethod
    def _resolve_entry_size_pct(*, execution_plan: ExecutionPlan, handoff: dict[str, Any] | None) -> float:
        if execution_plan.executable_size_pct is not None:
            return float(execution_plan.executable_size_pct or 0.0)
        handoff = handoff or {}
        return float(handoff.get("executable_size_pct") or handoff.get("position_size_pct") or 0.0)

    def fetch_runtime_snapshot(self) -> AdapterRuntimeSnapshot:
        return AdapterRuntimeSnapshot(fetched_at=datetime.now().replace(microsecond=0), snapshot_valid=False)

    def assess_reconciliation(
        self,
        *,
        runtime_snapshot: AdapterRuntimeSnapshot,
        expected_position_state: str,
        expected_direction: str,
        expected_size_pct: float,
    ) -> ReconciliationResult:
        if not runtime_snapshot.snapshot_valid:
            if not self.get_capabilities().supports_real_execution:
                return ReconciliationResult(
                    in_sync=True,
                    protective_stop_present=False,
                )
            return ReconciliationResult(
                in_sync=False,
                protective_stop_present=False,
                needs_position_sync=True,
                needs_order_sync=False,
                reason_codes=["runtime_snapshot_unavailable"],
            )
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
    def _resolve_handoff_direction(handoff: dict[str, Any] | None) -> str:
        primary = str((handoff or {}).get("direction") or "")
        fallback = str((handoff or {}).get("current_position_direction") or "")
        if primary in {"long", "short"}:
            return primary
        if fallback in {"long", "short"}:
            return fallback
        return primary or fallback

    @staticmethod
    def _build_idempotency_key(*, target: str, handoff: dict[str, Any] | None) -> str:
        generated_at = str((handoff or {}).get("generated_at") or "")
        action = str((handoff or {}).get("action") or "wait")
        direction = BaseExchangeAdapter._resolve_handoff_direction(handoff) or "neutral"
        return f"{target}:{generated_at}:{action}:{direction}"

    @staticmethod
    def _resolve_execution_warnings(handoff: dict[str, Any] | None) -> list[str]:
        value = (handoff or {}).get("execution_warnings")
        if value in (None, ""):
            return []
        if isinstance(value, str):
            candidates = value.split(",")
        elif isinstance(value, list):
            candidates = value
        else:
            candidates = [value]
        return [
            warning
            for warning in (str(candidate).strip() for candidate in candidates)
            if warning
        ]


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
                    idempotency_key=command.idempotency_key,
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

    def fetch_runtime_snapshot(self) -> AdapterRuntimeSnapshot:
        raise NotImplementedError("Real adapter subclasses must override fetch_runtime_snapshot")

    def prepare_requests(self, *, commands: list[ExecutionCommand]) -> list[PreparedAdapterRequest]:
        raise NotImplementedError

    def validate_prepared_request(
        self,
        *,
        command: ExecutionCommand,
        prepared: PreparedAdapterRequest,
        runtime_mode: RuntimeMode,
        runtime_snapshot: AdapterRuntimeSnapshot | None = None,
    ) -> PreparedAdapterRequest:
        return prepared

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
        runtime_snapshot: AdapterRuntimeSnapshot | None = None
        for command, prepared in zip(commands, prepared_requests, strict=False):
            real_validation_mode = runtime_mode in {RuntimeMode.REAL, RuntimeMode.SIMULATED_REAL}
            if real_validation_mode and self._has_route_c_missing_warning(command):
                results.append(
                    self._build_execution_result(
                        command=command,
                        runtime_mode=runtime_mode,
                        prepared=prepared,
                        status="error",
                        accepted=False,
                        simulated=False,
                        reason="unsafe_request_mapping",
                        extra_details={
                            "error": "Real entry order requires Route C/orderbook confirmation",
                            "reason_code": "route_c_missing",
                        },
                    )
                )
                continue
            current_runtime_snapshot = runtime_snapshot
            if real_validation_mode and self._requires_runtime_snapshot(command):
                if current_runtime_snapshot is None:
                    try:
                        current_runtime_snapshot = self.fetch_runtime_snapshot()
                    except BinanceRequestConfigError as exc:
                        results.append(
                            self._build_execution_result(
                                command=command,
                                runtime_mode=runtime_mode,
                                prepared=prepared,
                                status="error",
                                accepted=False,
                                simulated=False,
                                reason="request_signing_failed",
                                extra_details={"error": str(exc)},
                            )
                        )
                        continue
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
                                    "http_status": exc.http_status,
                                    "response_payload": exc.payload,
                                    "error": str(exc),
                                },
                            )
                        )
                        continue
                    runtime_snapshot = current_runtime_snapshot
            try:
                prepared = self.validate_prepared_request(
                    command=command,
                    prepared=prepared,
                    runtime_mode=RuntimeMode.REAL if real_validation_mode else runtime_mode,
                    runtime_snapshot=current_runtime_snapshot,
                )
                signed_request = self._signer.sign(prepared)
            except BinanceRequestMappingError as exc:
                results.append(
                    self._build_execution_result(
                        command=command,
                        runtime_mode=runtime_mode,
                        prepared=prepared,
                        status="error",
                        accepted=False,
                        simulated=runtime_mode != RuntimeMode.REAL,
                        reason="unsafe_request_mapping",
                        extra_details={
                            "error": str(exc),
                            "runtime_snapshot": self._runtime_snapshot_diagnostics(current_runtime_snapshot),
                        },
                    )
                )
                continue
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
                        simulated=runtime_mode != RuntimeMode.REAL,
                        reason=reason,
                        extra_details={
                            "http_status": exc.http_status,
                            "response_payload": exc.payload,
                            "error": str(exc),
                        },
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
                if self._is_timestamp_outside_recv_window(exc):
                    try:
                        self._signer.refresh_timestamp_offset()
                        signed_request = self._signer.sign(prepared)
                        response = self._transport.send(signed_request)
                    except (BinanceRequestConfigError, BinanceTransportError) as retry_exc:
                        exc = retry_exc if isinstance(retry_exc, BinanceTransportError) else exc
                    else:
                        exc = None
                if exc is not None:
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

            account_payload: Any = None
            if command.target == "reconcile_position_and_orders":
                current = response.payload[0] if isinstance(response.payload, list) and response.payload else response.payload
                if BinancePerpAdapter._has_entered_position(current):
                    try:
                        account_response = self._transport.send(
                            self._signer.sign(
                                PreparedAdapterRequest(
                                    method="GET",
                                    path="/fapi/v2/account",
                                )
                            )
                        )
                        account_payload = account_response.payload
                    except (BinanceRequestConfigError, BinanceTransportError):
                        account_payload = None

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
                        "response_summary": self._summarize_response_payload(
                            command=command,
                            payload=response.payload,
                            account_payload=account_payload,
                        ),
                    },
                )
            )
            if command.target == "entry_order":
                runtime_snapshot = None
        return results

    def preflight_commands(self, *, commands: list[ExecutionCommand]) -> list[CommandExecutionResult]:
        results: list[CommandExecutionResult] = []
        prepared_requests = self.prepare_requests(commands=commands)
        runtime_snapshot: AdapterRuntimeSnapshot | None = None
        for command, prepared in zip(commands, prepared_requests, strict=False):
            current_runtime_snapshot = runtime_snapshot
            if self._requires_runtime_snapshot(command):
                if current_runtime_snapshot is None:
                    current_runtime_snapshot = self.fetch_runtime_snapshot()
                    runtime_snapshot = current_runtime_snapshot
            try:
                prepared = self.validate_prepared_request(
                    command=command,
                    prepared=prepared,
                    runtime_mode=RuntimeMode.REAL,
                    runtime_snapshot=current_runtime_snapshot,
                )
                signed_request = self._signer.sign(prepared)
            except BinanceRequestMappingError as exc:
                results.append(
                    self._build_execution_result(
                        command=command,
                        runtime_mode=RuntimeMode.REAL,
                        prepared=prepared,
                        status="error",
                        accepted=False,
                        simulated=True,
                        reason="unsafe_request_mapping",
                        extra_details={
                            "error": str(exc),
                            "preflight": True,
                            "runtime_snapshot": self._runtime_snapshot_diagnostics(current_runtime_snapshot),
                        },
                    )
                )
                continue
            except BinanceRequestConfigError as exc:
                results.append(
                    self._build_execution_result(
                        command=command,
                        runtime_mode=RuntimeMode.REAL,
                        prepared=prepared,
                        status="error",
                        accepted=False,
                        simulated=True,
                        reason="request_signing_failed",
                        extra_details={"error": str(exc), "preflight": True},
                    )
                )
                continue
            except BinanceTransportError as exc:
                if self._is_timestamp_outside_recv_window(exc):
                    try:
                        self._signer.refresh_timestamp_offset()
                        prepared = self.validate_prepared_request(
                            command=command,
                            prepared=prepared,
                            runtime_mode=RuntimeMode.REAL,
                            runtime_snapshot=current_runtime_snapshot,
                        )
                        signed_request = self._signer.sign(prepared)
                    except (BinanceRequestConfigError, BinanceTransportError) as retry_exc:
                        exc = retry_exc if isinstance(retry_exc, BinanceTransportError) else exc
                    else:
                        results.append(
                            self._build_execution_result(
                                command=command,
                                runtime_mode=RuntimeMode.REAL,
                                prepared=prepared,
                                status="preflight_ready",
                                accepted=True,
                                simulated=True,
                                reason=command.reason,
                                extra_details={
                                    "signed_request": signed_request.model_dump(mode="json"),
                                    "preflight": True,
                                    "timestamp_offset_refreshed": True,
                                },
                            )
                        )
                        continue
                status = "timeout" if exc.kind == "timeout" else "rejected" if exc.kind == "http_error" else "error"
                reason = "transport_timeout" if exc.kind == "timeout" else "exchange_rejected" if exc.kind == "http_error" else "transport_error"
                results.append(
                    self._build_execution_result(
                        command=command,
                        runtime_mode=RuntimeMode.REAL,
                        prepared=prepared,
                        status=status,
                        accepted=False,
                        simulated=True,
                        reason=reason,
                        extra_details={
                            "http_status": exc.http_status,
                            "response_payload": exc.payload,
                            "error": str(exc),
                            "preflight": True,
                        },
                    )
                )
                continue
            results.append(
                self._build_execution_result(
                    command=command,
                    runtime_mode=RuntimeMode.REAL,
                    prepared=prepared,
                    status="preflight_ready",
                    accepted=True,
                    simulated=True,
                    reason=command.reason,
                    extra_details={
                        "signed_request": signed_request.model_dump(mode="json"),
                        "preflight": True,
                    },
                )
            )
        return results

    @staticmethod
    def _requires_runtime_snapshot(command: ExecutionCommand) -> bool:
        return command.target in {"entry_order", "reduce_order", "exit_order", "maintain_protective_stop", "advance_breakeven_stop", "advance_trailing_stop"}

    @staticmethod
    def _runtime_snapshot_diagnostics(runtime_snapshot: AdapterRuntimeSnapshot | None) -> dict[str, Any]:
        if runtime_snapshot is None:
            return {"snapshot_valid": False, "error_message": "runtime_snapshot_missing"}
        return {
            "snapshot_valid": runtime_snapshot.snapshot_valid,
            "fetched_at": runtime_snapshot.fetched_at.isoformat() if runtime_snapshot.fetched_at else None,
            "error_endpoint": runtime_snapshot.error_endpoint,
            "error_kind": runtime_snapshot.error_kind,
            "error_message": runtime_snapshot.error_message,
            "error_http_status": runtime_snapshot.error_http_status,
            "error_payload": runtime_snapshot.error_payload,
        }

    @staticmethod
    def _has_route_c_missing_warning(command: ExecutionCommand) -> bool:
        if command.target != "entry_order":
            return False
        payload = command.payload
        warnings = getattr(payload, "execution_warnings", [])
        return any(str(warning).strip() == "route_c_missing" for warning in warnings)

    @staticmethod
    def _is_timestamp_outside_recv_window(exc: BinanceTransportError) -> bool:
        if exc.kind != "http_error" or not isinstance(exc.payload, dict):
            return False
        return exc.payload.get("code") == -1021

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
        client_order_id = self._extract_client_order_id(prepared=prepared)
        exchange_order_id = self._extract_exchange_order_id(details.get("response_payload"))
        error_kind = self._resolve_error_kind(reason=reason, extra_details=extra_details)
        return CommandExecutionResult(
            target=command.target,
            status=status,
            accepted=accepted,
            simulated=simulated,
            reason=reason,
            details=details,
            idempotency_key=command.idempotency_key,
            client_order_id=client_order_id,
            exchange_order_id=exchange_order_id,
            error_kind=error_kind,
        )

    @staticmethod
    def _extract_client_order_id(*, prepared: PreparedAdapterRequest) -> str:
        params = prepared.params or {}
        return str(params.get("newClientOrderId") or params.get("clientAlgoId") or "")

    @staticmethod
    def _extract_exchange_order_id(payload: Any) -> str:
        if not isinstance(payload, dict):
            return ""
        return str(payload.get("orderId") or payload.get("algoId") or "")

    @staticmethod
    def _resolve_error_kind(*, reason: str, extra_details: dict[str, Any] | None) -> str:
        if not extra_details:
            return ""
        if "error" not in extra_details:
            return ""
        if reason == "unsafe_request_mapping":
            return "unsafe_request_mapping"
        if reason == "request_signing_failed":
            return "request_config_error"
        if reason == "transport_timeout":
            return "timeout"
        if reason == "exchange_rejected":
            return "http_error"
        if reason == "transport_error":
            return "transport_error"
        return str(reason or "error")

    @staticmethod
    def _summarize_response_payload(*, command: ExecutionCommand, payload: Any, account_payload: Any = None) -> dict[str, Any]:
        if command.target == "sync_recent_fills" and isinstance(payload, list):
            latest = payload[-1] if payload else {}
            distinct_order_count = len(
                {
                    str(item.get("orderId") or "")
                    for item in payload
                    if isinstance(item, dict) and str(item.get("orderId") or "")
                }
            )
            return {
                "fill_count": len(payload),
                "distinct_order_count": distinct_order_count,
                "latest_trade_id": str((latest or {}).get("id") or ""),
                "latest_order_id": str((latest or {}).get("orderId") or ""),
                "latest_side": str((latest or {}).get("side") or ""),
                "latest_price": str((latest or {}).get("price") or ""),
                "latest_qty": str((latest or {}).get("qty") or ""),
                "latest_quote_qty": str((latest or {}).get("quoteQty") or ""),
                "latest_time": (latest or {}).get("time"),
                "latest_realized_pnl": str((latest or {}).get("realizedPnl") or ""),
            }
        if command.target == "reconcile_position_and_orders" and isinstance(payload, list):
            current = payload[0] if payload else {}
            amt_raw = (current or {}).get("positionAmt")
            try:
                position_amt = float(amt_raw or 0.0)
            except (TypeError, ValueError):
                position_amt = 0.0
            position_snapshot = BinancePerpAdapter._build_position_snapshot(payload, account_payload=account_payload)
            account_equity, account_equity_source = BinancePerpAdapter._extract_account_equity_with_source(account_payload)
            return {
                "position_state": position_snapshot.position_state,
                "direction": position_snapshot.direction,
                "size_pct": position_snapshot.size_pct,
                "position_amt": position_amt,
                "entry_price": (current or {}).get("entryPrice"),
                "break_even_price": (current or {}).get("breakEvenPrice"),
                "mark_price": (current or {}).get("markPrice"),
                "notional": (current or {}).get("notional"),
                "unrealized_profit": (current or {}).get("unRealizedProfit"),
                "leverage": position_snapshot.leverage,
                "account_equity": account_equity,
                "account_equity_source": account_equity_source,
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
    def fetch_runtime_snapshot(self) -> AdapterRuntimeSnapshot:
        fetched_at = datetime.now().replace(microsecond=0)
        try:
            position_request = PreparedAdapterRequest(
                method="GET",
                path="/fapi/v2/positionRisk",
                params={"symbol": "ETHUSDT"},
            )
            open_orders_request = PreparedAdapterRequest(
                method="GET",
                path="/fapi/v1/openOrders",
                params={"symbol": "ETHUSDT"},
            )
        except BinanceRequestConfigError:
            raise
        try:
            position_response = self._send_snapshot_request_with_recv_window_retry(position_request)
        except BinanceTransportError as exc:
            return self._invalid_runtime_snapshot(
                fetched_at=fetched_at,
                endpoint="/fapi/v2/positionRisk",
                exc=exc,
            )
        try:
            open_orders_response = self._send_snapshot_request_with_recv_window_retry(open_orders_request)
        except BinanceTransportError as exc:
            return self._invalid_runtime_snapshot(
                fetched_at=fetched_at,
                endpoint="/fapi/v1/openOrders",
                exc=exc,
            )
        account_payload: Any = None
        raw_position = position_response.payload[0] if isinstance(position_response.payload, list) and position_response.payload else position_response.payload
        try:
            account_request = PreparedAdapterRequest(
                method="GET",
                path="/fapi/v2/account",
            )
            account_response = self._send_snapshot_request_with_recv_window_retry(account_request)
            account_payload = account_response.payload
        except BinanceRequestConfigError:
            raise
        except BinanceTransportError as exc:
            account_payload = self._account_snapshot_warning(exc)

        open_orders = self._build_open_orders_snapshot(open_orders_response.payload)
        position_snapshot = self._build_position_snapshot(position_response.payload, account_payload=account_payload)
        if position_snapshot.position_state == "ENTERED" and not self._has_protective_stop(open_orders):
            try:
                open_algo_orders_request = PreparedAdapterRequest(
                    method="GET",
                    path="/fapi/v1/openAlgoOrders",
                    params={"symbol": "ETHUSDT", "algoType": "CONDITIONAL"},
                )
                open_algo_orders_response = self._send_snapshot_request_with_recv_window_retry(open_algo_orders_request)
                open_orders = [*open_orders, *self._build_open_algo_orders_snapshot(open_algo_orders_response.payload)]
            except BinanceTransportError as exc:
                return self._invalid_runtime_snapshot(
                    fetched_at=fetched_at,
                    endpoint="/fapi/v1/openAlgoOrders",
                    exc=exc,
                )
        account_equity, account_equity_source = self._extract_account_equity_with_source(account_payload)
        snapshot = AdapterRuntimeSnapshot(
            fetched_at=fetched_at,
            position=position_snapshot,
            open_orders=open_orders,
            protective_stop_present=self._has_protective_stop(open_orders),
            account_equity=account_equity,
            account_equity_source=account_equity_source,
        )
        if isinstance(account_payload, dict) and isinstance(account_payload.get("_snapshot_warning"), dict):
            warning = account_payload["_snapshot_warning"]
            snapshot.error_endpoint = str(warning.get("endpoint") or "")
            snapshot.error_kind = str(warning.get("kind") or "")
            snapshot.error_message = str(warning.get("message") or "")
            snapshot.error_http_status = warning.get("http_status")
            snapshot.error_payload = warning.get("payload")
        return snapshot

    def _send_snapshot_request_with_recv_window_retry(self, request: PreparedAdapterRequest) -> TransportResponse:
        try:
            return self._transport.send(self._signer.sign(request))
        except BinanceTransportError as exc:
            if not self._is_timestamp_outside_recv_window(exc):
                raise
            self._signer.refresh_timestamp_offset()
            return self._transport.send(self._signer.sign(request))

    def fetch_open_algo_orders_raw(self) -> list[dict[str, Any]]:
        request = PreparedAdapterRequest(
            method="GET",
            path="/fapi/v1/openAlgoOrders",
            params={"symbol": "ETHUSDT", "algoType": "CONDITIONAL"},
        )
        response = self._send_snapshot_request_with_recv_window_retry(request)
        if not isinstance(response.payload, list):
            raise BinanceTransportError(
                kind="json_error",
                message="Malformed open algo orders response",
                http_status=response.http_status,
                payload=response.payload,
            )
        return [item for item in response.payload if isinstance(item, dict)]

    def fetch_user_trades_raw(
        self,
        *,
        symbol: str = "ETHUSDT",
        limit: int = 100,
        start_time_ms: int | None = None,
        end_time_ms: int | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"symbol": symbol, "limit": limit}
        if start_time_ms is not None:
            params["startTime"] = start_time_ms
        if end_time_ms is not None:
            params["endTime"] = end_time_ms
        response = self._send_snapshot_request_with_recv_window_retry(
            PreparedAdapterRequest(method="GET", path="/fapi/v1/userTrades", params=params)
        )
        if not isinstance(response.payload, list):
            raise BinanceTransportError(
                kind="json_error",
                message="Malformed user trades response",
                http_status=response.http_status,
                payload=response.payload,
            )
        return [item for item in response.payload if isinstance(item, dict)]

    def cancel_algo_order_raw(self, *, algo_id: str = "", client_algo_id: str = "") -> dict[str, Any]:
        params: dict[str, Any] = {}
        if algo_id:
            params["algoId"] = algo_id
        if client_algo_id:
            params["clientAlgoId"] = client_algo_id
        if not params:
            raise BinanceRequestMappingError("Cancel algo order requires algo_id or client_algo_id")
        response = self._send_snapshot_request_with_recv_window_retry(
            PreparedAdapterRequest(method="DELETE", path="/fapi/v1/algoOrder", params=params)
        )
        if not isinstance(response.payload, dict):
            raise BinanceTransportError(
                kind="json_error",
                message="Malformed cancel algo order response",
                http_status=response.http_status,
                payload=response.payload,
            )
        return response.payload

    def place_algo_order_raw(self, *, params: dict[str, Any]) -> dict[str, Any]:
        response = self._send_snapshot_request_with_recv_window_retry(
            PreparedAdapterRequest(method="POST", path="/fapi/v1/algoOrder", params=dict(params))
        )
        if not isinstance(response.payload, dict):
            raise BinanceTransportError(
                kind="json_error",
                message="Malformed place algo order response",
                http_status=response.http_status,
                payload=response.payload,
            )
        return response.payload

    @staticmethod
    def _account_snapshot_warning(exc: BinanceTransportError) -> dict[str, Any]:
        return {
            "_snapshot_warning": {
                "endpoint": "/fapi/v2/account",
                "kind": exc.kind,
                "message": str(exc),
                "http_status": exc.http_status,
                "payload": exc.payload,
            }
        }

    @staticmethod
    def _invalid_runtime_snapshot(
        *,
        fetched_at: datetime,
        endpoint: str,
        exc: BinanceTransportError,
    ) -> AdapterRuntimeSnapshot:
        return AdapterRuntimeSnapshot(
            fetched_at=fetched_at,
            snapshot_valid=False,
            error_endpoint=endpoint,
            error_kind=exc.kind,
            error_message=str(exc),
            error_http_status=exc.http_status,
            error_payload=exc.payload,
        )

    def prepare_requests(self, *, commands: list[ExecutionCommand]) -> list[PreparedAdapterRequest]:
        requests: list[PreparedAdapterRequest] = []
        for command in commands:
            requests.append(self._map_command_to_request(command))
        return requests

    @staticmethod
    def _build_position_snapshot(payload: Any, *, account_payload: Any = None) -> PositionSnapshot:
        current = payload[0] if isinstance(payload, list) and payload else payload if isinstance(payload, dict) else {}
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
        entry_price = BinancePerpAdapter._to_optional_float((current or {}).get("entryPrice"))
        leverage = BinancePerpAdapter._to_optional_int((current or {}).get("leverage"))
        size_pct = BinancePerpAdapter._resolve_position_size_pct(current=current, account_payload=account_payload, leverage=leverage)
        return PositionSnapshot(
            position_state="ENTERED" if position_amt != 0.0 else "FLAT",
            direction=direction,
            size_pct=size_pct,
            position_amt=position_amt,
            entry_price=entry_price,
            mark_price=BinancePerpAdapter._to_optional_float((current or {}).get("markPrice")),
            leverage=leverage,
        )

    @staticmethod
    def _has_entered_position(payload: Any) -> bool:
        current = payload if isinstance(payload, dict) else {}
        try:
            return float((current or {}).get("positionAmt") or 0.0) != 0.0
        except (TypeError, ValueError):
            return False

    @staticmethod
    def _resolve_position_size_pct(*, current: dict[str, Any], account_payload: Any, leverage: int | None) -> float:
        notional = BinancePerpAdapter._to_optional_float(current.get("notional"))
        if notional is None:
            entry_price = BinancePerpAdapter._to_optional_float(current.get("entryPrice"))
            position_amt = BinancePerpAdapter._to_optional_float(current.get("positionAmt"))
            if entry_price is not None and position_amt is not None:
                notional = abs(entry_price * position_amt)
        if notional is None or notional <= 0.0:
            return 0.0
        account_equity = BinancePerpAdapter._extract_account_equity(account_payload)
        if account_equity is None or account_equity <= 0.0:
            return 0.0
        live_leverage = leverage or BinancePerpAdapter._to_optional_int(current.get("leverage")) or 10
        denominator = account_equity * float(live_leverage)
        if denominator <= 0.0:
            return 0.0
        return max(0.0, min(1.0, round(abs(notional) / denominator, 4)))

    @staticmethod
    def _extract_account_equity(account_payload: Any) -> float | None:
        value, _ = BinancePerpAdapter._extract_account_equity_with_source(account_payload)
        return value

    @staticmethod
    def _extract_account_equity_with_source(account_payload: Any) -> tuple[float | None, str]:
        if not isinstance(account_payload, dict):
            return None, ""
        for key in ("totalWalletBalance", "totalMarginBalance", "totalCrossWalletBalance"):
            value = BinancePerpAdapter._to_optional_float(account_payload.get(key))
            if value is not None and value > 0.0:
                return value, key
        assets = account_payload.get("assets")
        if isinstance(assets, list):
            for asset in assets:
                if not isinstance(asset, dict):
                    continue
                if str(asset.get("asset") or "") != "USDT":
                    continue
                for key in ("walletBalance", "marginBalance", "crossWalletBalance"):
                    value = BinancePerpAdapter._to_optional_float(asset.get(key))
                    if value is not None and value > 0.0:
                        return value, f"assets.USDT.{key}"
        return None, ""

    @staticmethod
    def _build_open_orders_snapshot(payload: Any) -> list[OrderSnapshot]:
        if not isinstance(payload, list):
            return []
        orders: list[OrderSnapshot] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            reduce_only = BinancePerpAdapter._to_bool(item.get("reduceOnly")) or BinancePerpAdapter._to_bool(item.get("closePosition"))
            orders.append(
                OrderSnapshot(
                    order_id=str(item.get("orderId") or ""),
                    order_type=str(item.get("type") or ""),
                    status=str(item.get("status") or "open"),
                    side=str(item.get("side") or ""),
                    reduce_only=reduce_only,
                    price=BinancePerpAdapter._to_optional_float(item.get("price")),
                    trigger_price=BinancePerpAdapter._to_optional_float(item.get("stopPrice")),
                )
            )
        return orders

    @staticmethod
    def _build_open_algo_orders_snapshot(payload: Any) -> list[OrderSnapshot]:
        if not isinstance(payload, list):
            return []
        orders: list[OrderSnapshot] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            algo_status = str(item.get("algoStatus") or item.get("status") or "").upper()
            if algo_status not in {"NEW", "PARTIALLY_FILLED"}:
                continue
            reduce_only = BinancePerpAdapter._to_bool(item.get("reduceOnly")) or BinancePerpAdapter._to_bool(item.get("closePosition"))
            orders.append(
                OrderSnapshot(
                    order_id=f"algo:{item.get('algoId') or item.get('clientAlgoId') or ''}",
                    order_type=str(item.get("orderType") or item.get("type") or ""),
                    status=algo_status.lower(),
                    side=str(item.get("side") or ""),
                    reduce_only=reduce_only,
                    price=BinancePerpAdapter._to_optional_float(item.get("price")),
                    trigger_price=BinancePerpAdapter._to_optional_float(item.get("triggerPrice") or item.get("stopPrice")),
                )
            )
        return orders

    @staticmethod
    def _has_protective_stop(open_orders: list[OrderSnapshot]) -> bool:
        protective_types = {"STOP", "STOP_MARKET", "TRAILING_STOP_MARKET"}
        return any(order.reduce_only and order.order_type in protective_types for order in open_orders)

    @staticmethod
    def _to_optional_float(value: Any) -> float | None:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return None
        return None if parsed == 0.0 else parsed

    @staticmethod
    def _to_optional_int(value: Any) -> int | None:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return None
        return None if parsed == 0 else parsed

    @staticmethod
    def _to_bool(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        return str(value).lower() == "true"

    def validate_prepared_request(
        self,
        *,
        command: ExecutionCommand,
        prepared: PreparedAdapterRequest,
        runtime_mode: RuntimeMode,
        runtime_snapshot: AdapterRuntimeSnapshot | None = None,
    ) -> PreparedAdapterRequest:
        if runtime_mode != RuntimeMode.REAL:
            return prepared
        if command.target == "entry_order":
            return self._resolve_entry_order_request(command=command, prepared=prepared, runtime_snapshot=runtime_snapshot)
        if command.target == "reduce_order":
            return self._resolve_reduce_order_request(command=command, prepared=prepared, runtime_snapshot=runtime_snapshot)
        if command.target == "exit_order":
            return self._resolve_exit_order_request(command=command, prepared=prepared, runtime_snapshot=runtime_snapshot)
        if command.target == "maintain_protective_stop":
            return self._resolve_protective_stop_request(command=command, prepared=prepared, runtime_snapshot=runtime_snapshot)
        if command.target == "advance_breakeven_stop":
            return self._resolve_breakeven_stop_request(command=command, prepared=prepared, runtime_snapshot=runtime_snapshot)
        if command.target == "advance_trailing_stop":
            return self._resolve_trailing_stop_request(command=command, prepared=prepared, runtime_snapshot=runtime_snapshot)
        return prepared

    def _resolve_entry_order_request(
        self,
        *,
        command: ExecutionCommand,
        prepared: PreparedAdapterRequest,
        runtime_snapshot: AdapterRuntimeSnapshot | None,
    ) -> PreparedAdapterRequest:
        payload = command.payload
        if not isinstance(payload, EntryOrderPayload):
            raise BinanceRequestMappingError("Entry order payload is required for real entry execution")
        runtime_snapshot = self._require_valid_runtime_snapshot(
            runtime_snapshot=runtime_snapshot,
            error_context="Entry order",
        )
        if runtime_snapshot.position.position_state == "ENTERED":
            raise BinanceRequestMappingError("Real entry order requires a flat live position")
        position_size_pct = float(payload.position_size_pct or 0.0)
        if position_size_pct <= 0.0:
            raise BinanceRequestMappingError("Real entry order requires a positive position_size_pct")
        account_equity = self._resolve_account_equity_for_entry(runtime_snapshot)
        symbol_contract = self._fetch_symbol_contract()
        leverage = self._resolve_entry_leverage(runtime_snapshot)
        mark_price = self._resolve_entry_mark_price(symbol_contract=symbol_contract, runtime_snapshot=runtime_snapshot)
        quantity = self._derive_entry_quantity(
            position_size_pct=position_size_pct,
            account_equity=account_equity,
            leverage=leverage,
            mark_price=mark_price,
            min_qty=symbol_contract["min_qty"],
            step_size=symbol_contract["step_size"],
        )
        return prepared.model_copy(
            update={
                "params": {
                    **prepared.params,
                    "quantity": quantity,
                },
                "body": {
                    **prepared.body,
                    "resolved_quantity": quantity,
                    "resolved_mark_price": format(mark_price, "f"),
                    "resolved_account_equity": format(account_equity, "f"),
                    "resolved_leverage": leverage,
                    "resolved_step_size": format(symbol_contract["step_size"], "f"),
                    "resolved_min_qty": format(symbol_contract["min_qty"], "f"),
                    "resolution_mode": "entry_quantity_from_size_pct",
                },
            }
        )

    def _resolve_exit_order_request(
        self,
        *,
        command: ExecutionCommand,
        prepared: PreparedAdapterRequest,
        runtime_snapshot: AdapterRuntimeSnapshot | None,
    ) -> PreparedAdapterRequest:
        payload = command.payload
        if not isinstance(payload, ExitOrderPayload):
            raise BinanceRequestMappingError("Exit order payload is required for real exit execution")
        position = self._resolve_live_position_for_exit(
            runtime_snapshot=runtime_snapshot,
            payload_direction=payload.direction,
        )
        quantity = self._format_exit_quantity(position.position_amt)
        return prepared.model_copy(
            update={
                "params": {
                    **prepared.params,
                    "side": self._resolve_close_side(position.direction),
                    "quantity": quantity,
                },
                "body": {
                    **prepared.body,
                    "resolved_position_amt": quantity,
                    "resolution_mode": "exit_quantity_from_live_position",
                },
            }
        )

    def _resolve_reduce_order_request(
        self,
        *,
        command: ExecutionCommand,
        prepared: PreparedAdapterRequest,
        runtime_snapshot: AdapterRuntimeSnapshot | None,
    ) -> PreparedAdapterRequest:
        payload = command.payload
        if not isinstance(payload, ReduceOrderPayload):
            raise BinanceRequestMappingError("Reduce order payload is required for real reduce execution")
        self._resolve_live_position_for_exit(
            runtime_snapshot=runtime_snapshot,
            payload_direction=payload.direction,
        )
        raise BinanceRequestMappingError(
            "Real reduce order requires an explicit reduce quantity contract from quant handoff"
        )

    @staticmethod
    def _resolve_entry_leverage(runtime_snapshot: AdapterRuntimeSnapshot) -> int:
        leverage = runtime_snapshot.position.leverage or 10
        if leverage <= 0:
            raise BinanceRequestMappingError("Real entry order requires a positive live leverage")
        return leverage

    @staticmethod
    def _resolve_entry_mark_price(*, symbol_contract: dict[str, Decimal], runtime_snapshot: AdapterRuntimeSnapshot) -> Decimal:
        runtime_mark_price = runtime_snapshot.position.mark_price
        if runtime_mark_price is not None and runtime_mark_price > 0.0:
            return Decimal(str(runtime_mark_price))
        mark_price = symbol_contract["mark_price"]
        if mark_price <= 0:
            raise BinanceRequestMappingError("Real entry order requires a positive mark price")
        return mark_price

    def _fetch_account_equity_for_entry(self) -> Decimal:
        account_response = self._transport.send(
            self._signer.sign(
                PreparedAdapterRequest(
                    method="GET",
                    path="/fapi/v2/account",
                )
            )
        )
        account_equity = self._extract_account_equity(account_response.payload)
        if account_equity is None or account_equity <= 0.0:
            raise BinanceRequestMappingError("Real entry order requires a positive account equity")
        return Decimal(str(account_equity))

    def _resolve_account_equity_for_entry(self, runtime_snapshot: AdapterRuntimeSnapshot) -> Decimal:
        if runtime_snapshot.account_equity is not None and runtime_snapshot.account_equity > 0.0:
            return Decimal(str(runtime_snapshot.account_equity))
        return self._fetch_account_equity_for_entry()

    def _fetch_symbol_contract(self) -> dict[str, Decimal]:
        exchange_info_response = self._transport.send(
            self._signer.sign(
                PreparedAdapterRequest(
                    method="GET",
                    path="/fapi/v1/exchangeInfo",
                    requires_auth=False,
                )
            )
        )
        premium_index_response = self._transport.send(
            self._signer.sign(
                PreparedAdapterRequest(
                    method="GET",
                    path="/fapi/v1/premiumIndex",
                    requires_auth=False,
                    params={"symbol": "ETHUSDT"},
                )
            )
        )
        return self._extract_symbol_contract(exchange_info_response.payload, premium_index_response.payload)

    @staticmethod
    def _extract_symbol_contract(exchange_info_payload: Any, premium_index_payload: Any) -> dict[str, Decimal]:
        if not isinstance(exchange_info_payload, dict):
            raise BinanceRequestMappingError("Real entry order requires valid exchangeInfo payload")
        symbols = exchange_info_payload.get("symbols")
        if not isinstance(symbols, list):
            raise BinanceRequestMappingError("Real entry order requires exchangeInfo symbols metadata")
        current = next((item for item in symbols if isinstance(item, dict) and str(item.get("symbol") or "") == "ETHUSDT"), None)
        if current is None:
            raise BinanceRequestMappingError("Real entry order requires ETHUSDT symbol metadata")
        filters = current.get("filters")
        if not isinstance(filters, list):
            raise BinanceRequestMappingError("Real entry order requires ETHUSDT filters metadata")
        lot_filter = next(
            (
                item for item in filters
                if isinstance(item, dict) and str(item.get("filterType") or "") in {"MARKET_LOT_SIZE", "LOT_SIZE"}
            ),
            None,
        )
        if lot_filter is None:
            raise BinanceRequestMappingError("Real entry order requires ETHUSDT lot size metadata")
        step_size = BinancePerpAdapter._to_positive_decimal(lot_filter.get("stepSize"), error_message="Real entry order requires a positive stepSize")
        min_qty = BinancePerpAdapter._to_positive_decimal(lot_filter.get("minQty"), error_message="Real entry order requires a positive minQty")
        mark_price = BinancePerpAdapter._extract_mark_price_decimal(premium_index_payload)
        return {
            "step_size": step_size,
            "min_qty": min_qty,
            "mark_price": mark_price,
        }

    @staticmethod
    def _extract_mark_price_decimal(premium_index_payload: Any) -> Decimal:
        if not isinstance(premium_index_payload, dict):
            raise BinanceRequestMappingError("Real entry order requires valid premium index payload")
        return BinancePerpAdapter._to_positive_decimal(
            premium_index_payload.get("markPrice"),
            error_message="Real entry order requires a positive mark price",
        )

    @staticmethod
    def _derive_entry_quantity(
        *,
        position_size_pct: float,
        account_equity: Decimal,
        leverage: int,
        mark_price: Decimal,
        min_qty: Decimal,
        step_size: Decimal,
    ) -> str:
        try:
            size_pct = Decimal(str(position_size_pct))
            leverage_decimal = Decimal(str(leverage))
        except InvalidOperation as exc:
            raise BinanceRequestMappingError("Real entry order size inputs are not valid decimals") from exc
        if size_pct <= 0 or size_pct > 1:
            raise BinanceRequestMappingError("Real entry order requires position_size_pct between 0 and 1")
        if account_equity <= 0 or leverage_decimal <= 0 or mark_price <= 0:
            raise BinanceRequestMappingError("Real entry order requires positive equity, leverage, and mark price")
        quantity = (size_pct * account_equity * leverage_decimal / mark_price).quantize(step_size, rounding=ROUND_DOWN)
        if quantity < min_qty:
            raise BinanceRequestMappingError("Real entry order resolved quantity is below Binance minQty")
        if quantity <= 0:
            raise BinanceRequestMappingError("Real entry order resolved quantity must be positive")
        return format(quantity, "f")

    @staticmethod
    def _to_positive_decimal(value: Any, *, error_message: str) -> Decimal:
        try:
            parsed = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise BinanceRequestMappingError(error_message) from exc
        if parsed <= 0:
            raise BinanceRequestMappingError(error_message)
        return parsed

    def _resolve_protective_stop_request(
        self,
        *,
        command: ExecutionCommand,
        prepared: PreparedAdapterRequest,
        runtime_snapshot: AdapterRuntimeSnapshot | None,
    ) -> PreparedAdapterRequest:
        payload = command.payload
        if not isinstance(payload, ProtectiveStopPayload):
            raise BinanceRequestMappingError("Protective stop payload is required for real protective stop execution")
        position = self._resolve_live_position_for_stop(
            runtime_snapshot=runtime_snapshot,
            payload_direction=payload.direction,
            error_context="Protective stop",
        )
        stop_ratio = payload.initial_stop_loss
        if stop_ratio is None:
            raise BinanceRequestMappingError("Real protective stop requires initial_stop_loss")
        stop_price = self._derive_stop_price(direction=position.direction, reference_price=position.entry_price, stop_ratio=stop_ratio)
        quantity = self._format_exit_quantity(position.position_amt)
        trigger_key = "triggerPrice" if prepared.path == "/fapi/v1/algoOrder" else "stopPrice"
        return prepared.model_copy(
            update={
                "params": {
                    **prepared.params,
                    "side": self._resolve_close_side(position.direction),
                    trigger_key: stop_price,
                    "quantity": quantity,
                },
                "body": {
                    **prepared.body,
                    "resolved_from_entry_price": position.entry_price,
                    "resolved_stop_price": stop_price,
                    "resolved_position_amt": quantity,
                    "resolution_mode": "initial_stop_from_live_entry",
                },
            }
        )

    def _resolve_breakeven_stop_request(
        self,
        *,
        command: ExecutionCommand,
        prepared: PreparedAdapterRequest,
        runtime_snapshot: AdapterRuntimeSnapshot | None,
    ) -> PreparedAdapterRequest:
        payload = command.payload
        if not isinstance(payload, BreakevenPayload):
            raise BinanceRequestMappingError("Breakeven payload is required for real breakeven execution")
        if payload.breakeven_trigger is None:
            raise BinanceRequestMappingError("Real breakeven stop requires breakeven_trigger")
        position = self._resolve_live_position_for_stop(
            runtime_snapshot=runtime_snapshot,
            payload_direction=payload.direction,
            error_context="Breakeven stop",
        )
        raise BinanceRequestMappingError(
            "Real breakeven stop replace requires Binance Algo stop cancel/replace support"
        )
        stop_price = self._format_live_entry_stop_price(position.entry_price)
        quantity = self._format_exit_quantity(position.position_amt)
        return prepared.model_copy(
            update={
                "params": {
                    **prepared.params,
                    "side": self._resolve_close_side(position.direction),
                    "stopPrice": stop_price,
                    "quantity": quantity,
                },
                "body": {
                    **prepared.body,
                    "resolved_from_entry_price": position.entry_price,
                    "resolved_stop_price": stop_price,
                    "resolved_position_amt": quantity,
                    "resolution_mode": "breakeven_from_live_entry",
                },
            }
        )

    @staticmethod
    def _require_valid_runtime_snapshot(
        *,
        runtime_snapshot: AdapterRuntimeSnapshot | None,
        error_context: str,
    ) -> AdapterRuntimeSnapshot:
        if runtime_snapshot is None or not runtime_snapshot.snapshot_valid:
            raise BinanceRequestMappingError(
                f"A valid runtime snapshot is required before sending a real {error_context.lower()}"
            )
        return runtime_snapshot

    @staticmethod
    def _resolve_live_position_for_stop(
        *,
        runtime_snapshot: AdapterRuntimeSnapshot | None,
        payload_direction: str,
        error_context: str,
    ) -> PositionSnapshot:
        runtime_snapshot = BinancePerpAdapter._require_valid_runtime_snapshot(
            runtime_snapshot=runtime_snapshot,
            error_context=error_context,
        )
        position = runtime_snapshot.position
        if position.position_state != "ENTERED":
            raise BinanceRequestMappingError(f"Real {error_context.lower()} requires an existing entered position")
        if position.direction not in {"long", "short"}:
            raise BinanceRequestMappingError(f"Real {error_context.lower()} requires a known position direction")
        if payload_direction and payload_direction != position.direction:
            raise BinanceRequestMappingError(f"{error_context} direction does not match live position direction")
        reference_price = position.entry_price
        if reference_price is None or reference_price <= 0.0:
            raise BinanceRequestMappingError(f"Real {error_context.lower()} requires a valid live entry price")
        return position.model_copy(update={"entry_price": reference_price})

    @staticmethod
    def _resolve_live_position_for_exit(
        *,
        runtime_snapshot: AdapterRuntimeSnapshot | None,
        payload_direction: str,
    ) -> PositionSnapshot:
        runtime_snapshot = BinancePerpAdapter._require_valid_runtime_snapshot(
            runtime_snapshot=runtime_snapshot,
            error_context="Exit order",
        )
        position = runtime_snapshot.position
        if position.position_state != "ENTERED":
            raise BinanceRequestMappingError("Real exit order requires an existing entered position")
        if position.direction not in {"long", "short"}:
            raise BinanceRequestMappingError("Real exit order requires a known position direction")
        if payload_direction and payload_direction != position.direction:
            raise BinanceRequestMappingError("Exit order direction does not match live position direction")
        return position

    @staticmethod
    def _format_exit_quantity(position_amt: float | None) -> str:
        try:
            quantity = Decimal(str(position_amt or 0.0)).copy_abs()
        except InvalidOperation as exc:
            raise BinanceRequestMappingError("Real exit order position amount is not a valid decimal") from exc
        if quantity <= 0:
            raise BinanceRequestMappingError("Real exit order requires a positive live position amount")
        normalized = quantity.normalize()
        return format(normalized, "f")

    @staticmethod
    def _resolve_close_side(direction: str) -> str:
        if direction == "long":
            return "SELL"
        if direction == "short":
            return "BUY"
        raise BinanceRequestMappingError("Close order direction must be long or short")

    @staticmethod
    def _format_live_entry_stop_price(reference_price: float) -> str:
        try:
            reference = Decimal(str(reference_price))
        except InvalidOperation as exc:
            raise BinanceRequestMappingError("Breakeven stop price input is not a valid decimal") from exc
        if reference <= 0:
            raise BinanceRequestMappingError("Breakeven stop requires a positive live entry price")
        return format(reference, "f")

    def _resolve_trailing_stop_request(
        self,
        *,
        command: ExecutionCommand,
        prepared: PreparedAdapterRequest,
        runtime_snapshot: AdapterRuntimeSnapshot | None,
    ) -> PreparedAdapterRequest:
        payload = command.payload
        if not isinstance(payload, TrailingStopPayload):
            raise BinanceRequestMappingError("Trailing stop payload is required for real trailing execution")
        if payload.trailing_activation_ratio is None:
            raise BinanceRequestMappingError("Real trailing stop requires trailing_activation_ratio")
        if payload.trailing_callback_rate_pct is None:
            raise BinanceRequestMappingError("Real trailing stop requires trailing_callback_rate_pct")
        position = self._resolve_live_position_for_stop(
            runtime_snapshot=runtime_snapshot,
            payload_direction=payload.direction,
            error_context="Trailing stop",
        )
        raise BinanceRequestMappingError(
            "Real trailing stop replace requires Binance Algo stop cancel/replace support"
        )
        activation_price = self._derive_trailing_activation_price(
            direction=position.direction,
            reference_price=position.entry_price,
            activation_ratio=payload.trailing_activation_ratio,
        )
        callback_rate = self._format_callback_rate_pct(payload.trailing_callback_rate_pct)
        return prepared.model_copy(
            update={
                "params": {
                    **prepared.params,
                    "side": self._resolve_close_side(position.direction),
                    "activationPrice": activation_price,
                    "callbackRate": callback_rate,
                },
                "body": {
                    **prepared.body,
                    "resolved_from_entry_price": position.entry_price,
                    "resolved_activation_price": activation_price,
                    "resolved_callback_rate": callback_rate,
                    "resolution_mode": "trailing_from_quant_contract",
                },
            }
        )

    @staticmethod
    def _format_callback_rate_pct(callback_rate_pct: float) -> str:
        try:
            callback_rate = Decimal(str(callback_rate_pct))
        except InvalidOperation as exc:
            raise BinanceRequestMappingError("Trailing stop callback rate is not a valid decimal") from exc
        if callback_rate <= 0 or callback_rate > 10:
            raise BinanceRequestMappingError("Trailing stop callback rate must be between 0 and 10")
        return format(callback_rate.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP), "f")

    @staticmethod
    def _derive_trailing_activation_price(*, direction: str, reference_price: float, activation_ratio: float) -> str:
        try:
            reference = Decimal(str(reference_price))
            ratio = Decimal(str(activation_ratio))
        except InvalidOperation as exc:
            raise BinanceRequestMappingError("Trailing stop activation price inputs are not valid decimals") from exc
        if reference <= 0:
            raise BinanceRequestMappingError("Trailing stop requires a positive reference price")
        if direction == "long":
            if ratio <= 1:
                raise BinanceRequestMappingError("Long trailing activation ratio must be greater than 1")
        elif direction == "short":
            if ratio <= 0 or ratio >= 1:
                raise BinanceRequestMappingError("Short trailing activation ratio must be between 0 and 1")
        else:
            raise BinanceRequestMappingError("Trailing stop direction must be long or short")
        resolved = (reference * ratio).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
        if resolved <= 0:
            raise BinanceRequestMappingError("Resolved trailing activation price must be positive")
        return format(resolved, "f")

    @staticmethod
    def _derive_stop_price(*, direction: str, reference_price: float, stop_ratio: float) -> str:
        try:
            reference = Decimal(str(reference_price))
            ratio = Decimal(str(stop_ratio))
        except InvalidOperation as exc:
            raise BinanceRequestMappingError("Protective stop price inputs are not valid decimals") from exc
        if reference <= 0:
            raise BinanceRequestMappingError("Protective stop requires a positive reference price")
        if direction == "long":
            if ratio <= 0 or ratio >= 1:
                raise BinanceRequestMappingError("Long protective stop ratio must be between 0 and 1")
        elif direction == "short":
            if ratio <= 1:
                raise BinanceRequestMappingError("Short protective stop ratio must be greater than 1")
        else:
            raise BinanceRequestMappingError("Protective stop direction must be long or short")
        resolved = (reference * ratio).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
        if resolved <= 0:
            raise BinanceRequestMappingError("Resolved protective stop price must be positive")
        return format(resolved, "f")

    def _map_command_to_request(self, command: ExecutionCommand) -> PreparedAdapterRequest:
        if command.target == "entry_order":
            return self._build_order_request(
                command=command,
                side=self._resolve_entry_side(
                    action=getattr(command.payload, "action", ""),
                    direction=getattr(command.payload, "direction", ""),
                ),
                order_type="MARKET",
                new_order_resp_type=True,
            )
        if command.target == "reduce_order":
            return self._build_order_request(
                command=command,
                side=self._resolve_close_side_from_payload(command.payload),
                order_type="MARKET",
                reduce_only=True,
                new_order_resp_type=True,
            )
        if command.target == "exit_order":
            return self._build_order_request(
                command=command,
                side=self._resolve_close_side_from_payload(command.payload),
                order_type="MARKET",
                reduce_only=True,
                new_order_resp_type=True,
            )
        if command.target == "maintain_protective_stop":
            return self._build_order_request(
                command=command,
                side=self._resolve_close_side_from_payload(command.payload),
                order_type="STOP_MARKET",
                reduce_only=True,
                working_type="MARK_PRICE",
                include_payload_body=True,
                algo_order=True,
            )
        if command.target == "advance_breakeven_stop":
            return self._build_order_request(
                command=command,
                side=self._resolve_close_side_from_payload(command.payload),
                order_type="STOP_MARKET",
                reduce_only=True,
                working_type="MARK_PRICE",
                include_payload_body=True,
            )
        if command.target == "advance_trailing_stop":
            return self._build_order_request(
                command=command,
                side=self._resolve_close_side_from_payload(command.payload),
                order_type="TRAILING_STOP_MARKET",
                reduce_only=True,
                working_type="MARK_PRICE",
                include_payload_body=True,
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

    @staticmethod
    def _resolve_entry_side(*, action: str, direction: str = "") -> str:
        normalized_action = action.strip().lower()
        normalized_direction = direction.strip().lower()
        if normalized_action == "entry_long" or normalized_direction == "long":
            return "BUY"
        if normalized_action == "entry_short" or normalized_direction == "short":
            return "SELL"
        raise BinanceRequestMappingError("Entry order direction must be long or short")

    @staticmethod
    def _resolve_close_side_from_payload(payload: Any) -> str:
        return "SELL" if getattr(payload, "direction", "") == "long" else "BUY"

    @staticmethod
    def _build_order_request(
        *,
        command: ExecutionCommand,
        side: str,
        order_type: str,
        reduce_only: bool = False,
        working_type: str = "",
        new_order_resp_type: bool = False,
        include_payload_body: bool = False,
        algo_order: bool = False,
    ) -> PreparedAdapterRequest:
        client_id_key = "clientAlgoId" if algo_order else "newClientOrderId"
        params = {
            "symbol": "ETHUSDT",
            "side": side,
            "type": order_type,
            client_id_key: BinancePerpAdapter._build_client_order_id(command),
        }
        if algo_order:
            params["algoType"] = "CONDITIONAL"
        if reduce_only:
            params["reduceOnly"] = "true"
        if working_type:
            params["workingType"] = working_type
        if new_order_resp_type:
            params["newOrderRespType"] = "RESULT"
        return PreparedAdapterRequest(
            method="POST",
            path="/fapi/v1/algoOrder" if algo_order else "/fapi/v1/order",
            params=params,
            body=command.payload.model_dump(mode="json") if include_payload_body else {},
            idempotency_key=command.idempotency_key,
        )

    @staticmethod
    def _build_client_order_id(command: ExecutionCommand) -> str:
        aliases = {
            "entry_order": "eo",
            "reduce_order": "ro",
            "exit_order": "xo",
            "maintain_protective_stop": "ps",
            "advance_breakeven_stop": "be",
            "advance_trailing_stop": "ts",
        }
        alias = aliases.get(command.target, "cmd")
        digest = sha256(command.idempotency_key.encode("utf-8")).hexdigest()[:16]
        return f"ethbot-{alias}-{digest}"
