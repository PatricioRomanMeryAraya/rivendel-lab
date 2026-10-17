"""Regresión: base TTM vs anual en _fetch_live (deriva temporal)."""
import unittest
from unittest.mock import patch
import pandas as pd
import numpy as np

from dcf.data import _fetch_live


def _df(rowmap, dates):
    names = list(rowmap.keys())
    return pd.DataFrame([rowmap[n] for n in names], index=names,
                        columns=[pd.Timestamp(d) for d in dates])


class FakeTicker:
    def __init__(self, inc_dates, q_dates):
        self.income_stmt = _df({"Total Revenue": [100.0, 90.0],
                                "Operating Income": [20.0, 18.0],
                                "Pretax Income": [18.0, 16.0],
                                "Tax Provision": [3.6, 3.2],
                                "Interest Expense": [-1.0, -1.0]},
                               inc_dates)
        self.balance_sheet = _df({"Total Debt": [30.0],
                                  "Cash And Cash Equivalents": [10.0],
                                  "Minority Interest": [0.0]}, inc_dates[:1])
        self.cashflow = _df({"Capital Expenditure": [-5.0],
                             "Depreciation And Amortization": [4.0],
                             "Change In Working Capital": [-1.0]}, inc_dates[:1])
        nq = len(q_dates)
        self.quarterly_income_stmt = _df(
            {"Total Revenue": [40.0] * nq,
             "Operating Income": [12.0] * nq,
             "Pretax Income": [11.0] * nq,
             "Tax Provision": [2.2] * nq,
             "Interest Expense": [-0.5] * nq}, q_dates)
        self.quarterly_cashflow = _df(
            {"Capital Expenditure": [-2.0] * nq,
             "Depreciation And Amortization": [1.5] * nq,
             "Change In Working Capital": [-0.5] * nq}, q_dates)
        self.quarterly_balance_sheet = _df(
            {"Total Debt": [25.0], "Cash And Cash Equivalents": [12.0],
             "Minority Interest": [0.0]}, q_dates[:1])
        self.info = {"currentPrice": 50.0, "sharesOutstanding": 1e9,
                     "beta": 1.2, "longName": "Mock", "sector": "Technology",
                     "country": "United States", "currency": "USD"}

    @property
    def fast_info(self):
        return {}


def _run(inc_dates, q_dates):
    fake = FakeTicker(inc_dates, q_dates)
    with patch("yfinance.Ticker", return_value=fake):
        return _fetch_live("MOCK")


class TestTTMBase(unittest.TestCase):
    def test_ttm_cuando_trimestres_mas_nuevos(self):
        cd = _run(["2025-12-31", "2024-12-31"],
                  ["2026-06-30", "2026-03-31", "2025-12-31", "2025-09-30"])
        self.assertEqual(cd.source, "yfinance-ttm")
        self.assertEqual(cd.asof, "TTM-2026-06-30")
        # TTM: 4 x 40 = 160 (no el anual 100); EBIT 4 x 12 = 48
        self.assertAlmostEqual(cd.revenue, 160.0)
        self.assertAlmostEqual(cd.ebit, 48.0)
        # balance del trimestre, no del anual
        self.assertAlmostEqual(cd.total_debt, 25.0)
        self.assertAlmostEqual(cd.cash, 12.0)

    def test_anual_cuando_anual_mas_nuevo(self):
        cd = _run(["2026-12-31", "2025-12-31"],
                  ["2026-06-30", "2026-03-31", "2025-12-31", "2025-09-30"])
        self.assertEqual(cd.source, "yfinance")
        self.assertEqual(cd.asof, "2026-12-31")
        self.assertAlmostEqual(cd.revenue, 100.0)
        self.assertAlmostEqual(cd.ebit, 20.0)
        self.assertAlmostEqual(cd.total_debt, 30.0)

    def test_sin_4_trimestres_usa_anual(self):
        cd = _run(["2025-12-31", "2024-12-31"],
                  ["2026-06-30", "2026-03-31", "2025-12-31"])
        self.assertEqual(cd.source, "yfinance")
        self.assertAlmostEqual(cd.revenue, 100.0)


if __name__ == "__main__":
    unittest.main()
