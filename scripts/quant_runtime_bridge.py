from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class QuantRuntimeContracts:
    decision_envelope_factory: Callable[[dict[str, Any]], Any]
    run_live_judgement: Callable[..., dict[str, Any]]
    build_execution_handoff: Callable[[Any], Any]


@dataclass(frozen=True)
class QuantOutcomeWriters:
    decision_outcome_factory: Callable[..., Any]
    upsert_decision_outcome: Callable[..., Any]
    write_decision_outcomes_summary: Callable[..., Any]


def ensure_quant_src_path(quant_root: Path) -> Path:
    quant_src = quant_root / "src"
    normalized = str(quant_src)
    if normalized not in sys.path:
        sys.path.insert(0, normalized)
    return quant_src


def ensure_runtime_src_paths(*, bot_root: Path, quant_root: Path) -> None:
    for src_path in (bot_root / "src", ensure_quant_src_path(quant_root)):
        normalized = str(src_path)
        if normalized not in sys.path:
            sys.path.insert(0, normalized)


def load_quant_runtime_contracts(*, bot_root: Path, quant_root: Path) -> QuantRuntimeContracts:
    ensure_runtime_src_paths(bot_root=bot_root, quant_root=quant_root)
    from contracts.execution import DecisionEnvelope
    from interfaces.live_judgement import run_live_judgement
    from interfaces.runner import build_execution_handoff

    return QuantRuntimeContracts(
        decision_envelope_factory=DecisionEnvelope.model_validate,
        run_live_judgement=run_live_judgement,
        build_execution_handoff=build_execution_handoff,
    )


def load_quant_outcome_writers(*, quant_root: Path, fallback_quant_root: Path) -> QuantOutcomeWriters:
    requested_src = quant_root / "src"
    selected_root = quant_root if (requested_src / "analysis" / "decision_outcomes.py").exists() else fallback_quant_root
    quant_src = ensure_quant_src_path(selected_root)
    sys.path[:] = [item for item in sys.path if item != str(quant_src)]
    sys.path.insert(0, str(quant_src))
    clear_quant_module_cache()

    from analysis import DecisionOutcome, write_decision_outcomes_summary
    from analysis.decision_outcomes import upsert_decision_outcome

    return QuantOutcomeWriters(
        decision_outcome_factory=DecisionOutcome,
        upsert_decision_outcome=upsert_decision_outcome,
        write_decision_outcomes_summary=write_decision_outcomes_summary,
    )


def clear_quant_module_cache() -> None:
    for module_name in list(sys.modules):
        if (
            module_name == "analysis"
            or module_name.startswith("analysis.")
            or module_name == "contracts"
            or module_name.startswith("contracts.")
            or module_name == "interfaces"
            or module_name.startswith("interfaces.")
        ):
            sys.modules.pop(module_name, None)
