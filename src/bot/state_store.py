from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .action_enums import PositionAction
from .automation_state import AutomationState, coerce_automation_state_for_execution_layer
from .exchange_adapter import AdapterRuntimeSnapshot, CommandExecutionResult
from .execution_summary import summarize_execution_results
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
    automation_state: AutomationState = AutomationState.DISABLED
    observed_position_state: str = "FLAT"
    observed_position_direction: str = "neutral"
    observed_position_size_pct: float = Field(ge=0.0, le=1.0, default=0.0)
    pending_action: str = ""
    recovery_required: bool = False
    reconciliation_required: bool = False
    protective_stop_required: bool = False
    consecutive_api_failure_count: int = Field(default=0, ge=0)
    last_api_failure_at: str = ""
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

    def record_api_success(self, *, state: BotRuntimeState | None = None) -> BotRuntimeState:
        next_state = (state or self.load()).model_copy(deep=True)
        next_state.consecutive_api_failure_count = 0
        next_state.last_api_failure_at = ""
        if next_state.execution_state is ExecutionLayerState.DEGRADED and not next_state.reconciliation_required:
            if next_state.observed_position_state == "ENTERED" or next_state.observed_position_size_pct > 0.0:
                next_state.execution_state = ExecutionLayerState.POSITION_OPEN
            else:
                next_state.execution_state = ExecutionLayerState.IDLE
            next_state.automation_state = coerce_automation_state_for_execution_layer(
                execution_state=next_state.execution_state.value,
                automation_state=next_state.automation_state,
            )
        return self.save(next_state)

    def record_api_failure(
        self,
        *,
        state: BotRuntimeState | None = None,
        reason_code: str,
        failed_at: datetime | None = None,
        degraded_threshold: int = 3,
    ) -> BotRuntimeState:
        next_state = (state or self.load()).model_copy(deep=True)
        next_state.consecutive_api_failure_count += 1
        next_state.last_api_failure_at = (failed_at or datetime.now().replace(microsecond=0)).isoformat()
        if reason_code and reason_code not in next_state.last_reason_codes:
            next_state.last_reason_codes = [*next_state.last_reason_codes, reason_code]
        if next_state.consecutive_api_failure_count >= max(1, int(degraded_threshold)):
            next_state.execution_state = ExecutionLayerState.DEGRADED
            next_state.automation_state = coerce_automation_state_for_execution_layer(
                execution_state=next_state.execution_state.value,
                automation_state=next_state.automation_state,
            )
            next_state.recovery_required = True
        return self.save(next_state)

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
        execution_summary = summarize_execution_results(execution_results)
        previous_observed_position_state = state.observed_position_state
        previous_observed_position_size_pct = state.observed_position_size_pct
        self._apply_observed_position_inputs(
            next_state=next_state,
            handoff=handoff,
            effective_action=effective_action,
            execution_results=execution_results,
            runtime_snapshot=runtime_snapshot,
        )
        observed_position_state = next_state.observed_position_state
        observed_position_size_pct = next_state.observed_position_size_pct
        next_state.execution_state = self._resolve_execution_state(
            guard,
            effective_action,
            needs_reconciliation,
            primary_execution_failed=execution_summary["primary_failed"],
            primary_execution_succeeded=execution_summary["primary_succeeded"],
            auxiliary_execution_failed=execution_summary["auxiliary_failed"],
            previous_observed_position_state=previous_observed_position_state,
            previous_observed_position_size_pct=previous_observed_position_size_pct,
            observed_position_state=observed_position_state,
            observed_position_size_pct=observed_position_size_pct,
        )
        next_state.automation_state = coerce_automation_state_for_execution_layer(
            execution_state=next_state.execution_state.value,
            automation_state=next_state.automation_state,
        )
        next_state.pending_action = self._resolve_pending_action(
            effective_action,
            primary_execution_failed=execution_summary["primary_failed"],
            primary_execution_succeeded=execution_summary["primary_succeeded"],
            auxiliary_execution_failed=execution_summary["auxiliary_failed"],
            needs_reconciliation=needs_reconciliation,
            previous_observed_position_state=previous_observed_position_state,
            previous_observed_position_size_pct=previous_observed_position_size_pct,
            observed_position_state=observed_position_state,
            observed_position_size_pct=observed_position_size_pct,
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
        self._update_active_probe_metadata(
            next_state=next_state,
            handoff=handoff,
            effective_action=effective_action,
            execution_results=execution_results,
        )
        return self.save(next_state)

    @staticmethod
    def _is_runtime_snapshot_valid(runtime_snapshot: AdapterRuntimeSnapshot) -> bool:
        return bool(runtime_snapshot.snapshot_valid)

    @staticmethod
    def _apply_observed_position_inputs(
        *,
        next_state: BotRuntimeState,
        handoff: dict[str, Any] | None,
        effective_action: str,
        execution_results: Sequence[CommandExecutionResult] | None,
        runtime_snapshot: AdapterRuntimeSnapshot | None,
    ) -> None:
        has_reconciliation_summary = StateStore._has_accepted_reconciliation_summary(execution_results)
        if runtime_snapshot is not None and StateStore._is_runtime_snapshot_valid(runtime_snapshot) and not has_reconciliation_summary:
            next_state.observed_position_state = str(runtime_snapshot.position.position_state or next_state.observed_position_state)
            next_state.observed_position_direction = str(runtime_snapshot.position.direction or next_state.observed_position_direction)
            next_state.observed_position_size_pct = float(runtime_snapshot.position.size_pct)
            return
        StateStore._apply_reconciliation_summary(
            next_state=next_state,
            handoff=handoff,
            execution_results=execution_results,
        )
        if has_reconciliation_summary:
            return
        if runtime_snapshot is not None and StateStore._is_runtime_snapshot_valid(runtime_snapshot):
            next_state.observed_position_state = str(runtime_snapshot.position.position_state or next_state.observed_position_state)
            next_state.observed_position_direction = str(runtime_snapshot.position.direction or next_state.observed_position_direction)
            next_state.observed_position_size_pct = float(runtime_snapshot.position.size_pct)
            return
        if not handoff:
            return
        requested_action = str(handoff.get("action") or "")
        entry_actions = {PositionAction.ENTRY_LONG.value, PositionAction.ENTRY_SHORT.value, PositionAction.SMALL_PROBE.value}
        if requested_action in entry_actions and effective_action not in entry_actions:
            return
        fallback_state = str(handoff.get("position_state") or "")
        fallback_direction = str(handoff.get("current_position_direction") or "")
        fallback_size = handoff.get("position_size_pct")
        observed_size_pct = float(next_state.observed_position_size_pct)
        if fallback_state and next_state.observed_position_state in {"", "FLAT"} and observed_size_pct <= 0.0:
            next_state.observed_position_state = fallback_state
        if fallback_direction and next_state.observed_position_direction in {"", "neutral"} and observed_size_pct <= 0.0:
            next_state.observed_position_direction = fallback_direction
        if observed_size_pct <= 0.0 and fallback_size is not None:
            next_state.observed_position_size_pct = float(fallback_size)

    @staticmethod
    def _resolve_execution_state(
        guard: GuardDecision,
        effective_action: str,
        needs_reconciliation: bool,
        *,
        primary_execution_failed: bool,
        primary_execution_succeeded: bool,
        auxiliary_execution_failed: bool,
        previous_observed_position_state: str,
        previous_observed_position_size_pct: float,
        observed_position_state: str,
        observed_position_size_pct: float,
    ) -> ExecutionLayerState:
        if guard.blocked:
            return ExecutionLayerState.BLOCKED
        if guard.degraded:
            return ExecutionLayerState.DEGRADED
        if primary_execution_failed:
            if effective_action in {PositionAction.ENTRY_LONG.value, PositionAction.ENTRY_SHORT.value, PositionAction.SMALL_PROBE.value}:
                return ExecutionLayerState.ENTRY_PENDING
            if effective_action == PositionAction.REDUCE.value:
                return ExecutionLayerState.REDUCE_PENDING
            if effective_action == PositionAction.EXIT.value:
                return ExecutionLayerState.EXIT_PENDING
        if auxiliary_execution_failed or needs_reconciliation:
            return ExecutionLayerState.RECONCILING
        if primary_execution_succeeded:
            if effective_action in {PositionAction.ENTRY_LONG.value, PositionAction.ENTRY_SHORT.value, PositionAction.SMALL_PROBE.value}:
                if observed_position_state == "ENTERED" or observed_position_size_pct > 0.0:
                    return ExecutionLayerState.POSITION_OPEN
                return ExecutionLayerState.ENTRY_PENDING
            if effective_action == PositionAction.REDUCE.value:
                if StateStore._observed_reduce_applied(
                    previous_observed_position_state=previous_observed_position_state,
                    previous_observed_position_size_pct=previous_observed_position_size_pct,
                    observed_position_state=observed_position_state,
                    observed_position_size_pct=observed_position_size_pct,
                ):
                    return ExecutionLayerState.POSITION_OPEN if (
                        observed_position_state == "ENTERED" or observed_position_size_pct > 0.0
                    ) else ExecutionLayerState.IDLE
                return ExecutionLayerState.REDUCE_PENDING
            if effective_action == PositionAction.EXIT.value:
                if observed_position_state == "FLAT" and observed_position_size_pct <= 0.0:
                    return ExecutionLayerState.IDLE
                return ExecutionLayerState.EXIT_PENDING
        if effective_action in {PositionAction.ENTRY_LONG.value, PositionAction.ENTRY_SHORT.value, PositionAction.SMALL_PROBE.value}:
            return ExecutionLayerState.ENTRY_PENDING
        if effective_action == PositionAction.REDUCE.value:
            return ExecutionLayerState.REDUCE_PENDING
        if effective_action == PositionAction.EXIT.value:
            return ExecutionLayerState.EXIT_PENDING
        if observed_position_state == "ENTERED" or observed_position_size_pct > 0.0:
            return ExecutionLayerState.POSITION_OPEN
        return ExecutionLayerState.IDLE

    @staticmethod
    def _resolve_pending_action(
        effective_action: str,
        *,
        primary_execution_failed: bool,
        primary_execution_succeeded: bool,
        auxiliary_execution_failed: bool,
        needs_reconciliation: bool,
        previous_observed_position_state: str,
        previous_observed_position_size_pct: float,
        observed_position_state: str,
        observed_position_size_pct: float,
    ) -> str:
        if effective_action in {"", PositionAction.WAIT.value, PositionAction.OBSERVE_ONLY.value, PositionAction.PAPER_ONLY.value}:
            return ""
        if auxiliary_execution_failed:
            return ""
        if primary_execution_failed:
            return effective_action
        if primary_execution_succeeded:
            if effective_action in {PositionAction.ENTRY_LONG.value, PositionAction.ENTRY_SHORT.value, PositionAction.SMALL_PROBE.value}:
                if auxiliary_execution_failed or needs_reconciliation:
                    return effective_action
                return "" if (observed_position_state == "ENTERED" or observed_position_size_pct > 0.0) else effective_action
            if effective_action == PositionAction.REDUCE.value:
                if auxiliary_execution_failed or needs_reconciliation:
                    return effective_action
                return "" if StateStore._observed_reduce_applied(
                    previous_observed_position_state=previous_observed_position_state,
                    previous_observed_position_size_pct=previous_observed_position_size_pct,
                    observed_position_state=observed_position_state,
                    observed_position_size_pct=observed_position_size_pct,
                ) else effective_action
            if effective_action == PositionAction.EXIT.value:
                if auxiliary_execution_failed or needs_reconciliation:
                    return effective_action
                return "" if (observed_position_state == "FLAT" and observed_position_size_pct <= 0.0) else effective_action
        return effective_action

    @staticmethod
    def _observed_reduce_applied(
        *,
        previous_observed_position_state: str,
        previous_observed_position_size_pct: float,
        observed_position_state: str,
        observed_position_size_pct: float,
    ) -> bool:
        if observed_position_state == "FLAT" and observed_position_size_pct <= 0.0:
            return True
        if previous_observed_position_state != "ENTERED" and previous_observed_position_size_pct <= 0.0:
            return False
        return observed_position_size_pct < previous_observed_position_size_pct

    @staticmethod
    def _collect_recent_idempotency_keys(
        *,
        previous: Sequence[str],
        execution_results: Sequence[CommandExecutionResult] | None,
    ) -> list[str]:
        keys = [str(item) for item in previous if str(item)]
        for result in execution_results or []:
            key = str(result.idempotency_key or (result.details or {}).get("idempotency_key") or "")
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
            details = dict(result.details)
            response_summary = details.get("response_summary")
            normalized_details = dict(response_summary) if isinstance(response_summary, dict) and response_summary else details
            return {
                "status": result.status,
                "accepted": result.accepted,
                "simulated": result.simulated,
                "details": normalized_details,
            }
        return {}

    @staticmethod
    def _has_accepted_reconciliation_summary(execution_results: Sequence[CommandExecutionResult] | None) -> bool:
        for result in reversed(list(execution_results or [])):
            if result.target != "reconcile_position_and_orders" or not result.accepted:
                continue
            summary = (result.details or {}).get("response_summary") or {}
            if isinstance(summary, dict) and summary:
                return True
        return False

    @staticmethod
    def _update_active_probe_metadata(
        *,
        next_state: BotRuntimeState,
        handoff: dict[str, Any] | None,
        effective_action: str,
        execution_results: Sequence[CommandExecutionResult] | None,
    ) -> None:
        metadata = dict(next_state.metadata)
        if next_state.observed_position_state == "FLAT" and next_state.observed_position_size_pct <= 0.0:
            StateStore._clear_active_probe_metadata(metadata)
            next_state.metadata = metadata
            return
        if not StateStore._is_active_contrarian_probe_entry(
            next_state=next_state,
            handoff=handoff,
            effective_action=effective_action,
            execution_results=execution_results,
        ):
            next_state.metadata = metadata
            return
        handoff = handoff or {}
        started_at = StateStore._parse_datetime(handoff.get("generated_at")) or datetime.now().replace(microsecond=0)
        expiry_bars = int(handoff.get("probe_expiry_bars") or 0)
        expiry_timeframe = str(handoff.get("probe_expiry_timeframe") or "")
        expires_at = StateStore._probe_expiry_timestamp(
            started_at=started_at,
            bars=expiry_bars,
            timeframe=expiry_timeframe,
        )
        metadata.update(
            {
                "active_probe_source": "contrarian_short_probe",
                "active_probe_started_at": started_at.isoformat(),
                "active_probe_expiry_bars": expiry_bars,
                "active_probe_expiry_timeframe": expiry_timeframe,
                "active_probe_expires_at": expires_at.isoformat() if expires_at else "",
                "active_probe_invalid_if_no_followthrough": bool(handoff.get("probe_invalid_if_no_followthrough")),
                "active_probe_risk_tier": str(handoff.get("probe_risk_tier") or ""),
            }
        )
        next_state.metadata = metadata

    @staticmethod
    def _is_active_contrarian_probe_entry(
        *,
        next_state: BotRuntimeState,
        handoff: dict[str, Any] | None,
        effective_action: str,
        execution_results: Sequence[CommandExecutionResult] | None,
    ) -> bool:
        if effective_action != PositionAction.SMALL_PROBE.value:
            return False
        if str((handoff or {}).get("probe_source") or "") != "contrarian_short_probe":
            return False
        if StateStore._has_accepted_entry_order(execution_results):
            return True
        return str(next_state.metadata.get("active_probe_source") or "") == "contrarian_short_probe"

    @staticmethod
    def _has_accepted_entry_order(execution_results: Sequence[CommandExecutionResult] | None) -> bool:
        return any(
            result.target == "entry_order" and result.accepted
            for result in execution_results or []
        )

    @staticmethod
    def _clear_active_probe_metadata(metadata: dict[str, Any]) -> None:
        for key in (
            "active_probe_source",
            "active_probe_started_at",
            "active_probe_expiry_bars",
            "active_probe_expiry_timeframe",
            "active_probe_expires_at",
            "active_probe_invalid_if_no_followthrough",
            "active_probe_risk_tier",
        ):
            metadata.pop(key, None)

    @staticmethod
    def _parse_datetime(value: Any) -> datetime | None:
        if isinstance(value, datetime):
            return value.replace(tzinfo=None)
        text = str(value or "").strip()
        if not text:
            return None
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            return None

    @staticmethod
    def _probe_expiry_timestamp(*, started_at: datetime, bars: int, timeframe: str) -> datetime | None:
        if bars <= 0:
            return None
        timeframe_minutes = StateStore._timeframe_minutes(timeframe)
        if timeframe_minutes <= 0:
            return None
        return started_at + timedelta(minutes=bars * timeframe_minutes)

    @staticmethod
    def _timeframe_minutes(timeframe: str) -> int:
        normalized = timeframe.strip().lower()
        if normalized.endswith("m"):
            return int(normalized[:-1] or "0")
        if normalized.endswith("h"):
            return int(normalized[:-1] or "0") * 60
        return 0

    @staticmethod
    def _apply_reconciliation_summary(
        *,
        next_state: BotRuntimeState,
        handoff: dict[str, Any] | None,
        execution_results: Sequence[CommandExecutionResult] | None,
    ) -> None:
        for result in reversed(list(execution_results or [])):
            if result.target != "reconcile_position_and_orders":
                continue
            summary = ((result.details or {}).get("response_summary") or {}) if result.accepted else {}
            if not isinstance(summary, dict) or not summary:
                return
            position_state = str(summary.get("position_state") or next_state.observed_position_state)
            direction = str(summary.get("direction") or next_state.observed_position_direction)
            next_state.observed_position_state = position_state
            next_state.observed_position_direction = "neutral" if position_state == "FLAT" else direction
            if position_state == "FLAT":
                next_state.observed_position_size_pct = 0.0
                return
            summary_size = summary.get("size_pct")
            if summary_size is not None:
                next_state.observed_position_size_pct = float(summary_size)
                return
            handoff_size = (handoff or {}).get("position_size_pct")
            if handoff_size is not None:
                next_state.observed_position_size_pct = float(handoff_size)
            return
