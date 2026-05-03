from __future__ import annotations

from collections.abc import Sequence

from .exchange_adapter import CommandExecutionResult


def summarize_execution_results(
    execution_results: Sequence[CommandExecutionResult] | None,
) -> dict[str, bool]:
    summary = {
        "has_failure": False,
        "primary_failed": False,
        "primary_succeeded": False,
        "auxiliary_failed": False,
        "protective_stop_failed": False,
        "capability_blocked": False,
    }
    if not execution_results:
        return summary
    primary_targets = {"entry_order", "reduce_order", "exit_order"}
    failure_statuses = {"failed", "rejected", "error", "timeout", "not_implemented"}
    for result in execution_results:
        failed = (not result.accepted) or (result.status.lower() in failure_statuses)
        if failed and _is_expected_capability_block(result):
            summary["capability_blocked"] = True
            continue
        if failed:
            summary["has_failure"] = True
            if result.target in primary_targets:
                summary["primary_failed"] = True
                continue
            summary["auxiliary_failed"] = True
            if result.target == "maintain_protective_stop":
                summary["protective_stop_failed"] = True
            continue
        if result.target in primary_targets:
            summary["primary_succeeded"] = True
    return summary


def _is_expected_capability_block(result: CommandExecutionResult) -> bool:
    if result.target not in {"advance_breakeven_stop", "advance_trailing_stop"}:
        return False
    if result.reason != "unsafe_request_mapping":
        return False
    error = str((result.details or {}).get("error") or "")
    return "Algo stop cancel/replace support" in error
