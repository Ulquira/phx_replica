import unittest
from typing import Any, Dict, List

from phx_sync.config import Settings
from phx_sync.photos.enhancer import process_all_photos


class _FakeCursor:
    def __init__(self, rows_store: List[Dict[str, Any]], dictionary: bool = False):
        self._store = rows_store
        self._dictionary = dictionary
        self._last_result: List[Any] = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, query: str, params: Any = None):
        q = " ".join(query.split())
        if q.startswith("SHOW COLUMNS"):
            self._last_result = [("Img_mejorada",)]
            return

        if q.startswith("SELECT Documento, Nombre_Tecnico_Limpio, Foto_Img"):
            excluded = set()
            if "NOT IN" in q and params:
                excluded = set(params[:-1])
                limit = int(params[-1])
            else:
                limit = int(params[0]) if params else 50
            pending = [
                r
                for r in self._store
                if r.get("Documento")
                and not r.get("Img_mejorada")
                and r["Documento"] not in excluded
            ]
            self._last_result = pending[:limit]
            return

        if q.startswith("UPDATE `vw_info_cuadrillas` SET `Img_mejorada` = %s WHERE `Documento` = %s"):
            enhanced, doc = params
            for r in self._store:
                if r["Documento"] == doc:
                    r["Img_mejorada"] = enhanced
            return

    def fetchone(self):
        return self._last_result[0] if self._last_result else None

    def fetchall(self):
        return list(self._last_result)


class _FakeConn:
    def __init__(self, rows_store: List[Dict[str, Any]]):
        self._store = rows_store

    def cursor(self, dictionary: bool = False):
        return _FakeCursor(self._store, dictionary=dictionary)


class TestEnhancer(unittest.TestCase):
    def test_failed_photos_do_not_cause_infinite_loop(self):
        store = [
            {"Documento": "111", "Nombre_Tecnico_Limpio": "TECH ONE", "Foto_Img": "data:image/jpeg;base64,AAA", "Img_mejorada": None},
            {"Documento": "222", "Nombre_Tecnico_Limpio": "TECH TWO", "Foto_Img": "data:image/jpeg;base64,BBB", "Img_mejorada": None},
            {"Documento": "333", "Nombre_Tecnico_Limpio": "TECH THREE", "Foto_Img": "data:image/jpeg;base64,CCC", "Img_mejorada": None},
        ]
        conn = _FakeConn(store)
        cfg = Settings(photo_batch_size=2, photo_max_batches=10)

        def fake_enhancer(raw: str):
            # Simulate failure for AAA and CCC, success for BBB
            return "data:image/jpeg;base64,ENHANCED" if raw.endswith("BBB") else None

        total, ok, failed = process_all_photos(
            settings=cfg,
            conn=conn,
            enhancer_fn=fake_enhancer,
            sleep_sec=0,
        )

        self.assertEqual(total, 3)
        self.assertEqual(ok, 1)
        self.assertEqual(failed, 2)
        self.assertEqual(store[1]["Img_mejorada"], "data:image/jpeg;base64,ENHANCED")
        self.assertIsNone(store[0]["Img_mejorada"])
        self.assertIsNone(store[2]["Img_mejorada"])


if __name__ == "__main__":
    unittest.main()
