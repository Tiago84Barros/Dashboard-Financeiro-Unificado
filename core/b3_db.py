"""
core/b3_db.py
Camada de acesso a dados do Dashboard Fundamentalista B3 (App 1).

Variável de ambiente esperada (em ordem de prioridade):
  1. SUPABASE_DB_URL_B3  — URL dedicada ao banco do App 1
  2. SUPABASE_DB_URL     — URL do App 1 (nome antigo)
  3. settings.db_url     — fallback: mesmo banco do app unificado (respeita
                           SUPABASE_UNIFICADO_URL > DATABASE_URL > SUPABASE_DB_URL)

ATENÇÃO (achado A-009, artifacts/app4_professionalizacao/correcao_a009_b3_db_fallback.md):
esta prioridade é PRÓPRIA e INDEPENDENTE da de core.config.Settings.db_url —
mantida de propósito porque scripts de ingestão sobrescrevem SUPABASE_DB_URL_B3
apenas na sessão do shell para apontar o coletor a um banco de staging local sem
alterar o restante do app (ver local_staging/README.md). Isso significa que, se
SUPABASE_DB_URL_B3/SUPABASE_DB_URL estiverem definidas no ambiente/.env (comum em
produção, para o banco original do App 1), esta camada NUNCA respeita um
DATABASE_URL sobrescrito no processo — mesmo que o resto do app respeite. Quando
isso diverge, _resolve_url() registra um logger.warning (sem credenciais) e
core.b3_data.load_setores() marca `df.attrs["fallback_legado"] = True` para que
a UI (views/empresas_b3.py) avise o usuário — nunca fica silencioso.

Tabelas usadas (schema public do Supabase do App 1):
  - setores                    → listagem de empresas B3 por setor
  - "Demonstracoes_Financeiras" → DRE anual (Receita, EBIT, Lucro, etc.)
  - multiplos                  → múltiplos fundamentalistas anuais
"""
from __future__ import annotations

import logging
import math
import os

import pandas as pd
import streamlit as st
from sqlalchemy import create_engine, text

from core.data_quality import clean_multiples_frame

logger = logging.getLogger(__name__)

# ── Engine ────────────────────────────────────────────────────────────────────

def _mask_url(url: str) -> str:
    """Reduz uma connection string a `esquema://usuario@host:porta/db`, sem senha."""
    try:
        from sqlalchemy.engine import make_url
        u = make_url(url)
        host = u.host or "?"
        port = f":{u.port}" if u.port else ""
        db = f"/{u.database}" if u.database else ""
        user = f"{u.username}@" if u.username else ""
        return f"{u.drivername}://{user}{host}{port}{db}"
    except Exception:
        return "<url ilegível>"


def _resolve_url() -> str | None:
    for key in ("SUPABASE_DB_URL_B3", "SUPABASE_DB_URL"):
        v = None
        try:
            if hasattr(st, "secrets") and key in st.secrets:
                v = str(st.secrets[key])
        except Exception:
            pass
        v = v or os.getenv(key)
        if v:
            # A-009: este caminho tem prioridade PRÓPRIA (independente de
            # settings.db_url/DATABASE_URL) por compatibilidade retroativa com
            # scripts de ingestão que sobrescrevem SUPABASE_DB_URL_B3 na sessão
            # do shell (ver local_staging/README.md). Quando diverge do que o
            # resto do app usaria, registra para não ficar silencioso — ver
            # core.b3_data.load_setores para o aviso visível na UI.
            try:
                from core.config import settings
                unified = settings.db_url
            except Exception:
                unified = os.getenv("DATABASE_URL") or ""
            if unified and unified != v:
                logger.warning(
                    "b3_db._resolve_url: usando %s=%s (legado), diverge do banco "
                    "unificado %s — DATABASE_URL/SUPABASE_UNIFICADO_URL do processo "
                    "está sendo IGNORADO para esta conexão.",
                    key, _mask_url(v), _mask_url(unified),
                )
            return v
    # Último recurso: mesmo banco do app unificado
    try:
        from core.config import settings
        return settings.db_url
    except Exception:
        return os.getenv("DATABASE_URL")


@st.cache_resource(show_spinner=False)
def _engine():
    url = _resolve_url()
    if not url:
        return None
    # Quando Empresas B3 e o app unificado apontam para a mesma base, reutilize a
    # engine central. Isso evita dois pools concorrentes por processo Streamlit.
    try:
        from core.config import settings
        if settings.db_url and url == settings.db_url:
            from core.database import get_engine
            return get_engine()
    except Exception:
        pass
    is_sqlite = url.startswith("sqlite")
    is_local = "localhost" in url or "127.0.0.1" in url
    kw: dict = {"pool_pre_ping": True}
    if not is_sqlite:
        connect_args: dict = {"connect_timeout": 10}
        if not is_local:  # Supabase exige SSL; staging local (Docker) não tem.
            connect_args["sslmode"] = "require"
        kw.update({"pool_size": 1, "max_overflow": 1, "pool_timeout": 10,
                   "pool_recycle": 300, "pool_use_lifo": True,
                   "connect_args": connect_args})
    try:
        return create_engine(url, **kw)
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


# ── Loaders públicos ──────────────────────────────────────────────────────────

@st.cache_data(ttl=3600, show_spinner=False)
def load_setores() -> pd.DataFrame:
    """Retorna DataFrame com colunas: ticker, nome_empresa, SETOR, SUBSETOR, SEGMENTO."""
    df = _q("""
        SELECT ticker, nome_empresa, "SETOR", "SUBSETOR", "SEGMENTO"
        FROM public.setores
        WHERE ticker IS NOT NULL
        ORDER BY "SETOR", ticker
    """)
    if df.empty:
        return df
    df["ticker"] = (
        df["ticker"].astype(str)
        .str.replace(".SA", "", regex=False)
        .str.strip().str.upper()
    )
    for c in ("nome_empresa", "SETOR", "SUBSETOR", "SEGMENTO"):
        if c not in df.columns:
            df[c] = ""
        df[c] = df[c].fillna("").astype(str)
    return df


@st.cache_data(ttl=3600, show_spinner=False)
def load_demonstracoes(ticker: str) -> pd.DataFrame:
    """Retorna histórico de demonstrações financeiras anuais para o ticker."""
    tk  = ticker.strip().upper().replace(".SA", "")
    tks = f"{tk}.SA"
    df  = _q(
        """
        SELECT *
        FROM public."Demonstracoes_Financeiras"
        WHERE "Ticker" = :tk OR "Ticker" = :tks
        ORDER BY data ASC
        """,
        {"tk": tk, "tks": tks},
    )
    if df.empty:
        return df
    # normaliza coluna de data
    data_col = next((c for c in df.columns if c.lower() == "data"), None)
    if data_col:
        df[data_col] = pd.to_datetime(df[data_col], errors="coerce")
        df = df.dropna(subset=[data_col]).sort_values(data_col)
        if data_col != "Data":
            df = df.rename(columns={data_col: "Data"})
    return df


@st.cache_data(ttl=3600, show_spinner=False)
def load_multiplos(ticker: str) -> pd.Series:
    """Retorna a linha mais recente de múltiplos fundamentalistas como Series."""
    tk  = ticker.strip().upper().replace(".SA", "")
    tks = f"{tk}.SA"
    df  = _q(
        """
        SELECT *
        FROM public.multiplos
        WHERE "Ticker" = :tk OR "Ticker" = :tks
        ORDER BY data DESC
        LIMIT 1
        """,
        {"tk": tk, "tks": tks},
    )
    if df.empty:
        return pd.Series(dtype=object)
    return df.iloc[0]


@st.cache_data(ttl=3600, show_spinner=False)
def load_multiplos_todos(ano_ref_max: int | None = None) -> pd.DataFrame:
    """Retorna o múltiplo mais recente de TODOS os tickers (para ranking e avançada).

    Args:
      ano_ref_max: se informado, considera apenas linhas cujo ano de `data`
        seja ≤ ano_ref_max. Usado para garantir que o score se baseie no
        último ano COMPLETO (ano anterior) e nunca em dado parcial do ano
        corrente — conforme a metodologia "score do ano anterior decide o
        ano atual". Tickers sem nenhuma linha ≤ ano_ref_max são omitidos
        (não há ano-base completo para decidir).
    """
    where_ano = ""
    params: dict = {}
    if ano_ref_max is not None:
        where_ano = "AND EXTRACT(YEAR FROM data) <= :ano_max"
        params["ano_max"] = int(ano_ref_max)
    df = _q(f"""
        SELECT DISTINCT ON ("Ticker")
               "Ticker", data,
               "P/L", "P/VP", "DY", "ROE", "ROA", "ROIC",
               "Margem_Liquida", "Margem_Operacional",
               "Endividamento_Total", "Liquidez_Corrente",
               "EV_EBIT", "P_FCO", "Payout"
        FROM public.multiplos
        WHERE "Ticker" IS NOT NULL
        {where_ano}
        ORDER BY "Ticker", data DESC
    """, params)
    if df.empty:
        return df
    df["Ticker"] = (
        df["Ticker"].astype(str)
        .str.replace(".SA", "", regex=False)
        .str.strip().str.upper()
    )
    for c in [col for col in df.columns if col not in ("Ticker", "data")]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


@st.cache_data(ttl=3600, show_spinner=False)
def load_multiplos_historico(ticker: str) -> pd.DataFrame:
    """Retorna todo o histórico de múltiplos para um ticker (ordem crescente)."""
    tk  = ticker.strip().upper().replace(".SA", "")
    tks = f"{tk}.SA"
    df  = _q(
        """
        SELECT *
        FROM public.multiplos
        WHERE "Ticker" = :tk OR "Ticker" = :tks
        ORDER BY data ASC
        """,
        {"tk": tk, "tks": tks},
    )
    if df.empty:
        return df
    data_col = next((c for c in df.columns if c.lower() == "data"), None)
    if data_col:
        df[data_col] = pd.to_datetime(df[data_col], errors="coerce")
        df = df.dropna(subset=[data_col]).sort_values(data_col)
        if data_col != "Data":
            df = df.rename(columns={data_col: "Data"})
    # Blindagem: public.multiplos tem UNIDADES MISTURADAS por ano (linhas antigas
    # em percent: ROIC 25.199 = 25%, ROE 27.5%; linhas novas em decimal 0.13).
    # O modelo é decimal (0.15 = 15%), então as linhas percent são outliers fora
    # de faixa. clean_multiples_frame vira faixa-fora → NaN (ausente, nunca 0),
    # impedindo que os valores legados corrompidos envenenem score/médias.
    return clean_multiples_frame(df)


@st.cache_data(ttl=3600, show_spinner=False)
def load_portfolio_snapshot() -> tuple[dict, pd.DataFrame]:
    """Retorna o snapshot mais recente (header + items).
    Retorna ({}, DataFrame vazio) se a tabela não existir."""
    snap = _q("""
        SELECT id, plan_hash, selic_ref, created_at
        FROM public.portfolio_snapshots
        ORDER BY created_at DESC
        LIMIT 1
    """)
    if snap.empty:
        return {}, pd.DataFrame()
    header = snap.iloc[0].to_dict()
    items = _q(
        """
        SELECT ticker, weight, segmento
        FROM public.portfolio_snapshot_items
        WHERE snapshot_id = :sid
        ORDER BY weight DESC
        """,
        {"sid": header["id"]},
    )
    if not items.empty:
        items["ticker"] = (
            items["ticker"].astype(str)
            .str.replace(".SA", "", regex=False)
            .str.strip().str.upper()
        )
    return header, items


# ── Batch loaders (Análise Avançada) ─────────────────────────────────────────

def _safe_in_clause(tickers: tuple[str, ...]) -> tuple[list[str], str] | None:
    """Constrói cláusula IN segura com tickers sanitizados (apenas alnum + dígito)."""
    tks_clean = [t.strip().upper().replace(".SA", "") for t in tickers]
    tks_sa    = [f"{t}.SA" for t in tks_clean]
    all_tks   = [t for t in tks_clean + tks_sa
                 if t.replace(".", "").replace("3", "3").replace("4", "4").isalnum()
                 or all(c.isalnum() for c in t.replace(".", ""))]
    if not all_tks:
        return None
    placeholders = ", ".join(f"'{t}'" for t in all_tks)
    return tks_clean, placeholders


@st.cache_data(ttl=3600, show_spinner=False)
def load_multiplos_historico_batch(tickers: tuple[str, ...]) -> dict[str, pd.DataFrame]:
    """Retorna histórico de múltiplos para múltiplos tickers em uma query."""
    if not tickers:
        return {}
    parsed = _safe_in_clause(tickers)
    if parsed is None:
        return {}
    tks_clean, placeholders = parsed
    if len(tks_clean) > 80:
        result: dict[str, pd.DataFrame] = {}
        for i in range(0, len(tks_clean), 80):
            result.update(load_multiplos_historico_batch(tuple(tks_clean[i:i + 80])))
        return result
    df = _q(f"""
        SELECT *
        FROM public.multiplos
        WHERE "Ticker" IN ({placeholders})
        ORDER BY "Ticker", data ASC
    """)
    if df.empty:
        return {}
    data_col = next((c for c in df.columns if c.lower() == "data"), None)
    if data_col:
        df[data_col] = pd.to_datetime(df[data_col], errors="coerce")
        df = df.dropna(subset=[data_col]).sort_values(["Ticker", data_col])
        if data_col != "Data":
            df = df.rename(columns={data_col: "Data"})
    df["Ticker"] = (
        df["Ticker"].astype(str)
        .str.replace(".SA", "", regex=False)
        .str.strip().str.upper()
    )
    result: dict[str, pd.DataFrame] = {}
    for tk in tks_clean:
        sub = df[df["Ticker"] == tk].copy().reset_index(drop=True)
        if not sub.empty:
            # Blindagem de unidade/outlier (ver load_multiplos_historico).
            result[tk] = clean_multiples_frame(sub)
    return result


@st.cache_data(ttl=3600, show_spinner=False)
def load_demonstracoes_batch(tickers: tuple[str, ...]) -> dict[str, pd.DataFrame]:
    """Retorna demonstrações financeiras para múltiplos tickers em uma query."""
    if not tickers:
        return {}
    parsed = _safe_in_clause(tickers)
    if parsed is None:
        return {}
    tks_clean, placeholders = parsed
    df = _q(f"""
        SELECT *
        FROM public."Demonstracoes_Financeiras"
        WHERE "Ticker" IN ({placeholders})
        ORDER BY "Ticker", data ASC
    """)
    if df.empty:
        return {}
    data_col = next((c for c in df.columns if c.lower() == "data"), None)
    if data_col:
        df[data_col] = pd.to_datetime(df[data_col], errors="coerce")
        df = df.dropna(subset=[data_col]).sort_values(["Ticker", data_col])
        if data_col != "Data":
            df = df.rename(columns={data_col: "Data"})
    df["Ticker"] = (
        df["Ticker"].astype(str)
        .str.replace(".SA", "", regex=False)
        .str.strip().str.upper()
    )
    result: dict[str, pd.DataFrame] = {}
    for tk in tks_clean:
        sub = df[df["Ticker"] == tk].copy().reset_index(drop=True)
        if not sub.empty:
            result[tk] = sub
    return result


@st.cache_data(ttl=3600, show_spinner=False)
def load_historico_anos() -> dict[str, int]:
    """Retorna número de anos distintos de histórico DRE por ticker."""
    df = _q("""
        SELECT "Ticker",
               COUNT(DISTINCT EXTRACT(YEAR FROM data))::int AS anos
        FROM public."Demonstracoes_Financeiras"
        WHERE "Ticker" IS NOT NULL AND data IS NOT NULL
        GROUP BY "Ticker"
    """)
    if df.empty:
        return {}
    result: dict[str, int] = {}
    for _, row in df.iterrows():
        tk = str(row["Ticker"]).replace(".SA", "").strip().upper()
        try:
            result[tk] = int(row["anos"])
        except Exception:
            pass
    return result


@st.cache_data(ttl=3600, show_spinner=False)
def load_selic_macro() -> dict[int, float]:
    """
    Retorna {ano: selic_decimal} da tabela public.macro do App 1.
    Retorna {} se a tabela não existir ou estiver vazia.
    Aceita Selic em escala decimal (0.1275) ou percentual (12.75).
    """
    df = _q("""
        SELECT ano, selic
        FROM public.macro
        WHERE selic IS NOT NULL
        ORDER BY ano
    """)
    if df.empty:
        return {}
    result: dict[int, float] = {}
    for _, row in df.iterrows():
        try:
            ano = int(row["ano"])
            selic = float(row["selic"])
            if abs(selic) > 1:
                selic = selic / 100.0
            if 0 <= selic <= 1:
                result[ano] = selic
        except Exception:
            pass
    return result


@st.cache_data(ttl=3600, show_spinner=False)
def load_macro_history() -> dict[int, dict[str, float]]:
    """
    Retorna indicadores macro anuais da public.macro.
    Usado como ajuste contextual do scoring; retorna {} se a tabela/colunas
    ainda nao estiverem disponiveis.
    """
    df = _q("""
        SELECT ano, selic, ipca, cambio, balanca_comercial, icc, icc_delta,
               pib, divida_publica, juros_real_ex_ante
        FROM public.macro
        ORDER BY ano
    """)
    if df.empty:
        return {}

    numeric_cols = [c for c in df.columns if c != "ano"]
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    if "selic" in df.columns:
        df["selic"] = df["selic"].where(df["selic"].abs() <= 1, df["selic"] / 100.0)

    result: dict[int, dict[str, float]] = {}
    for _, row in df.iterrows():
        try:
            ano = int(row["ano"])
        except Exception:
            continue
        vals: dict[str, float] = {}
        for col in numeric_cols:
            try:
                value = float(row[col])
            except Exception:
                continue
            if pd.notna(value) and math.isfinite(value):
                vals[col] = value
        if vals:
            result[ano] = vals
    return result
