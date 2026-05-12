from types import SimpleNamespace

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
