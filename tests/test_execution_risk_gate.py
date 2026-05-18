from datetime import datetime, timedelta, timezone

from bot.execution_risk_gate import ExecutionRiskGate, ExecutionRiskGateConfig


def _fresh_handoff(**payload):
    payload.setdefault("factor_lookup_generated_at", datetime.now(timezone.utc).isoformat())
    return payload


def test_execution_risk_gate_requires_execution_allowed_when_configured() -> None:
    decision = ExecutionRiskGate(
        ExecutionRiskGateConfig(require_execution_allowed=True)
    ).evaluate(
        handoff={"action": "entry_long", "position_size_pct": 0.2, "initial_stop_loss": 0.98}
    )

    assert decision.allowed is False
    assert decision.reason_codes == ["execution_allowed_missing"]


def test_execution_risk_gate_blocks_entry_without_executable_stop() -> None:
    decision = ExecutionRiskGate().evaluate(
        handoff=_fresh_handoff(action="entry_short", position_size_pct=0.2, execution_allowed=True)
    )

    assert decision.allowed is False
    assert decision.reason_codes == ["stop_not_executable"]


def test_execution_risk_gate_derives_executable_size_from_stop_distance() -> None:
    decision = ExecutionRiskGate(
        ExecutionRiskGateConfig(
            leverage=10,
            entry_margin_budget_usdt=None,
            max_account_risk_pct_per_trade=0.01,
            require_execution_allowed=True,
        )
    ).evaluate(
        handoff=_fresh_handoff(
            action="entry_long",
            position_size_pct=0.8,
            initial_stop_loss=0.98,
            execution_allowed=True,
        )
    )

    assert decision.allowed is True
    assert decision.executable_size_pct == 0.05
    assert decision.stop_distance_pct == 0.02
    assert decision.account_risk_pct == 0.01
    assert decision.reason_codes == ["execution_risk_gate_pass"]


def test_execution_risk_gate_blocks_entry_when_factor_lookup_timestamp_is_stale() -> None:
    stale_at = (datetime.now(timezone.utc) - timedelta(hours=30)).isoformat()

    decision = ExecutionRiskGate(
        ExecutionRiskGateConfig(require_execution_allowed=True)
    ).evaluate(
        handoff={
            "action": "entry_long",
            "position_size_pct": 0.2,
            "initial_stop_loss": 0.98,
            "execution_allowed": True,
            "factor_lookup_generated_at": stale_at,
            "factor_lookup_stale": False,
        }
    )

    assert decision.allowed is False
    assert decision.reason_codes == ["factor_lookup_age_over_threshold"]


def test_execution_risk_gate_uses_three_hour_factor_lookup_age_floor_by_default() -> None:
    stale_at = (datetime.now(timezone.utc) - timedelta(hours=4)).isoformat()

    decision = ExecutionRiskGate(
        ExecutionRiskGateConfig(require_execution_allowed=True)
    ).evaluate(
        handoff={
            "action": "entry_long",
            "position_size_pct": 0.2,
            "initial_stop_loss": 0.98,
            "execution_allowed": True,
            "factor_lookup_generated_at": stale_at,
            "factor_lookup_stale": False,
        }
    )

    assert decision.allowed is False
    assert decision.reason_codes == ["factor_lookup_age_over_threshold"]


def test_execution_risk_gate_factor_lookup_age_floor_can_be_configured() -> None:
    four_hours_old = (datetime.now(timezone.utc) - timedelta(hours=4)).isoformat()

    decision = ExecutionRiskGate(
        ExecutionRiskGateConfig(
            require_execution_allowed=True,
            factor_lookup_stale_after_sec=24 * 60 * 60,
            entry_margin_budget_usdt=None,
        )
    ).evaluate(
        handoff={
            "action": "entry_long",
            "position_size_pct": 0.2,
            "initial_stop_loss": 0.98,
            "execution_allowed": True,
            "factor_lookup_generated_at": four_hours_old,
            "factor_lookup_stale": False,
        }
    )

    assert decision.allowed is True
    assert decision.reason_codes == ["execution_risk_gate_pass"]


def test_execution_risk_gate_blocks_entry_when_handoff_freshness_unknown() -> None:
    decision = ExecutionRiskGate(
        ExecutionRiskGateConfig(require_execution_allowed=True)
    ).evaluate(
        handoff={
            "action": "entry_long",
            "position_size_pct": 0.2,
            "initial_stop_loss": 0.98,
            "execution_allowed": True,
        }
    )

    assert decision.allowed is False
    assert decision.reason_codes == ["handoff_freshness_unknown"]


def test_execution_risk_gate_blocks_entry_when_scoring_chain_is_frozen() -> None:
    decision = ExecutionRiskGate(
        ExecutionRiskGateConfig(require_execution_allowed=True)
    ).evaluate(
        handoff=_fresh_handoff(
            action="entry_short",
            position_size_pct=0.2,
            initial_stop_loss=1.02,
            execution_allowed=True,
            scoring_chain_frozen=True,
        )
    )

    assert decision.allowed is False
    assert decision.reason_codes == ["scoring_chain_frozen"]


def test_execution_risk_gate_does_not_interpret_quant_sizing_tier() -> None:
    decision = ExecutionRiskGate(
        ExecutionRiskGateConfig(
            leverage=10,
            entry_margin_budget_usdt=None,
            max_account_risk_pct_per_trade=0.01,
            require_execution_allowed=True,
        )
    ).evaluate(
        handoff=_fresh_handoff(
            action="entry_long",
            position_size_pct=0.8,
            initial_stop_loss=0.98,
            execution_allowed=True,
            sizing_tier="probe",
            sizing_bias="conservative",
        )
    )

    assert decision.allowed is True
    assert decision.executable_size_pct == 0.05
    assert decision.reason_codes == ["execution_risk_gate_pass"]


def test_execution_risk_gate_caps_small_probe_size() -> None:
    decision = ExecutionRiskGate(
        ExecutionRiskGateConfig(
            leverage=10,
            entry_margin_budget_usdt=None,
            max_account_risk_pct_per_trade=0.01,
            max_probe_account_risk_pct=0.002,
            max_probe_size_pct=0.02,
            require_execution_allowed=True,
        )
    ).evaluate(
        handoff=_fresh_handoff(
            action="small_probe",
            direction="short",
            position_size_pct=0.5,
            initial_stop_loss=1.005,
            execution_allowed=True,
        )
    )

    assert decision.allowed is True
    assert decision.requested_size_pct == 0.5
    assert decision.executable_size_pct == 0.02
    assert decision.size_cap_source == "bot_execution_risk_gate"
    assert decision.size_cap_reason == "max_probe_size_pct"
    assert decision.stop_distance_pct == 0.005
    assert decision.account_risk_pct == 0.002
    assert "size_truncated_by_bot_risk_gate" in decision.reason_codes


def test_execution_risk_gate_reports_probe_truncation_for_quant_8pct_request() -> None:
    decision = ExecutionRiskGate(
        ExecutionRiskGateConfig(
            leverage=10,
            entry_margin_budget_usdt=None,
            max_account_risk_pct_per_trade=0.01,
            max_probe_account_risk_pct=0.002,
            max_probe_size_pct=0.02,
            require_execution_allowed=True,
        )
    ).evaluate(
        handoff=_fresh_handoff(
            action="small_probe",
            direction="long",
            requested_size_pct=0.08,
            position_size_pct=0.08,
            initial_stop_loss=0.99,
            execution_allowed=True,
        )
    )

    assert decision.allowed is True
    assert decision.requested_size_pct == 0.08
    assert decision.executable_size_pct == 0.02
    assert decision.size_cap_source == "bot_execution_risk_gate"
    assert decision.size_cap_reason == "max_probe_size_pct"
    assert "size_truncated_by_bot_risk_gate" in decision.reason_codes


def test_execution_risk_gate_default_probe_cap_matches_ten_pct_contract() -> None:
    decision = ExecutionRiskGate(
        ExecutionRiskGateConfig(
            leverage=10,
            entry_margin_budget_usdt=None,
            max_probe_account_risk_pct=0.02,
            require_execution_allowed=True,
        )
    ).evaluate(
        handoff=_fresh_handoff(
            action="small_probe",
            direction="long",
            requested_size_pct=0.2,
            position_size_pct=0.2,
            stop_distance_pct=0.01,
            execution_allowed=True,
        )
    )

    assert decision.allowed is True
    assert decision.executable_size_pct == 0.1
    assert decision.size_cap_reason == "max_probe_size_pct"
    assert "size_truncated_by_bot_risk_gate" in decision.reason_codes


def test_execution_risk_gate_caps_technical_contrarian_probe_risk() -> None:
    decision = ExecutionRiskGate(
        ExecutionRiskGateConfig(
            leverage=10,
            entry_margin_budget_usdt=None,
            max_account_risk_pct_per_trade=0.01,
            max_probe_account_risk_pct=0.002,
            max_probe_size_pct=0.02,
            require_execution_allowed=True,
        )
    ).evaluate(
        handoff=_fresh_handoff(
            action="small_probe",
            direction="short",
            probe_source="contrarian_short_probe",
            probe_risk_tier="technical",
            position_size_pct=0.5,
            initial_stop_loss=1.005,
            execution_allowed=True,
        )
    )

    assert decision.allowed is True
    assert decision.executable_size_pct == 0.0025
    assert decision.account_risk_pct == 0.00075


def test_execution_risk_gate_caps_crowding_contrarian_probe_risk() -> None:
    decision = ExecutionRiskGate(
        ExecutionRiskGateConfig(
            leverage=10,
            entry_margin_budget_usdt=None,
            max_account_risk_pct_per_trade=0.01,
            max_probe_account_risk_pct=0.002,
            max_probe_size_pct=0.02,
            require_execution_allowed=True,
        )
    ).evaluate(
        handoff=_fresh_handoff(
            action="small_probe",
            direction="short",
            probe_source="contrarian_short_probe",
            probe_risk_tier="crowding",
            position_size_pct=0.5,
            initial_stop_loss=1.005,
            execution_allowed=True,
        )
    )

    assert decision.allowed is True
    assert decision.executable_size_pct == 0.005
    assert decision.account_risk_pct == 0.0015


def test_execution_risk_gate_blocks_when_exchange_min_qty_cannot_be_met() -> None:
    decision = ExecutionRiskGate(
        ExecutionRiskGateConfig(
            leverage=10,
            entry_margin_budget_usdt=None,
            max_probe_account_risk_pct=0.002,
            max_probe_size_pct=0.02,
            require_execution_allowed=True,
            exchange_min_order_qty=0.001,
            exchange_qty_step_size=0.001,
        )
    ).evaluate(
        handoff=_fresh_handoff(
            action="small_probe",
            direction="long",
            position_size_pct=0.1,
            initial_stop_loss=0.9844,
            execution_allowed=True,
        ),
        runtime_state={
            "runtime_account_equity": 10.0,
            "runtime_mark_price": 2300.0,
            "runtime_leverage": 10,
        },
    )

    assert decision.allowed is False
    assert decision.executable_size_pct == 0.0
    assert decision.reason_codes == ["account_too_small_for_exchange_min_qty"]


def test_execution_risk_gate_allows_when_rounded_exchange_qty_meets_min_qty() -> None:
    decision = ExecutionRiskGate(
        ExecutionRiskGateConfig(
            leverage=10,
            entry_margin_budget_usdt=None,
            max_probe_account_risk_pct=0.002,
            max_probe_size_pct=0.02,
            require_execution_allowed=True,
            exchange_min_order_qty=0.001,
            exchange_qty_step_size=0.001,
        )
    ).evaluate(
        handoff=_fresh_handoff(
            action="small_probe",
            direction="long",
            position_size_pct=0.1,
            initial_stop_loss=0.9844,
            execution_allowed=True,
        ),
        runtime_state={
            "runtime_account_equity": 20.0,
            "runtime_mark_price": 2300.0,
            "runtime_leverage": 10,
        },
    )

    assert decision.allowed is True
    assert decision.reason_codes == [
        "execution_risk_gate_pass",
        "size_truncated_by_bot_risk_gate",
    ]


def test_execution_risk_gate_caps_fixed_margin_probe_to_handoff_request() -> None:
    decision = ExecutionRiskGate(
        ExecutionRiskGateConfig(
            leverage=10,
            entry_margin_budget_usdt=10.0,
            max_probe_account_risk_pct=0.002,
            max_probe_size_pct=0.10,
            require_execution_allowed=True,
            exchange_min_order_qty=0.001,
            exchange_qty_step_size=0.001,
        )
    ).evaluate(
        handoff=_fresh_handoff(
            action="small_probe",
            direction="long",
            position_size_pct=0.1,
            initial_stop_loss=0.9844,
            execution_allowed=True,
        ),
        runtime_state={
            "runtime_account_equity": 11.0,
            "runtime_mark_price": 3150.0,
            "runtime_leverage": 10,
        },
    )

    assert decision.allowed is True
    assert decision.executable_size_pct == 0.1
    assert decision.size_cap_source == "bot_execution_risk_gate"
    assert decision.size_cap_reason == "requested_size_pct"
    assert decision.stop_distance_pct == 0.0156
    assert decision.account_risk_pct == 0.0156
    assert decision.reason_codes == [
        "execution_risk_gate_pass",
        "size_truncated_by_bot_risk_gate",
        "fixed_margin_budget_sizing",
        "small_account_budget_overrides_account_risk_cap",
    ]


def test_execution_risk_gate_falls_back_to_risk_sizing_when_account_exceeds_budget_mode() -> None:
    decision = ExecutionRiskGate(
        ExecutionRiskGateConfig(
            leverage=10,
            entry_margin_budget_usdt=10.0,
            entry_margin_budget_max_equity_usdt=50.0,
            max_account_risk_pct_per_trade=0.01,
            require_execution_allowed=True,
        )
    ).evaluate(
        handoff=_fresh_handoff(
            action="entry_long",
            position_size_pct=0.8,
            initial_stop_loss=0.98,
            execution_allowed=True,
        ),
        runtime_state={
            "runtime_account_equity": 1000.0,
            "runtime_mark_price": 3150.0,
            "runtime_leverage": 10,
        },
    )

    assert decision.allowed is True
    assert decision.executable_size_pct == 0.05
    assert decision.account_risk_pct == 0.01
    assert decision.reason_codes == ["execution_risk_gate_pass"]


def test_execution_risk_gate_falls_back_to_risk_sizing_when_demo_mode_is_disabled() -> None:
    decision = ExecutionRiskGate(
        ExecutionRiskGateConfig(
            leverage=10,
            demo_small_account_mode=False,
            entry_margin_budget_usdt=10.0,
            max_account_risk_pct_per_trade=0.01,
            require_execution_allowed=True,
        )
    ).evaluate(
        handoff=_fresh_handoff(
            action="entry_long",
            position_size_pct=0.8,
            initial_stop_loss=0.98,
            execution_allowed=True,
        ),
        runtime_state={
            "runtime_account_equity": 11.0,
            "runtime_mark_price": 3150.0,
            "runtime_leverage": 10,
        },
    )

    assert decision.allowed is True
    assert decision.executable_size_pct == 0.05
    assert decision.account_risk_pct == 0.01
    assert decision.reason_codes == ["execution_risk_gate_pass"]
