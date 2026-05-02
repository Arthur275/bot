from __future__ import annotations

import json
from argparse import Namespace
from datetime import datetime
from pathlib import Path

from bot.exchange_adapter import AdapterRuntimeSnapshot, PositionSnapshot
from bot.network_guard import GuardDecision
from bot.state_store import StateStore
from scripts import preview_high_risk_handoff


class FakeAdapter:
    def __init__(self, *, snapshot: AdapterRuntimeSnapshot | None = None, raw_orders: list[dict] | None = None) -> None:
        self._snapshot = snapshot or _snapshot()
        self._raw_orders = raw_orders if raw_orders is not None else [_order()]

    def fetch_runtime_snapshot(self) -> AdapterRuntimeSnapshot:
        return self._snapshot

    def fetch_open_algo_orders_raw(self) -> list[dict]:
        return self._raw_orders


def _snapshot(*, direction: str = "long", amount: float = 0.043, mark_price: float = 2320.0) -> AdapterRuntimeSnapshot:
    return AdapterRuntimeSnapshot(
        fetched_at=datetime(2026, 5, 3, 1, 0, 0),
        position=PositionSnapshot(
            position_state="ENTERED",
            direction=direction,
            size_pct=0.5,
            position_amt=amount if direction == "long" else -amount,
            entry_price=2300.0,
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
        "triggerPrice": "2314.1",
        "reduceOnly": True,
        "closePosition": False,
    }
    payload.update(overrides)
    return payload


def _handoff(**overrides) -> dict:
    payload = {
        "version": 1,
        "handoff_id": "hr-1",
        "generated_at": "2099-05-03T01:00:00",
        "expires_at": "2099-05-03T01:05:00",
        "action": "reduce",
        "runtime_mode": "real",
        "engine_mode": "strict-live",
        "symbol": "ETH",
        "exchange_symbol": "ETHUSDT",
        "direction": "long",
        "position_state": "ENTERED",
        "risk_filter_status": "pass",
        "reduce_fraction": 0.5,
        "reason": "risk downgrade",
    }
    payload.update(overrides)
    return payload


def _args(tmp_path: Path, handoff_file: Path) -> Namespace:
    return Namespace(
        handoff_file=str(handoff_file),
        state_path=str(tmp_path / "shared_state" / "bot_state.json"),
        report_root=str(tmp_path / "reports" / "high_risk"),
        proxy_url="http://127.0.0.1:7897",
        api_key_env="BINANCE_TRADE_API_KEY",
        api_secret_env="BINANCE_TRADE_API_SECRET",
        kill_switch_path=str(tmp_path / "disable_real_execution.flag"),
        lock_path=str(tmp_path / "high_risk_action.lock"),
        json=False,
    )


def _write_handoff(tmp_path: Path, payload: dict) -> Path:
    path = tmp_path / "handoff.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _state_store(tmp_path: Path) -> StateStore:
    path = tmp_path / "shared_state" / "bot_state.json"
    store = StateStore(path)
    state = store.load()
    state.metadata["protective_stop"] = {"trigger_price": 2314.1, "lock_stage": 2}
    store.save(state)
    return store


def test_high_risk_preview_reports_reduce_expected_state(tmp_path: Path) -> None:
    handoff_file = _write_handoff(tmp_path, _handoff())

    payload = preview_high_risk_handoff.run(
        args=_args(tmp_path, handoff_file),
        adapter=FakeAdapter(),
        state_store=_state_store(tmp_path),
        network_decision=GuardDecision(judgement_status="ok", ready=True),
    )

    assert payload["blocked_reasons"] == []
    assert payload["execution_enabled"] is False
    expected = payload["expected_after_execution"]
    assert expected["position_after"]["position_amt"] == 0.0215
    assert expected["protective_stop_after"]["action"] == "rebuild_required"
    assert expected["protective_stop_after"]["quantity"] == 0.0215
    assert (tmp_path / "reports" / "high_risk" / "latest_preview.json").exists()


def test_high_risk_preview_blocks_multiple_protective_stops(tmp_path: Path) -> None:
    handoff_file = _write_handoff(tmp_path, _handoff())

    payload = preview_high_risk_handoff.run(
        args=_args(tmp_path, handoff_file),
        adapter=FakeAdapter(raw_orders=[_order(), _order(algoId=2, clientAlgoId="x")]),
        state_store=_state_store(tmp_path),
        network_decision=GuardDecision(judgement_status="ok", ready=True),
    )

    assert "multiple_protective_stops_manual_required" in payload["blocked_reasons"]


def test_high_risk_preview_uses_exchange_stop_for_trailing_gate(tmp_path: Path) -> None:
    handoff_file = _write_handoff(
        tmp_path,
        _handoff(
            action="trailing",
            reduce_fraction=None,
            trailing_rule={"activation_price": 2310.0, "callback_rate": 0.4},
        ),
    )

    payload = preview_high_risk_handoff.run(
        args=_args(tmp_path, handoff_file),
        adapter=FakeAdapter(raw_orders=[_order(triggerPrice="2314.1")]),
        state_store=_state_store(tmp_path),
        network_decision=GuardDecision(judgement_status="ok", ready=True),
    )

    assert payload["exchange_protective_stop"]["trigger_price"] == 2314.1
    assert "trailing_activation_below_exchange_stop" in payload["blocked_reasons"]


def test_high_risk_preview_marks_trailing_as_preview_only_when_gate_passes(tmp_path: Path) -> None:
    handoff_file = _write_handoff(
        tmp_path,
        _handoff(
            action="trailing",
            reduce_fraction=None,
            trailing_rule={"activation_price": 2340.0, "callback_rate": 0.4},
        ),
    )

    payload = preview_high_risk_handoff.run(
        args=_args(tmp_path, handoff_file),
        adapter=FakeAdapter(raw_orders=[_order(triggerPrice="2314.1")]),
        state_store=_state_store(tmp_path),
        network_decision=GuardDecision(judgement_status="ok", ready=True),
    )

    assert payload["blocked_reasons"] == []
    assert payload["execution_enabled"] is False
    expected = payload["expected_after_execution"]
    assert expected["protective_stop_after"]["fixed_stop_cancel_allowed"] is False
    assert expected["transition_coverage"]["coverage_plan_status"] == "preview_only"
