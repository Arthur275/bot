from __future__ import annotations

from datetime import datetime
from hashlib import sha256

from pydantic import BaseModel, ConfigDict, Field

from .audit_logger import AuditLogger
from .config import BotConfig, RuntimeMode
from .engine_client import EngineClient
from .execution_risk_gate import ExecutionRiskGate
from .exchange_adapter import AdapterCapabilities, AdapterRuntimeSnapshot, ExchangeAdapter, ExchangeAdapterProtocol, ReconciliationResult
from .exchange_adapter import CommandExecutionResult, ExecutionCommand
from .execution_summary import summarize_execution_results
from .network_guard import NetworkGuard
from .orchestrator_reporting import (
    summarize_action,
    summarize_command_results,
    summarize_commands,
    summarize_execution_overview,
    summarize_runtime_overview,
)
from .position_manager import PositionManager
from .state_store import StateStore
from .time_utils import parse_datetime_utc, utc_now


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
        if engine_client is None:
            raise RuntimeError("必须显式注入已绑定 live_judgement / handoff / envelope factory 的 EngineClient")
        self._engine_client = engine_client
        self._network_guard = network_guard or NetworkGuard()
        self._state_store = state_store or StateStore(config.state_store_path)
        self._audit_logger = audit_logger or AuditLogger(config.audit_log_path)
        self._position_manager = position_manager or PositionManager(
            ExecutionRiskGate.from_values(
                leverage=config.leverage,
                demo_small_account_mode=config.demo_small_account_mode,
                entry_margin_budget_usdt=config.entry_margin_budget_usdt,
                entry_margin_budget_max_equity_usdt=config.entry_margin_budget_max_equity_usdt,
                max_account_risk_pct_per_trade=config.max_account_risk_pct_per_trade,
                max_probe_account_risk_pct=config.max_probe_account_risk_pct,
                max_probe_size_pct=config.max_probe_size_pct,
                exchange_min_order_qty=config.exchange_min_order_qty,
                exchange_qty_step_size=config.exchange_qty_step_size,
                require_execution_allowed=config.require_execution_allowed,
                factor_lookup_stale_after_sec=config.factor_lookup_max_age_sec,
            )
        )
        if exchange_adapter is not None:
            self._exchange_adapter = exchange_adapter
        elif config.runtime_mode == RuntimeMode.SHADOW:
            self._exchange_adapter = ExchangeAdapter()
        else:
            raise RuntimeError("RuntimeMode.REAL 和 RuntimeMode.SIMULATED_REAL 必须显式注入支持 runtime snapshot 的 exchange adapter")

    def run_cycle(self, *, generated_at: datetime | None = None) -> ShadowCycleReport:
        cycle_generated_at = parse_datetime_utc(generated_at) or utc_now()
        try:
            return self._run_cycle_unchecked(cycle_generated_at=cycle_generated_at)
        except Exception as exc:
            self._audit_cycle_exception(
                event_type="shadow_cycle_exception",
                generated_at=cycle_generated_at,
                exc=exc,
            )
            raise

    def _run_cycle_unchecked(self, *, cycle_generated_at: datetime) -> ShadowCycleReport:
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
            adapter_capabilities=capabilities,
            runtime_state=self._build_runtime_state_payload(
                state=state,
                handoff=cycle.handoff,
                runtime_snapshot=runtime_snapshot,
                reconciliation=reconciliation,
                runtime_now=cycle_generated_at,
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
        execution_commands, confirmation_results = self._apply_manual_entry_confirmation_gate(
            execution_commands=execution_commands,
            handoff=cycle.handoff,
            generated_at=cycle_generated_at,
        )
        execution_results = self._exchange_adapter.execute_commands(
            commands=execution_commands,
            runtime_mode=self._config.runtime_mode,
        )
        execution_results = confirmation_results + execution_results
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
        execution_result_summary = summarize_execution_results(execution_results)
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
                "judgement": cycle.judgement,
                "requested_action": execution_plan.requested_action,
                "effective_action": execution_plan.effective_action,
                "plan_reason": execution_plan.plan_reason,
                "action_summary": action_summary,
                "execution_overview": execution_overview,
                "runtime_overview": runtime_overview,
                "guard": guard.model_dump(mode="json"),
                "runtime_snapshot": runtime_snapshot_for_state.model_dump(mode="json"),
                "runtime_snapshot_before": runtime_snapshot.model_dump(mode="json"),
                "runtime_snapshot_after": runtime_snapshot_for_state.model_dump(mode="json"),
                "reconciliation": reconciliation.model_dump(mode="json"),
                "execution_plan": execution_plan.model_dump(mode="json"),
                "automation_state": updated_state.automation_state.value,
                "command_reasons": [command.reason for command in execution_commands],
                "command_summary": command_summary,
                "command_result_summary": command_result_summary,
                "execution_commands": [command.model_dump(mode="json") for command in execution_commands],
                "execution_results": [result.model_dump(mode="json") for result in execution_results],
                "execution_result_summary": execution_result_summary,
                "adapter_actions": [action.model_dump(mode="json") for action in adapter_actions],
                "state": updated_state.model_dump(mode="json"),
                "reason_codes": list(guard.reason_codes),
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
        cycle_generated_at = parse_datetime_utc(generated_at) or utc_now()
        try:
            return self._run_risk_assist_cycle_unchecked(cycle_generated_at=cycle_generated_at)
        except Exception as exc:
            self._audit_cycle_exception(
                event_type="risk_assist_cycle_exception",
                generated_at=cycle_generated_at,
                exc=exc,
            )
            raise

    def _run_risk_assist_cycle_unchecked(self, *, cycle_generated_at: datetime) -> RiskAssistCycleReport:
        state = self._state_store.load()
        if not self._is_risk_cycle_eligible(state):
            self._audit_logger.append(
                event_type="risk_assist_cycle_skipped",
                generated_at=cycle_generated_at,
                payload={
                    "reason": "risk_loop_not_eligible",
                    "automation_state": state.automation_state.value,
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
            adapter_capabilities=capabilities,
            runtime_state=self._build_runtime_state_payload(
                state=state,
                handoff=cycle.handoff,
                runtime_snapshot=runtime_snapshot,
                reconciliation=reconciliation,
                runtime_now=cycle_generated_at,
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
        execution_commands, confirmation_results = self._apply_manual_entry_confirmation_gate(
            execution_commands=execution_commands,
            handoff=cycle.handoff,
            generated_at=cycle_generated_at,
        )
        execution_results = self._exchange_adapter.execute_commands(
            commands=execution_commands,
            runtime_mode=self._config.runtime_mode,
        )
        execution_results = confirmation_results + execution_results
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
        execution_result_summary = summarize_execution_results(execution_results)
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
                "engine_mode": self._config.engine_mode.value,
                "adapter_capabilities": capabilities.model_dump(mode="json"),
                "runtime_snapshot": runtime_snapshot_for_state.model_dump(mode="json"),
                "runtime_snapshot_before": runtime_snapshot.model_dump(mode="json"),
                "runtime_snapshot_after": runtime_snapshot_for_state.model_dump(mode="json"),
                "reconciliation": reconciliation.model_dump(mode="json"),
                "judgement_status": cycle.judgement.get("status"),
                "judgement": cycle.judgement,
                "guard": guard.model_dump(mode="json"),
                "execution_plan": execution_plan.model_dump(mode="json"),
                "automation_state": updated_state.automation_state.value,
                "command_reasons": [command.reason for command in execution_commands],
                "command_summary": command_summary,
                "command_result_summary": command_result_summary,
                "execution_commands": [command.model_dump(mode="json") for command in execution_commands],
                "execution_results": [result.model_dump(mode="json") for result in execution_results],
                "execution_result_summary": execution_result_summary,
                "adapter_actions": [action.model_dump(mode="json") for action in adapter_actions],
                "state": updated_state.model_dump(mode="json"),
                "reason_codes": list(guard.reason_codes),
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
        return summarize_action(
            requested_action=requested_action,
            effective_action=effective_action,
            plan_reason=plan_reason,
            guard=guard,
            reconciliation=reconciliation,
        )

    @staticmethod
    def _summarize_execution_overview(
        *,
        requested_action: str,
        effective_action: str,
        execution_commands,
        execution_results,
        execution_result_summary: dict[str, bool],
    ) -> dict[str, object]:
        return summarize_execution_overview(
            requested_action=requested_action,
            effective_action=effective_action,
            execution_commands=execution_commands,
            execution_results=execution_results,
            execution_result_summary=execution_result_summary,
        )

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
        return summarize_runtime_overview(
            expected_position_state=expected_position_state,
            expected_direction=expected_direction,
            expected_size_pct=expected_size_pct,
            runtime_snapshot=runtime_snapshot,
            reconciliation=reconciliation,
            updated_state=updated_state,
        )

    @staticmethod
    def _summarize_commands(execution_commands) -> list[dict[str, str]]:
        return summarize_commands(execution_commands)

    @staticmethod
    def _summarize_command_results(execution_results) -> list[dict[str, object]]:
        return summarize_command_results(execution_results)

    def _apply_manual_entry_confirmation_gate(
        self,
        *,
        execution_commands: list[ExecutionCommand],
        handoff: dict[str, object] | None,
        generated_at: datetime,
    ) -> tuple[list[ExecutionCommand], list[CommandExecutionResult]]:
        if self._config.runtime_mode != RuntimeMode.REAL:
            return execution_commands, []
        if not self._config.manual_entry_confirmation_required:
            return execution_commands, []
        if not any(command.target == "entry_order" for command in execution_commands):
            return execution_commands, []

        expected_token = self._build_manual_entry_confirmation_token(
            execution_commands=execution_commands,
            handoff=handoff,
            generated_at=generated_at,
        )
        if self._config.manual_entry_confirmation_token == expected_token:
            return execution_commands, []

        blocked_targets = {"entry_order", "maintain_protective_stop"}
        blocked_results = [
            CommandExecutionResult(
                target=command.target,
                status="blocked",
                accepted=False,
                simulated=True,
                reason="manual_entry_confirmation_required",
                details={
                    "confirmation_required": True,
                    "expected_confirmation_token": expected_token,
                    "provided_confirmation_token": bool(self._config.manual_entry_confirmation_token),
                    "runtime_mode": self._config.runtime_mode.value,
                    "command_type": command.command_type,
                    "operation": command.operation,
                    "payload": command.payload.model_dump(mode="json"),
                    "idempotency_key": command.idempotency_key,
                },
                idempotency_key=command.idempotency_key,
                error_kind="manual_entry_confirmation_required",
            )
            for command in execution_commands
            if command.target in blocked_targets
        ]
        executable_commands = [
            command
            for command in execution_commands
            if command.target not in blocked_targets
        ]
        return executable_commands, blocked_results

    @staticmethod
    def _build_manual_entry_confirmation_token(
        *,
        execution_commands: list[ExecutionCommand],
        handoff: dict[str, object] | None,
        generated_at: datetime,
    ) -> str:
        entry_command = next(command for command in execution_commands if command.target == "entry_order")
        payload = entry_command.payload.model_dump(mode="json")
        basis = {
            "action": payload.get("action"),
            "direction": payload.get("direction"),
            "initial_stop_loss": payload.get("initial_stop_loss"),
            "target": entry_command.target,
        }
        serialized = "|".join(f"{key}={basis[key]}" for key in sorted(basis))
        return "ENTRY-" + sha256(serialized.encode("utf-8")).hexdigest()[:12].upper()

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

    def _audit_cycle_exception(self, *, event_type: str, generated_at: datetime, exc: Exception) -> None:
        self._audit_logger.append(
            event_type=event_type,
            generated_at=generated_at,
            payload={
                "runtime_mode": self._config.runtime_mode.value,
                "engine_mode": self._config.engine_mode.value,
                "error_type": exc.__class__.__name__,
                "error_message": str(exc),
            },
        )

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

    def _build_runtime_state_payload(
        self,
        *,
        state,
        handoff: dict[str, object] | None,
        runtime_snapshot: AdapterRuntimeSnapshot,
        reconciliation: ReconciliationResult,
        runtime_now: datetime,
    ) -> dict[str, object]:
        payload = state.model_dump(mode="json")
        payload["runtime_now"] = runtime_now.isoformat()
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
        if runtime_snapshot_valid:
            payload["protective_stop_present"] = bool(runtime_snapshot.protective_stop_present)
            if runtime_snapshot.account_equity is not None:
                payload["runtime_account_equity"] = float(runtime_snapshot.account_equity)
                payload["demo_small_account_mode"] = self._config.demo_small_account_mode
                configured_budget = ShadowOrchestrator._resolve_entry_margin_budget(handoff)
                if configured_budget is not None:
                    payload["entry_margin_budget_usdt"] = min(configured_budget, float(runtime_snapshot.account_equity))
                    if self._config.entry_margin_budget_max_equity_usdt is not None:
                        payload["entry_margin_budget_max_equity_usdt"] = self._config.entry_margin_budget_max_equity_usdt
            if runtime_snapshot.account_equity_source:
                payload["runtime_account_equity_source"] = runtime_snapshot.account_equity_source
            if runtime_position.mark_price is not None:
                payload["runtime_mark_price"] = float(runtime_position.mark_price)
            if runtime_position.leverage is not None:
                payload["runtime_leverage"] = int(runtime_position.leverage)
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
        if runtime_snapshot_valid:
            payload["protective_stop_required"] = expected_open_risk and not reconciliation.protective_stop_present
        else:
            payload["protective_stop_required"] = bool(payload.get("protective_stop_required")) or expected_open_risk
        payload["recovery_required"] = not reconciliation.in_sync
        payload["reconciliation_required"] = not reconciliation.in_sync
        return payload

    @staticmethod
    def _resolve_entry_margin_budget(handoff: dict[str, object]) -> float | None:
        for key in ("entry_margin_budget_usdt", "margin_budget_usdt"):
            value = handoff.get(key)
            try:
                if value is not None and value != "":
                    parsed = float(value)
                    if parsed > 0.0:
                        return parsed
            except (TypeError, ValueError):
                continue
        return None
