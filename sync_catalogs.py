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

# Tablas a sincronizar
TABLES_TO_SYNC = ["Usuarios", "tecnicos", "cuadrillas", "EmpreTerce", "Empresas"]

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

def map_sql_type(sql_type: str) -> str:
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
    return "LONGTEXT"

def get_table_schema(conn, table_name):
    cursor = conn.cursor()
    cursor.execute("""
        SELECT COLUMN_NAME, DATA_TYPE
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = 'dbo' AND TABLE_NAME = ?
        ORDER BY ORDINAL_POSITION
    """, (table_name,))
    columns = [{"name": row[0], "type": row[1]} for row in cursor.fetchall()]
    return columns

def fetch_all_rows(conn, table_name):
    cursor = conn.cursor()
    cursor.execute(f"SELECT * FROM [dbo].[{table_name}]")
    
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
    logger.info(f"Sincronizando tabla: {table_name}")
    
    # 1. Obtener esquema y datos de Azure
    columns = get_table_schema(azure_conn, table_name)
    if not columns:
        logger.warning(f"La tabla {table_name} no existe o no tiene columnas en Azure.")
        return
        
    rows, query_columns = fetch_all_rows(azure_conn, table_name)
    logger.info(f"Se extrajeron {len(rows)} registros de {table_name} (Azure).")

    # 2. Crear o recrear tabla en MySQL
    cursor = mysql_conn.cursor()
    
    # Para tablas catalogo, si queremos una réplica exacta sin preocuparnos de llaves primarias complejas,
    # podemos hacer un Drop y Create, o crearla si no existe y luego hacer un Truncate.
    # Haremos TRUNCATE para no perder los permisos o vistas asociadas si ya existe.
    
    cursor.execute(f"SHOW TABLES LIKE '{table_name}'")
    exists = cursor.fetchone() is not None
    
    if not exists:
        column_defs = []
        for col in columns:
            column_defs.append(f"{quote_ident(col['name'])} {map_sql_type(col['type'])}")
        
        create_sql = f"CREATE TABLE {quote_ident(table_name)} ({', '.join(column_defs)})"
        cursor.execute(create_sql)
        logger.info(f"Tabla {table_name} creada en MySQL.")
    
    # 3. Limpiar tabla destino y cargar nuevos datos (Full Sync / Refresh)
    cursor.execute(f"TRUNCATE TABLE {quote_ident(table_name)}")
    
    if rows:
        placeholders = ", ".join(["%s"] * len(query_columns))
        insert_sql = f"INSERT INTO {quote_ident(table_name)} ({', '.join(quote_ident(c) for c in query_columns)}) VALUES ({placeholders})"
        
        batch_data = [tuple(row.get(c) for c in query_columns) for row in rows]
        
        chunk_size = 1000
        for i in range(0, len(batch_data), chunk_size):
            cursor.executemany(insert_sql, batch_data[i:i + chunk_size])
            
        logger.info(f"Se insertaron {len(batch_data)} registros en {table_name} (MySQL).")

def create_mysql_view(mysql_conn):
    logger.info("Creando/Actualizando vista de información de cuadrillas...")
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
    logger.info("Vista vw_info_cuadrillas creada exitosamente.")

def main():
    try:
        azure_conn = get_azure_connection()
        mysql_conn = get_mysql_connection()
        
        for table in TABLES_TO_SYNC:
            sync_table(azure_conn, mysql_conn, table)
            
        # Crear la vista que integra las tablas replicadas
        create_mysql_view(mysql_conn)
            
    except Exception as e:
        logger.error(f"Error sincronizando catálogos: {e}")
    finally:
        if 'azure_conn' in locals() and azure_conn: azure_conn.close()
        if 'mysql_conn' in locals() and mysql_conn: mysql_conn.close()
        logger.info("Proceso finalizado.")

if __name__ == "__main__":
    main()