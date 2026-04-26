from pathlib import Path

from bot.exchange_adapter import AdapterRuntimeSnapshot, CommandExecutionResult, PositionSnapshot
from bot.network_guard import GuardDecision
from bot.state_store import ExecutionLayerState, StateStore


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
        plan_reason="entry_allowed",
        needs_reconciliation=False,
        maintain_protective_stop=True,
    )
    assert updated.execution_state is ExecutionLayerState.ENTRY_PENDING
    assert updated.pending_action == "entry_long"
    assert updated.last_plan_reason == "entry_allowed"
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
    assert updated.recovery_required is True
    assert updated.reconciliation_required is True


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
        plan_reason="recovery_reconciliation_required",
        needs_reconciliation=True,
        maintain_protective_stop=True,
        runtime_snapshot=AdapterRuntimeSnapshot(
            position=PositionSnapshot(position_state="ENTERED", direction="long", size_pct=0.3),
            protective_stop_present=True,
        ),
    )
    assert updated.execution_state is ExecutionLayerState.RECONCILING
    assert updated.recovery_required is True
    assert updated.reconciliation_required is True
    assert updated.protective_stop_required is True


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
        plan_reason="entry_allowed",
        execution_results=[
            CommandExecutionResult(
                target="entry_order",
                status="rejected",
                accepted=False,
                simulated=True,
                reason="entry_allowed",
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
        plan_reason="entry_allowed",
        maintain_protective_stop=True,
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
        plan_reason="wait_or_noop",
        runtime_snapshot=AdapterRuntimeSnapshot(
            position=PositionSnapshot(position_state="ENTERED", direction="long", size_pct=0.3),
            protective_stop_present=True,
        ),
    )
    assert updated.execution_state is ExecutionLayerState.POSITION_OPEN
    assert updated.observed_position_state == "ENTERED"
    assert updated.observed_position_direction == "long"
    assert updated.observed_position_size_pct == 0.3


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
