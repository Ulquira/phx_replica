import os
import re
import hashlib
from datetime import datetime
from typing import Dict, List, Tuple

import mysql.connector
import pyodbc
from dotenv import load_dotenv

load_dotenv()

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


def convert_value(value):
    if value is None:
        return None
    if isinstance(value, (datetime,)):
        return value.isoformat(timespec="seconds")
    return value


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
    )


def get_view_columns(conn) -> List[Dict[str, object]]:
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT COLUMN_NAME, DATA_TYPE
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = 'dbo' AND TABLE_NAME = 'VW_WinOrdeTraba'
        ORDER BY ORDINAL_POSITION
        """
    )
    return [{"name": row[0], "type": row[1]} for row in cursor.fetchall()]


def detect_date_column(columns: List[Dict[str, object]]) -> str | None:
    candidates = ["fecha", "fechacreacion", "fecharegistro", "createdat", "created", "date", "fechaactualizacion"]
    for column in columns:
        name = column["name"].lower()
        if name in candidates or "fecha" in name or "date" in name or "created" in name:
            return column["name"]
    return None


def build_source_query(date_column: str | None) -> str:
    base = "SELECT * FROM [dbo].[VW_WinOrdeTraba]"
    if date_column:
        return f"{base} WHERE CAST([{date_column}] AS date) = CAST(GETDATE() AS date)"
    return base


def compute_row_key(values: Dict[str, object]) -> str:
    payload = json.dumps(values, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def read_source_rows(conn) -> Tuple[List[Dict[str, object]], List[Dict[str, object]]]:
    columns = get_view_columns(conn)
    date_column = detect_date_column(columns)
    query = build_source_query(date_column)
    cursor = conn.cursor()
    cursor.execute(query)
    rows = []
    column_names = [col["name"] for col in columns]
    for raw in cursor.fetchall():
        record = {col_name: convert_value(value) for col_name, value in zip(column_names, raw)}
        rows.append(record)
    return columns, rows


def ensure_mysql_table(mysql_conn, columns: List[Dict[str, object]]) -> Tuple[str, str]:
    cursor = mysql_conn.cursor()
    safe_table = re.sub(r"[^0-9A-Za-z_]+", "_", MYSQL_TABLE).strip("_") or "mirror_table"
    column_defs = []
    for column in columns:
        name = column["name"]
        safe_name = re.sub(r"[^0-9A-Za-z_]+", "_", name).strip("_") or "column"
        column_defs.append(f"{quote_ident(safe_name)} LONGTEXT")

    # Add a synthetic primary key if the source does not expose a likely natural key.
    natural_key_col = None
    for candidate in ["id", "codigo", "linea", "numero", "registro", "orden"]:
        if candidate in {c["name"].lower() for c in columns}:
            natural_key_col = next(c["name"] for c in columns if c["name"].lower() == candidate)
            break

    if natural_key_col is None:
        column_defs.insert(0, f"{quote_ident('__row_key')} VARCHAR(64) PRIMARY KEY")
        key_col = "__row_key"
    else:
        key_col = re.sub(r"[^0-9A-Za-z_]+", "_", natural_key_col).strip("_") or "id"
        column_defs.insert(0, f"{quote_ident(key_col)} VARCHAR(255) PRIMARY KEY")

    create_sql = f"CREATE TABLE IF NOT EXISTS {quote_ident(safe_table)} (\n" + ",\n".join(column_defs) + "\n)"
    cursor.execute(create_sql)
    return safe_table, key_col


def load_existing_rows(mysql_conn, table_name: str, key_column: str) -> Dict[str, Dict[str, object]]:
    cursor = mysql_conn.cursor(dictionary=True)
    cursor.execute(f"SELECT * FROM {quote_ident(table_name)}")
    rows = cursor.fetchall()
    result = {}
    for row in rows:
        key_value = row.get(key_column)
        if key_value is None:
            continue
        result[str(key_value)] = row
    return result


def sync_rows():
    azure_conn = get_azure_connection()
    mysql_conn = get_mysql_connection()

    try:
        columns, source_rows = read_source_rows(azure_conn)
        table_name, key_col = ensure_mysql_table(mysql_conn, columns)
        existing = load_existing_rows(mysql_conn, table_name, key_col)

        state_column = None
        for column in columns:
            if column["name"].lower() == "estado":
                state_column = column["name"]
                break

        for row in source_rows:
            if key_col == "__row_key":
                key_value = compute_row_key(row)
            else:
                key_value = row.get(next(c["name"] for c in columns if re.sub(r"[^0-9A-Za-z_]+", "_", c["name"]).strip("_") or "id" == key_col))
                if key_value is None:
                    key_value = compute_row_key(row)

            if key_value is None:
                continue

            key_value_str = str(key_value)
            current_state = row.get(state_column) if state_column else None
            existing_row = existing.get(key_value_str)

            if existing_row is None:
                insert_row(mysql_conn, table_name, columns, key_col, key_value_str, row)
                existing[key_value_str] = row
            elif current_state is not None and existing_row.get(state_column) != current_state:
                update_row(mysql_conn, table_name, columns, key_col, key_value_str, row)
                existing[key_value_str] = row
            else:
                continue

        print(f"Sincronización completada. Tabla MySQL: {table_name}")
        print(f"Filas procesadas desde Azure: {len(source_rows)}")
    finally:
        azure_conn.close()
        mysql_conn.close()


def insert_row(mysql_conn, table_name: str, columns: List[Dict[str, object]], key_col: str, key_value: str, row: Dict[str, object]):
    safe_cols = [re.sub(r"[^0-9A-Za-z_]+", "_", c["name"]).strip("_") or "column" for c in columns]
    if key_col == "__row_key":
        safe_cols = ["__row_key"] + safe_cols
        values = [key_value] + [row.get(c["name"]) for c in columns]
    else:
        safe_cols = [key_col] + safe_cols
        values = [key_value] + [row.get(c["name"]) for c in columns]
    placeholders = ", ".join(["%s"] * len(safe_cols))
    columns_sql = ", ".join([quote_ident(col) for col in safe_cols])
    cursor = mysql_conn.cursor()
    cursor.execute(f"INSERT INTO {quote_ident(table_name)} ({columns_sql}) VALUES ({placeholders})", values)


def update_row(mysql_conn, table_name: str, columns: List[Dict[str, object]], key_col: str, key_value: str, row: Dict[str, object]):
    safe_cols = [re.sub(r"[^0-9A-Za-z_]+", "_", c["name"]).strip("_") or "column" for c in columns]
    if key_col == "__row_key":
        safe_cols = ["__row_key"] + safe_cols
        values = [key_value] + [row.get(c["name"]) for c in columns]
    else:
        safe_cols = [key_col] + safe_cols
        values = [key_value] + [row.get(c["name"]) for c in columns]
    assignments = ", ".join([f"{quote_ident(col)} = %s" for col in safe_cols[1:]])
    cursor = mysql_conn.cursor()
    cursor.execute(
        f"UPDATE {quote_ident(table_name)} SET {assignments} WHERE {quote_ident(key_col)} = %s",
        values[1:] + [key_value],
    )


if __name__ == "__main__":
    import json
    sync_rows()
