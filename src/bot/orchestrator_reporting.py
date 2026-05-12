from __future__ import annotations

from typing import Any

from .exchange_adapter import AdapterRuntimeSnapshot, ReconciliationResult


PRIMARY_COMMAND_TARGETS = {"entry_order", "reduce_order", "exit_order"}


def summarize_action(
    *,
    requested_action: str,
    effective_action: str,
    plan_reason: str,
    guard: Any,
    reconciliation: ReconciliationResult,
) -> dict[str, object]:
    return {
        "requested_action": requested_action,
        "effective_action": effective_action,
        "plan_reason": plan_reason,
        "blocked": guard.blocked,
        "degraded": guard.degraded,
        "guard_reason_codes": list(guard.reason_codes),
        "reconciliation_in_sync": reconciliation.in_sync,
        "reconciliation_reason_codes": list(reconciliation.reason_codes),
    }


def summarize_execution_overview(
    *,
    requested_action: str,
    effective_action: str,
    execution_commands: list[Any],
    execution_results: list[Any],
    execution_result_summary: dict[str, bool],
) -> dict[str, object]:
    primary_command = next((command for command in execution_commands if command.target in PRIMARY_COMMAND_TARGETS), None)
    primary_result = next((result for result in execution_results if result.target in PRIMARY_COMMAND_TARGETS), None)
    auxiliary_targets = [command.target for command in execution_commands if command.target not in PRIMARY_COMMAND_TARGETS]
    return {
        "requested_action": requested_action,
        "effective_action": effective_action,
        "primary_target": primary_command.target if primary_command else primary_result.target if primary_result else "",
        "primary_reason": primary_command.reason if primary_command else primary_result.reason if primary_result else "",
        "primary_status": primary_result.status if primary_result else "not_applicable",
        "primary_accepted": primary_result.accepted if primary_result else False,
        "has_primary_failure": execution_result_summary["primary_failed"],
        "has_auxiliary_failure": execution_result_summary["auxiliary_failed"],
        "auxiliary_targets": auxiliary_targets,
    }


def summarize_runtime_overview(
    *,
    expected_position_state: str,
    expected_direction: str,
    expected_size_pct: float,
    runtime_snapshot: AdapterRuntimeSnapshot,
    reconciliation: ReconciliationResult,
    updated_state: Any,
) -> dict[str, object]:
    return {
        "expected_position_state": expected_position_state,
        "expected_direction": expected_direction,
        "expected_size_pct": expected_size_pct,
        "runtime_position_state": runtime_snapshot.position.position_state,
        "runtime_direction": runtime_snapshot.position.direction,
        "runtime_size_pct": runtime_snapshot.position.size_pct,
        "runtime_protective_stop_present": runtime_snapshot.protective_stop_present,
        "observed_position_state": updated_state.observed_position_state,
        "observed_direction": updated_state.observed_position_direction,
        "observed_size_pct": updated_state.observed_position_size_pct,
        "execution_state": updated_state.execution_state.value,
        "pending_action": updated_state.pending_action,
        "recovery_required": updated_state.recovery_required,
        "reconciliation_required": updated_state.reconciliation_required,
        "protective_stop_required": updated_state.protective_stop_required,
        "reconciliation_in_sync": reconciliation.in_sync,
        "reconciliation_reason_codes": list(reconciliation.reason_codes),
        "recent_fill_summary": dict(updated_state.recent_fill_summary),
    }


def summarize_commands(execution_commands: list[Any]) -> list[dict[str, str]]:
    return [
        {
            "target": command.target,
            "reason": command.reason,
            "operation": command.operation,
        }
        for command in execution_commands
    ]


def summarize_command_results(execution_results: list[Any]) -> list[dict[str, object]]:
    return [
        {
            "target": result.target,
            "reason": result.reason,
            "status": result.status,
            "accepted": result.accepted,
            "simulated": result.simulated,
            "idempotency_key": result.idempotency_key,
            "client_order_id": result.client_order_id,
            "exchange_order_id": result.exchange_order_id,
            "error_kind": result.error_kind,
        }
        for result in execution_results
    ]
