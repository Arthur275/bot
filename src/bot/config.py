from __future__ import annotations

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
    exchange_venue: str = "binance_usdt_perp"
    exchange_symbol: str = "ETHUSDT"
    exchange_api_base_url: str = "https://fapi.binance.com"
    adapter_client_order_prefix: str = "ethbot"
    exchange_api_key_env: str = "BINANCE_API_KEY"
    exchange_api_secret_env: str = "BINANCE_API_SECRET"
    recv_window_ms: int = Field(default=5000, gt=0)
    timeframe: str = "15m"
    risk_check_timeframe: str = "5m"
    runtime_mode: RuntimeMode = RuntimeMode.SHADOW
    engine_mode: EngineMode = EngineMode.STRICT_LIVE
    engine_src_path: Path = Path("D:/quant_system_rebuild/src")
    proxy_url: str | None = None
    include_okx_overlay: bool = True
    include_coinglass_overlay: bool = False
    timeout_sec: float = Field(default=15.0, gt=0.0)
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
        if self.timeframe != "15m":
            raise ValueError("BotConfig 只支持 timeframe='15m'")
        if self.risk_check_timeframe != "5m":
            raise ValueError("BotConfig 只支持 risk_check_timeframe='5m'")
        if self.runtime_mode == RuntimeMode.REAL and self.engine_mode != EngineMode.STRICT_LIVE:
            raise ValueError("真实执行只允许 engine_mode='strict-live'")
        return self
