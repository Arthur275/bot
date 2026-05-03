from __future__ import annotations

from collections.abc import Mapping
from typing import Any


HANDOFF_BASE_FIELDS = (
    "action",
    "direction",
    "execution_allowed",
    "execution_block_reason",
    "execution_warnings",
    "position_size_pct",
    "executable_size_pct",
    "sizing_tier",
    "sizing_bias",
    "initial_stop_loss",
    "stop_distance_pct",
    "estimated_cost_pct",
    "net_edge_pct",
)

HANDOFF_DIAGNOSTIC_FIELDS = (
    "execution_profile",
    "context_timeframe",
    "structure_timeframe",
    "trigger_timeframe",
    "execution_timeframe",
    "confidence",
    "thesis_score",
    "adverse_score",
    "signal_size_pct",
    "position_cap_pct",
    "max_account_risk_pct_per_trade",
    "research_gate_status",
    "risk_filter_status",
    "execution_opportunity_status",
    "execution_layer_reasoning",
    "risk_reason_codes",
    "transition_reason_codes",
    "runtime_vetoes",
    "degrade_flags",
    "probe_source",
    "trigger_ready",
    "trigger_reversal",
    "trigger_direction",
    "setup_direction",
    "entry_timing_score",
    "breakout_support",
    "retest_support",
    "slope_support",
    "regime_alignment",
    "probe_expiry_bars",
    "probe_expiry_timeframe",
    "probe_invalid_if_no_followthrough",
    "probe_risk_tier",
    "staleness_veto",
    "conflict_veto",
    "overlay_bias",
    "overlay_summary",
    "research_gate_reasons",
    "diagnostic_status",
    "diagnostic_category",
    "diagnostic_source",
    "reasoning_summary",
    "breakeven_trigger",
    "trailing_rule",
    "trailing_activation_ratio",
    "trailing_callback_rate_pct",
)

HANDOFF_SUMMARY_FIELDS = (*HANDOFF_BASE_FIELDS, *HANDOFF_DIAGNOSTIC_FIELDS)
SNAPSHOT_DIAGNOSTIC_FIELDS = ("snapshot_ref_keys", "has_orderbook_snapshot")
SAMPLE_HANDOFF_FIELDS = (*HANDOFF_DIAGNOSTIC_FIELDS, *SNAPSHOT_DIAGNOSTIC_FIELDS)
JUDGEMENT_DIAGNOSTIC_FIELDS = (
    "status",
    "entry_mode",
    "diagnostic",
    "diagnostic_status",
    "diagnostic_source",
    "diagnostic_category",
    "diagnostic_boundary",
    "diagnostic_attempts",
    "diagnostic_error_type",
    "diagnostic_error_detail",
    "diagnostic_url",
    "diagnostic_proxy_url",
    "source_diagnostics_count",
)


def summarize_handoff(handoff: Mapping[str, Any]) -> dict[str, Any]:
    summary = {field: handoff.get(field) for field in HANDOFF_SUMMARY_FIELDS}
    ref_keys = snapshot_ref_keys(handoff.get("snapshot_refs"))
    summary["snapshot_ref_keys"] = ref_keys
    summary["has_orderbook_snapshot"] = any("orderbook" in key.lower() for key in ref_keys)
    return summary


def summarize_judgement(judgement: Mapping[str, Any]) -> dict[str, Any]:
    summary = {field: judgement.get(field) for field in JUDGEMENT_DIAGNOSTIC_FIELDS}
    source_diagnostics = judgement.get("source_diagnostics")
    if isinstance(source_diagnostics, Mapping):
        summary["source_diagnostic_keys"] = sorted(str(key) for key in source_diagnostics)
    else:
        summary["source_diagnostic_keys"] = []
    return summary


def snapshot_ref_keys(value: Any) -> list[str]:
    if not isinstance(value, Mapping):
        return []
    return sorted(str(key) for key in value)
