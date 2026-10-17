"""Núcleo del modelo DCF (FCFF a dos etapas, estilo Damodaran).

Port fiel de StockValuation.io §3–§5 a módulo importable, sin Jupyter ni
matplotlib. Solo requiere numpy. La capa de datos vive en dcf.data y los
inputs macro en dcf.macro.
"""
import numpy as np
from dataclasses import dataclass, field, asdict
import datetime as dt

# --- Supuestos por defecto del modelo (overridables) ---
DEFAULTS = dict(
    years=10,              # horizonte explícito
    high_growth_years=5,   # años de crecimiento alto antes de la convergencia
    rev_growth=None,       # crecim. de ingresos fase alta (None => DERIVADO de datos)
    target_margin=None,    # margen operativo objetivo (None => usa el margen actual)
    sales_to_capital=None, # ventas/capital (None => Ingresos/Capital Invertido propio)
    rf=None,               # tasa libre de riesgo (None => macro en vivo)
    erp=None,              # prima de riesgo (None => macro en vivo)
    marginal_tax=0.25,     # tasa marginal terminal
    stable_roic=0.12,      # ROIC en estado estable (terminal)
    mature_beta=1.0,       # beta de la empresa madura (terminal)
    reinvest_mode="sales_to_capital",  # "sales_to_capital" | "net_capex_pct"
    net_capex_pct=None,    # si "net_capex_pct": reinversión como fracción de ingresos
    mid_year=False,        # convención mid-year + año parcial (stub) del año en curso
)

# Últimos recursos (sólo si el dato real no está disponible):
REV_GROWTH_FALLBACK = 0.10   # crecimiento de ingresos si no hay analistas/fundamental/histórico
S2C_FALLBACK        = 2.0    # ventas/capital si no se puede calcular Ingresos/Capital Invertido
RF_FALLBACK, ERP_FALLBACK = 0.0425, 0.045


@dataclass
class CompanyData:
    ticker: str
    name: str
    price: float
    shares: float
    beta: float
    sector: str
    country: str
    currency: str
    revenue: float
    ebit: float
    tax_rate: float        # efectiva (del último FY)
    interest_expense: float
    total_debt: float
    cash: float
    minority: float
    source: str = "yfinance"
    asof: str = ""
    rev_growth_est: float = None        # crecimiento de ingresos derivado de datos
    sales_to_capital_est: float = None  # ventas/capital propio = Ingresos / Capital Invertido
    growth_detail: dict = None          # triangulación: analistas / fundamental / histórico
    is_cyclical: bool = False            # Damodaran: CV(márgenes anuales) >= 1.0
    cycle_detail: dict = None            # set normalizado (método 2), etiquetado

    def to_json(self):
        import json
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, s):
        import json
        return cls(**json.loads(s))


@dataclass
class ValuationOutput:
    ticker: str; price: float; per_share: float; upside: float
    ev: float; equity: float
    wacc_initial: float; wacc_stable: float; g_stable: float
    terminal_value: float; pv_terminal: float
    table: list; assumptions: dict; coc: dict
    source: str = ""; asof: str = ""


# Tabla rating sintético Damodaran (large-cap): (cobertura mínima, rating, spread)
_RATING_TABLE = [
    (8.50, "Aaa/AAA", 0.0069), (6.50, "Aa2/AA", 0.0085), (5.50, "A1/A+", 0.0107),
    (4.25, "A2/A",   0.0118), (3.00, "A3/A-",  0.0133), (2.50, "Baa2/BBB", 0.0171),
    (2.25, "Ba1/BB+",0.0231), (2.00, "Ba2/BB", 0.0277), (1.75, "B1/B+",  0.0397),
    (1.50, "B2/B",   0.0511), (1.25, "B3/B-",  0.0617), (0.80, "Caa/CCC",0.0866),
    (0.65, "Ca2/CC", 0.0950), (0.20, "C2/C",   0.1208), (-1e9, "D2/D",   0.1654),
]

def synthetic_rating(icr: float):
    if not np.isfinite(icr): icr = 1e9
    for lo, rating, spread in _RATING_TABLE:
        if icr >= lo:
            return rating, spread
    return _RATING_TABLE[-1][1], _RATING_TABLE[-1][2]


def cost_of_capital(cd: CompanyData, rf, erp, marginal_tax, beta=None):
    """Ke (CAPM) + Kd vía rating sintético (cobertura de intereses) + WACC."""
    beta = cd.beta if beta is None else beta
    mv_equity = cd.price * cd.shares
    debt = max(cd.total_debt, 0.0)
    icr = (cd.ebit / cd.interest_expense) if cd.interest_expense > 0 else 1e9
    rating, spread = synthetic_rating(icr)
    ke = rf + beta * erp
    kd_pre = rf + spread
    kd_after = kd_pre * (1 - marginal_tax)
    tot = mv_equity + debt
    we, wd = (mv_equity / tot, debt / tot) if tot > 0 else (1.0, 0.0)
    wacc = we * ke + wd * kd_after
    return dict(beta=beta, icr=icr, rating=rating, spread=spread, ke=ke,
                kd_pre=kd_pre, kd_after=kd_after, we=we, wd=wd, wacc=wacc,
                mv_equity=mv_equity, debt=debt)


def _lin(a, b, n):
    if n <= 1: return [b]
    return [a + (b - a) * i / (n - 1) for i in range(n)]


def _resolve(cd: CompanyData, a: dict = None, macro: dict = None) -> dict:
    """Completa supuestos en None con DATOS reales: rf/ERP (macro) y crecimiento
    y sales-to-capital (derivados de la empresa). Cualquier valor explícito manda."""
    A = dict(DEFAULTS); A.update(a or {})
    macro = macro or {}
    if A.get("rf") is None:
        A["rf"] = macro.get("rf", RF_FALLBACK)
    if A.get("erp") is None:
        A["erp"] = macro.get("erp", ERP_FALLBACK)
    if A.get("rev_growth") is None:
        est = getattr(cd, "rev_growth_est", None)
        A["rev_growth"] = est if (est is not None and np.isfinite(est)) else REV_GROWTH_FALLBACK
    if A.get("sales_to_capital") is None:
        est = getattr(cd, "sales_to_capital_est", None)
        A["sales_to_capital"] = est if (est is not None and np.isfinite(est) and est > 0) else S2C_FALLBACK
    return A


def value_company(cd: CompanyData, a: dict = None, macro: dict = None) -> ValuationOutput:
    """Motor DCF: FCFF a dos etapas. `macro` = dict con rf/erp (ver dcf.macro)."""
    A = _resolve(cd, a, macro)
    N, hg = int(A["years"]), int(A["high_growth_years"])
    rf, erp, mtax = A["rf"], A["erp"], A["marginal_tax"]
    base_margin = cd.ebit / cd.revenue if cd.revenue else 0.0
    target_margin = base_margin if A["target_margin"] is None else A["target_margin"]
    s2c = A["sales_to_capital"]
    reinvest_mode = A.get("reinvest_mode", "sales_to_capital")
    ncp = A.get("net_capex_pct")

    coc = cost_of_capital(cd, rf, erp, mtax)
    wacc0 = coc["wacc"]
    g_stable = min(rf, A["rev_growth"])
    coc_stable = cost_of_capital(cd, rf, erp, mtax, beta=A["mature_beta"])
    wacc_stable = max(coc_stable["wacc"], g_stable + 0.005)

    # Sendas (años 1..N): crecimiento constante en fase alta y convergencia a g_stable;
    # margen, impuesto y WACC convergen hacia su valor maduro hacia el año N.
    growth = [A["rev_growth"]] * hg + _lin(A["rev_growth"], g_stable, N - hg + 1)[1:]
    margin = _lin(base_margin, target_margin, N)
    taxsch = [cd.tax_rate] * hg + _lin(cd.tax_rate, mtax, N - hg + 1)[1:]
    waccsch = [wacc0] * hg + _lin(wacc0, wacc_stable, N - hg + 1)[1:]

    # Convención mid-year + año parcial (stub) del año en curso.
    # Por defecto (mid_year=False) el descuento es a fin de año.
    mid = bool(A.get("mid_year"))
    if mid:
        today = dt.date.today()
        stub = (dt.date(today.year, 12, 31) - today).days / 365.0
        stub = min(max(stub, 1e-6), 1.0)
    else:
        stub = 1.0

    rows, prev_rev, pv_sum = [], cd.revenue, 0.0
    disc_end = 1.0   # factor de descuento acumulado al FIN del período corrido
    for i in range(N):
        yr = stub if (mid and i == 0) else 1.0      # duración del período (años)
        rev = prev_rev * (1 + growth[i])            # ingresos anualizados del año i
        ebit = rev * margin[i]
        nopat = ebit * (1 - taxsch[i])
        if reinvest_mode == "net_capex_pct" and ncp is not None:
            reinvest = rev * ncp                    # reinversión = % de ingresos
        else:
            reinvest = (rev - prev_rev) / s2c if s2c else 0.0   # ventas/capital
        fcff = (nopat - reinvest) * yr              # flujo del período (parcial si stub)
        gf = (1 + waccsch[i]) ** yr
        disc_cf = disc_end * ((1 + waccsch[i]) ** (yr / 2)) if mid else disc_end * gf
        disc_end *= gf
        pv = fcff / disc_cf
        pv_sum += pv
        rows.append(dict(anio=i + 1, ingresos=rev, crecimiento=growth[i], margen=margin[i],
                         ebit=ebit, nopat=nopat, reinversion=reinvest, fcff=fcff,
                         wacc=waccsch[i], factor_desc=disc_cf, vp_fcff=pv))
        prev_rev = rev

    # Valor terminal (perpetuidad al final del horizonte; reinversión = g/ROIC)
    rev_T = prev_rev * (1 + g_stable)
    nopat_T = rev_T * target_margin * (1 - mtax)
    reinv_rate_T = g_stable / A["stable_roic"] if A["stable_roic"] > 0 else 0.0
    fcff_T = nopat_T * (1 - reinv_rate_T)
    tv = fcff_T / (wacc_stable - g_stable)
    pv_tv = tv / disc_end

    ev = pv_sum + pv_tv
    equity = ev - cd.total_debt + cd.cash - cd.minority
    per_share = equity / cd.shares if cd.shares else float("nan")
    upside = per_share / cd.price - 1 if cd.price else float("nan")

    return ValuationOutput(
        ticker=cd.ticker, price=cd.price, per_share=per_share, upside=upside,
        ev=ev, equity=equity, wacc_initial=wacc0, wacc_stable=wacc_stable,
        g_stable=g_stable, terminal_value=tv, pv_terminal=pv_tv,
        table=rows, assumptions=A, coc=coc, source=cd.source, asof=cd.asof)


def _solve(cd, key, lo, hi, base=None, macro=None, tol=1e-6, iters=100):
    """Bisección: valor del supuesto `key` en [lo,hi] tal que valor/acción == precio."""
    A = _resolve(cd, base, macro)
    px = cd.price
    def f(x):
        B = dict(A); B[key] = x
        return value_company(cd, B, macro).per_share - px
    flo, fhi = f(lo), f(hi)
    if not (np.isfinite(flo) and np.isfinite(fhi)) or flo * fhi > 0:
        return None                      # el precio no es alcanzable en [lo,hi]
    for _ in range(iters):
        mid = 0.5 * (lo + hi); fm = f(mid)
        if abs(fm) < tol or (hi - lo) < tol:
            return mid
        if flo * fm <= 0: hi, fhi = mid, fm
        else: lo, flo = mid, fm
    return 0.5 * (lo + hi)


def implied_margin(cd, base=None, macro=None, lo=0.0, hi=0.60):
    """Margen operativo de largo plazo (target_margin) implícito en el precio."""
    return _solve(cd, "target_margin", lo, hi, base, macro)


def implied_growth(cd, base=None, macro=None, lo=-0.05, hi=0.40):
    """Crecimiento de ingresos (rev_growth) implícito en el precio."""
    return _solve(cd, "rev_growth", lo, hi, base, macro)


def reverse_dcf(cd, base=None, macro=None):
    """Supuestos implícitos en el precio vs. el caso base. Sin prints: devuelve dict."""
    A = _resolve(cd, base, macro)
    base_margin = cd.ebit / cd.revenue if cd.revenue else float("nan")
    tm0 = base_margin if A["target_margin"] is None else A["target_margin"]
    im, ig = implied_margin(cd, base, macro), implied_growth(cd, base, macro)
    return dict(implied_margin=im, implied_growth=ig,
                base_margin=tm0, base_growth=A["rev_growth"])


def normalized_company(cd):
    """CompanyData con el set normalizado de la cíclica (Damodaran, método 2).

    Requiere cd.is_cyclical y cd.cycle_detail. No toca el resto de la compañía:
    el WACC, la deuda y la caja siguen siendo los observados."""
    from dataclasses import replace
    if not (cd.is_cyclical and cd.cycle_detail):
        raise ValueError("la compañía no tiene set normalizado (no es cíclica)")
    d = cd.cycle_detail
    gdet = dict(cd.growth_detail or {})
    gdet.update({"fuente": "fundamental normalizado (ciclo)",
                 "elegido": d["g_norm"],
                 "metodo": d.get("metodo", ""),
                 "n_years": d.get("n_years"), "cv_margenes": d.get("cv_margenes")})
    return replace(cd,
                   ebit=d["revenue_base"] * d["m_norm"],
                   rev_growth_est=d["g_norm"],
                   sales_to_capital_est=d["s2c_norm"],
                   source=(cd.source or "") + "+norm",
                   growth_detail=gdet)


def value_normalized(cd, macro=None):
    """Valuación DCF con el set normalizado (solo cíclicas)."""
    return value_company(normalized_company(cd), None, macro)


def implied_cycle_margin(cd, macro=None, price=None, lo=0.005, hi=0.80, iters=50):
    """Margen de mitad de ciclo implícito en el precio (solo cíclicas).

    Bisección sobre el modelo normalizado: en cada paso se reconstruye el set
    consistente (ROIC_norm = s2c_norm x margen_nopat; g = mediana(RR) x ROIC).
    Devuelve {implied_margin, value} o None si el precio cae fuera del rango."""
    from dataclasses import replace
    if not (cd.is_cyclical and cd.cycle_detail):
        return None
    price = cd.price if price is None else price
    d0 = cd.cycle_detail
    def _val(m):
        dd = dict(d0)
        dd["m_norm"] = m
        dd["roic_norm"] = dd["s2c_norm"] * m * (1 - cd.tax_rate)
        dd["g_norm"] = float(min(max(dd["rr_med"] * dd["roic_norm"], 0.0), 0.60))
        cdn = normalized_company(replace(cd, cycle_detail=dd))
        return value_company(cdn, None, macro).per_share
    try:
        vlo, vhi = _val(lo), _val(hi)
    except Exception:
        return None
    if not (np.isfinite(vlo) and np.isfinite(vhi)) or not (vlo <= price <= vhi):
        return None
    for _ in range(iters):
        mid = (lo + hi) / 2
        if _val(mid) < price:
            lo = mid
        else:
            hi = mid
    m_imp = (lo + hi) / 2
    return {"implied_margin": m_imp, "value": _val(m_imp)}


def sensitivity(cd, base_assumptions=None, macro=None, growths=None, margins=None):
    """Matriz del valor intrínseco por acción variando crecimiento (filas) y
    margen objetivo (columnas). Devuelve (growths, margins, M) como listas."""
    A0 = _resolve(cd, base_assumptions, macro)
    base_margin = cd.ebit / cd.revenue if cd.revenue else 0.10
    tm0 = base_margin if A0["target_margin"] is None else A0["target_margin"]
    growths = growths if growths is not None else [A0["rev_growth"] + d for d in (-0.04, -0.02, 0, 0.02, 0.04)]
    margins = margins if margins is not None else [max(0.01, tm0 + d) for d in (-0.06, -0.03, 0, 0.03, 0.06)]
    M = np.zeros((len(growths), len(margins)))
    for i, g in enumerate(growths):
        for j, m in enumerate(margins):
            A = dict(A0); A.update(rev_growth=g, target_margin=m)
            M[i, j] = value_company(cd, A, macro).per_share
    return growths, margins, M.tolist()
