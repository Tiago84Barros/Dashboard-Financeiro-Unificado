"""
core/proventos.py
Camada de serviço de proventos — dividendos, JCP e rendimentos de FII.

Padrão idêntico ao core/investimentos.py:
  MOCK_MODE=true  → retorna dados mockados
  MOCK_MODE=false → tenta banco real; fallback para mock em qualquer erro

Tabelas consultadas (Fase 5.2):
  dividends  — todos os proventos (amount_per_unit, quantity, total_amount,
               ex_date, payment_date, type)
  assets     — ticker, nome, classe (JOIN por asset_id)

Nota sobre datas:
  payment_date é NOT NULL e é a data canônica para agrupamento histórico.
  ex_date é opcional (pode ser NULL ou conter datas históricas incorretas).
  Todas as queries usam payment_date para filtros de período.

Schema do dict retornado por get_proventos():
  data_source              str
  total_mes                float   Proventos com payment_date no mês corrente
  total_ano                float   Proventos com payment_date no ano corrente
  total_historico          float   Todos os proventos registrados
  num_ativos               int     Ativos distintos com ao menos 1 provento
  num_eventos              int     Total de registros em dividends
  historico_mensal         list    [{mes, ano, label, total}]
  por_ativo                list    [{ticker, nome, classe, cor, total, num_eventos, pct}]
  por_tipo                 list    [{tipo, label, total, num_eventos, pct}]
  eventos                  list    [{id, ticker, nome, classe, cor, tipo, label_tipo,
                                     amount_per_unit, quantity, total_amount,
                                     ex_date, payment_date}]
"""
import logging
from collections import defaultdict
from datetime import date as _date
from datetime import timedelta as _timedelta

import streamlit as st

from core.config import settings

logger = logging.getLogger(__name__)

# ── Constantes ────────────────────────────────────────────────────────────────
_TIPO_LABEL: dict[str, str] = {
    "dividend":     "Dividendo",
    "jcp":          "JCP",
    "reit_income":  "Rendimento FII",
    "amortization": "Amortização",
    "other":        "Outros",
}

_MESES_PT = {
    1: "Jan", 2: "Fev", 3: "Mar", 4: "Abr",
    5: "Mai", 6: "Jun", 7: "Jul", 8: "Ago",
    9: "Set", 10: "Out", 11: "Nov", 12: "Dez",
}

_CLASS_LABEL: dict[str, str] = {
    "reit":         "FII",
    "stock":        "Ações BR",
    "fixed_income": "Renda Fixa",
    "etf":          "ETF",
    "bdr":          "BDR",
    "other":        "Outros",
}

_CLASS_COR: dict[str, str] = {
    "reit":         "#E84C9B",
    "fii":          "#E84C9B",
    "stock":        "#4C9BE8",
    "fixed_income": "#A855F7",
    "renda_fixa":   "#A855F7",
    "tesouro":      "#2ECC71",
    "fundo_rf":     "#7C3AED",
    "etf":          "#F5A623",
    "etf_br":       "#F5A623",
    "etf_intl":     "#63cab7",
    "bdr":          "#4C9BE8",
    "crypto":       "#FF6B35",
    "other":        "#8b9ab0",
}

# ── Mock: eventos representativos ─────────────────────────────────────────────
# (ticker, nome, classe, tipo, amount_per_unit, quantity, total_amount, payment_date)
_MOCK_EVENTOS_RAW: list[tuple] = [
    # Mai 2026
    ("BITH11", "BTG Pactual Infra FII",      "reit",  "reit_income", 1.28, 190,  243.20, "2026-05-12"),
    ("HGLG11", "CSHG Logística FII",          "reit",  "reit_income", 1.18,  44,   51.92, "2026-05-12"),
    ("KNCR11", "Kinea CR FII",                "reit",  "reit_income", 1.10,  64,   70.40, "2026-05-12"),
    ("BRCO11", "Bresco Logística FII",        "reit",  "reit_income", 0.90,  60,   54.00, "2026-05-12"),
    ("VISC11", "Vinci Shopping Centers FII",  "reit",  "reit_income", 0.75,  62,   46.50, "2026-05-12"),
    ("IRDM11", "Iridium Recebíveis CRI FII",  "reit",  "reit_income", 0.68,  69,   46.92, "2026-05-12"),
    # Abr 2026
    ("PSSA3",  "Porto Seguro",                "stock", "dividend",    0.85, 643,  546.55, "2026-04-15"),
    ("BBAS3F", "Banco do Brasil",             "stock", "dividend",    0.62, 339,  210.18, "2026-04-10"),
    ("BITH11", "BTG Pactual Infra FII",       "reit",  "reit_income", 1.25, 190,  237.50, "2026-04-10"),
    ("HGLG11", "CSHG Logística FII",          "reit",  "reit_income", 1.15,  44,   50.60, "2026-04-10"),
    ("KNCR11", "Kinea CR FII",                "reit",  "reit_income", 1.08,  64,   69.12, "2026-04-10"),
    # Mar 2026
    ("PETR3F", "Petrobras PN",                "stock", "dividend",    1.20, 230,  276.00, "2026-03-20"),
    ("BITH11", "BTG Pactual Infra FII",       "reit",  "reit_income", 1.22, 190,  231.80, "2026-03-10"),
    ("BRCO11", "Bresco Logística FII",        "reit",  "reit_income", 0.88,  60,   52.80, "2026-03-10"),
    ("VISC11", "Vinci Shopping Centers FII",  "reit",  "reit_income", 0.72,  62,   44.64, "2026-03-10"),
    # Fev 2026
    ("ITUB3F", "Itaú Unibanco",               "stock", "jcp",         0.35, 174,   60.90, "2026-02-15"),
    ("BITH11", "BTG Pactual Infra FII",       "reit",  "reit_income", 1.20, 190,  228.00, "2026-02-10"),
    ("HGLG11", "CSHG Logística FII",          "reit",  "reit_income", 1.12,  44,   49.28, "2026-02-10"),
    ("IRDM11", "Iridium Recebíveis CRI FII",  "reit",  "reit_income", 0.65,  69,   44.85, "2026-02-10"),
    # Jan 2026
    ("BITH11", "BTG Pactual Infra FII",       "reit",  "reit_income", 1.18, 190,  224.20, "2026-01-10"),
    ("BRCO11", "Bresco Logística FII",        "reit",  "reit_income", 0.85,  60,   51.00, "2026-01-10"),
    ("VISC11", "Vinci Shopping Centers FII",  "reit",  "reit_income", 0.70,  62,   43.40, "2026-01-10"),
    ("KNCR11", "Kinea CR FII",                "reit",  "reit_income", 1.05,  64,   67.20, "2026-01-10"),
]

# ── SQL ───────────────────────────────────────────────────────────────────────
_SQL_PROVENTOS = """
    SELECT
        d.id::text,
        d.type,
        d.amount_per_unit,
        d.quantity,
        d.total_amount,
        d.ex_date,
        d.payment_date,
        a.ticker,
        a.name      AS asset_name,
        a.class     AS asset_class
    FROM   dividends d
    JOIN   assets a ON a.id = d.asset_id
    WHERE  d.user_id = :uid
    ORDER  BY d.payment_date DESC
"""


# ─────────────────────────────────────────────────────────────────────────────
# API pública
# ─────────────────────────────────────────────────────────────────────────────

@st.cache_data(ttl=300)
def get_proventos() -> dict:
    """
    Retorna o dicionário completo de dados para a página Proventos.
    Mesmo padrão mock/real/fallback dos demais módulos core/.
    """
    if settings.MOCK_MODE:
        dados = _proventos_mock()
        dados["data_source"] = "mock"
        return dados

    try:
        dados = _proventos_real()
        dados["data_source"] = "real"
        return dados
    except Exception as exc:
        logger.warning(
            "[proventos] Banco real falhou (%s: %s) — usando mock.",
            type(exc).__name__,
            str(exc)[:120],
        )
        dados = _proventos_mock()
        dados["data_source"] = "mock_fallback"
        return dados


# ─────────────────────────────────────────────────────────────────────────────
# MOCK
# ─────────────────────────────────────────────────────────────────────────────

def _proventos_mock() -> dict:
    hoje = _date.today()
    eventos = []
    for i, row in enumerate(_MOCK_EVENTOS_RAW):
        ticker, nome, classe_raw, tipo_raw, apu, qty, total, pd_str = row
        pd_obj = _date.fromisoformat(pd_str)
        eventos.append({
            "id":              str(i + 1),
            "ticker":          ticker,
            "nome":            nome,
            "classe":          _CLASS_LABEL.get(classe_raw, classe_raw),
            "cor":             _CLASS_COR.get(classe_raw, "#718096"),
            "tipo":            tipo_raw,
            "label_tipo":      _TIPO_LABEL.get(tipo_raw, tipo_raw),
            "amount_per_unit": float(apu),
            "quantity":        float(qty),
            "total_amount":    float(total),
            "ex_date":         None,
            "payment_date":    pd_obj,
        })
    return _montar_dict(eventos, hoje)


# ─────────────────────────────────────────────────────────────────────────────
# REAL
# ─────────────────────────────────────────────────────────────────────────────

def _proventos_real() -> dict:
    """
    Consulta dividends + assets e monta o dict de proventos.

    SEGURANÇA:
      - Somente SELECT.
      - Todos os registros filtrados por OWNER_USER_ID.
    """
    from sqlalchemy import text
    from core.database import get_engine

    engine = get_engine()
    if engine is None:
        raise RuntimeError("Engine indisponível — configure SUPABASE_UNIFICADO_URL.")

    owner = settings.OWNER_USER_ID
    if not owner:
        raise RuntimeError("OWNER_USER_ID não configurado.")

    hoje = _date.today()

    with engine.connect() as conn:
        rows = conn.execute(text(_SQL_PROVENTOS), {"uid": owner}).fetchall()

    if not rows:
        # Tabela vazia é válida — retorna dicionário zerado
        return _montar_dict([], hoje)

    def _f(v) -> float:
        return float(v) if v is not None else 0.0

    eventos = []
    for r in rows:
        classe_raw = r.asset_class or "other"
        tipo_raw   = r.type or "other"
        eventos.append({
            "id":              r.id,
            "ticker":          r.ticker,
            "nome":            r.asset_name,
            "classe":          _CLASS_LABEL.get(classe_raw, classe_raw.title()),
            "cor":             _CLASS_COR.get(classe_raw, "#718096"),
            "tipo":            tipo_raw,
            "label_tipo":      _TIPO_LABEL.get(tipo_raw, tipo_raw),
            "amount_per_unit": _f(r.amount_per_unit),
            "quantity":        _f(r.quantity),
            "total_amount":    _f(r.total_amount),
            "ex_date":         r.ex_date,
            "payment_date":    r.payment_date,
        })

    return _montar_dict(eventos, hoje)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers de montagem (compartilhados por mock e real)
# ─────────────────────────────────────────────────────────────────────────────

def _montar_dict(eventos: list, hoje: _date) -> dict:
    """Monta o dict de proventos a partir de uma lista de eventos normalizados."""
    inicio_12m = hoje - _timedelta(days=365)
    total_mes     = sum(
        e["total_amount"] for e in eventos
        if e["payment_date"] and
           e["payment_date"].year == hoje.year and
           e["payment_date"].month == hoje.month
    )
    total_ano     = sum(
        e["total_amount"] for e in eventos
        if e["payment_date"] and e["payment_date"].year == hoje.year
    )
    total_12m     = sum(
        e["total_amount"] for e in eventos
        if e["payment_date"] and inicio_12m <= e["payment_date"] <= hoje
    )
    total_hist    = sum(e["total_amount"] for e in eventos)
    ativos_set    = {e["ticker"] for e in eventos}

    return {
        "total_mes":       round(total_mes, 2),
        "total_ano":       round(total_ano, 2),
        "total_12m":       round(total_12m, 2),
        "total_historico": round(total_hist, 2),
        "num_ativos":      len(ativos_set),
        "num_eventos":     len(eventos),
        "historico_mensal": _historico_mensal(eventos),
        "por_ativo":        _agregar_por_ativo(eventos, total_hist),
        "por_tipo":         _agregar_por_tipo(eventos, total_hist),
        "eventos":          eventos,
        # data_source injetado pelo caller
    }


def _historico_mensal(eventos: list) -> list:
    """Agrupa proventos por mês/ano em ordem cronológica (últimos 12 meses com dados)."""
    buckets: dict[tuple, float] = defaultdict(float)
    for e in eventos:
        if e["payment_date"]:
            key = (e["payment_date"].year, e["payment_date"].month)
            buckets[key] += e["total_amount"]

    resultado = sorted(
        [
            {
                "mes":   _MESES_PT[mes],
                "ano":   ano,
                "label": f"{_MESES_PT[mes]}/{str(ano)[2:]}",
                "total": round(total, 2),
            }
            for (ano, mes), total in buckets.items()
        ],
        key=lambda x: (x["ano"], list(_MESES_PT.values()).index(x["mes"])),
    )
    return resultado[-12:]  # últimos 12 meses com dados


def _agregar_por_ativo(eventos: list, total: float) -> list:
    """Agrega proventos por ativo."""
    buckets: dict[str, dict] = {}
    for e in eventos:
        t = e["ticker"]
        if t not in buckets:
            buckets[t] = {
                "ticker":          t,
                "nome":            e["nome"],
                "classe":          e["classe"],
                "cor":             e["cor"],
                "total":           0.0,
                "num_eventos":     0,
                "ultimo_pagamento": None,
            }
        buckets[t]["total"]      += e["total_amount"]
        buckets[t]["num_eventos"] += 1
        pd = e.get("payment_date")
        if pd and (buckets[t]["ultimo_pagamento"] is None or pd > buckets[t]["ultimo_pagamento"]):
            buckets[t]["ultimo_pagamento"] = pd

    base = total or 1.0
    return sorted(
        [
            {
                **b,
                "total": round(b["total"], 2),
                "pct":   round(b["total"] / base * 100, 1),
                "ultimo_pagamento": (
                    b["ultimo_pagamento"].strftime("%d/%m/%Y")
                    if b["ultimo_pagamento"] else None
                ),
            }
            for b in buckets.values()
        ],
        key=lambda x: x["total"],
        reverse=True,
    )


def _agregar_por_tipo(eventos: list, total: float) -> list:
    """Agrega proventos por tipo."""
    buckets: dict[str, dict] = {}
    for e in eventos:
        tp = e["tipo"]
        if tp not in buckets:
            buckets[tp] = {"tipo": tp, "label": e["label_tipo"], "total": 0.0, "num_eventos": 0}
        buckets[tp]["total"]      += e["total_amount"]
        buckets[tp]["num_eventos"] += 1

    base = total or 1.0
    return sorted(
        [
            {**b, "total": round(b["total"], 2), "pct": round(b["total"] / base * 100, 1)}
            for b in buckets.values()
        ],
        key=lambda x: x["total"],
        reverse=True,
    )
