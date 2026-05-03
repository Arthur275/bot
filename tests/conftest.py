from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest


@pytest.fixture
def tmp_path(request: pytest.FixtureRequest) -> Path:
    safe_name = "".join(
        character if character.isalnum() or character in {"_", "-"} else "_"
        for character in request.node.nodeid
    )
    configured_root = os.environ.get("BOT_TEST_TMP_ROOT")
    if configured_root:
        root = Path(configured_root) / "pytest_case_tmp"
    else:
        root = Path.home() / ".codex" / "memories" / "pytest_bot" / "pytest_case_tmp"
    root.mkdir(parents=True, exist_ok=True)
    path = root / safe_name
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True, exist_ok=True)
    return path
