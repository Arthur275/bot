from __future__ import annotations

from .exchange_models import AdapterRuntimeSnapshot, ReconciliationResult


def assess_runtime_reconciliation(
    *,
    runtime_snapshot: AdapterRuntimeSnapshot,
    expected_position_state: str,
    expected_direction: str,
    expected_size_pct: float,
    supports_real_execution: bool,
) -> ReconciliationResult:
    if not runtime_snapshot.snapshot_valid:
        if not supports_real_execution:
            return ReconciliationResult(
                in_sync=True,
                protective_stop_present=False,
            )
        return ReconciliationResult(
            in_sync=False,
            protective_stop_present=False,
            needs_position_sync=True,
            needs_order_sync=False,
            reason_codes=["runtime_snapshot_unavailable"],
        )
    reason_codes: list[str] = []
    needs_position_sync = False
    needs_order_sync = False

    position = runtime_snapshot.position
    if position.position_state != expected_position_state:
        needs_position_sync = True
        reason_codes.append("position_state_mismatch")
    if position.direction != expected_direction:
        needs_position_sync = True
        reason_codes.append("position_direction_mismatch")
    if abs(float(position.size_pct) - float(expected_size_pct)) > 1e-9:
        needs_position_sync = True
        reason_codes.append("position_size_mismatch")
    if expected_size_pct > 0.0 and not runtime_snapshot.protective_stop_present:
        needs_order_sync = True
        reason_codes.append("protective_stop_missing")

    return ReconciliationResult(
        in_sync=not (needs_position_sync or needs_order_sync),
        protective_stop_present=runtime_snapshot.protective_stop_present,
        needs_position_sync=needs_position_sync,
        needs_order_sync=needs_order_sync,
        reason_codes=reason_codes,
    )
