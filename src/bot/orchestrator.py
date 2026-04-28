from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from .audit_logger import AuditLogger
from .config import BotConfig, RuntimeMode
from .engine_client import EngineClient
from .exchange_adapter import AdapterCapabilities, AdapterRuntimeSnapshot, ExchangeAdapter, ExchangeAdapterProtocol, ReconciliationResult
from .network_guard import NetworkGuard
from .position_manager import PositionManager
from .state_store import StateStore


class ShadowCycleReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    generated_at: datetime
    runtime_mode: str = "shadow"
    adapter_supports_real_execution: bool = False
    judgement_status: str
    requested_action: str = ""
    effective_action: str = "wait"
    plan_reason: str = ""
    action_summary: dict[str, object] = Field(default_factory=dict)
    execution_overview: dict[str, object] = Field(default_factory=dict)
    runtime_overview: dict[str, object] = Field(default_factory=dict)
    command_reasons: list[str] = Field(default_factory=list)
    command_summary: list[dict[str, str]] = Field(default_factory=list)
    command_result_summary: list[dict[str, object]] = Field(default_factory=list)
    adapter_action_types: list[str] = Field(default_factory=list)
    command_types: list[str] = Field(default_factory=list)
    command_result_statuses: list[str] = Field(default_factory=list)
    reconciliation_in_sync: bool = True
    reconciliation_reason_codes: list[str] = Field(default_factory=list)
    degraded: bool = False
    blocked: bool = False
    reason_codes: list[str] = Field(default_factory=list)
    state_path: str = ""
    audit_log_path: str = ""


class RiskAssistCycleReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    generated_at: datetime
    runtime_mode: str = "shadow"
    adapter_supports_real_execution: bool = False
    eligible: bool = False
    requested_action: str = "wait"
    effective_action: str = "wait"
    plan_reason: str = "risk_loop_skipped"
    action_summary: dict[str, object] = Field(default_factory=dict)
    execution_overview: dict[str, object] = Field(default_factory=dict)
    runtime_overview: dict[str, object] = Field(default_factory=dict)
    command_reasons: list[str] = Field(default_factory=list)
    command_summary: list[dict[str, str]] = Field(default_factory=list)
    command_result_summary: list[dict[str, object]] = Field(default_factory=list)
    adapter_action_types: list[str] = Field(default_factory=list)
    command_types: list[str] = Field(default_factory=list)
    command_result_statuses: list[str] = Field(default_factory=list)
    reconciliation_in_sync: bool = True
    reconciliation_reason_codes: list[str] = Field(default_factory=list)
    state_path: str = ""
    audit_log_path: str = ""


class ShadowOrchestrator:
    def __init__(
        self,
        config: BotConfig,
        *,
        engine_client: EngineClient | None = None,
        network_guard: NetworkGuard | None = None,
        state_store: StateStore | None = None,
        audit_logger: AuditLogger | None = None,
        position_manager: PositionManager | None = None,
        exchange_adapter: ExchangeAdapterProtocol | None = None,
    ) -> None:
        self._config = config
        self._engine_client = engine_client or EngineClient(config)
        self._network_guard = network_guard or NetworkGuard()
        self._state_store = state_store or StateStore(config.state_store_path)
        self._audit_logger = audit_logger or AuditLogger(config.audit_log_path)
        self._position_manager = position_manager or PositionManager()
        self._exchange_adapter = exchange_adapter or ExchangeAdapter()

    def run_cycle(self, *, generated_at: datetime | None = None) -> ShadowCycleReport:
        cycle_generated_at = generated_at or datetime.now().replace(microsecond=0)
        state = self._state_store.load()
        cycle = self._engine_client.fetch_cycle(
            current_state=state.observed_position_state,
            current_position_size_pct=state.observed_position_size_pct,
            current_position_direction=state.observed_position_direction,
            generated_at=cycle_generated_at,
        )
        guard = self._network_guard.evaluate(judgement=cycle.judgement, handoff=cycle.handoff)
        capabilities = self._exchange_adapter.get_capabilities()
        runtime_snapshot = self._exchange_adapter.fetch_runtime_snapshot()
        reconciliation = self._exchange_adapter.assess_reconciliation(
            runtime_snapshot=runtime_snapshot,
            expected_position_state=state.observed_position_state,
            expected_direction=state.observed_position_direction,
            expected_size_pct=state.observed_position_size_pct,
        )
        execution_plan = self._position_manager.build_execution_plan(
            handoff=cycle.handoff,
            guard=guard,
            runtime_state=self._build_runtime_state_payload(
                state=state,
                handoff=cycle.handoff,
                runtime_snapshot=runtime_snapshot,
                reconciliation=reconciliation,
            ),
        )
        adapter_actions = self._exchange_adapter.plan_actions(
            execution_plan=execution_plan,
            handoff=cycle.handoff,
        )
        execution_commands = self._exchange_adapter.build_commands(
            execution_plan=execution_plan,
            handoff=cycle.handoff,
        )
        execution_results = self._exchange_adapter.execute_commands(
            commands=execution_commands,
            runtime_mode=self._config.runtime_mode,
        )
        runtime_snapshot_for_state = self._resolve_runtime_snapshot_after_execution(
            runtime_snapshot=runtime_snapshot,
            execution_results=execution_results,
        )
        command_summary = self._summarize_commands(execution_commands)
        command_result_summary = self._summarize_command_results(execution_results)
        action_summary = self._summarize_action(
            requested_action=execution_plan.requested_action,
            effective_action=execution_plan.effective_action,
            plan_reason=execution_plan.plan_reason,
            guard=guard,
            reconciliation=reconciliation,
        )
        execution_result_summary = self._summarize_execution_results(execution_results)
        execution_overview = self._summarize_execution_overview(
            requested_action=execution_plan.requested_action,
            effective_action=execution_plan.effective_action,
            execution_commands=execution_commands,
            execution_results=execution_results,
            execution_result_summary=execution_result_summary,
        )
        updated_state = self._state_store.record_shadow_cycle(
            state=state,
            judgement=cycle.judgement,
            handoff=cycle.handoff,
            guard=guard,
            effective_action=execution_plan.effective_action,
            plan_reason=execution_plan.plan_reason,
            needs_reconciliation=execution_plan.needs_reconciliation,
            maintain_protective_stop=execution_plan.maintain_protective_stop,
            execution_results=execution_results,
            runtime_snapshot=runtime_snapshot_for_state,
        )
        runtime_overview = self._summarize_runtime_overview(
            expected_position_state=state.observed_position_state,
            expected_direction=state.observed_position_direction,
            expected_size_pct=state.observed_position_size_pct,
            runtime_snapshot=runtime_snapshot_for_state,
            reconciliation=reconciliation,
            updated_state=updated_state,
        )
        self._audit_logger.append(
            event_type="shadow_cycle",
            generated_at=cycle_generated_at,
            payload={
                "runtime_mode": self._config.runtime_mode.value,
                "engine_mode": self._config.engine_mode.value,
                "adapter_capabilities": capabilities.model_dump(mode="json"),
                "judgement_status": cycle.judgement.get("status"),
                "requested_action": execution_plan.requested_action,
                "effective_action": execution_plan.effective_action,
                "plan_reason": execution_plan.plan_reason,
                "action_summary": action_summary,
                "execution_overview": execution_overview,
                "runtime_overview": runtime_overview,
                "guard": guard.model_dump(mode="json"),
                "runtime_snapshot": runtime_snapshot_for_state.model_dump(mode="json"),
                "reconciliation": reconciliation.model_dump(mode="json"),
                "execution_plan": execution_plan.model_dump(mode="json"),
                "command_reasons": [command.reason for command in execution_commands],
                "command_summary": command_summary,
                "command_result_summary": command_result_summary,
                "execution_commands": [command.model_dump(mode="json") for command in execution_commands],
                "execution_results": [result.model_dump(mode="json") for result in execution_results],
                "execution_result_summary": execution_result_summary,
                "adapter_actions": [action.model_dump(mode="json") for action in adapter_actions],
                "state": updated_state.model_dump(mode="json"),
                "recent_fill_summary": updated_state.recent_fill_summary,
                "handoff": cycle.handoff or {},
            },
        )
        return ShadowCycleReport(
            generated_at=cycle_generated_at,
            runtime_mode=self._config.runtime_mode.value,
            adapter_supports_real_execution=capabilities.supports_real_execution,
            judgement_status=str(cycle.judgement.get("status") or ""),
            requested_action=execution_plan.requested_action,
            effective_action=execution_plan.effective_action,
            plan_reason=execution_plan.plan_reason,
            action_summary=action_summary,
            execution_overview=execution_overview,
            runtime_overview=runtime_overview,
            command_reasons=[command.reason for command in execution_commands],
            command_summary=command_summary,
            command_result_summary=command_result_summary,
            adapter_action_types=[action.action_type for action in adapter_actions],
            command_types=[command.target for command in execution_commands],
            command_result_statuses=[result.status for result in execution_results],
            reconciliation_in_sync=reconciliation.in_sync,
            reconciliation_reason_codes=list(reconciliation.reason_codes),
            degraded=guard.degraded,
            blocked=guard.blocked,
            reason_codes=list(guard.reason_codes),
            state_path=str(self._state_store.output_path),
            audit_log_path=str(self._audit_logger.output_path),
        )

    def run_risk_assist_cycle(self, *, generated_at: datetime | None = None) -> RiskAssistCycleReport:
        cycle_generated_at = generated_at or datetime.now().replace(microsecond=0)
        state = self._state_store.load()
        if not self._is_risk_cycle_eligible(state):
            self._audit_logger.append(
                event_type="risk_assist_cycle_skipped",
                generated_at=cycle_generated_at,
                payload={
                    "reason": "risk_loop_not_eligible",
                    "state": state.model_dump(mode="json"),
                },
            )
            return RiskAssistCycleReport(
                generated_at=cycle_generated_at,
                runtime_mode=self._config.runtime_mode.value,
                adapter_supports_real_execution=False,
                eligible=False,
                state_path=str(self._state_store.output_path),
                audit_log_path=str(self._audit_logger.output_path),
            )

        cycle = self._engine_client.fetch_risk_cycle(
            current_state=state.observed_position_state,
            current_position_size_pct=state.observed_position_size_pct,
            current_position_direction=state.observed_position_direction,
            generated_at=cycle_generated_at,
        )
        guard = self._network_guard.evaluate(judgement=cycle.judgement, handoff=cycle.handoff)
        capabilities = self._exchange_adapter.get_capabilities()
        runtime_snapshot = self._exchange_adapter.fetch_runtime_snapshot()
        reconciliation = self._exchange_adapter.assess_reconciliation(
            runtime_snapshot=runtime_snapshot,
            expected_position_state=state.observed_position_state,
            expected_direction=state.observed_position_direction,
            expected_size_pct=state.observed_position_size_pct,
        )
        execution_plan = self._position_manager.build_execution_plan(
            handoff=cycle.handoff,
            guard=guard,
            runtime_state=self._build_runtime_state_payload(
                state=state,
                handoff=cycle.handoff,
                runtime_snapshot=runtime_snapshot,
                reconciliation=reconciliation,
            ),
        )
        adapter_actions = self._exchange_adapter.plan_actions(
            execution_plan=execution_plan,
            handoff=cycle.handoff,
        )
        execution_commands = self._exchange_adapter.build_commands(
            execution_plan=execution_plan,
            handoff=cycle.handoff,
        )
        execution_results = self._exchange_adapter.execute_commands(
            commands=execution_commands,
            runtime_mode=self._config.runtime_mode,
        )
        runtime_snapshot_for_state = self._resolve_runtime_snapshot_after_execution(
            runtime_snapshot=runtime_snapshot,
            execution_results=execution_results,
        )
        command_summary = self._summarize_commands(execution_commands)
        command_result_summary = self._summarize_command_results(execution_results)
        action_summary = self._summarize_action(
            requested_action=execution_plan.requested_action,
            effective_action=execution_plan.effective_action,
            plan_reason=execution_plan.plan_reason,
            guard=guard,
            reconciliation=reconciliation,
        )
        execution_result_summary = self._summarize_execution_results(execution_results)
        execution_overview = self._summarize_execution_overview(
            requested_action=execution_plan.requested_action,
            effective_action=execution_plan.effective_action,
            execution_commands=execution_commands,
            execution_results=execution_results,
            execution_result_summary=execution_result_summary,
        )
        updated_state = self._state_store.record_shadow_cycle(
            state=state,
            judgement=cycle.judgement,
            handoff=cycle.handoff,
            guard=guard,
            effective_action=execution_plan.effective_action,
            plan_reason=execution_plan.plan_reason,
            needs_reconciliation=execution_plan.needs_reconciliation,
            maintain_protective_stop=execution_plan.maintain_protective_stop,
            execution_results=execution_results,
            runtime_snapshot=runtime_snapshot_for_state,
        )
        runtime_overview = self._summarize_runtime_overview(
            expected_position_state=state.observed_position_state,
            expected_direction=state.observed_position_direction,
            expected_size_pct=state.observed_position_size_pct,
            runtime_snapshot=runtime_snapshot_for_state,
            reconciliation=reconciliation,
            updated_state=updated_state,
        )
        self._audit_logger.append(
            event_type="risk_assist_cycle",
            generated_at=cycle_generated_at,
            payload={
                "requested_action": execution_plan.requested_action,
                "effective_action": execution_plan.effective_action,
                "plan_reason": execution_plan.plan_reason,
                "action_summary": action_summary,
                "execution_overview": execution_overview,
                "runtime_overview": runtime_overview,
                "runtime_mode": self._config.runtime_mode.value,
                "adapter_capabilities": capabilities.model_dump(mode="json"),
                "runtime_snapshot": runtime_snapshot_for_state.model_dump(mode="json"),
                "reconciliation": reconciliation.model_dump(mode="json"),
                "command_reasons": [command.reason for command in execution_commands],
                "command_summary": command_summary,
                "command_result_summary": command_result_summary,
                "execution_commands": [command.model_dump(mode="json") for command in execution_commands],
                "execution_results": [result.model_dump(mode="json") for result in execution_results],
                "execution_result_summary": execution_result_summary,
                "adapter_actions": [action.model_dump(mode="json") for action in adapter_actions],
                "state": updated_state.model_dump(mode="json"),
                "recent_fill_summary": updated_state.recent_fill_summary,
                "handoff": cycle.handoff or {},
            },
        )
        return RiskAssistCycleReport(
            generated_at=cycle_generated_at,
            runtime_mode=self._config.runtime_mode.value,
            adapter_supports_real_execution=capabilities.supports_real_execution,
            eligible=True,
            requested_action=execution_plan.requested_action,
            effective_action=execution_plan.effective_action,
            plan_reason=execution_plan.plan_reason,
            action_summary=action_summary,
            execution_overview=execution_overview,
            runtime_overview=runtime_overview,
            command_reasons=[command.reason for command in execution_commands],
            command_summary=command_summary,
            command_result_summary=command_result_summary,
            adapter_action_types=[action.action_type for action in adapter_actions],
            command_types=[command.target for command in execution_commands],
            command_result_statuses=[result.status for result in execution_results],
            reconciliation_in_sync=reconciliation.in_sync,
            reconciliation_reason_codes=list(reconciliation.reason_codes),
            state_path=str(self._state_store.output_path),
            audit_log_path=str(self._audit_logger.output_path),
        )

    @staticmethod
    def _summarize_action(
        *,
        requested_action: str,
        effective_action: str,
        plan_reason: str,
        guard,
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

    @staticmethod
    def _summarize_execution_overview(
        *,
        requested_action: str,
        effective_action: str,
        execution_commands,
        execution_results,
        execution_result_summary: dict[str, bool],
    ) -> dict[str, object]:
        primary_targets = {"entry_order", "reduce_order", "exit_order"}
        primary_command = next((command for command in execution_commands if command.target in primary_targets), None)
        primary_result = next((result for result in execution_results if result.target in primary_targets), None)
        auxiliary_targets = [command.target for command in execution_commands if command.target not in primary_targets]
        return {
            "requested_action": requested_action,
            "effective_action": effective_action,
            "primary_target": primary_command.target if primary_command else "",
            "primary_reason": primary_command.reason if primary_command else "",
            "primary_status": primary_result.status if primary_result else "not_applicable",
            "primary_accepted": primary_result.accepted if primary_result else False,
            "has_primary_failure": execution_result_summary["primary_failed"],
            "has_auxiliary_failure": execution_result_summary["auxiliary_failed"],
            "auxiliary_targets": auxiliary_targets,
        }

    @staticmethod
    def _summarize_runtime_overview(
        *,
        expected_position_state: str,
        expected_direction: str,
        expected_size_pct: float,
        runtime_snapshot: AdapterRuntimeSnapshot,
        reconciliation: ReconciliationResult,
        updated_state,
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

    @staticmethod
    def _summarize_commands(execution_commands) -> list[dict[str, str]]:
        return [
            {
                "target": command.target,
                "reason": command.reason,
                "operation": command.operation,
            }
            for command in execution_commands
        ]

    @staticmethod
    def _summarize_command_results(execution_results) -> list[dict[str, object]]:
        return [
            {
                "target": result.target,
                "reason": result.reason,
                "status": result.status,
                "accepted": result.accepted,
                "simulated": result.simulated,
            }
            for result in execution_results
        ]

    def _resolve_runtime_snapshot_after_execution(
        self,
        *,
        runtime_snapshot: AdapterRuntimeSnapshot,
        execution_results,
    ) -> AdapterRuntimeSnapshot:
        if self._config.runtime_mode != RuntimeMode.REAL:
            return runtime_snapshot
        if not any(self._result_requires_runtime_snapshot_refresh(result) for result in execution_results):
            return runtime_snapshot
        return self._exchange_adapter.fetch_runtime_snapshot()

    @staticmethod
    def _result_requires_runtime_snapshot_refresh(result) -> bool:
        return (
            result.target in {"entry_order", "reduce_order", "exit_order"}
            and result.accepted
            and str(result.status).lower() == "accepted"
        )

    @staticmethod
    def _summarize_execution_results(execution_results) -> dict[str, bool]:
        has_failure = False
        primary_failed = False
        auxiliary_failed = False
        protective_stop_failed = False
        primary_targets = {"entry_order", "reduce_order", "exit_order"}
        failure_statuses = {"failed", "rejected", "error", "timeout", "not_implemented"}
        for result in execution_results:
            failed = (not result.accepted) or (str(result.status).lower() in failure_statuses)
            if not failed:
                continue
            has_failure = True
            if result.target in primary_targets:
                primary_failed = True
                continue
            auxiliary_failed = True
            if result.target == "maintain_protective_stop":
                protective_stop_failed = True
        return {
            "has_failure": has_failure,
            "primary_failed": primary_failed,
            "auxiliary_failed": auxiliary_failed,
            "protective_stop_failed": protective_stop_failed,
        }

    @staticmethod
    def _is_risk_cycle_eligible(state) -> bool:
        return bool(
            state.observed_position_size_pct > 0.0
            or state.observed_position_state == "ENTERED"
            or state.pending_action
            or state.recovery_required
            or state.execution_state.value == "degraded"
        )

    @staticmethod
    def _is_runtime_snapshot_valid(runtime_snapshot: AdapterRuntimeSnapshot) -> bool:
        return bool(runtime_snapshot.snapshot_valid)

    @staticmethod
    def _build_runtime_state_payload(
        *,
        state,
        handoff: dict[str, object] | None,
        runtime_snapshot: AdapterRuntimeSnapshot,
        reconciliation: ReconciliationResult,
    ) -> dict[str, object]:
        payload = state.model_dump(mode="json")
        runtime_snapshot_valid = ShadowOrchestrator._is_runtime_snapshot_valid(runtime_snapshot)
        runtime_position = runtime_snapshot.position
        runtime_position_state = str(runtime_position.position_state or "") if runtime_snapshot_valid else ""
        runtime_position_direction = str(runtime_position.direction or "") if runtime_snapshot_valid else ""
        runtime_position_size_pct = float(runtime_position.size_pct or 0.0) if runtime_snapshot_valid else 0.0
        handoff = handoff or {}
        expected_open_risk = bool(
            state.observed_position_size_pct > 0.0
            or runtime_position_size_pct > 0.0
            or state.observed_position_state == "ENTERED"
            or runtime_position_state == "ENTERED"
        )
        if runtime_position_state:
            payload["observed_position_state"] = runtime_position_state
        if runtime_position_direction:
            payload["observed_position_direction"] = runtime_position_direction
        if runtime_position_size_pct > 0.0:
            payload["observed_position_size_pct"] = runtime_position_size_pct
        observed_position_size_pct = float(payload.get("observed_position_size_pct") or 0.0)
        payload["breakeven_ready"] = bool(handoff.get("breakeven_trigger")) and expected_open_risk
        payload["trailing_ready"] = bool(handoff.get("trailing_activation_ratio")) and bool(
            handoff.get("trailing_callback_rate_pct")
        ) and expected_open_risk
        payload["recent_fill_sync_required"] = (
            runtime_snapshot_valid
            and runtime_position_state == "ENTERED"
            and runtime_position_size_pct <= 0.0
            and observed_position_size_pct <= 0.0
            and (
                reconciliation.needs_position_sync
                or "position_size_mismatch" in reconciliation.reason_codes
                or "position_state_mismatch" in reconciliation.reason_codes
            )
        )
        payload["protective_stop_required"] = bool(payload.get("protective_stop_required")) or (
            expected_open_risk and not reconciliation.protective_stop_present
        )
        payload["recovery_required"] = not reconciliation.in_sync
        payload["reconciliation_required"] = not reconciliation.in_sync
        return payload
