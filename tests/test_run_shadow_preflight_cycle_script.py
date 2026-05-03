from __future__ import annotations

import sys
from pathlib import Path

from datetime import datetime

from bot.exchange_adapter import AdapterCapabilities, AdapterRuntimeSnapshot, CommandExecutionResult, PositionSnapshot

from scripts import run_shadow_preflight_cycle


def _write_fake_quant_modules(src_root: Path, *, handoff_payload: str) -> None:
    contracts_dir = src_root / "contracts"
    interfaces_dir = src_root / "interfaces"
    contracts_dir.mkdir(parents=True, exist_ok=True)
    interfaces_dir.mkdir(parents=True, exist_ok=True)
    (contracts_dir / "__init__.py").write_text("", encoding="utf-8")
    (interfaces_dir / "__init__.py").write_text("", encoding="utf-8")
    (contracts_dir / "execution.py").write_text(
        "\n".join(
            [
                "class DecisionEnvelope:",
                "    @classmethod",
                "    def model_validate(cls, payload):",
                "        return dict(payload)",
            ]
        ),
        encoding="utf-8",
    )
    (interfaces_dir / "live_judgement.py").write_text(
        "\n".join(
            [
                "def run_live_judgement(**kwargs):",
                "    return {",
                "        'status': 'ok',",
                "        'entry_mode': 'live_bundle',",
                "        'diagnostic_status': 'success',",
                "        'diagnostic_source': 'coingecko',",
                "        'diagnostic_category': 'none',",
                "        'research_bundle': {'ready': True, 'bundle_status': 'healthy'},",
                "        'decision': {'generated_at': '2026-05-01T01:00:00'},",
                "    }",
            ]
        ),
        encoding="utf-8",
    )
    (interfaces_dir / "runner.py").write_text(
        "\n".join(
            [
                "def build_execution_handoff(envelope):",
                f"    return {handoff_payload}",
            ]
        ),
        encoding="utf-8",
    )


def _args(*, quant_root: Path, output_root: Path) -> run_shadow_preflight_cycle.ParsedArgs:
    args = run_shadow_preflight_cycle.ParsedArgs()
    args.quant_root = str(quant_root)
    args.output_root = str(output_root)
    args.proxy_url = ""
    args.include_okx_overlay = False
    args.include_coinglass_overlay = False
    args.api_key_env = "BINANCE_API_KEY"
    args.api_secret_env = "BINANCE_API_SECRET"
    return args


def _clear_fake_quant_modules() -> None:
    for module_name in list(sys.modules):
        if (
            module_name == "contracts"
            or module_name.startswith("contracts.")
            or module_name == "interfaces"
            or module_name.startswith("interfaces.")
        ):
            sys.modules.pop(module_name, None)


class FakeBinancePerpAdapter:
    def __init__(self, credentials) -> None:
        self.credentials = credentials

    def get_capabilities(self):
        return AdapterCapabilities(supports_real_execution=True)

    def fetch_runtime_snapshot(self):
        return AdapterRuntimeSnapshot(
            fetched_at=datetime(2026, 5, 1, 1, 5, 0),
            snapshot_valid=True,
            account_equity=11.0,
            account_equity_source="fake",
            position=PositionSnapshot(
                position_state="FLAT",
                direction="",
                size_pct=0.0,
                mark_price=3150.0,
                leverage=10,
            ),
        )

    def assess_reconciliation(
        self,
        *,
        runtime_snapshot,
        expected_position_state,
        expected_direction,
        expected_size_pct,
    ):
        from bot.exchange_adapter import ReconciliationResult

        return ReconciliationResult(in_sync=True)

    def plan_actions(self, *, execution_plan, handoff):
        return []

    def build_commands(self, *, execution_plan, handoff):
        from bot.exchange_adapter import ExchangeAdapter

        return ExchangeAdapter().build_commands(execution_plan=execution_plan, handoff=handoff)

    def execute_commands(self, *, commands, runtime_mode):
        from bot.exchange_adapter import ExchangeAdapter

        return ExchangeAdapter().execute_commands(commands=commands, runtime_mode=runtime_mode)

    def preflight_commands(self, *, commands):
        return [
            CommandExecutionResult(
                target="entry_order",
                status="preflight_ready",
                accepted=True,
                simulated=True,
                reason="effective_action:small_probe",
                details={
                    "prepared_request": {
                        "method": "POST",
                        "path": "/fapi/v1/order",
                        "params": {
                            "side": "SELL",
                            "type": "MARKET",
                            "quantity": "0.031",
                            "newClientOrderId": "entry_order:2026-05-01T01:05:00:small_probe:short",
                        },
                        "body": {
                            "resolution_mode": "entry_quantity_from_size_pct",
                            "resolved_account_equity": "11.0",
                            "resolved_leverage": 10,
                            "resolved_mark_price": "3150.0",
                        },
                    },
                    "signed_request": {
                        "params": {
                            "quantity": "0.031",
                        },
                    },
                },
            ),
        ]


def test_shadow_preflight_script_skips_preflight_when_shadow_cycle_has_no_commands(monkeypatch, tmp_path: Path) -> None:
    quant_root = tmp_path / "fake_quant_wait"
    _write_fake_quant_modules(
        quant_root / "src",
        handoff_payload="{"
        "'generated_at': '2026-05-01T01:00:00',"
        "'action': 'wait',"
        "'direction': 'long',"
        "'execution_allowed': False,"
        "'execution_block_reason': 'not_entry_action',"
        "'position_size_pct': 0.0,"
        "'executable_size_pct': 0.0,"
        "'initial_stop_loss': 0.9809,"
        "'stop_distance_pct': 0.0191,"
        "'research_gate_status': 'open',"
        "'execution_layer_reasoning': 'waiting_for_trigger',"
        "'trigger_ready': False,"
        "'breakout_support': False,"
        "'retest_support': False,"
        "'slope_support': 0.508,"
        "'setup_direction': 'long',"
        "'snapshot_refs': {'eth_orderbook': 'ccxt_orderbook:ETH/USDT'}"
        "}",
    )

    monkeypatch.setattr(run_shadow_preflight_cycle, "_load_binance_perp_adapter", lambda: FakeBinancePerpAdapter)
    _clear_fake_quant_modules()
    payload = run_shadow_preflight_cycle.run_cycle(
        args=_args(quant_root=quant_root, output_root=tmp_path / "out_wait"),
        bot_root=Path(__file__).resolve().parents[1],
    )

    assert payload["requested_action"] == "wait"
    assert payload["effective_action"] == "wait"
    assert payload["command_targets"] == []
    assert payload["preflight_statuses"] == []
    assert payload["preflight"] == []
    assert payload["preflight_error"] == ""
    assert payload["judgement"]["status"] == "ok"
    assert payload["judgement"]["entry_mode"] == "live_bundle"
    assert payload["judgement"]["diagnostic_status"] == "success"
    assert payload["judgement"]["diagnostic_source"] == "coingecko"
    assert payload["handoff"]["execution_block_reason"] == "not_entry_action"
    assert payload["handoff"]["execution_layer_reasoning"] == "waiting_for_trigger"
    assert payload["handoff"]["trigger_ready"] is False
    assert payload["handoff"]["breakout_support"] is False
    assert payload["handoff"]["retest_support"] is False
    assert payload["handoff"]["slope_support"] == 0.508
    assert payload["handoff"]["snapshot_ref_keys"] == ["eth_orderbook"]
    assert payload["handoff"]["has_orderbook_snapshot"] is True


def test_shadow_preflight_script_runs_preflight_for_entry_commands(monkeypatch, tmp_path: Path) -> None:
    quant_root = tmp_path / "fake_quant_entry"
    _write_fake_quant_modules(
        quant_root / "src",
        handoff_payload="{"
        "'generated_at': '2026-05-01T01:05:00',"
        "'action': 'small_probe',"
        "'direction': 'short',"
        "'execution_allowed': True,"
        "'position_size_pct': 0.2,"
        "'executable_size_pct': 0.02,"
        "'max_account_risk_pct_per_trade': 0.01,"
        "'initial_stop_loss': 1.018,"
        "'stop_distance_pct': 0.018"
        "}",
    )
    calls: list[list[str]] = []

    class RecordingFakeBinancePerpAdapter(FakeBinancePerpAdapter):
        def preflight_commands(self, *, commands):
            calls.append([command.target for command in commands])
            return super().preflight_commands(commands=commands)

    monkeypatch.setattr(run_shadow_preflight_cycle, "_load_binance_perp_adapter", lambda: RecordingFakeBinancePerpAdapter)

    _clear_fake_quant_modules()
    payload = run_shadow_preflight_cycle.run_cycle(
        args=_args(quant_root=quant_root, output_root=tmp_path / "out_entry"),
        bot_root=Path(__file__).resolve().parents[1],
    )

    assert payload["requested_action"] == "small_probe"
    assert payload["effective_action"] == "small_probe"
    assert payload["execution_plan"]["executable_size_pct"] == 0.909091
    assert "fixed_margin_budget_sizing" in payload["execution_plan"]["notes"]
    assert calls == [["entry_order", "maintain_protective_stop"]]
    assert payload["command_targets"] == ["entry_order", "maintain_protective_stop"]
    assert payload["preflight_statuses"] == ["preflight_ready"]
    assert payload["preflight_error"] == ""
    assert payload["preflight"][0]["target"] == "entry_order"
    assert payload["preflight"][0]["side"] == "SELL"
    assert payload["preflight"][0]["quantity"] == "0.031"
    assert payload["preflight"][0]["newClientOrderId"] == "entry_order:2026-05-01T01:05:00:small_probe:short"
