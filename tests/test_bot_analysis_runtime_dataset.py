from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from bot.audit_logger import AuditLogger
from bot.analysis.duckdb_store import connect_duckdb
from bot.analysis.runtime_dataset import ingest_audit_log, write_runtime_summary

pytest.importorskip("duckdb")


def test_bot_runtime_dataset_ingests_audit_log_idempotently(tmp_path: Path) -> None:
    audit_path = tmp_path / "audit.jsonl"
    db_path = tmp_path / "bot_runtime.duckdb"
    _write_cycle(audit_path)

    first = ingest_audit_log(audit_log_path=audit_path, db_path=db_path)
    second = ingest_audit_log(audit_log_path=audit_path, db_path=db_path)

    assert first == {
        "events_seen": 1,
        "cycles_upserted": 1,
        "commands_upserted": 2,
        "runtime_summaries_upserted": 1,
    }
    assert second == first
    conn = connect_duckdb(db_path)
    try:
        assert conn.execute("SELECT count(*) FROM bot_cycles").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM bot_command_samples").fetchone()[0] == 2
        assert conn.execute("SELECT count(*) FROM bot_runtime_summaries").fetchone()[0] == 1
        command = conn.execute(
            """
            SELECT target, operation, status, idempotency_key, client_order_id, error_kind
            FROM bot_command_samples
            WHERE target = 'entry_order'
            """
        ).fetchone()
    finally:
        conn.close()
    assert command == ("entry_order", "place", "accepted", "entry-1", "cid-1", "")


def test_bot_runtime_summary_writes_json_and_markdown(tmp_path: Path) -> None:
    audit_path = tmp_path / "audit.jsonl"
    db_path = tmp_path / "bot_runtime.duckdb"
    output_json_path = tmp_path / "bot_runtime_summary.json"
    output_md_path = tmp_path / "bot_runtime_summary.md"
    _write_cycle(audit_path)
    ingest_audit_log(audit_log_path=audit_path, db_path=db_path)

    summary = write_runtime_summary(
        db_path=db_path,
        output_json_path=output_json_path,
        output_md_path=output_md_path,
    )

    assert summary["total_cycles"] == 1
    assert summary["total_commands"] == 2
    assert summary["command_status_counts"] == {"accepted": 1, "simulated": 1}
    assert json.loads(output_json_path.read_text(encoding="utf-8"))["total_cycles"] == 1
    assert "Bot Runtime Summary" in output_md_path.read_text(encoding="utf-8")


def test_bot_runtime_dataset_matches_results_to_commands_by_target_when_indexes_shift(tmp_path: Path) -> None:
    audit_path = tmp_path / "audit.jsonl"
    db_path = tmp_path / "bot_runtime.duckdb"
    AuditLogger(audit_path).append(
        event_type="shadow_cycle",
        generated_at=datetime(2026, 5, 4, 12, 1, 0),
        payload={
            "runtime_mode": "real",
            "engine_mode": "strict-live",
            "handoff": {"action": "entry_long"},
            "effective_action": "entry_long",
            "action_summary": {"blocked": False, "degraded": False},
            "state": {"execution_state": "idle", "automation_state": "action_blocked"},
            "automation_state": "action_blocked",
            "runtime_snapshot_before": {"position": {"position_state": "FLAT", "direction": "neutral", "size_pct": 0.0}},
            "runtime_snapshot_after": {"position": {"position_state": "FLAT", "direction": "neutral", "size_pct": 0.0}},
            "execution_commands": [
                {"target": "manual_entry_confirmation", "operation": "confirm", "command_type": "manual_gate"},
                {"target": "entry_order", "operation": "place", "command_type": "entry_order"},
                {"target": "maintain_protective_stop", "operation": "upsert", "command_type": "protective_stop"},
            ],
            "execution_results": [
                {"target": "entry_order", "status": "blocked", "accepted": False, "simulated": True, "reason": "manual_entry_confirmation_required"},
                {"target": "maintain_protective_stop", "status": "blocked", "accepted": False, "simulated": True, "reason": "manual_entry_confirmation_required"},
            ],
        },
    )

    ingest_audit_log(audit_log_path=audit_path, db_path=db_path)

    conn = connect_duckdb(db_path)
    try:
        rows = conn.execute(
            """
            SELECT target, operation, command_type
            FROM bot_command_samples
            ORDER BY command_index
            """
        ).fetchall()
    finally:
        conn.close()
    assert rows == [
        ("entry_order", "place", "entry_order"),
        ("maintain_protective_stop", "upsert", "protective_stop"),
    ]


def _write_cycle(audit_path: Path) -> None:
    AuditLogger(audit_path).append(
        event_type="shadow_cycle",
        generated_at=datetime(2026, 5, 4, 12, 0, 0),
        payload={
            "runtime_mode": "real",
            "engine_mode": "strict-live",
            "handoff": {
                "symbol": "ETH",
                "exchange_symbol": "ETHUSDT",
                "action": "entry_long",
            },
            "effective_action": "entry_long",
            "action_summary": {"blocked": False, "degraded": False},
            "reason_codes": [],
            "state": {
                "execution_state": "position_open",
                "automation_state": "entry_submitted",
            },
            "automation_state": "entry_submitted",
            "runtime_snapshot_before": {
                "position": {"position_state": "FLAT", "direction": "neutral", "size_pct": 0.0},
                "protective_stop_present": False,
            },
            "runtime_snapshot_after": {
                "position": {"position_state": "ENTERED", "direction": "long", "size_pct": 0.3},
                "protective_stop_present": True,
            },
            "execution_commands": [
                {
                    "target": "entry_order",
                    "operation": "place",
                    "command_type": "entry_order",
                },
                {
                    "target": "maintain_protective_stop",
                    "operation": "upsert",
                    "command_type": "protective_stop",
                },
            ],
            "execution_results": [
                {
                    "target": "entry_order",
                    "status": "accepted",
                    "accepted": True,
                    "simulated": False,
                    "reason": "effective_action:entry_long",
                    "idempotency_key": "entry-1",
                    "client_order_id": "cid-1",
                    "exchange_order_id": "oid-1",
                    "error_kind": "",
                    "details": {},
                },
                {
                    "target": "maintain_protective_stop",
                    "status": "simulated",
                    "accepted": True,
                    "simulated": True,
                    "reason": "protective_stop_required",
                    "idempotency_key": "stop-1",
                    "client_order_id": "",
                    "exchange_order_id": "",
                    "error_kind": "",
                    "details": {},
                },
            ],
        },
    )
