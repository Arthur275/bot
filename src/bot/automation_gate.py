from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .action_enums import PositionAction
from .config import DEFAULT_KILL_SWITCH_PATH
from .execution_risk_gate import DEFAULT_FACTOR_LOOKUP_STALE_AFTER_SEC, FACTOR_LOOKUP_FUTURE_TOLERANCE_SEC
from .risk_filter_contract import risk_filter_allows_real_entry

ENTRY_ACTIONS = {
    PositionAction.ENTRY_LONG.value,
    PositionAction.ENTRY_SHORT.value,
    PositionAction.SMALL_PROBE.value,
}
HIGH_RISK_ACTIONS = {PositionAction.REDUCE.value, PositionAction.EXIT.value}
PROTECT_ACTIONS = {"protective_stop_repair", "protect", "maintain_protective_stop"}
POST_ENTRY_RISK_TARGETS = {"advance_breakeven_stop", "advance_trailing_stop"}
TRIGGER_READY_SMALL_PROBE_SOURCE = "trigger_ready_small_probe"
MAX_TRIGGER_READY_SMALL_PROBE_SIZE_PCT = 0.10
TRIGGER_READY_SMALL_PROBE_HARD_BLOCK_CODES = {
    "bundle_missing",
    "data_health_veto",
    "factor_governance_unavailable",
    "factor_lookup_empty",
    "factor_lookup_generated_at_missing",
    "factor_lookup_missing",
    "factor_lookup_rebuild_failed",
    "factor_lookup_rebuild_still_stale",
    "factor_lookup_stale",
    "research_health_missing",
    "research_missing",
    "research_not_ready",
    "research_stale",
    "research_unavailable",
    "scoring_chain_frozen",
    "unavailable",
}


class RealOrderGateDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    allowed: bool = False
    action: str = ""
    automation_boundary: str = "real_orders_disabled"
    reason_codes: list[str] = Field(default_factory=list)


def evaluate_real_order_gate(
    *,
    payload: dict[str, Any],
    enable_real_orders: bool,
    kill_switch_path: str | Path | None = None,
) -> RealOrderGateDecision:
    action = str(payload.get("effective_action") or payload.get("requested_action") or "")
    reason_codes: list[str] = []
    if not enable_real_orders:
        return RealOrderGateDecision(
            enabled=False,
            allowed=False,
            action=action,
            automation_boundary="no_order_submission",
            reason_codes=["real_orders_disabled"],
        )
    if _kill_switch_enabled(kill_switch_path):
        return RealOrderGateDecision(
            enabled=True,
            allowed=False,
            action=action,
            automation_boundary="real_order_submission_blocked",
            reason_codes=["kill_switch_enabled"],
        )

    handoff = payload.get("handoff") or {}
    execution_plan = payload.get("execution_plan") or {}
    runtime_snapshot = payload.get("runtime_snapshot") or {}
    position = runtime_snapshot.get("position") or {}
    adapter_capabilities = payload.get("adapter_capabilities") or {}
    command_targets = _command_targets(payload)
    trigger_ready_small_probe_contract_open = _trigger_ready_small_probe_contract_open(
        action=action,
        payload=payload,
        handoff=handoff,
        execution_plan=execution_plan,
    )

    if str(payload.get("runtime_mode") or "") != "real":
        reason_codes.append("runtime_mode_not_real")
    if str(payload.get("engine_mode") or handoff.get("engine_mode") or "strict-live") != "strict-live":
        reason_codes.append("engine_mode_not_strict_live")
    if _truthy(payload.get("blocked", False)) or (
        _truthy(payload.get("degraded", False)) and not trigger_ready_small_probe_contract_open
    ):
        reason_codes.append("cycle_blocked_or_degraded")
    if POST_ENTRY_RISK_TARGETS.intersection(command_targets):
        _append_post_entry_risk_gate_reasons(
            reason_codes=reason_codes,
            command_targets=command_targets,
            adapter_capabilities=adapter_capabilities,
        )
    if _has_strategy_tp_ladder(handoff) and not _has_take_profit_order(command_targets):
        reason_codes.append("take_profit_orders_not_planned")
    if action not in ENTRY_ACTIONS and action not in HIGH_RISK_ACTIONS and action not in PROTECT_ACTIONS:
        reason_codes.append("action_not_executable")
    if _is_trigger_ready_small_probe_candidate(action=action, handoff=handoff):
        _append_trigger_ready_small_probe_contract_reasons(
            reason_codes=reason_codes,
            payload=payload,
            handoff=handoff,
            execution_plan=execution_plan,
        )

    if action == PositionAction.REDUCE.value:
        reason_codes.append("real_reduce_not_implemented")
        reason_codes.append("high_risk_auto_submit_not_enabled")
    elif action in HIGH_RISK_ACTIONS:
        reason_codes.append("high_risk_auto_submit_not_enabled")
    elif action in PROTECT_ACTIONS:
        _append_protective_repair_gate_reasons(
            reason_codes=reason_codes,
            handoff=handoff,
            runtime_snapshot=runtime_snapshot,
            position=position,
            payload=payload,
        )
    elif action in ENTRY_ACTIONS:
        _append_entry_gate_reasons(
            reason_codes=reason_codes,
            handoff=handoff,
            execution_plan=execution_plan,
            runtime_snapshot=runtime_snapshot,
            position=position,
            payload=payload,
            trigger_ready_small_probe_contract_open=trigger_ready_small_probe_contract_open,
        )

    allowed = not reason_codes
    return RealOrderGateDecision(
        enabled=True,
        allowed=allowed,
        action=action,
        automation_boundary="real_order_submission_allowed" if allowed else "real_order_submission_blocked",
        reason_codes=reason_codes,
    )


def _append_entry_gate_reasons(
    *,
    reason_codes: list[str],
    handoff: dict[str, Any],
    execution_plan: dict[str, Any],
    runtime_snapshot: dict[str, Any],
    position: dict[str, Any],
    payload: dict[str, Any],
    trigger_ready_small_probe_contract_open: bool = False,
) -> None:
    if handoff.get("execution_allowed") is not True:
        reason_codes.append("execution_not_allowed")
    if (
        not risk_filter_allows_real_entry(handoff.get("risk_filter_status"))
        and not trigger_ready_small_probe_contract_open
    ):
        reason_codes.append("risk_filter_not_pass")
    if runtime_snapshot.get("snapshot_valid") is not True:
        reason_codes.append("runtime_snapshot_invalid")
    if str(position.get("position_state") or "FLAT") != "FLAT":
        reason_codes.append("live_position_not_flat")
    if not execution_plan.get("place_entry_order"):
        reason_codes.append("entry_order_not_planned")
    if not execution_plan.get("maintain_protective_stop"):
        reason_codes.append("protective_stop_not_planned")
    if handoff.get("initial_stop_loss") in (None, ""):
        reason_codes.append("initial_stop_loss_missing")
    if not _has_ready_preflight(payload, "entry_order"):
        reason_codes.append("entry_preflight_not_ready")
    if not _has_ready_preflight(payload, "maintain_protective_stop"):
        reason_codes.append("protective_stop_preflight_not_ready")


def _has_ready_preflight(payload: dict[str, Any], target: str) -> bool:
    for item in payload.get("preflight") or []:
        if item.get("target") == target and item.get("status") == "preflight_ready" and not item.get("error"):
            return True
    return False


def _command_targets(payload: dict[str, Any]) -> set[str]:
    targets = {str(target) for target in payload.get("command_targets") or [] if str(target)}
    for command in payload.get("execution_commands") or []:
        if isinstance(command, dict) and command.get("target"):
            targets.add(str(command.get("target")))
    return targets


def _has_strategy_tp_ladder(handoff: dict[str, Any]) -> bool:
    ladder = handoff.get("tp_ladder")
    return isinstance(ladder, list) and len(ladder) > 0


def _has_take_profit_order(command_targets: set[str]) -> bool:
    return any(target == "take_profit_order" or target.startswith("take_profit_order:") for target in command_targets)


def _trigger_ready_small_probe_contract_open(
    *,
    action: str,
    payload: dict[str, Any],
    handoff: dict[str, Any],
    execution_plan: dict[str, Any],
) -> bool:
    if not _is_trigger_ready_small_probe_candidate(action=action, handoff=handoff):
        return False
    if handoff.get("execution_allowed") is not True:
        return False
    if _truthy(payload.get("blocked", False)):
        return False
    if str(handoff.get("risk_filter_status") or "").strip().lower() != "degraded":
        return False
    if _trigger_ready_small_probe_size_block_reason(handoff=handoff, execution_plan=execution_plan, payload=payload):
        return False
    return not _trigger_ready_small_probe_hard_block_codes(payload=payload, handoff=handoff)


def _is_trigger_ready_small_probe_candidate(*, action: str, handoff: dict[str, Any]) -> bool:
    return (
        action == PositionAction.SMALL_PROBE.value
        and str(handoff.get("probe_source") or "").strip().lower() == TRIGGER_READY_SMALL_PROBE_SOURCE
    )


def _append_trigger_ready_small_probe_contract_reasons(
    *,
    reason_codes: list[str],
    payload: dict[str, Any],
    handoff: dict[str, Any],
    execution_plan: dict[str, Any],
) -> None:
    size_reason = _trigger_ready_small_probe_size_block_reason(
        handoff=handoff,
        execution_plan=execution_plan,
        payload=payload,
    )
    if size_reason:
        _append_reason_once(reason_codes, size_reason)
    for code in _trigger_ready_small_probe_hard_block_codes(payload=payload, handoff=handoff):
        _append_reason_once(reason_codes, code)


def _trigger_ready_small_probe_hard_block_codes(*, payload: dict[str, Any], handoff: dict[str, Any]) -> list[str]:
    codes = set(_normalized_reason_codes(payload, handoff))
    if _truthy(handoff.get("scoring_chain_frozen", False)):
        codes.add("scoring_chain_frozen")
    if _truthy(handoff.get("factor_lookup_stale", False)):
        codes.add("factor_lookup_stale")
    generated_at = str(handoff.get("factor_lookup_generated_at") or "").strip()
    if not generated_at:
        codes.add("factor_lookup_generated_at_missing")
    else:
        age_seconds = _factor_lookup_age_seconds(generated_at)
        if age_seconds is None:
            codes.add("factor_lookup_generated_at_missing")
        elif age_seconds > DEFAULT_FACTOR_LOOKUP_STALE_AFTER_SEC:
            codes.add("factor_lookup_stale")
        elif age_seconds < -FACTOR_LOOKUP_FUTURE_TOLERANCE_SEC:
            codes.add("factor_lookup_generated_at_missing")
    if _truthy(handoff.get("staleness_veto", False)):
        codes.add("staleness_veto")
    if _truthy(handoff.get("conflict_veto", False)):
        codes.add("conflict_veto")
    if str(handoff.get("research_gate_status") or "").strip().lower() == "blocked":
        codes.add("research_gate_blocked")
    if handoff.get("runtime_vetoes"):
        codes.add("runtime_entry_veto")
    return sorted(code for code in codes if code in TRIGGER_READY_SMALL_PROBE_HARD_BLOCK_CODES or code in {
        "conflict_veto",
        "research_gate_blocked",
        "runtime_entry_veto",
        "staleness_veto",
    })


def _normalized_reason_codes(payload: dict[str, Any], handoff: dict[str, Any]) -> list[str]:
    values: list[Any] = []
    for container in (payload, handoff):
        for key in (
            "degrade_flags",
            "execution_warnings",
            "reason_codes",
            "research_gate_reasons",
            "risk_reason_codes",
            "runtime_vetoes",
            "transition_reason_codes",
        ):
            raw = container.get(key)
            if isinstance(raw, list):
                values.extend(raw)
            elif raw not in (None, ""):
                values.append(raw)
    for key in ("execution_block_reason", "risk_filter_status"):
        raw = handoff.get(key)
        if raw not in (None, ""):
            values.append(raw)
    return [str(value).strip().lower() for value in values if str(value).strip()]


def _trigger_ready_small_probe_size_block_reason(
    *,
    handoff: dict[str, Any],
    execution_plan: dict[str, Any],
    payload: dict[str, Any],
) -> str:
    requested_size = _to_float(handoff.get("position_size_pct"))
    if requested_size is None or requested_size <= 0.0:
        return "trigger_ready_probe_size_missing"
    for size in _trigger_ready_small_probe_size_values(
        handoff=handoff,
        execution_plan=execution_plan,
        payload=payload,
    ):
        if size > MAX_TRIGGER_READY_SMALL_PROBE_SIZE_PCT:
            return "trigger_ready_probe_size_over_cap"
    return ""


def _trigger_ready_small_probe_size_values(
    *,
    handoff: dict[str, Any],
    execution_plan: dict[str, Any],
    payload: dict[str, Any],
) -> list[float]:
    values: list[float] = []
    for raw in (
        handoff.get("position_size_pct"),
        handoff.get("executable_size_pct"),
        execution_plan.get("executable_size_pct"),
    ):
        parsed = _to_float(raw)
        if parsed is not None:
            values.append(parsed)
    for command in payload.get("execution_commands") or []:
        if not isinstance(command, dict):
            continue
        command_payload = command.get("payload")
        if not isinstance(command_payload, dict):
            continue
        parsed = _to_float(command_payload.get("position_size_pct"))
        if parsed is not None:
            values.append(parsed)
    return values


def _to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _factor_lookup_age_seconds(generated_at: str) -> float | None:
    try:
        parsed = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc)
    else:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - parsed).total_seconds()


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value or "").strip().lower() in {"1", "true", "yes", "y"}


def _append_reason_once(reason_codes: list[str], reason_code: str) -> None:
    if reason_code not in reason_codes:
        reason_codes.append(reason_code)


def _append_post_entry_risk_gate_reasons(
    *,
    reason_codes: list[str],
    command_targets: set[str],
    adapter_capabilities: dict[str, Any],
) -> None:
    if "advance_breakeven_stop" in command_targets and adapter_capabilities.get("supports_breakeven_update") is not True:
        reason_codes.append("breakeven_update_not_supported")
    if "advance_trailing_stop" in command_targets and adapter_capabilities.get("supports_trailing_stop_update") is not True:
        reason_codes.append("trailing_stop_update_not_supported")


def _append_protective_repair_gate_reasons(
    *,
    reason_codes: list[str],
    handoff: dict[str, Any],
    runtime_snapshot: dict[str, Any],
    position: dict[str, Any],
    payload: dict[str, Any],
) -> None:
    if runtime_snapshot.get("snapshot_valid") is not True:
        reason_codes.append("runtime_snapshot_invalid")
    if str(position.get("position_state") or "FLAT") != "ENTERED":
        reason_codes.append("live_position_not_entered")
    if runtime_snapshot.get("protective_stop_present") is True:
        reason_codes.append("protective_stop_already_present")
    if handoff.get("initial_stop_loss") in (None, ""):
        reason_codes.append("initial_stop_loss_missing")
    if not _has_ready_preflight(payload, "maintain_protective_stop"):
        reason_codes.append("protective_stop_preflight_not_ready")


def _kill_switch_enabled(kill_switch_path: str | Path | None) -> bool:
    path = Path(kill_switch_path) if kill_switch_path is not None else DEFAULT_KILL_SWITCH_PATH
    return path.exists()
