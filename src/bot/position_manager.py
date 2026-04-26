from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .network_guard import GuardDecision


class ExecutionPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requested_action: str
    effective_action: str
    plan_reason: str
    place_entry_order: bool = False
    place_reduce_order: bool = False
    place_exit_order: bool = False
    maintain_protective_stop: bool = False
    advance_breakeven: bool = False
    advance_trailing_stop: bool = False
    sync_recent_fills: bool = False
    needs_reconciliation: bool = False
    recovery_action: str = ""
    notes: list[str] = Field(default_factory=list)


class PositionManager:
    def build_execution_plan(
        self,
        *,
        handoff: dict[str, Any] | None,
        guard: GuardDecision,
        runtime_state: dict[str, Any] | None = None,
    ) -> ExecutionPlan:
        requested_action = str((handoff or {}).get("action") or "wait")
        runtime_state = runtime_state or {}
        recovery_required = bool(runtime_state.get("recovery_required"))
        reconciliation_required = bool(runtime_state.get("reconciliation_required"))
        protective_stop_required = bool(runtime_state.get("protective_stop_required"))
        breakeven_ready = bool(runtime_state.get("breakeven_ready"))
        trailing_ready = bool(runtime_state.get("trailing_ready"))
        recent_fill_sync_required = bool(runtime_state.get("recent_fill_sync_required"))
        has_open_risk = self._has_open_risk(runtime_state, handoff)
        needs_recovery_reconciliation = recovery_required or reconciliation_required
        needs_protective_stop = has_open_risk or protective_stop_required

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

        if requested_action in {"entry_long", "entry_short", "small_probe"} and not guard.allow_entry:
            return ExecutionPlan(
                requested_action=requested_action,
                effective_action="wait",
                plan_reason="entry_disallowed_by_guard",
                maintain_protective_stop=needs_protective_stop,
                needs_reconciliation=guard.degraded or needs_recovery_reconciliation,
                recovery_action="reconcile_before_reentry" if (guard.degraded or needs_recovery_reconciliation) else "",
                notes=list(guard.reason_codes),
            )

        if requested_action == "reduce" and not guard.allow_reduce:
            return ExecutionPlan(
                requested_action=requested_action,
                effective_action="wait",
                plan_reason="reduce_disallowed_by_guard",
                maintain_protective_stop=needs_protective_stop,
                needs_reconciliation=guard.degraded or needs_recovery_reconciliation,
                recovery_action="reconcile_position_before_reduce" if (guard.degraded or needs_recovery_reconciliation) else "",
                notes=list(guard.reason_codes),
            )

        if requested_action == "exit" and not guard.allow_exit:
            return ExecutionPlan(
                requested_action=requested_action,
                effective_action="wait",
                plan_reason="exit_disallowed_by_guard",
                maintain_protective_stop=needs_protective_stop,
                needs_reconciliation=True,
                recovery_action="reconcile_position_before_exit",
                notes=list(guard.reason_codes),
            )

        if requested_action in {"observe_only", "paper_only"}:
            return ExecutionPlan(
                requested_action=requested_action,
                effective_action="wait",
                plan_reason="non_executable_observation_action",
                maintain_protective_stop=needs_protective_stop,
                needs_reconciliation=needs_recovery_reconciliation,
                recovery_action="reconcile_runtime_state" if needs_recovery_reconciliation else "",
                notes=[requested_action],
            )

        if requested_action == "small_probe":
            return ExecutionPlan(
                requested_action=requested_action,
                effective_action="small_probe",
                plan_reason="probe_allowed_in_shadow_only",
                place_entry_order=True,
                maintain_protective_stop=True,
                notes=["probe_action"],
            )

        if requested_action in {"entry_long", "entry_short"}:
            return ExecutionPlan(
                requested_action=requested_action,
                effective_action=requested_action,
                plan_reason="entry_allowed",
                place_entry_order=True,
                maintain_protective_stop=True,
            )

        if requested_action == "reduce":
            return ExecutionPlan(
                requested_action=requested_action,
                effective_action="reduce",
                plan_reason="reduce_allowed",
                place_reduce_order=True,
                maintain_protective_stop=True,
                advance_breakeven=breakeven_ready,
                advance_trailing_stop=trailing_ready,
                sync_recent_fills=recent_fill_sync_required,
                needs_reconciliation=needs_recovery_reconciliation,
                recovery_action="reconcile_runtime_state" if needs_recovery_reconciliation else "",
            )

        if requested_action == "exit":
            return ExecutionPlan(
                requested_action=requested_action,
                effective_action="exit",
                plan_reason="exit_allowed",
                place_exit_order=True,
                maintain_protective_stop=True,
                sync_recent_fills=recent_fill_sync_required,
                needs_reconciliation=needs_recovery_reconciliation,
                recovery_action="reconcile_runtime_state" if needs_recovery_reconciliation else "",
            )

        if needs_recovery_reconciliation:
            return ExecutionPlan(
                requested_action=requested_action,
                effective_action="wait",
                plan_reason="recovery_reconciliation_required",
                maintain_protective_stop=needs_protective_stop,
                advance_breakeven=breakeven_ready and has_open_risk,
                advance_trailing_stop=trailing_ready and has_open_risk,
                sync_recent_fills=recent_fill_sync_required,
                needs_reconciliation=True,
                recovery_action="reconcile_runtime_state",
            )

        if has_open_risk and (breakeven_ready or trailing_ready or recent_fill_sync_required):
            return ExecutionPlan(
                requested_action=requested_action,
                effective_action="wait",
                plan_reason="risk_management_only",
                maintain_protective_stop=needs_protective_stop,
                advance_breakeven=breakeven_ready,
                advance_trailing_stop=trailing_ready,
                sync_recent_fills=recent_fill_sync_required,
            )

        return ExecutionPlan(
            requested_action=requested_action,
            effective_action="wait",
            plan_reason="wait_or_noop",
            maintain_protective_stop=needs_protective_stop,
        )

    @staticmethod
    def _has_open_risk(runtime_state: dict[str, Any], handoff: dict[str, Any] | None) -> bool:
        observed_size = float(runtime_state.get("observed_position_size_pct") or 0.0)
        handoff_size = float((handoff or {}).get("position_size_pct") or 0.0)
        return observed_size > 0.0 or handoff_size > 0.0
