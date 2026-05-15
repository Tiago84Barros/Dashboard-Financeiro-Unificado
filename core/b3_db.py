"""
core/b3_db.py
Camada de acesso a dados do Dashboard Fundamentalista B3 (App 1).

Variável de ambiente esperada (em ordem de prioridade):
  1. SUPABASE_DB_URL_B3  — URL dedicada ao banco do App 1
  2. SUPABASE_DB_URL     — URL do App 1 (nome antigo)
  3. DATABASE_URL        — fallback: mesmo banco do app unificado

Tabelas usadas (schema public do Supabase do App 1):
  - setores                    → listagem de empresas B3 por setor
  - "Demonstracoes_Financeiras" → DRE anual (Receita, EBIT, Lucro, etc.)
  - multiplos                  → múltiplos fundamentalistas anuais
"""
from __future__ import annotations

import os
from functools import lru_cache

import pandas as pd
import streamlit as st
from sqlalchemy import create_engine, text


# ── Engine ────────────────────────────────────────────────────────────────────

def _resolve_url() -> str | None:
    for key in ("SUPABASE_DB_URL_B3", "SUPABASE_DB_URL"):
        try:
            if hasattr(st, "secrets") and key in st.secrets:
                return str(st.secrets[key])
        except Exception:
            pass
        v = os.getenv(key)
        if v:
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
    is_sqlite = url.startswith("sqlite")
    kw: dict = {"pool_pre_ping": True}
    if not is_sqlite:
        kw.update({
            "pool_size": 2,
            "max_overflow": 2,
            "connect_args": {"connect_timeout": 10, "sslmode": "require"},
        })
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
def load_multiplos_todos() -> pd.DataFrame:
    """Retorna o múltiplo mais recente de TODOS os tickers (para ranking e avançada)."""
    df = _q("""
        SELECT DISTINCT ON ("Ticker")
               "Ticker", data,
               "P/L", "P/VP", "DY", "ROE", "ROA", "ROIC",
               "Margem_Liquida", "Margem_Operacional",
               "Endividamento_Total", "Liquidez_Corrente",
               "EV_EBIT", "P_FCO", "Payout"
        FROM public.multiplos
        WHERE "Ticker" IS NOT NULL
        ORDER BY "Ticker", data DESC
    """)
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
    return df


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
