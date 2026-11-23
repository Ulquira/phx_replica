import unittest

from pydantic import ValidationError

from phx_sync.config import Settings


class TestSettings(unittest.TestCase):
    def test_defaults_enforce_tls(self):
        cfg = Settings()
        self.assertFalse(cfg.mysql_ssl_disabled)
        self.assertFalse(cfg.azure_trust_server_cert)
        self.assertEqual(cfg.mysql_port, 3306)
        self.assertEqual(cfg.photo_batch_size, 50)

    def test_require_azure_raises_when_missing(self):
        cfg = Settings(azure_server="srv")
        with self.assertRaises(RuntimeError) as ctx:
            cfg.require_azure()
        self.assertIn("AZURE_DATABASE", str(ctx.exception))

    def test_require_mysql_raises_when_missing(self):
        cfg = Settings(mysql_host="localhost")
        with self.assertRaises(RuntimeError) as ctx:
            cfg.require_mysql()
        self.assertIn("MYSQL_PASSWORD", str(ctx.exception))

    def test_require_gemini_raises_when_missing(self):
        cfg = Settings()
        with self.assertRaises(RuntimeError):
            cfg.require_gemini()

    def test_invalid_port_rejected(self):
        with self.assertRaises(ValidationError):
            Settings(mysql_port=99999)

    def test_empty_mysql_table_rejected(self):
        with self.assertRaises(ValidationError):
            Settings(mysql_table="   ")


if __name__ == "__main__":
    unittest.main()
