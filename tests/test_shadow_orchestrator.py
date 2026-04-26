from datetime import datetime
from pathlib import Path

from bot.config import BotConfig, RuntimeMode
from bot.engine_client import EngineCyclePayload
from bot.exchange_adapter import (
    AdapterCapabilities,
    AdapterRuntimeSnapshot,
    CommandExecutionResult,
    PositionSnapshot,
    ReconciliationResult,
)
from bot.network_guard import NetworkGuard
from bot.orchestrator import ShadowOrchestrator
from bot.state_store import StateStore


class FakeEngineClient:
    def __init__(self, payload: EngineCyclePayload) -> None:
        self._payload = payload
        self.fetch_cycle_calls = 0
        self.fetch_risk_cycle_calls = 0

    def fetch_cycle(self, **_: object) -> EngineCyclePayload:
        self.fetch_cycle_calls += 1
        return self._payload

    def fetch_risk_cycle(self, **_: object) -> EngineCyclePayload:
        self.fetch_risk_cycle_calls += 1
        return self._payload


class FakeExchangeAdapter:
    def __init__(
        self,
        *,
        snapshot: AdapterRuntimeSnapshot | None = None,
        reconciliation: ReconciliationResult | None = None,
        execution_results: list[CommandExecutionResult] | None = None,
        capabilities: AdapterCapabilities | None = None,
    ) -> None:
        self._snapshot = snapshot or AdapterRuntimeSnapshot()
        self._reconciliation = reconciliation or ReconciliationResult(
            in_sync=True,
            protective_stop_present=False,
        )
        self._execution_results = execution_results
        self._capabilities = capabilities or AdapterCapabilities(
            supports_real_execution=False,
            supports_recent_fill_sync=True,
            supports_trailing_stop_update=True,
            supports_breakeven_update=True,
        )
        self.last_runtime_mode = None

    def fetch_runtime_snapshot(self) -> AdapterRuntimeSnapshot:
        return self._snapshot

    def get_capabilities(self) -> AdapterCapabilities:
        return self._capabilities

    def assess_reconciliation(
        self,
        *,
        runtime_snapshot: AdapterRuntimeSnapshot,
        expected_position_state: str,
        expected_direction: str,
        expected_size_pct: float,
    ) -> ReconciliationResult:
        return self._reconciliation

    def plan_actions(self, **kwargs: object):
        from bot.exchange_adapter import ExchangeAdapter

        return ExchangeAdapter().plan_actions(**kwargs)

    def build_commands(self, **kwargs: object):
        from bot.exchange_adapter import ExchangeAdapter

        return ExchangeAdapter().build_commands(**kwargs)

    def execute_commands(self, *, commands, runtime_mode):
        self.last_runtime_mode = runtime_mode
        if self._execution_results is not None:
            return self._execution_results
        from bot.exchange_adapter import ExchangeAdapter

        return ExchangeAdapter().execute_commands(commands=commands, runtime_mode=runtime_mode)


def test_shadow_orchestrator_writes_state_and_audit_log(tmp_path: Path) -> None:
    payload = EngineCyclePayload(
        judgement={
            "status": "ok",
            "diagnostic": "",
            "research_bundle": {"ready": True, "bundle_status": "healthy"},
        },
        handoff={
            "generated_at": "2026-04-26T12:00:00",
            "action": "entry_long",
            "risk_filter_status": "pass",
            "runtime_vetoes": [],
            "degrade_flags": [],
            "staleness_veto": False,
            "conflict_veto": False,
            "position_state": "ARMED",
            "current_position_direction": "neutral",
            "position_size_pct": 0.0,
            "initial_stop_loss": 0.97,
            "breakeven_trigger": 1.01,
            "trailing_rule": "trail_with_trigger",
        },
    )
    config = BotConfig(
        state_store_path=tmp_path / "state.json",
        audit_log_path=tmp_path / "audit.jsonl",
        artifacts_root=tmp_path / "runtime",
    )
    fake_client = FakeEngineClient(payload)
    orchestrator = ShadowOrchestrator(
        config,
        engine_client=fake_client,
        network_guard=NetworkGuard(),
        state_store=StateStore(config.state_store_path),
        exchange_adapter=FakeExchangeAdapter(),
    )
    report = orchestrator.run_cycle(generated_at=datetime(2026, 4, 26, 12, 0, 0))
    assert report.runtime_mode == "shadow"
    assert report.adapter_supports_real_execution is False
    assert report.effective_action == "entry_long"
    assert report.plan_reason == "entry_allowed"
    assert report.adapter_action_types == ["entry_order", "maintain_protective_stop"]
    assert report.command_types == ["entry_order", "maintain_protective_stop"]
    assert report.command_result_statuses == ["simulated", "simulated"]
    assert report.reconciliation_in_sync is True
    assert report.blocked is False
    assert fake_client.fetch_cycle_calls == 1
    assert config.state_store_path.exists()
    assert config.audit_log_path.exists()


def test_shadow_orchestrator_turns_entry_into_wait_when_guard_disallows_it(tmp_path: Path) -> None:
    payload = EngineCyclePayload(
        judgement={
            "status": "ok",
            "diagnostic": "request_diagnostic=transport | category=transport | boundary=request",
            "research_bundle": {"ready": True, "bundle_status": "healthy"},
        },
        handoff={
            "generated_at": "2026-04-26T12:00:00",
            "action": "entry_short",
            "risk_filter_status": "pass",
            "runtime_vetoes": [],
            "degrade_flags": [],
            "staleness_veto": False,
            "conflict_veto": False,
            "position_state": "ARMED",
            "current_position_direction": "neutral",
            "position_size_pct": 0.0,
        },
    )
    config = BotConfig(
        state_store_path=tmp_path / "state.json",
        audit_log_path=tmp_path / "audit.jsonl",
        artifacts_root=tmp_path / "runtime",
    )
    orchestrator = ShadowOrchestrator(
        config,
        engine_client=FakeEngineClient(payload),
        network_guard=NetworkGuard(),
        state_store=StateStore(config.state_store_path),
        exchange_adapter=FakeExchangeAdapter(),
    )
    report = orchestrator.run_cycle(generated_at=datetime(2026, 4, 26, 12, 0, 0))
    assert report.effective_action == "wait"
    assert report.plan_reason == "entry_disallowed_by_guard"
    assert report.adapter_action_types == ["reconcile_position_and_orders"]
    assert report.command_types == ["reconcile_position_and_orders"]
    assert report.degraded is True


def test_shadow_orchestrator_maps_observe_only_to_wait(tmp_path: Path) -> None:
    payload = EngineCyclePayload(
        judgement={
            "status": "ok",
            "diagnostic": "",
            "research_bundle": {"ready": True, "bundle_status": "healthy"},
        },
        handoff={
            "generated_at": "2026-04-26T12:00:00",
            "action": "observe_only",
            "risk_filter_status": "pass",
            "runtime_vetoes": [],
            "degrade_flags": [],
            "staleness_veto": False,
            "conflict_veto": False,
            "position_state": "ARMED",
            "current_position_direction": "neutral",
            "position_size_pct": 0.0,
        },
    )
    config = BotConfig(
        state_store_path=tmp_path / "state.json",
        audit_log_path=tmp_path / "audit.jsonl",
        artifacts_root=tmp_path / "runtime",
    )
    orchestrator = ShadowOrchestrator(
        config,
        engine_client=FakeEngineClient(payload),
        network_guard=NetworkGuard(),
        state_store=StateStore(config.state_store_path),
        exchange_adapter=FakeExchangeAdapter(),
    )
    report = orchestrator.run_cycle(generated_at=datetime(2026, 4, 26, 12, 0, 0))
    assert report.effective_action == "wait"
    assert report.plan_reason == "non_executable_observation_action"


def test_shadow_orchestrator_reports_reconciliation_gap_from_adapter(tmp_path: Path) -> None:
    payload = EngineCyclePayload(
        judgement={
            "status": "ok",
            "diagnostic": "",
            "research_bundle": {"ready": True, "bundle_status": "healthy"},
        },
        handoff={
            "generated_at": "2026-04-26T12:00:00",
            "action": "wait",
            "risk_filter_status": "pass",
            "runtime_vetoes": [],
            "degrade_flags": [],
            "staleness_veto": False,
            "conflict_veto": False,
            "position_state": "ENTERED",
            "current_position_direction": "long",
            "position_size_pct": 0.3,
        },
    )
    config = BotConfig(
        state_store_path=tmp_path / "state.json",
        audit_log_path=tmp_path / "audit.jsonl",
        artifacts_root=tmp_path / "runtime",
    )
    state_store = StateStore(config.state_store_path)
    state = state_store.load()
    state.observed_position_state = "ENTERED"
    state.observed_position_direction = "long"
    state.observed_position_size_pct = 0.3
    state_store.save(state)
    orchestrator = ShadowOrchestrator(
        config,
        engine_client=FakeEngineClient(payload),
        network_guard=NetworkGuard(),
        state_store=state_store,
        exchange_adapter=FakeExchangeAdapter(
            snapshot=AdapterRuntimeSnapshot(
                position=PositionSnapshot(position_state="ENTERED", direction="long", size_pct=0.2),
                protective_stop_present=False,
            ),
            reconciliation=ReconciliationResult(
                in_sync=False,
                protective_stop_present=False,
                needs_position_sync=True,
                needs_order_sync=True,
                reason_codes=["position_size_mismatch", "protective_stop_missing"],
            ),
        ),
    )
    report = orchestrator.run_cycle(generated_at=datetime(2026, 4, 26, 12, 0, 0))
    assert report.reconciliation_in_sync is False
    assert "position_size_mismatch" in report.reconciliation_reason_codes


def test_risk_assist_cycle_skips_when_state_is_not_eligible(tmp_path: Path) -> None:
    payload = EngineCyclePayload(judgement={"status": "ok", "research_bundle": {"ready": True, "bundle_status": "healthy"}}, handoff=None)
    config = BotConfig(
        state_store_path=tmp_path / "state.json",
        audit_log_path=tmp_path / "audit.jsonl",
        artifacts_root=tmp_path / "runtime",
    )
    fake_client = FakeEngineClient(payload)
    orchestrator = ShadowOrchestrator(
        config,
        engine_client=fake_client,
        network_guard=NetworkGuard(),
        state_store=StateStore(config.state_store_path),
        exchange_adapter=FakeExchangeAdapter(),
    )
    report = orchestrator.run_risk_assist_cycle(generated_at=datetime(2026, 4, 26, 12, 5, 0))
    assert report.eligible is False
    assert fake_client.fetch_risk_cycle_calls == 0


def test_risk_assist_cycle_runs_when_recovery_is_required(tmp_path: Path) -> None:
    payload = EngineCyclePayload(
        judgement={
            "status": "ok",
            "diagnostic": "",
            "research_bundle": {"ready": True, "bundle_status": "healthy"},
        },
        handoff={
            "generated_at": "2026-04-26T12:05:00",
            "action": "reduce",
            "risk_filter_status": "pass",
            "runtime_vetoes": [],
            "degrade_flags": [],
            "staleness_veto": False,
            "conflict_veto": False,
            "position_state": "ENTERED",
            "current_position_direction": "long",
            "position_size_pct": 0.3,
            "reduce_conditions": ["crowding_warning"],
            "initial_stop_loss": 0.97,
        },
    )
    config = BotConfig(
        state_store_path=tmp_path / "state.json",
        audit_log_path=tmp_path / "audit.jsonl",
        artifacts_root=tmp_path / "runtime",
    )
    state_store = StateStore(config.state_store_path)
    state = state_store.load()
    state.recovery_required = True
    state.reconciliation_required = True
    state.protective_stop_required = True
    state.observed_position_state = "ENTERED"
    state.observed_position_direction = "long"
    state.observed_position_size_pct = 0.3
    state_store.save(state)
    fake_client = FakeEngineClient(payload)
    orchestrator = ShadowOrchestrator(
        config,
        engine_client=fake_client,
        network_guard=NetworkGuard(),
        state_store=state_store,
        exchange_adapter=FakeExchangeAdapter(
            snapshot=AdapterRuntimeSnapshot(
                position=PositionSnapshot(position_state="ENTERED", direction="long", size_pct=0.3),
                protective_stop_present=False,
            ),
            reconciliation=ReconciliationResult(
                in_sync=False,
                protective_stop_present=False,
                needs_position_sync=False,
                needs_order_sync=True,
                reason_codes=["protective_stop_missing"],
            ),
        ),
    )
    report = orchestrator.run_risk_assist_cycle(generated_at=datetime(2026, 4, 26, 12, 5, 0))
    assert report.runtime_mode == "shadow"
    assert report.adapter_supports_real_execution is False
    assert report.eligible is True
    assert report.effective_action == "reduce"
    assert report.reconciliation_in_sync is False
    assert "reduce_order" in report.adapter_action_types
    assert "reconcile_position_and_orders" in report.adapter_action_types
    assert "reduce_order" in report.command_types
    assert "reconcile_position_and_orders" in report.command_types
    assert all(status == "simulated" for status in report.command_result_statuses)
    assert fake_client.fetch_risk_cycle_calls == 1


def test_shadow_orchestrator_marks_rejected_execution_for_recovery(tmp_path: Path) -> None:
    payload = EngineCyclePayload(
        judgement={
            "status": "ok",
            "diagnostic": "",
            "research_bundle": {"ready": True, "bundle_status": "healthy"},
        },
        handoff={
            "generated_at": "2026-04-26T12:00:00",
            "action": "entry_long",
            "risk_filter_status": "pass",
            "runtime_vetoes": [],
            "degrade_flags": [],
            "staleness_veto": False,
            "conflict_veto": False,
            "position_state": "ARMED",
            "current_position_direction": "neutral",
            "position_size_pct": 0.0,
            "initial_stop_loss": 0.97,
        },
    )
    config = BotConfig(
        state_store_path=tmp_path / "state.json",
        audit_log_path=tmp_path / "audit.jsonl",
        artifacts_root=tmp_path / "runtime",
    )
    state_store = StateStore(config.state_store_path)
    orchestrator = ShadowOrchestrator(
        config,
        engine_client=FakeEngineClient(payload),
        network_guard=NetworkGuard(),
        state_store=state_store,
        exchange_adapter=FakeExchangeAdapter(
            execution_results=[
                CommandExecutionResult(
                    target="entry_order",
                    status="rejected",
                    accepted=False,
                    simulated=True,
                    reason="entry_allowed",
                )
            ]
        ),
    )
    report = orchestrator.run_cycle(generated_at=datetime(2026, 4, 26, 12, 0, 0))
    updated_state = state_store.load()
    assert report.command_result_statuses == ["rejected"]
    assert updated_state.execution_state.value == "entry_pending"
    assert updated_state.pending_action == "entry_long"
    assert updated_state.recovery_required is True
    assert updated_state.reconciliation_required is False




def test_shadow_orchestrator_passes_simulated_real_mode_to_adapter(tmp_path: Path) -> None:
    payload = EngineCyclePayload(
        judgement={
            "status": "ok",
            "diagnostic": "",
            "research_bundle": {"ready": True, "bundle_status": "healthy"},
        },
        handoff={
            "generated_at": "2026-04-26T12:30:00",
            "action": "entry_long",
            "risk_filter_status": "pass",
            "runtime_vetoes": [],
            "degrade_flags": [],
            "staleness_veto": False,
            "conflict_veto": False,
            "position_state": "ARMED",
            "current_position_direction": "neutral",
            "position_size_pct": 0.0,
            "initial_stop_loss": 0.97,
        },
    )
    config = BotConfig(
        runtime_mode=RuntimeMode.SIMULATED_REAL,
        state_store_path=tmp_path / "state.json",
        audit_log_path=tmp_path / "audit.jsonl",
        artifacts_root=tmp_path / "runtime",
    )
    adapter = FakeExchangeAdapter(
        capabilities=AdapterCapabilities(
            supports_real_execution=True,
            supports_recent_fill_sync=True,
            supports_trailing_stop_update=True,
            supports_breakeven_update=True,
        )
    )
    orchestrator = ShadowOrchestrator(
        config,
        engine_client=FakeEngineClient(payload),
        network_guard=NetworkGuard(),
        state_store=StateStore(config.state_store_path),
        exchange_adapter=adapter,
    )
    report = orchestrator.run_cycle(generated_at=datetime(2026, 4, 26, 12, 30, 0))
    assert adapter.last_runtime_mode is RuntimeMode.SIMULATED_REAL
    assert report.runtime_mode == "simulated-real"
    assert report.adapter_supports_real_execution is True
    assert all(status == "simulated" for status in report.command_result_statuses)


def test_shadow_orchestrator_marks_stop_failure_for_reconciliation(tmp_path: Path) -> None:
    payload = EngineCyclePayload(
        judgement={
            "status": "ok",
            "diagnostic": "",
            "research_bundle": {"ready": True, "bundle_status": "healthy"},
        },
        handoff={
            "generated_at": "2026-04-26T12:00:00",
            "action": "entry_long",
            "risk_filter_status": "pass",
            "runtime_vetoes": [],
            "degrade_flags": [],
            "staleness_veto": False,
            "conflict_veto": False,
            "position_state": "ARMED",
            "current_position_direction": "neutral",
            "position_size_pct": 0.0,
            "initial_stop_loss": 0.97,
            "breakeven_trigger": 1.01,
            "trailing_rule": "trail_with_trigger",
        },
    )
    config = BotConfig(
        state_store_path=tmp_path / "state.json",
        audit_log_path=tmp_path / "audit.jsonl",
        artifacts_root=tmp_path / "runtime",
    )
    state_store = StateStore(config.state_store_path)
    orchestrator = ShadowOrchestrator(
        config,
        engine_client=FakeEngineClient(payload),
        network_guard=NetworkGuard(),
        state_store=state_store,
        exchange_adapter=FakeExchangeAdapter(
            execution_results=[
                CommandExecutionResult(
                    target="entry_order",
                    status="simulated",
                    accepted=True,
                    simulated=True,
                    reason="entry_allowed",
                ),
                CommandExecutionResult(
                    target="maintain_protective_stop",
                    status="failed",
                    accepted=False,
                    simulated=True,
                    reason="protective_stop_required",
                ),
            ]
        ),
    )
    report = orchestrator.run_cycle(generated_at=datetime(2026, 4, 26, 12, 0, 0))
    updated_state = state_store.load()
    assert report.command_result_statuses == ["simulated", "failed"]
    assert updated_state.execution_state.value == "reconciling"
    assert updated_state.pending_action == ""
    assert updated_state.recovery_required is True
    assert updated_state.reconciliation_required is True
    assert updated_state.protective_stop_required is True
