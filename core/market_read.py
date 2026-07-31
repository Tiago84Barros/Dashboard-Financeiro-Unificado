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

from core.data_quality import clean_multiples_frame

# Métricas do snapshot, na MESMA grafia das colunas do legado public.multiplos.
# Liquidez_Corrente ainda não é computável a partir de market.* (gap conhecido):
# entra como coluna vazia para preservar o shape de saída.
_MULT_COLS = ["P/L", "P/VP", "DY", "ROE", "ROA", "ROIC",
              "Margem_Liquida", "Margem_Operacional", "Endividamento_Total",
              "Liquidez_Corrente", "EV_EBIT", "P_FCO", "Payout",
              # SINAIS (0/1), não indicadores: registram balanço estruturalmente
              # rompido, que a faixa coerente descartava como se fosse ausência.
              # O piso de qualidade precisa vê-los para REPROVAR; ranking e
              # score os ignoram (não estão em CANONICAL_MULTIPLOS_FIELDS).
              "Patrimonio_Negativo", "Endividamento_Fora_De_Faixa", "FCO_Negativo"]


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
    return _suprime_razoes_sobre_patrimonio_negativo(
        wide[["Ticker", "year", *_MULT_COLS]])


# Razões cujo DENOMINADOR é o patrimônio líquido. Com PL negativo o quociente
# inverte de sinal e desastre vira destaque.
_RAZOES_SOBRE_PL = ("ROE", "P/VP", "Endividamento_Total")


def _suprime_razoes_sobre_patrimonio_negativo(wide: pd.DataFrame) -> pd.DataFrame:
    """Anula ROE/P-VP/Endividamento quando o patrimônio líquido é negativo.

    Por que na LEITURA e não só no cálculo: ``calculated_metrics`` tem mais de
    uma fonte para a mesma métrica. O ETL calcula (``market.compute``) e a brapi
    entrega a dela pronta (``brapi_trailing``). Guardar apenas o cálculo deixava
    passar a versão da brapi — medido em 30/07/2026, MWET4 exibia ROE de +4,23
    com patrimônio negativo e prejuízo de R$ 28,6 mi, e o método gravado era
    ``brapi_trailing``. Das 45 empresas com patrimônio negativo, 32 mostravam
    ROE POSITIVO, até +423%; a faixa canônica de ROE (-3, 5) aceita o número,
    porque os dois sinais se cancelam antes de ela olhar.

    Suprimir aqui cobre todo consumidor do frame, seja qual for a fonte que
    gravou. O veredito sobre a empresa não se perde: fica no sinal
    ``Patrimonio_Negativo``, que é justamente a coluna consultada para saber
    disso.
    """
    if wide.empty or "Patrimonio_Negativo" not in wide.columns:
        return wide
    insolvente = pd.to_numeric(wide["Patrimonio_Negativo"],
                               errors="coerce").fillna(0) == 1
    if not bool(insolvente.any()):
        return wide
    wide = wide.copy()
    for coluna in _RAZOES_SOBRE_PL:
        if coluna in wide.columns:
            wide.loc[insolvente, coluna] = float("nan")
    return wide


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
          AND a.asset_type IN ('stock', 'unit')
          AND a.is_active IS TRUE
          AND a.company_id IS NOT NULL
          AND s."SETOR" IS NOT NULL
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
        JOIN market.assets a ON a.ticker=cm.ticker
          AND a.asset_type IN ('stock','unit')
          AND a.is_active IS TRUE
          AND a.company_id IS NOT NULL
        LEFT JOIN ly ON ly.ticker = cm.ticker
        WHERE cm.period = 'ttm'
          AND (cm.confidence_score IS NULL OR cm.confidence_score >= 80)
          {where_tk}
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


def _annual_upto_long(ano_ref_max: int) -> pd.DataFrame:
    """Métricas do último exercício ANUAL fechado ≤ ano_ref_max, por ticker.

    Fix auditoria 2026-07 (ponto-no-tempo): antes, o snapshot TTM calculado
    HOJE era rotulado com o ano do último balanço anual e passava pelo filtro
    de ano-base — métricas de 2026 apareciam como dados de 2025, contrariando
    a promessa "dados parciais do ano corrente são ignorados". Com
    ano_ref_max, agora servimos as métricas ANUAIS do exercício-base real.
    O JOIN com income_statements exige DRE anual publicada: ticker sem
    demonstração não tem exercício-base e sai do ranking (em vez de entrar
    com dados de hoje disfarçados de ano anterior).
    """
    return _q("""
        WITH stmt AS (
            SELECT ticker, MAX(year) AS ymax
            FROM market.income_statements
            WHERE period = 'annual'
            GROUP BY ticker
        ),
        ly AS (
            SELECT cm.ticker, MAX(cm.year) AS y
            FROM market.calculated_metrics cm
            JOIN market.assets a ON a.ticker=cm.ticker
              AND a.asset_type IN ('stock','unit')
              AND a.is_active IS TRUE
              AND a.company_id IS NOT NULL
            JOIN stmt s ON s.ticker = cm.ticker
            WHERE cm.period = 'annual' AND cm.year > 0
              AND (cm.confidence_score IS NULL OR cm.confidence_score >= 80)
              AND cm.year <= LEAST(CAST(:amax AS int), s.ymax)
            GROUP BY cm.ticker
        )
        SELECT cm.ticker AS "Ticker", cm.year AS year,
               cm.metric_name, cm.metric_value
        FROM market.calculated_metrics cm
        JOIN market.assets a ON a.ticker=cm.ticker
          AND a.asset_type IN ('stock','unit')
          AND a.is_active IS TRUE
          AND a.company_id IS NOT NULL
        JOIN ly ON ly.ticker = cm.ticker AND ly.y = cm.year
        WHERE cm.period = 'annual'
          AND (cm.confidence_score IS NULL OR cm.confidence_score >= 80)
    """, {"amax": int(ano_ref_max)})


@st.cache_data(ttl=3600, show_spinner=False)
def load_multiplos_todos(ano_ref_max: int | None = None) -> pd.DataFrame:
    """Múltiplos de TODOS os tickers — ranking/avançada.

    ano_ref_max=None  → snapshot TTM atual (visões "hoje", ex.: carteira).
    ano_ref_max=YYYY  → métricas do último exercício ANUAL ≤ YYYY
                        (ponto-no-tempo; fix auditoria 2026-07).
    """
    long_df = (_annual_upto_long(int(ano_ref_max))
               if ano_ref_max is not None else _multiplos_long())
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
    # Dividendos por ano (paridade com load_demonstracoes single) — os
    # relatórios LLM usam a série p/ ancorar a política de proventos em números.
    divs = _q("SELECT ticker, EXTRACT(YEAR FROM event_date)::int AS y, SUM(amount) AS d "
              "FROM market.dividends WHERE ticker = ANY(:tks) AND event_date IS NOT NULL "
              "GROUP BY 1, 2", {"tks": tks})
    dmap: dict[tuple[str, int], float] = (
        {(str(r.ticker).upper(), int(r.y)): float(r.d) for r in divs.itertuples()}
        if not divs.empty else {}
    )
    out: dict[str, pd.DataFrame] = {}
    for tk in tks:
        sub = df[df["Ticker"] == tk].copy().reset_index(drop=True)
        if not sub.empty:
            sub["Dividendos"] = sub["Data"].dt.year.map(
                lambda y, t=tk: dmap.get((t, int(y))) if pd.notna(y) else None
            )
            out[tk] = sub.sort_values("Data").reset_index(drop=True)
    return out


def _annual_long(where_sql: str, params: dict) -> pd.DataFrame:
    # Fix auditoria 2026-07 (ponto-no-tempo): linhas 'annual' criadas apenas
    # por dividendos do ano corrente (sem DRE anual publicada) continham DY
    # parcial calculado com o preço de HOJE e viravam a última linha do
    # histórico usado pelo backtest. Limita cada ticker ao último ano COM
    # demonstração anual publicada.
    has_vintages = _q("""
        SELECT to_regclass('market.calculated_metric_vintages') IS NOT NULL AS ok
    """)
    if not has_vintages.empty and bool(has_vintages.iloc[0]["ok"]):
        # Ponto-no-tempo: PREFERE vintages reais (não-baseline) quando existirem;
        # cai no 'migration_baseline' (backfill limpo em decimal) só quando é a
        # ÚNICA fonte — hoje 100% das vintages são baseline. Excluí-las deixava o
        # histórico vazio e o app voltava ao public.multiplos legado (unidades
        # misturadas/corrompidas). Para o baseline, o available_at é o carimbo da
        # INGESTÃO (semana passada), não a disponibilidade histórica real; por
        # isso é anulado (→ o scorer usa o corte fiscal, idêntico ao legado, que
        # nunca teve AvailableAt) — preservando a integridade do walk-forward.
        return _q(f"""
            WITH first_vintage AS (
                SELECT DISTINCT ON (ticker, year, quarter, metric_name)
                       ticker, year, quarter, metric_name, metric_value,
                       CASE WHEN availability_quality = 'migration_baseline'
                            THEN NULL ELSE available_at END AS available_at,
                       availability_quality
                FROM market.calculated_metric_vintages
                WHERE period = 'annual'
                  AND (confidence_score IS NULL OR confidence_score >= 80)
                ORDER BY ticker, year, quarter, metric_name,
                         (availability_quality = 'migration_baseline') ASC,
                         recorded_at ASC
            ),
            stmt AS (
                SELECT ticker, MAX(year) AS ymax
                FROM market.income_statements
                WHERE period = 'annual'
                GROUP BY ticker
            )
            SELECT cm.ticker AS "Ticker", cm.year AS year,
                   cm.metric_name, cm.metric_value,
                   cm.available_at, cm.availability_quality
            FROM first_vintage cm
            JOIN market.assets a ON a.ticker=cm.ticker
              AND a.asset_type IN ('stock','unit')
              AND a.company_id IS NOT NULL
            JOIN stmt s ON s.ticker = cm.ticker
            WHERE cm.year <= s.ymax AND {where_sql}
        """, params)
    return _q(f"""
        WITH stmt AS (
            SELECT ticker, MAX(year) AS ymax
            FROM market.income_statements
            WHERE period = 'annual'
            GROUP BY ticker
        )
        SELECT cm.ticker AS "Ticker", cm.year AS year,
               cm.metric_name, cm.metric_value
        FROM market.calculated_metrics cm
        JOIN market.assets a ON a.ticker=cm.ticker
          AND a.asset_type IN ('stock','unit')
          AND a.company_id IS NOT NULL
        JOIN stmt s ON s.ticker = cm.ticker
        WHERE cm.period = 'annual'
          AND cm.year <= s.ymax
          AND (cm.confidence_score IS NULL OR cm.confidence_score >= 80)
          AND {where_sql}
    """, params)


@st.cache_data(ttl=3600, show_spinner=False)
def load_multiplos_historico(ticker: str) -> pd.DataFrame:
    """Histórico anual de múltiplos (1 linha por ano), colunas iguais ao legado."""
    tk = ticker.strip().upper().replace(".SA", "")
    long_df = _annual_long("cm.ticker = :tk", {"tk": tk})
    if long_df.empty:
        return pd.DataFrame()
    wide = _pivot_metrics(long_df)
    if "available_at" in long_df:
        availability = (
            long_df.groupby(["Ticker", "year"], as_index=False)["available_at"].max()
        )
        wide = wide.merge(availability, on=["Ticker", "year"], how="left")
    wide["Ticker"] = _norm_ticker(wide["Ticker"])
    out = _attach_data(wide, "Data").sort_values("Data").reset_index(drop=True)
    out = clean_multiples_frame(out)  # belt: faixa-fora/outlier → NaN
    cols = ["Ticker", "Data", *_MULT_COLS]
    # Mantém AvailableAt só se houver disponibilidade real (não-baseline); com
    # baseline puro (available_at anulado) a coluna some → paridade com o legado.
    if "available_at" in out and out["available_at"].notna().any():
        out = out.rename(columns={"available_at": "AvailableAt"})
        cols.append("AvailableAt")
    return out[cols]


@st.cache_data(ttl=3600, show_spinner=False)
def load_multiplos_historico_batch(tickers: tuple[str, ...]) -> dict[str, pd.DataFrame]:
    """Histórico anual de múltiplos p/ vários tickers (dict ticker->DataFrame)."""
    if not tickers:
        return {}
    tks = [t.strip().upper().replace(".SA", "") for t in tickers]
    long_df = _annual_long("cm.ticker = ANY(:tks)", {"tks": tks})
    if long_df.empty:
        return {}
    wide = _pivot_metrics(long_df)
    if "available_at" in long_df:
        availability = (
            long_df.groupby(["Ticker", "year"], as_index=False)["available_at"].max()
        )
        wide = wide.merge(availability, on=["Ticker", "year"], how="left")
    wide["Ticker"] = _norm_ticker(wide["Ticker"])
    out = _attach_data(wide, "Data").sort_values(["Ticker", "Data"])
    out = clean_multiples_frame(out)  # belt: faixa-fora/outlier → NaN
    keep_av = "available_at" in out and out["available_at"].notna().any()
    if keep_av:
        out = out.rename(columns={"available_at": "AvailableAt"})
    result: dict[str, pd.DataFrame] = {}
    for tk in tks:
        sub = out[out["Ticker"] == tk].copy().reset_index(drop=True)
        if not sub.empty:
            cols = ["Ticker", "Data", *_MULT_COLS]
            if keep_av and "AvailableAt" in sub:
                cols.append("AvailableAt")
            result[tk] = sub[cols]
    return result


@st.cache_data(ttl=3600, show_spinner=False)
def load_fiis(segmento: str | None = None) -> pd.DataFrame:
    """Ranking de FIIs de market.fiis (score desc). Filtro opcional por segmento."""
    conditions = ["u.active_status IN ('listed','active')",
                  "f.ticker ~ '^[A-Z]{4}11$'", "f.price > 0"]
    params = {}
    if segmento:
        conditions.append("COALESCE(f.segmento_cvm, f.segmento) = :seg")
        params["seg"] = segmento
    where = "WHERE " + " AND ".join(conditions)
    df = _q(f"""
        WITH current_universe AS (
            SELECT DISTINCT ON (ticker) ticker, active_status
            FROM market.fii_universe_history
            WHERE knowledge_at <= now()
            ORDER BY ticker, knowledge_at DESC, reference_date DESC
        )
        SELECT f.ticker AS "Ticker", f.name AS "Nome",
               COALESCE(f.segmento_cvm, f.segmento) AS "Segmento", f.tipo AS "Tipo",
               f.price AS "Preço", f.pvp AS "P/VP", f.dy_12m AS "DY_12m",
               f.liquidez_diaria AS "Liquidez_Diaria",
               f.patrimonio_liquido AS "Patrimonio", f.vpa AS "VPA",
               f.num_cotistas AS "Cotistas", f.tipo_gestao AS "Gestao",
               f.pct_imoveis AS "Pct_Imoveis", f.pct_papel AS "Pct_Papel",
               f.pct_caixa AS "Pct_Caixa", f.pct_fundos AS "Pct_Fundos",
               f.score AS "Score", f.updated_at,
               f.cvm_ref_date, f.vacancia_ref_date
        FROM market.fiis f JOIN current_universe u USING (ticker) {where}
        ORDER BY f.score DESC NULLS LAST, f.ticker
    """, params)
    if df.empty:
        return df
    df["Ticker"] = _norm_ticker(df["Ticker"])
    for c in ("Preço", "P/VP", "DY_12m", "Liquidez_Diaria", "Patrimonio", "VPA",
              "Cotistas", "Pct_Imoveis", "Pct_Papel", "Pct_Caixa", "Pct_Fundos", "Score"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


@st.cache_data(ttl=900, show_spinner=False)
def load_fii_methodology_inputs(prefer_snapshot: bool = True) -> pd.DataFrame:
    """Inputs PIT; o publicador pode forçar reconstrução a partir das tabelas-base.

    ``prefer_snapshot=False`` evita que a rotina de publicação leia e republique
    indefinidamente a própria vitrine antiga em vez do warehouse atualizado.
    """
    snapshot = _q("""
        SELECT ticker, payload_json, as_of_date, available_at, knowledge_at,
               reference_date, vintage, source, quality_status,
               schema_version, generated_at, payload_sha256, coverage_json
        FROM market.fii_selection_inputs
        WHERE quality_status IN ('published', 'accepted')
        ORDER BY generated_at DESC, ticker
    """)
    if prefer_snapshot and not snapshot.empty:
        records: list[dict] = []
        seen: set[str] = set()
        for item in snapshot.to_dict("records"):
            ticker = str(item.get("ticker") or "").replace(".SA", "").strip().upper()
            if not ticker or ticker in seen:
                continue
            payload = item.get("payload_json")
            if isinstance(payload, str):
                try:
                    import json
                    payload = json.loads(payload)
                except (TypeError, ValueError):
                    payload = None
            if not isinstance(payload, dict):
                continue
            record = dict(payload)
            record["ticker"] = ticker
            record["snapshot_metadata"] = {
                "as_of_date": item.get("as_of_date"),
                "available_at": item.get("available_at"),
                "knowledge_at": item.get("knowledge_at"),
                "reference_date": item.get("reference_date"),
                "vintage": item.get("vintage"),
                "source": item.get("source"),
                "quality_status": item.get("quality_status"),
                "schema_version": item.get("schema_version"),
                "generated_at": item.get("generated_at"),
                "payload_sha256": item.get("payload_sha256"),
                "coverage": item.get("coverage_json") or {},
            }
            records.append(record)
            seen.add(ticker)
        if records:
            return pd.DataFrame(records)

    base = load_fiis().copy()
    if base.empty:
        return base
    if "VPA" in base:
        from data_pipeline.market import fii as _fz
        base["P/VP"] = [_fz.pvp_efetivo(price, vpa, fallback)
                        for price, vpa, fallback in zip(base["Preço"], base["VPA"], base["P/VP"])]
    quality = load_fii_quality()
    if not quality.empty and "Hist_Meses" in quality:
        enrichment_columns = [
            column for column in (
                "Ticker", "Hist_Meses", "Num_Imoveis", "Vacancia", "N_Regioes",
                "N_UFs", "Property_Diversification", "CAGR", "Max_Drawdown",
                "Multi_Setorial", "Series_Source", "Series_AvailableAt",
            ) if column in quality.columns
        ]
        base = base.merge(quality[enrichment_columns], on="Ticker", how="left")
    observations = _q("""
        SELECT DISTINCT ON (ticker, metric_name)
               ticker, metric_name, value_numeric, value_text, value_json,
               reference_date, available_at, knowledge_at, availability_quality,
               vintage, source, quality_status
        FROM market.fii_metric_observations
        WHERE knowledge_at <= now()
          AND quality_status IN ('observed','accepted')
        ORDER BY ticker, metric_name, knowledge_at DESC, reference_date DESC, observed_at DESC
    """)
    exposures = _q("""
        WITH latest_ref AS (
            SELECT ticker, exposure_type, max(reference_date) AS reference_date
            FROM market.fii_exposures WHERE knowledge_at <= now() GROUP BY 1,2
        ), latest_at AS (
            SELECT e.ticker, e.exposure_type, e.reference_date, max(e.available_at) AS available_at
            FROM market.fii_exposures e JOIN latest_ref r USING (ticker, exposure_type, reference_date)
            GROUP BY 1,2,3
        )
        SELECT e.ticker, e.exposure_type, e.exposure_name, e.exposure_weight,
               e.reference_date, e.available_at, e.vintage, e.source
        FROM market.fii_exposures e JOIN latest_at l
          USING (ticker, exposure_type, reference_date, available_at)
    """)
    calibrations = _q("""
        SELECT metric_name,
               sum(lower_bound * GREATEST(reviewed_count,1)) /
                   NULLIF(sum(GREATEST(reviewed_count,1)),0) AS lower_bound,
               sum(reviewed_count) AS reviewed_count
        FROM market.fii_parser_calibrations
        GROUP BY metric_name
    """)
    calibration_map = ({str(row.metric_name): float(row.lower_bound)
                        for row in calibrations.itertuples()}
                       if not calibrations.empty else {})
    rows: list[dict] = []
    metric_aliases = {
        # Nomes legados/documentais normalizados para o vocabulário da
        # metodologia. O nome canônico sempre prevalece quando ambos existem.
        "cap_rate_implicito": "implied_cap_rate",
        "wault_years": "wault_anos",
    }
    for _, item in base.iterrows():
        ticker = str(item["Ticker"])
        row = {
            "ticker": ticker, "name": item.get("Nome"), "tipo": item.get("Tipo"),
            "sector": item.get("Segmento"), "dy_12m": item.get("DY_12m"),
            "pvp": item.get("P/VP"), "liquidez_diaria": item.get("Liquidez_Diaria"),
            "price": item.get("Preço"), "vpa": item.get("VPA"),
            "patrimonio_liquido": item.get("Patrimonio"),
            "num_cotistas": item.get("Cotistas"), "tipo_gestao": item.get("Gestao"),
            "pct_imoveis": item.get("Pct_Imoveis"), "pct_papel": item.get("Pct_Papel"),
            "pct_caixa": item.get("Pct_Caixa"), "pct_fundos": item.get("Pct_Fundos"),
            "history_months": item.get("Hist_Meses"), "updated_at": item.get("updated_at"),
            "cvm_ref_date": item.get("cvm_ref_date"),
            "vacancia_ref_date": item.get("vacancia_ref_date"),
            "total_return_trend": item.get("CAGR"),
            "max_drawdown": item.get("Max_Drawdown"),
            "multi_category": item.get("Multi_Setorial"),
            "vacancia_fisica": item.get("Vacancia"),
            "property_count": item.get("Num_Imoveis"),
            "property_diversification": item.get("Property_Diversification"),
            "region_count": item.get("N_Regioes"),
            "metric_metadata": {
                "dy_12m": {"available_at": str(item.get("updated_at")), "source": "brapi"},
                "liquidez_diaria": {"available_at": str(item.get("updated_at")), "source": "brapi"},
                "pvp": {"available_at": str(item.get("updated_at")), "source": "cvm_vpa+brapi_quote"},
                "total_return_trend": {
                    "available_at": str(item.get("Series_AvailableAt") or item.get("updated_at")),
                    "source": str(item.get("Series_Source") or "brapi_adjusted_close"),
                },
                "max_drawdown": {
                    "available_at": str(item.get("Series_AvailableAt") or item.get("updated_at")),
                    "source": str(item.get("Series_Source") or "brapi_adjusted_close"),
                },
            },
        }
        if not observations.empty:
            for obs in observations[observations["ticker"] == ticker].to_dict("records"):
                value = obs.get("value_numeric")
                if pd.isna(value):
                    value = obs.get("value_text") if pd.notna(obs.get("value_text")) else obs.get("value_json")
                raw_metric = str(obs["metric_name"])
                metric = metric_aliases.get(raw_metric, raw_metric)
                if raw_metric != metric and metric in row:
                    continue
                row[metric] = value
                row["metric_metadata"][metric] = {
                    "reference_date": str(obs.get("reference_date")),
                    "available_at": str(obs.get("available_at")),
                    "knowledge_at": str(obs.get("knowledge_at")),
                    "availability_quality": obs.get("availability_quality"),
                    "vintage": obs.get("vintage"),
                    "source": obs.get("source"),
                    "source_quality": {
                        "verified_publication": .95,
                        "first_observed_proxy": .80,
                        "retrospective_backfill": .55,
                        "migration_baseline": .20,
                    }.get(str(obs.get("availability_quality") or ""), .50),
                    "raw_metric_name": raw_metric,
                }
        if not exposures.empty:
            exp = exposures[exposures["ticker"] == ticker]
            for kind, group in exp.groupby("exposure_type"):
                mapping = {str(r.exposure_name): float(r.exposure_weight) for r in group.itertuples()}
                key = {"tenant": "tenants", "debtor": "debtors", "issuer": "issuers",
                       "indexer": "indexers", "region": "regions",
                       "holding": "holdings"}.get(str(kind))
                if key:
                    row[key] = mapping
                elif kind in ("manager", "sector") and mapping:
                    row[str(kind)] = max(mapping, key=mapping.get)
        observed_calibrations = [calibration_map[key] for key in row
                                 if key in calibration_map]
        row["parser_calibration"] = (sum(observed_calibrations) / len(observed_calibrations)
                                     if observed_calibrations else 1.0)
        rows.append(row)
    return pd.DataFrame(rows)


@st.cache_data(ttl=900, show_spinner=False)
def load_fii_validation_status(methodology_version: str = "6.0.0") -> dict:
    df = _q("""
        SELECT status, metrics_json, blockers_json, as_of_date, finished_at
        FROM market.fii_validation_runs
        WHERE methodology_version = :version
        ORDER BY COALESCE(finished_at, started_at) DESC LIMIT 1
    """, {"version": methodology_version})
    if df.empty:
        return {"status": "unvalidated", "blockers": ["nenhuma validação PIT persistida"]}
    row = df.iloc[0].to_dict()
    return {"status": row.get("status"), "metrics": row.get("metrics_json") or {},
            "blockers": row.get("blockers_json") or [], "as_of_date": row.get("as_of_date")}


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
def load_fii_one(ticker: str) -> pd.Series:
    """Linha única de market.fiis (detalhe do FII), incluindo vacância e nº de imóveis."""
    tk = ticker.strip().upper().replace(".SA", "")
    df = _q("""
        SELECT ticker AS "Ticker", name AS "Nome",
               COALESCE(segmento_cvm, segmento) AS "Segmento", tipo AS "Tipo",
               price AS "Preço", pvp AS "P/VP", dy_12m AS "DY_12m",
               liquidez_diaria AS "Liquidez_Diaria",
               patrimonio_liquido AS "Patrimonio", vpa AS "VPA",
               num_cotistas AS "Cotistas", tipo_gestao AS "Gestao",
               pct_imoveis AS "Pct_Imoveis", pct_papel AS "Pct_Papel",
               pct_caixa AS "Pct_Caixa", pct_fundos AS "Pct_Fundos",
               vacancia AS "Vacancia", vacancia_ref_date AS "Vacancia_Ref",
               num_imoveis AS "Num_Imoveis", score AS "Score", updated_at
        FROM market.fiis WHERE ticker = :tk
    """, {"tk": tk})
    if df.empty:
        return pd.Series(dtype=object)
    df["Ticker"] = _norm_ticker(df["Ticker"])
    for c in ("Preço", "P/VP", "DY_12m", "Liquidez_Diaria", "Patrimonio", "VPA",
              "Cotistas", "Pct_Imoveis", "Pct_Papel", "Pct_Caixa", "Pct_Fundos",
              "Vacancia", "Num_Imoveis", "Score"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.iloc[0]


@st.cache_data(ttl=3600, show_spinner=False)
def load_fii_metrics_mensal(ticker: str) -> pd.DataFrame:
    """
    Série MENSAL de fundamentos do FII (market.fii_metrics_monthly) + P/VP histórico
    = preço de fechamento BRUTO de fim de mês (market.historical_prices.close) ÷ VPA.
    Colunas: Data, VPA, P/VP, Patrimonio, Cotistas, DY_Patrimonial, Pct_*.
    """
    tk = ticker.strip().upper().replace(".SA", "")
    met = _q("""
        SELECT ref_month AS "Data", vpa AS "VPA",
               patrimonio_liquido AS "Patrimonio", num_cotistas AS "Cotistas",
               dy_patrimonial_mes AS "DY_Patrimonial",
               pct_imoveis AS "Pct_Imoveis", pct_papel AS "Pct_Papel",
               pct_caixa AS "Pct_Caixa", pct_fundos AS "Pct_Fundos"
        FROM market.fii_metrics_monthly WHERE ticker = :tk ORDER BY ref_month
    """, {"tk": tk})
    if met.empty:
        return pd.DataFrame()
    met["Data"] = pd.to_datetime(met["Data"], errors="coerce")
    for c in ("VPA", "Patrimonio", "Cotistas", "DY_Patrimonial",
              "Pct_Imoveis", "Pct_Papel", "Pct_Caixa", "Pct_Fundos"):
        met[c] = pd.to_numeric(met[c], errors="coerce")
    # preço bruto (NÃO ajustado) de fim de mês → P/VP histórico = preço ÷ VPA
    px = _q("SELECT date, close FROM market.historical_prices "
            "WHERE ticker = :tk AND close IS NOT NULL ORDER BY date", {"tk": tk})
    met["P/VP"] = pd.NA
    if not px.empty:
        px["date"] = pd.to_datetime(px["date"], errors="coerce")
        px = px.dropna(subset=["date"])
        px_m = (pd.to_numeric(px.set_index("date")["close"], errors="coerce")
                .resample("ME").last())
        # casa cada ref_month (1º dia) ao fechamento bruto do mesmo mês
        close_by_month = {ts.to_period("M"): v for ts, v in px_m.items()}
        met["_close"] = met["Data"].dt.to_period("M").map(close_by_month)
        met["P/VP"] = (met["_close"] / met["VPA"]).where(met["VPA"] > 0)
        met = met.drop(columns=["_close"])
    return met.dropna(subset=["Data"]).reset_index(drop=True)


@st.cache_data(ttl=3600, show_spinner=False)
def load_fii_imoveis(ticker: str) -> pd.DataFrame:
    """Imóveis do FII (market.fii_imoveis): nome, área, cidade/UF, região, vacância."""
    tk = ticker.strip().upper().replace(".SA", "")
    df = _q("""
        SELECT nome_imovel AS "Imóvel", area_m2 AS "Área_m2", vacancia AS "Vacância",
               cidade AS "Cidade", uf AS "UF", regiao AS "Região",
               segmento_imovel AS "Segmento", pct_receita AS "Pct_Receita", fonte AS "Fonte"
        FROM market.fii_imoveis WHERE ticker = :tk
        ORDER BY regiao NULLS LAST, area_m2 DESC NULLS LAST
    """, {"tk": tk})
    if df.empty:
        return df
    for c in ("Área_m2", "Vacância", "Pct_Receita"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


@st.cache_data(ttl=3600, show_spinner=False)
def load_fii_quality() -> pd.DataFrame:
    """
    Sinais de qualidade por FII para a carteira-modelo por critérios:
      DY_12m, P/VP, Liquidez_Diaria, Tipo, Segmento, Num_Imoveis,
      N_Regioes (imóveis em regiões distintas), N_UFs, Multi_Setorial,
      CAGR e Max_Drawdown (da série de retorno total).
    """
    from data_pipeline.market import fii as _fz
    base = _q("""
        SELECT ticker AS "Ticker", name AS "Nome", tipo AS "Tipo",
               COALESCE(segmento_cvm, segmento) AS "Segmento",
               dy_12m AS "DY_12m", pvp AS "P/VP", liquidez_diaria AS "Liquidez_Diaria",
               num_imoveis AS "Num_Imoveis", vacancia AS "Vacancia", score AS "Score"
        FROM market.fiis
    """)
    if base.empty:
        return pd.DataFrame()
    base["Ticker"] = _norm_ticker(base["Ticker"])
    for c in ("DY_12m", "P/VP", "Liquidez_Diaria", "Num_Imoveis", "Vacancia", "Score"):
        base[c] = pd.to_numeric(base[c], errors="coerce")
    # multi-setorial: fundo diversificado por classificação (Multicategoria/híbrido)
    seg = base["Segmento"].fillna("").str.lower()
    base["Multi_Setorial"] = seg.str.contains("multi") | (base["Tipo"] == "hibrido")

    # Regiões/UFs distintas e diversificação econômica dos imóveis. O índice
    # 1-HHI só é calculado com ao menos 60% da receita identificada.
    properties = _q("SELECT ticker, regiao, uf, pct_receita FROM market.fii_imoveis")
    if not properties.empty:
        properties["ticker"] = _norm_ticker(properties["ticker"])
        properties["pct_receita"] = pd.to_numeric(properties["pct_receita"], errors="coerce")
        region_map: dict[str, tuple[int, int]] = {}
        property_diversification: dict[str, float] = {}
        for ticker, group in properties.groupby("ticker"):
            region_map[str(ticker)] = (
                int(group["regiao"].dropna().nunique()),
                int(group["uf"].dropna().nunique()),
            )
            weights = group.loc[group["pct_receita"].gt(0), "pct_receita"].astype(float)
            coverage = float(weights.sum())
            if coverage >= .60:
                normalized = weights / coverage
                property_diversification[str(ticker)] = float(1 - (normalized ** 2).sum())
        base["N_Regioes"] = base["Ticker"].map(lambda t: region_map.get(t, (0, 0))[0])
        base["N_UFs"] = base["Ticker"].map(lambda t: region_map.get(t, (0, 0))[1])
        base["Property_Diversification"] = base["Ticker"].map(property_diversification)
    else:
        base["N_Regioes"] = 0
        base["N_UFs"] = 0
        base["Property_Diversification"] = None

    # CAGR + drawdown da série de retorno total (adjusted_close mensal)
    px = _q("SELECT ticker, date, COALESCE(adjusted_close, close) AS c "
            "FROM market.historical_prices "
            "WHERE ticker IN (SELECT ticker FROM market.fiis) "
            "AND COALESCE(adjusted_close, close) IS NOT NULL ORDER BY ticker, date")
    cagr_map, dd_map, mes_map = {}, {}, {}
    series_source: dict[str, str] = {}
    series_available: dict[str, object] = {}
    if not px.empty:
        px["ticker"] = _norm_ticker(px["ticker"])
        for tk, g in px.groupby("ticker"):
            met = _fz.price_metrics(list(zip(g["date"], g["c"])))
            cagr_map[tk] = met["cagr"]
            dd_map[tk] = met["max_drawdown"]
            mes_map[tk] = met.get("meses")
            series_source[tk] = "brapi_adjusted_close"
            series_available[tk] = g["date"].max()

    # O plano Pro nem sempre devolve série ajustada para todo o universo. Para
    # os tickers ausentes, reconstrói retorno total com fechamento oficial B3 +
    # proventos públicos, sem transformar preço bruto em retorno total.
    missing_tickers = sorted(set(base["Ticker"].dropna().astype(str)) - set(cagr_map))
    if missing_tickers:
        b3 = _q("""
            SELECT ticker,trade_date AS date,close
            FROM market.fii_b3_security_history
            WHERE ticker=ANY(:tickers) AND close>0
            ORDER BY ticker,trade_date
        """, {"tickers": missing_tickers})
        dividends = _q("""
            SELECT ticker,event_date,amount FROM market.dividends
            WHERE ticker=ANY(:tickers) AND event_date IS NOT NULL AND amount IS NOT NULL
            ORDER BY ticker,event_date
        """, {"tickers": missing_tickers})
        dividend_map = ({str(tk): list(zip(group["event_date"], group["amount"]))
                         for tk, group in dividends.groupby("ticker")}
                        if not dividends.empty else {})
        if not b3.empty:
            b3["ticker"] = _norm_ticker(b3["ticker"])
            for tk, group in b3.groupby("ticker"):
                price_history = list(zip(group["date"], group["close"]))
                series, _ = _fz.backtest(
                    {str(tk): 1.0}, {str(tk): price_history},
                    {str(tk): dividend_map.get(str(tk), [])},
                )
                if series.empty or "Carteira" not in series:
                    continue
                met = _fz.price_metrics(list(zip(series["Data"], series["Carteira"])))
                cagr_map[str(tk)] = met["cagr"]
                dd_map[str(tk)] = met["max_drawdown"]
                mes_map[str(tk)] = met.get("meses")
                series_source[str(tk)] = "b3_close+cvm_brapi_dividends_total_return_v1"
                series_available[str(tk)] = group["date"].max()
    base["CAGR"] = base["Ticker"].map(cagr_map)
    base["Max_Drawdown"] = base["Ticker"].map(dd_map)
    base["Hist_Meses"] = base["Ticker"].map(mes_map)
    base["Series_Source"] = base["Ticker"].map(series_source)
    base["Series_AvailableAt"] = base["Ticker"].map(series_available)
    return base


@st.cache_data(ttl=3600, show_spinner=False)
def load_mercado_retorno_mensal() -> pd.DataFrame:
    """
    Retornos MENSAIS (retorno total, adjusted_close) de referência de mercado:
      IFIX     — proxy pelo ETF XFIX11;
      Universo — mediana mensal dos retornos de TODOS os FIIs (mercado equal-weight).
    DataFrame indexado por data (fim de mês) com colunas 'IFIX' e 'Universo'.
    """
    px = _q("SELECT ticker, date, COALESCE(adjusted_close, close) AS c "
            "FROM market.historical_prices "
            "WHERE (ticker IN (SELECT ticker FROM market.fiis) OR ticker = 'XFIX11') "
            "AND COALESCE(adjusted_close, close) IS NOT NULL ORDER BY ticker, date")
    if px.empty:
        return pd.DataFrame()
    px["date"] = pd.to_datetime(px["date"], errors="coerce")
    px = px.dropna(subset=["date"])
    wide = px.pivot_table(index="date", columns="ticker", values="c",
                          aggfunc="last").resample("ME").last()
    rets = wide.pct_change(fill_method=None)
    ifix = rets["XFIX11"] if "XFIX11" in rets.columns else pd.Series(index=rets.index, dtype=float)
    uni = rets.drop(columns=["XFIX11"], errors="ignore").median(axis=1)
    return pd.DataFrame({"IFIX": ifix, "Universo": uni}).dropna(how="all")


@st.cache_data(ttl=3600, show_spinner=False)
def load_precos_mensais(tickers: tuple[str, ...]) -> pd.DataFrame:
    """
    Preços MENSAIS (último pregão do mês) AJUSTADOS por proventos+splits
    (adjusted_close = retorno total), a partir de market.historical_prices.

    Espelha a saída de views._batch_yf_precos_mensais: DataFrame com
    DatetimeIndex mensal × colunas = tickers (sem .SA). Substitui o download
    do yfinance no backtest do portfólio B3.
    """
    if not tickers:
        return pd.DataFrame()
    tks = [t.strip().upper().replace(".SA", "") for t in tickers]
    df = _q("SELECT ticker, date, COALESCE(adjusted_close, close) AS c "
            "FROM market.historical_prices WHERE ticker = ANY(:t) "
            "AND COALESCE(adjusted_close, close) IS NOT NULL ORDER BY ticker, date",
            {"t": tks})
    if df.empty:
        return pd.DataFrame()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"])
    wide = df.pivot_table(index="date", columns="ticker", values="c", aggfunc="last")
    mensal = wide.resample("ME").last()          # último preço válido de cada mês
    mensal.columns = [str(c).strip().upper() for c in mensal.columns]
    return mensal.dropna(how="all")


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


# Pregões por mês, para converter o giro MENSAL da série em estimativa diária.
# market.historical_prices guarda um candle por mês (a brapi devolve mensal no
# range longo), então close*volume de uma linha é o giro do MÊS inteiro.
_PREGOES_POR_MES = 21


@st.cache_data(ttl=3600, show_spinner=False)
def load_giro_diario(dias: int = 400) -> dict[str, float]:
    """{ticker: giro financeiro diário estimado em R$}.

    Usa a MEDIANA das barras mensais, não a média: um único mês de leilão ou de
    entrada em índice multiplica o volume e faria um papel ilíquido parecer
    negociável. Validação cruzada em 30/07/2026 — EUCA4 deu ~R$ 570 mil/dia
    aqui contra R$ 766 mil de ADTV publicado, mesma ordem de grandeza.
    """
    df = _q("""
        SELECT p.ticker,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY p.close * p.volume)
                 AS giro_mensal
        FROM market.historical_prices p
        WHERE p.date >= CURRENT_DATE - make_interval(days => :dias)
          AND p.volume > 0 AND p.close > 0
        GROUP BY p.ticker
    """, {"dias": int(dias)})
    if df.empty:
        return {}
    out: dict[str, float] = {}
    for _, row in df.iterrows():
        tk = str(row["ticker"]).replace(".SA", "").strip().upper()
        try:
            out[tk] = float(row["giro_mensal"]) / _PREGOES_POR_MES
        except (TypeError, ValueError):
            pass
    return out


@st.cache_data(ttl=3600, show_spinner=False)
def load_classes_irmas() -> dict[str, tuple[str, ...]]:
    """{ticker: classes ATIVAS da mesma empresa}, incluindo o próprio.

    Agrupa por ``company_id`` e não pela raiz de 4 letras: a raiz é heurística e
    junta tickers de empresas distintas que começam igual, o que aqui produziria
    troca entre companhias diferentes — exatamente o que este módulo não pode
    fazer.
    """
    df = _q("""
        SELECT a.company_id, a.ticker
        FROM market.assets a
        WHERE a.is_active AND a.company_id IS NOT NULL
          AND a.asset_type IN ('stock', 'unit')
    """)
    if df.empty:
        return {}
    por_empresa: dict[object, list[str]] = {}
    for _, row in df.iterrows():
        tk = str(row["ticker"]).replace(".SA", "").strip().upper()
        por_empresa.setdefault(row["company_id"], []).append(tk)
    out: dict[str, tuple[str, ...]] = {}
    for tickers in por_empresa.values():
        grupo = tuple(sorted(set(tickers)))
        for tk in grupo:
            out[tk] = grupo
    return out


@st.cache_data(ttl=3600, show_spinner=False)
def load_resiliencia_ciclo() -> dict[str, dict]:
    """{ticker: {razao, margem_normal, margem_crise, anos}} nas recessões.

    Compara a margem operacional MEDIANA dos anos de recessão com a dos anos
    normais, no histórico da própria empresa. Mediana e não média: um único ano
    de item extraordinário deslocaria a média e inverteria a leitura.

    Só descreve o passado do próprio ticker — não é inferência cruzada e não
    depende de amplitude de segmento. Ver core.b3_cycle_evidence para por que o
    resultado informa mas não reclassifica.
    """
    from core.b3_cycle_evidence import ANOS_DE_CRISE

    df = _q("""
        WITH m AS (
            SELECT ticker, year, ebit::numeric / NULLIF(revenue, 0) AS margem
            FROM market.income_statements
            WHERE period = 'annual' AND ebit IS NOT NULL AND revenue > 0
        )
        SELECT ticker,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY margem)
                 FILTER (WHERE year = ANY(:crise))        AS margem_crise,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY margem)
                 FILTER (WHERE NOT (year = ANY(:crise)))  AS margem_normal,
               count(*) FILTER (WHERE year = ANY(:crise)) AS anos_crise
        FROM m
        GROUP BY ticker
    """, {"crise": list(ANOS_DE_CRISE)})
    if df.empty:
        return {}

    out: dict[str, dict] = {}
    for _, row in df.iterrows():
        tk = str(row["ticker"]).replace(".SA", "").strip().upper()
        try:
            normal = float(row["margem_normal"])
            crise = float(row["margem_crise"])
            anos = int(row["anos_crise"])
        except (TypeError, ValueError):
            continue
        # Margem normal <= 0 torna a razão sem sentido (dois negativos dão
        # positivo — o mesmo defeito do ROE sobre patrimônio negativo).
        if normal != normal or crise != crise or normal <= 0:
            continue
        out[tk] = {"razao": crise / normal, "margem_normal": normal,
                   "margem_crise": crise, "anos": anos}
    return out
