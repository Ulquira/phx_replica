import os
import io
import time
import base64
import logging
from PIL import Image
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

# Prompt estructurado según GUÍA DE FOTOGRAFÍA WIN EN RUTA:
# 1. Conservar rostro, facciones e identidad exacta del técnico real.
# 2. Mantener uniforme, casco y accesorios reales del técnico sin cambios.
# 3. Fondo blanco puro y uniforme (sin sombras ni objetos).
# 4. Iluminación frontal, limpieza mínima y mejor composición.
# 5. Encuadre más ajustado, de frente, cabeza y torso.
ENHANCE_PROMPT = """
You are retouching a corporate work portrait. Preserve the person exactly as photographed.

STRICT RULES:
1. FACE AND IDENTITY: Keep the exact same face, eyes, skin tone, expression, hairstyle, and identity as in the original image. Do not change the person, do not replace the face, do not smooth it into a different person.
2. CLOTHING AND EQUIPMENT: Keep the exact same work uniform, helmet, vest, shirt, badge, lanyard, logos, and accessories shown in the photo. Do not change the uniform, do not create a different shirt or color, do not remove the helmet if present. Only do minimal cleanup on the existing clothes and helmet.
3. BACKGROUND: Replace the background with a seamless pure white background (#FFFFFF) with no shadows, no clutter, no visible distractions.
4. FRAMING: Crop tighter to a clean head-and-chest portrait, centered and straightened gently if needed. Keep it close to the original pose and body position, not a full-body shot.
5. LIGHTING AND CLEANUP: Improve clarity slightly, remove small dust, stains, and minor blemishes, but keep the overall look realistic and natural. No stylization, no cartoon effect, no face sculpting, no body reshaping.
6. REALISM OVER STYLE: The result should look like a lightly cleaned up original corporate portrait, not a new generated outfit or different person.
"""

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
