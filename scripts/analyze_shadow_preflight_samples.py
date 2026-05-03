from __future__ import annotations

import argparse
import json
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from statistics import mean, median
from typing import Any

try:
    from .shadow_preflight_diagnostics import HANDOFF_DIAGNOSTIC_FIELDS, snapshot_ref_keys
except ImportError:
    from shadow_preflight_diagnostics import HANDOFF_DIAGNOSTIC_FIELDS, snapshot_ref_keys


NUMERIC_FIELDS = (
    "position_size_pct",
    "executable_size_pct",
    "stop_distance_pct",
    "account_risk_pct",
    "initial_stop_loss",
    "estimated_cost_pct",
    "net_edge_pct",
    "confidence",
    "thesis_score",
    "adverse_score",
    "signal_size_pct",
    "entry_timing_score",
    "slope_support",
    "regime_alignment",
    "position_cap_pct",
    "max_account_risk_pct_per_trade",
    "trailing_callback_rate_pct",
)


@dataclass(frozen=True)
class SampleLoadResult:
    records: list[dict[str, Any]]
    malformed_lines: list[int]


def load_jsonl_samples(path: str | Path) -> SampleLoadResult:
    normalized_path = Path(path)
    records: list[dict[str, Any]] = []
    malformed_lines: list[int] = []
    if not normalized_path.exists():
        return SampleLoadResult(records=records, malformed_lines=malformed_lines)

    for line_number, line in enumerate(normalized_path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            malformed_lines.append(line_number)
            continue
        if isinstance(payload, dict):
            records.append(payload)
        else:
            malformed_lines.append(line_number)
    return SampleLoadResult(records=records, malformed_lines=malformed_lines)


def summarize_samples(
    records: list[dict[str, Any]],
    *,
    source_path: str | Path,
    malformed_lines: list[int] | None = None,
) -> dict[str, Any]:
    records = [_enrich_record(record) for record in records]
    total = len(records)
    latest = records[-1] if records else {}
    decision_ready_records = [record for record in records if _is_decision_ready_record(record)]
    preflight_attempts = [
        record
        for record in records
        if _as_list(record.get("command_targets"))
    ]
    preflight_ready = [
        record
        for record in preflight_attempts
        if _as_list(record.get("preflight_statuses"))
        and all(str(status) == "preflight_ready" for status in _as_list(record.get("preflight_statuses")))
        and not str(record.get("preflight_error") or "")
    ]
    entry_preflight_attempts = _preflight_items(records, target="entry_order")
    entry_preflight_ready = [
        item
        for item in entry_preflight_attempts
        if str(item.get("status") or "") == "preflight_ready" and not str(item.get("error") or "")
    ]
    entry_preflight_errors = [
        item
        for item in entry_preflight_attempts
        if str(item.get("status") or "") != "preflight_ready" or str(item.get("error") or "")
    ]
    protective_stop_errors = [
        item
        for item in _preflight_items(records, target="maintain_protective_stop")
        if str(item.get("status") or "") != "preflight_ready" or str(item.get("error") or "")
    ]
    route_c_warning_count = sum(
        1
        for record in records
        if "route_c_missing" in _as_string_list(record.get("execution_warnings"))
    )
    return {
        "generated_at": datetime.now().replace(microsecond=0).isoformat(),
        "source_path": str(Path(source_path)),
        "total_samples": total,
        "malformed_line_count": len(malformed_lines or []),
        "malformed_lines": list(malformed_lines or []),
        "status_counts": _counter_table(_counter(records, "status"), total),
        "effective_action_counts": _counter_table(_counter(records, "effective_action"), total),
        "requested_action_counts": _counter_table(_counter(records, "requested_action"), total),
        "direction_counts": _counter_table(_counter(records, "direction"), total),
        "execution_allowed_counts": _counter_table(_counter_bool(records, "execution_allowed"), total),
        "block_reason_counts": _counter_table(_counter(records, "execution_block_reason", default="<none>"), total),
        "decision_diagnosis_counts": _counter_table(_counter_derived(records, _diagnose_record), total),
        "research_gate_status_counts": _counter_table(_counter(records, "research_gate_status", default="<missing>"), total),
        "risk_filter_status_counts": _counter_table(_counter(records, "risk_filter_status", default="<missing>"), total),
        "execution_opportunity_status_counts": _counter_table(
            _counter(records, "execution_opportunity_status", default="<missing>"), total
        ),
        "execution_layer_reasoning_counts": _counter_table(
            _counter(records, "execution_layer_reasoning", default="<missing>"), total
        ),
        "setup_direction_counts": _counter_table(_counter(records, "setup_direction", default="<missing>"), total),
        "trigger_direction_counts": _counter_table(_counter(records, "trigger_direction", default="<missing>"), total),
        "trigger_ready_counts": _counter_table(_counter_bool(records, "trigger_ready"), total),
        "breakout_support_counts": _counter_table(_counter_bool(records, "breakout_support"), total),
        "retest_support_counts": _counter_table(_counter_bool(records, "retest_support"), total),
        "staleness_veto_counts": _counter_table(_counter_bool(records, "staleness_veto"), total),
        "conflict_veto_counts": _counter_table(_counter_bool(records, "conflict_veto"), total),
        "overlay_bias_counts": _counter_table(_counter(records, "overlay_bias", default="<missing>"), total),
        "transition_reason_counts": _counter_table(_counter_list(records, "transition_reason_codes"), total),
        "risk_reason_counts": _counter_table(_counter_list(records, "risk_reason_codes"), total),
        "degrade_flag_counts": _counter_table(_counter_list(records, "degrade_flags"), total),
        "snapshot_ref_key_counts": _counter_table(_counter_list(records, "snapshot_ref_keys"), total),
        "has_orderbook_snapshot_counts": _counter_table(_counter_bool(records, "has_orderbook_snapshot"), total),
        "execution_profile_counts": _counter_table(_counter(records, "execution_profile", default="<missing>"), total),
        "reason_code_counts": _counter_table(_counter_list(records, "reason_codes"), total),
        "execution_warning_counts": _counter_table(_counter_list(records, "execution_warnings"), total),
        "command_target_counts": _counter_table(_counter_list(records, "command_targets"), total),
        "preflight_status_counts": _counter_table(_counter_list(records, "preflight_statuses"), total),
        "preflight_attempt_count": len(preflight_attempts),
        "preflight_ready_count": len(preflight_ready),
        "preflight_ready_rate": _ratio(len(preflight_ready), len(preflight_attempts)),
        "preflight_error_count": sum(1 for record in records if str(record.get("preflight_error") or "")),
        "entry_preflight_attempt_count": len(entry_preflight_attempts),
        "entry_preflight_ready_count": len(entry_preflight_ready),
        "entry_preflight_ready_rate": _ratio(len(entry_preflight_ready), len(entry_preflight_attempts)),
        "entry_preflight_error_count": len(entry_preflight_errors),
        "entry_preflight_error_reason_distribution": _counter_table(
            _counter_preflight_error_reasons(entry_preflight_errors), len(entry_preflight_errors)
        ),
        "protective_stop_preflight_error_reason_distribution": _counter_table(
            _counter_preflight_error_reasons(protective_stop_errors), len(protective_stop_errors)
        ),
        "runtime_snapshot_error_distribution": _counter_table(
            _counter_runtime_snapshot_errors(_preflight_items(records)), len(_preflight_items(records))
        ),
        "route_c_warning_count": route_c_warning_count,
        "route_c_warning_rate": _ratio(route_c_warning_count, total),
        "decision_ready_sample_count": len(decision_ready_records),
        "decision_ready_sample_rate": _ratio(len(decision_ready_records), total),
        "decision_ready_effective_action_counts": _counter_table(
            _counter(decision_ready_records, "effective_action"), len(decision_ready_records)
        ),
        "decision_ready_diagnosis_counts": _counter_table(
            _counter_derived(decision_ready_records, _diagnose_record), len(decision_ready_records)
        ),
        "numeric_stats": {
            field: _numeric_stats(records, field)
            for field in NUMERIC_FIELDS
        },
        "latest_sample": {
            "sample_id": latest.get("sample_id"),
            "started_at": latest.get("started_at"),
            "finished_at": latest.get("finished_at"),
            "status": latest.get("status"),
            "requested_action": latest.get("requested_action"),
            "effective_action": latest.get("effective_action"),
            "direction": latest.get("direction"),
            "execution_allowed": latest.get("execution_allowed"),
            "execution_block_reason": latest.get("execution_block_reason"),
            "execution_warnings": _as_string_list(latest.get("execution_warnings")),
            "reason_codes": _as_string_list(latest.get("reason_codes")),
            "decision_diagnosis": _diagnose_record(latest) if latest else "",
            "research_gate_status": latest.get("research_gate_status"),
            "risk_filter_status": latest.get("risk_filter_status"),
            "execution_opportunity_status": latest.get("execution_opportunity_status"),
            "execution_layer_reasoning": latest.get("execution_layer_reasoning"),
            "trigger_ready": latest.get("trigger_ready"),
            "trigger_direction": latest.get("trigger_direction"),
            "setup_direction": latest.get("setup_direction"),
            "entry_timing_score": latest.get("entry_timing_score"),
            "breakout_support": latest.get("breakout_support"),
            "retest_support": latest.get("retest_support"),
            "slope_support": latest.get("slope_support"),
            "regime_alignment": latest.get("regime_alignment"),
            "staleness_veto": latest.get("staleness_veto"),
            "conflict_veto": latest.get("conflict_veto"),
            "overlay_bias": latest.get("overlay_bias"),
            "transition_reason_codes": _as_string_list(latest.get("transition_reason_codes")),
            "risk_reason_codes": _as_string_list(latest.get("risk_reason_codes")),
            "snapshot_ref_keys": _as_string_list(latest.get("snapshot_ref_keys")),
            "has_orderbook_snapshot": latest.get("has_orderbook_snapshot"),
            "reasoning_summary": latest.get("reasoning_summary"),
        },
    }


def render_markdown_report(summary: dict[str, Any]) -> str:
    lines = [
        "# Shadow Preflight Sample Report",
        "",
        f"- generated_at: {summary['generated_at']}",
        f"- source_path: {summary['source_path']}",
        f"- total_samples: {summary['total_samples']}",
        f"- malformed_line_count: {summary['malformed_line_count']}",
        f"- route_c_warning_rate: {_format_rate(summary['route_c_warning_rate'])}",
        f"- preflight_ready_rate: {_format_rate(summary['preflight_ready_rate'])} ({summary['preflight_ready_count']}/{summary['preflight_attempt_count']})",
        f"- entry_preflight_ready_rate: {_format_rate(summary['entry_preflight_ready_rate'])} ({summary['entry_preflight_ready_count']}/{summary['entry_preflight_attempt_count']})",
        f"- decision_ready_sample_rate: {_format_rate(summary['decision_ready_sample_rate'])} ({summary['decision_ready_sample_count']}/{summary['total_samples']})",
        "",
        "## Latest Sample",
        "",
    ]
    latest = summary["latest_sample"]
    for key in (
        "sample_id",
        "started_at",
        "finished_at",
        "status",
        "requested_action",
        "effective_action",
        "direction",
        "execution_allowed",
        "execution_block_reason",
        "decision_diagnosis",
        "research_gate_status",
        "risk_filter_status",
        "execution_opportunity_status",
        "execution_layer_reasoning",
        "trigger_ready",
        "trigger_direction",
        "setup_direction",
        "entry_timing_score",
        "breakout_support",
        "retest_support",
        "slope_support",
        "regime_alignment",
        "staleness_veto",
        "conflict_veto",
        "overlay_bias",
        "has_orderbook_snapshot",
    ):
        lines.append(f"- {key}: {latest.get(key)}")
    lines.append(f"- execution_warnings: {', '.join(latest.get('execution_warnings') or []) or '<none>'}")
    lines.append(f"- reason_codes: {', '.join(latest.get('reason_codes') or []) or '<none>'}")
    lines.append(f"- transition_reason_codes: {', '.join(latest.get('transition_reason_codes') or []) or '<none>'}")
    lines.append(f"- risk_reason_codes: {', '.join(latest.get('risk_reason_codes') or []) or '<none>'}")
    lines.append(f"- snapshot_ref_keys: {', '.join(latest.get('snapshot_ref_keys') or []) or '<none>'}")
    lines.append(f"- reasoning_summary: {latest.get('reasoning_summary') or '<none>'}")

    sections = (
        ("Effective Actions", "effective_action_counts"),
        ("Research-Ready Effective Actions", "decision_ready_effective_action_counts"),
        ("Directions", "direction_counts"),
        ("Block Reasons", "block_reason_counts"),
        ("Decision Diagnosis", "decision_diagnosis_counts"),
        ("Research-Ready Decision Diagnosis", "decision_ready_diagnosis_counts"),
        ("Research Gate Status", "research_gate_status_counts"),
        ("Risk Filter Status", "risk_filter_status_counts"),
        ("Execution Opportunity Status", "execution_opportunity_status_counts"),
        ("Execution Layer Reasoning", "execution_layer_reasoning_counts"),
        ("Setup Directions", "setup_direction_counts"),
        ("Trigger Directions", "trigger_direction_counts"),
        ("Trigger Ready", "trigger_ready_counts"),
        ("Breakout Support", "breakout_support_counts"),
        ("Retest Support", "retest_support_counts"),
        ("Staleness Veto", "staleness_veto_counts"),
        ("Conflict Veto", "conflict_veto_counts"),
        ("Overlay Bias", "overlay_bias_counts"),
        ("Transition Reasons", "transition_reason_counts"),
        ("Risk Reasons", "risk_reason_counts"),
        ("Degrade Flags", "degrade_flag_counts"),
        ("Snapshot Ref Keys", "snapshot_ref_key_counts"),
        ("Orderbook Snapshot", "has_orderbook_snapshot_counts"),
        ("Execution Profiles", "execution_profile_counts"),
        ("Reason Codes", "reason_code_counts"),
        ("Execution Warnings", "execution_warning_counts"),
        ("Command Targets", "command_target_counts"),
        ("Preflight Statuses", "preflight_status_counts"),
        ("Entry Preflight Error Reasons", "entry_preflight_error_reason_distribution"),
        ("Protective Stop Preflight Error Reasons", "protective_stop_preflight_error_reason_distribution"),
        ("Runtime Snapshot Errors", "runtime_snapshot_error_distribution"),
    )
    for title, key in sections:
        lines.extend(["", f"## {title}", ""])
        rows = summary.get(key) or []
        if not rows:
            lines.append("- <none>")
            continue
        for row in rows[:20]:
            lines.append(f"- {row['value']}: {row['count']} ({_format_rate(row['rate'])})")

    lines.extend(["", "## Numeric Stats", ""])
    for field, stats in summary["numeric_stats"].items():
        if stats["count"] == 0:
            lines.append(f"- {field}: <none>")
            continue
        lines.append(
            f"- {field}: count={stats['count']} min={stats['min']} "
            f"p50={stats['p50']} avg={stats['avg']} max={stats['max']}"
        )
    lines.append("")
    return "\n".join(lines)


def _enrich_record(record: dict[str, Any]) -> dict[str, Any]:
    enriched = dict(record)
    audit_handoff = _load_audit_handoff(enriched.get("audit_log_path"))
    if not audit_handoff:
        return enriched

    for field in HANDOFF_DIAGNOSTIC_FIELDS:
        if _is_missing(enriched.get(field)) and field in audit_handoff:
            enriched[field] = audit_handoff.get(field)
    if _is_missing(enriched.get("snapshot_ref_keys")) or _is_missing(enriched.get("has_orderbook_snapshot")):
        snapshot_refs = audit_handoff.get("snapshot_refs")
        ref_keys = snapshot_ref_keys(snapshot_refs)
        if _is_missing(enriched.get("snapshot_ref_keys")):
            enriched["snapshot_ref_keys"] = ref_keys
        if _is_missing(enriched.get("has_orderbook_snapshot")):
            enriched["has_orderbook_snapshot"] = any("orderbook" in key.lower() for key in ref_keys)
    return enriched


def _load_audit_handoff(path_value: Any) -> dict[str, Any]:
    if _is_missing(path_value):
        return {}
    try:
        audit_path = Path(str(path_value))
        if not audit_path.exists():
            return {}
        lines = [line for line in audit_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        if not lines:
            return {}
        event = json.loads(lines[-1])
    except (OSError, json.JSONDecodeError):
        return {}
    payload = event.get("payload") if isinstance(event, dict) else {}
    handoff = payload.get("handoff") if isinstance(payload, dict) else {}
    return dict(handoff) if isinstance(handoff, Mapping) else {}


def _diagnose_record(record: dict[str, Any]) -> str:
    status = str(record.get("status") or "")
    if status and status != "ok":
        return f"sample_status:{status}"

    all_codes = {
        *_as_string_list(record.get("reason_codes")),
        *_as_string_list(record.get("risk_reason_codes")),
        *_as_string_list(record.get("transition_reason_codes")),
    }
    warnings = set(_as_string_list(record.get("execution_warnings")))
    if "route_c_missing" in warnings:
        return "route_c_missing"
    if "research_not_ready" in all_codes:
        return "research_not_ready"
    if _to_bool(record.get("staleness_veto")) is True:
        return "staleness_veto"
    if _to_bool(record.get("conflict_veto")) is True:
        return "conflict_veto"
    if "net_edge_below_cost" in all_codes:
        return "net_edge_below_cost"

    requested_action = str(record.get("requested_action") or "")
    effective_action = str(record.get("effective_action") or "")
    if effective_action == "wait":
        transition_codes = set(_as_string_list(record.get("transition_reason_codes")))
        execution_reason = str(record.get("execution_layer_reasoning") or "")
        setup_direction = str(record.get("setup_direction") or "")
        trigger_ready = _to_bool(record.get("trigger_ready"))
        if (
            "setup_ready_waiting_trigger" in transition_codes
            or execution_reason == "waiting_for_trigger"
            or (trigger_ready is False and setup_direction not in {"", "neutral", "<missing>"})
        ):
            return "setup_ready_waiting_trigger"
        if execution_reason:
            return execution_reason
        block_reason = str(record.get("execution_block_reason") or "")
        if block_reason:
            return f"wait:{block_reason}"
        return "wait"
    if requested_action and requested_action != effective_action:
        return f"{requested_action}->{effective_action}"
    return effective_action or "<missing>"


def _is_decision_ready_record(record: dict[str, Any]) -> bool:
    if str(record.get("status") or "") != "ok":
        return False
    return _diagnose_record(record) != "research_not_ready"


def _counter_derived(records: list[dict[str, Any]], resolver: Any) -> Counter[str]:
    counter: Counter[str] = Counter()
    for record in records:
        value = str(resolver(record) or "").strip()
        if value:
            counter[value] += 1
    return counter


def _counter(records: list[dict[str, Any]], field: str, *, default: str = "") -> Counter[str]:
    counter: Counter[str] = Counter()
    for record in records:
        value = record.get(field)
        text = str(value if value not in (None, "") else default).strip()
        if text:
            counter[text] += 1
    return counter


def _counter_bool(records: list[dict[str, Any]], field: str) -> Counter[str]:
    counter: Counter[str] = Counter()
    for record in records:
        value = _to_bool(record.get(field))
        if value is None:
            counter["missing"] += 1
        else:
            counter[str(value).lower()] += 1
    return counter


def _counter_list(records: list[dict[str, Any]], field: str) -> Counter[str]:
    counter: Counter[str] = Counter()
    for record in records:
        values = _as_string_list(record.get(field))
        if not values:
            continue
        counter.update(values)
    return counter


def _preflight_items(records: list[dict[str, Any]], *, target: str = "") -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for record in records:
        detailed = record.get("preflight")
        if isinstance(detailed, list) and detailed:
            for item in detailed:
                if not isinstance(item, Mapping):
                    continue
                normalized = dict(item)
                if target and str(normalized.get("target") or "") != target:
                    continue
                items.append(normalized)
            continue

        targets = _as_string_list(record.get("command_targets"))
        statuses = _as_string_list(record.get("preflight_statuses"))
        for index, command_target in enumerate(targets):
            if target and command_target != target:
                continue
            status = statuses[index] if index < len(statuses) else ""
            items.append(
                {
                    "target": command_target,
                    "status": status,
                    "reason": "",
                    "error": record.get("preflight_error") or _legacy_preflight_error_hint(command_target, status),
                    "runtime_snapshot": None,
                }
            )
    return items


def _counter_preflight_error_reasons(items: list[dict[str, Any]]) -> Counter[str]:
    counter: Counter[str] = Counter()
    for item in items:
        counter[_classify_preflight_error(item)] += 1
    return counter


def _counter_runtime_snapshot_errors(items: list[dict[str, Any]]) -> Counter[str]:
    counter: Counter[str] = Counter()
    for item in items:
        snapshot = item.get("runtime_snapshot")
        if not isinstance(snapshot, Mapping):
            continue
        if snapshot.get("snapshot_valid") is not False:
            continue
        counter[_classify_runtime_snapshot_error(snapshot)] += 1
    return counter


def _classify_preflight_error(item: Mapping[str, Any]) -> str:
    snapshot = item.get("runtime_snapshot")
    if isinstance(snapshot, Mapping) and snapshot.get("snapshot_valid") is False:
        return _classify_runtime_snapshot_error(snapshot)

    text = " ".join(
        str(value or "")
        for value in (
            item.get("error"),
            item.get("reason"),
            item.get("status"),
        )
    ).lower()
    if "-1021" in text or "timestamp" in text:
        return "timestamp_error_-1021"
    if "minqty" in text or "min_qty" in text or "account_too_small_for_exchange_min_qty" in text:
        return "account_too_small_for_exchange_min_qty"
    if "existing entered position" in text or "protective stop requires an existing" in text:
        return "protective_stop_missing_position"
    if "snapshot" in text and ("invalid" in text or "required" in text or "missing" in text):
        return "snapshot_invalid"
    if "timeout" in text:
        return "transport_timeout"
    if "exchange_rejected" in text or "rejected" in text or "http 4" in text:
        return "exchange_rejected"
    if "request_signing_failed" in text or "signing" in text:
        return "request_signing_failed"
    if "unsafe_request_mapping" in text:
        return "unsafe_request_mapping"
    return "unknown_error"


def _legacy_preflight_error_hint(target: str, status: str) -> str:
    if status != "error":
        return ""
    if target == "maintain_protective_stop":
        return "Real protective stop requires an existing entered position"
    return ""


def _classify_runtime_snapshot_error(snapshot: Mapping[str, Any]) -> str:
    payload = snapshot.get("error_payload")
    payload_text = json.dumps(payload, ensure_ascii=False).lower() if payload is not None else ""
    text = " ".join(
        str(value or "")
        for value in (
            snapshot.get("error_kind"),
            snapshot.get("error_message"),
            snapshot.get("error_endpoint"),
            snapshot.get("error_http_status"),
            payload_text,
        )
    ).lower()
    if "-1021" in text or "timestamp" in text:
        return "timestamp_error_-1021"
    if "timeout" in text:
        return "transport_timeout"
    if "http_error" in text or "http 4" in text or "http 5" in text:
        return "exchange_rejected"
    if "snapshot" in text:
        return "snapshot_invalid"
    return "unknown_error"


def _counter_table(counter: Counter[str], total: int) -> list[dict[str, Any]]:
    return [
        {"value": value, "count": count, "rate": _ratio(count, total)}
        for value, count in counter.most_common()
    ]


def _numeric_stats(records: list[dict[str, Any]], field: str) -> dict[str, Any]:
    values = [
        value
        for value in (_to_float(record.get(field)) for record in records)
        if value is not None
    ]
    if not values:
        return {"count": 0, "min": None, "p50": None, "avg": None, "max": None}
    return {
        "count": len(values),
        "min": round(min(values), 6),
        "p50": round(median(values), 6),
        "avg": round(mean(values), 6),
        "max": round(max(values), 6),
    }


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _as_string_list(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        candidates = value.split(",")
    elif isinstance(value, list):
        candidates = value
    else:
        candidates = [value]
    return [
        text
        for text in (str(candidate).strip() for candidate in candidates)
        if text
    ]


def _to_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes"}:
            return True
        if normalized in {"false", "0", "no"}:
            return False
    return None


def _is_missing(value: Any) -> bool:
    return value is None or value == "" or value == []


def _ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 6)


def _format_rate(value: Any) -> str:
    try:
        return f"{float(value) * 100:.2f}%"
    except (TypeError, ValueError):
        return "0.00%"


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze shadow preflight sampler JSONL output.")
    parser.add_argument("samples_path", help="Path to samples.jsonl")
    parser.add_argument("--output-json", default="", help="Optional path to write summary JSON.")
    parser.add_argument("--output-md", default="", help="Optional path to write markdown report.")
    parser.add_argument("--format", choices=("json", "markdown"), default="markdown")
    args = parser.parse_args()

    loaded = load_jsonl_samples(args.samples_path)
    summary = summarize_samples(
        loaded.records,
        source_path=args.samples_path,
        malformed_lines=loaded.malformed_lines,
    )
    if args.output_json:
        Path(args.output_json).write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown = render_markdown_report(summary)
    if args.output_md:
        Path(args.output_md).write_text(markdown, encoding="utf-8")
    if args.format == "json":
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
