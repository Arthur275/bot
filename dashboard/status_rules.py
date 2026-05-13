from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def parse_dt(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed.astimezone(timezone.utc)


def age_seconds(value: Any, *, now: datetime | None = None) -> int | None:
    parsed = parse_dt(value)
    if parsed is None:
        return None
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return max(0, int((current.astimezone(timezone.utc) - parsed).total_seconds()))


def runtime_status(*, generated_at: Any, ok: bool, stale_after_sec: int, error: str = "") -> dict[str, Any]:
    age = age_seconds(generated_at)
    if error:
        level = "red"
        label = "ERROR"
    elif not ok:
        level = "red"
        label = "BLOCKED"
    elif age is None:
        level = "gray"
        label = "UNKNOWN"
    elif age > stale_after_sec:
        level = "yellow"
        label = "STALE"
    else:
        level = "green"
        label = "RUNNING"
    return {"label": label, "level": level, "age_sec": age}


def lookup_status(*, generated_at: Any, stale: bool = False, stale_after_sec: int = 72 * 3600) -> dict[str, Any]:
    age = age_seconds(generated_at)
    if stale:
        return {"label": "STALE", "level": "yellow", "age_sec": age}
    if age is None:
        return {"label": "UNKNOWN", "level": "gray", "age_sec": None}
    if age > stale_after_sec:
        return {"label": "STALE", "level": "yellow", "age_sec": age}
    return {"label": "FRESH", "level": "green", "age_sec": age}


def kill_switch_status(*, enabled: bool) -> dict[str, Any]:
    return {
        "label": "ON" if enabled else "OFF",
        "level": "red" if enabled else "green",
        "enabled": enabled,
    }
