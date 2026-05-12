from types import SimpleNamespace

from bot.exchange_adapter import AdapterRuntimeSnapshot, PositionSnapshot, ReconciliationResult
from bot.orchestrator_reporting import (
    summarize_action,
    summarize_command_results,
    summarize_commands,
    summarize_execution_overview,
    summarize_runtime_overview,
)


def test_summarize_execution_overview_prefers_primary_command_and_result() -> None:
    commands = [
        SimpleNamespace(target="maintain_protective_stop", reason="protect", operation="place"),
        SimpleNamespace(target="entry_order", reason="entry", operation="place"),
    ]
    results = [
        SimpleNamespace(target="entry_order", reason="accepted", status="accepted", accepted=True),
    ]

    assert summarize_execution_overview(
        requested_action="entry_long",
        effective_action="entry_long",
        execution_commands=commands,
        execution_results=results,
        execution_result_summary={"primary_failed": False, "auxiliary_failed": False},
    ) == {
        "requested_action": "entry_long",
        "effective_action": "entry_long",
        "primary_target": "entry_order",
        "primary_reason": "entry",
        "primary_status": "accepted",
        "primary_accepted": True,
        "has_primary_failure": False,
        "has_auxiliary_failure": False,
        "auxiliary_targets": ["maintain_protective_stop"],
    }


def test_summarize_runtime_overview_maps_state_and_reconciliation() -> None:
    state = SimpleNamespace(
        observed_position_state="ENTERED",
        observed_position_direction="long",
        observed_position_size_pct=0.25,
        execution_state=SimpleNamespace(value="idle"),
        pending_action="",
        recovery_required=False,
        reconciliation_required=False,
        protective_stop_required=True,
        recent_fill_summary={"fills": 1},
    )

    payload = summarize_runtime_overview(
        expected_position_state="ENTERED",
        expected_direction="long",
        expected_size_pct=0.25,
        runtime_snapshot=AdapterRuntimeSnapshot(
            position=PositionSnapshot(position_state="ENTERED", direction="long", size_pct=0.25),
            protective_stop_present=True,
        ),
        reconciliation=ReconciliationResult(in_sync=True, reason_codes=["ok"]),
        updated_state=state,
    )

    assert payload["runtime_position_state"] == "ENTERED"
    assert payload["observed_direction"] == "long"
    assert payload["reconciliation_reason_codes"] == ["ok"]
    assert payload["recent_fill_summary"] == {"fills": 1}


def test_summarize_action_commands_and_results() -> None:
    guard = SimpleNamespace(blocked=False, degraded=True, reason_codes=["research_degraded"])
    reconciliation = ReconciliationResult(in_sync=False, reason_codes=["size_mismatch"])
    command = SimpleNamespace(target="entry_order", reason="entry", operation="place")
    result = SimpleNamespace(
        target="entry_order",
        reason="accepted",
        status="accepted",
        accepted=True,
        simulated=False,
        idempotency_key="idem",
        client_order_id="client",
        exchange_order_id="exchange",
        error_kind="",
    )

    assert summarize_action(
        requested_action="entry_long",
        effective_action="entry_long",
        plan_reason="ready",
        guard=guard,
        reconciliation=reconciliation,
    )["guard_reason_codes"] == ["research_degraded"]
    assert summarize_commands([command]) == [{"target": "entry_order", "reason": "entry", "operation": "place"}]
    assert summarize_command_results([result])[0]["exchange_order_id"] == "exchange"
