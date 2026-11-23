from __future__ import annotations

import base64
import io
import logging
import time
from typing import Any, Callable, Optional, Set, Tuple

from google import genai
from PIL import Image, ImageOps

from phx_sync.config import Settings, get_settings
from phx_sync.db import get_mysql_connection

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("enhance_photos")

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


def get_gemini_client(settings: Optional[Settings] = None) -> genai.Client:
    cfg = settings or get_settings()
    api_key = cfg.require_gemini()
    return genai.Client(api_key=api_key)


def fix_image_orientation(image: Image.Image) -> Image.Image:
    """Apply EXIF orientation and rotate landscape photos to portrait."""
    try:
        image = ImageOps.exif_transpose(image)
    except Exception:
        pass
    if image.width > image.height:
        image = image.rotate(270, expand=True)
    return image


def ensure_column_exists(conn: Any) -> None:
    with conn.cursor() as cursor:
        cursor.execute("SHOW COLUMNS FROM `vw_info_cuadrillas` LIKE 'Img_mejorada'")
        if not cursor.fetchone():
            logger.info("Adding `Img_mejorada` column to `vw_info_cuadrillas`...")
            cursor.execute(
                "ALTER TABLE `vw_info_cuadrillas` ADD COLUMN `Img_mejorada` LONGTEXT NULL AFTER `Foto_Img`"
            )


def enhance_single_image(
    raw_b64: str,
    *,
    client: Optional[genai.Client] = None,
    model: Optional[str] = None,
    max_retries: int = 3,
    settings: Optional[Settings] = None,
) -> Optional[str]:
    cfg = settings or get_settings()
    gemini_client = client or get_gemini_client(cfg)
    model_name = model or cfg.gemini_image_model

    try:
        clean_b64 = raw_b64.split(",", 1)[1] if "," in raw_b64 else raw_b64
        foto_bytes = base64.b64decode(clean_b64)
        input_image = fix_image_orientation(Image.open(io.BytesIO(foto_bytes)))

        response = None
        for attempt in range(max_retries):
            try:
                response = gemini_client.models.generate_content(
                    model=model_name,
                    contents=[input_image, ENHANCE_PROMPT],
                )
                break
            except Exception as api_err:
                err_msg = str(api_err)
                if ("429" in err_msg or "ResourceExhausted" in err_msg) and attempt < max_retries - 1:
                    wait_sec = (attempt + 1) * 3
                    logger.warning("Rate limit reached. Retrying in %ss...", wait_sec)
                    time.sleep(wait_sec)
                    continue
                logger.error("Gemini API error (%s): %s", model_name, api_err)
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
                    if img.mode != "RGB":
                        img = img.convert("RGB")
                    img.thumbnail((600, 600), Image.Resampling.LANCZOS)
                    buf = io.BytesIO()
                    img.save(buf, format="JPEG", quality=82, optimize=True)
                    b64_res = base64.b64encode(buf.getvalue()).decode("utf-8")
                    return f"data:image/jpeg;base64,{b64_res}"
        return None
    except Exception as exc:
        logger.error("Error processing image: %s", exc)
        return None


def _build_pending_photos_query(failed_docs: Set[str], batch_size: int) -> Tuple[str, Tuple[Any, ...]]:
    base_sql = """
        SELECT Documento, Nombre_Tecnico_Limpio, Foto_Img
        FROM vw_info_cuadrillas
        WHERE Documento IS NOT NULL AND Documento <> ''
          AND Foto_Img IS NOT NULL AND Foto_Img LIKE 'data:image/%'
          AND (Img_mejorada IS NULL OR Img_mejorada = '')
          AND (Desaprueba_img IS NULL OR Desaprueba_img NOT LIKE '%DESAPROB%')
          AND (Estado IS NULL OR Estado <> 'Baja')
    """
    params: list[Any] = []
    if failed_docs:
        sorted_failed = sorted(failed_docs)
        placeholders = ", ".join(["%s"] * len(sorted_failed))
        base_sql += f" AND Documento NOT IN ({placeholders})"
        params.extend(sorted_failed)
    base_sql += " LIMIT %s"
    params.append(batch_size)
    return base_sql, tuple(params)


def process_all_photos(
    *,
    settings: Optional[Settings] = None,
    conn: Any = None,
    enhancer_fn: Optional[Callable[[str], Optional[str]]] = None,
    sleep_sec: float = 1.5,
) -> Tuple[int, int, int]:
    """Process pending technician photos in bounded batches without retrying failed rows forever.

    Returns (total_processed, success_count, failed_count).
    """
    cfg = settings or get_settings()
    owns_conn = conn is None
    total_processed = 0
    success_count = 0
    failed_docs: Set[str] = set()

    if enhancer_fn is None:
        gemini_client = get_gemini_client(cfg)
        enhancer_fn = lambda raw: enhance_single_image(
            raw, client=gemini_client, model=cfg.gemini_image_model, settings=cfg
        )

    try:
        if owns_conn:
            conn = get_mysql_connection(cfg, autocommit=True, timeout=20)
        ensure_column_exists(conn)

        with conn.cursor(dictionary=True) as cursor, conn.cursor() as cursor_upd:
            for batch_num in range(1, cfg.photo_max_batches + 1):
                query, params = _build_pending_photos_query(failed_docs, cfg.photo_batch_size)
                cursor.execute(query, params)
                rows = cursor.fetchall()
                if not rows:
                    break

                logger.info("Batch %s: fetched %s pending photos...", batch_num, len(rows))
                progressed = False

                for row in rows:
                    doc = str(row.get("Documento") or "").strip()
                    if not doc or doc in failed_docs:
                        continue

                    total_processed += 1
                    nombre = row.get("Nombre_Tecnico_Limpio") or "UNKNOWN"
                    foto_b64 = row.get("Foto_Img") or ""

                    logger.info("[%s] Processing photo for %s (%s)...", total_processed, nombre, doc)
                    enhanced_data_uri = enhancer_fn(foto_b64)

                    if enhanced_data_uri:
                        cursor_upd.execute(
                            "UPDATE `vw_info_cuadrillas` SET `Img_mejorada` = %s WHERE `Documento` = %s",
                            (enhanced_data_uri, doc),
                        )
                        success_count += 1
                        progressed = True
                        logger.info(" -> [OK] Saved enhanced photo for %s", nombre)
                    else:
                        failed_docs.add(doc)
                        logger.warning(" -> [FAIL] Skipped for remainder of run: %s (%s)", nombre, doc)

                    if sleep_sec > 0:
                        time.sleep(sleep_sec)

                if not progressed and len(rows) < cfg.photo_batch_size:
                    break

        logger.info(
            "=== Photo processing complete: %s/%s enhanced (%s failed/skipped) ===",
            success_count,
            total_processed,
            len(failed_docs),
        )
        return total_processed, success_count, len(failed_docs)
    except Exception as exc:
        logger.error("Error during photo processing: %s", exc, exc_info=True)
        raise
    finally:
        if owns_conn and conn is not None:
            try:
                conn.close()
            except Exception:
                pass
