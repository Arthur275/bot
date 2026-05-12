from __future__ import annotations

from pathlib import Path


def repo_root_from_script(file_path: str | Path) -> Path:
    path = Path(file_path).resolve()
    for parent in path.parents:
        if (parent / "src" / "bot").exists() and (parent / "scripts").exists():
            return parent
    raise RuntimeError(f"Cannot resolve eth_trading_bot repo root from {path}")
