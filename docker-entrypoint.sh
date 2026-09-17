#!/bin/sh
set -e

: "Starting Azure Container App entrypoint"

if [ -z "$SYNC_INTERVAL" ]; then
  SYNC_INTERVAL=60
fi

echo "==== INICIANDO SINCRONIZACION DE CATALOGOS ===="
python -u sync_catalogs.py || echo "Advertencia: Fallo en catálogos, pero continuando..."

echo "==== INICIANDO RECUPERACION RETROACTIVA (BACKFILL) ===="
python -u backfill_sync.py || echo "Advertencia: Fallo en backfill, pero continuando..."

echo "==== INICIANDO MEJORA DE FOTOS CON IA (GEMINI) ===="
python -u enhance_photos_batch.py || echo "Advertencia: Fallo en mejora de fotos con IA, pero continuando..."

CYCLE_COUNT=0

while true; do
  echo "==== SYNC START: $(date -u +'%Y-%m-%dT%H:%M:%SZ') ===="
  python -u app.py
  echo "==== SYNC END: $(date -u +'%Y-%m-%dT%H:%M:%SZ') ===="

  CYCLE_COUNT=$((CYCLE_COUNT + 1))
  # Cada 30 ciclos (~30 min), refrescar catálogo de cuadrillas y procesar fotos nuevas con IA
  if [ "$CYCLE_COUNT" -ge 30 ]; then
    echo "==== CICLO PROGRAMADO: REFRESCANDO CATALOGO Y FOTOS PENDIENTES ===="
    python -u sync_catalogs.py || echo "Advertencia: Fallo en sincronización periódica de catálogos..."
    python -u enhance_photos_batch.py || echo "Advertencia: Fallo en procesamiento de fotos pendientes..."
    CYCLE_COUNT=0
  fi

  echo "Waiting $SYNC_INTERVAL seconds until next sync..."
  sleep "$SYNC_INTERVAL"
done
