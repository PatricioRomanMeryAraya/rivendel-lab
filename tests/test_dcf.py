"""Tests del módulo DCF (port de StockValuation.io).

Deterministas: usan el fixture AAPL y macro fija, sin red. Compatibles con
pytest y con `python -m unittest`.
"""
import math
import os
import sys
import unittest
from unittest.mock import patch
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from dcf import model as M
from dcf.data import _FIXTURES, growth_report
from dcf.macro import RF_FALLBACK, ERP_FALLBACK
from dcf import to_dict, _clean

MACRO = {"rf": RF_FALLBACK, "erp": ERP_FALLBACK}


def _close(a, b, tol=1e-6):
    return abs(a - b) <= tol * max(1.0, abs(a), abs(b))


class TestCore(unittest.TestCase):
    def test_fixture_regression(self):
        """El fixture AAPL con macro fija da el valor del notebook (bit-idéntico)."""
        vo = M.value_company(_FIXTURES["AAPL"], None, MACRO)
        self.assertTrue(_close(vo.per_share, 134.6379, 1e-3), vo.per_share)
        self.assertTrue(_close(vo.upside, -0.4146, 1e-3), vo.upside)
        self.assertTrue(_close(vo.wacc_initial, 0.0948, 1e-3), vo.wacc_initial)
        self.assertEqual(vo.coc["rating"], "Aaa/AAA")
        self.assertEqual(len(vo.table), 10)
        # el fixture no trae estimación de crecimiento -> fallback etiquetado
        self.assertEqual(vo.assumptions["rev_growth"], M.REV_GROWTH_FALLBACK)

    def test_assumptions_override(self):
        vo = M.value_company(_FIXTURES["AAPL"], {"rev_growth": 0.15}, MACRO)
        self.assertEqual(vo.assumptions["rev_growth"], 0.15)
        self.assertGreater(vo.per_share, 134.0)

    def test_mid_year_stub(self):
        a = M.value_company(_FIXTURES["AAPL"], {"mid_year": True}, MACRO)
        b = M.value_company(_FIXTURES["AAPL"], {"mid_year": False}, MACRO)
        self.assertNotAlmostEqual(a.per_share, b.per_share, places=2)

    def test_reverse_dcf(self):
        rd = M.reverse_dcf(_FIXTURES["AAPL"], None, MACRO)
        self.assertTrue(_close(rd["implied_margin"], 0.5739, 1e-3), rd["implied_margin"])
        self.assertTrue(_close(rd["implied_growth"], 0.2034, 1e-3), rd["implied_growth"])
        # el precio del fixture exige más margen y más crecimiento que el caso base
        self.assertGreater(rd["implied_margin"], rd["base_margin"])
        self.assertGreater(rd["implied_growth"], rd["base_growth"])

    def test_sensitivity_shape(self):
        g, m, Mx = M.sensitivity(_FIXTURES["AAPL"], None, MACRO)
        self.assertEqual((len(g), len(m)), (5, 5))
        self.assertEqual((len(Mx), len(Mx[0])), (5, 5))
        # monotonía: más crecimiento y más margen -> más valor
        self.assertLess(Mx[0][0], Mx[4][4])

    def test_synthetic_rating(self):
        self.assertEqual(M.synthetic_rating(10.0)[0], "Aaa/AAA")
        self.assertEqual(M.synthetic_rating(0.1)[0], "D2/D")

    def test_growth_report_text(self):
        txt = growth_report(_FIXTURES["AAPL"])
        self.assertIn("AAPL", txt)
        self.assertIn("USADO", txt)


class TestMacro(unittest.TestCase):
    def test_fred_parser(self):
        """El parser de fredgraph.csv salta los '.' faltantes y toma el último válido."""
        from dcf import macro as MM
        sample = "DATE,DGS10\r\n2026-10-14,4.05\r\n2026-10-15,4.02\r\n2026-10-16,.\r\n"
        class FakeResp:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return sample.encode()
        with patch.object(urllib.request, "urlopen", return_value=FakeResp()):
            v, asof = MM._fetch_rf_fred()
        self.assertAlmostEqual(v, 0.0402, places=9)
        self.assertEqual(asof, "2026-10-15")

    def test_fetcher_chain_order(self):
        """_macro_one_multi prueba fetchers en orden y etiqueta la fuente usada."""
        from dcf import macro as MM
        calls = []
        def boom():
            calls.append("fred"); raise TimeoutError("sin red")
        def ok():
            calls.append("yf"); return 0.05, "2026-10-16"
        with patch.object(MM, "_macro_get", return_value=None), \
             patch.object(MM, "_macro_put", return_value=None):
            r = MM._macro_one_multi("rf", [("FRED-DGS10", boom), ("yfinance-^TNX", ok)],
                                    24, 0.0425)
        self.assertEqual(calls, ["fred", "yf"])
        self.assertEqual(r["source"], "yfinance-^TNX")
        self.assertAlmostEqual(r["value"], 0.05)

    def test_all_fail_labeled_fallback(self):
        """Si todo falla y no hay cache, el fallback constante va etiquetado."""
        from dcf import macro as MM
        def boom():
            raise ConnectionError("x")
        with patch.object(MM, "_macro_get", return_value=None), \
             patch.object(MM, "_macro_put", return_value=None):
            r = MM._macro_one_multi("rf", [("FRED-DGS10", boom)], 24, 0.0425)
        self.assertEqual(r["value"], 0.0425)
        self.assertIn("FRED-DGS10", r["source"])
        self.assertIn("fallback-constante", r["source"])


class TestJson(unittest.TestCase):
    def test_clean_kills_nan(self):
        d = _clean({"a": float("nan"), "b": float("inf"), "c": [1.0, float("nan")]})
        self.assertEqual(d, {"a": None, "b": None, "c": [1.0, None]})

    def test_to_dict_serializable(self):
        import json
        vo = M.value_company(_FIXTURES["AAPL"], None, MACRO)
        d = to_dict(vo, MACRO, _FIXTURES["AAPL"])
        s = json.dumps(d, allow_nan=False)  # como server._json
        back = json.loads(s)
        self.assertEqual(back["ticker"], "AAPL")
        self.assertIn("growth_detail", back["company"])


if __name__ == "__main__":
    unittest.main()
