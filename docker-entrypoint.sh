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

while true; do
  echo "==== SYNC START: $(date -u +'%Y-%m-%dT%H:%M:%SZ') ===="
  python -u app.py
  echo "==== SYNC END: $(date -u +'%Y-%m-%dT%H:%M:%SZ') ===="
  echo "Waiting $SYNC_INTERVAL seconds until next sync..."
  sleep "$SYNC_INTERVAL"
done
