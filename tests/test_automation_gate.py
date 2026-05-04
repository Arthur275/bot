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
