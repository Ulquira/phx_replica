import unittest
import zlib
from datetime import datetime
from unittest.mock import MagicMock

from phx_sync.db import quote_ident, safe_name
from phx_sync.sync.work_orders import (
    compute_row_fingerprint,
    fetch_backfill_rows,
    fetch_source_rows,
    map_sql_type,
    normalize_val_for_fp,
    validate_iso_date,
)


class TestWorkOrders(unittest.TestCase):
    def test_quote_ident_escapes_backticks(self):
        self.assertEqual(quote_ident("col`name"), "`col``name`")

    def test_safe_name_sanitizes(self):
        self.assertEqual(safe_name("VW_WinOrde-Traba!"), "VW_WinOrde_Traba")
        self.assertEqual(safe_name("!!!"), "column")

    def test_map_sql_type(self):
        self.assertEqual(map_sql_type("int"), "BIGINT")
        self.assertEqual(map_sql_type("nvarchar", -1), "LONGTEXT")
        self.assertEqual(map_sql_type("varchar", 120), "VARCHAR(120)")
        self.assertEqual(map_sql_type("datetime2"), "DATETIME")
        self.assertEqual(map_sql_type("decimal"), "DOUBLE")

    def test_normalize_val_for_fp(self):
        self.assertEqual(normalize_val_for_fp(None), "")
        self.assertEqual(normalize_val_for_fp(datetime(2026, 9, 11, 8, 30, 0)), "2026-09-11 08:30:00")
        self.assertEqual(normalize_val_for_fp("2026-09-11T08:30:00"), "2026-09-11 08:30:00")

    def test_fingerprint_matches_mysql_crc32_format(self):
        obs = "Instalación completada sin novedades"
        source_row = {"Estado": "Finalizada", "Observaciones OT": obs}
        mysql_row = {"Estado": "Finalizada", "Observaciones OT": zlib.crc32(obs.encode("utf-8"))}
        self.assertEqual(compute_row_fingerprint(source_row), compute_row_fingerprint(mysql_row))

    def test_fingerprint_changes_on_field_diff(self):
        row_a = {"Estado": "En camino", "Cuadrilla": "C1"}
        row_b = {"Estado": "Finalizada", "Cuadrilla": "C1"}
        self.assertNotEqual(compute_row_fingerprint(row_a), compute_row_fingerprint(row_b))

    def test_validate_iso_date_rejects_sql_injection(self):
        self.assertEqual(validate_iso_date("2026-09-11"), "2026-09-11")
        with self.assertRaises(ValueError):
            validate_iso_date("2026-09-11' OR '1'='1")

    def test_fetch_backfill_rows_uses_parameterized_query(self):
        mock_cursor = MagicMock()
        mock_cursor.description = [("OrdenId",), ("FechaCreacion",)]
        mock_cursor.fetchall.return_value = [(101, datetime(2026, 9, 12, 10, 0, 0))]
        mock_conn = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

        columns = [{"name": "OrdenId", "type": "int"}, {"name": "FechaCreacion", "type": "datetime"}]
        rows = fetch_backfill_rows(mock_conn, columns, start_date="2026-09-11")

        self.assertEqual(len(rows), 1)
        sql, params = mock_cursor.execute.call_args[0]
        self.assertIn("WHERE t.[FechaCreacion] >= ?", sql)
        self.assertEqual(params, ("2026-09-11",))

    def test_fetch_source_rows_uses_parameterized_lookback(self):
        mock_cursor = MagicMock()
        mock_cursor.description = [("OrdenId",)]
        mock_cursor.fetchall.return_value = [(1,)]
        mock_conn = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

        columns = [{"name": "OrdenId", "type": "int"}, {"name": "Fecha", "type": "datetime"}]
        fetch_source_rows(mock_conn, columns, lookback_days=2)

        sql, params = mock_cursor.execute.call_args[0]
        self.assertIn("DATEADD(day, -?, CAST(GETDATE() AS date))", sql)
        self.assertEqual(params, (2,))


if __name__ == "__main__":
    unittest.main()
