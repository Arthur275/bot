from bot.network_guard import GuardDecision
from bot.position_manager import PositionManager


def test_position_manager_maps_entry_to_entry_plan() -> None:
    plan = PositionManager().build_execution_plan(
        handoff={"action": "entry_long", "position_size_pct": 0.0},
        guard=GuardDecision(
            judgement_status="ok",
            allow_entry=True,
            allow_reduce=True,
            allow_exit=True,
        ),
    )
    assert plan.effective_action == "entry_long"
    assert plan.place_entry_order is True
    assert plan.maintain_protective_stop is True


def test_position_manager_keeps_observe_only_as_quant_action() -> None:
    plan = PositionManager().build_execution_plan(
        handoff={"action": "observe_only", "position_size_pct": 0.0},
        guard=GuardDecision(
            judgement_status="ok",
            allow_entry=True,
            allow_reduce=True,
            allow_exit=True,
        ),
    )
    assert plan.effective_action == "observe_only"
    assert plan.plan_reason == "quant_action_passthrough"


def test_position_manager_keeps_protective_stop_when_entry_is_disallowed_with_open_risk() -> None:
    plan = PositionManager().build_execution_plan(
        handoff={"action": "entry_short", "position_size_pct": 0.2},
        guard=GuardDecision(
            judgement_status="ok",
            allow_entry=False,
            allow_reduce=True,
            allow_exit=True,
            degraded=True,
            reason_codes=["diagnostic:transport"],
        ),
        runtime_state={"observed_position_size_pct": 0.2},
    )
    assert plan.effective_action == "wait"
    assert plan.maintain_protective_stop is True
    assert plan.needs_reconciliation is True
    assert plan.recovery_action == "reconcile_before_reentry"


def test_position_manager_requests_reconciliation_when_runtime_recovery_is_pending() -> None:
    plan = PositionManager().build_execution_plan(
        handoff={"action": "wait", "position_size_pct": 0.3},
        guard=GuardDecision(
            judgement_status="ok",
            allow_entry=True,
            allow_reduce=True,
            allow_exit=True,
        ),
        runtime_state={
            "observed_position_size_pct": 0.3,
            "recovery_required": True,
            "reconciliation_required": True,
            "protective_stop_required": True,
        },
    )
    assert plan.effective_action == "wait"
    assert plan.plan_reason == "quant_action_passthrough"
    assert plan.needs_reconciliation is True
    assert plan.maintain_protective_stop is True
    assert plan.recovery_action == "reconcile_runtime_state"


def test_position_manager_blocks_new_entry_until_reconciliation_clears() -> None:
    plan = PositionManager().build_execution_plan(
        handoff={"action": "entry_long", "position_size_pct": 0.2},
        guard=GuardDecision(
            judgement_status="ok",
            allow_entry=True,
            allow_reduce=True,
            allow_exit=True,
        ),
        runtime_state={
            "observed_position_state": "ENTERED",
            "observed_position_size_pct": 0.2,
            "recovery_required": True,
            "reconciliation_required": True,
            "protective_stop_required": True,
        },
    )
    assert plan.requested_action == "entry_long"
    assert plan.effective_action == "wait"
    assert plan.plan_reason == "entry_blocked_until_reconciliation"
    assert plan.maintain_protective_stop is True
    assert plan.needs_reconciliation is True
    assert plan.recovery_action == "reconcile_before_reentry"
    assert plan.place_entry_order is False


def test_position_manager_keeps_execution_hygiene_actions_under_wait() -> None:
    plan = PositionManager().build_execution_plan(
        handoff={
            "action": "wait",
            "position_size_pct": 0.3,
            "breakeven_trigger": 1.01,
            "trailing_rule": "trail_with_trigger",
        },
        guard=GuardDecision(
            judgement_status="ok",
            allow_entry=True,
            allow_reduce=True,
            allow_exit=True,
        ),
        runtime_state={
            "observed_position_size_pct": 0.3,
            "breakeven_ready": True,
            "trailing_ready": True,
            "recent_fill_sync_required": True,
        },
    )
    assert plan.effective_action == "wait"
    assert plan.plan_reason == "quant_action_passthrough"
    assert plan.maintain_protective_stop is True
    assert plan.advance_breakeven is True
    assert plan.advance_trailing_stop is True
    assert plan.sync_recent_fills is True


def test_position_manager_treats_entered_state_as_open_risk_without_size_pct() -> None:
    plan = PositionManager().build_execution_plan(
        handoff={"action": "wait", "position_state": "ENTERED", "position_size_pct": 0.0},
        guard=GuardDecision(
            judgement_status="ok",
            allow_entry=True,
            allow_reduce=True,
            allow_exit=True,
        ),
        runtime_state={
            "observed_position_state": "ENTERED",
            "observed_position_size_pct": 0.0,
            "recent_fill_sync_required": True,
        },
    )
    assert plan.plan_reason == "quant_action_passthrough"
    assert plan.maintain_protective_stop is True
    assert plan.sync_recent_fills is True



def test_position_manager_requests_recent_fill_sync_for_open_risk_reconciliation_gap() -> None:
    plan = PositionManager().build_execution_plan(
        handoff={"action": "wait", "position_state": "ENTERED", "position_size_pct": 0.0},
        guard=GuardDecision(
            judgement_status="ok",
            allow_entry=True,
            allow_reduce=True,
            allow_exit=True,
        ),
        runtime_state={
            "observed_position_state": "ENTERED",
            "observed_position_size_pct": 0.0,
            "recent_fill_sync_required": True,
            "reconciliation_required": True,
        },
    )
    assert plan.plan_reason == "quant_action_passthrough"
    assert plan.maintain_protective_stop is True
    assert plan.sync_recent_fills is True
    assert plan.needs_reconciliation is True



def test_position_manager_does_not_request_recent_fill_sync_without_runtime_gap_signal() -> None:
    plan = PositionManager().build_execution_plan(
        handoff={"action": "wait", "position_state": "ENTERED", "position_size_pct": 0.0},
        guard=GuardDecision(
            judgement_status="ok",
            allow_entry=True,
            allow_reduce=True,
            allow_exit=True,
        ),
        runtime_state={
            "observed_position_state": "ENTERED",
            "observed_position_size_pct": 0.0,
            "recent_fill_sync_required": False,
            "reconciliation_required": True,
            "protective_stop_required": True,
        },
    )
    assert plan.plan_reason == "quant_action_passthrough"
    assert plan.maintain_protective_stop is True
    assert plan.sync_recent_fills is False
    assert plan.needs_reconciliation is True


def test_position_manager_keeps_exit_available_during_recovery() -> None:
    plan = PositionManager().build_execution_plan(
        handoff={"action": "exit", "position_state": "ENTERED", "position_size_pct": 0.3},
        guard=GuardDecision(
            judgement_status="ok",
            allow_entry=True,
            allow_reduce=True,
            allow_exit=True,
        ),
        runtime_state={
            "observed_position_state": "ENTERED",
            "observed_position_size_pct": 0.3,
            "recovery_required": True,
            "reconciliation_required": True,
            "protective_stop_required": True,
        },
    )
    assert plan.requested_action == "exit"
    assert plan.effective_action == "exit"
    assert plan.plan_reason == "quant_action_passthrough"
    assert plan.place_exit_order is True
    assert plan.maintain_protective_stop is True
    assert plan.needs_reconciliation is True
    assert plan.recovery_action == "reconcile_runtime_state"
