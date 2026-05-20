from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .action_enums import PositionAction
from .execution_risk_gate import ExecutionRiskGate
from .network_guard import GuardDecision
from .time_utils import parse_datetime_utc

ACTIVE_PROBE_SOURCES = {"contrarian_short_probe", "trigger_ready_small_probe"}


class ExecutionPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requested_action: str
    effective_action: str
    plan_reason: str
    place_entry_order: bool = False
    place_reduce_order: bool = False
    place_exit_order: bool = False
    maintain_protective_stop: bool = False
    place_take_profit_orders: bool = False
    advance_breakeven: bool = False
    advance_trailing_stop: bool = False
    sync_recent_fills: bool = False
    needs_reconciliation: bool = False
    recovery_action: str = ""
    executable_size_pct: float | None = None
    stop_distance_pct: float | None = None
    account_risk_pct: float | None = None
    notes: list[str] = Field(default_factory=list)


class PositionManager:
    def __init__(self, execution_risk_gate: ExecutionRiskGate | None = None) -> None:
        self._execution_risk_gate = execution_risk_gate or ExecutionRiskGate()

    def build_execution_plan(
        self,
        *,
        handoff: dict[str, Any] | None,
        guard: GuardDecision,
        runtime_state: dict[str, Any] | None = None,
        adapter_capabilities: Any | None = None,
    ) -> ExecutionPlan:
        requested_action = str((handoff or {}).get("action") or "wait")
        runtime_state = runtime_state or {}
        recovery_required = bool(runtime_state.get("recovery_required"))
        reconciliation_required = bool(runtime_state.get("reconciliation_required"))
        protective_stop_required = bool(runtime_state.get("protective_stop_required"))
        breakeven_ready = bool(runtime_state.get("breakeven_ready"))
        trailing_ready = bool(runtime_state.get("trailing_ready"))
        recent_fill_sync_required = bool(runtime_state.get("recent_fill_sync_required"))
        supports_breakeven_update = bool(getattr(adapter_capabilities, "supports_breakeven_update", False))
        supports_trailing_stop_update = bool(getattr(adapter_capabilities, "supports_trailing_stop_update", False))
        supports_take_profit_orders = bool(getattr(adapter_capabilities, "supports_take_profit_orders", False))
        has_open_risk = self._has_open_risk(runtime_state, handoff)
        has_take_profit_contract = self._has_take_profit_contract(handoff)
        needs_recovery_reconciliation = recovery_required or reconciliation_required
        protective_stop_present = bool(runtime_state.get("protective_stop_present"))
        needs_protective_stop = bool(
            protective_stop_required
            or (has_open_risk and not protective_stop_present)
        )
        entry_actions = {PositionAction.ENTRY_LONG.value, PositionAction.ENTRY_SHORT.value, PositionAction.SMALL_PROBE.value}

        if guard.blocked:
            return ExecutionPlan(
                requested_action=requested_action,
                effective_action="wait",
                plan_reason="blocked_by_guard",
                maintain_protective_stop=needs_protective_stop,
                needs_reconciliation=True,
                recovery_action="await_manual_or_runtime_recovery",
                notes=list(guard.reason_codes),
            )

        if requested_action in entry_actions and not guard.allow_entry:
            return ExecutionPlan(
                requested_action=requested_action,
                effective_action="wait",
                plan_reason="entry_disallowed_by_guard",
                maintain_protective_stop=needs_protective_stop,
                needs_reconciliation=guard.degraded or needs_recovery_reconciliation,
                recovery_action="reconcile_before_reentry" if (guard.degraded or needs_recovery_reconciliation) else "",
                notes=list(guard.reason_codes),
            )

        if requested_action in entry_actions and needs_recovery_reconciliation:
            return ExecutionPlan(
                requested_action=requested_action,
                effective_action="wait",
                plan_reason="entry_blocked_until_reconciliation",
                maintain_protective_stop=needs_protective_stop,
                needs_reconciliation=True,
                recovery_action="reconcile_before_reentry",
            )

        risk_decision = self._execution_risk_gate.evaluate(handoff=handoff, runtime_state=runtime_state)

        expired_probe_source = self._expired_active_probe_source(runtime_state=runtime_state, has_open_risk=has_open_risk)
        if expired_probe_source:
            if expired_probe_source == "contrarian_short_probe" and self._continues_active_probe(
                handoff=handoff,
                probe_source=expired_probe_source,
            ):
                return ExecutionPlan(
                    requested_action=requested_action,
                    effective_action=PositionAction.SMALL_PROBE.value,
                    plan_reason="contrarian_probe_rolled_forward",
                    maintain_protective_stop=needs_protective_stop,
                    needs_reconciliation=needs_recovery_reconciliation,
                    recovery_action="reconcile_runtime_state" if needs_recovery_reconciliation else "",
                    notes=["contrarian_probe_expiry_rolled_forward"],
                )
            expiry_reason = self._probe_expiry_reason(expired_probe_source)
            return ExecutionPlan(
                requested_action=requested_action,
                effective_action=PositionAction.EXIT.value,
                plan_reason=expiry_reason,
                place_exit_order=True,
                maintain_protective_stop=needs_protective_stop,
                needs_reconciliation=needs_recovery_reconciliation,
                recovery_action="reconcile_runtime_state" if needs_recovery_reconciliation else "",
                notes=[expiry_reason],
            )

        if requested_action in entry_actions and not risk_decision.allowed:
            return ExecutionPlan(
                requested_action=requested_action,
                effective_action="wait",
                plan_reason="entry_blocked_by_execution_risk_gate",
                maintain_protective_stop=needs_protective_stop,
                needs_reconciliation=needs_recovery_reconciliation,
                recovery_action="reconcile_runtime_state" if needs_recovery_reconciliation else "",
                executable_size_pct=0.0,
                stop_distance_pct=risk_decision.stop_distance_pct,
                account_risk_pct=risk_decision.account_risk_pct,
                notes=list(risk_decision.reason_codes),
            )

        if requested_action == PositionAction.REDUCE.value and not guard.allow_reduce:
            return ExecutionPlan(
                requested_action=requested_action,
                effective_action="wait",
                plan_reason="reduce_disallowed_by_guard",
                maintain_protective_stop=needs_protective_stop,
                needs_reconciliation=guard.degraded or needs_recovery_reconciliation,
                recovery_action="reconcile_position_before_reduce" if (guard.degraded or needs_recovery_reconciliation) else "",
                notes=list(guard.reason_codes),
            )

        if requested_action == PositionAction.EXIT.value and not guard.allow_exit:
            return ExecutionPlan(
                requested_action=requested_action,
                effective_action="wait",
                plan_reason="exit_disallowed_by_guard",
                maintain_protective_stop=needs_protective_stop,
                needs_reconciliation=True,
                recovery_action="reconcile_position_before_exit",
                notes=list(guard.reason_codes),
            )

        effective_action = requested_action or "wait"
        action_refreshes_protective_stop = effective_action in {
            PositionAction.ENTRY_LONG.value,
            PositionAction.ENTRY_SHORT.value,
            PositionAction.SMALL_PROBE.value,
            PositionAction.REDUCE.value,
            PositionAction.EXIT.value,
        }
        return ExecutionPlan(
            requested_action=requested_action,
            effective_action=effective_action,
            plan_reason=self._resolve_passthrough_reason(effective_action),
            place_entry_order=effective_action in {PositionAction.ENTRY_LONG.value, PositionAction.ENTRY_SHORT.value, PositionAction.SMALL_PROBE.value},
            place_reduce_order=effective_action == PositionAction.REDUCE.value,
            place_exit_order=effective_action == PositionAction.EXIT.value,
            maintain_protective_stop=needs_protective_stop or action_refreshes_protective_stop,
            place_take_profit_orders=effective_action in entry_actions and supports_take_profit_orders and has_take_profit_contract,
            advance_breakeven=breakeven_ready and has_open_risk and supports_breakeven_update,
            advance_trailing_stop=trailing_ready and has_open_risk and supports_trailing_stop_update,
            sync_recent_fills=recent_fill_sync_required and (has_open_risk or effective_action in {PositionAction.REDUCE.value, PositionAction.EXIT.value}),
            needs_reconciliation=needs_recovery_reconciliation,
            recovery_action="reconcile_runtime_state" if needs_recovery_reconciliation else "",
            executable_size_pct=risk_decision.executable_size_pct if effective_action in entry_actions else None,
            stop_distance_pct=risk_decision.stop_distance_pct if effective_action in entry_actions else None,
            account_risk_pct=risk_decision.account_risk_pct if effective_action in entry_actions else None,
            notes=list(risk_decision.reason_codes) if effective_action in entry_actions else [],
        )

    @staticmethod
    def _resolve_passthrough_reason(effective_action: str) -> str:
        return "quant_action_passthrough"

    @staticmethod
    def _has_open_risk(runtime_state: dict[str, Any], handoff: dict[str, Any] | None) -> bool:
        observed_size = float(runtime_state.get("observed_position_size_pct") or 0.0)
        handoff_size = float((handoff or {}).get("position_size_pct") or 0.0)
        observed_state = str(runtime_state.get("observed_position_state") or "")
        handoff_state = str((handoff or {}).get("position_state") or "")
        handoff_action = str((handoff or {}).get("action") or "")
        entry_actions = {PositionAction.ENTRY_LONG.value, PositionAction.ENTRY_SHORT.value, PositionAction.SMALL_PROBE.value}
        if observed_size > 0.0 or observed_state == "ENTERED" or handoff_state == "ENTERED":
            return True
        return handoff_size > 0.0 and handoff_action not in entry_actions

    @staticmethod
    def _has_take_profit_contract(handoff: dict[str, Any] | None) -> bool:
        handoff = handoff or {}
        direct_orders = handoff.get("take_profit_orders")
        if isinstance(direct_orders, list) and direct_orders:
            return all(PositionManager._take_profit_order_has_size(item) for item in direct_orders)
        ladder = handoff.get("tp_ladder")
        if not isinstance(ladder, list) or not ladder:
            return False
        has_fractions = PositionManager._has_take_profit_ladder_size_list(
            handoff=handoff,
            ladder=ladder,
            keys=("tp_reduce_fractions", "take_profit_reduce_fractions"),
        )
        has_qtys = PositionManager._has_take_profit_ladder_size_list(
            handoff=handoff,
            ladder=ladder,
            keys=("tp_reduce_qtys", "take_profit_reduce_qtys"),
        )
        return has_fractions != has_qtys

    @staticmethod
    def _has_take_profit_ladder_size_list(
        *,
        handoff: dict[str, Any],
        ladder: list[Any],
        keys: tuple[str, ...],
    ) -> bool:
        for key in keys:
            values = handoff.get(key)
            if isinstance(values, list) and len(values) == len(ladder) and values:
                return True
        return False

    @staticmethod
    def _take_profit_order_has_size(item: Any) -> bool:
        if not isinstance(item, dict):
            return False
        has_ratio = item.get("price_ratio") not in (None, "") or item.get("target_ratio") not in (None, "") or item.get("ratio") not in (None, "")
        has_fraction = item.get("reduce_fraction") not in (None, "")
        has_qty = item.get("reduce_qty") not in (None, "")
        return has_ratio and (has_fraction != has_qty)

    @staticmethod
    def _expired_active_probe_source(*, runtime_state: dict[str, Any], has_open_risk: bool) -> str:
        if not has_open_risk:
            return ""
        metadata = runtime_state.get("metadata")
        if not isinstance(metadata, dict):
            return ""
        probe_source = str(metadata.get("active_probe_source") or "")
        if probe_source not in ACTIVE_PROBE_SOURCES:
            return ""
        expires_at = PositionManager._parse_datetime(metadata.get("active_probe_expires_at"))
        runtime_now = PositionManager._parse_datetime(runtime_state.get("runtime_now"))
        if expires_at is None or runtime_now is None:
            return ""
        return probe_source if runtime_now >= expires_at else ""

    @staticmethod
    def _continues_active_probe(*, handoff: dict[str, Any] | None, probe_source: str) -> bool:
        handoff = handoff or {}
        return (
            str(handoff.get("action") or "") == PositionAction.SMALL_PROBE.value
            and str(handoff.get("direction") or "") == "short"
            and str(handoff.get("probe_source") or "") == probe_source
        )

    @staticmethod
    def _probe_expiry_reason(probe_source: str) -> str:
        if probe_source == "trigger_ready_small_probe":
            return "trigger_ready_probe_expired"
        return "contrarian_probe_expired"

    @staticmethod
    def _parse_datetime(value: Any) -> datetime | None:
        return parse_datetime_utc(value)
