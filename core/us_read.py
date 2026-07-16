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
from sqlalchemy import text

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
