"""Regresión: normalización de cíclicas (Damodaran, 'Ups and Downs')."""
import unittest
import pandas as pd
import numpy as np

from dcf.data import _cycle_stats, _normalized_cycle_set
from dcf.model import (CompanyData, normalized_company, value_normalized,
                       implied_cycle_margin, value_company)


def _inc(margins, revs):
    n = len(margins)
    return pd.DataFrame(
        [[r for r in revs], [r * m for r, m in zip(revs, margins)]],
        index=["Total Revenue", "Operating Income"],
        columns=[pd.Timestamp(f"{2025 - i}-12-31") for i in range(n)])


class FakeT:
    def __init__(self, n):
        self.cashflow = pd.DataFrame(
            [[-6.0] * n, [2.0] * n, [-1.0] * n],
            index=["Capital Expenditure", "Depreciation And Amortization",
                   "Change In Working Capital"],
            columns=[pd.Timestamp(f"{2025 - i}-12-31") for i in range(n)])


class TestCycleStats(unittest.TestCase):
    def test_mu_like_es_ciclica(self):
        inc = _inc([0.262, 0.052, -0.348, 0.316], [37.4, 25.1, 15.5, 30.8])
        s = _cycle_stats(inc)
        self.assertTrue(s["is_cyclical"])
        self.assertEqual(s["n"], 4)
        self.assertGreater(s["cv"], 1.0)

    def test_aapl_like_no_es_ciclica(self):
        inc = _inc([0.320, 0.315, 0.298, 0.303], [400, 385, 383, 394])
        s = _cycle_stats(inc)
        self.assertFalse(s["is_cyclical"])
        self.assertLess(s["cv"], 1.0)

    def test_pocos_anos_no_es_ciclica(self):
        inc = _inc([0.30, -0.30, 0.25], [10, 10, 10])
        self.assertFalse(_cycle_stats(inc)["is_cyclical"])

    def test_sin_datos_no_es_ciclica(self):
        self.assertFalse(_cycle_stats(None)["is_cyclical"])


class TestNormalizedSet(unittest.TestCase):
    def _cd(self):
        inc = _inc([0.262, 0.052, -0.348, 0.316], [37.4, 25.1, 15.5, 30.8])
        t = FakeT(4)
        bs = pd.DataFrame(
            [[50.0] * 4, [8.0] * 4, [10.0] * 4],
            index=["Total Stockholder Equity", "Total Debt",
                   "Cash And Cash Equivalents"],
            columns=[pd.Timestamp(f"{2025 - i}-12-31") for i in range(4)])
        d = _normalized_cycle_set(t, inc, bs, 90.0, 0.15)
        self.assertIsNotNone(d)
        return d

    def test_consistencia_interna(self):
        d = self._cd()
        # identidad que exige la mecánica Δventas/s2c: ROIC = s2c x margen nopat
        self.assertAlmostEqual(d["roic_norm"], d["s2c_norm"] * d["m_norm"] * 0.85)
        self.assertAlmostEqual(d["m_norm"], np.mean([0.262, 0.052, -0.348, 0.316]))
        self.assertGreaterEqual(d["g_norm"], 0.0)
        self.assertLessEqual(d["g_norm"], 0.60)
        self.assertEqual(d["n_years"], 4)

    def test_roundtrip_margen_implicito(self):
        d = self._cd()
        cd = CompanyData(
            ticker="MOCK", name="Mock", price=100.0, shares=1e9, beta=1.2,
            sector="Technology", country="US", currency="USD",
            revenue=d["revenue_base"], ebit=d["revenue_base"] * 0.30,
            tax_rate=0.15, interest_expense=0.0, total_debt=8.0, cash=10.0,
            minority=0.0, source="mock", asof="2025",
            rev_growth_est=0.10, sales_to_capital_est=1.0,
            is_cyclical=True, cycle_detail=d)
        macro = {"rf": 0.04, "erp": 0.045}
        vn = value_normalized(cd, macro)
        self.assertTrue(np.isfinite(vn.per_share))
        # el margen implícito en el valor normalizado recupera m_norm
        icm = implied_cycle_margin(cd, macro, price=vn.per_share)
        self.assertIsNotNone(icm)
        self.assertAlmostEqual(icm["implied_margin"], d["m_norm"], places=4)

    def test_sin_set_devuelve_none(self):
        cd = CompanyData(
            ticker="MOCK", name="Mock", price=100.0, shares=1e9, beta=1.0,
            sector="Tech", country="US", currency="USD", revenue=100.0,
            ebit=30.0, tax_rate=0.2, interest_expense=0.0, total_debt=0.0,
            cash=0.0, minority=0.0)
        self.assertIsNone(implied_cycle_margin(cd, {"rf": 0.04, "erp": 0.045}))
        with self.assertRaises(ValueError):
            normalized_company(cd)


if __name__ == "__main__":
    unittest.main()
