from bot.execution_risk_gate import ExecutionRiskGate, ExecutionRiskGateConfig


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
        handoff={"action": "entry_short", "position_size_pct": 0.2, "execution_allowed": True}
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
        handoff={
            "action": "entry_long",
            "position_size_pct": 0.8,
            "initial_stop_loss": 0.98,
            "execution_allowed": True,
        }
    )

    assert decision.allowed is True
    assert decision.executable_size_pct == 0.05
    assert decision.stop_distance_pct == 0.02
    assert decision.account_risk_pct == 0.01
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
        handoff={
            "action": "small_probe",
            "direction": "short",
            "position_size_pct": 0.5,
            "initial_stop_loss": 1.005,
            "execution_allowed": True,
        }
    )

    assert decision.allowed is True
    assert decision.executable_size_pct == 0.02
    assert decision.stop_distance_pct == 0.005
    assert decision.account_risk_pct == 0.002


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
        handoff={
            "action": "small_probe",
            "direction": "short",
            "probe_source": "contrarian_short_probe",
            "probe_risk_tier": "technical",
            "position_size_pct": 0.5,
            "initial_stop_loss": 1.005,
            "execution_allowed": True,
        }
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
        handoff={
            "action": "small_probe",
            "direction": "short",
            "probe_source": "contrarian_short_probe",
            "probe_risk_tier": "crowding",
            "position_size_pct": 0.5,
            "initial_stop_loss": 1.005,
            "execution_allowed": True,
        }
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
        handoff={
            "action": "small_probe",
            "direction": "long",
            "position_size_pct": 0.1,
            "initial_stop_loss": 0.9844,
            "execution_allowed": True,
        },
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
        handoff={
            "action": "small_probe",
            "direction": "long",
            "position_size_pct": 0.1,
            "initial_stop_loss": 0.9844,
            "execution_allowed": True,
        },
        runtime_state={
            "runtime_account_equity": 20.0,
            "runtime_mark_price": 2300.0,
            "runtime_leverage": 10,
        },
    )

    assert decision.allowed is True
    assert decision.reason_codes == ["execution_risk_gate_pass"]


def test_execution_risk_gate_uses_fixed_margin_budget_when_runtime_equity_is_available() -> None:
    decision = ExecutionRiskGate(
        ExecutionRiskGateConfig(
            leverage=10,
            entry_margin_budget_usdt=10.0,
            max_probe_account_risk_pct=0.002,
            max_probe_size_pct=0.02,
            require_execution_allowed=True,
            exchange_min_order_qty=0.001,
            exchange_qty_step_size=0.001,
        )
    ).evaluate(
        handoff={
            "action": "small_probe",
            "direction": "long",
            "position_size_pct": 0.1,
            "initial_stop_loss": 0.9844,
            "execution_allowed": True,
        },
        runtime_state={
            "runtime_account_equity": 11.0,
            "runtime_mark_price": 3150.0,
            "runtime_leverage": 10,
        },
    )

    assert decision.allowed is True
    assert decision.executable_size_pct == 0.909091
    assert decision.stop_distance_pct == 0.0156
    assert decision.account_risk_pct == 0.141818
    assert decision.reason_codes == [
        "execution_risk_gate_pass",
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
        handoff={
            "action": "entry_long",
            "position_size_pct": 0.8,
            "initial_stop_loss": 0.98,
            "execution_allowed": True,
        },
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
        handoff={
            "action": "entry_long",
            "position_size_pct": 0.8,
            "initial_stop_loss": 0.98,
            "execution_allowed": True,
        },
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
