import os
import logging
from datetime import datetime
import pyodbc
import mysql.connector
from dotenv import load_dotenv

from sync_azure_mysql_local import (
    get_azure_connection,
    get_mysql_connection,
    get_source_columns,
    pick_date_column,
    get_target_columns,
    ensure_mysql_table,
    ensure_control_table,
    record_run,
    log_run_summary,
    write_summary_csv,
    safe_name,
    detect_state_column,
    load_existing_rows,
    upsert_rows_batch,
    MYSQL_TABLE,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("backfill_sync")

def fetch_backfill_rows(conn, columns, start_date="2026-09-11"):
    date_column = pick_date_column(columns)
    
    base_query = """
        SELECT t.*, 
               CASE 
                   WHEN t.Estado = 'En camino' 
                   THEN CAST(p.Latitud AS VARCHAR(50)) + ',' + CAST(p.Longitud AS VARCHAR(50)) 
                   ELSE NULL 
               END AS Georeferencia_tecnico
        FROM [dbo].[VW_WinOrdeTraba] t
        LEFT JOIN [dbo].[Cuadrillas] c ON t.Cuadrilla = c.Nombre
        LEFT JOIN [dbo].[VW_CuadriUltiPosi] p ON c.CuadriId = p.CuadrillaId
    """
    
    if date_column:
        query = f"{base_query} WHERE t.[{date_column}] >= '{start_date}'"
    else:
        query = base_query

    logger.info(f"Consultando Azure SQL para fechas >= {start_date}...")
    with conn.cursor() as cursor:
        cursor.execute(query)
        query_columns = [col[0] for col in cursor.description]
        rows = []
        for raw in cursor.fetchall():
            row = {}
            for idx, col_name in enumerate(query_columns):
                value = raw[idx]
                if isinstance(value, datetime):
                    row[col_name] = value.isoformat(timespec="seconds")
                else:
                    row[col_name] = value
            rows.append(row)
    return rows

def run_backfill(start_date="2026-09-11"):
    started_at = datetime.now()
    azure_conn = None
    mysql_conn = None
    status = "success"
    message = f"Backfill retroactivo completado desde {start_date}"
    error = None
    rows_processed = 0
    inserted = 0
    updated = 0
    skipped = 0

    try:
        logger.info(f"=== INICIANDO RECUPERACION RETROACTIVA (Desde {start_date}) ===")
        azure_conn = get_azure_connection()
        mysql_conn = get_mysql_connection()
        ensure_control_table(mysql_conn)

        columns = get_source_columns(azure_conn)
        rows = fetch_backfill_rows(azure_conn, columns, start_date)
        rows_processed = len(rows)
        logger.info(f"Se obtuvieron {rows_processed} registros totales en Azure SQL para el periodo.")

        table_name = ensure_mysql_table(mysql_conn, columns)
        target_columns = get_target_columns(mysql_conn, safe_name(MYSQL_TABLE))
        state_column = detect_state_column(columns)
        
        target_ids = [r.get('OrdenId') for r in rows if r.get('OrdenId') is not None]
        logger.info(f"Verificando {len(target_ids)} IDs existentes en MySQL...")
        existing_rows = load_existing_rows(mysql_conn, table_name, target_ids, state_column)

        rows_to_upsert = []
        for row in rows:
            order_id = row.get('OrdenId')
            existing = existing_rows.get(str(order_id)) if order_id is not None else None

            if existing is None:
                rows_to_upsert.append(row)
                inserted += 1
            elif state_column and existing.get(safe_name(state_column)) != row.get(state_column):
                rows_to_upsert.append(row)
                updated += 1
            else:
                skipped += 1

        if rows_to_upsert:
            logger.info(f"Insertando/Actualizando {len(rows_to_upsert)} registros en MySQL (Lotes de 500)...")
            upsert_rows_batch(mysql_conn, table_name, columns, rows_to_upsert, target_columns)

        log_run_summary(rows_processed, inserted, updated, skipped)
        finished_at = datetime.now()
        logger.info(f"Duración de la recuperación: {(finished_at - started_at).total_seconds():.1f}s")
        logger.info("=== RECUPERACION FINALIZADA CON EXITO ===")
    except Exception as exc:
        status = "error"
        error = str(exc)
        logger.exception("Fallo el backfill: %s", exc)
        raise
    finally:
        finished_at = datetime.now()
        if mysql_conn is not None:
            try:
                record_run(mysql_conn, started_at, finished_at, status, rows_processed, inserted, updated, skipped, message, error)
            except Exception:
                pass
            mysql_conn.close()
        if azure_conn is not None:
            azure_conn.close()

if __name__ == "__main__":
    run_backfill("2026-09-11")
