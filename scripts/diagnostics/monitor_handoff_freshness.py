from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCRIPT_PATH = Path(__file__).resolve()
for parent in SCRIPT_PATH.parents:
    if (parent / "src" / "bot").exists() and (parent / "scripts").exists():
        if str(parent) not in sys.path:
            sys.path.insert(0, str(parent))
        break

from scripts.path_utils import repo_root_from_script


DEFAULT_FACTOR_LOOKUP_MAX_AGE_SEC = 3 * 60 * 60
FACTOR_LOOKUP_FUTURE_TOLERANCE_SEC = 60
ALERT_EXIT_CODE = 2


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(list(argv) if argv is not None else None)
    payload = monitor_handoff_freshness(
        quant_root=Path(args.quant_root),
        max_age_sec=int(args.max_age_sec),
        now=datetime.now(UTC),
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if payload["status"] == "ok" else ALERT_EXIT_CODE


def monitor_handoff_freshness(
    *,
    quant_root: Path,
    max_age_sec: int | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    max_age = _factor_lookup_max_age_sec(max_age_sec)
    current = _ensure_utc(now or datetime.now(UTC))
    handoff_path = _latest_handoff_path(quant_root)
    reason_codes: list[str] = []
    if handoff_path is None:
        return _payload(
            status="alert",
            reason_codes=["handoff_missing"],
            quant_root=quant_root,
            handoff_path=None,
            max_age_sec=max_age,
            now=current,
        )
    handoff = _read_json(handoff_path)
    if not isinstance(handoff, Mapping):
        return _payload(
            status="alert",
            reason_codes=["handoff_unreadable"],
            quant_root=quant_root,
            handoff_path=handoff_path,
            max_age_sec=max_age,
            now=current,
        )
    generated_at = str(handoff.get("factor_lookup_generated_at") or "").strip()
    age_seconds = _age_seconds(generated_at, now=current)
    if age_seconds is None:
        reason_codes.append("handoff_freshness_unknown")
    else:
        if age_seconds > max_age:
            reason_codes.append("factor_lookup_age_over_threshold")
        if age_seconds < -FACTOR_LOOKUP_FUTURE_TOLERANCE_SEC:
            reason_codes.append("factor_lookup_generated_at_in_future")
    producer_stale = bool(handoff.get("factor_lookup_stale", False))
    computed_stale = age_seconds is None or bool(reason_codes)
    if producer_stale:
        reason_codes.append("factor_lookup_stale")
    if not producer_stale and computed_stale:
        reason_codes.append("factor_lookup_stale_flag_conflict")
    if bool(handoff.get("scoring_chain_frozen", False)):
        reason_codes.append("scoring_chain_frozen")
    return _payload(
        status="alert" if reason_codes else "ok",
        reason_codes=list(dict.fromkeys(reason_codes)),
        quant_root=quant_root,
        handoff_path=handoff_path,
        max_age_sec=max_age,
        now=current,
        factor_lookup_generated_at=generated_at,
        factor_lookup_age_seconds=age_seconds,
        factor_lookup_stale=bool(producer_stale or computed_stale),
        factor_lookup_producer_stale=producer_stale,
        scoring_chain_frozen=bool(handoff.get("scoring_chain_frozen", False)),
    )


def _build_parser() -> argparse.ArgumentParser:
    bot_root = repo_root_from_script(__file__)
    parser = argparse.ArgumentParser(description="Read-only alert check for quant handoff factor lookup freshness.")
    parser.add_argument("--quant-root", default=(bot_root.parent / "quant_system_rebuild").as_posix())
    parser.add_argument("--max-age-sec", type=int, default=_factor_lookup_max_age_sec(None))
    return parser


def _latest_handoff_path(quant_root: Path) -> Path | None:
    cycles_root = quant_root / "runtime" / "cycles"
    pinned_roots = [
        cycles_root / "latest_strict_live",
        cycles_root / "latest_strict_live_after_research_refresh",
        cycles_root / "latest_strict_live_research_impact_check",
    ]
    for root in pinned_roots:
        for name in ("handoff.json", "execution_handoff.json"):
            candidate = root / name
            if candidate.exists():
                return candidate
    try:
        cycle_dirs = [path for path in cycles_root.iterdir() if path.is_dir()]
    except OSError:
        return None
    cycle_dirs.sort(key=_cycle_sort_timestamp, reverse=True)
    for cycle_dir in cycle_dirs:
        for name in ("handoff.json", "execution_handoff.json"):
            candidate = cycle_dir / name
            if candidate.exists():
                return candidate
    return None


def _cycle_sort_timestamp(path: Path) -> float:
    for name in ("handoff.json", "execution_handoff.json", "scheduler_status.json", "decision.json"):
        timestamp = _json_generated_timestamp(path / name)
        if timestamp is not None:
            return timestamp
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _json_generated_timestamp(path: Path) -> float | None:
    payload = _read_json(path)
    if not isinstance(payload, Mapping):
        return None
    for key in ("generated_at", "finished_at", "started_at"):
        parsed = _parse_utc_datetime(payload.get(key))
        if parsed is not None:
            return parsed.timestamp()
    return None


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _age_seconds(generated_at: str, *, now: datetime) -> float | None:
    generated = _parse_utc_datetime(generated_at)
    if generated is None:
        return None
    return round((now - generated).total_seconds(), 3)


def _parse_utc_datetime(value: Any) -> datetime | None:
    if value is None or not str(value).strip():
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return _ensure_utc(parsed)


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _factor_lookup_max_age_sec(value: int | None) -> int:
    if value is not None:
        return max(0, int(value))
    raw = os.environ.get("HANDOFF_MONITOR_FACTOR_LOOKUP_MAX_AGE_SEC") or os.environ.get("FACTOR_LOOKUP_MAX_AGE_SEC")
    if not raw:
        return DEFAULT_FACTOR_LOOKUP_MAX_AGE_SEC
    try:
        return max(0, int(float(raw)))
    except ValueError:
        return DEFAULT_FACTOR_LOOKUP_MAX_AGE_SEC


def _payload(
    *,
    status: str,
    reason_codes: list[str],
    quant_root: Path,
    handoff_path: Path | None,
    max_age_sec: int,
    now: datetime,
    factor_lookup_generated_at: str = "",
    factor_lookup_age_seconds: float | None = None,
    factor_lookup_stale: bool = True,
    factor_lookup_producer_stale: bool = False,
    scoring_chain_frozen: bool = False,
) -> dict[str, Any]:
    return {
        "generated_at": now.isoformat(),
        "status": status,
        "reason_codes": reason_codes,
        "quant_root": quant_root.as_posix(),
        "handoff_path": handoff_path.as_posix() if handoff_path is not None else "",
        "max_age_sec": max_age_sec,
        "factor_lookup_generated_at": factor_lookup_generated_at,
        "factor_lookup_age_seconds": factor_lookup_age_seconds,
        "factor_lookup_stale": factor_lookup_stale,
        "factor_lookup_producer_stale": factor_lookup_producer_stale,
        "scoring_chain_frozen": scoring_chain_frozen,
    }


if __name__ == "__main__":
    raise SystemExit(main())
