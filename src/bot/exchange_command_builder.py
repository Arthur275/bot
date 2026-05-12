from __future__ import annotations

from typing import Any

from .exchange_models import (
    BreakevenPayload,
    EntryOrderPayload,
    ExecutionCommand,
    ExitOrderPayload,
    ProtectiveStopPayload,
    RecentFillsPayload,
    ReconciliationPayload,
    ReduceOrderPayload,
    TakeProfitOrderPayload,
    TrailingStopPayload,
)
from .position_manager import ExecutionPlan


def build_execution_commands(*, execution_plan: ExecutionPlan, handoff: dict[str, Any] | None) -> list[ExecutionCommand]:
    commands: list[ExecutionCommand] = []
    resolved_direction = resolve_handoff_direction(handoff)
    if execution_plan.place_entry_order:
        commands.append(
            ExecutionCommand(
                command_type="order",
                operation="place",
                target="entry_order",
                idempotency_key=build_idempotency_key(target="entry_order", handoff=handoff),
                reason=resolve_primary_command_reason(execution_plan),
                payload=EntryOrderPayload(
                    action=execution_plan.effective_action,
                    direction=resolved_direction,
                    initial_stop_loss=(handoff or {}).get("initial_stop_loss"),
                    position_size_pct=resolve_entry_size_pct(execution_plan=execution_plan, handoff=handoff),
                    execution_warnings=resolve_execution_warnings(handoff),
                ),
            )
        )
    if execution_plan.place_reduce_order:
        commands.append(
            ExecutionCommand(
                command_type="order",
                operation="place",
                target="reduce_order",
                idempotency_key=build_idempotency_key(target="reduce_order", handoff=handoff),
                reason=resolve_primary_command_reason(execution_plan),
                payload=ReduceOrderPayload(
                    action=execution_plan.effective_action,
                    direction=resolved_direction,
                    reduce_conditions=list((handoff or {}).get("reduce_conditions", [])),
                ),
            )
        )
    if execution_plan.place_exit_order:
        commands.append(
            ExecutionCommand(
                command_type="order",
                operation="place",
                target="exit_order",
                idempotency_key=build_idempotency_key(target="exit_order", handoff=handoff),
                reason=resolve_primary_command_reason(execution_plan),
                payload=ExitOrderPayload(
                    action=execution_plan.effective_action,
                    direction=resolved_direction,
                ),
            )
        )
    if execution_plan.maintain_protective_stop:
        commands.append(
            ExecutionCommand(
                command_type="order",
                operation="upsert",
                target="maintain_protective_stop",
                idempotency_key=build_idempotency_key(target="maintain_protective_stop", handoff=handoff),
                reason="protective_stop_required",
                payload=ProtectiveStopPayload(
                    direction=resolved_direction,
                    initial_stop_loss=(handoff or {}).get("initial_stop_loss"),
                    breakeven_trigger=(handoff or {}).get("breakeven_trigger"),
                    trailing_rule=(handoff or {}).get("trailing_rule") or "",
                    tp_ladder=list((handoff or {}).get("tp_ladder", [])),
                ),
            )
        )
    if execution_plan.place_take_profit_orders:
        commands.extend(build_take_profit_commands(handoff=handoff, direction=resolved_direction))
    if execution_plan.advance_breakeven:
        commands.append(
            ExecutionCommand(
                command_type="risk",
                operation="tighten",
                target="advance_breakeven_stop",
                idempotency_key=build_idempotency_key(target="advance_breakeven_stop", handoff=handoff),
                reason="breakeven_ready",
                payload=BreakevenPayload(
                    direction=resolved_direction,
                    breakeven_trigger=(handoff or {}).get("breakeven_trigger"),
                    trailing_rule=(handoff or {}).get("trailing_rule") or "",
                ),
            )
        )
    if execution_plan.advance_trailing_stop:
        commands.append(
            ExecutionCommand(
                command_type="risk",
                operation="tighten",
                target="advance_trailing_stop",
                idempotency_key=build_idempotency_key(target="advance_trailing_stop", handoff=handoff),
                reason="trailing_ready",
                payload=TrailingStopPayload(
                    direction=resolved_direction,
                    trailing_rule=(handoff or {}).get("trailing_rule") or "",
                    trailing_activation_ratio=(handoff or {}).get("trailing_activation_ratio"),
                    trailing_callback_rate_pct=(handoff or {}).get("trailing_callback_rate_pct"),
                    tp_ladder=list((handoff or {}).get("tp_ladder", [])),
                ),
            )
        )
    if execution_plan.sync_recent_fills:
        commands.append(
            ExecutionCommand(
                command_type="sync",
                operation="query",
                target="sync_recent_fills",
                idempotency_key=build_idempotency_key(target="sync_recent_fills", handoff=handoff),
                reason="recent_fill_sync_required",
                payload=RecentFillsPayload(),
            )
        )
    if execution_plan.needs_reconciliation:
        commands.append(
            ExecutionCommand(
                command_type="sync",
                operation="query",
                target="reconcile_position_and_orders",
                idempotency_key=build_idempotency_key(target="reconcile_position_and_orders", handoff=handoff),
                reason="reconciliation_required",
                payload=ReconciliationPayload(recovery_action=execution_plan.recovery_action),
            )
        )
    return commands


def build_take_profit_commands(*, handoff: dict[str, Any] | None, direction: str) -> list[ExecutionCommand]:
    commands: list[ExecutionCommand] = []
    for index, payload in enumerate(resolve_take_profit_payloads(handoff=handoff, direction=direction), start=1):
        commands.append(
            ExecutionCommand(
                command_type="order",
                operation="place",
                target="take_profit_order",
                idempotency_key=build_idempotency_key(target=f"take_profit_order:{index}", handoff=handoff),
                reason=f"take_profit_level:{index}",
                payload=payload,
            )
        )
    return commands


def resolve_take_profit_payloads(*, handoff: dict[str, Any] | None, direction: str) -> list[TakeProfitOrderPayload]:
    handoff = handoff or {}
    direct_orders = handoff.get("take_profit_orders")
    if isinstance(direct_orders, list) and direct_orders:
        payloads: list[TakeProfitOrderPayload] = []
        for index, item in enumerate(direct_orders, start=1):
            if not isinstance(item, dict):
                continue
            price_ratio = item.get("price_ratio") or item.get("target_ratio") or item.get("ratio")
            payloads.append(
                TakeProfitOrderPayload(
                    direction=direction,
                    price_ratio=float(price_ratio),
                    reduce_fraction=optional_float(item.get("reduce_fraction")),
                    reduce_qty=optional_float(item.get("reduce_qty")),
                    level=int(item.get("level") or index),
                )
            )
        return payloads
    ladder = handoff.get("tp_ladder")
    if not isinstance(ladder, list) or not ladder:
        return []
    fractions = first_list(handoff, "tp_reduce_fractions", "take_profit_reduce_fractions")
    qtys = first_list(handoff, "tp_reduce_qtys", "take_profit_reduce_qtys")
    if (fractions is None) == (qtys is None):
        return []
    if fractions is not None:
        if len(fractions) != len(ladder):
            return []
        return [
            TakeProfitOrderPayload(
                direction=direction,
                price_ratio=float(price_ratio),
                reduce_fraction=float(fractions[index]),
                level=index + 1,
            )
            for index, price_ratio in enumerate(ladder)
        ]
    if len(qtys) != len(ladder):
        return []
    return [
        TakeProfitOrderPayload(
            direction=direction,
            price_ratio=float(price_ratio),
            reduce_qty=float(qtys[index]),
            level=index + 1,
        )
        for index, price_ratio in enumerate(ladder)
    ]


def resolve_primary_command_reason(execution_plan: ExecutionPlan) -> str:
    action = execution_plan.effective_action or execution_plan.requested_action or "wait"
    return f"effective_action:{action}"


def resolve_entry_size_pct(*, execution_plan: ExecutionPlan, handoff: dict[str, Any] | None) -> float:
    if execution_plan.executable_size_pct is not None:
        return float(execution_plan.executable_size_pct or 0.0)
    handoff = handoff or {}
    return float(handoff.get("executable_size_pct") or handoff.get("position_size_pct") or 0.0)


def first_list(handoff: dict[str, Any], *keys: str) -> list[Any] | None:
    for key in keys:
        value = handoff.get(key)
        if isinstance(value, list):
            return value
    return None


def optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    return float(value)


def resolve_handoff_direction(handoff: dict[str, Any] | None) -> str:
    primary = str((handoff or {}).get("direction") or "")
    fallback = str((handoff or {}).get("current_position_direction") or "")
    if primary in {"long", "short"}:
        return primary
    if fallback in {"long", "short"}:
        return fallback
    return primary or fallback


def build_idempotency_key(*, target: str, handoff: dict[str, Any] | None) -> str:
    generated_at = str((handoff or {}).get("generated_at") or "")
    action = str((handoff or {}).get("action") or "wait")
    direction = resolve_handoff_direction(handoff) or "neutral"
    package_scope = str(
        (handoff or {}).get("source_run_id")
        or (handoff or {}).get("handoff_id")
        or (handoff or {}).get("package_id")
        or ""
    )
    if package_scope:
        return f"{target}:{package_scope}:{generated_at}:{action}:{direction}"
    return f"{target}:{generated_at}:{action}:{direction}"


def resolve_execution_warnings(handoff: dict[str, Any] | None) -> list[str]:
    value = (handoff or {}).get("execution_warnings")
    if value in (None, ""):
        return []
    if isinstance(value, str):
        candidates = value.split(",")
    elif isinstance(value, list):
        candidates = value
    else:
        candidates = [value]
    return [
        warning
        for warning in (str(candidate).strip() for candidate in candidates)
        if warning
    ]
