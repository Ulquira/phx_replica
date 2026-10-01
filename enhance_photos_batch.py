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

# Prompt de recorte y limpieza de fondo sin filtros de belleza ni alteraciones faciales/corporales
ENHANCE_PROMPT = """
Task: Clean background removal and portrait framing ONLY.

STRICT RULES (ZERO BEAUTY FILTER / ZERO AI GENERATION):

1. ABSOLUTE FACIAL PRESERVATION (100% UNTOUCHED):
- DO NOT apply any beauty filter, skin smoothing, makeup effect, or facial retouching.
- Keep the exact original face, beard, facial hair, skin pores, wrinkles, blemishes, eyes, lips, and natural skin tone 100% untouched and identical to the original photo.
- DO NOT alter, rejuvenate, soften, or redraw any part of the face.

2. BACKGROUND REPLACEMENT ONLY:
- Replace the entire background behind the person with a solid, clean, seamless pure white background (#FFFFFF).
- Do not leave background shadows, objects, walls, or borders.

3. FRAMING & ORIENTATION:
- Ensure the person is perfectly upright and vertical (head/helmet on top, chest/shoulders at the bottom).
- Frame strictly as a close-up ID portrait from mid-chest up to the top of the helmet/head.
- DO NOT show legs, waist, or full body. Center the subject.

4. ABSOLUTE ZERO CLOTHING / GEAR MODIFICATION:
- Keep the EXACT same original clothing, fabric, vest, shirt, helmet, straps, logos, badges, and lanyards.
- DO NOT modify, redraw, recolor, or replace any part of the uniform or gear.
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
                    img = Image.open(io.BytesIO(enhanced_bytes))
                    if img.mode != 'RGB':
                        img = img.convert('RGB')
                    img.thumbnail((600, 600), Image.Resampling.LANCZOS)
                    buf = io.BytesIO()
                    img.save(buf, format='JPEG', quality=82, optimize=True)
                    b64_res = base64.b64encode(buf.getvalue()).decode('utf-8')
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
