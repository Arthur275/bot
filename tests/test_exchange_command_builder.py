from bot.exchange_command_builder import build_idempotency_key, resolve_execution_warnings, resolve_take_profit_payloads
from bot.exchange_reconciliation import assess_runtime_reconciliation
from bot.exchange_models import AdapterRuntimeSnapshot, PositionSnapshot


def test_build_idempotency_key_prefers_package_scope() -> None:
    assert build_idempotency_key(
        target="entry_order",
        handoff={
            "source_run_id": "run-1",
            "generated_at": "2026-05-12T00:00:00",
            "action": "entry_long",
            "direction": "long",
        },
    ) == "entry_order:run-1:2026-05-12T00:00:00:entry_long:long"


def test_resolve_take_profit_payloads_from_ladder_and_fractions() -> None:
    payloads = resolve_take_profit_payloads(
        direction="long",
        handoff={
            "tp_ladder": [1.01, 1.02],
            "tp_reduce_fractions": [0.5, 0.5],
        },
    )

    assert [payload.level for payload in payloads] == [1, 2]
    assert [payload.reduce_fraction for payload in payloads] == [0.5, 0.5]


def test_resolve_execution_warnings_accepts_csv_and_list() -> None:
    assert resolve_execution_warnings({"execution_warnings": "a, b,, "}) == ["a", "b"]
    assert resolve_execution_warnings({"execution_warnings": ["a", "", "b"]}) == ["a", "b"]


def test_assess_runtime_reconciliation_detects_position_and_order_mismatch() -> None:
    result = assess_runtime_reconciliation(
        runtime_snapshot=AdapterRuntimeSnapshot(
            position=PositionSnapshot(position_state="FLAT", direction="neutral", size_pct=0.0),
            protective_stop_present=False,
        ),
        expected_position_state="ENTERED",
        expected_direction="long",
        expected_size_pct=0.25,
        supports_real_execution=True,
    )

    assert result.in_sync is False
    assert result.needs_position_sync is True
    assert result.needs_order_sync is True
    assert result.reason_codes == [
        "position_state_mismatch",
        "position_direction_mismatch",
        "position_size_mismatch",
        "protective_stop_missing",
    ]
