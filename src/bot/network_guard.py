from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

_CROWDING_ENTRY_DEGRADE_FLAGS = {
    "crowding_warning",
    "okx_longs_crowded",
    "okx_funding_crowded_longs",
    "coinglass_crowding_longs",
}

_RESEARCH_ENTRY_DEGRADE_FLAGS = {
    "research_degraded",
    "research_freshness_degraded",
    "insufficient_quality_folds",
    "low_passed_trade_share",
}


class GuardDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    judgement_status: str
    diagnostic_category: str = ""
    bundle_status: str = ""
    ready: bool = False
    allow_entry: bool = True
    allow_reduce: bool = True
    allow_exit: bool = True
    degraded: bool = False
    blocked: bool = False
    reason_codes: list[str] = Field(default_factory=list)


class NetworkGuard:
    def evaluate(
        self,
        *,
        judgement: dict[str, Any],
        handoff: dict[str, Any] | None,
    ) -> GuardDecision:
        judgement_status = str(judgement.get("status") or "")
        research_bundle = judgement.get("research_bundle") or {}
        bundle_status = str(research_bundle.get("bundle_status") or "")
        ready = bool(research_bundle.get("ready"))
        diagnostic = self._parse_diagnostic(str(judgement.get("diagnostic") or ""))
        diagnostic_category = str(diagnostic.get("category") or str((handoff or {}).get("diagnostic_category") or ""))
        risk_filter_status = str((handoff or {}).get("risk_filter_status") or "")
        runtime_vetoes = [str(item) for item in (handoff or {}).get("runtime_vetoes", [])]
        degrade_flags = [str(item) for item in (handoff or {}).get("degrade_flags", [])]
        research_gate_status = str((handoff or {}).get("research_gate_status") or "open")
        requested_action = str((handoff or {}).get("action") or "")
        requested_direction = str((handoff or {}).get("direction") or "")
        probe_source = str((handoff or {}).get("probe_source") or "")
        staleness_veto = bool((handoff or {}).get("staleness_veto"))
        conflict_veto = bool((handoff or {}).get("conflict_veto"))
        orderbook_short_pressure = bool((handoff or {}).get("orderbook_short_pressure"))

        allow_entry = True
        allow_reduce = True
        allow_exit = True
        degraded = False
        blocked = False
        reason_codes: list[str] = []

        if judgement_status != "ok":
            reason_codes.append("judgement_not_ok")
            allow_entry = False
            if diagnostic_category == "pipeline":
                blocked = True
                allow_reduce = False
                allow_exit = False
                reason_codes.append("pipeline_blocked")
            else:
                degraded = True

        if not ready:
            allow_entry = False
            degraded = True
            reason_codes.append("research_not_ready")

        if bundle_status and bundle_status not in {"healthy", "ready"}:
            allow_entry = False
            degraded = True
            reason_codes.append(f"bundle_status:{bundle_status}")

        if diagnostic_category in {"transport", "data_source"}:
            allow_entry = False
            degraded = True
            reason_codes.append(f"diagnostic:{diagnostic_category}")

        if diagnostic_category == "pipeline":
            blocked = True
            allow_entry = False
            allow_reduce = False
            allow_exit = False
            reason_codes.append("diagnostic:pipeline")

        if risk_filter_status in {"veto", "blocked"}:
            allow_entry = False
            reason_codes.append(f"risk_filter:{risk_filter_status}")
        elif risk_filter_status == "degraded":
            degraded = True

        if runtime_vetoes or staleness_veto or conflict_veto or research_gate_status == "blocked":
            allow_entry = False
            reason_codes.append("runtime_entry_veto")

        if (
            probe_source.strip().lower() == "contrarian_short_probe"
            and requested_action.strip().lower() == "small_probe"
            and requested_direction.strip().lower() == "short"
            and not orderbook_short_pressure
        ):
            allow_entry = False
            degraded = True
            reason_codes.append("contrarian_probe_orderbook_pressure_missing")

        if degrade_flags:
            degraded = True
            reason_codes.extend(f"degrade_flag:{flag}" for flag in degrade_flags)
            entry_veto_flags = [
                flag
                for flag in degrade_flags
                if not self._is_entry_allowed_degrade_flag(
                    flag,
                    requested_action=requested_action,
                    requested_direction=requested_direction,
                    probe_source=probe_source,
                    research_gate_status=research_gate_status,
                )
            ]
            if entry_veto_flags:
                allow_entry = False

        if blocked:
            degraded = False

        return GuardDecision(
            judgement_status=judgement_status,
            diagnostic_category=diagnostic_category,
            bundle_status=bundle_status,
            ready=ready,
            allow_entry=allow_entry,
            allow_reduce=allow_reduce,
            allow_exit=allow_exit,
            degraded=degraded,
            blocked=blocked,
            reason_codes=list(dict.fromkeys(reason_codes)),
        )

    @staticmethod
    def _is_entry_allowed_degrade_flag(
        flag: str,
        *,
        requested_action: str,
        requested_direction: str,
        probe_source: str,
        research_gate_status: str,
    ) -> bool:
        if NetworkGuard._is_crowding_short_entry_degrade_flag(
            flag,
            requested_action=requested_action,
            requested_direction=requested_direction,
        ):
            return True
        if NetworkGuard._is_trend_continuation_probe_crowding_flag(
            flag,
            requested_action=requested_action,
            probe_source=probe_source,
        ):
            return True
        normalized_flag = flag.strip().lower()
        return research_gate_status == "open" and normalized_flag in _RESEARCH_ENTRY_DEGRADE_FLAGS

    @staticmethod
    def _is_crowding_short_entry_degrade_flag(
        flag: str,
        *,
        requested_action: str,
        requested_direction: str,
    ) -> bool:
        normalized_flag = flag.strip().lower()
        normalized_action = requested_action.strip().lower()
        normalized_direction = requested_direction.strip().lower()
        if normalized_flag not in _CROWDING_ENTRY_DEGRADE_FLAGS:
            return False
        if normalized_action == "entry_short":
            return True
        return normalized_action == "small_probe" and normalized_direction == "short"

    @staticmethod
    def _is_trend_continuation_probe_crowding_flag(
        flag: str,
        *,
        requested_action: str,
        probe_source: str,
    ) -> bool:
        normalized_flag = flag.strip().lower()
        normalized_action = requested_action.strip().lower()
        normalized_probe_source = probe_source.strip().lower()
        return (
            normalized_flag in _CROWDING_ENTRY_DEGRADE_FLAGS
            and normalized_action == "small_probe"
            and normalized_probe_source == "trend_continuation_probe"
        )

    @staticmethod
    def _parse_diagnostic(diagnostic: str) -> dict[str, str]:
        segments = [segment.strip() for segment in diagnostic.split("|") if segment.strip()]
        parsed: dict[str, str] = {}
        for segment in segments:
            if "=" not in segment:
                continue
            key, value = segment.split("=", 1)
            parsed[key.strip()] = value.strip()
        return parsed
