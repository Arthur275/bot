from __future__ import annotations

from datetime import datetime, timedelta

from bot.exchange_adapter import AdapterRuntimeSnapshot, PositionSnapshot
from bot.high_risk_gate import HighRiskGate
from bot.network_guard import GuardDecision


def _snapshot(*, direction: str = "long", amount: float = 0.043) -> AdapterRuntimeSnapshot:
    return AdapterRuntimeSnapshot(
        fetched_at=datetime(2026, 5, 3, 1, 0, 0),
        position=PositionSnapshot(
            position_state="ENTERED",
            direction=direction,
            size_pct=0.5,
            position_amt=amount,
            entry_price=2300.0,
            mark_price=2320.0,
            leverage=10,
        ),
        protective_stop_present=True,
    )


def _network(*, degraded: bool = False, blocked: bool = False) -> GuardDecision:
    return GuardDecision(judgement_status="ok", ready=True, degraded=degraded, blocked=blocked)


def _handoff(**overrides):
    payload = {
        "handoff_id": "hr-1",
        "generated_at": "2026-05-03T01:00:00",
        "expires_at": "2026-05-03T01:05:00",
        "action": "reduce",
        "runtime_mode": "real",
        "engine_mode": "strict-live",
        "symbol": "ETH",
        "exchange_symbol": "ETHUSDT",
        "direction": "long",
        "position_state": "ENTERED",
        "risk_filter_status": "pass",
        "reduce_fraction": 0.5,
        "reason": "risk downgrade",
    }
    payload.update(overrides)
    return payload


def _gate(tmp_path):
    return HighRiskGate(
        kill_switch_path=tmp_path / "disable_real_execution.flag",
        lock_path=tmp_path / "high_risk_action.lock",
        now_fn=lambda: datetime(2026, 5, 3, 1, 1, 0),
    )


def test_high_risk_gate_allows_valid_reduce(tmp_path) -> None:
    decision = _gate(tmp_path).evaluate(
        raw_handoff=_handoff(),
        network_decision=_network(),
        runtime_snapshot=_snapshot(),
    )

    assert decision.allowed is True
    assert decision.reason_codes == ["high_risk_gate_pass"]


def test_high_risk_gate_blocks_expired_handoff(tmp_path) -> None:
    decision = _gate(tmp_path).evaluate(
        raw_handoff=_handoff(expires_at="2026-05-03T01:00:30"),
        network_decision=_network(),
        runtime_snapshot=_snapshot(),
    )

    assert decision.allowed is False
    assert "handoff_expired" in decision.reason_codes


def test_high_risk_gate_blocks_network_degraded_for_all_actions(tmp_path) -> None:
    decision = _gate(tmp_path).evaluate(
        raw_handoff=_handoff(action="trailing", reduce_fraction=None, trailing_rule={"activation_price": 2340.0, "callback_rate": 0.4}),
        network_decision=_network(degraded=True),
        runtime_snapshot=_snapshot(),
    )

    assert decision.allowed is False
    assert "network_unhealthy" in decision.reason_codes


def test_high_risk_gate_blocks_kill_switch_and_lock(tmp_path) -> None:
    (tmp_path / "disable_real_execution.flag").write_text("1", encoding="utf-8")
    (tmp_path / "high_risk_action.lock").write_text("lock", encoding="utf-8")

    decision = _gate(tmp_path).evaluate(
        raw_handoff=_handoff(),
        network_decision=_network(),
        runtime_snapshot=_snapshot(),
    )

    assert decision.allowed is False
    assert "kill_switch_enabled" in decision.reason_codes
    assert "high_risk_action_in_flight" in decision.reason_codes


def test_high_risk_gate_blocks_duplicate_handoff_id(tmp_path) -> None:
    decision = _gate(tmp_path).evaluate(
        raw_handoff=_handoff(),
        network_decision=_network(),
        runtime_snapshot=_snapshot(),
        executed_handoff_ids={"hr-1"},
    )

    assert decision.allowed is False
    assert "handoff_id_already_executed" in decision.reason_codes


def test_high_risk_gate_blocks_non_real_or_non_strict_live_handoff(tmp_path) -> None:
    decision = _gate(tmp_path).evaluate(
        raw_handoff=_handoff(runtime_mode="shadow", engine_mode="sample-fallback"),
        network_decision=_network(),
        runtime_snapshot=_snapshot(),
    )

    assert decision.allowed is False
    assert "runtime_mode_not_real" in decision.reason_codes
    assert "engine_mode_not_strict_live" in decision.reason_codes


def test_high_risk_gate_blocks_reduce_qty_above_position(tmp_path) -> None:
    decision = _gate(tmp_path).evaluate(
        raw_handoff=_handoff(reduce_fraction=None, reduce_qty=0.1),
        network_decision=_network(),
        runtime_snapshot=_snapshot(amount=0.043),
    )

    assert decision.allowed is False
    assert "reduce_qty_exceeds_position" in decision.reason_codes


def test_high_risk_gate_requires_trailing_rule(tmp_path) -> None:
    decision = _gate(tmp_path).evaluate(
        raw_handoff=_handoff(action="trailing", reduce_fraction=None),
        network_decision=_network(),
        runtime_snapshot=_snapshot(),
    )

    assert decision.allowed is False
    assert "handoff_schema_invalid" in decision.reason_codes
