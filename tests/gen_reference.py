#!/usr/bin/env python3
"""Genera el golden-master `reference_values.json`.

La referencia europea se calcula con Black-Scholes de ALTA PRECISION en Python
(`math.erf`, precision de maquina), independiente del erf aproximado (A&S) del
motor JS: comparar uno contra otro valida la implementacion JS de forma cruzada.
Las anclas americanas son valores de literatura (binomial convergido).
"""
import json
import math
import os


def _N(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _pdf(x):
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def bs(kind, S, K, T, sig, r, q):
    if T <= 0 or sig <= 0:
        intr = max(S - K, 0.0) if kind == "call" else max(K - S, 0.0)
        d = (1.0 if S > K else 0.0) if kind == "call" else (-1.0 if S < K else 0.0)
        return dict(price=intr, delta=d, gamma=0.0, vega=0.0, theta=0.0, rho=0.0)
    sqT = math.sqrt(T)
    v = sig * sqT
    d1 = (math.log(S / K) + (r - q + sig * sig / 2.0) * T) / v
    d2 = d1 - v
    eqt, ert, pdf = math.exp(-q * T), math.exp(-r * T), _pdf(d1)
    gamma = eqt * pdf / (S * v)
    vega = S * eqt * pdf * sqT
    if kind == "call":
        price = S * eqt * _N(d1) - K * ert * _N(d2)
        delta = eqt * _N(d1)
        theta = -(S * eqt * pdf * sig) / (2 * sqT) - r * K * ert * _N(d2) + q * S * eqt * _N(d1)
        rho = K * T * ert * _N(d2)
    else:
        price = K * ert * _N(-d2) - S * eqt * _N(-d1)
        delta = -eqt * _N(-d1)
        theta = -(S * eqt * pdf * sig) / (2 * sqT) + r * K * ert * _N(-d2) - q * S * eqt * _N(-d1)
        rho = -K * T * ert * _N(-d2)
    return dict(price=price, delta=delta, gamma=gamma, vega=vega, theta=theta, rho=rho)


def build():
    cases = []
    for kind in ("call", "put"):
        for S, K in [(100, 100), (100, 90), (100, 110), (215, 215), (120, 125)]:
            for T in (0.05, 0.25, 1.0):
                for sig in (0.15, 0.40, 0.90):
                    for r in (0.01, 0.05):
                        cases.append(dict(op="bs", kind=kind, S=S, K=K, T=T, sig=sig, r=r, q=0.0))
    ref = [bs(c["kind"], c["S"], c["K"], c["T"], c["sig"], c["r"], c["q"]) for c in cases]
    american = [
        {"case": dict(op="am", kind="put", S=100, K=100, T=1, sig=0.2, r=0.05, q=0.0, N=2000),
         "expect": 6.0896, "tol": 0.012},
        {"case": dict(op="am", kind="call", S=100, K=100, T=1, sig=0.2, r=0.05, q=0.0, N=2000),
         "expect": 10.4506, "tol": 0.02},
    ]
    return {"bs": {"cases": cases, "ref": ref}, "american": american}


def main():
    out = os.path.join(os.path.dirname(__file__), "reference_values.json")
    data = build()
    with open(out, "w") as fh:
        json.dump(data, fh, indent=1)
    print("escrito", out, "·", len(data["bs"]["cases"]), "casos BS")


if __name__ == "__main__":
    main()
