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
from datetime import datetime, timezone

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


def _db_is_local() -> bool:
    """True se a engine atual aponta para o warehouse local (não Supabase)."""
    try:
        from core.config import settings
        url = (settings.db_url or "").lower()
        return "localhost" in url or "127.0.0.1" in url
    except Exception:  # noqa: BLE001
        return False


def snapshot_ready() -> bool:
    """True se a vitrine (company_snapshots) existir e tiver linhas."""
    eng = _engine()
    if eng is None:
        return False
    try:
        with eng.connect() as conn:
            if not conn.execute(text(
                    "SELECT to_regclass('market_us.company_snapshots')")).scalar():
                return False
            return bool(conn.execute(text(
                "SELECT 1 FROM market_us.company_snapshots LIMIT 1")).scalar())
    except Exception as exc:  # noqa: BLE001
        logger.warning("snapshot_ready falhou: %s", exc)
        return False


def data_status() -> dict:
    """Status para os badges da view (conexão, schema/vitrine, defasagem)."""
    eng = _engine()
    is_local = _db_is_local()
    if eng is None:
        return {"connected": False, "schema_ready": False, "offline": True,
                "mode": "none", "db_is_local": is_local, "last_update": None,
                "companies": 0, "reason": "banco não configurado"}
    if not schema_ready():
        # Sem as tabelas completas, a vitrine (snapshot publicado) ainda serve
        # a leitura — é assim que o deploy no Streamlit Cloud mostra dados.
        if snapshot_ready():
            try:
                with eng.connect() as conn:
                    n = conn.execute(text(
                        "SELECT COUNT(*) FROM market_us.company_snapshots")).scalar() or 0
                    last = conn.execute(text(
                        "SELECT MAX(generated_at) FROM market_us.company_snapshots")).scalar()
            except Exception:  # noqa: BLE001
                n, last = 0, None
            return {"connected": True, "schema_ready": True, "offline": False,
                    "mode": "snapshot", "db_is_local": is_local,
                    "last_update": last, "companies": int(n),
                    "reason": ("Dados via vitrine (snapshot publicado do warehouse "
                               "local). Backtests e sincronização rodam só localmente.")}
        # Nem tabelas completas nem vitrine: a instrução muda conforme o ambiente.
        if is_local:
            reason = ("schema market_us ausente — rode "
                      "`python run_us_ingest.py init-schema --warehouse` e depois o bootstrap")
        else:
            reason = ("Esta seção usa o warehouse LOCAL. Para o deploy mostrar "
                      "dados, publique a vitrine: `python run_us_ingest.py snapshot "
                      "--warehouse` e depois `python scripts/publish_us_snapshot.py` "
                      "(ver aba Sincronização).")
        return {"connected": True, "schema_ready": False, "offline": True,
                "mode": "none", "db_is_local": is_local, "last_update": None,
                "companies": 0, "reason": reason}
    try:
        with eng.connect() as conn:
            companies = conn.execute(text(
                "SELECT COUNT(*) FROM market_us.companies")).scalar() or 0
            last = conn.execute(text(
                "SELECT MAX(ingested_at) FROM market_us.income_statements")).scalar()
        return {"connected": True, "schema_ready": True,
                "offline": companies == 0, "mode": "warehouse",
                "db_is_local": is_local, "last_update": last,
                "companies": int(companies), "reason": None}
    except Exception as exc:  # noqa: BLE001
        logger.warning("data_status falhou: %s", exc)
        return {"connected": False, "schema_ready": False, "offline": True,
                "mode": "none", "db_is_local": is_local, "last_update": None,
                "companies": 0, "reason": str(exc)[:120]}


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
    where = ["a.analysis_status='eligible'"]
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


_COMPANY_FINANCIAL_COLS = [
    "fiscal_year", "revenue", "operating_income", "ebit", "ebitda",
    "net_income", "total_assets", "total_equity", "cash_and_equivalents",
    "short_term_debt", "long_term_debt", "total_debt", "net_debt",
    "current_assets", "current_liabilities", "invested_capital",
    "shares_outstanding", "operating_cash_flow", "investing_cash_flow",
    "capex", "free_cash_flow", "dividends_paid", "dividends_per_share",
]


def load_company_financials(symbol: str) -> pd.DataFrame:
    """Série anual GAAP consolidada para a análise individual da empresa."""
    eng = _engine()
    cols = list(_COMPANY_FINANCIAL_COLS)
    if eng is None or not schema_ready() or not symbol:
        return pd.DataFrame(columns=cols)
    sql = (
        "SELECT i.fiscal_year, i.revenue, i.operating_income, i.ebit, i.ebitda, "
        "i.net_income, b.total_assets, b.total_equity, b.cash_and_equivalents, "
        "b.short_term_debt, b.long_term_debt, b.total_debt, b.net_debt, "
        "b.current_assets, b.current_liabilities, b.invested_capital, "
        "b.shares_outstanding, cf.operating_cash_flow, "
        "CASE WHEN cf.capex IS NULL AND cf.acquisitions IS NULL AND cf.investments IS NULL "
        "THEN NULL ELSE COALESCE(cf.capex, 0) + COALESCE(cf.acquisitions, 0) + "
        "COALESCE(cf.investments, 0) END AS investing_cash_flow, "
        "cf.capex, cf.free_cash_flow, cf.dividends_paid, "
        "CASE WHEN b.shares_outstanding IS NOT NULL AND b.shares_outstanding <> 0 "
        "THEN ABS(cf.dividends_paid) / b.shares_outstanding END AS dividends_per_share "
        "FROM market_us.income_statements i "
        "LEFT JOIN market_us.balance_sheets b "
        "  ON b.company_id=i.company_id AND b.period=i.period "
        "  AND b.fiscal_year=i.fiscal_year AND b.fiscal_quarter=i.fiscal_quarter "
        "  AND b.quality_status IN ('raw','validated') "
        "LEFT JOIN market_us.cash_flow_statements cf "
        "  ON cf.company_id=i.company_id AND cf.period=i.period "
        "  AND cf.fiscal_year=i.fiscal_year AND cf.fiscal_quarter=i.fiscal_quarter "
        "  AND cf.quality_status IN ('raw','validated') "
        "WHERE i.symbol=:sym AND i.period='annual' "
        "AND i.quality_status IN ('raw','validated') "
        "ORDER BY i.fiscal_year")
    try:
        with eng.connect() as conn:
            return pd.read_sql(text(sql), conn, params={"sym": symbol.upper()})
    except Exception as exc:  # noqa: BLE001
        logger.warning("load_company_financials falhou: %s", exc)
        return pd.DataFrame(columns=cols)


def load_company_market_data(symbol: str) -> dict:
    """Preço, dividendos e múltiplos históricos do warehouse, sem chamadas externas."""
    empty = {
        "prices": pd.DataFrame(columns=["date", "price"]),
        "dividends": pd.DataFrame(columns=["date", "amount"]),
        "metrics": pd.DataFrame(columns=["fiscal_year", "metric_name", "metric_value"]),
    }
    eng = _engine()
    if eng is None or not schema_ready() or not symbol:
        return empty
    sym = symbol.upper()
    try:
        with eng.connect() as conn:
            prices = pd.read_sql(text(
                "SELECT date, COALESCE(adjusted_close, close) AS price "
                "FROM market_us.prices_daily WHERE symbol=:s ORDER BY date"),
                conn, params={"s": sym})
            dividends = pd.read_sql(text(
                "SELECT event_date AS date, COALESCE(adjusted_amount, amount) AS amount "
                "FROM market_us.dividends WHERE symbol=:s ORDER BY event_date"),
                conn, params={"s": sym})
            metrics = pd.read_sql(text(
                "SELECT fiscal_year, metric_name, metric_value "
                "FROM market_us.key_metrics WHERE symbol=:s AND period='annual' "
                "ORDER BY fiscal_year, metric_name"), conn, params={"s": sym})
        return {"prices": prices, "dividends": dividends, "metrics": metrics}
    except Exception as exc:  # noqa: BLE001
        logger.warning("load_company_market_data(%s) falhou: %s", sym, exc)
        return empty


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
                 "current_liabilities", "invested_capital", "shares_outstanding",
                 "total_liabilities", "short_term_debt", "long_term_debt",
                 "retained_earnings")
_CASHFLOW_COLS = ("fiscal_year", "operating_cash_flow", "capex", "free_cash_flow",
                  "acquisitions", "investments", "dividends_paid",
                  "stock_repurchase", "stock_issuance", "depreciation_and_amortization",
                  # SBC alimenta sbc_to_revenue/fcf_ex_sbc_margin no score (v0.5.0).
                  "stock_based_compensation")


def _latest_close(conn, symbol: str):
    try:
        return conn.execute(text(
            "SELECT COALESCE(adjusted_close, close) FROM market_us.prices_daily "
            "WHERE symbol=:s ORDER BY date DESC LIMIT 1"), {"s": symbol}).scalar()
    except Exception:  # noqa: BLE001
        return None


def _latest_shares(balance_rows) -> float | None:
    """Ações em circulação mais recentes com dado (float)."""
    for r in reversed(balance_rows or []):
        v = r.get("shares_outstanding")
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                return None
    return None


def _latest_market_cap(conn, symbol: str, balance_rows=None):
    """Market cap: da market_cap_history se houver; senão preço×ações (derivado).

    A ingestão atual não popula market_cap_history (EDGAR não dá cotação); então
    derivamos do último preço (yfinance) × ações em circulação (balanço). Sem isso
    valuation e Altman Z ficariam vazios.
    """
    try:
        mc = conn.execute(text(
            "SELECT market_cap FROM market_us.market_cap_history "
            "WHERE symbol=:s ORDER BY date DESC LIMIT 1"), {"s": symbol}).scalar()
        if mc is not None:
            return mc
    except Exception:  # noqa: BLE001
        pass
    close = _latest_close(conn, symbol)
    shares = _latest_shares(balance_rows)
    if close is not None and shares:
        try:
            return float(close) * shares
        except (TypeError, ValueError):
            return None
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
                "WHERE a.symbol=:s AND a.analysis_status='eligible' LIMIT 1"),
                {"s": sym}).fetchone()
            if ident is None:
                return None
            cid = int(ident[0])

            def _series(table, cols):
                q = (f"SELECT {', '.join(cols)} FROM market_us.{table} "
                     f"WHERE company_id=:c AND period='annual' "
                     f"AND quality_status IN ('raw','validated') ORDER BY fiscal_year")
                return [dict(r._mapping) for r in conn.execute(text(q), {"c": cid})]

            balance = _series("balance_sheets", _BALANCE_COLS)
            return {
                "name": ident[1], "sector": ident[2], "industry": ident[3],
                "income": _series("income_statements", _INCOME_COLS),
                "balance": balance,
                "cashflow": _series("cash_flow_statements", _CASHFLOW_COLS),
                "market_cap": _latest_market_cap(conn, sym, balance),
                "price": None,
            }
    except Exception as exc:  # noqa: BLE001
        logger.warning("load_company_bundle(%s) falhou: %s", sym, exc)
        return None


def load_scoring_frame(limit_companies: int | None = None) -> pd.DataFrame:
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
            q = ("SELECT c.id, MIN(a.symbol) AS symbol, MAX(c.name) AS name, "
                 "MAX(c.sector) AS sector, MAX(c.industry) AS industry "
                 "FROM market_us.companies c JOIN market_us.assets a ON a.company_id=c.id "
                 "AND a.analysis_status='eligible' "
                 "WHERE EXISTS (SELECT 1 FROM market_us.income_statements i "
                 "              WHERE i.company_id=c.id AND i.period='annual') "
                 "GROUP BY c.id ORDER BY c.id")
            params: dict = {}
            if limit_companies:               # None/0 = universo inteiro
                q += " LIMIT :lim"
                params["lim"] = int(limit_companies)
            comp = pd.read_sql(text(q), conn, params=params)
            if comp.empty:
                return pd.DataFrame(columns=cols)
            ids = [int(x) for x in comp["id"].tolist()]

            def _bulk(table, cols_):
                q = text(f"SELECT company_id, {', '.join(cols_)} "
                          f"FROM market_us.{table} WHERE period='annual' "
                          f"AND quality_status IN ('raw','validated') "
                          f"AND company_id IN :ids ORDER BY company_id, fiscal_year"
                         ).bindparams(bindparam("ids", expanding=True))
                return pd.read_sql(q, conn, params={"ids": ids})

            inc = _bulk("income_statements", _INCOME_COLS)
            bal = _bulk("balance_sheets", _BALANCE_COLS)
            cfw = _bulk("cash_flow_statements", _CASHFLOW_COLS)
            mcaps = pd.read_sql(text(
                "SELECT DISTINCT ON (symbol) symbol, market_cap "
                "FROM market_us.market_cap_history ORDER BY symbol, date DESC"), conn)
            # último preço por símbolo (deriva market cap quando não há histórico)
            closes = pd.read_sql(text(
                "SELECT DISTINCT ON (symbol) symbol, COALESCE(adjusted_close, close) AS px "
                "FROM market_us.prices_daily ORDER BY symbol, date DESC"), conn)
    except Exception as exc:  # noqa: BLE001
        logger.warning("load_scoring_frame falhou: %s", exc)
        return pd.DataFrame(columns=cols)

    mcap_by_symbol = dict(zip(mcaps.get("symbol", []), mcaps.get("market_cap", []))) \
        if not mcaps.empty else {}
    close_by_symbol = dict(zip(closes.get("symbol", []), closes.get("px", []))) \
        if not closes.empty else {}
    inc_g = {k: v.to_dict("records") for k, v in inc.groupby("company_id")} if not inc.empty else {}
    bal_g = {k: v.to_dict("records") for k, v in bal.groupby("company_id")} if not bal.empty else {}
    cfw_g = {k: v.to_dict("records") for k, v in cfw.groupby("company_id")} if not cfw.empty else {}

    rows = []
    for _, c in comp.iterrows():
        cid = int(c["id"])
        bal_rows = bal_g.get(cid, [])
        mcap = mcap_by_symbol.get(c["symbol"])
        if mcap is None:  # deriva: último preço × ações em circulação
            shares = _latest_shares(bal_rows)
            px = close_by_symbol.get(c["symbol"])
            if px is not None and shares:
                mcap = float(px) * shares
        m = compute_company_metrics(
            inc_g.get(cid, []), bal_rows, cfw_g.get(cid, []), market_cap=mcap)
        rows.append({"symbol": c["symbol"], "name": c["name"],
                     "sector": c["sector"], "industry": c["industry"], **m})
    return pd.DataFrame(rows)


def load_advanced_snapshot(symbol: str) -> dict | None:
    """Indicadores avançados (Piotroski/Altman/Sloan/ROIC incremental) de 1 empresa."""
    from core.us_advanced import advanced_snapshot
    bundle = load_company_bundle(symbol)
    if not bundle or not bundle.get("income"):
        return None
    snap = advanced_snapshot(bundle["income"], bundle.get("balance", []),
                             bundle.get("cashflow", []), bundle.get("market_cap"))
    snap["symbol"] = (symbol or "").upper()
    snap["name"] = bundle.get("name")
    snap["sector"] = bundle.get("sector")
    return snap


def load_asymmetry_frame(limit_companies: int | None = None) -> pd.DataFrame:
    """Cross-section de assimetria (Empresas Fora da Curva). Uma linha por empresa.

    Reaproveita as séries anuais e calcula métricas + trajetória + score de
    assimetria (core.us_asymmetry). Vazio se não houver dados.
    """
    from core.us_asymmetry import build_trajectory, score_asymmetry
    from core.us_metrics import compute_company_metrics
    cols = ["symbol", "name", "sector", "asymmetry_score", "confidence", "stage"]
    eng = _engine()
    if eng is None or not schema_ready():
        return pd.DataFrame(columns=cols)
    try:
        with eng.connect() as conn:
            q = ("SELECT c.id, MIN(a.symbol) AS symbol, MAX(c.name) AS name, "
                 "MAX(c.sector) AS sector, MAX(c.industry) AS industry "
                 "FROM market_us.companies c JOIN market_us.assets a ON a.company_id=c.id "
                 "AND a.analysis_status='eligible' "
                 "WHERE EXISTS (SELECT 1 FROM market_us.income_statements i "
                 "  WHERE i.company_id=c.id AND i.period='annual') "
                 "GROUP BY c.id ORDER BY c.id")
            params = {}
            if limit_companies:
                q += " LIMIT :lim"
                params["lim"] = int(limit_companies)
            comp = pd.read_sql(text(q), conn, params=params)
            if comp.empty:
                return pd.DataFrame(columns=cols)
            ids = [int(x) for x in comp["id"]]

            def _bulk(table, cols_):
                q = text(f"SELECT company_id, {', '.join(cols_)} "
                         f"FROM market_us.{table} WHERE period='annual' "
                         f"AND quality_status IN ('raw','validated') "
                         f"AND company_id IN :ids ORDER BY company_id, fiscal_year"
                         ).bindparams(bindparam("ids", expanding=True))
                return pd.read_sql(q, conn, params={"ids": ids})

            inc = _bulk("income_statements", _INCOME_COLS)
            bal = _bulk("balance_sheets", _BALANCE_COLS)
            cfw = _bulk("cash_flow_statements", _CASHFLOW_COLS)
    except Exception as exc:  # noqa: BLE001
        logger.warning("load_asymmetry_frame falhou: %s", exc)
        return pd.DataFrame(columns=cols)

    inc_g = {k: v.to_dict("records") for k, v in inc.groupby("company_id")} if not inc.empty else {}
    bal_g = {k: v.to_dict("records") for k, v in bal.groupby("company_id")} if not bal.empty else {}
    cfw_g = {k: v.to_dict("records") for k, v in cfw.groupby("company_id")} if not cfw.empty else {}

    rows = []
    for _, c in comp.iterrows():
        cid = int(c["id"])
        i, b, f = inc_g.get(cid, []), bal_g.get(cid, []), cfw_g.get(cid, [])
        m = compute_company_metrics(i, b, f)
        if m.get("_years", 0) < 3:
            continue
        a = score_asymmetry(m, build_trajectory(i, b, f))
        rows.append({"symbol": c["symbol"], "name": c["name"], "sector": c["sector"],
                     "industry": c["industry"], **a})
    df = pd.DataFrame(rows)
    return df.sort_values("asymmetry_score", ascending=False).reset_index(drop=True) \
        if not df.empty else df


# ── Vitrine (snapshot) — leitura no deploy/Supabase ───────────────────────────
_SNAP_IDENTITY = ("symbol", "cik", "name", "sector", "industry", "exchange",
                  "security_type", "is_reit", "is_active")
_SNAP_SCORES = ("score", "score_quality", "score_growth", "score_solidity",
                 "score_capital_efficiency", "score_valuation", "score_shareholder",
                 "coverage", "score_confidence", "score_status", "critical_missing")


def _parse_json_col(value):
    """JSONB pode chegar como dict (psycopg2) ou str — normaliza para objeto."""
    if value is None or isinstance(value, (dict, list)):
        return value
    try:
        import json
        return json.loads(value)
    except (TypeError, ValueError):
        return None


def _snapshot_df(extra_json: str | None = None,
                 extra_jsons: tuple[str, ...] = ()) -> pd.DataFrame:
    eng = _engine()
    if eng is None:
        return pd.DataFrame()
    cols = list(_SNAP_IDENTITY) + list(_SNAP_SCORES)
    if extra_json:
        cols.append(extra_json)
    cols.extend(c for c in extra_jsons if c not in cols)
    try:
        with eng.connect() as conn:
            return pd.read_sql(text(
                f"SELECT {', '.join(cols)} FROM market_us.company_snapshots "
                f"ORDER BY score DESC NULLS LAST"), conn)
    except Exception as exc:  # noqa: BLE001
        logger.warning("_snapshot_df falhou: %s", exc)
        return pd.DataFrame()


def load_snapshot_scored() -> pd.DataFrame:
    """Frame equivalente ao scored_universe, lido da vitrine (métricas incluídas).

    Expande também o bloco ``advanced`` (Altman Z, Piotroski F, accruals de
    Sloan, ROIC incremental). Esses indicadores eram calculados e gravados para
    TODAS as empresas, mas só apareciam na análise individual — nunca chegavam
    à construção de carteira. Trazê-los ao frame é o que permite usá-los como
    penalidade de risco em ``core/us_advanced_lab.build_entry_scores``.
    """
    # `financials` NÃO entra aqui. Medido em 03/08/2026 sobre a vitrine
    # publicada: a consulta inteira trafega 29 MB descomprimidos, dos quais
    # **24 MB (83%) são esse bloco** — e ele só serve de reserva para derivar
    # payout_ratio em vitrine antiga. Buscá-lo sempre fazia a leitura estourar a
    # conexão ("SSL connection has been closed unexpectedly") antes de terminar;
    # sem ele, as mesmas 3.052 linhas chegam em 70s.
    #
    # Continua havendo reserva: se payout_ratio faltar, uma segunda consulta traz
    # só symbol+financials. Vitrine atual não precisa dela (2.830 de 2.830 têm a
    # chave), então o caminho caro deixou de ser o caminho normal.
    df = _snapshot_df("metrics", extra_jsons=("advanced",))
    if df.empty:
        return df
    metric_dicts = [(_parse_json_col(v) or {}) for v in df.pop("metrics")]
    metrics_df = pd.DataFrame(metric_dicts, index=df.index)
    blocos = [df, metrics_df]
    if "advanced" in df.columns:
        avancado = [(_parse_json_col(v) or {}) for v in blocos[0].pop("advanced")]
        blocos.append(pd.DataFrame(avancado, index=df.index))
    out = pd.concat(blocos, axis=1)

    # payout_ratio passou a ser calculado em core.us_metrics, mas uma vitrine
    # gerada antes disso não o tem. Derivar de `financials` (que traz
    # dividends_paid e net_income por exercício) faz a verificação valer sem
    # exigir re-publicação — e continua correta depois dela.
    if "payout_ratio" not in out.columns or out["payout_ratio"].isna().all():
        out["payout_ratio"] = _payout_da_vitrine_antiga(out.get("symbol"))
    return out


def _payout_da_vitrine_antiga(symbols) -> list | None:
    """Deriva payout_ratio do bloco `financials`, só quando ele falta.

    Consulta separada e deliberadamente rara: é o bloco mais pesado da vitrine.
    Falha de leitura devolve None em vez de levantar — a ausência do payout
    reduz a evidência de uma verificação, mas não pode esvaziar a aba inteira.
    """
    eng = _engine()
    if eng is None or symbols is None:
        return None
    try:
        with eng.connect() as conn:
            bruto = dict(conn.execute(text(
                "SELECT symbol, financials FROM market_us.company_snapshots"
            )).fetchall())
    except Exception as exc:  # noqa: BLE001
        logger.warning("payout de reserva indisponível: %s", exc)
        return None
    return [_payout_do_historico(_parse_json_col(bruto.get(s)) or [])
            for s in symbols]


def _payout_do_historico(historico) -> float | None:
    """Payout do último exercício com lucro positivo. None se indeterminável."""
    if not isinstance(historico, list) or not historico:
        return None
    anos = [linha for linha in historico
            if isinstance(linha, dict) and linha.get("fiscal_year") is not None]
    for linha in sorted(anos, key=lambda x: x["fiscal_year"], reverse=True):
        lucro, dividendos = linha.get("net_income"), linha.get("dividends_paid")
        if lucro is None or dividendos is None:
            continue
        try:
            lucro, dividendos = float(lucro), abs(float(dividendos))
        except (TypeError, ValueError):
            continue
        if lucro > 0:                      # payout sobre prejuízo não é razão
            return dividendos / lucro
    return None


def load_snapshot_asymmetry() -> pd.DataFrame:
    """Frame equivalente ao asymmetry_universe, lido da vitrine."""
    df = _snapshot_df("asymmetry")
    if df.empty:
        return df
    asym = [(_parse_json_col(v) or {}) for v in df.pop("asymmetry")]
    asym_df = pd.DataFrame(asym, index=df.index)
    out = pd.concat([df[["symbol", "name", "sector", "industry"]], asym_df], axis=1)
    out = out.dropna(subset=["asymmetry_score"])
    return out.sort_values("asymmetry_score", ascending=False).reset_index(drop=True)


def _snapshot_json_for(symbol: str, column: str):
    eng = _engine()
    if eng is None or not symbol:
        return None
    try:
        with eng.connect() as conn:
            val = conn.execute(text(
                f"SELECT {column} FROM market_us.company_snapshots WHERE symbol=:s"),
                {"s": symbol.upper()}).scalar()
        return _parse_json_col(val)
    except Exception as exc:  # noqa: BLE001
        logger.warning("_snapshot_json_for(%s,%s) falhou: %s", symbol, column, exc)
        return None


def load_snapshot_dossie(symbol: str) -> dict | None:
    return _snapshot_json_for(symbol, "dossie")


def load_snapshot_advanced(symbol: str) -> dict | None:
    return _snapshot_json_for(symbol, "advanced")


def load_snapshot_financials(symbol: str) -> pd.DataFrame:
    rows = _snapshot_json_for(symbol, "financials") or []
    return (pd.DataFrame(rows).reindex(columns=_COMPANY_FINANCIAL_COLS)
            if rows else pd.DataFrame(columns=_COMPANY_FINANCIAL_COLS))


def load_snapshot_company_market_data(symbol: str) -> dict:
    """Históricos compactos publicados dentro do dossiê da vitrine."""
    dossie = load_snapshot_dossie(symbol) or {}
    payload = dossie.get("_company_analysis", {}) if isinstance(dossie, dict) else {}
    return {
        "prices": pd.DataFrame(payload.get("prices", []), columns=["date", "price"]),
        "dividends": pd.DataFrame(
            payload.get("dividends", []), columns=["date", "amount"]),
        "metrics": pd.DataFrame(
            payload.get("metrics", []),
            columns=["fiscal_year", "metric_name", "metric_value"]),
    }


def load_snapshot_companies(sector: str | None = None, search: str | None = None,
                            limit: int = 500) -> pd.DataFrame:
    df = _snapshot_df()
    if df.empty:
        return df[[c for c in _SNAP_IDENTITY if c in df.columns]] \
            if len(df.columns) else pd.DataFrame(columns=list(_SNAP_IDENTITY))
    out = df[list(_SNAP_IDENTITY)].copy()
    if sector:
        out = out[out["sector"] == sector]
    if search:
        q = search.lower()
        out = out[out["symbol"].str.lower().str.contains(q, na=False)
                  | out["name"].str.lower().str.contains(q, na=False)]
    return out.sort_values("symbol").head(limit).reset_index(drop=True)


def load_snapshot_overview() -> dict:
    df = _snapshot_df()
    base = {"companies": 0, "assets": 0, "sectors": 0, "delisted": 0,
            "reits": 0, "last_update": None, "with_statements": 0}
    if df.empty:
        return base
    base["companies"] = base["assets"] = base["with_statements"] = len(df)
    base["sectors"] = int(df["sector"].dropna().nunique())
    base["reits"] = int(df["is_reit"].fillna(False).sum())
    base["delisted"] = int((~df["is_active"].fillna(True)).sum())
    eng = _engine()
    try:
        with eng.connect() as conn:
            base["last_update"] = conn.execute(text(
                "SELECT MAX(generated_at) FROM market_us.company_snapshots")).scalar()
    except Exception:  # noqa: BLE001
        pass
    return base


def load_score_panel(score_version: str | None = None,
                     horizon_months: int = 12) -> pd.DataFrame:
    """Painel PIT (date, symbol, score, fwd_return) para o backtest da Fase 6.

    Junta market_us.score_vintages (histórico PIT) a prices_monthly. Vazio até o
    histórico de scores ser computado (run_us_ingest.py score-history).
    """
    from data_pipeline.us.scoring_history import build_annual_panel
    if score_version is None:
        from core.us_methodology import US_FUNDAMENTAL_SCORE_VERSION
        score_version = US_FUNDAMENTAL_SCORE_VERSION
    cols = ["date", "symbol", "score", "fwd_return"]
    eng = _engine()
    if eng is None or not schema_ready():
        return pd.DataFrame(columns=cols)
    try:
        with eng.connect() as conn:
            vq = ("SELECT as_of_date, symbol, score FROM market_us.score_vintages "
                  "WHERE track='fundamental'")
            params: dict = {}
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


def load_us_giro_diario(dias: int = 180) -> dict[str, dict]:
    """{symbol: {"giro_diario_usd": float, "giro_diario_usd_at": iso str|None}}.

    Diferente do B3, aqui a série é DIÁRIA de verdade (``prices_daily``), então
    não há a aproximação de dividir barra mensal por 21 pregões. Mediana e não
    média: um dia de leilão ou de entrada em índice não vira liquidez
    permanente.

    ``giro_diario_usd_at`` é a data da observação de preço mais recente dentro
    da janela (``MAX(date)``), em meia-noite UTC — é o timestamp que
    ``core.us_liquidity._timestamp_atual`` usa para decidir se a medição de
    negociabilidade está atual (gate de liquidez). Sem esta chave, nenhuma
    medição de giro tinha como ser considerada "atual" em lugar nenhum do
    pipeline, e o piso de negociabilidade ficava permanentemente
    inatingível — achado descoberto ao investigar por que a Criação de
    Portfólio bloqueava 100% do universo mesmo com preços recém-atualizados.
    """
    eng = _engine()
    if eng is None or not schema_ready():
        return {}
    with eng.connect() as conn:
        df = pd.read_sql_query(text("""
        SELECT p.symbol,
               percentile_cont(0.5) WITHIN GROUP (
                   ORDER BY (p.close * p.volume)::double precision) AS giro,
               MAX(p.date) AS ultima_data
        FROM market_us.prices_daily p
        WHERE p.date >= CURRENT_DATE - make_interval(days => :dias)
          AND p.volume > 0 AND p.close > 0
        GROUP BY p.symbol
        """), conn, params={"dias": int(dias)})
    if df is None or df.empty:
        return {}
    out: dict[str, dict] = {}
    for _, row in df.iterrows():
        try:
            giro = float(row["giro"])
        except (TypeError, ValueError):
            continue
        symbol = str(row["symbol"]).strip().upper()
        ultima_data = row.get("ultima_data")
        as_of = None
        if ultima_data is not None and pd.notna(ultima_data):
            ts = pd.Timestamp(ultima_data)
            as_of = datetime(ts.year, ts.month, ts.day, tzinfo=timezone.utc).isoformat()
        out[symbol] = {"giro_diario_usd": giro, "giro_diario_usd_at": as_of}
    return out


def load_us_resiliencia() -> dict[str, dict]:
    """Margem operacional nas recessões vs anos normais, por símbolo.

    Devolve também quantos exercícios caem em CADA janela de crise, porque o
    consumidor precisa saber se o número vem de 2008 (recessão de demanda) ou só
    da COVID (choque de oferta, curto e com estímulo fiscal). Ver
    core.us_cycle_evidence para por que só a primeira sustenta veredito.
    """
    from core.us_cycle_evidence import (
        CRISE_2008,
        CRISE_COVID,
        MARGEM_NORMAL_MINIMA,
    )

    crises = list(CRISE_2008) + list(CRISE_COVID)
    eng = _engine()
    if eng is None or not schema_ready():
        return {}
    with eng.connect() as conn:
        df = pd.read_sql_query(text("""
        WITH m AS (
            SELECT symbol, fiscal_year,
                   operating_income::double precision / NULLIF(revenue, 0) AS margem
            FROM market_us.income_statements
            WHERE period = 'annual' AND revenue > 0
              AND operating_income IS NOT NULL
        )
        SELECT symbol,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY margem)
                 FILTER (WHERE fiscal_year = ANY(:crises))       AS margem_crise,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY margem)
                 FILTER (WHERE NOT (fiscal_year = ANY(:crises))) AS margem_normal,
               count(*) FILTER (WHERE fiscal_year = ANY(:c2008)) AS anos_2008,
               count(*) FILTER (WHERE fiscal_year = ANY(:covid)) AS anos_covid
        FROM m GROUP BY symbol
        """), conn, params={"crises": crises, "c2008": list(CRISE_2008),
                            "covid": list(CRISE_COVID)})
    if df is None or df.empty:
        return {}

    out: dict[str, dict] = {}
    for _, row in df.iterrows():
        try:
            normal = float(row["margem_normal"])
            crise = float(row["margem_crise"])
        except (TypeError, ValueError):
            continue
        # Margem normal <= 0 torna a razão sem sentido: dois negativos dão
        # positivo, o mesmo defeito do ROE sobre patrimônio negativo no B3.
        #
        # E margem normal PERTO de zero explode a razão sem que nada tenha
        # acontecido com o negócio: medido em 03/08/2026, AZTA tinha margem
        # normal de 0,10% e aparecia com razão de 59,74. O piso de 2% custa 25
        # das 525 empresas com a crise de 2008 e elimina a cauda inventada.
        if normal != normal or crise != crise or normal < MARGEM_NORMAL_MINIMA:
            continue
        out[str(row["symbol"]).strip().upper()] = {
            "razao": crise / normal, "margem_normal": normal,
            "margem_crise": crise,
            "anos_2008": int(row["anos_2008"] or 0),
            "anos_covid": int(row["anos_covid"] or 0),
        }
    return out


def load_us_macro() -> dict:
    """Último valor observado de cada indicador macro (market_us.macro_observations).

    Offline: lê só o warehouse. Devolve {} quando a tabela não existe ou está
    vazia — nesse caso a interface cai na premissa e diz que caiu, em vez de
    exibir número sem procedência.
    """
    eng = _engine()
    if eng is None:
        return {}
    try:
        with eng.connect() as conn:
            existe = conn.execute(text("""
                SELECT EXISTS (
                    SELECT 1 FROM information_schema.tables
                    WHERE table_schema = 'market_us'
                      AND table_name = 'macro_observations'
                )
            """)).scalar()
            if not existe:
                return {}
            linhas = conn.execute(text("""
                SELECT DISTINCT ON (indicator)
                       indicator, observed_at, value
                FROM market_us.macro_observations
                ORDER BY indicator, observed_at DESC
            """)).mappings().all()
    except Exception as exc:  # noqa: BLE001 - leitura opcional nunca quebra a UI
        logger.warning("load_us_macro falhou: %s", exc)
        return {}
    if not linhas:
        return {}

    from core.us_macro import FONTE_OBSERVADO

    snapshot: dict = {}
    datas = []
    for linha in linhas:
        try:
            snapshot[str(linha["indicator"])] = float(linha["value"])
        except (TypeError, ValueError):
            continue
        if linha["observed_at"] is not None:
            datas.append(linha["observed_at"])
    if not snapshot:
        return {}
    snapshot["fonte"] = FONTE_OBSERVADO
    # A data-base do conjunto é a do indicador MAIS DEFASADO: o regime só vale
    # até onde a série mais antiga alcança.
    snapshot["as_of"] = min(datas).isoformat() if datas else None
    return snapshot


# ── Série mensal (formato do análogo da B3) ───────────────────────────────────

def load_precos_mensais_us(symbols: tuple[str, ...], *, engine=None) -> pd.DataFrame:
    """Preços mensais dos EUA (adjusted_close = retorno total) no MESMO formato
    de core.market_read.load_precos_mensais: DatetimeIndex mensal × colunas =
    símbolos. Necessário porque core.global_portfolio.returns.retornos_mensais
    consome as duas fontes pelo mesmo caminho e as concatena alinhando por
    data — um formato divergente aqui viraria coluna duplicada ou índice
    desalinhado, e o defeito apareceria como cobertura estranha, não como erro.

    `engine` é injetável (os testes usam SQLite em memória); por padrão resolve
    pelo motor do módulo. SQLite não tem schema, então o nome da tabela muda
    por dialeto — mesmo truque de scripts.publish_us_prices_monthly.
    Símbolos vazios, ou tabela ausente, devolvem DataFrame vazio (nunca levanta).
    """
    if not symbols:
        return pd.DataFrame()
    syms = [s.strip().upper() for s in symbols if s and s.strip()]
    if not syms:
        return pd.DataFrame()
    eng = engine if engine is not None else _engine()
    if eng is None:
        return pd.DataFrame()
    tabela = ("prices_monthly" if eng.dialect.name == "sqlite"
              else "market_us.prices_monthly")
    try:
        with eng.connect() as conn:
            df = pd.read_sql_query(
                text(f"SELECT symbol, month_end, adjusted_close FROM {tabela} "
                     "WHERE symbol IN :symbols AND adjusted_close IS NOT NULL "
                     "ORDER BY symbol, month_end").bindparams(
                         bindparam("symbols", expanding=True)),
                conn, params={"symbols": syms})
    except Exception as exc:  # noqa: BLE001 - tabela ausente/schema não criado
        logger.warning("load_precos_mensais_us falhou: %s", exc)
        return pd.DataFrame()
    if df.empty:
        return pd.DataFrame()
    df["month_end"] = pd.to_datetime(df["month_end"], errors="coerce")
    df = df.dropna(subset=["month_end"])
    wide = df.pivot_table(index="month_end", columns="symbol",
                          values="adjusted_close", aggfunc="last")
    mensal = wide.resample("ME").last()           # último preço válido de cada mês
    mensal.columns = [str(c).strip().upper() for c in mensal.columns]
    return mensal.dropna(how="all")
