"""Golden-master del motor de pricing JS (bsOne / amPrice / amGreeks).

Compatible con pytest y con `python -m unittest`. Corre el motor JS via node
(tests/js_engine.js) y lo compara contra la referencia de alta precision
generada por gen_reference.py, mas anclas americanas de literatura.
"""
import json
import os
import re
import subprocess
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
HTML = os.path.join(ROOT, "web", "index.html")
REF = os.path.join(HERE, "reference_values.json")


def _ensure_ref():
    if not os.path.exists(REF):
        import gen_reference  # noqa: PLC0415
        gen_reference.main()


def run_js(cases):
    fd, name = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w") as f:
        json.dump(cases, f)
    try:
        out = subprocess.check_output(["node", os.path.join(HERE, "js_engine.js"), name])
    finally:
        os.unlink(name)
    return json.loads(out)


def close(a, b, rel=1e-3, ab=1e-3):
    return abs(a - b) <= ab + rel * abs(b)


class TestJsSyntax(unittest.TestCase):
    def test_script_parses(self):
        with open(HTML) as fh:
            m = re.search(r"<script>(.*)</script>", fh.read(), re.S)
        self.assertIsNotNone(m, "no encuentro <script> en index.html")
        fd, name = tempfile.mkstemp(suffix=".js")
        with os.fdopen(fd, "w") as f:
            f.write(m.group(1))
        try:
            r = subprocess.run(["node", "--check", name], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
        finally:
            os.unlink(name)


class TestBlackScholesGolden(unittest.TestCase):
    def test_greeks_match_high_precision(self):
        _ensure_ref()
        with open(REF) as fh:
            data = json.load(fh)
        got = run_js(data["bs"]["cases"])
        self.assertEqual(len(got), len(data["bs"]["ref"]))
        for c, exp, g in zip(data["bs"]["cases"], data["bs"]["ref"], got):
            for k in ("price", "delta", "gamma", "vega", "theta", "rho"):
                self.assertTrue(close(g[k], exp[k]),
                                msg="%s %s: js=%.6f ref=%.6f" % (c, k, g[k], exp[k]))


class TestAmerican(unittest.TestCase):
    def test_literature_anchors(self):
        _ensure_ref()
        with open(REF) as fh:
            data = json.load(fh)
        cases = [a["case"] for a in data["american"]]
        got = run_js(cases)
        for a, g in zip(data["american"], got):
            self.assertLess(abs(g["price"] - a["expect"]), a["tol"],
                            msg="%s -> %.4f (esperado %.4f)" % (a["case"], g["price"], a["expect"]))

    def test_american_put_gt_european(self):
        got = run_js([
            {"op": "am", "kind": "put", "S": 100, "K": 100, "T": 1, "sig": 0.2, "r": 0.05, "q": 0, "N": 800},
            {"op": "bs", "kind": "put", "S": 100, "K": 100, "T": 1, "sig": 0.2, "r": 0.05, "q": 0},
        ])
        self.assertGreater(got[0]["price"], got[1]["price"] + 0.3)

    def test_american_call_equals_european_no_div(self):
        got = run_js([
            {"op": "am", "kind": "call", "S": 100, "K": 100, "T": 1, "sig": 0.2, "r": 0.05, "q": 0, "N": 1500},
            {"op": "bs", "kind": "call", "S": 100, "K": 100, "T": 1, "sig": 0.2, "r": 0.05, "q": 0},
        ])
        self.assertLess(abs(got[0]["price"] - got[1]["price"]), 0.02)

    def test_discrete_dividend_call_early_exercise(self):
        # dividendo grande antes del vto -> prima de ejercicio anticipado > 0
        got = run_js([
            {"op": "am", "kind": "call", "S": 100, "K": 100, "T": 0.5, "sig": 0.3, "r": 0.05, "q": 0,
             "N": 800, "divs": [[0.49, 5.0]]},
            {"op": "am", "kind": "call", "S": 100, "K": 100, "T": 0.5, "sig": 0.3, "r": 0.05, "q": 0,
             "N": 800, "divs": []},
        ])
        self.assertLess(got[0]["price"], got[1]["price"])  # el div baja el call


class TestPyVollibCrossCheck(unittest.TestCase):
    """Chequeo cruzado independiente si py_vollib esta instalado (CI)."""

    def test_crosscheck(self):
        try:
            from py_vollib.black_scholes import black_scholes
            from py_vollib.black_scholes.greeks.analytical import delta as pv_delta
        except Exception:
            self.skipTest("py_vollib no instalado")
        cases, expect = [], []
        for flag in ("c", "p"):
            for S, K, T, sig, r in [(100, 100, 1, 0.2, 0.05), (215, 215, 0.1, 0.4, 0.03),
                                    (120, 125, 0.25, 0.9, 0.04)]:
                kind = "call" if flag == "c" else "put"
                cases.append({"op": "bs", "kind": kind, "S": S, "K": K, "T": T, "sig": sig, "r": r, "q": 0})
                expect.append((black_scholes(flag, S, K, T, r, sig),
                               pv_delta(flag, S, K, T, r, sig)))
        got = run_js(cases)
        for g, (px, dl) in zip(got, expect):
            self.assertTrue(close(g["price"], px, ab=2e-3), "precio js=%.5f pv=%.5f" % (g["price"], px))
            self.assertTrue(close(g["delta"], dl, ab=2e-3), "delta js=%.5f pv=%.5f" % (g["delta"], dl))


if __name__ == "__main__":
    unittest.main()
