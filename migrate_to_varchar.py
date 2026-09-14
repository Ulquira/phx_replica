import os
import logging
from dotenv import load_dotenv
import pyodbc
import mysql.connector

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("migrator")

AZURE_SERVER = os.getenv("AZURE_SERVER")
AZURE_DATABASE = os.getenv("AZURE_DATABASE")
AZURE_USER = os.getenv("AZURE_USER")
AZURE_PASSWORD = os.getenv("AZURE_PASSWORD")

MYSQL_HOST = os.getenv("MYSQL_HOST")
MYSQL_PORT = int(os.getenv("MYSQL_PORT", "3306"))
MYSQL_DATABASE = os.getenv("MYSQL_DATABASE")
MYSQL_USER = os.getenv("MYSQL_USER")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD")
MYSQL_TABLE = os.getenv("MYSQL_TABLE", "VW_WinORdeTraba")

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
        ssl_disabled=True
    )

def run_migration():
    logger.info("Iniciando migración de tipos a VARCHAR...")
    azure_conn = get_azure_connection()
    cursor_az = azure_conn.cursor()
    cursor_az.execute("""
        SELECT COLUMN_NAME, DATA_TYPE, CHARACTER_MAXIMUM_LENGTH
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = 'dbo' AND TABLE_NAME = 'VW_WinOrdeTraba'
    """)
    azure_cols = {row[0].lower(): {"type": row[1], "len": row[2]} for row in cursor_az.fetchall()}
    azure_conn.close()

    mysql_conn = get_mysql_connection()
    cursor_my = mysql_conn.cursor(dictionary=True)
    cursor_my.execute(f"SHOW COLUMNS FROM `{MYSQL_TABLE}`")
    mysql_cols = cursor_my.fetchall()

    alter_statements = []
    
    for m_col in mysql_cols:
        col_name = m_col['Field']
        current_type = m_col['Type'].lower()
        
        if "text" not in current_type and "blob" not in current_type:
            continue
            
        # Saltamos token y link que ya controlamos y a la georeferencia 
        if col_name.lower() in ['token', 'link', 'georeferencia_tecnico']:
            continue
            
        az_info = azure_cols.get(col_name.lower())
        if not az_info:
            continue
            
        char_len = az_info['len']
        
        # Validar si conviene pasarlo a VARCHAR
        if char_len is not None and char_len > 0 and char_len <= 16000:
            nuevo_tipo = f"VARCHAR({char_len})"
            # Armar la sentencia MODIFY
            logger.info(f"Columna '{col_name}': {current_type} -> {nuevo_tipo}")
            alter_statements.append(f"MODIFY COLUMN `{col_name}` {nuevo_tipo}")

    if not alter_statements:
        logger.info("No hay columnas para optimizar.")
    else:
        # Ejecutar todas las modificaciones en un solo ALTER TABLE
        alter_query = f"ALTER TABLE `{MYSQL_TABLE}` " + ", ".join(alter_statements)
        logger.info(f"Ejecutando ALTER TABLE...")
        cursor_my.execute(alter_query)
        logger.info(f"¡Éxito! Se optimizaron {len(alter_statements)} columnas de LONGTEXT a VARCHAR en MySQL.")
        
    mysql_conn.close()

if __name__ == "__main__":
    try:
        run_migration()
    except Exception as e:
        logger.error(f"Error: {e}")
