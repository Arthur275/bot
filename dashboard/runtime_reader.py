from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class RuntimeSnapshotReader:
    bot_root: Path
    quant_root: Path

    @property
    def bot_runtime(self) -> Path:
        return self.bot_root / "runtime"

    @property
    def quant_runtime(self) -> Path:
        return self.quant_root / "runtime"

    @property
    def bot_scheduler_root(self) -> Path:
        return self.bot_runtime / "bot_runtime_scheduler"

    @property
    def quant_analysis_root(self) -> Path:
        return self.quant_runtime / "analysis"

    @property
    def quant_scheduler_root(self) -> Path:
        return self.quant_runtime / "scheduler"

    @property
    def kill_switch_path(self) -> Path:
        return self.bot_runtime / "controls" / "disable_real_execution.flag"

    def json(self, path: Path) -> dict[str, Any]:
        return read_json(path)

    def json_status(self, path: Path) -> dict[str, Any]:
        return json_read_status(path)

    def tail_jsonl(self, path: Path, *, limit: int) -> list[dict[str, Any]]:
        return tail_jsonl(path, limit=limit)

    def jsonl_count(self, path: Path) -> int:
        return jsonl_count(path)

    def mtime_iso(self, path: Path) -> str:
        return mtime_iso(path)


def read_json(path: Path) -> dict[str, Any]:
    status = json_read_status(path)
    return status["payload"] if isinstance(status.get("payload"), dict) else {}


def json_read_status(path: Path) -> dict[str, Any]:
    try:
        if not path.exists():
            return {"status": "missing", "path": str(path), "payload": {}}
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        return {"status": "read_error", "path": str(path), "error": str(exc), "payload": {}}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        return {"status": "invalid_json", "path": str(path), "error": str(exc), "payload": {}}
    if not isinstance(payload, dict):
        return {"status": "not_object", "path": str(path), "payload": {}}
    return {"status": "ok", "path": str(path), "payload": payload}


def tail_jsonl(path: Path, *, limit: int) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()[-limit:]
    except OSError:
        return []
    events: list[dict[str, Any]] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            events.append(payload)
    return events


def jsonl_count(path: Path) -> int:
    try:
        return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
    except OSError:
        return 0


def mtime_iso(path: Path) -> str:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()
    except OSError:
        return ""
