"""
core/market_read.py
Loaders de leitura backed pelo schema market.* (BRAPI Pro), com as MESMAS
assinaturas e formatos de saída de core.b3_db — para troca por feature flag
(ver core.b3_data e MARKET_READ_SOURCE).

Cobertura nesta fase (paridade de colunas garantida a partir de market.*):
  • load_setores            ← market.assets + market.companies
  • load_multiplos_todos    ← snapshot ttm de market.calculated_metrics
  • load_multiplos          ← idem (linha única, Series)
  • load_historico_anos     ← anos distintos em market.income_statements

Os demais loaders (demonstrações, múltiplos históricos por ano, macro, snapshot
de carteira) ainda dependem de dados não precomputados em market.* e por isso
NÃO são reimplementados aqui — o facade (core.b3_data) os mantém no legado.
"""
from __future__ import annotations

import datetime as _dt

import pandas as pd
import streamlit as st
from sqlalchemy import text

# Métricas do snapshot, na MESMA grafia das colunas do legado public.multiplos.
# Liquidez_Corrente ainda não é computável a partir de market.* (gap conhecido):
# entra como coluna vazia para preservar o shape de saída.
_MULT_COLS = ["P/L", "P/VP", "DY", "ROE", "ROA", "ROIC",
              "Margem_Liquida", "Margem_Operacional", "Endividamento_Total",
              "Liquidez_Corrente", "EV_EBIT", "P_FCO", "Payout"]


@st.cache_resource(show_spinner=False)
def _engine():
    """Engine do banco unificado (onde vive o schema market.*)."""
    try:
        from core.database import get_engine
        return get_engine()
    except Exception:
        return None


def _q(sql: str, params: dict | None = None) -> pd.DataFrame:
    eng = _engine()
    if eng is None:
        return pd.DataFrame()
    try:
        with eng.connect() as conn:
            return pd.read_sql_query(text(sql), conn, params=params or {})
    except Exception:
        return pd.DataFrame()


def _norm_ticker(s: pd.Series) -> pd.Series:
    return s.astype(str).str.replace(".SA", "", regex=False).str.strip().str.upper()


# ── Pivot puro (testável sem banco) ───────────────────────────────────────────

def _pivot_metrics(long_df: pd.DataFrame) -> pd.DataFrame:
    """
    long_df: colunas Ticker, year, metric_name, metric_value (formato longo).
    Retorna formato largo com uma coluna por métrica + Ticker + year, garantindo
    todas as colunas de _MULT_COLS (ausentes viram NaN).
    """
    if long_df.empty:
        return pd.DataFrame(columns=["Ticker", "year", *_MULT_COLS])
    wide = long_df.pivot_table(index=["Ticker", "year"], columns="metric_name",
                               values="metric_value", aggfunc="last").reset_index()
    wide.columns.name = None
    for c in _MULT_COLS:
        if c not in wide.columns:
            wide[c] = pd.NA
    for c in _MULT_COLS:
        wide[c] = pd.to_numeric(wide[c], errors="coerce")
    return wide[["Ticker", "year", *_MULT_COLS]]


# ── Loaders ───────────────────────────────────────────────────────────────────

@st.cache_data(ttl=3600, show_spinner=False)
def load_setores() -> pd.DataFrame:
    """ticker, nome_empresa, SETOR, SUBSETOR, SEGMENTO (de market.assets+companies)."""
    df = _q("""
        SELECT a.ticker,
               COALESCE(c.name, a.ticker) AS nome_empresa,
               c.sector    AS "SETOR",
               c.subsector AS "SUBSETOR",
               c.segment   AS "SEGMENTO"
        FROM market.assets a
        LEFT JOIN market.companies c ON c.id = a.company_id
        WHERE a.ticker IS NOT NULL
        ORDER BY c.sector NULLS LAST, a.ticker
    """)
    if df.empty:
        return df
    df["ticker"] = _norm_ticker(df["ticker"])
    for col in ("nome_empresa", "SETOR", "SUBSETOR", "SEGMENTO"):
        if col not in df.columns:
            df[col] = ""
        df[col] = df[col].fillna("").astype(str)
    return df


def _multiplos_long(ticker: str | None = None) -> pd.DataFrame:
    """Snapshot ttm em formato longo, com o ano do último balanço anual por ticker."""
    where_tk = ""
    params: dict = {}
    if ticker is not None:
        where_tk = "AND cm.ticker = :tk"
        params["tk"] = ticker.strip().upper().replace(".SA", "")
    return _q(f"""
        WITH ly AS (
            SELECT ticker, MAX(year) AS y
            FROM market.income_statements
            WHERE period = 'annual'
            GROUP BY ticker
        )
        SELECT cm.ticker AS "Ticker", COALESCE(ly.y, 0) AS year,
               cm.metric_name, cm.metric_value
        FROM market.calculated_metrics cm
        LEFT JOIN ly ON ly.ticker = cm.ticker
        WHERE cm.period = 'ttm' {where_tk}
    """, params)


def _attach_data(wide: pd.DataFrame) -> pd.DataFrame:
    """Adiciona coluna `data` (31/12 do ano de referência) e remove `year`."""
    def _to_date(y):
        try:
            y = int(y)
            return pd.Timestamp(_dt.date(y, 12, 31)) if y > 0 else pd.NaT
        except Exception:
            return pd.NaT
    wide = wide.copy()
    wide["data"] = wide["year"].map(_to_date)
    return wide.drop(columns=["year"])


@st.cache_data(ttl=3600, show_spinner=False)
def load_multiplos_todos(ano_ref_max: int | None = None) -> pd.DataFrame:
    """Múltiplo mais recente (snapshot ttm) de TODOS os tickers — ranking/avançada."""
    long_df = _multiplos_long()
    if long_df.empty:
        return pd.DataFrame()
    if ano_ref_max is not None:
        long_df = long_df[long_df["year"] <= int(ano_ref_max)]
        if long_df.empty:
            return pd.DataFrame()
    wide = _pivot_metrics(long_df)
    wide["Ticker"] = _norm_ticker(wide["Ticker"])
    out = _attach_data(wide)
    return out[["Ticker", "data", *_MULT_COLS]]


@st.cache_data(ttl=3600, show_spinner=False)
def load_multiplos(ticker: str) -> pd.Series:
    """Linha mais recente de múltiplos (snapshot ttm) como Series."""
    long_df = _multiplos_long(ticker)
    if long_df.empty:
        return pd.Series(dtype=object)
    wide = _pivot_metrics(long_df)
    if wide.empty:
        return pd.Series(dtype=object)
    wide["Ticker"] = _norm_ticker(wide["Ticker"])
    return _attach_data(wide).iloc[0]


@st.cache_data(ttl=3600, show_spinner=False)
def load_historico_anos() -> dict[str, int]:
    """{ticker: nº de anos de demonstração anual} a partir de market.income_statements."""
    df = _q("""
        SELECT ticker, COUNT(DISTINCT year)::int AS anos
        FROM market.income_statements
        WHERE period = 'annual' AND ticker IS NOT NULL AND year IS NOT NULL
        GROUP BY ticker
    """)
    if df.empty:
        return {}
    out: dict[str, int] = {}
    for _, row in df.iterrows():
        tk = str(row["ticker"]).replace(".SA", "").strip().upper()
        try:
            out[tk] = int(row["anos"])
        except Exception:
            pass
    return out
