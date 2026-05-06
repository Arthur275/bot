from __future__ import annotations

import argparse
import json
from pathlib import Path

from scripts import record_manual_close_outcome


class FakeTradeHistoryAdapter:
    def __init__(self, trades: list[dict[str, object]]) -> None:
        self.trades = trades
        self.requests: list[dict[str, object]] = []

    def fetch_user_trades_raw(
        self,
        *,
        symbol: str = "ETHUSDT",
        limit: int = 100,
        start_time_ms: int | None = None,
        end_time_ms: int | None = None,
    ) -> list[dict[str, object]]:
        self.requests.append(
            {
                "symbol": symbol,
                "limit": limit,
                "start_time_ms": start_time_ms,
                "end_time_ms": end_time_ms,
            }
        )
        return list(self.trades)


def _args(tmp_path: Path, *, dry_run: bool = True) -> argparse.Namespace:
    return argparse.Namespace(
        symbol="ETHUSDT",
        timeframe="15m",
        limit=100,
        start_time="",
        end_time="",
        api_key_env="BINANCE_TRADE_API_KEY",
        api_secret_env="BINANCE_TRADE_API_SECRET",
        proxy_url=None,
        quant_root=str(tmp_path / "quant"),
        db_path=str(tmp_path / "quant" / "runtime" / "analysis" / "quant_analysis.duckdb"),
        output_root=str(tmp_path / "manual_outcomes"),
        decision_run_id="",
        handoff_id="",
        candidate_package_id="manual_close",
        close_order_id="",
        dry_run=dry_run,
    )


def test_infer_manual_long_close_pairs_recent_buy_and_sell() -> None:
    match = record_manual_close_outcome.infer_manual_long_close(
        [
            {
                "id": 1,
                "orderId": 11,
                "side": "BUY",
                "price": "3000",
                "qty": "0.01",
                "time": 1_777_000_000_000,
                "realizedPnl": "0",
                "commission": "0.012",
            },
            {
                "id": 2,
                "orderId": 12,
                "side": "SELL",
                "price": "3030",
                "qty": "0.01",
                "time": 1_777_000_900_000,
                "realizedPnl": "0.3",
                "commission": "0.01212",
            },
        ]
    )

    assert match is not None
    assert match.direction == "long"
    assert match.entry_price == 3000.0
    assert match.exit_price == 3030.0
    assert match.quantity == 0.01
    assert match.realized_pnl == 0.3


def test_run_dry_run_fetches_read_only_history_and_writes_manual_artifact(tmp_path: Path) -> None:
    adapter = FakeTradeHistoryAdapter(
        [
            {
                "id": 1,
                "orderId": 11,
                "side": "BUY",
                "price": "3000",
                "qty": "0.01",
                "time": 1_777_000_000_000,
                "realizedPnl": "0",
                "commission": "0.012",
            },
            {
                "id": 2,
                "orderId": 12,
                "side": "SELL",
                "price": "3030",
                "qty": "0.01",
                "time": 1_777_000_900_000,
                "realizedPnl": "0.3",
                "commission": "0.01212",
            },
        ]
    )

    result = record_manual_close_outcome.run(args=_args(tmp_path), adapter=adapter)

    assert result["status"] == "dry_run"
    assert adapter.requests == [{"symbol": "ETHUSDT", "limit": 100, "start_time_ms": None, "end_time_ms": None}]
    payload = json.loads(Path(result["artifact_path"]).read_text(encoding="utf-8"))
    assert payload["manual_close"] is True
    assert payload["source"] == "binance_user_trades_read_only"
    assert payload["match"]["close_order_id"] == "12"
    assert payload["outcome"]["candidate_package_id"] == "manual_close"
    assert payload["outcome"]["status"] == "resolved"
    assert not (tmp_path / "quant" / "runtime" / "analysis" / "quant_analysis.duckdb").exists()


def test_run_records_no_match_without_outcome(tmp_path: Path) -> None:
    adapter = FakeTradeHistoryAdapter(
        [
            {
                "id": 1,
                "orderId": 11,
                "side": "BUY",
                "price": "3000",
                "qty": "0.01",
                "time": 1_777_000_000_000,
                "realizedPnl": "0",
                "commission": "0.012",
            }
        ]
    )

    result = record_manual_close_outcome.run(args=_args(tmp_path), adapter=adapter)

    assert result["status"] == "no_match"
    payload = json.loads(Path(result["artifact_path"]).read_text(encoding="utf-8"))
    assert payload["status"] == "no_match"
    assert "outcome" not in payload
