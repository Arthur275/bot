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


def test_network_guard_turns_off_entry_when_degrade_flags_exist() -> None:
    guard = NetworkGuard().evaluate(
        judgement={
            "status": "ok",
            "diagnostic": "",
            "research_bundle": {"ready": True, "bundle_status": "healthy"},
        },
        handoff={
            "risk_filter_status": "pass",
            "runtime_vetoes": [],
            "degrade_flags": ["research_degraded"],
            "staleness_veto": False,
            "conflict_veto": False,
        },
    )
    assert guard.degraded is True
    assert guard.allow_entry is False
    assert "degrade_flag:research_degraded" in guard.reason_codes
