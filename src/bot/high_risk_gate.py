from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .config import EngineMode, RuntimeMode
from .network_guard import GuardDecision


HighRiskAction = Literal["trailing", "reduce", "exit"]
SUPPORTED_HANDOFF_VERSION = 1
DEFAULT_STALE_AFTER_SEC = 180.0
BINANCE_TRAILING_CALLBACK_MIN = 0.1
BINANCE_TRAILING_CALLBACK_MAX = 5.0
DEFAULT_TRAILING_ACTIVATION_MIN_DISTANCE_PCT = 0.005


class TrailingRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    activation_price: float = Field(gt=0.0)
    callback_rate: float = Field(gt=0.0)
    working_type: str = "MARK_PRICE"


class ExchangeProtectiveStop(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trigger_price: float = Field(gt=0.0)
    side: Literal["BUY", "SELL"]
    order_type: str = "STOP_MARKET"
    algo_id: str = ""
    client_algo_id: str = ""


class HighRiskHandoff(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int
    handoff_id: str = Field(min_length=1)
    generated_at: datetime
    expires_at: datetime
    action: HighRiskAction
    runtime_mode: RuntimeMode = RuntimeMode.REAL
    engine_mode: EngineMode = EngineMode.STRICT_LIVE
    symbol: str = "ETH"
    exchange_symbol: str = "ETHUSDT"
    direction: Literal["long", "short"]
    position_state: str = "ENTERED"
    risk_filter_status: str = "pass"
    reduce_fraction: float | None = Field(default=None, gt=0.0, le=1.0)
    reduce_qty: float | None = Field(default=None, gt=0.0)
    trailing_rule: TrailingRule | None = None
    reason: str = ""

    @model_validator(mode="after")
    def validate_action_fields(self) -> "HighRiskHandoff":
        if self.action == "trailing" and self.trailing_rule is None:
            raise ValueError("trailing_rule_required")
        if self.action == "reduce" and self.reduce_fraction is None and self.reduce_qty is None:
            raise ValueError("reduce_size_required")
        if self.action == "reduce" and self.reduce_fraction is not None and self.reduce_qty is not None:
            raise ValueError("reduce_size_ambiguous")
        if self.action == "exit" and (self.reduce_fraction is not None or self.reduce_qty is not None):
            raise ValueError("exit_must_not_set_reduce_size")
        if self.action != "trailing" and self.trailing_rule is not None:
            raise ValueError("trailing_rule_only_allowed_for_trailing")
        return self


class HighRiskGateDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allowed: bool
    action: str = ""
    handoff_id: str = ""
    blocked_reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)
    handoff: HighRiskHandoff | None = None


class HighRiskGate:
    def __init__(
        self,
        *,
        kill_switch_path: str | Path,
        lock_path: str | Path,
        exchange_min_order_qty: float = 0.001,
        stale_after_sec: float = DEFAULT_STALE_AFTER_SEC,
        trailing_activation_min_distance_pct: float = DEFAULT_TRAILING_ACTIVATION_MIN_DISTANCE_PCT,
        now_fn: Any | None = None,
    ) -> None:
        self._kill_switch_path = Path(kill_switch_path)
        self._lock_path = Path(lock_path)
        self._exchange_min_order_qty = float(exchange_min_order_qty)
        self._stale_after_sec = float(stale_after_sec)
        self._trailing_activation_min_distance_pct = float(trailing_activation_min_distance_pct)
        self._now_fn = now_fn or (lambda: datetime.now().replace(microsecond=0))

    def evaluate(
        self,
        *,
        raw_handoff: dict[str, Any],
        network_decision: GuardDecision,
        runtime_snapshot: Any,
        state_metadata: dict[str, Any] | None = None,
        exchange_protective_stop: dict[str, Any] | ExchangeProtectiveStop | None = None,
        executed_handoff_ids: set[str] | None = None,
    ) -> HighRiskGateDecision:
        try:
            handoff = HighRiskHandoff.model_validate(raw_handoff)
        except Exception as exc:
            return self._blocked("handoff_schema_invalid", detail=str(exc))
        try:
            protective_stop = (
                exchange_protective_stop
                if isinstance(exchange_protective_stop, ExchangeProtectiveStop)
                else ExchangeProtectiveStop.model_validate(exchange_protective_stop)
                if exchange_protective_stop is not None
                else None
            )
        except Exception as exc:
            return self._blocked("exchange_protective_stop_invalid", action=handoff.action, handoff_id=handoff.handoff_id, detail=str(exc))

        blocked: list[str] = []
        warnings: list[str] = []
        if self._kill_switch_path.exists():
            blocked.append("kill_switch_enabled")
        if self._lock_path.exists():
            blocked.append("high_risk_action_in_flight")
        if network_decision.blocked or network_decision.degraded:
            blocked.append("network_unhealthy")
        now = self._now_fn()
        generated_age_sec = (now - handoff.generated_at).total_seconds()
        if handoff.expires_at <= now:
            blocked.append("handoff_expired")
        elif generated_age_sec > self._stale_after_sec:
            warnings.append("handoff_stale")
        if handoff.handoff_id in (executed_handoff_ids or set()):
            blocked.append("handoff_id_already_executed")
        if handoff.version != SUPPORTED_HANDOFF_VERSION:
            blocked.append("handoff_version_unsupported")
        if handoff.runtime_mode != RuntimeMode.REAL:
            blocked.append("runtime_mode_not_real")
        if handoff.engine_mode != EngineMode.STRICT_LIVE:
            blocked.append("engine_mode_not_strict_live")
        if handoff.symbol != "ETH" or handoff.exchange_symbol != "ETHUSDT":
            blocked.append("symbol_scope_mismatch")
        if handoff.position_state != "ENTERED":
            blocked.append("position_state_not_entered")
        if handoff.risk_filter_status in {"blocked", "veto", "degraded"}:
            blocked.append(f"risk_filter:{handoff.risk_filter_status}")
        blocked.extend(
            self._validate_snapshot(
                handoff=handoff,
                runtime_snapshot=runtime_snapshot,
                state_metadata=state_metadata or {},
                exchange_protective_stop=protective_stop,
                exchange_min_order_qty=self._exchange_min_order_qty,
                trailing_activation_min_distance_pct=self._trailing_activation_min_distance_pct,
            )
        )
        blocked = list(dict.fromkeys(blocked))
        warnings = list(dict.fromkeys(warnings))
        if blocked:
            return HighRiskGateDecision(
                allowed=False,
                action=handoff.action,
                handoff_id=handoff.handoff_id,
                blocked_reasons=blocked,
                warnings=warnings,
                reason_codes=blocked,
                handoff=handoff,
            )
        return HighRiskGateDecision(
            allowed=True,
            action=handoff.action,
            handoff_id=handoff.handoff_id,
            blocked_reasons=[],
            warnings=warnings,
            reason_codes=["high_risk_gate_pass"],
            handoff=handoff,
        )

    @staticmethod
    def _validate_snapshot(
        *,
        handoff: HighRiskHandoff,
        runtime_snapshot: Any,
        state_metadata: dict[str, Any],
        exchange_protective_stop: ExchangeProtectiveStop | None,
        exchange_min_order_qty: float,
        trailing_activation_min_distance_pct: float,
    ) -> list[str]:
        reasons: list[str] = []
        if runtime_snapshot is None or not bool(getattr(runtime_snapshot, "snapshot_valid", False)):
            return ["snapshot_invalid"]
        position = getattr(runtime_snapshot, "position", None)
        if position is None:
            return ["position_snapshot_missing"]
        if str(getattr(position, "position_state", "")) != "ENTERED":
            reasons.append("exchange_position_not_entered")
        if str(getattr(position, "direction", "")) != handoff.direction:
            reasons.append("direction_mismatch")
        amount = getattr(position, "position_amt", None)
        if amount in (None, "", 0, 0.0):
            reasons.append("position_amount_missing")
            return reasons
        position_amount = abs(float(amount))
        if handoff.action == "reduce":
            reduce_qty = handoff.reduce_qty
            if reduce_qty is None and handoff.reduce_fraction is not None:
                reduce_qty = position_amount * float(handoff.reduce_fraction)
            if handoff.reduce_fraction is not None and handoff.reduce_fraction >= 1.0:
                reasons.append("reduce_fraction_is_exit_use_exit_action")
            if reduce_qty is not None and reduce_qty > position_amount:
                reasons.append("reduce_qty_exceeds_position")
            remaining_qty = position_amount - float(reduce_qty or 0.0)
            if remaining_qty <= 0:
                reasons.append("reduce_would_close_position_use_exit_action")
            elif remaining_qty < exchange_min_order_qty:
                reasons.append("reduce_remaining_qty_below_exchange_min_order_qty")
        if handoff.action == "trailing":
            reasons.extend(
                HighRiskGate._validate_trailing(
                    handoff=handoff,
                    runtime_snapshot=runtime_snapshot,
                    state_metadata=state_metadata,
                    exchange_protective_stop=exchange_protective_stop,
                    trailing_activation_min_distance_pct=trailing_activation_min_distance_pct,
                )
            )
        return reasons

    @staticmethod
    def _validate_trailing(
        *,
        handoff: HighRiskHandoff,
        runtime_snapshot: Any,
        state_metadata: dict[str, Any],
        exchange_protective_stop: ExchangeProtectiveStop | None,
        trailing_activation_min_distance_pct: float,
    ) -> list[str]:
        reasons: list[str] = []
        position = getattr(runtime_snapshot, "position", None)
        trailing_rule = handoff.trailing_rule
        if trailing_rule is None:
            return ["trailing_rule_required"]
        if not (BINANCE_TRAILING_CALLBACK_MIN <= float(trailing_rule.callback_rate) <= BINANCE_TRAILING_CALLBACK_MAX):
            reasons.append("trailing_callback_rate_out_of_range")
        protective_record = (state_metadata or {}).get("protective_stop") or {}
        if "lock_stage" not in protective_record:
            reasons.append("lock_stage_missing")
            lock_stage = 0
        else:
            try:
                lock_stage = int(protective_record.get("lock_stage"))
            except (TypeError, ValueError):
                lock_stage = 0
                reasons.append("lock_stage_invalid")
        if lock_stage < 2:
            reasons.append("lock_stage_below_trailing_minimum")
        if exchange_protective_stop is None:
            reasons.append("protective_stop_missing_for_trailing")
            return reasons
        expected_side = "SELL" if handoff.direction == "long" else "BUY"
        if exchange_protective_stop.side != expected_side:
            reasons.append("protective_stop_side_mismatch")
        activation = float(trailing_rule.activation_price)
        exchange_stop = float(exchange_protective_stop.trigger_price)
        mark_price = getattr(position, "mark_price", None)
        if mark_price in (None, "", 0, 0.0):
            reasons.append("mark_price_missing")
            return reasons
        mark = float(mark_price)
        min_distance = mark * trailing_activation_min_distance_pct
        if handoff.direction == "long":
            if activation < exchange_stop:
                reasons.append("trailing_activation_below_exchange_stop")
            if activation < mark + min_distance:
                reasons.append("trailing_activation_too_close_to_mark")
        else:
            if activation > exchange_stop:
                reasons.append("trailing_activation_above_exchange_stop")
            if activation > mark - min_distance:
                reasons.append("trailing_activation_too_close_to_mark")
        return reasons

    @staticmethod
    def _blocked(reason: str, *, action: str = "", handoff_id: str = "", detail: str = "") -> HighRiskGateDecision:
        codes = [reason]
        if detail:
            codes.append(detail)
        return HighRiskGateDecision(allowed=False, action=action, handoff_id=handoff_id, blocked_reasons=codes, reason_codes=codes)


def write_high_risk_lock(*, lock_path: str | Path, handoff_id: str, action: str, now: datetime | None = None) -> None:
    path = Path(lock_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    timestamp = (now or datetime.now().replace(microsecond=0)).isoformat()
    path.write_text(f"{timestamp}|{action}|{handoff_id}", encoding="utf-8")


def clear_high_risk_lock(*, lock_path: str | Path) -> None:
    Path(lock_path).unlink(missing_ok=True)
