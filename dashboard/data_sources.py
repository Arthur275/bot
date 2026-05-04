from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .status_rules import kill_switch_status, lookup_status, runtime_status


BOT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_QUANT_ROOT = BOT_ROOT.parent / "quant_system_rebuild"


@dataclass(frozen=True)
class DashboardPaths:
    bot_root: Path = BOT_ROOT
    quant_root: Path = DEFAULT_QUANT_ROOT

    @classmethod
    def from_env(cls) -> "DashboardPaths":
        return cls(
            bot_root=Path(os.environ.get("ETH_BOT_ROOT") or BOT_ROOT),
            quant_root=Path(os.environ.get("QUANT_ROOT") or DEFAULT_QUANT_ROOT),
        )


def load_dashboard_snapshot(paths: DashboardPaths | None = None) -> dict[str, Any]:
    paths = paths or DashboardPaths.from_env()
    bot_runtime = paths.bot_root / "runtime"
    quant_runtime = paths.quant_root / "runtime"
    bot_scheduler_root = bot_runtime / "bot_runtime_scheduler"
    quant_analysis_root = quant_runtime / "analysis"
    quant_scheduler_root = quant_runtime / "scheduler"

    bot_heartbeat = _read_json(bot_scheduler_root / "heartbeat.json")
    bot_cycle = _read_json(bot_scheduler_root / "latest_cycle.json")
    bot_state = _read_json(bot_runtime / "state_store.json")
    candidate = _read_json(bot_scheduler_root / "latest_candidate_execution_package.json")
    worker_audit = _tail_jsonl(bot_runtime / "real_order_worker" / "audit.jsonl", limit=8)
    bot_samples = _jsonl_count(bot_scheduler_root / "samples.jsonl")

    quant_heartbeat = _read_json(quant_scheduler_root / "heartbeat.json")
    factor_summary = _read_json(quant_analysis_root / "factor_summary.json")
    factor_ingest = _read_json(quant_analysis_root / "factor_ingest_latest.json")
    factor_lookup = _read_latest_lookup(paths.quant_root)
    quant_handoff = _read_latest_handoff(paths.quant_root)
    quant_cycle = _read_latest_quant_cycle(paths.quant_root)
    quant_decision = quant_cycle.get("decision", {})
    quant_risk = quant_decision.get("risk_report", {}) if isinstance(quant_decision, dict) else {}
    quant_regime = quant_decision.get("regime_state", {}) if isinstance(quant_decision, dict) else {}
    quant_scheduler_status = quant_cycle.get("scheduler_status", {})
    quant_db_counts = _read_quant_duckdb_counts(quant_analysis_root / "quant_analysis.duckdb")

    kill_switch_path = bot_runtime / "controls" / "disable_real_execution.flag"
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "paths": {
            "bot_root": str(paths.bot_root),
            "quant_root": str(paths.quant_root),
            "kill_switch_path": str(kill_switch_path),
        },
        "runtime": {
            "factor_collector": runtime_status(
                generated_at=factor_ingest.get("generated_at") or _mtime_iso(quant_analysis_root / "factor_summary.json"),
                ok=bool(factor_summary or factor_ingest or quant_db_counts.get("factor_samples")),
                stale_after_sec=30 * 60,
                error=str(factor_ingest.get("error") or ""),
            ),
            "quant_scheduler": runtime_status(
                generated_at=quant_scheduler_status.get("generated_at") or quant_heartbeat.get("generated_at") or quant_cycle.get("generated_at") or quant_handoff.get("generated_at"),
                ok=str(quant_scheduler_status.get("status") or quant_heartbeat.get("status") or "ok") not in {"error", "blocked"},
                stale_after_sec=30 * 60,
                error=str(quant_scheduler_status.get("error") or quant_heartbeat.get("error") or ""),
            ),
            "bot_scheduler": runtime_status(
                generated_at=bot_heartbeat.get("generated_at") or bot_cycle.get("finished_at"),
                ok=str(bot_heartbeat.get("status") or "") in {"ok", "degraded", ""},
                stale_after_sec=15 * 60,
                error=str(bot_cycle.get("error") or ""),
            ),
            "real_worker": _worker_status(worker_audit=worker_audit, candidate=candidate),
            "kill_switch": kill_switch_status(enabled=kill_switch_path.exists()),
        },
        "factor": {
            "total_samples": _int(factor_summary.get("total_samples"), fallback=quant_db_counts.get("factor_samples", 0)),
            "unique_observations": _int(factor_summary.get("unique_observation_count")),
            "lookup_version": factor_lookup.get("lookup_version", ""),
            "lookup_rows": _int(factor_lookup.get("factor_lookup_rows"), fallback=quant_db_counts.get("factor_lookup", 0)),
            "lookup_status": lookup_status(
                generated_at=factor_lookup.get("generated_at") or _mtime_iso(quant_analysis_root / "factor_summary.json"),
                stale=bool(factor_lookup.get("factor_lookup_stale", False)),
            ),
            "top_reason_codes": factor_summary.get("top_reason_codes", [])[:8],
            "top_degrade_flags": factor_summary.get("top_degrade_flags", [])[:8],
            "sample_growth": {
                "bot_scheduler_samples": bot_samples,
                "factor_values": quant_db_counts.get("factor_values", 0),
            },
            "db_available": bool(quant_db_counts),
        },
        "quant": {
            "action": bot_cycle.get("effective_action") or quant_decision.get("action") or quant_handoff.get("action") or "",
            "direction": bot_cycle.get("direction") or quant_decision.get("direction") or quant_handoff.get("direction") or "",
            "risk_filter_status": bot_cycle.get("risk_filter_status") or quant_risk.get("risk_filter_status") or quant_handoff.get("risk_filter_status") or "",
            "confidence": bot_cycle.get("confidence") or quant_decision.get("confidence") or quant_handoff.get("confidence"),
            "sizing_tier": bot_cycle.get("sizing_tier") or quant_decision.get("sizing_tier") or _nested(quant_decision, "sizing_decision", "sizing_tier") or quant_handoff.get("sizing_tier") or "",
            "reasoning_summary": bot_cycle.get("reasoning_summary") or quant_decision.get("reasoning_summary") or quant_handoff.get("reasoning_summary") or "",
            "supporting_factors": quant_handoff.get("supporting_factor_codes", [])[:10],
            "opposing_factors": quant_handoff.get("opposing_factor_codes", [])[:10],
            "veto_factors": quant_handoff.get("veto_factor_codes", [])[:10],
            "degrade_flags": bot_cycle.get("degrade_flags") or quant_risk.get("degrade_flags") or quant_handoff.get("degrade_flags") or [],
            "regime_bucket": quant_handoff.get("regime_bucket", "") or _regime_bucket(quant_regime),
            "factor_lookup_version": quant_handoff.get("factor_lookup_version", "") or factor_lookup.get("lookup_version", ""),
            "factor_lookup_stale": bool(quant_handoff.get("factor_lookup_stale", False)),
            "execution_warnings": quant_handoff.get("execution_warnings", []),
            "automation_boundary": bot_cycle.get("automation_boundary", ""),
        },
        "bot": {
            "execution_state": bot_state.get("execution_state", ""),
            "automation_state": bot_state.get("automation_state", ""),
            "position_state": bot_state.get("observed_position_state", ""),
            "position_direction": bot_state.get("observed_position_direction", ""),
            "position_size_pct": bot_state.get("observed_position_size_pct", 0.0),
            "protective_stop_required": bool(bot_state.get("protective_stop_required", False)),
            "candidate_package": _candidate_summary(candidate),
            "automation_boundary": bot_cycle.get("automation_boundary", ""),
            "worker_events": worker_audit,
            "latest_cycle": {
                "sample_id": bot_cycle.get("sample_id"),
                "finished_at": bot_cycle.get("finished_at"),
                "requested_action": bot_cycle.get("requested_action"),
                "effective_action": bot_cycle.get("effective_action"),
                "preflight_error": bot_cycle.get("preflight_error", ""),
                "reason_codes": bot_cycle.get("reason_codes", []),
            },
        },
    }


def _worker_status(*, worker_audit: list[dict[str, Any]], candidate: dict[str, Any]) -> dict[str, Any]:
    if not worker_audit:
        if candidate:
            return {"label": "READY", "level": "yellow", "age_sec": None}
        return {"label": "DISABLED", "level": "gray", "age_sec": None}
    latest = worker_audit[-1]
    payload = latest.get("payload") or {}
    status = str(payload.get("status") or "")
    return runtime_status(
        generated_at=latest.get("generated_at"),
        ok=status in {"submitted", "skipped"} or not status,
        stale_after_sec=15 * 60,
        error="" if status != "blocked" else ",".join(payload.get("reason_codes") or ["blocked"]),
    )


def _candidate_summary(candidate: dict[str, Any]) -> dict[str, Any]:
    if not candidate:
        return {"present": False}
    return {
        "present": True,
        "package_id": candidate.get("package_id", ""),
        "action": candidate.get("action", ""),
        "direction": candidate.get("direction", ""),
        "generated_at": candidate.get("generated_at", ""),
        "expires_at": candidate.get("expires_at", ""),
        "gate_allowed": bool((candidate.get("real_order_gate") or {}).get("allowed", False)),
        "command_targets": [item.get("target") for item in candidate.get("execution_commands") or []],
    }


def _read_latest_lookup(quant_root: Path) -> dict[str, Any]:
    candidates = [
        quant_root / "runtime" / "analysis" / "factor_lookup_summary.json",
        quant_root / "runtime" / "analysis" / "factor_summary.json",
    ]
    for path in candidates:
        payload = _read_json(path)
        if payload:
            return payload
    return {}


def _read_latest_handoff(quant_root: Path) -> dict[str, Any]:
    cycle_roots = [
        quant_root / "runtime" / "cycles" / "latest_strict_live",
        quant_root / "runtime" / "cycles" / "latest_strict_live_after_research_refresh",
        quant_root / "runtime" / "cycles" / "latest_strict_live_research_impact_check",
    ]
    for root in cycle_roots:
        for name in ("handoff.json", "execution_handoff.json"):
            payload = _read_json(root / name)
            if payload:
                return payload
    return {}


def _read_latest_quant_cycle(quant_root: Path) -> dict[str, Any]:
    cycles_root = quant_root / "runtime" / "cycles"
    try:
        roots = sorted(
            [path for path in cycles_root.iterdir() if path.is_dir()],
            key=lambda path: _cycle_sort_timestamp(path),
            reverse=True,
        )
    except OSError:
        return {}
    for root in roots:
        scheduler_status = _read_json(root / "scheduler_status.json")
        if not scheduler_status:
            continue
        decision_payload = _read_json(root / "decision.json")
        if not decision_payload:
            continue
        return {
            "cycle_dir": str(root),
            "generated_at": scheduler_status.get("generated_at") or decision_payload.get("generated_at") or _mtime_iso(root / "scheduler_status.json"),
            "decision": decision_payload.get("decision") or {},
            "scheduler_status": scheduler_status,
        }
    for root in roots[:20]:
        decision_payload = _read_json(root / "decision.json")
        if not decision_payload:
            continue
        return {
            "cycle_dir": str(root),
            "generated_at": decision_payload.get("generated_at") or _mtime_iso(root / "decision.json"),
            "decision": decision_payload.get("decision") or {},
            "scheduler_status": {},
        }
    return {}


def _cycle_sort_timestamp(root: Path) -> float:
    for name in ("scheduler_status.json", "decision.json"):
        payload = _read_json(root / name)
        generated_at = payload.get("generated_at") if payload else None
        timestamp = _parse_timestamp(generated_at)
        if timestamp is not None:
            return timestamp
    try:
        return root.stat().st_mtime
    except OSError:
        return 0.0


def _parse_timestamp(value: Any) -> float | None:
    if value is None or not str(value).strip():
        return None
    try:
        raw = str(value)
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        return datetime.fromisoformat(raw).timestamp()
    except ValueError:
        return None


def _nested(payload: dict[str, Any], *keys: str) -> Any:
    current: Any = payload
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _regime_bucket(regime: dict[str, Any]) -> str:
    direction = str(regime.get("direction") or "")
    regime_type = str(regime.get("regime_type") or "")
    if direction and regime_type:
        return f"{regime_type}_{direction}"
    return direction or regime_type


def _read_quant_duckdb_counts(db_path: Path) -> dict[str, int]:
    if not db_path.exists():
        return {}
    try:
        import duckdb  # type: ignore
    except ModuleNotFoundError:
        return {}
    counts: dict[str, int] = {}
    try:
        conn = duckdb.connect(str(db_path), read_only=True)
        try:
            for table in ("factor_samples", "factor_values", "factor_lookup"):
                if _duckdb_table_exists(conn, table):
                    counts[table] = int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        finally:
            conn.close()
    except Exception:
        return {}
    return counts


def _duckdb_table_exists(conn: Any, table: str) -> bool:
    row = conn.execute(
        "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = ?",
        [table],
    ).fetchone()
    return bool(row and row[0])


def _read_json(path: Path) -> dict[str, Any]:
    try:
        if not path.exists():
            return {}
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _tail_jsonl(path: Path, *, limit: int) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()[-limit:]
    except OSError:
        return []
    events: list[dict[str, Any]] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            events.append(payload)
    return events


def _jsonl_count(path: Path) -> int:
    try:
        return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
    except OSError:
        return 0


def _mtime_iso(path: Path) -> str:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()
    except OSError:
        return ""


def _int(value: Any, *, fallback: Any = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(fallback)
        except (TypeError, ValueError):
            return 0
