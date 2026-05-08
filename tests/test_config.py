from pathlib import Path

import pytest

from bot.config import BotConfig, EngineMode, RuntimeMode


def test_bot_config_enforces_eth_only_scope() -> None:
    with pytest.raises(ValueError, match="symbol='ETH'"):
        BotConfig(symbol="BTC")


def test_bot_config_rejects_real_mode_with_sample_fallback() -> None:
    with pytest.raises(ValueError, match="真实执行只允许"):
        BotConfig(runtime_mode=RuntimeMode.REAL, engine_mode=EngineMode.SAMPLE_FALLBACK)


def test_bot_config_accepts_shadow_strict_live_defaults(tmp_path: Path) -> None:
    config = BotConfig(
        audit_log_path=tmp_path / "audit.jsonl",
        state_store_path=tmp_path / "state.json",
        artifacts_root=tmp_path / "runtime",
    )
    assert config.symbol == "ETH"
    assert config.runtime_mode is RuntimeMode.SHADOW
    assert config.exchange_venue == "okx_usdt_swap"
    assert config.exchange_symbol == "ETH-USDT-SWAP"
    assert config.adapter_client_order_prefix == "ethbot"
    assert config.exchange_api_key_env == "OKX_TRADE_API_KEY"
    assert config.exchange_api_secret_env == "OKX_TRADE_API_SECRET"
    assert config.exchange_api_passphrase_env == "OKX_TRADE_PASSPHRASE"
    assert config.engine_src_path is None
    assert config.demo_small_account_mode is True
    assert config.entry_margin_budget_usdt == 10.0
    assert config.entry_margin_budget_max_equity_usdt == 50.0
    assert config.max_account_risk_pct_per_trade == 0.01
    assert config.max_probe_account_risk_pct == 0.002
    assert config.max_probe_size_pct == 0.02
    assert config.require_execution_allowed is True
    assert config.manual_entry_confirmation_required is True
    assert config.manual_entry_confirmation_token == ""


def test_bot_config_accepts_legacy_binance_scope(tmp_path: Path) -> None:
    config = BotConfig(
        audit_log_path=tmp_path / "audit.jsonl",
        state_store_path=tmp_path / "state.json",
        artifacts_root=tmp_path / "runtime",
        exchange_venue="binance_usdt_perp",
        exchange_symbol="ETHUSDT",
        exchange_api_base_url="https://fapi.binance.com",
        exchange_api_key_env="BINANCE_TRADE_API_KEY",
        exchange_api_secret_env="BINANCE_TRADE_API_SECRET",
    )

    assert config.exchange_venue == "binance_usdt_perp"
    assert config.exchange_symbol == "ETHUSDT"


def test_bot_config_rejects_mismatched_exchange_symbol() -> None:
    with pytest.raises(ValueError, match="ETH-USDT-SWAP"):
        BotConfig(exchange_symbol="ETHUSDT")

    with pytest.raises(ValueError, match="ETHUSDT"):
        BotConfig(exchange_venue="binance_usdt_perp", exchange_symbol="ETH-USDT-SWAP")
