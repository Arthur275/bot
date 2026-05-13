from __future__ import annotations

import json
import re
import threading
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from datetime import datetime, timedelta, timezone
from pathlib import Path

import dashboard.app as dashboard_app
from dashboard.app import DashboardHandler, OverviewSnapshotCache
from dashboard.data_sources import DashboardPaths, load_dashboard_snapshot
from dashboard.decision_review import build_daily_review, build_decision_review, normalize_decision_review, write_governance_suggestions
from dashboard.status_rules import age_seconds, kill_switch_status, lookup_status, runtime_status

REPO_ROOT = Path(__file__).resolve().parents[1]


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _append_jsonl(path: Path, payloads: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(payload) for payload in payloads), encoding="utf-8")


def test_runtime_status_marks_running_stale_and_error() -> None:
    now = datetime.now(timezone.utc)

    running = runtime_status(generated_at=now.isoformat(), ok=True, stale_after_sec=60)
    stale = runtime_status(generated_at=(now - timedelta(seconds=90)).isoformat(), ok=True, stale_after_sec=60)
    error = runtime_status(generated_at=now.isoformat(), ok=True, stale_after_sec=60, error="network")

    assert running["label"] == "RUNNING"
    assert stale["label"] == "STALE"
    assert error["label"] == "ERROR"
    assert kill_switch_status(enabled=True)["level"] == "red"
    assert lookup_status(generated_at=now.isoformat())["label"] == "FRESH"


def test_age_seconds_treats_naive_timestamps_as_local_time() -> None:
    local_now = datetime.now().replace(microsecond=0)
    utc_now = local_now.astimezone(timezone.utc)

    assert age_seconds(local_now.isoformat(), now=utc_now) == 0


def test_load_dashboard_snapshot_reads_bot_and_quant_runtime_files(tmp_path: Path) -> None:
    bot_root = tmp_path / "eth_trading_bot"
    quant_root = tmp_path / "quant_system_rebuild"
    generated_at = datetime.now(timezone.utc).isoformat()

    _write_json(bot_root / "runtime" / "bot_runtime_scheduler" / "heartbeat.json", {"generated_at": generated_at, "status": "ok"})
    _write_json(
        bot_root / "runtime" / "bot_runtime_scheduler" / "latest_cycle.json",
        {
            "finished_at": generated_at,
            "effective_action": "entry_long",
            "requested_action": "entry_long",
            "direction": "long",
            "risk_filter_status": "pass",
            "confidence": 0.72,
            "sizing_tier": "tier_2",
            "reasoning_summary": "trend aligned",
            "reason_codes": ["trend_aligned"],
            "risk_reason_codes": ["market_data_restricted_two_source", "edge_estimate_missing"],
            "execution_block_reason": "not_entry_action",
            "execution_layer_reasoning": "higher_timeframe_not_ready",
            "execution_opportunity_status": "blocked",
            "data_health_score": 65.0,
            "market_data_mode": "restricted_two_source",
            "consensus_quality": "degraded",
            "consensus_source_count": 2,
            "consensus_sources": ["OKX", "Bitget"],
            "binance_source_health": "unavailable",
            "binance_source_failure_reason": "HTTP 451",
            "net_edge_pct": None,
            "estimated_cost_pct": 0.0012,
            "estimated_fee_pct": 0.0006,
            "estimated_slippage_pct": 0.0004,
            "estimated_funding_pct": 0.0002,
            "edge_source": "consensus",
            "execution_allowed": True,
            "executable_size_pct": 0.005,
            "position_size_pct": 0.005,
            "position_cap_pct": 0.03,
            "probe_source": "strong_momentum_probe",
            "probe_risk_tier": "strong_momentum",
            "probe_expiry_bars": 6,
            "probe_expiry_timeframe": "15m",
            "probe_invalid_if_no_followthrough": True,
            "runtime_vetoes": [],
            "research_gate_status": "blocked",
            "research_gate_reasons": ["wf_quality_insufficient"],
            "transition_reason_codes": ["setup_ready_waiting_trigger"],
            "sizing_reason_codes": ["capped_by_strong_momentum_probe", "edge_estimate_missing"],
            "setup_direction": "long",
            "trigger_direction": "long",
            "trigger_ready": False,
            "setup_strength": 0.83,
            "entry_timing_score": 0.52,
            "retest_support": True,
            "breakout_support": False,
            "slope_support": True,
            "overlay_bias": "bullish",
            "runtime_account_equity": 88.25,
            "runtime_account_equity_source": "totalEq",
            "runtime_unrealized_pnl_usd": 1.75,
            "exchange_venue": "okx_usdt_swap",
            "exchange_symbol": "ETH-USDT-SWAP",
            "runtime_snapshot": {
                "fetched_at": generated_at,
                "snapshot_valid": True,
                "position": {"position_state": "FLAT", "direction": "neutral", "size_pct": 0.0, "mark_price": 3450.0},
            },
            "automation_boundary": "real_order_submission_candidate",
        },
    )
    _write_json(
        bot_root / "runtime" / "state_store.json",
        {
            "execution_state": "idle",
            "automation_state": "observing",
            "observed_position_state": "ARMED",
            "observed_position_direction": "long",
            "observed_position_size_pct": 0.25,
        },
    )
    _write_json(
        bot_root / "runtime" / "bot_runtime_scheduler" / "latest_candidate_execution_package.json",
        {
            "package_id": "pkg-1",
            "action": "entry_long",
            "direction": "long",
            "generated_at": generated_at,
            "expires_at": generated_at,
            "real_order_gate": {"allowed": True},
            "execution_commands": [{"target": "entry_order"}, {"target": "maintain_protective_stop"}],
        },
    )
    _append_jsonl(
        bot_root / "runtime" / "real_order_worker" / "audit.jsonl",
        [{"event_type": "real_order_worker", "generated_at": generated_at, "payload": {"status": "skipped"}}],
    )
    _append_jsonl(bot_root / "runtime" / "bot_runtime_scheduler" / "samples.jsonl", [{"sample_id": "s1"}])

    _write_json(quant_root / "runtime" / "scheduler" / "heartbeat.json", {"generated_at": generated_at, "status": "ok"})
    _write_json(
        quant_root / "runtime" / "scheduler" / "reason_code_map.json",
        {
            "schema": "reason_code_map_v1",
            "reason_code_text": {
                "truth_candidate_unqualified": "quant 共享映射：真实候选不合格",
                "research_not_ready": "quant 共享映射：research 未就绪",
                "wf_quality_insufficient": "quant 共享映射：walk-forward 质量不足",
            },
        },
    )
    _write_json(
        quant_root / "runtime" / "scheduler" / "research_health.json",
        {
            "generated_at": generated_at,
            "status": "blocked",
            "issues": ["research_not_ready"],
            "metadata": {
                "ready": False,
                "research_refresh": {
                    "refresh_aliases": True,
                    "refresh_aliases_every": 12,
                    "loop_iteration": 24,
                },
                "research_bundle": {
                    "decision_ready": False,
                    "research_health": {
                        "research_health_status": "unavailable",
                        "decision": "unavailable",
                        "freshness": "stale",
                        "dataset_timestamp": "2026-05-04T00:00:00",
                        "reason_codes": ["research_not_ready", "wf_quality_insufficient", "truth_candidate_unqualified"],
                        "research_health_summary": "bundle stale",
                    },
                },
            },
        },
    )
    _write_json(
        quant_root / "runtime" / "analysis" / "factor_summary.json",
        {
            "generated_at": generated_at,
            "total_samples": 25,
            "unique_observation_count": 20,
            "factor_lookup_rows": 9,
            "top_reason_codes": [{"name": "trend_aligned", "count": 4}],
            "top_degrade_flags": [{"name": "research_stale", "count": 2}],
        },
    )
    _write_json(
        quant_root / "runtime" / "analysis" / "factor_lookup_summary.json",
        {
            "generated_at": generated_at,
            "lookup_version": "lookup-20260504",
            "factor_lookup_rows": 9,
        },
    )
    _write_json(
        quant_root / "runtime" / "analysis" / "factor_governance_summary.json",
        {
            "generated_at": generated_at,
            "lookup_version": "lookup-20260504",
            "status": "watch",
            "reason_codes": ["sample_count_low"],
            "rows": [
                {
                    "factor_name": "trigger_state.entry_timing_score",
                    "factor_value_bucket": "0.50-0.75",
                    "factor_grade": "core",
                    "factor_lifecycle": "watch",
                    "factor_effect": "neutral",
                    "sample_count": 9,
                    "win_rate": 0.55,
                    "stop_hit_rate": 0.2,
                    "net_expectancy_pct": 0.0,
                    "reason_codes": ["sample_count_low"],
                }
            ],
        },
    )
    _write_json(
        quant_root / "runtime" / "reports" / "candidate_scan_feature_matrix_smoke.json",
        {
            "status": "ok",
            "lab": "vectorbt_candidate_scan",
            "generated_at": generated_at,
            "candidate_count": 2,
            "payload": {
                "candidate_summaries": [
                    {
                        "candidate_id": "funding_rate",
                        "factor_names": ["funding_rate"],
                        "row_count": 46,
                        "summary": {
                            "total_signals": 4,
                            "signal_rate": 0.088889,
                            "win_rate_like": 1.0,
                            "avg_return_pct": 0.580943,
                        },
                        "authenticity": {
                            "verdict": "suspect_false_positive",
                            "reason_codes": ["high_win_rate_low_sample", "time_alignment_sensitive_factor"],
                            "total_rows": 46,
                            "total_signals": 4,
                            "signal_day_count": 1,
                            "single_day_signal_ratio": 1.0,
                            "top_return_contribution_ratio": 0.84,
                        },
                    },
                    {
                        "candidate_id": "entry_timing_score+setup_strength",
                        "factor_names": ["entry_timing_score", "setup_strength"],
                        "row_count": 80,
                        "summary": {
                            "total_signals": 40,
                            "signal_rate": 0.5,
                            "win_rate_like": 0.55,
                            "avg_return_pct": 0.012,
                        },
                        "authenticity": {
                            "verdict": "candidate_watch",
                            "reason_codes": [],
                            "total_rows": 80,
                            "total_signals": 40,
                            "signal_day_count": 4,
                        },
                    },
                ]
            },
        },
    )
    _write_json(
        quant_root / "runtime" / "fresh_research" / "all_results.json",
        {
            "timestamp": "20260511_150606",
            "results": [
                {
                    "candidate_id": "funding_rate",
                    "total_triggers": 4,
                    "avg_return_pct": 0.580943,
                    "win_rate": 1.0,
                    "source_scan_generated_at": "2026-05-11T15:06:06",
                    "candidate_review_status": "review_candidate",
                    "live_candidate_status": "not_live_ready",
                    "live_block_reasons": ["scan_row_count_below_live_minimum"],
                    "walk_forward_quality_passed_folds": 0,
                    "walk_forward_passed_trade_share": 0.0,
                },
                {
                    "candidate_id": "entry_timing_score+setup_strength",
                    "total_triggers": 40,
                    "avg_return_pct": 0.012,
                    "win_rate": 0.55,
                    "source_scan_generated_at": "2026-05-11T15:07:06",
                    "candidate_review_status": "qualified_candidate",
                    "live_candidate_status": "live_candidate",
                    "live_block_reasons": [],
                    "walk_forward_quality_passed_folds": 3,
                    "walk_forward_passed_trade_share": 0.72,
                },
            ],
        },
    )
    _write_json(
        quant_root / "runtime" / "fresh_research" / "producer_output_latest.json",
        {
            "issues": [],
            "metadata": {
                "generated_at": "2026-05-11T15:08:06",
                "valid_for_live_decision": True,
                "review_candidate_count": 1,
                "qualified_candidate_count": 1,
                "live_candidate_count": 1,
                "candidate_count": 2,
            },
        },
    )
    _write_json(quant_root / "runtime" / "analysis" / "factor_ingest_latest.json", {"generated_at": generated_at})
    _write_json(
        quant_root / "runtime" / "cycles" / "latest_strict_live" / "execution_handoff.json",
        {
            "generated_at": generated_at,
            "supporting_factor_codes": ["trend_aligned"],
            "opposing_factor_codes": ["crowding_warning"],
            "veto_factor_codes": [],
            "regime_bucket": "trend_long",
            "factor_lookup_version": "lookup-20260504",
            "execution_warnings": ["route_c_missing"],
        },
    )
    _write_json(
        quant_root / "runtime" / "cycles" / "eth-15m-latest" / "decision.json",
        {
            "generated_at": generated_at,
            "decision": {
                "action": "small_probe",
                "direction": "long",
                "confidence": 0.42,
                "thesis_score": 0.58,
                "reasoning_summary": "latest quant cycle",
                "risk_report": {
                    "risk_filter_status": "degraded",
                    "degrade_flags": ["research_degraded"],
                    "data_health_score": 0.65,
                },
                "sizing_decision": {"sizing_tier": "none"},
                "trigger_state": {"entry_timing_score": 0.31},
                "regime_state": {"regime_type": "trend", "direction": "long"},
            },
            "metadata": {
                "market_data_mode": "restricted_two_source",
                "consensus_quality": "degraded",
                "consensus_source_count": 2,
                "estimated_gross_edge_pct": 0.002,
                "estimated_cost_pct": 0.0007,
                "edge_source": "atr_15m_okx",
                "edge_estimate_status": "confirmed_positive",
            },
        },
    )
    _write_json(
        quant_root / "runtime" / "cycles" / "eth-15m-latest" / "scheduler_status.json",
        {"generated_at": generated_at, "status": "ok", "run_id": "eth-15m-latest"},
    )

    snapshot = load_dashboard_snapshot(DashboardPaths(bot_root=bot_root, quant_root=quant_root))

    assert snapshot["runtime"]["factor_collector"]["label"] == "RUNNING"
    assert snapshot["runtime"]["bot_scheduler"]["label"] == "RUNNING"
    assert snapshot["runtime"]["real_worker"]["label"] == "RUNNING"
    assert "decision_review" not in snapshot["runtime"]
    assert snapshot["optional_workers"]["decision_review"]["optional"] is True
    assert snapshot["optional_workers"]["decision_review"]["enabled"] is False
    assert snapshot["optional_workers"]["decision_review"]["label"] == "OPTIONAL_DISABLED"
    assert snapshot["optional_workers"]["decision_review"]["status"] == "disabled"
    assert snapshot["optional_workers"]["decision_review"]["age_sec"] is None
    assert snapshot["factor"]["total_samples"] == 25
    assert snapshot["factor"]["lookup_version"] == "lookup-20260504"
    assert snapshot["factor"]["lookup_rows"] == 9
    assert snapshot["factor"]["governance"]["status"] == "watch"
    assert snapshot["factor"]["governance"]["rows"][0]["factor_lifecycle"] == "watch"
    assert snapshot["factor"]["governance"]["rows"][0]["factor_effect"] == "neutral"
    assert snapshot["factor"]["governance"]["rows"][0]["net_expectancy_pct"] == 0.0
    assert snapshot["factor"]["candidate_authenticity"]["status"] == "needs_review"
    assert snapshot["factor"]["candidate_authenticity"]["suspect_count"] == 1
    assert snapshot["factor"]["candidate_authenticity"]["watch_count"] == 1
    assert snapshot["factor"]["candidate_authenticity"]["top_candidates"][0]["candidate_id"] == "funding_rate"
    assert snapshot["factor"]["candidate_authenticity"]["top_candidates"][0]["verdict"] == "suspect_false_positive"
    assert snapshot["factor"]["candidate_authenticity"]["top_candidates"][0]["reason_codes"] == [
        "high_win_rate_low_sample",
        "time_alignment_sensitive_factor",
    ]
    assert snapshot["factor"]["candidate_promotion"]["status"] == "live_candidate"
    assert snapshot["factor"]["candidate_promotion"]["review_candidate_count"] == 1
    assert snapshot["factor"]["candidate_promotion"]["qualified_candidate_count"] == 1
    assert snapshot["factor"]["candidate_promotion"]["live_candidate_count"] == 1
    assert snapshot["factor"]["candidate_promotion"]["latest_promotion_at"] == "2026-05-11T15:07:06"
    assert snapshot["factor"]["candidate_promotion"]["top_candidates"][0]["candidate_id"] == "entry_timing_score+setup_strength"
    assert snapshot["factor"]["candidate_promotion"]["top_candidates"][0]["live_candidate_status"] == "live_candidate"
    assert snapshot["factor"]["db_available"] is False
    assert snapshot["factor"]["sample_growth"]["bot_scheduler_samples"] == 1
    assert snapshot["performance"]["account_equity"] == 88.25
    assert snapshot["performance"]["account_equity_source"] == "totalEq"
    assert snapshot["performance"]["total_profit_usd"] == 1.75
    assert snapshot["performance"]["source"] == "bot_latest_cycle"
    assert snapshot["performance"]["ignored_source"] == ""
    assert snapshot["quant"]["action"] == "entry_long"
    assert snapshot["quant"]["supporting_factors"] == ["trend_aligned"]
    assert snapshot["quant"]["regime_bucket"] == "trend_long"
    assert snapshot["quant"]["factor_lookup_version"] == "lookup-20260504"
    assert snapshot["quant"]["execution_warnings"] == ["route_c_missing"]
    assert snapshot["quant"]["automation_boundary"] == "real_order_submission_candidate"
    assert snapshot["quant"]["execution_block_reason"] == "not_entry_action"
    assert snapshot["quant"]["execution_layer_reasoning"] == "higher_timeframe_not_ready"
    assert snapshot["quant"]["execution_opportunity_status"] == "blocked"
    assert snapshot["quant"]["risk_reason_codes"] == ["market_data_restricted_two_source", "edge_estimate_missing"]
    assert snapshot["quant"]["thesis_score"] == 0.58
    assert snapshot["quant"]["data_health_score"] == 65.0
    assert snapshot["quant"]["market_data_mode"] == "restricted_two_source"
    assert snapshot["quant"]["consensus_quality"] == "degraded"
    assert snapshot["quant"]["consensus_source_count"] == 2
    assert snapshot["quant"]["consensus_sources"] == ["OKX", "Bitget"]
    assert snapshot["quant"]["binance_source_health"] == "unavailable"
    assert snapshot["quant"]["binance_source_failure_reason"] == "HTTP 451"
    assert snapshot["quant"]["net_edge_pct"] is None
    assert snapshot["quant"]["estimated_cost_pct"] == 0.0012
    assert snapshot["quant"]["estimated_fee_pct"] == 0.0006
    assert snapshot["quant"]["estimated_slippage_pct"] == 0.0004
    assert snapshot["quant"]["estimated_funding_pct"] == 0.0002
    assert snapshot["quant"]["edge_source"] == "consensus"
    assert snapshot["quant"]["execution_allowed"] is True
    assert snapshot["quant"]["executable_size_pct"] == 0.005
    assert snapshot["quant"]["position_size_pct"] == 0.005
    assert snapshot["quant"]["position_cap_pct"] == 0.03
    assert snapshot["quant"]["probe_source"] == "strong_momentum_probe"
    assert snapshot["quant"]["probe_risk_tier"] == "strong_momentum"
    assert snapshot["quant"]["probe_expiry_bars"] == 6
    assert snapshot["quant"]["probe_expiry_timeframe"] == "15m"
    assert snapshot["quant"]["probe_invalid_if_no_followthrough"] is True
    assert snapshot["quant"]["research_gate_status"] == "blocked"
    assert snapshot["quant"]["research_gate_reasons"] == ["wf_quality_insufficient"]
    assert snapshot["quant"]["transition_reason_codes"] == ["setup_ready_waiting_trigger"]
    assert snapshot["quant"]["sizing_reason_codes"] == ["capped_by_strong_momentum_probe", "edge_estimate_missing"]
    assert snapshot["quant"]["setup_direction"] == "long"
    assert snapshot["quant"]["trigger_direction"] == "long"
    assert snapshot["quant"]["trigger_ready"] is False
    assert snapshot["quant"]["setup_strength"] == 0.83
    assert snapshot["quant"]["entry_timing_score"] == 0.52
    assert snapshot["quant"]["retest_support"] is True
    assert snapshot["quant"]["breakout_support"] is False
    assert snapshot["quant"]["slope_support"] is True
    assert snapshot["quant"]["overlay_bias"] == "bullish"
    assert {"cycle_status_timeline", "quant_metric_series", "reason_code_counts", "consensus_quality_series"} <= set(snapshot["charts"])
    assert snapshot["quant"]["research"]["status"] == "unavailable"
    assert snapshot["quant"]["research"]["freshness"] == "stale"
    assert snapshot["quant"]["research"]["refresh_every"] == 12
    assert snapshot["quant"]["research"]["refresh_aliases"] is True
    assert snapshot["quant"]["research"]["reason_texts"][0] == {
        "code": "research_not_ready",
        "text": "quant 共享映射：research 未就绪",
    }
    assert snapshot["quant"]["research"]["reason_texts"][2] == {
        "code": "truth_candidate_unqualified",
        "text": "quant 共享映射：真实候选不合格",
    }
    assert snapshot["charts"]["quant_metric_series"][0]["data_health_score"] == 0.65
    assert snapshot["charts"]["quant_metric_series"][0]["confidence"] == 0.42
    assert snapshot["charts"]["quant_metric_series"][0]["thesis_score"] == 0.58
    assert snapshot["charts"]["quant_metric_series"][0]["entry_timing_score"] == 0.31
    assert snapshot["charts"]["quant_metric_series"][0]["net_edge_pct"] == 0.13
    assert snapshot["charts"]["quant_metric_series"][0]["estimated_cost_pct"] == 0.07
    assert snapshot["charts"]["quant_metric_series"][0]["edge_source"] == "atr_15m_okx"
    assert snapshot["charts"]["consensus_quality_series"][0]["quality"] == "degraded"
    assert snapshot["charts"]["consensus_quality_series"][0]["quality_value"] == 2
    assert snapshot["charts"]["consensus_quality_series"][0]["source_count"] == 2
    assert snapshot["charts"]["consensus_quality_series"][0]["market_data_mode"] == "restricted_two_source"
    assert snapshot["bot"]["candidate_package"]["gate_allowed"] is True
    assert snapshot["bot"]["candidate_package"]["command_targets"] == ["entry_order", "maintain_protective_stop"]
    assert snapshot["bot"]["position_state"] == "FLAT"
    assert snapshot["bot"]["position_direction"] == "neutral"
    assert snapshot["bot"]["position_size_pct"] == 0.0
    assert snapshot["bot"]["worker_events"][0]["payload"]["status"] == "skipped"


def test_load_dashboard_snapshot_reads_latest_quant_cycle_without_handoff(tmp_path: Path) -> None:
    bot_root = tmp_path / "eth_trading_bot"
    quant_root = tmp_path / "quant_system_rebuild"
    generated_at = datetime.now(timezone.utc).isoformat()
    _write_json(quant_root / "runtime" / "scheduler" / "heartbeat.json", {"generated_at": generated_at, "status": "ok"})
    _write_json(
        quant_root / "runtime" / "cycles" / "eth-15m-new" / "decision.json",
        {
            "generated_at": generated_at,
            "decision": {
                "action": "small_probe",
                "direction": "long",
                "confidence": 0.42,
                "reasoning_summary": "latest quant cycle",
                "risk_report": {"risk_filter_status": "degraded", "degrade_flags": ["research_degraded"]},
                "sizing_decision": {"sizing_tier": "none"},
                "regime_state": {"regime_type": "trend", "direction": "long"},
            },
            "metadata": {
                "market_data_mode": "restricted_two_source",
                "consensus_quality": "degraded",
                "consensus_source_count": 2,
                "consensus_sources": ["OKX", "Bitget"],
                "binance_source_health": "unavailable",
                "binance_source_failure_reason": "HTTP 451",
            },
        },
    )
    _write_json(
        quant_root / "runtime" / "cycles" / "eth-15m-new" / "scheduler_status.json",
        {"generated_at": generated_at, "status": "ok", "run_id": "eth-15m-new"},
    )

    snapshot = load_dashboard_snapshot(DashboardPaths(bot_root=bot_root, quant_root=quant_root))

    assert snapshot["runtime"]["quant_scheduler"]["label"] == "RUNNING"
    assert snapshot["quant"]["action"] == "small_probe"
    assert snapshot["quant"]["direction"] == "long"
    assert snapshot["quant"]["risk_filter_status"] == "degraded"
    assert snapshot["quant"]["confidence"] == 0.42
    assert snapshot["quant"]["sizing_tier"] == "none"
    assert snapshot["quant"]["reasoning_summary"] == "latest quant cycle"
    assert snapshot["quant"]["degrade_flags"] == ["research_degraded"]
    assert snapshot["quant"]["regime_bucket"] == "trend_long"
    assert snapshot["quant"]["market_data_mode"] == "restricted_two_source"
    assert snapshot["quant"]["consensus_quality"] == "degraded"
    assert snapshot["quant"]["consensus_source_count"] == 2
    assert snapshot["quant"]["consensus_sources"] == ["OKX", "Bitget"]
    assert snapshot["quant"]["binance_source_health"] == "unavailable"
    assert snapshot["quant"]["binance_source_failure_reason"] == "HTTP 451"
    assert snapshot["decision_review"]["review_status"] == "unavailable"
    assert snapshot["decision_review"]["data_source_quality"]["handoff_available"] is False


def test_dashboard_trigger_watch_tracks_high_confidence_wait_shadow_outcomes(tmp_path: Path) -> None:
    bot_root = tmp_path / "eth_trading_bot"
    quant_root = tmp_path / "quant_system_rebuild"
    generated_at = datetime.now(timezone.utc).isoformat()
    watch_base = datetime.now(timezone.utc) - timedelta(minutes=36)
    watch_cycles = []
    for offset, action, confidence, setup_strength, entry_score, price, transition in [
        (0, "wait", 0.62, 0.82, 0.50, 100.0, "setup_ready_waiting_trigger"),
        (5, "wait", 0.58, 0.70, 0.42, 101.0, "no_entry_alignment"),
        (10, "observe_only", 0.55, 0.30, 0.10, 100.5, "no_entry_alignment"),
        (15, "wait", 0.59, 0.65, 0.20, 102.0, "no_entry_alignment"),
        (20, "wait", 0.61, 0.84, 0.49, 103.0, "setup_ready_waiting_trigger"),
        (25, "observe_only", 0.42, 0.10, 0.05, 104.0, "no_entry_alignment"),
        (30, "observe_only", 0.41, 0.10, 0.05, 104.5, "no_entry_alignment"),
    ]:
        run_dt = watch_base + timedelta(minutes=offset)
        run_id = f"eth-15m-{run_dt.strftime('%Y%m%dT%H%M%SZ')}-watch"
        watch_cycles.append((run_id, run_dt.isoformat(), action, "long", confidence, False, setup_strength, entry_score, price, transition))
    for run_id, run_at, action, direction, confidence, trigger_ready, setup_strength, entry_score, price, transition in watch_cycles:
        _write_json(
            quant_root / "runtime" / "cycles" / run_id / "decision.json",
            {
                "generated_at": run_at,
                "decision": {
                    "action": action,
                    "direction": direction,
                    "confidence": confidence,
                    "thesis_score": 0.58,
                    "reasoning_summary": f"trigger_15m_ready=no | transition={transition}",
                    "risk_report": {
                        "risk_filter_status": "degraded",
                        "degrade_flags": ["research_degraded"],
                        "reason_codes": [transition],
                        "data_health_score": 0.65,
                    },
                    "sizing_decision": {"sizing_tier": "none"},
                    "setup_state": {"setup_direction": direction, "setup_strength": setup_strength},
                    "trigger_state": {"trigger_ready": trigger_ready, "entry_timing_score": entry_score},
                    "regime_state": {"regime_type": "trend", "direction": direction},
                },
                "metadata": {"consensus_mark_price": price},
            },
        )
        _write_json(
            quant_root / "runtime" / "cycles" / run_id / "scheduler_status.json",
            {"generated_at": run_at, "status": "ok", "run_id": run_id},
        )
    _write_json(
        quant_root / "runtime" / "cycles" / "eth-15m-latest" / "decision.json",
        {
            "generated_at": generated_at,
            "decision": {"action": "observe_only", "direction": "neutral", "confidence": 0.1},
            "metadata": {"consensus_mark_price": 105.0},
        },
    )
    _write_json(
        quant_root / "runtime" / "cycles" / "eth-15m-latest" / "scheduler_status.json",
        {"generated_at": generated_at, "status": "ok", "run_id": "eth-15m-latest"},
    )

    snapshot = load_dashboard_snapshot(DashboardPaths(bot_root=bot_root, quant_root=quant_root))

    trigger_watch = snapshot["quant"]["trigger_watch"]
    assert trigger_watch["status"] == "idle"
    assert trigger_watch["label"] == "暂无等待触发"
    assert trigger_watch["threshold_confidence"] == 0.6
    assert trigger_watch["stats"]["sample_count"] == 2
    assert trigger_watch["stats"]["resolved_1_count"] == 2
    assert trigger_watch["stats"]["resolved_3_count"] == 2
    assert trigger_watch["stats"]["resolved_6_count"] == 1
    assert trigger_watch["stats"]["avg_return_1"] == 0.00985437
    assert trigger_watch["stats"]["positive_rate_1"] == 1.0
    assert trigger_watch["recent"][-1]["sample_id"] == watch_cycles[4][0]
    assert trigger_watch["recent"][-1]["source"] == "quant_cycle"
    assert trigger_watch["recent"][-1]["price"] == 103.0
    assert trigger_watch["current"] == {}


def test_dashboard_trigger_watch_marks_current_active_from_bot_latest_cycle(tmp_path: Path) -> None:
    bot_root = tmp_path / "eth_trading_bot"
    quant_root = tmp_path / "quant_system_rebuild"
    generated_at = datetime.now(timezone.utc).isoformat()
    _write_json(
        bot_root / "runtime" / "bot_runtime_scheduler" / "latest_cycle.json",
        {
            "sample_id": "bot-latest",
            "finished_at": generated_at,
            "effective_action": "wait",
            "direction": "long",
            "confidence": 0.603,
            "entry_timing_score": 0.50,
            "trigger_ready": False,
            "risk_filter_status": "degraded",
            "execution_block_reason": "not_entry_action",
            "reasoning_summary": "setup_15m=long:0.83 | trigger_15m_ready=no | transition=setup_ready_waiting_trigger",
            "runtime_snapshot": {"position": {"mark_price": 2316.18}},
        },
    )

    snapshot = load_dashboard_snapshot(DashboardPaths(bot_root=bot_root, quant_root=quant_root))

    trigger_watch = snapshot["quant"]["trigger_watch"]
    assert trigger_watch["status"] == "active"
    assert trigger_watch["label"] == "等待触发"
    assert trigger_watch["stats"]["sample_count"] == 1
    assert trigger_watch["current"]["sample_id"] == "bot-latest"
    assert trigger_watch["current"]["setup_strength"] == 0.83
    assert trigger_watch["current"]["price"] == 2316.18


def test_dashboard_trigger_watch_merges_bot_samples_for_rescaled_confidence(tmp_path: Path) -> None:
    bot_root = tmp_path / "eth_trading_bot"
    quant_root = tmp_path / "quant_system_rebuild"
    base = datetime.now(timezone.utc) - timedelta(minutes=12)
    price_rows = [
        (base - timedelta(minutes=1), 100.0),
        (base + timedelta(minutes=5), 101.0),
        (base + timedelta(minutes=10), 102.0),
        (base + timedelta(minutes=15), 103.0),
    ]
    for run_at, price in price_rows:
        run_id = f"eth-15m-{run_at.strftime('%Y%m%dT%H%M%SZ')}-price"
        _write_json(
            quant_root / "runtime" / "cycles" / run_id / "decision.json",
            {
                "generated_at": run_at.isoformat(),
                "decision": {"action": "observe_only", "direction": "neutral", "confidence": 0.1},
                "metadata": {"consensus_mark_price": price},
            },
        )
        _write_json(
            quant_root / "runtime" / "cycles" / run_id / "scheduler_status.json",
            {"generated_at": run_at.isoformat(), "status": "ok", "run_id": run_id},
        )
    _append_jsonl(
        bot_root / "runtime" / "bot_runtime_scheduler" / "samples.jsonl",
        [
            {
                "sample_id": 1,
                "finished_at": base.isoformat(),
                "effective_action": "wait",
                "direction": "long",
                "confidence": 0.603,
                "entry_timing_score": 0.50,
                "trigger_ready": False,
                "reasoning_summary": "setup_15m=long:0.83 | trigger_15m_ready=no | transition=setup_ready_waiting_trigger",
                "runtime_snapshot": {"position": {"mark_price": None}},
            },
            {
                "sample_id": 2,
                "finished_at": (base + timedelta(minutes=5)).isoformat(),
                "effective_action": "observe_only",
                "direction": "long",
                "confidence": 0.10,
                "runtime_snapshot": {"position": {"mark_price": None}},
            },
        ],
    )

    snapshot = load_dashboard_snapshot(DashboardPaths(bot_root=bot_root, quant_root=quant_root))

    trigger_watch = snapshot["quant"]["trigger_watch"]
    assert trigger_watch["status"] == "idle"
    assert trigger_watch["stats"]["sample_count"] == 1
    assert trigger_watch["stats"]["avg_return_1"] == 0.01
    assert trigger_watch["stats"]["avg_return_3"] == 0.03
    assert trigger_watch["recent"][0]["source"] == "bot_sample"
    assert trigger_watch["recent"][0]["setup_strength"] == 0.83
    assert trigger_watch["recent"][0]["price"] == 100.0


def test_dashboard_performance_reads_okx_runtime_snapshot_position_pnl(tmp_path: Path) -> None:
    bot_root = tmp_path / "eth_trading_bot"
    quant_root = tmp_path / "quant_system_rebuild"
    generated_at = datetime.now(timezone.utc).isoformat()
    _write_json(
        bot_root / "runtime" / "bot_runtime_scheduler" / "latest_cycle.json",
        {
            "finished_at": generated_at,
            "exchange_venue": "okx_usdt_swap",
            "exchange_symbol": "ETH-USDT-SWAP",
            "runtime_snapshot": {
                "fetched_at": generated_at,
                "snapshot_valid": True,
                "account_equity": 102.5,
                "account_equity_source": "totalEq",
                "position": {
                    "position_state": "ENTERED",
                    "mark_price": 3120.5,
                    "unrealized_pnl_usd": 4.75,
                    "unrealized_pnl_pct_on_margin": 0.052,
                    "price_vs_entry_pct": 0.0066,
                },
            },
        },
    )

    snapshot = load_dashboard_snapshot(DashboardPaths(bot_root=bot_root, quant_root=quant_root))

    assert snapshot["performance"]["account_equity"] == 102.5
    assert snapshot["performance"]["account_equity_source"] == "totalEq"
    assert snapshot["performance"]["total_profit_usd"] == 4.75
    assert snapshot["performance"]["total_profit_pct"] == 0.052
    assert snapshot["performance"]["price_vs_entry_pct"] == 0.0066
    assert snapshot["performance"]["position_state"] == "ENTERED"
    assert snapshot["performance"]["mark_price"] == 3120.5
    assert snapshot["performance"]["source"] == "bot_latest_cycle"
    assert snapshot["performance"]["ignored_source"] == ""


def test_dashboard_performance_ignores_binance_runtime_snapshot(tmp_path: Path) -> None:
    bot_root = tmp_path / "eth_trading_bot"
    quant_root = tmp_path / "quant_system_rebuild"
    generated_at = datetime.now(timezone.utc).isoformat()
    _write_json(
        bot_root / "runtime" / "bot_runtime_scheduler" / "latest_cycle.json",
        {
            "finished_at": generated_at,
            "exchange_venue": "binance_usdt_perp",
            "exchange_symbol": "ETHUSDT",
            "runtime_snapshot": {
                "fetched_at": generated_at,
                "snapshot_valid": True,
                "account_equity": 13.69,
                "account_equity_source": "totalWalletBalance",
                "position": {"position_state": "FLAT", "mark_price": 2300.0, "unrealized_pnl_usd": 2.5},
            },
        },
    )

    snapshot = load_dashboard_snapshot(DashboardPaths(bot_root=bot_root, quant_root=quant_root))

    assert snapshot["performance"]["account_equity"] is None
    assert snapshot["performance"]["total_profit_usd"] is None
    assert snapshot["performance"]["source"] == "unavailable"
    assert snapshot["performance"]["ignored_source"] == "binance_usdt_perp"


def test_load_dashboard_snapshot_does_not_treat_non_entry_cycle_as_scheduler_failure(tmp_path: Path) -> None:
    bot_root = tmp_path / "eth_trading_bot"
    quant_root = tmp_path / "quant_system_rebuild"
    old_at = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    blocked_at = datetime.now(timezone.utc).isoformat()
    _write_json(quant_root / "runtime" / "scheduler" / "heartbeat.json", {"generated_at": old_at, "status": "ok"})
    _write_json(
        quant_root / "runtime" / "cycles" / "old-ok" / "decision.json",
        {"generated_at": old_at, "decision": {"action": "wait", "direction": "neutral"}},
    )
    _write_json(
        quant_root / "runtime" / "cycles" / "old-ok" / "scheduler_status.json",
        {"generated_at": old_at, "status": "ok", "run_id": "old-ok"},
    )
    _write_json(
        quant_root / "runtime" / "cycles" / "new-blocked" / "scheduler_status.json",
        {
            "generated_at": blocked_at,
            "status": "blocked",
            "run_id": "new-blocked",
            "issues": ["HTTP Error 451"],
            "metadata": {"diagnostic": "request_diagnostic=retry_exhausted"},
        },
    )

    snapshot = load_dashboard_snapshot(DashboardPaths(bot_root=bot_root, quant_root=quant_root))

    assert snapshot["runtime"]["quant_scheduler"]["label"] == "RUNNING"
    assert snapshot["runtime"]["quant_scheduler"]["age_sec"] is not None
    assert snapshot["quant"]["action"] == "wait"


def test_candidate_promotion_tolerates_missing_return_metrics(tmp_path: Path) -> None:
    bot_root = tmp_path / "eth_trading_bot"
    quant_root = tmp_path / "quant_system_rebuild"
    generated_at = datetime.now(timezone.utc).isoformat()
    _write_json(
        quant_root / "runtime" / "fresh_research" / "all_results.json",
        {
            "timestamp": generated_at,
            "results": [
                {
                    "candidate_id": "sparse_candidate",
                    "candidate_review_status": "review_candidate",
                    "live_candidate_status": "not_live_ready",
                    "total_triggers": 3,
                    "live_block_reasons": ["walk_forward_evidence_missing"],
                },
                {
                    "candidate_id": "qualified_candidate",
                    "candidate_review_status": "qualified_candidate",
                    "live_candidate_status": "not_live_ready",
                    "source_scan_generated_at": generated_at,
                    "avg_return_pct": 0.04,
                    "total_triggers": 20,
                    "live_block_reasons": ["scan_row_count_below_live_minimum"],
                },
            ],
        },
    )

    snapshot = load_dashboard_snapshot(DashboardPaths(bot_root=bot_root, quant_root=quant_root))

    promotion = snapshot["factor"]["candidate_promotion"]
    assert promotion["status"] == "qualified_candidate"
    assert promotion["qualified_candidate_count"] == 1
    assert promotion["latest_promotion_at"] == generated_at
    assert promotion["top_candidates"][0]["candidate_id"] == "qualified_candidate"
    assert promotion["top_candidates"][1]["candidate_id"] == "sparse_candidate"


def test_load_dashboard_snapshot_prefers_complete_scheduler_cycle_over_newer_snapshot_cycle(tmp_path: Path) -> None:
    bot_root = tmp_path / "eth_trading_bot"
    quant_root = tmp_path / "quant_system_rebuild"
    complete_at = datetime.now(timezone.utc).isoformat()
    snapshot_at = (datetime.now(timezone.utc) + timedelta(seconds=60)).isoformat()
    _write_json(quant_root / "runtime" / "scheduler" / "heartbeat.json", {"generated_at": complete_at, "status": "ok"})
    _write_json(
        quant_root / "runtime" / "cycles" / "complete" / "decision.json",
        {"generated_at": complete_at, "decision": {"action": "wait", "direction": "long"}},
    )
    _write_json(
        quant_root / "runtime" / "cycles" / "complete" / "scheduler_status.json",
        {"generated_at": complete_at, "status": "ok", "run_id": "complete"},
    )
    _write_json(
        quant_root / "runtime" / "cycles" / "snap" / "decision.json",
        {"generated_at": snapshot_at, "decision": {"action": "entry_long", "direction": "long"}},
    )
    _write_json(
        quant_root / "runtime" / "cycles" / "snap" / "snapshot_registry.json",
        {"generated_at": snapshot_at},
    )

    snapshot = load_dashboard_snapshot(DashboardPaths(bot_root=bot_root, quant_root=quant_root))

    assert snapshot["runtime"]["quant_scheduler"]["label"] == "RUNNING"
    assert snapshot["quant"]["action"] == "wait"
    incomplete = snapshot["quant"]["latest_incomplete_cycle"]
    assert incomplete["present"] is True
    assert incomplete["cycle_dir"].endswith("snap")
    assert incomplete["generated_at"] == snapshot_at
    assert incomplete["status"] == "incomplete_missing_scheduler_status"
    assert incomplete["has_snapshot_registry"] is True
    assert incomplete["has_decision"] is True
    assert incomplete["has_scheduler_status"] is False


def test_load_dashboard_snapshot_treats_incomplete_scheduler_status_as_incomplete_cycle(tmp_path: Path) -> None:
    bot_root = tmp_path / "eth_trading_bot"
    quant_root = tmp_path / "quant_system_rebuild"
    complete_at = datetime.now(timezone.utc).isoformat()
    snapshot_at = (datetime.now(timezone.utc) + timedelta(seconds=60)).isoformat()
    _write_json(quant_root / "runtime" / "scheduler" / "heartbeat.json", {"generated_at": complete_at, "status": "ok"})
    _write_json(
        quant_root / "runtime" / "cycles" / "complete" / "decision.json",
        {"generated_at": complete_at, "decision": {"action": "wait", "direction": "long"}},
    )
    _write_json(
        quant_root / "runtime" / "cycles" / "complete" / "scheduler_status.json",
        {"generated_at": complete_at, "status": "ok", "run_id": "complete"},
    )
    _write_json(
        quant_root / "runtime" / "cycles" / "snap" / "snapshot_registry.json",
        {"generated_at": snapshot_at},
    )
    _write_json(
        quant_root / "runtime" / "cycles" / "snap" / "scheduler_status.json",
        {"generated_at": snapshot_at, "status": "incomplete_snapshot_only", "run_id": "snap"},
    )

    snapshot = load_dashboard_snapshot(DashboardPaths(bot_root=bot_root, quant_root=quant_root))

    assert snapshot["runtime"]["quant_scheduler"]["label"] == "RUNNING"
    assert snapshot["quant"]["action"] == "wait"
    incomplete = snapshot["quant"]["latest_incomplete_cycle"]
    assert incomplete["present"] is True
    assert incomplete["status"] == "incomplete_snapshot_only"
    assert incomplete["has_scheduler_status"] is True
    assert incomplete["missing_parts"] == ["decision"]


def test_load_dashboard_snapshot_marks_kill_switch(tmp_path: Path) -> None:
    bot_root = tmp_path / "eth_trading_bot"
    quant_root = tmp_path / "quant_system_rebuild"
    kill_switch = bot_root / "runtime" / "controls" / "disable_real_execution.flag"
    kill_switch.parent.mkdir(parents=True)
    kill_switch.write_text("1", encoding="utf-8")

    snapshot = load_dashboard_snapshot(DashboardPaths(bot_root=bot_root, quant_root=quant_root))

    assert snapshot["runtime"]["kill_switch"]["label"] == "ON"
    assert snapshot["runtime"]["kill_switch"]["enabled"] is True


def test_dashboard_performance_ignores_binance_protective_stop_preview(tmp_path: Path) -> None:
    bot_root = tmp_path / "eth_trading_bot"
    quant_root = tmp_path / "quant_system_rebuild"
    generated_at = datetime.now(timezone.utc).isoformat()
    _write_json(bot_root / "runtime" / "bot_runtime_scheduler" / "latest_cycle.json", {"finished_at": generated_at})
    _write_json(
        bot_root / "runtime" / "reports" / "protective_stop_replace" / "latest_preview.json",
        {
            "created_at": generated_at,
            "recorded_protective_stop": {"venue": "binance_usdt_perp", "symbol": "ETHUSDT"},
            "snapshot": {
                "fetched_at": generated_at,
                "snapshot_valid": True,
                "account_equity": 13.69,
                "account_equity_source": "totalWalletBalance",
                "position": {"position_state": "FLAT", "mark_price": 2300.0},
            },
            "pnl_state": {"unrealized_pnl_usd": 2.5},
        },
    )

    snapshot = load_dashboard_snapshot(DashboardPaths(bot_root=bot_root, quant_root=quant_root))

    assert snapshot["performance"]["account_equity"] is None
    assert snapshot["performance"]["total_profit_usd"] is None
    assert snapshot["performance"]["source"] == "unavailable"
    assert snapshot["performance"]["ignored_source"] == "binance_usdt_perp"


def test_dashboard_static_dom_contract_is_complete() -> None:
    static_root = REPO_ROOT / "dashboard" / "static"
    html = (static_root / "index.html").read_text(encoding="utf-8")
    app_js = (static_root / "app.js").read_text(encoding="utf-8")
    styles_css = (static_root / "styles.css").read_text(encoding="utf-8")

    html_ids = set(re.findall(r'id="([^"]+)"', html))
    referenced_ids = set(re.findall(r'\$\("([^"]+)"\)', app_js))

    assert '<script src="/app.js"></script>' in html
    assert '<link rel="stylesheet" href="/styles.css" />' in html
    assert "ETH 运行观察面板" in html
    assert "只读观察自动化实盘链路" in html
    assert "样本采集与因子治理" in html
    assert "候选晋升状态" in html
    assert "量化市场判断" in html
    assert "机器人下单链路" in html
    assert "决策审查报告" in html
    assert "主要原因" in html
    assert "运行状态时间线" in html
    assert '<script src="/vendor/echarts.min.js"></script>' in html
    assert (static_root / "vendor" / "echarts.min.js").exists()
    assert "审查报告仅供解释和复盘，不参与自动下单" in html
    assert "当前交易状态" in html
    assert "setInterval(refreshWithBanner, 5000)" in app_js
    assert "refreshPaused" in app_js
    assert "severityForReason" in app_js
    assert "buildNoTradeSummary" in app_js
    assert "renderTriggerWatch" in app_js
    assert "renderCandidatePromotion" in app_js
    assert "renderProbeDiagnostics" in app_js
    assert "renderDecisionBrief" in app_js
    assert "function executionModeText(runtime)" in app_js
    assert 'blocked: "未放行"' in app_js
    assert 'if (raw === "blocked") return "yellow";' in app_js
    assert 'function cycleStatusLabel(status)' in app_js
    assert 'if (raw === "blocked") return "未入场";' in app_js
    assert "cycleStatusLabel(item.status)" in app_js
    assert '["未入场", "快照未完成", "降级", "正常"]' in app_js
    assert "试探仓未放行" in app_js
    assert "执行未放行原因" in app_js
    assert "硬拦截" in app_js
    assert '.replace(/\\bblocked\\b/gi, "未放行")' in app_js
    assert '["预检错误", cycle.preflight_error || "ok"]' in app_js
    assert {"runtimeGrid", "factorDetails", "quantDetails", "auditEvents"} <= html_ids
    assert {"candidatePromotionBadge", "candidatePromotionSummary", "candidatePromotionRows"} <= html_ids
    assert {"triggerWatchBadge", "triggerWatchSummary", "triggerWatchStats", "triggerWatchRows"} <= html_ids
    assert {"cycleStatusChart", "quantMetricsChart", "reasonCodesChart", "consensusChart"} <= html_ids
    assert {"researchBadge", "researchDetails", "researchReasons", "quantReasons"} <= html_ids
    assert {"probeDiagnosticBadge", "probeDiagnosticSummary", "probeDiagnosticFacts", "probeDiagnosticReasons"} <= html_ids
    assert {"marketDataBadge", "marketDataDetails", "edgeCostBadge", "edgeCostDetails"} <= html_ids
    assert {"degradeFlags", "riskCodes"} <= html_ids
    assert {"reviewStatusBadge", "reviewSourceQuality", "reviewRiskFindings", "summaryAction", "summaryBlockReason"} <= html_ids
    assert {"decisionBriefBadge", "decisionBriefTitle", "decisionBriefText", "decisionBriefMode", "decisionBriefGate", "decisionBriefResearch"} <= html_ids
    assert {"pauseBtn", "modeNotice"} <= html_ids
    assert "历史样本原因统计" in html
    assert 'class="ops-grid"' in html
    assert 'class="execution-grid"' in html
    assert referenced_ids <= html_ids
    assert "�" not in html
    assert "�" not in app_js
    assert ".innerHTML" not in app_js
    assert "replaceChildren" not in app_js
    assert "function clearElement(el)" in app_js
    assert "judgement_not_ok: \"量化判断未返回可执行结果\"" in app_js
    assert '"diagnostic:data_source": "数据源异常"' in app_js
    assert '"risk_filter:unknown": "风控状态未知"' in app_js
    assert 'waiting: "等待"' in app_js
    assert '.replace(/\\bunavailable\\b/gi, "不可用")' in app_js
    assert '.replace(/\\bwaiting\\b/gi, "等待")' in app_js
    assert 'appendText(chip, "small", displayCode(row.code))' not in app_js
    assert "function reasonChipTitle(row, context = \"\")" in app_js
    assert "chip.title = reasonChipTitle(row, options.context || \"\")" in app_js
    assert "原始代码：${code}" in app_js
    assert "renderQuality(review.data_source_quality || {})" in app_js
    assert "function reviewStatusText(status)" in app_js
    assert "$(\"summaryReview\").textContent = reviewStatusText(review.review_status)" in app_js
    assert 'labelKey === "review_status" ? reviewStatusText(label)' in app_js
    assert 'setBadge($("reviewStatusBadge"), reviewStatusText(reviewStatus), levelForStatus(reviewStatus))' in app_js
    assert 'setText("reviewStatus", reviewStatusText(reviewStatus))' in app_js
    assert '.replace(/\\b(research|execution|risk|real_order|automation)_gate\\b/gi, "$1 闸门")' in app_js
    assert '.replace(/\\bgate\\b/gi, "闸门")' not in app_js
    assert 'monitor: "观察 / 等待确认"' in app_js
    assert 'block_long: "阻断多头"' in app_js
    assert "renderOptionalWorkers(data.optional_workers || {})" in app_js
    assert "renderCharts(data.charts || {})" in app_js
    assert "echarts.init" in app_js
    assert "setPill($(" in app_js
    assert "latest_incomplete_cycle" in app_js
    assert "trigger_watch" in app_js
    assert "strong_momentum_probe: \"强动量试探仓\"" in app_js
    assert "capped_by_strong_momentum_probe: \"强动量试探仓上限压制\"" in app_js
    assert 'suspect_false_positive: "疑似假阳性"' in app_js
    assert 'authenticity_missing: "报告未含候选诊断"' in app_js
    assert 'scan_row_count_below_live_minimum: "扫描样本数未达 live 门槛"' in app_js
    assert "最近晋升 暂无 qualified/live" in app_js
    assert "这是研究诊断，不参与自动下单" in app_js
    assert ".probe-diagnostic" in styles_css
    assert ".promotion-table" in styles_css
    assert ".diagnostic-list" in styles_css
    assert ".decision-brief" in styles_css
    assert "submitted_all_accepted" in app_js
    assert "partial_failed" in app_js
    assert "color-scheme: dark" in styles_css
    assert '"Microsoft YaHei UI"' in styles_css
    assert "width: min(100%, 1680px)" in styles_css
    assert ".dashboard-grid" in styles_css
    assert ".ops-grid" in styles_css
    assert ".execution-grid" in styles_css
    assert "minmax(680px, 1.2fr)" in styles_css
    assert "grid-template-columns: minmax(0, 1fr) minmax(0, 1fr)" in styles_css
    assert "overflow-wrap: anywhere" in styles_css
    assert "word-break: break-word" in styles_css
    assert "max-height: 260px" in styles_css
    assert "overflow-x: hidden" in styles_css
    assert ".reason-chip.hard" in styles_css
    assert ".audit-item.degraded" in styles_css
    assert ".trigger-watch-row" in styles_css
    assert "min-height: 44px" in styles_css
    assert "@media (max-width: 480px)" in styles_css
    assert ".toolbar {\n    grid-template-columns: 1fr;" in styles_css
    assert "@media (max-width: 1280px)" in styles_css
    assert "@media (max-width: 980px)" in styles_css
    assert "@media (max-width: 720px)" in styles_css


def test_dashboard_http_serves_static_and_overview_api(tmp_path: Path, monkeypatch) -> None:
    bot_root = tmp_path / "eth_trading_bot"
    quant_root = tmp_path / "quant_system_rebuild"
    generated_at = datetime.now(timezone.utc).isoformat()
    _write_json(bot_root / "runtime" / "bot_runtime_scheduler" / "heartbeat.json", {"generated_at": generated_at, "status": "ok"})
    _write_json(quant_root / "runtime" / "scheduler" / "heartbeat.json", {"generated_at": generated_at, "status": "ok"})
    monkeypatch.setenv("ETH_BOT_ROOT", str(bot_root))
    monkeypatch.setenv("QUANT_ROOT", str(quant_root))

    server = ThreadingHTTPServer(("127.0.0.1", 0), DashboardHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    conn = HTTPConnection(host, port, timeout=5)
    try:
        conn.request("GET", "/")
        response = conn.getresponse()
        html = response.read().decode("utf-8")
        assert response.status == 200
        assert "ETH 运行观察面板" in html

        conn.request("GET", "/app.js")
        response = conn.getresponse()
        body = response.read().decode("utf-8")
        assert response.status == 200
        assert "function render(data)" in body

        conn.request("GET", "/api/health")
        response = conn.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
        assert response.status == 200
        assert payload == {"status": "ok"}

        conn.request("GET", "/api/overview")
        response = conn.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
        assert response.status == 200
        assert payload["paths"]["bot_root"] == str(bot_root)
        assert payload["runtime"]["bot_scheduler"]["label"] == "RUNNING"
        assert payload["runtime"]["quant_scheduler"]["label"] == "RUNNING"
        assert "decision_review" not in payload["runtime"]
        assert payload["optional_workers"]["decision_review"]["label"] == "OPTIONAL_DISABLED"
        assert payload["decision_review"]["review_status"] == "unavailable"
        assert payload["decision_review"]["summary"]
    finally:
        conn.close()
        server.shutdown()
        server.server_close()


def test_dashboard_reports_invalid_json_source_quality(tmp_path: Path) -> None:
    bot_root = tmp_path / "bot"
    quant_root = tmp_path / "quant"
    heartbeat_path = bot_root / "runtime" / "bot_runtime_scheduler" / "heartbeat.json"
    heartbeat_path.parent.mkdir(parents=True)
    heartbeat_path.write_text("{not-json", encoding="utf-8")

    snapshot = load_dashboard_snapshot(DashboardPaths(bot_root=bot_root, quant_root=quant_root))

    heartbeat_quality = snapshot["data_quality"]["json_sources"]["bot_heartbeat"]
    assert heartbeat_quality["status"] == "invalid_json"
    assert heartbeat_quality["path"] == str(heartbeat_path)
    assert any(item["name"] == "bot_heartbeat" and item["status"] == "invalid_json" for item in snapshot["data_quality"]["json_source_issues"])


def test_dashboard_overview_cache_reuses_snapshot_within_ttl(monkeypatch, tmp_path: Path) -> None:
    bot_root = tmp_path / "bot"
    quant_root = tmp_path / "quant"
    calls: list[DashboardPaths] = []
    clock = {"now": 10.0}
    cache = OverviewSnapshotCache(ttl_sec=1.0)

    def fake_load_dashboard_snapshot(paths: DashboardPaths) -> dict:
        calls.append(paths)
        return {"call_count": len(calls), "paths": {"bot_root": str(paths.bot_root), "quant_root": str(paths.quant_root)}}

    monkeypatch.setattr(dashboard_app, "load_dashboard_snapshot", fake_load_dashboard_snapshot)
    monkeypatch.setattr(dashboard_app.time, "monotonic", lambda: clock["now"])

    first = cache.get(DashboardPaths(bot_root=bot_root, quant_root=quant_root))
    second = cache.get(DashboardPaths(bot_root=bot_root, quant_root=quant_root))

    assert first == second
    assert len(calls) == 1


def test_dashboard_overview_cache_shares_initial_load_across_threads(monkeypatch, tmp_path: Path) -> None:
    bot_root = tmp_path / "bot"
    quant_root = tmp_path / "quant"
    calls: list[DashboardPaths] = []
    started = threading.Event()
    release = threading.Event()
    cache = OverviewSnapshotCache(ttl_sec=1.0)

    def fake_load_dashboard_snapshot(paths: DashboardPaths) -> dict:
        calls.append(paths)
        started.set()
        assert release.wait(timeout=5)
        return {"call_count": len(calls), "paths": {"bot_root": str(paths.bot_root), "quant_root": str(paths.quant_root)}}

    monkeypatch.setattr(dashboard_app, "load_dashboard_snapshot", fake_load_dashboard_snapshot)

    results: list[dict] = []

    def get_snapshot() -> None:
        results.append(cache.get(DashboardPaths(bot_root=bot_root, quant_root=quant_root)))

    first_thread = threading.Thread(target=get_snapshot)
    second_thread = threading.Thread(target=get_snapshot)
    first_thread.start()
    assert started.wait(timeout=5)
    second_thread.start()
    release.set()
    first_thread.join(timeout=5)
    second_thread.join(timeout=5)

    assert len(results) == 2
    assert results[0] == results[1]
    assert len(calls) == 1


def test_dashboard_overview_cache_returns_stale_snapshot_while_refreshing(monkeypatch, tmp_path: Path) -> None:
    bot_root = tmp_path / "bot"
    quant_root = tmp_path / "quant"
    calls: list[DashboardPaths] = []
    clock = {"now": 10.0}
    cache = OverviewSnapshotCache(ttl_sec=1.0)

    def fake_load_dashboard_snapshot(paths: DashboardPaths) -> dict:
        calls.append(paths)
        return {"call_count": len(calls), "paths": {"bot_root": str(paths.bot_root), "quant_root": str(paths.quant_root)}}

    class InlineThread:
        def __init__(self, *, target, args, daemon) -> None:
            self._target = target
            self._args = args
            self.daemon = daemon

        def start(self) -> None:
            self._target(*self._args)

    monkeypatch.setattr(dashboard_app, "load_dashboard_snapshot", fake_load_dashboard_snapshot)
    monkeypatch.setattr(dashboard_app.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(dashboard_app.threading, "Thread", InlineThread)

    first = cache.get(DashboardPaths(bot_root=bot_root, quant_root=quant_root))
    clock["now"] = 11.1
    stale = cache.get(DashboardPaths(bot_root=bot_root, quant_root=quant_root))
    refreshed = cache.get(DashboardPaths(bot_root=bot_root, quant_root=quant_root))

    assert stale == first
    assert refreshed["call_count"] == 2
    assert len(calls) == 2


def test_decision_review_marks_missing_sources_as_watch(tmp_path: Path) -> None:
    bot_root = tmp_path / "bot"
    quant_root = tmp_path / "quant"
    generated_at = datetime.now(timezone.utc).isoformat()
    _write_json(
        quant_root / "runtime" / "cycles" / "cycle-1" / "execution_handoff.json",
        {
            "generated_at": generated_at,
            "handoff_id": "handoff-1",
            "supporting_factor_codes": ["funding_rate:negative"],
            "opposing_factor_codes": [],
            "veto_factor_codes": [],
        },
    )
    _write_json(
        quant_root / "runtime" / "cycles" / "cycle-1" / "decision.json",
        {"generated_at": generated_at, "decision": {"risk_report": {"risk_filter_status": "pass"}}},
    )

    review = build_decision_review(bot_root=bot_root, quant_root=quant_root, now=datetime.now(timezone.utc))

    assert review["review_status"] == "watch"
    assert review["source_run_id"] == "cycle-1"
    assert review["handoff_id"] == "handoff-1"
    assert review["data_source_quality"]["handoff_available"] is True
    assert review["data_source_quality"]["factor_lookup_available"] is False
    assert any(item["code"] == "factor_lookup_missing" for item in review["risk_findings"])


def test_decision_review_default_stale_threshold_covers_five_minute_cycle(tmp_path: Path) -> None:
    bot_root = tmp_path / "bot"
    quant_root = tmp_path / "quant"
    now = datetime.now(timezone.utc)
    generated_at = (now - timedelta(seconds=420)).isoformat()
    _write_json(
        quant_root / "runtime" / "cycles" / "cycle-1" / "execution_handoff.json",
        {
            "generated_at": generated_at,
            "handoff_id": "handoff-1",
            "supporting_factor_codes": [],
            "opposing_factor_codes": [],
            "veto_factor_codes": [],
        },
    )

    review = build_decision_review(bot_root=bot_root, quant_root=quant_root, now=now)

    assert review["source_stale_threshold_sec"] == 600
    assert review["source_handoff_age_sec"] == 420
    assert review["source_stale"] is False


def test_decision_review_prefers_handoff_source_run_id(tmp_path: Path) -> None:
    bot_root = tmp_path / "bot"
    quant_root = tmp_path / "quant"
    generated_at = datetime.now(timezone.utc).isoformat()
    _write_json(
        quant_root / "runtime" / "cycles" / "cycle-1" / "execution_handoff.json",
        {
            "generated_at": generated_at,
            "run_id": "legacy-run",
            "source_run_id": "explicit-source-run",
            "supporting_factor_codes": [],
            "opposing_factor_codes": [],
            "veto_factor_codes": [],
        },
    )

    review = build_decision_review(bot_root=bot_root, quant_root=quant_root, now=datetime.now(timezone.utc))

    assert review["source_run_id"] == "explicit-source-run"


def test_decision_review_rejects_dangerous_governance_suggestion_fields() -> None:
    review = normalize_decision_review(
        {
            "review_status": "clear",
            "governance_review_suggestions": [
                {
                    "factor_name": "crowding_warning",
                    "allow_entry": True,
                    "set_sizing": "tier_3",
                    "reason": "bad",
                }
            ],
        }
    )

    suggestion = review["governance_review_suggestions"][0]
    assert suggestion["suggested_action"] == "rejected_dangerous_fields"
    assert suggestion["actionable"] is False
    assert "allow_entry" in suggestion["reason"]
    assert "set_sizing" in suggestion["reason"]


def test_governance_suggestions_are_sanitized_before_landing(tmp_path: Path) -> None:
    output_path = tmp_path / "runtime" / "reviews" / "governance_suggestions.json"

    suggestions = write_governance_suggestions(
        output_path,
        [
            {"factor_name": "funding_rate", "suggested_action": "manual_governance_review", "reason": "watch"},
            {"factor_name": "crowding_warning", "bypass_veto": True},
        ],
    )

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert suggestions == payload
    assert payload[0]["actionable"] is False
    assert payload[1]["suggested_action"] == "rejected_dangerous_fields"
    assert "bypass_veto" in payload[1]["reason"]


def test_daily_review_summarizes_worker_audit_and_outcomes_without_execution_control(tmp_path: Path) -> None:
    bot_root = tmp_path / "bot"
    quant_root = tmp_path / "quant"
    generated_at = datetime.now(timezone.utc).isoformat()
    _write_json(
        quant_root / "runtime" / "cycles" / "cycle-1" / "execution_handoff.json",
        {"generated_at": generated_at, "handoff_id": "handoff-1"},
    )
    _append_jsonl(
        bot_root / "runtime" / "real_order_worker" / "audit.jsonl",
        [
            {"generated_at": generated_at, "event_type": "real_order_worker", "payload": {"status": "skipped"}},
            {"generated_at": generated_at, "event_type": "real_order_worker", "payload": {"status": "submitted"}},
        ],
    )
    _write_json(
        quant_root / "runtime" / "analysis" / "decision_outcomes_summary.json",
        {"resolved_count": 2, "avg_net_return_pct": 0.003, "stop_hit_rate": 0.5},
    )

    review = build_daily_review(bot_root=bot_root, quant_root=quant_root, now=datetime.now(timezone.utc))

    assert review["schema"] == "daily_runtime_review_v1"
    assert review["version"] == 1
    assert review["review_mode"] == "daily_integrity_review"
    assert review["worker_status_counts"] == {"skipped": 1, "submitted": 1}
    assert review["resolved_outcome_count"] == 2
    assert review["summary"] == "每日复盘只供审计和学习，不参与实时下单。"
