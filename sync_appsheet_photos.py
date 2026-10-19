import os
import base64
import logging
from pathlib import Path
import mysql.connector
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("sync_appsheet_photos")

# Configuración de base de datos
MYSQL_HOST = os.getenv("MYSQL_HOST")
MYSQL_PORT = int(os.getenv("MYSQL_PORT", "3306"))
MYSQL_DATABASE = os.getenv("MYSQL_DATABASE")
MYSQL_USER = os.getenv("MYSQL_USER")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD")

# Directorio base del script
BASE_DIR = Path(__file__).resolve().parent

def find_images_directory() -> Path | None:
    """Detecta automáticamente cualquier carpeta descargada de AppSheet."""
    # 1. Buscar coincidencia exacta o con prefijo en el directorio local
    for entry in BASE_DIR.iterdir():
        if entry.is_dir() and "vw_info_cuadrillas_Images" in entry.name:
            sub = entry / "vw_info_cuadrillas_Images"
            return sub if sub.exists() else entry
    
    # 2. Carpeta fija de AppSheet si se descomprime directamente
    fixed = BASE_DIR / "vw_info_cuadrillas_Images"
    if fixed.exists():
        return fixed

    return None

def sync_photos_to_base64():
    img_dir = find_images_directory()
    if not img_dir or not img_dir.exists():
        logger.error(
            f"No se encontró la carpeta de imágenes en {BASE_DIR}. "
            "Asegúrate de colocar la carpeta descargada de AppSheet (vw_info_cuadrillas_Images...) aquí."
        )
        return

    logger.info(f"Carpeta de imágenes localizada: {img_dir}")
    
    # Mapeo rápido de nombres en minúsculas -> ruta del archivo
    disk_files = {f.name.lower(): f for f in img_dir.rglob("*.jpg")}
    logger.info(f"Archivos .jpg detectados en disco: {len(disk_files)}")

    conn = mysql.connector.connect(
        host=MYSQL_HOST,
        port=MYSQL_PORT,
        user=MYSQL_USER,
        password=MYSQL_PASSWORD,
        database=MYSQL_DATABASE,
        autocommit=True
    )
    cur = conn.cursor(dictionary=True)

    # 1. Buscar filas que tengan ruta en Img_mejorada
    cur.execute("""
        SELECT Documento, Nombre_Tecnico_Limpio, Img_mejorada
        FROM vw_info_cuadrillas
        WHERE Img_mejorada LIKE 'vw_info_cuadrillas_Images/%'
    """)
    rows_mejorada = cur.fetchall()

    # 2. Buscar filas que tengan ruta en Foto_Img (original)
    cur.execute("""
        SELECT Documento, Nombre_Tecnico_Limpio, Foto_Img
        FROM vw_info_cuadrillas
        WHERE Foto_Img LIKE 'vw_info_cuadrillas_Images/%'
    """)
    rows_original = cur.fetchall()

    logger.info(f"Rutas pendientes detectadas en MySQL -> Img_mejorada: {len(rows_mejorada)} | Foto_Img: {len(rows_original)}")

    if not rows_mejorada and not rows_original:
        logger.info("Todas las fotos ya están en formato Base64. No se requieren cambios.")
        cur.close()
        conn.close()
        return

    update_cur = conn.cursor()
    updated_mejorada = 0
    updated_original = 0
    missing_files = 0

    # Procesar Img_mejorada
    for r in rows_mejorada:
        path_val = r["Img_mejorada"]
        filename = Path(path_val).name.lower()

        if filename in disk_files:
            with open(disk_files[filename], "rb") as f_img:
                b64 = base64.b64encode(f_img.read()).decode("utf-8")
            data_uri = f"data:image/jpeg;base64,{b64}"
            update_cur.execute(
                "UPDATE `vw_info_cuadrillas` SET `Img_mejorada` = %s WHERE `Img_mejorada` = %s",
                (data_uri, path_val)
            )
            updated_mejorada += update_cur.rowcount
        else:
            missing_files += 1
            logger.warning(f"[Img_mejorada no encontrada] {filename} para {r['Nombre_Tecnico_Limpio']} ({r['Documento']})")

    # Procesar Foto_Img
    for r in rows_original:
        path_val = r["Foto_Img"]
        filename = Path(path_val).name.lower()

        if filename in disk_files:
            with open(disk_files[filename], "rb") as f_img:
                b64 = base64.b64encode(f_img.read()).decode("utf-8")
            data_uri = f"data:image/jpeg;base64,{b64}"
            update_cur.execute(
                "UPDATE `vw_info_cuadrillas` SET `Foto_Img` = %s WHERE `Foto_Img` = %s",
                (data_uri, path_val)
            )
            updated_original += update_cur.rowcount
        else:
            missing_files += 1
            logger.warning(f"[Foto_Img no encontrada] {filename} para {r['Nombre_Tecnico_Limpio']} ({r['Documento']})")

    update_cur.close()
    cur.close()
    conn.close()

    logger.info("=" * 60)
    logger.info("RESUMEN DE SINCRONIZACIÓN:")
    logger.info(f" - Img_mejorada convertidas a Base64: {updated_mejorada}")
    logger.info(f" - Foto_Img convertidas a Base64:    {updated_original}")
    logger.info(f" - Archivos no encontrados en disco:  {missing_files}")
    logger.info("=" * 60)

if __name__ == "__main__":
    sync_photos_to_base64()
