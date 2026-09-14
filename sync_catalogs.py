import os
import base64
import logging
from datetime import datetime
import pyodbc
import mysql.connector
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("sync_view")

AZURE_SERVER = os.getenv("AZURE_SERVER")
AZURE_DATABASE = os.getenv("AZURE_DATABASE")
AZURE_USER = os.getenv("AZURE_USER")
AZURE_PASSWORD = os.getenv("AZURE_PASSWORD")

MYSQL_HOST = os.getenv("MYSQL_HOST")
MYSQL_PORT = int(os.getenv("MYSQL_PORT", "3306"))
MYSQL_DATABASE = os.getenv("MYSQL_DATABASE")
MYSQL_USER = os.getenv("MYSQL_USER")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD")

TABLE_NAME = "vw_info_cuadrillas"

QUERY_AZURE = """
SELECT 
    e.RazonSocial AS Empresa,
    c.Nombre AS Cuadrilla,
    p.RazonSocial AS Partner,
    u.NumeMovil AS Telefono,
    u.NumeDocuIden AS Documento,
    u.Foto AS Foto
FROM Usuarios u
INNER JOIN tecnicos t ON t.codiusua = u.codiusua
INNER JOIN cuadrillas c ON t.cuadriid = c.cuadriid
INNER JOIN EmpreTerce p ON p.EmpreTerceId = c.EmpreTerceId
INNER JOIN Empresas e ON c.EmpresaId = e.EmpresaId
"""

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
        connection_timeout=15
    )

def convert_bytes_to_img_data_uri(foto_bytes):
    if not foto_bytes:
        return None
    try:
        if isinstance(foto_bytes, (bytes, bytearray)):
            if len(foto_bytes) == 0:
                return None
            b64 = base64.b64encode(foto_bytes).decode('utf-8')
            if foto_bytes.startswith(b'\x89PNG'):
                mime = 'image/png'
            elif foto_bytes.startswith(b'GIF'):
                mime = 'image/gif'
            elif foto_bytes.startswith(b'BM'):
                mime = 'image/bmp'
            elif foto_bytes.startswith(b'RIFF') and len(foto_bytes) >= 12 and foto_bytes[8:12] == b'WEBP':
                mime = 'image/webp'
            else:
                mime = 'image/jpeg'
            return f"data:{mime};base64,{b64}"
    except Exception:
        return None
    return None

def sync_direct_query():
    azure_conn = None
    mysql_conn = None
    try:
        logger.info("Iniciando extracción directa de la consulta desde Azure...")
        azure_conn = get_azure_connection()
        cursor_az = azure_conn.cursor()
        cursor_az.execute(QUERY_AZURE)
        
        columns = [col[0] for col in cursor_az.description]
        rows = cursor_az.fetchall()
        logger.info(f"Se extrajeron {len(rows)} registros desde Azure.")
        
        mysql_conn = get_mysql_connection()
        cursor_my = mysql_conn.cursor()
        
        # Eliminar si existe como vista o tabla
        cursor_my.execute(f"DROP VIEW IF EXISTS `{TABLE_NAME}`")
        cursor_my.execute(f"DROP TABLE IF EXISTS `{TABLE_NAME}`")
        
        # Crear la tabla física optimizada en MySQL con la columna Foto_Img (Data URI / Base64)
        create_sql = f"""
        CREATE TABLE `{TABLE_NAME}` (
            `Empresa` VARCHAR(255),
            `Cuadrilla` VARCHAR(255),
            `Partner` VARCHAR(255),
            `Telefono` VARCHAR(50),
            `Documento` VARCHAR(50),
            `Foto` LONGBLOB,
            `Foto_Img` LONGTEXT,
            INDEX idx_cuadrilla (`Cuadrilla`),
            INDEX idx_documento (`Documento`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
        cursor_my.execute(create_sql)
        logger.info(f"Tabla `{TABLE_NAME}` creada en MySQL con índices y columna Foto_Img.")
        
        if rows:
            foto_idx = columns.index("Foto") if "Foto" in columns else -1
            target_cols = list(columns) + ["Foto_Img"]
            
            placeholders = ", ".join(["%s"] * len(target_cols))
            insert_sql = f"INSERT INTO `{TABLE_NAME}` (`{ '`, `'.join(target_cols) }`) VALUES ({placeholders})"
            
            batch_data = []
            for row in rows:
                foto_val = row[foto_idx] if foto_idx >= 0 else None
                img_data_uri = convert_bytes_to_img_data_uri(foto_val)
                batch_data.append(tuple(list(row) + [img_data_uri]))
            
            chunk_size = 50
            for i in range(0, len(batch_data), chunk_size):
                cursor_my.executemany(insert_sql, batch_data[i:i + chunk_size])
                mysql_conn.commit()
                
            logger.info(f"¡Éxito! Se insertaron {len(batch_data)} registros en `{TABLE_NAME}` en MySQL con Foto_Img convertida.")
            
        cursor_my.close()
        cursor_az.close()
    except Exception as exc:
        logger.error(f"Error durante la sincronización de la consulta: {exc}", exc_info=True)
    finally:
        if azure_conn:
            try: azure_conn.close()
            except: pass
        if mysql_conn:
            try: mysql_conn.close()
            except: pass
        logger.info("=== Fin de ejecución ===")

if __name__ == "__main__":
    sync_direct_query()