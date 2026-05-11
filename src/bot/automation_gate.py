from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .action_enums import PositionAction
from .risk_filter_contract import risk_filter_allows_real_entry

ENTRY_ACTIONS = {
    PositionAction.ENTRY_LONG.value,
    PositionAction.ENTRY_SHORT.value,
    PositionAction.SMALL_PROBE.value,
}
HIGH_RISK_ACTIONS = {PositionAction.REDUCE.value, PositionAction.EXIT.value}
PROTECT_ACTIONS = {"protective_stop_repair", "protect", "maintain_protective_stop"}
POST_ENTRY_RISK_TARGETS = {"advance_breakeven_stop", "advance_trailing_stop"}
DEFAULT_KILL_SWITCH_PATH = Path("runtime/controls/disable_real_execution.flag")


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

    if str(payload.get("runtime_mode") or "") != "real":
        reason_codes.append("runtime_mode_not_real")
    if str(payload.get("engine_mode") or handoff.get("engine_mode") or "strict-live") != "strict-live":
        reason_codes.append("engine_mode_not_strict_live")
    if bool(payload.get("blocked", False)) or bool(payload.get("degraded", False)):
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
) -> None:
    if handoff.get("execution_allowed") is not True:
        reason_codes.append("execution_not_allowed")
    if not risk_filter_allows_real_entry(handoff.get("risk_filter_status")):
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
