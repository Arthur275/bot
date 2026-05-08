from __future__ import annotations

from argparse import Namespace
from datetime import datetime
from pathlib import Path

from bot.exchange_adapter import AdapterRuntimeSnapshot, PositionSnapshot
from bot.state_store import StateStore
from scripts import preview_protective_stop_replace


class FakeAdapter:
    def __init__(
        self,
        *,
        raw_orders: list[dict],
        snapshot: AdapterRuntimeSnapshot | None = None,
        raw_order_sequence: list[list[dict]] | None = None,
        snapshot_sequence: list[AdapterRuntimeSnapshot] | None = None,
        place_response: dict | None = None,
        place_raises: Exception | None = None,
    ) -> None:
        self._raw_orders = raw_orders
        self._raw_order_sequence = list(raw_order_sequence or [])
        self._snapshot_sequence = list(snapshot_sequence or [])
        self.cancel_calls = []
        self.place_calls = []
        self._place_response = place_response
        self._place_raises = place_raises
        self._snapshot = snapshot or AdapterRuntimeSnapshot(
            fetched_at=datetime.now().replace(microsecond=0),
            position=PositionSnapshot(
                position_state="ENTERED",
                direction="long",
                size_pct=0.9,
                position_amt=0.043,
                entry_price=2300.26,
                mark_price=2325.0,
                leverage=10,
            ),
            protective_stop_present=True,
        )

    def fetch_runtime_snapshot(self) -> AdapterRuntimeSnapshot:
        if self._snapshot_sequence:
            return self._snapshot_sequence.pop(0)
        return self._snapshot

    def fetch_open_algo_orders_raw(self) -> list[dict]:
        if self._raw_order_sequence:
            return self._raw_order_sequence.pop(0)
        return self._raw_orders

    def cancel_algo_order_raw(self, *, algo_id: str = "", client_algo_id: str = "") -> dict:
        self.cancel_calls.append({"algo_id": algo_id, "client_algo_id": client_algo_id})
        return {"algoId": algo_id, "algoStatus": "CANCELED"}

    def place_algo_order_raw(self, *, params: dict) -> dict:
        if self._place_raises:
            raise self._place_raises
        self.place_calls.append(dict(params))
        return self._place_response or {
            "algoId": 2000000000000000,
            "clientAlgoId": params["clientAlgoId"],
            "algoStatus": "NEW",
        }


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


def _new_order(**overrides):
    payload = _order(
        algoId=2000000000000000,
        clientAlgoId="ethbotpsreplace20260502181854",
        triggerPrice="2307.2",
    )
    payload.update(overrides)
    return payload


def _snapshot(*, position_state: str = "ENTERED", position_amt: float = 0.043) -> AdapterRuntimeSnapshot:
    return AdapterRuntimeSnapshot(
        fetched_at=datetime.now().replace(microsecond=0),
        position=PositionSnapshot(
            position_state=position_state,
            direction="long" if position_state != "FLAT" else "neutral",
            size_pct=0.9 if position_state != "FLAT" else 0.0,
            position_amt=position_amt if position_state != "FLAT" else 0.0,
            entry_price=2300.26,
            mark_price=2325.0,
            leverage=10,
        ),
        protective_stop_present=position_state != "FLAT",
    )


def _args(tmp_path: Path, **overrides) -> Namespace:
    values = {
        "state_path": str(tmp_path / "shared_state" / "bot_state.json"),
        "report_root": str(tmp_path / "reports" / "protective_stop_replace"),
        "proxy_url": "http://127.0.0.1:7897",
        "api_key_env": "BINANCE_TRADE_API_KEY",
        "api_secret_env": "BINANCE_TRADE_API_SECRET",
        "api_passphrase_env": "",
        "target_mode": "ratchet",
        "min_profit_lock_pct": 0.003,
        "min_mark_buffer_pct": 0.005,
        "snapshot_max_age_sec": 30,
        "preview_file": "",
        "confirm_token": "",
        "max_preview_age_sec": 180,
        "accept_gap_risk": False,
        "allow_missing_repair": False,
        "json": False,
    }
    values.update(overrides)
    return Namespace(**values)


def _save_adopted_state(path: Path, **overrides) -> None:
    store = StateStore(path)
    state = store.load()
    record = {
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
        "position_amt_at_adopt": 0.043,
        "position_direction_at_adopt": "long",
        "entry_price_at_adopt": 2300.26,
        "adopted_from": "exchange_open_algo_orders",
        "preview_created_at": "2026-05-02T17:55:54",
        "confirmed_at": "2026-05-02T17:56:07",
    }
    record.update(overrides)
    state.metadata["protective_stop"] = record
    store.save(state)


def test_replace_preview_ready_renders_risk_and_dry_run_requests(tmp_path: Path, monkeypatch) -> None:
    from bot import exchange_adapter

    state_path = tmp_path / "shared_state" / "bot_state.json"
    _save_adopted_state(state_path)
    fake = FakeAdapter(
        raw_orders=[_order()],
        raw_order_sequence=[[ _order() ], [], [_new_order()]],
        snapshot_sequence=[_snapshot(), _snapshot(), _snapshot()],
    )
    monkeypatch.setattr(exchange_adapter, "BinancePerpAdapter", lambda credentials: fake)

    payload = preview_protective_stop_replace.run(args=_args(tmp_path, state_path=str(state_path)))

    assert payload["replace_ready"] is True
    assert payload["blocked_reasons"] == []
    assert payload["risk_change"]["direction_label"] == "SAFER"
    assert payload["risk_change"]["target_stop_price"] == 2307.2
    assert payload["request_preview"]["cancel"]["path"] == "/fapi/v1/algoOrder"
    assert payload["request_preview"]["cancel"]["method"] == "DELETE"
    assert payload["request_preview"]["cancel"]["params"]["algoId"] == "1000001522632139"
    place = payload["request_preview"]["place"]
    assert place["path"] == "/fapi/v1/algoOrder"
    assert place["params"]["triggerPrice"] == "2307.2"
    assert place["params"]["quantity"] == "0.043"
    assert payload["confirm_token"].startswith("REPLACE-")
    assert "--accept-gap-risk" in payload["confirm_command"]
    panel = preview_protective_stop_replace.render_panel(payload)
    assert "Position / PnL" in panel
    assert "Dry-run REST Requests" in panel
    assert "Cancel -> Place Gap Risk" in panel
    assert "CONFIRM COMMAND" in panel


def test_ratchet_advances_only_to_next_stage(tmp_path: Path, monkeypatch) -> None:
    from bot import exchange_adapter

    state_path = tmp_path / "shared_state" / "bot_state.json"
    _save_adopted_state(
        state_path,
        trigger_price=2307.2,
        lock_stage=1,
        lock_target_price=2307.2,
        lock_target_pct=0.003,
    )
    snapshot = AdapterRuntimeSnapshot(
        fetched_at=datetime.now().replace(microsecond=0),
        position=PositionSnapshot(
            position_state="ENTERED",
            direction="long",
            size_pct=0.9,
            position_amt=0.043,
            entry_price=2300.26,
            mark_price=2322.0,
            leverage=10,
        ),
        protective_stop_present=True,
    )
    fake = FakeAdapter(raw_orders=[_order(triggerPrice="2307.2")], snapshot=snapshot)
    monkeypatch.setattr(exchange_adapter, "BinancePerpAdapter", lambda credentials: fake)

    payload = preview_protective_stop_replace.run(args=_args(tmp_path, state_path=str(state_path)))

    assert payload["replace_ready"] is True
    assert payload["risk_change"]["current_lock_stage"] == 1
    assert payload["risk_change"]["target_lock_stage"] == 2
    assert payload["risk_change"]["target_stop_price"] == 2314.1
    assert payload["request_preview"]["place"]["params"]["triggerPrice"] == "2314.1"


def test_ratchet_does_not_move_stop_down_on_pullback(tmp_path: Path, monkeypatch) -> None:
    from bot import exchange_adapter

    state_path = tmp_path / "shared_state" / "bot_state.json"
    _save_adopted_state(
        state_path,
        trigger_price=2314.1,
        lock_stage=2,
        lock_target_price=2314.1,
        lock_target_pct=0.006,
    )
    snapshot = AdapterRuntimeSnapshot(
        fetched_at=datetime.now().replace(microsecond=0),
        position=PositionSnapshot(
            position_state="ENTERED",
            direction="long",
            size_pct=0.9,
            position_amt=0.043,
            entry_price=2300.26,
            mark_price=2314.0,
            leverage=10,
        ),
        protective_stop_present=True,
    )
    fake = FakeAdapter(raw_orders=[_order(triggerPrice="2314.1")], snapshot=snapshot)
    monkeypatch.setattr(exchange_adapter, "BinancePerpAdapter", lambda credentials: fake)

    payload = preview_protective_stop_replace.run(args=_args(tmp_path, state_path=str(state_path)))

    assert payload["replace_ready"] is False
    assert "ratchet_stage_not_advanced" in payload["blocked_reasons"]
    assert payload["risk_change"]["current_lock_stage"] == 2
    assert payload["risk_change"]["target_lock_stage"] == 2
    assert payload["risk_change"]["target_stop_price"] == 2314.1


def test_replace_preview_blocks_missing_adopted_record(tmp_path: Path, monkeypatch) -> None:
    from bot import exchange_adapter

    fake = FakeAdapter(
        raw_orders=[_order()],
        raw_order_sequence=[[_order()], [_order()], [], [_new_order()]],
        snapshot_sequence=[_snapshot(), _snapshot(), _snapshot()],
    )
    monkeypatch.setattr(exchange_adapter, "BinancePerpAdapter", lambda credentials: fake)

    payload = preview_protective_stop_replace.run(args=_args(tmp_path))

    assert payload["replace_ready"] is False
    assert payload["blocked_reasons"] == ["recorded_protective_stop_missing"]


def test_replace_preview_blocks_algo_id_mismatch(tmp_path: Path, monkeypatch) -> None:
    from bot import exchange_adapter

    state_path = tmp_path / "shared_state" / "bot_state.json"
    _save_adopted_state(state_path, algo_id="old")
    fake = FakeAdapter(
        raw_orders=[_order()],
        raw_order_sequence=[[_order()], [_order()], [], [_new_order()]],
        snapshot_sequence=[_snapshot(), _snapshot(), _snapshot()],
    )
    monkeypatch.setattr(exchange_adapter, "BinancePerpAdapter", lambda credentials: fake)

    payload = preview_protective_stop_replace.run(args=_args(tmp_path, state_path=str(state_path)))

    assert payload["replace_ready"] is False
    assert "algo_id_mismatch" in payload["blocked_reasons"]


def test_replace_preview_blocks_multiple_open_algo_orders(tmp_path: Path, monkeypatch) -> None:
    from bot import exchange_adapter

    state_path = tmp_path / "shared_state" / "bot_state.json"
    _save_adopted_state(state_path)
    fake = FakeAdapter(raw_orders=[_order(algoId=1), _order(algoId=2, clientAlgoId="ghost")])
    monkeypatch.setattr(exchange_adapter, "BinancePerpAdapter", lambda credentials: fake)

    payload = preview_protective_stop_replace.run(args=_args(tmp_path, state_path=str(state_path)))

    assert payload["replace_ready"] is False
    assert "multiple_active_protective_algo_orders" in payload["blocked_reasons"]


def test_replace_preview_blocks_when_mark_buffer_is_not_met(tmp_path: Path, monkeypatch) -> None:
    from bot import exchange_adapter

    state_path = tmp_path / "shared_state" / "bot_state.json"
    _save_adopted_state(state_path)
    snapshot = AdapterRuntimeSnapshot(
        fetched_at=datetime.now().replace(microsecond=0),
        position=PositionSnapshot(
            position_state="ENTERED",
            direction="long",
            size_pct=0.9,
            position_amt=0.043,
            entry_price=2300.26,
            mark_price=2305.0,
            leverage=10,
        ),
        protective_stop_present=True,
    )
    fake = FakeAdapter(raw_orders=[_order()], snapshot=snapshot)
    monkeypatch.setattr(exchange_adapter, "BinancePerpAdapter", lambda credentials: fake)

    payload = preview_protective_stop_replace.run(args=_args(tmp_path, state_path=str(state_path)))

    assert payload["replace_ready"] is False
    assert "min_mark_buffer_not_met" in payload["blocked_reasons"]


def test_replace_confirm_requires_gap_risk_acceptance(tmp_path: Path, monkeypatch) -> None:
    from bot import exchange_adapter

    state_path = tmp_path / "shared_state" / "bot_state.json"
    _save_adopted_state(state_path)
    fake = FakeAdapter(raw_orders=[_order()])
    monkeypatch.setattr(exchange_adapter, "BinancePerpAdapter", lambda credentials: fake)
    preview = preview_protective_stop_replace.run(args=_args(tmp_path, state_path=str(state_path)))
    preview_file = tmp_path / "reports" / "protective_stop_replace" / "latest_preview.json"

    confirmed = preview_protective_stop_replace.run(
        args=_args(
            tmp_path,
            state_path=str(state_path),
            preview_file=str(preview_file),
            confirm_token=preview["confirm_token"],
            accept_gap_risk=False,
        )
    )

    assert confirmed["replace_ready"] is False
    assert "gap_risk_not_accepted" in confirmed["blocked_reasons"]
    assert fake.cancel_calls == []
    assert fake.place_calls == []


def test_replace_confirm_executes_cancel_place_and_updates_state(tmp_path: Path, monkeypatch) -> None:
    from bot import exchange_adapter

    state_path = tmp_path / "shared_state" / "bot_state.json"
    _save_adopted_state(state_path)
    fake = FakeAdapter(
        raw_orders=[_order()],
        raw_order_sequence=[[_order()], [_order()], [], [_new_order()]],
        snapshot_sequence=[_snapshot(), _snapshot(), _snapshot()],
    )
    monkeypatch.setattr(exchange_adapter, "BinancePerpAdapter", lambda credentials: fake)
    preview = preview_protective_stop_replace.run(args=_args(tmp_path, state_path=str(state_path)))
    preview_file = tmp_path / "reports" / "protective_stop_replace" / "latest_preview.json"

    confirmed = preview_protective_stop_replace.run(
        args=_args(
            tmp_path,
            state_path=str(state_path),
            preview_file=str(preview_file),
            confirm_token=preview["confirm_token"],
            accept_gap_risk=True,
        )
    )

    assert confirmed["state_written"] is True
    assert "request_preview_changed" not in confirmed["blocked_reasons"]
    assert confirmed["cancel_verify"]["verified"] is True
    assert confirmed["place_verify"]["verified"] is True
    assert fake.cancel_calls == [{"algo_id": "1000001522632139", "client_algo_id": ""}]
    assert fake.place_calls[0]["triggerPrice"] == "2307.2"
    assert fake.place_calls[0]["clientAlgoId"] == preview["request_preview"]["place"]["params"]["clientAlgoId"]
    state = StateStore(state_path).load()
    record = state.metadata["protective_stop"]
    assert record["algo_id"] == "2000000000000000"
    assert record["previous_algo_id"] == "1000001522632139"
    assert record["trigger_price"] == 2307.2
    assert record["lock_stage"] == 1
    assert record["lock_target_price"] == 2307.2


def test_missing_repair_places_without_cancel_and_writes_ratchet_stage(tmp_path: Path, monkeypatch) -> None:
    from bot import exchange_adapter

    state_path = tmp_path / "shared_state" / "bot_state.json"
    _save_adopted_state(
        state_path,
        trigger_price=2307.2,
        lock_stage=1,
        lock_target_price=2307.2,
        lock_target_pct=0.003,
    )
    snapshot = AdapterRuntimeSnapshot(
        fetched_at=datetime.now().replace(microsecond=0),
        position=PositionSnapshot(
            position_state="ENTERED",
            direction="long",
            size_pct=0.9,
            position_amt=0.043,
            entry_price=2300.26,
            mark_price=2322.0,
            leverage=10,
        ),
        protective_stop_present=False,
    )
    fake = FakeAdapter(
        raw_orders=[],
        raw_order_sequence=[[], [], [_new_order(triggerPrice="2314.1")]],
        snapshot_sequence=[snapshot, snapshot],
    )
    monkeypatch.setattr(exchange_adapter, "BinancePerpAdapter", lambda credentials: fake)
    preview = preview_protective_stop_replace.run(
        args=_args(tmp_path, state_path=str(state_path), allow_missing_repair=True)
    )
    preview_file = tmp_path / "reports" / "protective_stop_replace" / "latest_preview.json"

    confirmed = preview_protective_stop_replace.run(
        args=_args(
            tmp_path,
            state_path=str(state_path),
            preview_file=str(preview_file),
            confirm_token=preview["confirm_token"],
            accept_gap_risk=True,
            allow_missing_repair=True,
        )
    )

    assert preview["repair_missing"] is True
    assert "cancel" not in preview["request_preview"]
    assert confirmed["state_written"] is True
    assert fake.cancel_calls == []
    assert fake.place_calls[0]["triggerPrice"] == "2314.1"
    state = StateStore(state_path).load()
    record = state.metadata["protective_stop"]
    assert record["adopted_from"] == "missing_repair_place_only"
    assert record["lock_stage"] == 2
    assert record["lock_target_price"] == 2314.1


def test_replace_confirm_aborts_when_old_stop_is_still_active_after_cancel(tmp_path: Path, monkeypatch) -> None:
    from bot import exchange_adapter

    state_path = tmp_path / "shared_state" / "bot_state.json"
    _save_adopted_state(state_path)
    fake = FakeAdapter(
        raw_orders=[_order()],
        raw_order_sequence=[[_order()], [_order()], [_order()], [_order()]],
        snapshot_sequence=[_snapshot(), _snapshot()],
    )
    monkeypatch.setattr(exchange_adapter, "BinancePerpAdapter", lambda credentials: fake)
    preview = preview_protective_stop_replace.run(args=_args(tmp_path, state_path=str(state_path)))
    preview_file = tmp_path / "reports" / "protective_stop_replace" / "latest_preview.json"

    confirmed = preview_protective_stop_replace.run(
        args=_args(
            tmp_path,
            state_path=str(state_path),
            preview_file=str(preview_file),
            confirm_token=preview["confirm_token"],
            accept_gap_risk=True,
        )
    )

    assert confirmed["state_written"] is False
    assert "old_stop_still_active" in confirmed["blocked_reasons"]
    assert fake.place_calls == []
    state = StateStore(state_path).load()
    assert state.protective_stop_required is False


def test_replace_confirm_aborts_when_position_closed_after_cancel(tmp_path: Path, monkeypatch) -> None:
    from bot import exchange_adapter

    state_path = tmp_path / "shared_state" / "bot_state.json"
    _save_adopted_state(state_path)
    fake = FakeAdapter(
        raw_orders=[_order()],
        raw_order_sequence=[[_order()], [_order()], []],
        snapshot_sequence=[_snapshot(), _snapshot(), _snapshot(position_state="FLAT")],
    )
    monkeypatch.setattr(exchange_adapter, "BinancePerpAdapter", lambda credentials: fake)
    preview = preview_protective_stop_replace.run(args=_args(tmp_path, state_path=str(state_path)))
    preview_file = tmp_path / "reports" / "protective_stop_replace" / "latest_preview.json"

    confirmed = preview_protective_stop_replace.run(
        args=_args(
            tmp_path,
            state_path=str(state_path),
            preview_file=str(preview_file),
            confirm_token=preview["confirm_token"],
            accept_gap_risk=True,
        )
    )

    assert confirmed["state_written"] is False
    assert "position_closed_after_cancel" in confirmed["blocked_reasons"]
    assert fake.place_calls == []


def test_replace_confirm_marks_recovery_when_place_fails_after_cancel_verified(tmp_path: Path, monkeypatch) -> None:
    from bot import exchange_adapter

    state_path = tmp_path / "shared_state" / "bot_state.json"
    _save_adopted_state(state_path)
    fake = FakeAdapter(
        raw_orders=[_order()],
        raw_order_sequence=[[_order()], [_order()], []],
        snapshot_sequence=[_snapshot(), _snapshot(), _snapshot()],
        place_raises=RuntimeError("place failed"),
    )
    monkeypatch.setattr(exchange_adapter, "BinancePerpAdapter", lambda credentials: fake)
    preview = preview_protective_stop_replace.run(args=_args(tmp_path, state_path=str(state_path)))
    preview_file = tmp_path / "reports" / "protective_stop_replace" / "latest_preview.json"

    confirmed = preview_protective_stop_replace.run(
        args=_args(
            tmp_path,
            state_path=str(state_path),
            preview_file=str(preview_file),
            confirm_token=preview["confirm_token"],
            accept_gap_risk=True,
        )
    )

    assert confirmed["state_written"] is False
    assert "place_failed" in confirmed["blocked_reasons"]
    state = StateStore(state_path).load()
    assert state.recovery_required is True
    assert state.reconciliation_required is True
    assert state.protective_stop_required is True


def test_replace_confirm_requires_verified_new_stop_before_state_write(tmp_path: Path, monkeypatch) -> None:
    from bot import exchange_adapter

    state_path = tmp_path / "shared_state" / "bot_state.json"
    _save_adopted_state(state_path)
    fake = FakeAdapter(
        raw_orders=[_order()],
        raw_order_sequence=[[_order()], [_order()], [], [_new_order(triggerPrice="2308.0")]],
        snapshot_sequence=[_snapshot(), _snapshot(), _snapshot()],
    )
    monkeypatch.setattr(exchange_adapter, "BinancePerpAdapter", lambda credentials: fake)
    preview = preview_protective_stop_replace.run(args=_args(tmp_path, state_path=str(state_path)))
    preview_file = tmp_path / "reports" / "protective_stop_replace" / "latest_preview.json"

    confirmed = preview_protective_stop_replace.run(
        args=_args(
            tmp_path,
            state_path=str(state_path),
            preview_file=str(preview_file),
            confirm_token=preview["confirm_token"],
            accept_gap_risk=True,
        )
    )

    assert confirmed["state_written"] is False
    assert "place_unverified" in confirmed["blocked_reasons"]
    assert confirmed["place_verify"]["mismatches"] == ["trigger_price"]
    state = StateStore(state_path).load()
    assert state.protective_stop_required is True
