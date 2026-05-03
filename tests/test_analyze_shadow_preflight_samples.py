from __future__ import annotations

import json
from pathlib import Path

from scripts.analyze_shadow_preflight_samples import (
    load_jsonl_samples,
    render_markdown_report,
    summarize_samples,
)


def test_analyze_shadow_preflight_samples_counts_actions_and_warnings(tmp_path: Path) -> None:
    samples_path = tmp_path / "samples.jsonl"
    audit_path = tmp_path / "sample_1_audit.jsonl"
    audit_path.write_text(
        json.dumps(
            {
                "payload": {
                    "handoff": {
                        "research_gate_status": "open",
                        "risk_filter_status": "degraded",
                        "execution_opportunity_status": "monitor",
                        "execution_layer_reasoning": "waiting_for_trigger",
                        "transition_reason_codes": ["setup_ready_waiting_trigger"],
                        "risk_reason_codes": ["consensus:long"],
                        "trigger_ready": False,
                        "breakout_support": False,
                        "retest_support": False,
                        "trigger_direction": "long",
                        "setup_direction": "long",
                        "entry_timing_score": 0.2515,
                        "slope_support": 0.508,
                        "regime_alignment": 1.0,
                        "staleness_veto": False,
                        "conflict_veto": False,
                        "overlay_bias": "neutral",
                        "degrade_flags": ["crowding_warning"],
                        "snapshot_refs": {"eth_orderbook": "ccxt_orderbook:ETH/USDT"},
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    samples_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "sample_id": 1,
                        "status": "ok",
                        "requested_action": "wait",
                        "effective_action": "wait",
                        "direction": "long",
                        "execution_allowed": False,
                        "execution_block_reason": "not_entry_action",
                        "execution_warnings": [],
                        "reason_codes": ["risk_filter:veto"],
                        "command_targets": [],
                        "preflight_statuses": [],
                        "stop_distance_pct": 0.0143,
                        "audit_log_path": str(audit_path),
                    }
                ),
                json.dumps(
                    {
                        "sample_id": 2,
                        "status": "ok",
                        "requested_action": "small_probe",
                        "effective_action": "small_probe",
                        "direction": "short",
                        "execution_allowed": True,
                        "execution_block_reason": "",
                        "execution_warnings": ["route_c_missing"],
                        "reason_codes": ["crowding_warning"],
                        "command_targets": ["entry_order"],
                        "preflight_statuses": ["preflight_ready"],
                        "preflight": [
                            {
                                "target": "entry_order",
                                "status": "preflight_ready",
                                "error": "",
                            },
                            {
                                "target": "maintain_protective_stop",
                                "status": "error",
                                "reason": "unsafe_request_mapping",
                                "error": "Real protective stop requires an existing entered position",
                            },
                        ],
                        "executable_size_pct": 0.02,
                        "stop_distance_pct": 0.018,
                    }
                ),
                json.dumps(
                    {
                        "sample_id": 3,
                        "status": "ok",
                        "requested_action": "small_probe",
                        "effective_action": "wait",
                        "direction": "long",
                        "execution_allowed": True,
                        "execution_block_reason": "",
                        "execution_warnings": [],
                        "reason_codes": ["degrade_flag:research_degraded"],
                        "command_targets": ["entry_order"],
                        "preflight_statuses": ["error"],
                        "preflight": [
                            {
                                "target": "entry_order",
                                "status": "error",
                                "reason": "unsafe_request_mapping",
                                "error": "A valid runtime snapshot is required before sending a real entry order",
                                "runtime_snapshot": {
                                    "snapshot_valid": False,
                                    "error_endpoint": "/fapi/v1/openOrders",
                                    "error_kind": "http_error",
                                    "error_message": "HTTP 400",
                                    "error_http_status": 400,
                                    "error_payload": {"code": -1021},
                                },
                            }
                        ],
                    }
                ),
                "{not valid json",
            ]
        ),
        encoding="utf-8",
    )

    loaded = load_jsonl_samples(samples_path)
    summary = summarize_samples(loaded.records, source_path=samples_path, malformed_lines=loaded.malformed_lines)
    markdown = render_markdown_report(summary)

    assert len(loaded.records) == 3
    assert loaded.malformed_lines == [4]
    assert summary["total_samples"] == 3
    assert summary["route_c_warning_count"] == 1
    assert summary["preflight_attempt_count"] == 2
    assert summary["preflight_ready_rate"] == 0.5
    assert summary["entry_preflight_attempt_count"] == 2
    assert summary["entry_preflight_ready_count"] == 1
    assert summary["entry_preflight_ready_rate"] == 0.5
    assert summary["entry_preflight_error_reason_distribution"] == [
        {"value": "timestamp_error_-1021", "count": 1, "rate": 1.0}
    ]
    assert summary["protective_stop_preflight_error_reason_distribution"] == [
        {"value": "protective_stop_missing_position", "count": 1, "rate": 1.0}
    ]
    assert summary["runtime_snapshot_error_distribution"] == [
        {"value": "timestamp_error_-1021", "count": 1, "rate": 0.333333}
    ]
    assert summary["decision_ready_sample_count"] == 3
    assert summary["decision_ready_sample_rate"] == 1.0
    assert summary["effective_action_counts"][0] == {"value": "wait", "count": 2, "rate": 0.666667}
    assert summary["numeric_stats"]["stop_distance_pct"]["max"] == 0.018
    assert summary["numeric_stats"]["entry_timing_score"]["max"] == 0.2515
    assert {
        "value": "setup_ready_waiting_trigger",
        "count": 1,
        "rate": 0.333333,
    } in summary["decision_diagnosis_counts"]
    assert {"value": "eth_orderbook", "count": 1, "rate": 0.333333} in summary["snapshot_ref_key_counts"]
    assert {"value": "false", "count": 1, "rate": 0.333333} in summary["trigger_ready_counts"]
    assert {"value": "false", "count": 1, "rate": 0.333333} in summary["breakout_support_counts"]
    assert summary["numeric_stats"]["slope_support"]["max"] == 0.508
    assert "entry_preflight_ready_rate: 50.00% (1/2)" in markdown
    assert "route_c_missing: 1 (33.33%)" in markdown
    assert "setup_ready_waiting_trigger: 1 (33.33%)" in markdown
    assert "eth_orderbook: 1 (33.33%)" in markdown
    assert "timestamp_error_-1021: 1 (100.00%)" in markdown
    assert "protective_stop_missing_position: 1 (100.00%)" in markdown
    assert "decision_ready_sample_rate: 100.00% (3/3)" in markdown
