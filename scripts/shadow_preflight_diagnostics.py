from __future__ import annotations

import sys
from pathlib import Path

_repo_root = Path(__file__).resolve().parents[1]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))
from scripts.ops import shadow_preflight_diagnostics as _impl

sys.modules[__name__] = _impl
