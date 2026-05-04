from __future__ import annotations

from pathlib import Path
from typing import Any

DEFAULT_BOT_RUNTIME_DB_PATH = Path("runtime/analysis/bot_runtime.duckdb")

SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS bot_cycles (
        run_id VARCHAR PRIMARY KEY,
        source_path VARCHAR,
        source_line INTEGER,
        event_type VARCHAR,
        generated_at TIMESTAMP,
        runtime_mode VARCHAR,
        engine_mode VARCHAR,
        symbol VARCHAR,
        exchange_symbol VARCHAR,
        quant_action VARCHAR,
        bot_effective_action VARCHAR,
        execution_layer_state VARCHAR,
        automation_state VARCHAR,
        blocked BOOLEAN,
        degraded BOOLEAN,
        reason_codes_json VARCHAR,
        audit_log_path VARCHAR,
        state_path VARCHAR
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS bot_command_samples (
        run_id VARCHAR,
        command_index INTEGER,
        generated_at TIMESTAMP,
        target VARCHAR,
        operation VARCHAR,
        status VARCHAR,
        accepted BOOLEAN,
        simulated BOOLEAN,
        reason VARCHAR,
        idempotency_key VARCHAR,
        client_order_id VARCHAR,
        exchange_order_id VARCHAR,
        error_kind VARCHAR,
        command_type VARCHAR,
        runtime_mode VARCHAR,
        PRIMARY KEY (run_id, command_index)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS bot_runtime_summaries (
        run_id VARCHAR PRIMARY KEY,
        generated_at TIMESTAMP,
        position_state_before VARCHAR,
        position_direction_before VARCHAR,
        position_size_pct_before DOUBLE,
        protective_stop_present_before BOOLEAN,
        position_state_after VARCHAR,
        position_direction_after VARCHAR,
        position_size_pct_after DOUBLE,
        protective_stop_present_after BOOLEAN
    )
    """,
)

INDEX_STATEMENTS = (
    "CREATE INDEX IF NOT EXISTS idx_bot_cycles_generated_at ON bot_cycles(generated_at)",
    "CREATE INDEX IF NOT EXISTS idx_bot_cycles_action ON bot_cycles(quant_action, bot_effective_action)",
    "CREATE INDEX IF NOT EXISTS idx_bot_command_samples_target_status ON bot_command_samples(target, status)",
)


class BotDuckDBDependencyError(RuntimeError):
    pass


def get_default_db_path() -> Path:
    return DEFAULT_BOT_RUNTIME_DB_PATH


def require_duckdb() -> Any:
    try:
        import duckdb  # type: ignore
    except ModuleNotFoundError as exc:
        raise BotDuckDBDependencyError(
            "bot analysis 需要 duckdb，请先执行 pip install '.[analysis]' 或 pip install duckdb"
        ) from exc
    return duckdb


def connect_duckdb(db_path: str | Path | None = None) -> Any:
    duckdb = require_duckdb()
    normalized_db_path = Path(db_path) if db_path else get_default_db_path()
    normalized_db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = duckdb.connect(str(normalized_db_path))
    initialize_schema(conn)
    return conn


def initialize_schema(conn: Any) -> None:
    for statement in SCHEMA_STATEMENTS:
        conn.execute(statement)
    for statement in INDEX_STATEMENTS:
        conn.execute(statement)
