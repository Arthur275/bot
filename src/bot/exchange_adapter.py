from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_DOWN, ROUND_HALF_UP
from hashlib import sha256
from typing import Any

from .binance_transport import (
    BinanceRequestConfigError,
    BinanceRequestSigner,
    BinanceTransport,
    BinanceTransportError,
    TransportResponse,
)
from .config import RuntimeMode
from .exchange_command_builder import (
    build_execution_commands,
    build_idempotency_key,
    build_take_profit_commands,
    first_list,
    optional_float,
    resolve_entry_size_pct,
    resolve_execution_warnings,
    resolve_handoff_direction,
    resolve_primary_command_reason,
    resolve_take_profit_payloads,
)
from .exchange_models import (
    AdapterAction,
    AdapterCapabilities,
    AdapterCredentials,
    AdapterRuntimeSnapshot,
    BinanceRequestMappingError,
    BreakevenPayload,
    CommandExecutionResult,
    EntryOrderPayload,
    ExchangeAdapterProtocol,
    ExchangeRequestConfigError,
    ExchangeTransportError,
    ExecutionCommand,
    ExitOrderPayload,
    OrderSnapshot,
    PositionSnapshot,
    PreparedAdapterRequest,
    ProtectiveStopPayload,
    RecentFillsPayload,
    ReconciliationPayload,
    ReconciliationResult,
    ReduceOrderPayload,
    TakeProfitOrderPayload,
    TrailingStopPayload,
)
from .exchange_reconciliation import assess_runtime_reconciliation
from .okx_transport import OkxRequestConfigError, OkxRequestSigner, OkxTransport, OkxTransportError
from .position_manager import ExecutionPlan
from .time_utils import parse_datetime_utc, utc_now


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
        return build_execution_commands(execution_plan=execution_plan, handoff=handoff)

    def _build_take_profit_commands(self, *, handoff: dict[str, Any] | None, direction: str) -> list[ExecutionCommand]:
        return build_take_profit_commands(handoff=handoff, direction=direction)

    def _resolve_take_profit_payloads(self, *, handoff: dict[str, Any] | None, direction: str) -> list[TakeProfitOrderPayload]:
        return resolve_take_profit_payloads(handoff=handoff, direction=direction)

    @staticmethod
    def _resolve_primary_command_reason(execution_plan: ExecutionPlan) -> str:
        return resolve_primary_command_reason(execution_plan)

    @staticmethod
    def _resolve_entry_size_pct(*, execution_plan: ExecutionPlan, handoff: dict[str, Any] | None) -> float:
        return resolve_entry_size_pct(execution_plan=execution_plan, handoff=handoff)

    @staticmethod
    def _first_list(handoff: dict[str, Any], *keys: str) -> list[Any] | None:
        return first_list(handoff, *keys)

    @staticmethod
    def _optional_float(value: Any) -> float | None:
        return optional_float(value)

    def fetch_runtime_snapshot(self) -> AdapterRuntimeSnapshot:
        return AdapterRuntimeSnapshot(fetched_at=utc_now(), snapshot_valid=False)

    def assess_reconciliation(
        self,
        *,
        runtime_snapshot: AdapterRuntimeSnapshot,
        expected_position_state: str,
        expected_direction: str,
        expected_size_pct: float,
    ) -> ReconciliationResult:
        return assess_runtime_reconciliation(
            runtime_snapshot=runtime_snapshot,
            expected_position_state=expected_position_state,
            expected_direction=expected_direction,
            expected_size_pct=expected_size_pct,
            supports_real_execution=self.get_capabilities().supports_real_execution,
        )

    def get_capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities()

    @staticmethod
    def _resolve_handoff_direction(handoff: dict[str, Any] | None) -> str:
        return resolve_handoff_direction(handoff)

    @staticmethod
    def _build_idempotency_key(*, target: str, handoff: dict[str, Any] | None) -> str:
        return build_idempotency_key(target=target, handoff=handoff)

    @staticmethod
    def _resolve_execution_warnings(handoff: dict[str, Any] | None) -> list[str]:
        return resolve_execution_warnings(handoff)


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
            supports_take_profit_orders=True,
        )


class RealExchangeAdapter(BaseExchangeAdapter):
    def __init__(
        self,
        credentials: AdapterCredentials,
        *,
        signer: Any | None = None,
        transport: Any | None = None,
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
        for command, prepared in zip(commands, prepared_requests, strict=True):
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
                    except (BinanceRequestConfigError, OkxRequestConfigError) as exc:
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
                    except (BinanceTransportError, OkxTransportError) as exc:
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
            except (BinanceRequestConfigError, OkxRequestConfigError) as exc:
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
            except (BinanceTransportError, OkxTransportError) as exc:
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
            except (BinanceTransportError, OkxTransportError) as exc:
                if self._is_timestamp_outside_recv_window(exc):
                    try:
                        self._signer.refresh_timestamp_offset()
                        signed_request = self._signer.sign(prepared)
                        response = self._transport.send(signed_request)
                    except (BinanceRequestConfigError, OkxRequestConfigError, BinanceTransportError, OkxTransportError) as retry_exc:
                        exc = retry_exc if isinstance(retry_exc, (BinanceTransportError, OkxTransportError)) else exc
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
            if command.target == "reconcile_position_and_orders" and isinstance(self, BinancePerpAdapter):
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
                    except (BinanceRequestConfigError, OkxRequestConfigError, BinanceTransportError, OkxTransportError):
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
        for command, prepared in zip(commands, prepared_requests, strict=True):
            current_runtime_snapshot = runtime_snapshot
            if self._requires_runtime_snapshot(command):
                if current_runtime_snapshot is None:
                    try:
                        current_runtime_snapshot = self.fetch_runtime_snapshot()
                    except (BinanceRequestConfigError, OkxRequestConfigError) as exc:
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
                    except (BinanceTransportError, OkxTransportError) as exc:
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
            except (BinanceRequestConfigError, OkxRequestConfigError) as exc:
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
            except (BinanceTransportError, OkxTransportError) as exc:
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
                    except (BinanceRequestConfigError, OkxRequestConfigError, BinanceTransportError, OkxTransportError) as retry_exc:
                        exc = retry_exc if isinstance(retry_exc, (BinanceTransportError, OkxTransportError)) else exc
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
        return command.target in {"entry_order", "reduce_order", "exit_order", "maintain_protective_stop", "take_profit_order", "advance_breakeven_stop", "advance_trailing_stop"}

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
    def _is_timestamp_outside_recv_window(exc: ExchangeTransportError) -> bool:
        if not isinstance(exc, BinanceTransportError):
            return False
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
        body = prepared.body if isinstance(prepared.body, dict) else {}
        return str(
            params.get("newClientOrderId")
            or params.get("clientAlgoId")
            or params.get("clOrdId")
            or params.get("algoClOrdId")
            or body.get("clOrdId")
            or body.get("algoClOrdId")
            or ""
        )

    @staticmethod
    def _extract_exchange_order_id(payload: Any) -> str:
        if not isinstance(payload, dict):
            return ""
        if isinstance(payload.get("data"), list) and payload["data"]:
            first = payload["data"][0]
            if isinstance(first, dict):
                return str(first.get("ordId") or first.get("algoId") or "")
        return str(payload.get("orderId") or payload.get("algoId") or payload.get("ordId") or "")

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
        if command.target == "sync_recent_fills" and isinstance(payload, dict) and isinstance(payload.get("data"), list):
            latest = payload["data"][0] if payload["data"] else {}
            distinct_order_count = len(
                {
                    str(item.get("ordId") or "")
                    for item in payload["data"]
                    if isinstance(item, dict) and str(item.get("ordId") or "")
                }
            )
            return {
                "fill_count": len(payload["data"]),
                "distinct_order_count": distinct_order_count,
                "latest_trade_id": str((latest or {}).get("tradeId") or ""),
                "latest_order_id": str((latest or {}).get("ordId") or ""),
                "latest_side": str((latest or {}).get("side") or ""),
                "latest_price": str((latest or {}).get("fillPx") or ""),
                "latest_qty": str((latest or {}).get("fillSz") or ""),
                "latest_time": (latest or {}).get("ts"),
                "latest_realized_pnl": str((latest or {}).get("pnl") or ""),
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
            if isinstance(payload.get("data"), list) and payload["data"]:
                first = payload["data"][0]
                if isinstance(first, dict):
                    summary = {}
                    for key in ("ordId", "algoId", "sCode", "sMsg", "clOrdId", "algoClOrdId", "side", "ordType", "sz", "triggerPx", "px"):
                        if key in first:
                            summary[key] = first.get(key)
                    if command.target == "take_profit_order":
                        summary["resolved_take_profit_price"] = first.get("px") or first.get("price")
                        summary["resolved_reduce_qty"] = first.get("sz") or first.get("quantity")
                    return summary
            summary: dict[str, Any] = {}
            for key in ("orderId", "status", "symbol", "side", "type", "clientOrderId"):
                if key in payload:
                    summary[key] = payload.get(key)
            if command.target == "take_profit_order":
                prepared_body = command.payload.model_dump(mode="json")
                summary["resolved_take_profit_price"] = prepared_body.get("resolved_take_profit_price")
                summary["resolved_reduce_qty"] = prepared_body.get("resolved_reduce_qty")
            return summary
        return {}

    def get_capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            supports_real_execution=True,
            supports_recent_fill_sync=True,
            supports_trailing_stop_update=False,
            supports_breakeven_update=False,
            supports_take_profit_orders=True,
        )


class BinancePerpAdapter(RealExchangeAdapter):
    ENTRY_MARK_PRICE_MAX_AGE_SEC = 15.0

    def fetch_runtime_snapshot(self) -> AdapterRuntimeSnapshot:
        fetched_at = utc_now()
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
            mark_price_fetched_at=fetched_at if position_snapshot.mark_price is not None else None,
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
        entry_price = BinancePerpAdapter._to_optional_positive_float((current or {}).get("entryPrice"))
        mark_price = BinancePerpAdapter._to_optional_positive_float((current or {}).get("markPrice"))
        leverage = BinancePerpAdapter._to_optional_int((current or {}).get("leverage"))
        size_pct = BinancePerpAdapter._resolve_position_size_pct(current=current, account_payload=account_payload, leverage=leverage)
        return PositionSnapshot(
            position_state="ENTERED" if position_amt != 0.0 else "FLAT",
            direction=direction,
            size_pct=size_pct,
            position_amt=position_amt,
            entry_price=entry_price,
            mark_price=mark_price,
            leverage=leverage,
            unrealized_pnl_usd=BinancePerpAdapter._to_optional_float_preserve_zero((current or {}).get("unRealizedProfit")),
            price_vs_entry_pct=BinancePerpAdapter._price_vs_entry_pct(
                entry_price=entry_price,
                mark_price=mark_price,
                direction=direction,
            ),
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
        notional = BinancePerpAdapter._to_optional_float_preserve_zero(current.get("notional"))
        if notional is None:
            entry_price = BinancePerpAdapter._to_optional_positive_float(current.get("entryPrice"))
            position_amt = BinancePerpAdapter._to_optional_float_preserve_zero(current.get("positionAmt"))
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
    def _price_vs_entry_pct(*, entry_price: float | None, mark_price: float | None, direction: str) -> float | None:
        if entry_price is None or mark_price is None or entry_price <= 0.0:
            return None
        raw = (mark_price - entry_price) / entry_price
        return -raw if direction == "short" else raw

    @staticmethod
    def _extract_account_equity_with_source(account_payload: Any) -> tuple[float | None, str]:
        if not isinstance(account_payload, dict):
            return None, ""
        for key in ("totalWalletBalance", "totalMarginBalance", "totalCrossWalletBalance"):
            value = BinancePerpAdapter._to_optional_positive_float(account_payload.get(key))
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
                    value = BinancePerpAdapter._to_optional_positive_float(asset.get(key))
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
                    client_order_id=str(item.get("clientOrderId") or ""),
                    order_type=str(item.get("type") or ""),
                    status=str(item.get("status") or "open"),
                    side=str(item.get("side") or ""),
                    reduce_only=reduce_only,
                    quantity=BinancePerpAdapter._to_optional_positive_float(item.get("origQty") or item.get("quantity")),
                    price=BinancePerpAdapter._to_optional_positive_float(item.get("price")),
                    trigger_price=BinancePerpAdapter._to_optional_positive_float(item.get("stopPrice")),
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
                    client_order_id=str(item.get("clientAlgoId") or ""),
                    order_type=str(item.get("orderType") or item.get("type") or ""),
                    status=algo_status.lower(),
                    side=str(item.get("side") or ""),
                    reduce_only=reduce_only,
                    quantity=BinancePerpAdapter._to_optional_positive_float(item.get("origQty") or item.get("quantity")),
                    price=BinancePerpAdapter._to_optional_positive_float(item.get("price")),
                    trigger_price=BinancePerpAdapter._to_optional_positive_float(item.get("triggerPrice") or item.get("stopPrice")),
                )
            )
        return orders

    @staticmethod
    def _has_protective_stop(open_orders: list[OrderSnapshot]) -> bool:
        protective_types = {"STOP", "STOP_MARKET", "TRAILING_STOP_MARKET"}
        return any(order.reduce_only and order.order_type in protective_types for order in open_orders)

    @staticmethod
    def _to_optional_float_preserve_zero(value: Any) -> float | None:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _to_optional_positive_float(value: Any) -> float | None:
        parsed = BinancePerpAdapter._to_optional_float_preserve_zero(value)
        if parsed is None or parsed <= 0.0:
            return None
        return parsed

    _to_optional_float = _to_optional_positive_float

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
        if command.target == "take_profit_order":
            return self._resolve_take_profit_order_request(command=command, prepared=prepared, runtime_snapshot=runtime_snapshot)
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
        mark_price, mark_price_source, mark_price_age_sec = self._resolve_entry_mark_price(
            symbol_contract=symbol_contract,
            runtime_snapshot=runtime_snapshot,
        )
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
                    "mark_price_source": mark_price_source,
                    "mark_price_age_sec": mark_price_age_sec,
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

    def _resolve_take_profit_order_request(
        self,
        *,
        command: ExecutionCommand,
        prepared: PreparedAdapterRequest,
        runtime_snapshot: AdapterRuntimeSnapshot | None,
    ) -> PreparedAdapterRequest:
        payload = command.payload
        if not isinstance(payload, TakeProfitOrderPayload):
            raise BinanceRequestMappingError("Take-profit payload is required for real take-profit execution")
        position = self._resolve_live_position_for_stop(
            runtime_snapshot=runtime_snapshot,
            payload_direction=payload.direction,
            error_context="Take-profit order",
        )
        price = self._derive_take_profit_price(
            direction=position.direction,
            reference_price=position.entry_price,
            price_ratio=payload.price_ratio,
        )
        quantity = self._resolve_reduce_quantity(
            position_amt=position.position_amt,
            reduce_fraction=payload.reduce_fraction,
            reduce_qty=payload.reduce_qty,
            quantity_label="take-profit",
        )
        return prepared.model_copy(
            update={
                "params": {
                    **prepared.params,
                    "side": self._resolve_close_side(position.direction),
                    "price": price,
                    "quantity": quantity,
                },
                "body": {
                    **prepared.body,
                    "resolved_from_entry_price": position.entry_price,
                    "resolved_take_profit_price": price,
                    "resolved_position_amt": self._format_exit_quantity(position.position_amt),
                    "resolved_reduce_qty": quantity,
                    "resolution_mode": "take_profit_from_live_entry",
                },
            }
        )

    @staticmethod
    def _resolve_entry_leverage(runtime_snapshot: AdapterRuntimeSnapshot) -> int:
        leverage = runtime_snapshot.position.leverage or 10
        if leverage <= 0:
            raise BinanceRequestMappingError("Real entry order requires a positive live leverage")
        return leverage

    @staticmethod
    def _resolve_entry_mark_price(
        *,
        symbol_contract: dict[str, Decimal],
        runtime_snapshot: AdapterRuntimeSnapshot,
    ) -> tuple[Decimal, str, float | None]:
        runtime_mark_price = runtime_snapshot.position.mark_price
        mark_price_timestamp = runtime_snapshot.mark_price_fetched_at or runtime_snapshot.fetched_at
        mark_price_age_sec = BinancePerpAdapter._mark_price_age_sec(mark_price_timestamp)
        if (
            runtime_mark_price is not None
            and runtime_mark_price > 0.0
            and mark_price_age_sec is not None
            and mark_price_age_sec <= BinancePerpAdapter.ENTRY_MARK_PRICE_MAX_AGE_SEC
        ):
            return Decimal(str(runtime_mark_price)), "runtime_snapshot", mark_price_age_sec
        mark_price = symbol_contract["mark_price"]
        if mark_price <= 0:
            raise BinanceRequestMappingError("Real entry order requires a positive mark price")
        return mark_price, "premium_index", mark_price_age_sec

    @staticmethod
    def _mark_price_age_sec(mark_price_timestamp: datetime | None) -> float | None:
        if mark_price_timestamp is None:
            return None
        normalized_timestamp = parse_datetime_utc(mark_price_timestamp)
        if normalized_timestamp is None:
            return None
        return max(0.0, (utc_now() - normalized_timestamp).total_seconds())

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
    def _resolve_reduce_quantity(
        *,
        position_amt: float | None,
        reduce_fraction: float | None,
        reduce_qty: float | None,
        quantity_label: str,
    ) -> str:
        try:
            position_quantity = Decimal(str(abs(position_amt or 0.0)))
        except InvalidOperation as exc:
            raise BinanceRequestMappingError(f"Real {quantity_label} order position amount is not a valid decimal") from exc
        if position_quantity <= 0:
            raise BinanceRequestMappingError(f"Real {quantity_label} order requires a positive live position amount")
        if (reduce_fraction is None) == (reduce_qty is None):
            raise BinanceRequestMappingError(f"Real {quantity_label} order requires exactly one of reduce_fraction or reduce_qty")
        if reduce_qty is not None:
            quantity = Decimal(str(reduce_qty))
        else:
            quantity = position_quantity * Decimal(str(reduce_fraction))
        if quantity <= 0:
            raise BinanceRequestMappingError(f"Real {quantity_label} order requires a positive reduce quantity")
        if quantity > position_quantity:
            raise BinanceRequestMappingError(f"Real {quantity_label} order reduce quantity exceeds live position amount")
        return format(quantity.normalize(), "f")

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
    def _derive_take_profit_price(*, direction: str, reference_price: float, price_ratio: float) -> str:
        try:
            reference = Decimal(str(reference_price))
            ratio = Decimal(str(price_ratio))
        except InvalidOperation as exc:
            raise BinanceRequestMappingError("Take-profit price inputs are not valid decimals") from exc
        if reference <= 0:
            raise BinanceRequestMappingError("Take-profit order requires a positive reference price")
        if direction == "long":
            if ratio <= 1:
                raise BinanceRequestMappingError("Long take-profit price ratio must be greater than 1")
        elif direction == "short":
            if ratio <= 0 or ratio >= 1:
                raise BinanceRequestMappingError("Short take-profit price ratio must be between 0 and 1")
        else:
            raise BinanceRequestMappingError("Take-profit direction must be long or short")
        resolved = (reference * ratio).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
        if resolved <= 0:
            raise BinanceRequestMappingError("Resolved take-profit price must be positive")
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
        if command.target == "take_profit_order":
            return self._build_order_request(
                command=command,
                side=self._resolve_close_side_from_payload(command.payload),
                order_type="LIMIT",
                reduce_only=True,
                include_payload_body=True,
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
        if order_type == "LIMIT":
            params["timeInForce"] = "GTC"
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
            "take_profit_order": "tp",
            "advance_breakeven_stop": "be",
            "advance_trailing_stop": "ts",
        }
        alias = aliases.get(command.target, "cmd")
        digest = sha256(command.idempotency_key.encode("utf-8")).hexdigest()[:16]
        return f"ethbot-{alias}-{digest}"


class OkxUsdtSwapAdapter(RealExchangeAdapter):
    ENTRY_MARK_PRICE_MAX_AGE_SEC = 15.0
    INST_ID = "ETH-USDT-SWAP"

    def __init__(
        self,
        credentials: AdapterCredentials,
        *,
        signer: OkxRequestSigner | None = None,
        transport: OkxTransport | None = None,
    ) -> None:
        self._credentials = credentials
        self._signer = signer or OkxRequestSigner(credentials)
        self._transport = transport or OkxTransport(credentials)

    def fetch_runtime_snapshot(self) -> AdapterRuntimeSnapshot:
        fetched_at = utc_now()
        try:
            positions_response = self._send_snapshot_request(
                PreparedAdapterRequest(
                    method="GET",
                    path="/api/v5/account/positions",
                    params={"instId": self.INST_ID},
                )
            )
            orders_response = self._send_snapshot_request(
                PreparedAdapterRequest(
                    method="GET",
                    path="/api/v5/trade/orders-pending",
                    params={"instId": self.INST_ID},
                )
            )
            balance_response = self._send_snapshot_request(
                PreparedAdapterRequest(
                    method="GET",
                    path="/api/v5/account/balance",
                    params={"ccy": "USDT"},
                )
            )
        except OkxRequestConfigError:
            raise
        except OkxTransportError as exc:
            return self._invalid_runtime_snapshot(
                fetched_at=fetched_at,
                endpoint=self._error_endpoint_from_payload(exc.payload),
                exc=exc,
            )
        algo_payload: Any = []
        try:
            algo_response = self._send_snapshot_request(
                PreparedAdapterRequest(
                    method="GET",
                    path="/api/v5/trade/orders-algo-pending",
                    params={"instId": self.INST_ID, "ordType": "conditional"},
                )
            )
            algo_payload = algo_response.payload
        except OkxTransportError as exc:
            return self._invalid_runtime_snapshot(
                fetched_at=fetched_at,
                endpoint="/api/v5/trade/orders-algo-pending",
                exc=exc,
            )
        position_snapshot = self._build_position_snapshot(
            positions_response.payload,
            balance_payload=balance_response.payload,
        )
        open_orders = [
            *self._build_open_orders_snapshot(orders_response.payload),
            *self._build_open_algo_orders_snapshot(algo_payload),
        ]
        account_equity, account_equity_source = self._extract_account_equity_with_source(balance_response.payload)
        return AdapterRuntimeSnapshot(
            fetched_at=fetched_at,
            mark_price_fetched_at=fetched_at if position_snapshot.mark_price is not None else None,
            position=position_snapshot,
            open_orders=open_orders,
            protective_stop_present=self._has_protective_stop(open_orders),
            account_equity=account_equity,
            account_equity_source=account_equity_source,
        )

    def _send_snapshot_request(self, request: PreparedAdapterRequest) -> TransportResponse:
        return self._transport.send(self._signer.sign(request))

    @staticmethod
    def _invalid_runtime_snapshot(
        *,
        fetched_at: datetime,
        endpoint: str,
        exc: OkxTransportError,
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

    @staticmethod
    def _error_endpoint_from_payload(payload: Any) -> str:
        if isinstance(payload, dict):
            return str(payload.get("endpoint") or payload.get("path") or "")
        return ""

    def prepare_requests(self, *, commands: list[ExecutionCommand]) -> list[PreparedAdapterRequest]:
        return [self._map_command_to_request(command) for command in commands]

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
        if command.target == "exit_order":
            return self._resolve_exit_order_request(command=command, prepared=prepared, runtime_snapshot=runtime_snapshot)
        if command.target == "reduce_order":
            raise BinanceRequestMappingError("Real OKX reduce order requires an explicit reduce size contract from quant handoff")
        if command.target == "maintain_protective_stop":
            return self._resolve_protective_stop_request(command=command, prepared=prepared, runtime_snapshot=runtime_snapshot)
        if command.target == "take_profit_order":
            return self._resolve_take_profit_order_request(command=command, prepared=prepared, runtime_snapshot=runtime_snapshot)
        if command.target in {"advance_breakeven_stop", "advance_trailing_stop"}:
            raise BinanceRequestMappingError("Real OKX stop replace is not enabled until cancel/replace reconciliation is implemented")
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
            raise BinanceRequestMappingError("Entry order payload is required for real OKX entry execution")
        runtime_snapshot = self._require_valid_runtime_snapshot(runtime_snapshot=runtime_snapshot, error_context="Entry order")
        if runtime_snapshot.position.position_state == "ENTERED":
            raise BinanceRequestMappingError("Real OKX entry order requires a flat live position")
        position_size_pct = float(payload.position_size_pct or 0.0)
        if position_size_pct <= 0.0:
            raise BinanceRequestMappingError("Real OKX entry order requires a positive position_size_pct")
        account_equity = self._resolve_account_equity_for_entry(runtime_snapshot)
        contract = self._fetch_symbol_contract()
        leverage = runtime_snapshot.position.leverage or 10
        if leverage <= 0:
            raise BinanceRequestMappingError("Real OKX entry order requires a positive leverage")
        mark_price, mark_price_source, mark_price_age_sec = self._resolve_entry_mark_price(
            contract=contract,
            runtime_snapshot=runtime_snapshot,
        )
        size = self._derive_entry_contract_size(
            position_size_pct=position_size_pct,
            account_equity=account_equity,
            leverage=leverage,
            mark_price=mark_price,
            contract_value=contract["contract_value"],
            min_size=contract["min_size"],
            lot_size=contract["lot_size"],
        )
        body = {
            **prepared.body,
            "sz": size,
        }
        return prepared.model_copy(
            update={
                "body": {
                    **body,
                    "resolved_size": size,
                    "resolved_mark_price": format(mark_price, "f"),
                    "mark_price_source": mark_price_source,
                    "mark_price_age_sec": mark_price_age_sec,
                    "resolved_account_equity": format(account_equity, "f"),
                    "resolved_leverage": leverage,
                    "resolved_lot_size": format(contract["lot_size"], "f"),
                    "resolved_min_size": format(contract["min_size"], "f"),
                    "resolved_contract_value": format(contract["contract_value"], "f"),
                    "resolution_mode": "okx_entry_contracts_from_size_pct",
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
            raise BinanceRequestMappingError("Exit order payload is required for real OKX exit execution")
        position = self._resolve_live_position_for_exit(
            runtime_snapshot=runtime_snapshot,
            payload_direction=payload.direction,
        )
        size = self._format_position_contract_size(position.position_amt)
        return prepared.model_copy(
            update={
                "body": {
                    **prepared.body,
                    "side": self._resolve_close_side(position.direction),
                    "sz": size,
                    "resolved_position_amt": size,
                    "resolution_mode": "okx_exit_size_from_live_position",
                },
            }
        )

    def _resolve_protective_stop_request(
        self,
        *,
        command: ExecutionCommand,
        prepared: PreparedAdapterRequest,
        runtime_snapshot: AdapterRuntimeSnapshot | None,
    ) -> PreparedAdapterRequest:
        payload = command.payload
        if not isinstance(payload, ProtectiveStopPayload):
            raise BinanceRequestMappingError("Protective stop payload is required for real OKX stop execution")
        position = self._resolve_live_position_for_stop(
            runtime_snapshot=runtime_snapshot,
            payload_direction=payload.direction,
            error_context="Protective stop",
        )
        if payload.initial_stop_loss is None:
            raise BinanceRequestMappingError("Real OKX protective stop requires initial_stop_loss")
        trigger_price = self._derive_stop_price(
            direction=position.direction,
            reference_price=position.entry_price,
            stop_ratio=payload.initial_stop_loss,
        )
        size = self._format_position_contract_size(position.position_amt)
        return prepared.model_copy(
            update={
                "body": {
                    **prepared.body,
                    "side": self._resolve_close_side(position.direction),
                    "sz": size,
                    "triggerPx": trigger_price,
                    "orderPx": "-1",
                    "resolved_from_entry_price": position.entry_price,
                    "resolved_stop_price": trigger_price,
                    "resolved_position_amt": size,
                    "resolution_mode": "okx_initial_stop_from_live_entry",
                },
            }
        )

    def _resolve_take_profit_order_request(
        self,
        *,
        command: ExecutionCommand,
        prepared: PreparedAdapterRequest,
        runtime_snapshot: AdapterRuntimeSnapshot | None,
    ) -> PreparedAdapterRequest:
        payload = command.payload
        if not isinstance(payload, TakeProfitOrderPayload):
            raise BinanceRequestMappingError("Take-profit payload is required for real OKX take-profit execution")
        position = self._resolve_live_position_for_stop(
            runtime_snapshot=runtime_snapshot,
            payload_direction=payload.direction,
            error_context="Take-profit order",
        )
        price = BinancePerpAdapter._derive_take_profit_price(
            direction=position.direction,
            reference_price=position.entry_price,
            price_ratio=payload.price_ratio,
        )
        size = BinancePerpAdapter._resolve_reduce_quantity(
            position_amt=position.position_amt,
            reduce_fraction=payload.reduce_fraction,
            reduce_qty=payload.reduce_qty,
            quantity_label="OKX take-profit",
        )
        return prepared.model_copy(
            update={
                "body": {
                    **prepared.body,
                    "side": self._resolve_close_side(position.direction),
                    "sz": size,
                    "px": price,
                    "resolved_from_entry_price": position.entry_price,
                    "resolved_take_profit_price": price,
                    "resolved_position_amt": self._format_position_contract_size(position.position_amt),
                    "resolved_reduce_qty": size,
                    "resolution_mode": "okx_take_profit_from_live_entry",
                },
            }
        )

    def _map_command_to_request(self, command: ExecutionCommand) -> PreparedAdapterRequest:
        if command.target == "entry_order":
            return self._build_order_request(
                command=command,
                side=self._resolve_entry_side(
                    action=getattr(command.payload, "action", ""),
                    direction=getattr(command.payload, "direction", ""),
                ),
                reduce_only=False,
            )
        if command.target == "exit_order":
            return self._build_order_request(
                command=command,
                side=self._resolve_close_side_from_payload(command.payload),
                reduce_only=True,
            )
        if command.target == "reduce_order":
            return self._build_order_request(
                command=command,
                side=self._resolve_close_side_from_payload(command.payload),
                reduce_only=True,
            )
        if command.target == "maintain_protective_stop":
            return self._build_algo_order_request(command=command)
        if command.target == "take_profit_order":
            return self._build_take_profit_order_request(command=command)
        if command.target == "sync_recent_fills":
            return PreparedAdapterRequest(
                method="GET",
                path="/api/v5/trade/fills",
                params={"instId": self.INST_ID, "limit": 20},
                idempotency_key=command.idempotency_key,
            )
        if command.target == "reconcile_position_and_orders":
            return PreparedAdapterRequest(
                method="GET",
                path="/api/v5/account/positions",
                params={"instId": self.INST_ID},
                idempotency_key=command.idempotency_key,
            )
        return PreparedAdapterRequest(
            method="GET",
            path="/api/v5/public/time",
            requires_auth=False,
            idempotency_key=command.idempotency_key,
        )

    @staticmethod
    def _build_order_request(*, command: ExecutionCommand, side: str, reduce_only: bool) -> PreparedAdapterRequest:
        body = {
            "instId": OkxUsdtSwapAdapter.INST_ID,
            "tdMode": "cross",
            "side": side,
            "ordType": "market",
            "clOrdId": OkxUsdtSwapAdapter._build_client_order_id(command),
        }
        if reduce_only:
            body["reduceOnly"] = "true"
        return PreparedAdapterRequest(
            method="POST",
            path="/api/v5/trade/order",
            body=body,
            idempotency_key=command.idempotency_key,
        )

    @staticmethod
    def _build_take_profit_order_request(*, command: ExecutionCommand) -> PreparedAdapterRequest:
        return PreparedAdapterRequest(
            method="POST",
            path="/api/v5/trade/order",
            body={
                "instId": OkxUsdtSwapAdapter.INST_ID,
                "tdMode": "cross",
                "side": OkxUsdtSwapAdapter._resolve_close_side_from_payload(command.payload),
                "ordType": "limit",
                "reduceOnly": "true",
                "clOrdId": OkxUsdtSwapAdapter._build_client_order_id(command),
            },
            idempotency_key=command.idempotency_key,
        )

    @staticmethod
    def _build_algo_order_request(*, command: ExecutionCommand) -> PreparedAdapterRequest:
        return PreparedAdapterRequest(
            method="POST",
            path="/api/v5/trade/order-algo",
            body={
                "instId": OkxUsdtSwapAdapter.INST_ID,
                "tdMode": "cross",
                "ordType": "conditional",
                "algoClOrdId": OkxUsdtSwapAdapter._build_client_order_id(command),
                "closeFraction": "1",
            },
            idempotency_key=command.idempotency_key,
        )

    def fetch_open_algo_orders_raw(self) -> list[dict[str, Any]]:
        response = self._send_snapshot_request(
            PreparedAdapterRequest(
                method="GET",
                path="/api/v5/trade/orders-algo-pending",
                params={"instId": self.INST_ID, "ordType": "conditional"},
            )
        )
        data = self._okx_data(response.payload)
        return [item for item in data if isinstance(item, dict)]

    def cancel_algo_order_raw(self, *, algo_id: str = "", client_algo_id: str = "") -> dict[str, Any]:
        if not algo_id and not client_algo_id:
            raise BinanceRequestMappingError("Cancel OKX algo order requires algo_id or client_algo_id")
        body = {
            "instId": self.INST_ID,
            **({"algoId": algo_id} if algo_id else {}),
            **({"algoClOrdId": client_algo_id} if client_algo_id else {}),
        }
        response = self._send_snapshot_request(
            PreparedAdapterRequest(method="POST", path="/api/v5/trade/cancel-algos", body=[body])
        )
        return response.payload if isinstance(response.payload, dict) else {"payload": response.payload}

    def place_algo_order_raw(self, *, params: dict[str, Any]) -> dict[str, Any]:
        body = dict(params)
        if "symbol" in body and "instId" not in body:
            body["instId"] = self.INST_ID
            body.pop("symbol", None)
        if "type" in body and "ordType" not in body:
            order_type = str(body.pop("type") or "")
            body["ordType"] = "conditional" if order_type.upper() == "STOP_MARKET" else order_type
        if "quantity" in body and "sz" not in body:
            body["sz"] = str(body.pop("quantity"))
        if "triggerPrice" in body and "triggerPx" not in body:
            body["triggerPx"] = str(body.pop("triggerPrice"))
        if "clientAlgoId" in body and "algoClOrdId" not in body:
            body["algoClOrdId"] = str(body.pop("clientAlgoId"))
        body.pop("algoType", None)
        body.pop("workingType", None)
        if "tdMode" not in body:
            body["tdMode"] = "cross"
        if "ordType" not in body:
            body["ordType"] = "conditional"
        if str(body.get("ordType") or "").lower() == "conditional" and str(body.get("orderPx") or "") == "":
            body["orderPx"] = "-1"
        response = self._send_snapshot_request(
            PreparedAdapterRequest(method="POST", path="/api/v5/trade/order-algo", body=body)
        )
        return response.payload if isinstance(response.payload, dict) else {"payload": response.payload}

    def fetch_user_trades_raw(
        self,
        *,
        symbol: str = "ETH-USDT-SWAP",
        limit: int = 100,
        start_time_ms: int | None = None,
        end_time_ms: int | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"instId": symbol or self.INST_ID, "limit": limit}
        if start_time_ms is not None:
            params["begin"] = start_time_ms
        if end_time_ms is not None:
            params["end"] = end_time_ms
        response = self._send_snapshot_request(
            PreparedAdapterRequest(method="GET", path="/api/v5/trade/fills", params=params)
        )
        data = self._okx_data(response.payload)
        return [item for item in data if isinstance(item, dict)]

    @staticmethod
    def _build_position_snapshot(payload: Any, *, balance_payload: Any = None) -> PositionSnapshot:
        current = OkxUsdtSwapAdapter._first_okx_item(payload)
        pos_raw = current.get("pos") if current else None
        position_amt = BinancePerpAdapter._to_optional_float_preserve_zero(pos_raw)
        position_amt = position_amt if position_amt is not None else 0.0
        pos_side = str((current or {}).get("posSide") or "").lower()
        direction = "neutral"
        if position_amt > 0:
            direction = "short" if pos_side == "short" else "long"
        elif position_amt < 0:
            direction = "short"
        entry_price = BinancePerpAdapter._to_optional_positive_float((current or {}).get("avgPx"))
        mark_price = BinancePerpAdapter._to_optional_positive_float((current or {}).get("markPx"))
        leverage = BinancePerpAdapter._to_optional_int((current or {}).get("lever"))
        size_pct = OkxUsdtSwapAdapter._resolve_position_size_pct(current=current, balance_payload=balance_payload, leverage=leverage)
        return PositionSnapshot(
            position_state="ENTERED" if position_amt != 0.0 else "FLAT",
            direction=direction,
            size_pct=size_pct,
            position_amt=position_amt,
            entry_price=entry_price,
            mark_price=mark_price,
            leverage=leverage,
            unrealized_pnl_usd=BinancePerpAdapter._to_optional_float_preserve_zero((current or {}).get("upl")),
            unrealized_pnl_pct_on_margin=BinancePerpAdapter._to_optional_float_preserve_zero((current or {}).get("uplRatio")),
            price_vs_entry_pct=BinancePerpAdapter._price_vs_entry_pct(
                entry_price=entry_price,
                mark_price=mark_price,
                direction=direction,
            ),
        )

    @staticmethod
    def _resolve_position_size_pct(*, current: dict[str, Any], balance_payload: Any, leverage: int | None) -> float:
        notional = BinancePerpAdapter._to_optional_float_preserve_zero(current.get("notionalUsd"))
        if notional is None:
            notional = BinancePerpAdapter._to_optional_float_preserve_zero(current.get("notionalUsdForBorrow"))
        equity = OkxUsdtSwapAdapter._extract_account_equity(balance_payload)
        if notional is None or notional <= 0.0 or equity is None or equity <= 0.0:
            return 0.0
        live_leverage = leverage or 10
        denominator = equity * float(live_leverage)
        if denominator <= 0.0:
            return 0.0
        return max(0.0, min(1.0, round(abs(notional) / denominator, 4)))

    @staticmethod
    def _extract_account_equity(balance_payload: Any) -> float | None:
        value, _ = OkxUsdtSwapAdapter._extract_account_equity_with_source(balance_payload)
        return value

    @staticmethod
    def _extract_account_equity_with_source(balance_payload: Any) -> tuple[float | None, str]:
        current = OkxUsdtSwapAdapter._first_okx_item(balance_payload)
        if not current:
            return None, ""
        for key in ("totalEq", "adjEq"):
            value = BinancePerpAdapter._to_optional_positive_float(current.get(key))
            if value is not None and value > 0.0:
                return value, key
        details = current.get("details")
        if isinstance(details, list):
            for item in details:
                if not isinstance(item, dict) or str(item.get("ccy") or "") != "USDT":
                    continue
                for key in ("eq", "cashBal", "availEq"):
                    value = BinancePerpAdapter._to_optional_positive_float(item.get(key))
                    if value is not None and value > 0.0:
                        return value, f"details.USDT.{key}"
        return None, ""

    @staticmethod
    def _build_open_orders_snapshot(payload: Any) -> list[OrderSnapshot]:
        orders: list[OrderSnapshot] = []
        for item in OkxUsdtSwapAdapter._okx_data(payload):
            if not isinstance(item, dict):
                continue
            orders.append(
                OrderSnapshot(
                    order_id=str(item.get("ordId") or item.get("clOrdId") or ""),
                    client_order_id=str(item.get("clOrdId") or ""),
                    order_type=str(item.get("ordType") or ""),
                    status=str(item.get("state") or "open"),
                    side=str(item.get("side") or "").upper(),
                    reduce_only=BinancePerpAdapter._to_bool(item.get("reduceOnly")),
                    quantity=BinancePerpAdapter._to_optional_positive_float(item.get("sz")),
                    price=BinancePerpAdapter._to_optional_positive_float(item.get("px")),
                    trigger_price=None,
                )
            )
        return orders

    @staticmethod
    def _build_open_algo_orders_snapshot(payload: Any) -> list[OrderSnapshot]:
        orders: list[OrderSnapshot] = []
        for item in OkxUsdtSwapAdapter._okx_data(payload):
            if not isinstance(item, dict):
                continue
            state = str(item.get("state") or "").lower()
            if state and state not in {"live", "effective"}:
                continue
            close_fraction = str(item.get("closeFraction") or "")
            reduce_only = close_fraction == "1" or BinancePerpAdapter._to_bool(item.get("reduceOnly"))
            orders.append(
                OrderSnapshot(
                    order_id=f"algo:{item.get('algoId') or item.get('algoClOrdId') or ''}",
                    client_order_id=str(item.get("algoClOrdId") or ""),
                    order_type=str(item.get("ordType") or "conditional").upper(),
                    status=state or "live",
                    side=str(item.get("side") or "").upper(),
                    reduce_only=reduce_only,
                    quantity=BinancePerpAdapter._to_optional_positive_float(item.get("sz")),
                    price=BinancePerpAdapter._to_optional_positive_float(item.get("orderPx")),
                    trigger_price=BinancePerpAdapter._to_optional_positive_float(item.get("triggerPx")),
                )
            )
        return orders

    @staticmethod
    def _has_protective_stop(open_orders: list[OrderSnapshot]) -> bool:
        return any(order.reduce_only and order.order_type.upper() in {"CONDITIONAL", "STOP", "STOP_MARKET"} for order in open_orders)

    def _fetch_symbol_contract(self) -> dict[str, Decimal]:
        instrument_response = self._transport.send(
            self._signer.sign(
                PreparedAdapterRequest(
                    method="GET",
                    path="/api/v5/public/instruments",
                    requires_auth=False,
                    params={"instType": "SWAP", "instId": self.INST_ID},
                )
            )
        )
        ticker_response = self._transport.send(
            self._signer.sign(
                PreparedAdapterRequest(
                    method="GET",
                    path="/api/v5/market/ticker",
                    requires_auth=False,
                    params={"instId": self.INST_ID},
                )
            )
        )
        return self._extract_symbol_contract(instrument_response.payload, ticker_response.payload)

    @staticmethod
    def _extract_symbol_contract(instrument_payload: Any, ticker_payload: Any) -> dict[str, Decimal]:
        instrument = OkxUsdtSwapAdapter._first_okx_item(instrument_payload)
        if not instrument:
            raise BinanceRequestMappingError("Real OKX entry order requires instrument metadata")
        lot_size = BinancePerpAdapter._to_positive_decimal(instrument.get("lotSz"), error_message="Real OKX entry order requires a positive lotSz")
        min_size = BinancePerpAdapter._to_positive_decimal(instrument.get("minSz"), error_message="Real OKX entry order requires a positive minSz")
        contract_value = BinancePerpAdapter._to_positive_decimal(instrument.get("ctVal"), error_message="Real OKX entry order requires a positive ctVal")
        ticker = OkxUsdtSwapAdapter._first_okx_item(ticker_payload)
        mark_price = BinancePerpAdapter._to_positive_decimal(
            (ticker or {}).get("last"),
            error_message="Real OKX entry order requires a positive mark price",
        )
        return {
            "lot_size": lot_size,
            "min_size": min_size,
            "contract_value": contract_value,
            "mark_price": mark_price,
        }

    @staticmethod
    def _derive_entry_contract_size(
        *,
        position_size_pct: float,
        account_equity: Decimal,
        leverage: int,
        mark_price: Decimal,
        contract_value: Decimal,
        min_size: Decimal,
        lot_size: Decimal,
    ) -> str:
        try:
            size_pct = Decimal(str(position_size_pct))
            leverage_decimal = Decimal(str(leverage))
        except InvalidOperation as exc:
            raise BinanceRequestMappingError("Real OKX entry order size inputs are not valid decimals") from exc
        if size_pct <= 0 or size_pct > 1:
            raise BinanceRequestMappingError("Real OKX entry order requires position_size_pct between 0 and 1")
        raw_size = size_pct * account_equity * leverage_decimal / (mark_price * contract_value)
        size = raw_size.quantize(lot_size, rounding=ROUND_DOWN)
        if size < min_size:
            raise BinanceRequestMappingError("Real OKX entry order resolved size is below minSz")
        if size <= 0:
            raise BinanceRequestMappingError("Real OKX entry order resolved size must be positive")
        return format(size, "f")

    @staticmethod
    def _resolve_entry_mark_price(
        *,
        contract: dict[str, Decimal],
        runtime_snapshot: AdapterRuntimeSnapshot,
    ) -> tuple[Decimal, str, float | None]:
        runtime_mark_price = runtime_snapshot.position.mark_price
        mark_price_timestamp = runtime_snapshot.mark_price_fetched_at or runtime_snapshot.fetched_at
        mark_price_age_sec = BinancePerpAdapter._mark_price_age_sec(mark_price_timestamp)
        if (
            runtime_mark_price is not None
            and runtime_mark_price > 0.0
            and mark_price_age_sec is not None
            and mark_price_age_sec <= OkxUsdtSwapAdapter.ENTRY_MARK_PRICE_MAX_AGE_SEC
        ):
            return Decimal(str(runtime_mark_price)), "runtime_snapshot", mark_price_age_sec
        mark_price = contract["mark_price"]
        if mark_price <= 0:
            raise BinanceRequestMappingError("Real OKX entry order requires a positive mark price")
        return mark_price, "ticker", mark_price_age_sec

    def _resolve_account_equity_for_entry(self, runtime_snapshot: AdapterRuntimeSnapshot) -> Decimal:
        if runtime_snapshot.account_equity is not None and runtime_snapshot.account_equity > 0.0:
            return Decimal(str(runtime_snapshot.account_equity))
        balance_response = self._transport.send(
            self._signer.sign(
                PreparedAdapterRequest(
                    method="GET",
                    path="/api/v5/account/balance",
                    params={"ccy": "USDT"},
                )
            )
        )
        equity = self._extract_account_equity(balance_response.payload)
        if equity is None or equity <= 0.0:
            raise BinanceRequestMappingError("Real OKX entry order requires a positive account equity")
        return Decimal(str(equity))

    @staticmethod
    def _require_valid_runtime_snapshot(
        *,
        runtime_snapshot: AdapterRuntimeSnapshot | None,
        error_context: str,
    ) -> AdapterRuntimeSnapshot:
        if runtime_snapshot is None or not runtime_snapshot.snapshot_valid:
            raise BinanceRequestMappingError(
                f"A valid runtime snapshot is required before sending a real OKX {error_context.lower()}"
            )
        return runtime_snapshot

    @staticmethod
    def _resolve_live_position_for_stop(
        *,
        runtime_snapshot: AdapterRuntimeSnapshot | None,
        payload_direction: str,
        error_context: str,
    ) -> PositionSnapshot:
        runtime_snapshot = OkxUsdtSwapAdapter._require_valid_runtime_snapshot(
            runtime_snapshot=runtime_snapshot,
            error_context=error_context,
        )
        position = runtime_snapshot.position
        if position.position_state != "ENTERED":
            raise BinanceRequestMappingError(f"Real OKX {error_context.lower()} requires an existing entered position")
        if position.direction not in {"long", "short"}:
            raise BinanceRequestMappingError(f"Real OKX {error_context.lower()} requires a known position direction")
        if payload_direction and payload_direction != position.direction:
            raise BinanceRequestMappingError(f"OKX {error_context} direction does not match live position direction")
        if position.entry_price is None or position.entry_price <= 0.0:
            raise BinanceRequestMappingError(f"Real OKX {error_context.lower()} requires a valid live entry price")
        return position

    @staticmethod
    def _resolve_live_position_for_exit(
        *,
        runtime_snapshot: AdapterRuntimeSnapshot | None,
        payload_direction: str,
    ) -> PositionSnapshot:
        runtime_snapshot = OkxUsdtSwapAdapter._require_valid_runtime_snapshot(
            runtime_snapshot=runtime_snapshot,
            error_context="Exit order",
        )
        position = runtime_snapshot.position
        if position.position_state != "ENTERED":
            raise BinanceRequestMappingError("Real OKX exit order requires an existing entered position")
        if position.direction not in {"long", "short"}:
            raise BinanceRequestMappingError("Real OKX exit order requires a known position direction")
        if payload_direction and payload_direction != position.direction:
            raise BinanceRequestMappingError("OKX exit order direction does not match live position direction")
        return position

    @staticmethod
    def _format_position_contract_size(position_amt: float | None) -> str:
        try:
            size = Decimal(str(abs(position_amt or 0.0)))
        except InvalidOperation as exc:
            raise BinanceRequestMappingError("Real OKX position size is not a valid decimal") from exc
        if size <= 0:
            raise BinanceRequestMappingError("Real OKX order requires a positive live position amount")
        return format(size.normalize(), "f")

    @staticmethod
    def _resolve_entry_side(*, action: str, direction: str = "") -> str:
        normalized_action = action.strip().lower()
        normalized_direction = direction.strip().lower()
        if normalized_action == "entry_long" or normalized_direction == "long":
            return "buy"
        if normalized_action == "entry_short" or normalized_direction == "short":
            return "sell"
        raise BinanceRequestMappingError("OKX entry order direction must be long or short")

    @staticmethod
    def _resolve_close_side(direction: str) -> str:
        if direction == "long":
            return "sell"
        if direction == "short":
            return "buy"
        raise BinanceRequestMappingError("OKX close order direction must be long or short")

    @staticmethod
    def _resolve_close_side_from_payload(payload: Any) -> str:
        return "sell" if getattr(payload, "direction", "") == "long" else "buy"

    @staticmethod
    def _derive_stop_price(*, direction: str, reference_price: float, stop_ratio: float) -> str:
        return BinancePerpAdapter._derive_stop_price(
            direction=direction,
            reference_price=reference_price,
            stop_ratio=stop_ratio,
        )

    @staticmethod
    def _build_client_order_id(command: ExecutionCommand) -> str:
        aliases = {
            "entry_order": "eo",
            "reduce_order": "ro",
            "exit_order": "xo",
            "maintain_protective_stop": "ps",
            "take_profit_order": "tp",
            "advance_breakeven_stop": "be",
            "advance_trailing_stop": "ts",
        }
        alias = aliases.get(command.target, "cmd")
        digest = sha256(command.idempotency_key.encode("utf-8")).hexdigest()[:16]
        return f"ethbot-{alias}-{digest}"

    @staticmethod
    def _okx_data(payload: Any) -> list[Any]:
        if isinstance(payload, dict) and isinstance(payload.get("data"), list):
            return payload["data"]
        if isinstance(payload, list):
            return payload
        return []

    @staticmethod
    def _first_okx_item(payload: Any) -> dict[str, Any]:
        data = OkxUsdtSwapAdapter._okx_data(payload)
        first = data[0] if data else {}
        return first if isinstance(first, dict) else {}
