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
    assert config.exchange_venue == "binance_usdt_perp"
    assert config.exchange_symbol == "ETHUSDT"
    assert config.adapter_client_order_prefix == "ethbot"
    assert config.recv_window_ms == 5000
