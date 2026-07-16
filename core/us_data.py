"""
core/us_data.py
Facade de leitura Empresas Americanas — a VIEW importa ESTE módulo.

Espelha o padrão core/b3_data.py (facade → market_read). Aqui a facade adiciona
cache Streamlit (@st.cache_data) sobre core.us_read e blinda a UI: qualquer erro
vira estrutura vazia/segura. A chave FMP NUNCA passa por aqui.
"""
from __future__ import annotations

import core.us_read as _read

try:
    import streamlit as st
    _cache = st.cache_data(ttl=300, show_spinner=False)
except Exception:  # contexto sem Streamlit (testes/CLI): no-op
    def _cache(fn):
        return fn


@_cache
def data_status() -> dict:
    return _read.data_status()


@_cache
def overview() -> dict:
    return _read.load_overview()


@_cache
def companies(sector: str | None = None, search: str | None = None,
              limit: int = 500):
    return _read.load_companies(sector=sector, search=search, limit=limit)


@_cache
def company_financials(symbol: str):
    return _read.load_company_financials(symbol)


@_cache
def quality_audit(limit: int = 200):
    return _read.load_quality_audit(limit=limit)


@_cache
def ingestion_runs():
    return _read.load_ingestion_runs()


def schema_ready() -> bool:
    return _read.schema_ready()
