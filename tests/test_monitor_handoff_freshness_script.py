import importlib.util
import json
from datetime import UTC, datetime
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "scripts" / "diagnostics" / "monitor_handoff_freshness.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("monitor_handoff_freshness_test", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_monitor_handoff_freshness_alerts_when_factor_lookup_age_exceeds_threshold(tmp_path: Path) -> None:
    module = _load_module()
    quant_root = tmp_path / "quant_system_rebuild"
    _write_json(
        quant_root / "runtime" / "cycles" / "latest_strict_live" / "handoff.json",
        {
            "generated_at": "2026-05-18T12:00:00Z",
            "factor_lookup_generated_at": "2026-05-18T08:00:00Z",
            "factor_lookup_stale": False,
        },
    )

    payload = module.monitor_handoff_freshness(
        quant_root=quant_root,
        max_age_sec=3 * 60 * 60,
        now=datetime(2026, 5, 18, 12, 0, 0, tzinfo=UTC),
    )

    assert payload["status"] == "alert"
    assert payload["factor_lookup_age_seconds"] == 14400.0
    assert "factor_lookup_age_over_threshold" in payload["reason_codes"]
    assert "factor_lookup_stale_flag_conflict" in payload["reason_codes"]


def test_monitor_handoff_freshness_alerts_when_scoring_chain_is_frozen(tmp_path: Path) -> None:
    module = _load_module()
    quant_root = tmp_path / "quant_system_rebuild"
    _write_json(
        quant_root / "runtime" / "cycles" / "latest_strict_live" / "handoff.json",
        {
            "generated_at": "2026-05-18T12:00:00Z",
            "factor_lookup_generated_at": "2026-05-18T11:30:00Z",
            "factor_lookup_stale": False,
            "scoring_chain_frozen": True,
        },
    )

    payload = module.monitor_handoff_freshness(
        quant_root=quant_root,
        max_age_sec=3 * 60 * 60,
        now=datetime(2026, 5, 18, 12, 0, 0, tzinfo=UTC),
    )

    assert payload["status"] == "alert"
    assert payload["factor_lookup_stale"] is False
    assert payload["reason_codes"] == ["scoring_chain_frozen"]


def test_monitor_handoff_freshness_passes_fresh_handoff(tmp_path: Path) -> None:
    module = _load_module()
    quant_root = tmp_path / "quant_system_rebuild"
    _write_json(
        quant_root / "runtime" / "cycles" / "eth-15m-current" / "handoff.json",
        {
            "generated_at": "2026-05-18T12:00:00Z",
            "factor_lookup_generated_at": "2026-05-18T11:30:00Z",
            "factor_lookup_stale": False,
        },
    )

    payload = module.monitor_handoff_freshness(
        quant_root=quant_root,
        max_age_sec=3 * 60 * 60,
        now=datetime(2026, 5, 18, 12, 0, 0, tzinfo=UTC),
    )

    assert payload["status"] == "ok"
    assert payload["reason_codes"] == []
    assert payload["factor_lookup_age_seconds"] == 1800.0
