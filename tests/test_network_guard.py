from bot.network_guard import NetworkGuard


def test_network_guard_blocks_pipeline_failure() -> None:
    guard = NetworkGuard().evaluate(
        judgement={
            "status": "blocked",
            "diagnostic": "request_diagnostic=none | category=pipeline | boundary=strict_live_blocks_before_policy",
            "research_bundle": {"ready": False, "bundle_status": "blocked"},
        },
        handoff=None,
    )
    assert guard.blocked is True
    assert guard.allow_entry is False
    assert guard.allow_reduce is False
    assert guard.allow_exit is False


def test_network_guard_degrades_transport_without_blocking_exit() -> None:
    guard = NetworkGuard().evaluate(
        judgement={
            "status": "blocked",
            "diagnostic": "request_diagnostic=transport | category=transport | boundary=request",
            "research_bundle": {"ready": True, "bundle_status": "healthy"},
        },
        handoff={
            "risk_filter_status": "pass",
            "research_gate_status": "open",
            "runtime_vetoes": [],
            "degrade_flags": [],
            "staleness_veto": False,
            "conflict_veto": False,
        },
    )
    assert guard.degraded is True
    assert guard.blocked is False
    assert guard.allow_entry is False
    assert guard.allow_exit is True


def test_network_guard_keeps_research_degraded_as_soft_penalty_when_research_gate_open() -> None:
    guard = NetworkGuard().evaluate(
        judgement={
            "status": "ok",
            "diagnostic": "",
            "research_bundle": {"ready": True, "bundle_status": "healthy"},
        },
        handoff={
            "action": "entry_long",
            "direction": "long",
            "risk_filter_status": "degraded",
            "research_gate_status": "open",
            "runtime_vetoes": [],
            "degrade_flags": ["research_degraded"],
            "staleness_veto": False,
            "conflict_veto": False,
        },
    )
    assert guard.degraded is True
    assert guard.allow_entry is True
    assert guard.allow_signal_tracking is True
    assert guard.allow_real_entry is False
    assert "risk_filter:degraded" in guard.reason_codes
    assert "degrade_flag:research_degraded" in guard.reason_codes


def test_network_guard_blocks_entry_when_research_gate_is_blocked() -> None:
    guard = NetworkGuard().evaluate(
        judgement={
            "status": "ok",
            "diagnostic": "",
            "research_bundle": {"ready": True, "bundle_status": "healthy"},
        },
        handoff={
            "action": "entry_long",
            "direction": "long",
            "risk_filter_status": "degraded",
            "research_gate_status": "blocked",
            "runtime_vetoes": [],
            "degrade_flags": ["research_degraded"],
            "staleness_veto": False,
            "conflict_veto": False,
        },
    )
    assert guard.degraded is True
    assert guard.allow_entry is False
    assert "runtime_entry_veto" in guard.reason_codes


def test_network_guard_allows_contrarian_short_entry_on_crowding_only_degrade() -> None:
    guard = NetworkGuard().evaluate(
        judgement={
            "status": "ok",
            "diagnostic": "",
            "research_bundle": {"ready": True, "bundle_status": "healthy"},
        },
        handoff={
            "action": "entry_short",
            "direction": "short",
            "risk_filter_status": "pass",
            "research_gate_status": "open",
            "runtime_vetoes": [],
            "degrade_flags": ["crowding_warning", "okx_longs_crowded"],
            "staleness_veto": False,
            "conflict_veto": False,
        },
    )
    assert guard.degraded is True
    assert guard.allow_entry is True
    assert guard.allow_signal_tracking is True
    assert guard.allow_real_entry is False
    assert "degrade_flag:crowding_warning" in guard.reason_codes
    assert "degrade_flag:okx_longs_crowded" in guard.reason_codes


def test_network_guard_keeps_blocking_short_entry_when_non_research_non_crowding_degrade_exists() -> None:
    guard = NetworkGuard().evaluate(
        judgement={
            "status": "ok",
            "diagnostic": "",
            "research_bundle": {"ready": True, "bundle_status": "healthy"},
        },
        handoff={
            "action": "entry_short",
            "direction": "short",
            "risk_filter_status": "pass",
            "research_gate_status": "open",
            "runtime_vetoes": [],
            "degrade_flags": ["okx_longs_crowded", "data_health_degraded"],
            "staleness_veto": False,
            "conflict_veto": False,
        },
    )
    assert guard.degraded is True
    assert guard.allow_entry is False
    assert "degrade_flag:okx_longs_crowded" in guard.reason_codes
    assert "degrade_flag:data_health_degraded" in guard.reason_codes


def test_network_guard_allows_trend_continuation_small_probe_on_crowding_degrade() -> None:
    guard = NetworkGuard().evaluate(
        judgement={
            "status": "ok",
            "diagnostic": "",
            "research_bundle": {"ready": True, "bundle_status": "healthy"},
        },
        handoff={
            "action": "small_probe",
            "direction": "long",
            "probe_source": "trend_continuation_probe",
            "risk_filter_status": "degraded",
            "research_gate_status": "open",
            "runtime_vetoes": [],
            "degrade_flags": ["crowding_warning", "research_degraded"],
            "staleness_veto": False,
            "conflict_veto": False,
        },
    )

    assert guard.degraded is True
    assert guard.allow_entry is True
    assert guard.allow_signal_tracking is True
    assert guard.allow_real_entry is False
    assert "degrade_flag:crowding_warning" in guard.reason_codes
    assert "degrade_flag:research_degraded" in guard.reason_codes


def test_network_guard_keeps_blocking_long_crowding_entry_without_probe_source() -> None:
    guard = NetworkGuard().evaluate(
        judgement={
            "status": "ok",
            "diagnostic": "",
            "research_bundle": {"ready": True, "bundle_status": "healthy"},
        },
        handoff={
            "action": "entry_long",
            "direction": "long",
            "risk_filter_status": "degraded",
            "research_gate_status": "open",
            "runtime_vetoes": [],
            "degrade_flags": ["crowding_warning", "research_degraded"],
            "staleness_veto": False,
            "conflict_veto": False,
        },
    )

    assert guard.degraded is True
    assert guard.allow_entry is False


def test_network_guard_blocks_contrarian_probe_without_orderbook_short_pressure() -> None:
    guard = NetworkGuard().evaluate(
        judgement={
            "status": "ok",
            "diagnostic": "",
            "research_bundle": {"ready": True, "bundle_status": "healthy"},
        },
        handoff={
            "action": "small_probe",
            "direction": "short",
            "probe_source": "contrarian_short_probe",
            "orderbook_short_pressure": False,
            "risk_filter_status": "degraded",
            "research_gate_status": "open",
            "runtime_vetoes": [],
            "degrade_flags": ["research_degraded"],
            "staleness_veto": False,
            "conflict_veto": False,
        },
    )

    assert guard.degraded is True
    assert guard.allow_entry is False
    assert "contrarian_probe_orderbook_pressure_missing" in guard.reason_codes


def test_network_guard_allows_contrarian_probe_with_orderbook_short_pressure() -> None:
    guard = NetworkGuard().evaluate(
        judgement={
            "status": "ok",
            "diagnostic": "",
            "research_bundle": {"ready": True, "bundle_status": "healthy"},
        },
        handoff={
            "action": "small_probe",
            "direction": "short",
            "probe_source": "contrarian_short_probe",
            "orderbook_short_pressure": True,
            "risk_filter_status": "degraded",
            "research_gate_status": "open",
            "runtime_vetoes": [],
            "degrade_flags": ["research_degraded"],
            "staleness_veto": False,
            "conflict_veto": False,
        },
    )

    assert guard.degraded is True
    assert guard.allow_entry is True
    assert guard.allow_real_entry is False


def test_network_guard_blocks_unavailable_risk_filter_status_for_real_entry() -> None:
    for status in ("unavailable", "research_unavailable", "unknown_future_status"):
        guard = NetworkGuard().evaluate(
            judgement={
                "status": "ok",
                "diagnostic": "",
                "research_bundle": {"ready": True, "bundle_status": "healthy"},
            },
            handoff={
                "action": "entry_long",
                "direction": "long",
                "risk_filter_status": status,
                "research_gate_status": "open",
                "runtime_vetoes": [],
                "degrade_flags": [],
                "staleness_veto": False,
                "conflict_veto": False,
            },
        )

        assert guard.allow_entry is False
        assert guard.allow_real_entry is False
        assert guard.allow_signal_tracking is False
        assert f"risk_filter:{status}" in guard.reason_codes
