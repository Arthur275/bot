from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any


def atomic_write_json(path: str | Path, payload: dict[str, Any], *, sort_keys: bool = False) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=sort_keys)
    temp_path = target.with_name(f".awj-{os.getpid()}-{time.time_ns():x}.tmp")
    try:
        with temp_path.open("w", encoding="utf-8") as file:
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp_path, target)
    except Exception:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass
        raise
