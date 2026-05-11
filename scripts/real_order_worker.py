from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Callable, Protocol

BOT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = BOT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from bot.audit_logger import AuditLogger
from bot.config import BotConfig, RuntimeMode
from bot.exchange_adapter import AdapterCredentials, AdapterRuntimeSnapshot, BinancePerpAdapter, CommandExecutionResult, ExecutionCommand, OkxUsdtSwapAdapter, OrderSnapshot
from bot.state_store import StateStore


ENTRY_ACTIONS = {"entry_long", "entry_short", "small_probe"}
HIGH_RISK_ACTIONS = {"reduce", "exit"}
PROTECT_ACTIONS = {"protect", "protective_stop_repair", "maintain_protective_stop"}
ACTIVE_ALGO_STATUSES = {"NEW", "PARTIALLY_FILLED"}
OKX_ACTIVE_ALGO_STATUSES = {"LIVE", "EFFECTIVE"}
ACTIVE_ORDER_STATUSES = {"NEW", "PARTIALLY_FILLED", "LIVE", "EFFECTIVE"}
PROTECTIVE_ORDER_TYPES = {"STOP", "STOP_MARKET", "TRAILING_STOP_MARKET", "CONDITIONAL"}
TAKE_PROFIT_ORDER_TYPES = {"LIMIT"}
SUPPORTED_EXCHANGE_SYMBOLS = {"ETH-USDT-SWAP", "ETHUSDT"}
BOT_PROTECTIVE_STOP_CLIENT_PREFIXES = ("ethbot-ps-", "ethbot-be-", "ethbot-ts-")
BOT_TAKE_PROFIT_CLIENT_PREFIXES = ("ethbot-tp-",)
STOP_PRICE_TOLERANCE = Decimal("0.00000001")
WORKER_LOCK_OWNER = "real_order_worker"


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


class OrderSnapshotLike(Protocol):
    order_id: str
    client_order_id: str
    order_type: str
    status: str
    side: str
    reduce_only: bool
    quantity: float | None
    price: float | None


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
    parser.add_argument("--api-passphrase-env", default=BotConfig().exchange_api_passphrase_env)
    parser.add_argument("--proxy-url", default=None)
    parser.add_argument("--submit-real-orders", action="store_true", default=False)
    parser.add_argument("--stale-lock-after-sec", type=int, default=900)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    result = run_once(args=args)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["status"] in {"submitted_all_accepted", "skipped", "blocked"} else 1


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
        adapter = (adapter_factory or _build_real_adapter)(args)
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

        pre_submit_snapshot = _fetch_runtime_snapshot_with_state(
            adapter=adapter,
            state_store=state_store,
            context="immediate_pre_submit_position_check",
        )
        pre_submit_ghost_cleanup = _cleanup_ghost_stops_if_needed(adapter=adapter, package=package, runtime_snapshot=pre_submit_snapshot)
        if pre_submit_ghost_cleanup:
            return _record_worker_event(audit=audit, event_type="real_order_worker_blocked", payload=pre_submit_ghost_cleanup)
        pre_submit_legality = _validate_action_still_legal(package=package, runtime_snapshot=pre_submit_snapshot)
        if pre_submit_legality:
            return _record_worker_event(audit=audit, event_type="real_order_worker_blocked", payload=pre_submit_legality)
        if _kill_switch_enabled(Path(args.kill_switch_path)):
            return _record_worker_event(
                audit=audit,
                event_type="real_order_worker_blocked",
                payload=_blocked(reason_codes=["kill_switch_enabled_before_submit"], package=package, runtime_snapshot=pre_submit_snapshot),
            )

        pending_payload = {
            "status": "pending",
            "package_id": package.get("package_id", ""),
            "action": package.get("action", ""),
            "commands": [_command_audit_payload(command) for command in commands],
            "runtime_snapshot_before": pre_submit_snapshot.model_dump(mode="json"),
        }
        pending_event = audit.append(event_type="real_order_worker_command_pending", payload=pending_payload)

        try:
            results = _execute_with_action_closure(
                adapter=adapter,
                commands=commands,
                action=str(package.get("action") or ""),
                package=package,
                state_store=state_store,
                kill_switch_path=Path(args.kill_switch_path),
            )
        except Exception as exc:
            state_store.record_api_failure(reason_code=f"submit_response_unknown:{exc.__class__.__name__}")
            result_payload = {
                "status": "unknown_after_exception",
                "package_id": package.get("package_id", ""),
                "action": package.get("action", ""),
                "pending_generated_at": pending_event.generated_at.isoformat(),
                "commands": [_command_audit_payload(command) for command in commands],
                "results": [],
                "runtime_snapshot_after": {},
                "error": {"type": exc.__class__.__name__, "message": str(exc)},
            }
            return _record_worker_event(audit=audit, event_type="real_order_worker_command_result", payload=result_payload)
        after_snapshot = _fetch_runtime_snapshot_with_state(
            adapter=adapter,
            state_store=state_store,
            context="post_submit_position_refresh",
        )
        status = _summarize_execution_status(results)
        result_payload = {
            "status": status,
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
                {
                    "pid": os.getpid(),
                    "owner": WORKER_LOCK_OWNER,
                    "created_at": datetime.now().replace(microsecond=0).isoformat(),
                    "process_start_token": _process_start_token(os.getpid()),
                    "script_path": str(Path(__file__).resolve()),
                },
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
        metadata = self._read_lock_metadata()
        if self._has_live_worker_owner(metadata):
            return False
        if max(0.0, time.time() - stat.st_mtime) < self._stale_after_sec:
            return False
        try:
            self._lock_path.unlink()
        except FileNotFoundError:
            return True
        return True

    def _read_lock_metadata(self) -> dict[str, Any]:
        try:
            payload = json.loads(self._lock_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _has_live_worker_owner(metadata: dict[str, Any]) -> bool:
        if not _lock_metadata_matches_worker(metadata):
            return False
        pid = _coerce_positive_int(metadata.get("pid"))
        if pid is None or not _process_is_running(pid):
            return False
        recorded_start_token = str(metadata.get("process_start_token") or "")
        if not recorded_start_token:
            return True
        current_start_token = _process_start_token(pid)
        return not current_start_token or current_start_token == recorded_start_token


def _lock_metadata_matches_worker(metadata: dict[str, Any]) -> bool:
    owner = str(metadata.get("owner") or metadata.get("command") or "")
    if owner == WORKER_LOCK_OWNER:
        return True
    script_path = str(metadata.get("script_path") or "").replace("\\", "/").lower()
    return script_path.endswith("/real_order_worker.py")


def _coerce_positive_int(value: object) -> int | None:
    try:
        candidate = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return candidate if candidate > 0 else None


def _process_is_running(pid: int) -> bool:
    if pid == os.getpid():
        return True
    if os.name == "nt":
        return _windows_process_is_running(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _process_start_token(pid: int) -> str:
    if os.name == "nt":
        return _windows_process_start_token(pid)
    proc_stat = Path(f"/proc/{pid}/stat")
    try:
        payload = proc_stat.read_text(encoding="utf-8")
    except OSError:
        return ""
    parts = payload.rsplit(") ", maxsplit=1)
    if len(parts) != 2:
        return ""
    stat_fields = parts[1].split()
    if len(stat_fields) < 20:
        return ""
    return f"proc_start_ticks:{stat_fields[19]}"


def _windows_process_is_running(pid: int) -> bool:
    import ctypes

    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(0x1000, False, pid)
    if not handle:
        return False
    try:
        exit_code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return True
        return exit_code.value == 259
    finally:
        kernel32.CloseHandle(handle)


def _windows_process_start_token(pid: int) -> str:
    import ctypes

    class FileTime(ctypes.Structure):
        _fields_ = [("dwLowDateTime", ctypes.c_ulong), ("dwHighDateTime", ctypes.c_ulong)]

    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(0x1000, False, pid)
    if not handle:
        return ""
    try:
        created_at = FileTime()
        exit_time = FileTime()
        kernel_time = FileTime()
        user_time = FileTime()
        if not kernel32.GetProcessTimes(
            handle,
            ctypes.byref(created_at),
            ctypes.byref(exit_time),
            ctypes.byref(kernel_time),
            ctypes.byref(user_time),
        ):
            return ""
        created_at_ticks = (int(created_at.dwHighDateTime) << 32) + int(created_at.dwLowDateTime)
        return f"win_filetime:{created_at_ticks}"
    finally:
        kernel32.CloseHandle(handle)


def _build_real_adapter(args: argparse.Namespace) -> OkxUsdtSwapAdapter | BinancePerpAdapter:
    config = BotConfig(proxy_url=args.proxy_url)
    credentials = AdapterCredentials(
        venue=config.exchange_venue,
        api_key_env=args.api_key_env,
        api_secret_env=args.api_secret_env,
        api_passphrase_env=getattr(args, "api_passphrase_env", config.exchange_api_passphrase_env),
        recv_window_ms=config.recv_window_ms,
        timeout_sec=config.timeout_sec,
        proxy_url=args.proxy_url,
        api_base_url=config.exchange_api_base_url,
    )
    if config.exchange_venue == "okx_usdt_swap":
        return OkxUsdtSwapAdapter(credentials)
    return BinancePerpAdapter(credentials)


def _build_binance_adapter(args: argparse.Namespace) -> BinancePerpAdapter:
    config = BotConfig(
        proxy_url=args.proxy_url,
        exchange_venue="binance_usdt_perp",
        exchange_symbol="ETHUSDT",
        exchange_api_base_url="https://fapi.binance.com",
        exchange_api_key_env="BINANCE_TRADE_API_KEY",
        exchange_api_secret_env="BINANCE_TRADE_API_SECRET",
    )
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
    exchange_symbol = str(package.get("exchange_symbol") or BotConfig().exchange_symbol)
    if exchange_symbol not in SUPPORTED_EXCHANGE_SYMBOLS:
        reason_codes.append("exchange_symbol_not_supported")
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
    reconciliation_keys: set[str] = set()
    failed_keys: set[str] = set()
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
            status = str(payload.get("status") or "")
            if status == "unknown_after_exception":
                reconciliation_keys.update(matched)
                continue
            matched_results = [
                item
                for item in payload.get("results") or []
                if str(item.get("idempotency_key") or "") in matched
            ]
            for result in matched_results:
                key = str(result.get("idempotency_key") or "")
                if _result_has_submission_confirmation(result):
                    completed_keys.add(key)
                    continue
                if str(result.get("status") or "") == "timeout" or str(result.get("error_kind") or "") == "timeout":
                    reconciliation_keys.add(key)
                else:
                    failed_keys.add(key)
    pending_keys.difference_update(completed_keys)
    pending_keys.difference_update(reconciliation_keys)
    pending_keys.difference_update(failed_keys)
    if completed_keys:
        return {"status": "blocked", "reason_codes": ["idempotency_key_already_completed"], "idempotency_keys": sorted(completed_keys)}
    if reconciliation_keys:
        return {"status": "blocked", "reason_codes": ["pending_idempotency_key_requires_recovery"], "idempotency_keys": sorted(reconciliation_keys)}
    if pending_keys:
        return {"status": "blocked", "reason_codes": ["pending_idempotency_key_requires_recovery"], "idempotency_keys": sorted(pending_keys)}
    return None


def _result_has_submission_confirmation(result: dict[str, Any]) -> bool:
    if result.get("accepted") is not True:
        return False
    if str(result.get("exchange_order_id") or "") or str(result.get("client_order_id") or ""):
        return True
    details = result.get("details") if isinstance(result.get("details"), dict) else {}
    response_payload = details.get("response_payload")
    if isinstance(response_payload, dict) and (response_payload.get("orderId") or response_payload.get("algoId") or response_payload.get("clientOrderId") or response_payload.get("clientAlgoId")):
        return True
    if isinstance(response_payload, dict) and isinstance(response_payload.get("data"), list):
        for item in response_payload["data"]:
            if not isinstance(item, dict):
                continue
            if item.get("ordId") or item.get("algoId") or item.get("clOrdId") or item.get("algoClOrdId"):
                return True
    return False


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
        cancelable, external, invalid = _classify_cancelable_algo_orders(
            open_algo_orders=open_algo_orders,
            runtime_snapshot=runtime_snapshot,
            expected_direction="",
            require_side_match=False,
        )
        if external:
            return _blocked(
                reason_codes=["external_algo_order_present"],
                package=package,
                runtime_snapshot=runtime_snapshot,
            )
        if invalid:
            return _blocked(
                reason_codes=["bot_algo_order_semantics_mismatch"],
                package=package,
                runtime_snapshot=runtime_snapshot,
            )
        canceled = _cancel_algo_orders(adapter=adapter, open_algo_orders=cancelable)
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
    package: dict[str, Any],
    state_store: StateStore,
    kill_switch_path: Path,
) -> list[CommandExecutionResult]:
    if action in ENTRY_ACTIONS:
        return _execute_entry_with_protective_stop_retry(
            adapter=adapter,
            commands=commands,
            package=package,
            state_store=state_store,
            kill_switch_path=kill_switch_path,
        )
    if action in PROTECT_ACTIONS:
        return _execute_protective_stop_with_retry(
            adapter=adapter,
            commands=commands,
            attempts=3,
            package=package,
            state_store=state_store,
            kill_switch_path=kill_switch_path,
            allow_when_kill_switch=True,
        )
    if action == "reduce":
        return _execute_reduce_with_stop_refresh(
            adapter=adapter,
            commands=commands,
            package=package,
            state_store=state_store,
            kill_switch_path=kill_switch_path,
        )
    results = _execute_command_batch_after_final_checks(
        adapter=adapter,
        commands=commands,
        package=package,
        state_store=state_store,
        kill_switch_path=kill_switch_path,
        block_on_kill_switch=False,
    )
    if action == "exit":
        cleanup_result = _cleanup_open_algo_orders(
            adapter=adapter,
            expected_direction=_commands_direction(commands) or str(package.get("direction") or ""),
            require_side_match=True,
        )
        if cleanup_result.get("invalid_orders"):
            results.append(_synthetic_command_result(target="cleanup_open_algo_orders", reason="bot_algo_order_semantics_mismatch"))
        elif cleanup_result.get("external_orders"):
            results.append(_synthetic_command_result(target="cleanup_open_algo_orders", reason="external_algo_order_present"))
    return results


def _execute_reduce_with_stop_refresh(
    *,
    adapter: RealOrderAdapter,
    commands: list[ExecutionCommand],
    package: dict[str, Any],
    state_store: StateStore,
    kill_switch_path: Path,
) -> list[CommandExecutionResult]:
    reduce_commands = [command for command in commands if command.target == "reduce_order"]
    stop_commands = [command for command in commands if command.target == "maintain_protective_stop"]
    other_commands = [command for command in commands if command.target not in {"reduce_order", "maintain_protective_stop"}]
    results: list[CommandExecutionResult] = []
    if reduce_commands:
        reduce_results = _execute_command_batch_after_final_checks(
            adapter=adapter,
            commands=reduce_commands,
            package=package,
            state_store=state_store,
            kill_switch_path=kill_switch_path,
            block_on_kill_switch=False,
        )
        results.extend(reduce_results)
        if not all(result.accepted for result in reduce_results):
            return results
    if stop_commands:
        old_stop_identities = _fetch_cancelable_algo_order_identities(
            adapter=adapter,
            expected_direction=_commands_direction(stop_commands) or str(package.get("direction") or ""),
        )
        stop_results = _execute_protective_stop_with_retry(
            adapter=adapter,
            commands=stop_commands,
            attempts=3,
            package=package,
            state_store=state_store,
            kill_switch_path=kill_switch_path,
            allow_when_kill_switch=True,
            old_stop_identities=old_stop_identities,
        )
        results.extend(stop_results)
        if stop_results and all(result.accepted for result in stop_results):
            confirmed_new_identities = _confirmed_result_identities(stop_results)
            _cleanup_open_algo_orders(
                adapter=adapter,
                expected_direction=_commands_direction(stop_commands) or str(package.get("direction") or ""),
                allowed_identities=old_stop_identities,
                excluded_identities=confirmed_new_identities,
            )
    if other_commands:
        results.extend(
            _execute_command_batch_after_final_checks(
                adapter=adapter,
                commands=other_commands,
                package=package,
                state_store=state_store,
                kill_switch_path=kill_switch_path,
                block_on_kill_switch=False,
            )
        )
    return results


def _execute_entry_with_protective_stop_retry(
    *,
    adapter: RealOrderAdapter,
    commands: list[ExecutionCommand],
    package: dict[str, Any],
    state_store: StateStore,
    kill_switch_path: Path,
) -> list[CommandExecutionResult]:
    entry_commands = [command for command in commands if command.target == "entry_order"]
    stop_commands = [command for command in commands if command.target == "maintain_protective_stop"]
    take_profit_commands = [command for command in commands if command.target == "take_profit_order"]
    other_commands = [command for command in commands if command.target not in {"entry_order", "maintain_protective_stop", "take_profit_order"}]
    results: list[CommandExecutionResult] = []
    if entry_commands:
        entry_results = _execute_command_batch_after_final_checks(
            adapter=adapter,
            commands=entry_commands,
            package=package,
            state_store=state_store,
            kill_switch_path=kill_switch_path,
            block_on_kill_switch=True,
        )
        results.extend(entry_results)
        if not all(result.accepted for result in entry_results):
            return results
    if stop_commands:
        stop_results = _execute_protective_stop_with_retry(
            adapter=adapter,
            commands=stop_commands,
            attempts=3,
            package=package,
            state_store=state_store,
            kill_switch_path=kill_switch_path,
            allow_when_kill_switch=True,
        )
        results.extend(stop_results)
        if not (stop_results and all(result.accepted for result in stop_results)):
            return results
    if take_profit_commands:
        take_profit_results = _execute_take_profit_orders(
            adapter=adapter,
            commands=take_profit_commands,
            package=package,
            state_store=state_store,
            kill_switch_path=kill_switch_path,
        )
        results.extend(take_profit_results)
    if other_commands:
        results.extend(
            _execute_command_batch_after_final_checks(
                adapter=adapter,
                commands=other_commands,
                package=package,
                state_store=state_store,
                kill_switch_path=kill_switch_path,
                block_on_kill_switch=False,
            )
        )
    return results


def _execute_protective_stop_with_retry(
    *,
    adapter: RealOrderAdapter,
    commands: list[ExecutionCommand],
    attempts: int,
    package: dict[str, Any],
    state_store: StateStore,
    kill_switch_path: Path,
    allow_when_kill_switch: bool,
    old_stop_identities: set[tuple[str, str]] | None = None,
) -> list[CommandExecutionResult]:
    latest_results: list[CommandExecutionResult] = []
    for _ in range(max(1, attempts)):
        latest_results = _execute_command_batch_after_final_checks(
            adapter=adapter,
            commands=commands,
            package=package,
            state_store=state_store,
            kill_switch_path=kill_switch_path,
            block_on_kill_switch=not allow_when_kill_switch,
        )
        if latest_results and all(result.accepted for result in latest_results) and _protective_stop_confirmed_active(
            adapter=adapter,
            commands=commands,
            results=latest_results,
            old_stop_identities=old_stop_identities or set(),
        ):
            return latest_results
    return latest_results


def _execute_command_batch_after_final_checks(
    *,
    adapter: RealOrderAdapter,
    commands: list[ExecutionCommand],
    package: dict[str, Any],
    state_store: StateStore,
    kill_switch_path: Path,
    block_on_kill_switch: bool,
) -> list[CommandExecutionResult]:
    if not commands:
        return []
    if block_on_kill_switch and _kill_switch_enabled(kill_switch_path):
        return [_synthetic_command_result(target=command.target, reason="kill_switch_enabled_before_submit", command=command) for command in commands]
    runtime_snapshot = _fetch_runtime_snapshot_with_state(
        adapter=adapter,
        state_store=state_store,
        context=f"immediate_pre_{commands[0].target}_submit_check",
    )
    reason_codes = _validate_commands_against_runtime_snapshot(
        package=package,
        commands=commands,
        runtime_snapshot=runtime_snapshot,
    )
    if reason_codes:
        reason = ",".join(reason_codes)
        return [_synthetic_command_result(target=command.target, reason=reason, command=command) for command in commands]
    if block_on_kill_switch and _kill_switch_enabled(kill_switch_path):
        return [_synthetic_command_result(target=command.target, reason="kill_switch_enabled_before_submit", command=command) for command in commands]
    return adapter.execute_commands(commands=commands, runtime_mode=RuntimeMode.REAL)


def _validate_commands_against_runtime_snapshot(
    *,
    package: dict[str, Any],
    commands: list[ExecutionCommand],
    runtime_snapshot: AdapterRuntimeSnapshot,
) -> list[str]:
    if not runtime_snapshot.snapshot_valid:
        return ["runtime_snapshot_invalid"]
    reason_codes: list[str] = []
    package_direction = str(package.get("direction") or "")
    for command in commands:
        direction = str(getattr(command.payload, "direction", "") or package_direction)
        if command.target == "entry_order":
            if runtime_snapshot.position.position_state != "FLAT":
                reason_codes.append("live_position_not_flat_before_submit")
            if runtime_snapshot.protective_stop_present:
                reason_codes.append("ghost_protective_stop_present_before_submit")
        elif command.target in {"reduce_order", "exit_order", "maintain_protective_stop", "advance_breakeven_stop", "advance_trailing_stop"}:
            if runtime_snapshot.position.position_state != "ENTERED":
                reason_codes.append("live_position_not_entered_before_submit")
            if direction and direction != runtime_snapshot.position.direction:
                reason_codes.append("live_position_direction_mismatch_before_submit")
            if command.target in {"reduce_order", "exit_order", "maintain_protective_stop"} and not runtime_snapshot.position.position_amt:
                reason_codes.append("live_position_quantity_missing_before_submit")
        elif command.target == "take_profit_order":
            if runtime_snapshot.position.position_state != "ENTERED":
                reason_codes.append("live_position_not_entered_before_submit")
            if direction and direction != runtime_snapshot.position.direction:
                reason_codes.append("live_position_direction_mismatch_before_submit")
            if not runtime_snapshot.position.position_amt:
                reason_codes.append("live_position_quantity_missing_before_submit")
    return list(dict.fromkeys(reason_codes))


def _execute_take_profit_orders(
    *,
    adapter: RealOrderAdapter,
    commands: list[ExecutionCommand],
    package: dict[str, Any],
    state_store: StateStore,
    kill_switch_path: Path,
) -> list[CommandExecutionResult]:
    results = _execute_command_batch_after_final_checks(
        adapter=adapter,
        commands=commands,
        package=package,
        state_store=state_store,
        kill_switch_path=kill_switch_path,
        block_on_kill_switch=False,
    )
    if not (results and all(result.accepted for result in results)):
        return results
    if _take_profit_orders_confirmed_active(adapter=adapter, commands=commands, results=results):
        return results
    return [
        _unconfirmed_submission_result(command=command, submitted_result=result, reason="take_profit_order_confirmation_missing")
        for command, result in zip(commands, results, strict=False)
    ]


def _protective_stop_confirmed_active(
    *,
    adapter: RealOrderAdapter,
    commands: list[ExecutionCommand],
    results: list[CommandExecutionResult],
    old_stop_identities: set[tuple[str, str]],
) -> bool:
    open_algo_orders = adapter.fetch_open_algo_orders_raw()
    if len(commands) != len(results):
        return False
    for command, result in zip(commands, results, strict=False):
        match = find_matching_protective_stop(
            command=command,
            result=result,
            open_algo_orders=open_algo_orders,
            old_stop_identities=old_stop_identities,
        )
        if match is None:
            return False
        result.details["protective_stop_confirmation"] = {"matched_order": match}
    return True


def _take_profit_orders_confirmed_active(
    *,
    adapter: RealOrderAdapter,
    commands: list[ExecutionCommand],
    results: list[CommandExecutionResult],
) -> bool:
    open_orders = adapter.fetch_runtime_snapshot().open_orders
    if len(commands) != len(results):
        return False
    for command, result in zip(commands, results, strict=False):
        match = find_matching_take_profit_order(
            command=command,
            result=result,
            open_orders=open_orders,
        )
        if match is None:
            return False
        result.details["take_profit_confirmation"] = {"matched_order": match.model_dump(mode="json")}
    return True


def _cleanup_open_algo_orders(
    *,
    adapter: RealOrderAdapter,
    expected_direction: str = "",
    require_side_match: bool = True,
    allowed_identities: set[tuple[str, str]] | None = None,
    excluded_identities: set[tuple[str, str]] | None = None,
) -> dict[str, Any]:
    open_algo_orders = adapter.fetch_open_algo_orders_raw()
    cancelable, external, invalid = _classify_cancelable_algo_orders(
        open_algo_orders=open_algo_orders,
        runtime_snapshot=None,
        expected_direction=expected_direction,
        require_side_match=require_side_match,
    )
    allowed = allowed_identities
    excluded = excluded_identities or set()
    if allowed is not None:
        cancelable = [order for order in cancelable if _algo_order_identity(order) in allowed]
    if excluded:
        cancelable = [order for order in cancelable if _algo_order_identity(order) not in excluded]
    canceled = _cancel_algo_orders(adapter=adapter, open_algo_orders=cancelable)
    return {"canceled": canceled, "external_orders": external, "invalid_orders": invalid}


def _cancel_algo_orders(*, adapter: RealOrderAdapter, open_algo_orders: list[dict[str, Any]]) -> list[dict[str, Any]]:
    canceled: list[dict[str, Any]] = []
    for order in open_algo_orders:
        algo_id = _algo_order_exchange_id(order)
        client_algo_id = _algo_order_client_id(order)
        if not algo_id and not client_algo_id:
            continue
        canceled.append(adapter.cancel_algo_order_raw(algo_id=algo_id, client_algo_id=client_algo_id))
    return canceled


def _classify_cancelable_algo_orders(
    *,
    open_algo_orders: list[dict[str, Any]],
    runtime_snapshot: AdapterRuntimeSnapshot | None,
    expected_direction: str = "",
    require_side_match: bool = True,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    cancelable: list[dict[str, Any]] = []
    external: list[dict[str, Any]] = []
    invalid: list[dict[str, Any]] = []
    direction = expected_direction or (runtime_snapshot.position.direction if runtime_snapshot is not None else "")
    for order in open_algo_orders:
        if not _is_bot_owned_protective_algo_order(order):
            if _is_active_protective_algo_order(order):
                external.append(order)
            continue
        reason_codes = _protective_order_mismatch_reasons(order=order, direction=direction, require_side_match=require_side_match)
        if reason_codes:
            invalid.append({**order, "_mismatch_reasons": reason_codes})
            continue
        cancelable.append(order)
    return cancelable, external, invalid


def find_matching_protective_stop(
    *,
    command: ExecutionCommand,
    result: CommandExecutionResult,
    open_algo_orders: list[dict[str, Any]],
    old_stop_identities: set[tuple[str, str]] | None = None,
) -> dict[str, Any] | None:
    old_stop_identities = old_stop_identities or set()
    result_identities = _result_identity_candidates(result)
    for order in open_algo_orders:
        if _algo_order_identity(order) in old_stop_identities and _algo_order_identity(order) not in result_identities:
            continue
        if not _is_bot_owned_protective_algo_order(order):
            continue
        if _algo_order_identity(order) in result_identities:
            if _protective_order_mismatch_reasons(
                order=order,
                direction=str(getattr(command.payload, "direction", "") or ""),
                require_side_match=True,
            ):
                continue
            if not _quantity_matches_command_or_result(order=order, command=command, result=result):
                continue
            if not _trigger_price_matches_command_or_result(order=order, command=command, result=result):
                continue
            return order
    for order in open_algo_orders:
        if _algo_order_identity(order) in old_stop_identities:
            continue
        if not _is_bot_owned_protective_algo_order(order):
            continue
        if _protective_order_mismatch_reasons(
            order=order,
            direction=str(getattr(command.payload, "direction", "") or ""),
            require_side_match=True,
        ):
            continue
        if not _quantity_matches_command_or_result(order=order, command=command, result=result):
            continue
        if not _trigger_price_matches_command_or_result(order=order, command=command, result=result):
            continue
        return order
    return None


def _fetch_cancelable_algo_order_identities(*, adapter: RealOrderAdapter, expected_direction: str) -> set[tuple[str, str]]:
    open_algo_orders = adapter.fetch_open_algo_orders_raw()
    cancelable, _, _ = _classify_cancelable_algo_orders(
        open_algo_orders=open_algo_orders,
        runtime_snapshot=None,
        expected_direction=expected_direction,
        require_side_match=True,
    )
    return {_algo_order_identity(order) for order in cancelable}


def _confirmed_result_identities(results: list[CommandExecutionResult]) -> set[tuple[str, str]]:
    identities: set[tuple[str, str]] = set()
    for result in results:
        identities.update(_result_identity_candidates(result))
        confirmation = result.details.get("protective_stop_confirmation") if isinstance(result.details, dict) else {}
        if isinstance(confirmation, dict):
            matched_order = confirmation.get("matched_order")
            if isinstance(matched_order, dict):
                identities.add(_algo_order_identity(matched_order))
    identities.discard(("", ""))
    return identities


def _result_identity_candidates(result: CommandExecutionResult) -> set[tuple[str, str]]:
    identities: set[tuple[str, str]] = set()
    if result.exchange_order_id or result.client_order_id:
        identities.add((str(result.exchange_order_id or ""), str(result.client_order_id or "")))
    details = result.details if isinstance(result.details, dict) else {}
    for payload in (
        details.get("response_payload"),
        details.get("response_summary"),
        details.get("prepared_request", {}).get("params") if isinstance(details.get("prepared_request"), dict) else {},
        details.get("prepared_request", {}).get("body") if isinstance(details.get("prepared_request"), dict) else {},
        details.get("signed_request", {}).get("params") if isinstance(details.get("signed_request"), dict) else {},
        details.get("signed_request", {}).get("body") if isinstance(details.get("signed_request"), dict) else {},
    ):
        if isinstance(payload, dict):
            identities.add(
                (
                    str(payload.get("algoId") or payload.get("ordId") or payload.get("orderId") or ""),
                    str(payload.get("algoClOrdId") or payload.get("clOrdId") or payload.get("clientAlgoId") or payload.get("clientOrderId") or ""),
                )
            )
            data = payload.get("data")
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict):
                        identities.add(
                            (
                                str(item.get("algoId") or item.get("ordId") or item.get("orderId") or ""),
                                str(item.get("algoClOrdId") or item.get("clOrdId") or item.get("clientAlgoId") or item.get("clientOrderId") or ""),
                            )
                        )
    identities.discard(("", ""))
    return identities


def find_matching_take_profit_order(
    *,
    command: ExecutionCommand,
    result: CommandExecutionResult,
    open_orders: list[OrderSnapshot],
) -> OrderSnapshot | None:
    result_identities = _result_identity_candidates(result)
    for order in open_orders:
        if not _is_bot_owned_take_profit_order(order):
            continue
        if not _order_snapshot_matches_result_identity(order=order, result_identities=result_identities):
            continue
        if _take_profit_order_mismatch_reasons(
            order=order,
            direction=str(getattr(command.payload, "direction", "") or ""),
        ):
            continue
        if not _take_profit_price_matches_command_or_result(order=order, result=result):
            continue
        if not _take_profit_quantity_matches_command_or_result(order=order, result=result):
            continue
        return order
    return None


def _is_bot_owned_take_profit_order(order: OrderSnapshotLike) -> bool:
    client_order_id = str(getattr(order, "client_order_id", "") or "")
    order_id = str(getattr(order, "order_id", "") or "")
    return (
        client_order_id.startswith(BOT_TAKE_PROFIT_CLIENT_PREFIXES)
        or order_id.startswith(BOT_TAKE_PROFIT_CLIENT_PREFIXES)
    ) and _is_active_take_profit_order(order)


def _is_active_take_profit_order(order: OrderSnapshotLike) -> bool:
    return str(getattr(order, "status", "") or "").upper() in ACTIVE_ORDER_STATUSES and str(getattr(order, "order_type", "") or "").upper() in TAKE_PROFIT_ORDER_TYPES


def _order_snapshot_identity(order: OrderSnapshotLike) -> tuple[str, str]:
    order_id = str(getattr(order, "order_id", "") or "")
    client_order_id = str(getattr(order, "client_order_id", "") or "")
    return (order_id, client_order_id)


def _order_snapshot_matches_result_identity(*, order: OrderSnapshotLike, result_identities: set[tuple[str, str]]) -> bool:
    order_id = str(getattr(order, "order_id", "") or "")
    client_order_id = str(getattr(order, "client_order_id", "") or "")
    if not order_id and not client_order_id:
        return False
    return any(
        (order_id and order_id in identity) or (client_order_id and client_order_id in identity)
        for identity in result_identities
    )


def _take_profit_order_mismatch_reasons(*, order: OrderSnapshotLike, direction: str) -> list[str]:
    reason_codes: list[str] = []
    if not _is_active_take_profit_order(order):
        reason_codes.append("order_not_active_take_profit")
    if not bool(getattr(order, "reduce_only", False)):
        reason_codes.append("reduce_only_missing")
    if direction in {"long", "short"}:
        expected_side = "SELL" if direction == "long" else "BUY"
        if str(getattr(order, "side", "") or "").upper() != expected_side:
            reason_codes.append("side_mismatch")
    if _to_decimal(getattr(order, "price", None)) is None:
        reason_codes.append("price_missing")
    if not str(getattr(order, "order_id", "") or getattr(order, "client_order_id", "") or ""):
        reason_codes.append("order_identity_missing")
    return reason_codes


def _take_profit_price_matches_command_or_result(*, order: OrderSnapshotLike, result: CommandExecutionResult) -> bool:
    order_price = _to_decimal(getattr(order, "price", None))
    if order_price is None:
        return False
    expected_values: list[Any] = []
    details = result.details if isinstance(result.details, dict) else {}
    expected_values.append(details.get("response_summary", {}).get("resolved_take_profit_price") if isinstance(details.get("response_summary"), dict) else None)
    expected_values.append(details.get("prepared_request", {}).get("params", {}).get("price") if isinstance(details.get("prepared_request"), dict) else None)
    expected_values.append(details.get("prepared_request", {}).get("body", {}).get("px") if isinstance(details.get("prepared_request"), dict) else None)
    expected_values.append(details.get("signed_request", {}).get("params", {}).get("price") if isinstance(details.get("signed_request"), dict) else None)
    expected_values.append(details.get("signed_request", {}).get("body", {}).get("px") if isinstance(details.get("signed_request"), dict) else None)
    for value in expected_values:
        expected = _to_decimal(value)
        if expected is not None and abs(order_price - expected) <= STOP_PRICE_TOLERANCE:
            return True
    return False


def _take_profit_quantity_matches_command_or_result(*, order: OrderSnapshotLike, result: CommandExecutionResult) -> bool:
    order_quantity = _to_decimal(getattr(order, "quantity", None))
    if order_quantity is None:
        return True
    expected_values: list[Any] = []
    details = result.details if isinstance(result.details, dict) else {}
    expected_values.append(details.get("response_summary", {}).get("resolved_reduce_qty") if isinstance(details.get("response_summary"), dict) else None)
    expected_values.append(details.get("prepared_request", {}).get("params", {}).get("quantity") if isinstance(details.get("prepared_request"), dict) else None)
    expected_values.append(details.get("prepared_request", {}).get("body", {}).get("sz") if isinstance(details.get("prepared_request"), dict) else None)
    expected_values.append(details.get("signed_request", {}).get("params", {}).get("quantity") if isinstance(details.get("signed_request"), dict) else None)
    expected_values.append(details.get("signed_request", {}).get("body", {}).get("sz") if isinstance(details.get("signed_request"), dict) else None)
    for value in expected_values:
        expected = _to_decimal(value)
        if expected is not None and abs(order_quantity - expected) <= STOP_PRICE_TOLERANCE:
            return True
    return False


def _algo_order_identity(order: dict[str, Any]) -> tuple[str, str]:
    return (_algo_order_exchange_id(order), _algo_order_client_id(order))


def _algo_order_exchange_id(order: dict[str, Any]) -> str:
    return str(order.get("algoId") or order.get("ordId") or order.get("orderId") or "")


def _algo_order_client_id(order: dict[str, Any]) -> str:
    return str(order.get("algoClOrdId") or order.get("clOrdId") or order.get("clientAlgoId") or order.get("clientOrderId") or "")


def _is_bot_owned_protective_algo_order(order: dict[str, Any]) -> bool:
    client_algo_id = _algo_order_client_id(order)
    return client_algo_id.startswith(BOT_PROTECTIVE_STOP_CLIENT_PREFIXES) and _is_active_protective_algo_order(order)


def _is_active_protective_algo_order(order: dict[str, Any]) -> bool:
    status = _algo_order_status(order)
    order_type = _algo_order_type(order)
    return status in (ACTIVE_ALGO_STATUSES | OKX_ACTIVE_ALGO_STATUSES) and order_type in PROTECTIVE_ORDER_TYPES


def _protective_order_mismatch_reasons(*, order: dict[str, Any], direction: str, require_side_match: bool) -> list[str]:
    reason_codes: list[str] = []
    symbol = _algo_order_symbol(order)
    status = _algo_order_status(order)
    order_type = _algo_order_type(order)
    if symbol not in SUPPORTED_EXCHANGE_SYMBOLS:
        reason_codes.append("symbol_mismatch")
    if status not in (ACTIVE_ALGO_STATUSES | OKX_ACTIVE_ALGO_STATUSES):
        reason_codes.append("algo_status_not_active")
    if order_type not in PROTECTIVE_ORDER_TYPES:
        reason_codes.append("order_type_not_protective")
    if not _algo_order_is_reduce_only(order):
        reason_codes.append("reduce_only_missing")
    if require_side_match and direction in {"long", "short"}:
        expected_side = "SELL" if direction == "long" else "BUY"
        if str(order.get("side") or "").upper() != expected_side:
            reason_codes.append("side_mismatch")
    if _algo_order_trigger_price(order) is None:
        reason_codes.append("trigger_price_missing")
    if not _algo_order_exchange_id(order) and not _algo_order_client_id(order):
        reason_codes.append("algo_identity_missing")
    return reason_codes


def _algo_order_symbol(order: dict[str, Any]) -> str:
    return str(order.get("instId") or order.get("symbol") or "ETH-USDT-SWAP")


def _algo_order_status(order: dict[str, Any]) -> str:
    return str(order.get("algoStatus") or order.get("state") or order.get("status") or "").upper()


def _algo_order_type(order: dict[str, Any]) -> str:
    return str(order.get("ordType") or order.get("orderType") or order.get("type") or "").upper()


def _algo_order_is_reduce_only(order: dict[str, Any]) -> bool:
    return (
        _to_bool(order.get("reduceOnly"))
        or _to_bool(order.get("closePosition"))
        or str(order.get("closeFraction") or "") == "1"
    )


def _algo_order_trigger_price(order: dict[str, Any]) -> Decimal | None:
    return _to_decimal(order.get("triggerPx") or order.get("triggerPrice") or order.get("stopPrice"))


def _quantity_matches_command_or_result(*, order: dict[str, Any], command: ExecutionCommand, result: CommandExecutionResult) -> bool:
    if _to_bool(order.get("closePosition")) or str(order.get("closeFraction") or "") == "1":
        return True
    order_quantity = _to_decimal(order.get("sz") or order.get("quantity") or order.get("origQty"))
    if order_quantity is None:
        return True
    expected_values: list[Any] = []
    details = result.details if isinstance(result.details, dict) else {}
    expected_values.append(details.get("response_summary", {}).get("resolved_position_amt") if isinstance(details.get("response_summary"), dict) else None)
    expected_values.append(details.get("prepared_request", {}).get("params", {}).get("quantity") if isinstance(details.get("prepared_request"), dict) else None)
    expected_values.append(details.get("prepared_request", {}).get("body", {}).get("sz") if isinstance(details.get("prepared_request"), dict) else None)
    expected_values.append(details.get("signed_request", {}).get("params", {}).get("quantity") if isinstance(details.get("signed_request"), dict) else None)
    expected_values.append(details.get("signed_request", {}).get("body", {}).get("sz") if isinstance(details.get("signed_request"), dict) else None)
    expected_values.append(getattr(command.payload, "quantity", None))
    for value in expected_values:
        expected = _to_decimal(value)
        if expected is not None and abs(order_quantity - expected) <= STOP_PRICE_TOLERANCE:
            return True
    return False


def _trigger_price_matches_command_or_result(*, order: dict[str, Any], command: ExecutionCommand, result: CommandExecutionResult) -> bool:
    order_trigger = _algo_order_trigger_price(order)
    if order_trigger is None:
        return False
    expected_values: list[Any] = []
    details = result.details if isinstance(result.details, dict) else {}
    expected_values.append(details.get("response_summary", {}).get("resolved_stop_price") if isinstance(details.get("response_summary"), dict) else None)
    expected_values.append(details.get("prepared_request", {}).get("params", {}).get("triggerPrice") if isinstance(details.get("prepared_request"), dict) else None)
    expected_values.append(details.get("prepared_request", {}).get("params", {}).get("stopPrice") if isinstance(details.get("prepared_request"), dict) else None)
    expected_values.append(details.get("prepared_request", {}).get("body", {}).get("triggerPx") if isinstance(details.get("prepared_request"), dict) else None)
    expected_values.append(details.get("signed_request", {}).get("params", {}).get("triggerPrice") if isinstance(details.get("signed_request"), dict) else None)
    expected_values.append(details.get("signed_request", {}).get("params", {}).get("stopPrice") if isinstance(details.get("signed_request"), dict) else None)
    expected_values.append(details.get("signed_request", {}).get("body", {}).get("triggerPx") if isinstance(details.get("signed_request"), dict) else None)
    for value in expected_values:
        expected = _to_decimal(value)
        if expected is not None and abs(order_trigger - expected) <= STOP_PRICE_TOLERANCE:
            return True
    return False


def _commands_direction(commands: list[ExecutionCommand]) -> str:
    for command in commands:
        direction = str(getattr(command.payload, "direction", "") or "")
        if direction in {"long", "short"}:
            return direction
    return ""


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() == "true"


def _to_decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _synthetic_command_result(*, target: str, reason: str, command: ExecutionCommand | None = None) -> CommandExecutionResult:
    return CommandExecutionResult(
        target=target,
        status="blocked",
        accepted=False,
        simulated=False,
        reason=reason,
        details={"synthetic_worker_block": True},
        idempotency_key=command.idempotency_key if command is not None else "",
        error_kind="worker_blocked",
    )


def _unconfirmed_submission_result(
    *,
    command: ExecutionCommand,
    submitted_result: CommandExecutionResult,
    reason: str,
) -> CommandExecutionResult:
    details = dict(submitted_result.details) if isinstance(submitted_result.details, dict) else {}
    details["unconfirmed_submission_result"] = submitted_result.model_dump(mode="json")
    details["synthetic_worker_block"] = True
    return CommandExecutionResult(
        target=command.target,
        status="timeout",
        accepted=False,
        simulated=False,
        reason=reason,
        details=details,
        idempotency_key=command.idempotency_key,
        client_order_id=submitted_result.client_order_id,
        exchange_order_id=submitted_result.exchange_order_id,
        error_kind="timeout",
    )


def _summarize_execution_status(results: list[CommandExecutionResult]) -> str:
    if not results:
        return "all_failed"
    accepted = [result for result in results if result.accepted]
    if len(accepted) == len(results):
        return "submitted_all_accepted"
    if not accepted:
        return "all_failed"
    return "partial_failed"


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
