from __future__ import annotations

from bot.automation_state import (
    AutomationState,
    coerce_automation_state_for_execution_layer,
    is_automation_execution_pair_allowed,
)


def test_automation_state_allows_observing_and_entry_prefight_for_idle() -> None:
    assert is_automation_execution_pair_allowed(
        execution_state="idle",
        automation_state=AutomationState.OBSERVING,
    )
    assert is_automation_execution_pair_allowed(
        execution_state="idle",
        automation_state=AutomationState.ENTRY_PREFLIGHT_READY,
    )


def test_automation_state_forces_blocked_during_reconciliation_or_blocked_execution() -> None:
    assert (
        coerce_automation_state_for_execution_layer(
            execution_state="reconciling",
            automation_state=AutomationState.ENTRY_SUBMITTING,
        )
        is AutomationState.ACTION_BLOCKED
    )
    assert (
        coerce_automation_state_for_execution_layer(
            execution_state="blocked",
            automation_state=AutomationState.HIGH_RISK_SUBMITTING,
        )
        is AutomationState.ACTION_BLOCKED
    )


def test_automation_state_degraded_only_allows_observing_or_action_blocked() -> None:
    assert (
        coerce_automation_state_for_execution_layer(
            execution_state="degraded",
            automation_state=AutomationState.OBSERVING,
        )
        is AutomationState.OBSERVING
    )
    assert (
        coerce_automation_state_for_execution_layer(
            execution_state="degraded",
            automation_state=AutomationState.REDUCE_PREFLIGHT_READY,
        )
        is AutomationState.ACTION_BLOCKED
    )
    assert (
        coerce_automation_state_for_execution_layer(
            execution_state="degraded",
            automation_state=AutomationState.DISABLED,
        )
        is AutomationState.ACTION_BLOCKED
    )


def test_automation_state_serializes_as_stable_string() -> None:
    assert AutomationState.ENTRY_SUBMITTING.value == "entry_submitting"
