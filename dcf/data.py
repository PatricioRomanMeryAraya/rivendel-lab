"""Capa de datos: CompanyData vía yfinance (con timeout), con respaldo en
cache SQLite y fixture ilustrativo. El crecimiento de ingresos se triangula
con las 3 vías de Damodaran (analistas -> fundamental -> histórico).

Solo numpy en el top-level; yfinance se importa perezosamente dentro de
_fetch_live. Los DataFrames de yfinance se consumen por duck-typing (no se
importa pandas aquí).
"""
import numpy as np

from .model import (CompanyData, REV_GROWTH_FALLBACK)
from .store import save_company, load_company

# --- Fixture de respaldo (AAPL, cifras ilustrativas FY24, USD) ---
_FIXTURES = {
    "AAPL": CompanyData(
        ticker="AAPL", name="Apple Inc. (fixture ilustrativo)", price=230.0,
        shares=15.20e9, beta=1.20, sector="Technology", country="United States",
        currency="USD", revenue=391.0e9, ebit=123.2e9, tax_rate=0.241,
        interest_expense=3.8e9, total_debt=106.0e9, cash=65.0e9, minority=0.0,
        source="fixture", asof="FY2024-ilustrativo"),
}


def _latest(df, *names):
    """Primer valor no-NaN (columna más reciente) entre filas candidatas de un
    DataFrame yfinance."""
    if df is None or getattr(df, "empty", True):
        return float("nan")
    for n in names:
        if n in df.index:
            row = df.loc[n]
            row = row.dropna()
            if len(row):
                return float(row.iloc[0])
    return float("nan")


def _cagr(series):
    """CAGR geométrico de una serie de ingresos (ordenada más reciente primero)."""
    s = [float(x) for x in series if np.isfinite(x) and x > 0]
    if len(s) < 2:
        return float("nan")
    new, old, n = s[0], s[-1], len(s) - 1
    return (new / old) ** (1.0 / n) - 1.0


def _invested_capital(bs, debt, cash):
    eq = _latest(bs, "Stockholders Equity", "Total Stockholder Equity", "Common Stock Equity")
    ic = (debt or 0.0) + (eq if np.isfinite(eq) else 0.0) - (cash or 0.0)
    return ic if np.isfinite(ic) else float("nan")


# --- Base rates de crecimiento (Mauboussin / Counterpoint Global, Morgan Stanley,
# "Competitive Advantage Period: The Neglected Value Driver", abr-2026, Exhibit 18) ---
# Distribución del CAGR de ventas a 3 años condicionada al CAGR de ventas de los 3 años
# previos. Universo: compañías públicas de EEUU 1950-2025, cifras nominales, ventas
# mínimas USD 10M (de 2025). Persistencia r = 0.16 (3a) / 0.14 (5a): regresión a la
# media muy fuerte; media, mediana y desvío caen con el tamaño de la firma.
# Cada fila: (cota_inf, cota_sup, mediana, media, P(CAGR siguiente >= 20%), etiqueta).
# Se usa como TOPE automático al crecimiento del modelo (outside view): el crecimiento
# elegido nunca supera la mediana de su fila. El horizonte del modelo es 5 años pero la
# tabla es a 3; como la persistencia a 5a (0.14) es aún menor que a 3a (0.16), usar la
# mediana a 3a como tope es, si acaso, generoso.
_BASERATE_ROWS = [
    (float("-inf"), -0.12, 0.050, 0.096, 0.23, "<-12%"),
    (-0.12, -0.08, 0.038, 0.054, 0.14, "-12% a -8%"),
    (-0.08, -0.04, 0.039, 0.053, 0.13, "-8% a -4%"),
    (-0.04,  0.00, 0.039, 0.054, 0.12, "-4% a 0%"),
    ( 0.00,  0.04, 0.049, 0.061, 0.10, "0% a 4%"),
    ( 0.04,  0.08, 0.063, 0.072, 0.09, "4% a 8%"),
    ( 0.08,  0.12, 0.079, 0.086, 0.14, "8% a 12%"),
    ( 0.12,  0.16, 0.093, 0.097, 0.15, "12% a 16%"),
    ( 0.16,  0.20, 0.102, 0.108, 0.20, "16% a 20%"),
    ( 0.20,  0.24, 0.109, 0.114, 0.23, "20% a 24%"),
    ( 0.24,  0.28, 0.117, 0.125, 0.28, "24% a 28%"),
    ( 0.28,  0.32, 0.117, 0.126, 0.28, "28% a 32%"),
    ( 0.32,  0.36, 0.125, 0.136, 0.32, "32% a 36%"),
    ( 0.36,  0.40, 0.124, 0.141, 0.34, "36% a 40%"),
    ( 0.40,  0.44, 0.132, 0.148, 0.36, "40% a 44%"),
    ( 0.44, float("inf"), 0.133, 0.158, 0.41, ">44%"),
]


def _base_rate_brake(inc):
    """Tope de Mauboussin al crecimiento: mediana base-rate de la fila que
    corresponde al CAGR de ingresos de los 3 años previos (anual yfinance).

    Todo el input sale de yfinance; la tabla está hardcodeada arriba con su fuente.
    Devuelve (tope, etiqueta_fila, cagr_previo_3a, p_sostener_ge20).
    Tope None si no hay 4 anuales con ingresos positivos."""
    rev = None
    try:
        for name in ("Total Revenue", "TotalRevenue", "OperatingRevenue"):
            if inc is not None and not getattr(inc, "empty", True) and name in inc.index:
                row = inc.loc[name].dropna().values
                vals = [float(x) for x in row[:4] if np.isfinite(x) and x > 0]
                if len(vals) == 4:
                    rev = vals
                break
    except Exception:
        rev = None
    if rev is None:
        return None, None, float("nan"), float("nan")
    prior = (rev[0] / rev[3]) ** (1.0 / 3.0) - 1.0
    for lo, hi, med, mean, p20, lab in _BASERATE_ROWS:
        if lo <= prior < hi:
            return med, lab, prior, p20
    return None, None, prior, float("nan")


def _estimate_growth_s2c(t, inc, bs, info, revenue, ebit, tax, debt, cash, flows=None):
    """Devuelve (rev_growth, sales_to_capital, detalle) — crecimiento estilo
    Damodaran, 3 vías: analistas -> fundamental (RR x ROIC) -> histórico (CAGR).

    `flows` (opcional): dict con capex/dep/cwc del MISMO período que revenue/ebit
    (p.ej. TTM). Si no se da, salen del cashflow anual de yfinance.
    `bs` se usa para el capital invertido (debe ser del mismo período).
    El CAGR histórico siempre usa el `inc` anual multi-año."""
    det = {}
    # (1) Analistas: crecimiento de ingresos del próximo año fiscal (consenso forward).
    analyst = float("nan")
    try:
        re = t.revenue_estimate
        if re is not None and not getattr(re, "empty", True) and "growth" in re.columns:
            for per in ("+1y", "0y"):
                if per in re.index:
                    gv = float(re.loc[per, "growth"])
                    if np.isfinite(gv) and -0.5 <= gv <= 1.5:
                        analyst = gv; break
    except Exception:
        pass
    if not np.isfinite(analyst):
        rg = info.get("revenueGrowth")
        try:
            if rg is not None and np.isfinite(float(rg)) and -0.5 <= float(rg) <= 1.0:
                analyst = float(rg)
        except Exception:
            pass
    det["analistas"] = analyst
    # (2) Histórico: CAGR de ingresos.
    hist = float("nan")
    try:
        for name in ("Total Revenue", "TotalRevenue", "OperatingRevenue"):
            if inc is not None and not getattr(inc, "empty", True) and name in inc.index:
                hist = _cagr(inc.loc[name].dropna().values); break
    except Exception:
        pass
    det["historico"] = hist
    # (3) Fundamental (Damodaran): g = RR x ROIC.
    fund = roic = rr = float("nan")
    try:
        if flows is not None:
            capex, dep, cwc = flows["capex"], flows["dep"], flows["cwc"]
        else:
            cf = t.cashflow
            capex = _latest(cf, "Capital Expenditure", "CapitalExpenditure")
            dep   = _latest(cf, "Depreciation And Amortization",
                            "Depreciation Amortization Depletion", "Depreciation")
            cwc   = _latest(cf, "Change In Working Capital", "ChangeInWorkingCapital")
        nopat = ebit * (1 - tax)
        ic = _invested_capital(bs, debt, cash)
        if np.isfinite(nopat) and nopat > 0 and np.isfinite(ic) and ic > 0:
            net_capex = max((abs(capex) if np.isfinite(capex) else 0.0) - (dep if np.isfinite(dep) else 0.0), 0.0)
            dwc = max(-cwc, 0.0) if np.isfinite(cwc) else 0.0    # aumento de WC = reinversión
            rr = (net_capex + dwc) / nopat
            roic = nopat / ic
            fund = max(min(rr, 1.5), 0.0) * min(roic, 0.30)      # normaliza retornos excesivos
    except Exception:
        pass
    det["fundamental"] = fund; det["roic"] = roic; det["reinv_rate"] = rr
    # (4) Sales-to-capital propio = Ingresos / Capital Invertido.
    s2c = float("nan")
    try:
        ic = _invested_capital(bs, debt, cash)
        if np.isfinite(ic) and ic > 0 and revenue:
            s2c = min(max(revenue / ic, 0.3), 6.0)
    except Exception:
        pass
    det["sales_to_capital"] = s2c
    # Elección por defecto: analistas -> fundamental -> histórico -> constante.
    pick, src = None, None
    if np.isfinite(analyst) and -0.3 <= analyst <= 0.80:
        pick, src = analyst, "analistas (consenso fwd)"
    elif np.isfinite(fund) and 0.0 <= fund <= 0.40:
        pick, src = fund, "fundamental (RR x ROIC)"
    elif np.isfinite(hist) and -0.3 <= hist <= 0.80:
        pick, src = hist, "histórico (CAGR)"
    if pick is None:
        pick, src = REV_GROWTH_FALLBACK, "fallback-constante"
    pick = float(min(max(pick, -0.20), 0.60))
    det["fuente"] = src; det["elegido"] = pick
    # (5) Freno de Mauboussin (outside view): el crecimiento elegido no supera la
    # mediana base-rate de su fila histórica. Todo sale de yfinance (4 anuales).
    br_cap, br_row, br_prior, br_p20 = _base_rate_brake(inc)
    det["base_rate_previo_3a"] = br_prior
    det["base_rate_fila"] = br_row
    det["base_rate_tope"] = br_cap
    det["base_rate_p_sostener_20"] = br_p20
    det["freno_base_rate"] = False
    if br_cap is not None and pick > br_cap:
        det["freno_base_rate"] = True
        det["fuente_previa_freno"] = src
        det["elegido_previo_freno"] = pick
        pick = float(br_cap)
        src = "base-rate Mauboussin (tope)"
        det["fuente"] = src; det["elegido"] = pick
    return pick, (float(s2c) if np.isfinite(s2c) else None), det


def _ttm_set(t):
    """Set TTM desde los últimos 4 trimestres, con fechas alineadas.

    Devuelve dict con revenue, ebit, pretax, taxprov, intexp, capex, dep, cwc
    (sumas TTM), debt/cash/minority (último balance trimestral), bs (balance
    trimestral para capital invertido) y latest (fecha del último trimestre).
    Devuelve None si no hay 4 trimestres con revenue/EBIT finitos.

    Regla anti-deriva: el que llama debe usar TODO el set junto (o el anual
    completo), nunca mezclar flujos TTM con balance anual ni viceversa.
    """
    qi = getattr(t, "quarterly_income_stmt", None)
    if qi is None or getattr(qi, "empty", True) or len(qi.columns) < 4:
        return None
    cols = list(qi.columns[:4])
    try:
        latest = cols[0].date().isoformat()
    except Exception:
        return None

    def _sum4(df, *names):
        if df is None or getattr(df, "empty", True):
            return float("nan")
        for n in names:
            if n in df.index:
                try:
                    row = df.loc[n]
                    vals = [float(row[c]) for c in cols if c in df.columns]
                except Exception:
                    return float("nan")
                return float(np.nansum(vals)) if vals else float("nan")
        return float("nan")

    qc = getattr(t, "quarterly_cashflow", None)
    qb = getattr(t, "quarterly_balance_sheet", None)
    rev = _sum4(qi, "Total Revenue", "TotalRevenue", "OperatingRevenue")
    ebit = _sum4(qi, "Operating Income", "OperatingIncome", "EBIT")
    if not (np.isfinite(rev) and np.isfinite(ebit)):
        return None
    out = dict(
        revenue=rev, ebit=ebit,
        pretax=_sum4(qi, "Pretax Income", "PretaxIncome"),
        taxprov=_sum4(qi, "Tax Provision", "TaxProvision", "IncomeTaxExpense"),
        intexp=abs(_sum4(qi, "Interest Expense", "InterestExpense",
                         "Interest Expense Non Operating")),
        capex=_sum4(qc, "Capital Expenditure", "CapitalExpenditure"),
        dep=_sum4(qc, "Depreciation And Amortization",
                  "Depreciation Amortization Depletion", "Depreciation"),
        cwc=_sum4(qc, "Change In Working Capital", "ChangeInWorkingCapital"),
        latest=latest, bs=qb,
    )
    # Balance: última columna trimestral (punto en el tiempo, misma fecha que latest
    # si el balance está al día; si no hay balance trimestral, NaN -> el que llama
    # decide el respaldo).
    debt = cash = minority = float("nan")
    if qb is not None and not getattr(qb, "empty", True) and len(qb.columns):
        bcol = qb.columns[0]
        def _b(*names):
            for n in names:
                if n in qb.index:
                    try: return float(qb.loc[n][bcol])
                    except Exception: return float("nan")
            return float("nan")
        debt = _b("Total Debt", "TotalDebt")
        if not np.isfinite(debt):
            ltd = _b("Long Term Debt", "LongTermDebt")
            cur = _b("Current Debt", "CurrentDebt")
            debt = float(np.nansum([ltd, cur])) if (np.isfinite(ltd) or np.isfinite(cur)) else float("nan")
        cash = _b("Cash Cash Equivalents And Short Term Investments",
                  "Cash And Cash Equivalents", "CashAndCashEquivalents")
        minority = _b("Minority Interest", "MinorityInterest")
    out.update(debt=debt, cash=cash, minority=minority)
    return out


# --- Normalización de cíclicas (Damodaran, "Ups and Downs", 2009) ---
# Regla: no pronosticar el ciclo ("fútil y peligroso"); normalizar ganancias,
# flujos y crecimiento a mitad de ciclo. Detección automática y conservadora:
# la oscilación de márgenes debe ser del orden de la media (CV >= 1.0).
CYCLICAL_CV_MIN = 1.0
CYCLICAL_YEARS_MIN = 4


def _cycle_stats(inc):
    """Márgenes operativos anuales y test de ciclicidad.

    Devuelve {n, margins, mean, cv, is_cyclical}. Cíclica si hay >= 4 años y
    CV(márgenes) >= 1.0. Los años sin dato se descartan."""
    out = {"n": 0, "margins": [], "mean": float("nan"), "cv": float("nan"),
           "is_cyclical": False}
    if inc is None or getattr(inc, "empty", True):
        return out
    rev = inc.loc["Total Revenue"] if "Total Revenue" in inc.index else None
    ebit = inc.loc["Operating Income"] if "Operating Income" in inc.index else None
    if rev is None or ebit is None:
        return out
    mgs = []
    for c in inc.columns:
        try:
            r, e = float(rev[c]), float(ebit[c])
        except Exception:
            continue
        if np.isfinite(r) and np.isfinite(e) and r > 0:
            mgs.append(e / r)
    n = len(mgs)
    out["n"], out["margins"] = n, mgs
    if n >= CYCLICAL_YEARS_MIN:
        mu = float(np.mean(mgs))
        out["mean"] = mu
        if abs(mu) > 1e-12:
            out["cv"] = float(np.std(mgs) / abs(mu))
            out["is_cyclical"] = out["cv"] >= CYCLICAL_CV_MIN
    return out


def _normalized_cycle_set(t, inc, bs, revenue_base, tax):
    """Set normalizado Damodaran para cíclicas (método 2: margen medio del ciclo
    x revenue actual, válido cuando la escala cambió).

    Internamente consistente: la mecánica del modelo (reinversión = Δventas/s2c)
    exige ROIC_norm = s2c_norm x margen_nopat_norm (identidad); con otra ROIC el
    crecimiento RR x ROIC no cuadra con la reinversión que el modelo aplica.
    g_norm = mediana(RR en años con NOPAT>0) x ROIC_norm (la mediana es robusta
    a los outliers de reinversión típicos del trough; con NOPAT<=0 el RR no
    existe, Damodaran). Todo etiquetado. Devuelve None si no hay base."""
    try:
        cf = t.cashflow
    except Exception:
        cf = None
    cols = list(inc.columns)
    def _at(df, col, *names):
        if df is None or getattr(df, "empty", True):
            return float("nan")
        target = col.date() if hasattr(col, "date") else col
        bcol = None
        for c in df.columns:
            try:
                d = c.date() if hasattr(c, "date") else c
            except Exception:
                continue
            if d == target:
                bcol = c
                break
        if bcol is None:
            return float("nan")
        for n in names:
            if n in df.index:
                try:
                    return float(df.loc[n][bcol])
                except Exception:
                    return float("nan")
        return float("nan")

    margins, s2cs, rrs = [], [], []
    for c in cols:
        try:
            r = float(inc.loc["Total Revenue"][c]); e = float(inc.loc["Operating Income"][c])
        except Exception:
            continue
        if not (np.isfinite(r) and np.isfinite(e)) or r <= 0:
            continue
        margins.append(e / r)
        debt_c = _at(bs, c, "Total Debt", "TotalDebt")
        if not np.isfinite(debt_c):
            ltd = _at(bs, c, "Long Term Debt", "LongTermDebt")
            cur = _at(bs, c, "Current Debt", "CurrentDebt")
            debt_c = float(np.nansum([ltd, cur])) if (np.isfinite(ltd) or np.isfinite(cur)) else float("nan")
        cash_c = _at(bs, c, "Cash Cash Equivalents And Short Term Investments",
                     "Cash And Cash Equivalents", "CashAndCashEquivalents")
        # capital invertido de ese año con el bs alineado por fecha
        eq_c = _at(bs, c, "Stockholders Equity", "Total Stockholder Equity", "Common Stock Equity")
        ic = ((debt_c or 0.0) + (eq_c if np.isfinite(eq_c) else 0.0) - (cash_c or 0.0))
        if np.isfinite(ic) and ic > 0:
            s2cs.append(r / ic)
        nopat = e * (1 - tax)
        if np.isfinite(nopat) and nopat > 0:
            capex = _at(cf, c, "Capital Expenditure", "CapitalExpenditure")
            dep = _at(cf, c, "Depreciation And Amortization",
                      "Depreciation Amortization Depletion", "Depreciation")
            cwc = _at(cf, c, "Change In Working Capital", "ChangeInWorkingCapital")
            net_capex = max((abs(capex) if np.isfinite(capex) else 0.0) - (dep if np.isfinite(dep) else 0.0), 0.0)
            dwc = max(-cwc, 0.0) if np.isfinite(cwc) else 0.0
            rrs.append((net_capex + dwc) / nopat)
    if len(margins) < CYCLICAL_YEARS_MIN or not rrs or not s2cs:
        return None
    m_norm = float(np.mean(margins))
    s2c_norm = float(min(max(np.mean(s2cs), 0.3), 6.0))
    rr_med = float(np.median(rrs))
    nopat_m = m_norm * (1 - tax)
    roic_norm = s2c_norm * nopat_m          # identidad de consistencia (ver docstring)
    g_norm = float(min(max(rr_med * roic_norm, 0.0), 0.60))
    return {
        "metodo": "Damodaran: margen medio del ciclo x revenue actual (escala cambió)",
        "n_years": len(margins),
        "cv_margenes": float(np.std(margins) / abs(np.mean(margins))) if abs(np.mean(margins)) > 1e-12 else float("nan"),
        "m_norm": m_norm,
        "revenue_base": float(revenue_base),
        "ebit_normalizado": float(revenue_base) * m_norm,
        "s2c_norm": s2c_norm,
        "rr_med": rr_med,
        "roic_norm": roic_norm,
        "g_norm": g_norm,
    }


def _fetch_live(ticker: str) -> CompanyData:
    import yfinance as yf
    t = yf.Ticker(ticker)
    info = {}
    try: info = t.info or {}
    except Exception: info = {}
    try: fi = t.fast_info
    except Exception: fi = {}

    inc = getattr(t, "income_stmt", None)
    bs  = getattr(t, "balance_sheet", None)
    if inc is None or getattr(inc, "empty", True): inc = getattr(t, "financials", None)
    try:
        ann_date = inc.columns[0].date().isoformat() if (inc is not None and len(inc.columns)) else None
    except Exception:
        ann_date = None

    # --- Elección del set temporal: el más nuevo manda, y se usa COMPLETO ---
    # (flujos + balance del mismo período; nunca mezclar TTM con anual).
    ttm = _ttm_set(t)
    use_ttm = (ttm is not None and ann_date is not None and ttm["latest"] > ann_date)
    if use_ttm:
        revenue, ebit = ttm["revenue"], ttm["ebit"]
        pretax, taxprov, intexp = ttm["pretax"], ttm["taxprov"], ttm["intexp"]
        debt, cash, minority = ttm["debt"], ttm["cash"], ttm["minority"]
        if not np.isfinite(debt):      # sin balance trimestral: respaldo anual
            debt = _latest(bs, "Total Debt", "TotalDebt")
        if not np.isfinite(cash):
            cash = _latest(bs, "Cash Cash Equivalents And Short Term Investments",
                           "Cash And Cash Equivalents", "CashAndCashEquivalents")
        if not np.isfinite(minority):
            minority = _latest(bs, "Minority Interest", "MinorityInterest")
        bs_for_ic = ttm["bs"] if (ttm["bs"] is not None and not getattr(ttm["bs"], "empty", True)) else bs
        flows = dict(capex=ttm["capex"], dep=ttm["dep"], cwc=ttm["cwc"])
        asof, source = "TTM-" + ttm["latest"], "yfinance-ttm"
    else:
        revenue = _latest(inc, "Total Revenue", "TotalRevenue", "OperatingRevenue")
        ebit    = _latest(inc, "Operating Income", "OperatingIncome", "EBIT")
        pretax  = _latest(inc, "Pretax Income", "PretaxIncome")
        taxprov = _latest(inc, "Tax Provision", "TaxProvision", "IncomeTaxExpense")
        intexp  = abs(_latest(inc, "Interest Expense", "InterestExpense",
                              "Interest Expense Non Operating"))
        debt    = _latest(bs, "Total Debt", "TotalDebt")
        if not np.isfinite(debt):
            ltd = _latest(bs, "Long Term Debt", "LongTermDebt")
            cur = _latest(bs, "Current Debt", "CurrentDebt", "Current Debt And Capital Lease Obligation")
            debt = np.nansum([ltd, cur]) if (np.isfinite(ltd) or np.isfinite(cur)) else float("nan")
        cash = _latest(bs, "Cash Cash Equivalents And Short Term Investments",
                       "Cash And Cash Equivalents", "CashAndCashEquivalents")
        minority = _latest(bs, "Minority Interest", "MinorityInterest")
        bs_for_ic, flows = bs, None
        asof, source = (ann_date or ""), "yfinance"

    def _g(obj, attr):
        try: return getattr(obj, attr)
        except Exception:
            try: return obj[attr]
            except Exception: return None

    price  = info.get("currentPrice") or info.get("regularMarketPrice") or _g(fi, "last_price")
    shares = info.get("sharesOutstanding") or _g(fi, "shares")

    tax = (taxprov / pretax) if (np.isfinite(taxprov) and np.isfinite(pretax) and pretax > 0) else 0.21
    tax = float(min(max(tax, 0.0), 0.35))

    need = [price, shares, revenue, ebit]
    if any((x is None) or (not np.isfinite(float(x))) for x in need):
        sector = info.get("sector") or ""
        if sector.startswith("Financial"):
            raise ValueError(
                f"{ticker.upper()} es del sector financiero ({sector}): el DCF FCFF no aplica a "
                "bancos/aseguradoras (no reportan EBIT/reinversión convencionales). "
                "Corresponde un modelo FCFE/dividendos.")
        raise ValueError("datos insuficientes de yfinance (faltan precio/acciones/ingresos/EBIT)")

    total_debt = float(debt) if np.isfinite(debt) else 0.0
    cash_v = float(cash) if np.isfinite(cash) else 0.0

    # Crecimiento de ingresos SIN número mágico (Damodaran) + sales/capital propio.
    # En modo TTM, el capital invertido y los flujos de reinversión salen del
    # mismo período (bs_for_ic / flows); el CAGR histórico siempre usa el anual.
    try:
        rg_est, s2c_est, gdet = _estimate_growth_s2c(
            t, inc, bs_for_ic, info, float(revenue), float(ebit), tax,
            total_debt, cash_v, flows=flows)
    except Exception as _e:
        rg_est, s2c_est, gdet = None, None, {"error": f"{type(_e).__name__}: {_e}"}

    # Detección de cíclica + set normalizado (Damodaran). No reemplaza la base
    # punto-en-el-ciclo: se reporta como ancla separada y etiquetada.
    cstats = _cycle_stats(inc)
    is_cyclical, cycle_detail = bool(cstats["is_cyclical"]), None
    if is_cyclical:
        cycle_detail = _normalized_cycle_set(t, inc, bs, float(revenue), tax)
        if cycle_detail is None:
            is_cyclical = False

    return CompanyData(
        ticker=ticker.upper(),
        name=info.get("longName") or info.get("shortName") or ticker.upper(),
        price=float(price), shares=float(shares),
        beta=float(info.get("beta") or 1.0),
        sector=info.get("sector") or "n/d",
        country=info.get("country") or "United States",
        currency=info.get("currency") or "USD",
        revenue=float(revenue), ebit=float(ebit), tax_rate=tax,
        interest_expense=float(intexp) if np.isfinite(intexp) else 0.0,
        total_debt=total_debt,
        cash=cash_v,
        minority=float(minority) if np.isfinite(minority) else 0.0,
        source=source,
        asof=asof,
        rev_growth_est=rg_est, sales_to_capital_est=s2c_est, growth_detail=gdet,
        is_cyclical=is_cyclical, cycle_detail=cycle_detail)


def fetch_company(ticker: str, live: bool = True, timeout: float = 25.0) -> CompanyData:
    """Trae datos vía yfinance (con timeout por hilo); si falla, usa cache/fixture."""
    ticker = ticker.upper()
    box = {}
    if live:
        import threading
        def _w():
            try: box["cd"] = _fetch_live(ticker)
            except Exception as e: box["err"] = e
        th = threading.Thread(target=_w, daemon=True); th.start(); th.join(timeout)
        if "cd" in box:
            try: save_company(box["cd"])   # todo fetch vivo exitoso alimenta el cache offline
            except Exception: pass
            return box["cd"]
    cached = load_company(ticker)
    if cached is not None:
        return cached
    if ticker in _FIXTURES:
        return _FIXTURES[ticker]
    causa = f" Causa en vivo: {box['err']}" if box.get("err") is not None else ""
    raise RuntimeError(f"Sin datos en vivo, cache ni fixture para {ticker}.{causa}")


def growth_report(cd: CompanyData) -> str:
    """Triangulación del crecimiento de ingresos como texto (para la UI/API)."""
    d = cd.growth_detail or {}
    def pc(x): return f"{x*100:6.2f}%" if (x is not None and np.isfinite(x)) else "    n/d"
    s2c = d.get("sales_to_capital")
    lines = [
        f"=== {cd.ticker} · crecimiento de ingresos (3 vías Damodaran) ===",
        f"  Analistas (consenso fwd) : {pc(d.get('analistas'))}",
        f"  Fundamental (RR x ROIC)  : {pc(d.get('fundamental'))}   "
        f"(ROIC {pc(d.get('roic'))}, RR {pc(d.get('reinv_rate'))})",
        f"  Histórico (CAGR)         : {pc(d.get('historico'))}",
        f"  -> USADO (año 1)         : {pc(d.get('elegido'))}   [{d.get('fuente', '?')}]",
        f"  Sales/Capital (empresa)  : {s2c:6.2f}" if (s2c and np.isfinite(s2c))
        else "  Sales/Capital (empresa)  :    n/d  (usa fallback)",
    ]
    br = d.get("base_rate_tope")
    if br is not None and np.isfinite(br):
        p20 = d.get("base_rate_p_sostener_20")
        lines.append(
            f"  Base-rate Mauboussin   : tope {pc(br)}  "
            f"[CAGR previo 3a {pc(d.get('base_rate_previo_3a'))}, "
            f"fila {d.get('base_rate_fila')}, "
            f"P(sostener ≥20%) {p20*100:.0f}%]" if (p20 is not None and np.isfinite(p20))
            else f"  Base-rate Mauboussin   : tope {pc(br)}  "
                 f"[CAGR previo 3a {pc(d.get('base_rate_previo_3a'))}, "
                 f"fila {d.get('base_rate_fila')}]"
        )
        if d.get("freno_base_rate"):
            lines.append(
                f"  FRENO APLICADO         : {pc(d.get('elegido_previo_freno'))} "
                f"[{d.get('fuente_previa_freno')}] -> {pc(d.get('elegido'))}"
            )
    return "\n".join(lines)
