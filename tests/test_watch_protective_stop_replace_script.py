from __future__ import annotations

from argparse import Namespace
from datetime import datetime, timedelta
from pathlib import Path

from bot.exchange_adapter import AdapterRuntimeSnapshot, PositionSnapshot
from bot.state_store import StateStore
from scripts import watch_protective_stop_replace


class FakeAdapter:
    def __init__(self, *, raw_orders: list[dict], snapshot: AdapterRuntimeSnapshot) -> None:
        self._raw_orders = raw_orders
        self._snapshot = snapshot
        self.cancel_calls = []
        self.place_calls = []

    def fetch_runtime_snapshot(self) -> AdapterRuntimeSnapshot:
        return self._snapshot

    def fetch_open_algo_orders_raw(self) -> list[dict]:
        return self._raw_orders


def _snapshot(*, mark_price: float = 2313.0, position_state: str = "ENTERED") -> AdapterRuntimeSnapshot:
    return AdapterRuntimeSnapshot(
        fetched_at=datetime.now().replace(microsecond=0),
        position=PositionSnapshot(
            position_state=position_state,
            direction="long",
            size_pct=0.9,
            position_amt=0.043,
            entry_price=2300.26,
            mark_price=mark_price,
            leverage=10,
        ),
        protective_stop_present=True,
    )


def _order(**overrides) -> dict:
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


def _state_store(path: Path) -> StateStore:
    store = StateStore(path)
    state = store.load()
    state.metadata["protective_stop"] = {
        "version": 1,
        "venue": "binance_usdt_perp",
        "symbol": "ETHUSDT",
        "algo_id": "1000001522632139",
        "client_algo_id": "ethbotps20260502142000",
        "side": "SELL",
        "order_type": "STOP_MARKET",
        "algo_status": "NEW",
        "trigger_price": 2264.6,
        "close_position": False,
        "quantity": 0.043,
    }
    store.save(state)
    return store


def test_watch_ready_prints_preview_command(tmp_path: Path) -> None:
    store = _state_store(tmp_path / "shared_state" / "bot_state.json")
    adapter = FakeAdapter(raw_orders=[_order()], snapshot=_snapshot(mark_price=2313.0))

    result = watch_protective_stop_replace.evaluate_once(
        adapter=adapter,
        state_store=store,
        now=datetime(2026, 5, 2, 18, 30, 0),
        state_path=tmp_path / "shared_state" / "bot_state.json",
        report_root=tmp_path / "reports" / "protective_stop_replace",
        ready_buffer_pct=0.005,
        reset_buffer_pct=0.003,
        tight_stop_distance_pct=0.008,
    )

    assert result["status"] == "ready"
    assert result["buffer_pct"] > 0.005
    assert "preview_protective_stop_replace.py" in result["preview_command"]
    panel = watch_protective_stop_replace.render_watch_line(
        result=result,
        action=watch_protective_stop_replace.WatchAction(should_print=True),
    )
    assert "[READY TO REPLACE]" in panel
    assert "PREVIEW COMMAND" in panel


def test_watch_uses_next_ratchet_stage_buffer(tmp_path: Path) -> None:
    store = _state_store(tmp_path / "shared_state" / "bot_state.json")
    state = store.load()
    state.metadata["protective_stop"]["lock_stage"] = 1
    state.metadata["protective_stop"]["lock_target_price"] = 2307.2
    state.metadata["protective_stop"]["lock_target_pct"] = 0.003
    state.metadata["protective_stop"]["trigger_price"] = 2307.2
    store.save(state)
    adapter = FakeAdapter(raw_orders=[_order(triggerPrice="2307.2")], snapshot=_snapshot(mark_price=2314.0))

    result = watch_protective_stop_replace.evaluate_once(
        adapter=adapter,
        state_store=store,
        now=datetime(2026, 5, 2, 18, 30, 0),
        state_path=tmp_path / "shared_state" / "bot_state.json",
        report_root=tmp_path / "reports" / "protective_stop_replace",
        ready_buffer_pct=0.005,
        reset_buffer_pct=0.003,
        tight_stop_distance_pct=0.008,
    )

    assert result["status"] == "watching"
    assert result["ready_buffer_pct"] == 0.009


def test_watch_can_preview_missing_repair(tmp_path: Path) -> None:
    store = _state_store(tmp_path / "shared_state" / "bot_state.json")
    adapter = FakeAdapter(raw_orders=[], snapshot=_snapshot(mark_price=2313.0))

    result = watch_protective_stop_replace.evaluate_once(
        adapter=adapter,
        state_store=store,
        now=datetime(2026, 5, 2, 18, 30, 0),
        state_path=tmp_path / "shared_state" / "bot_state.json",
        report_root=tmp_path / "reports" / "protective_stop_replace",
        ready_buffer_pct=0.005,
        reset_buffer_pct=0.003,
        tight_stop_distance_pct=0.008,
        allow_missing_repair=True,
    )

    assert result["status"] == "ready"
    assert result["repair_missing"] is True
    assert "--allow-missing-repair" in result["preview_command"]


def test_watch_state_alerts_once_until_reset() -> None:
    state = watch_protective_stop_replace.WatchState()
    ready = {"status": "ready"}
    reset = {"status": "reset"}
    now = datetime(2026, 5, 2, 18, 30, 0)

    first = state.update(result=ready, now=now, heartbeat_every_sec=300)
    second = state.update(result=ready, now=now + timedelta(seconds=30), heartbeat_every_sec=300)
    reset_action = state.update(result=reset, now=now + timedelta(seconds=60), heartbeat_every_sec=300)
    third = state.update(result=ready, now=now + timedelta(seconds=90), heartbeat_every_sec=300)

    assert first.should_print is True
    assert second.should_print is False
    assert reset_action.should_print is True
    assert third.should_print is True


def test_watch_reset_below_hysteresis_band(tmp_path: Path) -> None:
    store = _state_store(tmp_path / "shared_state" / "bot_state.json")
    adapter = FakeAdapter(raw_orders=[_order()], snapshot=_snapshot(mark_price=2306.0))

    result = watch_protective_stop_replace.evaluate_once(
        adapter=adapter,
        state_store=store,
        now=datetime(2026, 5, 2, 18, 30, 0),
        state_path=tmp_path / "shared_state" / "bot_state.json",
        report_root=tmp_path / "reports" / "protective_stop_replace",
        ready_buffer_pct=0.005,
        reset_buffer_pct=0.003,
        tight_stop_distance_pct=0.008,
    )

    assert result["status"] == "reset"
    assert result["buffer_pct"] < 0.003


def test_watch_flat_position_stops(tmp_path: Path) -> None:
    store = _state_store(tmp_path / "shared_state" / "bot_state.json")
    adapter = FakeAdapter(raw_orders=[_order()], snapshot=_snapshot(position_state="FLAT"))

    result = watch_protective_stop_replace.evaluate_once(
        adapter=adapter,
        state_store=store,
        now=datetime(2026, 5, 2, 18, 30, 0),
        state_path=tmp_path / "shared_state" / "bot_state.json",
        report_root=tmp_path / "reports" / "protective_stop_replace",
        ready_buffer_pct=0.005,
        reset_buffer_pct=0.003,
        tight_stop_distance_pct=0.008,
    )

    assert result["status"] == "closed"
    assert "Position closed" in watch_protective_stop_replace.render_watch_line(
        result=result,
        action=watch_protective_stop_replace.WatchAction(should_print=True),
    )


def test_watch_blocks_algo_id_mismatch(tmp_path: Path) -> None:
    store = _state_store(tmp_path / "shared_state" / "bot_state.json")
    adapter = FakeAdapter(raw_orders=[_order(algoId=999)], snapshot=_snapshot(mark_price=2313.0))

    result = watch_protective_stop_replace.evaluate_once(
        adapter=adapter,
        state_store=store,
        now=datetime(2026, 5, 2, 18, 30, 0),
        state_path=tmp_path / "shared_state" / "bot_state.json",
        report_root=tmp_path / "reports" / "protective_stop_replace",
        ready_buffer_pct=0.005,
        reset_buffer_pct=0.003,
        tight_stop_distance_pct=0.008,
    )

    assert result["status"] == "blocked"
    assert result["blocked_reasons"] == ["algo_id_mismatch"]


def test_auto_confirm_replace_requires_gap_risk_acceptance(tmp_path: Path) -> None:
    args = Namespace(
        accept_gap_risk=False,
        watch_report_root=str(tmp_path / "watch"),
    )

    payload = watch_protective_stop_replace.auto_confirm_replace(args=args)

    assert payload["state_written"] is False
    assert payload["blocked_reasons"] == ["gap_risk_not_accepted"]
    assert (tmp_path / "watch" / "latest_auto_confirm.json").exists()


def test_auto_confirm_replace_uses_preview_then_confirm(tmp_path: Path, monkeypatch) -> None:
    calls = []

    def fake_run_replace_preview(*, args):
        calls.append(args)
        if not args.confirm_token:
            return {"replace_ready": True, "confirm_token": "REPLACE-OK", "blocked_reasons": []}
        return {
            "state_written": True,
            "blocked_reasons": [],
            "new_protective_stop_record": {"algo_id": "200", "trigger_price": 2307.2},
        }

    monkeypatch.setattr(watch_protective_stop_replace, "run_replace_preview", fake_run_replace_preview)
    args = Namespace(
        accept_gap_risk=True,
        watch_report_root=str(tmp_path / "watch"),
        state_path=str(tmp_path / "state.json"),
        report_root=str(tmp_path / "reports"),
        proxy_url="http://127.0.0.1:7897",
        api_key_env="BINANCE_TRADE_API_KEY",
        api_secret_env="BINANCE_TRADE_API_SECRET",
        api_passphrase_env="",
        ready_buffer_pct=0.005,
        max_preview_age_sec=180,
    )

    payload = watch_protective_stop_replace.auto_confirm_replace(args=args)

    assert payload["state_written"] is True
    assert len(calls) == 2
    assert calls[0].confirm_token == ""
    assert calls[1].confirm_token == "REPLACE-OK"
    assert calls[1].accept_gap_risk is True
    assert "AUTO REPLACE CONFIRMED" in watch_protective_stop_replace.render_auto_confirm_line(payload)


def test_auto_confirm_replace_respects_existing_lock(tmp_path: Path) -> None:
    watch_root = tmp_path / "watch"
    watch_root.mkdir(parents=True)
    (watch_root / "auto_replace.lock").write_text("locked", encoding="utf-8")
    args = Namespace(
        accept_gap_risk=True,
        watch_report_root=str(watch_root),
    )

    payload = watch_protective_stop_replace.auto_confirm_replace(args=args)

    assert payload["state_written"] is False
    assert payload["blocked_reasons"] == ["auto_replace_lock_exists"]
