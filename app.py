import sys

from phx_sync.sync.work_orders import sync_once

if __name__ == "__main__":
    try:
        days = int(sys.argv[1]) if len(sys.argv) > 1 else None
        sync_once(lookback_days=days)
    except Exception as exc:
        print(f"Sync process failed: {exc}", flush=True)
        raise
