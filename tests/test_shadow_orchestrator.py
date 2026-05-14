from datetime import datetime
from pathlib import Path
import json

import pytest

from bot.audit_logger import AuditLogger
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


def test_audit_logger_redacts_signed_request_secrets(tmp_path: Path) -> None:
    logger = AuditLogger(tmp_path / "audit.jsonl")

    logger.append(
        event_type="risk_assist_cycle",
        generated_at=datetime(2026, 4, 28, 19, 0, 0),
        payload={
            "execution_results": [
                {
                    "details": {
                        "signed_request": {
                            "method": "GET",
                            "url": "https://fapi.binance.com/fapi/v2/positionRisk",
                            "headers": {
                                "Authorization": "bearer-token",
                                "OK-ACCESS-KEY": "okx-key",
                                "OK-ACCESS-PASSPHRASE": "okx-passphrase",
                                "OK-ACCESS-SIGN": "okx-signature",
                                "OK-ACCESS-TIMESTAMP": "2026-05-13T14:21:25.234Z",
                                "User-Agent": "eth-trading-bot/1.0",
                                "X-MBX-APIKEY": "secret-key",
                            },
                            "params": {
                                "symbol": "ETHUSDT",
                                "timestamp": 1777373493070,
                                "recvWindow": 60000,
                                "signature": "top-secret-signature",
                            },
                        }
                    }
                }
            ]
        },
    )

    audit_event = json.loads((tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    signed_request = audit_event["payload"]["execution_results"][0]["details"]["signed_request"]

    assert signed_request["headers"] == {
        "Authorization": "<redacted>",
        "OK-ACCESS-KEY": "<redacted>",
        "OK-ACCESS-PASSPHRASE": "<redacted>",
        "OK-ACCESS-SIGN": "<redacted>",
        "OK-ACCESS-TIMESTAMP": "<redacted>",
        "User-Agent": "eth-trading-bot/1.0",
        "X-MBX-APIKEY": "<redacted>",
    }
    assert signed_request["params"]["symbol"] == "ETHUSDT"
    assert signed_request["params"]["timestamp"] == "<redacted>"
    assert signed_request["params"]["recvWindow"] == "<redacted>"
    assert signed_request["params"]["signature"] == "<redacted>"



class FakeEngineClient:
    def __init__(self, payload: EngineCyclePayload) -> None:
        self._payload = payload
        self.fetch_cycle_calls = 0

    def fetch_cycle(self, **_: object) -> EngineCyclePayload:
        self.fetch_cycle_calls += 1
        return self._payload


class FailingEngineClient:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def fetch_cycle(self, **_: object) -> EngineCyclePayload:
        raise self._exc


class FakeExchangeAdapter:
    def __init__(
        self,
        *,
        snapshot: AdapterRuntimeSnapshot | None = None,
        snapshot_sequence: list[AdapterRuntimeSnapshot] | None = None,
        reconciliation: ReconciliationResult | None = None,
        execution_results: list[CommandExecutionResult] | None = None,
        capabilities: AdapterCapabilities | None = None,
    ) -> None:
        self._snapshot = snapshot or AdapterRuntimeSnapshot()
        self._snapshot_sequence = list(snapshot_sequence or [])
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
        self.last_commands = None

    def fetch_runtime_snapshot(self) -> AdapterRuntimeSnapshot:
        if self._snapshot_sequence:
            return self._snapshot_sequence.pop(0)
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
        self.last_commands = list(commands)
        if self._execution_results is not None:
            return self._execution_results
        from bot.exchange_adapter import ExchangeAdapter

        return ExchangeAdapter().execute_commands(commands=commands, runtime_mode=runtime_mode)


def test_shadow_orchestrator_requires_explicit_engine_client(tmp_path: Path) -> None:
    config = BotConfig(
        state_store_path=tmp_path / "state.json",
        audit_log_path=tmp_path / "audit.jsonl",
        artifacts_root=tmp_path / "runtime",
    )
    with pytest.raises(RuntimeError, match="必须显式注入"):
        ShadowOrchestrator(
            config,
            network_guard=NetworkGuard(),
            state_store=StateStore(config.state_store_path),
            exchange_adapter=FakeExchangeAdapter(),
        )



def test_shadow_orchestrator_requires_explicit_adapter_for_non_shadow_runtime(tmp_path: Path) -> None:
    config = BotConfig(
        runtime_mode=RuntimeMode.SIMULATED_REAL,
        state_store_path=tmp_path / "state.json",
        audit_log_path=tmp_path / "audit.jsonl",
        artifacts_root=tmp_path / "runtime",
    )
    with pytest.raises(RuntimeError, match="显式注入"):
        ShadowOrchestrator(
            config,
            engine_client=FakeEngineClient(EngineCyclePayload(judgement={"status": "ok"}, handoff=None)),
            network_guard=NetworkGuard(),
            state_store=StateStore(config.state_store_path),
        )



def test_shadow_orchestrator_surfaces_breakeven_and_trailing_commands_in_simulated_real(tmp_path: Path) -> None:
    payload = EngineCyclePayload(
        judgement={
            "status": "ok",
            "diagnostic": "",
            "research_bundle": {"ready": True, "bundle_status": "healthy"},
        },
        handoff={
            "generated_at": "2026-04-26T13:00:00",
            "action": "wait",
            "risk_filter_status": "pass",
            "runtime_vetoes": [],
            "degrade_flags": [],
            "staleness_veto": False,
            "conflict_veto": False,
            "position_state": "ENTERED",
            "current_position_direction": "long",
            "position_size_pct": 0.3,
            "direction": "long",
            "initial_stop_loss": 0.97,
            "breakeven_trigger": 1.01,
            "trailing_rule": "trail_with_trigger",
            "trailing_activation_ratio": 1.01,
            "trailing_callback_rate_pct": 0.5,
            "tp_ladder": [1.01, 1.02],
        },
    )
    config = BotConfig(
        runtime_mode=RuntimeMode.SIMULATED_REAL,
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
            capabilities=AdapterCapabilities(
                supports_real_execution=True,
                supports_recent_fill_sync=True,
                supports_trailing_stop_update=True,
                supports_breakeven_update=True,
            ),
            snapshot=AdapterRuntimeSnapshot(
                position=PositionSnapshot(position_state="ENTERED", direction="long", size_pct=0.3),
                protective_stop_present=True,
            ),
        ),
    )

    report = orchestrator.run_cycle(generated_at=datetime(2026, 4, 26, 13, 0, 0))

    assert report.runtime_mode == "simulated-real"
    assert report.command_reasons == ["protective_stop_required", "breakeven_ready", "trailing_ready"]
    assert report.command_summary == [
        {"target": "maintain_protective_stop", "reason": "protective_stop_required", "operation": "upsert"},
        {"target": "advance_breakeven_stop", "reason": "breakeven_ready", "operation": "tighten"},
        {"target": "advance_trailing_stop", "reason": "trailing_ready", "operation": "tighten"},
    ]
    assert report.command_types == ["maintain_protective_stop", "advance_breakeven_stop", "advance_trailing_stop"]
    audit_event = json.loads(config.audit_log_path.read_text(encoding="utf-8").splitlines()[-1])
    assert audit_event["event_type"] == "shadow_cycle"
    assert audit_event["payload"]["command_reasons"] == ["protective_stop_required", "breakeven_ready", "trailing_ready"]
    assert audit_event["payload"]["command_summary"] == [
        {"target": "maintain_protective_stop", "reason": "protective_stop_required", "operation": "upsert"},
        {"target": "advance_breakeven_stop", "reason": "breakeven_ready", "operation": "tighten"},
        {"target": "advance_trailing_stop", "reason": "trailing_ready", "operation": "tighten"},
    ]



def test_risk_assist_cycle_surfaces_breakeven_and_trailing_commands_in_simulated_real(tmp_path: Path) -> None:
    payload = EngineCyclePayload(
        judgement={
            "status": "ok",
            "diagnostic": "",
            "research_bundle": {"ready": True, "bundle_status": "healthy"},
        },
        handoff={
            "generated_at": "2026-04-26T13:05:00",
            "action": "wait",
            "risk_filter_status": "pass",
            "runtime_vetoes": [],
            "degrade_flags": [],
            "staleness_veto": False,
            "conflict_veto": False,
            "position_state": "ENTERED",
            "current_position_direction": "long",
            "position_size_pct": 0.3,
            "direction": "long",
            "initial_stop_loss": 0.97,
            "breakeven_trigger": 1.01,
            "trailing_rule": "trail_with_trigger",
            "trailing_activation_ratio": 1.01,
            "trailing_callback_rate_pct": 0.5,
            "tp_ladder": [1.01, 1.02],
        },
    )
    config = BotConfig(
        runtime_mode=RuntimeMode.SIMULATED_REAL,
        state_store_path=tmp_path / "state.json",
        audit_log_path=tmp_path / "audit.jsonl",
        artifacts_root=tmp_path / "runtime",
    )
    state_store = StateStore(config.state_store_path)
    state = state_store.load()
    state.observed_position_state = "ENTERED"
    state.observed_position_direction = "long"
    state.observed_position_size_pct = 0.3
    state.recovery_required = True
    state.reconciliation_required = True
    state_store.save(state)
    orchestrator = ShadowOrchestrator(
        config,
        engine_client=FakeEngineClient(payload),
        network_guard=NetworkGuard(),
        state_store=state_store,
        exchange_adapter=FakeExchangeAdapter(
            capabilities=AdapterCapabilities(
                supports_real_execution=True,
                supports_recent_fill_sync=True,
                supports_trailing_stop_update=True,
                supports_breakeven_update=True,
            ),
            snapshot=AdapterRuntimeSnapshot(
                position=PositionSnapshot(position_state="ENTERED", direction="long", size_pct=0.3),
                protective_stop_present=True,
            ),
            reconciliation=ReconciliationResult(
                in_sync=False,
                protective_stop_present=True,
                needs_position_sync=True,
                needs_order_sync=False,
                reason_codes=["position_size_mismatch"],
            ),
        ),
    )

    report = orchestrator.run_risk_assist_cycle(generated_at=datetime(2026, 4, 26, 13, 5, 0))

    assert report.runtime_mode == "simulated-real"
    assert report.command_reasons == ["breakeven_ready", "trailing_ready", "reconciliation_required"]
    assert report.command_summary == [
        {"target": "advance_breakeven_stop", "reason": "breakeven_ready", "operation": "tighten"},
        {"target": "advance_trailing_stop", "reason": "trailing_ready", "operation": "tighten"},
        {"target": "reconcile_position_and_orders", "reason": "reconciliation_required", "operation": "query"},
    ]
    assert report.command_types == [
        "advance_breakeven_stop",
        "advance_trailing_stop",
        "reconcile_position_and_orders",
    ]
    audit_event = json.loads(config.audit_log_path.read_text(encoding="utf-8").splitlines()[-1])
    assert audit_event["event_type"] == "risk_assist_cycle"
    assert audit_event["payload"]["command_reasons"] == [
        "breakeven_ready",
        "trailing_ready",
        "reconciliation_required",
    ]
    assert audit_event["payload"]["command_summary"] == [
        {"target": "advance_breakeven_stop", "reason": "breakeven_ready", "operation": "tighten"},
        {"target": "advance_trailing_stop", "reason": "trailing_ready", "operation": "tighten"},
        {"target": "reconcile_position_and_orders", "reason": "reconciliation_required", "operation": "query"},
    ]



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
            "execution_allowed": True,
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
    assert report.plan_reason == "quant_action_passthrough"
    assert report.action_summary == {
        "requested_action": "entry_long",
        "effective_action": "entry_long",
        "plan_reason": "quant_action_passthrough",
        "blocked": False,
        "degraded": False,
        "guard_reason_codes": [],
        "reconciliation_in_sync": True,
        "reconciliation_reason_codes": [],
    }
    assert report.execution_overview == {
        "requested_action": "entry_long",
        "effective_action": "entry_long",
        "primary_target": "entry_order",
        "primary_reason": "effective_action:entry_long",
        "primary_status": "simulated",
        "primary_accepted": True,
        "has_primary_failure": False,
        "has_auxiliary_failure": False,
        "auxiliary_targets": ["maintain_protective_stop"],
    }
    assert report.command_reasons == ["effective_action:entry_long", "protective_stop_required"]
    assert report.command_summary == [
        {"target": "entry_order", "reason": "effective_action:entry_long", "operation": "place"},
        {"target": "maintain_protective_stop", "reason": "protective_stop_required", "operation": "upsert"},
    ]
    assert report.command_result_summary == [
        {"target": "entry_order", "reason": "effective_action:entry_long", "status": "simulated", "accepted": True, "simulated": True, "idempotency_key": "entry_order:2026-04-26T12:00:00:entry_long:neutral", "client_order_id": "", "exchange_order_id": "", "error_kind": ""},
        {"target": "maintain_protective_stop", "reason": "protective_stop_required", "status": "simulated", "accepted": True, "simulated": True, "idempotency_key": "maintain_protective_stop:2026-04-26T12:00:00:entry_long:neutral", "client_order_id": "", "exchange_order_id": "", "error_kind": ""},
    ]
    assert report.adapter_action_types == ["entry_order", "maintain_protective_stop"]
    assert report.command_types == ["entry_order", "maintain_protective_stop"]
    assert report.command_result_statuses == ["simulated", "simulated"]
    assert report.reconciliation_in_sync is True
    assert report.blocked is False
    assert fake_client.fetch_cycle_calls == 1
    assert config.state_store_path.exists()
    assert config.audit_log_path.exists()
    audit_event = json.loads(config.audit_log_path.read_text(encoding="utf-8").splitlines()[-1])
    assert audit_event["event_type"] == "shadow_cycle"
    assert audit_event["payload"]["execution_result_summary"] == {
        "has_failure": False,
        "primary_failed": False,
        "primary_succeeded": True,
        "auxiliary_failed": False,
        "protective_stop_failed": False,
        "capability_blocked": False,
    }
    assert audit_event["payload"]["action_summary"] == {
        "requested_action": "entry_long",
        "effective_action": "entry_long",
        "plan_reason": "quant_action_passthrough",
        "blocked": False,
        "degraded": False,
        "guard_reason_codes": [],
        "reconciliation_in_sync": True,
        "reconciliation_reason_codes": [],
    }
    assert audit_event["payload"]["execution_overview"] == {
        "requested_action": "entry_long",
        "effective_action": "entry_long",
        "primary_target": "entry_order",
        "primary_reason": "effective_action:entry_long",
        "primary_status": "simulated",
        "primary_accepted": True,
        "has_primary_failure": False,
        "has_auxiliary_failure": False,
        "auxiliary_targets": ["maintain_protective_stop"],
    }
    assert audit_event["payload"]["command_reasons"] == ["effective_action:entry_long", "protective_stop_required"]
    assert audit_event["payload"]["command_summary"] == [
        {"target": "entry_order", "reason": "effective_action:entry_long", "operation": "place"},
        {"target": "maintain_protective_stop", "reason": "protective_stop_required", "operation": "upsert"},
    ]
    assert audit_event["payload"]["command_result_summary"] == [
        {"target": "entry_order", "reason": "effective_action:entry_long", "status": "simulated", "accepted": True, "simulated": True, "idempotency_key": "entry_order:2026-04-26T12:00:00:entry_long:neutral", "client_order_id": "", "exchange_order_id": "", "error_kind": ""},
        {"target": "maintain_protective_stop", "reason": "protective_stop_required", "status": "simulated", "accepted": True, "simulated": True, "idempotency_key": "maintain_protective_stop:2026-04-26T12:00:00:entry_long:neutral", "client_order_id": "", "exchange_order_id": "", "error_kind": ""},
    ]
    assert audit_event["payload"]["automation_state"] == "disabled"
    assert audit_event["payload"]["runtime_snapshot_before"]["position"]["position_state"] == "FLAT"
    assert audit_event["payload"]["runtime_snapshot_after"] == audit_event["payload"]["runtime_snapshot"]
    assert audit_event["payload"]["reason_codes"] == []
    assert audit_event["payload"]["recent_fill_summary"] == {}


def test_shadow_orchestrator_audits_run_cycle_exception(tmp_path: Path) -> None:
    config = BotConfig(
        state_store_path=tmp_path / "state.json",
        audit_log_path=tmp_path / "audit.jsonl",
        artifacts_root=tmp_path / "runtime",
    )
    orchestrator = ShadowOrchestrator(
        config,
        engine_client=FailingEngineClient(RuntimeError("engine unavailable")),
        network_guard=NetworkGuard(),
        state_store=StateStore(config.state_store_path),
        exchange_adapter=FakeExchangeAdapter(),
    )

    with pytest.raises(RuntimeError, match="engine unavailable"):
        orchestrator.run_cycle(generated_at=datetime(2026, 4, 26, 12, 0, 0))

    audit_event = json.loads(config.audit_log_path.read_text(encoding="utf-8").splitlines()[-1])
    assert audit_event["event_type"] == "shadow_cycle_exception"
    assert audit_event["payload"]["error_type"] == "RuntimeError"
    assert audit_event["payload"]["error_message"] == "engine unavailable"


def test_shadow_orchestrator_audits_risk_assist_cycle_exception(tmp_path: Path) -> None:
    config = BotConfig(
        runtime_mode=RuntimeMode.SIMULATED_REAL,
        state_store_path=tmp_path / "state.json",
        audit_log_path=tmp_path / "audit.jsonl",
        artifacts_root=tmp_path / "runtime",
    )
    state_store = StateStore(config.state_store_path)
    state = state_store.load()
    state.observed_position_state = "ENTERED"
    state.observed_position_size_pct = 0.2
    state_store.save(state)
    orchestrator = ShadowOrchestrator(
        config,
        engine_client=FailingEngineClient(RuntimeError("risk engine unavailable")),
        network_guard=NetworkGuard(),
        state_store=state_store,
        exchange_adapter=FakeExchangeAdapter(
            capabilities=AdapterCapabilities(supports_real_execution=True),
            snapshot=AdapterRuntimeSnapshot(
                position=PositionSnapshot(position_state="ENTERED", direction="long", size_pct=0.2),
                protective_stop_present=True,
            ),
        ),
    )

    with pytest.raises(RuntimeError, match="risk engine unavailable"):
        orchestrator.run_risk_assist_cycle(generated_at=datetime(2026, 4, 26, 12, 5, 0))

    audit_event = json.loads(config.audit_log_path.read_text(encoding="utf-8").splitlines()[-1])
    assert audit_event["event_type"] == "risk_assist_cycle_exception"
    assert audit_event["payload"]["error_type"] == "RuntimeError"
    assert audit_event["payload"]["error_message"] == "risk engine unavailable"


def test_shadow_orchestrator_reports_post_execution_runtime_snapshot_in_real_mode(tmp_path: Path) -> None:
    payload = EngineCyclePayload(
        judgement={
            "status": "ok",
            "diagnostic": "",
            "research_bundle": {"ready": True, "bundle_status": "healthy"},
        },
        handoff={
            "generated_at": "2026-04-26T14:00:00",
            "action": "entry_long",
            "risk_filter_status": "pass",
            "runtime_vetoes": [],
            "degrade_flags": [],
            "staleness_veto": False,
            "conflict_veto": False,
            "position_state": "ARMED",
            "current_position_direction": "neutral",
            "position_size_pct": 0.0,
            "execution_allowed": True,
            "direction": "long",
            "initial_stop_loss": 0.97,
        },
    )
    config = BotConfig(
        runtime_mode=RuntimeMode.REAL,
        manual_entry_confirmation_required=False,
        state_store_path=tmp_path / "state.json",
        audit_log_path=tmp_path / "audit.jsonl",
        artifacts_root=tmp_path / "runtime",
    )
    orchestrator = ShadowOrchestrator(
        config,
        engine_client=FakeEngineClient(payload),
        network_guard=NetworkGuard(),
        state_store=StateStore(config.state_store_path),
        exchange_adapter=FakeExchangeAdapter(
            capabilities=AdapterCapabilities(
                supports_real_execution=True,
                supports_recent_fill_sync=True,
                supports_trailing_stop_update=True,
                supports_breakeven_update=True,
            ),
            snapshot_sequence=[
                AdapterRuntimeSnapshot(
                    position=PositionSnapshot(position_state="FLAT", direction="neutral", size_pct=0.0),
                    protective_stop_present=False,
                ),
                AdapterRuntimeSnapshot(
                    position=PositionSnapshot(position_state="ENTERED", direction="long", size_pct=0.3),
                    protective_stop_present=True,
                ),
            ],
            execution_results=[
                CommandExecutionResult(
                    target="entry_order",
                    status="accepted",
                    accepted=True,
                    simulated=False,
                    reason="effective_action:entry_long",
                )
            ],
        ),
    )

    report = orchestrator.run_cycle(generated_at=datetime(2026, 4, 26, 14, 0, 0))

    assert report.runtime_mode == "real"
    assert report.runtime_overview["runtime_position_state"] == "ENTERED"
    assert report.runtime_overview["runtime_direction"] == "long"
    assert report.runtime_overview["runtime_size_pct"] == 0.3
    assert report.runtime_overview["runtime_protective_stop_present"] is True
    assert report.runtime_overview["observed_position_state"] == "ENTERED"
    assert report.runtime_overview["observed_direction"] == "long"
    assert report.runtime_overview["observed_size_pct"] == 0.3
    audit_event = json.loads(config.audit_log_path.read_text(encoding="utf-8").splitlines()[-1])
    assert audit_event["payload"]["runtime_snapshot"]["position"]["position_state"] == "ENTERED"
    assert audit_event["payload"]["runtime_snapshot"]["position"]["direction"] == "long"
    assert audit_event["payload"]["runtime_snapshot"]["position"]["size_pct"] == 0.3
    assert audit_event["payload"]["runtime_snapshot_before"]["position"]["position_state"] == "FLAT"
    assert audit_event["payload"]["runtime_snapshot_after"]["position"]["position_state"] == "ENTERED"



def test_shadow_orchestrator_blocks_real_entry_without_manual_confirmation(tmp_path: Path) -> None:
    payload = EngineCyclePayload(
        judgement={
            "status": "ok",
            "diagnostic": "",
            "research_bundle": {"ready": True, "bundle_status": "healthy"},
        },
        handoff={
            "generated_at": "2026-04-26T14:00:00",
            "action": "entry_long",
            "risk_filter_status": "pass",
            "runtime_vetoes": [],
            "degrade_flags": [],
            "staleness_veto": False,
            "conflict_veto": False,
            "position_state": "ARMED",
            "current_position_direction": "neutral",
            "position_size_pct": 0.0,
            "execution_allowed": True,
            "direction": "long",
            "initial_stop_loss": 0.97,
        },
    )
    config = BotConfig(
        runtime_mode=RuntimeMode.REAL,
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
        ),
        snapshot=AdapterRuntimeSnapshot(
            position=PositionSnapshot(position_state="FLAT", direction="neutral", size_pct=0.0),
            protective_stop_present=False,
        ),
    )
    orchestrator = ShadowOrchestrator(
        config,
        engine_client=FakeEngineClient(payload),
        network_guard=NetworkGuard(),
        state_store=StateStore(config.state_store_path),
        exchange_adapter=adapter,
    )

    report = orchestrator.run_cycle(generated_at=datetime(2026, 4, 26, 14, 0, 0))

    assert adapter.last_commands == []
    assert report.command_types == []
    assert report.command_result_statuses == ["blocked", "blocked"]
    assert report.execution_overview["primary_target"] == "entry_order"
    assert report.execution_overview["primary_status"] == "blocked"
    assert report.execution_overview["primary_accepted"] is False
    audit_event = json.loads(config.audit_log_path.read_text(encoding="utf-8").splitlines()[-1])
    results = audit_event["payload"]["execution_results"]
    assert [result["target"] for result in results] == ["entry_order", "maintain_protective_stop"]
    assert {result["status"] for result in results} == {"blocked"}
    assert results[0]["details"]["confirmation_required"] is True
    assert results[0]["details"]["expected_confirmation_token"].startswith("ENTRY-")
    assert results[0]["idempotency_key"] == "entry_order:2026-04-26T14:00:00:entry_long:long"
    assert results[0]["error_kind"] == "manual_entry_confirmation_required"
    assert audit_event["payload"]["command_result_summary"][0]["idempotency_key"] == "entry_order:2026-04-26T14:00:00:entry_long:long"
    assert audit_event["payload"]["command_result_summary"][0]["error_kind"] == "manual_entry_confirmation_required"


def test_shadow_orchestrator_allows_real_entry_with_matching_manual_confirmation(tmp_path: Path) -> None:
    payload = EngineCyclePayload(
        judgement={
            "status": "ok",
            "diagnostic": "",
            "research_bundle": {"ready": True, "bundle_status": "healthy"},
        },
        handoff={
            "generated_at": "2026-04-26T14:00:00",
            "action": "entry_long",
            "risk_filter_status": "pass",
            "runtime_vetoes": [],
            "degrade_flags": [],
            "staleness_veto": False,
            "conflict_veto": False,
            "position_state": "ARMED",
            "current_position_direction": "neutral",
            "position_size_pct": 0.0,
            "execution_allowed": True,
            "direction": "long",
            "initial_stop_loss": 0.97,
        },
    )
    generated_at = datetime(2026, 4, 26, 14, 0, 0)
    preview_config = BotConfig(
        runtime_mode=RuntimeMode.REAL,
        state_store_path=tmp_path / "preview_state.json",
        audit_log_path=tmp_path / "preview_audit.jsonl",
        artifacts_root=tmp_path / "runtime",
    )
    preview_adapter = FakeExchangeAdapter(
        capabilities=AdapterCapabilities(
            supports_real_execution=True,
            supports_recent_fill_sync=True,
            supports_trailing_stop_update=True,
            supports_breakeven_update=True,
        ),
        snapshot=AdapterRuntimeSnapshot(
            position=PositionSnapshot(position_state="FLAT", direction="neutral", size_pct=0.0),
            protective_stop_present=False,
        ),
    )
    ShadowOrchestrator(
        preview_config,
        engine_client=FakeEngineClient(payload),
        network_guard=NetworkGuard(),
        state_store=StateStore(preview_config.state_store_path),
        exchange_adapter=preview_adapter,
    ).run_cycle(generated_at=generated_at)
    preview_audit = json.loads(preview_config.audit_log_path.read_text(encoding="utf-8").splitlines()[-1])
    token = preview_audit["payload"]["execution_results"][0]["details"]["expected_confirmation_token"]
    config = BotConfig(
        runtime_mode=RuntimeMode.REAL,
        manual_entry_confirmation_token=token,
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
        ),
        snapshot_sequence=[
            AdapterRuntimeSnapshot(
                position=PositionSnapshot(position_state="FLAT", direction="neutral", size_pct=0.0),
                protective_stop_present=False,
            ),
            AdapterRuntimeSnapshot(
                position=PositionSnapshot(position_state="ENTERED", direction="long", size_pct=0.3),
                protective_stop_present=True,
            ),
        ],
        execution_results=[
            CommandExecutionResult(
                target="entry_order",
                status="accepted",
                accepted=True,
                simulated=False,
                reason="effective_action:entry_long",
            )
        ],
    )
    orchestrator = ShadowOrchestrator(
        config,
        engine_client=FakeEngineClient(payload),
        network_guard=NetworkGuard(),
        state_store=StateStore(config.state_store_path),
        exchange_adapter=adapter,
    )

    report = orchestrator.run_cycle(generated_at=generated_at)

    assert [command.target for command in adapter.last_commands] == ["entry_order", "maintain_protective_stop"]
    assert report.command_result_statuses == ["accepted"]


def test_shadow_orchestrator_refreshes_runtime_snapshot_after_real_reduce_success(tmp_path: Path) -> None:
    payload = EngineCyclePayload(
        judgement={
            "status": "ok",
            "diagnostic": "",
            "research_bundle": {"ready": True, "bundle_status": "healthy"},
        },
        handoff={
            "generated_at": "2026-04-26T14:05:00",
            "action": "reduce",
            "risk_filter_status": "pass",
            "runtime_vetoes": [],
            "degrade_flags": [],
            "staleness_veto": False,
            "conflict_veto": False,
            "position_state": "ENTERED",
            "current_position_direction": "long",
            "position_size_pct": 0.3,
            "direction": "long",
            "reduce_conditions": ["crowding_warning"],
            "initial_stop_loss": 0.97,
        },
    )
    config = BotConfig(
        runtime_mode=RuntimeMode.REAL,
        manual_entry_confirmation_required=False,
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
            capabilities=AdapterCapabilities(
                supports_real_execution=True,
                supports_recent_fill_sync=True,
                supports_trailing_stop_update=True,
                supports_breakeven_update=True,
            ),
            snapshot_sequence=[
                AdapterRuntimeSnapshot(
                    position=PositionSnapshot(position_state="ENTERED", direction="long", size_pct=0.3),
                    protective_stop_present=False,
                ),
                AdapterRuntimeSnapshot(
                    position=PositionSnapshot(position_state="ENTERED", direction="long", size_pct=0.15),
                    protective_stop_present=True,
                ),
            ],
            reconciliation=ReconciliationResult(
                in_sync=False,
                protective_stop_present=False,
                needs_position_sync=True,
                needs_order_sync=True,
                reason_codes=["position_size_mismatch", "protective_stop_missing"],
            ),
            execution_results=[
                CommandExecutionResult(
                    target="reduce_order",
                    status="accepted",
                    accepted=True,
                    simulated=False,
                    reason="effective_action:reduce",
                )
            ],
        ),
    )

    report = orchestrator.run_risk_assist_cycle(generated_at=datetime(2026, 4, 26, 14, 5, 0))

    assert report.runtime_mode == "real"
    assert report.runtime_overview["runtime_position_state"] == "ENTERED"
    assert report.runtime_overview["runtime_direction"] == "long"
    assert report.runtime_overview["runtime_size_pct"] == 0.15
    assert report.runtime_overview["runtime_protective_stop_present"] is True
    assert report.runtime_overview["observed_position_state"] == "ENTERED"
    assert report.runtime_overview["observed_direction"] == "long"
    assert report.runtime_overview["observed_size_pct"] == 0.15
    updated_state = state_store.load()
    assert updated_state.observed_position_size_pct == 0.15



def test_risk_assist_cycle_prefers_real_reconciliation_summary_over_stale_runtime_snapshot(tmp_path: Path) -> None:
    payload = EngineCyclePayload(
        judgement={
            "status": "ok",
            "diagnostic": "",
            "research_bundle": {"ready": True, "bundle_status": "healthy"},
        },
        handoff={
            "generated_at": "2026-04-26T14:06:00",
            "action": "reduce",
            "risk_filter_status": "pass",
            "runtime_vetoes": [],
            "degrade_flags": [],
            "staleness_veto": False,
            "conflict_veto": False,
            "position_state": "ENTERED",
            "current_position_direction": "long",
            "position_size_pct": 0.3,
            "direction": "long",
            "reduce_conditions": ["crowding_warning"],
            "initial_stop_loss": 0.97,
        },
    )
    config = BotConfig(
        runtime_mode=RuntimeMode.REAL,
        manual_entry_confirmation_required=False,
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
            capabilities=AdapterCapabilities(
                supports_real_execution=True,
                supports_recent_fill_sync=True,
                supports_trailing_stop_update=True,
                supports_breakeven_update=True,
            ),
            snapshot=AdapterRuntimeSnapshot(
                position=PositionSnapshot(position_state="ENTERED", direction="long", size_pct=0.3),
                protective_stop_present=True,
            ),
            reconciliation=ReconciliationResult(
                in_sync=False,
                protective_stop_present=True,
                needs_position_sync=True,
                needs_order_sync=False,
                reason_codes=["position_size_mismatch"],
            ),
            execution_results=[
                CommandExecutionResult(
                    target="reconcile_position_and_orders",
                    status="accepted",
                    accepted=True,
                    simulated=False,
                    reason="reconciliation_required",
                    details={
                        "response_summary": {
                            "position_state": "ENTERED",
                            "direction": "long",
                            "size_pct": 0.15,
                        }
                    },
                )
            ],
        ),
    )

    report = orchestrator.run_risk_assist_cycle(generated_at=datetime(2026, 4, 26, 14, 6, 0))

    assert report.runtime_mode == "real"
    assert report.runtime_overview["runtime_size_pct"] == 0.3
    assert report.runtime_overview["observed_size_pct"] == 0.15
    updated_state = state_store.load()
    assert updated_state.observed_position_size_pct == 0.15



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
    assert report.action_summary == {
        "requested_action": "entry_short",
        "effective_action": "wait",
        "plan_reason": "entry_disallowed_by_guard",
        "blocked": False,
        "degraded": True,
        "guard_reason_codes": ["diagnostic:transport"],
        "reconciliation_in_sync": True,
        "reconciliation_reason_codes": [],
    }
    assert report.adapter_action_types == ["reconcile_position_and_orders"]
    assert report.command_types == ["reconcile_position_and_orders"]
    assert report.degraded is True


def test_shadow_orchestrator_blocks_new_entry_when_recovery_is_pending(tmp_path: Path) -> None:
    payload = EngineCyclePayload(
        judgement={
            "status": "ok",
            "diagnostic": "",
            "research_bundle": {"ready": True, "bundle_status": "healthy"},
        },
        handoff={
            "generated_at": "2026-04-26T12:02:00",
            "action": "entry_long",
            "risk_filter_status": "pass",
            "runtime_vetoes": [],
            "degrade_flags": [],
            "staleness_veto": False,
            "conflict_veto": False,
            "position_state": "ARMED",
            "current_position_direction": "neutral",
            "position_size_pct": 0.2,
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
    state.observed_position_size_pct = 0.2
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
                needs_position_sync=False,
                needs_order_sync=True,
                reason_codes=["protective_stop_missing"],
            ),
        ),
    )
    report = orchestrator.run_cycle(generated_at=datetime(2026, 4, 26, 12, 2, 0))
    assert report.effective_action == "wait"
    assert report.plan_reason == "entry_blocked_until_reconciliation"
    assert report.command_reasons == ["protective_stop_required", "reconciliation_required"]
    assert report.command_types == ["maintain_protective_stop", "reconcile_position_and_orders"]
    assert "entry_order" not in report.command_types



def test_shadow_orchestrator_keeps_observe_only_as_quant_action(tmp_path: Path) -> None:
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
    assert report.effective_action == "observe_only"
    assert report.plan_reason == "quant_action_passthrough"


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
    assert fake_client.fetch_cycle_calls == 0
    audit_event = json.loads(config.audit_log_path.read_text(encoding="utf-8").splitlines()[-1])
    assert audit_event["event_type"] == "risk_assist_cycle_skipped"
    assert audit_event["payload"]["automation_state"] == "disabled"


def test_risk_assist_cycle_runs_when_entered_state_has_zero_size_pct(tmp_path: Path) -> None:
    payload = EngineCyclePayload(
        judgement={
            "status": "ok",
            "diagnostic": "",
            "research_bundle": {"ready": True, "bundle_status": "healthy"},
        },
        handoff={
            "generated_at": "2026-04-26T12:06:00",
            "action": "wait",
            "risk_filter_status": "pass",
            "runtime_vetoes": [],
            "degrade_flags": [],
            "staleness_veto": False,
            "conflict_veto": False,
            "position_state": "ENTERED",
            "current_position_direction": "long",
            "position_size_pct": 0.0,
            "direction": "long",
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
    state.observed_position_state = "ENTERED"
    state.observed_position_direction = "long"
    state.observed_position_size_pct = 0.0
    state_store.save(state)
    fake_client = FakeEngineClient(payload)
    orchestrator = ShadowOrchestrator(
        config,
        engine_client=fake_client,
        network_guard=NetworkGuard(),
        state_store=state_store,
        exchange_adapter=FakeExchangeAdapter(
            snapshot=AdapterRuntimeSnapshot(
                position=PositionSnapshot(position_state="ENTERED", direction="long", size_pct=0.0),
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
    report = orchestrator.run_risk_assist_cycle(generated_at=datetime(2026, 4, 26, 12, 6, 0))
    assert report.eligible is True
    assert fake_client.fetch_cycle_calls == 1
    assert "protective_stop_required" in report.command_reasons



def test_risk_assist_cycle_surfaces_runtime_snapshot_unavailable_across_report_audit_and_state(tmp_path: Path) -> None:
    payload = EngineCyclePayload(
        judgement={
            "status": "ok",
            "diagnostic": "",
            "research_bundle": {"ready": True, "bundle_status": "healthy"},
        },
        handoff={
            "generated_at": "2026-04-26T12:06:30",
            "action": "wait",
            "risk_filter_status": "pass",
            "runtime_vetoes": [],
            "degrade_flags": [],
            "staleness_veto": False,
            "conflict_veto": False,
            "position_state": "ENTERED",
            "current_position_direction": "long",
            "position_size_pct": 0.3,
            "initial_stop_loss": 0.97,
        },
    )
    config = BotConfig(
        runtime_mode=RuntimeMode.SIMULATED_REAL,
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
    adapter = FakeExchangeAdapter(
        capabilities=AdapterCapabilities(
            supports_real_execution=True,
            supports_recent_fill_sync=True,
            supports_trailing_stop_update=True,
            supports_breakeven_update=True,
        ),
        snapshot=AdapterRuntimeSnapshot(
            position=PositionSnapshot(position_state="FLAT", direction="neutral", size_pct=0.0),
            protective_stop_present=False,
            snapshot_valid=False,
        ),
        reconciliation=ReconciliationResult(
            in_sync=False,
            protective_stop_present=False,
            needs_position_sync=True,
            needs_order_sync=False,
            reason_codes=["runtime_snapshot_unavailable"],
        ),
    )
    orchestrator = ShadowOrchestrator(
        config,
        engine_client=FakeEngineClient(payload),
        network_guard=NetworkGuard(),
        state_store=state_store,
        exchange_adapter=adapter,
    )

    report = orchestrator.run_risk_assist_cycle(generated_at=datetime(2026, 4, 26, 12, 6, 30))

    assert report.reconciliation_in_sync is False
    assert report.reconciliation_reason_codes == ["runtime_snapshot_unavailable"]
    assert report.action_summary["reconciliation_reason_codes"] == ["runtime_snapshot_unavailable"]
    assert report.runtime_overview["reconciliation_reason_codes"] == ["runtime_snapshot_unavailable"]
    assert report.command_reasons == [
        "protective_stop_required",
        "reconciliation_required",
    ]
    assert report.command_types == [
        "maintain_protective_stop",
        "reconcile_position_and_orders",
    ]
    updated_state = state_store.load()
    assert updated_state.recovery_required is True
    assert updated_state.reconciliation_required is True
    assert updated_state.protective_stop_required is True
    assert updated_state.observed_position_state == "ENTERED"
    assert updated_state.observed_position_direction == "long"
    assert updated_state.observed_position_size_pct == 0.3

    audit_event = json.loads(config.audit_log_path.read_text(encoding="utf-8").splitlines()[-1])
    assert audit_event["event_type"] == "risk_assist_cycle"
    assert audit_event["payload"]["action_summary"]["reconciliation_reason_codes"] == ["runtime_snapshot_unavailable"]
    assert audit_event["payload"]["runtime_overview"]["reconciliation_reason_codes"] == ["runtime_snapshot_unavailable"]
    assert audit_event["payload"]["reconciliation"]["reason_codes"] == ["runtime_snapshot_unavailable"]



def test_risk_assist_cycle_clears_stale_recovery_flags_when_runtime_is_in_sync(tmp_path: Path) -> None:
    payload = EngineCyclePayload(
        judgement={
            "status": "ok",
            "diagnostic": "",
            "research_bundle": {"ready": True, "bundle_status": "healthy"},
        },
        handoff={
            "generated_at": "2026-04-26T12:07:00",
            "action": "wait",
            "risk_filter_status": "pass",
            "runtime_vetoes": [],
            "degrade_flags": [],
            "staleness_veto": False,
            "conflict_veto": False,
            "position_state": "ENTERED",
            "current_position_direction": "long",
            "position_size_pct": 0.25,
        },
    )
    config = BotConfig(
        runtime_mode=RuntimeMode.SIMULATED_REAL,
        state_store_path=tmp_path / "state.json",
        audit_log_path=tmp_path / "audit.jsonl",
        artifacts_root=tmp_path / "runtime",
    )
    state_store = StateStore(config.state_store_path)
    state = state_store.load()
    state.recovery_required = True
    state.reconciliation_required = True
    state.protective_stop_required = False
    state.observed_position_state = "ENTERED"
    state.observed_position_direction = "long"
    state.observed_position_size_pct = 0.25
    state_store.save(state)
    fake_client = FakeEngineClient(payload)
    orchestrator = ShadowOrchestrator(
        config,
        engine_client=fake_client,
        network_guard=NetworkGuard(),
        state_store=state_store,
        exchange_adapter=FakeExchangeAdapter(
            capabilities=AdapterCapabilities(
                supports_real_execution=True,
                supports_recent_fill_sync=True,
                supports_trailing_stop_update=True,
                supports_breakeven_update=True,
            ),
            snapshot=AdapterRuntimeSnapshot(
                position=PositionSnapshot(position_state="ENTERED", direction="long", size_pct=0.25),
                protective_stop_present=True,
            ),
            reconciliation=ReconciliationResult(
                in_sync=True,
                protective_stop_present=True,
                needs_position_sync=False,
                needs_order_sync=False,
                reason_codes=[],
            ),
        ),
    )
    report = orchestrator.run_risk_assist_cycle(generated_at=datetime(2026, 4, 26, 12, 7, 0))
    updated_state = state_store.load()
    assert report.eligible is True
    assert report.command_types == []
    assert updated_state.execution_state.value == "position_open"
    assert updated_state.recovery_required is False
    assert updated_state.reconciliation_required is False
    assert updated_state.protective_stop_required is False


def test_risk_assist_cycle_clears_stale_protective_stop_flag_when_runtime_stop_exists(tmp_path: Path) -> None:
    payload = EngineCyclePayload(
        judgement={
            "status": "ok",
            "diagnostic": "",
            "research_bundle": {"ready": True, "bundle_status": "healthy"},
        },
        handoff={
            "generated_at": "2026-04-26T12:07:30",
            "action": "wait",
            "risk_filter_status": "pass",
            "runtime_vetoes": [],
            "degrade_flags": [],
            "staleness_veto": False,
            "conflict_veto": False,
            "position_state": "ENTERED",
            "current_position_direction": "long",
            "position_size_pct": 0.25,
        },
    )
    config = BotConfig(
        runtime_mode=RuntimeMode.SIMULATED_REAL,
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
    state.observed_position_size_pct = 0.25
    state_store.save(state)
    adapter = FakeExchangeAdapter(
        capabilities=AdapterCapabilities(
            supports_real_execution=True,
            supports_recent_fill_sync=True,
            supports_trailing_stop_update=True,
            supports_breakeven_update=True,
        ),
        snapshot=AdapterRuntimeSnapshot(
            position=PositionSnapshot(position_state="ENTERED", direction="long", size_pct=0.25),
            protective_stop_present=True,
        ),
        reconciliation=ReconciliationResult(
            in_sync=True,
            protective_stop_present=True,
            needs_position_sync=False,
            needs_order_sync=False,
            reason_codes=[],
        ),
    )
    orchestrator = ShadowOrchestrator(
        config,
        engine_client=FakeEngineClient(payload),
        network_guard=NetworkGuard(),
        state_store=state_store,
        exchange_adapter=adapter,
    )

    report = orchestrator.run_risk_assist_cycle(generated_at=datetime(2026, 4, 26, 12, 7, 30))

    updated_state = state_store.load()
    assert report.command_types == []
    assert adapter.last_commands == []
    assert updated_state.execution_state.value == "position_open"
    assert updated_state.recovery_required is False
    assert updated_state.reconciliation_required is False
    assert updated_state.protective_stop_required is False



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
            snapshot=AdapterRuntimeSnapshot(snapshot_valid=False),
            reconciliation=ReconciliationResult(
                in_sync=True,
                protective_stop_present=False,
                needs_position_sync=False,
                needs_order_sync=False,
                reason_codes=[],
            ),
        ),
    )
    report = orchestrator.run_risk_assist_cycle(generated_at=datetime(2026, 4, 26, 12, 5, 0))
    assert report.runtime_mode == "shadow"
    assert report.adapter_supports_real_execution is False
    assert report.eligible is True
    assert report.effective_action == "reduce"
    assert report.action_summary == {
        "requested_action": "reduce",
        "effective_action": "reduce",
        "plan_reason": "quant_action_passthrough",
        "blocked": False,
        "degraded": False,
        "guard_reason_codes": [],
        "reconciliation_in_sync": True,
        "reconciliation_reason_codes": [],
    }
    assert report.command_reasons == [
        "effective_action:reduce",
        "protective_stop_required",
    ]
    assert report.command_summary == [
        {"target": "reduce_order", "reason": "effective_action:reduce", "operation": "place"},
        {"target": "maintain_protective_stop", "reason": "protective_stop_required", "operation": "upsert"},
    ]
    assert report.command_result_summary == [
        {"target": "reduce_order", "reason": "effective_action:reduce", "status": "simulated", "accepted": True, "simulated": True, "idempotency_key": "reduce_order:2026-04-26T12:05:00:reduce:long", "client_order_id": "", "exchange_order_id": "", "error_kind": ""},
        {"target": "maintain_protective_stop", "reason": "protective_stop_required", "status": "simulated", "accepted": True, "simulated": True, "idempotency_key": "maintain_protective_stop:2026-04-26T12:05:00:reduce:long", "client_order_id": "", "exchange_order_id": "", "error_kind": ""},
    ]
    assert report.reconciliation_in_sync is True
    assert "reduce_order" in report.adapter_action_types
    assert "reconcile_position_and_orders" not in report.adapter_action_types
    assert "reduce_order" in report.command_types
    assert "reconcile_position_and_orders" not in report.command_types
    assert all(status == "simulated" for status in report.command_result_statuses)
    assert fake_client.fetch_cycle_calls == 1



def test_risk_assist_cycle_keeps_exit_available_during_recovery(tmp_path: Path) -> None:
    payload = EngineCyclePayload(
        judgement={
            "status": "ok",
            "diagnostic": "",
            "research_bundle": {"ready": True, "bundle_status": "healthy"},
        },
        handoff={
            "generated_at": "2026-04-26T12:06:00",
            "action": "exit",
            "risk_filter_status": "pass",
            "runtime_vetoes": [],
            "degrade_flags": [],
            "staleness_veto": False,
            "conflict_veto": False,
            "position_state": "ENTERED",
            "current_position_direction": "long",
            "position_size_pct": 0.3,
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
    report = orchestrator.run_risk_assist_cycle(generated_at=datetime(2026, 4, 26, 12, 6, 0))
    assert report.runtime_mode == "shadow"
    assert report.adapter_supports_real_execution is False
    assert report.eligible is True
    assert report.effective_action == "exit"
    assert report.action_summary == {
        "requested_action": "exit",
        "effective_action": "exit",
        "plan_reason": "quant_action_passthrough",
        "blocked": False,
        "degraded": False,
        "guard_reason_codes": [],
        "reconciliation_in_sync": False,
        "reconciliation_reason_codes": ["protective_stop_missing"],
    }
    assert report.command_reasons == [
        "effective_action:exit",
        "protective_stop_required",
        "reconciliation_required",
    ]
    assert report.command_summary == [
        {"target": "exit_order", "reason": "effective_action:exit", "operation": "place"},
        {"target": "maintain_protective_stop", "reason": "protective_stop_required", "operation": "upsert"},
        {"target": "reconcile_position_and_orders", "reason": "reconciliation_required", "operation": "query"},
    ]
    assert report.command_result_summary == [
        {"target": "exit_order", "reason": "effective_action:exit", "status": "simulated", "accepted": True, "simulated": True, "idempotency_key": "exit_order:2026-04-26T12:06:00:exit:long", "client_order_id": "", "exchange_order_id": "", "error_kind": ""},
        {"target": "maintain_protective_stop", "reason": "protective_stop_required", "status": "simulated", "accepted": True, "simulated": True, "idempotency_key": "maintain_protective_stop:2026-04-26T12:06:00:exit:long", "client_order_id": "", "exchange_order_id": "", "error_kind": ""},
        {"target": "reconcile_position_and_orders", "reason": "reconciliation_required", "status": "simulated", "accepted": True, "simulated": True, "idempotency_key": "reconcile_position_and_orders:2026-04-26T12:06:00:exit:long", "client_order_id": "", "exchange_order_id": "", "error_kind": ""},
    ]
    assert report.reconciliation_in_sync is False
    assert "exit_order" in report.adapter_action_types
    assert "reconcile_position_and_orders" in report.adapter_action_types
    assert "exit_order" in report.command_types
    assert "reconcile_position_and_orders" in report.command_types
    assert all(status == "simulated" for status in report.command_result_statuses)
    assert fake_client.fetch_cycle_calls == 1




def test_risk_assist_cycle_audit_log_persists_recovery_metadata_fields(tmp_path: Path) -> None:
    payload = EngineCyclePayload(
        judgement={
            "status": "ok",
            "diagnostic": "request_diagnostic=transport | category=transport | boundary=request",
            "research_bundle": {"ready": True, "bundle_status": "healthy"},
        },
        handoff={
            "generated_at": "2026-04-26T12:05:00",
            "action": "reduce",
            "risk_filter_status": "pass",
            "runtime_vetoes": [],
            "degrade_flags": ["research_degraded"],
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
    orchestrator = ShadowOrchestrator(
        config,
        engine_client=FakeEngineClient(payload),
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

    assert report.effective_action == "reduce"
    audit_event = json.loads(config.audit_log_path.read_text(encoding="utf-8").splitlines()[-1])
    assert audit_event["event_type"] == "risk_assist_cycle"
    assert audit_event["payload"]["plan_reason"] == "quant_action_passthrough"
    assert audit_event["payload"]["state"]["last_plan_reason"] == "quant_action_passthrough"
    assert audit_event["payload"]["action_summary"] == {
        "requested_action": "reduce",
        "effective_action": "reduce",
        "plan_reason": "quant_action_passthrough",
        "blocked": False,
        "degraded": True,
        "guard_reason_codes": ["diagnostic:transport", "degrade_flag:research_degraded"],
        "reconciliation_in_sync": False,
        "reconciliation_reason_codes": ["protective_stop_missing"],
    }
    assert audit_event["payload"]["command_reasons"] == [
        "effective_action:reduce",
        "protective_stop_required",
        "reconciliation_required",
    ]
    assert audit_event["payload"]["command_summary"] == [
        {"target": "reduce_order", "reason": "effective_action:reduce", "operation": "place"},
        {"target": "maintain_protective_stop", "reason": "protective_stop_required", "operation": "upsert"},
        {"target": "reconcile_position_and_orders", "reason": "reconciliation_required", "operation": "query"},
    ]
    assert audit_event["payload"]["command_result_summary"] == [
        {"target": "reduce_order", "reason": "effective_action:reduce", "status": "simulated", "accepted": True, "simulated": True, "idempotency_key": "reduce_order:2026-04-26T12:05:00:reduce:long", "client_order_id": "", "exchange_order_id": "", "error_kind": ""},
        {"target": "maintain_protective_stop", "reason": "protective_stop_required", "status": "simulated", "accepted": True, "simulated": True, "idempotency_key": "maintain_protective_stop:2026-04-26T12:05:00:reduce:long", "client_order_id": "", "exchange_order_id": "", "error_kind": ""},
        {"target": "reconcile_position_and_orders", "reason": "reconciliation_required", "status": "simulated", "accepted": True, "simulated": True, "idempotency_key": "reconcile_position_and_orders:2026-04-26T12:05:00:reduce:long", "client_order_id": "", "exchange_order_id": "", "error_kind": ""},
    ]
    assert audit_event["payload"]["state"]["last_handoff_action"] == "reduce"
    assert audit_event["payload"]["state"]["last_diagnostic_category"] == "transport"
    assert audit_event["payload"]["state"]["last_reason_codes"] == [
        "diagnostic:transport",
        "degrade_flag:research_degraded",
    ]



def test_risk_assist_cycle_persists_recovery_metadata_fields(tmp_path: Path) -> None:
    payload = EngineCyclePayload(
        judgement={
            "status": "ok",
            "diagnostic": "request_diagnostic=transport | category=transport | boundary=request",
            "research_bundle": {"ready": True, "bundle_status": "healthy"},
        },
        handoff={
            "generated_at": "2026-04-26T12:05:00",
            "action": "reduce",
            "risk_filter_status": "pass",
            "runtime_vetoes": [],
            "degrade_flags": ["research_degraded"],
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
    orchestrator = ShadowOrchestrator(
        config,
        engine_client=FakeEngineClient(payload),
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

    assert report.effective_action == "reduce"
    updated_state = state_store.load()
    assert updated_state.last_plan_reason == "quant_action_passthrough"
    assert updated_state.last_handoff_action == "reduce"
    assert updated_state.last_diagnostic_category == "transport"
    assert updated_state.last_reason_codes == ["diagnostic:transport", "degrade_flag:research_degraded"]



def test_risk_assist_cycle_keeps_reason_codes_and_recovery_state_aligned(tmp_path: Path) -> None:
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

    report = orchestrator.run_risk_assist_cycle(generated_at=datetime(2026, 4, 26, 12, 5, 0))

    assert report.reconciliation_in_sync is False
    assert report.reconciliation_reason_codes == ["position_size_mismatch", "protective_stop_missing"]
    updated_state = state_store.load()
    assert updated_state.execution_state.value == "reconciling"
    assert updated_state.pending_action == "reduce"
    assert updated_state.recovery_required is True
    assert updated_state.reconciliation_required is True
    assert updated_state.protective_stop_required is True
    assert updated_state.observed_position_size_pct == 0.2



def test_risk_assist_cycle_marks_primary_reduce_failure_for_recovery(tmp_path: Path) -> None:
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
    orchestrator = ShadowOrchestrator(
        config,
        engine_client=FakeEngineClient(payload),
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
            execution_results=[
                CommandExecutionResult(
                    target="reduce_order",
                    status="rejected",
                    accepted=False,
                    simulated=True,
                    reason="quant_action_passthrough",
                ),
                CommandExecutionResult(
                    target="reconcile_position_and_orders",
                    status="simulated",
                    accepted=True,
                    simulated=True,
                    reason="reconciliation_required",
                ),
            ],
        ),
    )

    report = orchestrator.run_risk_assist_cycle(generated_at=datetime(2026, 4, 26, 12, 5, 0))

    assert report.command_result_statuses == ["rejected", "simulated"]
    updated_state = state_store.load()
    assert updated_state.execution_state.value == "reduce_pending"
    assert updated_state.pending_action == "reduce"
    assert updated_state.recovery_required is True
    assert updated_state.reconciliation_required is True
    assert updated_state.protective_stop_required is True



def test_risk_assist_cycle_marks_stop_failure_for_reconciliation(tmp_path: Path) -> None:
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
    orchestrator = ShadowOrchestrator(
        config,
        engine_client=FakeEngineClient(payload),
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
            execution_results=[
                CommandExecutionResult(
                    target="reduce_order",
                    status="simulated",
                    accepted=True,
                    simulated=True,
                    reason="quant_action_passthrough",
                ),
                CommandExecutionResult(
                    target="maintain_protective_stop",
                    status="failed",
                    accepted=False,
                    simulated=True,
                    reason="protective_stop_required",
                ),
                CommandExecutionResult(
                    target="reconcile_position_and_orders",
                    status="simulated",
                    accepted=True,
                    simulated=True,
                    reason="reconciliation_required",
                ),
            ],
        ),
    )

    report = orchestrator.run_risk_assist_cycle(generated_at=datetime(2026, 4, 26, 12, 5, 0))

    assert report.command_result_statuses == ["simulated", "failed", "simulated"]
    assert report.execution_overview == {
        "requested_action": "reduce",
        "effective_action": "reduce",
        "primary_target": "reduce_order",
        "primary_reason": "effective_action:reduce",
        "primary_status": "simulated",
        "primary_accepted": True,
        "has_primary_failure": False,
        "has_auxiliary_failure": True,
        "auxiliary_targets": ["maintain_protective_stop", "reconcile_position_and_orders"],
    }
    assert report.runtime_overview == {
        "expected_position_state": "ENTERED",
        "expected_direction": "long",
        "expected_size_pct": 0.3,
        "runtime_position_state": "ENTERED",
        "runtime_direction": "long",
        "runtime_size_pct": 0.3,
        "runtime_protective_stop_present": False,
        "observed_position_state": "ENTERED",
        "observed_direction": "long",
        "observed_size_pct": 0.3,
        "execution_state": "reconciling",
        "pending_action": "",
        "recovery_required": True,
        "reconciliation_required": True,
        "protective_stop_required": True,
        "reconciliation_in_sync": False,
        "reconciliation_reason_codes": ["protective_stop_missing"],
        "recent_fill_summary": {},
    }
    updated_state = state_store.load()
    assert updated_state.execution_state.value == "reconciling"
    assert updated_state.pending_action == ""
    assert updated_state.recovery_required is True
    assert updated_state.reconciliation_required is True
    assert updated_state.protective_stop_required is True
    audit_event = json.loads(config.audit_log_path.read_text(encoding="utf-8").splitlines()[-1])
    assert audit_event["event_type"] == "risk_assist_cycle"
    assert audit_event["payload"]["execution_result_summary"] == {
        "has_failure": True,
        "primary_failed": False,
        "primary_succeeded": True,
        "auxiliary_failed": True,
        "protective_stop_failed": True,
        "capability_blocked": False,
    }
    assert audit_event["payload"]["execution_overview"] == report.execution_overview
    assert audit_event["payload"]["runtime_overview"] == report.runtime_overview



def test_risk_assist_cycle_audit_log_marks_failure_categories(tmp_path: Path) -> None:
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
            "direction": "long",
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
    adapter = FakeExchangeAdapter(
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
        execution_results=[
            CommandExecutionResult(
                target="reduce_order",
                status="rejected",
                accepted=False,
                simulated=True,
                reason="effective_action:reduce",
            ),
            CommandExecutionResult(
                target="maintain_protective_stop",
                status="failed",
                accepted=False,
                simulated=True,
                reason="protective_stop_required",
            ),
            CommandExecutionResult(
                target="reconcile_position_and_orders",
                status="simulated",
                accepted=True,
                simulated=True,
                reason="reconciliation_required",
            ),
        ],
    )
    orchestrator = ShadowOrchestrator(
        config,
        engine_client=FakeEngineClient(payload),
        network_guard=NetworkGuard(),
        state_store=state_store,
        exchange_adapter=adapter,
    )

    report = orchestrator.run_risk_assist_cycle(generated_at=datetime(2026, 4, 26, 12, 5, 0))

    assert report.command_result_statuses == ["rejected", "failed", "simulated"]
    assert report.execution_overview == {
        "requested_action": "reduce",
        "effective_action": "reduce",
        "primary_target": "reduce_order",
        "primary_reason": "effective_action:reduce",
        "primary_status": "rejected",
        "primary_accepted": False,
        "has_primary_failure": True,
        "has_auxiliary_failure": True,
        "auxiliary_targets": ["maintain_protective_stop", "reconcile_position_and_orders"],
    }
    audit_event = json.loads(config.audit_log_path.read_text(encoding="utf-8").splitlines()[-1])
    assert audit_event["event_type"] == "risk_assist_cycle"
    assert audit_event["payload"]["execution_result_summary"] == {
        "has_failure": True,
        "primary_failed": True,
        "primary_succeeded": False,
        "auxiliary_failed": True,
        "protective_stop_failed": True,
        "capability_blocked": False,
    }
    assert audit_event["payload"]["execution_overview"] == {
        "requested_action": "reduce",
        "effective_action": "reduce",
        "primary_target": "reduce_order",
        "primary_reason": "effective_action:reduce",
        "primary_status": "rejected",
        "primary_accepted": False,
        "has_primary_failure": True,
        "has_auxiliary_failure": True,
        "auxiliary_targets": ["maintain_protective_stop", "reconcile_position_and_orders"],
    }
    assert audit_event["payload"]["reconciliation"]["reason_codes"] == ["protective_stop_missing"]
    assert audit_event["payload"]["state"]["execution_state"] == "reduce_pending"
    assert audit_event["payload"]["state"]["pending_action"] == ""
    assert audit_event["payload"]["state"]["recovery_required"] is True
    assert audit_event["payload"]["state"]["reconciliation_required"] is True
    assert audit_event["payload"]["state"]["protective_stop_required"] is True



def test_risk_assist_cycle_persists_reconciliation_and_fill_summaries_in_simulated_real(tmp_path: Path) -> None:
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
            "direction": "long",
            "reduce_conditions": ["crowding_warning"],
            "initial_stop_loss": 0.97,
        },
    )
    config = BotConfig(
        runtime_mode=RuntimeMode.SIMULATED_REAL,
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
    adapter = FakeExchangeAdapter(
        capabilities=AdapterCapabilities(
            supports_real_execution=True,
            supports_recent_fill_sync=True,
            supports_trailing_stop_update=True,
            supports_breakeven_update=True,
        ),
        snapshot=AdapterRuntimeSnapshot(
            position=PositionSnapshot(position_state="ENTERED", direction="long", size_pct=0.25),
            protective_stop_present=True,
        ),
        reconciliation=ReconciliationResult(
            in_sync=False,
            protective_stop_present=True,
            needs_position_sync=True,
            needs_order_sync=False,
            reason_codes=["position_size_mismatch"],
        ),
        execution_results=[
            CommandExecutionResult(
                target="reduce_order",
                status="simulated",
                accepted=True,
                simulated=True,
                reason="effective_action:reduce",
                details={"idempotency_key": "reduce-1"},
            ),
            CommandExecutionResult(
                target="sync_recent_fills",
                status="simulated",
                accepted=True,
                simulated=True,
                reason="recent_fill_sync_required",
                details={
                    "response_summary": {
                        "fill_count": 3,
                        "latest_trade_id": "21",
                    },
                    "idempotency_key": "fills-2",
                },
            ),
            CommandExecutionResult(
                target="reconcile_position_and_orders",
                status="simulated",
                accepted=True,
                simulated=True,
                reason="reconciliation_required",
                details={
                    "response_summary": {
                        "position_state": "ENTERED",
                        "direction": "long",
                        "size_pct": 0.25,
                    },
                    "idempotency_key": "reconcile-2",
                },
            ),
        ],
    )
    orchestrator = ShadowOrchestrator(
        config,
        engine_client=FakeEngineClient(payload),
        network_guard=NetworkGuard(),
        state_store=state_store,
        exchange_adapter=adapter,
    )

    report = orchestrator.run_risk_assist_cycle(generated_at=datetime(2026, 4, 26, 12, 5, 0))

    assert report.runtime_mode == "simulated-real"
    assert report.runtime_overview == {
        "expected_position_state": "ENTERED",
        "expected_direction": "long",
        "expected_size_pct": 0.3,
        "runtime_position_state": "ENTERED",
        "runtime_direction": "long",
        "runtime_size_pct": 0.25,
        "runtime_protective_stop_present": True,
        "observed_position_state": "ENTERED",
        "observed_direction": "long",
        "observed_size_pct": 0.25,
        "execution_state": "reconciling",
        "pending_action": "reduce",
        "recovery_required": True,
        "reconciliation_required": True,
        "protective_stop_required": True,
        "reconciliation_in_sync": False,
        "reconciliation_reason_codes": ["position_size_mismatch"],
        "recent_fill_summary": {
            "status": "simulated",
            "accepted": True,
            "simulated": True,
            "details": {
                "fill_count": 3,
                "latest_trade_id": "21",
            },
        },
    }
    updated_state = state_store.load()
    assert updated_state.recent_fill_summary == {
        "status": "simulated",
        "accepted": True,
        "simulated": True,
        "details": {
            "fill_count": 3,
            "latest_trade_id": "21",
        },
    }
    assert updated_state.observed_position_state == "ENTERED"
    assert updated_state.observed_position_direction == "long"
    assert updated_state.observed_position_size_pct == 0.25



def test_risk_assist_cycle_audit_log_includes_recent_fill_summary(tmp_path: Path) -> None:
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
            "direction": "long",
            "reduce_conditions": ["crowding_warning"],
            "initial_stop_loss": 0.97,
        },
    )
    config = BotConfig(
        runtime_mode=RuntimeMode.SIMULATED_REAL,
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
    adapter = FakeExchangeAdapter(
        capabilities=AdapterCapabilities(
            supports_real_execution=True,
            supports_recent_fill_sync=True,
            supports_trailing_stop_update=True,
            supports_breakeven_update=True,
        ),
        snapshot=AdapterRuntimeSnapshot(
            position=PositionSnapshot(position_state="ENTERED", direction="long", size_pct=0.25),
            protective_stop_present=True,
        ),
        reconciliation=ReconciliationResult(
            in_sync=False,
            protective_stop_present=True,
            needs_position_sync=True,
            needs_order_sync=False,
            reason_codes=["position_size_mismatch"],
        ),
        execution_results=[
            CommandExecutionResult(
                target="reduce_order",
                status="simulated",
                accepted=True,
                simulated=True,
                reason="effective_action:reduce",
                details={"idempotency_key": "reduce-1"},
            ),
            CommandExecutionResult(
                target="sync_recent_fills",
                status="simulated",
                accepted=True,
                simulated=True,
                reason="recent_fill_sync_required",
                details={
                    "response_summary": {
                        "fill_count": 3,
                        "latest_trade_id": "21",
                    },
                    "idempotency_key": "fills-2",
                },
            ),
            CommandExecutionResult(
                target="reconcile_position_and_orders",
                status="simulated",
                accepted=True,
                simulated=True,
                reason="reconciliation_required",
                details={
                    "response_summary": {
                        "position_state": "ENTERED",
                        "direction": "long",
                        "size_pct": 0.25,
                    },
                    "idempotency_key": "reconcile-2",
                },
            ),
        ],
    )
    orchestrator = ShadowOrchestrator(
        config,
        engine_client=FakeEngineClient(payload),
        network_guard=NetworkGuard(),
        state_store=state_store,
        exchange_adapter=adapter,
    )

    report = orchestrator.run_risk_assist_cycle(generated_at=datetime(2026, 4, 26, 12, 5, 0))

    assert report.runtime_mode == "simulated-real"
    audit_event = json.loads(config.audit_log_path.read_text(encoding="utf-8").splitlines()[-1])
    assert audit_event["event_type"] == "risk_assist_cycle"
    assert audit_event["payload"]["execution_result_summary"] == {
        "has_failure": False,
        "primary_failed": False,
        "primary_succeeded": True,
        "auxiliary_failed": False,
        "protective_stop_failed": False,
        "capability_blocked": False,
    }
    assert audit_event["payload"]["runtime_overview"] == report.runtime_overview
    assert audit_event["payload"]["recent_fill_summary"] == {
        "status": "simulated",
        "accepted": True,
        "simulated": True,
        "details": {
            "fill_count": 3,
            "latest_trade_id": "21",
        },
    }


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
            "execution_allowed": True,
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
                    reason="effective_action:entry_long",
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




def test_shadow_orchestrator_persists_reconciliation_and_fill_summaries_in_simulated_real(tmp_path: Path) -> None:
    payload = EngineCyclePayload(
        judgement={
            "status": "ok",
            "diagnostic": "",
            "research_bundle": {"ready": True, "bundle_status": "healthy"},
        },
        handoff={
            "generated_at": "2026-04-26T12:45:00",
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
        runtime_mode=RuntimeMode.SIMULATED_REAL,
        state_store_path=tmp_path / "state.json",
        audit_log_path=tmp_path / "audit.jsonl",
        artifacts_root=tmp_path / "runtime",
    )
    state_store = StateStore(config.state_store_path)
    state = state_store.load()
    state.observed_position_state = "ENTERED"
    state.observed_position_direction = "long"
    state.observed_position_size_pct = 0.3
    state.recovery_required = True
    state.reconciliation_required = True
    state_store.save(state)
    adapter = FakeExchangeAdapter(
        capabilities=AdapterCapabilities(
            supports_real_execution=True,
            supports_recent_fill_sync=True,
            supports_trailing_stop_update=True,
            supports_breakeven_update=True,
        ),
        snapshot=AdapterRuntimeSnapshot(
            position=PositionSnapshot(position_state="ENTERED", direction="long", size_pct=0.25),
            protective_stop_present=True,
        ),
        reconciliation=ReconciliationResult(
            in_sync=False,
            protective_stop_present=True,
            needs_position_sync=True,
            needs_order_sync=False,
            reason_codes=["position_size_mismatch"],
        ),
        execution_results=[
            CommandExecutionResult(
                target="sync_recent_fills",
                status="simulated",
                accepted=True,
                simulated=True,
                reason="recent_fill_sync_required",
                details={
                    "response_summary": {
                        "fill_count": 2,
                        "latest_trade_id": "12",
                    },
                    "idempotency_key": "fills-1",
                },
            ),
            CommandExecutionResult(
                target="reconcile_position_and_orders",
                status="simulated",
                accepted=True,
                simulated=True,
                reason="reconciliation_required",
                details={
                    "response_summary": {
                        "position_state": "ENTERED",
                        "direction": "long",
                        "size_pct": 0.25,
                    },
                    "idempotency_key": "reconcile-1",
                },
            ),
        ],
    )
    orchestrator = ShadowOrchestrator(
        config,
        engine_client=FakeEngineClient(payload),
        network_guard=NetworkGuard(),
        state_store=state_store,
        exchange_adapter=adapter,
    )
    report = orchestrator.run_cycle(generated_at=datetime(2026, 4, 26, 12, 45, 0))
    updated_state = state_store.load()
    assert report.runtime_mode == "simulated-real"
    assert report.reconciliation_in_sync is False
    assert report.runtime_overview == {
        "expected_position_state": "ENTERED",
        "expected_direction": "long",
        "expected_size_pct": 0.3,
        "runtime_position_state": "ENTERED",
        "runtime_direction": "long",
        "runtime_size_pct": 0.25,
        "runtime_protective_stop_present": True,
        "observed_position_state": "ENTERED",
        "observed_direction": "long",
        "observed_size_pct": 0.25,
        "execution_state": "reconciling",
        "pending_action": "",
        "recovery_required": True,
        "reconciliation_required": True,
        "protective_stop_required": False,
        "reconciliation_in_sync": False,
        "reconciliation_reason_codes": ["position_size_mismatch"],
        "recent_fill_summary": {
            "status": "simulated",
            "accepted": True,
            "simulated": True,
            "details": {
                "fill_count": 2,
                "latest_trade_id": "12",
            },
        },
    }
    assert updated_state.observed_position_size_pct == 0.25
    assert updated_state.recent_fill_summary == {
        "status": "simulated",
        "accepted": True,
        "simulated": True,
        "details": {
            "fill_count": 2,
            "latest_trade_id": "12",
        },
    }


def test_shadow_orchestrator_passes_simulated_real_runtime_to_adapter(tmp_path: Path) -> None:
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


def test_shadow_orchestrator_audit_log_includes_recent_fill_summary(tmp_path: Path) -> None:
    payload = EngineCyclePayload(
        judgement={
            "status": "ok",
            "diagnostic": "",
            "research_bundle": {"ready": True, "bundle_status": "healthy"},
        },
        handoff={
            "generated_at": "2026-04-26T12:45:00",
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
        runtime_mode=RuntimeMode.SIMULATED_REAL,
        state_store_path=tmp_path / "state.json",
        audit_log_path=tmp_path / "audit.jsonl",
        artifacts_root=tmp_path / "runtime",
    )
    state_store = StateStore(config.state_store_path)
    state = state_store.load()
    state.observed_position_state = "ENTERED"
    state.observed_position_direction = "long"
    state.observed_position_size_pct = 0.3
    state.recovery_required = True
    state.reconciliation_required = True
    state_store.save(state)
    adapter = FakeExchangeAdapter(
        capabilities=AdapterCapabilities(
            supports_real_execution=True,
            supports_recent_fill_sync=True,
            supports_trailing_stop_update=True,
            supports_breakeven_update=True,
        ),
        snapshot=AdapterRuntimeSnapshot(
            position=PositionSnapshot(position_state="ENTERED", direction="long", size_pct=0.25),
            protective_stop_present=True,
        ),
        reconciliation=ReconciliationResult(
            in_sync=False,
            protective_stop_present=True,
            needs_position_sync=True,
            needs_order_sync=False,
            reason_codes=["position_size_mismatch"],
        ),
        execution_results=[
            CommandExecutionResult(
                target="sync_recent_fills",
                status="simulated",
                accepted=True,
                simulated=True,
                reason="recent_fill_sync_required",
                details={
                    "response_summary": {
                        "fill_count": 2,
                        "latest_trade_id": "12",
                    },
                    "idempotency_key": "fills-1",
                },
            ),
            CommandExecutionResult(
                target="reconcile_position_and_orders",
                status="simulated",
                accepted=True,
                simulated=True,
                reason="reconciliation_required",
                details={
                    "response_summary": {
                        "position_state": "ENTERED",
                        "direction": "long",
                        "size_pct": 0.25,
                    },
                    "idempotency_key": "reconcile-1",
                },
            ),
        ],
    )
    orchestrator = ShadowOrchestrator(
        config,
        engine_client=FakeEngineClient(payload),
        network_guard=NetworkGuard(),
        state_store=state_store,
        exchange_adapter=adapter,
    )

    orchestrator.run_cycle(generated_at=datetime(2026, 4, 26, 12, 45, 0))

    audit_event = json.loads(config.audit_log_path.read_text(encoding="utf-8").splitlines()[-1])
    assert audit_event["event_type"] == "shadow_cycle"
    assert audit_event["payload"]["execution_result_summary"] == {
        "has_failure": False,
        "primary_failed": False,
        "primary_succeeded": False,
        "auxiliary_failed": False,
        "protective_stop_failed": False,
        "capability_blocked": False,
    }
    assert audit_event["payload"]["runtime_overview"] == {
        "expected_position_state": "ENTERED",
        "expected_direction": "long",
        "expected_size_pct": 0.3,
        "runtime_position_state": "ENTERED",
        "runtime_direction": "long",
        "runtime_size_pct": 0.25,
        "runtime_protective_stop_present": True,
        "observed_position_state": "ENTERED",
        "observed_direction": "long",
        "observed_size_pct": 0.25,
        "execution_state": "reconciling",
        "pending_action": "",
        "recovery_required": True,
        "reconciliation_required": True,
        "protective_stop_required": False,
        "reconciliation_in_sync": False,
        "reconciliation_reason_codes": ["position_size_mismatch"],
        "recent_fill_summary": {
            "status": "simulated",
            "accepted": True,
            "simulated": True,
            "details": {
                "fill_count": 2,
                "latest_trade_id": "12",
            },
        },
    }
    assert audit_event["payload"]["recent_fill_summary"] == {
        "status": "simulated",
        "accepted": True,
        "simulated": True,
        "details": {
            "fill_count": 2,
            "latest_trade_id": "12",
        },
    }


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
            "execution_allowed": True,
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
                    reason="effective_action:entry_long",
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
    assert report.execution_overview == {
        "requested_action": "entry_long",
        "effective_action": "entry_long",
        "primary_target": "entry_order",
        "primary_reason": "effective_action:entry_long",
        "primary_status": "simulated",
        "primary_accepted": True,
        "has_primary_failure": False,
        "has_auxiliary_failure": True,
        "auxiliary_targets": ["maintain_protective_stop"],
    }
    assert report.runtime_overview == {
        "expected_position_state": "FLAT",
        "expected_direction": "neutral",
        "expected_size_pct": 0.0,
        "runtime_position_state": "FLAT",
        "runtime_direction": "neutral",
        "runtime_size_pct": 0.0,
        "runtime_protective_stop_present": False,
        "observed_position_state": "FLAT",
        "observed_direction": "neutral",
        "observed_size_pct": 0.0,
        "execution_state": "reconciling",
        "pending_action": "",
        "recovery_required": True,
        "reconciliation_required": True,
        "protective_stop_required": True,
        "reconciliation_in_sync": True,
        "reconciliation_reason_codes": [],
        "recent_fill_summary": {},
    }
    assert updated_state.execution_state.value == "reconciling"
    assert updated_state.pending_action == ""
    assert updated_state.recovery_required is True
    assert updated_state.reconciliation_required is True
    assert updated_state.protective_stop_required is True
    audit_event = json.loads(config.audit_log_path.read_text(encoding="utf-8").splitlines()[-1])
    assert audit_event["event_type"] == "shadow_cycle"
    assert audit_event["payload"]["execution_result_summary"] == {
        "has_failure": True,
        "primary_failed": False,
        "primary_succeeded": True,
        "auxiliary_failed": True,
        "protective_stop_failed": True,
        "capability_blocked": False,
    }
    assert audit_event["payload"]["execution_overview"] == report.execution_overview
    assert audit_event["payload"]["runtime_overview"] == report.runtime_overview


def test_shadow_orchestrator_keeps_entered_state_as_open_risk_without_runtime_size(tmp_path: Path) -> None:
    payload = EngineCyclePayload(
        judgement={
            "status": "ok",
            "diagnostic": "",
            "research_bundle": {"ready": True, "bundle_status": "healthy"},
        },
        handoff={
            "generated_at": "2026-04-26T12:40:00",
            "action": "wait",
            "risk_filter_status": "pass",
            "runtime_vetoes": [],
            "degrade_flags": [],
            "staleness_veto": False,
            "conflict_veto": False,
            "position_state": "ENTERED",
            "current_position_direction": "long",
            "position_size_pct": 0.0,
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
    state.observed_position_size_pct = 0.0
    state_store.save(state)
    orchestrator = ShadowOrchestrator(
        config,
        engine_client=FakeEngineClient(payload),
        network_guard=NetworkGuard(),
        state_store=state_store,
        exchange_adapter=FakeExchangeAdapter(
            snapshot=AdapterRuntimeSnapshot(
                position=PositionSnapshot(position_state="ENTERED", direction="long", size_pct=0.0),
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
    report = orchestrator.run_cycle(generated_at=datetime(2026, 4, 26, 12, 40, 0))
    assert report.reconciliation_in_sync is False
    updated_state = state_store.load()
    assert updated_state.protective_stop_required is True
    assert updated_state.observed_position_state == "ENTERED"



def test_shadow_orchestrator_does_not_schedule_recent_fill_sync_for_protective_stop_only_gap(tmp_path: Path) -> None:
    payload = EngineCyclePayload(
        judgement={
            "status": "ok",
            "diagnostic": "",
            "research_bundle": {"ready": True, "bundle_status": "healthy"},
        },
        handoff={
            "generated_at": "2026-04-26T12:39:30",
            "action": "wait",
            "risk_filter_status": "pass",
            "runtime_vetoes": [],
            "degrade_flags": [],
            "staleness_veto": False,
            "conflict_veto": False,
            "position_state": "ENTERED",
            "current_position_direction": "long",
            "position_size_pct": 0.0,
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
    state.observed_position_size_pct = 0.0
    state_store.save(state)
    orchestrator = ShadowOrchestrator(
        config,
        engine_client=FakeEngineClient(payload),
        network_guard=NetworkGuard(),
        state_store=state_store,
        exchange_adapter=FakeExchangeAdapter(
            snapshot=AdapterRuntimeSnapshot(
                position=PositionSnapshot(position_state="ENTERED", direction="long", size_pct=0.0),
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
    report = orchestrator.run_cycle(generated_at=datetime(2026, 4, 26, 12, 39, 30))
    assert report.reconciliation_in_sync is False
    assert report.command_reasons == [
        "protective_stop_required",
        "reconciliation_required",
    ]
    assert report.command_types == [
        "maintain_protective_stop",
        "reconcile_position_and_orders",
    ]


    payload = EngineCyclePayload(
        judgement={
            "status": "ok",
            "diagnostic": "",
            "research_bundle": {"ready": True, "bundle_status": "healthy"},
        },
        handoff={
            "generated_at": "2026-04-26T12:40:00",
            "action": "wait",
            "risk_filter_status": "pass",
            "runtime_vetoes": [],
            "degrade_flags": [],
            "staleness_veto": False,
            "conflict_veto": False,
            "position_state": "ENTERED",
            "current_position_direction": "long",
            "position_size_pct": 0.0,
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
    state.observed_position_size_pct = 0.0
    state_store.save(state)
    orchestrator = ShadowOrchestrator(
        config,
        engine_client=FakeEngineClient(payload),
        network_guard=NetworkGuard(),
        state_store=state_store,
        exchange_adapter=FakeExchangeAdapter(
            snapshot=AdapterRuntimeSnapshot(
                position=PositionSnapshot(position_state="ENTERED", direction="long", size_pct=0.0),
                protective_stop_present=False,
            ),
            reconciliation=ReconciliationResult(
                in_sync=False,
                protective_stop_present=False,
                needs_position_sync=True,
                needs_order_sync=False,
                reason_codes=["position_size_mismatch"],
            ),
        ),
    )
    report = orchestrator.run_cycle(generated_at=datetime(2026, 4, 26, 12, 40, 0))
    assert report.reconciliation_in_sync is False
    assert report.command_reasons == [
        "protective_stop_required",
        "recent_fill_sync_required",
        "reconciliation_required",
    ]
    assert report.command_types == [
        "maintain_protective_stop",
        "sync_recent_fills",
        "reconcile_position_and_orders",
    ]
    updated_state = state_store.load()
    assert updated_state.protective_stop_required is True
    assert updated_state.observed_position_state == "ENTERED"



def test_shadow_orchestrator_preserves_open_risk_when_runtime_snapshot_is_invalid(tmp_path: Path) -> None:
    payload = EngineCyclePayload(
        judgement={
            "status": "ok",
            "diagnostic": "",
            "research_bundle": {"ready": True, "bundle_status": "healthy"},
        },
        handoff={
            "generated_at": "2026-04-26T12:41:00",
            "action": "wait",
            "risk_filter_status": "pass",
            "runtime_vetoes": [],
            "degrade_flags": [],
            "staleness_veto": False,
            "conflict_veto": False,
            "position_state": "ENTERED",
            "current_position_direction": "long",
            "position_size_pct": 0.0,
            "initial_stop_loss": 0.97,
            "breakeven_trigger": 1.01,
            "trailing_rule": "trail_with_trigger",
            "trailing_activation_ratio": 1.01,
            "trailing_callback_rate_pct": 0.5,
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
                position=PositionSnapshot(position_state="FLAT", direction="neutral", size_pct=0.0),
                protective_stop_present=False,
                snapshot_valid=False,
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

    report = orchestrator.run_cycle(generated_at=datetime(2026, 4, 26, 12, 41, 0))

    assert report.command_reasons == [
        "protective_stop_required",
        "breakeven_ready",
        "trailing_ready",
        "reconciliation_required",
    ]
    assert report.command_types == [
        "maintain_protective_stop",
        "advance_breakeven_stop",
        "advance_trailing_stop",
        "reconcile_position_and_orders",
    ]
    updated_state = state_store.load()
    assert updated_state.observed_position_state == "ENTERED"
    assert updated_state.observed_position_direction == "long"
    assert updated_state.observed_position_size_pct == 0.3
