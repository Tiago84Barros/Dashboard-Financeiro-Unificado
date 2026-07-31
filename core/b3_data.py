"""
core/b3_data.py
Facade de leitura B3 — fonte financeira ÚNICA: market.* (brapi).

As telas devem importar ESTE módulo no lugar de core.b3_db:
    import core.b3_data as _db
e seguir chamando _db.load_*() — as assinaturas são idênticas.

Histórico do cutover (2026-07): a migração legado→market usava a feature flag
MARKET_READ_SOURCE com porteiro de cobertura/qualidade e fallback ao legado.
O cutover foi CONCLUÍDO: as tabelas legadas de fundamentos (public.multiplos,
"Demonstracoes_Financeiras") foram DROPADAS por injetarem dados contraditórios
(unidades misturadas por ano), e os loaders financeiros/setores abaixo leem só
market.* — a flag deixou de ter efeito e o gate foi aposentado. Restam no
legado apenas tabelas de outros domínios: snapshot de carteira, selic e macro.
"""
from __future__ import annotations

import logging
import os

import pandas as pd

import core.b3_db as _legacy
import core.market_read as _market

logger = logging.getLogger(__name__)

_VALID = ("legacy", "market", "compare")


def read_source() -> str:
    """
    Valor da flag MARKET_READ_SOURCE (legacy|market|compare). Mantida apenas
    como INFORMATIVO (painel admin de saúde de dados) — desde o cutover
    2026-07 os loaders financeiros/setores ignoram a flag e leem só market.*.
    """
    val = None
    try:
        import streamlit as st
        if hasattr(st, "secrets") and "MARKET_READ_SOURCE" in st.secrets:
            val = str(st.secrets["MARKET_READ_SOURCE"])
    except Exception:
        pass
    val = (val or os.getenv("MARKET_READ_SOURCE") or "legacy").strip().lower()
    return val if val in _VALID else "legacy"


def market_active() -> bool:
    """
    Cutover FINANCEIRO concluído (2026-07): os múltiplos, demonstrações e
    histórico vêm EXCLUSIVAMENTE do market.* (brapi) — ver os loaders
    financeiros abaixo, que ignoram MARKET_READ_SOURCE e não caem mais no
    legado public.multiplos/"Demonstracoes_Financeiras" (desativados por
    injetarem dados contraditórios). Logo os 'reparos defensivos' das telas
    (imputação MICE/mediana, patch via Fundamentus, dividendos via yfinance)
    ficam SEMPRE desligados: com fonte única e limpa, nulo = ausente (rank
    neutro), nunca algo a reconstruir por fonte externa (não-brapi).
    """
    return True


# setores: taxonomia B3 (referência). PREFERE a versão market.*, que herda o
# setor da ON para as PNs/units pela raiz de 4 letras (BBDC4->BBDC3) — o legado
# public.setores só lista a ON, deixando ~100 classes PN SEM setor (Bradesco,
# Cemig, Braskem…), o que degrada o scoring intra-setor. Legado como fallback.
def load_setores(*a, **k):
    try:
        m = _market.load_setores(*a, **k)
        if m is not None and not getattr(m, "empty", False):
            return m
    except Exception as exc:
        logger.warning("setores market falhou (%s) — fallback legado", exc)
    return _legacy.load_setores(*a, **k)


# ── Dados FINANCEIROS: fonte ÚNICA = market.* (brapi) ─────────────────────────
# O legado public.multiplos / "Demonstracoes_Financeiras" foi DESATIVADO por
# injetar valores financeiros contraditórios (unidades misturadas por ano,
# dados defasados). Estes loaders IGNORAM MARKET_READ_SOURCE e NÃO caem no
# legado: brapi é a fonte única da verdade. Em erro/ausência do market devolvem
# vazio (nulo = ausente, rank neutro), NUNCA o legado. Snapshot de carteira,
# selic e macro seguem no legado (outras tabelas, não são as desativadas).

def _financeiro(name: str, vazio, *a, **k):
    try:
        res = getattr(_market, name)(*a, **k)
        return res if res is not None else vazio
    except Exception as exc:
        logger.warning("market %s falhou (%s) — financeiro sem fallback legado",
                       name, exc)
        return vazio


def load_multiplos_todos(*a, **k):
    return _financeiro("load_multiplos_todos", pd.DataFrame(), *a, **k)


def load_multiplos(*a, **k):
    return _financeiro("load_multiplos", pd.DataFrame(), *a, **k)


def load_historico_anos(*a, **k):
    return _financeiro("load_historico_anos", pd.DataFrame(), *a, **k)


def load_giro_diario(*a, **k):
    """Giro financeiro diário estimado por ticker (piso de negociabilidade)."""
    return _financeiro("load_giro_diario", {}, *a, **k)


def load_classes_irmas(*a, **k):
    """Classes ativas de cada empresa, por ticker (troca ON↔PN da mesma dona)."""
    return _financeiro("load_classes_irmas", {}, *a, **k)


def load_resiliencia_ciclo(*a, **k):
    """Margem operacional nas recessões vs anos normais, por ticker."""
    return _financeiro("load_resiliencia_ciclo", {}, *a, **k)


def load_demonstracoes(*a, **k):
    return _financeiro("load_demonstracoes", pd.DataFrame(), *a, **k)


def load_demonstracoes_batch(*a, **k):
    return _financeiro("load_demonstracoes_batch", {}, *a, **k)


def load_multiplos_historico(*a, **k):
    return _financeiro("load_multiplos_historico", pd.DataFrame(), *a, **k)


def load_multiplos_historico_batch(*a, **k):
    return _financeiro("load_multiplos_historico_batch", {}, *a, **k)


def load_portfolio_snapshot(*a, **k):
    return _legacy.load_portfolio_snapshot(*a, **k)


def load_selic_macro(*a, **k):
    return _legacy.load_selic_macro(*a, **k)


def load_macro_history(*a, **k):
    return _legacy.load_macro_history(*a, **k)


# Passthrough p/ código que acessa a engine do App 1 diretamente.
def _engine():
    return _legacy._engine()
