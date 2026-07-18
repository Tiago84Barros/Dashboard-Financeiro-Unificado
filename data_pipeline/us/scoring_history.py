"""
data_pipeline/us/scoring_history.py
Computa o HISTÓRICO point-in-time de scores (market_us.score_vintages) e monta o
painel para o backtest.

PIT de verdade: para cada data-base `as_of`, só entram observações financeiras com
available_at ≤ as_of (o que o investidor saberia naquele dia). O score daquela data
usa apenas a série visível — nunca dados publicados depois. É isto que evita
look-ahead no backtest da Fase 6.

Os helpers puros (visible_rows, forward_returns_from_monthly) são testados; a
orquestração em banco roda com o warehouse via run_us_ingest.py score-history.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Iterable, Sequence

import pandas as pd
from sqlalchemy import bindparam, text

from core.us_metrics import compute_company_metrics
from core.us_score import score_cross_section

logger = logging.getLogger("us_scoring_history")

_INCOME = ("company_id", "fiscal_year", "available_at", "revenue", "gross_profit",
           "operating_income", "ebit", "ebitda", "net_income", "interest_expense", "eps")
_BALANCE = ("company_id", "fiscal_year", "available_at", "total_assets", "total_equity",
            "total_debt", "net_debt", "cash_and_equivalents", "current_assets",
            "current_liabilities", "invested_capital", "shares_outstanding")
_CASHFLOW = ("company_id", "fiscal_year", "available_at", "operating_cash_flow",
             "capex", "free_cash_flow", "dividends_paid", "stock_repurchase",
             "stock_issuance")


def visible_rows(rows: Sequence[dict], as_of: date) -> list[dict]:
    """Filtra as observações conhecíveis em `as_of` (available_at ≤ as_of).

    Sem available_at, a linha é considerada NÃO conhecível (conservador — evita
    look-ahead). Puro e testável.
    """
    out = []
    for r in rows:
        av = r.get("available_at")
        if av is None:
            continue
        if hasattr(av, "date"):
            av = av.date()
        if av <= as_of:
            out.append(r)
    return out


def forward_returns_from_monthly(monthly: pd.DataFrame) -> pd.DataFrame:
    """Retorno futuro por (symbol, month_end) a partir de prices_monthly.

    monthly: colunas ['symbol','month_end','adjusted_close']. Retorna
    ['symbol','date','fwd_return'] onde fwd_return é o retorno do mês seguinte.
    Puro e testável.
    """
    if monthly is None or monthly.empty:
        return pd.DataFrame(columns=["symbol", "date", "fwd_return"])
    m = monthly.sort_values(["symbol", "month_end"]).copy()
    m["next_close"] = m.groupby("symbol")["adjusted_close"].shift(-1)
    m["fwd_return"] = m["next_close"] / m["adjusted_close"] - 1.0
    out = m.dropna(subset=["fwd_return"])[["symbol", "month_end", "fwd_return"]]
    return out.rename(columns={"month_end": "date"})


# ── Derivação de prices_monthly (backtest lê desta tabela) ────────────────────
def derive_prices_monthly(engine) -> dict:
    """Deriva o fechamento MENSAL (último pregão do mês) de prices_daily.

    O backtest PIT lê market_us.prices_monthly; a ingestão só grava o diário.
    Sem este passo o painel do backtest fica vazio. SQL puro no Postgres
    (DISTINCT ON pega o último pregão de cada mês); idempotente por (symbol,
    month_end). total_return = retorno mês a mês do adjusted_close.
    """
    if engine is None:
        return {"ok": False, "reason": "engine indisponível"}
    sql = """
        INSERT INTO market_us.prices_monthly
            (symbol, month_end, close, adjusted_close, volume, total_return, source)
        WITH last_of_month AS (
            SELECT DISTINCT ON (symbol, date_trunc('month', date))
                symbol, date AS month_end, close,
                COALESCE(adjusted_close, close) AS adjusted_close, volume
            FROM market_us.prices_daily
            ORDER BY symbol, date_trunc('month', date), date DESC
        )
        SELECT symbol, month_end, close, adjusted_close, volume,
               adjusted_close / NULLIF(
                   LAG(adjusted_close) OVER (PARTITION BY symbol ORDER BY month_end), 0) - 1
                   AS total_return,
               'derived'
        FROM last_of_month
        ON CONFLICT (symbol, month_end) DO UPDATE SET
            close = EXCLUDED.close, adjusted_close = EXCLUDED.adjusted_close,
            volume = EXCLUDED.volume, total_return = EXCLUDED.total_return
    """
    with engine.begin() as conn:
        conn.execute(text(sql))
        n = conn.execute(text("SELECT COUNT(*) FROM market_us.prices_monthly")).scalar()
    return {"ok": True, "rows": int(n or 0)}


# ── Orquestração em banco (roda com warehouse) ────────────────────────────────
def _bulk(conn, table: str, cols: Sequence[str], ids: list[int]) -> pd.DataFrame:
    q = text(f"SELECT {', '.join(cols)} FROM market_us.{table} "
             f"WHERE period='annual' AND company_id IN :ids "
             f"ORDER BY company_id, fiscal_year"
             ).bindparams(bindparam("ids", expanding=True))
    return pd.read_sql(q, conn, params={"ids": ids})


def compute_score_history(engine, as_of_dates: Iterable[date], *,
                          score_version: str, limit_companies: int = 800) -> dict:
    """Para cada as_of, calcula o score PIT e grava em market_us.score_vintages."""
    if engine is None:
        return {"ok": False, "reason": "engine indisponível"}
    as_of_dates = list(as_of_dates)
    written = 0
    with engine.begin() as conn:
        comp = pd.read_sql(text(
            "SELECT c.id, MIN(a.symbol) AS symbol, MAX(c.sector) AS sector, "
            "MAX(c.industry) AS industry FROM market_us.companies c "
            "JOIN market_us.assets a ON a.company_id=c.id "
            "WHERE EXISTS (SELECT 1 FROM market_us.income_statements i "
            "  WHERE i.company_id=c.id AND i.period='annual') "
            "GROUP BY c.id ORDER BY c.id LIMIT :lim"),
            conn, params={"lim": int(limit_companies)})
        if comp.empty:
            return {"ok": True, "written": 0, "reason": "sem empresas"}
        ids = [int(x) for x in comp["id"]]
        inc = _bulk(conn, "income_statements", _INCOME, ids)
        bal = _bulk(conn, "balance_sheets", _BALANCE, ids)
        cfw = _bulk(conn, "cash_flow_statements", _CASHFLOW, ids)
        inc_g = {k: v.to_dict("records") for k, v in inc.groupby("company_id")}
        bal_g = {k: v.to_dict("records") for k, v in bal.groupby("company_id")}
        cfw_g = {k: v.to_dict("records") for k, v in cfw.groupby("company_id")}

        for as_of in as_of_dates:
            rows = []
            for _, c in comp.iterrows():
                cid = int(c["id"])
                m = compute_company_metrics(
                    visible_rows(inc_g.get(cid, []), as_of),
                    visible_rows(bal_g.get(cid, []), as_of),
                    visible_rows(cfw_g.get(cid, []), as_of))
                if m.get("_years", 0) < 2:
                    continue
                rows.append({"company_id": cid, "symbol": c["symbol"],
                             "sector": c["sector"], "industry": c["industry"], **m})
            if not rows:
                continue
            scored = score_cross_section(pd.DataFrame(rows))
            for _, r in scored.iterrows():
                conn.execute(text(
                    "INSERT INTO market_us.score_vintages "
                    "(company_id, symbol, score_version, as_of_date, track, score) "
                    "VALUES (:cid,:sym,:ver,:asof,'fundamental',:score) "
                    "ON CONFLICT (company_id, score_version, as_of_date, track) "
                    "DO UPDATE SET score=EXCLUDED.score"),
                    {"cid": int(r["company_id"]), "sym": r["symbol"],
                     "ver": score_version, "asof": as_of, "score": float(r["score"])})
                written += 1
    return {"ok": True, "written": written, "dates": len(as_of_dates)}


def build_annual_panel(vintages: pd.DataFrame, monthly: pd.DataFrame,
                       horizon_months: int = 12) -> pd.DataFrame:
    """Painel do backtest: junta score (as_of) ao retorno futuro de `horizon_months`.

    vintages: ['as_of_date','symbol','score']; monthly: ['symbol','month_end',
    'adjusted_close']. Para cada (as_of, symbol) usa o preço no mês ≤ as_of e o
    preço ~horizon meses depois. Puro e testável.
    """
    cols = ["date", "symbol", "score", "fwd_return"]
    if vintages is None or vintages.empty or monthly is None or monthly.empty:
        return pd.DataFrame(columns=cols)
    m = monthly.dropna(subset=["adjusted_close"]).copy()
    m["month_end"] = pd.to_datetime(m["month_end"])
    price_by_symbol = {s: g.sort_values("month_end") for s, g in m.groupby("symbol")}
    rows = []
    for _, v in vintages.iterrows():
        sym = v["symbol"]
        g = price_by_symbol.get(sym)
        if g is None:
            continue
        as_of = pd.to_datetime(v["as_of_date"])
        past = g[g["month_end"] <= as_of]
        if past.empty:
            continue
        p0 = float(past.iloc[-1]["adjusted_close"])
        target = as_of + pd.DateOffset(months=horizon_months)
        fut = g[g["month_end"] >= target]
        if fut.empty or p0 <= 0:
            continue
        p1 = float(fut.iloc[0]["adjusted_close"])
        rows.append({"date": v["as_of_date"], "symbol": sym,
                     "score": float(v["score"]), "fwd_return": p1 / p0 - 1.0})
    return pd.DataFrame(rows, columns=cols)


def annual_asof_dates(start_year: int, end_year: int, month: int = 6, day: int = 30) -> list[date]:
    """Datas-base anuais (default 30/jun) para o histórico PIT."""
    return [date(y, month, day) for y in range(start_year, end_year + 1)]
