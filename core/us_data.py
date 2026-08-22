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


def _use_snapshot() -> bool:
    """True quando só a vitrine existe (deploy) — sem as tabelas completas."""
    return not _read.schema_ready() and _read.snapshot_ready()


@_cache
def overview() -> dict:
    return _read.load_snapshot_overview() if _use_snapshot() else _read.load_overview()


@_cache
def companies(sector: str | None = None, search: str | None = None,
              limit: int = 500):
    if _use_snapshot():
        return _read.load_snapshot_companies(sector=sector, search=search, limit=limit)
    return _read.load_companies(sector=sector, search=search, limit=limit)


@_cache
def company_financials(symbol: str):
    if _use_snapshot():
        return _read.load_snapshot_financials(symbol)
    return _read.load_company_financials(symbol)


@_cache
def company_market_data(symbol: str):
    """Históricos de cotação, dividendos e múltiplos, sempre offline-first."""
    if _use_snapshot():
        return _read.load_snapshot_company_market_data(symbol)
    return _read.load_company_market_data(symbol)


@_cache
def macro_observado() -> dict:
    """Snapshot macro OBSERVADO (FRED) do warehouse; {} quando não ingerido."""
    return _read.load_us_macro()


@_cache
def quality_audit(limit: int = 200):
    return _read.load_quality_audit(limit=limit)


@_cache
def ingestion_runs():
    return _read.load_ingestion_runs()


def _anexa_negociabilidade_e_ciclo(frame):
    """Garante giro diário e travessia de crise como COLUNAS do cross-section.

    No modo vitrine elas já chegam expandidas do blob ``metrics``; aqui é o
    caminho local, que lê ``prices_daily`` e ``income_statements`` direto. Os
    nomes são os mesmos nos dois modos (data_pipeline.us.snapshot), então o
    motor de carteira e as telas leem uma coluna sem saber de qual modo vieram.
    """
    import pandas as pd

    from data_pipeline.us.snapshot import NEGOCIABILIDADE_COLS

    if frame is None or frame.empty or "symbol" not in frame.columns:
        return frame
    if all(c in frame.columns for c in NEGOCIABILIDADE_COLS):
        return frame

    try:
        giro = _read.load_us_giro_diario()
        ciclo = _read.load_us_resiliencia()
    except Exception:                       # dado ausente não pode zerar a aba
        return frame

    chaves = frame["symbol"].astype(str).str.strip().str.upper()
    out = frame.copy()
    # giro_diario_usd_at é a data (MAX(date)) da observação de preço usada no
    # cálculo do giro, a mesma fonte que a vitrine — não é inventada, é lida
    # de core.us_read.load_us_giro_diario junto com o valor de giro.
    out["giro_diario_usd"] = chaves.map(
        lambda s: (giro.get(s) or {}).get("giro_diario_usd")).astype(float)
    out["giro_diario_usd_at"] = chaves.map(
        lambda s: (giro.get(s) or {}).get("giro_diario_usd_at"))
    for coluna, campo in (("crise_razao", "razao"),
                          ("crise_anos_2008", "anos_2008"),
                          ("crise_anos_covid", "anos_covid"),
                          ("crise_margem_normal", "margem_normal"),
                          ("crise_margem_crise", "margem_crise")):
        out[coluna] = chaves.map(
            lambda s, _c=campo: (ciclo.get(s) or {}).get(_c))
        out[coluna] = pd.to_numeric(out[coluna], errors="coerce")
    return out


@_cache
def scored_universe(limit_companies: int | None = None):
    """Cross-section com score fundamentalista calculado (para as abas de análise).

    No warehouse local, calcula ao vivo. No deploy (só vitrine), lê os scores já
    computados da vitrine — mesmo formato de colunas.
    """
    import pandas as pd
    if _use_snapshot():
        return _anexa_negociabilidade_e_ciclo(_read.load_snapshot_scored())
    import core.us_score as _score
    frame = _read.load_scoring_frame(limit_companies=limit_companies)
    if frame is None or frame.empty:
        return frame if frame is not None else pd.DataFrame()
    return _anexa_negociabilidade_e_ciclo(_score.score_cross_section(frame))


def dossie(symbol: str) -> dict:
    if _use_snapshot():
        d = _read.load_snapshot_dossie(symbol)
        return d if d else {"symbol": (symbol or "").upper(),
                            "erro": "sem dados na vitrine para esta empresa"}
    import core.us_dossie as _dos
    return _dos.build_dossie(symbol)


# Horizonte do painel PIT: o score de uma data-base é confrontado com o retorno
# dos 12 meses seguintes. Constante compartilhada porque o benchmark do backtest
# TEM de medir a mesma janela — se um medir 12 meses e o outro 1, o excesso vira
# diferença de calendário disfarçada de habilidade.
HORIZONTE_PAINEL_MESES = 12


@_cache
def score_panel(score_version: str | None = None,
                horizon_months: int = HORIZONTE_PAINEL_MESES):
    return _read.load_score_panel(score_version=score_version,
                                  horizon_months=horizon_months)


@_cache
def advanced_snapshot(symbol: str):
    """Piotroski F-Score, Altman Z-Score, accruals de Sloan e ROIC incremental."""
    if _use_snapshot():
        return _read.load_snapshot_advanced(symbol)
    return _read.load_advanced_snapshot(symbol)


@_cache
def asymmetry_universe(limit_companies: int | None = None):
    """Cross-section de assimetria (Empresas Fora da Curva)."""
    if _use_snapshot():
        return _read.load_snapshot_asymmetry()
    return _read.load_asymmetry_frame(limit_companies=limit_companies)


def backtest(top_n: int = 20, weighting: str = "score", *,
             benchmark=None, benchmark_loader=None) -> dict:
    """Backtest PIT sobre o painel de scores (vazio até computar o histórico).

    O painel é ANUAL (rebalance a cada data-base, retorno futuro de 12 meses):
    periods_per_year=1, senão a anualização compõe 12× demais.

    `benchmark` é o rótulo do seletor "Benchmark de mercado" da tela de criação
    (core.us_benchmark.BENCHMARK_LABELS). Passá-lo acrescenta o índice como
    baseline; o equal-weight do universo continua sendo calculado e reportado.
    Sem série publicada para o índice, o resultado traz `benchmark.ok = False`
    com erro nomeado — nunca um excesso calculado contra outra coisa.
    """
    import core.us_backtest as _bt
    panel = score_panel(horizon_months=HORIZONTE_PAINEL_MESES)
    if panel is None or panel.empty:
        return {"ok": False, "reason": "sem histórico de pontuações (rode score-history)"}
    return _bt.walk_forward(panel, top_n=top_n, weighting=weighting,
                            periods_per_year=1, benchmark=benchmark,
                            benchmark_loader=benchmark_loader,
                            benchmark_horizon_months=HORIZONTE_PAINEL_MESES)


def benchmark_options() -> tuple[str, ...]:
    """Rótulos do seletor de benchmark, na ordem do mapa versionado.

    A UI lê a lista daqui em vez de repetir os rótulos: literal duplicado na
    view foi como o seletor passou a oferecer opções que o motor não conhecia.
    """
    from core.us_benchmark import BENCHMARK_LABELS
    return BENCHMARK_LABELS


def schema_ready() -> bool:
    return _read.schema_ready()
