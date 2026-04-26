from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from .audit_logger import AuditLogger
from .config import BotConfig
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
            runtime_snapshot=runtime_snapshot,
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
                "guard": guard.model_dump(mode="json"),
                "runtime_snapshot": runtime_snapshot.model_dump(mode="json"),
                "reconciliation": reconciliation.model_dump(mode="json"),
                "execution_plan": execution_plan.model_dump(mode="json"),
                "execution_commands": [command.model_dump(mode="json") for command in execution_commands],
                "execution_results": [result.model_dump(mode="json") for result in execution_results],
                "adapter_actions": [action.model_dump(mode="json") for action in adapter_actions],
                "state": updated_state.model_dump(mode="json"),
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
            runtime_snapshot=runtime_snapshot,
        )
        self._audit_logger.append(
            event_type="risk_assist_cycle",
            generated_at=cycle_generated_at,
            payload={
                "requested_action": execution_plan.requested_action,
                "effective_action": execution_plan.effective_action,
                "plan_reason": execution_plan.plan_reason,
                "runtime_mode": self._config.runtime_mode.value,
                "adapter_capabilities": capabilities.model_dump(mode="json"),
                "runtime_snapshot": runtime_snapshot.model_dump(mode="json"),
                "reconciliation": reconciliation.model_dump(mode="json"),
                "execution_commands": [command.model_dump(mode="json") for command in execution_commands],
                "execution_results": [result.model_dump(mode="json") for result in execution_results],
                "adapter_actions": [action.model_dump(mode="json") for action in adapter_actions],
                "state": updated_state.model_dump(mode="json"),
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
            adapter_action_types=[action.action_type for action in adapter_actions],
            command_types=[command.target for command in execution_commands],
            command_result_statuses=[result.status for result in execution_results],
            reconciliation_in_sync=reconciliation.in_sync,
            reconciliation_reason_codes=list(reconciliation.reason_codes),
            state_path=str(self._state_store.output_path),
            audit_log_path=str(self._audit_logger.output_path),
        )

    @staticmethod
    def _is_risk_cycle_eligible(state) -> bool:
        return bool(
            state.observed_position_size_pct > 0.0
            or state.pending_action
            or state.recovery_required
            or state.execution_state.value == "degraded"
        )

    @staticmethod
    def _build_runtime_state_payload(
        *,
        state,
        runtime_snapshot: AdapterRuntimeSnapshot,
        reconciliation: ReconciliationResult,
    ) -> dict[str, object]:
        payload = state.model_dump(mode="json")
        expected_open_risk = bool(state.observed_position_size_pct > 0.0 or runtime_snapshot.position.size_pct > 0.0)
        payload["observed_position_size_pct"] = runtime_snapshot.position.size_pct
        payload["protective_stop_required"] = bool(payload.get("protective_stop_required")) or (
            expected_open_risk and not reconciliation.protective_stop_present
        )
        payload["recovery_required"] = bool(payload.get("recovery_required")) or not reconciliation.in_sync
        payload["reconciliation_required"] = bool(payload.get("reconciliation_required")) or not reconciliation.in_sync
        return payload
