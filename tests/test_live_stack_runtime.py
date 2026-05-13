from types import SimpleNamespace

from bot.config import BotConfig
from bot.exchange_adapter import AdapterCredentials, OkxUsdtSwapAdapter
from bot import live_stack_runtime


def test_runtime_resource_runs_main_cycle_through_static_engine_client() -> None:
    calls = {}
    report = SimpleNamespace(status="ok")

    class FakeOrchestrator:
        def __init__(self, config, *, engine_client, state_store, exchange_adapter) -> None:
            calls["config"] = config
            calls["engine_client"] = engine_client
            calls["state_store"] = state_store
            calls["exchange_adapter"] = exchange_adapter

        def run_cycle(self, *, generated_at):
            calls["generated_at"] = generated_at
            calls["cycle"] = self._engine_payload()
            return report

        def _engine_payload(self):
            return calls["engine_client"].fetch_cycle(current_state="FLAT")

    live_stack_runtime.ShadowOrchestrator = FakeOrchestrator
    runtime = live_stack_runtime.BotRuntimeResources(
        config=object(),
        adapter=object(),
        state_store=object(),
        engine_client=object(),
    )

    result = runtime.run_cycle(
        judgement={"status": "ok"},
        handoff={"action": "wait"},
        generated_at="now",
    )

    assert result is report
    assert calls["cycle"].judgement == {"status": "ok"}
    assert calls["cycle"].handoff == {"action": "wait"}
    assert calls["generated_at"] == "now"


def test_runtime_resource_loads_state_payload() -> None:
    state = SimpleNamespace(model_dump=lambda mode: {"mode": mode, "execution_state": "idle"})
    runtime = live_stack_runtime.BotRuntimeResources(
        config=object(),
        adapter=object(),
        state_store=SimpleNamespace(load=lambda: state),
        engine_client=object(),
    )

    assert runtime.load_state_payload() == {"mode": "json", "execution_state": "idle"}


def test_build_exchange_adapter_uses_okx_adapter_for_default_bot_config() -> None:
    config = BotConfig()
    credentials = AdapterCredentials(
        venue=config.exchange_venue,
        api_key_env=config.exchange_api_key_env,
        api_secret_env=config.exchange_api_secret_env,
        api_passphrase_env=config.exchange_api_passphrase_env,
        recv_window_ms=config.recv_window_ms,
        timeout_sec=config.timeout_sec,
        proxy_url=config.proxy_url,
        api_base_url=config.exchange_api_base_url,
    )

    adapter = live_stack_runtime._build_exchange_adapter(config, credentials)

    assert isinstance(adapter, OkxUsdtSwapAdapter)


def test_build_bot_runtime_passes_okx_passphrase_env_to_credentials(tmp_path, monkeypatch) -> None:
    captured = {}

    class FakeOkxAdapter:
        def __init__(self, credentials) -> None:
            captured["credentials"] = credentials

    monkeypatch.setattr(live_stack_runtime, "OkxUsdtSwapAdapter", FakeOkxAdapter)

    runtime = live_stack_runtime.build_bot_runtime(
        paths={
            "bot_state_path": tmp_path / "state.json",
            "bot_audit_path": tmp_path / "audit.jsonl",
            "bot_artifacts_dir": tmp_path / "artifacts",
        },
        proxy_url=None,
        run_live_judgement_fn=lambda **_: {"status": "blocked"},
        build_execution_handoff_fn=lambda envelope: {},
        decision_envelope_factory=lambda payload: payload,
    )

    assert runtime.adapter is captured["credentials"] or isinstance(runtime.adapter, FakeOkxAdapter)
    assert captured["credentials"].api_passphrase_env == "OKX_TRADE_PASSPHRASE"
