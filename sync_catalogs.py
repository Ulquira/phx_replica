import os
import re
import logging
from datetime import datetime
from pathlib import Path

import pyodbc
import mysql.connector
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("sync_catalogs")

AZURE_SERVER = os.getenv("AZURE_SERVER")
AZURE_DATABASE = os.getenv("AZURE_DATABASE")
AZURE_USER = os.getenv("AZURE_USER")
AZURE_PASSWORD = os.getenv("AZURE_PASSWORD")

MYSQL_HOST = os.getenv("MYSQL_HOST")
MYSQL_PORT = int(os.getenv("MYSQL_PORT", "3306"))
MYSQL_DATABASE = os.getenv("MYSQL_DATABASE")
MYSQL_USER = os.getenv("MYSQL_USER")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD")

# Tablas a sincronizar (primero las tablas ligeras para que la vista esté lista en segundos)
TABLES_TO_SYNC = ["Empresas", "EmpreTerce", "cuadrillas", "tecnicos", "Usuarios"]

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

def quote_ident(name: str) -> str:
    return "`" + name.replace("`", "``") + "`"

def map_sql_type(sql_type: str, char_length=None) -> str:
    t = (sql_type or "").lower()
    if "int" in t and "char" not in t and "varchar" not in t:
        return "BIGINT"
    if "decimal" in t or "numeric" in t or "money" in t or "float" in t or "real" in t:
        return "DOUBLE"
    if "bit" in t:
        return "TINYINT"
    if "datetime" in t or "date" in t or "time" in t:
        return "DATETIME"
    if "image" in t or "varbinary" in t or "binary" in t:
        return "LONGBLOB"
    if "geometry" in t or "geography" in t:
        return "TEXT"
        
    if char_length is not None:
        try:
            l = int(char_length)
            if l == -1 or l > 16000:
                return "LONGTEXT"
            return f"VARCHAR({l})"
        except:
            pass
            
    return "VARCHAR(255)"

def get_real_table_name_and_schema(conn, table_name):
    cursor = conn.cursor()
    # Buscar el nombre real de la tabla sin importar mayúsculas/minúsculas
    cursor.execute("""
        SELECT TABLE_NAME 
        FROM INFORMATION_SCHEMA.TABLES 
        WHERE TABLE_SCHEMA = 'dbo' AND LOWER(TABLE_NAME) = LOWER(?)
    """, (table_name,))
    row = cursor.fetchone()
    if not row:
        return None, []
    real_name = row[0]
    
    cursor.execute("""
        SELECT COLUMN_NAME, DATA_TYPE, CHARACTER_MAXIMUM_LENGTH
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = 'dbo' AND TABLE_NAME = ?
        ORDER BY ORDINAL_POSITION
    """, (real_name,))
    columns = [{"name": row[0], "type": row[1], "length": row[2]} for row in cursor.fetchall()]
    return real_name, columns

def fetch_all_rows(conn, real_table_name, columns):
    cursor = conn.cursor()
    
    # Manejo de tipos espaciales (geography/geometry/hierarchyid) que pyodbc no soporta nativamente (Error -151)
    select_items = []
    for col in columns:
        col_name = col["name"]
        data_type = (col["type"] or "").lower()
        if data_type in ["geometry", "geography"]:
            select_items.append(f"CAST([{col_name}].STAsText() AS VARCHAR(MAX)) AS [{col_name}]")
        elif data_type in ["hierarchyid"]:
            select_items.append(f"CAST([{col_name}] AS VARCHAR(MAX)) AS [{col_name}]")
        elif data_type in ["timestamp", "rowversion"]:
            select_items.append(f"CAST([{col_name}] AS BIGINT) AS [{col_name}]")
        else:
            select_items.append(f"[{col_name}]")
            
    query = f"SELECT {', '.join(select_items)} FROM [dbo].[{real_table_name}]"
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
    return rows, query_columns

def sync_table(azure_conn, mysql_conn, table_name):
    try:
        logger.info(f"--- Iniciando sincronización de tabla: {table_name} ---")
        
        # 1. Obtener nombre real y esquema de Azure
        real_name, columns = get_real_table_name_and_schema(azure_conn, table_name)
        if not real_name or not columns:
            logger.warning(f"La tabla '{table_name}' no existe en Azure SQL (dbo). Se omite.")
            return False
            
        rows, query_columns = fetch_all_rows(azure_conn, real_name, columns)
        logger.info(f"Se extrajeron {len(rows)} registros de [{real_name}] (Azure).")

        # 2. Crear o recrear tabla en MySQL
        cursor = mysql_conn.cursor()
        
        # Forzamos DROP y CREATE
        cursor.execute(f"DROP TABLE IF EXISTS {quote_ident(table_name)}")
        
        column_defs = []
        has_blob = False
        for col in columns:
            col_type = map_sql_type(col['type'], col.get('length'))
            if "blob" in col_type.lower():
                has_blob = True
            column_defs.append(f"{quote_ident(col['name'])} {col_type}")
        
        create_sql = f"CREATE TABLE {quote_ident(table_name)} ({', '.join(column_defs)}) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
        cursor.execute(create_sql)
        logger.info(f"Tabla `{table_name}` recreada en MySQL con {len(column_defs)} columnas.")
        
        if rows:
            placeholders = ", ".join(["%s"] * len(query_columns))
            insert_sql = f"INSERT INTO {quote_ident(table_name)} ({', '.join(quote_ident(c) for c in query_columns)}) VALUES ({placeholders})"
            
            batch_data = [tuple(row.get(c) for c in query_columns) for row in rows]
            
            # Usar lotes adecuados según tipo
            chunk_size = 100 if has_blob else 500
            for i in range(0, len(batch_data), chunk_size):
                cursor.executemany(insert_sql, batch_data[i:i + chunk_size])
                mysql_conn.commit()
                
            logger.info(f"¡Éxito! Se insertaron {len(batch_data)} registros en `{table_name}` (MySQL).")
        cursor.close()
        return True
    except Exception as exc:
        logger.error(f"Error sincronizando tabla '{table_name}': {exc}", exc_info=True)
        return False

def create_mysql_view(mysql_conn):
    try:
        logger.info("Creando/Actualizando vista `vw_info_cuadrillas` en MySQL...")
        view_sql = """
        CREATE OR REPLACE VIEW vw_info_cuadrillas AS
        SELECT 
            e.RazonSocial as Empresa,
            c.Nombre as Cuadrilla,
            p.RazonSocial as Partner,
            u.NumeMovil as Telefono,
            u.NumeDocuIden as Documento,
            u.foto
        FROM Usuarios u
        INNER JOIN tecnicos t ON t.codiusua = u.codiusua
        INNER JOIN cuadrillas c ON t.cuadriid = c.cuadriid
        INNER JOIN EmpreTerce p ON p.EmpreTerceId = c.EmpreTerceId
        INNER JOIN Empresas e ON c.EmpresaId = e.EmpresaId
        """
        cursor = mysql_conn.cursor()
        cursor.execute(view_sql)
        cursor.close()
        logger.info("¡Vista `vw_info_cuadrillas` creada exitosamente en MySQL!")
        return True
    except Exception as exc:
        logger.error(f"Error creando vista `vw_info_cuadrillas`: {exc}", exc_info=True)
        return False

def main():
    azure_conn = None
    mysql_conn = None
    try:
        azure_conn = get_azure_connection()
        mysql_conn = get_mysql_connection()
        
        for table in TABLES_TO_SYNC:
            sync_table(azure_conn, mysql_conn, table)
            
        # Crear la vista que integra las tablas replicadas
        create_mysql_view(mysql_conn)
            
    except Exception as e:
        logger.error(f"Error general en catálogos: {e}", exc_info=True)
    finally:
        if azure_conn: 
            try: azure_conn.close()
            except: pass
        if mysql_conn: 
            try: mysql_conn.close()
            except: pass
        logger.info("=== Proceso de catálogos finalizado ===")

if __name__ == "__main__":
    main()