"""CLI entrypoint for technician catalog synchronization."""

from phx_sync.sync.catalogs import (
    TABLE_NAME,
    clean_cuadrilla_name,
    convert_bytes_to_img_data_uri,
    sync_direct_query,
)

__all__ = [
    "TABLE_NAME",
    "clean_cuadrilla_name",
    "convert_bytes_to_img_data_uri",
    "sync_direct_query",
]

if __name__ == "__main__":
    sync_direct_query()
