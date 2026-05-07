from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


SCHEMA = "decision_review_v1"
VERSION = 1
DEFAULT_STALE_THRESHOLD_SEC = 180
REVIEW_BOUNDARY_SUMMARY = "审查报告仅供解释和复盘，不参与自动下单。"
REVIEW_MODES = {
    "async_light",
    "async_full",
    "daily_integrity_review",
    "daily_outcome_review",
    "outcome_reflection",
    "manual_audit",
}
REVIEW_STATUSES = {"clear", "watch", "needs_attention", "unavailable"}
DANGEROUS_GOVERNANCE_SUGGESTION_FIELDS = {
    "allow_entry",
    "set_sizing",
    "bypass_veto",
    "override_risk",
    "force_execution",
    "execution_allowed",
    "submit_order",
    "candidate_package",
}


def load_decision_review(
    *,
    bot_root: Path,
    quant_root: Path,
    now: datetime | None = None,
    stale_threshold_sec: int = DEFAULT_STALE_THRESHOLD_SEC,
) -> dict[str, Any]:
    review_path = bot_root / "runtime" / "reviews" / "latest_decision_review.json"
    payload = _read_json(review_path)
    if not payload:
        return build_decision_review(
            bot_root=bot_root,
            quant_root=quant_root,
            now=now,
            stale_threshold_sec=stale_threshold_sec,
        )
    return normalize_decision_review(payload)


def build_decision_review(
    *,
    bot_root: Path,
    quant_root: Path,
    now: datetime | None = None,
    stale_threshold_sec: int = DEFAULT_STALE_THRESHOLD_SEC,
    review_mode: str = "async_light",
) -> dict[str, Any]:
    current = now or datetime.now(timezone.utc)
    handoff, handoff_path = _read_latest_handoff(quant_root)
    factor_lookup = _read_json(quant_root / "runtime" / "analysis" / "factor_lookup_summary.json")
    factor_summary = _read_json(quant_root / "runtime" / "analysis" / "factor_summary.json")
    risk_report = _extract_risk_report(quant_root=quant_root, handoff=handoff)
    candidate = _read_json(bot_root / "runtime" / "bot_runtime_scheduler" / "latest_candidate_execution_package.json")
    worker_audit_available = (bot_root / "runtime" / "real_order_worker" / "audit.jsonl").exists()
    outcome_samples_available = _outcome_samples_available(quant_root)
    source_generated_at = str(handoff.get("generated_at") or "")
    source_age = _age_seconds(source_generated_at, now=current)
    source_stale = source_age is None or source_age > stale_threshold_sec
    quality = {
        "handoff_available": bool(handoff),
        "factor_lookup_available": bool(factor_lookup),
        "factor_summary_available": bool(factor_summary),
        "risk_report_available": bool(risk_report),
        "candidate_package_available": bool(candidate),
        "worker_audit_available": worker_audit_available,
        "outcome_samples_available": outcome_samples_available,
    }
    findings: list[dict[str, str]] = []
    execution_findings: list[dict[str, str]] = []
    if not handoff:
        status = "unavailable"
        summary = "审查报告不可用：没有找到 execution handoff。"
    else:
        status = "clear"
        summary = REVIEW_BOUNDARY_SUMMARY
        if source_stale:
            status = "watch"
            findings.append({"code": "source_handoff_stale", "text": "来源 handoff 已过期，审查只能作为历史解释。"})
        if not factor_lookup:
            status = "watch"
            findings.append({"code": "factor_lookup_missing", "text": "因子 lookup 不可用，不能判断因子治理质量。"})
        if not factor_summary:
            status = "watch"
            findings.append({"code": "factor_summary_missing", "text": "样本采集摘要不可用，因子证据完整性不足。"})
        if not risk_report:
            status = "watch"
            findings.append({"code": "risk_report_missing", "text": "风险报告不可用，审查不能标记为清晰。"})
        if not worker_audit_available:
            execution_findings.append({"code": "worker_audit_missing", "text": "执行 worker 审计日志不可用。"})
        if findings:
            summary = "审查报告存在观察项；审查不参与自动下单。"
    return normalize_decision_review(
        {
            "schema": SCHEMA,
            "version": VERSION,
            "generated_at": current.isoformat(),
            "source_run_id": _resolve_source_run_id(handoff=handoff, handoff_path=handoff_path),
            "handoff_id": str(handoff.get("handoff_id") or handoff.get("id") or ""),
            "source_handoff_age_sec": source_age,
            "source_stale_threshold_sec": int(stale_threshold_sec),
            "source_stale": source_stale,
            "review_mode": review_mode,
            "review_status": status,
            "latency_ms": 0,
            "timeout": False,
            "fallback_used": False,
            "structured_fields_accepted": True,
            "data_source_quality": quality,
            "bull_case": _case_from_codes(handoff.get("supporting_factor_codes"), label="supporting_factor"),
            "bear_case": _case_from_codes(handoff.get("opposing_factor_codes"), label="opposing_factor"),
            "risk_findings": findings + _case_from_codes(handoff.get("veto_factor_codes"), label="veto_factor"),
            "execution_findings": execution_findings,
            "governance_review_suggestions": [],
            "unresolved_questions": [],
            "summary": summary,
        }
    )


def normalize_decision_review(payload: Mapping[str, Any]) -> dict[str, Any]:
    mode = str(payload.get("review_mode") or "async_light")
    status = str(payload.get("review_status") or payload.get("status") or "unavailable")
    normalized_status = status if status in REVIEW_STATUSES else "unavailable"
    suggestions = _sanitize_governance_suggestions(payload.get("governance_review_suggestions"))
    return {
        "available": normalized_status != "unavailable",
        "schema": SCHEMA,
        "version": VERSION,
        "generated_at": str(payload.get("generated_at") or ""),
        "source_run_id": str(payload.get("source_run_id") or ""),
        "handoff_id": str(payload.get("handoff_id") or ""),
        "source_handoff_age_sec": _none_or_int(payload.get("source_handoff_age_sec")),
        "source_stale_threshold_sec": _none_or_int(payload.get("source_stale_threshold_sec"), DEFAULT_STALE_THRESHOLD_SEC),
        "source_stale": bool(payload.get("source_stale", True)),
        "review_mode": mode if mode in REVIEW_MODES else "async_light",
        "review_status": normalized_status,
        "status": normalized_status,
        "latency_ms": _none_or_int(payload.get("latency_ms"), 0),
        "timeout": bool(payload.get("timeout", False)),
        "fallback_used": bool(payload.get("fallback_used", False)),
        "structured_fields_accepted": bool(payload.get("structured_fields_accepted", True)),
        "data_source_quality": _normalize_quality(payload.get("data_source_quality")),
        "bull_case": _normalize_finding_list(payload.get("bull_case")),
        "bear_case": _normalize_finding_list(payload.get("bear_case")),
        "risk_findings": _normalize_finding_list(payload.get("risk_findings")),
        "execution_findings": _normalize_finding_list(payload.get("execution_findings")),
        "governance_review_suggestions": suggestions,
        "unresolved_questions": _normalize_finding_list(payload.get("unresolved_questions")),
        "summary": str(payload.get("summary") or REVIEW_BOUNDARY_SUMMARY),
    }


def write_decision_review(path: Path, payload: Mapping[str, Any]) -> dict[str, Any]:
    normalized = normalize_decision_review(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8")
    return normalized


def write_governance_suggestions(path: Path, suggestions: Any) -> list[dict[str, Any]]:
    sanitized = _sanitize_governance_suggestions(suggestions)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sanitized, ensure_ascii=False, indent=2), encoding="utf-8")
    return sanitized


def build_daily_review(
    *,
    bot_root: Path,
    quant_root: Path,
    review_date: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = now or datetime.now(timezone.utc)
    normalized_date = review_date or current.date().isoformat()
    handoff, handoff_path = _read_latest_handoff(quant_root)
    worker_events = _tail_jsonl(bot_root / "runtime" / "real_order_worker" / "audit.jsonl", limit=200)
    outcome_summary = _read_json(quant_root / "runtime" / "analysis" / "decision_outcomes_summary.json")
    review = build_decision_review(bot_root=bot_root, quant_root=quant_root, now=current, review_mode="daily_integrity_review")
    return {
        "schema": "daily_runtime_review_v1",
        "version": VERSION,
        "generated_at": current.isoformat(),
        "review_date": normalized_date,
        "review_mode": "daily_integrity_review",
        "source_run_id": _resolve_source_run_id(handoff=handoff, handoff_path=handoff_path),
        "data_source_quality": review["data_source_quality"],
        "decision_review_status": review["review_status"],
        "worker_audit_event_count": len(worker_events),
        "worker_status_counts": _count_worker_statuses(worker_events),
        "outcome_summary_available": bool(outcome_summary),
        "resolved_outcome_count": int(outcome_summary.get("resolved_count") or 0) if outcome_summary else 0,
        "avg_net_return_pct": float(outcome_summary.get("avg_net_return_pct") or 0.0) if outcome_summary else 0.0,
        "stop_hit_rate": float(outcome_summary.get("stop_hit_rate") or 0.0) if outcome_summary else 0.0,
        "summary": "每日复盘只供审计和学习，不参与实时下单。",
    }


def write_daily_review(path: Path, payload: Mapping[str, Any]) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = dict(payload)
    normalized["version"] = VERSION
    path.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8")
    return normalized


def _sanitize_governance_suggestions(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    sanitized: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        dangerous = sorted(DANGEROUS_GOVERNANCE_SUGGESTION_FIELDS & {str(key) for key in item.keys()})
        if dangerous:
            sanitized.append(
                {
                    "factor_name": str(item.get("factor_name") or ""),
                    "source_run_id": str(item.get("source_run_id") or ""),
                    "suggested_action": "rejected_dangerous_fields",
                    "reason": "governance suggestion 包含禁止字段：" + ",".join(dangerous),
                    "actionable": False,
                }
            )
            continue
        sanitized.append(
            {
                "factor_name": str(item.get("factor_name") or ""),
                "source_run_id": str(item.get("source_run_id") or ""),
                "suggested_action": str(item.get("suggested_action") or "manual_governance_review"),
                "reason": str(item.get("reason") or ""),
                "actionable": False,
            }
        )
    return sanitized


def _normalize_quality(value: Any) -> dict[str, bool]:
    source = value if isinstance(value, Mapping) else {}
    defaults = {
        "handoff_available": False,
        "factor_lookup_available": False,
        "factor_summary_available": False,
        "risk_report_available": False,
        "candidate_package_available": False,
        "worker_audit_available": False,
        "outcome_samples_available": False,
    }
    return {key: bool(source.get(key, default)) for key, default in defaults.items()}


def _normalize_finding_list(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    rows: list[dict[str, str]] = []
    for item in value:
        if isinstance(item, Mapping):
            rows.append({"code": str(item.get("code") or item.get("name") or ""), "text": str(item.get("text") or item.get("reason") or "")})
        elif str(item):
            rows.append({"code": str(item), "text": ""})
    return rows


def _case_from_codes(value: Any, *, label: str) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    return [{"code": str(code), "text": label} for code in value[:8] if str(code)]


def _read_latest_handoff(quant_root: Path) -> tuple[dict[str, Any], Path | None]:
    candidates = [
        quant_root / "runtime" / "cycles" / "latest_strict_live" / "handoff.json",
        quant_root / "runtime" / "cycles" / "latest_strict_live" / "execution_handoff.json",
    ]
    cycles_root = quant_root / "runtime" / "cycles"
    try:
        cycle_dirs = sorted([path for path in cycles_root.iterdir() if path.is_dir()], key=lambda path: path.stat().st_mtime, reverse=True)
    except OSError:
        cycle_dirs = []
    for cycle_dir in cycle_dirs[:30]:
        candidates.extend([cycle_dir / "handoff.json", cycle_dir / "execution_handoff.json"])
    for path in candidates:
        payload = _read_json(path)
        if payload:
            return payload, path
    return {}, None


def _extract_risk_report(*, quant_root: Path, handoff: Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(handoff.get("risk_report"), Mapping):
        return dict(handoff["risk_report"])
    cycles_root = quant_root / "runtime" / "cycles"
    try:
        cycle_dirs = sorted([path for path in cycles_root.iterdir() if path.is_dir()], key=lambda path: path.stat().st_mtime, reverse=True)
    except OSError:
        return {}
    for cycle_dir in cycle_dirs[:30]:
        decision = _read_json(cycle_dir / "decision.json")
        payload = decision.get("decision") if isinstance(decision.get("decision"), Mapping) else decision
        risk = payload.get("risk_report") if isinstance(payload, Mapping) else None
        if isinstance(risk, Mapping):
            return dict(risk)
    return {}


def _resolve_source_run_id(*, handoff: Mapping[str, Any], handoff_path: Path | None) -> str:
    for key in ("source_run_id", "run_id"):
        if handoff.get(key):
            return str(handoff[key])
    if handoff_path is not None:
        return handoff_path.parent.name
    return ""


def _outcome_samples_available(quant_root: Path) -> bool:
    return (quant_root / "runtime" / "analysis" / "decision_outcomes_summary.json").exists()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        if not path.exists():
            return {}
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _tail_jsonl(path: Path, *, limit: int) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()[-limit:]
    except OSError:
        return []
    events: list[dict[str, Any]] = []
    for line in lines:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            events.append(payload)
    return events


def _count_worker_statuses(events: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for event in events:
        payload = event.get("payload") if isinstance(event.get("payload"), Mapping) else {}
        status = str(payload.get("status") or event.get("event_type") or "unknown")
        counts[status] = counts.get(status, 0) + 1
    return counts


def _age_seconds(value: str, *, now: datetime) -> int | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return max(0, int((now.astimezone(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds()))


def _none_or_int(value: Any, fallback: int | None = None) -> int | None:
    if value is None:
        return fallback
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback
