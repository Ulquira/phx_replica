import os
import re
import json
import csv
import hashlib
import logging
from datetime import datetime
from pathlib import Path

import pyodbc
import mysql.connector
from dotenv import load_dotenv

load_dotenv()

LOG_PATH = Path(__file__).with_name("sync.log")
logger = logging.getLogger("azure_mysql_sync")
logger.setLevel(logging.INFO)
if not logger.handlers:
    file_handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
    stream_handler = logging.StreamHandler()
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    file_handler.setFormatter(formatter)
    stream_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)

AZURE_SERVER = os.getenv("AZURE_SERVER")
AZURE_DATABASE = os.getenv("AZURE_DATABASE")
AZURE_USER = os.getenv("AZURE_USER")
AZURE_PASSWORD = os.getenv("AZURE_PASSWORD")

MYSQL_HOST = os.getenv("MYSQL_HOST")
MYSQL_PORT = int(os.getenv("MYSQL_PORT", "3306"))
MYSQL_DATABASE = os.getenv("MYSQL_DATABASE")
MYSQL_USER = os.getenv("MYSQL_USER")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD")
MYSQL_TABLE = os.getenv("MYSQL_TABLE", "vw_winordetraba_mirror")


def quote_ident(name: str) -> str:
    return "`" + name.replace("`", "``") + "`"


def safe_name(raw: str) -> str:
    return re.sub(r"[^0-9A-Za-z_]+", "_", raw).strip("_") or "column"


def map_sql_type(sql_type: str) -> str:
    t = (sql_type or "").lower()
    if "int" in t and "char" not in t and "varchar" not in t:
        return "BIGINT"
    if "decimal" in t or "numeric" in t or "money" in t or "smallmoney" in t or "float" in t or "real" in t:
        return "DOUBLE"
    if "bit" in t:
        return "TINYINT"
    if "datetime" in t or "date" in t or "time" in t:
        return "DATETIME"
    if "char" in t or "text" in t or "xml" in t:
        return "LONGTEXT"
    return "LONGTEXT"


def get_azure_connection():
    conn_str = (
        "DRIVER={ODBC Driver 18 for SQL Server};"
        f"SERVER={AZURE_SERVER};"
        f"DATABASE={AZURE_DATABASE};"
        f"UID={AZURE_USER};"
        f"PWD={AZURE_PASSWORD};"
        "Encrypt=yes;"
        "TrustServerCertificate=yes;"
        "Connection Timeout=30;"
    )
    return pyodbc.connect(conn_str)


def get_mysql_connection():
    return mysql.connector.connect(
        host=MYSQL_HOST,
        port=MYSQL_PORT,
        user=MYSQL_USER,
        password=MYSQL_PASSWORD,
        database=MYSQL_DATABASE,
        autocommit=True,
        ssl_disabled=True,
        connection_timeout=10
    )


def get_source_columns(conn):
    cursor = conn.cursor()
    cursor.execute("""
        SELECT COLUMN_NAME, DATA_TYPE
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = 'dbo' AND TABLE_NAME = 'VW_WinOrdeTraba'
        ORDER BY ORDINAL_POSITION
    """)
    return [
        {"name": row[0], "type": row[1]}
        for row in cursor.fetchall()
    ]


def pick_date_column(columns):
    for col in columns:
        name = col["name"].lower()
        if "fecha" in name or "date" in name or "created" in name or "updated" in name:
            return col["name"]
    return None


def fetch_source_rows(conn, columns):
    date_column = pick_date_column(columns)
    if date_column:
        query = f"SELECT * FROM [dbo].[VW_WinOrdeTraba] WHERE CAST([{date_column}] AS date) = CAST(GETDATE() AS date)"
    else:
        query = "SELECT * FROM [dbo].[VW_WinOrdeTraba]"

    cursor = conn.cursor()
    cursor.execute(query)
    rows = []
    for raw in cursor.fetchall():
        row = {}
        for idx, col in enumerate(columns):
            value = raw[idx]
            if isinstance(value, datetime):
                row[col["name"]] = value.isoformat(timespec="seconds")
            else:
                row[col["name"]] = value
        rows.append(row)
    return rows


def get_target_columns(mysql_conn, table_name):
    cursor = mysql_conn.cursor()
    cursor.execute(
        "SELECT COLUMN_NAME FROM information_schema.columns WHERE table_schema=%s AND table_name=%s ORDER BY ORDINAL_POSITION",
        (MYSQL_DATABASE, table_name),
    )
    return [row[0] for row in cursor.fetchall()]


def ensure_mysql_table(mysql_conn, columns):
    table_name = safe_name(MYSQL_TABLE)
    cursor = mysql_conn.cursor()
    cursor.execute(f"SHOW TABLES LIKE '{table_name}'")
    exists = cursor.fetchone() is not None
    if not exists:
        column_defs = []
        has_orden_id = False
        for col in columns:
            if col['name'].lower() == 'ordenid':
                has_orden_id = True
                column_defs.append(f"{quote_ident(col['name'])} {map_sql_type(col['type'])} PRIMARY KEY")
            else:
                column_defs.append(f"{quote_ident(col['name'])} {map_sql_type(col['type'])}")
        
        if not has_orden_id:
            column_defs.insert(0, f"{quote_ident('OrdenId')} INT NOT NULL PRIMARY KEY")

        # Agregar las columnas token y link por defecto al crear la tabla
        column_defs.append(f"{quote_ident('token')} VARCHAR(255)")
        column_defs.append(f"{quote_ident('link')} VARCHAR(255)")

        create_sql = f"CREATE TABLE {quote_ident(table_name)} ({', '.join(column_defs)})"
        cursor.execute(create_sql)
    else:
        # Si la tabla ya existe, nos aseguramos de que tenga las columnas token y link
        cursor.execute(f"SHOW COLUMNS FROM {quote_ident(table_name)} LIKE 'token'")
        if not cursor.fetchone():
            cursor.execute(f"ALTER TABLE {quote_ident(table_name)} ADD COLUMN {quote_ident('token')} VARCHAR(255)")
            
        cursor.execute(f"SHOW COLUMNS FROM {quote_ident(table_name)} LIKE 'link'")
        if not cursor.fetchone():
            cursor.execute(f"ALTER TABLE {quote_ident(table_name)} ADD COLUMN {quote_ident('link')} VARCHAR(255)")

    return table_name


def load_existing_rows(mysql_conn, table_name):
    cursor = mysql_conn.cursor(dictionary=True)
    cursor.execute(f"SELECT * FROM {quote_ident(table_name)}")
    rows = cursor.fetchall()
    return {str(row.get('OrdenId', '')): row for row in rows if row.get('OrdenId') is not None}


def ensure_control_table(mysql_conn):
    cursor = mysql_conn.cursor()
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


def record_run(mysql_conn, started_at, finished_at, status, rows_processed, inserted, updated, skipped, message, error):
    cursor = mysql_conn.cursor()
    cursor.execute(
        """
        INSERT INTO sync_runs (started_at, finished_at, status, rows_processed, inserted, updated, skipped, message, error)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (started_at, finished_at, status, rows_processed, inserted, updated, skipped, message, error),
    )
    # return the inserted run id for external logging
    try:
        return cursor.lastrowid
    except Exception:
        return None


def log_run_summary(rows_processed, inserted, updated, skipped):
    total_changes = inserted + updated
    logger.info("========================================")
    logger.info("Sincronización visual: %s filas procesadas", rows_processed)
    logger.info("Insertadas: %s", inserted)
    logger.info("Actualizadas: %s", updated)
    logger.info("Omitidas: %s", skipped)
    logger.info("Total de cambios: %s", total_changes)
    if total_changes == 0 and rows_processed > 0:
        logger.info("No hubo cambios en los datos de esta ejecución.")
    elif rows_processed == 0:
        logger.info("No se encontraron filas para procesar.")
    logger.info("========================================")


def write_summary_csv(run_id, started_at, finished_at, rows_processed, inserted, updated, skipped, status):
    path = Path(__file__).with_name("sync_summary.csv")
    duration = None
    try:
        duration = (finished_at - started_at).total_seconds()
    except Exception:
        duration = ""

    header = ["run_id", "started_at", "finished_at", "status", "rows_processed", "inserted", "updated", "skipped", "duration_seconds"]
    row = [run_id or "", started_at.isoformat(sep=" "), finished_at.isoformat(sep=" "), status, rows_processed, inserted, updated, skipped, duration]

    write_header = not path.exists()
    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if write_header:
            writer.writerow(header)
        writer.writerow(row)


def normalize_name(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z_]+", "_", value).strip("_").lower()


def resolve_target_column(target_columns, source_name):
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


def detect_state_column(columns):
    for col in columns:
        if col["name"].lower() == "estado":
            return col["name"]
    return None


def upsert_row(mysql_conn, table_name, columns, row, state_column, target_columns):
    mapped_columns = []
    for col in columns:
        if col["name"].lower() in ["link", "token", "cuadrilla_nombre"]:
            continue
        target_name = resolve_target_column(target_columns, col["name"])
        if target_name is None:
            continue
        mapped_columns.append((col["name"], target_name))

    if not mapped_columns:
        return False

    insert_columns = []
    for source_name, target_name in mapped_columns:
        if target_name.lower() == "ordenid":
            key_value = row.get(source_name)
            continue
        insert_columns.append((source_name, target_name))

    if "ordenid" not in {target_name.lower() for _, target_name in mapped_columns}:
        return False

    sql_columns = [quote_ident("OrdenId")]
    sql_values = [key_value]
    for source_name, target_name in insert_columns:
        sql_columns.append(quote_ident(target_name))
        sql_values.append(row.get(source_name))

    if len(sql_columns) == 1:
        return False

    placeholders = ", ".join(["%s"] * len(sql_columns))
    update_clause = ", ".join([f"{quote_ident(target_name)} = VALUES({quote_ident(target_name)})" for _, target_name in insert_columns])
    insert_sql = f"INSERT INTO {quote_ident(table_name)} ({', '.join(sql_columns)}) VALUES ({placeholders}) ON DUPLICATE KEY UPDATE {update_clause}"
    cursor = mysql_conn.cursor()
    cursor.execute(insert_sql, sql_values)
    return True


def sync_once():
    started_at = datetime.now()
    azure_conn = None
    mysql_conn = None
    status = "success"
    message = "Sincronización completada"
    error = None
    rows_processed = 0
    inserted = 0
    updated = 0
    skipped = 0

    try:
        azure_conn = get_azure_connection()
        mysql_conn = get_mysql_connection()
        ensure_control_table(mysql_conn)

        logger.info("Iniciando sincronización")
        print("=== Iniciando sincronización ===", flush=True)
        columns = get_source_columns(azure_conn)
        rows = fetch_source_rows(azure_conn, columns)
        rows_processed = len(rows)
        table_name = ensure_mysql_table(mysql_conn, columns)
        target_columns = get_target_columns(mysql_conn, safe_name(MYSQL_TABLE))
        existing_rows = load_existing_rows(mysql_conn, table_name)
        state_column = detect_state_column(columns)

        for row in rows:
            order_id = row.get('OrdenId')
            existing = existing_rows.get(str(order_id)) if order_id is not None else None

            if existing is None:
                upsert_row(mysql_conn, table_name, columns, row, state_column, target_columns)
                inserted += 1
            elif state_column and existing.get(safe_name(state_column)) != row.get(state_column):
                upsert_row(mysql_conn, table_name, columns, row, state_column, target_columns)
                updated += 1
            else:
                skipped += 1

        logger.info("Sincronización completada: insertados=%s, actualizados=%s, omitidos=%s", inserted, updated, skipped)
        logger.info("Tabla MySQL: %s", table_name)
        log_run_summary(rows_processed, inserted, updated, skipped)
        print("========================================", flush=True)
        print(f"Sincronización completada: insertados={inserted}, actualizados={updated}, omitidos={skipped}", flush=True)
        print(f"Tabla MySQL: {table_name}", flush=True)
        print(f"Total de cambios: {inserted + updated}", flush=True)
        if inserted + updated == 0 and rows_processed > 0:
            print("No hubo cambios en los datos de esta ejecución.", flush=True)
        elif rows_processed == 0:
            print("No se encontraron filas para procesar.", flush=True)
        print(f"Inicio: {started_at.isoformat(sep=' ')}", flush=True)
        
        # Asignar finished_at aquí para evitar UnboundLocalError en el log de éxito
        finished_at = datetime.now()
        
        print(f"Fin: {finished_at.isoformat(sep=' ')}", flush=True)
        print(f"Duración: {(finished_at - started_at).total_seconds():.1f} segundos", flush=True)
        print("========================================", flush=True)
        print("=== FIN DE EJECUCIÓN ===", flush=True)
    except Exception as exc:
        status = "error"
        error = str(exc)
        message = "Error en la sincronización"
        logger.exception("Fallo la sincronización: %s", exc)
        print(f"Error en la sincronización: {exc}", flush=True)
        raise
    finally:
        finished_at = datetime.now()
        run_id = None
        if mysql_conn is not None:
            try:
                run_id = record_run(mysql_conn, started_at, finished_at, status, rows_processed, inserted, updated, skipped, message, error)
            except Exception as log_exc:
                logger.exception("No se pudo registrar el estado de la sincronización: %s", log_exc)
            mysql_conn.close()
        # write a CSV summary line for easy visual inspection
        try:
            write_summary_csv(run_id, started_at, finished_at, rows_processed, inserted, updated, skipped, status)
        except Exception as csv_exc:
            logger.exception("No se pudo escribir el CSV resumen: %s", csv_exc)
        if azure_conn is not None:
            azure_conn.close()


if __name__ == "__main__":
    sync_once()
