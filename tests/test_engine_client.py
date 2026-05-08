import pytest
from datetime import datetime

from bot.config import BotConfig
from bot.engine_client import EngineClient


class FakeEnvelope:
    def __init__(self, payload: dict) -> None:
        self.payload = payload


class FakeHandoff:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def model_dump(self, *, mode: str) -> dict:
        assert mode == "json"
        return dict(self._payload)


def test_engine_client_requires_injected_engine_functions(tmp_path) -> None:
    with pytest.raises(RuntimeError, match="必须注入"):
        EngineClient(
            BotConfig(
                state_store_path=tmp_path / "state.json",
                audit_log_path=tmp_path / "audit.jsonl",
                artifacts_root=tmp_path / "runtime",
            )
        )


def test_engine_client_returns_handoff_when_judgement_is_ok(tmp_path) -> None:
    observed_calls: dict = {}

    def fake_run_live_judgement(**kwargs):
        observed_calls.update(kwargs)
        return {
            "status": "ok",
            "decision": {
                "generated_at": "2026-04-26T12:00:00",
                "symbol": "ETH",
                "timeframe": "15m",
                "decision": {"action": "entry_long"},
            },
            "research_bundle": {"ready": True, "bundle_status": "healthy"},
            "diagnostic": "",
        }

    def fake_envelope_factory(payload: dict) -> FakeEnvelope:
        return FakeEnvelope(payload)

    def fake_build_execution_handoff(envelope: FakeEnvelope) -> FakeHandoff:
        assert envelope.payload["decision"]["action"] == "entry_long"
        return FakeHandoff({"action": "entry_long", "position_state": "ARMED"})

    client = EngineClient(
        BotConfig(
            state_store_path=tmp_path / "state.json",
            audit_log_path=tmp_path / "audit.jsonl",
            artifacts_root=tmp_path / "runtime",
        ),
        run_live_judgement_fn=fake_run_live_judgement,
        build_execution_handoff_fn=fake_build_execution_handoff,
        decision_envelope_factory=fake_envelope_factory,
    )
    result = client.fetch_cycle(generated_at=datetime(2026, 4, 26, 12, 0, 0))
    assert result.handoff == {"action": "entry_long", "position_state": "ARMED"}
    assert observed_calls["mode"] == "strict-live"
    assert observed_calls["symbol"] == "ETH"
    assert observed_calls["consensus_mode"] == "auto"
    assert observed_calls["consensus_min_sources"] == 3
    assert observed_calls["consensus_request_timeout_sec"] == 10.0


def test_engine_client_skips_handoff_when_judgement_is_blocked(tmp_path) -> None:
    client = EngineClient(
        BotConfig(
            state_store_path=tmp_path / "state.json",
            audit_log_path=tmp_path / "audit.jsonl",
            artifacts_root=tmp_path / "runtime",
        ),
        run_live_judgement_fn=lambda **_: {
            "status": "blocked",
            "decision": None,
            "research_bundle": None,
            "diagnostic": "request_diagnostic=none | category=pipeline",
        },
        build_execution_handoff_fn=lambda envelope: envelope,
        decision_envelope_factory=lambda payload: payload,
    )
    result = client.fetch_cycle(generated_at=datetime(2026, 4, 26, 12, 0, 0))
    assert result.handoff is None
    assert result.judgement["status"] == "blocked"


def test_engine_client_fetch_cycle_reuses_current_position_state(tmp_path) -> None:
    observed_calls: dict = {}

    def fake_run_live_judgement(**kwargs):
        observed_calls.update(kwargs)
        return {
            "status": "blocked",
            "decision": None,
            "research_bundle": {"ready": True, "bundle_status": "healthy"},
            "diagnostic": "request_diagnostic=transport | category=transport",
        }

    client = EngineClient(
        BotConfig(
            state_store_path=tmp_path / "state.json",
            audit_log_path=tmp_path / "audit.jsonl",
            artifacts_root=tmp_path / "runtime",
        ),
        run_live_judgement_fn=fake_run_live_judgement,
        build_execution_handoff_fn=lambda envelope: envelope,
        decision_envelope_factory=lambda payload: payload,
    )
    client.fetch_cycle(
        current_state="ENTERED",
        current_position_size_pct=0.3,
        current_position_direction="long",
        generated_at=datetime(2026, 4, 26, 12, 5, 0),
    )
    assert observed_calls["current_state"] == "ENTERED"
    assert observed_calls["current_position_size_pct"] == 0.3
    assert observed_calls["current_position_direction"] == "long"
