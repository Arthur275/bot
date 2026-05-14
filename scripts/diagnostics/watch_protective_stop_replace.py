from __future__ import annotations

import argparse
from argparse import Namespace
import json
import time
from datetime import UTC, datetime
from decimal import InvalidOperation
from pathlib import Path
from typing import Any

from scripts.path_utils import repo_root_from_script

try:
    from scripts.adhoc.adopt_protective_stop import _decimal_equal, _fmt, _money, _to_bool, _to_decimal
    from .preview_protective_stop_replace import (
        DEFAULT_REPORT_ROOT as DEFAULT_REPLACE_REPORT_ROOT,
        DEFAULT_STATE_PATH,
        _filter_protective_algo_orders,
        run as run_replace_preview,
    )
except ImportError:
    from scripts.adhoc.adopt_protective_stop import _decimal_equal, _fmt, _money, _to_bool, _to_decimal
    from scripts.diagnostics.preview_protective_stop_replace import (
        DEFAULT_REPORT_ROOT as DEFAULT_REPLACE_REPORT_ROOT,
        DEFAULT_STATE_PATH,
        _filter_protective_algo_orders,
        run as run_replace_preview,
    )


BOT_ROOT = repo_root_from_script(__file__)
DEFAULT_WATCH_REPORT_ROOT = str(BOT_ROOT / "runtime" / "reports" / "protective_stop_replace_watch")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only watcher for protective stop breakeven replace readiness.")
    parser.add_argument("--state-path", default=DEFAULT_STATE_PATH)
    parser.add_argument("--report-root", default=DEFAULT_REPLACE_REPORT_ROOT)
    parser.add_argument("--watch-report-root", default=DEFAULT_WATCH_REPORT_ROOT)
    parser.add_argument("--proxy-url", default="http://127.0.0.1:7897")
    parser.add_argument("--api-key-env", default="OKX_TRADE_API_KEY")
    parser.add_argument("--api-secret-env", default="OKX_TRADE_API_SECRET")
    parser.add_argument("--api-passphrase-env", default="OKX_TRADE_PASSPHRASE")
    parser.add_argument("--watch-interval-sec", type=float, default=30.0)
    parser.add_argument("--ready-buffer-pct", type=float, default=0.005)
    parser.add_argument("--reset-buffer-pct", type=float, default=0.003)
    parser.add_argument("--tight-stop-distance-pct", type=float, default=0.008)
    parser.add_argument("--heartbeat-every-sec", type=float, default=300.0)
    parser.add_argument("--auto-confirm-replace", action="store_true")
    parser.add_argument("--accept-gap-risk", action="store_true")
    parser.add_argument("--allow-missing-repair", action="store_true")
    parser.add_argument("--max-preview-age-sec", type=int, default=180)
    parser.add_argument("--max-iterations", type=int, default=0, help="Testing guard. 0 means run until stopped.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return run(args=args)


def run(*, args: argparse.Namespace, adapter: Any | None = None, state_store: Any | None = None, sleep_fn: Any = time.sleep) -> int:
    from bot.config import BotConfig
    from bot.exchange_adapter import AdapterCredentials, BinancePerpAdapter, OkxUsdtSwapAdapter
    from bot.state_store import StateStore

    state_path = Path(args.state_path)
    report_root = Path(args.report_root)
    watch_report_root = Path(args.watch_report_root)
    watch_report_root.mkdir(parents=True, exist_ok=True)
    if adapter is None:
        config = BotConfig(
            state_store_path=state_path,
            audit_log_path=watch_report_root / "watch_audit.jsonl",
            artifacts_root=watch_report_root / "artifacts",
            proxy_url=args.proxy_url or None,
            exchange_venue="binance_usdt_perp" if str(args.api_key_env).startswith("BINANCE_") else "okx_usdt_swap",
            exchange_symbol="ETHUSDT" if str(args.api_key_env).startswith("BINANCE_") else "ETH-USDT-SWAP",
            exchange_api_base_url="https://fapi.binance.com" if str(args.api_key_env).startswith("BINANCE_") else "https://www.okx.com",
        )
        credentials = AdapterCredentials(
            venue=config.exchange_venue,
            api_key_env=args.api_key_env,
            api_secret_env=args.api_secret_env,
            api_passphrase_env=getattr(args, "api_passphrase_env", None) or config.exchange_api_passphrase_env,
            recv_window_ms=config.recv_window_ms,
            timeout_sec=config.timeout_sec,
            proxy_url=config.proxy_url,
            api_base_url=config.exchange_api_base_url,
        )
        adapter = OkxUsdtSwapAdapter(credentials) if config.exchange_venue == "okx_usdt_swap" else BinancePerpAdapter(credentials)
    if state_store is None:
        state_store = StateStore(state_path)

    state = WatchState()
    auto_state = AutoReplaceState()
    print("[WATCHER STARTED] Re-evaluating from fresh exchange snapshot. No in-flight action will be resumed.")
    print("[WATCHING] Protective stop replace readiness")
    print(f"State path: {state_path}")
    print(f"Report root: {report_root}")
    if bool(args.auto_confirm_replace) and bool(args.accept_gap_risk):
        print("Mode: auto-confirm. Cancel/place may be called after readiness and confirm revalidation.")
    elif bool(args.auto_confirm_replace):
        print("Mode: auto-confirm requested, but gap risk not accepted. No cancel/place endpoint is called.")
    else:
        print("Mode: read-only. No cancel/place endpoint is called.")

    iteration = 0
    while True:
        iteration += 1
        now = datetime.now(UTC).replace(microsecond=0)
        result = evaluate_once(
            adapter=adapter,
            state_store=state_store,
            now=now,
            state_path=state_path,
            report_root=report_root,
            ready_buffer_pct=float(args.ready_buffer_pct),
            reset_buffer_pct=float(args.reset_buffer_pct),
            tight_stop_distance_pct=float(args.tight_stop_distance_pct),
            allow_missing_repair=bool(args.allow_missing_repair),
        )
        action = state.update(result=result, now=now, heartbeat_every_sec=float(args.heartbeat_every_sec))
        if action.should_print:
            print(render_watch_line(result=result, action=action))
        if result.get("status") == "ready" and bool(args.auto_confirm_replace) and auto_state.can_start():
            auto_state.mark_preflight()
            auto_payload = auto_confirm_replace(args=args)
            auto_state.mark_done()
            print(render_auto_confirm_line(auto_payload))
            if auto_payload.get("state_written"):
                return 0
        if action.stop:
            return action.exit_code
        if args.max_iterations and iteration >= int(args.max_iterations):
            return 0
        sleep_fn(float(args.watch_interval_sec))


class WatchState:
    def __init__(self) -> None:
        self.alerted = False
        self.last_heartbeat_at: datetime | None = None
        self.last_status = ""

    def update(self, *, result: dict[str, Any], now: datetime, heartbeat_every_sec: float) -> "WatchAction":
        status = str(result.get("status") or "")
        if status in {"closed", "blocked"}:
            return WatchAction(should_print=True, stop=True, exit_code=0 if status == "closed" else 2)
        if status == "reset":
            self.alerted = False
            self.last_status = status
            return WatchAction(should_print=True)
        if status == "ready":
            if not self.alerted:
                self.alerted = True
                self.last_status = status
                return WatchAction(should_print=True)
            return WatchAction(should_print=False)
        heartbeat_due = (
            self.last_heartbeat_at is None
            or (now - self.last_heartbeat_at).total_seconds() >= heartbeat_every_sec
            or status != self.last_status
            or bool(result.get("tight_to_stop"))
        )
        self.last_status = status
        if heartbeat_due:
            self.last_heartbeat_at = now
        return WatchAction(should_print=heartbeat_due)


class WatchAction:
    def __init__(self, *, should_print: bool, stop: bool = False, exit_code: int = 0) -> None:
        self.should_print = should_print
        self.stop = stop
        self.exit_code = exit_code


class AutoReplaceState:
    def __init__(self) -> None:
        self.status = "IDLE"

    def can_start(self) -> bool:
        return self.status == "IDLE"

    def mark_preflight(self) -> None:
        self.status = "PREFLIGHT"

    def mark_confirming(self) -> None:
        self.status = "CONFIRMING"

    def mark_done(self) -> None:
        self.status = "IDLE"


def evaluate_once(
    *,
    adapter: Any,
    state_store: Any,
    now: datetime,
    state_path: Path,
    report_root: Path,
    ready_buffer_pct: float,
    reset_buffer_pct: float,
    tight_stop_distance_pct: float,
    allow_missing_repair: bool = False,
) -> dict[str, Any]:
    state = state_store.load()
    record = (state.metadata or {}).get("protective_stop")
    snapshot = adapter.fetch_runtime_snapshot()
    position = snapshot.position
    base: dict[str, Any] = {
        "created_at": now.isoformat(),
        "status": "watching",
        "message": "",
        "state_path": str(state_path),
        "report_root": str(report_root),
        "position_state": position.position_state,
        "direction": position.direction,
        "position_amt": position.position_amt,
        "entry_price": position.entry_price,
        "mark_price": position.mark_price,
        "recorded_algo_id": (record or {}).get("algo_id") if isinstance(record, dict) else "",
        "blocked_reasons": [],
    }
    if position.position_state == "FLAT":
        base["status"] = "closed"
        base["message"] = "Position closed. Watch stopped."
        return base
    if not isinstance(record, dict):
        return _blocked(base, "recorded_protective_stop_missing")
    if not snapshot.snapshot_valid:
        return _blocked(base, "snapshot_invalid")
    if position.position_state != "ENTERED":
        return _blocked(base, "position_not_entered")
    if position.direction not in {"long", "short"}:
        return _blocked(base, "position_direction_unknown")
    try:
        raw_orders = adapter.fetch_open_algo_orders_raw()
    except Exception as exc:
        base["open_algo_orders_error"] = f"{exc.__class__.__name__}: {exc}"
        return _blocked(base, "open_algo_orders_unavailable")
    candidates = _filter_protective_algo_orders(raw_orders)
    if len(candidates) == 0:
        if not allow_missing_repair:
            return _blocked(base, "no_active_protective_algo_order")
        metrics = _build_metrics(position=position, stop_price=record.get("trigger_price"), ready_buffer_pct=_next_stage_buffer(record, ready_buffer_pct))
        base.update(metrics)
        base["status"] = "ready"
        base["message"] = "READY TO REPAIR MISSING PROTECTIVE STOP"
        base["repair_missing"] = True
        base["preview_command"] = _build_preview_command(state_path=state_path, report_root=report_root, allow_missing_repair=True)
        return base
    if len(candidates) != 1:
        return _blocked(base, "multiple_active_protective_algo_orders")
    candidate = candidates[0]
    base["exchange_algo_id"] = str(candidate.get("algoId") or "")
    base["stop_price"] = _to_optional_float(candidate.get("triggerPrice") or candidate.get("stopPrice"))
    blocked_reason = _validate_candidate(record=record, candidate=candidate, position=position)
    if blocked_reason:
        return _blocked(base, blocked_reason)

    effective_ready_buffer_pct = _next_stage_buffer(record, ready_buffer_pct)
    metrics = _build_metrics(position=position, stop_price=base["stop_price"], ready_buffer_pct=effective_ready_buffer_pct)
    base.update(metrics)
    base["tight_to_stop"] = metrics["stop_distance_pct"] is not None and metrics["stop_distance_pct"] < tight_stop_distance_pct
    if metrics["buffer_pct"] is not None and metrics["buffer_pct"] >= effective_ready_buffer_pct:
        base["status"] = "ready"
        base["message"] = "READY TO REPLACE"
        base["preview_command"] = _build_preview_command(state_path=state_path, report_root=report_root)
    elif metrics["buffer_pct"] is not None and metrics["buffer_pct"] < reset_buffer_pct:
        base["status"] = "reset"
        base["message"] = "Alert reset below hysteresis band."
    else:
        base["status"] = "watching"
        base["message"] = "Watching."
    return base


def _validate_candidate(*, record: dict[str, Any], candidate: dict[str, Any], position: Any) -> str:
    recorded_algo_id = str(record.get("algo_id") or "")
    exchange_algo_id = str(candidate.get("algoId") or "")
    if not recorded_algo_id or recorded_algo_id != exchange_algo_id:
        return "algo_id_mismatch"
    expected_side = "SELL" if position.direction == "long" else "BUY"
    if str(candidate.get("side") or "").upper() != expected_side:
        return "side_mismatch"
    if not _to_bool(candidate.get("closePosition")) and not _decimal_equal(candidate.get("quantity"), abs(float(position.position_amt or 0.0))):
        return "quantity_mismatch"
    if not _decimal_equal(candidate.get("triggerPrice") or candidate.get("stopPrice"), record.get("trigger_price")):
        return "trigger_price_mismatch"
    return ""


def _build_metrics(*, position: Any, stop_price: float | None, ready_buffer_pct: float) -> dict[str, Any]:
    entry = _to_decimal(position.entry_price)
    mark = _to_decimal(position.mark_price)
    stop = _to_decimal(stop_price)
    if entry is None or mark is None or stop is None or entry <= 0 or mark <= 0:
        return {"buffer_pct": None, "stop_distance_pct": None, "ready_buffer_pct": ready_buffer_pct}
    if position.direction == "short":
        buffer_pct = (entry - mark) / entry
    else:
        buffer_pct = (mark - entry) / entry
    stop_distance_pct = abs(mark - stop) / mark
    return {
        "buffer_pct": float(buffer_pct),
        "stop_distance_pct": float(stop_distance_pct),
        "ready_buffer_pct": ready_buffer_pct,
    }


def render_watch_line(*, result: dict[str, Any], action: WatchAction) -> str:
    status = str(result.get("status") or "watching")
    if status == "closed":
        return result.get("message") or "Position closed. Watch stopped."
    if status == "blocked":
        reasons = ", ".join(result.get("blocked_reasons") or [])
        return f"[BLOCKED] {reasons}"
    prefix = "[READY TO REPLACE]" if status == "ready" else "[RESET]" if status == "reset" else "[WATCHING]"
    tight = "  |  TIGHT TO STOP" if result.get("tight_to_stop") else ""
    line = (
        f"{prefix} ETH mark={_money(result.get('mark_price'))}"
        f"  |  buffer={_pct(result.get('buffer_pct'))} (target {_pct(result.get('ready_buffer_pct'))})"
        f"  |  stop_distance={_pct(result.get('stop_distance_pct'))}{tight}"
        f"  |  entry={_money(result.get('entry_price'))} stop={_money(result.get('stop_price'))}"
    )
    if status == "ready":
        line += "\n\nPREVIEW COMMAND\n" + "=" * 72 + "\n" + str(result.get("preview_command") or "") + "\n" + "=" * 72
    return line


def _blocked(base: dict[str, Any], reason: str) -> dict[str, Any]:
    base["status"] = "blocked"
    base["blocked_reasons"] = [reason]
    return base


def _next_stage_buffer(record: dict[str, Any], fallback: float) -> float:
    stages = {0: 0.005, 1: 0.009, 2: 0.013}
    stage = int(record.get("lock_stage") or 0)
    return float(stages.get(stage, fallback))


def _build_preview_command(*, state_path: Path, report_root: Path, allow_missing_repair: bool = False) -> str:
    command = (
        "python scripts\\preview_protective_stop_replace.py `\n"
        f"  --state-path {_quote_arg(str(state_path))} `\n"
        f"  --report-root {_quote_arg(str(report_root))} `\n"
        "  --proxy-url http://127.0.0.1:7897"
    )
    if allow_missing_repair:
        command += " `\n  --allow-missing-repair"
    return command


def auto_confirm_replace(*, args: argparse.Namespace) -> dict[str, Any]:
    lock_path = Path(args.watch_report_root) / "auto_replace.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        lock_handle = lock_path.open("x", encoding="utf-8")
    except FileExistsError:
        payload = {
            "mode": "auto_confirm",
            "created_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
            "state_written": False,
            "blocked_reasons": ["auto_replace_lock_exists"],
        }
        _write_watch_report(payload=payload, watch_report_root=Path(args.watch_report_root))
        return payload
    with lock_handle:
        lock_handle.write(datetime.now(UTC).replace(microsecond=0).isoformat())
    try:
        return _auto_confirm_replace_locked(args=args)
    finally:
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass


def _auto_confirm_replace_locked(*, args: argparse.Namespace) -> dict[str, Any]:
    if not bool(args.accept_gap_risk):
        payload = {
            "mode": "auto_confirm",
            "created_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
            "state_written": False,
            "blocked_reasons": ["gap_risk_not_accepted"],
        }
        _write_watch_report(payload=payload, watch_report_root=Path(args.watch_report_root))
        return payload
    preview_args = _replace_args_from_watch(args=args)
    preview = run_replace_preview(args=preview_args)
    if not preview.get("replace_ready"):
        payload = {
            "mode": "auto_confirm",
            "created_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
            "state_written": False,
            "preview": preview,
            "blocked_reasons": list(preview.get("blocked_reasons") or ["preview_not_ready"]),
        }
        _write_watch_report(payload=payload, watch_report_root=Path(args.watch_report_root))
        return payload
    confirm_args = _replace_args_from_watch(
        args=args,
        preview_file=str(Path(args.report_root) / "latest_preview.json"),
        confirm_token=str(preview.get("confirm_token") or ""),
        accept_gap_risk=True,
    )
    confirmed = run_replace_preview(args=confirm_args)
    payload = {
        "mode": "auto_confirm",
        "created_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "state_written": bool(confirmed.get("state_written")),
        "preview": preview,
        "confirm": confirmed,
        "blocked_reasons": list(confirmed.get("blocked_reasons") or []),
    }
    _write_watch_report(payload=payload, watch_report_root=Path(args.watch_report_root))
    return payload


def _replace_args_from_watch(
    *,
    args: argparse.Namespace,
    preview_file: str = "",
    confirm_token: str = "",
    accept_gap_risk: bool = False,
) -> Namespace:
    return Namespace(
        state_path=str(args.state_path),
        report_root=str(args.report_root),
        proxy_url=str(args.proxy_url or ""),
        api_key_env=str(args.api_key_env),
        api_secret_env=str(args.api_secret_env),
        api_passphrase_env=str(getattr(args, "api_passphrase_env", "")),
        target_mode="ratchet",
        allow_missing_repair=bool(getattr(args, "allow_missing_repair", False)),
        min_profit_lock_pct=0.003,
        min_mark_buffer_pct=float(args.ready_buffer_pct),
        snapshot_max_age_sec=30,
        preview_file=preview_file,
        confirm_token=confirm_token,
        max_preview_age_sec=int(args.max_preview_age_sec),
        accept_gap_risk=accept_gap_risk,
        json=False,
    )


def render_auto_confirm_line(payload: dict[str, Any]) -> str:
    if payload.get("state_written"):
        confirm = payload.get("confirm") or {}
        record = confirm.get("new_protective_stop_record") or {}
        return (
            "[AUTO REPLACE CONFIRMED] "
            f"new_algo_id={record.get('algo_id', '')} stop={_money(record.get('trigger_price'))}"
        )
    reasons = ", ".join(payload.get("blocked_reasons") or ["unknown"])
    return f"[AUTO REPLACE BLOCKED] {reasons}"


def _write_watch_report(*, payload: dict[str, Any], watch_report_root: Path) -> None:
    watch_report_root.mkdir(parents=True, exist_ok=True)
    (watch_report_root / "latest_auto_confirm.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    timestamp = str(payload.get("created_at") or datetime.now(UTC).replace(microsecond=0).isoformat()).replace(":", "").replace("-", "")
    (watch_report_root / f"auto_confirm_{timestamp}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _to_optional_float(value: Any) -> float | None:
    parsed = _to_decimal(value)
    return None if parsed is None else float(parsed)


def _pct(value: Any) -> str:
    if value in (None, ""):
        return "n/a"
    try:
        return f"{float(value) * 100:.2f}%"
    except (TypeError, ValueError, InvalidOperation):
        return str(value)


def _quote_arg(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


if __name__ == "__main__":
    raise SystemExit(main())
