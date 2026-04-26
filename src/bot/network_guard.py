from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


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
        staleness_veto = bool((handoff or {}).get("staleness_veto"))
        conflict_veto = bool((handoff or {}).get("conflict_veto"))

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

        if risk_filter_status in {"degraded", "veto", "blocked"}:
            allow_entry = False
            degraded = risk_filter_status == "degraded" or degraded
            if risk_filter_status in {"veto", "blocked"}:
                reason_codes.append(f"risk_filter:{risk_filter_status}")

        if runtime_vetoes or staleness_veto or conflict_veto:
            allow_entry = False
            reason_codes.append("runtime_entry_veto")

        if degrade_flags:
            allow_entry = False
            degraded = True
            reason_codes.extend(f"degrade_flag:{flag}" for flag in degrade_flags)

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
    def _parse_diagnostic(diagnostic: str) -> dict[str, str]:
        segments = [segment.strip() for segment in diagnostic.split("|") if segment.strip()]
        parsed: dict[str, str] = {}
        for segment in segments:
            if "=" not in segment:
                continue
            key, value = segment.split("=", 1)
            parsed[key.strip()] = value.strip()
        return parsed
