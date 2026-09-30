#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Rivendel · Payoff Lab local
===========================

Servidor mínimo (solo stdlib) que sirve el lab de payoff y le enchufa datos
reales de mercado: precio spot, vencimientos listados, cadena de opciones con
bid/ask/IV y volatilidad realizada.

Proveedor de datos
------------------
1. `yfinance` si está instalado  → proveedor primario.
2. API pública de Yahoo por urllib → respaldo puro-stdlib, para cuando en
   Termux no se puede compilar pandas/numpy. Mismos campos, misma forma.
3. Si ninguno responde, el lab sigue funcionando en modo manual (el original).

Uso
---
    python3 server.py                # http://127.0.0.1:8765
    python3 server.py --port 9000
    python3 server.py --host 0.0.0.0 # accesible desde la LAN
"""

from __future__ import annotations

import argparse
import csv
import errno
import http.cookiejar
import io
import json
import math
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(ROOT, "web")

UA = ("Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Mobile Safari/537.36")

TTL_QUOTE = 60.0     # segundos: el spot se refresca seguido
TTL_CHAIN = 300.0    # la cadena de opciones cambia más lento y pesa más
TTL_HIST = 3600.0    # el histórico diario, una vez por hora alcanza
TTL_CURVE = 6 * 3600.0  # la curva del Tesoro cambia una vez por día hábil

TICKER_RE = re.compile(r"^[A-Za-z0-9.\-=^]{1,20}$")

# ---------------------------------------------------------------- utilidades


def num(x, nd=6):
    """float limpio para JSON: NaN/inf/None -> None."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if math.isnan(v) or math.isinf(v):
        return None
    return round(v, nd)


def whole(x):
    """int limpio: NaN/None/basura -> 0 (yfinance manda NaN en volume/oi)."""
    v = num(x, 0)
    return int(v) if v is not None else 0


def realized_vol(closes, window):
    """Vol anualizada de los últimos `window` retornos log diarios."""
    c = [float(x) for x in closes if x is not None and float(x) > 0]
    c = c[-(window + 1):]
    if len(c) < max(6, window // 3):
        return None
    r = [math.log(c[i] / c[i - 1]) for i in range(1, len(c))]
    if len(r) < 3:
        return None
    m = sum(r) / len(r)
    var = sum((x - m) ** 2 for x in r) / (len(r) - 1)
    return num(math.sqrt(var * 252.0), 4)


def days_to(expiry):
    """Días naturales de hoy al vencimiento 'YYYY-MM-DD' (mínimo 0)."""
    try:
        d = datetime.strptime(expiry, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return max(0, (d - datetime.now(timezone.utc)).days)


def now_iso():
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")


class DataError(Exception):
    """Error de datos que el front puede mostrar tal cual."""


# ------------------------------------------------------------------- caché

_cache = {}
_cache_lock = threading.Lock()


def cached(key, ttl, fn):
    now = time.time()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
    val = fn()
    with _cache_lock:
        _cache[key] = (time.time(), val)
    return val


# --------------------------------------------------- proveedor 1: yfinance

_yf = None
_yf_error = None
_yf_lock = threading.Lock()   # yfinance no promete ser thread-safe


def yf_mod():
    """Importa yfinance una sola vez. Devuelve None si no está disponible."""
    global _yf, _yf_error
    if _yf is None and _yf_error is None:
        try:
            import yfinance  # noqa: PLC0415  (import perezoso a propósito)
            _yf = yfinance
        except Exception as exc:                      # pragma: no cover
            _yf_error = "%s: %s" % (type(exc).__name__, exc)
    return _yf


def _yf_spot(t):
    """Último precio por la vía más barata que responda."""
    try:
        fi = t.fast_info
        for k in ("lastPrice", "last_price", "regularMarketPrice"):
            try:
                v = num(fi[k], 4)
            except Exception:
                v = None
            if v:
                return v
    except Exception:
        pass
    try:
        h = t.history(period="5d", interval="1d", auto_adjust=False)
        if len(h):
            return num(h["Close"].dropna().iloc[-1], 4)
    except Exception:
        pass
    return None


def _yf_fast(t, *keys):
    try:
        fi = t.fast_info
    except Exception:
        return None
    for k in keys:
        try:
            v = fi[k]
        except Exception:
            continue
        if v is not None:
            return v
    return None


def yf_load(sym):
    yf = yf_mod()
    if yf is None:
        raise DataError("yfinance no está instalado")
    with _yf_lock:
        t = yf.Ticker(sym)
        price = _yf_spot(t)
        if not price:
            raise DataError("sin precio para %s" % sym)
        prev = num(_yf_fast(t, "previousClose", "previous_close"), 4)
        cur = _yf_fast(t, "currency") or "USD"
        try:
            expiries = list(t.options or ())
        except Exception:
            expiries = []
        closes = []
        try:
            h = t.history(period="6mo", interval="1d", auto_adjust=True)
            closes = [float(x) for x in h["Close"].dropna().tolist()]
        except Exception:
            closes = []
    return {
        "symbol": sym.upper(),
        "price": price,
        "prevClose": prev,
        "currency": cur,
        "expiries": expiries,
        "rv20": realized_vol(closes, 20),
        "rv60": realized_vol(closes, 60),
        "provider": "yfinance",
    }


def _row(d):
    """Normaliza una fila de la cadena (dict con las claves de yfinance)."""
    k = num(d.get("strike"), 4)
    if k is None:
        return None
    bid, ask = num(d.get("bid"), 4), num(d.get("ask"), 4)
    last = num(d.get("lastPrice"), 4)
    if bid and ask and ask >= bid:
        mid, src = num((bid + ask) / 2.0, 4), "mid"
    elif last:
        mid, src = last, "last"
    elif bid or ask:
        mid, src = (bid or ask), "quote"
    else:
        return None
    return {
        "k": k, "bid": bid, "ask": ask, "last": last, "mid": mid, "src": src,
        "iv": num(d.get("impliedVolatility"), 4),
        "vol": whole(d.get("volume")),
        "oi": whole(d.get("openInterest")),
    }


def yf_chain(sym, expiry):
    yf = yf_mod()
    if yf is None:
        raise DataError("yfinance no está instalado")
    with _yf_lock:
        t = yf.Ticker(sym)
        try:
            ch = t.option_chain(expiry)
        except Exception as exc:
            raise DataError("sin cadena para %s %s (%s)" % (sym, expiry, exc))
        calls = ch.calls.to_dict("records")
        puts = ch.puts.to_dict("records")
        under = getattr(ch, "underlying", None) or {}
        spot = num(under.get("regularMarketPrice"), 4) or _yf_spot(t)
        name = under.get("shortName") or under.get("longName")
    return _pack_chain(sym, expiry, spot, calls, puts, name, "yfinance")


# ------------------------------------------- proveedor 2: Yahoo por urllib

_opener = None
_opener_lock = threading.Lock()


def opener():
    """Opener con cookies. Yahoo devuelve 429 a secas si no hay sesión."""
    global _opener
    with _opener_lock:
        if _opener is None:
            jar = http.cookiejar.CookieJar()
            _opener = urllib.request.build_opener(
                urllib.request.HTTPCookieProcessor(jar))
            _opener.addheaders = [("User-Agent", UA),
                                  ("Accept", "application/json,text/plain,*/*")]
            for warm in ("https://fc.yahoo.com/",
                         "https://finance.yahoo.com/quote/SPY"):
                try:
                    _opener.open(warm, timeout=15).read(1)
                except Exception:
                    pass
    return _opener


def http_json(url):
    try:
        with opener().open(url, timeout=20) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        raise DataError("Yahoo respondió HTTP %s" % exc.code)
    except Exception as exc:
        raise DataError("red: %s" % exc)


def yh_options(sym, epoch=None):
    url = "https://query2.finance.yahoo.com/v7/finance/options/%s" % urllib.parse.quote(sym)
    if epoch:
        url += "?date=%d" % epoch
    j = http_json(url)
    res = ((j.get("optionChain") or {}).get("result") or [])
    if not res:
        err = ((j.get("optionChain") or {}).get("error") or {})
        raise DataError(err.get("description") or "Yahoo no devolvió datos para %s" % sym)
    return res[0]


def yh_load(sym):
    r = yh_options(sym)
    q = r.get("quote") or {}
    price = num(q.get("regularMarketPrice"), 4)
    if not price:
        raise DataError("sin precio para %s" % sym)
    expiries = [datetime.fromtimestamp(e, timezone.utc).strftime("%Y-%m-%d")
                for e in (r.get("expirationDates") or [])]
    closes = []
    try:
        url = ("https://query1.finance.yahoo.com/v8/finance/chart/%s"
               "?range=6mo&interval=1d" % urllib.parse.quote(sym))
        ch = ((http_json(url).get("chart") or {}).get("result") or [{}])[0]
        closes = [c for c in (((ch.get("indicators") or {}).get("quote") or [{}])[0]
                              .get("close") or []) if c]
    except DataError:
        closes = []
    return {
        "symbol": sym.upper(),
        "price": price,
        "prevClose": num(q.get("regularMarketPreviousClose"), 4),
        "currency": q.get("currency") or "USD",
        "name": q.get("shortName") or q.get("longName"),
        "expiries": expiries,
        "rv20": realized_vol(closes, 20),
        "rv60": realized_vol(closes, 60),
        "provider": "yahoo-http",
    }


def yh_chain(sym, expiry):
    try:
        epoch = int(datetime.strptime(expiry, "%Y-%m-%d")
                    .replace(tzinfo=timezone.utc).timestamp())
    except ValueError:
        raise DataError("vencimiento inválido: %s" % expiry)
    r = yh_options(sym, epoch)
    opts = (r.get("options") or [{}])[0]
    q = r.get("quote") or {}
    return _pack_chain(sym, expiry, num(q.get("regularMarketPrice"), 4),
                       opts.get("calls") or [], opts.get("puts") or [],
                       q.get("shortName"), "yahoo-http")


# ----------------------------------------------------- armado de la cadena

MAX_STRIKES = 90   # por lado; SPY lista ~300 y el lab no necesita esa cola


def _trim(rows, spot):
    """Se queda con los strikes más cercanos al spot. Móvil, datos, batería."""
    if not spot or len(rows) <= MAX_STRIKES:
        return rows, len(rows)
    near = sorted(rows, key=lambda x: abs(x["k"] - spot))[:MAX_STRIKES]
    near.sort(key=lambda x: x["k"])
    return near, len(rows)


def _pack_chain(sym, expiry, spot, calls, puts, name, provider):
    c = [x for x in (_row(d) for d in calls) if x]
    p = [x for x in (_row(d) for d in puts) if x]
    c.sort(key=lambda x: x["k"])
    p.sort(key=lambda x: x["k"])
    if not c and not p:
        raise DataError("la cadena de %s %s vino vacía" % (sym, expiry))
    listed = len(c) + len(p)
    c, _ = _trim(c, spot)
    p, _ = _trim(p, spot)

    atm_iv = None
    if spot:
        near = sorted(
            [x for x in c + p if x["iv"]],
            key=lambda x: abs(x["k"] - spot))[:4]
        ivs = [x["iv"] for x in near]
        if ivs:
            atm_iv = num(sum(ivs) / len(ivs), 4)

    d = days_to(expiry)
    sigma1 = None
    if spot and atm_iv and d is not None:
        sigma1 = num(spot * atm_iv * math.sqrt(max(d, 1) / 365.0), 4)

    return {
        "symbol": sym.upper(), "expiry": expiry, "days": d, "spot": spot,
        "name": name, "atmIV": atm_iv, "sigma1": sigma1,
        "calls": c, "puts": p, "listed": listed, "shown": len(c) + len(p),
        "provider": provider, "asof": now_iso(),
    }


# ------------------------------------------------------ tasa libre de riesgo

RFR_DEFAULT = 0.043      # respaldo si ^IRX no contesta (T-bill 13s, aprox.)


def _quote_last(sym):
    """Último precio de un símbolo por la vía que haya. Sirve para índices como
    ^IRX (rendimiento del T-bill a 13 semanas, cotizado en %)."""
    yf = yf_mod()
    if yf is not None:
        try:
            with _yf_lock:
                fi = yf.Ticker(sym).fast_info
            for k in ("lastPrice", "last_price", "regularMarketPrice"):
                try:
                    v = num(fi[k], 6)
                except Exception:
                    v = None
                if v is not None:
                    return v
        except Exception:
            pass
    try:
        url = ("https://query1.finance.yahoo.com/v8/finance/chart/%s"
               "?range=5d&interval=1d" % urllib.parse.quote(sym))
        meta = (((http_json(url).get("chart") or {}).get("result") or [{}])[0]
                .get("meta") or {})
        return num(meta.get("regularMarketPrice"), 6)
    except Exception:
        return None


# Mapa de tenores de la curva del Tesoro (nombre de columna -> años).
TENOR_YEARS = {
    "1 Mo": 1/12., "1.5 Month": 1.5/12., "2 Mo": 2/12., "3 Mo": 0.25,
    "4 Mo": 4/12., "6 Mo": 0.5, "1 Yr": 1.0, "2 Yr": 2.0, "3 Yr": 3.0,
    "5 Yr": 5.0, "7 Yr": 7.0, "10 Yr": 10.0, "20 Yr": 20.0, "30 Yr": 30.0,
}

# Respaldo si no hay red: curva razonable (ago-2026), tasas en decimal.
STATIC_CURVE = {"points": [[0.0833, 0.038], [0.25, 0.039], [0.5, 0.040],
                           [1.0, 0.040], [2.0, 0.042], [5.0, 0.044],
                           [10.0, 0.047], [30.0, 0.053]],
                "asof": "respaldo", "src": "default"}


def _curve_treasury_gov():
    """Curva de rendimientos par del Tesoro de EE.UU.: todos los tenores en una
    sola descarga, fuente oficial. La primera fila del CSV es el día más reciente."""
    yr = datetime.now().year
    url = ("https://home.treasury.gov/resource-center/data-chart-center/"
           "interest-rates/daily-treasury-rates.csv/%d/all"
           "?type=daily_treasury_yield_curve&field_tdr_date_value=%d"
           "&page&_format=csv" % (yr, yr))
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as r:
        text = r.read().decode("utf-8", "replace")
    rows = list(csv.reader(io.StringIO(text)))
    if len(rows) < 2:
        raise DataError("Treasury: CSV sin datos")
    header, data = rows[0], rows[1]
    pts = []
    for i, col in enumerate(header):
        yrs = TENOR_YEARS.get(col.strip())
        if yrs is None or i >= len(data):
            continue
        v = num(data[i], 6)
        if v is not None and v > 0:
            pts.append([yrs, round(v / 100.0, 6)])
    if not pts:
        raise DataError("Treasury: sin puntos numéricos")
    pts.sort(key=lambda p: p[0])
    return {"points": pts, "asof": data[0], "src": "treasury.gov"}


def _curve_yahoo_cmt():
    """Respaldo: índices de tesorería a vencimiento constante de Yahoo. Curva
    gruesa (13s / 5a / 10a / 30a) pero alcanza para interpolar."""
    pts = []
    for sym, yrs in (("^IRX", 0.25), ("^FVX", 5.0), ("^TNX", 10.0), ("^TYX", 30.0)):
        v = _quote_last(sym)
        if v is not None and 0 < v <= 25:
            pts.append([yrs, round(v / 100.0, 6)])
    if not pts:
        raise DataError("Yahoo CMT: sin puntos")
    pts.sort(key=lambda p: p[0])
    return {"points": pts, "asof": now_iso(), "src": "yahoo-cmt"}


def treasury_curve():
    """Curva del Tesoro (letras/notas) por tenor. Treasury.gov → Yahoo CMT →
    respaldo estático. Se cachea; cambia una vez por día hábil."""
    for fn in (_curve_treasury_gov, _curve_yahoo_cmt):
        try:
            return fn()
        except Exception:
            continue
    return dict(STATIC_CURVE)


def curve_rate(cur, years):
    """Interpola linealmente la tasa (decimal) al plazo `years`. Plana fuera de
    los extremos de la curva."""
    pts = (cur or {}).get("points") or []
    if not pts:
        return None
    if years <= pts[0][0]:
        return pts[0][1]
    if years >= pts[-1][0]:
        return pts[-1][1]
    for i in range(1, len(pts)):
        x0, y0 = pts[i - 1]
        x1, y1 = pts[i]
        if years <= x1:
            return round(y0 + (y1 - y0) * (years - x0) / (x1 - x0), 6)
    return pts[-1][1]


# -------------------------------------------------- dividendos (discretos)

def _dividends_schedule(sym):
    """Próximos ex-dividendos ESTIMADOS proyectando la cadencia histórica.
    yfinance da el historial; para muchos ETF apalancados info.exDividendDate
    viene vacío, así que se proyecta desde el último pago con la cadencia y el
    monto reciente (mediana). Todo marcado como estimado."""
    import datetime as _dt
    yf = yf_mod()
    if yf is None:
        raise DataError("sin yfinance para dividendos")
    with _yf_lock:
        s = yf.Ticker(sym).dividends
    items = [(d.date(), float(a)) for d, a in s.items() if float(a) > 0]
    if not items:
        return {"divs": [], "estimated": True, "src": "sin-historial"}
    items = items[-8:]
    dates = [d for d, _ in items]
    amts = sorted(a for _, a in items[-4:])
    amt = amts[len(amts) // 2]                      # mediana reciente
    gaps = sorted((dates[i] - dates[i - 1]).days for i in range(1, len(dates)))
    step = gaps[len(gaps) // 2] if gaps else 91
    if step < 20:
        step = 91
    today = _dt.date.today()
    out, nd = [], dates[-1]
    for _ in range(16):
        nd = nd + _dt.timedelta(days=step)
        if nd <= today:
            continue
        out.append({"date": nd.isoformat(), "amount": round(amt, 4)})
        if (nd - today).days > 760:
            break
    return {"divs": out, "estimated": True, "src": "yfinance-proyectado",
            "cadenceDays": step, "amount": round(amt, 4), "asof": now_iso()}


# ------------------------------------------------------- despacho unificado

def provider_chain():
    """Orden de intento: yfinance primero, HTTP crudo después."""
    if yf_mod() is not None:
        return [("yfinance", yf_load, yf_chain), ("yahoo-http", yh_load, yh_chain)]
    return [("yahoo-http", yh_load, yh_chain)]


def fetch(kind, sym, expiry=None):
    errors = []
    for name, load, chain in provider_chain():
        try:
            return (chain(sym, expiry) if kind == "chain" else load(sym))
        except DataError as exc:
            errors.append("%s → %s" % (name, exc))
        except Exception as exc:                      # pragma: no cover
            errors.append("%s → %s: %s" % (name, type(exc).__name__, exc))
    raise DataError(" · ".join(errors) or "sin proveedor de datos")


# ---------------------------------------------------------------- servidor

STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/rivendel.css": ("rivendel.css", "text/css; charset=utf-8"),
}


class Handler(BaseHTTPRequestHandler):
    server_version = "Rivendel/1.0"
    protocol_version = "HTTP/1.1"

    # ------------------------------------------------------------ helpers
    def _send(self, code, body, ctype, extra=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False, allow_nan=False),
                   "application/json; charset=utf-8")

    def _q(self):
        parts = urllib.parse.urlsplit(self.path)
        return parts.path, urllib.parse.parse_qs(parts.query)

    def _ticker(self, q):
        t = (q.get("t") or [""])[0].strip()
        if not TICKER_RE.match(t):
            raise DataError("ticker inválido: %r" % t)
        return t.upper()

    # -------------------------------------------------------------- rutas
    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        path, q = self._q()
        try:
            if path.startswith("/api/"):
                return self._api(path, q)
            if path in STATIC:
                fname, ctype = STATIC[path]
                with open(os.path.join(WEB, fname), "rb") as fh:
                    return self._send(200, fh.read(), ctype)
            self._send(404, "no such thing\n", "text/plain; charset=utf-8")
        except DataError as exc:
            self._json({"error": str(exc)}, 502)
        except BrokenPipeError:                        # pragma: no cover
            pass
        except Exception as exc:                       # pragma: no cover
            self._json({"error": "%s: %s" % (type(exc).__name__, exc)}, 500)

    def _api(self, path, q):
        if path == "/api/health":
            yf = yf_mod()
            return self._json({
                "ok": True,
                "yfinance": getattr(yf, "__version__", None) if yf else None,
                "yfinanceError": _yf_error,
                "provider": provider_chain()[0][0],
                "python": sys.version.split()[0],
                "asof": now_iso(),
            })

        if path == "/api/curve":
            try:
                cur = cached("curve", TTL_CURVE, treasury_curve)
            except Exception:
                cur = dict(STATIC_CURVE)
            return self._json(cur)

        if path == "/api/div":
            sym = self._ticker(q)
            try:
                return self._json(cached("div:" + sym, TTL_CURVE,
                                         lambda: _dividends_schedule(sym)))
            except Exception as exc:
                return self._json({"divs": [], "estimated": True, "error": str(exc)})

        if path == "/api/load":
            sym = self._ticker(q)
            data = cached("load:" + sym, TTL_QUOTE, lambda: fetch("load", sym))
            out = dict(data)
            out["asof"] = now_iso()
            if out.get("price") and out.get("prevClose"):
                out["changePct"] = num(
                    (out["price"] / out["prevClose"] - 1.0) * 100.0, 3)
            try:
                cur = cached("curve", TTL_CURVE, treasury_curve)
                out["riskFree"] = curve_rate(cur, 0.25)   # ~3 meses, referencia
                out["riskFreeSrc"] = cur.get("src")
            except Exception:
                out["riskFree"] = RFR_DEFAULT
                out["riskFreeSrc"] = "default"
            return self._json(out)

        if path == "/api/chain":
            sym = self._ticker(q)
            exp = (q.get("e") or [""])[0].strip()
            if not re.match(r"^\d{4}-\d{2}-\d{2}$", exp):
                raise DataError("vencimiento inválido: %r" % exp)
            key = "chain:%s:%s" % (sym, exp)
            return self._json(cached(key, TTL_CHAIN,
                                     lambda: fetch("chain", sym, exp)))

        self._json({"error": "endpoint desconocido: %s" % path}, 404)

    # ----------------------------------------------------------- logging
    def log_message(self, fmt, *args):
        sys.stderr.write("  %s  %s\n" % (time.strftime("%H:%M:%S"), fmt % args))


def main():
    ap = argparse.ArgumentParser(description="Payoff Lab local con datos reales")
    ap.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8765)))
    a = ap.parse_args()

    try:                      # que el banner salga aunque redirijas a un log
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:
        pass

    try:
        srv = ThreadingHTTPServer((a.host, a.port), Handler)
    except OSError as exc:
        if exc.errno != errno.EADDRINUSE:
            raise
        print("\n  El puerto %d ya está ocupado." % a.port)
        print("  Puede ser este mismo lab, corriendo de antes: probá abrir")
        print("      http://localhost:%d" % a.port)
        print("  Si no, levantalo en otro puerto:")
        print("      PORT=%d bash run.sh\n" % (a.port + 1))
        sys.exit(1)
    srv.daemon_threads = True

    print("\n  Rivendel · Payoff Lab")
    print("  ─────────────────────")
    print("  http://%s:%d" % ("localhost" if a.host == "127.0.0.1" else a.host, a.port))
    print("  ctrl-c para parar\n")

    # yfinance tarda varios segundos en importar (más en un teléfono). Se hace
    # en paralelo para que la página esté servida desde el primer segundo.
    def probe():
        yf = yf_mod()
        if yf:
            print("  datos    yfinance %s" % getattr(yf, "__version__", "?"))
        else:
            print("  datos    Yahoo por HTTP (sin yfinance: %s)" % _yf_error)
    threading.Thread(target=probe, daemon=True).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n  cortado\n")
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
