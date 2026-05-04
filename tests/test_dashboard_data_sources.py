from __future__ import annotations

import json
import re
import threading
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dashboard.app import DashboardHandler
from dashboard.data_sources import DashboardPaths, load_dashboard_snapshot
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
    assert snapshot["factor"]["db_available"] is False
    assert snapshot["factor"]["sample_growth"]["bot_scheduler_samples"] == 1
    assert snapshot["quant"]["action"] == "entry_long"
    assert snapshot["quant"]["supporting_factors"] == ["trend_aligned"]
    assert snapshot["quant"]["regime_bucket"] == "trend_long"
    assert snapshot["quant"]["factor_lookup_version"] == "lookup-20260504"
    assert snapshot["quant"]["execution_warnings"] == ["route_c_missing"]
    assert snapshot["quant"]["automation_boundary"] == "real_order_submission_candidate"
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

    html_ids = set(re.findall(r'id="([^"]+)"', html))
    referenced_ids = set(re.findall(r'\$\("([^"]+)"\)', app_js))

    assert '<script src="/app.js"></script>' in html
    assert '<link rel="stylesheet" href="/styles.css" />' in html
    assert {"runtimeGrid", "factorDetails", "quantDetails", "auditEvents"} <= html_ids
    assert referenced_ids <= html_ids
    assert "�" not in html
    assert "�" not in app_js


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
        assert "ETH 运行控制台" in html

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
    finally:
        conn.close()
        server.shutdown()
        server.server_close()
