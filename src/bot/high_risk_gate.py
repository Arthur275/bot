from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .config import EngineMode, RuntimeMode
from .network_guard import GuardDecision


HighRiskAction = Literal["trailing", "reduce", "exit"]


class TrailingRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    activation_price: float = Field(gt=0.0)
    callback_rate: float = Field(gt=0.0)
    working_type: str = "MARK_PRICE"


class HighRiskHandoff(BaseModel):
    model_config = ConfigDict(extra="forbid")

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
        if self.action == "exit" and (self.reduce_fraction is not None or self.reduce_qty is not None):
            raise ValueError("exit_must_not_set_reduce_size")
        return self


class HighRiskGateDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allowed: bool
    action: str = ""
    handoff_id: str = ""
    reason_codes: list[str] = Field(default_factory=list)
    handoff: HighRiskHandoff | None = None


class HighRiskGate:
    def __init__(
        self,
        *,
        kill_switch_path: str | Path,
        lock_path: str | Path,
        now_fn: Any | None = None,
    ) -> None:
        self._kill_switch_path = Path(kill_switch_path)
        self._lock_path = Path(lock_path)
        self._now_fn = now_fn or (lambda: datetime.now().replace(microsecond=0))

    def evaluate(
        self,
        *,
        raw_handoff: dict[str, Any],
        network_decision: GuardDecision,
        runtime_snapshot: Any,
        executed_handoff_ids: set[str] | None = None,
    ) -> HighRiskGateDecision:
        try:
            handoff = HighRiskHandoff.model_validate(raw_handoff)
        except Exception as exc:
            return self._blocked("handoff_schema_invalid", detail=str(exc))

        reasons: list[str] = []
        if self._kill_switch_path.exists():
            reasons.append("kill_switch_enabled")
        if self._lock_path.exists():
            reasons.append("high_risk_action_in_flight")
        if network_decision.blocked or network_decision.degraded:
            reasons.append("network_unhealthy")
        now = self._now_fn()
        if handoff.expires_at <= now:
            reasons.append("handoff_expired")
        if handoff.handoff_id in (executed_handoff_ids or set()):
            reasons.append("handoff_id_already_executed")
        if handoff.runtime_mode != RuntimeMode.REAL:
            reasons.append("runtime_mode_not_real")
        if handoff.engine_mode != EngineMode.STRICT_LIVE:
            reasons.append("engine_mode_not_strict_live")
        if handoff.symbol != "ETH" or handoff.exchange_symbol != "ETHUSDT":
            reasons.append("symbol_scope_mismatch")
        if handoff.position_state != "ENTERED":
            reasons.append("position_state_not_entered")
        if handoff.risk_filter_status in {"blocked", "veto", "degraded"}:
            reasons.append(f"risk_filter:{handoff.risk_filter_status}")
        reasons.extend(self._validate_snapshot(handoff=handoff, runtime_snapshot=runtime_snapshot))
        if reasons:
            return HighRiskGateDecision(
                allowed=False,
                action=handoff.action,
                handoff_id=handoff.handoff_id,
                reason_codes=list(dict.fromkeys(reasons)),
                handoff=handoff,
            )
        return HighRiskGateDecision(
            allowed=True,
            action=handoff.action,
            handoff_id=handoff.handoff_id,
            reason_codes=["high_risk_gate_pass"],
            handoff=handoff,
        )

    @staticmethod
    def _validate_snapshot(*, handoff: HighRiskHandoff, runtime_snapshot: Any) -> list[str]:
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
        if handoff.action == "reduce":
            reduce_qty = handoff.reduce_qty
            if reduce_qty is not None and amount not in (None, "") and reduce_qty > abs(float(amount)):
                reasons.append("reduce_qty_exceeds_position")
        return reasons

    @staticmethod
    def _blocked(reason: str, *, detail: str = "") -> HighRiskGateDecision:
        codes = [reason]
        if detail:
            codes.append(detail)
        return HighRiskGateDecision(allowed=False, reason_codes=codes)


def write_high_risk_lock(*, lock_path: str | Path, handoff_id: str, action: str, now: datetime | None = None) -> None:
    path = Path(lock_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    timestamp = (now or datetime.now().replace(microsecond=0)).isoformat()
    path.write_text(f"{timestamp}|{action}|{handoff_id}", encoding="utf-8")


def clear_high_risk_lock(*, lock_path: str | Path) -> None:
    Path(lock_path).unlink(missing_ok=True)
