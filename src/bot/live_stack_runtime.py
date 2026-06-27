from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import BotConfig, EngineMode, RuntimeMode
from .engine_client import EngineClient, EngineCyclePayload
from .exchange_adapter import AdapterCredentials, BinancePerpAdapter, OkxUsdtSwapAdapter
from .orchestrator import ShadowOrchestrator
from .state_store import StateStore


class StaticEngineClient:
    def __init__(self, *, judgement: Mapping[str, Any], handoff: Mapping[str, Any] | None) -> None:
        self._judgement = dict(judgement)
        self._handoff = dict(handoff) if isinstance(handoff, Mapping) else None

    def fetch_cycle(self, **_: Any) -> EngineCyclePayload:
        return EngineCyclePayload(judgement=dict(self._judgement), handoff=self._handoff)

    def fetch_risk_cycle(self, **_: Any) -> EngineCyclePayload:
        return self.fetch_cycle()


@dataclass
class BotRuntimeResources:
    config: BotConfig
    adapter: BinancePerpAdapter | OkxUsdtSwapAdapter
    state_store: StateStore
    engine_client: EngineClient

    def run_cycle(
        self,
        *,
        judgement: Mapping[str, Any],
        handoff: Mapping[str, Any] | None,
        generated_at: datetime,
    ) -> Any:
        orchestrator = ShadowOrchestrator(
            self.config,
            engine_client=StaticEngineClient(judgement=judgement, handoff=handoff),
            state_store=self.state_store,
            exchange_adapter=self.adapter,
        )
        return orchestrator.run_cycle(generated_at=generated_at)

    def run_risk_assist_cycle(self, *, generated_at: datetime) -> Any:
        orchestrator = ShadowOrchestrator(
            self.config,
            engine_client=self.engine_client,
            state_store=self.state_store,
            exchange_adapter=self.adapter,
        )
        return orchestrator.run_risk_assist_cycle(generated_at=generated_at)

    def load_state_payload(self) -> dict[str, Any]:
        state = self.state_store.load()
        return state.model_dump(mode="json") if hasattr(state, "model_dump") else dict(state)


def _build_exchange_adapter(config: BotConfig, credentials: AdapterCredentials) -> BinancePerpAdapter | OkxUsdtSwapAdapter:
    if config.exchange_venue == "okx_usdt_swap":
        return OkxUsdtSwapAdapter(credentials)
    return BinancePerpAdapter(credentials)


def build_bot_runtime(
    *,
    paths: Mapping[str, Path],
    proxy_url: str | None,
    run_live_judgement_fn: Callable[..., dict[str, Any]],
    build_execution_handoff_fn: Callable[[Any], Any],
    decision_envelope_factory: Callable[[dict[str, Any]], Any],
) -> BotRuntimeResources:
    config = BotConfig(
        runtime_mode=RuntimeMode.REAL,
        engine_mode=EngineMode.STRICT_LIVE,
        proxy_url=proxy_url or None,
        state_store_path=paths["bot_state_path"],
        audit_log_path=paths["bot_audit_path"],
        artifacts_root=paths["bot_artifacts_dir"],
        incomplete_snapshot_status_dir=paths.get("incomplete_snapshot_status_dir"),
    )
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
    return BotRuntimeResources(
        config=config,
        adapter=_build_exchange_adapter(config, credentials),
        state_store=StateStore(config.state_store_path),
        engine_client=EngineClient(
            config,
            run_live_judgement_fn=run_live_judgement_fn,
            build_execution_handoff_fn=build_execution_handoff_fn,
            decision_envelope_factory=decision_envelope_factory,
        ),
    )
