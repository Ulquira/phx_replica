"""Compatibility entrypoint for work orders sync (delegates to phx_sync.sync.work_orders)."""

from phx_sync.config import get_settings
from phx_sync.db import get_azure_connection, get_mysql_connection, normalize_name, quote_ident, safe_name
from phx_sync.sync.work_orders import (
    FINGERPRINT_KEY_COLS,
    FINGERPRINT_TEXT_COLS,
    compute_row_fingerprint,
    detect_state_column,
    ensure_control_table,
    ensure_mysql_table,
    fetch_source_rows,
    get_source_columns,
    get_target_columns,
    load_existing_rows,
    log_run_summary,
    map_sql_type,
    normalize_val_for_fp,
    pick_date_column,
    record_run,
    resolve_target_column,
    sync_once,
    upsert_rows_batch,
    write_summary_csv,
)

MYSQL_TABLE = get_settings().mysql_table

__all__ = [
    "FINGERPRINT_KEY_COLS",
    "FINGERPRINT_TEXT_COLS",
    "MYSQL_TABLE",
    "compute_row_fingerprint",
    "detect_state_column",
    "ensure_control_table",
    "ensure_mysql_table",
    "fetch_source_rows",
    "get_azure_connection",
    "get_mysql_connection",
    "get_source_columns",
    "get_target_columns",
    "load_existing_rows",
    "log_run_summary",
    "map_sql_type",
    "normalize_name",
    "normalize_val_for_fp",
    "pick_date_column",
    "quote_ident",
    "record_run",
    "resolve_target_column",
    "safe_name",
    "sync_once",
    "upsert_rows_batch",
    "write_summary_csv",
]

if __name__ == "__main__":
    sync_once()
