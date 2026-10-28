import os
import io
import re
import base64
import logging
from datetime import datetime
from PIL import Image, ImageOps
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
    u.Foto AS Foto,
    CASE 
        WHEN u.fechabaja IS NOT NULL OR c.fechabaja IS NOT NULL OR t.fechabaja IS NOT NULL THEN 'Baja'
        ELSE 'Activo'
    END AS Estado
FROM Usuarios u
INNER JOIN tecnicos t ON t.codiusua = u.codiusua
INNER JOIN cuadrillas c ON t.cuadriid = c.cuadriid
INNER JOIN EmpreTerce p ON p.EmpreTerceId = c.EmpreTerceId
INNER JOIN Empresas e ON c.EmpresaId = e.EmpresaId
"""

def clean_cuadrilla_name(raw: str) -> str:
    if not raw:
        return ""
    text = raw.strip()
    
    # 1. Regla principal: Cortar SIEMPRE todo lo que esté ANTES y HASTA el último SGA, SGI o SGM
    if re.search(r'\b(?:SGA|SGI|SGM)\b', text, flags=re.IGNORECASE):
        cleaned = re.sub(r'^.*\b(?:SGA|SGI|SGM)\b\s*', '', text, flags=re.IGNORECASE).strip()
        cleaned = re.sub(r'\s+K\d+\s+.*$', '', cleaned, flags=re.IGNORECASE).strip()
        if cleaned:
            return cleaned

    # 2. Si NO tiene SGA, SGI o SGM:
    # Quitar códigos de cuadrilla tipo: "DP 14 ", "P 3 ", "K 11 ", "D 3 ", "BAJA FR ", etc.
    text = re.sub(r'^(?:BAJA\s+FR|BAJA|ALTA|[A-Z]{1,3}\s*\d+)\s+', '', text, flags=re.IGNORECASE)
    
    # 3. Quitar tipos de operación y modalidades operativas
    operaciones = r'MOTOWIN|POSTVENTA|TRASLADO|REPARACION|INSTALACION|MANTENIMIENTO'
    text = re.sub(rf'^(?:{operaciones})\s+', '', text, flags=re.IGNORECASE)
    text = re.sub(rf'^(?:{operaciones})\s+', '', text, flags=re.IGNORECASE)
    
    # 4. Quitar contratas conocidas
    contratas = (
        r'ALFITELL|AP&TELL|AVANTEL|BIO|BMP|CESPEDES|COBRA|COMFICA|DATANTENNA|'
        r'DIGETEL|DOMINION|EJAS|GRUPO\s+JBA|JBF(?:\s+TELCO)?|KAJOMI|LP\s+STAR|'
        r'M&D|MALLAUSA|MEBRA|OLMA|SISCARD|SUMMUM|TADELC|TLI|VISUAL|YEVIZACOM|'
        r'ZARITEL|LARI|EZENTIS|GLOBAL|WIN|ALL\s+TELECOM|ANOVO|ONI'
    )
    text = re.sub(rf'^(?:{contratas})\s+', '', text, flags=re.IGNORECASE)
    
    # Limpieza secundaria por si el orden viene invertido (ej. "POSTVENTA VISUAL" o "TRASLADO TADELC")
    text = re.sub(rf'^(?:{operaciones})\s+', '', text, flags=re.IGNORECASE)
    text = re.sub(rf'^(?:{contratas})\s+', '', text, flags=re.IGNORECASE)
    
    # 5. Quitar sufijos tipo "K13 KAJOMI", "K3 VISUAL", "K19 CESPEDES"
    text = re.sub(r'\s+K\d+\s+.*$', '', text, flags=re.IGNORECASE)
    
    return text.strip()

def get_azure_connection():
    drivers = pyodbc.drivers()
    driver = "ODBC Driver 18 for SQL Server"
    if driver not in drivers:
        for fallback in ["ODBC Driver 17 for SQL Server", "SQL Server"]:
            if fallback in drivers:
                driver = fallback
                break
    conn_str = (
        f"DRIVER={{{driver}}};"
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
            img = Image.open(io.BytesIO(foto_bytes))
            try:
                img = ImageOps.exif_transpose(img)
            except Exception:
                pass
            if img.mode != 'RGB':
                img = img.convert('RGB')
            img.thumbnail((600, 600), Image.Resampling.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format='JPEG', quality=82, optimize=True)
            b64 = base64.b64encode(buf.getvalue()).decode('utf-8')
            return f"data:image/jpeg;base64,{b64}"
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

        cursor_my.execute(f"""
            CREATE TABLE IF NOT EXISTS `{TABLE_NAME}` (
                `Empresa` VARCHAR(255),
                `Cuadrilla` VARCHAR(255),
                `Nombre_Tecnico_Limpio` VARCHAR(255),
                `Partner` VARCHAR(255),
                `Telefono` VARCHAR(50),
                `Documento` VARCHAR(50),
                `Foto_Img` LONGTEXT,
                `Img_mejorada` LONGTEXT,
                `foto_aprobada` TINYINT(1) DEFAULT 0,
                `Desaprueba_img` VARCHAR(50) DEFAULT NULL,
                `Estado` VARCHAR(50) DEFAULT 'Activo',
                INDEX idx_cuadrilla (`Cuadrilla`),
                INDEX idx_nombre_limpio (`Nombre_Tecnico_Limpio`),
                INDEX idx_documento (`Documento`),
                INDEX idx_foto_aprobada (`foto_aprobada`),
                INDEX idx_estado (`Estado`)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)

        required_columns = {
            "Nombre_Tecnico_Limpio": "VARCHAR(255)",
            "Img_mejorada": "LONGTEXT",
            "foto_aprobada": "TINYINT(1) DEFAULT 0",
            "Desaprueba_img": "VARCHAR(50) DEFAULT NULL",
            "Estado": "VARCHAR(50) DEFAULT 'Activo'",
        }
        for column_name, column_type in required_columns.items():
            cursor_my.execute(f"SHOW COLUMNS FROM `{TABLE_NAME}` LIKE %s", (column_name,))
            if not cursor_my.fetchone():
                cursor_my.execute(f"ALTER TABLE `{TABLE_NAME}` ADD COLUMN `{column_name}` {column_type}")

        cursor_my.execute(f"SELECT Documento, Desaprueba_img FROM `{TABLE_NAME}` WHERE Documento IS NOT NULL AND Documento <> ''")
        existing_records = {str(r[0]): r[1] for r in cursor_my.fetchall()}

        update_sql = f"""
            UPDATE `{TABLE_NAME}`
            SET Empresa = %s, Cuadrilla = %s, Nombre_Tecnico_Limpio = %s,
                Partner = %s, Telefono = %s, Foto_Img = COALESCE(%s, Foto_Img),
                Estado = %s
            WHERE Documento = %s
        """
        insert_sql = f"""
            INSERT INTO `{TABLE_NAME}`
                (Empresa, Cuadrilla, Nombre_Tecnico_Limpio, Partner, Telefono, Foto_Img, Estado, Documento)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """

        inserted_count = 0
        updated_count = 0
        synced_docs = set()

        for row in rows:
            row_dict = {col: row[i] for i, col in enumerate(columns)}
            cuadrilla_val = row_dict.get("Cuadrilla") or ""
            doc_val = str(row_dict.get("Documento") or "").strip()[:50]
            estado_val = row_dict.get("Estado") or "Activo"
            disapproved_val = existing_records.get(doc_val)

            # Si el técnico tiene desaprobación registrada, no sobreescribir la imagen
            if disapproved_val and "DESAPROB" in str(disapproved_val).upper():
                img_data_uri = None
            else:
                img_data_uri = convert_bytes_to_img_data_uri(row_dict.get("Foto"))

            record_values = (
                row_dict.get("Empresa"),
                cuadrilla_val,
                clean_cuadrilla_name(cuadrilla_val),
                row_dict.get("Partner"),
                row_dict.get("Telefono"),
                img_data_uri,
                estado_val,
            )

            if doc_val and doc_val in existing_records:
                cursor_my.execute(update_sql, (*record_values, doc_val))
                updated_count += 1
            else:
                cursor_my.execute(insert_sql, (*record_values, doc_val))
                inserted_count += 1
                if doc_val:
                    existing_records[doc_val] = None

            if doc_val:
                synced_docs.add(doc_val)

        # Conciliación: marcar como 'Baja' registros en MySQL que ya no existen en Azure
        missing_docs = set(existing_records.keys()) - synced_docs
        marked_baja = 0
        if missing_docs:
            batch_size = 500
            missing_list = list(missing_docs)
            for i in range(0, len(missing_list), batch_size):
                chunk = missing_list[i:i + batch_size]
                format_strings = ','.join(['%s'] * len(chunk))
                cursor_my.execute(
                    f"UPDATE `{TABLE_NAME}` SET Estado = 'Baja' WHERE Documento IN ({format_strings}) AND (Estado <> 'Baja' OR Estado IS NULL)",
                    tuple(chunk)
                )
                marked_baja += cursor_my.rowcount
            if marked_baja > 0:
                logger.info(f"Reconciliación: {marked_baja} técnicos no encontrados en Azure marcados como 'Baja'.")

        mysql_conn.commit()
        logger.info(
            f"Catálogo actualizado: {inserted_count} nuevos, {updated_count} actualizados, {marked_baja} reconciliados como baja."
        )
            
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
