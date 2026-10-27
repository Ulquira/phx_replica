import time
import logging
from datetime import datetime
from sync_catalogs import sync_direct_query
from enhance_photos_batch import process_all_photos

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("sync_tecnicos.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("auto_sync_tecnicos")

INTERVAL_SECONDS = 1800  # 30 minutos

def run_pipeline():
    logger.info("=== INICIANDO SINCRONIZACIÓN DE TÉCNICOS Y FOTOS ===")
    try:
        logger.info("[1/2] Extrayendo catálogos y fotos desde Azure SQL...")
        sync_direct_query()
        logger.info("[1/2] Extracción completada.")
    except Exception as exc:
        logger.error(f"[1/2] Error en sync_catalogs: {exc}", exc_info=True)

    try:
        logger.info("[2/2] Procesando fotos pendientes con IA Gemini...")
        process_all_photos()
        logger.info("[2/2] Procesamiento de fotos completado.")
    except Exception as exc:
        logger.error(f"[2/2] Error en enhance_photos_batch: {exc}", exc_info=True)
    
    logger.info(f"=== CICLO FINALIZADO. Próxima ejecución en {INTERVAL_SECONDS // 60} minutos ===")

def main():
    logger.info(f"Demonio de actualización de técnicos iniciado (Frecuencia: cada {INTERVAL_SECONDS // 60} min)")
    while True:
        start_time = time.time()
        run_pipeline()
        elapsed = time.time() - start_time
        sleep_time = max(0, INTERVAL_SECONDS - elapsed)
        
        try:
            time.sleep(sleep_time)
        except KeyboardInterrupt:
            logger.info("Servicio detenido manualmente.")
            break

if __name__ == "__main__":
    main()
