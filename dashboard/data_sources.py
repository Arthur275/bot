from __future__ import annotations

import json
import os
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .decision_review import load_decision_review
from .reason_text import enrich_reason_codes, load_reason_code_text_map
from .status_rules import kill_switch_status, lookup_status, runtime_status


BOT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_QUANT_ROOT = BOT_ROOT.parent / "quant_system_rebuild"
INCOMPLETE_QUANT_STATUSES = {"incomplete_snapshot_only", "incomplete_missing_scheduler_status"}


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
    json_source_quality = {
        "bot_heartbeat": _json_read_status(bot_scheduler_root / "heartbeat.json"),
        "bot_latest_cycle": _json_read_status(bot_scheduler_root / "latest_cycle.json"),
        "bot_state": _json_read_status(bot_runtime / "state_store.json"),
        "bot_candidate_package": _json_read_status(bot_scheduler_root / "latest_candidate_execution_package.json"),
        "quant_heartbeat": _json_read_status(quant_scheduler_root / "heartbeat.json"),
        "quant_research_health": _json_read_status(quant_scheduler_root / "research_health.json"),
        "quant_factor_summary": _json_read_status(quant_analysis_root / "factor_summary.json"),
        "quant_factor_ingest": _json_read_status(quant_analysis_root / "factor_ingest_latest.json"),
        "quant_factor_governance": _json_read_status(quant_analysis_root / "factor_governance_summary.json"),
    }
    worker_audit = _tail_jsonl(bot_runtime / "real_order_worker" / "audit.jsonl", limit=8)
    bot_samples = _jsonl_count(bot_scheduler_root / "samples.jsonl")
    performance = _performance_summary(
        bot_cycle=bot_cycle,
        preview_path=bot_runtime / "reports" / "protective_stop_replace" / "latest_preview.json",
    )

    quant_heartbeat = _read_json(quant_scheduler_root / "heartbeat.json")
    research_health = _read_json(quant_scheduler_root / "research_health.json")
    reason_code_text_map = load_reason_code_text_map(quant_scheduler_root / "reason_code_map.json")
    factor_summary = _read_json(quant_analysis_root / "factor_summary.json")
    factor_ingest = _read_json(quant_analysis_root / "factor_ingest_latest.json")
    factor_lookup = _read_latest_lookup(paths.quant_root)
    factor_governance = _read_json(quant_analysis_root / "factor_governance_summary.json")
    quant_handoff = _read_latest_handoff(paths.quant_root)
    quant_cycle = _read_latest_quant_cycle(paths.quant_root)
    quant_incomplete_cycle = _read_latest_incomplete_quant_cycle(paths.quant_root)
    quant_decision = quant_cycle.get("decision", {})
    quant_metadata = quant_cycle.get("metadata", {})
    quant_risk = quant_decision.get("risk_report", {}) if isinstance(quant_decision, dict) else {}
    quant_regime = quant_decision.get("regime_state", {}) if isinstance(quant_decision, dict) else {}
    quant_scheduler_status = _read_latest_quant_scheduler_status(paths.quant_root) or quant_cycle.get("scheduler_status", {})
    quant_db_counts = _read_quant_duckdb_counts(quant_analysis_root / "quant_analysis.duckdb")
    decision_review_report_present = (bot_runtime / "reviews" / "latest_decision_review.json").exists()
    decision_review = load_decision_review(bot_root=paths.bot_root, quant_root=paths.quant_root)
    charts = _charts_summary(bot_root=paths.bot_root, quant_root=paths.quant_root)

    kill_switch_path = bot_runtime / "controls" / "disable_real_execution.flag"
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "paths": {
            "bot_root": str(paths.bot_root),
            "quant_root": str(paths.quant_root),
            "kill_switch_path": str(kill_switch_path),
        },
        "data_quality": {
            "json_sources": json_source_quality,
            "json_source_issues": [
                {"name": name, **status}
                for name, status in json_source_quality.items()
                if status["status"] not in {"ok", "missing"}
            ],
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
        "optional_workers": {
            "decision_review": _decision_review_worker_status(
                decision_review,
                report_present=decision_review_report_present,
            ),
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
            "governance": _factor_governance_summary(factor_governance),
        },
        "quant": {
            "action": bot_cycle.get("effective_action") or quant_decision.get("action") or quant_handoff.get("action") or "",
            "direction": bot_cycle.get("direction") or quant_decision.get("direction") or quant_handoff.get("direction") or "",
            "risk_filter_status": bot_cycle.get("risk_filter_status") or quant_risk.get("risk_filter_status") or quant_handoff.get("risk_filter_status") or "",
            "confidence": bot_cycle.get("confidence") or quant_decision.get("confidence") or quant_handoff.get("confidence"),
            "sizing_tier": bot_cycle.get("sizing_tier") or quant_decision.get("sizing_tier") or _nested(quant_decision, "sizing_decision", "sizing_tier") or quant_handoff.get("sizing_tier") or "",
            "reasoning_summary": bot_cycle.get("reasoning_summary") or quant_decision.get("reasoning_summary") or quant_handoff.get("reasoning_summary") or "",
            "execution_block_reason": bot_cycle.get("execution_block_reason") or quant_handoff.get("execution_block_reason") or "",
            "reason_codes": _list(bot_cycle.get("reason_codes")) or _list(quant_risk.get("reason_codes")) or _list(quant_handoff.get("risk_reason_codes")),
            "risk_reason_codes": _list(bot_cycle.get("risk_reason_codes")) or _list(quant_handoff.get("risk_reason_codes")),
            "supporting_factors": quant_handoff.get("supporting_factor_codes", [])[:10],
            "opposing_factors": quant_handoff.get("opposing_factor_codes", [])[:10],
            "veto_factors": quant_handoff.get("veto_factor_codes", [])[:10],
            "degrade_flags": bot_cycle.get("degrade_flags") or quant_risk.get("degrade_flags") or quant_handoff.get("degrade_flags") or [],
            "data_health_score": _first_present(bot_cycle.get("data_health_score"), quant_risk.get("data_health_score"), quant_handoff.get("data_health_score")),
            "market_data_mode": _first_present(bot_cycle.get("market_data_mode"), quant_metadata.get("market_data_mode"), quant_handoff.get("market_data_mode")),
            "consensus_quality": _first_present(bot_cycle.get("consensus_quality"), quant_metadata.get("consensus_quality"), quant_handoff.get("consensus_quality")),
            "consensus_source_count": _first_present(bot_cycle.get("consensus_source_count"), quant_metadata.get("consensus_source_count"), quant_handoff.get("consensus_source_count")),
            "consensus_sources": _first_present(bot_cycle.get("consensus_sources"), quant_metadata.get("consensus_sources"), quant_handoff.get("consensus_sources")),
            "binance_source_health": _first_present(bot_cycle.get("binance_source_health"), quant_metadata.get("binance_source_health"), quant_handoff.get("binance_source_health")),
            "binance_source_failure_reason": _first_present(bot_cycle.get("binance_source_failure_reason"), quant_metadata.get("binance_source_failure_reason"), quant_handoff.get("binance_source_failure_reason")),
            "net_edge_pct": _first_present(bot_cycle.get("net_edge_pct"), quant_handoff.get("net_edge_pct")),
            "estimated_cost_pct": _first_present(bot_cycle.get("estimated_cost_pct"), quant_handoff.get("estimated_cost_pct")),
            "estimated_fee_pct": _first_present(bot_cycle.get("estimated_fee_pct"), quant_handoff.get("estimated_fee_pct")),
            "estimated_slippage_pct": _first_present(bot_cycle.get("estimated_slippage_pct"), quant_handoff.get("estimated_slippage_pct")),
            "estimated_funding_pct": _first_present(bot_cycle.get("estimated_funding_pct"), quant_handoff.get("estimated_funding_pct")),
            "edge_source": _first_present(bot_cycle.get("edge_source"), quant_handoff.get("edge_source")),
            "latest_incomplete_cycle": quant_incomplete_cycle,
            "regime_bucket": quant_handoff.get("regime_bucket", "") or _regime_bucket(quant_regime),
            "factor_lookup_version": quant_handoff.get("factor_lookup_version", "") or factor_lookup.get("lookup_version", ""),
            "factor_lookup_stale": bool(quant_handoff.get("factor_lookup_stale", False)),
            "execution_warnings": quant_handoff.get("execution_warnings", []),
            "automation_boundary": bot_cycle.get("automation_boundary", ""),
            "research": _research_summary(research_health, reason_code_text_map=reason_code_text_map),
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
        "performance": performance,
        "decision_review": decision_review,
        "charts": charts,
    }


def _research_summary(payload: dict[str, Any], *, reason_code_text_map: dict[str, str] | None = None) -> dict[str, Any]:
    metadata = payload.get("metadata") if isinstance(payload, dict) else {}
    metadata = metadata if isinstance(metadata, dict) else {}
    research_bundle = metadata.get("research_bundle") if isinstance(metadata, dict) else {}
    research_bundle = research_bundle if isinstance(research_bundle, dict) else {}
    health = research_bundle.get("research_health") if isinstance(research_bundle, dict) else {}
    health = health if isinstance(health, dict) else {}
    refresh = metadata.get("research_refresh") if isinstance(metadata, dict) else {}
    refresh = refresh if isinstance(refresh, dict) else {}
    reason_codes = health.get("reason_codes") or research_bundle.get("reason_codes") or payload.get("issues") or []
    generated_at = payload.get("generated_at") or research_bundle.get("generated_at") or ""
    status = str(health.get("research_health_status") or payload.get("status") or "unknown")
    decision = str(health.get("decision") or research_bundle.get("research_decision") or "")
    return {
        "status": status,
        "decision": decision,
        "freshness": health.get("freshness", ""),
        "summary": health.get("research_health_summary", ""),
        "generated_at": generated_at,
        "dataset_timestamp": health.get("dataset_timestamp", ""),
        "decision_ready": bool(research_bundle.get("decision_ready") or metadata.get("ready")),
        "refresh_aliases": bool(refresh.get("refresh_aliases", False)),
        "refresh_every": _int(refresh.get("refresh_aliases_every")),
        "loop_iteration": _int(refresh.get("loop_iteration")),
        "reason_codes": list(reason_codes)[:12] if isinstance(reason_codes, list) else [],
        "reason_texts": enrich_reason_codes(reason_codes, limit=12, mapping=reason_code_text_map),
    }


def _factor_governance_summary(payload: dict[str, Any]) -> dict[str, Any]:
    rows = payload.get("rows") if isinstance(payload, dict) else []
    rows = rows if isinstance(rows, list) else []
    return {
        "status": str(payload.get("status") or "unknown") if isinstance(payload, dict) else "unknown",
        "lookup_version": str(payload.get("lookup_version") or "") if isinstance(payload, dict) else "",
        "generated_at": str(payload.get("generated_at") or "") if isinstance(payload, dict) else "",
        "reason_codes": list(payload.get("reason_codes") or [])[:8] if isinstance(payload, dict) else [],
        "rows": [
            {
                "factor_name": str(row.get("factor_name") or ""),
                "factor_value_bucket": str(row.get("factor_value_bucket") or ""),
                "factor_grade": str(row.get("factor_grade") or ""),
                "factor_lifecycle": str(row.get("factor_lifecycle") or ""),
                "factor_effect": str(row.get("factor_effect") or ""),
                "sample_count": _int(row.get("sample_count")),
                "win_rate": row.get("win_rate"),
                "stop_hit_rate": row.get("stop_hit_rate"),
                "net_expectancy_pct": row.get("net_expectancy_pct"),
                "reason_codes": list(row.get("reason_codes") or [])[:5],
            }
            for row in rows[:6]
            if isinstance(row, dict)
        ],
    }


def _worker_status(*, worker_audit: list[dict[str, Any]], candidate: dict[str, Any]) -> dict[str, Any]:
    if not worker_audit:
        if candidate:
            return {"label": "READY", "level": "yellow", "age_sec": None}
        return {"label": "DISABLED", "level": "gray", "age_sec": None}
    latest = worker_audit[-1]
    payload = latest.get("payload") or {}
    status = str(payload.get("status") or "")
    failed_statuses = {"partial_failed", "all_failed", "unknown_after_exception"}
    return runtime_status(
        generated_at=latest.get("generated_at"),
        ok=status in {"submitted_all_accepted", "skipped"} or not status,
        stale_after_sec=15 * 60,
        error=(
            ",".join(payload.get("reason_codes") or [status])
            if status == "blocked" or status in failed_statuses
            else ""
        ),
    )


def _decision_review_worker_status(review: dict[str, Any], *, report_present: bool) -> dict[str, Any]:
    status = str(review.get("review_status") or "unavailable")
    if not report_present:
        return {
            "label": "OPTIONAL_DISABLED",
            "level": "gray",
            "age_sec": None,
            "optional": True,
            "enabled": False,
            "status": "disabled",
            "note": review.get("summary", ""),
        }
    level = {"clear": "green", "watch": "yellow", "needs_attention": "red"}.get(status, "gray")
    return {
        "label": status.upper(),
        "level": level,
        "age_sec": review.get("source_handoff_age_sec"),
        "optional": True,
        "enabled": True,
        "status": status,
        "note": review.get("summary", ""),
    }


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


def _performance_summary(*, bot_cycle: dict[str, Any], preview_path: Path) -> dict[str, Any]:
    cycle_summary = _performance_from_bot_cycle(bot_cycle)
    if cycle_summary["ignored_source"]:
        return cycle_summary
    if cycle_summary["account_equity"] is not None or cycle_summary["total_profit_usd"] is not None:
        return cycle_summary

    preview = _read_json(preview_path)
    if _preview_venue(preview) == "binance_usdt_perp":
        return {
            "account_equity": None,
            "account_equity_source": "",
            "total_profit_usd": None,
            "total_profit_pct": None,
            "price_vs_entry_pct": None,
            "position_state": "",
            "mark_price": None,
            "fetched_at": "",
            "snapshot_valid": False,
            "source": "unavailable",
            "ignored_source": "binance_usdt_perp",
        }

    snapshot = preview.get("snapshot") if isinstance(preview, dict) else {}
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    pnl_state = preview.get("pnl_state") if isinstance(preview, dict) else {}
    pnl_state = pnl_state if isinstance(pnl_state, dict) else {}
    position = snapshot.get("position") if isinstance(snapshot, dict) else {}
    position = position if isinstance(position, dict) else {}
    return {
        "account_equity": snapshot.get("account_equity"),
        "account_equity_source": snapshot.get("account_equity_source", ""),
        "total_profit_usd": pnl_state.get("unrealized_pnl_usd"),
        "total_profit_pct": pnl_state.get("unrealized_pnl_pct_on_margin"),
        "price_vs_entry_pct": pnl_state.get("price_vs_entry_pct"),
        "position_state": position.get("position_state", ""),
        "mark_price": position.get("mark_price"),
        "fetched_at": snapshot.get("fetched_at") or preview.get("created_at", ""),
        "snapshot_valid": bool(snapshot.get("snapshot_valid", False)),
        "source": "protective_stop_preview",
        "ignored_source": "",
    }


def _performance_from_bot_cycle(bot_cycle: dict[str, Any]) -> dict[str, Any]:
    runtime_snapshot = bot_cycle.get("runtime_snapshot") if isinstance(bot_cycle, dict) else {}
    runtime_snapshot = runtime_snapshot if isinstance(runtime_snapshot, dict) else {}
    position = runtime_snapshot.get("position") if isinstance(runtime_snapshot, dict) else {}
    position = position if isinstance(position, dict) else {}
    venue = _cycle_venue(bot_cycle, runtime_snapshot)
    if venue == "binance_usdt_perp":
        return {
            "account_equity": None,
            "account_equity_source": "",
            "total_profit_usd": None,
            "total_profit_pct": None,
            "price_vs_entry_pct": None,
            "position_state": "",
            "mark_price": None,
            "fetched_at": runtime_snapshot.get("fetched_at") or bot_cycle.get("finished_at") or "",
            "snapshot_valid": False,
            "source": "unavailable",
            "ignored_source": "binance_usdt_perp",
        }
    return {
        "account_equity": _first_present(
            bot_cycle.get("runtime_account_equity"),
            runtime_snapshot.get("account_equity"),
        ),
        "account_equity_source": _first_present(
            bot_cycle.get("runtime_account_equity_source"),
            runtime_snapshot.get("account_equity_source"),
        ) or "",
        "total_profit_usd": _first_present(
            bot_cycle.get("runtime_unrealized_pnl_usd"),
            bot_cycle.get("unrealized_pnl_usd"),
            position.get("unrealized_pnl_usd"),
            position.get("unrealized_profit"),
        ),
        "total_profit_pct": _first_present(
            bot_cycle.get("runtime_unrealized_pnl_pct"),
            bot_cycle.get("unrealized_pnl_pct_on_margin"),
            position.get("unrealized_pnl_pct_on_margin"),
        ),
        "price_vs_entry_pct": _first_present(bot_cycle.get("price_vs_entry_pct"), position.get("price_vs_entry_pct")),
        "position_state": position.get("position_state") or bot_cycle.get("runtime_position_state") or "",
        "mark_price": _first_present(bot_cycle.get("runtime_mark_price"), position.get("mark_price")),
        "fetched_at": runtime_snapshot.get("fetched_at") or bot_cycle.get("finished_at") or "",
        "snapshot_valid": bool(runtime_snapshot.get("snapshot_valid", False)),
        "source": "bot_latest_cycle",
        "ignored_source": "",
    }


def _cycle_venue(bot_cycle: dict[str, Any], runtime_snapshot: dict[str, Any]) -> str:
    venue = str(
        _first_present(
            bot_cycle.get("exchange_venue"),
            runtime_snapshot.get("exchange_venue"),
            runtime_snapshot.get("venue"),
        )
        or ""
    )
    if venue:
        return venue
    symbol = str(
        _first_present(
            bot_cycle.get("exchange_symbol"),
            runtime_snapshot.get("exchange_symbol"),
            runtime_snapshot.get("symbol"),
            _nested(runtime_snapshot, "position", "symbol"),
        )
        or ""
    )
    if symbol == "ETHUSDT":
        return "binance_usdt_perp"
    if symbol == "ETH-USDT-SWAP":
        return "okx_usdt_swap"
    return ""


def _preview_venue(preview: Any) -> str:
    if not isinstance(preview, dict):
        return ""
    candidates = [
        preview.get("venue"),
        preview.get("exchange_venue"),
        _nested(preview, "recorded_protective_stop", "venue"),
        _nested(preview, "existing_record", "venue"),
        _nested(preview, "new_protective_stop_record", "venue"),
    ]
    symbol = _nested(preview, "snapshot", "position", "symbol") or _nested(preview, "recorded_protective_stop", "symbol")
    for value in candidates:
        venue = str(value or "")
        if venue:
            return venue
    if str(symbol or "") == "ETHUSDT":
        return "binance_usdt_perp"
    if str(symbol or "") == "ETH-USDT-SWAP":
        return "okx_usdt_swap"
    return ""


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
            "metadata": decision_payload.get("metadata") or {},
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
            "metadata": decision_payload.get("metadata") or {},
            "scheduler_status": {},
        }
    return {}


def _read_latest_quant_scheduler_status(quant_root: Path) -> dict[str, Any]:
    cycles_root = quant_root / "runtime" / "cycles"
    try:
        roots = sorted(
            [path for path in cycles_root.iterdir() if path.is_dir() and (path / "scheduler_status.json").exists()],
            key=lambda path: _scheduler_status_sort_timestamp(path),
            reverse=True,
        )
    except OSError:
        return {}
    for root in roots:
        payload = _read_json(root / "scheduler_status.json")
        if _is_incomplete_quant_status(payload.get("status")):
            continue
        if payload:
            return {**payload, "cycle_dir": str(root)}
    return {}


def _read_latest_incomplete_quant_cycle(quant_root: Path) -> dict[str, Any]:
    cycles_root = quant_root / "runtime" / "cycles"
    try:
        roots = sorted(
            [path for path in cycles_root.iterdir() if path.is_dir()],
            key=lambda path: _cycle_sort_timestamp(path),
            reverse=True,
        )
    except OSError:
        return {"present": False}
    for root in roots:
        snapshot_registry = _read_json(root / "snapshot_registry.json")
        if not snapshot_registry:
            continue
        scheduler_status = _read_json(root / "scheduler_status.json")
        if scheduler_status and not _is_incomplete_quant_status(scheduler_status.get("status")):
            continue
        has_decision = _read_json(root / "decision.json") != {}
        has_scheduler_status = bool(scheduler_status)
        missing_parts = [] if has_scheduler_status else ["scheduler_status"]
        if not has_decision:
            missing_parts.append("decision")
        status = str(scheduler_status.get("status") or "") if scheduler_status else ""
        return {
            "present": True,
            "cycle_dir": str(root),
            "generated_at": status and scheduler_status.get("generated_at") or snapshot_registry.get("generated_at") or _mtime_iso(root / "snapshot_registry.json"),
            "status": status or ("incomplete_missing_scheduler_status" if has_decision else "incomplete_snapshot_only"),
            "has_snapshot_registry": True,
            "has_decision": has_decision,
            "has_scheduler_status": has_scheduler_status,
            "missing_parts": missing_parts,
        }
    return {"present": False}


def _charts_summary(*, bot_root: Path, quant_root: Path) -> dict[str, Any]:
    bot_samples = _tail_jsonl(bot_root / "runtime" / "bot_runtime_scheduler" / "samples.jsonl", limit=80)
    return {
        "cycle_status_timeline": _cycle_status_timeline(quant_root, limit=80),
        "quant_metric_series": _quant_metric_series(bot_samples),
        "reason_code_counts": _reason_code_counts(bot_samples, limit=10),
        "consensus_quality_series": _consensus_quality_series(bot_samples),
    }


def _cycle_status_timeline(quant_root: Path, *, limit: int) -> list[dict[str, Any]]:
    cycles_root = quant_root / "runtime" / "cycles"
    try:
        roots = sorted(
            [path for path in cycles_root.iterdir() if path.is_dir()],
            key=lambda path: _cycle_sort_timestamp(path),
            reverse=True,
        )[:limit]
    except OSError:
        return []
    rows: list[dict[str, Any]] = []
    for root in reversed(roots):
        status_payload = _read_json(root / "scheduler_status.json")
        decision_payload = _read_json(root / "decision.json")
        status = str(status_payload.get("status") or ("ok" if decision_payload else "missing"))
        generated_at = (
            status_payload.get("generated_at")
            or decision_payload.get("generated_at")
            or _mtime_iso(root / "scheduler_status.json")
            or _mtime_iso(root / "decision.json")
            or _mtime_iso(root)
        )
        rows.append(
            {
                "run_id": root.name,
                "generated_at": generated_at,
                "status": status,
                "status_value": _cycle_status_value(status),
                "has_decision": bool(decision_payload),
                "has_scheduler_status": bool(status_payload),
            }
        )
    return rows


def _quant_metric_series(samples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for sample in samples[-80:]:
        rows.append(
            {
                "generated_at": str(sample.get("finished_at") or sample.get("started_at") or ""),
                "sample_id": sample.get("sample_id"),
                "action": sample.get("effective_action") or sample.get("requested_action") or "",
                "data_health_score": _chart_float(sample.get("data_health_score"), scale_unit=True),
                "confidence": _chart_float(sample.get("confidence"), scale_unit=True),
                "net_edge_pct": _chart_float(sample.get("net_edge_pct"), scale_pct=True),
                "estimated_cost_pct": _chart_float(sample.get("estimated_cost_pct"), scale_pct=True),
            }
        )
    return rows


def _reason_code_counts(samples: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    counter: Counter[str] = Counter()
    for sample in samples[-80:]:
        for key in ("reason_codes", "risk_reason_codes", "degrade_flags"):
            values = sample.get(key)
            if isinstance(values, list):
                counter.update(str(value) for value in values if str(value))
    return [{"code": code, "count": count} for code, count in counter.most_common(limit)]


def _consensus_quality_series(samples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for sample in samples[-80:]:
        quality = str(sample.get("consensus_quality") or "")
        rows.append(
            {
                "generated_at": str(sample.get("finished_at") or sample.get("started_at") or ""),
                "quality": quality,
                "quality_value": _consensus_quality_value(quality),
                "source_count": _chart_float(sample.get("consensus_source_count")),
                "market_data_mode": str(sample.get("market_data_mode") or ""),
            }
        )
    return rows


def _cycle_status_value(status: str) -> int:
    normalized = str(status or "").lower()
    if normalized == "ok":
        return 3
    if normalized in {"degraded", "incomplete_snapshot_only", "incomplete_missing_scheduler_status"}:
        return 2
    if normalized == "blocked":
        return 1
    return 0


def _consensus_quality_value(quality: str) -> int:
    normalized = str(quality or "").lower()
    if normalized in {"full", "acceptable"}:
        return 3
    if normalized in {"restricted_two_source", "degraded"}:
        return 2
    if normalized == "unreliable":
        return 1
    return 0


def _chart_float(value: Any, *, scale_unit: bool = False, scale_pct: bool = False) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if scale_pct:
        number = number * 100.0
    elif scale_unit and abs(number) <= 1.0:
        number = number * 100.0
    return round(number, 6)


def _is_incomplete_quant_status(value: Any) -> bool:
    return str(value or "") in INCOMPLETE_QUANT_STATUSES


def _scheduler_status_sort_timestamp(root: Path) -> float:
    path = root / "scheduler_status.json"
    payload = _read_json(path)
    timestamp = _parse_timestamp(payload.get("generated_at") if payload else None)
    if timestamp is not None:
        return timestamp
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


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


def _first_present(*values: Any) -> Any:
    for value in values:
        if value not in (None, ""):
            return value
    return None


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


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
    status = _json_read_status(path)
    return status["payload"] if isinstance(status.get("payload"), dict) else {}


def _json_read_status(path: Path) -> dict[str, Any]:
    try:
        if not path.exists():
            return {"status": "missing", "path": str(path), "payload": {}}
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        return {"status": "read_error", "path": str(path), "error": str(exc), "payload": {}}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        return {"status": "invalid_json", "path": str(path), "error": str(exc), "payload": {}}
    if not isinstance(payload, dict):
        return {"status": "not_object", "path": str(path), "payload": {}}
    return {"status": "ok", "path": str(path), "payload": payload}


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
