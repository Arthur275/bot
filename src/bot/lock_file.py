from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Callable

from .time_utils import utc_now


ProcessCheck = Callable[[int], bool]
MetadataPredicate = Callable[[dict[str, Any]], bool]
StartTokenResolver = Callable[[int], str]


class ExclusiveLock:
    def __init__(
        self,
        *,
        lock_path: Path,
        stale_after_sec: int = 900,
        owner: str,
        extra_payload: dict[str, Any] | None = None,
        process_check: ProcessCheck | None = None,
        protected_metadata: MetadataPredicate | None = None,
        start_token_resolver: StartTokenResolver | None = None,
    ) -> None:
        self._lock_path = lock_path
        self._stale_after_sec = stale_after_sec
        self._owner = owner
        self._extra_payload = dict(extra_payload or {})
        self._process_check = process_check or process_exists
        self._protected_metadata = protected_metadata
        self._start_token_resolver = start_token_resolver
        self._handle: int | None = None
        self._token = f"{os.getpid()}-{time.time_ns():x}"

    def __enter__(self) -> "ExclusiveLock":
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self._open_exclusive()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._handle is not None:
            os.close(self._handle)
            self._handle = None
        self._release_owned_lock()

    def _open_exclusive(self) -> int:
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        try:
            handle = os.open(str(self._lock_path), flags)
        except FileExistsError as exc:
            if not self._claim_stale_lock():
                raise RuntimeError(f"{self._owner} already running: {self._lock_path}") from exc
            try:
                handle = os.open(str(self._lock_path), flags)
            except FileExistsError as retry_exc:
                raise RuntimeError(f"{self._owner} already running: {self._lock_path}") from retry_exc
        payload = self._build_payload()
        os.write(handle, json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8"))
        return handle

    def _build_payload(self) -> dict[str, Any]:
        payload = {
            "pid": os.getpid(),
            "owner": self._owner,
            "created_at": utc_now().isoformat(),
            "lock_token": self._token,
        }
        if self._start_token_resolver is not None:
            payload["process_start_token"] = self._start_token_resolver(os.getpid())
        payload.update(self._extra_payload)
        return payload

    def _claim_stale_lock(self) -> bool:
        try:
            stat = self._lock_path.stat()
        except FileNotFoundError:
            return True
        metadata = read_lock_metadata(self._lock_path)
        if self._protected_metadata is not None and self._protected_metadata(metadata):
            return False
        pid = coerce_positive_int(metadata.get("pid"))
        if self._protected_metadata is None and pid is not None and self._process_check(pid):
            return False
        if pid is not None and not self._process_check(pid):
            return self._move_lock_to_tombstone()
        age_sec = max(0.0, time.time() - stat.st_mtime)
        if age_sec < self._stale_after_sec:
            return False
        return self._move_lock_to_tombstone()

    def _move_lock_to_tombstone(self) -> bool:
        tombstone_path = self._lock_path.with_name(f".{self._lock_path.name}.{os.getpid()}.{time.time_ns():x}.stale")
        try:
            os.replace(self._lock_path, tombstone_path)
        except FileNotFoundError:
            return True
        except OSError:
            return False
        return True

    def _release_owned_lock(self) -> None:
        metadata = read_lock_metadata(self._lock_path)
        if str(metadata.get("lock_token") or "") != self._token:
            return
        try:
            self._lock_path.unlink()
        except FileNotFoundError:
            pass


def read_lock_metadata(lock_path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def coerce_positive_int(value: object) -> int | None:
    try:
        candidate = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return candidate if candidate > 0 else None


def process_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True
