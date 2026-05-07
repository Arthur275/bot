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
from dashboard.status_rules import kill_switch_status, lookup_status, runtime_status

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
            "automation_boundary": "real_order_submission_candidate",
        },
    )
    _write_json(
        bot_root / "runtime" / "state_store.json",
        {
            "execution_state": "idle",
            "automation_state": "observing",
            "observed_position_state": "FLAT",
            "observed_position_direction": "neutral",
            "observed_position_size_pct": 0.0,
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
                        "reason_codes": ["research_not_ready", "wf_quality_insufficient"],
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
                "reasoning_summary": "latest quant cycle",
                "risk_report": {
                    "risk_filter_status": "degraded",
                    "degrade_flags": ["research_degraded"],
                },
                "sizing_decision": {"sizing_tier": "none"},
                "regime_state": {"regime_type": "trend", "direction": "long"},
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
    assert snapshot["factor"]["total_samples"] == 25
    assert snapshot["factor"]["lookup_version"] == "lookup-20260504"
    assert snapshot["factor"]["lookup_rows"] == 9
    assert snapshot["factor"]["governance"]["status"] == "watch"
    assert snapshot["factor"]["governance"]["rows"][0]["factor_lifecycle"] == "watch"
    assert snapshot["factor"]["governance"]["rows"][0]["factor_effect"] == "neutral"
    assert snapshot["factor"]["governance"]["rows"][0]["net_expectancy_pct"] == 0.0
    assert snapshot["factor"]["db_available"] is False
    assert snapshot["factor"]["sample_growth"]["bot_scheduler_samples"] == 1
    assert snapshot["quant"]["action"] == "entry_long"
    assert snapshot["quant"]["supporting_factors"] == ["trend_aligned"]
    assert snapshot["quant"]["regime_bucket"] == "trend_long"
    assert snapshot["quant"]["factor_lookup_version"] == "lookup-20260504"
    assert snapshot["quant"]["execution_warnings"] == ["route_c_missing"]
    assert snapshot["quant"]["automation_boundary"] == "real_order_submission_candidate"
    assert snapshot["quant"]["research"]["status"] == "unavailable"
    assert snapshot["quant"]["research"]["freshness"] == "stale"
    assert snapshot["quant"]["research"]["refresh_every"] == 12
    assert snapshot["quant"]["research"]["refresh_aliases"] is True
    assert snapshot["quant"]["research"]["reason_texts"][0] == {
        "code": "research_not_ready",
        "text": "quant 共享映射：research 未就绪",
    }
    assert snapshot["bot"]["candidate_package"]["gate_allowed"] is True
    assert snapshot["bot"]["candidate_package"]["command_targets"] == ["entry_order", "maintain_protective_stop"]
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
    assert snapshot["decision_review"]["review_status"] == "unavailable"
    assert snapshot["decision_review"]["data_source_quality"]["handoff_available"] is False


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


def test_load_dashboard_snapshot_marks_kill_switch(tmp_path: Path) -> None:
    bot_root = tmp_path / "eth_trading_bot"
    quant_root = tmp_path / "quant_system_rebuild"
    kill_switch = bot_root / "runtime" / "controls" / "disable_real_execution.flag"
    kill_switch.parent.mkdir(parents=True)
    kill_switch.write_text("1", encoding="utf-8")

    snapshot = load_dashboard_snapshot(DashboardPaths(bot_root=bot_root, quant_root=quant_root))

    assert snapshot["runtime"]["kill_switch"]["label"] == "ON"
    assert snapshot["runtime"]["kill_switch"]["enabled"] is True


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
    assert "量化市场判断" in html
    assert "机器人下单链路" in html
    assert "决策审查报告" in html
    assert "审查报告仅供解释和复盘，不参与自动下单" in html
    assert "setInterval(refreshWithBanner, 5000)" in app_js
    assert '["预检错误", cycle.preflight_error || "ok"]' in app_js
    assert {"runtimeGrid", "factorDetails", "quantDetails", "auditEvents"} <= html_ids
    assert {"researchBadge", "researchDetails", "researchReasons"} <= html_ids
    assert {"reviewStatusBadge", "reviewSourceQuality", "reviewRiskFindings", "summaryAction"} <= html_ids
    assert referenced_ids <= html_ids
    assert "�" not in html
    assert "�" not in app_js
    assert ".innerHTML" not in app_js
    assert "replaceChildren" not in app_js
    assert "function clearElement(el)" in app_js
    assert "renderQuality(review.data_source_quality || {})" in app_js
    assert "setPill($(" in app_js
    assert "submitted_all_accepted" in app_js
    assert "partial_failed" in app_js
    assert "color-scheme: dark" in styles_css
    assert '"Microsoft YaHei UI"' in styles_css
    assert "width: min(100%, 1680px)" in styles_css
    assert ".dashboard-grid" in styles_css
    assert "grid-template-columns: minmax(0, 1fr) minmax(0, 1fr)" in styles_css
    assert "overflow-wrap: anywhere" in styles_css
    assert "word-break: break-word" in styles_css
    assert "max-height: 260px" in styles_css
    assert "overflow-x: hidden" in styles_css
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

        conn.request("GET", "/api/overview")
        response = conn.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
        assert response.status == 200
        assert payload["paths"]["bot_root"] == str(bot_root)
        assert payload["runtime"]["bot_scheduler"]["label"] == "RUNNING"
        assert payload["runtime"]["quant_scheduler"]["label"] == "RUNNING"
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
    clock["now"] = 11.1
    third = cache.get(DashboardPaths(bot_root=bot_root, quant_root=quant_root))

    assert first == second
    assert third["call_count"] == 2
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
