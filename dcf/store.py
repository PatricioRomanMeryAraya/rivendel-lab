"""Persistencia SQLite: cache de compañías y registro auditable de valoraciones.
Puerto nativo del `postgres` del diseño original; comparte DB con el notebook
(~/.stockvaluation/sv.db) para que cache e historial sean continuos.
"""
import os, json, sqlite3, datetime as dt

from .model import CompanyData
from .macro import SV_HOME, DB_PATH, _macro_table  # noqa: F401  (misma DB)


def _db():
    os.makedirs(SV_HOME, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.execute("""CREATE TABLE IF NOT EXISTS companies(
        ticker TEXT PRIMARY KEY, name TEXT, sector TEXT, country TEXT,
        currency TEXT, asof TEXT, payload TEXT)""")
    con.execute("""CREATE TABLE IF NOT EXISTS valuations(
        id INTEGER PRIMARY KEY AUTOINCREMENT, ticker TEXT, asof TEXT,
        per_share REAL, price REAL, ev REAL, equity REAL, wacc REAL,
        upside REAL, assumptions TEXT, created TEXT, flag TEXT)""")
    try:
        con.execute("ALTER TABLE valuations ADD COLUMN flag TEXT")
    except sqlite3.OperationalError:
        pass  # columna ya presente (migración de DBs previas)
    return con


def save_company(cd: CompanyData):
    with _db() as con:
        con.execute("INSERT OR REPLACE INTO companies VALUES (?,?,?,?,?,?,?)",
                    (cd.ticker, cd.name, cd.sector, cd.country, cd.currency, cd.asof, cd.to_json()))


def load_company(ticker: str):
    with _db() as con:
        r = con.execute("SELECT payload FROM companies WHERE ticker=?", (ticker.upper(),)).fetchone()
    return CompanyData.from_json(r[0]) if r else None


def save_valuation(vo):
    flag = "no-significativa: equity<=0" if vo.equity <= 0 else None
    with _db() as con:
        con.execute("""INSERT INTO valuations
            (ticker,asof,per_share,price,ev,equity,wacc,upside,assumptions,created,flag)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (vo.ticker, vo.asof, vo.per_share, vo.price, vo.ev, vo.equity,
             vo.wacc_initial, vo.upside, json.dumps(vo.assumptions),
             dt.datetime.now().isoformat(), flag))


def list_valuations(ticker: str = None, limit: int = 20):
    """Historial de corridas como lista de dicts (JSON-friendly, sin pandas)."""
    q = "SELECT ticker,created,per_share,price,upside,wacc,flag FROM valuations"
    args = ()
    if ticker:
        q += " WHERE ticker=?"; args = (ticker.upper(),)
    q += " ORDER BY id DESC LIMIT ?"; args = args + (limit,)
    with _db() as con:
        rows = con.execute(q, args).fetchall()
    cols = ["ticker", "creado", "valor_acc", "precio", "upside", "wacc", "flag"]
    return [dict(zip(cols, r)) for r in rows]
