from __future__ import annotations

import json
from collections.abc import Sequence
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .exchange_adapter import AdapterRuntimeSnapshot, CommandExecutionResult
from .network_guard import GuardDecision


class ExecutionLayerState(str, Enum):
    IDLE = "idle"
    ENTRY_PENDING = "entry_pending"
    POSITION_OPEN = "position_open"
    REDUCE_PENDING = "reduce_pending"
    EXIT_PENDING = "exit_pending"
    RECONCILING = "reconciling"
    DEGRADED = "degraded"
    BLOCKED = "blocked"


class BotRuntimeState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    execution_state: ExecutionLayerState = ExecutionLayerState.IDLE
    observed_position_state: str = "FLAT"
    observed_position_direction: str = "neutral"
    observed_position_size_pct: float = Field(ge=0.0, le=1.0, default=0.0)
    pending_action: str = ""
    recovery_required: bool = False
    reconciliation_required: bool = False
    protective_stop_required: bool = False
    last_judgement_status: str = ""
    last_judgement_generated_at: str = ""
    last_handoff_action: str = ""
    last_effective_action: str = ""
    last_plan_reason: str = ""
    last_diagnostic_category: str = ""
    last_reason_codes: list[str] = Field(default_factory=list)
    recent_idempotency_keys: list[str] = Field(default_factory=list)
    recent_fill_summary: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class StateStore:
    def __init__(self, output_path: str | Path) -> None:
        self._output_path = Path(output_path)

    @property
    def output_path(self) -> Path:
        return self._output_path

    def load(self) -> BotRuntimeState:
        if not self._output_path.exists():
            return BotRuntimeState()
        payload = json.loads(self._output_path.read_text(encoding="utf-8"))
        return BotRuntimeState.model_validate(payload)

    def save(self, state: BotRuntimeState) -> BotRuntimeState:
        self._output_path.parent.mkdir(parents=True, exist_ok=True)
        self._output_path.write_text(
            json.dumps(state.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return state

    def record_shadow_cycle(
        self,
        *,
        state: BotRuntimeState,
        judgement: dict[str, Any],
        handoff: dict[str, Any] | None,
        guard: GuardDecision,
        effective_action: str,
        plan_reason: str = "",
        needs_reconciliation: bool = False,
        maintain_protective_stop: bool = False,
        execution_results: Sequence[CommandExecutionResult] | None = None,
        runtime_snapshot: AdapterRuntimeSnapshot | None = None,
    ) -> BotRuntimeState:
        next_state = state.model_copy(deep=True)
        execution_summary = self._summarize_execution_results(execution_results)
        next_state.execution_state = self._resolve_execution_state(
            guard,
            effective_action,
            needs_reconciliation,
            primary_execution_failed=execution_summary["primary_failed"],
            auxiliary_execution_failed=execution_summary["auxiliary_failed"],
            observed_position_size_pct=(runtime_snapshot.position.size_pct if runtime_snapshot is not None else next_state.observed_position_size_pct),
        )
        next_state.pending_action = self._resolve_pending_action(
            effective_action,
            primary_execution_failed=execution_summary["primary_failed"],
            auxiliary_execution_failed=execution_summary["auxiliary_failed"],
        )
        next_state.recovery_required = (
            guard.degraded
            or guard.blocked
            or needs_reconciliation
            or execution_summary["has_failure"]
        )
        next_state.reconciliation_required = needs_reconciliation or execution_summary["auxiliary_failed"]
        next_state.protective_stop_required = maintain_protective_stop or execution_summary["protective_stop_failed"]
        next_state.last_judgement_status = str(judgement.get("status") or "")
        next_state.last_judgement_generated_at = str(
            ((handoff or {}).get("generated_at") or (judgement.get("decision") or {}).get("generated_at") or "")
        )
        next_state.last_handoff_action = str((handoff or {}).get("action") or "")
        next_state.last_effective_action = effective_action
        next_state.last_plan_reason = plan_reason
        next_state.last_diagnostic_category = guard.diagnostic_category
        next_state.last_reason_codes = list(guard.reason_codes)
        next_state.recent_idempotency_keys = self._collect_recent_idempotency_keys(
            previous=next_state.recent_idempotency_keys,
            execution_results=execution_results,
        )
        next_state.recent_fill_summary = self._build_recent_fill_summary(execution_results=execution_results)
        if runtime_snapshot is not None:
            next_state.observed_position_state = str(runtime_snapshot.position.position_state or next_state.observed_position_state)
            next_state.observed_position_direction = str(runtime_snapshot.position.direction or next_state.observed_position_direction)
            next_state.observed_position_size_pct = float(runtime_snapshot.position.size_pct)
        elif handoff:
            next_state.observed_position_state = str(handoff.get("position_state") or next_state.observed_position_state)
            next_state.observed_position_direction = str(handoff.get("current_position_direction") or next_state.observed_position_direction)
            next_state.observed_position_size_pct = float(
                handoff.get("position_size_pct") if handoff.get("position_size_pct") is not None else next_state.observed_position_size_pct
            )
        return self.save(next_state)

    @staticmethod
    def _resolve_execution_state(
        guard: GuardDecision,
        effective_action: str,
        needs_reconciliation: bool,
        *,
        primary_execution_failed: bool,
        auxiliary_execution_failed: bool,
        observed_position_size_pct: float,
    ) -> ExecutionLayerState:
        if guard.blocked:
            return ExecutionLayerState.BLOCKED
        if guard.degraded:
            return ExecutionLayerState.DEGRADED
        if primary_execution_failed:
            if effective_action in {"entry_long", "entry_short", "small_probe"}:
                return ExecutionLayerState.ENTRY_PENDING
            if effective_action == "reduce":
                return ExecutionLayerState.REDUCE_PENDING
            if effective_action == "exit":
                return ExecutionLayerState.EXIT_PENDING
        if auxiliary_execution_failed or needs_reconciliation:
            return ExecutionLayerState.RECONCILING
        if effective_action in {"entry_long", "entry_short", "small_probe"}:
            return ExecutionLayerState.ENTRY_PENDING
        if effective_action == "reduce":
            return ExecutionLayerState.REDUCE_PENDING
        if effective_action == "exit":
            return ExecutionLayerState.EXIT_PENDING
        if observed_position_size_pct > 0.0:
            return ExecutionLayerState.POSITION_OPEN
        return ExecutionLayerState.IDLE

    @staticmethod
    def _resolve_pending_action(
        effective_action: str,
        *,
        primary_execution_failed: bool,
        auxiliary_execution_failed: bool,
    ) -> str:
        if effective_action in {"", "wait", "observe_only", "paper_only"}:
            return ""
        if auxiliary_execution_failed:
            return ""
        if primary_execution_failed:
            return effective_action
        return effective_action

    @staticmethod
    def _summarize_execution_results(
        execution_results: Sequence[CommandExecutionResult] | None,
    ) -> dict[str, bool]:
        summary = {
            "has_failure": False,
            "primary_failed": False,
            "auxiliary_failed": False,
            "protective_stop_failed": False,
        }
        if not execution_results:
            return summary
        primary_targets = {"entry_order", "reduce_order", "exit_order"}
        failure_statuses = {"failed", "rejected", "error", "timeout", "not_implemented"}
        for result in execution_results:
            failed = (not result.accepted) or (result.status.lower() in failure_statuses)
            if not failed:
                continue
            summary["has_failure"] = True
            if result.target in primary_targets:
                summary["primary_failed"] = True
                continue
            summary["auxiliary_failed"] = True
            if result.target == "maintain_protective_stop":
                summary["protective_stop_failed"] = True
        return summary

    @staticmethod
    def _collect_recent_idempotency_keys(
        *,
        previous: Sequence[str],
        execution_results: Sequence[CommandExecutionResult] | None,
    ) -> list[str]:
        keys = [str(item) for item in previous if str(item)]
        for result in execution_results or []:
            key = str((result.details or {}).get("idempotency_key") or "")
            if key:
                keys.append(key)
        deduped = list(dict.fromkeys(keys))
        return deduped[-20:]

    @staticmethod
    def _build_recent_fill_summary(
        *,
        execution_results: Sequence[CommandExecutionResult] | None,
    ) -> dict[str, Any]:
        for result in reversed(list(execution_results or [])):
            if result.target != "sync_recent_fills":
                continue
            return {
                "status": result.status,
                "accepted": result.accepted,
                "simulated": result.simulated,
                "details": dict(result.details),
            }
        return {}
