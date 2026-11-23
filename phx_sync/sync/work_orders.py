from __future__ import annotations

import csv
import hashlib
import logging
import zlib
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from phx_sync.config import Settings, get_settings
from phx_sync.db import (
    WORK_ORDERS_BASE_QUERY,
    get_azure_connection,
    get_mysql_connection,
    normalize_name,
    quote_ident,
    safe_name,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
LOG_PATH = PROJECT_ROOT / "sync.log"
SUMMARY_CSV_PATH = PROJECT_ROOT / "sync_summary.csv"

logger = logging.getLogger("azure_mysql_sync")
logger.setLevel(logging.INFO)
if not logger.handlers:
    _formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    _stream_handler = logging.StreamHandler()
    _stream_handler.setFormatter(_formatter)
    logger.addHandler(_stream_handler)
    try:
        _file_handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
        _file_handler.setFormatter(_formatter)
        logger.addHandler(_file_handler)
    except OSError:
        logger.warning("Cannot open %s for writing; logging to stdout only.", LOG_PATH)

FINGERPRINT_KEY_COLS: List[str] = [
    "Estado", "Estado OT", "Cuadrilla", "FechaUltiEsta", "F.Visita", "FechaIniVisi", "FechaFinVisi",
    "TeleMovilNume", "TeleFijoNume", "Georeferencia_tecnico", "Motivo", "Motivo Regestión",
    "Motivo Cancelación", "Motivo Finalización", "Motivo Anulación", "Motivo Suspensión",
    "Usuario Responsable", "Usuario Asignó", "Direccion", "ClienteFinal", "Número Documento",
    "IdenServi", "Producto", "Sector Operativo", "Zona", "Region",
]
FINGERPRINT_TEXT_COLS: List[str] = [
    "Observaciones OT", "Observaciones Tareas", "Diagnóstico Técnico", "Datos Técnicos", "Historial Estados",
]


def _quote_tsql_ident(name: str) -> str:
    return "[" + name.replace("]", "]]") + "]"


def map_sql_type(sql_type: Optional[str], char_length: Any = None) -> str:
    t = (sql_type or "").lower()
    if "int" in t and "char" not in t and "varchar" not in t:
        return "BIGINT"
    if any(k in t for k in ("decimal", "numeric", "money", "smallmoney", "float", "real")):
        return "DOUBLE"
    if "bit" in t:
        return "TINYINT"
    if any(k in t for k in ("datetime", "date", "time")):
        return "DATETIME"
    if any(k in t for k in ("image", "varbinary", "binary")):
        return "LONGBLOB"

    if char_length is not None:
        try:
            length = int(char_length)
            if length == -1 or length > 16000:
                return "LONGTEXT"
            return f"VARCHAR({length})"
        except (TypeError, ValueError):
            pass

    return "VARCHAR(255)"


def get_source_columns(conn: Any) -> List[Dict[str, Any]]:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT COLUMN_NAME, DATA_TYPE, CHARACTER_MAXIMUM_LENGTH
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = 'dbo' AND TABLE_NAME = 'VW_WinOrdeTraba'
            ORDER BY ORDINAL_POSITION
            """
        )
        columns = [
            {"name": row[0], "type": row[1], "length": row[2]}
            for row in cursor.fetchall()
        ]
    columns.append({"name": "Georeferencia_tecnico", "type": "varchar", "length": 255})
    return columns


def pick_date_column(columns: Sequence[Dict[str, Any]]) -> Optional[str]:
    for col in columns:
        name = str(col["name"]).lower()
        if any(token in name for token in ("fecha", "date", "created", "updated")):
            return str(col["name"])
    return None


def _rows_from_cursor(cursor: Any) -> List[Dict[str, Any]]:
    query_columns = [col[0] for col in cursor.description]
    rows: List[Dict[str, Any]] = []
    for raw in cursor.fetchall():
        row: Dict[str, Any] = {}
        for idx, col_name in enumerate(query_columns):
            value = raw[idx]
            if isinstance(value, datetime):
                row[col_name] = value.strftime("%Y-%m-%d %H:%M:%S")
            else:
                row[col_name] = value
        rows.append(row)
    return rows


def fetch_source_rows(conn: Any, columns: Sequence[Dict[str, Any]], lookback_days: int = 1) -> List[Dict[str, Any]]:
    days = int(lookback_days)
    if days < 0:
        raise ValueError(f"lookback_days must be >= 0, got {days}")

    date_column = pick_date_column(columns)
    with conn.cursor() as cursor:
        if date_column:
            quoted_col = _quote_tsql_ident(date_column)
            query = f"{WORK_ORDERS_BASE_QUERY} WHERE t.{quoted_col} >= DATEADD(day, -?, CAST(GETDATE() AS date))"
            cursor.execute(query, (days,))
        else:
            cursor.execute(WORK_ORDERS_BASE_QUERY)
        return _rows_from_cursor(cursor)


def validate_iso_date(date_str: str) -> str:
    try:
        parsed = datetime.strptime(date_str.strip(), "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError(f"Invalid start_date '{date_str}'. Expected YYYY-MM-DD format.") from exc
    return parsed.strftime("%Y-%m-%d")


def fetch_backfill_rows(
    conn: Any,
    columns: Sequence[Dict[str, Any]],
    start_date: str = "2026-09-11",
    end_date: Optional[str] = None,
) -> List[Dict[str, Any]]:
    normalized_start = validate_iso_date(start_date)
    normalized_end = validate_iso_date(end_date) if end_date else None
    date_column = pick_date_column(columns)

    with conn.cursor() as cursor:
        if date_column:
            quoted_col = _quote_tsql_ident(date_column)
            if normalized_end:
                logger.info("Querying Azure SQL for dates >= %s and < %s...", normalized_start, normalized_end)
                query = f"{WORK_ORDERS_BASE_QUERY} WHERE t.{quoted_col} >= ? AND t.{quoted_col} < ?"
                cursor.execute(query, (normalized_start, normalized_end))
            else:
                logger.info("Querying Azure SQL for dates >= %s...", normalized_start)
                query = f"{WORK_ORDERS_BASE_QUERY} WHERE t.{quoted_col} >= ?"
                cursor.execute(query, (normalized_start,))
        else:
            logger.info("Querying Azure SQL without date filter...")
            cursor.execute(WORK_ORDERS_BASE_QUERY)
        return _rows_from_cursor(cursor)


def get_target_columns(mysql_conn: Any, table_name: str, database: Optional[str] = None) -> List[str]:
    db_name = database or get_settings().mysql_database
    with mysql_conn.cursor() as cursor:
        cursor.execute(
            "SELECT COLUMN_NAME FROM information_schema.columns "
            "WHERE table_schema=%s AND table_name=%s ORDER BY ORDINAL_POSITION",
            (db_name, table_name),
        )
        return [row[0] for row in cursor.fetchall()]


def ensure_mysql_table(mysql_conn: Any, columns: Sequence[Dict[str, Any]], table_name_raw: Optional[str] = None) -> str:
    table_name = safe_name(table_name_raw or get_settings().mysql_table)
    with mysql_conn.cursor() as cursor:
        cursor.execute("SHOW TABLES LIKE %s", (table_name,))
        exists = cursor.fetchone() is not None
        if not exists:
            column_defs: List[str] = []
            has_orden_id = False
            for col in columns:
                if str(col["name"]).lower() == "ordenid":
                    has_orden_id = True
                    column_defs.append(
                        f"{quote_ident(col['name'])} {map_sql_type(col['type'], col.get('length'))} PRIMARY KEY"
                    )
                else:
                    column_defs.append(
                        f"{quote_ident(col['name'])} {map_sql_type(col['type'], col.get('length'))}"
                    )

            if not has_orden_id:
                column_defs.insert(0, f"{quote_ident('OrdenId')} INT NOT NULL PRIMARY KEY")

            column_defs.append(f"{quote_ident('token')} VARCHAR(255)")
            column_defs.append(f"{quote_ident('link')} VARCHAR(255)")

            create_sql = f"CREATE TABLE {quote_ident(table_name)} ({', '.join(column_defs)})"
            cursor.execute(create_sql)
        else:
            for col_name, col_type in (
                ("token", "VARCHAR(255)"),
                ("link", "VARCHAR(255)"),
                ("Georeferencia_tecnico", "TEXT"),
            ):
                cursor.execute(f"SHOW COLUMNS FROM {quote_ident(table_name)} LIKE %s", (col_name,))
                if not cursor.fetchone():
                    cursor.execute(f"ALTER TABLE {quote_ident(table_name)} ADD COLUMN {quote_ident(col_name)} {col_type}")

            estado_col = next((c["name"] for c in columns if str(c["name"]).lower() == "estado"), None)
            if estado_col:
                cursor.execute(f"SHOW INDEX FROM {quote_ident(table_name)} WHERE Key_name = 'idx_estado'")
                if not cursor.fetchone():
                    cursor.execute(
                        f"ALTER TABLE {quote_ident(table_name)} ADD INDEX idx_estado ({quote_ident(estado_col)}(255))"
                    )

            fecha_col = pick_date_column(columns)
            if fecha_col:
                cursor.execute(f"SHOW INDEX FROM {quote_ident(table_name)} WHERE Key_name = 'idx_fecha'")
                if not cursor.fetchone():
                    cursor.execute(
                        f"ALTER TABLE {quote_ident(table_name)} ADD INDEX idx_fecha ({quote_ident(fecha_col)})"
                    )
    return table_name


def normalize_val_for_fp(val: Any) -> str:
    if val is None:
        return ""
    if isinstance(val, datetime):
        return val.strftime("%Y-%m-%d %H:%M:%S")
    s = str(val).strip()
    if len(s) >= 19 and s[10] == "T":
        s = s.replace("T", " ")
    return s


def compute_row_fingerprint(row_dict: Dict[str, Any]) -> str:
    """Compute an MD5 fingerprint across tracked dynamic columns."""
    parts: List[str] = []
    for col in FINGERPRINT_KEY_COLS:
        parts.append(normalize_val_for_fp(row_dict.get(col)))
    for col in FINGERPRINT_TEXT_COLS:
        val = row_dict.get(col)
        if val is None or val == "":
            parts.append("0")
        elif isinstance(val, int):
            parts.append(str(val))
        else:
            parts.append(str(zlib.crc32(str(val).encode("utf-8"))))
    return hashlib.md5("|".join(parts).encode("utf-8")).hexdigest()


def load_existing_rows(
    mysql_conn: Any,
    table_name: str,
    target_ids: Sequence[Any],
    state_column: Optional[str] = None,
) -> Dict[str, Dict[str, Any]]:
    del state_column  # Kept for backwards-compatible signature
    if not target_ids:
        return {}
    results: Dict[str, Dict[str, Any]] = {}
    chunk_size = 1000
    id_list = [i for i in target_ids if i is not None]

    select_parts = [quote_ident("OrdenId")]
    for col in FINGERPRINT_KEY_COLS:
        select_parts.append(quote_ident(col))
    for idx, col in enumerate(FINGERPRINT_TEXT_COLS):
        select_parts.append(f"CRC32(COALESCE({quote_ident(col)}, '')) AS {quote_ident(f'_crc_{idx}')}")

    col_str = ", ".join(select_parts)

    with mysql_conn.cursor(dictionary=True) as cursor:
        for i in range(0, len(id_list), chunk_size):
            chunk = id_list[i : i + chunk_size]
            placeholders = ", ".join(["%s"] * len(chunk))
            cursor.execute(
                f"SELECT {col_str} FROM {quote_ident(table_name)} WHERE OrdenId IN ({placeholders})",
                chunk,
            )
            for row in cursor.fetchall():
                row_for_fp = {col: row.get(col) for col in FINGERPRINT_KEY_COLS}
                for idx, col in enumerate(FINGERPRINT_TEXT_COLS):
                    row_for_fp[col] = row.get(f"_crc_{idx}")
                results[str(row["OrdenId"])] = {
                    "fingerprint": compute_row_fingerprint(row_for_fp),
                    "raw": row,
                }
    return results


def ensure_control_table(mysql_conn: Any) -> None:
    with mysql_conn.cursor() as cursor:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS sync_runs (
                run_id BIGINT AUTO_INCREMENT PRIMARY KEY,
                started_at DATETIME NOT NULL,
                finished_at DATETIME NULL,
                status VARCHAR(20) NOT NULL,
                rows_processed INT DEFAULT 0,
                inserted INT DEFAULT 0,
                updated INT DEFAULT 0,
                skipped INT DEFAULT 0,
                message LONGTEXT NULL,
                error LONGTEXT NULL
            )
            """
        )
        cursor.execute("SHOW INDEX FROM sync_runs WHERE Key_name = 'idx_started_at'")
        if not cursor.fetchone():
            cursor.execute("ALTER TABLE sync_runs ADD INDEX idx_started_at (started_at)")

        cursor.execute("SHOW INDEX FROM sync_runs WHERE Key_name = 'idx_status'")
        if not cursor.fetchone():
            cursor.execute("ALTER TABLE sync_runs ADD INDEX idx_status (status)")


def record_run(
    mysql_conn: Any,
    started_at: datetime,
    finished_at: datetime,
    status: str,
    rows_processed: int,
    inserted: int,
    updated: int,
    skipped: int,
    message: Optional[str],
    error: Optional[str],
) -> Optional[int]:
    with mysql_conn.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO sync_runs (started_at, finished_at, status, rows_processed, inserted, updated, skipped, message, error)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (started_at, finished_at, status, rows_processed, inserted, updated, skipped, message, error),
        )
        return cursor.lastrowid


def log_run_summary(rows_processed: int, inserted: int, updated: int, skipped: int) -> None:
    total_changes = inserted + updated
    logger.info("========================================")
    logger.info("Sync summary: %s rows processed", rows_processed)
    logger.info("Inserted: %s | Updated: %s | Skipped: %s | Total changes: %s", inserted, updated, skipped, total_changes)
    if total_changes == 0 and rows_processed > 0:
        logger.info("No data changes detected in this run.")
    elif rows_processed == 0:
        logger.info("No rows found to process.")
    logger.info("========================================")


def write_summary_csv(
    run_id: Optional[int],
    started_at: datetime,
    finished_at: datetime,
    rows_processed: int,
    inserted: int,
    updated: int,
    skipped: int,
    status: str,
    csv_path: Path = SUMMARY_CSV_PATH,
) -> None:
    try:
        duration: Any = (finished_at - started_at).total_seconds()
    except Exception:
        duration = ""

    header = [
        "run_id", "started_at", "finished_at", "status",
        "rows_processed", "inserted", "updated", "skipped", "duration_seconds",
    ]
    row = [
        run_id or "",
        started_at.isoformat(sep=" "),
        finished_at.isoformat(sep=" "),
        status,
        rows_processed,
        inserted,
        updated,
        skipped,
        duration,
    ]

    write_header = not csv_path.exists()
    with open(csv_path, "a", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        if write_header:
            writer.writerow(header)
        writer.writerow(row)


def resolve_target_column(target_columns: Sequence[str], source_name: str) -> Optional[str]:
    if not target_columns:
        return None
    direct = next((col for col in target_columns if col.lower() == source_name.lower()), None)
    if direct is not None:
        return direct
    normalized = normalize_name(source_name)
    for col in target_columns:
        if normalize_name(col) == normalized:
            return col
    return None


def detect_state_column(columns: Sequence[Dict[str, Any]]) -> Optional[str]:
    for col in columns:
        if str(col["name"]).lower() == "estado":
            return str(col["name"])
    return None


def upsert_rows_batch(
    mysql_conn: Any,
    table_name: str,
    columns: Sequence[Dict[str, Any]],
    rows_to_upsert: Sequence[Dict[str, Any]],
    target_columns: Sequence[str],
) -> None:
    mapped_columns: List[Tuple[str, str]] = []
    for col in columns:
        col_name = str(col["name"])
        if col_name.lower() in {"link", "token"}:
            continue
        target_name = resolve_target_column(target_columns, col_name)
        if target_name:
            mapped_columns.append((col_name, target_name))

    if not mapped_columns or "ordenid" not in {t.lower() for _, t in mapped_columns}:
        return

    ordenid_mapping = next(m for m in mapped_columns if m[1].lower() == "ordenid")
    other_mappings = [m for m in mapped_columns if m[1].lower() != "ordenid"]
    final_mappings = [ordenid_mapping] + other_mappings
    target_names = [t for _, t in final_mappings]

    batch_data = [tuple(row.get(s) for s, _ in final_mappings) for row in rows_to_upsert]

    placeholders = ", ".join(["%s"] * len(target_names))
    update_clause = ", ".join(
        [
            f"{quote_ident(t)} = `new_row`.{quote_ident(t)}"
            if t.lower() != "georeferencia_tecnico"
            else f"{quote_ident(t)} = COALESCE(`new_row`.{quote_ident(t)}, {quote_ident(table_name)}.{quote_ident(t)})"
            for t in target_names
            if t.lower() != "ordenid"
        ]
    )
    insert_sql = (
        f"INSERT INTO {quote_ident(table_name)} ({', '.join(quote_ident(c) for c in target_names)}) "
        f"VALUES ({placeholders}) AS `new_row` ON DUPLICATE KEY UPDATE {update_clause}"
    )

    with mysql_conn.cursor() as cursor:
        chunk_size = 500
        for i in range(0, len(batch_data), chunk_size):
            cursor.executemany(insert_sql, batch_data[i : i + chunk_size])


def _diff_and_upsert(
    mysql_conn: Any,
    table_name: str,
    columns: Sequence[Dict[str, Any]],
    rows: Sequence[Dict[str, Any]],
    target_columns: Sequence[str],
) -> Tuple[int, int, int]:
    state_column = detect_state_column(columns)
    target_ids = [r.get("OrdenId") for r in rows if r.get("OrdenId") is not None]
    existing_rows = load_existing_rows(mysql_conn, table_name, target_ids, state_column)

    rows_to_upsert: List[Dict[str, Any]] = []
    inserted = 0
    updated = 0
    skipped = 0

    for row in rows:
        order_id = row.get("OrdenId")
        existing_info = existing_rows.get(str(order_id)) if order_id is not None else None

        if existing_info is None:
            rows_to_upsert.append(row)
            inserted += 1
        else:
            source_fp = compute_row_fingerprint(row)
            if existing_info.get("fingerprint") != source_fp:
                rows_to_upsert.append(row)
                updated += 1
            else:
                skipped += 1

    if rows_to_upsert:
        upsert_rows_batch(mysql_conn, table_name, columns, rows_to_upsert, target_columns)

    return inserted, updated, skipped


def sync_once(lookback_days: Optional[int] = None, settings: Optional[Settings] = None) -> None:
    cfg = settings or get_settings()
    days = cfg.sync_lookback_days if lookback_days is None else int(lookback_days)
    started_at = datetime.now()
    azure_conn = None
    mysql_conn = None
    status = "success"
    message = f"Sync completed (window: {days} day(s))"
    error: Optional[str] = None
    rows_processed = inserted = updated = skipped = 0

    try:
        azure_conn = get_azure_connection(cfg)
        mysql_conn = get_mysql_connection(cfg, autocommit=True)
        ensure_control_table(mysql_conn)

        logger.info("Starting sync (lookback window: %s day(s))", days)
        columns = get_source_columns(azure_conn)
        rows = fetch_source_rows(azure_conn, columns, lookback_days=days)
        rows_processed = len(rows)
        table_name = ensure_mysql_table(mysql_conn, columns, cfg.mysql_table)
        target_columns = get_target_columns(mysql_conn, table_name, cfg.mysql_database)

        inserted, updated, skipped = _diff_and_upsert(mysql_conn, table_name, columns, rows, target_columns)
        log_run_summary(rows_processed, inserted, updated, skipped)
    except Exception as exc:
        status = "error"
        error = str(exc)
        message = "Sync failed"
        logger.exception("Sync failed: %s", exc)
        raise
    finally:
        finished_at = datetime.now()
        run_id = None
        if mysql_conn is not None:
            try:
                run_id = record_run(
                    mysql_conn, started_at, finished_at, status,
                    rows_processed, inserted, updated, skipped, message, error,
                )
            except Exception as log_exc:
                logger.exception("Could not record sync run: %s", log_exc)
            mysql_conn.close()
        try:
            write_summary_csv(run_id, started_at, finished_at, rows_processed, inserted, updated, skipped, status)
        except Exception as csv_exc:
            logger.exception("Could not write summary CSV: %s", csv_exc)
        if azure_conn is not None:
            azure_conn.close()


def build_date_chunks(start_date: str, end_date: Optional[str] = None, chunk_days: int = 7) -> List[Tuple[str, Optional[str]]]:
    """Split a date range into bounded [start, end) chunks to avoid OOM on large backfills."""
    if chunk_days < 1:
        raise ValueError(f"chunk_days must be >= 1, got {chunk_days}")
    start_dt = datetime.strptime(validate_iso_date(start_date), "%Y-%m-%d")
    if end_date:
        end_dt = datetime.strptime(validate_iso_date(end_date), "%Y-%m-%d")
        if end_dt <= start_dt:
            raise ValueError(f"end_date ({end_date}) must be after start_date ({start_date})")
    else:
        end_dt = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)

    if (end_dt - start_dt).days <= chunk_days:
        return [(start_dt.strftime("%Y-%m-%d"), end_dt.strftime("%Y-%m-%d") if end_date else None)]

    chunks: List[Tuple[str, Optional[str]]] = []
    cursor_dt = start_dt
    while cursor_dt < end_dt:
        next_dt = min(cursor_dt + timedelta(days=chunk_days), end_dt)
        chunk_end = None if (not end_date and next_dt >= end_dt) else next_dt.strftime("%Y-%m-%d")
        chunks.append((cursor_dt.strftime("%Y-%m-%d"), chunk_end))
        cursor_dt = next_dt
    return chunks


def run_backfill(
    start_date: str = "2026-09-11",
    end_date: Optional[str] = None,
    chunk_days: int = 7,
    settings: Optional[Settings] = None,
) -> None:
    cfg = settings or get_settings()
    normalized_start = validate_iso_date(start_date)
    normalized_end = validate_iso_date(end_date) if end_date else None
    chunks = build_date_chunks(normalized_start, normalized_end, chunk_days=chunk_days)
    range_label = f"{normalized_start}..{normalized_end}" if normalized_end else f"from {normalized_start}"

    started_at = datetime.now()
    azure_conn = None
    mysql_conn = None
    status = "success"
    message = f"Retroactive backfill completed ({range_label})"
    error: Optional[str] = None
    rows_processed = inserted = updated = skipped = 0

    try:
        logger.info("=== STARTING RETROACTIVE BACKFILL (%s, %s chunk(s)) ===", range_label, len(chunks))
        azure_conn = get_azure_connection(cfg)
        mysql_conn = get_mysql_connection(cfg, autocommit=True)
        ensure_control_table(mysql_conn)

        columns = get_source_columns(azure_conn)
        table_name = ensure_mysql_table(mysql_conn, columns, cfg.mysql_table)
        target_columns = get_target_columns(mysql_conn, table_name, cfg.mysql_database)

        for idx, (c_start, c_end) in enumerate(chunks, 1):
            logger.info("Chunk %s/%s: %s -> %s", idx, len(chunks), c_start, c_end or "now")
            rows = fetch_backfill_rows(azure_conn, columns, c_start, c_end)
            rows_processed += len(rows)
            if rows:
                c_ins, c_upd, c_skip = _diff_and_upsert(mysql_conn, table_name, columns, rows, target_columns)
                inserted += c_ins
                updated += c_upd
                skipped += c_skip
                logger.info(
                    "Chunk %s/%s done: rows=%s, inserted=%s, updated=%s, skipped=%s",
                    idx, len(chunks), len(rows), c_ins, c_upd, c_skip,
                )

        log_run_summary(rows_processed, inserted, updated, skipped)
        logger.info("Backfill duration: %.1fs", (datetime.now() - started_at).total_seconds())
    except Exception as exc:
        status = "error"
        error = str(exc)
        logger.exception("Backfill failed: %s", exc)
        raise
    finally:
        finished_at = datetime.now()
        if mysql_conn is not None:
            try:
                record_run(
                    mysql_conn, started_at, finished_at, status,
                    rows_processed, inserted, updated, skipped, message, error,
                )
            except Exception as log_exc:
                logger.exception("Could not record backfill run: %s", log_exc)
            mysql_conn.close()
        if azure_conn is not None:
            azure_conn.close()
