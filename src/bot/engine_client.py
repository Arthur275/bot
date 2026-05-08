from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from .config import BotConfig


class EngineCyclePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    judgement: dict[str, Any]
    handoff: dict[str, Any] | None = None


class EngineClient:
    def __init__(
        self,
        config: BotConfig,
        *,
        run_live_judgement_fn: Callable[..., dict[str, Any]] | None = None,
        build_execution_handoff_fn: Callable[[Any], Any] | None = None,
        decision_envelope_factory: Callable[[dict[str, Any]], Any] | None = None,
    ) -> None:
        self._config = config
        self._run_live_judgement = run_live_judgement_fn
        self._build_execution_handoff = build_execution_handoff_fn
        self._decision_envelope_factory = decision_envelope_factory
        if (
            self._run_live_judgement is None
            or self._build_execution_handoff is None
            or self._decision_envelope_factory is None
        ):
            raise RuntimeError("必须注入 live_judgement / build_execution_handoff 函数")

    def fetch_cycle(
        self,
        *,
        current_state: str = "FLAT",
        current_position_size_pct: float = 0.0,
        current_position_direction: str = "neutral",
        generated_at: datetime | None = None,
    ) -> EngineCyclePayload:
        judgement = self._run_live_judgement(
            symbol=self._config.symbol,
            timeframe=self._config.timeframe,
            mode=self._config.engine_mode.value,
            timeout_sec=self._config.timeout_sec,
            include_coinglass_overlay=self._config.resolved_include_coinglass_overlay,
            include_okx_overlay=self._config.include_okx_overlay,
            proxy_url=self._config.proxy_url,
            consensus_mode=self._config.consensus_mode,
            consensus_min_sources=self._config.consensus_min_sources,
            consensus_request_timeout_sec=self._config.consensus_request_timeout_sec,
            calibration_path=str(self._config.calibration_path) if self._config.calibration_path else None,
            sample_root=str(self._config.sample_root) if self._config.sample_root else None,
            research_sync_request_path=str(self._config.research_sync_request_path)
            if self._config.research_sync_request_path
            else None,
            research_dispatch_request_path=str(self._config.research_dispatch_request_path)
            if self._config.research_dispatch_request_path
            else None,
            current_state=current_state,
            current_position_size_pct=current_position_size_pct,
            current_position_direction=current_position_direction,
            generated_at=generated_at,
        )
        if judgement.get("status") != "ok" or not judgement.get("decision"):
            return EngineCyclePayload(judgement=judgement, handoff=None)

        envelope = self._decision_envelope_factory(dict(judgement["decision"]))
        handoff = self._build_execution_handoff(envelope)
        if hasattr(handoff, "model_dump"):
            handoff_payload = handoff.model_dump(mode="json")
        else:
            handoff_payload = dict(handoff)
        return EngineCyclePayload(judgement=judgement, handoff=handoff_payload)
