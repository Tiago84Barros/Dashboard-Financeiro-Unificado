"""
core/us_read.py
Camada de LEITURA de market_us.* (warehouse local) — offline-first.

Princípios (do enunciado):
  - A interface lê SÓ o banco local; nunca chama a FMP.
  - Se o schema/dados não existirem ou o banco cair, retorna estruturas VAZIAS e
    seguras (a view não quebra) — nunca levanta para a UI.
  - Informa a data da última atualização; não apaga nem inventa dados.

Usa a engine central (core.database.get_engine). Nenhuma engine nova é criada.
"""
from __future__ import annotations

import logging

import pandas as pd
from sqlalchemy import bindparam, text

logger = logging.getLogger("us_read")

_FACT_TABLES = ("income_statements", "balance_sheets", "cash_flow_statements",
                "prices_daily", "dividends", "key_metrics")


def _engine():
    try:
        from core.database import get_engine
        return get_engine()
    except Exception as exc:  # noqa: BLE001
        logger.warning("engine indisponível: %s", exc)
        return None


def schema_ready() -> bool:
    """True se o schema market_us e a tabela companies existirem."""
    eng = _engine()
    if eng is None:
        return False
    try:
        with eng.connect() as conn:
            return bool(conn.execute(text(
                "SELECT to_regclass('market_us.companies')")).scalar())
    except Exception as exc:  # noqa: BLE001
        logger.warning("schema_ready falhou: %s", exc)
        return False


def data_status() -> dict:
    """Status para os badges da view (conexão, schema, defasagem)."""
    eng = _engine()
    if eng is None:
        return {"connected": False, "schema_ready": False, "offline": True,
                "last_update": None, "companies": 0, "reason": "banco não configurado"}
    if not schema_ready():
        return {"connected": True, "schema_ready": False, "offline": True,
                "last_update": None, "companies": 0,
                "reason": "schema market_us ausente — rode init-schema + bootstrap"}
    try:
        with eng.connect() as conn:
            companies = conn.execute(text(
                "SELECT COUNT(*) FROM market_us.companies")).scalar() or 0
            last = conn.execute(text(
                "SELECT MAX(ingested_at) FROM market_us.income_statements")).scalar()
        return {"connected": True, "schema_ready": True,
                "offline": companies == 0, "last_update": last,
                "companies": int(companies), "reason": None}
    except Exception as exc:  # noqa: BLE001
        logger.warning("data_status falhou: %s", exc)
        return {"connected": False, "schema_ready": False, "offline": True,
                "last_update": None, "companies": 0, "reason": str(exc)[:120]}


def load_overview() -> dict:
    """KPIs da aba Visão Geral (contagens, cobertura, última atualização)."""
    eng = _engine()
    base = {"companies": 0, "assets": 0, "sectors": 0, "delisted": 0,
            "reits": 0, "last_update": None, "with_statements": 0}
    if eng is None or not schema_ready():
        return base
    try:
        with eng.connect() as conn:
            base["companies"] = int(conn.execute(text(
                "SELECT COUNT(*) FROM market_us.companies")).scalar() or 0)
            base["assets"] = int(conn.execute(text(
                "SELECT COUNT(*) FROM market_us.assets")).scalar() or 0)
            base["sectors"] = int(conn.execute(text(
                "SELECT COUNT(DISTINCT sector) FROM market_us.companies "
                "WHERE sector IS NOT NULL")).scalar() or 0)
            base["delisted"] = int(conn.execute(text(
                "SELECT COUNT(*) FROM market_us.assets WHERE is_delisted")).scalar() or 0)
            base["reits"] = int(conn.execute(text(
                "SELECT COUNT(*) FROM market_us.companies WHERE is_reit")).scalar() or 0)
            base["with_statements"] = int(conn.execute(text(
                "SELECT COUNT(DISTINCT company_id) FROM market_us.income_statements"
            )).scalar() or 0)
            base["last_update"] = conn.execute(text(
                "SELECT MAX(ingested_at) FROM market_us.income_statements")).scalar()
    except Exception as exc:  # noqa: BLE001
        logger.warning("load_overview falhou: %s", exc)
    return base


def load_companies(sector: str | None = None, search: str | None = None,
                   limit: int = 500) -> pd.DataFrame:
    """Lista de empresas para a aba Explorar (join com o ativo primário)."""
    eng = _engine()
    cols = ["symbol", "name", "sector", "industry", "exchange", "security_type",
            "is_reit", "is_active", "cik"]
    if eng is None or not schema_ready():
        return pd.DataFrame(columns=cols)
    where = ["1=1"]
    params: dict = {"lim": int(limit)}
    if sector:
        where.append("c.sector = :sector")
        params["sector"] = sector
    if search:
        where.append("(a.symbol ILIKE :q OR c.name ILIKE :q)")
        params["q"] = f"%{search}%"
    sql = (
        "SELECT a.symbol, c.name, c.sector, c.industry, a.exchange, a.security_type, "
        "c.is_reit, c.is_active, c.cik "
        "FROM market_us.assets a "
        "JOIN market_us.companies c ON c.id = a.company_id "
        f"WHERE {' AND '.join(where)} "
        "ORDER BY a.symbol LIMIT :lim")
    try:
        with eng.connect() as conn:
            return pd.read_sql(text(sql), conn, params=params)
    except Exception as exc:  # noqa: BLE001
        logger.warning("load_companies falhou: %s", exc)
        return pd.DataFrame(columns=cols)


def load_company_financials(symbol: str) -> pd.DataFrame:
    """Série anual (receita, lucro, FCF) de uma empresa para o dossiê/exploração."""
    eng = _engine()
    cols = ["fiscal_year", "revenue", "net_income", "ebitda", "free_cash_flow",
            "total_equity", "total_debt"]
    if eng is None or not schema_ready() or not symbol:
        return pd.DataFrame(columns=cols)
    sql = (
        "SELECT i.fiscal_year, i.revenue, i.net_income, i.ebitda, "
        "cf.free_cash_flow, b.total_equity, b.total_debt "
        "FROM market_us.income_statements i "
        "LEFT JOIN market_us.balance_sheets b "
        "  ON b.company_id=i.company_id AND b.period=i.period "
        "  AND b.fiscal_year=i.fiscal_year AND b.fiscal_quarter=i.fiscal_quarter "
        "LEFT JOIN market_us.cash_flow_statements cf "
        "  ON cf.company_id=i.company_id AND cf.period=i.period "
        "  AND cf.fiscal_year=i.fiscal_year AND cf.fiscal_quarter=i.fiscal_quarter "
        "WHERE i.symbol=:sym AND i.period='annual' "
        "ORDER BY i.fiscal_year")
    try:
        with eng.connect() as conn:
            return pd.read_sql(text(sql), conn, params={"sym": symbol.upper()})
    except Exception as exc:  # noqa: BLE001
        logger.warning("load_company_financials falhou: %s", exc)
        return pd.DataFrame(columns=cols)


def load_quality_audit(limit: int = 200) -> pd.DataFrame:
    eng = _engine()
    cols = ["created_at", "symbol", "table_name", "check_name", "severity", "passed", "detail"]
    if eng is None or not schema_ready():
        return pd.DataFrame(columns=cols)
    try:
        with eng.connect() as conn:
            return pd.read_sql(text(
                "SELECT created_at, symbol, table_name, check_name, severity, passed, detail "
                "FROM market_us.data_quality_audit ORDER BY created_at DESC LIMIT :l"),
                conn, params={"l": int(limit)})
    except Exception as exc:  # noqa: BLE001
        logger.warning("load_quality_audit falhou: %s", exc)
        return pd.DataFrame(columns=cols)


_INCOME_COLS = ("fiscal_year", "revenue", "gross_profit", "operating_income",
                "ebit", "ebitda", "net_income", "interest_expense", "eps")
_BALANCE_COLS = ("fiscal_year", "total_assets", "total_equity", "total_debt",
                 "net_debt", "cash_and_equivalents", "current_assets",
                 "current_liabilities", "invested_capital", "shares_outstanding")
_CASHFLOW_COLS = ("fiscal_year", "operating_cash_flow", "capex", "free_cash_flow",
                  "dividends_paid", "stock_repurchase", "stock_issuance")


def _latest_market_cap(conn, symbol: str):
    try:
        return conn.execute(text(
            "SELECT market_cap FROM market_us.market_cap_history "
            "WHERE symbol=:s ORDER BY date DESC LIMIT 1"), {"s": symbol}).scalar()
    except Exception:  # noqa: BLE001
        return None


def load_company_bundle(symbol: str) -> dict | None:
    """Séries anuais + identidade + market cap de UMA empresa (para o dossiê)."""
    eng = _engine()
    if eng is None or not schema_ready() or not symbol:
        return None
    sym = symbol.upper()
    try:
        with eng.connect() as conn:
            ident = conn.execute(text(
                "SELECT c.id, c.name, c.sector, c.industry "
                "FROM market_us.assets a JOIN market_us.companies c ON c.id=a.company_id "
                "WHERE a.symbol=:s LIMIT 1"), {"s": sym}).fetchone()
            if ident is None:
                return None
            cid = int(ident[0])

            def _series(table, cols):
                q = (f"SELECT {', '.join(cols)} FROM market_us.{table} "
                     f"WHERE company_id=:c AND period='annual' ORDER BY fiscal_year")
                return [dict(r._mapping) for r in conn.execute(text(q), {"c": cid})]

            return {
                "name": ident[1], "sector": ident[2], "industry": ident[3],
                "income": _series("income_statements", _INCOME_COLS),
                "balance": _series("balance_sheets", _BALANCE_COLS),
                "cashflow": _series("cash_flow_statements", _CASHFLOW_COLS),
                "market_cap": _latest_market_cap(conn, sym),
                "price": None,
            }
    except Exception as exc:  # noqa: BLE001
        logger.warning("load_company_bundle(%s) falhou: %s", sym, exc)
        return None


def load_scoring_frame(limit_companies: int = 800) -> pd.DataFrame:
    """Cross-section de métricas (uma linha por empresa) para o score/comparação.

    Puxa as séries anuais em lote e calcula as métricas em Python (core.us_metrics).
    Retorna vazio se não houver dados — a UI trata offline.
    """
    from core.us_metrics import compute_company_metrics
    cols = ["symbol", "name", "sector", "industry"]
    eng = _engine()
    if eng is None or not schema_ready():
        return pd.DataFrame(columns=cols)
    try:
        with eng.connect() as conn:
            comp = pd.read_sql(text(
                "SELECT c.id, MIN(a.symbol) AS symbol, MAX(c.name) AS name, "
                "MAX(c.sector) AS sector, MAX(c.industry) AS industry "
                "FROM market_us.companies c JOIN market_us.assets a ON a.company_id=c.id "
                "WHERE EXISTS (SELECT 1 FROM market_us.income_statements i "
                "              WHERE i.company_id=c.id AND i.period='annual') "
                "GROUP BY c.id ORDER BY c.id LIMIT :lim"),
                conn, params={"lim": int(limit_companies)})
            if comp.empty:
                return pd.DataFrame(columns=cols)
            ids = [int(x) for x in comp["id"].tolist()]

            def _bulk(table, cols_):
                q = text(f"SELECT company_id, {', '.join(cols_)} "
                         f"FROM market_us.{table} WHERE period='annual' "
                         f"AND company_id IN :ids ORDER BY company_id, fiscal_year"
                         ).bindparams(bindparam("ids", expanding=True))
                return pd.read_sql(q, conn, params={"ids": ids})

            inc = _bulk("income_statements", _INCOME_COLS)
            bal = _bulk("balance_sheets", _BALANCE_COLS)
            cfw = _bulk("cash_flow_statements", _CASHFLOW_COLS)
            mcaps = pd.read_sql(text(
                "SELECT DISTINCT ON (symbol) symbol, market_cap "
                "FROM market_us.market_cap_history ORDER BY symbol, date DESC"), conn)
    except Exception as exc:  # noqa: BLE001
        logger.warning("load_scoring_frame falhou: %s", exc)
        return pd.DataFrame(columns=cols)

    mcap_by_symbol = dict(zip(mcaps.get("symbol", []), mcaps.get("market_cap", []))) \
        if not mcaps.empty else {}
    inc_g = {k: v.to_dict("records") for k, v in inc.groupby("company_id")} if not inc.empty else {}
    bal_g = {k: v.to_dict("records") for k, v in bal.groupby("company_id")} if not bal.empty else {}
    cfw_g = {k: v.to_dict("records") for k, v in cfw.groupby("company_id")} if not cfw.empty else {}

    rows = []
    for _, c in comp.iterrows():
        cid = int(c["id"])
        m = compute_company_metrics(
            inc_g.get(cid, []), bal_g.get(cid, []), cfw_g.get(cid, []),
            market_cap=mcap_by_symbol.get(c["symbol"]))
        rows.append({"symbol": c["symbol"], "name": c["name"],
                     "sector": c["sector"], "industry": c["industry"], **m})
    return pd.DataFrame(rows)


def load_score_panel(score_version: str | None = None,
                     horizon_months: int = 12) -> pd.DataFrame:
    """Painel PIT (date, symbol, score, fwd_return) para o backtest da Fase 6.

    Junta market_us.score_vintages (histórico PIT) a prices_monthly. Vazio até o
    histórico de scores ser computado (run_us_ingest.py score-history).
    """
    from data_pipeline.us.scoring_history import build_annual_panel
    cols = ["date", "symbol", "score", "fwd_return"]
    eng = _engine()
    if eng is None or not schema_ready():
        return pd.DataFrame(columns=cols)
    try:
        with eng.connect() as conn:
            vq = ("SELECT as_of_date, symbol, score FROM market_us.score_vintages "
                  "WHERE track='fundamental'")
            params: dict = {}
            if score_version:
                vq += " AND score_version=:v"
                params["v"] = score_version
            vintages = pd.read_sql(text(vq), conn, params=params)
            if vintages.empty:
                return pd.DataFrame(columns=cols)
            monthly = pd.read_sql(text(
                "SELECT symbol, month_end, adjusted_close "
                "FROM market_us.prices_monthly"), conn)
    except Exception as exc:  # noqa: BLE001
        logger.warning("load_score_panel falhou: %s", exc)
        return pd.DataFrame(columns=cols)
    return build_annual_panel(vintages, monthly, horizon_months=horizon_months)


def load_ingestion_runs() -> pd.DataFrame:
    eng = _engine()
    cols = ["run_key", "domain", "status", "calls_made", "rows_written",
            "started_at", "finished_at"]
    if eng is None or not schema_ready():
        return pd.DataFrame(columns=cols)
    try:
        with eng.connect() as conn:
            return pd.read_sql(text(
                "SELECT run_key, domain, status, calls_made, rows_written, "
                "started_at, finished_at FROM market_us.ingestion_runs "
                "ORDER BY started_at DESC LIMIT 50"), conn)
    except Exception as exc:  # noqa: BLE001
        logger.warning("load_ingestion_runs falhou: %s", exc)
        return pd.DataFrame(columns=cols)
