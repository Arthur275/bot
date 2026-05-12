from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from scripts.path_utils import repo_root_from_script
from scripts.quant_runtime_bridge import ensure_runtime_src_paths, load_quant_runtime_contracts


def _add_src_paths(*, bot_root: Path, quant_root: Path) -> None:
    ensure_runtime_src_paths(bot_root=bot_root, quant_root=quant_root)


def main() -> int:
    bot_root = repo_root_from_script(__file__)
    default_quant_root = Path(os.environ.get("QUANT_ROOT") or bot_root.parent / "quant_system_rebuild")
    parser = argparse.ArgumentParser(description="Run one ETH bot shadow cycle against quant strict-live output.")
    parser.add_argument("--quant-root", default=str(default_quant_root))
    parser.add_argument(
        "--output-root",
        default=str(Path.home() / ".codex" / "memories" / "eth_bot_shadow_live"),
    )
    parser.add_argument("--proxy-url", default="http://127.0.0.1:7897")
    parser.add_argument("--include-okx-overlay", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--include-coinglass-overlay", action=argparse.BooleanOptionalAction, default=None)
    args = parser.parse_args()

    quant_root = Path(args.quant_root)
    contracts = load_quant_runtime_contracts(bot_root=bot_root, quant_root=quant_root)

    from bot.config import BotConfig
    from bot.engine_client import EngineClient
    from bot.orchestrator import ShadowOrchestrator

    output_root = Path(args.output_root)
    if not output_root.is_absolute():
        output_root = bot_root / output_root
    output_root.mkdir(parents=True, exist_ok=True)

    config = BotConfig(
        audit_log_path=output_root / "audit.jsonl",
        state_store_path=output_root / "state.json",
        artifacts_root=output_root / "artifacts",
        proxy_url=args.proxy_url or None,
        include_okx_overlay=bool(args.include_okx_overlay),
        include_coinglass_overlay=args.include_coinglass_overlay,
    )
    client = EngineClient(
        config,
        run_live_judgement_fn=contracts.run_live_judgement,
        build_execution_handoff_fn=contracts.build_execution_handoff,
        decision_envelope_factory=contracts.decision_envelope_factory,
    )
    report = ShadowOrchestrator(config, engine_client=client).run_cycle(
        generated_at=datetime.now().replace(microsecond=0)
    )

    payload = {
        "runtime_mode": report.runtime_mode,
        "requested_action": report.requested_action,
        "effective_action": report.effective_action,
        "plan_reason": report.plan_reason,
        "command_types": report.command_types,
        "command_result_statuses": report.command_result_statuses,
        "adapter_action_types": report.adapter_action_types,
        "blocked": report.blocked,
        "degraded": report.degraded,
        "reason_codes": report.reason_codes,
        "state_path": report.state_path,
        "audit_log_path": report.audit_log_path,
        "execution_overview": report.execution_overview,
    }
    audit_path = output_root / "audit.jsonl"
    if audit_path.exists():
        audit_event = json.loads(audit_path.read_text(encoding="utf-8").splitlines()[-1])
        execution_plan = audit_event["payload"].get("execution_plan", {})
        payload["audit_execution_plan"] = {
            "requested_action": execution_plan.get("requested_action"),
            "effective_action": execution_plan.get("effective_action"),
            "plan_reason": execution_plan.get("plan_reason"),
            "executable_size_pct": execution_plan.get("executable_size_pct"),
            "place_entry_order": execution_plan.get("place_entry_order"),
            "risk_gate_reason_codes": execution_plan.get("risk_gate_reason_codes"),
        }

    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
