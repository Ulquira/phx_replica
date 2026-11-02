import sys
from sync_azure_mysql_local import sync_once

if __name__ == "__main__":
    try:
        days = int(sys.argv[1]) if len(sys.argv) > 1 else None
        sync_once(lookback_days=days)
    except Exception as exc:
        print(f"Error en el proceso de sincronización: {exc}", flush=True)
        raise
