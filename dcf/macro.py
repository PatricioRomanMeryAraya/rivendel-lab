"""Inputs macro en vivo: rf (Tesoro 10Y) + ERP (Damodaran implied).

rf: FRED pública (DGS10, fredgraph.csv sin key) -> yfinance ^TNX -> cache -> constante.
ERP: Damodaran implied (página pública NYU Stern) -> cache -> constante.
Solo stdlib en el top-level; yfinance/requests/pandas se importan perezosamente.
"""
import os, sqlite3, datetime as dt, urllib.request

from .model import RF_FALLBACK, ERP_FALLBACK

SV_HOME = os.path.expanduser("~/.stockvaluation")
DB_PATH = os.path.join(SV_HOME, "sv.db")

_RF_TTL_H, _ERP_TTL_H = 24, 24 * 30   # frescura del cache (rf diario; ERP ~anual)


def _macro_table():
    os.makedirs(SV_HOME, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.execute("""CREATE TABLE IF NOT EXISTS macro(
        key TEXT PRIMARY KEY, value REAL, asof TEXT, source TEXT, fetched TEXT)""")
    return con


def _macro_get(key):
    with _macro_table() as con:
        return con.execute("SELECT value,asof,source,fetched FROM macro WHERE key=?",
                           (key,)).fetchone()


def _macro_put(key, value, asof, source):
    with _macro_table() as con:
        con.execute("INSERT OR REPLACE INTO macro VALUES (?,?,?,?,?)",
                    (key, float(value), asof, source, dt.datetime.now().isoformat()))


def _fresh(fetched, ttl_h):
    try:
        age = (dt.datetime.now() - dt.datetime.fromisoformat(fetched)).total_seconds() / 3600
        return age <= ttl_h
    except Exception:
        return False


def _fetch_rf_fred():
    """rf = DGS10 (10-Year Treasury Constant Maturity Rate), FRED pública
    vía fredgraph.csv (sin API key)."""
    import csv, io
    url = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS10"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        txt = r.read().decode("utf-8", "replace")
    v = asof = None
    for row in csv.DictReader(io.StringIO(txt)):
        try:
            x = float(row["DGS10"])
        except (ValueError, KeyError, TypeError):
            continue
        v, asof = x / 100.0, row["DATE"]
    if v is None or not (0.0 < v < 0.25):
        raise ValueError("DGS10 sin dato válido")
    return v, asof


def _fetch_rf_live():
    """rf = yield del Tesoro 10Y (^TNX) vía yfinance, en fracción."""
    import yfinance as yf
    h = yf.Ticker("^TNX").history(period="5d")
    s = h["Close"].dropna()
    v = float(s.iloc[-1]) / 100.0
    asof = s.index[-1].date().isoformat()
    if not (0.0 < v < 0.25):
        raise ValueError(f"rf fuera de rango plausible: {v}")
    return v, asof


def _fetch_erp_live():
    """ERP = implied equity risk premium (FCFE) de Damodaran, último año publicado."""
    import requests, io, pandas as pd
    url = "https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/histimpl.html"
    r = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    t = pd.read_html(io.StringIO(r.text))[0]
    t.columns = t.iloc[0]; t = t.iloc[1:]
    t = t[pd.to_numeric(t["Year"], errors="coerce").notna()]
    last = t.iloc[-1]
    v = float(str(last["Implied ERP (FCFE)"]).replace("%", "").strip()) / 100.0
    asof = str(int(float(last["Year"])))
    if not (0.0 < v < 0.15):
        raise ValueError(f"ERP fuera de rango plausible: {v}")
    return v, asof


def _macro_one(key, fetch_fn, ttl_h, fallback):
    return _macro_one_multi(key, [(key, fetch_fn)], ttl_h, fallback)


def _macro_one_multi(key, fetchers, ttl_h, fallback):
    """Prueba fetchers en orden (nombre, fn); luego cache añejo; luego constante."""
    cached = _macro_get(key)
    if cached and _fresh(cached[3], ttl_h):
        return dict(value=cached[0], asof=cached[1], source=cached[2] + "+cache")
    errs = []
    for name, fn in fetchers:
        try:
            v, asof = fn()
            _macro_put(key, v, asof, name)
            return dict(value=v, asof=asof, source=name)
        except Exception as e:
            errs.append("%s:%s" % (name, type(e).__name__))
    if cached:   # red caída pero hay cache (aunque esté añejo)
        return dict(value=cached[0], asof=cached[1], source=cached[2] + "+cache-añejo")
    return dict(value=fallback, asof="n/d",
                source="fallback-constante (%s)" % "; ".join(errs))


def macro_inputs(live: bool = True) -> dict:
    """rf (FRED DGS10 -> yfinance ^TNX) + ERP (Damodaran implied), en vivo con
    cache y fallback offline etiquetado."""
    if live:
        rf = _macro_one_multi("rf", [("FRED-DGS10", _fetch_rf_fred),
                                     ("yfinance-^TNX", _fetch_rf_live)],
                              _RF_TTL_H, RF_FALLBACK)
        erp = _macro_one("erp", _fetch_erp_live, _ERP_TTL_H, ERP_FALLBACK)
    else:  # sólo cache/constante (sin tocar la red)
        def _off(key, fb):
            c = _macro_get(key)
            return dict(value=c[0], asof=c[1], source=c[2] + "+cache") if c \
                else dict(value=fb, asof="n/d", source="fallback-constante")
        rf, erp = _off("rf", RF_FALLBACK), _off("erp", ERP_FALLBACK)
    return dict(rf=rf["value"], erp=erp["value"],
                rf_asof=rf["asof"], erp_asof=erp["asof"],
                rf_source=rf["source"], erp_source=erp["source"])
