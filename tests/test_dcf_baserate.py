"""Freno de Mauboussin (base rates) al crecimiento: _base_rate_brake y su
aplicación en _estimate_growth_s2c. Todo mockeado, sin red."""
import unittest
import pandas as pd
import numpy as np

from dcf.data import _base_rate_brake, _estimate_growth_s2c, _BASERATE_ROWS


def _inc_df(revenues):
    dates = [pd.Timestamp("2025-12-31") - pd.DateOffset(years=i)
             for i in range(len(revenues))]
    return pd.DataFrame([list(revenues)], index=["Total Revenue"], columns=dates)


class FakeT:
    def __init__(self, analyst_g):
        self.revenue_estimate = pd.DataFrame({"growth": [analyst_g]}, index=["+1y"])
        self.cashflow = pd.DataFrame([[0.0]], index=["Capital Expenditure"],
                                     columns=[pd.Timestamp("2025-12-31")])


class TestBaseRateBrake(unittest.TestCase):
    def test_tabla_tiene_16_filas_y_medianas_sanas(self):
        self.assertEqual(len(_BASERATE_ROWS), 16)
        meds = [r[2] for r in _BASERATE_ROWS]
        self.assertTrue(all(0.03 <= m <= 0.16 for m in meds))
        self.assertLessEqual(meds[0], meds[-1])  # el freno sube con el CAGR previo

    def test_fila_hipercrecimiento(self):
        # CAGR previo 3a = (400/80)^(1/3)-1 ~= 71% -> fila ">44%", tope 13.3%
        cap, row, prior, p20 = _base_rate_brake(_inc_df([400.0, 230.0, 135.0, 80.0]))
        self.assertEqual(row, ">44%")
        self.assertAlmostEqual(cap, 0.133, places=3)
        self.assertAlmostEqual(prior, (400.0 / 80.0) ** (1 / 3) - 1, places=6)
        self.assertAlmostEqual(p20, 0.41, places=2)

    def test_fila_borde_sobre_44(self):
        # prior apenas sobre 0.44 cae en ">44%" (cota inferior inclusiva)
        rev0 = 100.0 * (1.4401 ** 3)
        cap, row, prior, _ = _base_rate_brake(_inc_df([rev0, 100.0, 100.0, 100.0]))
        self.assertEqual(row, ">44%")
        self.assertGreater(prior, 0.44)

    def test_fila_crecimiento_normal(self):
        # CAGR previo ~= 2% -> fila "0% a 4%", tope 4.9%
        cap, row, prior, _ = _base_rate_brake(_inc_df([106.1, 104.0, 102.0, 100.0]))
        self.assertEqual(row, "0% a 4%")
        self.assertAlmostEqual(cap, 0.049, places=3)

    def test_sin_4_anuales_no_hay_tope(self):
        cap, row, prior, p20 = _base_rate_brake(_inc_df([100.0, 90.0, 80.0]))
        self.assertIsNone(cap)
        self.assertIsNone(row)
        self.assertTrue(np.isnan(prior))

    def test_freno_recorta_analista(self):
        t = FakeT(0.60)
        inc = _inc_df([400.0, 230.0, 135.0, 80.0])
        bs = pd.DataFrame([[0.0]], index=["X"], columns=[pd.Timestamp("2025-12-31")])
        g, s2c, det = _estimate_growth_s2c(t, inc, bs, {}, 400e9, 100e9, 0.21, 10e9, 20e9)
        self.assertTrue(det["freno_base_rate"])
        self.assertAlmostEqual(g, 0.133, places=3)
        self.assertEqual(det["fuente"], "base-rate Mauboussin (tope)")
        self.assertAlmostEqual(det["elegido_previo_freno"], 0.60, places=6)
        self.assertIn("analistas", det["fuente_previa_freno"])

    def test_sin_freno_si_no_supera_tope(self):
        t = FakeT(0.04)
        inc = _inc_df([106.1, 104.0, 102.0, 100.0])  # tope fila = 4.9%
        bs = pd.DataFrame([[0.0]], index=["X"], columns=[pd.Timestamp("2025-12-31")])
        g, s2c, det = _estimate_growth_s2c(t, inc, bs, {}, 106e9, 20e9, 0.21, 10e9, 20e9)
        self.assertFalse(det["freno_base_rate"])
        self.assertAlmostEqual(g, 0.04, places=6)


if __name__ == "__main__":
    unittest.main()
