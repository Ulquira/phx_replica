"""CLI entrypoint for retroactive work orders backfill."""

import sys
from datetime import datetime, timedelta

from phx_sync.sync.work_orders import build_date_chunks, fetch_backfill_rows, run_backfill

__all__ = ["build_date_chunks", "fetch_backfill_rows", "run_backfill"]

if __name__ == "__main__":
    default_date = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    target_start = sys.argv[1] if len(sys.argv) > 1 else default_date
    target_end = sys.argv[2] if len(sys.argv) > 2 else None
    run_backfill(target_start, target_end)
