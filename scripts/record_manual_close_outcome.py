from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Protocol


BOT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = BOT_ROOT / "src"
DEFAULT_QUANT_ROOT = Path("D:/开发/quant_system_rebuild")
for candidate in (SRC_ROOT, DEFAULT_QUANT_ROOT / "src"):
    normalized = str(candidate)
    if normalized not in sys.path:
        sys.path.insert(0, normalized)

from bot.config import BotConfig
from bot.exchange_adapter import AdapterCredentials, BinancePerpAdapter, OkxUsdtSwapAdapter


class TradeHistoryAdapter(Protocol):
    def fetch_user_trades_raw(
        self,
        *,
        symbol: str = "ETH-USDT-SWAP",
        limit: int = 100,
        start_time_ms: int | None = None,
        end_time_ms: int | None = None,
    ) -> list[dict[str, Any]]: ...


@dataclass(frozen=True)
class ManualCloseMatch:
    entry_trades: list[dict[str, Any]]
    close_trades: list[dict[str, Any]]
    direction: str
    entry_at: datetime
    exit_at: datetime
    entry_price: float
    exit_price: float
    quantity: float
    realized_pnl: float
    commission: float


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fetch read-only exchange trade history and record a manually closed ETH position outcome."
    )
    parser.add_argument("--symbol", default=BotConfig().exchange_symbol)
    parser.add_argument("--timeframe", default="15m")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--start-time", default="", help="UTC/ISO start time, optional")
    parser.add_argument("--end-time", default="", help="UTC/ISO end time, optional")
    parser.add_argument("--api-key-env", default=BotConfig().exchange_api_key_env)
    parser.add_argument("--api-secret-env", default=BotConfig().exchange_api_secret_env)
    parser.add_argument("--api-passphrase-env", default=BotConfig().exchange_api_passphrase_env)
    parser.add_argument("--proxy-url", default=None)
    parser.add_argument("--quant-root", default=str(DEFAULT_QUANT_ROOT))
    parser.add_argument("--db-path", default="")
    parser.add_argument("--output-root", default=str(BOT_ROOT / "runtime" / "manual_outcomes"))
    parser.add_argument("--decision-run-id", default="")
    parser.add_argument("--handoff-id", default="")
    parser.add_argument("--candidate-package-id", default="manual_close")
    parser.add_argument("--close-order-id", default="", help="Optional close order id to select a specific SELL close.")
    parser.add_argument("--dry-run", action="store_true", default=False)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    result = run(args=args)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["status"] in {"recorded", "dry_run", "ambiguous", "no_match"} else 1


def run(*, args: argparse.Namespace, adapter: TradeHistoryAdapter | None = None) -> dict[str, Any]:
    quant_root = Path(args.quant_root)
    _prioritize_quant_src(quant_root)

    adapter = adapter or _build_adapter(args)
    trades = sorted(
        [_normalize_trade(item) for item in adapter.fetch_user_trades_raw(
            symbol=args.symbol,
            limit=int(args.limit),
            start_time_ms=_parse_optional_time_ms(args.start_time),
            end_time_ms=_parse_optional_time_ms(args.end_time),
        )],
        key=lambda item: int(item.get("time") or 0),
    )
    close_order_id = str(args.close_order_id or "")
    match = infer_manual_long_close(trades, close_order_id=close_order_id)
    generated_at = datetime.now(timezone.utc).replace(microsecond=0)
    output_root = Path(args.output_root)
    artifact_path = output_root / f"manual_close_trades_{generated_at.strftime('%Y%m%dT%H%M%SZ')}.json"
    payload = {
        "version": 1,
        "status": "matched" if match else "no_match",
        "generated_at": generated_at.isoformat(),
        "symbol": args.symbol,
        "timeframe": args.timeframe,
        "source": "exchange_user_trades_read_only",
        "manual_close": True,
        "close_order_id_filter": close_order_id,
        "trade_count": len(trades),
        "trades": trades,
        "match": _match_payload(match) if match else {},
    }
    if not match:
        _write_json(artifact_path, payload)
        return {"status": "no_match", "artifact_path": str(artifact_path), "trade_count": len(trades)}

    outcome = build_outcome(
        match=match,
        args=args,
        generated_at=generated_at,
    )
    payload["outcome"] = outcome_payload(outcome)
    _write_json(artifact_path, payload)
    if args.dry_run:
        return {"status": "dry_run", "artifact_path": str(artifact_path), "outcome": payload["outcome"]}

    _clear_quant_module_cache()
    from analysis import write_decision_outcomes_summary
    from analysis.decision_outcomes import upsert_decision_outcome

    db_path = Path(args.db_path) if args.db_path else quant_root / "runtime" / "analysis" / "quant_analysis.duckdb"
    summary_path = quant_root / "runtime" / "analysis" / "decision_outcomes_summary.json"
    summary = upsert_decision_outcome(db_path, outcome)
    write_decision_outcomes_summary(summary_path, summary)
    return {
        "status": "recorded",
        "artifact_path": str(artifact_path),
        "db_path": str(db_path),
        "summary_path": str(summary_path),
        "outcome": payload["outcome"],
    }


def _build_adapter(args: argparse.Namespace) -> BinancePerpAdapter | OkxUsdtSwapAdapter:
    if str(args.symbol or "") == "ETHUSDT":
        api_key_env = args.api_key_env
        api_secret_env = args.api_secret_env
        if api_key_env == BotConfig().exchange_api_key_env:
            api_key_env = "BINANCE_TRADE_API_KEY"
        if api_secret_env == BotConfig().exchange_api_secret_env:
            api_secret_env = "BINANCE_TRADE_API_SECRET"
        config = BotConfig(
            proxy_url=args.proxy_url,
            exchange_venue="binance_usdt_perp",
            exchange_symbol="ETHUSDT",
            exchange_api_base_url="https://fapi.binance.com",
            exchange_api_key_env=api_key_env,
            exchange_api_secret_env=api_secret_env,
        )
    else:
        config = BotConfig(proxy_url=args.proxy_url)
    credentials = AdapterCredentials(
        venue=config.exchange_venue,
        api_key_env=config.exchange_api_key_env,
        api_secret_env=config.exchange_api_secret_env,
        api_passphrase_env="" if config.exchange_venue == "binance_usdt_perp" else (getattr(args, "api_passphrase_env", None) or getattr(config, "exchange_api_passphrase_env", "")),
        recv_window_ms=config.recv_window_ms,
        timeout_sec=config.timeout_sec,
        proxy_url=args.proxy_url,
        api_base_url=config.exchange_api_base_url,
    )
    return OkxUsdtSwapAdapter(credentials) if config.exchange_venue == "okx_usdt_swap" else BinancePerpAdapter(credentials)


def infer_manual_long_close(trades: list[dict[str, Any]], *, close_order_id: str = "") -> ManualCloseMatch | None:
    sells = [trade for trade in trades if _side(trade) == "SELL" and (not close_order_id or str(trade.get("orderId") or "") == close_order_id)]
    if not sells:
        return None
    close_order = str(sells[-1].get("orderId") or "")
    close_trades = [trade for trade in trades if str(trade.get("orderId") or "") == close_order and _side(trade) == "SELL"]
    close_qty = sum(_decimal(trade.get("qty")) for trade in close_trades)
    if close_qty <= 0:
        return None
    entry_trades: list[dict[str, Any]] = []
    entry_qty = Decimal("0")
    for trade in reversed([item for item in trades if int(item.get("time") or 0) <= int(close_trades[0].get("time") or 0)]):
        if _side(trade) != "BUY":
            if entry_qty > 0:
                break
            continue
        entry_trades.insert(0, trade)
        entry_qty += _decimal(trade.get("qty"))
        if entry_qty >= close_qty:
            break
    if not entry_trades or entry_qty < close_qty:
        return None
    return ManualCloseMatch(
        entry_trades=entry_trades,
        close_trades=close_trades,
        direction="long",
        entry_at=_trade_time(entry_trades[0]),
        exit_at=_trade_time(close_trades[-1]),
        entry_price=float(_weighted_price(entry_trades)),
        exit_price=float(_weighted_price(close_trades)),
        quantity=float(close_qty),
        realized_pnl=float(sum(_decimal(trade.get("realizedPnl")) for trade in close_trades)),
        commission=float(sum(_decimal(trade.get("commission")) for trade in [*entry_trades, *close_trades])),
    )


def build_outcome(*, match: ManualCloseMatch, args: argparse.Namespace, generated_at: datetime) -> Any:
    _prioritize_quant_src(Path(args.quant_root))
    _clear_quant_module_cache()
    from analysis import DecisionOutcome

    raw_return_pct = (match.exit_price / match.entry_price - 1.0) * 100.0
    notional = max(match.entry_price * match.quantity, 1e-12)
    estimated_cost_pct = abs(match.commission) / notional * 100.0
    net_return_pct = raw_return_pct - estimated_cost_pct
    close_order_id = str(match.close_trades[-1].get("orderId") or "")
    decision_run_id = args.decision_run_id or f"manual-close-{generated_at.strftime('%Y%m%dT%H%M%SZ')}-{close_order_id}"
    holding_bars = max(0, int((match.exit_at - match.entry_at).total_seconds() // (15 * 60)))
    return DecisionOutcome(
        decision_run_id=decision_run_id,
        handoff_id=args.handoff_id,
        candidate_package_id=args.candidate_package_id or "manual_close",
        order_id=close_order_id,
        client_order_id="",
        symbol=args.symbol,
        timeframe=args.timeframe,
        direction=match.direction,
        entry_at=match.entry_at,
        entry_price=match.entry_price,
        exit_at=match.exit_at,
        exit_price=match.exit_price,
        resolved_at=match.exit_at,
        holding_bars=holding_bars,
        raw_return_pct=raw_return_pct,
        estimated_cost_pct=estimated_cost_pct,
        net_return_pct=net_return_pct,
        mfe_pct=None,
        mae_pct=None,
        stop_hit=False,
        status="resolved",
    )


def outcome_payload(outcome: Any) -> dict[str, Any]:
    return {
        key: value.isoformat() if isinstance(value, datetime) else value
        for key, value in outcome.__dict__.items()
    }


def _match_payload(match: ManualCloseMatch) -> dict[str, Any]:
    return {
        "direction": match.direction,
        "entry_at": match.entry_at.isoformat(),
        "exit_at": match.exit_at.isoformat(),
        "entry_price": match.entry_price,
        "exit_price": match.exit_price,
        "quantity": match.quantity,
        "realized_pnl": match.realized_pnl,
        "commission": match.commission,
        "entry_trade_count": len(match.entry_trades),
        "close_trade_count": len(match.close_trades),
        "close_order_id": str(match.close_trades[-1].get("orderId") or ""),
    }


def _parse_optional_time_ms(value: str) -> int | None:
    if not value:
        return None
    normalized = value.strip()
    if normalized.isdigit():
        return int(normalized)
    parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp() * 1000)


def _trade_time(trade: dict[str, Any]) -> datetime:
    return datetime.fromtimestamp(int(trade.get("time") or 0) / 1000, tz=timezone.utc)


def _weighted_price(trades: list[dict[str, Any]]) -> Decimal:
    total_qty = sum(_decimal(trade.get("qty")) for trade in trades)
    if total_qty <= 0:
        return Decimal("0")
    notional = sum(_decimal(trade.get("price")) * _decimal(trade.get("qty")) for trade in trades)
    return notional / total_qty


def _side(trade: dict[str, Any]) -> str:
    return str(trade.get("side") or "").upper()


def _normalize_trade(trade: dict[str, Any]) -> dict[str, Any]:
    if "ordId" not in trade and "fillSz" not in trade:
        return trade
    normalized = dict(trade)
    normalized.setdefault("orderId", trade.get("ordId"))
    normalized.setdefault("qty", trade.get("fillSz"))
    normalized.setdefault("price", trade.get("fillPx"))
    normalized.setdefault("time", trade.get("ts"))
    normalized.setdefault("realizedPnl", trade.get("pnl"))
    normalized.setdefault("commission", trade.get("fee"))
    return normalized


def _decimal(value: Any) -> Decimal:
    try:
        return Decimal(str(value or "0"))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _prioritize_quant_src(quant_root: Path) -> None:
    requested_src = quant_root / "src"
    quant_src = str(requested_src if (requested_src / "analysis" / "decision_outcomes.py").exists() else DEFAULT_QUANT_ROOT / "src")
    sys.path[:] = [item for item in sys.path if item != quant_src]
    sys.path.insert(0, quant_src)


def _clear_quant_module_cache() -> None:
    for module_name in list(sys.modules):
        if (
            module_name == "analysis"
            or module_name.startswith("analysis.")
            or module_name == "contracts"
            or module_name.startswith("contracts.")
            or module_name == "interfaces"
            or module_name.startswith("interfaces.")
        ):
            sys.modules.pop(module_name, None)


if __name__ == "__main__":
    raise SystemExit(main())
