from __future__ import annotations

import importlib.util
import json
import sys
from argparse import Namespace
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
GENERATOR_PATH = REPO_ROOT / "docs" / "daily_reviews" / "generate_daily.py"


def _load_generator():
    spec = importlib.util.spec_from_file_location("daily_review_generator", GENERATOR_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _args(bot_root: Path, quant_root: Path, output_dir: Path, **overrides) -> Namespace:
    values = {
        "date": "2026-05-18",
        "symbol": "ETHUSDT",
        "timeframe": "15m",
        "bot_root": str(bot_root),
        "quant_root": str(quant_root),
        "output_dir": str(output_dir),
        "reviewer": "tester",
        "market_source": "none",
        "proxy_url": "",
        "force_regenerate": False,
        "dry_run": False,
        "item_limit": 200,
    }
    values.update(overrides)
    return Namespace(**values)


def test_generate_daily_review_consumes_existing_audit_and_shadow(tmp_path: Path) -> None:
    generator = _load_generator()
    bot_root = tmp_path / "eth_trading_bot"
    quant_root = tmp_path / "quant_system_rebuild"
    analysis_dir = quant_root / "runtime" / "analysis"
    scheduler_dir = quant_root / "runtime" / "scheduler"
    output_dir = bot_root / "docs" / "daily_reviews"
    audit_path = analysis_dir / "missed_opportunity_audit_20260518.json"

    _write_json(
        audit_path,
        {
            "status": "review",
            "category_counts": {
                "logic_gap": 1,
                "strategy_choice": 2,
                "reasonable_risk_control": 3,
                "observability_gap": 4,
            },
            "reason_counts": {"waiting_for_trigger": 2, "trigger_ready_probe_path_missing": 1},
            "summary": {
                "trigger_ready_count": 1,
                "execution_ready_count": 0,
                "audited_item_count": 2,
            },
            "items": [
                {
                    "cycle_id": "eth-15m-20260518T010000Z-aaaa",
                    "cycle_ts": "20260518T010000Z",
                    "category": "strategy_choice",
                    "action": "wait",
                    "trigger_ready": False,
                    "risk_filter_status": "degraded",
                    "reason_codes": ["waiting_for_trigger"],
                },
                {
                    "cycle_id": "eth-15m-20260518T011500Z-bbbb",
                    "cycle_ts": "20260518T011500Z",
                    "category": "logic_gap",
                    "action": "wait",
                    "trigger_ready": True,
                    "risk_filter_status": "degraded",
                    "reason_codes": ["trigger_ready_probe_path_missing"],
                },
            ],
            "inputs": {
                "start_ts": "20260518T000000Z",
                "end_ts": "20260519T000000Z",
                "symbol": "eth",
                "timeframe": "15m",
            },
        },
    )
    _write_json(
        analysis_dir / "missed_opportunity_shadow_outcomes_20260518.json",
        {
            "status": "blocked",
            "summary": {"logic_gap_count": 1, "evaluated_item_count": 1},
            "primary_summary": {"unique_net_favorable_rate": 0.5, "unique_available_count": 1},
            "inputs": {"audit_path": str(audit_path)},
        },
    )
    _write_json(
        analysis_dir / "live_ready_blocking_diagnostics.json",
        {
            "status": "blocked",
            "dominant_blocking_reason": "research_gate:research_stale",
            "gates": {
                "research_gate": {"status": "blocked", "blocking": True, "reason_codes": ["research_stale"]},
                "risk_filter": {"status": "veto", "blocking": True, "reason_codes": ["research_issue_present"]},
                "candidate_package": {"status": "blocked", "blocking": True, "reason_codes": ["live_candidate_count_zero"]},
            },
            "inputs": {"decision_path": "runtime/cycles/eth-15m-20260505T160027Z-old/decision.json"},
        },
    )
    _write_json(
        scheduler_dir / "daily_review.json",
        {
            "generated_at": "2026-05-18T23:59:00+00:00",
            "metadata": {"recommended_actions": ["review_research_health_before_promotion"]},
        },
    )

    result = generator.generate_daily_review(_args(bot_root, quant_root, output_dir))
    summary = result["summary"]

    assert summary["inputs"][1]["status"] == "ok"
    assert summary["inputs"][2]["status"] == "stale"
    assert summary["inputs"][3]["status"] == "ok"
    assert summary["system_health"]["primary_fault_domain"] == "strategy_wait"
    assert summary["system_health"]["diagnostics_status"] == "stale"
    assert summary["system_health"]["stale_diagnostics_reference"] == "research_gate:research_stale"
    assert summary["system_health"]["dominant_blocking_reason"] == "diagnostics_stale; audit_top_reason=waiting_for_trigger"
    assert summary["gate_layers"][1]["status"] == "unknown_stale_diagnostics"
    assert summary["gate_layers"][1]["reason"] == "diagnostics_stale_not_used_as_target_day_conclusion"
    assert summary["difference_categories"]["category_counts"]["logic_gap"] == 1
    assert summary["difference_categories"]["shadow_outcomes"]["evaluated_item_count"] == 1
    assert summary["timeline"][0]["event_review_label"] == "needs_review"

    markdown_path = output_dir / "2026-05-18_market_vs_system.md"
    summary_path = output_dir / "artifacts" / "2026-05-18_summary.json"
    assert markdown_path.exists()
    assert summary_path.exists()
    markdown = markdown_path.read_text(encoding="utf-8")
    assert "> Reviewer: tester" in markdown
    assert "事件级评价" in markdown
    assert "diagnostics_status: stale" in markdown
    assert "research_gate: stale_reference:blocked" in markdown
    assert "shadow_outcomes" in markdown


def test_generate_daily_review_force_regenerate_dry_run_plans_rebuild(tmp_path: Path) -> None:
    generator = _load_generator()
    bot_root = tmp_path / "eth_trading_bot"
    quant_root = tmp_path / "quant_system_rebuild"
    output_dir = bot_root / "docs" / "daily_reviews"
    _write_json(
        quant_root / "runtime" / "analysis" / "missed_opportunity_audit_20260518.json",
        {
            "inputs": {
                "start_ts": "20260518T000000Z",
                "end_ts": "20260519T000000Z",
                "symbol": "eth",
                "timeframe": "15m",
            }
        },
    )

    result = generator.generate_daily_review(
        _args(bot_root, quant_root, output_dir, force_regenerate=True, dry_run=True)
    )

    audit_input = result["summary"]["inputs"][1]
    assert audit_input["status"] == "regenerate_planned"
    assert not (output_dir / "2026-05-18_market_vs_system.md").exists()


def test_stale_diagnostics_uses_highest_count_audit_reason_not_json_order() -> None:
    generator = _load_generator()
    payload = {
        "reason_counts": {
            "minor_reason": 1,
            "decision_artifact_missing": 20,
            "waiting_for_trigger": 5,
        },
        "category_counts": {"strategy_choice": 5, "observability_gap": 20},
    }

    assert generator._dominant_audit_reason(payload) == "decision_artifact_missing"
    assert generator._fault_domain_from_audit(payload) == "unknown"
    assert list(generator._top_count_dict(payload["reason_counts"], limit=2)) == [
        "decision_artifact_missing",
        "waiting_for_trigger",
    ]


def test_market_source_auto_falls_back_to_unavailable_when_api_and_local_missing(tmp_path: Path, monkeypatch) -> None:
    generator = _load_generator()
    window = generator._review_window("2026-05-18")

    def raise_api_error(**_kwargs):
        raise RuntimeError("forced api failure")

    monkeypatch.setattr(generator, "_fetch_binance_klines", raise_api_error)

    payload, record = generator._load_market_summary(
        quant_root=tmp_path / "quant_system_rebuild",
        window=window,
        symbol="ETHUSDT",
        market_source="auto",
        proxy_url="",
    )

    assert payload["market_data_status"] == "unavailable"
    assert payload["main_regime"] == "unavailable"
    assert record.status == "unavailable"
    assert record.source == "unavailable"
    assert "binance_api_failed" in record.trace


def test_empty_audit_generates_safe_empty_timeline_and_unknown_layers(tmp_path: Path) -> None:
    generator = _load_generator()
    bot_root = tmp_path / "eth_trading_bot"
    quant_root = tmp_path / "quant_system_rebuild"
    output_dir = bot_root / "docs" / "daily_reviews"
    _write_json(
        quant_root / "runtime" / "analysis" / "missed_opportunity_audit_20260518.json",
        {
            "category_counts": {
                "logic_gap": 0,
                "strategy_choice": 0,
                "reasonable_risk_control": 0,
                "observability_gap": 0,
            },
            "reason_counts": {},
            "summary": {"trigger_ready_count": 0, "execution_ready_count": 0, "audited_item_count": 0},
            "items": [],
            "inputs": {
                "start_ts": "20260518T000000Z",
                "end_ts": "20260519T000000Z",
                "symbol": "eth",
                "timeframe": "15m",
            },
        },
    )

    result = generator.generate_daily_review(_args(bot_root, quant_root, output_dir))
    summary = result["summary"]

    assert summary["timeline"] == []
    assert summary["gate_layers"][0]["status"] == "no_execution_candidate"
    assert summary["gate_layers"][1]["status"] == "unknown"
    assert "no audited items" in result["markdown"]


def test_unavailable_market_data_markdown_includes_impact_note(tmp_path: Path) -> None:
    generator = _load_generator()
    bot_root = tmp_path / "eth_trading_bot"
    quant_root = tmp_path / "quant_system_rebuild"
    output_dir = bot_root / "docs" / "daily_reviews"
    _write_json(
        quant_root / "runtime" / "analysis" / "missed_opportunity_audit_20260518.json",
        {
            "category_counts": {"strategy_choice": 1},
            "reason_counts": {"waiting_for_trigger": 1},
            "summary": {"trigger_ready_count": 0, "execution_ready_count": 0, "audited_item_count": 1},
            "items": [
                {
                    "cycle_id": "eth-15m-20260518T010000Z-aaaa",
                    "cycle_ts": "20260518T010000Z",
                    "category": "strategy_choice",
                    "action": "wait",
                    "trigger_ready": False,
                    "risk_filter_status": "degraded",
                    "reason_codes": ["waiting_for_trigger"],
                }
            ],
            "inputs": {
                "start_ts": "20260518T000000Z",
                "end_ts": "20260519T000000Z",
                "symbol": "eth",
                "timeframe": "15m",
            },
        },
    )

    result = generator.generate_daily_review(_args(bot_root, quant_root, output_dir, market_source="none"))

    assert "- market_data_status: unavailable" in result["markdown"]
    assert "- impact: cannot judge missed/correct market opportunity from price action" in result["markdown"]
    assert "| 01:00 | unavailable@all_day |" in result["markdown"]


def test_market_summary_removes_internal_bar_objects_from_public_summary() -> None:
    generator = _load_generator()
    window = generator._review_window("2026-05-18")
    payload = generator._summarize_klines(
        symbol="ETHUSDT",
        window=window,
        source="test",
        klines_15m=[
            [1779062400000, "100", "102", "99", "101"],
            [1779063300000, "101", "103", "100", "102"],
        ],
        klines_1h=[],
    )

    public = generator._public_market_payload(payload)

    assert "bars_15m" not in public
    assert public["bar_count_for_timeline"] == 2
    json.dumps(public)


def test_market_summary_filters_end_boundary_bar() -> None:
    generator = _load_generator()
    window = generator._review_window("2026-05-18")
    payload = generator._summarize_klines(
        symbol="ETHUSDT",
        window=window,
        source="test",
        klines_15m=[
            [1779062400000, "100", "102", "99", "101"],
            [1779148800000, "101", "103", "100", "102"],
        ],
        klines_1h=[],
    )

    assert payload["candle_count_15m"] == 1


def test_write_analysis_artifact_falls_back_to_local_artifacts(tmp_path: Path, monkeypatch) -> None:
    generator = _load_generator()
    primary = tmp_path / "quant" / "runtime" / "analysis" / "missed_opportunity_audit_20260518.json"
    fallback = tmp_path / "bot" / "docs" / "daily_reviews" / "artifacts" / "missed_opportunity_audit_20260518.json"

    original_write_text = Path.write_text

    def guarded_write_text(self: Path, *args, **kwargs):
        if self == primary:
            raise PermissionError("read only quant artifact dir")
        return original_write_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", guarded_write_text)

    written_path, source = generator._write_analysis_artifact_with_fallback(
        primary_path=primary,
        fallback_path=fallback,
        payload={"status": "ok"},
        markdown="# ok\n",
    )

    assert written_path == fallback
    assert source == "builder_local_fallback"
    assert fallback.exists()
    assert fallback.with_suffix(".md").exists()
