"""
core/b3_data.py
Facade de leitura B3 com feature flag de origem de dados.

As telas devem importar ESTE módulo no lugar de core.b3_db:
    import core.b3_data as _db
e seguir chamando _db.load_*() — as assinaturas são idênticas.

A origem é escolhida por MARKET_READ_SOURCE (env ou st.secrets):
    legacy   (default) — lê do banco do App 1 (core.b3_db). Produção inalterada.
    market             — lê do schema market.* (core.market_read) quando suportado.
    compare            — lê do legado E do market, registra divergências em log,
                         e RETORNA o legado (seguro p/ a UI). Use p/ validar paridade.

Loaders ainda sem paridade em market.* (demonstrações, múltiplos históricos por
ano, macro, snapshot de carteira) caem sempre no legado, independente da flag.
"""
from __future__ import annotations

import logging
import os

import core.b3_db as _legacy
import core.market_read as _market

logger = logging.getLogger(__name__)

_VALID = ("legacy", "market", "compare")
# Loaders com implementação backed por market.* (paridade de colunas).
_MARKET_SUPPORTED = {"load_setores", "load_multiplos_todos",
                     "load_multiplos", "load_historico_anos",
                     "load_multiplos_historico", "load_multiplos_historico_batch"}


def read_source() -> str:
    """Origem de leitura corrente (legacy|market|compare). Default: legacy."""
    val = None
    try:
        import streamlit as st
        if hasattr(st, "secrets") and "MARKET_READ_SOURCE" in st.secrets:
            val = str(st.secrets["MARKET_READ_SOURCE"])
    except Exception:
        pass
    val = (val or os.getenv("MARKET_READ_SOURCE") or "legacy").strip().lower()
    return val if val in _VALID else "legacy"


def _min_coverage() -> float:
    """Cobertura mínima (market/legado) p/ ativar 'market'. Default 0.90."""
    try:
        v = float(os.getenv("MARKET_READ_MIN_COVERAGE", "0.90"))
        return min(max(v, 0.0), 1.0)
    except (TypeError, ValueError):
        return 0.90


def market_coverage() -> dict:
    """
    Razão de cobertura market/legado (tickers em load_multiplos_todos).
    Cacheado (reaproveita o cache dos loaders). Usado como porteiro do cutover:
    enquanto o bootstrap não cobrir o universo, 'market' não distorce o ranking
    misturando dados corrigidos (market) com placeholders do legado.
    """
    try:
        nl = int(_legacy.load_multiplos_todos()["Ticker"].nunique())
        nm = int(_market.load_multiplos_todos()["Ticker"].nunique())
    except Exception as exc:
        logger.warning("market_coverage: %s", exc)
        return {"legacy": 0, "market": 0, "ratio": 0.0, "ready": False, "min": _min_coverage()}
    ratio = (nm / nl) if nl else 0.0
    return {"legacy": nl, "market": nm, "ratio": round(ratio, 4),
            "ready": ratio >= _min_coverage(), "min": _min_coverage()}


def _market_ready() -> bool:
    """True se a cobertura do market.* atinge o limiar (porteiro do cutover)."""
    if os.getenv("MARKET_READ_FORCE", "").strip().lower() in ("1", "true", "yes"):
        return True  # override explícito p/ testes/rollout manual
    return market_coverage()["ready"]


def market_active() -> bool:
    """
    True quando a leitura está EFETIVAMENTE vindo do market.* (flag='market' E
    cobertura suficiente). As telas usam isto p/ desligar reparos defensivos
    (imputação MICE/mediana, patch via Fundamentus) quando a fonte já é limpa:
    com dado bom, nulo = ausente (rank neutro), não algo a reconstruir.
    Em 'legacy' e 'compare' (UI mostra legado) retorna False → reparos ativos.
    """
    return read_source() == "market" and _market_ready()


def _row_count(obj) -> int | None:
    try:
        return int(len(obj))
    except Exception:
        return None


def _log_divergence(name: str, legacy_res, market_res) -> None:
    """Comparação leve (contagem/cobertura) — parity detalhada fica na Fase 2."""
    nl, nm = _row_count(legacy_res), _row_count(market_res)
    extra = ""
    try:
        import pandas as pd
        if isinstance(legacy_res, pd.DataFrame) and isinstance(market_res, pd.DataFrame) \
                and "Ticker" in legacy_res.columns and "Ticker" in market_res.columns:
            sl = set(legacy_res["Ticker"].astype(str))
            sm = set(market_res["Ticker"].astype(str))
            extra = (f" | tickers comuns={len(sl & sm)} "
                     f"só_legado={len(sl - sm)} só_market={len(sm - sl)}")
    except Exception:
        pass
    logger.info("COMPARE %s: legado=%s market=%s%s", name, nl, nm, extra)


def _dispatch(name: str, *args, **kwargs):
    src = read_source()
    legacy_fn = getattr(_legacy, name)
    if name not in _MARKET_SUPPORTED or src == "legacy":
        return legacy_fn(*args, **kwargs)
    market_fn = getattr(_market, name)
    if src == "market":
        # porteiro do cutover: só serve market quando a cobertura é suficiente,
        # senão fica no legado (evita ranking misto corrigido×placeholder).
        if not _market_ready():
            logger.info("market %s adiado: cobertura %.0f%% < %.0f%% — usando legado",
                        name, market_coverage()["ratio"] * 100, _min_coverage() * 100)
            return legacy_fn(*args, **kwargs)
        try:
            return market_fn(*args, **kwargs)
        except Exception as exc:
            logger.warning("market %s falhou (%s) — fallback legado", name, exc)
            return legacy_fn(*args, **kwargs)
    # compare: roda os dois, loga divergência, devolve legado (seguro p/ UI)
    legacy_res = legacy_fn(*args, **kwargs)
    try:
        _log_divergence(name, legacy_res, market_fn(*args, **kwargs))
    except Exception as exc:
        logger.warning("compare %s: market falhou: %s", name, exc)
    return legacy_res


# ── Loaders backed por market.* (dispatch por flag) ───────────────────────────

def load_setores(*a, **k):
    return _dispatch("load_setores", *a, **k)


def load_multiplos_todos(*a, **k):
    return _dispatch("load_multiplos_todos", *a, **k)


def load_multiplos(*a, **k):
    return _dispatch("load_multiplos", *a, **k)


def load_historico_anos(*a, **k):
    return _dispatch("load_historico_anos", *a, **k)


# ── Loaders sem paridade em market.* — sempre legado ──────────────────────────

def load_demonstracoes(*a, **k):
    return _legacy.load_demonstracoes(*a, **k)


def load_demonstracoes_batch(*a, **k):
    return _legacy.load_demonstracoes_batch(*a, **k)


def load_multiplos_historico(*a, **k):
    return _dispatch("load_multiplos_historico", *a, **k)


def load_multiplos_historico_batch(*a, **k):
    return _dispatch("load_multiplos_historico_batch", *a, **k)


def load_portfolio_snapshot(*a, **k):
    return _legacy.load_portfolio_snapshot(*a, **k)


def load_selic_macro(*a, **k):
    return _legacy.load_selic_macro(*a, **k)


def load_macro_history(*a, **k):
    return _legacy.load_macro_history(*a, **k)


# Passthrough p/ código que acessa a engine do App 1 diretamente.
def _engine():
    return _legacy._engine()
