from datetime import datetime, timezone

from bot.network_guard import GuardDecision
from bot.position_manager import PositionManager
from bot.execution_risk_gate import ExecutionRiskGate, ExecutionRiskGateConfig
from bot.exchange_adapter import AdapterCapabilities, EntryOrderPayload, ExchangeAdapter


def _fresh_handoff(**payload):
    payload.setdefault("factor_lookup_generated_at", datetime.now(timezone.utc).isoformat())
    return payload


def test_position_manager_maps_entry_to_entry_plan() -> None:
    plan = PositionManager().build_execution_plan(
        handoff=_fresh_handoff(action="entry_long", position_size_pct=0.2, initial_stop_loss=0.98),
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


def test_position_manager_blocks_entry_when_execution_risk_gate_blocks() -> None:
    plan = PositionManager(
        ExecutionRiskGate(ExecutionRiskGateConfig(require_execution_allowed=True))
    ).build_execution_plan(
        handoff={"action": "entry_long", "position_size_pct": 0.2, "initial_stop_loss": 0.98},
        guard=GuardDecision(
            judgement_status="ok",
            allow_entry=True,
            allow_reduce=True,
            allow_exit=True,
        ),
    )
    assert plan.effective_action == "wait"
    assert plan.place_entry_order is False
    assert plan.maintain_protective_stop is False
    assert plan.plan_reason == "entry_blocked_by_execution_risk_gate"
    assert plan.notes == ["execution_allowed_missing"]


def test_position_manager_exposes_execution_risk_gate_result_on_entry_plan() -> None:
    plan = PositionManager(
        ExecutionRiskGate(
            ExecutionRiskGateConfig(
                leverage=10,
                entry_margin_budget_usdt=None,
                max_account_risk_pct_per_trade=0.01,
                require_execution_allowed=True,
            )
        )
    ).build_execution_plan(
        handoff=_fresh_handoff(
            action="entry_long",
            position_size_pct=0.8,
            initial_stop_loss=0.98,
            execution_allowed=True,
        ),
        guard=GuardDecision(
            judgement_status="ok",
            allow_entry=True,
            allow_reduce=True,
            allow_exit=True,
        ),
    )
    assert plan.effective_action == "entry_long"
    assert plan.executable_size_pct == 0.05
    assert plan.stop_distance_pct == 0.02
    assert plan.account_risk_pct == 0.01
    assert plan.notes == ["execution_risk_gate_pass"]


def test_position_manager_blocks_entry_below_exchange_min_qty() -> None:
    plan = PositionManager(
        ExecutionRiskGate(
            ExecutionRiskGateConfig(
                leverage=10,
                entry_margin_budget_usdt=None,
                max_probe_account_risk_pct=0.002,
                max_probe_size_pct=0.02,
                require_execution_allowed=True,
                exchange_min_order_qty=0.001,
                exchange_qty_step_size=0.001,
            )
        )
    ).build_execution_plan(
        handoff=_fresh_handoff(
            action="small_probe",
            direction="long",
            position_size_pct=0.1,
            initial_stop_loss=0.9844,
            execution_allowed=True,
        ),
        guard=GuardDecision(
            judgement_status="ok",
            allow_entry=True,
            allow_reduce=True,
            allow_exit=True,
        ),
        runtime_state={
            "runtime_account_equity": 10.0,
            "runtime_mark_price": 2300.0,
            "runtime_leverage": 10,
        },
    )

    assert plan.effective_action == "wait"
    assert plan.place_entry_order is False
    assert plan.plan_reason == "entry_blocked_by_execution_risk_gate"
    assert plan.notes == ["account_too_small_for_exchange_min_qty"]


def test_entry_handoff_uses_risk_sized_executable_size_in_adapter_payload() -> None:
    handoff = {
        "action": "entry_long",
        "direction": "long",
        "position_size_pct": 0.8,
        "initial_stop_loss": 0.98,
        "execution_allowed": True,
        "max_account_risk_pct_per_trade": 0.01,
    }
    handoff = _fresh_handoff(**handoff)
    plan = PositionManager(
        ExecutionRiskGate(
            ExecutionRiskGateConfig(
                leverage=10,
                entry_margin_budget_usdt=None,
                max_account_risk_pct_per_trade=0.01,
                require_execution_allowed=True,
            )
        )
    ).build_execution_plan(
        handoff=handoff,
        guard=GuardDecision(
            judgement_status="ok",
            allow_entry=True,
            allow_reduce=True,
            allow_exit=True,
        ),
    )

    commands = ExchangeAdapter().build_commands(execution_plan=plan, handoff=handoff)

    assert plan.place_entry_order is True
    assert plan.executable_size_pct == 0.05
    assert isinstance(commands[0].payload, EntryOrderPayload)
    assert commands[0].payload.position_size_pct == 0.05


def test_position_manager_does_not_interpret_quant_sizing_tier() -> None:
    plan = PositionManager().build_execution_plan(
        handoff=_fresh_handoff(
            action="entry_long",
            position_size_pct=0.2,
            initial_stop_loss=0.98,
            sizing_tier="none",
            sizing_bias="none",
        ),
        guard=GuardDecision(
            judgement_status="ok",
            allow_entry=True,
            allow_reduce=True,
            allow_exit=True,
        ),
    )

    assert plan.effective_action == "entry_long"
    assert plan.place_entry_order is True
    assert plan.plan_reason == "quant_action_passthrough"


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
    assert plan.advance_breakeven is False
    assert plan.advance_trailing_stop is False
    assert plan.sync_recent_fills is True


def test_position_manager_only_advances_post_entry_stops_when_adapter_supports_it() -> None:
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
        },
        adapter_capabilities=AdapterCapabilities(
            supports_breakeven_update=True,
            supports_trailing_stop_update=True,
        ),
    )

    assert plan.advance_breakeven is True
    assert plan.advance_trailing_stop is True
    assert plan.sync_recent_fills is False


def test_position_manager_plans_take_profit_only_with_explicit_contract_and_capability() -> None:
    plan = PositionManager().build_execution_plan(
        handoff=_fresh_handoff(
            action="entry_long",
            direction="long",
            execution_allowed=True,
            risk_filter_status="pass",
            position_size_pct=0.2,
            executable_size_pct=0.02,
            max_account_risk_pct_per_trade=0.01,
            initial_stop_loss=0.97,
            tp_ladder=[1.01, 1.02],
            tp_reduce_fractions=[0.5, 0.5],
        ),
        guard=GuardDecision(judgement_status="ok", allow_entry=True),
        adapter_capabilities=AdapterCapabilities(supports_take_profit_orders=True),
    )

    assert plan.place_entry_order is True
    assert plan.place_take_profit_orders is True


def test_position_manager_does_not_plan_take_profit_for_bare_ladder() -> None:
    plan = PositionManager().build_execution_plan(
        handoff=_fresh_handoff(
            action="entry_long",
            direction="long",
            execution_allowed=True,
            risk_filter_status="pass",
            position_size_pct=0.2,
            executable_size_pct=0.02,
            max_account_risk_pct_per_trade=0.01,
            initial_stop_loss=0.97,
            tp_ladder=[1.01, 1.02],
        ),
        guard=GuardDecision(judgement_status="ok", allow_entry=True),
        adapter_capabilities=AdapterCapabilities(supports_take_profit_orders=True),
    )

    assert plan.place_entry_order is True
    assert plan.place_take_profit_orders is False


def test_position_manager_does_not_plan_take_profit_for_malformed_direct_orders() -> None:
    plan = PositionManager().build_execution_plan(
        handoff=_fresh_handoff(
            action="entry_long",
            direction="long",
            execution_allowed=True,
            risk_filter_status="pass",
            position_size_pct=0.2,
            executable_size_pct=0.02,
            max_account_risk_pct_per_trade=0.01,
            initial_stop_loss=0.97,
            take_profit_orders=["not-a-contract"],
        ),
        guard=GuardDecision(judgement_status="ok", allow_entry=True),
        adapter_capabilities=AdapterCapabilities(supports_take_profit_orders=True),
    )

    assert plan.place_entry_order is True
    assert plan.place_take_profit_orders is False


def test_position_manager_does_not_plan_take_profit_for_ambiguous_ladder_size_contract() -> None:
    plan = PositionManager().build_execution_plan(
        handoff=_fresh_handoff(
            action="entry_long",
            direction="long",
            execution_allowed=True,
            risk_filter_status="pass",
            position_size_pct=0.2,
            executable_size_pct=0.02,
            max_account_risk_pct_per_trade=0.01,
            initial_stop_loss=0.97,
            tp_ladder=[1.01, 1.02],
            tp_reduce_fractions=[0.5, 0.5],
            tp_reduce_qtys=[0.01, 0.01],
        ),
        guard=GuardDecision(judgement_status="ok", allow_entry=True),
        adapter_capabilities=AdapterCapabilities(supports_take_profit_orders=True),
    )

    assert plan.place_entry_order is True
    assert plan.place_take_profit_orders is False


def test_position_manager_does_not_maintain_existing_protective_stop_under_wait() -> None:
    plan = PositionManager().build_execution_plan(
        handoff={
            "action": "wait",
            "position_state": "ENTERED",
            "position_size_pct": 0.3,
        },
        guard=GuardDecision(
            judgement_status="ok",
            allow_entry=True,
            allow_reduce=True,
            allow_exit=True,
        ),
        runtime_state={
            "observed_position_state": "ENTERED",
            "observed_position_size_pct": 0.3,
            "protective_stop_present": True,
            "protective_stop_required": False,
        },
    )
    assert plan.effective_action == "wait"
    assert plan.maintain_protective_stop is False


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


def test_position_manager_exits_expired_contrarian_probe() -> None:
    plan = PositionManager().build_execution_plan(
        handoff={"action": "wait", "position_state": "ENTERED", "position_size_pct": 0.0025},
        guard=GuardDecision(
            judgement_status="ok",
            allow_entry=True,
            allow_reduce=True,
            allow_exit=True,
        ),
        runtime_state={
            "observed_position_state": "ENTERED",
            "observed_position_size_pct": 0.0025,
            "runtime_now": "2026-05-02T04:01:00",
            "metadata": {
                "active_probe_source": "contrarian_short_probe",
                "active_probe_expires_at": "2026-05-02T04:00:00",
            },
        },
    )

    assert plan.effective_action == "exit"
    assert plan.place_exit_order is True
    assert plan.plan_reason == "contrarian_probe_expired"
    assert plan.notes == ["contrarian_probe_expired"]


def test_position_manager_rolls_expired_contrarian_probe_when_signal_continues() -> None:
    plan = PositionManager().build_execution_plan(
        handoff=_fresh_handoff(
            action="small_probe",
            direction="short",
            probe_source="contrarian_short_probe",
            position_state="ENTERED",
            position_size_pct=0.0025,
            initial_stop_loss=1.01,
            execution_allowed=True,
        ),
        guard=GuardDecision(
            judgement_status="ok",
            allow_entry=True,
            allow_reduce=True,
            allow_exit=True,
        ),
        runtime_state={
            "observed_position_state": "ENTERED",
            "observed_position_size_pct": 0.0025,
            "runtime_now": "2026-05-02T04:01:00",
            "metadata": {
                "active_probe_source": "contrarian_short_probe",
                "active_probe_expires_at": "2026-05-02T04:00:00",
            },
        },
    )

    assert plan.effective_action == "small_probe"
    assert plan.place_entry_order is False
    assert plan.place_exit_order is False
    assert plan.plan_reason == "contrarian_probe_rolled_forward"
    assert plan.notes == ["contrarian_probe_expiry_rolled_forward"]


def test_position_manager_exits_expired_trigger_ready_probe() -> None:
    plan = PositionManager().build_execution_plan(
        handoff={"action": "wait", "position_state": "ENTERED", "position_size_pct": 0.10},
        guard=GuardDecision(
            judgement_status="ok",
            allow_entry=True,
            allow_reduce=True,
            allow_exit=True,
        ),
        runtime_state={
            "observed_position_state": "ENTERED",
            "observed_position_size_pct": 0.10,
            "runtime_now": "2026-05-02T03:46:00",
            "metadata": {
                "active_probe_source": "trigger_ready_small_probe",
                "active_probe_expires_at": "2026-05-02T03:45:00",
                "active_probe_invalidate_conditions": [
                    "trigger_ready_long_failed_followthrough",
                    "trigger_reversal_15m",
                    "no_followthrough_after_3x15m",
                    "hard_risk_veto",
                ],
            },
        },
    )

    assert plan.effective_action == "exit"
    assert plan.place_exit_order is True
    assert plan.plan_reason == "trigger_ready_probe_expired"
    assert plan.notes == ["trigger_ready_probe_expired"]


def test_position_manager_invalidates_active_trigger_ready_probe_on_reversal_reason() -> None:
    plan = PositionManager().build_execution_plan(
        handoff={
            "action": "wait",
            "position_state": "ENTERED",
            "position_size_pct": 0.10,
            "transition_reason_codes": ["probe_trigger_reversal_exit"],
        },
        guard=GuardDecision(
            judgement_status="ok",
            allow_entry=True,
            allow_reduce=True,
            allow_exit=True,
        ),
        runtime_state={
            "observed_position_state": "ENTERED",
            "observed_position_direction": "long",
            "observed_position_size_pct": 0.10,
            "runtime_now": "2026-05-02T03:30:00",
            "metadata": {
                "active_probe_source": "trigger_ready_small_probe",
                "active_probe_expires_at": "2026-05-02T03:45:00",
                "active_probe_invalidate_conditions": [
                    "trigger_ready_long_failed_followthrough",
                    "trigger_reversal_15m",
                    "no_followthrough_after_3x15m",
                    "hard_risk_veto",
                ],
            },
        },
    )

    assert plan.effective_action == "exit"
    assert plan.place_exit_order is True
    assert plan.plan_reason == "trigger_ready_probe_invalidated"
    assert plan.notes == [
        "trigger_ready_probe_invalidated",
        "matched_invalidate_condition:trigger_reversal_15m",
    ]


def test_position_manager_does_not_invalidate_active_probe_on_unknown_condition() -> None:
    plan = PositionManager().build_execution_plan(
        handoff={
            "action": "wait",
            "position_state": "ENTERED",
            "position_size_pct": 0.10,
            "transition_reason_codes": ["probe_trigger_reversal_exit"],
        },
        guard=GuardDecision(
            judgement_status="ok",
            allow_entry=True,
            allow_reduce=True,
            allow_exit=True,
        ),
        runtime_state={
            "observed_position_state": "ENTERED",
            "observed_position_direction": "long",
            "observed_position_size_pct": 0.10,
            "runtime_now": "2026-05-02T03:30:00",
            "metadata": {
                "active_probe_source": "trigger_ready_small_probe",
                "active_probe_expires_at": "2026-05-02T03:45:00",
                "active_probe_invalidate_conditions": ["unknown_condition"],
            },
        },
    )

    assert plan.effective_action == "wait"
    assert plan.plan_reason == "quant_action_passthrough"
    assert plan.place_exit_order is False


def test_position_manager_does_not_invalidate_active_probe_without_open_risk() -> None:
    plan = PositionManager().build_execution_plan(
        handoff={
            "action": "wait",
            "position_state": "FLAT",
            "position_size_pct": 0.0,
            "transition_reason_codes": ["probe_trigger_reversal_exit"],
        },
        guard=GuardDecision(
            judgement_status="ok",
            allow_entry=True,
            allow_reduce=True,
            allow_exit=True,
        ),
        runtime_state={
            "observed_position_state": "FLAT",
            "observed_position_direction": "neutral",
            "observed_position_size_pct": 0.0,
            "runtime_now": "2026-05-02T03:30:00",
            "metadata": {
                "active_probe_source": "trigger_ready_small_probe",
                "active_probe_expires_at": "2026-05-02T03:45:00",
                "active_probe_invalidate_conditions": ["trigger_reversal_15m"],
            },
        },
    )

    assert plan.effective_action == "wait"
    assert plan.plan_reason == "quant_action_passthrough"
    assert plan.place_exit_order is False


def test_position_manager_invalidates_active_trigger_ready_probe_on_hard_risk_veto() -> None:
    plan = PositionManager().build_execution_plan(
        handoff={"action": "wait", "position_state": "ENTERED", "position_size_pct": 0.10},
        guard=GuardDecision(
            judgement_status="ok",
            allow_entry=False,
            allow_reduce=True,
            allow_exit=True,
            reason_codes=["runtime_entry_veto"],
        ),
        runtime_state={
            "observed_position_state": "ENTERED",
            "observed_position_direction": "long",
            "observed_position_size_pct": 0.10,
            "runtime_now": "2026-05-02T03:30:00",
            "metadata": {
                "active_probe_source": "trigger_ready_small_probe",
                "active_probe_expires_at": "2026-05-02T03:45:00",
                "active_probe_invalidate_conditions": ["hard_risk_veto"],
            },
        },
    )

    assert plan.effective_action == "exit"
    assert plan.place_exit_order is True
    assert plan.plan_reason == "trigger_ready_probe_invalidated"
    assert plan.notes == [
        "trigger_ready_probe_invalidated",
        "matched_invalidate_condition:hard_risk_veto",
    ]
