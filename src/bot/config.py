from __future__ import annotations

import os
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RuntimeMode(str, Enum):
    SHADOW = "shadow"
    SIMULATED_REAL = "simulated-real"
    REAL = "real"


class EngineMode(str, Enum):
    STRICT_LIVE = "strict-live"
    SAMPLE_FALLBACK = "sample-fallback"


class BotConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    symbol: str = "ETH"
    leverage: int = 10
    instrument_type: str = "perpetual"
    exchange_venue: str = "okx_usdt_swap"
    exchange_symbol: str = "ETH-USDT-SWAP"
    exchange_api_base_url: str = "https://www.okx.com"
    adapter_client_order_prefix: str = "ethbot"
    exchange_api_key_env: str = "OKX_TRADE_API_KEY"
    exchange_api_secret_env: str = "OKX_TRADE_API_SECRET"
    exchange_api_passphrase_env: str = "OKX_TRADE_PASSPHRASE"
    recv_window_ms: int = Field(default=60000, gt=0)
    timeframe: str = "15m"
    risk_check_timeframe: str = "5m"
    demo_small_account_mode: bool = True
    # Demo-only 10U margin budget. Disable demo_small_account_mode before production sizing.
    entry_margin_budget_usdt: float | None = Field(default=10.0, gt=0.0)
    entry_margin_budget_max_equity_usdt: float | None = Field(default=50.0, gt=0.0)
    max_account_risk_pct_per_trade: float = Field(default=0.01, gt=0.0, le=0.05)
    max_probe_account_risk_pct: float = Field(default=0.002, gt=0.0, le=0.02)
    max_probe_size_pct: float = Field(default=0.02, gt=0.0, le=1.0)
    exchange_min_order_qty: float = Field(default=0.001, gt=0.0)
    exchange_qty_step_size: float = Field(default=0.001, gt=0.0)
    require_execution_allowed: bool = True
    manual_entry_confirmation_required: bool = True
    manual_entry_confirmation_token: str = ""
    runtime_mode: RuntimeMode = RuntimeMode.SHADOW
    engine_mode: EngineMode = EngineMode.STRICT_LIVE
    engine_src_path: Path | None = None
    proxy_url: str | None = None
    include_okx_overlay: bool = True
    include_coinglass_overlay: bool | None = None
    timeout_sec: float = Field(default=15.0, gt=0.0)
    consensus_mode: str = "auto"
    consensus_min_sources: int = Field(default=3, gt=0)
    consensus_request_timeout_sec: float = Field(default=10.0, gt=0.0)
    artifacts_root: Path = Path("runtime")
    audit_log_path: Path = Path("runtime/audit_log.jsonl")
    state_store_path: Path = Path("runtime/state_store.json")
    calibration_path: Path | None = None
    sample_root: Path | None = None
    research_sync_request_path: Path | None = None
    research_dispatch_request_path: Path | None = None

    @model_validator(mode="after")
    def validate_scope(self) -> "BotConfig":
        if self.symbol != "ETH":
            raise ValueError("BotConfig 只支持 symbol='ETH'")
        if self.leverage != 10:
            raise ValueError("BotConfig 只支持 leverage=10")
        if self.instrument_type != "perpetual":
            raise ValueError("BotConfig 只支持 instrument_type='perpetual'")
        if self.exchange_venue not in {"okx_usdt_swap", "binance_usdt_perp"}:
            raise ValueError("BotConfig only supports exchange_venue='okx_usdt_swap' or 'binance_usdt_perp'")
        if self.exchange_venue == "okx_usdt_swap" and self.exchange_symbol != "ETH-USDT-SWAP":
            raise ValueError("OKX execution only supports exchange_symbol='ETH-USDT-SWAP'")
        if self.exchange_venue == "binance_usdt_perp" and self.exchange_symbol != "ETHUSDT":
            raise ValueError("Binance execution only supports exchange_symbol='ETHUSDT'")
        if self.timeframe != "15m":
            raise ValueError("BotConfig 只支持 timeframe='15m'")
        if self.risk_check_timeframe != "5m":
            raise ValueError("BotConfig 只支持 risk_check_timeframe='5m'")
        if self.runtime_mode == RuntimeMode.REAL and self.engine_mode != EngineMode.STRICT_LIVE:
            raise ValueError("真实执行只允许 engine_mode='strict-live'")
        return self

    @property
    def resolved_include_coinglass_overlay(self) -> bool:
        if self.include_coinglass_overlay is not None:
            return bool(self.include_coinglass_overlay)
        return bool(os.environ.get("COINGLASS_API_KEY", "").strip())
