from __future__ import annotations

import importlib
import sys
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
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
            self._load_engine_contracts(config.engine_src_path)

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
            include_coinglass_overlay=self._config.include_coinglass_overlay,
            include_okx_overlay=self._config.include_okx_overlay,
            proxy_url=self._config.proxy_url,
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

    def fetch_risk_cycle(
        self,
        *,
        current_state: str,
        current_position_size_pct: float,
        current_position_direction: str,
        generated_at: datetime | None = None,
    ) -> EngineCyclePayload:
        return self.fetch_cycle(
            current_state=current_state,
            current_position_size_pct=current_position_size_pct,
            current_position_direction=current_position_direction,
            generated_at=generated_at,
        )

    def _load_engine_contracts(self, engine_src_path: Path) -> None:
        normalized_engine_src_path = str(engine_src_path)
        if normalized_engine_src_path not in sys.path:
            sys.path.insert(0, normalized_engine_src_path)
        live_judgement_module = importlib.import_module("interfaces.live_judgement")
        runner_module = importlib.import_module("interfaces.runner")
        execution_contracts_module = importlib.import_module("contracts.execution")
        self._run_live_judgement = live_judgement_module.run_live_judgement
        self._build_execution_handoff = runner_module.build_execution_handoff
        self._decision_envelope_factory = execution_contracts_module.DecisionEnvelope.model_validate
