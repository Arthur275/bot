import json
from pathlib import Path

import pytest

from bot.atomic_io import atomic_write_json
from bot.exchange_adapter import AdapterRuntimeSnapshot, CommandExecutionResult, PositionSnapshot
from bot.network_guard import GuardDecision
from bot.automation_state import AutomationState
from bot.state_store import BotRuntimeState, ExecutionLayerState, StateStore


def test_atomic_write_json_preserves_existing_file_on_replace_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "state.json"
    path.write_text('{"status": "old"}', encoding="utf-8")

    def fail_replace(_src, _dst):
        raise OSError("replace failed")

    monkeypatch.setattr("bot.atomic_io.os.replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        atomic_write_json(path, {"status": "new"})

    assert json.loads(path.read_text(encoding="utf-8")) == {"status": "old"}
    assert list(tmp_path.glob(".*.tmp")) == []


def test_state_store_records_shadow_cycle_and_persists_state(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.json")
    state = store.load()
    updated = store.record_shadow_cycle(
        state=state,
        judgement={"status": "ok"},
        handoff={
            "generated_at": "2026-04-26T12:00:00",
            "action": "entry_long",
            "position_state": "ARMED",
            "current_position_direction": "neutral",
            "position_size_pct": 0.0,
        },
        guard=GuardDecision(
            judgement_status="ok",
            allow_entry=True,
            allow_reduce=True,
            allow_exit=True,
            degraded=False,
            blocked=False,
        ),
        effective_action="entry_long",
        plan_reason="quant_action_passthrough",
        needs_reconciliation=False,
        maintain_protective_stop=True,
    )
    assert updated.execution_state is ExecutionLayerState.ENTRY_PENDING
    assert updated.pending_action == "entry_long"
    assert updated.last_plan_reason == "quant_action_passthrough"
    assert updated.protective_stop_required is True
    assert store.load().pending_action == "entry_long"


def test_state_store_marks_blocked_state(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.json")
    updated = store.record_shadow_cycle(
        state=store.load(),
        judgement={"status": "blocked"},
        handoff=None,
        guard=GuardDecision(
            judgement_status="blocked",
            allow_entry=False,
            allow_reduce=False,
            allow_exit=False,
            degraded=False,
            blocked=True,
            reason_codes=["pipeline_blocked"],
        ),
        effective_action="wait",
        plan_reason="blocked_by_guard",
        needs_reconciliation=True,
        maintain_protective_stop=False,
    )
    assert updated.execution_state is ExecutionLayerState.BLOCKED
    assert updated.automation_state is AutomationState.ACTION_BLOCKED
    assert updated.recovery_required is True
    assert updated.reconciliation_required is True


def test_state_store_records_api_failures_and_enters_degraded_after_threshold(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.json")
    first = store.record_api_failure(reason_code="fetch_position_timeout")
    second = store.record_api_failure(state=first, reason_code="open_algo_orders_timeout")
    third = store.record_api_failure(state=second, reason_code="submit_response_unknown")

    assert first.consecutive_api_failure_count == 1
    assert second.consecutive_api_failure_count == 2
    assert third.consecutive_api_failure_count == 3
    assert third.execution_state is ExecutionLayerState.DEGRADED
    assert third.automation_state is AutomationState.ACTION_BLOCKED
    assert third.recovery_required is True
    assert third.last_api_failure_at
    assert "submit_response_unknown" in third.last_reason_codes


def test_state_store_api_success_clears_failure_count_and_recovers_idle_degraded_state(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.json")
    degraded = store.record_api_failure(reason_code="fetch_position_timeout", degraded_threshold=1)

    recovered = store.record_api_success(state=degraded)

    assert recovered.consecutive_api_failure_count == 0
    assert recovered.last_api_failure_at == ""
    assert recovered.execution_state is ExecutionLayerState.IDLE


def test_state_store_marks_reconciling_state_when_runtime_needs_recovery(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.json")
    updated = store.record_shadow_cycle(
        state=store.load(),
        judgement={"status": "ok"},
        handoff={
            "generated_at": "2026-04-26T12:05:00",
            "action": "wait",
            "position_state": "ENTERED",
            "current_position_direction": "long",
            "position_size_pct": 0.3,
        },
        guard=GuardDecision(
            judgement_status="ok",
            allow_entry=True,
            allow_reduce=True,
            allow_exit=True,
            degraded=False,
            blocked=False,
        ),
        effective_action="wait",
        plan_reason="quant_action_passthrough",
        needs_reconciliation=True,
        maintain_protective_stop=True,
        runtime_snapshot=AdapterRuntimeSnapshot(
            position=PositionSnapshot(position_state="ENTERED", direction="long", size_pct=0.3),
            protective_stop_present=True,
        ),
    )
    assert updated.execution_state is ExecutionLayerState.RECONCILING
    assert updated.automation_state is AutomationState.ACTION_BLOCKED
    assert updated.recovery_required is True
    assert updated.reconciliation_required is True
    assert updated.protective_stop_required is True


def test_state_store_persists_automation_state_for_allowed_execution_pair(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.json")
    state = BotRuntimeState(automation_state=AutomationState.OBSERVING)
    updated = store.record_shadow_cycle(
        state=state,
        judgement={"status": "ok"},
        handoff={"generated_at": "2026-04-26T12:06:00", "action": "wait"},
        guard=GuardDecision(judgement_status="ok"),
        effective_action="wait",
    )

    assert updated.execution_state is ExecutionLayerState.IDLE
    assert updated.automation_state is AutomationState.OBSERVING
    assert store.load().automation_state is AutomationState.OBSERVING


def test_state_store_collects_top_level_idempotency_key_before_legacy_details(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.json")
    updated = store.record_shadow_cycle(
        state=store.load(),
        judgement={"status": "ok"},
        handoff={"generated_at": "2026-04-26T12:07:00", "action": "wait"},
        guard=GuardDecision(judgement_status="ok"),
        effective_action="wait",
        execution_results=[
            CommandExecutionResult(
                target="entry_order",
                status="simulated",
                idempotency_key="top-level-key",
                details={"idempotency_key": "legacy-details-key"},
            )
        ],
    )

    assert updated.recent_idempotency_keys == ["top-level-key"]


def test_state_store_does_not_recover_on_expected_tightening_capability_block(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.json")
    state = store.load()
    state.observed_position_state = "ENTERED"
    state.observed_position_direction = "long"
    state.observed_position_size_pct = 0.3
    updated = store.record_shadow_cycle(
        state=state,
        judgement={"status": "ok"},
        handoff={
            "generated_at": "2026-04-26T12:05:00",
            "action": "wait",
            "position_state": "ENTERED",
            "current_position_direction": "long",
            "position_size_pct": 0.3,
        },
        guard=GuardDecision(
            judgement_status="ok",
            allow_entry=True,
            allow_reduce=True,
            allow_exit=True,
            degraded=False,
            blocked=False,
        ),
        effective_action="wait",
        plan_reason="quant_action_passthrough",
        needs_reconciliation=False,
        maintain_protective_stop=False,
        execution_results=[
            CommandExecutionResult(
                target="advance_breakeven_stop",
                status="error",
                accepted=False,
                simulated=True,
                reason="unsafe_request_mapping",
                details={
                    "error": "Real breakeven stop replace requires Binance Algo stop cancel/replace support",
                },
            )
        ],
        runtime_snapshot=AdapterRuntimeSnapshot(
            position=PositionSnapshot(position_state="ENTERED", direction="long", size_pct=0.3),
            protective_stop_present=True,
        ),
    )
    assert updated.execution_state is ExecutionLayerState.POSITION_OPEN
    assert updated.pending_action == ""
    assert updated.recovery_required is False
    assert updated.reconciliation_required is False
    assert updated.protective_stop_required is False


def test_state_store_marks_failed_execution_for_recovery(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.json")
    updated = store.record_shadow_cycle(
        state=store.load(),
        judgement={"status": "ok"},
        handoff={
            "generated_at": "2026-04-26T12:10:00",
            "action": "entry_long",
            "position_state": "ARMED",
            "current_position_direction": "neutral",
            "position_size_pct": 0.0,
        },
        guard=GuardDecision(
            judgement_status="ok",
            allow_entry=True,
            allow_reduce=True,
            allow_exit=True,
            degraded=False,
            blocked=False,
        ),
        effective_action="entry_long",
        plan_reason="quant_action_passthrough",
        execution_results=[
            CommandExecutionResult(
                target="entry_order",
                status="rejected",
                accepted=False,
                simulated=True,
                reason="quant_action_passthrough",
            )
        ],
    )
    assert updated.execution_state is ExecutionLayerState.ENTRY_PENDING
    assert updated.pending_action == "entry_long"
    assert updated.recovery_required is True
    assert updated.reconciliation_required is False


def test_state_store_marks_protective_stop_failure_for_reconciliation(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.json")
    updated = store.record_shadow_cycle(
        state=store.load(),
        judgement={"status": "ok"},
        handoff={
            "generated_at": "2026-04-26T12:12:00",
            "action": "entry_long",
            "position_state": "ARMED",
            "current_position_direction": "neutral",
            "position_size_pct": 0.0,
        },
        guard=GuardDecision(
            judgement_status="ok",
            allow_entry=True,
            allow_reduce=True,
            allow_exit=True,
            degraded=False,
            blocked=False,
        ),
        effective_action="entry_long",
        plan_reason="quant_action_passthrough",
        maintain_protective_stop=True,
        execution_results=[
            CommandExecutionResult(
                target="entry_order",
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
        ],
    )
    assert updated.execution_state is ExecutionLayerState.RECONCILING
    assert updated.pending_action == ""
    assert updated.recovery_required is True
    assert updated.reconciliation_required is True
    assert updated.protective_stop_required is True


def test_state_store_marks_position_open_from_runtime_snapshot(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.json")
    updated = store.record_shadow_cycle(
        state=store.load(),
        judgement={"status": "ok"},
        handoff={
            "generated_at": "2026-04-26T12:15:00",
            "action": "wait",
            "position_state": "ENTERED",
            "current_position_direction": "long",
            "position_size_pct": 0.3,
        },
        guard=GuardDecision(
            judgement_status="ok",
            allow_entry=True,
            allow_reduce=True,
            allow_exit=True,
            degraded=False,
            blocked=False,
        ),
        effective_action="wait",
        plan_reason="quant_action_passthrough",
        runtime_snapshot=AdapterRuntimeSnapshot(
            position=PositionSnapshot(position_state="ENTERED", direction="long", size_pct=0.3),
            protective_stop_present=True,
        ),
    )
    assert updated.execution_state is ExecutionLayerState.POSITION_OPEN
    assert updated.observed_position_state == "ENTERED"
    assert updated.observed_position_direction == "long"
    assert updated.observed_position_size_pct == 0.3


def test_state_store_clears_entry_pending_after_successful_entry_snapshot(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.json")
    updated = store.record_shadow_cycle(
        state=store.load(),
        judgement={"status": "ok"},
        handoff={
            "generated_at": "2026-04-26T12:16:00",
            "action": "entry_long",
            "position_state": "ARMED",
            "current_position_direction": "neutral",
            "position_size_pct": 0.0,
        },
        guard=GuardDecision(
            judgement_status="ok",
            allow_entry=True,
            allow_reduce=True,
            allow_exit=True,
            degraded=False,
            blocked=False,
        ),
        effective_action="entry_long",
        execution_results=[
            CommandExecutionResult(
                target="entry_order",
                status="accepted",
                accepted=True,
                simulated=False,
                reason="quant_action_passthrough",
            )
        ],
        runtime_snapshot=AdapterRuntimeSnapshot(
            position=PositionSnapshot(position_state="ENTERED", direction="long", size_pct=0.3),
            protective_stop_present=False,
        ),
    )
    assert updated.execution_state is ExecutionLayerState.POSITION_OPEN
    assert updated.pending_action == ""
    assert updated.recovery_required is False


def test_state_store_keeps_entry_pending_when_success_still_needs_reconciliation(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.json")
    updated = store.record_shadow_cycle(
        state=store.load(),
        judgement={"status": "ok"},
        handoff={
            "generated_at": "2026-04-26T12:16:30",
            "action": "entry_long",
            "position_state": "ARMED",
            "current_position_direction": "neutral",
            "position_size_pct": 0.0,
        },
        guard=GuardDecision(
            judgement_status="ok",
            allow_entry=True,
            allow_reduce=True,
            allow_exit=True,
            degraded=False,
            blocked=False,
        ),
        effective_action="entry_long",
        needs_reconciliation=True,
        execution_results=[
            CommandExecutionResult(
                target="entry_order",
                status="accepted",
                accepted=True,
                simulated=False,
                reason="quant_action_passthrough",
            )
        ],
        runtime_snapshot=AdapterRuntimeSnapshot(
            position=PositionSnapshot(position_state="ENTERED", direction="long", size_pct=0.3),
            protective_stop_present=False,
        ),
    )
    assert updated.execution_state is ExecutionLayerState.RECONCILING
    assert updated.pending_action == "entry_long"


def test_state_store_clears_exit_pending_after_successful_flat_snapshot(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.json")
    state = store.load()
    state.observed_position_state = "ENTERED"
    state.observed_position_direction = "long"
    state.observed_position_size_pct = 0.3
    updated = store.record_shadow_cycle(
        state=state,
        judgement={"status": "ok"},
        handoff={
            "generated_at": "2026-04-26T12:17:00",
            "action": "exit",
            "position_state": "ENTERED",
            "current_position_direction": "long",
            "position_size_pct": 0.3,
        },
        guard=GuardDecision(
            judgement_status="ok",
            allow_entry=True,
            allow_reduce=True,
            allow_exit=True,
            degraded=False,
            blocked=False,
        ),
        effective_action="exit",
        execution_results=[
            CommandExecutionResult(
                target="exit_order",
                status="accepted",
                accepted=True,
                simulated=False,
                reason="quant_action_passthrough",
            )
        ],
        runtime_snapshot=AdapterRuntimeSnapshot(
            position=PositionSnapshot(position_state="FLAT", direction="neutral", size_pct=0.0),
            protective_stop_present=False,
        ),
    )
    assert updated.execution_state is ExecutionLayerState.IDLE
    assert updated.pending_action == ""
    assert updated.observed_position_state == "FLAT"
    assert updated.observed_position_size_pct == 0.0



def test_state_store_marks_exit_reconciling_when_auxiliary_command_fails_after_flattening(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.json")
    state = store.load()
    state.observed_position_state = "ENTERED"
    state.observed_position_direction = "long"
    state.observed_position_size_pct = 0.3
    updated = store.record_shadow_cycle(
        state=state,
        judgement={"status": "ok"},
        handoff={
            "generated_at": "2026-04-26T12:17:15",
            "action": "exit",
            "position_state": "ENTERED",
            "current_position_direction": "long",
            "position_size_pct": 0.3,
        },
        guard=GuardDecision(
            judgement_status="ok",
            allow_entry=True,
            allow_reduce=True,
            allow_exit=True,
            degraded=False,
            blocked=False,
        ),
        effective_action="exit",
        execution_results=[
            CommandExecutionResult(
                target="exit_order",
                status="accepted",
                accepted=True,
                simulated=False,
                reason="quant_action_passthrough",
            ),
            CommandExecutionResult(
                target="reconcile_position_and_orders",
                status="failed",
                accepted=False,
                simulated=False,
                reason="reconciliation_required",
            ),
        ],
        runtime_snapshot=AdapterRuntimeSnapshot(
            position=PositionSnapshot(position_state="FLAT", direction="neutral", size_pct=0.0),
            protective_stop_present=False,
        ),
    )
    assert updated.execution_state is ExecutionLayerState.RECONCILING
    assert updated.pending_action == ""
    assert updated.recovery_required is True
    assert updated.reconciliation_required is True
    assert updated.protective_stop_required is False
    assert updated.observed_position_state == "FLAT"
    assert updated.observed_position_size_pct == 0.0



def test_state_store_clears_reduce_pending_after_reconciled_size_drop(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.json")
    state = store.load()
    state.observed_position_state = "ENTERED"
    state.observed_position_direction = "long"
    state.observed_position_size_pct = 0.3
    updated = store.record_shadow_cycle(
        state=state,
        judgement={"status": "ok"},
        handoff={
            "generated_at": "2026-04-26T12:18:00",
            "action": "reduce",
            "position_state": "ENTERED",
            "current_position_direction": "long",
            "position_size_pct": 0.3,
        },
        guard=GuardDecision(
            judgement_status="ok",
            allow_entry=True,
            allow_reduce=True,
            allow_exit=True,
            degraded=False,
            blocked=False,
        ),
        effective_action="reduce",
        execution_results=[
            CommandExecutionResult(
                target="reduce_order",
                status="accepted",
                accepted=True,
                simulated=False,
                reason="quant_action_passthrough",
            ),
            CommandExecutionResult(
                target="reconcile_position_and_orders",
                status="accepted",
                accepted=True,
                simulated=False,
                details={
                    "response_summary": {
                        "position_state": "ENTERED",
                        "direction": "long",
                        "size_pct": 0.15,
                    }
                },
            ),
        ],
    )
    assert updated.execution_state is ExecutionLayerState.POSITION_OPEN
    assert updated.pending_action == ""
    assert updated.observed_position_size_pct == 0.15



def test_state_store_marks_reduce_reconciling_when_auxiliary_command_fails_after_size_drop(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.json")
    state = store.load()
    state.observed_position_state = "ENTERED"
    state.observed_position_direction = "long"
    state.observed_position_size_pct = 0.3
    updated = store.record_shadow_cycle(
        state=state,
        judgement={"status": "ok"},
        handoff={
            "generated_at": "2026-04-26T12:18:15",
            "action": "reduce",
            "position_state": "ENTERED",
            "current_position_direction": "long",
            "position_size_pct": 0.3,
        },
        guard=GuardDecision(
            judgement_status="ok",
            allow_entry=True,
            allow_reduce=True,
            allow_exit=True,
            degraded=False,
            blocked=False,
        ),
        effective_action="reduce",
        execution_results=[
            CommandExecutionResult(
                target="reduce_order",
                status="accepted",
                accepted=True,
                simulated=False,
                reason="quant_action_passthrough",
            ),
            CommandExecutionResult(
                target="maintain_protective_stop",
                status="failed",
                accepted=False,
                simulated=False,
                reason="protective_stop_required",
            ),
        ],
        runtime_snapshot=AdapterRuntimeSnapshot(
            position=PositionSnapshot(position_state="ENTERED", direction="long", size_pct=0.15),
            protective_stop_present=False,
        ),
    )
    assert updated.execution_state is ExecutionLayerState.RECONCILING
    assert updated.pending_action == ""
    assert updated.recovery_required is True
    assert updated.reconciliation_required is True
    assert updated.protective_stop_required is True
    assert updated.observed_position_state == "ENTERED"
    assert updated.observed_position_size_pct == 0.15



def test_state_store_persists_recent_idempotency_keys(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.json")
    state = store.load()
    state.recent_idempotency_keys = [f"older-{index}" for index in range(19)]
    updated = store.record_shadow_cycle(
        state=state,
        judgement={"status": "ok"},
        handoff={"generated_at": "2026-04-26T12:20:00", "action": "wait"},
        guard=GuardDecision(
            judgement_status="ok",
            allow_entry=True,
            allow_reduce=True,
            allow_exit=True,
            degraded=False,
            blocked=False,
        ),
        effective_action="wait",
        execution_results=[
            CommandExecutionResult(
                target="sync_recent_fills",
                status="simulated",
                accepted=True,
                simulated=True,
                details={"idempotency_key": "older-18"},
            ),
            CommandExecutionResult(
                target="reduce_order",
                status="simulated",
                accepted=True,
                simulated=True,
                details={"idempotency_key": "new-1"},
            ),
            CommandExecutionResult(
                target="maintain_protective_stop",
                status="simulated",
                accepted=True,
                simulated=True,
                details={"idempotency_key": "new-2"},
            ),
        ],
    )
    assert len(updated.recent_idempotency_keys) == 20
    assert updated.recent_idempotency_keys[-2:] == ["new-1", "new-2"]
    assert updated.recent_idempotency_keys.count("older-18") == 1


def test_state_store_records_recent_fill_summary(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.json")
    updated = store.record_shadow_cycle(
        state=store.load(),
        judgement={"status": "ok"},
        handoff={"generated_at": "2026-04-26T12:25:00", "action": "wait"},
        guard=GuardDecision(
            judgement_status="ok",
            allow_entry=True,
            allow_reduce=True,
            allow_exit=True,
            degraded=False,
            blocked=False,
        ),
        effective_action="wait",
        execution_results=[
            CommandExecutionResult(
                target="sync_recent_fills",
                status="failed",
                accepted=False,
                simulated=True,
                details={"fill_count": 0},
            ),
            CommandExecutionResult(
                target="sync_recent_fills",
                status="simulated",
                accepted=True,
                simulated=True,
                details={"fill_count": 3, "latest_trade_id": "abc123"},
            ),
        ],
    )
    assert updated.recent_fill_summary == {
        "status": "simulated",
        "accepted": True,
        "simulated": True,
        "details": {"fill_count": 3, "latest_trade_id": "abc123"},
    }


def test_state_store_records_active_contrarian_probe_metadata(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.json")
    updated = store.record_shadow_cycle(
        state=store.load(),
        judgement={"status": "ok"},
        handoff={
            "generated_at": "2026-05-02T03:00:00",
            "action": "small_probe",
            "position_state": "ENTERED",
            "current_position_direction": "short",
            "position_size_pct": 0.0025,
            "probe_source": "contrarian_short_probe",
            "probe_expiry_bars": 4,
            "probe_expiry_timeframe": "15m",
            "probe_invalid_if_no_followthrough": True,
            "probe_risk_tier": "technical",
        },
        guard=GuardDecision(
            judgement_status="ok",
            allow_entry=True,
            allow_reduce=True,
            allow_exit=True,
            degraded=False,
            blocked=False,
        ),
        effective_action="small_probe",
        execution_results=[
            CommandExecutionResult(
                target="entry_order",
                status="simulated",
                accepted=True,
                simulated=True,
            )
        ],
        runtime_snapshot=AdapterRuntimeSnapshot(snapshot_valid=False),
    )

    assert updated.observed_position_state == "ENTERED"
    assert updated.metadata["active_probe_source"] == "contrarian_short_probe"
    assert updated.metadata["active_probe_started_at"] == "2026-05-02T03:00:00+00:00"
    assert updated.metadata["active_probe_expires_at"] == "2026-05-02T04:00:00+00:00"
    assert updated.metadata["active_probe_expiry_bars"] == 4
    assert updated.metadata["active_probe_expiry_timeframe"] == "15m"
    assert updated.metadata["active_probe_invalid_if_no_followthrough"] is True
    assert updated.metadata["active_probe_risk_tier"] == "technical"


def test_state_store_records_active_trigger_ready_probe_metadata(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.json")
    updated = store.record_shadow_cycle(
        state=store.load(),
        judgement={"status": "ok"},
        handoff={
            "generated_at": "2026-05-02T03:00:00",
            "action": "small_probe",
            "position_state": "ENTERED",
            "current_position_direction": "long",
            "position_size_pct": 0.10,
            "probe_source": "trigger_ready_small_probe",
            "probe_expiry_bars": 3,
            "probe_expiry_timeframe": "15m",
            "probe_invalid_if_no_followthrough": True,
            "probe_risk_tier": "trigger_ready",
            "invalidate_conditions": [
                "trigger_ready_long_failed_followthrough",
                "trigger_reversal_15m",
                "no_followthrough_after_3x15m",
                "hard_risk_veto",
            ],
        },
        guard=GuardDecision(
            judgement_status="ok",
            allow_entry=True,
            allow_reduce=True,
            allow_exit=True,
            degraded=False,
            blocked=False,
        ),
        effective_action="small_probe",
        execution_results=[
            CommandExecutionResult(
                target="entry_order",
                status="simulated",
                accepted=True,
                simulated=True,
            )
        ],
        runtime_snapshot=AdapterRuntimeSnapshot(snapshot_valid=False),
    )

    assert updated.observed_position_state == "ENTERED"
    assert updated.metadata["active_probe_source"] == "trigger_ready_small_probe"
    assert updated.metadata["active_probe_started_at"] == "2026-05-02T03:00:00+00:00"
    assert updated.metadata["active_probe_expires_at"] == "2026-05-02T03:45:00+00:00"
    assert updated.metadata["active_probe_expiry_bars"] == 3
    assert updated.metadata["active_probe_expiry_timeframe"] == "15m"
    assert updated.metadata["active_probe_invalid_if_no_followthrough"] is True
    assert updated.metadata["active_probe_risk_tier"] == "trigger_ready"
    assert updated.metadata["active_probe_invalidate_conditions"] == [
        "trigger_ready_long_failed_followthrough",
        "trigger_reversal_15m",
        "no_followthrough_after_3x15m",
        "hard_risk_veto",
    ]


def test_state_store_clears_active_probe_metadata_when_flat(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.json")
    state = store.load()
    state.observed_position_state = "ENTERED"
    state.observed_position_direction = "short"
    state.observed_position_size_pct = 0.0025
    state.metadata = {
        "active_probe_source": "contrarian_short_probe",
        "active_probe_expires_at": "2026-05-02T04:00:00",
    }

    updated = store.record_shadow_cycle(
        state=state,
        judgement={"status": "ok"},
        handoff={"generated_at": "2026-05-02T04:05:00", "action": "wait"},
        guard=GuardDecision(
            judgement_status="ok",
            allow_entry=True,
            allow_reduce=True,
            allow_exit=True,
            degraded=False,
            blocked=False,
        ),
        effective_action="wait",
        execution_results=[
            CommandExecutionResult(
                target="reconcile_position_and_orders",
                status="accepted",
                accepted=True,
                simulated=False,
                details={"response_summary": {"position_state": "FLAT", "direction": "neutral"}},
            )
        ],
    )

    assert updated.observed_position_state == "FLAT"
    assert "active_probe_source" not in updated.metadata
    assert "active_probe_expires_at" not in updated.metadata
    assert "active_probe_invalidate_conditions" not in updated.metadata


def test_state_store_clears_active_probe_metadata_when_exit_order_is_accepted(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.json")
    state = store.load()
    state.observed_position_state = "ENTERED"
    state.observed_position_direction = "long"
    state.observed_position_size_pct = 0.08
    state.metadata = {
        "active_probe_source": "trigger_ready_small_probe",
        "active_probe_started_at": "2026-05-20T03:00:00+00:00",
        "active_probe_expires_at": "2026-05-20T04:00:00+00:00",
        "active_probe_invalidate_conditions": [
            "trigger_ready_long_failed_followthrough",
            "trigger_reversal_15m",
            "no_followthrough_after_3x15m",
            "hard_risk_veto",
        ],
    }

    updated = store.record_shadow_cycle(
        state=state,
        judgement={"status": "ok"},
        handoff={"generated_at": "2026-05-20T03:46:00", "action": "wait"},
        guard=GuardDecision(
            judgement_status="ok",
            allow_entry=True,
            allow_reduce=True,
            allow_exit=True,
            degraded=False,
            blocked=False,
        ),
        effective_action="exit",
        plan_reason="trigger_ready_probe_invalidated",
        execution_results=[
            CommandExecutionResult(
                target="exit_order",
                status="simulated",
                accepted=True,
                simulated=True,
            )
        ],
        runtime_snapshot=AdapterRuntimeSnapshot(
            position=PositionSnapshot(position_state="ENTERED", direction="long", size_pct=0.08),
            snapshot_valid=True,
        ),
    )

    assert updated.execution_state is ExecutionLayerState.EXIT_PENDING
    assert updated.pending_action == "exit"
    assert "active_probe_source" not in updated.metadata
    assert "active_probe_expires_at" not in updated.metadata
    assert "active_probe_invalidate_conditions" not in updated.metadata


def test_state_store_rolls_active_contrarian_probe_expiry_without_new_entry_order(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.json")
    state = store.load()
    state.observed_position_state = "ENTERED"
    state.observed_position_direction = "short"
    state.observed_position_size_pct = 0.0025
    state.metadata = {
        "active_probe_source": "contrarian_short_probe",
        "active_probe_started_at": "2026-05-02T03:00:00",
        "active_probe_expires_at": "2026-05-02T04:00:00",
    }

    updated = store.record_shadow_cycle(
        state=state,
        judgement={"status": "ok"},
        handoff={
            "generated_at": "2026-05-02T04:01:00",
            "action": "small_probe",
            "position_state": "ENTERED",
            "current_position_direction": "short",
            "position_size_pct": 0.0025,
            "probe_source": "contrarian_short_probe",
            "probe_expiry_bars": 4,
            "probe_expiry_timeframe": "15m",
            "probe_invalid_if_no_followthrough": True,
            "probe_risk_tier": "technical",
        },
        guard=GuardDecision(
            judgement_status="ok",
            allow_entry=True,
            allow_reduce=True,
            allow_exit=True,
            degraded=False,
            blocked=False,
        ),
        effective_action="small_probe",
        plan_reason="contrarian_probe_rolled_forward",
        execution_results=[],
    )

    assert updated.metadata["active_probe_started_at"] == "2026-05-02T04:01:00+00:00"
    assert updated.metadata["active_probe_expires_at"] == "2026-05-02T05:01:00+00:00"


def test_state_store_prefers_recent_fill_response_summary(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.json")
    updated = store.record_shadow_cycle(
        state=store.load(),
        judgement={"status": "ok"},
        handoff={"generated_at": "2026-04-26T12:26:00", "action": "wait"},
        guard=GuardDecision(
            judgement_status="ok",
            allow_entry=True,
            allow_reduce=True,
            allow_exit=True,
            degraded=False,
            blocked=False,
        ),
        effective_action="wait",
        execution_results=[
            CommandExecutionResult(
                target="sync_recent_fills",
                status="accepted",
                accepted=True,
                simulated=False,
                details={
                    "response_summary": {
                        "fill_count": 2,
                        "latest_trade_id": "12",
                        "latest_order_id": "102",
                        "latest_realized_pnl": "2.50",
                    },
                    "response_payload": [{"id": 11}, {"id": 12}],
                },
            )
        ],
    )
    assert updated.recent_fill_summary == {
        "status": "accepted",
        "accepted": True,
        "simulated": False,
        "details": {
            "fill_count": 2,
            "latest_trade_id": "12",
            "latest_order_id": "102",
            "latest_realized_pnl": "2.50",
        },
    }


def test_state_store_marks_entered_from_reconciliation_summary(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.json")
    updated = store.record_shadow_cycle(
        state=store.load(),
        judgement={"status": "ok"},
        handoff={
            "generated_at": "2026-04-26T12:30:00",
            "action": "wait",
            "position_size_pct": 0.3,
        },
        guard=GuardDecision(
            judgement_status="ok",
            allow_entry=True,
            allow_reduce=True,
            allow_exit=True,
            degraded=False,
            blocked=False,
        ),
        effective_action="wait",
        execution_results=[
            CommandExecutionResult(
                target="reconcile_position_and_orders",
                status="accepted",
                accepted=True,
                simulated=False,
                details={
                    "response_summary": {
                        "position_state": "ENTERED",
                        "direction": "long",
                        "size_pct": 0.15,
                    }
                },
            )
        ],
    )
    assert updated.observed_position_state == "ENTERED"
    assert updated.observed_position_direction == "long"
    assert updated.observed_position_size_pct == 0.15
    assert updated.execution_state is ExecutionLayerState.POSITION_OPEN


def test_state_store_marks_flat_from_reconciliation_summary(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.json")
    state = store.load()
    state.observed_position_state = "ENTERED"
    state.observed_position_direction = "short"
    state.observed_position_size_pct = 0.2
    updated = store.record_shadow_cycle(
        state=state,
        judgement={"status": "ok"},
        handoff={"generated_at": "2026-04-26T12:35:00", "action": "wait"},
        guard=GuardDecision(
            judgement_status="ok",
            allow_entry=True,
            allow_reduce=True,
            allow_exit=True,
            degraded=False,
            blocked=False,
        ),
        effective_action="wait",
        execution_results=[
            CommandExecutionResult(
                target="reconcile_position_and_orders",
                status="accepted",
                accepted=True,
                simulated=False,
                details={
                    "response_summary": {
                        "position_state": "FLAT",
                        "direction": "neutral",
                    }
                },
            )
        ],
    )
    assert updated.observed_position_state == "FLAT"
    assert updated.observed_position_direction == "neutral"
    assert updated.observed_position_size_pct == 0.0
    assert updated.execution_state is ExecutionLayerState.IDLE



def test_state_store_uses_handoff_fallback_for_state_and_direction_when_no_other_source_exists(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.json")
    updated = store.record_shadow_cycle(
        state=store.load(),
        judgement={
            "status": "ok",
            "decision": {
                "generated_at": "2026-04-26T12:37:15",
                "metadata": {"run_id": "cycle-123"},
            },
        },
        handoff={
            "generated_at": "2026-04-26T12:37:15",
            "expires_at": "2026-04-26T12:40:15",
            "source_run_id": "cycle-123",
            "action": "wait",
            "position_state": "ENTERED",
            "current_position_direction": "long",
            "position_size_pct": 0.2,
        },
        guard=GuardDecision(
            judgement_status="ok",
            allow_entry=True,
            allow_reduce=True,
            allow_exit=True,
            degraded=False,
            blocked=False,
        ),
        effective_action="wait",
        execution_results=None,
        runtime_snapshot=AdapterRuntimeSnapshot(snapshot_valid=False),
    )
    assert updated.observed_position_state == "ENTERED"
    assert updated.observed_position_direction == "long"
    assert updated.observed_position_size_pct == 0.2
    assert updated.metadata["state_source"] == "handoff_fallback"
    assert updated.metadata["handoff_fallback_source_run_id"] == "cycle-123"
    assert updated.execution_state is ExecutionLayerState.POSITION_OPEN



def test_state_store_skips_expired_handoff_fallback(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.json")
    updated = store.record_shadow_cycle(
        state=store.load(),
        judgement={"status": "ok", "decision": {"generated_at": "2026-04-26T12:37:15"}},
        handoff={
            "generated_at": "2026-04-26T12:35:00",
            "expires_at": "2026-04-26T12:36:00",
            "action": "wait",
            "position_state": "ENTERED",
            "current_position_direction": "long",
            "position_size_pct": 0.2,
        },
        guard=GuardDecision(
            judgement_status="ok",
            allow_entry=True,
            allow_reduce=True,
            allow_exit=True,
            degraded=False,
            blocked=False,
        ),
        effective_action="wait",
        execution_results=None,
        runtime_snapshot=AdapterRuntimeSnapshot(snapshot_valid=False),
    )
    assert updated.observed_position_state == "FLAT"
    assert updated.observed_position_direction == "neutral"
    assert updated.observed_position_size_pct == 0.0
    assert updated.metadata["handoff_fallback_skipped"] is True
    assert updated.metadata["handoff_fallback_skip_reason"] == "handoff_expired"
    assert updated.execution_state is ExecutionLayerState.IDLE


def test_state_store_skips_mismatched_source_run_id_handoff_fallback(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.json")
    updated = store.record_shadow_cycle(
        state=store.load(),
        judgement={
            "status": "ok",
            "decision": {
                "generated_at": "2026-04-26T12:37:15",
                "metadata": {"run_id": "cycle-current"},
            },
        },
        handoff={
            "generated_at": "2026-04-26T12:37:15",
            "expires_at": "2026-04-26T12:40:15",
            "source_run_id": "cycle-old",
            "action": "wait",
            "position_state": "ENTERED",
            "current_position_direction": "long",
            "position_size_pct": 0.2,
        },
        guard=GuardDecision(
            judgement_status="ok",
            allow_entry=True,
            allow_reduce=True,
            allow_exit=True,
            degraded=False,
            blocked=False,
        ),
        effective_action="wait",
        execution_results=None,
        runtime_snapshot=AdapterRuntimeSnapshot(snapshot_valid=False),
    )
    assert updated.observed_position_state == "FLAT"
    assert updated.observed_position_direction == "neutral"
    assert updated.observed_position_size_pct == 0.0
    assert updated.metadata["handoff_fallback_skipped"] is True
    assert updated.metadata["handoff_fallback_skip_reason"] == "source_run_id_mismatch"
    assert updated.execution_state is ExecutionLayerState.IDLE


def test_state_store_does_not_use_blocked_entry_handoff_as_observed_position(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.json")
    updated = store.record_shadow_cycle(
        state=store.load(),
        judgement={"status": "ok"},
        handoff={
            "generated_at": "2026-04-26T12:37:20",
            "action": "small_probe",
            "position_state": "ENTERED",
            "current_position_direction": "long",
            "position_size_pct": 0.1,
        },
        guard=GuardDecision(
            judgement_status="ok",
            allow_entry=False,
            allow_reduce=True,
            allow_exit=True,
            degraded=True,
            blocked=False,
            reason_codes=["degrade_flag:research_degraded"],
        ),
        effective_action="wait",
        plan_reason="entry_disallowed_by_guard",
        execution_results=[],
        runtime_snapshot=AdapterRuntimeSnapshot(snapshot_valid=False),
    )
    assert updated.observed_position_state == "FLAT"
    assert updated.observed_position_direction == "neutral"
    assert updated.observed_position_size_pct == 0.0
    assert updated.execution_state is ExecutionLayerState.DEGRADED



def test_state_store_does_not_let_handoff_fallback_override_existing_open_position(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.json")
    state = store.load()
    state.observed_position_state = "ENTERED"
    state.observed_position_direction = "long"
    state.observed_position_size_pct = 0.3
    updated = store.record_shadow_cycle(
        state=state,
        judgement={"status": "ok"},
        handoff={
            "generated_at": "2026-04-26T12:37:30",
            "action": "wait",
            "position_state": "ARMED",
            "current_position_direction": "neutral",
            "position_size_pct": 0.0,
        },
        guard=GuardDecision(
            judgement_status="ok",
            allow_entry=True,
            allow_reduce=True,
            allow_exit=True,
            degraded=False,
            blocked=False,
        ),
        effective_action="wait",
        execution_results=None,
        runtime_snapshot=AdapterRuntimeSnapshot(snapshot_valid=False),
    )
    assert updated.observed_position_state == "ENTERED"
    assert updated.observed_position_direction == "long"
    assert updated.observed_position_size_pct == 0.3
    assert updated.execution_state is ExecutionLayerState.POSITION_OPEN


def test_state_store_prefers_runtime_snapshot_over_reconciliation_and_handoff_when_no_reconciliation_result_is_present(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.json")
    updated = store.record_shadow_cycle(
        state=store.load(),
        judgement={"status": "ok"},
        handoff={
            "generated_at": "2026-04-26T12:36:00",
            "action": "wait",
            "position_state": "ARMED",
            "current_position_direction": "neutral",
            "position_size_pct": 0.4,
        },
        guard=GuardDecision(
            judgement_status="ok",
            allow_entry=True,
            allow_reduce=True,
            allow_exit=True,
            degraded=False,
            blocked=False,
        ),
        effective_action="wait",
        execution_results=None,
        runtime_snapshot=AdapterRuntimeSnapshot(
            position=PositionSnapshot(position_state="ENTERED", direction="long", size_pct=0.3),
            protective_stop_present=True,
        ),
    )
    assert updated.observed_position_state == "ENTERED"
    assert updated.observed_position_direction == "long"
    assert updated.observed_position_size_pct == 0.3
    assert updated.execution_state is ExecutionLayerState.POSITION_OPEN



def test_state_store_prefers_accepted_reconciliation_summary_over_runtime_snapshot(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.json")
    state = store.load()
    state.observed_position_state = "ENTERED"
    state.observed_position_direction = "long"
    state.observed_position_size_pct = 0.3
    updated = store.record_shadow_cycle(
        state=state,
        judgement={"status": "ok"},
        handoff={"generated_at": "2026-04-26T12:36:45", "action": "wait"},
        guard=GuardDecision(
            judgement_status="ok",
            allow_entry=True,
            allow_reduce=True,
            allow_exit=True,
            degraded=False,
            blocked=False,
        ),
        effective_action="wait",
        execution_results=[
            CommandExecutionResult(
                target="reconcile_position_and_orders",
                status="accepted",
                accepted=True,
                simulated=False,
                details={
                    "response_summary": {
                        "position_state": "ENTERED",
                        "direction": "long",
                        "size_pct": 0.15,
                    }
                },
            )
        ],
        runtime_snapshot=AdapterRuntimeSnapshot(
            position=PositionSnapshot(position_state="ENTERED", direction="long", size_pct=0.3),
            protective_stop_present=True,
        ),
    )
    assert updated.observed_position_state == "ENTERED"
    assert updated.observed_position_direction == "long"
    assert updated.observed_position_size_pct == 0.15
    assert updated.execution_state is ExecutionLayerState.POSITION_OPEN
