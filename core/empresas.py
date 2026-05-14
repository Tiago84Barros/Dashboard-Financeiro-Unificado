"""
core/empresas.py
Camada de serviço para listagem e consulta de ativos cadastrados no banco.

Padrão: MOCK_MODE=true → 20 ativos mock, false → real com fallback.

Tabelas:
  assets       — ticker, name, class, sector, currency, exchange
  asset_quotes — cotação mais recente via LATERAL join

API pública:
  get_ativos()             → dict {data_source, total, por_classe, ativos[]}
  get_ativo_por_ticker(t)  → dict | None

Schema de cada ativo:
  ticker, nome, classe_raw, classe, setor_raw, setor,
  moeda, exchange, preco_atual (None se sem cotação),
  ultima_cotacao (datetime | None), tem_cotacao, cor
"""
import logging

import streamlit as st

from core.config import settings

logger = logging.getLogger(__name__)

# ── Mapeamentos reutilizados de core/investimentos ────────────────────────────
_CLASS_LABEL: dict[str, str] = {
    "reit":         "FII",
    "stock":        "Ações",
    "fixed_income": "Renda Fixa",
    "etf":          "ETF",
    "bdr":          "BDR",
    "crypto":       "Cripto",
    "other":        "Outros",
}

_CLASS_COR: dict[str, str] = {
    "reit":         "#9B59B6",
    "stock":        "#00C896",
    "fixed_income": "#4A9EFF",
    "etf":          "#F6C90E",
    "bdr":          "#FC5C7D",
    "crypto":       "#FF6B35",
    "other":        "#718096",
}

_SETOR_LABEL: dict[str, str] = {
    "real_estate":      "Imóveis / FII",
    "financials":       "Financeiro",
    "utilities":        "Utilidades",
    "energy":           "Energia",
    "materials":        "Materiais",
    "industrials":      "Industrial",
    "consumer":         "Consumo",
    "consumer_staples": "Consumo Básico",
    "health_care":      "Saúde",
    "technology":       "Tecnologia",
    "telecom":          "Telecom",
    "other":            "Outros",
}

# ── Mock ──────────────────────────────────────────────────────────────────────
# (ticker, nome, classe_raw, setor_raw, moeda, exchange, preco_mock)
_MOCK_RAW: list[tuple] = [
    ("BITH11",  "BTG Pactual Infra FII",         "reit",         "real_estate",  "BRL", "B3",   137.87),
    ("PSSA3",   "Porto Seguro",                   "stock",        "financials",   "BRL", "B3",    35.15),
    ("EQTL3F",  "Equatorial Energia",             "stock",        "utilities",    "BRL", "B3",    24.25),
    ("MXRF15",  "Maxi Renda FII",                 "reit",         "real_estate",  "BRL", "B3",    10.29),
    ("BBAS3F",  "Banco do Brasil",                "stock",        "financials",   "BRL", "B3",    33.30),
    ("BRCO11",  "Bresco Logística FII",           "reit",         "real_estate",  "BRL", "B3",   117.52),
    ("HGLG11",  "CSHG Logística FII",             "reit",         "real_estate",  "BRL", "B3",   157.06),
    ("KNCR11",  "Kinea CR FII",                   "reit",         "real_estate",  "BRL", "B3",   106.25),
    ("VISC11",  "Vinci Shopping Centers FII",     "reit",         "real_estate",  "BRL", "B3",   108.85),
    ("PETR3F",  "Petrobras PN",                   "stock",        "energy",       "BRL", "B3",    28.78),
    ("GMAT3",   "Getnet S.A.",                    "stock",        "financials",   "BRL", "B3",     1.40),
    ("TRPL3F",  "CTEEP",                          "stock",        "utilities",    "BRL", "B3",    29.19),
    ("ROMI3",   "Romi S.A.",                      "stock",        "industrials",  "BRL", "B3",     8.38),
    ("ITUB3F",  "Itaú Unibanco",                  "stock",        "financials",   "BRL", "B3",    25.37),
    ("IRDM11",  "Iridium Recebíveis CRI FII",     "reit",         "real_estate",  "BRL", "B3",    57.67),
    ("SBSP3",   "Sabesp",                         "stock",        "utilities",    "BRL", "B3",    33.87),
    ("CSMG3F",  "COPASA",                         "stock",        "utilities",    "BRL", "B3",    40.83),
    ("BRAP3",   "Bradespar",                      "stock",        "materials",    "BRL", "B3",    18.47),
    ("ISAE3",   "Isa Energia",                    "stock",        "utilities",    "BRL", "B3",    33.15),
    ("BRAP3F",  "Bradespar PN",                   "stock",        "materials",    "BRL", "B3",    18.47),
]

# ── SQL ───────────────────────────────────────────────────────────────────────
_SQL_ATIVOS = """
    SELECT
        a.id::text,
        a.ticker,
        a.name,
        a.class         AS asset_class,
        a.sector,
        a.currency,
        a.exchange,
        aq.close        AS preco_atual,
        aq.timestamp    AS ultima_cotacao
    FROM  assets a
    LEFT JOIN LATERAL (
        SELECT close, timestamp
        FROM   asset_quotes
        WHERE  asset_id = a.id
        ORDER  BY timestamp DESC
        LIMIT  1
    ) aq ON true
    ORDER BY a.class, a.ticker
"""

_SQL_ATIVO_TICKER = """
    SELECT
        a.id::text,
        a.ticker,
        a.name,
        a.class         AS asset_class,
        a.sector,
        a.currency,
        a.exchange,
        aq.close        AS preco_atual,
        aq.timestamp    AS ultima_cotacao
    FROM  assets a
    LEFT JOIN LATERAL (
        SELECT close, timestamp
        FROM   asset_quotes
        WHERE  asset_id = a.id
        ORDER  BY timestamp DESC
        LIMIT  1
    ) aq ON true
    WHERE UPPER(a.ticker) = UPPER(:ticker)
    LIMIT 1
"""


# ─────────────────────────────────────────────────────────────────────────────
# API pública
# ─────────────────────────────────────────────────────────────────────────────

@st.cache_data(ttl=300)
def get_ativos() -> dict:
    """
    Retorna dict com todos os ativos cadastrados + resumo por classe.
    """
    if settings.MOCK_MODE:
        d = _ativos_mock()
        d["data_source"] = "mock"
        return d

    try:
        d = _ativos_real()
        d["data_source"] = "real"
        return d
    except Exception as exc:
        logger.warning("[empresas] Banco real falhou (%s: %s) — mock.", type(exc).__name__, str(exc)[:120])
        d = _ativos_mock()
        d["data_source"] = "mock_fallback"
        return d


@st.cache_data(ttl=300)
def get_ativo_por_ticker(ticker: str):
    """Retorna dict de um ativo específico ou None se não encontrado."""
    if settings.MOCK_MODE:
        todos = _ativos_mock()["ativos"]
        return next((a for a in todos if a["ticker"].upper() == ticker.upper()), None)

    try:
        from sqlalchemy import text
        from core.database import get_engine

        engine = get_engine()
        if engine is None:
            return None

        with engine.connect() as conn:
            row = conn.execute(text(_SQL_ATIVO_TICKER), {"ticker": ticker}).fetchone()

        return _row_to_dict(row) if row else None
    except Exception as exc:
        logger.warning("[empresas] get_ativo_por_ticker falhou: %s", exc)
        return None


# ─────────────────────────────────────────────────────────────────────────────
# MOCK
# ─────────────────────────────────────────────────────────────────────────────

def _ativos_mock() -> dict:
    ativos = [
        _build_ativo(t, n, c, s, mo, ex, p, None)
        for t, n, c, s, mo, ex, p in _MOCK_RAW
    ]
    return {
        "total":      len(ativos),
        "com_cotacao": sum(1 for a in ativos if a["tem_cotacao"]),
        "por_classe": _agregar_classes(ativos),
        "por_setor":  _agregar_setores(ativos),
        "ativos":     ativos,
    }


# ─────────────────────────────────────────────────────────────────────────────
# REAL
# ─────────────────────────────────────────────────────────────────────────────

def _ativos_real() -> dict:
    from sqlalchemy import text
    from core.database import get_engine

    engine = get_engine()
    if engine is None:
        raise RuntimeError("Engine indisponível.")

    with engine.connect() as conn:
        rows = conn.execute(text(_SQL_ATIVOS)).fetchall()

    ativos = [_row_to_dict(r) for r in rows]
    return {
        "total":       len(ativos),
        "com_cotacao": sum(1 for a in ativos if a["tem_cotacao"]),
        "por_classe":  _agregar_classes(ativos),
        "por_setor":   _agregar_setores(ativos),
        "ativos":      ativos,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _row_to_dict(r) -> dict:
    return _build_ativo(
        r.ticker, r.name, r.asset_class, r.sector,
        r.currency, r.exchange, r.preco_atual, r.ultima_cotacao,
    )


def _build_ativo(
    ticker: str,
    nome: str,
    classe_raw: str,
    setor_raw,
    moeda: str,
    exchange,
    preco,
    ultima_cotacao=None,
) -> dict:
    cr = classe_raw or "other"
    sr = setor_raw  or "other"
    return {
        "ticker":          ticker,
        "nome":            nome,
        "classe_raw":      cr,
        "classe":          _CLASS_LABEL.get(cr, cr.replace("_", " ").title()),
        "setor_raw":       sr,
        "setor":           _SETOR_LABEL.get(sr, sr.replace("_", " ").title()),
        "moeda":           moeda or "BRL",
        "exchange":        exchange or "—",
        "preco_atual":     round(float(preco), 2) if preco is not None else None,
        "ultima_cotacao":  ultima_cotacao,
        "tem_cotacao":     preco is not None,
        "cor":             _CLASS_COR.get(cr, "#718096"),
    }


def _agregar_classes(ativos: list) -> list:
    buckets: dict[str, int] = {}
    for a in ativos:
        buckets[a["classe"]] = buckets.get(a["classe"], 0) + 1
    return sorted(
        [{"classe": k, "num_ativos": v} for k, v in buckets.items()],
        key=lambda x: x["num_ativos"],
        reverse=True,
    )


def _agregar_setores(ativos: list) -> list:
    buckets: dict[str, int] = {}
    for a in ativos:
        buckets[a["setor"]] = buckets.get(a["setor"], 0) + 1
    return sorted(
        [{"setor": k, "num_ativos": v} for k, v in buckets.items()],
        key=lambda x: x["num_ativos"],
        reverse=True,
    )
