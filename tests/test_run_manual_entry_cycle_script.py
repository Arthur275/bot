from argparse import Namespace

from scripts.run_manual_entry_cycle import _build_risk_preview, render_confirmation_panel


def test_manual_entry_panel_renders_human_preview_with_risk_and_final_confirm_command() -> None:
    args = Namespace(
        quant_root="D:/dev/quant",
        output_root="C:/Users/test/.codex/memories/manual",
        proxy_url="http://127.0.0.1:7897",
        include_okx_overlay=True,
        include_coinglass_overlay=False,
        api_key_env="OKX_TRADE_API_KEY",
        api_secret_env="OKX_TRADE_API_SECRET",
        api_passphrase_env="OKX_TRADE_PASSPHRASE",
    )
    payload = {
        "mode": "preview",
        "runtime_mode": "real",
        "effective_action": "small_probe",
        "plan_reason": "quant_action_passthrough",
        "runtime_snapshot": {
            "snapshot_valid": True,
            "account_equity": 11.18,
            "protective_stop_present": False,
            "position": {
                "position_state": "FLAT",
                "direction": "neutral",
                "size_pct": 0.0,
                "leverage": 10,
            },
        },
        "risk_preview": {
            "mark_price": 2300.0,
            "estimated_stop_price": 2264.35,
            "notional_usd": 98.9,
            "estimated_loss_usd": 1.53295,
            "loss_equity_ratio": 0.137115,
            "stop_distance_pct": 0.0155,
        },
        "manual_entry_confirmation": {
            "required": True,
            "expected_token": "ENTRY-ABC123",
            "provided": False,
            "matched": False,
        },
        "preflight_error": "",
        "preflight": [
            {
                "target": "entry_order",
                "status": "preflight_ready",
                "side": "buy",
                "type": "market",
                "quantity": "0.043",
            },
            {
                "target": "maintain_protective_stop",
                "status": "error",
                "error": "Real protective stop requires an existing entered position",
            },
        ],
        "audit_log_path": "C:/audit.jsonl",
        "state_path": "C:/state.json",
        "confirm_command": "python scripts\\run_manual_entry_cycle.py `\n  --confirm-token ENTRY-ABC123",
    }

    panel = render_confirmation_panel(payload, args=args)

    assert panel.startswith("[BLOCKED: MANUAL CONFIRMATION REQUIRED]")
    assert "Runtime Snapshot" in panel
    assert "Equity: $11.18" in panel
    assert "Quantity: 0.043 ETH" in panel
    assert "Estimated stop price: $2264.35" in panel
    assert "Estimated loss at stop: $1.53" in panel
    assert "Loss / equity: 13.71%" in panel
    assert "CONFIRM COMMAND" in panel
    assert panel.endswith("=" * 72)


def test_manual_entry_risk_preview_calculates_amounts_from_preflight_fields() -> None:
    risk = _build_risk_preview(
        entry_preflight={
            "quantity": "0.043",
            "resolved_mark_price": "2300",
            "resolved_account_equity": "11.18",
        },
        execution_plan={"stop_distance_pct": 0.0155},
        handoff={"direction": "long"},
        runtime_snapshot={},
    )

    assert risk["notional_usd"] == 98.9
    assert risk["estimated_loss_usd"] == 1.53295
    assert risk["estimated_stop_price"] == 2264.35
    assert round(risk["loss_equity_ratio"], 6) == 0.137115


def test_manual_entry_panel_suppresses_confirm_command_when_entry_preflight_not_ready() -> None:
    args = Namespace(
        quant_root="D:/dev/quant",
        output_root="C:/Users/test/.codex/memories/manual",
        proxy_url="http://127.0.0.1:7897",
        include_okx_overlay=True,
        include_coinglass_overlay=False,
        api_key_env="OKX_TRADE_API_KEY",
        api_secret_env="OKX_TRADE_API_SECRET",
        api_passphrase_env="OKX_TRADE_PASSPHRASE",
    )
    payload = {
        "mode": "preview",
        "runtime_mode": "real",
        "effective_action": "small_probe",
        "plan_reason": "quant_action_passthrough",
        "runtime_snapshot": {
            "snapshot_valid": True,
            "account_equity": None,
            "error_endpoint": "/api/v5/account/balance",
            "error_kind": "http_error",
            "error_message": "HTTP 400",
            "position": {"position_state": "FLAT", "direction": "neutral", "size_pct": 0.0, "leverage": 10},
        },
        "risk_preview": {"stop_distance_pct": 0.0155},
        "manual_entry_confirmation": {"required": True, "expected_token": "ENTRY-ABC123", "provided": False, "matched": False},
        "preflight_error": "",
        "preflight": [{"target": "entry_order", "status": "error", "error": "below minQty"}],
        "audit_log_path": "C:/audit.jsonl",
        "state_path": "C:/state.json",
        "confirm_command": "",
    }

    panel = render_confirmation_panel(payload, args=args)

    assert "CONFIRM COMMAND SUPPRESSED: PREFLIGHT NOT READY" in panel
    assert "--confirm-token" not in panel
