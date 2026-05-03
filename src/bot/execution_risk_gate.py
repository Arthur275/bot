from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_DOWN
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .action_enums import PositionAction


ENTRY_ACTIONS = {
    PositionAction.ENTRY_LONG.value,
    PositionAction.ENTRY_SHORT.value,
    PositionAction.SMALL_PROBE.value,
}


class ExecutionRiskGateConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    leverage: int = Field(default=10, gt=0)
    demo_small_account_mode: bool = True
    entry_margin_budget_usdt: float | None = Field(default=10.0, gt=0.0)
    entry_margin_budget_max_equity_usdt: float | None = Field(default=50.0, gt=0.0)
    max_account_risk_pct_per_trade: float = Field(default=0.01, gt=0.0, le=0.05)
    max_probe_account_risk_pct: float = Field(default=0.002, gt=0.0, le=0.02)
    max_probe_size_pct: float = Field(default=0.02, gt=0.0, le=1.0)
    exchange_min_order_qty: float = Field(default=0.001, gt=0.0)
    exchange_qty_step_size: float = Field(default=0.001, gt=0.0)
    require_execution_allowed: bool = False


class ExecutionRiskDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allowed: bool = True
    executable_size_pct: float = Field(default=0.0, ge=0.0, le=1.0)
    stop_distance_pct: float | None = None
    account_risk_pct: float | None = None
    reason_codes: list[str] = Field(default_factory=list)


class ExecutionRiskGate:
    def __init__(self, config: ExecutionRiskGateConfig | None = None) -> None:
        self._config = config or ExecutionRiskGateConfig()

    @classmethod
    def from_values(
        cls,
        *,
        leverage: int,
        demo_small_account_mode: bool,
        entry_margin_budget_usdt: float | None,
        entry_margin_budget_max_equity_usdt: float | None,
        max_account_risk_pct_per_trade: float,
        max_probe_account_risk_pct: float,
        max_probe_size_pct: float,
        exchange_min_order_qty: float,
        exchange_qty_step_size: float,
        require_execution_allowed: bool,
    ) -> "ExecutionRiskGate":
        return cls(
            ExecutionRiskGateConfig(
                leverage=leverage,
                demo_small_account_mode=demo_small_account_mode,
                entry_margin_budget_usdt=entry_margin_budget_usdt,
                entry_margin_budget_max_equity_usdt=entry_margin_budget_max_equity_usdt,
                max_account_risk_pct_per_trade=max_account_risk_pct_per_trade,
                max_probe_account_risk_pct=max_probe_account_risk_pct,
                max_probe_size_pct=max_probe_size_pct,
                exchange_min_order_qty=exchange_min_order_qty,
                exchange_qty_step_size=exchange_qty_step_size,
                require_execution_allowed=require_execution_allowed,
            )
        )

    def evaluate(
        self,
        *,
        handoff: dict[str, Any] | None,
        runtime_state: dict[str, Any] | None = None,
    ) -> ExecutionRiskDecision:
        handoff = handoff or {}
        runtime_state = runtime_state or {}
        action = str(handoff.get("action") or "wait")
        if action not in ENTRY_ACTIONS:
            return ExecutionRiskDecision()

        execution_allowed = handoff.get("execution_allowed")
        if execution_allowed is False:
            return self._blocked("execution_not_allowed_by_handoff")
        if self._config.require_execution_allowed and execution_allowed is not True:
            return self._blocked("execution_allowed_missing")

        stop_distance = self._resolve_stop_distance_pct(action=action, handoff=handoff)
        if stop_distance is None:
            return self._blocked("stop_not_executable")
        if stop_distance <= 0.0:
            return self._blocked("stop_distance_invalid")

        budget_size = self._resolve_budget_size_pct(runtime_state)
        reason_codes = ["execution_risk_gate_pass"]
        if budget_size is not None:
            executable_size = budget_size
            account_risk_pct = executable_size * float(self._config.leverage) * stop_distance
            reason_codes.append("fixed_margin_budget_sizing")
            if account_risk_pct > self._config.max_account_risk_pct_per_trade:
                reason_codes.append("small_account_budget_overrides_account_risk_cap")
        else:
            account_risk_pct = self._resolve_account_risk_pct(action=action, handoff=handoff)
            size_from_risk = account_risk_pct / stop_distance / float(self._config.leverage)
            requested_size = self._resolve_requested_size_pct(handoff)
            executable_size = min(size_from_risk, requested_size) if requested_size > 0.0 else size_from_risk
            if action == PositionAction.SMALL_PROBE.value:
                executable_size = min(executable_size, self._config.max_probe_size_pct)
        executable_size = max(0.0, min(1.0, round(executable_size, 6)))
        if executable_size <= 0.0:
            return self._blocked("executable_size_zero", stop_distance=stop_distance, account_risk_pct=account_risk_pct)
        exchange_reason = self._validate_exchange_min_qty(
            executable_size_pct=executable_size,
            runtime_state=runtime_state,
        )
        if exchange_reason == "account_too_small_for_exchange_min_qty":
            return self._blocked(exchange_reason, stop_distance=stop_distance, account_risk_pct=account_risk_pct)
        if exchange_reason:
            reason_codes.append(exchange_reason)
        return ExecutionRiskDecision(
            allowed=True,
            executable_size_pct=executable_size,
            stop_distance_pct=round(stop_distance, 6),
            account_risk_pct=round(account_risk_pct, 6),
            reason_codes=reason_codes,
        )

    def _resolve_account_risk_pct(self, *, action: str, handoff: dict[str, Any]) -> float:
        configured = self._to_float(handoff.get("max_account_risk_pct_per_trade"))
        account_risk = configured if configured and configured > 0.0 else self._config.max_account_risk_pct_per_trade
        if action == PositionAction.SMALL_PROBE.value:
            account_risk = min(account_risk, self._config.max_probe_account_risk_pct)
            if self._is_contrarian_probe(handoff):
                account_risk = min(account_risk, self._contrarian_account_risk_cap(handoff))
        return max(0.0, account_risk)

    def _resolve_requested_size_pct(self, handoff: dict[str, Any]) -> float:
        executable = self._to_float(handoff.get("executable_size_pct"))
        if executable and executable > 0.0:
            return min(executable, self._contrarian_size_cap(handoff))
        position_size = self._to_float(handoff.get("position_size_pct"))
        requested = position_size if position_size and position_size > 0.0 else 0.0
        return min(requested, self._contrarian_size_cap(handoff))

    def _resolve_budget_size_pct(self, runtime_state: dict[str, Any]) -> float | None:
        demo_mode = runtime_state.get("demo_small_account_mode")
        if demo_mode is None:
            demo_mode = self._config.demo_small_account_mode
        if not bool(demo_mode):
            return None
        budget = self._to_float(runtime_state.get("entry_margin_budget_usdt"))
        if budget is None:
            budget = self._config.entry_margin_budget_usdt
        if budget is None or budget <= 0.0:
            return None
        account_equity = self._to_float(runtime_state.get("runtime_account_equity"))
        if account_equity is None or account_equity <= 0.0:
            return None
        max_equity = self._to_float(runtime_state.get("entry_margin_budget_max_equity_usdt"))
        if max_equity is None:
            max_equity = self._config.entry_margin_budget_max_equity_usdt
        if max_equity is not None and account_equity > max_equity:
            return None
        return min(1.0, max(0.0, budget / account_equity))

    def _resolve_stop_distance_pct(self, *, action: str, handoff: dict[str, Any]) -> float | None:
        explicit = self._to_float(handoff.get("stop_distance_pct"))
        if explicit and explicit > 0.0:
            return explicit
        stop = self._to_float(handoff.get("initial_stop_loss"))
        if stop is None:
            return None
        direction = self._resolve_direction(action=action, handoff=handoff)
        if direction == "long":
            return 1.0 - stop
        if direction == "short":
            return stop - 1.0
        return None

    @staticmethod
    def _resolve_direction(*, action: str, handoff: dict[str, Any]) -> str:
        direction = str(handoff.get("direction") or handoff.get("current_position_direction") or "").lower()
        if direction in {"long", "short"}:
            return direction
        if action == PositionAction.ENTRY_LONG.value:
            return "long"
        if action == PositionAction.ENTRY_SHORT.value:
            return "short"
        return direction

    @staticmethod
    def _to_float(value: Any) -> float | None:
        try:
            if value is None or value == "":
                return None
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _is_contrarian_probe(handoff: dict[str, Any]) -> bool:
        return str(handoff.get("probe_source") or "").strip().lower() == "contrarian_short_probe"

    def _contrarian_size_cap(self, handoff: dict[str, Any]) -> float:
        if not self._is_contrarian_probe(handoff):
            return 1.0
        if self._contrarian_risk_tier(handoff) == "crowding":
            return 0.005
        return 0.0025

    def _contrarian_account_risk_cap(self, handoff: dict[str, Any]) -> float:
        if not self._is_contrarian_probe(handoff):
            return self._config.max_probe_account_risk_pct
        if self._contrarian_risk_tier(handoff) == "crowding":
            return 0.0015
        return 0.00075

    @staticmethod
    def _contrarian_risk_tier(handoff: dict[str, Any]) -> str:
        tier = str(handoff.get("probe_risk_tier") or "").strip().lower()
        return "crowding" if tier == "crowding" else "technical"

    def _validate_exchange_min_qty(
        self,
        *,
        executable_size_pct: float,
        runtime_state: dict[str, Any],
    ) -> str:
        if not runtime_state:
            return ""
        account_equity = self._to_float(runtime_state.get("runtime_account_equity"))
        mark_price = self._to_float(runtime_state.get("runtime_mark_price"))
        leverage = self._to_float(runtime_state.get("runtime_leverage")) or float(self._config.leverage)
        min_qty = self._to_float(runtime_state.get("exchange_min_order_qty")) or self._config.exchange_min_order_qty
        step_size = self._to_float(runtime_state.get("exchange_qty_step_size")) or self._config.exchange_qty_step_size
        if account_equity is None or mark_price is None or account_equity <= 0.0 or mark_price <= 0.0 or leverage <= 0.0:
            return "exchange_constraints_unavailable"
        try:
            raw_qty = (
                Decimal(str(executable_size_pct))
                * Decimal(str(account_equity))
                * Decimal(str(leverage))
                / Decimal(str(mark_price))
            )
            rounded_qty = raw_qty.quantize(Decimal(str(step_size)), rounding=ROUND_DOWN)
            minimum_qty = Decimal(str(min_qty))
        except (InvalidOperation, ValueError, ZeroDivisionError):
            return "exchange_constraints_unavailable"
        if rounded_qty < minimum_qty:
            return "account_too_small_for_exchange_min_qty"
        return ""

    @staticmethod
    def _blocked(
        reason: str,
        *,
        stop_distance: float | None = None,
        account_risk_pct: float | None = None,
    ) -> ExecutionRiskDecision:
        return ExecutionRiskDecision(
            allowed=False,
            executable_size_pct=0.0,
            stop_distance_pct=round(stop_distance, 6) if stop_distance is not None else None,
            account_risk_pct=round(account_risk_pct, 6) if account_risk_pct is not None else None,
            reason_codes=[reason],
        )
