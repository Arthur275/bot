from __future__ import annotations

from enum import Enum


class AutomationState(str, Enum):
    DISABLED = "disabled"
    OBSERVING = "observing"
    ENTRY_PREFLIGHT_READY = "entry_preflight_ready"
    ENTRY_SUBMITTING = "entry_submitting"
    ENTRY_SUBMITTED = "entry_submitted"
    POSITION_PROTECTED = "position_protected"
    REDUCE_PREFLIGHT_READY = "reduce_preflight_ready"
    EXIT_PREFLIGHT_READY = "exit_preflight_ready"
    HIGH_RISK_SUBMITTING = "high_risk_submitting"
    ACTION_BLOCKED = "action_blocked"
    ACTION_FAILED = "action_failed"


SUBMITTING_AUTOMATION_STATES = {
    AutomationState.ENTRY_SUBMITTING,
    AutomationState.HIGH_RISK_SUBMITTING,
}


def coerce_automation_state_for_execution_layer(
    *,
    execution_state: str,
    automation_state: AutomationState,
) -> AutomationState:
    normalized_execution_state = str(execution_state or "").lower()
    if normalized_execution_state in {"reconciling", "blocked"}:
        return AutomationState.ACTION_BLOCKED
    if normalized_execution_state == "degraded" and automation_state not in {
        AutomationState.OBSERVING,
        AutomationState.ACTION_BLOCKED,
    }:
        return AutomationState.ACTION_BLOCKED
    if automation_state in SUBMITTING_AUTOMATION_STATES and normalized_execution_state in {
        "reconciling",
        "blocked",
        "degraded",
    }:
        return AutomationState.ACTION_BLOCKED
    return automation_state


def is_automation_execution_pair_allowed(*, execution_state: str, automation_state: AutomationState) -> bool:
    return (
        coerce_automation_state_for_execution_layer(
            execution_state=execution_state,
            automation_state=automation_state,
        )
        == automation_state
    )
