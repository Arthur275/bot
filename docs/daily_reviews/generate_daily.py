from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any


DEFAULT_SYMBOL = "ETHUSDT"
DEFAULT_TIMEFRAME = "15m"
DEFAULT_PROXY_URL = "http://127.0.0.1:7897"
ALLOWED_MARKET_SOURCES = {"auto", "binance_api", "local", "none"}
_CYCLE_TS_RE = re.compile(r"(\d{8}T\d{6}Z)")


@dataclass(frozen=True)
class ReviewWindow:
    target_date: date
    start: datetime
    end: datetime
    start_key: str
    end_key: str
    ymd: str


@dataclass
class InputRecord:
    name: str
    status: str
    source: str
    trace: str
    path: str = ""

    def as_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "status": self.status,
            "source": self.source,
            "trace": self.trace,
            "path": self.path,
        }


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.market_source not in ALLOWED_MARKET_SOURCES:
        parser.error(f"--market-source must be one of {sorted(ALLOWED_MARKET_SOURCES)}")

    result = generate_daily_review(args)
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate a daily market-vs-system review draft.")
    parser.add_argument("--date", default="", help="Target UTC date, YYYY-MM-DD. Defaults to yesterday UTC.")
    parser.add_argument("--symbol", default=DEFAULT_SYMBOL)
    parser.add_argument("--timeframe", default=DEFAULT_TIMEFRAME)
    parser.add_argument("--bot-root", default=str(_default_bot_root()))
    parser.add_argument("--quant-root", default="")
    parser.add_argument("--output-dir", default="")
    parser.add_argument("--reviewer", default="")
    parser.add_argument("--market-source", default="auto")
    parser.add_argument("--proxy-url", default=os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY") or DEFAULT_PROXY_URL)
    parser.add_argument("--force-regenerate", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--item-limit", type=int, default=200)
    return parser


def generate_daily_review(args: argparse.Namespace) -> dict[str, Any]:
    bot_root = Path(args.bot_root).resolve()
    quant_root = Path(args.quant_root).resolve() if args.quant_root else _default_quant_root(bot_root)
    output_dir = Path(args.output_dir).resolve() if args.output_dir else bot_root / "docs" / "daily_reviews"
    artifacts_dir = output_dir / "artifacts"
    window = _review_window(args.date)
    cycle_symbol = _cycle_symbol(args.symbol)

    audit_payload, audit_input = _load_or_build_missed_audit(
        quant_root=quant_root,
        local_artifacts_dir=artifacts_dir,
        window=window,
        symbol=cycle_symbol,
        timeframe=args.timeframe,
        force_regenerate=bool(args.force_regenerate),
        item_limit=int(args.item_limit),
        dry_run=bool(args.dry_run),
    )
    shadow_payload, shadow_input = _load_or_build_shadow_outcomes(
        quant_root=quant_root,
        local_artifacts_dir=artifacts_dir,
        window=window,
        audit_payload=audit_payload,
        audit_input=audit_input,
        force_regenerate=bool(args.force_regenerate),
        dry_run=bool(args.dry_run),
    )
    diagnostics_payload, diagnostics_input = _load_live_ready_diagnostics(quant_root=quant_root, window=window)
    scheduler_payload, scheduler_input = _load_scheduler_daily_review(quant_root=quant_root, window=window)
    market_payload, market_input = _load_market_summary(
        quant_root=quant_root,
        window=window,
        symbol=args.symbol,
        market_source=args.market_source,
        proxy_url=args.proxy_url,
    )

    inputs = [market_input, audit_input, diagnostics_input, scheduler_input, shadow_input]
    system_health = _build_system_health(diagnostics_payload=diagnostics_payload, diagnostics_input=diagnostics_input, audit_payload=audit_payload)
    timeline = _build_timeline(audit_payload=audit_payload, market_payload=market_payload)
    gate_layers = _build_gate_layers(
        audit_payload=audit_payload,
        diagnostics_payload=diagnostics_payload,
        diagnostics_input=diagnostics_input,
    )
    difference_categories = _build_difference_categories(audit_payload=audit_payload, shadow_payload=shadow_payload)
    public_market_payload = _public_market_payload(market_payload)
    summary = {
        "date": window.target_date.isoformat(),
        "symbol": args.symbol,
        "timeframe": args.timeframe,
        "generated_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "reviewer": args.reviewer,
        "bot_root": str(bot_root),
        "quant_root": str(quant_root),
        "inputs": [record.as_dict() for record in inputs],
        "system_health": system_health,
        "market": public_market_payload,
        "gate_layers": gate_layers,
        "difference_categories": difference_categories,
        "timeline": timeline,
        "output_paths": {
            "markdown": str(output_dir / f"{window.target_date.isoformat()}_market_vs_system.md"),
            "summary_json": str(artifacts_dir / f"{window.target_date.isoformat()}_summary.json"),
        },
    }
    markdown = render_markdown(summary)

    if not args.dry_run:
        output_dir.mkdir(parents=True, exist_ok=True)
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        Path(summary["output_paths"]["markdown"]).write_text(markdown, encoding="utf-8")
        Path(summary["output_paths"]["summary_json"]).write_text(
            json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    return {"summary": summary, "markdown": markdown}


def _load_or_build_missed_audit(
    *,
    quant_root: Path,
    local_artifacts_dir: Path,
    window: ReviewWindow,
    symbol: str,
    timeframe: str,
    force_regenerate: bool,
    item_limit: int,
    dry_run: bool,
) -> tuple[dict[str, Any], InputRecord]:
    analysis_dir = quant_root / "runtime" / "analysis"
    path = analysis_dir / f"missed_opportunity_audit_{window.ymd}.json"
    local_path = local_artifacts_dir / f"missed_opportunity_audit_{window.ymd}.json"
    if not force_regenerate:
        for candidate_path, source_name in ((path, "existing_artifact"), (local_path, "existing_local_artifact")):
            if not candidate_path.exists():
                continue
            payload = _read_json(candidate_path)
            if _audit_is_fresh(payload, window=window, symbol=symbol, timeframe=timeframe):
                return payload, InputRecord(
                    name="missed_opportunity_audit",
                    status="ok",
                    source=source_name,
                    trace=_audit_trace(payload, candidate_path),
                    path=str(candidate_path),
                )

    if dry_run:
        status = "regenerate_planned" if force_regenerate or not path.exists() else "stale"
        return {}, InputRecord(
            name="missed_opportunity_audit",
            status=status,
            source="dry_run",
            trace=f"expected_path={path}",
            path=str(path),
        )

    try:
        _ensure_quant_src(quant_root)
        from analysis.missed_opportunity_audit import build_missed_opportunity_audit, render_missed_opportunity_audit_markdown

        payload = build_missed_opportunity_audit(
            cycles_root=quant_root / "runtime" / "cycles",
            factor_lookup_path=_existing_or_none(analysis_dir / "factor_lookup_summary.json"),
            factor_governance_path=_existing_or_none(analysis_dir / "factor_governance_scan_review_summary.json"),
            symbol=symbol,
            timeframe=timeframe,
            start_ts=window.start_key,
            end_ts=window.end_key,
            include_snapshot_only=True,
            item_limit=item_limit,
        )
        markdown = render_missed_opportunity_audit_markdown(payload)
        written_path, written_source = _write_analysis_artifact_with_fallback(
            primary_path=path,
            fallback_path=local_path,
            payload=payload,
            markdown=markdown,
        )
        return payload, InputRecord(
            name="missed_opportunity_audit",
            status="regenerated" if written_source == "builder" else "regenerated_local",
            source=written_source,
            trace=_audit_trace(payload, written_path),
            path=str(written_path),
        )
    except Exception as exc:
        return {}, InputRecord(
            name="missed_opportunity_audit",
            status="missing",
            source="builder_error",
            trace=f"{exc.__class__.__name__}: {exc}; expected_path={path}",
            path=str(path),
        )


def _load_or_build_shadow_outcomes(
    *,
    quant_root: Path,
    local_artifacts_dir: Path,
    window: ReviewWindow,
    audit_payload: dict[str, Any],
    audit_input: InputRecord,
    force_regenerate: bool,
    dry_run: bool,
) -> tuple[dict[str, Any], InputRecord]:
    analysis_dir = quant_root / "runtime" / "analysis"
    path = analysis_dir / f"missed_opportunity_shadow_outcomes_{window.ymd}.json"
    local_path = local_artifacts_dir / f"missed_opportunity_shadow_outcomes_{window.ymd}.json"
    if not force_regenerate:
        for candidate_path, source_name in ((path, "existing_artifact"), (local_path, "existing_local_artifact")):
            if not candidate_path.exists():
                continue
            payload = _read_json(candidate_path)
            if _shadow_is_fresh(payload, audit_path=audit_input.path, window=window):
                return payload, InputRecord(
                    name="shadow_outcomes",
                    status="ok",
                    source=source_name,
                    trace=_shadow_trace(payload, candidate_path),
                    path=str(candidate_path),
                )

    if not audit_payload or not audit_input.path:
        return {}, InputRecord(
            name="shadow_outcomes",
            status="skipped",
            source="missing_audit",
            trace=f"expected_path={path}",
            path=str(path),
        )
    if dry_run:
        return {}, InputRecord(
            name="shadow_outcomes",
            status="regenerate_planned" if force_regenerate else "missing",
            source="dry_run",
            trace=f"expected_path={path}; audit_path={audit_input.path}",
            path=str(path),
        )

    try:
        _ensure_quant_src(quant_root)
        from analysis.missed_opportunity_shadow_outcomes import (
            build_missed_opportunity_shadow_outcomes,
            render_missed_opportunity_shadow_outcomes_markdown,
        )

        payload = build_missed_opportunity_shadow_outcomes(
            audit_path=audit_input.path,
            cycles_root=quant_root / "runtime" / "cycles",
            item_limit=100,
        )
        markdown = render_missed_opportunity_shadow_outcomes_markdown(payload)
        written_path, written_source = _write_analysis_artifact_with_fallback(
            primary_path=path,
            fallback_path=local_path,
            payload=payload,
            markdown=markdown,
        )
        return payload, InputRecord(
            name="shadow_outcomes",
            status="regenerated" if written_source == "builder" else "regenerated_local",
            source=written_source,
            trace=_shadow_trace(payload, written_path),
            path=str(written_path),
        )
    except Exception as exc:
        return {}, InputRecord(
            name="shadow_outcomes",
            status="skipped",
            source="builder_error",
            trace=f"{exc.__class__.__name__}: {exc}; audit_path={audit_input.path}",
            path=str(path),
        )


def _load_live_ready_diagnostics(*, quant_root: Path, window: ReviewWindow) -> tuple[dict[str, Any], InputRecord]:
    path = quant_root / "runtime" / "analysis" / "live_ready_blocking_diagnostics.json"
    if not path.exists():
        return {}, InputRecord("live_ready_diagnostics", "missing", "missing", f"expected_path={path}", str(path))
    payload = _read_json(path)
    cycle_ts = _diagnostics_cycle_ts(payload)
    status = "ok" if cycle_ts.startswith(window.ymd) else "stale"
    return payload, InputRecord(
        name="live_ready_diagnostics",
        status=status,
        source="existing_artifact",
        trace=f"source_path={path}; decision_cycle_ts={cycle_ts or 'unknown'}",
        path=str(path),
    )


def _load_scheduler_daily_review(*, quant_root: Path, window: ReviewWindow) -> tuple[dict[str, Any], InputRecord]:
    path = quant_root / "runtime" / "scheduler" / "daily_review.json"
    if not path.exists():
        return {}, InputRecord("scheduler_daily_review", "missing", "missing", f"expected_path={path}", str(path))
    payload = _read_json(path)
    generated_at = str(payload.get("generated_at") or "")
    generated_date = _parse_date_from_iso(generated_at)
    status = "ok" if generated_date == window.target_date else "stale"
    return payload, InputRecord(
        name="scheduler_daily_review",
        status=status,
        source="existing_artifact",
        trace=f"source_path={path}; generated_at={generated_at or 'unknown'}",
        path=str(path),
    )


def _load_market_summary(
    *,
    quant_root: Path,
    window: ReviewWindow,
    symbol: str,
    market_source: str,
    proxy_url: str,
) -> tuple[dict[str, Any], InputRecord]:
    if market_source == "none":
        payload = _empty_market_payload(symbol=symbol, window=window, status="unavailable", source="none")
        return payload, InputRecord("market_data", "unavailable", "none", "market_source=none")

    if market_source in {"auto", "binance_api"}:
        try:
            klines_15m = _fetch_binance_klines(symbol=symbol, interval="15m", window=window, proxy_url=proxy_url)
            klines_1h = _fetch_binance_klines(symbol=symbol, interval="1h", window=window, proxy_url=proxy_url)
            payload = _summarize_klines(symbol=symbol, window=window, klines_15m=klines_15m, klines_1h=klines_1h, source="binance_api")
            return payload, InputRecord(
                name="market_data",
                status="ok",
                source="binance_api",
                trace=(
                    f"count={payload.get('candle_count_15m', 0)} 15m candles, "
                    f"window={window.start.isoformat()}..{window.end.isoformat()}"
                ),
            )
        except Exception as exc:
            if market_source == "binance_api":
                payload = _empty_market_payload(symbol=symbol, window=window, status="unavailable", source="binance_api")
                return payload, InputRecord("market_data", "unavailable", "binance_api", f"{exc.__class__.__name__}: {exc}")

    local_payload, local_input = _load_local_market_sample(quant_root=quant_root, window=window, symbol=symbol)
    if local_payload:
        return local_payload, local_input
    payload = _empty_market_payload(symbol=symbol, window=window, status="unavailable", source="unavailable")
    return payload, InputRecord("market_data", "unavailable", "unavailable", "binance_api_failed; local_sample_missing")


def _fetch_binance_klines(*, symbol: str, interval: str, window: ReviewWindow, proxy_url: str) -> list[list[Any]]:
    params = {
        "symbol": _market_symbol(symbol),
        "interval": interval,
        "startTime": int(window.start.timestamp() * 1000),
        "endTime": int(window.end.timestamp() * 1000) - 1,
        "limit": 1000,
    }
    url = "https://api.binance.com/api/v3/klines?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={"User-Agent": "daily-market-vs-system-review/1.0"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({"https": proxy_url, "http": proxy_url})) if proxy_url else urllib.request.build_opener()
    try:
        with opener.open(request, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(f"binance_api_unavailable:{exc}") from exc
    if not isinstance(payload, list):
        raise RuntimeError("binance_api_unexpected_payload")
    return payload


def _load_local_market_sample(*, quant_root: Path, window: ReviewWindow, symbol: str) -> tuple[dict[str, Any], InputRecord]:
    candidates: list[Path] = []
    for root in [quant_root / "runtime", quant_root / "tests" / "fixtures", quant_root / "claw数据测试", quant_root / "archive"]:
        if not root.exists():
            continue
        for pattern in ("*kline*.json", "*klines*.json"):
            candidates.extend(root.rglob(pattern))
            if len(candidates) > 50:
                break
        if candidates:
            break
    market_symbol = _market_symbol(symbol).lower()
    for path in candidates[:50]:
        if market_symbol not in path.name.lower() and _cycle_symbol(symbol) not in path.name.lower():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        klines = payload if isinstance(payload, list) else payload.get("klines") if isinstance(payload, dict) else None
        if not isinstance(klines, list) or not klines:
            continue
        summary = _summarize_klines(symbol=symbol, window=window, klines_15m=klines, klines_1h=[], source="local_sample")
        summary["market_data_status"] = "stale"
        return summary, InputRecord(
            name="market_data",
            status="stale",
            source="local_sample",
            trace=f"source_path={path}; sample may not cover target window",
            path=str(path),
        )
    return {}, InputRecord("market_data", "unavailable", "local_sample", "local_sample_missing")


def _summarize_klines(
    *,
    symbol: str,
    window: ReviewWindow,
    klines_15m: list[list[Any]],
    klines_1h: list[list[Any]],
    source: str,
) -> dict[str, Any]:
    bars = [bar for row in klines_15m if (bar := _parse_kline(row)) and window.start <= bar["open_time"] < window.end]
    bars_1h = [bar for row in klines_1h if (bar := _parse_kline(row)) and window.start <= bar["open_time"] < window.end]
    if not bars:
        return _empty_market_payload(symbol=symbol, window=window, status="unavailable", source=source)
    open_price = bars[0]["open"]
    close_price = bars[-1]["close"]
    high_bar = max(bars, key=lambda bar: bar["high"])
    low_bar = min(bars, key=lambda bar: bar["low"])
    high = float(high_bar["high"])
    low = float(low_bar["low"])
    day_return_pct = _pct(close_price - open_price, open_price)
    intraday_range_pct = _pct(high - low, open_price)
    max_15m_range_pct = max(_pct(bar["high"] - bar["low"], bar["open"]) for bar in bars)
    return {
        "market_data_status": "ok",
        "source": source,
        "symbol": _market_symbol(symbol),
        "date_utc": window.target_date.isoformat(),
        "candle_count_15m": len(bars),
        "candle_count_1h": len(bars_1h),
        "open": round(open_price, 8),
        "high": round(high, 8),
        "low": round(low, 8),
        "close": round(close_price, 8),
        "day_return_pct": round(day_return_pct, 4),
        "intraday_range_pct": round(intraday_range_pct, 4),
        "max_15m_range_pct": round(max_15m_range_pct, 4),
        "high_time": _format_hhmm(high_bar["open_time"]),
        "low_time": _format_hhmm(low_bar["open_time"]),
        "main_regime": _market_regime(day_return_pct=day_return_pct, intraday_range_pct=intraday_range_pct),
        "notable_windows": [
            f"high@{_format_hhmm(high_bar['open_time'])}",
            f"low@{_format_hhmm(low_bar['open_time'])}",
        ],
        "bars_15m": bars,
    }


def _public_market_payload(payload: dict[str, Any]) -> dict[str, Any]:
    public = dict(payload)
    bars = public.pop("bars_15m", [])
    public["bar_count_for_timeline"] = len(bars) if isinstance(bars, list) else 0
    return public


def _build_system_health(
    *,
    diagnostics_payload: dict[str, Any],
    diagnostics_input: InputRecord,
    audit_payload: dict[str, Any],
) -> dict[str, Any]:
    gates = diagnostics_payload.get("gates") if isinstance(diagnostics_payload.get("gates"), dict) else {}
    dominant = str(diagnostics_payload.get("dominant_blocking_reason") or "")
    if diagnostics_input.status == "stale":
        audit_reason = _dominant_audit_reason(audit_payload)
        return {
            "system_health": "unknown",
            "dominant_blocking_reason": f"diagnostics_stale; audit_top_reason={audit_reason}",
            "primary_fault_domain": _fault_domain_from_audit(audit_payload),
            "diagnostics_status": "stale",
            "stale_diagnostics_reference": dominant or "none",
            "gates": gates,
        }
    if diagnostics_payload:
        fault_domain = _fault_domain_from_gates(gates=gates, dominant=dominant)
        return {
            "system_health": "degraded" if diagnostics_payload.get("status") in {"blocked", "degraded"} else str(diagnostics_payload.get("status") or "unknown"),
            "dominant_blocking_reason": dominant or "none",
            "primary_fault_domain": fault_domain,
            "gates": gates,
        }
    category_counts = audit_payload.get("category_counts") if isinstance(audit_payload.get("category_counts"), dict) else {}
    if int(category_counts.get("observability_gap", 0)) > 0:
        domain = "unknown"
    elif int(category_counts.get("reasonable_risk_control", 0)) > 0:
        domain = "risk_control_block"
    else:
        domain = "strategy_wait"
    return {
        "system_health": "unknown",
        "dominant_blocking_reason": "diagnostics_missing",
        "primary_fault_domain": domain,
        "gates": {},
    }


def _build_timeline(*, audit_payload: dict[str, Any], market_payload: dict[str, Any]) -> list[dict[str, Any]]:
    items = [item for item in audit_payload.get("items", []) if isinstance(item, dict)]
    items.sort(key=lambda item: str(item.get("cycle_ts") or item.get("cycle_id") or ""))
    groups: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for item in items:
        key = (
            str(item.get("action") or ""),
            bool(item.get("trigger_ready")),
            str(item.get("risk_filter_status") or ""),
            _dominant_item_gate(item),
            str(item.get("category") or ""),
        )
        if current and current["key"] == key:
            current["items"].append(item)
            continue
        current = {"key": key, "items": [item]}
        groups.append(current)

    rows = []
    for group in groups[:80]:
        group_items = group["items"]
        first = group_items[0]
        last = group_items[-1]
        first_ts = str(first.get("cycle_ts") or "")
        last_ts = str(last.get("cycle_ts") or "")
        time_text = _timeline_time_range(first_ts, last_ts, len(group_items))
        rows.append(
            {
                "time_utc": time_text,
                "market": _market_at_cycle(market_payload=market_payload, cycle_ts=first_ts),
                "system": _system_timeline_text(first, len(group_items)),
                "trigger": str(bool(first.get("trigger_ready"))).lower(),
                "risk": str(first.get("risk_filter_status") or "unknown"),
                "gate": _dominant_item_gate(first),
                "cycle_count": len(group_items),
                "event_review_label": "needs_review",
                "category": str(first.get("category") or ""),
                "cycle_id": str(first.get("cycle_id") or ""),
            }
        )
    return rows


def _build_gate_layers(
    *,
    audit_payload: dict[str, Any],
    diagnostics_payload: dict[str, Any],
    diagnostics_input: InputRecord,
) -> list[dict[str, str]]:
    summary = audit_payload.get("summary") if isinstance(audit_payload.get("summary"), dict) else {}
    gates = diagnostics_payload.get("gates") if isinstance(diagnostics_payload.get("gates"), dict) else {}
    quant_status = "execution_ready_seen" if int(summary.get("execution_ready_count", 0)) else "no_execution_candidate"
    if int(summary.get("trigger_ready_count", 0)) > 0 and quant_status == "no_execution_candidate":
        quant_status = "trigger_seen_no_execution"
    if diagnostics_input.status == "stale":
        top_reasons = _top_count_dict(audit_payload.get("reason_counts"), limit=8)
        audit_evidence = ",".join(str(reason) for reason in top_reasons) or "audit_summary_unavailable"
        return [
            {
                "layer": "quant",
                "status": quant_status,
                "evidence": f"trigger_ready_count={summary.get('trigger_ready_count', 0)}, execution_ready_count={summary.get('execution_ready_count', 0)}",
                "reason": "audit_summary",
            },
            {
                "layer": "risk_gate",
                "status": "unknown_stale_diagnostics",
                "evidence": audit_evidence,
                "reason": "diagnostics_stale_not_used_as_target_day_conclusion",
            },
            {
                "layer": "bot_order",
                "status": "unknown_stale_diagnostics",
                "evidence": audit_evidence,
                "reason": "diagnostics_stale_not_used_as_target_day_conclusion",
            },
        ]
    risk_gate = gates.get("risk_filter") if isinstance(gates.get("risk_filter"), dict) else {}
    research_gate = gates.get("research_gate") if isinstance(gates.get("research_gate"), dict) else {}
    candidate_gate = gates.get("candidate_package") if isinstance(gates.get("candidate_package"), dict) else {}
    execution_guard = gates.get("execution_guard") if isinstance(gates.get("execution_guard"), dict) else {}
    diag_suffix = "stale diagnostics" if diagnostics_input.status == "stale" else "diagnostics"
    return [
        {
            "layer": "quant",
            "status": quant_status,
            "evidence": f"trigger_ready_count={summary.get('trigger_ready_count', 0)}, execution_ready_count={summary.get('execution_ready_count', 0)}",
            "reason": "audit_summary",
        },
        {
            "layer": "risk_gate",
            "status": str(risk_gate.get("status") or research_gate.get("status") or "unknown"),
            "evidence": _join_reason_codes(risk_gate.get("reason_codes") or research_gate.get("reason_codes")),
            "reason": diag_suffix,
        },
        {
            "layer": "bot_order",
            "status": str(candidate_gate.get("status") or execution_guard.get("status") or "not_reached"),
            "evidence": _join_reason_codes(candidate_gate.get("reason_codes") or execution_guard.get("reason_codes")),
            "reason": diag_suffix,
        },
    ]


def _build_difference_categories(*, audit_payload: dict[str, Any], shadow_payload: dict[str, Any]) -> dict[str, Any]:
    category_counts = audit_payload.get("category_counts") if isinstance(audit_payload.get("category_counts"), dict) else {}
    reason_counts = audit_payload.get("reason_counts") if isinstance(audit_payload.get("reason_counts"), dict) else {}
    shadow_summary = {}
    if shadow_payload:
        primary = shadow_payload.get("primary_summary") if isinstance(shadow_payload.get("primary_summary"), dict) else {}
        summary = shadow_payload.get("summary") if isinstance(shadow_payload.get("summary"), dict) else {}
        shadow_summary = {
            "status": str(shadow_payload.get("status") or "unknown"),
            "logic_gap_count": int(summary.get("logic_gap_count", 0)),
            "evaluated_item_count": int(summary.get("evaluated_item_count", 0)),
            "unique_net_favorable_rate": float(primary.get("unique_net_favorable_rate", 0.0)),
            "unique_available_count": int(primary.get("unique_available_count", 0)),
        }
    return {
        "category_counts": category_counts,
        "top_reasons": _top_count_dict(reason_counts, limit=12),
        "shadow_outcomes": shadow_summary,
    }


def render_markdown(summary: dict[str, Any]) -> str:
    lines = [
        f"# Daily Market vs System Review - {summary['date']}",
        "",
        f"> Symbol: {summary['symbol']}  ",
        "> Timezone: UTC  ",
        f"> Reviewer: {summary.get('reviewer') or '...'}  ",
        f"> Generated at: {summary['generated_at']}",
        "",
        "## 0. 输入质量",
        "",
        "| Input | Status | Source | Trace |",
        "|---|---|---|---|",
    ]
    for record in summary["inputs"]:
        lines.append(f"| {record['name']} | {record['status']} | {record['source']} | {_md_cell(record['trace'])} |")

    health = summary["system_health"]
    lines.extend(
        [
            "",
            "## 1. 系统健康度诊断",
            "",
            f"- system_health: {health.get('system_health', 'unknown')}",
            f"- dominant_blocking_reason: {health.get('dominant_blocking_reason', 'unknown')}",
            f"- primary_fault_domain: {health.get('primary_fault_domain', 'unknown')}",
        ]
    )
    if health.get("diagnostics_status"):
        lines.append(f"- diagnostics_status: {health.get('diagnostics_status')}")
    if health.get("stale_diagnostics_reference"):
        lines.append(f"- stale_diagnostics_reference: {health.get('stale_diagnostics_reference')}")
    gates = health.get("gates") if isinstance(health.get("gates"), dict) else {}
    gate_prefix = "stale_reference:" if health.get("diagnostics_status") == "stale" else ""
    for name in ["research_gate", "factor_governance", "risk_filter", "execution_guard", "candidate_package"]:
        gate = gates.get(name) if isinstance(gates.get(name), dict) else {}
        status = gate.get("status", "unknown")
        lines.append(f"- {name}: {gate_prefix}{status} ({_join_reason_codes(gate.get('reason_codes'))})")

    market = summary["market"]
    lines.extend(
        [
            "",
            "## 2. 市场摘要",
            "",
            f"- market_data_status: {market.get('market_data_status', 'unknown')}",
            f"- open/high/low/close: {market.get('open', 'N/A')}/{market.get('high', 'N/A')}/{market.get('low', 'N/A')}/{market.get('close', 'N/A')}",
            f"- day_return_pct: {market.get('day_return_pct', 'N/A')}",
            f"- intraday_range_pct: {market.get('intraday_range_pct', 'N/A')}",
            f"- max_15m_range_pct: {market.get('max_15m_range_pct', 'N/A')}",
            f"- main_regime: {market.get('main_regime', 'unknown')}",
            f"- notable_windows: {_join_list(market.get('notable_windows'))}",
        ]
    )
    if market.get("market_data_status") != "ok":
        lines.append("- impact: cannot judge missed/correct market opportunity from price action")

    lines.extend(
        [
            "",
            "## 3. 决策时间线",
            "",
            "| Time UTC | Market | System | Trigger | Risk | Gate | Cycle Count | 事件级评价 |",
            "|---|---|---|---|---|---|---:|---|",
        ]
    )
    if summary["timeline"]:
        for row in summary["timeline"]:
            lines.append(
                "| "
                f"{_md_cell(row['time_utc'])} | {_md_cell(row['market'])} | {_md_cell(row['system'])} | "
                f"{_md_cell(row['trigger'])} | {_md_cell(row['risk'])} | {_md_cell(row['gate'])} | "
                f"{row['cycle_count']} | {row['event_review_label']} |"
            )
    else:
        lines.append("| none | unavailable@all_day | no audited items | unknown | unknown | unknown | 0 | needs_review |")

    lines.extend(
        [
            "",
            "## 4. 三层闸门穿透",
            "",
            "| Layer | Status | Evidence | Reason |",
            "|---|---|---|---|",
        ]
    )
    for row in summary["gate_layers"]:
        lines.append(f"| {row['layer']} | {_md_cell(row['status'])} | {_md_cell(row['evidence'])} | {_md_cell(row['reason'])} |")

    categories = summary["difference_categories"].get("category_counts") or {}
    meanings = {
        "logic_gap": "接近可交易，需要设计审查",
        "strategy_choice": "策略有意等待",
        "reasonable_risk_control": "风控合理拦截",
        "observability_gap": "证据不足，先修观测",
    }
    lines.extend(["", "## 5. 差异归类", "", "| Category | Count | Meaning |", "|---|---:|---|"])
    for name in ["logic_gap", "strategy_choice", "reasonable_risk_control", "observability_gap"]:
        lines.append(f"| {name} | {int(categories.get(name, 0))} | {meanings[name]} |")
    shadow = summary["difference_categories"].get("shadow_outcomes") or {}
    if shadow:
        rate = float(shadow.get("unique_net_favorable_rate", 0.0))
        lines.extend(
            [
                "",
                "| 来源 | 结果 | 样本 |",
                "|---|---|---|",
                (
                    f"| shadow_outcomes | net_favorable_rate={rate:.3f}, status={shadow.get('status', 'unknown')} | "
                    f"{shadow.get('evaluated_item_count', 0)} logic_gap samples evaluated |"
                ),
            ]
        )

    lines.extend(
        [
            "",
            "## 6. 当天总评",
            "",
            "- label: needs_review",
            "- rationale: manual review required",
            "- evidence: see timeline and audit artifacts above",
            "- review_confidence: low",
            "",
            "Allowed labels: `hit`, `missed`, `correct_wait`, `wrong_wait`, `saved`, `wrong_entry`, `system_blind`.",
            "",
            "## 7. 偏差检查",
            "",
            "- hindsight_bias_risk: needs_review",
            "- confirmation_bias_risk: needs_review",
            "- which conclusions came from pre-defined strategy rules: needs_review",
            "- which conclusions came from hindsight price action: needs_review",
            "- notes:",
            "",
            "## 8. 下一步",
            "",
            "| Type | Action | Owner | Priority |",
            "|---|---|---|---|",
            "| needs_review | Fill daily overall label and bias check | reviewer | P1 |",
        ]
    )
    return "\n".join(lines) + "\n"


def _audit_is_fresh(payload: dict[str, Any], *, window: ReviewWindow, symbol: str, timeframe: str) -> bool:
    inputs = payload.get("inputs") if isinstance(payload.get("inputs"), dict) else {}
    return (
        _normalize_cycle_ts(str(inputs.get("start_ts") or "")) == window.start_key
        and _normalize_cycle_ts(str(inputs.get("end_ts") or "")) == window.end_key
        and (not inputs.get("symbol") or str(inputs.get("symbol")).lower() == symbol.lower())
        and (not inputs.get("timeframe") or str(inputs.get("timeframe")).lower() == timeframe.lower())
    )


def _shadow_is_fresh(payload: dict[str, Any], *, audit_path: str, window: ReviewWindow) -> bool:
    inputs = payload.get("inputs") if isinstance(payload.get("inputs"), dict) else {}
    raw_path = str(inputs.get("audit_path") or "")
    return bool(raw_path) and (Path(raw_path).name == Path(audit_path).name or window.ymd in raw_path)


def _audit_trace(payload: dict[str, Any], path: Path) -> str:
    inputs = payload.get("inputs") if isinstance(payload.get("inputs"), dict) else {}
    return f"source_path={path}; start_ts={inputs.get('start_ts', '')}; end_ts={inputs.get('end_ts', '')}"


def _shadow_trace(payload: dict[str, Any], path: Path) -> str:
    inputs = payload.get("inputs") if isinstance(payload.get("inputs"), dict) else {}
    return f"source_path={path}; audit_path={inputs.get('audit_path', '')}"


def _review_window(raw_date: str) -> ReviewWindow:
    target = date.fromisoformat(raw_date) if raw_date else (datetime.now(UTC).date() - timedelta(days=1))
    start = datetime.combine(target, time.min, tzinfo=UTC)
    end = start + timedelta(days=1)
    return ReviewWindow(
        target_date=target,
        start=start,
        end=end,
        start_key=start.strftime("%Y%m%dT%H%M%SZ"),
        end_key=end.strftime("%Y%m%dT%H%M%SZ"),
        ymd=target.strftime("%Y%m%d"),
    )


def _default_bot_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _default_quant_root(bot_root: Path) -> Path:
    sibling = bot_root.parent / "quant_system_rebuild"
    return sibling if sibling.exists() else Path("D:/开发/quant_system_rebuild")


def _ensure_quant_src(quant_root: Path) -> None:
    src = str((quant_root / "src").resolve())
    if src not in sys.path:
        sys.path.insert(0, src)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _existing_or_none(path: Path) -> str | None:
    return str(path) if path.exists() else None


def _write_analysis_artifact_with_fallback(
    *,
    primary_path: Path,
    fallback_path: Path,
    payload: dict[str, Any],
    markdown: str,
) -> tuple[Path, str]:
    try:
        primary_path.parent.mkdir(parents=True, exist_ok=True)
        primary_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        primary_path.with_suffix(".md").write_text(markdown, encoding="utf-8")
        return primary_path, "builder"
    except OSError:
        fallback_path.parent.mkdir(parents=True, exist_ok=True)
        fallback_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        fallback_path.with_suffix(".md").write_text(markdown, encoding="utf-8")
        return fallback_path, "builder_local_fallback"


def _cycle_symbol(symbol: str) -> str:
    raw = str(symbol or "").upper()
    if "BTC" in raw:
        return "btc"
    return "eth"


def _market_symbol(symbol: str) -> str:
    raw = str(symbol or "").upper().replace("-", "")
    if "BTC" in raw:
        return "BTCUSDT"
    if "ETH" in raw:
        return "ETHUSDT"
    return raw or DEFAULT_SYMBOL


def _diagnostics_cycle_ts(payload: dict[str, Any]) -> str:
    inputs = payload.get("inputs") if isinstance(payload.get("inputs"), dict) else {}
    for value in inputs.values():
        match = _CYCLE_TS_RE.search(str(value or ""))
        if match:
            return match.group(1)
    return ""


def _parse_date_from_iso(value: str) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC).date()
    except ValueError:
        return None


def _normalize_cycle_ts(value: str) -> str:
    raw = str(value or "").strip()
    if re.fullmatch(r"\d{8}T\d{6}Z", raw):
        return raw
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    except ValueError:
        return raw


def _parse_kline(row: Any) -> dict[str, Any] | None:
    if not isinstance(row, list) or len(row) < 5:
        return None
    try:
        open_time = datetime.fromtimestamp(int(row[0]) / 1000, tz=UTC)
        return {
            "open_time": open_time,
            "open": float(row[1]),
            "high": float(row[2]),
            "low": float(row[3]),
            "close": float(row[4]),
        }
    except (TypeError, ValueError, OSError):
        return None


def _empty_market_payload(*, symbol: str, window: ReviewWindow, status: str, source: str) -> dict[str, Any]:
    return {
        "market_data_status": status,
        "source": source,
        "symbol": _market_symbol(symbol),
        "date_utc": window.target_date.isoformat(),
        "candle_count_15m": 0,
        "candle_count_1h": 0,
        "main_regime": "unavailable" if status == "unavailable" else "unknown",
        "notable_windows": [],
        "bars_15m": [],
    }


def _pct(numerator: float, denominator: float) -> float:
    return 0.0 if denominator == 0 else (float(numerator) / float(denominator)) * 100.0


def _market_regime(*, day_return_pct: float, intraday_range_pct: float) -> str:
    if intraday_range_pct >= 3.0:
        return "volatile"
    if day_return_pct >= 1.0:
        return "trend_up"
    if day_return_pct <= -1.0:
        return "trend_down"
    return "range"


def _market_at_cycle(*, market_payload: dict[str, Any], cycle_ts: str) -> str:
    if market_payload.get("market_data_status") != "ok":
        return "unavailable@all_day"
    cycle_dt = _cycle_ts_to_datetime(cycle_ts)
    bars = market_payload.get("bars_15m") if isinstance(market_payload.get("bars_15m"), list) else []
    if not cycle_dt or not bars:
        return f"{market_payload.get('main_regime', 'unknown')}@unknown"
    selected = None
    for bar in bars:
        if not isinstance(bar, dict):
            continue
        start = bar.get("open_time")
        if isinstance(start, datetime) and start <= cycle_dt < start + timedelta(minutes=15):
            selected = bar
            break
    if selected is None:
        selected = min(
            (bar for bar in bars if isinstance(bar, dict) and isinstance(bar.get("open_time"), datetime)),
            key=lambda bar: abs((bar["open_time"] - cycle_dt).total_seconds()),
            default=None,
        )
    if not selected:
        return f"{market_payload.get('main_regime', 'unknown')}@unknown"
    candle_range = _pct(selected["high"] - selected["low"], selected["open"])
    move = "up" if selected["close"] > selected["open"] else "down" if selected["close"] < selected["open"] else "flat"
    regime = _candle_regime(candle_range=candle_range, move=move, day_regime=str(market_payload.get("main_regime") or "unknown"))
    return f"{regime}@{_format_hhmm(selected['open_time'])}, range_pct={candle_range:.2f}, move={move}"


def _candle_regime(*, candle_range: float, move: str, day_regime: str) -> str:
    if candle_range >= 1.0 and move == "down":
        return "breakdown"
    if candle_range >= 1.0 and move == "up":
        return "breakout"
    return day_regime if day_regime in {"range", "trend_up", "trend_down", "volatile"} else "unknown"


def _cycle_ts_to_datetime(value: str) -> datetime | None:
    try:
        return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
    except ValueError:
        return None


def _format_hhmm(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%H:%M")


def _timeline_time_range(first_ts: str, last_ts: str, count: int) -> str:
    first = _cycle_ts_to_datetime(first_ts)
    last = _cycle_ts_to_datetime(last_ts)
    if not first:
        return "unknown"
    if count <= 1 or not last or first == last:
        return _format_hhmm(first)
    return f"{_format_hhmm(first)}-{_format_hhmm(last)}"


def _dominant_item_gate(item: dict[str, Any]) -> str:
    block = str(item.get("execution_block_reason") or "")
    if block:
        return block
    reasons = item.get("reason_codes") if isinstance(item.get("reason_codes"), list) else []
    if reasons:
        return str(reasons[0])
    risk_reasons = item.get("risk_reason_codes") if isinstance(item.get("risk_reason_codes"), list) else []
    if risk_reasons:
        return str(risk_reasons[0])
    return str(item.get("category") or "unknown")


def _system_timeline_text(item: dict[str, Any], count: int) -> str:
    action = str(item.get("action") or "unknown")
    category = str(item.get("category") or "unknown")
    if count > 1:
        return f"action={action} continued {count} cycles; category={category}"
    return f"action={action}; category={category}"


def _fault_domain_from_gates(*, gates: dict[str, Any], dominant: str) -> str:
    normalized = dominant.lower()
    if any(token in normalized for token in ["factor", "transport", "proxy", "consensus", "market_data"]):
        return "data_infrastructure"
    if "research" in normalized:
        return "research_unavailable"
    if "risk" in normalized or "veto" in normalized:
        return "risk_control_block"
    if "candidate" in normalized or "execution" in normalized or "order" in normalized:
        return "bot_execution_block"
    if not any(isinstance(gate, dict) and gate.get("blocking") for gate in gates.values()):
        return "healthy"
    return "unknown"


def _fault_domain_from_audit(audit_payload: dict[str, Any]) -> str:
    reason = _dominant_audit_reason(audit_payload).lower()
    if any(token in reason for token in ["artifact_missing", "observability", "snapshot_only", "incomplete"]):
        return "unknown"
    if any(token in reason for token in ["factor", "transport", "proxy", "consensus", "market_data"]):
        return "data_infrastructure"
    if "research" in reason:
        return "research_unavailable"
    if any(token in reason for token in ["risk", "veto", "net_edge", "below_probe_floor"]):
        return "risk_control_block"
    if any(token in reason for token in ["candidate", "execution", "order"]):
        return "bot_execution_block"
    if "waiting_for_trigger" in reason:
        return "strategy_wait"
    return "unknown"


def _dominant_audit_reason(audit_payload: dict[str, Any]) -> str:
    top_reasons = _top_count_dict(audit_payload.get("reason_counts"), limit=1)
    if top_reasons:
        return str(next(iter(top_reasons)))
    top_categories = _top_count_dict(audit_payload.get("category_counts"), limit=1)
    if top_categories:
        return str(next(iter(top_categories)))
    return "unknown"


def _top_count_dict(value: Any, *, limit: int) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    ranked: list[tuple[str, float, Any]] = []
    for key, raw_count in value.items():
        try:
            count = float(raw_count)
        except (TypeError, ValueError):
            count = 0.0
        ranked.append((str(key), count, raw_count))
    ranked.sort(key=lambda item: (-item[1], item[0]))
    return {key: raw_count for key, _, raw_count in ranked[:limit]}


def _join_reason_codes(value: Any) -> str:
    if isinstance(value, list):
        return ",".join(str(item) for item in value) if value else "none"
    if isinstance(value, str) and value:
        return value
    return "none"


def _join_list(value: Any) -> str:
    if isinstance(value, list):
        return ", ".join(str(item) for item in value) if value else "none"
    return str(value) if value else "none"


def _md_cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


if __name__ == "__main__":
    raise SystemExit(main())
