import os
import io
import time
import base64
import logging
from PIL import Image, ImageOps
import mysql.connector
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("enhance_photos")

# Configuración API Gemini
api_key = os.getenv("GEMINI_API_KEY")
if not api_key:
    raise RuntimeError("GEMINI_API_KEY no está configurada. Añádela en el entorno o en .env con una clave válida de Google AI Studio.")
client = genai.Client(api_key=api_key)

# Conexión MySQL de Producción
MYSQL_HOST = os.getenv("MYSQL_HOST", "phx-win-mysql-9508.mysql.database.azure.com")
MYSQL_PORT = int(os.getenv("MYSQL_PORT", "3306"))
MYSQL_DATABASE = os.getenv("MYSQL_DATABASE", "BD_Phoenix")
MYSQL_USER = os.getenv("MYSQL_USER", "phxadmin")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "WinTelecom@2026!")

# Prompt estructurado para retrato vertical estricto de cabeza a pecho con fondo blanco
ENHANCE_PROMPT = """
Strict corporate technician ID portrait guidelines:

1. ORIENTATION (CRITICAL):
- Ensure the person is perfectly vertical and upright. Head and helmet MUST be at the top, chest/shoulders at the bottom.

2. FRAMING & CLOSE-UP CROPPING:
- Frame strictly as a tight head-and-chest portrait (from mid-chest up to top of the helmet).
- DO NOT show full body, waist, belt, or legs. Cut off below the chest.
- Center the face and helmet in a balanced vertical or square portrait.

3. PRESERVE IDENTITY & ATTIRE 100%:
- Keep the EXACT same person, face, facial features, skin tone, and expression.
- Keep the EXACT original orange WIN vest, safety helmet, company lanyard/badge, gray long sleeves, and contractor logos (WIN, DIGETEL, MALLAUSA, etc.).
- Do NOT generate random clothing. Clean minor surface dirt on the existing gear.

4. BACKGROUND:
- Solid, seamless pure white background (#FFFFFF) with no shadows, no corners, no walls.

5. OUTPUT:
- Professional, sharp, realistic corporate field technician portrait.
"""

def fix_image_orientation(image: Image.Image) -> Image.Image:
    """Corrige la orientación EXIF y rota a vertical si la foto original fue tomada acostada."""
    try:
        image = ImageOps.exif_transpose(image)
    except Exception:
        pass
    
    # Si la imagen es horizontal (ancho > alto), se toma típicamente con el móvil acostado hacia la izquierda
    if image.width > image.height:
        image = image.rotate(270, expand=True)
    return image

def get_mysql_conn():
    return mysql.connector.connect(
        host=MYSQL_HOST,
        port=MYSQL_PORT,
        user=MYSQL_USER,
        password=MYSQL_PASSWORD,
        database=MYSQL_DATABASE,
        autocommit=True,
        ssl_disabled=True,
        connection_timeout=20
    )

def ensure_column_exists(conn):
    cursor = conn.cursor()
    cursor.execute("SHOW COLUMNS FROM `vw_info_cuadrillas` LIKE 'Img_mejorada'")
    if not cursor.fetchone():
        logger.info("Agregando columna `Img_mejorada` a `vw_info_cuadrillas`...")
        cursor.execute("ALTER TABLE `vw_info_cuadrillas` ADD COLUMN `Img_mejorada` LONGTEXT NULL AFTER `Foto_Img`")
        logger.info("Columna `Img_mejorada` creada con éxito.")
    cursor.close()

def enhance_single_image(raw_b64: str) -> str | None:
    try:
        if "," in raw_b64:
            clean_b64 = raw_b64.split(",", 1)[1]
        else:
            clean_b64 = raw_b64

        foto_bytes = base64.b64decode(clean_b64)
        input_image = Image.open(io.BytesIO(foto_bytes))
        input_image = fix_image_orientation(input_image)

        response = client.models.generate_content(
            model='gemini-2.5-flash-image',
            contents=[input_image, ENHANCE_PROMPT],
        )

        for candidate in response.candidates:
            for part in candidate.content.parts:
                if part.inline_data:
                    enhanced_bytes = part.inline_data.data
                    b64_res = base64.b64encode(enhanced_bytes).decode('utf-8')
                    return f"data:image/jpeg;base64,{b64_res}"
        return None
    except Exception as exc:
        logger.error(f"Error procesando imagen con Gemini: {exc}")
        return None

def process_all_photos():
    conn = get_mysql_conn()
    ensure_column_exists(conn)
    
    cursor = conn.cursor(dictionary=True)
    cursor.execute("""
        SELECT Documento, Nombre_Tecnico_Limpio, Foto_Img 
        FROM vw_info_cuadrillas 
        WHERE Foto_Img IS NOT NULL AND Foto_Img <> '' 
          AND (Img_mejorada IS NULL OR Img_mejorada = '')
    """)
    rows = cursor.fetchall()
    logger.info(f"Se encontraron {len(rows)} fotos pendientes por procesar con IA.")

    cursor_upd = conn.cursor()
    success_count = 0

    for idx, r in enumerate(rows, 1):
        doc = r["Documento"]
        nombre = r["Nombre_Tecnico_Limpio"]
        foto_b64 = r["Foto_Img"]

        logger.info(f"[{idx}/{len(rows)}] Procesando foto de: {nombre} ({doc})...")
        enhanced_data_uri = enhance_single_image(foto_b64)

        if enhanced_data_uri:
            cursor_upd.execute(
                "UPDATE `vw_info_cuadrillas` SET `Img_mejorada` = %s WHERE `Documento` = %s",
                (enhanced_data_uri, doc)
            )
            success_count += 1
            logger.info(f" -> [OK] Guardada imagen mejorada para {nombre}")
        else:
            logger.warning(f" -> [FAIL] No se pudo generar la imagen para {nombre}")

        # Pequeña pausa para no saturar la cuota de rate limit por minuto
        time.sleep(1.5)

    cursor.close()
    cursor_upd.close()
    conn.close()
    logger.info(f"=== Procesamiento completado: {success_count}/{len(rows)} fotos mejoradas ===")

if __name__ == "__main__":
    process_all_photos()
