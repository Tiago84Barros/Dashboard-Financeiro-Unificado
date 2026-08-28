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

import json
import logging
from bisect import bisect_right
from datetime import date
from typing import Callable, Iterable, Sequence

import pandas as pd
from sqlalchemy import bindparam, text

from core import us_pit
from core.us_metrics import compute_company_metrics
from core.us_score import score_cross_section
from data_pipeline.us.edgar_facts import (
    derivar_balance,
    derivar_cashflow,
    derivar_income,
)

logger = logging.getLogger("us_scoring_history")

_INCOME = ("company_id", "fiscal_year", "available_at", "filed_at", "revenue", "gross_profit",
           "operating_income", "ebit", "ebitda", "net_income", "interest_expense", "eps")
_BALANCE = ("company_id", "fiscal_year", "available_at", "filed_at", "total_assets", "total_equity",
            "total_debt", "net_debt", "cash_and_equivalents", "current_assets",
            "current_liabilities", "invested_capital", "shares_outstanding")
_CASHFLOW = ("company_id", "fiscal_year", "available_at", "filed_at", "operating_cash_flow",
             "capex", "free_cash_flow", "dividends_paid", "stock_repurchase",
             "stock_issuance", "depreciation_and_amortization",
             # SBC entra no score v0.5.0; sem esta coluna o histórico PIT
             # reconstruiria as vintages sem a trilha de qualidade completa.
             "stock_based_compensation")


def visible_rows(rows: Sequence[dict], as_of: date,
                 derivar: Callable[[dict], None] | None = None) -> list[dict]:
    """Observacoes conheciveis em `as_of`, pela regra por campo quando ha `filed_at`.

    A regra por linha (`available_at <= as_of`) parecia a escolha conservadora e
    era a unica que existia. Ela depende do futuro: um campo que so estreou anos
    depois esconde a linha inteira em toda safra anterior, e so quem sobreviveu
    tem anos seguintes em que estrear tags. `core.us_pit` responde a mesma
    pergunta sem consultar nada posterior a `as_of`; linhas sem `filed_at`
    (ingeridas antes da migration 054) continuam sob a regra antiga.

    `derivar` recalcula os campos que saem de outros campos da mesma linha
    depois da mascara -- sem ele, `total_debt` ou `free_cash_flow` atravessariam
    carregando o valor calculado sobre um insumo que ainda nao era publico.
    """
    return us_pit.visiveis(rows, as_of, regra=us_pit.REGRA_CAMPO, derivar=derivar)


def forward_returns_from_monthly(monthly: pd.DataFrame) -> pd.DataFrame:
    """Retorno futuro por (symbol, month_end) a partir de prices_monthly.

    monthly: colunas ['symbol','month_end','adjusted_close']. Retorna
    ['symbol','date','fwd_return'] onde fwd_return é o retorno do mês seguinte.
    Puro e testável.
    """
    if monthly is None or monthly.empty:
        return pd.DataFrame(columns=["symbol", "date", "fwd_return"])
    m = monthly.sort_values(["symbol", "month_end"]).copy()
    m["month_end"] = pd.to_datetime(m["month_end"])
    m["next_close"] = m.groupby("symbol")["adjusted_close"].shift(-1)
    m["next_month"] = m.groupby("symbol")["month_end"].shift(-1)
    m["fwd_return"] = m["next_close"] / m["adjusted_close"] - 1.0
    # A-117: `shift(-1)` devolve a PRÓXIMA LINHA, não o próximo mês. Com um
    # buraco na série (jan/2020 e depois dez/2020) o retorno de 11 meses saía
    # rotulado como retorno mensal — +100% entrando numa série de vol mensal.
    # Exige que a linha seguinte esteja mesmo no mês seguinte (45 dias de folga
    # cobrem month_end de comprimentos diferentes).
    salto = (m["next_month"] - m["month_end"]).dt.days
    m.loc[~salto.between(20, 45), "fwd_return"] = float("nan")
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
        WHERE adjusted_close > 0
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
             f"WHERE period='annual' AND quality_status IN ('raw','validated') "
             f"AND company_id IN :ids "
             f"ORDER BY company_id, fiscal_year"
             ).bindparams(bindparam("ids", expanding=True))
    return pd.read_sql(q, conn, params={"ids": ids})


def _asof_value(points: list[tuple[date, float]], as_of: date) -> float | None:
    """Último valor cuja data é <= as_of; busca binária determinística."""
    if not points:
        return None
    idx = bisect_right([p[0] for p in points], as_of) - 1
    return None if idx < 0 else points[idx][1]


def compute_score_history(engine, as_of_dates: Iterable[date], *,
                          score_version: str, limit_companies: int | None = None) -> dict:
    """Para cada as_of, calcula o score PIT e grava em market_us.score_vintages."""
    if engine is None:
        return {"ok": False, "reason": "engine indisponível"}
    as_of_dates = list(as_of_dates)
    written = 0
    with engine.begin() as conn:
        sql = (
            "SELECT c.id, MIN(a.symbol) AS symbol, MAX(c.sector) AS sector, "
            "MAX(c.industry) AS industry, MIN(a.first_trade_date) AS first_trade_date, "
            "MAX(a.delisted_date) AS delisted_date FROM market_us.companies c "
            "JOIN market_us.assets a ON a.company_id=c.id "
            "AND a.analysis_status='eligible' "
            "WHERE EXISTS (SELECT 1 FROM market_us.income_statements i "
            "  WHERE i.company_id=c.id AND i.period='annual') "
            "GROUP BY c.id ORDER BY c.id")
        params = {}
        if limit_companies:
            sql += " LIMIT :lim"
            params["lim"] = int(limit_companies)
        comp = pd.read_sql(text(sql), conn, params=params)
        if comp.empty:
            return {"ok": True, "written": 0, "reason": "sem empresas"}
        ids = [int(x) for x in comp["id"]]
        inc = _bulk(conn, "income_statements", _INCOME, ids)
        bal = _bulk(conn, "balance_sheets", _BALANCE, ids)
        cfw = _bulk(conn, "cash_flow_statements", _CASHFLOW, ids)
        mcaps = pd.read_sql(text(
            "SELECT symbol, date, market_cap FROM market_us.market_cap_history "
            "WHERE market_cap>0 ORDER BY symbol,date"), conn)
        inc_g = {k: v.to_dict("records") for k, v in inc.groupby("company_id")}
        bal_g = {k: v.to_dict("records") for k, v in bal.groupby("company_id")}
        cfw_g = {k: v.to_dict("records") for k, v in cfw.groupby("company_id")}
        mcap_g: dict[str, list[tuple[date, float]]] = {}
        if not mcaps.empty:
            for sym, group in mcaps.groupby("symbol"):
                mcap_g[str(sym)] = [
                    ((d.date() if hasattr(d, "date") else d), float(v))
                    for d, v in zip(pd.to_datetime(group["date"]), group["market_cap"])
                ]

        for as_of in as_of_dates:
            rows = []
            for _, c in comp.iterrows():
                cid = int(c["id"])
                first_trade = c.get("first_trade_date")
                delisted = c.get("delisted_date")
                if pd.notna(first_trade) and pd.to_datetime(first_trade).date() > as_of:
                    continue
                if pd.notna(delisted) and pd.to_datetime(delisted).date() < as_of:
                    continue
                inc_vis = visible_rows(inc_g.get(cid, []), as_of, derivar_income)
                bal_vis = visible_rows(bal_g.get(cid, []), as_of, derivar_balance)
                cfw_vis = visible_rows(cfw_g.get(cid, []), as_of, derivar_cashflow)
                m = compute_company_metrics(
                    inc_vis, bal_vis, cfw_vis,
                    market_cap=_asof_value(mcap_g.get(str(c["symbol"]), []), as_of))
                if m.get("_years", 0) < 2:
                    continue
                rows.append({"company_id": cid, "symbol": c["symbol"],
                             "sector": c["sector"], "industry": c["industry"], **m})
            if not rows:
                continue
            scored = score_cross_section(pd.DataFrame(rows))
            payload = []
            for _, r in scored.iterrows():
                payload.append({"cid": int(r["company_id"]), "sym": r["symbol"],
                     "ver": score_version, "asof": as_of, "score": float(r["score"]),
                     "coverage": float(r.get("coverage") or 0),
                     "confidence": float(r.get("score_confidence") or 0),
                     "factors": json.dumps({
                         "score_status": r.get("score_status"),
                         "critical_missing": r.get("critical_missing") or [],
                         "methodology": score_version,
                     })})
            conn.execute(text(
                "INSERT INTO market_us.score_vintages "
                "(company_id, symbol, score_version, as_of_date, track, score, "
                " coverage, score_confidence, factors_json) "
                "VALUES (:cid,:sym,:ver,:asof,'fundamental',:score,:coverage,:confidence,CAST(:factors AS JSONB)) "
                "ON CONFLICT (company_id, score_version, as_of_date, track) "
                "DO UPDATE SET score=EXCLUDED.score, coverage=EXCLUDED.coverage, "
                "score_confidence=EXCLUDED.score_confidence, factors_json=EXCLUDED.factors_json"), payload)
            if limit_companies is None:
                prune = text(
                    "DELETE FROM market_us.score_vintages "
                    "WHERE score_version=:ver AND as_of_date=:asof "
                    "AND track='fundamental' AND company_id NOT IN :keep"
                ).bindparams(bindparam("keep", expanding=True))
                conn.execute(prune, {"ver": score_version, "asof": as_of,
                                     "keep": [int(x["cid"]) for x in payload]})
            written += len(payload)
    return {"ok": True, "written": written, "dates": len(as_of_dates)}


# Quanto o preço de saída pode estar além da data-alvo e o retorno ainda ser
# chamado de retorno de `horizon_months`. Ver A-117 no docstring abaixo.
TOLERANCIA_HORIZONTE_MESES = 3


def build_annual_panel(vintages: pd.DataFrame, monthly: pd.DataFrame,
                       horizon_months: int = 12,
                       tolerancia_meses: int = TOLERANCIA_HORIZONTE_MESES) -> pd.DataFrame:
    """Painel do backtest: junta score (as_of) ao retorno futuro de `horizon_months`.

    vintages: ['as_of_date','symbol','score']; monthly: ['symbol','month_end',
    'adjusted_close']. Para cada (as_of, symbol) usa o preço no mês ≤ as_of e o
    preço ~horizon meses depois. Puro e testável.

    Correções da auditoria 2026-08
    ------------------------------
    **A-116 — sobrevivência.** A versão anterior fazia ``if fut.empty: continue``:
    a ação que parou de negociar sumia do painel. É o viés de sobrevivência na
    forma mais pura — o perdedor que quebrou não conta, e o backtest fica melhor
    do que a realidade. Medido: uma cesta de duas ações, uma +30% e outra que
    caiu 80% e deslistou, aparecia como **+30,0%** em vez de −25,0%.

    Agora distinguimos duas ausências que a versão antiga confundia:

    * o **dado** acaba (as_of perto da borda do dataset) — aí o retorno é
      genuinamente inobservável e a linha sai, como antes;
    * a **ação** acaba, mas o dataset continua — aí ela deslistou. Usamos o
      último preço negociado como saída (equivale a vender na última cotação) e
      marcamos a linha em ``censored``. Continua otimista, porque deslistagem
      real costuma liquidar perto de zero sem cotação — mas errar para o lado
      otimista em alguns pontos percentuais é outra ordem de grandeza do que
      apagar a perda inteira.

    **A-117 — horizonte elástico.** ``fut.iloc[0]`` não tinha teto: se o próximo
    preço disponível estava 7 anos além do alvo, os +300% daquele período eram
    rotulados "retorno de 12 meses". Agora o preço de saída precisa cair dentro
    de ``tolerancia_meses`` após o alvo; fora disso a linha é tratada como
    censurada (a ação sumiu no meio do caminho), não como um retorno de horizonte.

    ``df.attrs`` carrega ``n_censored`` e ``n_inobservavel`` para quem quiser
    declarar a censura a jusante. Ver `tests/test_us_panel_sobrevivencia.py`.
    """
    cols = ["date", "symbol", "score", "fwd_return"]
    vazio = pd.DataFrame(columns=cols)
    vazio.attrs.update(n_censored=0, n_inobservavel=0)
    if vintages is None or vintages.empty or monthly is None or monthly.empty:
        return vazio
    m = monthly.dropna(subset=["adjusted_close"]).copy()
    m["month_end"] = pd.to_datetime(m["month_end"])
    fim_do_dado = m["month_end"].max()
    price_by_symbol = {s: g.sort_values("month_end") for s, g in m.groupby("symbol")}
    rows = []
    n_censored = n_inobservavel = 0
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
        if p0 <= 0:
            continue
        target = as_of + pd.DateOffset(months=horizon_months)
        limite = target + pd.DateOffset(months=int(tolerancia_meses))
        fut = g[(g["month_end"] >= target) & (g["month_end"] <= limite)]
        censurado = False
        if not fut.empty:
            p1 = float(fut.iloc[0]["adjusted_close"])
        else:
            ultimo = g.iloc[-1]
            # A ação sumiu antes do dataset acabar => deslistou: sai na última
            # cotação. O dataset é que acabou => inobservável: a linha sai.
            if ultimo["month_end"] < fim_do_dado and ultimo["month_end"] > as_of:
                p1, censurado = float(ultimo["adjusted_close"]), True
                n_censored += 1
            else:
                n_inobservavel += 1
                continue
        rows.append({"date": v["as_of_date"], "symbol": sym,
                     "score": float(v["score"]), "fwd_return": p1 / p0 - 1.0,
                     "censored": censurado})
    out = pd.DataFrame(rows, columns=cols + ["censored"])
    out.attrs.update(n_censored=n_censored, n_inobservavel=n_inobservavel)
    return out


def annual_asof_dates(start_year: int, end_year: int, month: int = 6, day: int = 30) -> list[date]:
    """Datas-base anuais (default 30/jun) para o histórico PIT."""
    return [date(y, month, day) for y in range(start_year, end_year + 1)]
