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
    """
    ticker, nome_empresa, SETOR, SUBSETOR, SEGMENTO.
    Universo/nomes vêm do market.* (assets+companies); a TAXONOMIA B3
    (Setor/Subsetor/Segmento) vem de public.setores — a brapi não traz a
    classificação B3 de forma consistente. public.setores é dado de
    referência (como cvm_to_ticker), não os fundamentos legados.
    """
    df = _q("""
        SELECT a.ticker,
               COALESCE(c.name, s.nome_empresa, a.ticker) AS nome_empresa,
               COALESCE(NULLIF(s."SETOR", ''),    NULLIF(c.sector, ''))    AS "SETOR",
               COALESCE(NULLIF(s."SUBSETOR", ''), NULLIF(c.subsector, '')) AS "SUBSETOR",
               COALESCE(NULLIF(s."SEGMENTO", ''), NULLIF(c.segment, ''))   AS "SEGMENTO"
        FROM market.assets a
        LEFT JOIN market.companies c ON c.id = a.company_id
        -- taxonomia B3: casa por ticker exato; senão pela RAIZ de 4 letras
        -- (PN/Unit herdam o setor do ON: BBDC4->BBDC3, ITUB4->ITUB3).
        LEFT JOIN LATERAL (
            SELECT s2."SETOR", s2."SUBSETOR", s2."SEGMENTO", s2.nome_empresa
            FROM public.setores s2
            WHERE s2."SETOR" IS NOT NULL
              AND (UPPER(REPLACE(s2.ticker, '.SA', '')) = a.ticker
                   OR LEFT(UPPER(REPLACE(s2.ticker, '.SA', '')), 4) = LEFT(a.ticker, 4))
            ORDER BY (UPPER(REPLACE(s2.ticker, '.SA', '')) = a.ticker) DESC
            LIMIT 1
        ) s ON TRUE
        WHERE a.ticker IS NOT NULL
        ORDER BY "SETOR" NULLS LAST, a.ticker
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


def _attach_data(wide: pd.DataFrame, col: str = "data") -> pd.DataFrame:
    """Adiciona coluna de data (31/12 do ano de referência) e remove `year`.
    `col`='data' (load_multiplos_todos) ou 'Data' (load_multiplos_historico)."""
    def _to_date(y):
        try:
            y = int(y)
            return pd.Timestamp(_dt.date(y, 12, 31)) if y > 0 else pd.NaT
        except Exception:
            return pd.NaT
    wide = wide.copy()
    wide[col] = wide["year"].map(_to_date)
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


# market.* (EN) -> colunas que as telas esperam (PT do legado)
_DEMO_SQL = """
    SELECT i.ticker AS "Ticker", i.year AS _year,
           i.revenue    AS "Receita_Liquida", i.ebit AS "EBIT",
           i.ebitda     AS "EBITDA", i.net_income AS "Lucro_Liquido", i.eps AS "LPA",
           b.equity     AS "Patrimonio_Liquido", b.net_debt AS "Divida_Liquida",
           b.gross_debt AS "Divida_Total", b.total_assets AS "Ativo_Total", b.cash AS "Caixa",
           c.operating_cash_flow AS "FCO", c.investing_cash_flow AS "FCI",
           c.free_cash_flow AS "FCF"
    FROM market.income_statements i
    LEFT JOIN market.balance_sheets b
        ON b.ticker=i.ticker AND b.period=i.period AND b.year=i.year AND b.quarter=i.quarter
    LEFT JOIN market.cash_flow_statements c
        ON c.ticker=i.ticker AND c.period=i.period AND c.year=i.year AND c.quarter=i.quarter
    WHERE i.period='annual' AND {where}
    ORDER BY i.ticker, i.year
"""


def _demo_finalize(df: pd.DataFrame) -> pd.DataFrame:
    """Adiciona Data (31/12), dividendos/ano e tipa numéricos."""
    if df.empty:
        return df
    df = df.copy()
    df["Ticker"] = _norm_ticker(df["Ticker"])
    df["Data"] = df["_year"].map(
        lambda y: pd.Timestamp(_dt.date(int(y), 12, 31)) if pd.notna(y) else pd.NaT)
    num = ["Receita_Liquida", "EBIT", "EBITDA", "Lucro_Liquido", "LPA",
           "Patrimonio_Liquido", "Divida_Liquida", "Divida_Total", "Ativo_Total",
           "Caixa", "FCO", "FCI", "FCF"]
    for c in num:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.drop(columns=["_year"])


@st.cache_data(ttl=3600, show_spinner=False)
def load_demonstracoes(ticker: str) -> pd.DataFrame:
    """Demonstrações anuais (market.*) com colunas PT iguais ao legado."""
    tk = ticker.strip().upper().replace(".SA", "")
    df = _demo_finalize(_q(_DEMO_SQL.format(where="i.ticker = :t"), {"t": tk}))
    if df.empty:
        return df
    divs = _q("SELECT EXTRACT(YEAR FROM event_date)::int AS y, SUM(amount) AS d "
              "FROM market.dividends WHERE ticker=:t AND event_date IS NOT NULL GROUP BY 1",
              {"t": tk})
    dmap = {int(r.y): float(r.d) for r in divs.itertuples()} if not divs.empty else {}
    df["Dividendos"] = df["Data"].dt.year.map(dmap)
    return df.sort_values("Data").reset_index(drop=True)


@st.cache_data(ttl=3600, show_spinner=False)
def load_demonstracoes_batch(tickers: tuple[str, ...]) -> dict[str, pd.DataFrame]:
    """Demonstrações anuais p/ vários tickers (dict ticker->DataFrame)."""
    if not tickers:
        return {}
    tks = [t.strip().upper().replace(".SA", "") for t in tickers]
    df = _demo_finalize(_q(_DEMO_SQL.format(where="i.ticker = ANY(:tks)"), {"tks": tks}))
    if df.empty:
        return {}
    out: dict[str, pd.DataFrame] = {}
    for tk in tks:
        sub = df[df["Ticker"] == tk].copy().reset_index(drop=True)
        if not sub.empty:
            out[tk] = sub.sort_values("Data").reset_index(drop=True)
    return out


def _annual_long(where_sql: str, params: dict) -> pd.DataFrame:
    return _q(f"""
        SELECT ticker AS "Ticker", year, metric_name, metric_value
        FROM market.calculated_metrics
        WHERE period = 'annual' AND {where_sql}
    """, params)


@st.cache_data(ttl=3600, show_spinner=False)
def load_multiplos_historico(ticker: str) -> pd.DataFrame:
    """Histórico anual de múltiplos (1 linha por ano), colunas iguais ao legado."""
    tk = ticker.strip().upper().replace(".SA", "")
    long_df = _annual_long("ticker = :tk", {"tk": tk})
    if long_df.empty:
        return pd.DataFrame()
    wide = _pivot_metrics(long_df)
    wide["Ticker"] = _norm_ticker(wide["Ticker"])
    out = _attach_data(wide, "Data").sort_values("Data").reset_index(drop=True)
    return out[["Ticker", "Data", *_MULT_COLS]]


@st.cache_data(ttl=3600, show_spinner=False)
def load_multiplos_historico_batch(tickers: tuple[str, ...]) -> dict[str, pd.DataFrame]:
    """Histórico anual de múltiplos p/ vários tickers (dict ticker->DataFrame)."""
    if not tickers:
        return {}
    tks = [t.strip().upper().replace(".SA", "") for t in tickers]
    long_df = _annual_long("ticker = ANY(:tks)", {"tks": tks})
    if long_df.empty:
        return {}
    wide = _pivot_metrics(long_df)
    wide["Ticker"] = _norm_ticker(wide["Ticker"])
    out = _attach_data(wide, "Data").sort_values(["Ticker", "Data"])
    result: dict[str, pd.DataFrame] = {}
    for tk in tks:
        sub = out[out["Ticker"] == tk].copy().reset_index(drop=True)
        if not sub.empty:
            result[tk] = sub[["Ticker", "Data", *_MULT_COLS]]
    return result


@st.cache_data(ttl=3600, show_spinner=False)
def load_fiis(segmento: str | None = None) -> pd.DataFrame:
    """Ranking de FIIs de market.fiis (score desc). Filtro opcional por segmento."""
    where, params = "", {}
    if segmento:
        where = "WHERE segmento = :seg"
        params["seg"] = segmento
    df = _q(f"""
        SELECT ticker AS "Ticker", name AS "Nome",
               COALESCE(segmento_cvm, segmento) AS "Segmento", tipo AS "Tipo",
               price AS "Preço", pvp AS "P/VP", dy_12m AS "DY_12m",
               liquidez_diaria AS "Liquidez_Diaria",
               patrimonio_liquido AS "Patrimonio", vpa AS "VPA",
               num_cotistas AS "Cotistas", tipo_gestao AS "Gestao",
               pct_imoveis AS "Pct_Imoveis", pct_papel AS "Pct_Papel",
               pct_caixa AS "Pct_Caixa", pct_fundos AS "Pct_Fundos",
               score AS "Score", updated_at
        FROM market.fiis {where}
        ORDER BY score DESC NULLS LAST, ticker
    """, params)
    if df.empty:
        return df
    df["Ticker"] = _norm_ticker(df["Ticker"])
    for c in ("Preço", "P/VP", "DY_12m", "Liquidez_Diaria", "Patrimonio", "VPA",
              "Cotistas", "Pct_Imoveis", "Pct_Papel", "Pct_Caixa", "Pct_Fundos", "Score"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


@st.cache_data(ttl=3600, show_spinner=False)
def load_fii_series(tickers: tuple[str, ...]) -> dict:
    """Séries históricas dos FIIs p/ backtest: {'precos': {tk:[(date,close)]},
    'dividendos': {tk:[(date,amount)]}}."""
    if not tickers:
        return {"precos": {}, "dividendos": {}}
    tks = [t.strip().upper().replace(".SA", "") for t in tickers]
    px = _q("SELECT ticker, date, COALESCE(adjusted_close, close) AS c "
            "FROM market.historical_prices WHERE ticker = ANY(:t) "
            "AND COALESCE(adjusted_close, close) IS NOT NULL ORDER BY ticker, date",
            {"t": tks})
    dv = _q("SELECT ticker, event_date, amount FROM market.dividends "
            "WHERE ticker = ANY(:t) AND event_date IS NOT NULL ORDER BY ticker, event_date",
            {"t": tks})
    ph = {tk: list(zip(g["date"], g["c"])) for tk, g in px.groupby("ticker")} if not px.empty else {}
    dh = {tk: list(zip(g["event_date"], g["amount"])) for tk, g in dv.groupby("ticker")} if not dv.empty else {}
    return {"precos": ph, "dividendos": dh}


@st.cache_data(ttl=3600, show_spinner=False)
def load_fii_segmentos() -> list[str]:
    """Segmentos distintos disponíveis em market.fiis (para filtros na tela)."""
    df = _q("SELECT DISTINCT COALESCE(segmento_cvm, segmento) AS seg FROM market.fiis "
            "WHERE COALESCE(segmento_cvm, segmento) IS NOT NULL ORDER BY 1")
    return [str(s) for s in df["seg"].tolist()] if not df.empty else []


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
