from __future__ import annotations

import sys
from pathlib import Path

from datetime import datetime, timezone

from bot.exchange_adapter import AdapterCapabilities, AdapterRuntimeSnapshot, CommandExecutionResult, PositionSnapshot

from scripts import run_shadow_preflight_cycle
from scripts.ops.shadow_preflight_diagnostics import summarize_handoff


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
    args.include_coinglass_overlay = None
    args.consensus_request_timeout_sec = 10.0
    args.research_sync_request_path = None
    args.research_dispatch_request_path = str(quant_root / "runtime" / "fresh_research" / "dispatch_request.json")
    args.api_key_env = "OKX_API_KEY"
    args.api_secret_env = "OKX_API_SECRET"
    args.api_passphrase_env = "OKX_API_PASSPHRASE"
    args.enable_real_orders = False
    return args


def _fresh_factor_lookup_generated_at() -> str:
    return datetime.now(timezone.utc).isoformat()


def test_bot_config_auto_enables_coinglass_when_api_key_is_available(monkeypatch) -> None:
    from bot.config import BotConfig

    monkeypatch.setenv("COINGLASS_API_KEY", "x" * 32)

    assert BotConfig().resolved_include_coinglass_overlay is True
    assert BotConfig(include_coinglass_overlay=False).resolved_include_coinglass_overlay is False


def test_summarize_handoff_preserves_trigger_ready_probe_hard_fault_fields() -> None:
    summary = summarize_handoff(
        {
            "action": "small_probe",
            "probe_source": "trigger_ready_small_probe",
            "factor_lookup_version": "lookup-v1",
            "factor_lookup_generated_at": "2026-05-18T08:00:00Z",
            "factor_lookup_stale": True,
            "factor_lookup_age_seconds": 14400.0,
            "scoring_chain_frozen": True,
            "snapshot_refs": {"eth_orderbook": "runtime/orderbook.json"},
        }
    )

    assert summary["probe_source"] == "trigger_ready_small_probe"
    assert summary["factor_lookup_version"] == "lookup-v1"
    assert summary["factor_lookup_generated_at"] == "2026-05-18T08:00:00Z"
    assert summary["factor_lookup_stale"] is True
    assert summary["factor_lookup_age_seconds"] == 14400.0
    assert summary["scoring_chain_frozen"] is True


def _clear_fake_quant_modules() -> None:
    for module_name in list(sys.modules):
        if (
            module_name == "contracts"
            or module_name.startswith("contracts.")
            or module_name == "interfaces"
            or module_name.startswith("interfaces.")
        ):
            sys.modules.pop(module_name, None)


class FakeOkxUsdtSwapAdapter:
    def __init__(self, credentials) -> None:
        self.credentials = credentials

    def get_capabilities(self):
        return AdapterCapabilities(supports_real_execution=True, supports_take_profit_orders=True)

    def fetch_runtime_snapshot(self):
        return AdapterRuntimeSnapshot(
            fetched_at=datetime(2026, 5, 1, 1, 5, 0),
            snapshot_valid=True,
            account_equity=50.0,
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
        results = []
        for command in commands:
            body = {
                "side": "sell",
                "ordType": "market",
                "sz": "0.015",
                "clOrdId": "entry_order:2026-05-01T01:05:00:small_probe:short",
                "resolution_mode": "okx_entry_contracts_from_size_pct",
                "resolved_account_equity": "50.0",
                "resolved_leverage": 10,
                "resolved_mark_price": "3150.0",
            }
            if command.target == "maintain_protective_stop":
                body.update({"ordType": "conditional", "algoClOrdId": "stop:key", "triggerPx": "3206.7"})
            elif command.target == "take_profit_order":
                body.update({"ordType": "limit", "clOrdId": f"tp:{command.idempotency_key}", "px": "3118.5"})
            results.append(
                CommandExecutionResult(
                    target=command.target,
                    status="preflight_ready",
                    accepted=True,
                    simulated=True,
                    reason=command.reason,
                    details={
                        "prepared_request": {
                            "method": "POST",
                            "path": "/api/v5/trade/order",
                            "params": {},
                            "body": body,
                        },
                        "signed_request": {
                            "body": {"sz": "0.015"},
                        },
                    },
                )
            )
        return results


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
        "'sizing_tier': 'none',"
        "'sizing_bias': 'none',"
        "'initial_stop_loss': 0.9809,"
        "'stop_distance_pct': 0.0191,"
        "'tp_ladder': [],"
        "'reduce_conditions': [],"
        "'invalidate_conditions': [],"
        "'trailing_rule': '',"
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

    monkeypatch.setattr(run_shadow_preflight_cycle, "_load_real_adapter", lambda venue: FakeOkxUsdtSwapAdapter)
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
    assert payload["handoff"]["sizing_tier"] == "none"
    assert payload["handoff"]["sizing_bias"] == "none"
    assert payload["handoff"]["tp_ladder"] == []
    assert payload["handoff"]["reduce_conditions"] == []
    assert payload["handoff"]["invalidate_conditions"] == []
    assert payload["handoff"]["trailing_rule"] == ""
    assert payload["handoff"]["execution_layer_reasoning"] == "waiting_for_trigger"
    assert payload["handoff"]["trigger_ready"] is False
    assert payload["handoff"]["breakout_support"] is False
    assert payload["handoff"]["retest_support"] is False
    assert payload["handoff"]["slope_support"] == 0.508
    assert payload["handoff"]["snapshot_ref_keys"] == ["eth_orderbook"]
    assert payload["handoff"]["has_orderbook_snapshot"] is True


def test_shadow_preflight_script_runs_preflight_for_entry_commands(monkeypatch, tmp_path: Path) -> None:
    quant_root = tmp_path / "fake_quant_entry"
    factor_lookup_generated_at = _fresh_factor_lookup_generated_at()
    _write_fake_quant_modules(
        quant_root / "src",
        handoff_payload="{"
        "'generated_at': '2026-05-01T01:05:00',"
        "'action': 'small_probe',"
        "'direction': 'short',"
        "'execution_allowed': True,"
        "'risk_filter_status': 'pass',"
        f"'factor_lookup_generated_at': '{factor_lookup_generated_at}',"
        "'factor_lookup_stale': False,"
        "'position_size_pct': 0.1,"
        "'executable_size_pct': 0.1,"
        "'sizing_tier': 'probe',"
        "'sizing_bias': 'conservative',"
        "'max_account_risk_pct_per_trade': 0.01,"
        "'initial_stop_loss': 1.018,"
        "'stop_distance_pct': 0.018,"
        "'tp_ladder': [0.99, 0.98],"
        "'tp_reduce_fractions': [0.6, 0.4],"
        "'reduce_conditions': ['crowding_warning'],"
        "'invalidate_conditions': ['setup_invalidated'],"
        "'trailing_rule': 'trail_with_trigger'"
        "}",
    )
    calls: list[list[str]] = []

    class RecordingFakeOkxUsdtSwapAdapter(FakeOkxUsdtSwapAdapter):
        def preflight_commands(self, *, commands):
            calls.append([command.target for command in commands])
            return super().preflight_commands(commands=commands)

    monkeypatch.setattr(run_shadow_preflight_cycle, "_load_real_adapter", lambda venue: RecordingFakeOkxUsdtSwapAdapter)

    _clear_fake_quant_modules()
    payload = run_shadow_preflight_cycle.run_cycle(
        args=_args(quant_root=quant_root, output_root=tmp_path / "out_entry"),
        bot_root=Path(__file__).resolve().parents[1],
    )

    assert payload["requested_action"] == "small_probe"
    assert payload["effective_action"] == "small_probe"
    assert payload["execution_plan"]["executable_size_pct"] == 0.1
    assert payload["handoff"]["sizing_tier"] == "probe"
    assert payload["handoff"]["sizing_bias"] == "conservative"
    assert payload["handoff"]["tp_ladder"] == [0.99, 0.98]
    assert payload["handoff"]["tp_reduce_fractions"] == [0.6, 0.4]
    assert payload["handoff"]["reduce_conditions"] == ["crowding_warning"]
    assert payload["handoff"]["invalidate_conditions"] == ["setup_invalidated"]
    assert payload["handoff"]["trailing_rule"] == "trail_with_trigger"
    assert "fixed_margin_budget_sizing" in payload["execution_plan"]["notes"]
    assert calls == [["entry_order", "maintain_protective_stop", "take_profit_order", "take_profit_order"]]
    assert payload["command_targets"] == ["entry_order", "maintain_protective_stop", "take_profit_order", "take_profit_order"]
    assert payload["preflight_statuses"] == ["preflight_ready", "preflight_ready", "preflight_ready", "preflight_ready"]
    assert payload["preflight_error"] == ""
    assert payload["preflight"][0]["target"] == "entry_order"
    assert payload["preflight"][0]["side"] == "sell"
    assert payload["preflight"][0]["quantity"] == "0.015"
    assert payload["preflight"][0]["newClientOrderId"] == "entry_order:2026-05-01T01:05:00:small_probe:short"
    assert payload["preflight"][2]["target"] == "take_profit_order"


def test_shadow_preflight_real_order_intent_emits_real_payload_after_simulated_validation(monkeypatch, tmp_path: Path) -> None:
    quant_root = tmp_path / "fake_quant_real_order_intent"
    factor_lookup_generated_at = _fresh_factor_lookup_generated_at()
    _write_fake_quant_modules(
        quant_root / "src",
        handoff_payload="{"
        "'generated_at': '2026-05-01T01:05:00',"
        "'action': 'small_probe',"
        "'direction': 'short',"
        "'execution_allowed': True,"
        "'risk_filter_status': 'pass',"
        f"'factor_lookup_generated_at': '{factor_lookup_generated_at}',"
        "'factor_lookup_stale': False,"
        "'position_size_pct': 0.1,"
        "'executable_size_pct': 0.1,"
        "'sizing_tier': 'probe',"
        "'sizing_bias': 'conservative',"
        "'max_account_risk_pct_per_trade': 0.01,"
        "'initial_stop_loss': 1.018,"
        "'stop_distance_pct': 0.018,"
        "'tp_ladder': [],"
        "'reduce_conditions': ['crowding_warning'],"
        "'invalidate_conditions': ['setup_invalidated'],"
        "'trailing_rule': 'trail_with_trigger'"
        "}",
    )
    runtime_modes = []

    class RecordingFakeOkxUsdtSwapAdapter(FakeOkxUsdtSwapAdapter):
        def execute_commands(self, *, commands, runtime_mode):
            runtime_modes.append(runtime_mode.value)
            return super().execute_commands(commands=commands, runtime_mode=runtime_mode)

    args = _args(quant_root=quant_root, output_root=tmp_path / "out_real_order_intent")
    args.enable_real_orders = True
    monkeypatch.setattr(run_shadow_preflight_cycle, "_load_real_adapter", lambda venue: RecordingFakeOkxUsdtSwapAdapter)

    _clear_fake_quant_modules()
    payload = run_shadow_preflight_cycle.run_cycle(
        args=args,
        bot_root=Path(__file__).resolve().parents[1],
    )

    assert runtime_modes == ["simulated-real"]
    assert payload["runtime_mode"] == "real"
    assert payload["planning_runtime_mode"] == "simulated-real"
    assert payload["real_order_submission_intent"] is True
    assert payload["engine_mode"] == "strict-live"
    assert payload["command_targets"] == ["entry_order", "maintain_protective_stop"]
    assert payload["preflight_statuses"] == ["preflight_ready", "preflight_ready"]


def test_shadow_preflight_script_blocks_entry_when_risk_filter_status_missing(monkeypatch, tmp_path: Path) -> None:
    quant_root = tmp_path / "fake_quant_entry_missing_risk"
    _write_fake_quant_modules(
        quant_root / "src",
        handoff_payload="{"
        "'generated_at': '2026-05-01T01:05:00',"
        "'action': 'small_probe',"
        "'direction': 'short',"
        "'execution_allowed': True,"
        "'position_size_pct': 0.2,"
        "'executable_size_pct': 0.02,"
        "'sizing_tier': 'probe',"
        "'sizing_bias': 'conservative',"
        "'max_account_risk_pct_per_trade': 0.01,"
        "'initial_stop_loss': 1.018,"
        "'stop_distance_pct': 0.018,"
        "'tp_ladder': [0.99, 0.98],"
        "'reduce_conditions': ['crowding_warning'],"
        "'invalidate_conditions': ['setup_invalidated'],"
        "'trailing_rule': 'trail_with_trigger'"
        "}",
    )
    calls: list[list[str]] = []

    class RecordingFakeOkxUsdtSwapAdapter(FakeOkxUsdtSwapAdapter):
        def preflight_commands(self, *, commands):
            calls.append([command.target for command in commands])
            return super().preflight_commands(commands=commands)

    monkeypatch.setattr(run_shadow_preflight_cycle, "_load_real_adapter", lambda venue: RecordingFakeOkxUsdtSwapAdapter)

    _clear_fake_quant_modules()
    payload = run_shadow_preflight_cycle.run_cycle(
        args=_args(quant_root=quant_root, output_root=tmp_path / "out_missing_risk"),
        bot_root=Path(__file__).resolve().parents[1],
    )

    assert payload["requested_action"] == "small_probe"
    assert payload["effective_action"] == "wait"
    assert payload["execution_plan"]["effective_action"] == "wait"
    assert payload["execution_plan"]["plan_reason"] == "entry_disallowed_by_guard"
    assert "risk_filter:unknown" in payload["execution_plan"]["notes"]
    assert payload["command_targets"] == []
    assert payload["preflight_statuses"] == []
    assert payload["preflight"] == []
    assert payload["preflight_error"] == ""
    assert calls == []


def test_shadow_preflight_records_missing_okx_passphrase_diagnostic(monkeypatch, tmp_path: Path) -> None:
    quant_root = tmp_path / "fake_quant_missing_passphrase"
    _write_fake_quant_modules(
        quant_root / "src",
        handoff_payload="{"
        "'generated_at': '2026-05-01T01:05:00',"
        "'action': 'wait',"
        "'direction': 'neutral',"
        "'execution_allowed': False"
        "}",
    )
    monkeypatch.delenv("OKX_API_KEY", raising=False)
    monkeypatch.delenv("OKX_API_SECRET", raising=False)
    monkeypatch.delenv("OKX_API_PASSPHRASE", raising=False)
    monkeypatch.setattr(run_shadow_preflight_cycle, "_load_real_adapter", lambda venue: FakeOkxUsdtSwapAdapter)

    _clear_fake_quant_modules()
    payload = run_shadow_preflight_cycle.run_cycle(
        args=_args(quant_root=quant_root, output_root=tmp_path / "out_missing_passphrase"),
        bot_root=Path(__file__).resolve().parents[1],
    )

    assert "missing_api_env:OKX_API_PASSPHRASE" in payload["preflight_diagnostics"]
