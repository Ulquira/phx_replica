import unittest

from phx_sync.sync.catalogs import clean_cuadrilla_name


class TestCatalogs(unittest.TestCase):
    def test_empty_or_none(self):
        self.assertEqual(clean_cuadrilla_name(None), "")
        self.assertEqual(clean_cuadrilla_name("   "), "")

    def test_sga_sgi_sgm_primary_rule(self):
        self.assertEqual(clean_cuadrilla_name("DP 14 MOTOWIN SGA JUAN PEREZ"), "JUAN PEREZ")
        self.assertEqual(clean_cuadrilla_name("K3 VISUAL SGI MARIA LOPEZ K13 KAJOMI"), "MARIA LOPEZ")
        self.assertEqual(clean_cuadrilla_name("COBRA SGM CARLOS RUIZ"), "CARLOS RUIZ")

    def test_prefixes_operations_and_contractors(self):
        self.assertEqual(clean_cuadrilla_name("DP 14 POSTVENTA KAJOMI PEDRO GOMEZ"), "PEDRO GOMEZ")
        self.assertEqual(clean_cuadrilla_name("BAJA FR TRASLADO VISUAL ANA TORRES K5 VISUAL"), "ANA TORRES")
        self.assertEqual(clean_cuadrilla_name("MOTOWIN INSTALACION COBRA LUIS DIAZ"), "LUIS DIAZ")


if __name__ == "__main__":
    unittest.main()
