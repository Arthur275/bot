from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .duckdb_store import connect_duckdb


def ingest_audit_log(*, audit_log_path: str | Path, db_path: str | Path | None = None) -> dict[str, int]:
    path = Path(audit_log_path)
    conn = connect_duckdb(db_path)
    try:
        return ingest_audit_log_with_connection(conn=conn, audit_log_path=path)
    finally:
        conn.close()


def ingest_audit_log_with_connection(*, conn: Any, audit_log_path: str | Path) -> dict[str, int]:
    path = Path(audit_log_path)
    counts = {"events_seen": 0, "cycles_upserted": 0, "commands_upserted": 0, "runtime_summaries_upserted": 0}
    if not path.exists():
        return counts
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        event = json.loads(line)
        event_type = str(event.get("event_type") or "")
        if event_type not in {"shadow_cycle", "risk_assist_cycle"}:
            continue
        counts["events_seen"] += 1
        payload = event.get("payload") or {}
        run_id = _build_run_id(path=path, line_no=line_no)
        generated_at = _parse_timestamp(event.get("generated_at"))
        conn.execute("DELETE FROM bot_cycles WHERE run_id = ?", [run_id])
        conn.execute("DELETE FROM bot_command_samples WHERE run_id = ?", [run_id])
        conn.execute("DELETE FROM bot_runtime_summaries WHERE run_id = ?", [run_id])
        conn.execute(
            """
            INSERT INTO bot_cycles VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                run_id,
                str(path),
                line_no,
                event_type,
                generated_at,
                _string(payload.get("runtime_mode")),
                _string(payload.get("engine_mode")),
                _string((payload.get("handoff") or {}).get("symbol") or "ETH"),
                _string((payload.get("handoff") or {}).get("exchange_symbol") or "ETHUSDT"),
                _string((payload.get("handoff") or {}).get("action")),
                _string(payload.get("effective_action")),
                _string((payload.get("state") or {}).get("execution_state")),
                _string(payload.get("automation_state") or (payload.get("state") or {}).get("automation_state")),
                bool((payload.get("action_summary") or {}).get("blocked", False)),
                bool((payload.get("action_summary") or {}).get("degraded", False)),
                json.dumps(payload.get("reason_codes") or [], ensure_ascii=False, sort_keys=True),
                str(path),
                _string((payload.get("state") or {}).get("state_path")),
            ],
        )
        counts["cycles_upserted"] += 1
        commands = list(payload.get("execution_commands") or [])
        results = list(payload.get("execution_results") or [])
        for index, result in enumerate(results):
            if not isinstance(result, dict):
                continue
            command = _match_command_for_result(commands=commands, result=result, result_index=index)
            conn.execute(
                """
                INSERT INTO bot_command_samples VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    run_id,
                    index,
                    generated_at,
                    _string(result.get("target")),
                    _string(command.get("operation") or (result.get("details") or {}).get("operation")),
                    _string(result.get("status")),
                    bool(result.get("accepted", False)),
                    bool(result.get("simulated", False)),
                    _string(result.get("reason")),
                    _string(result.get("idempotency_key")),
                    _string(result.get("client_order_id")),
                    _string(result.get("exchange_order_id")),
                    _string(result.get("error_kind")),
                    _string(command.get("command_type") or (result.get("details") or {}).get("command_type")),
                    _string(payload.get("runtime_mode")),
                ],
            )
            counts["commands_upserted"] += 1
        before = payload.get("runtime_snapshot_before") or {}
        after = payload.get("runtime_snapshot_after") or payload.get("runtime_snapshot") or {}
        conn.execute(
            """
            INSERT INTO bot_runtime_summaries VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                run_id,
                generated_at,
                _position_value(before, "position_state"),
                _position_value(before, "direction"),
                _position_float(before, "size_pct"),
                bool(before.get("protective_stop_present", False)),
                _position_value(after, "position_state"),
                _position_value(after, "direction"),
                _position_float(after, "size_pct"),
                bool(after.get("protective_stop_present", False)),
            ],
        )
        counts["runtime_summaries_upserted"] += 1
    return counts


def _match_command_for_result(*, commands: list[Any], result: dict[str, Any], result_index: int) -> dict[str, Any]:
    target = str(result.get("target") or "")
    target_matches = [
        command
        for command in commands
        if isinstance(command, dict) and str(command.get("target") or "") == target
    ]
    if len(target_matches) == 1:
        return target_matches[0]
    if result_index < len(commands) and isinstance(commands[result_index], dict):
        indexed = commands[result_index]
        if not target or str(indexed.get("target") or "") == target:
            return indexed
    return {}


def write_runtime_summary(
    *,
    db_path: str | Path | None = None,
    output_json_path: str | Path,
    output_md_path: str | Path,
) -> dict[str, Any]:
    conn = connect_duckdb(db_path)
    try:
        summary = build_runtime_summary(conn=conn)
    finally:
        conn.close()
    json_path = Path(output_json_path)
    md_path = Path(output_md_path)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    md_path.write_text(_render_markdown_summary(summary), encoding="utf-8")
    return summary


def build_runtime_summary(*, conn: Any) -> dict[str, Any]:
    total_cycles = conn.execute("SELECT count(*) FROM bot_cycles").fetchone()[0]
    total_commands = conn.execute("SELECT count(*) FROM bot_command_samples").fetchone()[0]
    latest = conn.execute(
        """
        SELECT generated_at, runtime_mode, quant_action, bot_effective_action, execution_layer_state, automation_state
        FROM bot_cycles ORDER BY generated_at DESC NULLS LAST, source_line DESC LIMIT 1
        """
    ).fetchone()
    statuses = conn.execute(
        "SELECT status, count(*) FROM bot_command_samples GROUP BY status ORDER BY status"
    ).fetchall()
    return {
        "total_cycles": int(total_cycles),
        "total_commands": int(total_commands),
        "latest_cycle": {
            "generated_at": str(latest[0]) if latest else "",
            "runtime_mode": latest[1] if latest else "",
            "quant_action": latest[2] if latest else "",
            "bot_effective_action": latest[3] if latest else "",
            "execution_layer_state": latest[4] if latest else "",
            "automation_state": latest[5] if latest else "",
        },
        "command_status_counts": {str(status or ""): int(count) for status, count in statuses},
    }


def _render_markdown_summary(summary: dict[str, Any]) -> str:
    latest = summary["latest_cycle"]
    lines = [
        "# Bot Runtime Summary",
        "",
        f"- total_cycles: {summary['total_cycles']}",
        f"- total_commands: {summary['total_commands']}",
        f"- latest_generated_at: {latest['generated_at']}",
        f"- latest_action: {latest['quant_action']} -> {latest['bot_effective_action']}",
        f"- latest_states: {latest['execution_layer_state']} / {latest['automation_state']}",
        "",
        "## Command Status Counts",
        "",
    ]
    for status, count in summary["command_status_counts"].items():
        lines.append(f"- {status}: {count}")
    lines.append("")
    return "\n".join(lines)


def _build_run_id(*, path: Path, line_no: int) -> str:
    return f"{path.resolve()}:{line_no}"


def _parse_timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _string(value: Any) -> str:
    return "" if value is None else str(value)


def _position_value(snapshot: dict[str, Any], key: str) -> str:
    position = snapshot.get("position") or {}
    return _string(position.get(key))


def _position_float(snapshot: dict[str, Any], key: str) -> float:
    position = snapshot.get("position") or {}
    try:
        return float(position.get(key) or 0.0)
    except (TypeError, ValueError):
        return 0.0
