from __future__ import annotations

import re
from typing import Optional

import mysql.connector
import pyodbc

from phx_sync.config import Settings, get_settings

WORK_ORDERS_BASE_QUERY = """
    SELECT t.*,
           CASE
               WHEN t.Estado = 'En camino'
               THEN CAST(p.Latitud AS VARCHAR(50)) + ',' + CAST(p.Longitud AS VARCHAR(50))
               ELSE NULL
           END AS Georeferencia_tecnico
    FROM [dbo].[VW_WinOrdeTraba] t
    LEFT JOIN [dbo].[Cuadrillas] c ON t.Cuadrilla = c.Nombre
    LEFT JOIN [dbo].[VW_CuadriUltiPosi] p ON c.CuadriId = p.CuadrillaId
"""


def quote_ident(name: str) -> str:
    return "`" + name.replace("`", "``") + "`"


def safe_name(raw: str) -> str:
    return re.sub(r"[^0-9A-Za-z_]+", "_", raw).strip("_") or "column"


def normalize_name(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z_]+", "_", value).strip("_").lower()


def _resolve_odbc_driver() -> str:
    drivers = pyodbc.drivers()
    for candidate in ("ODBC Driver 18 for SQL Server", "ODBC Driver 17 for SQL Server", "SQL Server"):
        if candidate in drivers:
            return candidate
    return "ODBC Driver 18 for SQL Server"


def get_azure_connection(settings: Optional[Settings] = None) -> pyodbc.Connection:
    cfg = settings or get_settings()
    cfg.require_azure()
    driver = _resolve_odbc_driver()
    trust_cert = "yes" if cfg.azure_trust_server_cert else "no"
    conn_str = (
        f"DRIVER={{{driver}}};"
        f"SERVER={cfg.azure_server};"
        f"DATABASE={cfg.azure_database};"
        f"UID={cfg.azure_user};"
        f"PWD={cfg.azure_password};"
        "Encrypt=yes;"
        f"TrustServerCertificate={trust_cert};"
    )
    return pyodbc.connect(conn_str, timeout=cfg.azure_conn_timeout)


def get_mysql_connection(
    settings: Optional[Settings] = None,
    *,
    autocommit: bool = True,
    timeout: Optional[int] = None,
) -> mysql.connector.MySQLConnection:
    cfg = settings or get_settings()
    cfg.require_mysql()
    return mysql.connector.connect(
        host=cfg.mysql_host,
        port=cfg.mysql_port,
        user=cfg.mysql_user,
        password=cfg.mysql_password,
        database=cfg.mysql_database,
        autocommit=autocommit,
        ssl_disabled=cfg.mysql_ssl_disabled,
        connection_timeout=timeout or cfg.mysql_conn_timeout,
    )
