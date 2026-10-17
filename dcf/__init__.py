"""Valuación DCF estilo Damodaran para Rivendel.

Port del proyecto StockValuation.io (notebook Termux) a paquete importable:
- dcf.model  — motor DCF puro (FCFF a dos etapas, rating sintético, reverse-DCF,
               sensibilidad). Solo numpy.
- dcf.data   — CompanyData vía yfinance (timeout por hilo), crecimiento de
               ingresos triangulado (analistas/fundamental/histórico), respaldo
               en cache SQLite y fixture ilustrativo.
- dcf.macro  — rf (Tesoro 10Y) y ERP (Damodaran implied) en vivo, con cache y
               fallback etiquetado.
- dcf.store  — persistencia SQLite: cache de compañías + historial auditable.
"""
import math

from .model import (CompanyData, ValuationOutput, DEFAULTS,
                    cost_of_capital, value_company, reverse_dcf,
                    implied_margin, implied_growth, sensitivity,
                    synthetic_rating, normalized_company, value_normalized,
                    implied_cycle_margin)
from .macro import macro_inputs
from .data import fetch_company, growth_report
from .store import save_valuation, list_valuations

__all__ = ["CompanyData", "ValuationOutput", "DEFAULTS", "cost_of_capital",
           "value_company", "reverse_dcf", "implied_margin", "implied_growth",
           "sensitivity", "synthetic_rating", "macro_inputs", "fetch_company",
           "growth_report", "save_valuation", "list_valuations",
           "normalized_company", "value_normalized", "implied_cycle_margin",
           "value_ticker", "to_dict"]


def value_ticker(ticker: str, assumptions: dict = None, live: bool = True):
    """Valora un ticker end-to-end: macro + datos + DCF + registro en historial.

    Devuelve (ValuationOutput, macro_dict, CompanyData).
    """
    macro = macro_inputs(live=live)
    cd = fetch_company(ticker, live=live)
    a = dict(assumptions or {})
    a.setdefault("rf", macro["rf"])
    a.setdefault("erp", macro["erp"])
    vo = value_company(cd, a, macro)
    try:
        save_valuation(vo)
    except Exception:
        pass
    return vo, macro, cd


def _clean(x):
    if isinstance(x, float) and (math.isnan(x) or math.isinf(x)):
        return None
    if isinstance(x, dict):
        return {k: _clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_clean(v) for v in x]
    return x


def to_dict(vo: ValuationOutput, macro: dict = None, cd: CompanyData = None) -> dict:
    """ValuationOutput -> dict JSON-safe (NaN/Inf -> None)."""
    out = dict(
        ticker=vo.ticker, price=vo.price, per_share=vo.per_share, upside=vo.upside,
        ev=vo.ev, equity=vo.equity,
        wacc_initial=vo.wacc_initial, wacc_stable=vo.wacc_stable,
        g_stable=vo.g_stable, terminal_value=vo.terminal_value,
        pv_terminal=vo.pv_terminal, table=vo.table,
        assumptions=vo.assumptions, coc=vo.coc,
        source=vo.source, asof=vo.asof,
    )
    if macro is not None:
        out["macro"] = macro
    if cd is not None:
        out["company"] = dict(
            name=cd.name, sector=cd.sector, country=cd.country, currency=cd.currency,
            revenue=cd.revenue, ebit=cd.ebit, tax_rate=cd.tax_rate,
            interest_expense=cd.interest_expense, total_debt=cd.total_debt,
            cash=cd.cash, minority=cd.minority, shares=cd.shares, beta=cd.beta,
            rev_growth_est=cd.rev_growth_est,
            sales_to_capital_est=cd.sales_to_capital_est,
            growth_detail=cd.growth_detail, source=cd.source, asof=cd.asof,
            is_cyclical=cd.is_cyclical, cycle_detail=cd.cycle_detail,
        )
        if cd.is_cyclical and cd.cycle_detail:
            try:
                vn = value_normalized(cd, macro)
                out["normalized"] = dict(per_share=vn.per_share, upside=vn.upside,
                                         ev=vn.ev, table=vn.table,
                                         detail=cd.cycle_detail)
                icm = implied_cycle_margin(cd, macro)
                if icm:
                    out["normalized"]["implied_margin"] = icm["implied_margin"]
            except Exception:
                pass
    return _clean(out)
