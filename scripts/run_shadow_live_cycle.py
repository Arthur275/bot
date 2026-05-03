from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path


def _add_src_paths(*, bot_root: Path, quant_root: Path) -> None:
    for src_path in (bot_root / "src", quant_root / "src"):
        normalized = str(src_path)
        if normalized not in sys.path:
            sys.path.insert(0, normalized)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one ETH bot shadow cycle against quant strict-live output.")
    parser.add_argument("--quant-root", default="D:/开发/quant_system_rebuild")
    parser.add_argument(
        "--output-root",
        default="C:/Users/左秋三/.codex/memories/eth_bot_shadow_live",
    )
    parser.add_argument("--proxy-url", default="http://127.0.0.1:7897")
    parser.add_argument("--include-okx-overlay", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--include-coinglass-overlay", action=argparse.BooleanOptionalAction, default=False)
    args = parser.parse_args()

    bot_root = Path(__file__).resolve().parents[1]
    quant_root = Path(args.quant_root)
    _add_src_paths(bot_root=bot_root, quant_root=quant_root)

    from bot.config import BotConfig
    from bot.engine_client import EngineClient
    from bot.orchestrator import ShadowOrchestrator
    from contracts.execution import DecisionEnvelope
    from interfaces.live_judgement import run_live_judgement
    from interfaces.runner import build_execution_handoff

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
        include_coinglass_overlay=bool(args.include_coinglass_overlay),
    )
    client = EngineClient(
        config,
        run_live_judgement_fn=run_live_judgement,
        build_execution_handoff_fn=build_execution_handoff,
        decision_envelope_factory=DecisionEnvelope.model_validate,
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
