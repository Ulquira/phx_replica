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
GEMINI_IMAGE_MODEL = os.getenv("GEMINI_IMAGE_MODEL", "gemini-2.5-flash-image")

# Conexión MySQL de Producción
MYSQL_HOST = os.getenv("MYSQL_HOST")
MYSQL_PORT = int(os.getenv("MYSQL_PORT", "3306"))
MYSQL_DATABASE = os.getenv("MYSQL_DATABASE")
MYSQL_USER = os.getenv("MYSQL_USER")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD")

# Prompt estructurado para retrato vertical estricto de cabeza a pecho con fondo blanco
ENHANCE_PROMPT = """
Task: Professional corporate technical ID portrait framing and background cleanup.

CRITICAL INSTRUCTIONS:

1. FRAMING & CROPPING (MOST IMPORTANT):
- Crop the image tightly to a chest-up portrait (head, safety helmet, face, neck, and upper chest/shoulders only).
- If the original photo is long, tall, 3/4 body, or full body, SHORTEN AND CROP IT TIGHTLY from mid-chest up to just above the top of the helmet.
- Completely CUT OFF and DO NOT SHOW the waist, belt, hips, legs, or lower body.
- Center the person horizontally and vertically, matching a standard corporate ID card photo.

2. ORIENTATION & STRAIGHTENING:
- Ensure the person is perfectly upright and vertical.
- Head and helmet MUST be at the top, shoulders and chest at the bottom.
- Straighten the subject if tilted or angled.

3. BACKGROUND REPLACEMENT:
- Replace the entire background behind the person with a solid, seamless, pure clean white background (#FFFFFF).
- No background shadows, no objects, no clutter, no walls, no borders.

4. ABSOLUTE ZERO FACE OR BODY ALTERATION:
- DO NOT change the person's face, facial features, eyes, nose, mouth, skin tone, beard, wrinkles, or expression.
- DO NOT apply aggressive beauty filters, plastic skin smoothing, or makeup effects.
- DO NOT alter the body shape or posture.
- Keep the real person 100% authentic, natural, and recognizable.

5. ABSOLUTE ZERO CLOTHING OR GEAR MODIFICATION:
- Keep the EXACT same uniform, fabric, orange vest, helmet, shirt, badges, lanyard, and logos exactly as shown in the original photo.
- DO NOT replace, redraw, or recolor the clothes, helmet, or vest.
- Only apply minimal subtle lighting and clarity cleanup to make the photo look neat and professional on white background.
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

def enhance_single_image(raw_b64: str, max_retries: int = 3) -> str | None:
    try:
        if "," in raw_b64:
            clean_b64 = raw_b64.split(",", 1)[1]
        else:
            clean_b64 = raw_b64

        foto_bytes = base64.b64decode(clean_b64)
        input_image = Image.open(io.BytesIO(foto_bytes))
        input_image = fix_image_orientation(input_image)

        response = None
        for attempt in range(max_retries):
            try:
                response = client.models.generate_content(
                    model=GEMINI_IMAGE_MODEL,
                    contents=[input_image, ENHANCE_PROMPT],
                )
                break
            except Exception as api_err:
                err_msg = str(api_err)
                if ("429" in err_msg or "ResourceExhausted" in err_msg) and attempt < max_retries - 1:
                    wait_sec = (attempt + 1) * 3
                    logger.warning(f"Límite de tasa alcanzado. Reintentando en {wait_sec}s...")
                    time.sleep(wait_sec)
                    continue
                logger.error(f"Error en API Gemini ({GEMINI_IMAGE_MODEL}): {api_err}")
                return None

        if not response or not response.candidates:
            return None

        for candidate in response.candidates:
            if not candidate.content or not candidate.content.parts:
                continue
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
        logger.error(f"Error procesando imagen: {exc}")
        return None

def process_all_photos():
    BATCH_SIZE = int(os.getenv("PHOTO_BATCH_SIZE", "50"))
    conn = None
    try:
        conn = get_mysql_conn()
        ensure_column_exists(conn)

        total_processed = 0
        success_count = 0

        with conn.cursor(dictionary=True) as cursor, conn.cursor() as cursor_upd:
            while True:
                cursor.execute("""
                    SELECT Documento, Nombre_Tecnico_Limpio, Foto_Img 
                    FROM vw_info_cuadrillas 
                    WHERE Foto_Img IS NOT NULL AND Foto_Img LIKE 'data:image/%' 
                      AND (Img_mejorada IS NULL OR Img_mejorada = '')
                      AND (Desaprueba_img IS NULL OR Desaprueba_img NOT LIKE '%DESAPROB%')
                      AND (Estado IS NULL OR Estado <> 'Baja')
                    LIMIT %s
                """, (BATCH_SIZE,))
                rows = cursor.fetchall()
                if not rows:
                    break

                logger.info(f"Lote de {len(rows)} fotos pendientes obtenido para procesar...")
                for idx, r in enumerate(rows, 1):
                    total_processed += 1
                    doc = r["Documento"]
                    nombre = r["Nombre_Tecnico_Limpio"]
                    foto_b64 = r["Foto_Img"]

                    logger.info(f"[{total_processed}] Procesando foto de: {nombre} ({doc})...")
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

                    time.sleep(1.5)

        logger.info(f"=== Procesamiento completado: {success_count}/{total_processed} fotos mejoradas ===")
    except Exception as exc:
        logger.error(f"Error durante el procesamiento de fotos: {exc}", exc_info=True)
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass

if __name__ == "__main__":
    process_all_photos()
