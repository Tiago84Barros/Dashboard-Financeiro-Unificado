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


@_cache
def scored_universe(limit_companies: int = 800):
    """Cross-section com score fundamentalista calculado (para as abas de análise)."""
    import core.us_score as _score
    frame = _read.load_scoring_frame(limit_companies=limit_companies)
    if frame is None or frame.empty:
        return frame if frame is not None else __import__("pandas").DataFrame()
    return _score.score_cross_section(frame)


def dossie(symbol: str) -> dict:
    import core.us_dossie as _dos
    return _dos.build_dossie(symbol)


@_cache
def score_panel(score_version: str | None = None, horizon_months: int = 12):
    return _read.load_score_panel(score_version=score_version,
                                  horizon_months=horizon_months)


@_cache
def advanced_snapshot(symbol: str):
    """Piotroski F-Score, Altman Z-Score, accruals de Sloan e ROIC incremental."""
    return _read.load_advanced_snapshot(symbol)


@_cache
def asymmetry_universe(limit_companies: int = 800):
    """Cross-section de assimetria (Empresas Fora da Curva)."""
    return _read.load_asymmetry_frame(limit_companies=limit_companies)


def backtest(top_n: int = 20, weighting: str = "score") -> dict:
    """Backtest PIT sobre o painel de scores (vazio até computar o histórico)."""
    import core.us_backtest as _bt
    panel = score_panel()
    if panel is None or panel.empty:
        return {"ok": False, "reason": "sem histórico de scores (rode score-history)"}
    return _bt.walk_forward(panel, top_n=top_n, weighting=weighting)


def schema_ready() -> bool:
    return _read.schema_ready()
