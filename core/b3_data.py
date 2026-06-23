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
                     "load_multiplos", "load_historico_anos"}


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
    return _legacy.load_multiplos_historico(*a, **k)


def load_multiplos_historico_batch(*a, **k):
    return _legacy.load_multiplos_historico_batch(*a, **k)


def load_portfolio_snapshot(*a, **k):
    return _legacy.load_portfolio_snapshot(*a, **k)


def load_selic_macro(*a, **k):
    return _legacy.load_selic_macro(*a, **k)


def load_macro_history(*a, **k):
    return _legacy.load_macro_history(*a, **k)


# Passthrough p/ código que acessa a engine do App 1 diretamente.
def _engine():
    return _legacy._engine()
