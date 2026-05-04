from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Protocol

BOT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = BOT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from bot.audit_logger import AuditLogger
from bot.config import BotConfig, RuntimeMode
from bot.exchange_adapter import AdapterCredentials, AdapterRuntimeSnapshot, BinancePerpAdapter, CommandExecutionResult, ExecutionCommand
from bot.state_store import StateStore


ENTRY_ACTIONS = {"entry_long", "entry_short", "small_probe"}
HIGH_RISK_ACTIONS = {"reduce", "exit"}
PROTECT_ACTIONS = {"protect", "protective_stop_repair", "maintain_protective_stop"}


class RealOrderAdapter(Protocol):
    def fetch_runtime_snapshot(self) -> AdapterRuntimeSnapshot: ...

    def execute_commands(
        self,
        *,
        commands: list[ExecutionCommand],
        runtime_mode: RuntimeMode,
    ) -> list[CommandExecutionResult]: ...

    def fetch_open_algo_orders_raw(self) -> list[dict[str, Any]]: ...

    def cancel_algo_order_raw(self, *, algo_id: str = "", client_algo_id: str = "") -> dict[str, Any]: ...


AdapterFactory = Callable[[argparse.Namespace], RealOrderAdapter]


def default_runtime_root() -> str:
    return str(Path(__file__).resolve().parents[1] / "runtime")


def default_package_path() -> str:
    return str(Path(default_runtime_root()) / "bot_runtime_scheduler" / "latest_candidate_execution_package.json")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Consume one vetted ETH candidate execution package and submit real orders.")
    parser.add_argument("command", choices=["run-once"])
    parser.add_argument("--package-path", default=default_package_path())
    parser.add_argument("--audit-log-path", default=str(Path(default_runtime_root()) / "real_order_worker" / "audit.jsonl"))
    parser.add_argument("--lock-path", default=str(Path(default_runtime_root()) / "locks" / "real_order_worker.lock"))
    parser.add_argument("--kill-switch-path", default=str(Path(default_runtime_root()) / "controls" / "disable_real_execution.flag"))
    parser.add_argument("--api-key-env", default=BotConfig().exchange_api_key_env)
    parser.add_argument("--api-secret-env", default=BotConfig().exchange_api_secret_env)
    parser.add_argument("--proxy-url", default=None)
    parser.add_argument("--submit-real-orders", action="store_true", default=False)
    parser.add_argument("--stale-lock-after-sec", type=int, default=900)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    result = run_once(args=args)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["status"] in {"submitted", "skipped", "blocked"} else 1


def run_once(*, args: argparse.Namespace, adapter_factory: AdapterFactory | None = None) -> dict[str, Any]:
    audit = AuditLogger(args.audit_log_path)
    state_store = StateStore(_resolve_state_path(args=args))
    package_result = _load_package(Path(args.package_path))
    if package_result["status"] != "ok":
        return _record_worker_event(audit=audit, event_type="real_order_worker_skipped", payload=package_result)
    package = package_result["package"]

    precheck = _precheck_package(package=package, submit_real_orders=bool(args.submit_real_orders), kill_switch_path=Path(args.kill_switch_path))
    if precheck:
        return _record_worker_event(audit=audit, event_type="real_order_worker_blocked", payload=precheck)

    commands = _load_execution_commands(package)
    if not commands:
        return _record_worker_event(
            audit=audit,
            event_type="real_order_worker_blocked",
            payload=_blocked(reason_codes=["execution_commands_missing"], package=package),
        )

    idempotency = _check_idempotency(audit_path=Path(args.audit_log_path), commands=commands)
    if idempotency:
        return _record_worker_event(audit=audit, event_type="real_order_worker_blocked", payload=idempotency)

    with WorkerLock(lock_path=Path(args.lock_path), stale_after_sec=int(args.stale_lock_after_sec)):
        if _kill_switch_enabled(Path(args.kill_switch_path)):
            return _record_worker_event(
                audit=audit,
                event_type="real_order_worker_blocked",
                payload=_blocked(reason_codes=["kill_switch_enabled"], package=package),
            )
        adapter = (adapter_factory or _build_binance_adapter)(args)
        first_snapshot = _fetch_runtime_snapshot_with_state(
            adapter=adapter,
            state_store=state_store,
            context="pre_submit_position_check",
        )
        ghost_cleanup = _cleanup_ghost_stops_if_needed(adapter=adapter, package=package, runtime_snapshot=first_snapshot)
        if ghost_cleanup:
            return _record_worker_event(audit=audit, event_type="real_order_worker_blocked", payload=ghost_cleanup)
        legality = _validate_action_still_legal(package=package, runtime_snapshot=first_snapshot)
        if legality:
            return _record_worker_event(audit=audit, event_type="real_order_worker_blocked", payload=legality)

        final_snapshot = _fetch_runtime_snapshot_with_state(
            adapter=adapter,
            state_store=state_store,
            context="final_pre_submit_position_check",
        )
        final_ghost_cleanup = _cleanup_ghost_stops_if_needed(adapter=adapter, package=package, runtime_snapshot=final_snapshot)
        if final_ghost_cleanup:
            return _record_worker_event(audit=audit, event_type="real_order_worker_blocked", payload=final_ghost_cleanup)
        final_legality = _validate_action_still_legal(package=package, runtime_snapshot=final_snapshot)
        if final_legality:
            return _record_worker_event(audit=audit, event_type="real_order_worker_blocked", payload=final_legality)

        pending_payload = {
            "status": "pending",
            "package_id": package.get("package_id", ""),
            "action": package.get("action", ""),
            "commands": [_command_audit_payload(command) for command in commands],
            "runtime_snapshot_before": final_snapshot.model_dump(mode="json"),
        }
        pending_event = audit.append(event_type="real_order_worker_command_pending", payload=pending_payload)

        try:
            results = _execute_with_action_closure(adapter=adapter, commands=commands, action=str(package.get("action") or ""))
        except Exception as exc:
            state_store.record_api_failure(reason_code=f"submit_response_unknown:{exc.__class__.__name__}")
            raise
        after_snapshot = _fetch_runtime_snapshot_with_state(
            adapter=adapter,
            state_store=state_store,
            context="post_submit_position_refresh",
        )
        result_payload = {
            "status": "submitted",
            "package_id": package.get("package_id", ""),
            "action": package.get("action", ""),
            "pending_generated_at": pending_event.generated_at.isoformat(),
            "commands": [_command_audit_payload(command) for command in commands],
            "results": [result.model_dump(mode="json") for result in results],
            "runtime_snapshot_after": after_snapshot.model_dump(mode="json"),
        }
        _record_worker_state_from_snapshot(state_store=state_store, runtime_snapshot=after_snapshot, results=results)
        return _record_worker_event(audit=audit, event_type="real_order_worker_command_result", payload=result_payload)


class WorkerLock:
    def __init__(self, *, lock_path: Path, stale_after_sec: int = 900) -> None:
        self._lock_path = lock_path
        self._stale_after_sec = stale_after_sec
        self._handle: int | None = None

    def __enter__(self) -> "WorkerLock":
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        try:
            self._handle = os.open(str(self._lock_path), flags)
        except FileExistsError as exc:
            if not self._try_clear_stale_lock():
                raise RuntimeError(f"real order worker already running: {self._lock_path}") from exc
            self._handle = os.open(str(self._lock_path), flags)
        os.write(
            self._handle,
            json.dumps(
                {"pid": os.getpid(), "created_at": datetime.now().replace(microsecond=0).isoformat()},
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8"),
        )
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._handle is not None:
            os.close(self._handle)
            self._handle = None
        try:
            self._lock_path.unlink()
        except FileNotFoundError:
            pass

    def _try_clear_stale_lock(self) -> bool:
        try:
            stat = self._lock_path.stat()
        except FileNotFoundError:
            return True
        if max(0.0, time.time() - stat.st_mtime) < self._stale_after_sec:
            return False
        try:
            self._lock_path.unlink()
        except FileNotFoundError:
            return True
        return True


def _build_binance_adapter(args: argparse.Namespace) -> BinancePerpAdapter:
    config = BotConfig(proxy_url=args.proxy_url)
    credentials = AdapterCredentials(
        venue=config.exchange_venue,
        api_key_env=args.api_key_env,
        api_secret_env=args.api_secret_env,
        recv_window_ms=config.recv_window_ms,
        timeout_sec=config.timeout_sec,
        proxy_url=args.proxy_url,
        api_base_url=config.exchange_api_base_url,
    )
    return BinancePerpAdapter(credentials)


def _resolve_state_path(*, args: argparse.Namespace) -> Path:
    package_path = Path(args.package_path)
    if package_path.exists():
        try:
            package = json.loads(package_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            package = {}
        state_path = str((package if isinstance(package, dict) else {}).get("state_path") or "")
        if state_path:
            return Path(state_path)
    return Path(default_runtime_root()) / "state_store.json"


def _fetch_runtime_snapshot_with_state(
    *,
    adapter: RealOrderAdapter,
    state_store: StateStore,
    context: str,
) -> AdapterRuntimeSnapshot:
    try:
        snapshot = adapter.fetch_runtime_snapshot()
    except Exception as exc:
        state_store.record_api_failure(reason_code=f"{context}:{exc.__class__.__name__}")
        raise
    if snapshot.snapshot_valid:
        state_store.record_api_success()
    else:
        reason = snapshot.error_kind or snapshot.error_endpoint or "runtime_snapshot_invalid"
        state_store.record_api_failure(reason_code=f"{context}:{reason}")
    return snapshot


def _load_package(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"status": "skipped", "reason_codes": ["candidate_execution_package_missing"], "package_path": str(path)}
    try:
        package = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {"status": "blocked", "reason_codes": ["candidate_execution_package_invalid_json"], "error": str(exc), "package_path": str(path)}
    if not isinstance(package, dict):
        return {"status": "blocked", "reason_codes": ["candidate_execution_package_not_object"], "package_path": str(path)}
    return {"status": "ok", "package": package, "package_path": str(path)}


def _precheck_package(*, package: dict[str, Any], submit_real_orders: bool, kill_switch_path: Path) -> dict[str, Any] | None:
    reason_codes: list[str] = []
    if not submit_real_orders:
        reason_codes.append("submit_real_orders_flag_missing")
    if _kill_switch_enabled(kill_switch_path):
        reason_codes.append("kill_switch_enabled")
    if _parse_datetime(package.get("expires_at")) <= _utcnow():
        reason_codes.append("execution_package_expired")
    gate = package.get("real_order_gate") or {}
    if gate.get("enabled") is not True or gate.get("allowed") is not True:
        reason_codes.append("real_order_gate_not_allowed")
    if gate.get("automation_boundary") != "real_order_submission_allowed":
        reason_codes.append("automation_boundary_not_allowed")
    if str(package.get("runtime_mode") or "") != "real":
        reason_codes.append("runtime_mode_not_real")
    if str(package.get("engine_mode") or "") != "strict-live":
        reason_codes.append("engine_mode_not_strict_live")
    if str(package.get("exchange_symbol") or "ETHUSDT") != "ETHUSDT":
        reason_codes.append("exchange_symbol_not_ethusdt")
    preflight = package.get("preflight") or []
    if not preflight or not all(item.get("status") == "preflight_ready" and not item.get("error") for item in preflight):
        reason_codes.append("preflight_not_ready")
    if reason_codes:
        return _blocked(reason_codes=reason_codes, package=package)
    return None


def _load_execution_commands(package: dict[str, Any]) -> list[ExecutionCommand]:
    commands = []
    for item in package.get("execution_commands") or []:
        commands.append(ExecutionCommand.model_validate(item))
    return commands


def _check_idempotency(*, audit_path: Path, commands: list[ExecutionCommand]) -> dict[str, Any] | None:
    keys = {command.idempotency_key for command in commands if command.idempotency_key}
    if not keys or not audit_path.exists():
        return None
    pending_keys: set[str] = set()
    completed_keys: set[str] = set()
    for line in audit_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        event_type = str(event.get("event_type") or "")
        payload = event.get("payload") or {}
        event_keys = {str(item.get("idempotency_key") or "") for item in payload.get("commands") or []}
        event_keys.update(str(item.get("idempotency_key") or "") for item in payload.get("results") or [])
        event_keys.discard("")
        matched = keys.intersection(event_keys)
        if not matched:
            continue
        if event_type == "real_order_worker_command_pending":
            pending_keys.update(matched)
        if event_type == "real_order_worker_command_result":
            completed_keys.update(matched)
    if completed_keys:
        return {"status": "blocked", "reason_codes": ["idempotency_key_already_completed"], "idempotency_keys": sorted(completed_keys)}
    if pending_keys:
        return {"status": "blocked", "reason_codes": ["pending_idempotency_key_requires_recovery"], "idempotency_keys": sorted(pending_keys)}
    return None


def _validate_action_still_legal(*, package: dict[str, Any], runtime_snapshot: AdapterRuntimeSnapshot) -> dict[str, Any] | None:
    reason_codes: list[str] = []
    action = str(package.get("action") or "")
    if not runtime_snapshot.snapshot_valid:
        reason_codes.append("runtime_snapshot_invalid")
    position_state = runtime_snapshot.position.position_state
    if action in ENTRY_ACTIONS:
        if position_state != "FLAT":
            reason_codes.append("live_position_not_flat")
        if runtime_snapshot.protective_stop_present:
            reason_codes.append("ghost_protective_stop_present")
    elif action in HIGH_RISK_ACTIONS or action in PROTECT_ACTIONS:
        if position_state != "ENTERED":
            reason_codes.append("live_position_not_entered")
        if str(package.get("direction") or "") and package.get("direction") != runtime_snapshot.position.direction:
            reason_codes.append("live_position_direction_mismatch")
        if action in PROTECT_ACTIONS and runtime_snapshot.protective_stop_present:
            reason_codes.append("protective_stop_already_present")
    else:
        reason_codes.append("action_not_executable")
    if reason_codes:
        return _blocked(reason_codes=reason_codes, package=package, runtime_snapshot=runtime_snapshot)
    return None


def _cleanup_ghost_stops_if_needed(
    *,
    adapter: RealOrderAdapter,
    package: dict[str, Any],
    runtime_snapshot: AdapterRuntimeSnapshot,
) -> dict[str, Any] | None:
    action = str(package.get("action") or "")
    if action not in ENTRY_ACTIONS:
        return None
    if not runtime_snapshot.snapshot_valid:
        return None
    if runtime_snapshot.position.position_state != "FLAT" or not runtime_snapshot.protective_stop_present:
        return None
    try:
        open_algo_orders = adapter.fetch_open_algo_orders_raw()
        canceled = _cancel_algo_orders(adapter=adapter, open_algo_orders=open_algo_orders)
    except Exception as exc:
        return _blocked(
            reason_codes=["ghost_stop_cancel_failed", f"ghost_stop_cancel_error:{exc.__class__.__name__}"],
            package=package,
            runtime_snapshot=runtime_snapshot,
        )
    if not canceled and open_algo_orders:
        return _blocked(
            reason_codes=["ghost_stop_cancel_failed"],
            package=package,
            runtime_snapshot=runtime_snapshot,
        )
    return None


def _execute_with_action_closure(
    *,
    adapter: RealOrderAdapter,
    commands: list[ExecutionCommand],
    action: str,
) -> list[CommandExecutionResult]:
    if action in ENTRY_ACTIONS:
        return _execute_entry_with_protective_stop_retry(adapter=adapter, commands=commands)
    if action in PROTECT_ACTIONS:
        return _execute_protective_stop_with_retry(adapter=adapter, commands=commands, attempts=3)
    if action == "reduce":
        return _execute_reduce_with_stop_refresh(adapter=adapter, commands=commands)
    results = adapter.execute_commands(commands=commands, runtime_mode=RuntimeMode.REAL)
    if action == "exit":
        _cleanup_open_algo_orders(adapter=adapter)
    return results


def _execute_reduce_with_stop_refresh(
    *,
    adapter: RealOrderAdapter,
    commands: list[ExecutionCommand],
) -> list[CommandExecutionResult]:
    reduce_commands = [command for command in commands if command.target == "reduce_order"]
    stop_commands = [command for command in commands if command.target == "maintain_protective_stop"]
    other_commands = [command for command in commands if command.target not in {"reduce_order", "maintain_protective_stop"}]
    results: list[CommandExecutionResult] = []
    if reduce_commands:
        reduce_results = adapter.execute_commands(commands=reduce_commands, runtime_mode=RuntimeMode.REAL)
        results.extend(reduce_results)
        if not all(result.accepted for result in reduce_results):
            return results
    if stop_commands:
        stop_results = _execute_protective_stop_with_retry(adapter=adapter, commands=stop_commands, attempts=3)
        results.extend(stop_results)
        if stop_results and all(result.accepted for result in stop_results):
            _cleanup_open_algo_orders(adapter=adapter)
    if other_commands:
        results.extend(adapter.execute_commands(commands=other_commands, runtime_mode=RuntimeMode.REAL))
    return results


def _execute_entry_with_protective_stop_retry(
    *,
    adapter: RealOrderAdapter,
    commands: list[ExecutionCommand],
) -> list[CommandExecutionResult]:
    entry_commands = [command for command in commands if command.target == "entry_order"]
    stop_commands = [command for command in commands if command.target == "maintain_protective_stop"]
    other_commands = [command for command in commands if command.target not in {"entry_order", "maintain_protective_stop"}]
    results: list[CommandExecutionResult] = []
    if entry_commands:
        entry_results = adapter.execute_commands(commands=entry_commands, runtime_mode=RuntimeMode.REAL)
        results.extend(entry_results)
        if not all(result.accepted for result in entry_results):
            return results
    if stop_commands:
        stop_results = _execute_protective_stop_with_retry(adapter=adapter, commands=stop_commands, attempts=3)
        results.extend(stop_results)
    if other_commands:
        results.extend(adapter.execute_commands(commands=other_commands, runtime_mode=RuntimeMode.REAL))
    return results


def _execute_protective_stop_with_retry(
    *,
    adapter: RealOrderAdapter,
    commands: list[ExecutionCommand],
    attempts: int,
) -> list[CommandExecutionResult]:
    latest_results: list[CommandExecutionResult] = []
    for _ in range(max(1, attempts)):
        latest_results = adapter.execute_commands(commands=commands, runtime_mode=RuntimeMode.REAL)
        if latest_results and all(result.accepted for result in latest_results) and _protective_stop_confirmed_active(adapter=adapter):
            return latest_results
    return latest_results


def _protective_stop_confirmed_active(*, adapter: RealOrderAdapter) -> bool:
    open_algo_orders = adapter.fetch_open_algo_orders_raw()
    return bool(open_algo_orders)


def _cleanup_open_algo_orders(*, adapter: RealOrderAdapter) -> list[dict[str, Any]]:
    open_algo_orders = adapter.fetch_open_algo_orders_raw()
    return _cancel_algo_orders(adapter=adapter, open_algo_orders=open_algo_orders)


def _cancel_algo_orders(*, adapter: RealOrderAdapter, open_algo_orders: list[dict[str, Any]]) -> list[dict[str, Any]]:
    canceled: list[dict[str, Any]] = []
    for order in open_algo_orders:
        algo_id = str(order.get("algoId") or "")
        client_algo_id = str(order.get("clientAlgoId") or "")
        if not algo_id and not client_algo_id:
            continue
        canceled.append(adapter.cancel_algo_order_raw(algo_id=algo_id, client_algo_id=client_algo_id))
    return canceled


def _record_worker_state_from_snapshot(
    *,
    state_store: StateStore,
    runtime_snapshot: AdapterRuntimeSnapshot,
    results: list[CommandExecutionResult],
) -> None:
    if not runtime_snapshot.snapshot_valid:
        return
    state = state_store.load()
    state.observed_position_state = runtime_snapshot.position.position_state
    state.observed_position_direction = runtime_snapshot.position.direction
    state.observed_position_size_pct = runtime_snapshot.position.size_pct
    if runtime_snapshot.position.position_state == "FLAT" and runtime_snapshot.position.size_pct <= 0.0:
        state.execution_state = state.execution_state.IDLE
        state.pending_action = ""
        state.reconciliation_required = False
        state.protective_stop_required = False
        state.recovery_required = False
    elif runtime_snapshot.position.position_state == "ENTERED" and runtime_snapshot.protective_stop_present:
        state.execution_state = state.execution_state.POSITION_OPEN
        state.pending_action = ""
        state.reconciliation_required = False
        state.protective_stop_required = False
        state.recovery_required = False
    elif runtime_snapshot.position.position_state == "ENTERED":
        state.execution_state = state.execution_state.RECONCILING
        state.pending_action = ""
        state.reconciliation_required = True
        state.protective_stop_required = True
        state.recovery_required = True
        if "protective_stop_missing_after_submit" not in state.last_reason_codes:
            state.last_reason_codes = [*state.last_reason_codes, "protective_stop_missing_after_submit"]
    state.recent_idempotency_keys = _merge_recent_idempotency_keys(
        previous=state.recent_idempotency_keys,
        results=results,
    )
    state_store.save(state)


def _merge_recent_idempotency_keys(
    *,
    previous: list[str],
    results: list[CommandExecutionResult],
) -> list[str]:
    keys = [key for key in previous if key]
    keys.extend(result.idempotency_key for result in results if result.idempotency_key)
    return list(dict.fromkeys(keys))[-20:]


def _record_worker_event(*, audit: AuditLogger, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    event = audit.append(event_type=event_type, payload=payload)
    result = dict(payload)
    result["event_type"] = event_type
    result["audit_log_path"] = str(audit.output_path)
    return result


def _blocked(
    *,
    reason_codes: list[str],
    package: dict[str, Any],
    runtime_snapshot: AdapterRuntimeSnapshot | None = None,
) -> dict[str, Any]:
    payload = {
        "status": "blocked",
        "package_id": package.get("package_id", ""),
        "action": package.get("action", ""),
        "reason_codes": reason_codes,
    }
    if runtime_snapshot is not None:
        payload["runtime_snapshot"] = runtime_snapshot.model_dump(mode="json")
    return payload


def _command_audit_payload(command: ExecutionCommand) -> dict[str, Any]:
    return {
        "target": command.target,
        "operation": command.operation,
        "command_type": command.command_type,
        "idempotency_key": command.idempotency_key,
        "reason": command.reason,
        "payload": command.payload.model_dump(mode="json"),
    }


def _kill_switch_enabled(path: Path) -> bool:
    return path.exists()


def _parse_datetime(value: Any) -> datetime:
    if not value:
        return datetime.min.replace(tzinfo=timezone.utc)
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


if __name__ == "__main__":
    raise SystemExit(main())
