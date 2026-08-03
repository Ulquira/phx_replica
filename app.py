from sync_azure_mysql_local import sync_once

if __name__ == "__main__":
    try:
        sync_once()
    except Exception as exc:
        print(f"Error en el proceso de sincronización: {exc}", flush=True)
        raise
