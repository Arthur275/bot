from __future__ import annotations

import argparse
import os
import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

try:
    from scripts.path_utils import repo_root_from_script
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from scripts.path_utils import repo_root_from_script

BOT_ROOT = repo_root_from_script(__file__)
SRC_ROOT = BOT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from bot.atomic_io import atomic_write_json
from bot.config import bot_runtime_scheduler_root, kill_switch_path, repo_root_from_file

try:
    from .run_shadow_preflight_cycle import DEFAULT_QUANT_ROOT, ParsedArgs, default_output_root, run_cycle
    from .run_shadow_preflight_sampler import _summarize_sample
except ImportError:
    from scripts.ops.run_shadow_preflight_cycle import DEFAULT_QUANT_ROOT, ParsedArgs, default_output_root, run_cycle
    from scripts.ops.run_shadow_preflight_sampler import _summarize_sample


CycleRunner = Callable[..., dict[str, Any]]


def default_runtime_root() -> str:
    return str(bot_runtime_scheduler_root(repo_root_from_file(__file__)))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Bot runtime scheduler: consume quant strict-live, run bot shadow planning, and OKX preflight without submitting orders."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_common(subparser: argparse.ArgumentParser) -> None:
        subparser.add_argument("--quant-root", default=DEFAULT_QUANT_ROOT)
        subparser.add_argument("--runtime-root", default=default_runtime_root())
        subparser.add_argument("--cycle-output-root", default=default_output_root())
        subparser.add_argument("--proxy-url", default="http://127.0.0.1:7897")
        subparser.add_argument("--include-okx-overlay", action=argparse.BooleanOptionalAction, default=True)
        subparser.add_argument("--include-coinglass-overlay", action=argparse.BooleanOptionalAction, default=None)
        subparser.add_argument("--consensus-request-timeout-sec", type=float, default=15.0)
        subparser.add_argument("--research-sync-request", dest="research_sync_request_path", default=None)
        subparser.add_argument("--research-dispatch-request", dest="research_dispatch_request_path", default=None)
        subparser.add_argument("--api-key-env", default=None)
        subparser.add_argument("--api-secret-env", default=None)
        subparser.add_argument("--api-passphrase-env", default=None)
        subparser.add_argument("--analysis-db-path", default=None)
        subparser.add_argument("--skip-analysis-ingest", action="store_true", default=False)
        subparser.add_argument("--enable-real-orders", action="store_true", default=False)
        subparser.add_argument("--kill-switch-path", default=str(kill_switch_path(repo_root_from_file(__file__))))

    once = subparsers.add_parser("run-once", help="Run one safe bot automation cycle.")
    add_common(once)

    loop = subparsers.add_parser("loop", help="Run safe bot automation cycles repeatedly.")
    add_common(loop)
    loop.add_argument("--interval-sec", type=int, default=300)
    loop.add_argument("--cycles", type=int, default=0, help="0 means run forever.")
    loop.add_argument("--max-consecutive-failures", type=int, default=3)
    loop.add_argument("--degraded-heartbeat-interval-sec", type=int, default=60)

    heartbeat = subparsers.add_parser("heartbeat", help="Write a scheduler heartbeat without running quant/bot.")
    heartbeat.add_argument("--runtime-root", default=default_runtime_root())
    return parser


def main() -> int:
    args = build_parser().parse_args()
    bot_root = BOT_ROOT
    if args.command == "heartbeat":
        payload = write_heartbeat(runtime_root=Path(args.runtime_root), status="ok", mode="heartbeat")
        print(json.dumps(payload, ensure_ascii=True, indent=2))
        return 0
    if args.command == "run-once":
        payload = run_once(args=args, bot_root=bot_root)
        print(json.dumps(payload, ensure_ascii=True, indent=2))
        return 0 if payload["status"] == "ok" else 1
    if args.command == "loop":
        return run_loop(args=args, bot_root=bot_root)
    raise ValueError(f"Unsupported command: {args.command}")


def run_once(*, args: argparse.Namespace, bot_root: Path, cycle_runner: CycleRunner = run_cycle) -> dict[str, Any]:
    runtime_root = Path(args.runtime_root)
    runtime_root.mkdir(parents=True, exist_ok=True)
    sample_id = _next_sample_id(runtime_root / "samples.jsonl")
    started_at = datetime.now().replace(microsecond=0)
    cycle_output_root = Path(args.cycle_output_root) / f"bot_runtime_{sample_id:04d}"
    try:
        payload = cycle_runner(args=_build_cycle_args(args, cycle_output_root), bot_root=bot_root)
        summary = _summarize_sample(sample_id=sample_id, payload=payload, started_at=started_at, status="ok")
    except Exception as exc:
        summary = _summarize_sample(
            sample_id=sample_id,
            payload={},
            started_at=started_at,
            status="error",
            error=f"{exc.__class__.__name__}: {exc}",
        )

    summary["mode"] = "shadow_preflight_only"
    gate = _evaluate_real_order_gate(
        payload=payload if "payload" in locals() else {},
        enable_real_orders=bool(args.enable_real_orders),
        kill_switch_path=getattr(args, "kill_switch_path", None),
    )
    summary["real_order_gate"] = gate
    summary["automation_boundary"] = gate["automation_boundary"]
    package = _write_candidate_execution_package(
        runtime_root=runtime_root,
        payload=payload if "payload" in locals() else {},
        summary=summary,
        real_order_gate=gate,
    )
    summary["candidate_execution_package"] = package
    if not bool(getattr(args, "skip_analysis_ingest", False)):
        summary["analysis_ingest"] = _run_analysis_ingest(
            audit_log_path=summary.get("audit_log_path") or "",
            db_path=getattr(args, "analysis_db_path", None),
            runtime_root=runtime_root,
        )
    _append_jsonl(runtime_root / "samples.jsonl", summary)
    _write_json(runtime_root / "latest_cycle.json", summary)
    write_heartbeat(
        runtime_root=runtime_root,
        status=summary["status"],
        mode="run-once",
        extra={
            "sample_id": sample_id,
            "latest_cycle_path": str(runtime_root / "latest_cycle.json"),
            "samples_path": str(runtime_root / "samples.jsonl"),
            "automation_boundary": summary["automation_boundary"],
            "candidate_execution_package_path": package.get("latest_path", ""),
        },
    )
    return summary


def run_loop(*, args: argparse.Namespace, bot_root: Path, cycle_runner: CycleRunner = run_cycle) -> int:
    lock_path = Path(args.runtime_root) / "scheduler.lock"
    with SchedulerLock(lock_path=lock_path):
        return _run_loop_unlocked(args=args, bot_root=bot_root, cycle_runner=cycle_runner)


def _run_loop_unlocked(*, args: argparse.Namespace, bot_root: Path, cycle_runner: CycleRunner = run_cycle) -> int:
    consecutive_failures = 0
    cycle_count = 0
    while args.cycles <= 0 or cycle_count < args.cycles:
        cycle_count += 1
        payload = run_once(args=args, bot_root=bot_root, cycle_runner=cycle_runner)
        if payload["status"] == "ok":
            consecutive_failures = 0
            sleep_sec = max(1, int(args.interval_sec))
        else:
            consecutive_failures += 1
            degraded = consecutive_failures >= max(1, int(args.max_consecutive_failures))
            write_heartbeat(
                runtime_root=Path(args.runtime_root),
                status="degraded" if degraded else "error",
                mode="loop",
                extra={
                    "consecutive_failures": consecutive_failures,
                    "max_consecutive_failures": int(args.max_consecutive_failures),
                    "last_error": payload.get("error") or "",
                },
            )
            sleep_sec = (
                max(1, int(args.degraded_heartbeat_interval_sec))
                if degraded
                else max(1, int(args.interval_sec))
            )
        print(json.dumps(payload, ensure_ascii=True, indent=2))
        if args.cycles <= 0 or cycle_count < args.cycles:
            time.sleep(sleep_sec)
    return 0


class SchedulerLock:
    def __init__(self, *, lock_path: Path, stale_after_sec: int = 900) -> None:
        self._lock_path = lock_path
        self._stale_after_sec = stale_after_sec
        self._handle: int | None = None

    def __enter__(self) -> "SchedulerLock":
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        try:
            self._handle = os.open(str(self._lock_path), flags)
        except FileExistsError as exc:
            if not self._try_clear_stale_lock():
                raise RuntimeError(f"bot runtime scheduler already running: {self._lock_path}") from exc
            self._handle = os.open(str(self._lock_path), flags)
        payload = {
            "pid": os.getpid(),
            "created_at": datetime.now().replace(microsecond=0).isoformat(),
        }
        os.write(self._handle, json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8"))
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
        lock_pid = self._read_lock_pid()
        if lock_pid is not None and not _process_exists(lock_pid):
            try:
                self._lock_path.unlink()
            except FileNotFoundError:
                return True
            return True
        age_sec = max(0.0, time.time() - stat.st_mtime)
        if age_sec < self._stale_after_sec:
            return False
        try:
            self._lock_path.unlink()
        except FileNotFoundError:
            return True
        return True

    def _read_lock_pid(self) -> int | None:
        try:
            payload = json.loads(self._lock_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        try:
            pid = int(payload.get("pid"))
        except (TypeError, ValueError):
            return None
        return pid if pid > 0 else None


def _process_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def write_heartbeat(*, runtime_root: Path, status: str, mode: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    runtime_root.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now().replace(microsecond=0).isoformat(),
        "status": status,
        "mode": mode,
    }
    payload.update(extra or {})
    _write_json(runtime_root / "heartbeat.json", payload)
    return payload


def _build_cycle_args(args: argparse.Namespace, output_root: Path) -> ParsedArgs:
    cycle_args = ParsedArgs()
    cycle_args.quant_root = args.quant_root
    cycle_args.output_root = str(output_root)
    cycle_args.proxy_url = args.proxy_url
    cycle_args.include_okx_overlay = bool(args.include_okx_overlay)
    cycle_args.include_coinglass_overlay = args.include_coinglass_overlay
    cycle_args.consensus_request_timeout_sec = float(getattr(args, "consensus_request_timeout_sec", 15.0) or 15.0)
    cycle_args.research_sync_request_path = getattr(args, "research_sync_request_path", None)
    cycle_args.research_dispatch_request_path = getattr(args, "research_dispatch_request_path", None)
    cycle_args.api_key_env = args.api_key_env
    cycle_args.api_secret_env = args.api_secret_env
    cycle_args.api_passphrase_env = getattr(args, "api_passphrase_env", None)
    return cycle_args


def _evaluate_real_order_gate(*, payload: dict[str, Any], enable_real_orders: bool, kill_switch_path: str | None = None) -> dict[str, Any]:
    try:
        from bot.automation_gate import evaluate_real_order_gate

        return evaluate_real_order_gate(
            payload=payload,
            enable_real_orders=enable_real_orders,
            kill_switch_path=kill_switch_path,
        ).model_dump(mode="json")
    except Exception as exc:
        return {
            "enabled": bool(enable_real_orders),
            "allowed": False,
            "action": str(payload.get("effective_action") or payload.get("requested_action") or ""),
            "automation_boundary": "real_order_submission_blocked" if enable_real_orders else "no_order_submission",
            "reason_codes": [f"real_order_gate_error:{exc.__class__.__name__}"],
        }


def _run_analysis_ingest(*, audit_log_path: str, db_path: str | None, runtime_root: Path) -> dict[str, Any]:
    if not audit_log_path:
        return {"status": "skipped", "reason": "audit_log_path_missing"}
    path = Path(audit_log_path)
    if not path.exists():
        return {"status": "skipped", "reason": "audit_log_path_not_found", "audit_log_path": str(path)}
    try:
        from bot.analysis.runtime_dataset import ingest_audit_log, write_runtime_summary

        effective_db_path = db_path or str(runtime_root / "analysis" / "bot_runtime.duckdb")
        counts = ingest_audit_log(audit_log_path=path, db_path=effective_db_path)
        write_runtime_summary(
            db_path=effective_db_path,
            output_json_path=runtime_root / "analysis" / "bot_runtime_summary.json",
            output_md_path=runtime_root / "analysis" / "bot_runtime_summary.md",
        )
        return {"status": "ok", "db_path": effective_db_path, **counts}
    except Exception as exc:
        return {"status": "error", "error": f"{exc.__class__.__name__}: {exc}"}


def _write_candidate_execution_package(
    *,
    runtime_root: Path,
    payload: dict[str, Any],
    summary: dict[str, Any],
    real_order_gate: dict[str, Any],
) -> dict[str, Any]:
    if not _candidate_package_allowed(payload=payload, real_order_gate=real_order_gate):
        return {
            "status": "skipped",
            "reason": "candidate_execution_package_not_allowed",
        }
    generated_at = datetime.now().replace(microsecond=0)
    action = str(payload.get("effective_action") or payload.get("requested_action") or "")
    package_id = _build_package_id(generated_at=generated_at, action=action)
    package = {
        "package_id": package_id,
        "generated_at": generated_at.isoformat(),
        "expires_at": (generated_at + timedelta(seconds=180)).isoformat(),
        "runtime_mode": payload.get("runtime_mode") or "",
        "engine_mode": payload.get("engine_mode") or (payload.get("handoff") or {}).get("engine_mode") or "strict-live",
        "symbol": payload.get("symbol") or "ETH",
        "exchange_symbol": payload.get("exchange_symbol") or "ETH-USDT-SWAP",
        "action": action,
        "direction": (payload.get("handoff") or {}).get("direction") or "",
        "handoff": payload.get("handoff") or {},
        "execution_plan": payload.get("execution_plan") or {},
        "execution_commands": payload.get("execution_commands") or [],
        "preflight": payload.get("preflight") or [],
        "real_order_gate": real_order_gate,
        "audit_log_path": payload.get("audit_log_path") or summary.get("audit_log_path") or "",
        "state_path": payload.get("state_path") or summary.get("state_path") or "",
        "source_cycle_path": str(runtime_root / "latest_cycle.json"),
    }
    candidates_dir = runtime_root / "candidates"
    latest_path = runtime_root / "latest_candidate_execution_package.json"
    archive_path = candidates_dir / f"candidate_{generated_at.strftime('%Y%m%dT%H%M%S')}_{package_id[-12:]}.json"
    _write_json(archive_path, package)
    _write_json(latest_path, package)
    return {
        "status": "written",
        "package_id": package_id,
        "latest_path": str(latest_path),
        "archive_path": str(archive_path),
        "expires_at": package["expires_at"],
    }


def _candidate_package_allowed(*, payload: dict[str, Any], real_order_gate: dict[str, Any]) -> bool:
    if real_order_gate.get("enabled") is not True:
        return False
    if real_order_gate.get("allowed") is not True:
        return False
    if real_order_gate.get("automation_boundary") != "real_order_submission_allowed":
        return False
    preflight = payload.get("preflight") or []
    if not preflight:
        return False
    return all(item.get("status") == "preflight_ready" and not item.get("error") for item in preflight)


def _build_package_id(*, generated_at: datetime, action: str) -> str:
    normalized_action = action or "unknown"
    return f"bot-eth-{normalized_action}-{generated_at.strftime('%Y%m%dT%H%M%S')}"


def _next_sample_id(samples_path: Path) -> int:
    if not samples_path.exists():
        return 1
    return sum(1 for line in samples_path.read_text(encoding="utf-8").splitlines() if line.strip()) + 1


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    atomic_write_json(path, payload, sort_keys=True)


if __name__ == "__main__":
    raise SystemExit(main())
