from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .action_enums import PositionAction
from .execution_risk_gate import ExecutionRiskGate
from .network_guard import GuardDecision
from .time_utils import parse_datetime_utc

ACTIVE_PROBE_SOURCES = {"contrarian_short_probe", "trigger_ready_small_probe"}
TRIGGER_READY_PROBE_SOURCE = "trigger_ready_small_probe"
TRIGGER_READY_PROBE_INVALIDATED_REASON = "trigger_ready_probe_invalidated"
TRIGGER_READY_PROBE_INVALIDATE_CONDITIONS = {
    "trigger_ready_long_failed_followthrough",
    "trigger_ready_short_failed_followthrough",
    "trigger_reversal_15m",
    "no_followthrough_after_3x15m",
    "hard_risk_veto",
}
TRIGGER_READY_PROBE_REVERSAL_CODES = {
    "probe_trigger_reversal_exit",
    "trigger_reversal_15m",
    "trigger_reversal",
    "transition:probe_trigger_reversal_exit",
    "manage_detail:exit:probe_trigger_reversal",
}
TRIGGER_READY_PROBE_HARD_RISK_CODES = {
    "conflict_veto",
    "data_health_veto",
    "force_exit",
    "hard_risk_veto",
    "position_exit_veto",
    "risk_filter:blocked",
    "risk_filter:research_unavailable",
    "risk_filter:unavailable",
    "risk_filter:veto",
    "risk_filter_not_pass",
    "runtime_entry_veto",
    "staleness_veto",
}


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
        active_probe_invalidation = ""
        if requested_action != PositionAction.EXIT.value:
            active_probe_invalidation = self._active_probe_invalidation(
                runtime_state=runtime_state,
                handoff=handoff,
                guard=guard,
                has_open_risk=has_open_risk,
            )
        if active_probe_invalidation and guard.allow_exit:
            return ExecutionPlan(
                requested_action=requested_action,
                effective_action=PositionAction.EXIT.value,
                plan_reason=TRIGGER_READY_PROBE_INVALIDATED_REASON,
                place_exit_order=True,
                maintain_protective_stop=needs_protective_stop,
                needs_reconciliation=needs_recovery_reconciliation,
                recovery_action="reconcile_runtime_state" if needs_recovery_reconciliation else "",
                notes=[
                    TRIGGER_READY_PROBE_INVALIDATED_REASON,
                    f"matched_invalidate_condition:{active_probe_invalidation}",
                ],
            )

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
        if probe_source == TRIGGER_READY_PROBE_SOURCE:
            return "trigger_ready_probe_expired"
        return "contrarian_probe_expired"

    @staticmethod
    def _parse_datetime(value: Any) -> datetime | None:
        return parse_datetime_utc(value)

    @staticmethod
    def _active_probe_invalidation(
        *,
        runtime_state: dict[str, Any],
        handoff: dict[str, Any] | None,
        guard: GuardDecision,
        has_open_risk: bool,
    ) -> str:
        if not has_open_risk:
            return ""
        metadata = runtime_state.get("metadata")
        if not isinstance(metadata, dict):
            return ""
        if str(metadata.get("active_probe_source") or "") != TRIGGER_READY_PROBE_SOURCE:
            return ""
        active_conditions = PositionManager._normalize_string_list(
            metadata.get("active_probe_invalidate_conditions")
        )
        if not active_conditions:
            return ""
        known_conditions = [
            condition
            for condition in active_conditions
            if PositionManager._normalize_code(condition) in TRIGGER_READY_PROBE_INVALIDATE_CONDITIONS
        ]
        if not known_conditions:
            return ""
        reason_codes = PositionManager._handoff_reason_codes(handoff)
        reason_codes.update(PositionManager._normalize_code(code) for code in guard.reason_codes)
        if guard.blocked:
            reason_codes.add("hard_risk_veto")

        for condition in PositionManager._ordered_trigger_ready_invalidation_conditions(known_conditions):
            normalized_condition = PositionManager._normalize_code(condition)
            if normalized_condition == "hard_risk_veto" and PositionManager._has_hard_risk_veto(reason_codes):
                return normalized_condition
            if normalized_condition == "trigger_reversal_15m" and PositionManager._has_trigger_reversal(reason_codes):
                return normalized_condition
            if normalized_condition == "no_followthrough_after_3x15m" and normalized_condition in reason_codes:
                return normalized_condition
            if normalized_condition in {
                "trigger_ready_long_failed_followthrough",
                "trigger_ready_short_failed_followthrough",
            } and PositionManager._has_failed_followthrough(
                reason_codes=reason_codes,
                condition=normalized_condition,
                runtime_state=runtime_state,
                handoff=handoff,
            ):
                return normalized_condition
        return ""

    @staticmethod
    def _ordered_trigger_ready_invalidation_conditions(conditions: list[str]) -> list[str]:
        priority = {
            "hard_risk_veto": 0,
            "trigger_reversal_15m": 1,
            "trigger_ready_long_failed_followthrough": 2,
            "trigger_ready_short_failed_followthrough": 2,
            "no_followthrough_after_3x15m": 3,
        }
        return sorted(
            conditions,
            key=lambda item: priority.get(PositionManager._normalize_code(item), 99),
        )

    @staticmethod
    def _handoff_reason_codes(handoff: dict[str, Any] | None) -> set[str]:
        handoff = handoff or {}
        codes: set[str] = set()
        for key in (
            "transition_reason_codes",
            "risk_reason_codes",
            "reason_codes",
            "runtime_vetoes",
            "degrade_flags",
            "invalidate_conditions",
            "reduce_conditions",
        ):
            codes.update(
                PositionManager._normalize_code(item)
                for item in PositionManager._normalize_string_list(handoff.get(key))
            )
        risk_report = handoff.get("risk_report")
        if isinstance(risk_report, dict):
            for key in ("reason_codes", "runtime_vetoes", "degrade_flags"):
                codes.update(
                    PositionManager._normalize_code(item)
                    for item in PositionManager._normalize_string_list(risk_report.get(key))
                )
        exit_plan = handoff.get("exit_plan")
        if isinstance(exit_plan, dict):
            codes.update(
                PositionManager._normalize_code(item)
                for item in PositionManager._normalize_string_list(exit_plan.get("invalidate_conditions"))
            )
            codes.update(
                PositionManager._normalize_code(item)
                for item in PositionManager._normalize_string_list(exit_plan.get("reduce_conditions"))
            )
        if bool(handoff.get("staleness_veto")):
            codes.add("staleness_veto")
        if bool(handoff.get("conflict_veto")):
            codes.add("conflict_veto")
        risk_filter_status = PositionManager._normalize_code(handoff.get("risk_filter_status"))
        if risk_filter_status and risk_filter_status not in {"pass", "degraded"}:
            codes.add(f"risk_filter:{risk_filter_status}")
        action = PositionManager._normalize_code(handoff.get("action"))
        if action:
            codes.add(f"action:{action}")
        return {code for code in codes if code}

    @staticmethod
    def _normalize_string_list(value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            stripped = value.strip()
            return [stripped] if stripped else []
        if not isinstance(value, (list, tuple, set)):
            return []
        return [str(item).strip() for item in value if str(item).strip()]

    @staticmethod
    def _normalize_code(value: Any) -> str:
        return str(value or "").strip().lower()

    @staticmethod
    def _has_hard_risk_veto(reason_codes: set[str]) -> bool:
        if reason_codes.intersection(TRIGGER_READY_PROBE_HARD_RISK_CODES):
            return True
        return any(
            code.startswith("risk_filter:")
            and code not in {"risk_filter:pass", "risk_filter:degraded"}
            for code in reason_codes
        )

    @staticmethod
    def _has_trigger_reversal(reason_codes: set[str]) -> bool:
        if reason_codes.intersection(TRIGGER_READY_PROBE_REVERSAL_CODES):
            return True
        return any("probe_trigger_reversal" in code or code.endswith(":trigger_reversal") for code in reason_codes)

    @staticmethod
    def _has_failed_followthrough(
        *,
        reason_codes: set[str],
        condition: str,
        runtime_state: dict[str, Any],
        handoff: dict[str, Any] | None,
    ) -> bool:
        if condition in reason_codes:
            return True
        if not any("failed_followthrough" in code or "no_followthrough" in code for code in reason_codes):
            return False
        expected_direction = "long" if condition == "trigger_ready_long_failed_followthrough" else "short"
        active_direction = PositionManager._active_probe_direction(runtime_state=runtime_state, handoff=handoff)
        return active_direction == expected_direction

    @staticmethod
    def _active_probe_direction(*, runtime_state: dict[str, Any], handoff: dict[str, Any] | None) -> str:
        handoff = handoff or {}
        for value in (
            handoff.get("current_position_direction"),
            handoff.get("direction"),
            runtime_state.get("observed_position_direction"),
        ):
            direction = PositionManager._normalize_code(value)
            if direction in {"long", "short"}:
                return direction
        return ""
