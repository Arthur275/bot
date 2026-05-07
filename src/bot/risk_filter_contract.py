from __future__ import annotations

from typing import Literal


RiskFilterExecutionClass = Literal["pass", "degraded", "blocked"]

PASS_STATUSES = {"pass"}
DEGRADED_STATUSES = {"degraded"}
BLOCKED_STATUSES = {"veto", "blocked", "unavailable", "research_unavailable"}


def classify_risk_filter_status(value: object) -> RiskFilterExecutionClass:
    normalized = str(value or "").strip().lower()
    if normalized in PASS_STATUSES:
        return "pass"
    if normalized in DEGRADED_STATUSES:
        return "degraded"
    return "blocked"


def risk_filter_allows_real_entry(value: object) -> bool:
    return classify_risk_filter_status(value) == "pass"


def risk_filter_allows_signal_tracking(value: object) -> bool:
    return classify_risk_filter_status(value) in {"pass", "degraded"}


def risk_filter_reason_code(value: object) -> str:
    normalized = str(value or "").strip().lower() or "unknown"
    return f"risk_filter:{normalized}"
