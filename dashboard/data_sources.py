from __future__ import annotations

import json
import os
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .decision_review import load_decision_review
from .reason_text import enrich_reason_codes, load_reason_code_text_map
from .runtime_reader import RuntimeSnapshotReader, json_read_status, jsonl_count, mtime_iso, read_json, tail_jsonl
from .status_rules import kill_switch_status, lookup_status, runtime_status


BOT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_QUANT_ROOT = BOT_ROOT.parent / "quant_system_rebuild"
INCOMPLETE_QUANT_STATUSES = {"incomplete_snapshot_only", "incomplete_missing_scheduler_status"}
TRIGGER_WATCH_CONFIDENCE_THRESHOLD = 0.60
TRIGGER_WATCH_HORIZONS = (1, 3, 6)
DEFAULT_FACTOR_LOOKUP_STALE_AFTER_SEC = 3 * 60 * 60
FACTOR_LOOKUP_FUTURE_TOLERANCE_SEC = 60


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
    reader = RuntimeSnapshotReader(bot_root=paths.bot_root, quant_root=paths.quant_root)
    bot_runtime = reader.bot_runtime
    quant_runtime = reader.quant_runtime
    bot_scheduler_root = reader.bot_scheduler_root
    quant_analysis_root = reader.quant_analysis_root
    quant_scheduler_root = reader.quant_scheduler_root

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
        "quant_candidate_scan": _json_read_status(quant_runtime / "reports" / "candidate_scan_feature_matrix_smoke.json"),
        "quant_parameter_scan": _json_read_status(quant_runtime / "reports" / "parameter_scan_feature_matrix_smoke.json"),
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
    candidate_scan = _read_json(quant_runtime / "reports" / "candidate_scan_feature_matrix_smoke.json")
    parameter_scan = _read_json(quant_runtime / "reports" / "parameter_scan_feature_matrix_smoke.json")
    quant_handoff = _read_latest_handoff(paths.quant_root)
    quant_handoff_freshness = _handoff_factor_lookup_freshness(quant_handoff)
    quant_cycle = _read_latest_quant_cycle(paths.quant_root)
    quant_incomplete_cycle = _read_latest_incomplete_quant_cycle(paths.quant_root)
    quant_decision = quant_cycle.get("decision", {})
    quant_metadata = quant_cycle.get("metadata", {})
    quant_risk = quant_decision.get("risk_report", {}) if isinstance(quant_decision, dict) else {}
    quant_regime = quant_decision.get("regime_state", {}) if isinstance(quant_decision, dict) else {}
    quant_sizing = quant_decision.get("sizing_decision", {}) if isinstance(quant_decision, dict) else {}
    quant_sizing = quant_sizing if isinstance(quant_sizing, dict) else {}
    quant_setup = quant_decision.get("setup_state", {}) if isinstance(quant_decision, dict) else {}
    quant_setup = quant_setup if isinstance(quant_setup, dict) else {}
    quant_trigger = quant_decision.get("trigger_state", {}) if isinstance(quant_decision, dict) else {}
    quant_trigger = quant_trigger if isinstance(quant_trigger, dict) else {}
    quant_scheduler_status = _read_latest_quant_scheduler_status(paths.quant_root) or quant_cycle.get("scheduler_status", {})
    quant_db_counts = _read_quant_duckdb_counts(quant_analysis_root / "quant_analysis.duckdb")
    decision_review_report_present = (bot_runtime / "reviews" / "latest_decision_review.json").exists()
    decision_review = load_decision_review(bot_root=paths.bot_root, quant_root=paths.quant_root)
    charts = _charts_summary(bot_root=paths.bot_root, quant_root=paths.quant_root)
    trigger_watch = _trigger_watch_summary(
        quant_root=paths.quant_root,
        bot_samples_path=bot_scheduler_root / "samples.jsonl",
        bot_latest_cycle=bot_cycle,
    )

    kill_switch_path = reader.kill_switch_path
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
                ok=str(quant_scheduler_status.get("status") or quant_heartbeat.get("status") or "ok") not in {"error", "failed"},
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
            "candidate_authenticity": _candidate_authenticity_summary(candidate_scan, parameter_scan),
            "candidate_promotion": _candidate_promotion_summary(
                _read_json(quant_runtime / "fresh_research" / "all_results.json"),
                _read_json(quant_runtime / "fresh_research" / "producer_output_latest.json"),
            ),
        },
        "quant": {
            "requested_action": bot_cycle.get("requested_action") or quant_decision.get("action") or quant_handoff.get("action") or "",
            "action": bot_cycle.get("effective_action") or quant_decision.get("action") or quant_handoff.get("action") or "",
            "direction": bot_cycle.get("direction") or quant_decision.get("direction") or quant_handoff.get("direction") or "",
            "risk_filter_status": bot_cycle.get("risk_filter_status") or quant_risk.get("risk_filter_status") or quant_handoff.get("risk_filter_status") or "",
            "confidence": bot_cycle.get("confidence") or quant_decision.get("confidence") or quant_handoff.get("confidence"),
            "thesis_score": bot_cycle.get("thesis_score") or quant_decision.get("thesis_score") or quant_handoff.get("thesis_score"),
            "sizing_tier": bot_cycle.get("sizing_tier") or quant_decision.get("sizing_tier") or _nested(quant_decision, "sizing_decision", "sizing_tier") or quant_handoff.get("sizing_tier") or "",
            "reasoning_summary": bot_cycle.get("reasoning_summary") or quant_decision.get("reasoning_summary") or quant_handoff.get("reasoning_summary") or "",
            "execution_block_reason": bot_cycle.get("execution_block_reason") or quant_handoff.get("execution_block_reason") or "",
            "reason_codes": _list(bot_cycle.get("reason_codes")) or _list(quant_risk.get("reason_codes")) or _list(quant_handoff.get("risk_reason_codes")),
            "risk_reason_codes": _list(bot_cycle.get("risk_reason_codes")) or _list(quant_handoff.get("risk_reason_codes")),
            "transition_reason_codes": _first_list_present(bot_cycle.get("transition_reason_codes"), quant_handoff.get("transition_reason_codes")),
            "sizing_reason_codes": _first_list_present(
                bot_cycle.get("sizing_reason_codes"),
                quant_sizing.get("reason_codes"),
                quant_handoff.get("sizing_reason_codes"),
            ),
            "supporting_factors": quant_handoff.get("supporting_factor_codes", [])[:10],
            "opposing_factors": quant_handoff.get("opposing_factor_codes", [])[:10],
            "veto_factors": quant_handoff.get("veto_factor_codes", [])[:10],
            "degrade_flags": bot_cycle.get("degrade_flags") or quant_risk.get("degrade_flags") or quant_handoff.get("degrade_flags") or [],
            "runtime_vetoes": _first_list_present(bot_cycle.get("runtime_vetoes"), quant_risk.get("runtime_vetoes"), quant_handoff.get("runtime_vetoes")),
            "research_gate_status": _first_present(bot_cycle.get("research_gate_status"), quant_risk.get("research_gate_status"), quant_handoff.get("research_gate_status")),
            "research_gate_reasons": _first_list_present(bot_cycle.get("research_gate_reasons"), quant_risk.get("research_gate_reasons"), quant_handoff.get("research_gate_reasons")),
            "execution_allowed": _first_present(bot_cycle.get("execution_allowed"), quant_handoff.get("execution_allowed")),
            "position_size_pct": _first_present(bot_cycle.get("position_size_pct"), quant_decision.get("position_size_pct"), quant_handoff.get("position_size_pct")),
            "executable_size_pct": _first_present(bot_cycle.get("executable_size_pct"), quant_handoff.get("executable_size_pct")),
            "signal_size_pct": _first_present(bot_cycle.get("signal_size_pct"), quant_sizing.get("signal_size_pct"), quant_handoff.get("signal_size_pct")),
            "position_cap_pct": _first_present(bot_cycle.get("position_cap_pct"), quant_risk.get("position_cap"), quant_handoff.get("position_cap_pct")),
            "probe_source": _first_present(bot_cycle.get("probe_source"), quant_decision.get("probe_source"), quant_handoff.get("probe_source")),
            "probe_risk_tier": _first_present(bot_cycle.get("probe_risk_tier"), quant_decision.get("probe_risk_tier"), quant_handoff.get("probe_risk_tier")),
            "probe_expiry_bars": _first_present(bot_cycle.get("probe_expiry_bars"), quant_decision.get("probe_expiry_bars"), quant_handoff.get("probe_expiry_bars")),
            "probe_expiry_timeframe": _first_present(bot_cycle.get("probe_expiry_timeframe"), quant_decision.get("probe_expiry_timeframe"), quant_handoff.get("probe_expiry_timeframe")),
            "probe_invalid_if_no_followthrough": _first_present(
                bot_cycle.get("probe_invalid_if_no_followthrough"),
                quant_decision.get("probe_invalid_if_no_followthrough"),
                quant_handoff.get("probe_invalid_if_no_followthrough"),
            ),
            "invalidate_conditions": _first_list_present(bot_cycle.get("invalidate_conditions"), _nested(quant_decision, "exit_plan", "invalidate_conditions"), quant_handoff.get("invalidate_conditions")),
            "setup_direction": _first_present(bot_cycle.get("setup_direction"), quant_setup.get("setup_direction"), quant_handoff.get("setup_direction")),
            "trigger_direction": _first_present(bot_cycle.get("trigger_direction"), quant_trigger.get("trigger_direction"), quant_handoff.get("trigger_direction")),
            "trigger_ready": _first_present(bot_cycle.get("trigger_ready"), quant_trigger.get("trigger_ready"), quant_handoff.get("trigger_ready")),
            "setup_strength": _first_present(bot_cycle.get("setup_strength"), quant_setup.get("setup_strength"), quant_handoff.get("setup_strength")),
            "entry_timing_score": _first_present(bot_cycle.get("entry_timing_score"), quant_trigger.get("entry_timing_score"), quant_handoff.get("entry_timing_score")),
            "breakout_support": _first_present(bot_cycle.get("breakout_support"), quant_trigger.get("breakout_support"), quant_handoff.get("breakout_support")),
            "retest_support": _first_present(bot_cycle.get("retest_support"), quant_trigger.get("retest_support"), quant_handoff.get("retest_support")),
            "slope_support": _first_present(bot_cycle.get("slope_support"), quant_trigger.get("slope_support"), quant_handoff.get("slope_support")),
            "overlay_bias": _first_present(bot_cycle.get("overlay_bias"), quant_handoff.get("overlay_bias")),
            "overlay_summary": _first_present(bot_cycle.get("overlay_summary"), quant_handoff.get("overlay_summary")),
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
            "factor_lookup_generated_at": _first_present(quant_handoff.get("factor_lookup_generated_at"), factor_lookup.get("generated_at")),
            "factor_lookup_age_seconds": quant_handoff_freshness["age_seconds"],
            "factor_lookup_stale": bool(quant_handoff_freshness["stale"]),
            "factor_lookup_producer_stale": bool(quant_handoff.get("factor_lookup_stale", False)),
            "handoff_freshness_status": quant_handoff_freshness["status"],
            "handoff_freshness_reason_codes": quant_handoff_freshness["reason_codes"],
            "execution_warnings": quant_handoff.get("execution_warnings", []),
            "automation_boundary": bot_cycle.get("automation_boundary", ""),
            "trigger_watch": trigger_watch,
            "research": _research_summary(research_health, reason_code_text_map=reason_code_text_map),
            "execution_layer_reasoning": bot_cycle.get("execution_layer_reasoning") or quant_handoff.get("execution_layer_reasoning") or "",
            "execution_opportunity_status": bot_cycle.get("execution_opportunity_status") or quant_handoff.get("execution_opportunity_status") or "",
        },
        "bot": {
            "execution_state": bot_state.get("execution_state", ""),
            "automation_state": bot_state.get("automation_state", ""),
            "position_state": _first_present(
                _nested(bot_cycle, "runtime_snapshot", "position", "position_state"),
                bot_state.get("observed_position_state", ""),
            ),
            "position_direction": _first_present(
                _nested(bot_cycle, "runtime_snapshot", "position", "direction"),
                bot_state.get("observed_position_direction", ""),
            ),
            "position_size_pct": _first_present(
                _nested(bot_cycle, "runtime_snapshot", "position", "size_pct"),
                bot_state.get("observed_position_size_pct", 0.0),
            ),
            "protective_stop_required": bool(bot_state.get("protective_stop_required", False)),
            "real_order_gate": _real_order_gate_summary(bot_cycle.get("real_order_gate") if isinstance(bot_cycle, dict) else {}),
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
                "real_order_gate": _real_order_gate_summary(bot_cycle.get("real_order_gate") if isinstance(bot_cycle, dict) else {}),
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


def _candidate_authenticity_summary(*scan_payloads: dict[str, Any]) -> dict[str, Any]:
    reports = [_candidate_authenticity_report(payload) for payload in scan_payloads if payload]
    reports = [report for report in reports if report["candidate_count"] or report["top_candidates"]]
    if not reports:
        return {
            "status": "missing",
            "label": "暂无扫描",
            "generated_at": "",
            "source": "",
            "source_count": 0,
            "candidate_count": 0,
            "suspect_count": 0,
            "research_only_count": 0,
            "watch_count": 0,
            "missing_authenticity_count": 0,
            "top_candidates": [],
        }

    top_candidates = sorted(
        [candidate for report in reports for candidate in report["top_candidates"]],
        key=lambda item: (_candidate_authenticity_rank(item), -float(item.get("avg_return_pct") or 0.0)),
    )[:8]
    suspect_count = sum(_int(report.get("suspect_count")) for report in reports)
    research_only_count = sum(_int(report.get("research_only_count")) for report in reports)
    watch_count = sum(_int(report.get("watch_count")) for report in reports)
    missing_authenticity_count = sum(_int(report.get("missing_authenticity_count")) for report in reports)
    if suspect_count:
        status = "needs_review"
        label = "需复核"
    elif research_only_count or missing_authenticity_count:
        status = "research_only"
        label = "仅研究"
    elif watch_count:
        status = "candidate_watch"
        label = "可观察"
    else:
        status = "not_promising"
        label = "暂不看好"

    return {
        "status": status,
        "label": label,
        "generated_at": max((str(report.get("generated_at") or "") for report in reports), default=""),
        "source": "+".join(str(report.get("source") or "") for report in reports if report.get("source")),
        "source_count": len(reports),
        "candidate_count": sum(_int(report.get("candidate_count")) for report in reports),
        "suspect_count": suspect_count,
        "research_only_count": research_only_count,
        "watch_count": watch_count,
        "missing_authenticity_count": missing_authenticity_count,
        "top_candidates": top_candidates,
    }


def _candidate_promotion_summary(all_results: dict[str, Any], producer_output: dict[str, Any]) -> dict[str, Any]:
    results = all_results.get("results") if isinstance(all_results, dict) else []
    results = [row for row in results if isinstance(row, dict)] if isinstance(results, list) else []
    metadata = producer_output.get("metadata") if isinstance(producer_output, dict) else {}
    metadata = metadata if isinstance(metadata, dict) else {}
    review_count = sum(1 for row in results if str(row.get("candidate_review_status") or "") == "review_candidate")
    qualified_count = sum(1 for row in results if str(row.get("candidate_review_status") or "") == "qualified_candidate")
    live_count = sum(1 for row in results if str(row.get("live_candidate_status") or "") == "live_candidate")
    review_count = _int(metadata.get("review_candidate_count"), fallback=review_count)
    qualified_count = _int(metadata.get("qualified_candidate_count"), fallback=qualified_count)
    live_count = _int(metadata.get("live_candidate_count"), fallback=live_count)
    candidate_count = _int(metadata.get("candidate_count"), fallback=len(results))
    generated_at = str(metadata.get("generated_at") or all_results.get("timestamp") or "")
    promoted_rows = [
        row
        for row in results
        if str(row.get("candidate_review_status") or "") == "qualified_candidate"
        or str(row.get("live_candidate_status") or "") == "live_candidate"
    ]
    latest_promotion_at = max(
        (
            str(
                row.get("promoted_at")
                or row.get("qualified_at")
                or row.get("live_candidate_at")
                or row.get("source_scan_generated_at")
                or generated_at
            )
            for row in promoted_rows
        ),
        default="",
    )
    top_candidates = sorted(
        results,
        key=lambda row: (
            0 if str(row.get("live_candidate_status") or "") == "live_candidate" else 1,
            0 if str(row.get("candidate_review_status") or "") == "qualified_candidate" else 1,
            -(_float(row.get("avg_return_pct")) or 0.0),
        ),
    )[:8]
    if live_count:
        status = "live_candidate"
        label = "已有 live 候选"
    elif qualified_count:
        status = "qualified_candidate"
        label = "已有合格候选"
    elif candidate_count:
        status = "review_candidate"
        label = "仅研究候选"
    else:
        status = "missing"
        label = "暂无候选"
    return {
        "status": status,
        "label": label,
        "generated_at": generated_at,
        "candidate_count": candidate_count,
        "review_candidate_count": review_count,
        "qualified_candidate_count": qualified_count,
        "live_candidate_count": live_count,
        "latest_promotion_at": latest_promotion_at,
        "valid_for_live_decision": bool(metadata.get("valid_for_live_decision")),
        "issues": _list(producer_output.get("issues")) if isinstance(producer_output, dict) else [],
        "top_candidates": [
            {
                "candidate_id": str(row.get("candidate_id") or row.get("metric_name") or ""),
                "candidate_review_status": str(row.get("candidate_review_status") or "review_candidate"),
                "live_candidate_status": str(row.get("live_candidate_status") or "not_live_ready"),
                "total_triggers": _int(row.get("total_triggers")),
                "win_rate": _float(row.get("win_rate")),
                "avg_return_pct": _float(row.get("avg_return_pct")),
                "walk_forward_quality_passed_folds": _int(row.get("walk_forward_quality_passed_folds")),
                "walk_forward_passed_trade_share": _float(row.get("walk_forward_passed_trade_share")),
                "live_block_reasons": _list(row.get("live_block_reasons"))[:5],
            }
            for row in top_candidates
        ],
    }


def _candidate_authenticity_report(payload: dict[str, Any]) -> dict[str, Any]:
    payload_candidates = _nested(payload, "payload", "candidate_summaries")
    candidates = payload_candidates if isinstance(payload_candidates, list) and payload_candidates else payload.get("top_candidates")
    candidates = candidates if isinstance(candidates, list) else []
    rows = [_candidate_authenticity_row(candidate) for candidate in candidates if isinstance(candidate, dict)]
    return {
        "source": str(payload.get("lab") or ""),
        "generated_at": str(payload.get("generated_at") or ""),
        "candidate_count": _int(payload.get("candidate_count"), fallback=len(rows)),
        "suspect_count": sum(1 for row in rows if row["verdict"] == "suspect_false_positive"),
        "research_only_count": sum(1 for row in rows if row["verdict"] == "research_only"),
        "watch_count": sum(1 for row in rows if row["verdict"] == "candidate_watch"),
        "missing_authenticity_count": sum(1 for row in rows if not row["has_authenticity"]),
        "top_candidates": rows[:8],
    }


def _candidate_authenticity_row(candidate: dict[str, Any]) -> dict[str, Any]:
    summary = candidate.get("summary") if isinstance(candidate.get("summary"), dict) else candidate
    authenticity = candidate.get("authenticity") if isinstance(candidate.get("authenticity"), dict) else {}
    reason_codes = _list(authenticity.get("reason_codes"))
    return {
        "candidate_id": str(candidate.get("candidate_id") or ""),
        "factor_names": [str(item) for item in _list(candidate.get("factor_names"))][:6],
        "verdict": str(authenticity.get("verdict") or "authenticity_missing"),
        "has_authenticity": bool(authenticity),
        "reason_codes": [str(item) for item in reason_codes[:8]],
        "total_rows": _int(authenticity.get("total_rows"), fallback=candidate.get("row_count")),
        "total_signals": _int(authenticity.get("total_signals"), fallback=summary.get("total_signals")),
        "signal_day_count": _int(authenticity.get("signal_day_count")),
        "single_day_signal_ratio": _float(authenticity.get("single_day_signal_ratio")),
        "top_return_contribution_ratio": _float(authenticity.get("top_return_contribution_ratio")),
        "win_rate_like": _float(summary.get("win_rate_like")),
        "avg_return_pct": _float(summary.get("avg_return_pct")),
        "signal_rate": _float(summary.get("signal_rate")),
        "factor_value_repetition": _list(authenticity.get("factor_value_repetition"))[:3],
    }


def _candidate_authenticity_rank(candidate: dict[str, Any]) -> int:
    verdict = str(candidate.get("verdict") or "")
    return {
        "suspect_false_positive": 0,
        "authenticity_missing": 1,
        "research_only": 2,
        "candidate_watch": 3,
        "not_promising": 4,
    }.get(verdict, 5)


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


def _real_order_gate_summary(gate: dict[str, Any]) -> dict[str, Any]:
    gate = gate if isinstance(gate, dict) else {}
    return {
        "enabled": bool(gate.get("enabled", False)),
        "allowed": bool(gate.get("allowed", False)),
        "action": str(gate.get("action") or ""),
        "automation_boundary": str(gate.get("automation_boundary") or ""),
        "reason_codes": [str(item) for item in _list(gate.get("reason_codes"))[:8]],
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
    quant_metric_rows = _quant_cycle_metric_rows(quant_root, limit=80)
    return {
        "cycle_status_timeline": _cycle_status_timeline(quant_root, limit=80),
        "quant_metric_series": quant_metric_rows or _quant_metric_series(bot_samples),
        "reason_code_counts": _reason_code_counts(bot_samples, limit=10),
        "consensus_quality_series": _quant_consensus_quality_series(quant_root, limit=80) or _consensus_quality_series(bot_samples),
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


def _quant_cycle_metric_rows(quant_root: Path, *, limit: int) -> list[dict[str, Any]]:
    cycles_root = quant_root / "runtime" / "cycles"
    try:
        roots = sorted(
            [path for path in cycles_root.iterdir() if path.is_dir() and (path / "decision.json").exists()],
            key=lambda path: _cycle_sort_timestamp(path),
            reverse=True,
        )[:limit]
    except OSError:
        return []
    rows: list[dict[str, Any]] = []
    for root in reversed(roots):
        payload = _read_json(root / "decision.json")
        decision = payload.get("decision") if isinstance(payload, dict) else {}
        decision = decision if isinstance(decision, dict) else {}
        risk = decision.get("risk_report") if isinstance(decision, dict) else {}
        risk = risk if isinstance(risk, dict) else {}
        metadata = payload.get("metadata") if isinstance(payload, dict) else {}
        metadata = metadata if isinstance(metadata, dict) else {}
        trigger = decision.get("trigger_state") if isinstance(decision, dict) else {}
        trigger = trigger if isinstance(trigger, dict) else {}
        estimated_cost_pct = _first_present(
            metadata.get("estimated_cost_pct"),
            _sum_optional_numbers(
                metadata.get("estimated_fee_pct"),
                metadata.get("estimated_slippage_pct"),
                metadata.get("estimated_funding_pct"),
            ),
        )
        net_edge_pct = _first_present(
            metadata.get("net_edge_pct"),
            _net_edge_from_gross_and_cost(metadata.get("estimated_gross_edge_pct"), estimated_cost_pct),
        )
        rows.append(
            {
                "generated_at": str(payload.get("generated_at") or _mtime_iso(root / "decision.json")),
                "sample_id": root.name,
                "action": decision.get("action") or "",
                "data_health_score": _chart_float(risk.get("data_health_score"), scale_unit=True),
                "confidence": _chart_float(decision.get("confidence"), scale_unit=True),
                "thesis_score": _chart_float(decision.get("thesis_score"), scale_unit=True),
                "entry_timing_score": _chart_float(trigger.get("entry_timing_score"), scale_unit=True),
                "net_edge_pct": _chart_float(net_edge_pct, scale_pct=True),
                "estimated_cost_pct": _chart_float(estimated_cost_pct, scale_pct=True),
                "edge_source": str(metadata.get("edge_source") or ""),
                "edge_estimate_status": str(metadata.get("edge_estimate_status") or ""),
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
                "thesis_score": _chart_float(sample.get("thesis_score"), scale_unit=True),
                "entry_timing_score": _chart_float(sample.get("entry_timing_score"), scale_unit=True),
                "net_edge_pct": _chart_float(sample.get("net_edge_pct"), scale_pct=True),
                "estimated_cost_pct": _chart_float(sample.get("estimated_cost_pct"), scale_pct=True),
                "edge_source": str(sample.get("edge_source") or ""),
                "edge_estimate_status": str(sample.get("edge_estimate_status") or ""),
            }
        )
    return rows


def _trigger_watch_summary(
    *,
    quant_root: Path,
    bot_samples_path: Path,
    bot_latest_cycle: dict[str, Any] | None = None,
) -> dict[str, Any]:
    rows = _merge_trigger_watch_rows(
        _trigger_watch_quant_rows(quant_root, limit=160),
        _trigger_watch_bot_rows(bot_samples_path, limit=160),
    )
    source = "quant_cycles+bot_samples"
    if bot_latest_cycle:
        latest_bot_row = _trigger_watch_row_from_bot_sample(bot_latest_cycle)
        if latest_bot_row and not _has_trigger_watch_row(rows, latest_bot_row):
            rows.append(latest_bot_row)
            rows.sort(key=lambda row: _parse_timestamp(row.get("generated_at")) or 0.0)
            source = f"{source}+latest_cycle"
    watch_indexes = [index for index, row in enumerate(rows) if _is_trigger_watch_row(row)]
    current = _trigger_watch_current(rows, watch_indexes)
    stats = _trigger_watch_stats(rows, watch_indexes)
    recent = [_trigger_watch_public_row(rows[index]) for index in watch_indexes[-6:]]
    return {
        "status": "active" if current else "idle",
        "label": "等待触发" if current else "暂无等待触发",
        "source": source,
        "threshold_confidence": TRIGGER_WATCH_CONFIDENCE_THRESHOLD,
        "horizons": list(TRIGGER_WATCH_HORIZONS),
        "current": current or {},
        "stats": stats,
        "recent": recent,
    }


def _merge_trigger_watch_rows(*row_groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for group in row_groups:
        for row in group:
            if not _has_trigger_watch_row(rows, row):
                rows.append(row)
    rows.sort(key=_trigger_watch_sort_ts)
    _fill_missing_trigger_watch_prices(rows)
    return rows


def _has_trigger_watch_row(rows: list[dict[str, Any]], candidate: dict[str, Any]) -> bool:
    candidate_source = str(candidate.get("source") or "")
    candidate_id = candidate.get("sample_id")
    candidate_at = str(candidate.get("generated_at") or "")
    return any(
        str(row.get("source") or "") == candidate_source
        and row.get("sample_id") == candidate_id
        and str(row.get("generated_at") or "") == candidate_at
        for row in rows
    )


def _trigger_watch_current(rows: list[dict[str, Any]], watch_indexes: list[int]) -> dict[str, Any] | None:
    if watch_indexes and watch_indexes[-1] == len(rows) - 1:
        return _trigger_watch_public_row(rows[watch_indexes[-1]])
    return None


def _trigger_watch_stats(rows: list[dict[str, Any]], watch_indexes: list[int]) -> dict[str, Any]:
    stats: dict[str, Any] = {"sample_count": len(watch_indexes)}
    for horizon in TRIGGER_WATCH_HORIZONS:
        outcomes = [
            value
            for index in watch_indexes
            for value in [_directional_future_return(rows, index, horizon)]
            if value is not None
        ]
        stats[f"resolved_{horizon}_count"] = len(outcomes)
        stats[f"avg_return_{horizon}"] = _average(outcomes)
        stats[f"positive_rate_{horizon}"] = _positive_rate(outcomes)
    return stats


def _trigger_watch_public_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "sample_id": row.get("sample_id"),
        "source": row.get("source"),
        "generated_at": row.get("generated_at"),
        "action": row.get("action"),
        "direction": row.get("direction"),
        "confidence": row.get("confidence"),
        "setup_strength": row.get("setup_strength"),
        "entry_timing_score": row.get("entry_timing_score"),
        "trigger_ready": row.get("trigger_ready"),
        "risk_filter_status": row.get("risk_filter_status"),
        "block_reason": row.get("block_reason"),
        "price": row.get("price"),
        "reason": row.get("reason"),
    }


def _trigger_watch_quant_rows(quant_root: Path, *, limit: int) -> list[dict[str, Any]]:
    cycles_root = quant_root / "runtime" / "cycles"
    try:
        roots = sorted(
            [path for path in cycles_root.iterdir() if path.is_dir() and (path / "decision.json").exists()],
            key=lambda path: _cycle_fast_sort_timestamp(path),
            reverse=True,
        )[:limit]
    except OSError:
        return []
    rows: list[dict[str, Any]] = []
    for root in reversed(roots):
        payload = _read_json(root / "decision.json")
        decision = payload.get("decision") if isinstance(payload, dict) else {}
        decision = decision if isinstance(decision, dict) else {}
        if not decision:
            continue
        risk = decision.get("risk_report") if isinstance(decision, dict) else {}
        risk = risk if isinstance(risk, dict) else {}
        trigger = decision.get("trigger_state") if isinstance(decision, dict) else {}
        trigger = trigger if isinstance(trigger, dict) else {}
        setup = decision.get("setup_state") if isinstance(decision, dict) else {}
        setup = setup if isinstance(setup, dict) else {}
        metadata = payload.get("metadata") if isinstance(payload, dict) else {}
        metadata = metadata if isinstance(metadata, dict) else {}
        rows.append(
            {
                "source": "quant_cycle",
                "sample_id": root.name,
                "_sort_ts": _cycle_fast_sort_timestamp(root),
                "generated_at": str(payload.get("generated_at") or _mtime_iso(root / "decision.json")),
                "action": str(decision.get("action") or ""),
                "direction": str(decision.get("direction") or setup.get("setup_direction") or trigger.get("trigger_direction") or ""),
                "confidence": _float(decision.get("confidence")),
                "setup_strength": _float(setup.get("setup_strength")),
                "entry_timing_score": _float(trigger.get("entry_timing_score")),
                "trigger_ready": trigger.get("trigger_ready"),
                "risk_filter_status": str(risk.get("risk_filter_status") or ""),
                "block_reason": str(decision.get("execution_block_reason") or ""),
                "reasoning_summary": str(decision.get("reasoning_summary") or ""),
                "reason_codes": _list(risk.get("reason_codes")) + _list(risk.get("degrade_flags")),
                "price": _decision_price(metadata, decision),
            }
        )
    return rows


def _trigger_watch_bot_rows(bot_samples_path: Path, *, limit: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for sample in _tail_jsonl(bot_samples_path, limit=limit):
        row = _trigger_watch_row_from_bot_sample(sample)
        if row:
            rows.append(row)
    rows.sort(key=_trigger_watch_sort_ts)
    return rows


def _trigger_watch_row_from_bot_sample(sample: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(sample, dict):
        return None
    runtime_snapshot = sample.get("runtime_snapshot") if isinstance(sample.get("runtime_snapshot"), dict) else {}
    position = runtime_snapshot.get("position") if isinstance(runtime_snapshot, dict) else {}
    position = position if isinstance(position, dict) else {}
    generated_at = str(sample.get("finished_at") or sample.get("generated_at") or sample.get("started_at") or "")
    return {
        "source": "bot_sample",
        "sample_id": sample.get("sample_id"),
        "_sort_ts": _parse_timestamp(generated_at),
        "generated_at": generated_at,
        "action": str(sample.get("effective_action") or sample.get("requested_action") or ""),
        "direction": str(sample.get("direction") or sample.get("setup_direction") or sample.get("trigger_direction") or ""),
        "confidence": _float(sample.get("confidence")),
        "setup_strength": _extract_setup_strength(sample),
        "entry_timing_score": _float(sample.get("entry_timing_score")),
        "trigger_ready": sample.get("trigger_ready"),
        "risk_filter_status": str(sample.get("risk_filter_status") or ""),
        "block_reason": str(sample.get("execution_block_reason") or ""),
        "reasoning_summary": str(sample.get("reasoning_summary") or ""),
        "reason_codes": _list(sample.get("reason_codes")) + _list(sample.get("transition_reason_codes")),
        "price": _first_float(
            sample.get("consensus_mark_price"),
            sample.get("mark_price"),
            _nested(sample, "market_snapshot", "mark_price"),
            position.get("mark_price"),
            sample.get("last_price"),
        ),
    }


def _is_trigger_watch_row(row: dict[str, Any]) -> bool:
    confidence = _float(row.get("confidence"))
    if confidence is None or confidence < TRIGGER_WATCH_CONFIDENCE_THRESHOLD:
        return False
    action = str(row.get("action") or "").lower()
    if action not in {"wait", "observe_only", "observe"}:
        return False
    direction = str(row.get("direction") or "").lower()
    if direction not in {"long", "short"}:
        return False
    summary = str(row.get("reasoning_summary") or "").lower()
    reasons = " ".join(str(code).lower() for code in _list(row.get("reason_codes")))
    trigger_ready = row.get("trigger_ready")
    trigger_not_ready = trigger_ready is False or str(trigger_ready).lower() == "false"
    return (
        trigger_not_ready
        or "trigger_15m_ready=no" in summary
        or "setup_ready_waiting_trigger" in summary
        or "setup_ready_waiting_trigger" in reasons
        or str(row.get("block_reason") or "").lower() in {"not_entry_action", "waiting_for_trigger"}
    )


def _directional_future_return(rows: list[dict[str, Any]], index: int, horizon: int) -> float | None:
    if horizon <= 0:
        return None
    base_price = _float(rows[index].get("price"))
    if base_price is None or base_price <= 0:
        return None
    base_ts = _trigger_watch_sort_ts(rows[index])
    future_rows = [
        row
        for row in rows[index + 1 :]
        if _trigger_watch_sort_ts(row) > base_ts and _float(row.get("price")) is not None
    ]
    quant_price_rows = [row for row in future_rows if row.get("source") == "quant_cycle"]
    if quant_price_rows:
        future_rows = quant_price_rows
    future_price = None
    seen = 0
    for row in future_rows:
        price = _float(row.get("price"))
        if price is None:
            continue
        seen += 1
        if seen == horizon:
            future_price = price
            break
    if future_price is None:
        return None
    direction = str(rows[index].get("direction") or "").lower()
    raw_return = (future_price - base_price) / base_price
    if direction == "short":
        raw_return = -raw_return
    elif direction != "long":
        return None
    return round(raw_return, 8)


def _trigger_watch_sort_ts(row: dict[str, Any]) -> float:
    timestamp = _float(row.get("_sort_ts"))
    if timestamp is not None:
        return timestamp
    return _parse_timestamp(row.get("generated_at")) or 0.0


def _fill_missing_trigger_watch_prices(rows: list[dict[str, Any]]) -> None:
    price_points = [
        (_trigger_watch_sort_ts(row), _float(row.get("price")))
        for row in rows
        if _float(row.get("price")) is not None
    ]
    for row in rows:
        if _float(row.get("price")) is not None:
            continue
        row_ts = _trigger_watch_sort_ts(row)
        if row_ts <= 0:
            continue
        fallback: tuple[float, float] | None = None
        for price_ts, price in price_points:
            if price is None:
                continue
            age = row_ts - price_ts
            if 0 <= age <= 20 * 60:
                fallback = (age, price)
            elif fallback is None and 0 > age >= -2 * 60:
                fallback = (abs(age), price)
        if fallback is not None:
            row["price"] = fallback[1]
            row["price_source"] = "nearest_quant_price"


def _decision_price(metadata: dict[str, Any], decision: dict[str, Any]) -> float | None:
    return _first_float(
        metadata.get("consensus_mark_price"),
        _nested(metadata, "market_data", "consensus_mark_price"),
        metadata.get("consensus_worst_case_price"),
        metadata.get("binance_mark_price"),
        _nested(metadata, "runtime_snapshot", "position", "mark_price"),
        decision.get("mark_price"),
        decision.get("price"),
    )


def _cycle_fast_sort_timestamp(root: Path) -> float:
    match = re.search(r"(\d{8}T\d{6}Z)", root.name)
    if match:
        try:
            return datetime.strptime(match.group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).timestamp()
        except ValueError:
            pass
    try:
        return root.stat().st_mtime
    except OSError:
        return 0.0


def _extract_setup_strength(sample: dict[str, Any]) -> float | None:
    value = sample.get("setup_strength")
    if value not in (None, ""):
        return _float(value)
    summary = str(sample.get("reasoning_summary") or "")
    match = re.search(r"setup_15m=[a-z_]+:([0-9.]+)", summary, re.IGNORECASE)
    return _float(match.group(1)) if match else None


def _first_float(*values: Any) -> float | None:
    for value in values:
        number = _float(value)
        if number is not None:
            return number
    return None


def _float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not number == number:
        return None
    return number


def _average(values: list[float]) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), 8)


def _positive_rate(values: list[float]) -> float | None:
    if not values:
        return None
    return round(sum(1 for value in values if value > 0) / len(values), 6)


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


def _quant_consensus_quality_series(quant_root: Path, *, limit: int) -> list[dict[str, Any]]:
    cycles_root = quant_root / "runtime" / "cycles"
    try:
        roots = sorted(
            [path for path in cycles_root.iterdir() if path.is_dir() and (path / "decision.json").exists()],
            key=lambda path: _cycle_fast_sort_timestamp(path),
            reverse=True,
        )[:limit]
    except OSError:
        return []
    rows: list[dict[str, Any]] = []
    for root in reversed(roots):
        payload = _read_json(root / "decision.json")
        metadata = payload.get("metadata") if isinstance(payload, dict) else {}
        metadata = metadata if isinstance(metadata, dict) else {}
        quality = str(metadata.get("consensus_quality") or "")
        rows.append(
            {
                "generated_at": str(payload.get("generated_at") or _mtime_iso(root / "decision.json")),
                "sample_id": root.name,
                "quality": quality,
                "quality_value": _consensus_quality_value(quality),
                "source_count": _chart_float(metadata.get("consensus_source_count")),
                "market_data_mode": str(metadata.get("market_data_mode") or ""),
            }
        )
    return [
        row
        for row in rows
        if row["quality"] or row["source_count"] is not None or row["market_data_mode"]
    ]


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
        number = number if abs(number) > 1.0 else number * 100.0
    elif scale_unit and abs(number) > 1.0:
        number = number / 100.0
    return round(number, 6)


def _sum_optional_numbers(*values: Any) -> float | None:
    total = 0.0
    found = False
    for value in values:
        try:
            if value is None or value == "":
                continue
            total += float(value)
            found = True
        except (TypeError, ValueError):
            continue
    return total if found else None


def _net_edge_from_gross_and_cost(gross_edge_pct: Any, estimated_cost_pct: Any) -> float | None:
    try:
        if gross_edge_pct is None or gross_edge_pct == "":
            return None
        gross = float(gross_edge_pct)
        cost = float(estimated_cost_pct or 0.0)
        return gross - cost
    except (TypeError, ValueError):
        return None


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


def _handoff_factor_lookup_freshness(handoff: dict[str, Any]) -> dict[str, Any]:
    reason_codes: list[str] = []
    generated_at = handoff.get("factor_lookup_generated_at")
    timestamp = _parse_timestamp(generated_at)
    if timestamp is None:
        reason_codes.append("handoff_freshness_unknown")
        return {
            "age_seconds": None,
            "stale": True,
            "status": "unknown",
            "reason_codes": reason_codes,
        }
    age_seconds = round(datetime.now(timezone.utc).timestamp() - timestamp, 3)
    if age_seconds > _factor_lookup_stale_after_sec():
        reason_codes.append("factor_lookup_age_over_threshold")
    if age_seconds < -FACTOR_LOOKUP_FUTURE_TOLERANCE_SEC:
        reason_codes.append("factor_lookup_generated_at_in_future")
    producer_stale = bool(handoff.get("factor_lookup_stale", False))
    stale = producer_stale or bool(reason_codes)
    if not producer_stale and reason_codes:
        reason_codes.append("factor_lookup_stale_flag_conflict")
    if producer_stale:
        reason_codes.append("factor_lookup_stale")
    status = "stale" if stale else "fresh"
    return {
        "age_seconds": age_seconds,
        "stale": stale,
        "status": status,
        "reason_codes": list(dict.fromkeys(reason_codes)),
    }


def _factor_lookup_stale_after_sec() -> int:
    raw = os.environ.get("DASHBOARD_FACTOR_LOOKUP_MAX_AGE_SEC") or os.environ.get("FACTOR_LOOKUP_MAX_AGE_SEC")
    if not raw:
        return DEFAULT_FACTOR_LOOKUP_STALE_AFTER_SEC
    try:
        return max(0, int(float(raw)))
    except ValueError:
        return DEFAULT_FACTOR_LOOKUP_STALE_AFTER_SEC


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


def _first_list_present(*values: Any) -> list[Any]:
    for value in values:
        if isinstance(value, list) and value:
            return value
    return []


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
    return read_json(path)


def _json_read_status(path: Path) -> dict[str, Any]:
    return json_read_status(path)


def _tail_jsonl(path: Path, *, limit: int) -> list[dict[str, Any]]:
    return tail_jsonl(path, limit=limit)


def _jsonl_count(path: Path) -> int:
    return jsonl_count(path)


def _mtime_iso(path: Path) -> str:
    return mtime_iso(path)


def _int(value: Any, *, fallback: Any = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(fallback)
        except (TypeError, ValueError):
            return 0
