from __future__ import annotations

import base64
import io
import logging
import re
from typing import Any, Optional

from PIL import Image, ImageOps

from phx_sync.config import Settings, get_settings
from phx_sync.db import get_azure_connection, get_mysql_connection

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("sync_catalogs")

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

_OPERATIONS_PATTERN = r"MOTOWIN|POSTVENTA|TRASLADO|REPARACION|INSTALACION|MANTENIMIENTO"
_CONTRACTORS_PATTERN = (
    r"ALFITELL|AP&TELL|AVANTEL|BIO|BMP|CESPEDES|COBRA|COMFICA|DATANTENNA|"
    r"DIGETEL|DOMINION|EJAS|GRUPO\s+JBA|JBF(?:\s+TELCO)?|KAJOMI|LP\s+STAR|"
    r"M&D|MALLAUSA|MEBRA|OLMA|SISCARD|SUMMUM|TADELC|TLI|VISUAL|YEVIZACOM|"
    r"ZARITEL|LARI|EZENTIS|GLOBAL|WIN|ALL\s+TELECOM|ANOVO|ONI"
)


def clean_cuadrilla_name(raw: Optional[str]) -> str:
    if not raw:
        return ""
    text = raw.strip()

    # 1. Primary rule: trim everything up to and including the last SGA, SGI, or SGM token
    if re.search(r"\b(?:SGA|SGI|SGM)\b", text, flags=re.IGNORECASE):
        cleaned = re.sub(r"^.*\b(?:SGA|SGI|SGM)\b\s*", "", text, flags=re.IGNORECASE).strip()
        cleaned = re.sub(r"\s+K\d+\s+.*$", "", cleaned, flags=re.IGNORECASE).strip()
        if cleaned:
            return cleaned

    # 2. Strip leading crew prefixes like "DP 14 ", "P 3 ", "K 11 ", "BAJA FR "
    text = re.sub(r"^(?:BAJA\s+FR|BAJA|ALTA|[A-Z]{1,3}\s*\d+)\s+", "", text, flags=re.IGNORECASE)

    # 3. Strip operational modality prefixes (up to two consecutive tokens)
    text = re.sub(rf"^(?:{_OPERATIONS_PATTERN})\s+", "", text, flags=re.IGNORECASE)
    text = re.sub(rf"^(?:{_OPERATIONS_PATTERN})\s+", "", text, flags=re.IGNORECASE)

    # 4. Strip known contractor prefixes and handle reversed operation/contractor ordering
    text = re.sub(rf"^(?:{_CONTRACTORS_PATTERN})\s+", "", text, flags=re.IGNORECASE)
    text = re.sub(rf"^(?:{_OPERATIONS_PATTERN})\s+", "", text, flags=re.IGNORECASE)
    text = re.sub(rf"^(?:{_CONTRACTORS_PATTERN})\s+", "", text, flags=re.IGNORECASE)

    # 5. Strip trailing suffixes like "K13 KAJOMI", "K3 VISUAL"
    text = re.sub(r"\s+K\d+\s+.*$", "", text, flags=re.IGNORECASE)

    return text.strip()


def convert_bytes_to_img_data_uri(foto_bytes: Any) -> Optional[str]:
    if not foto_bytes or not isinstance(foto_bytes, (bytes, bytearray)):
        return None
    try:
        img = Image.open(io.BytesIO(foto_bytes))
        try:
            img = ImageOps.exif_transpose(img)
        except Exception:
            pass
        if img.mode != "RGB":
            img = img.convert("RGB")
        img.thumbnail((600, 600), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=82, optimize=True)
        b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
        return f"data:image/jpeg;base64,{b64}"
    except Exception:
        return None


def sync_direct_query(settings: Optional[Settings] = None) -> None:
    cfg = settings or get_settings()
    azure_conn = None
    mysql_conn = None
    try:
        logger.info("Extracting technician catalog from Azure SQL...")
        azure_conn = get_azure_connection(cfg)
        with azure_conn.cursor() as cursor_az:
            cursor_az.execute(QUERY_AZURE)
            columns = [col[0] for col in cursor_az.description]
            rows = cursor_az.fetchall()
        logger.info("Extracted %s records from Azure.", len(rows))

        mysql_conn = get_mysql_connection(cfg, autocommit=False)
        with mysql_conn.cursor() as cursor_my:
            cursor_my.execute(
                f"""
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
                """
            )

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

            cursor_my.execute(
                f"SELECT Documento, Desaprueba_img FROM `{TABLE_NAME}` "
                "WHERE Documento IS NOT NULL AND Documento <> ''"
            )
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

            batch_updates = []
            batch_inserts = []
            synced_docs = set()

            for row in rows:
                row_dict = {col: row[i] for i, col in enumerate(columns)}
                cuadrilla_val = row_dict.get("Cuadrilla") or ""
                doc_val = str(row_dict.get("Documento") or "").strip()[:50]
                estado_val = row_dict.get("Estado") or "Activo"
                disapproved_val = existing_records.get(doc_val)

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
                    batch_updates.append((*record_values, doc_val))
                else:
                    batch_inserts.append((*record_values, doc_val))
                    if doc_val:
                        existing_records[doc_val] = None

                if doc_val:
                    synced_docs.add(doc_val)

            batch_size = 500
            for i in range(0, len(batch_updates), batch_size):
                cursor_my.executemany(update_sql, batch_updates[i : i + batch_size])
            for i in range(0, len(batch_inserts), batch_size):
                cursor_my.executemany(insert_sql, batch_inserts[i : i + batch_size])

            missing_docs = [d for d in (set(existing_records.keys()) - synced_docs) if d and str(d).strip()]
            marked_baja = 0
            if missing_docs:
                for i in range(0, len(missing_docs), batch_size):
                    chunk = missing_docs[i : i + batch_size]
                    placeholders = ",".join(["%s"] * len(chunk))
                    cursor_my.execute(
                        f"UPDATE `{TABLE_NAME}` SET Estado = 'Baja' "
                        f"WHERE Documento IN ({placeholders}) AND (Estado <> 'Baja' OR Estado IS NULL)",
                        tuple(chunk),
                    )
                    marked_baja += cursor_my.rowcount

            mysql_conn.commit()
            logger.info(
                "Catalog updated: %s inserted, %s updated, %s marked inactive.",
                len(batch_inserts),
                len(batch_updates),
                marked_baja,
            )
    except Exception as exc:
        if mysql_conn:
            try:
                mysql_conn.rollback()
            except Exception:
                pass
        logger.error("Catalog sync error: %s", exc, exc_info=True)
        raise
    finally:
        if azure_conn:
            try:
                azure_conn.close()
            except Exception:
                pass
        if mysql_conn:
            try:
                mysql_conn.close()
            except Exception:
                pass
