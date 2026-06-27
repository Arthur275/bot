from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .binance_transport import BinanceRequestConfigError, BinanceTransportError
from .config import RuntimeMode
from .okx_transport import OkxRequestConfigError, OkxTransportError
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
    unrealized_pnl_usd: float | None = None
    unrealized_pnl_pct_on_margin: float | None = None
    price_vs_entry_pct: float | None = None


class OrderSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    order_id: str = ""
    client_order_id: str = ""
    order_type: str
    status: str = "open"
    side: str = ""
    reduce_only: bool = False
    quantity: float | None = None
    price: float | None = None
    trigger_price: float | None = None


class AdapterRuntimeSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fetched_at: datetime | None = None
    mark_price_fetched_at: datetime | None = None
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


class TakeProfitOrderPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    direction: str = ""
    price_ratio: float = Field(gt=0.0)
    reduce_fraction: float | None = Field(default=None, gt=0.0, le=1.0)
    reduce_qty: float | None = Field(default=None, gt=0.0)
    level: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_size_contract(self) -> "TakeProfitOrderPayload":
        if (self.reduce_fraction is None) == (self.reduce_qty is None):
            raise ValueError("take_profit_requires_exactly_one_size_contract")
        return self


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
    payload: EntryOrderPayload | ReduceOrderPayload | ExitOrderPayload | ProtectiveStopPayload | TakeProfitOrderPayload | ReconciliationPayload | BreakevenPayload | TrailingStopPayload | RecentFillsPayload

    @model_validator(mode="before")
    @classmethod
    def coerce_payload_by_target(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        payload_by_target = {
            "entry_order": EntryOrderPayload,
            "reduce_order": ReduceOrderPayload,
            "exit_order": ExitOrderPayload,
            "maintain_protective_stop": ProtectiveStopPayload,
            "take_profit_order": TakeProfitOrderPayload,
            "reconcile_position_and_orders": ReconciliationPayload,
            "advance_breakeven_stop": BreakevenPayload,
            "advance_trailing_stop": TrailingStopPayload,
            "sync_recent_fills": RecentFillsPayload,
        }
        expected_model = payload_by_target.get(str(data.get("target") or ""))
        if expected_model is None or "payload" not in data:
            return data
        coerced = dict(data)
        coerced["payload"] = expected_model.model_validate(coerced["payload"])
        return coerced


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
    api_passphrase_env: str = ""
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
    supports_take_profit_orders: bool = False


class BinanceRequestMappingError(RuntimeError):
    pass


ExchangeRequestConfigError = BinanceRequestConfigError | OkxRequestConfigError
ExchangeTransportError = BinanceTransportError | OkxTransportError


class PreparedAdapterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    method: str
    path: str
    requires_auth: bool = True
    params: dict[str, Any] = Field(default_factory=dict)
    body: Any = Field(default_factory=dict)
    idempotency_key: str = ""
