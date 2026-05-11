from __future__ import annotations

from bot.automation_gate import evaluate_real_order_gate


def test_real_order_gate_disabled_by_default() -> None:
    decision = evaluate_real_order_gate(payload={"effective_action": "entry_long"}, enable_real_orders=False)

    assert decision.enabled is False
    assert decision.allowed is False
    assert decision.automation_boundary == "no_order_submission"
    assert decision.reason_codes == ["real_orders_disabled"]


def test_real_order_gate_allows_entry_only_when_hard_gates_pass() -> None:
    decision = evaluate_real_order_gate(
        enable_real_orders=True,
        payload={
            "runtime_mode": "real",
            "engine_mode": "strict-live",
            "effective_action": "entry_long",
            "blocked": False,
            "degraded": False,
            "handoff": {
                "execution_allowed": True,
                "risk_filter_status": "pass",
                "initial_stop_loss": 0.97,
            },
            "execution_plan": {
                "place_entry_order": True,
                "maintain_protective_stop": True,
            },
            "runtime_snapshot": {
                "snapshot_valid": True,
                "position": {"position_state": "FLAT"},
            },
            "preflight": [
                {"target": "entry_order", "status": "preflight_ready", "error": ""},
                {"target": "maintain_protective_stop", "status": "preflight_ready", "error": ""},
            ],
        },
    )

    assert decision.enabled is True
    assert decision.allowed is True
    assert decision.automation_boundary == "real_order_submission_allowed"
    assert decision.reason_codes == []


def test_real_order_gate_kill_switch_blocks_before_other_checks(tmp_path) -> None:
    kill_switch_path = tmp_path / "disable_real_execution.flag"
    kill_switch_path.write_text("1", encoding="utf-8")

    decision = evaluate_real_order_gate(
        enable_real_orders=True,
        kill_switch_path=kill_switch_path,
        payload={
            "runtime_mode": "shadow",
            "effective_action": "entry_long",
        },
    )

    assert decision.allowed is False
    assert decision.automation_boundary == "real_order_submission_blocked"
    assert decision.reason_codes == ["kill_switch_enabled"]


def test_real_order_gate_blocks_entry_without_protective_stop_preflight() -> None:
    decision = evaluate_real_order_gate(
        enable_real_orders=True,
        payload={
            "runtime_mode": "real",
            "engine_mode": "strict-live",
            "effective_action": "entry_long",
            "handoff": {
                "execution_allowed": True,
                "risk_filter_status": "pass",
                "initial_stop_loss": 0.97,
            },
            "execution_plan": {
                "place_entry_order": True,
                "maintain_protective_stop": True,
            },
            "runtime_snapshot": {
                "snapshot_valid": True,
                "position": {"position_state": "FLAT"},
            },
            "preflight": [
                {"target": "entry_order", "status": "preflight_ready", "error": ""},
            ],
        },
    )

    assert decision.allowed is False
    assert decision.automation_boundary == "real_order_submission_blocked"
    assert "protective_stop_preflight_not_ready" in decision.reason_codes


def test_real_order_gate_blocks_all_non_pass_risk_filter_statuses() -> None:
    for status in ("degraded", "unavailable", "research_unavailable", "veto", "blocked", "future_unknown"):
        decision = evaluate_real_order_gate(
            enable_real_orders=True,
            payload={
                "runtime_mode": "real",
                "engine_mode": "strict-live",
                "effective_action": "entry_long",
                "handoff": {
                    "execution_allowed": True,
                    "risk_filter_status": status,
                    "initial_stop_loss": 0.97,
                },
                "execution_plan": {
                    "place_entry_order": True,
                    "maintain_protective_stop": True,
                },
                "runtime_snapshot": {
                    "snapshot_valid": True,
                    "position": {"position_state": "FLAT"},
                },
                "preflight": [
                    {"target": "entry_order", "status": "preflight_ready", "error": ""},
                    {"target": "maintain_protective_stop", "status": "preflight_ready", "error": ""},
                ],
            },
        )

        assert decision.allowed is False
        assert "risk_filter_not_pass" in decision.reason_codes


def test_real_order_gate_blocks_high_risk_auto_submit_for_now() -> None:
    decision = evaluate_real_order_gate(
        enable_real_orders=True,
        payload={
            "runtime_mode": "real",
            "engine_mode": "strict-live",
            "effective_action": "reduce",
        },
    )

    assert decision.allowed is False
    assert "high_risk_auto_submit_not_enabled" in decision.reason_codes
    assert "real_reduce_not_implemented" in decision.reason_codes


def test_real_order_gate_blocks_entry_when_strategy_tp_ladder_has_no_tp_order() -> None:
    decision = evaluate_real_order_gate(
        enable_real_orders=True,
        payload={
            "runtime_mode": "real",
            "engine_mode": "strict-live",
            "effective_action": "entry_long",
            "blocked": False,
            "degraded": False,
            "handoff": {
                "execution_allowed": True,
                "risk_filter_status": "pass",
                "initial_stop_loss": 0.97,
                "tp_ladder": [1.01, 1.02],
            },
            "execution_plan": {
                "place_entry_order": True,
                "maintain_protective_stop": True,
            },
            "command_targets": ["entry_order", "maintain_protective_stop"],
            "runtime_snapshot": {
                "snapshot_valid": True,
                "position": {"position_state": "FLAT"},
            },
            "preflight": [
                {"target": "entry_order", "status": "preflight_ready", "error": ""},
                {"target": "maintain_protective_stop", "status": "preflight_ready", "error": ""},
            ],
        },
    )

    assert decision.allowed is False
    assert "take_profit_orders_not_planned" in decision.reason_codes


def test_real_order_gate_allows_entry_when_take_profit_order_is_planned() -> None:
    decision = evaluate_real_order_gate(
        enable_real_orders=True,
        payload={
            "runtime_mode": "real",
            "engine_mode": "strict-live",
            "effective_action": "entry_long",
            "blocked": False,
            "degraded": False,
            "handoff": {
                "execution_allowed": True,
                "risk_filter_status": "pass",
                "initial_stop_loss": 0.97,
                "tp_ladder": [1.01],
            },
            "execution_plan": {
                "place_entry_order": True,
                "maintain_protective_stop": True,
                "place_take_profit_orders": True,
            },
            "command_targets": ["entry_order", "maintain_protective_stop", "take_profit_order"],
            "runtime_snapshot": {
                "snapshot_valid": True,
                "position": {"position_state": "FLAT"},
            },
            "preflight": [
                {"target": "entry_order", "status": "preflight_ready", "error": ""},
                {"target": "maintain_protective_stop", "status": "preflight_ready", "error": ""},
                {"target": "take_profit_order", "status": "preflight_ready", "error": ""},
            ],
        },
    )

    assert decision.allowed is True
    assert "take_profit_orders_not_planned" not in decision.reason_codes


def test_real_order_gate_blocks_unsupported_post_entry_stop_commands() -> None:
    decision = evaluate_real_order_gate(
        enable_real_orders=True,
        payload={
            "runtime_mode": "real",
            "engine_mode": "strict-live",
            "effective_action": "wait",
            "command_targets": ["advance_breakeven_stop", "advance_trailing_stop"],
            "adapter_capabilities": {
                "supports_breakeven_update": False,
                "supports_trailing_stop_update": False,
            },
            "runtime_snapshot": {
                "snapshot_valid": True,
                "position": {"position_state": "ENTERED", "direction": "long"},
            },
            "preflight": [
                {"target": "advance_breakeven_stop", "status": "preflight_ready", "error": ""},
                {"target": "advance_trailing_stop", "status": "preflight_ready", "error": ""},
            ],
        },
    )

    assert decision.allowed is False
    assert "breakeven_update_not_supported" in decision.reason_codes
    assert "trailing_stop_update_not_supported" in decision.reason_codes


def test_real_order_gate_blocks_exit_without_reduce_contract_reason() -> None:
    decision = evaluate_real_order_gate(
        enable_real_orders=True,
        payload={
            "runtime_mode": "real",
            "engine_mode": "strict-live",
            "effective_action": "exit",
        },
    )

    assert decision.allowed is False
    assert "high_risk_auto_submit_not_enabled" in decision.reason_codes
    assert "real_reduce_not_implemented" not in decision.reason_codes


def test_real_order_gate_allows_protective_stop_repair_when_position_open_and_stop_missing() -> None:
    decision = evaluate_real_order_gate(
        enable_real_orders=True,
        payload={
            "runtime_mode": "real",
            "engine_mode": "strict-live",
            "effective_action": "protective_stop_repair",
            "handoff": {"initial_stop_loss": 0.97, "direction": "long"},
            "runtime_snapshot": {
                "snapshot_valid": True,
                "protective_stop_present": False,
                "position": {"position_state": "ENTERED", "direction": "long"},
            },
            "preflight": [
                {"target": "maintain_protective_stop", "status": "preflight_ready", "error": ""},
            ],
        },
    )

    assert decision.allowed is True
    assert decision.automation_boundary == "real_order_submission_allowed"
    assert decision.reason_codes == []


def test_real_order_gate_blocks_protective_stop_repair_when_stop_already_present() -> None:
    decision = evaluate_real_order_gate(
        enable_real_orders=True,
        payload={
            "runtime_mode": "real",
            "engine_mode": "strict-live",
            "effective_action": "protective_stop_repair",
            "handoff": {"initial_stop_loss": 0.97, "direction": "long"},
            "runtime_snapshot": {
                "snapshot_valid": True,
                "protective_stop_present": True,
                "position": {"position_state": "ENTERED", "direction": "long"},
            },
            "preflight": [
                {"target": "maintain_protective_stop", "status": "preflight_ready", "error": ""},
            ],
        },
    )

    assert decision.allowed is False
    assert "protective_stop_already_present" in decision.reason_codes
