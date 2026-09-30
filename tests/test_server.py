"""Tests de las funciones puras del servidor (stdlib, sin red).

Compatible con pytest y con `python -m unittest`.
"""
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import server  # noqa: E402


class TestCurve(unittest.TestCase):
    def test_interpolation_and_flat_ends(self):
        cur = {"points": [[0.25, 0.04], [1.0, 0.05]]}
        self.assertAlmostEqual(server.curve_rate(cur, 0.25), 0.04)
        self.assertAlmostEqual(server.curve_rate(cur, 1.0), 0.05)
        self.assertAlmostEqual(server.curve_rate(cur, 0.625), 0.045, places=6)  # punto medio
        self.assertEqual(server.curve_rate(cur, 0.05), 0.04)  # plana por debajo
        self.assertEqual(server.curve_rate(cur, 5.0), 0.05)   # plana por encima

    def test_empty_curve(self):
        self.assertIsNone(server.curve_rate({"points": []}, 1.0))

    def test_static_curve_usable(self):
        r = server.curve_rate(server.STATIC_CURVE, 0.25)
        self.assertTrue(0.0 < r < 0.2)


class TestNum(unittest.TestCase):
    def test_nan_and_inf(self):
        self.assertIsNone(server.num(float("nan")))
        self.assertIsNone(server.num(float("inf")))
        self.assertIsNone(server.num(None))
        self.assertIsNone(server.num("no-num"))

    def test_rounding(self):
        self.assertEqual(server.num(1.23456, 2), 1.23)

    def test_whole(self):
        self.assertEqual(server.whole(float("nan")), 0)
        self.assertEqual(server.whole(None), 0)
        self.assertEqual(server.whole(139.0), 139)  # OI/volumen son enteros
        self.assertEqual(server.whole(7.9), 8)       # num(x, 0) redondea


class TestRealizedVol(unittest.TestCase):
    def test_constant_returns_zero_vol(self):
        closes = [100.0 * math.exp(0.01 * i) for i in range(30)]  # log-retorno constante
        rv = server.realized_vol(closes, 20)
        self.assertIsNotNone(rv)
        self.assertAlmostEqual(rv, 0.0, places=6)

    def test_insufficient_data(self):
        self.assertIsNone(server.realized_vol([100.0, 101.0], 20))


class TestTenorsAndDates(unittest.TestCase):
    def test_tenor_years(self):
        self.assertEqual(server.TENOR_YEARS["3 Mo"], 0.25)
        self.assertAlmostEqual(server.TENOR_YEARS["6 Mo"], 0.5)
        self.assertEqual(server.TENOR_YEARS["30 Yr"], 30.0)

    def test_days_to(self):
        self.assertGreater(server.days_to("2099-01-01"), 0)
        self.assertEqual(server.days_to("2000-01-01"), 0)  # pasado -> 0 (no negativo)
        self.assertIsNone(server.days_to("no-fecha"))


if __name__ == "__main__":
    unittest.main()
