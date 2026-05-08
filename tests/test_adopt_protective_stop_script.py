from __future__ import annotations

from argparse import Namespace
from datetime import datetime
from pathlib import Path

from bot.exchange_adapter import AdapterRuntimeSnapshot, PositionSnapshot
from bot.state_store import StateStore
from scripts import adopt_protective_stop


class FakeAdapter:
    def __init__(self, *, raw_orders: list[dict], snapshot: AdapterRuntimeSnapshot | None = None) -> None:
        self._raw_orders = raw_orders
        self._snapshot = snapshot or AdapterRuntimeSnapshot(
            fetched_at=datetime.now().replace(microsecond=0),
            position=PositionSnapshot(
                position_state="ENTERED",
                direction="long",
                size_pct=0.9,
                position_amt=0.043,
                entry_price=2300.26,
                mark_price=2304.0,
                leverage=10,
            ),
            protective_stop_present=True,
        )

    def fetch_runtime_snapshot(self) -> AdapterRuntimeSnapshot:
        return self._snapshot

    def fetch_open_algo_orders_raw(self) -> list[dict]:
        return self._raw_orders


def _order(**overrides):
    payload = {
        "algoId": 1000001522632139,
        "clientAlgoId": "ethbotps20260502142000",
        "algoStatus": "NEW",
        "orderType": "STOP_MARKET",
        "side": "SELL",
        "quantity": "0.043",
        "triggerPrice": "2264.6",
        "reduceOnly": True,
        "closePosition": False,
    }
    payload.update(overrides)
    return payload


def _args(tmp_path: Path, **overrides) -> Namespace:
    values = {
        "state_path": str(tmp_path / "shared_state" / "bot_state.json"),
        "report_root": str(tmp_path / "reports" / "protective_stop_adopt"),
        "proxy_url": "http://127.0.0.1:7897",
        "api_key_env": "BINANCE_TRADE_API_KEY",
        "api_secret_env": "BINANCE_TRADE_API_SECRET",
        "api_passphrase_env": "",
        "confirm_token": "",
        "preview_file": "",
        "max_preview_age_sec": 180,
        "snapshot_max_age_sec": 30,
        "json": False,
    }
    values.update(overrides)
    return Namespace(**values)


def test_adopt_preview_generates_confirm_command_and_report(tmp_path: Path, monkeypatch) -> None:
    from bot import exchange_adapter

    fake = FakeAdapter(raw_orders=[_order()])
    monkeypatch.setattr(exchange_adapter, "BinancePerpAdapter", lambda credentials: fake)

    payload = adopt_protective_stop.run(args=_args(tmp_path))

    assert payload["mode"] == "preview"
    assert payload["blocked_reasons"] == []
    assert payload["confirm_token"].startswith("ADOPT-")
    assert "--confirm-token" in payload["confirm_command"]
    assert payload["adopt_record"]["version"] == 1
    assert payload["adopt_record"]["algo_id"] == "1000001522632139"
    assert payload["adopt_record"]["quantity"] == 0.043
    assert (tmp_path / "reports" / "protective_stop_adopt" / "latest_preview.json").exists()
    assert not (tmp_path / "shared_state" / "bot_state.json").exists()


def test_adopt_preview_blocks_when_already_adopted(tmp_path: Path, monkeypatch) -> None:
    from bot import exchange_adapter

    state_path = tmp_path / "shared_state" / "bot_state.json"
    store = StateStore(state_path)
    state = store.load()
    state.metadata["protective_stop"] = {"algo_id": "already", "confirmed_at": "2026-05-02T14:00:00"}
    store.save(state)
    fake = FakeAdapter(raw_orders=[_order()])
    monkeypatch.setattr(exchange_adapter, "BinancePerpAdapter", lambda credentials: fake)

    payload = adopt_protective_stop.run(args=_args(tmp_path, state_path=str(state_path)))

    assert payload["blocked_reasons"] == ["already_adopted"]
    assert payload["confirm_command"] == ""
    assert "Already adopted" in adopt_protective_stop.render_panel(payload, args=_args(tmp_path))


def test_adopt_preview_blocks_multiple_open_algo_orders(tmp_path: Path, monkeypatch) -> None:
    from bot import exchange_adapter

    fake = FakeAdapter(raw_orders=[_order(algoId=1), _order(algoId=2, clientAlgoId="ghost")])
    monkeypatch.setattr(exchange_adapter, "BinancePerpAdapter", lambda credentials: fake)

    payload = adopt_protective_stop.run(args=_args(tmp_path))

    assert "multiple_active_protective_algo_orders" in payload["blocked_reasons"]
    assert payload["confirm_command"] == ""


def test_adopt_confirm_rechecks_preview_and_writes_state_metadata(tmp_path: Path, monkeypatch) -> None:
    from bot import exchange_adapter

    fake = FakeAdapter(raw_orders=[_order()])
    monkeypatch.setattr(exchange_adapter, "BinancePerpAdapter", lambda credentials: fake)
    preview = adopt_protective_stop.run(args=_args(tmp_path))
    preview_file = tmp_path / "reports" / "protective_stop_adopt" / "latest_preview.json"

    confirmed = adopt_protective_stop.run(
        args=_args(
            tmp_path,
            confirm_token=preview["confirm_token"],
            preview_file=str(preview_file),
        )
    )

    assert confirmed["state_written"] is True
    state = StateStore(tmp_path / "shared_state" / "bot_state.json").load()
    record = state.metadata["protective_stop"]
    assert record["version"] == 1
    assert record["algo_id"] == "1000001522632139"
    assert record["client_algo_id"] == "ethbotps20260502142000"
    assert record["trigger_price"] == 2264.6
    assert record["close_position"] is False
    assert record["adopted_from"] == "exchange_open_algo_orders"
    assert state.observed_position_state == "ENTERED"
    assert state.observed_position_direction == "long"
    assert state.observed_position_size_pct == 0.9
    assert state.protective_stop_required is False


def test_adopt_confirm_blocks_expired_preview(tmp_path: Path, monkeypatch) -> None:
    from bot import exchange_adapter

    fake = FakeAdapter(raw_orders=[_order()])
    monkeypatch.setattr(exchange_adapter, "BinancePerpAdapter", lambda credentials: fake)
    preview = adopt_protective_stop.run(args=_args(tmp_path))
    preview_file = tmp_path / "reports" / "protective_stop_adopt" / "latest_preview.json"

    confirmed = adopt_protective_stop.run(
        args=_args(
            tmp_path,
            confirm_token=preview["confirm_token"],
            preview_file=str(preview_file),
            max_preview_age_sec=-1,
        )
    )

    assert confirmed["state_written"] is False
    assert "preview_expired" in confirmed["blocked_reasons"]
