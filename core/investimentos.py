"""
core/investimentos.py
Camada de serviço de investimentos — posições da carteira, alocação e custo médio.

Padrão idêntico ao core/financeiro.py:
  MOCK_MODE=true  → retorna dados mockados
  MOCK_MODE=false → tenta banco real; fallback para mock em qualquer erro

Tabelas consultadas (Fase 5.1):
  portfolio_positions  — posições atuais (qty, preço médio, total investido)
  assets               — ticker, nome, classe (class), setor, moeda
  asset_quotes         — cotação mais recente via LATERAL join (NULL quando vazia)

Query principal:
  SELECT pp.quantity, pp.average_price, pp.total_invested,
         a.ticker, a.name, a.class AS asset_class, a.sector, a.currency,
         aq.close AS current_price
  FROM   portfolio_positions pp
  JOIN   assets a ON a.id = pp.asset_id
  LEFT JOIN LATERAL (
      SELECT close FROM asset_quotes
      WHERE  asset_id = pp.asset_id
      ORDER  BY timestamp DESC LIMIT 1
  ) aq ON true
  WHERE  pp.user_id = :uid
  ORDER  BY pp.total_invested DESC

Chave "data_source" sempre presente no dict retornado:
  "real"          → dados do banco, tudo OK
  "mock"          → MOCK_MODE=true, mock intencional
  "mock_fallback" → banco falhou, caiu no mock automaticamente

Schema do dict retornado por get_carteira():
  data_source              str   "real" | "mock" | "mock_fallback"
  total_investido          float  Custo histórico total (qty × avg_price)
  total_mercado            float  Valor de mercado (= total_investido sem cotações)
  rentabilidade_total_pct  float  Rentabilidade total (0.0 sem cotações)
  num_ativos               int    Número de posições ativas
  cotacoes_disponiveis     bool   True quando asset_quotes estiver populado
  posicoes                 list   Ver _POSICAO_SCHEMA abaixo
  por_classe               list   Ver _CLASSE_SCHEMA abaixo
  por_setor                list   Ver _SETOR_SCHEMA abaixo

_POSICAO_SCHEMA: { ticker, nome, classe, setor, moeda,
                   quantidade, preco_medio, total_investido,
                   preco_atual, valor_mercado, rentab_pct, pct_carteira, cor }

_CLASSE_SCHEMA:  { nome, valor_mercado, total_investido,
                   pct_carteira, num_ativos, rentab_pct, cor }

_SETOR_SCHEMA:   { nome, valor_mercado, pct_carteira }
"""
import logging
from collections import defaultdict

import streamlit as st

from core.config import settings

logger = logging.getLogger(__name__)

# ── Mapeamentos de classe de ativo ────────────────────────────────────────────
_CLASS_LABEL: dict[str, str] = {
    "reit":         "FII",
    "stock":        "Ações BR",
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

# Posições mock baseadas nas 34 posições reais do banco (subconjunto de 20)
# Formato: (ticker, nome, classe, setor, moeda, qty, preco_medio, total_investido)
_MOCK_POSICOES_RAW: list[tuple] = [
    ("BITH11",  "BTG Pactual Infra FII",         "reit",  "real_estate", "BRL", 190,    137.87, 26_195.40),
    ("PSSA3",   "Porto Seguro",                   "stock", "financials",  "BRL", 643,     35.15, 22_598.69),
    ("EQTL3F",  "Equatorial Energia",             "stock", "utilities",   "BRL", 591,     24.25, 14_329.72),
    ("MXRF15",  "Maxi Renda FII",                 "stock", "real_estate", "BRL", 1352,    10.29, 13_912.08),
    ("BBAS3F",  "Banco do Brasil",                "stock", "financials",  "BRL", 339,     33.30, 11_289.32),
    ("BRCO11",  "Bresco Logística FII",           "reit",  "real_estate", "BRL",  60,    117.52,  7_051.36),
    ("HGLG11",  "CSHG Logística FII",             "reit",  "real_estate", "BRL",  44,    157.06,  6_910.78),
    ("KNCR11",  "Kinea CR FII",                   "reit",  "real_estate", "BRL",  64,    106.25,  6_799.76),
    ("VISC11",  "Vinci Shopping Centers FII",     "reit",  "real_estate", "BRL",  62,    108.85,  6_748.58),
    ("PETR3F",  "Petrobras PN",                   "stock", "energy",      "BRL", 230,     28.78,  6_620.29),
    ("GMAT3",   "Getnet S.A.",                    "stock", "financials",  "BRL", 4600,     1.40,  6_448.42),
    ("TRPL3F",  "CTEEP",                          "stock", "utilities",   "BRL", 200,     29.19,  5_838.32),
    ("ROMI3",   "Romi S.A.",                      "stock", "industrials", "BRL", 602,      8.38,  5_042.49),
    ("ITUB3F",  "Itaú Unibanco",                  "stock", "financials",  "BRL", 174,     25.37,  4_414.29),
    ("IRDM11",  "Iridium Recebíveis CRI FII",     "reit",  "real_estate", "BRL",  69,     57.67,  3_979.03),
    ("SBSP3",   "Sabesp",                         "stock", "utilities",   "BRL", 118,     33.87,  3_997.22),
    ("CSMG3F",  "COPASA",                         "stock", "utilities",   "BRL",  94,     40.83,  3_837.85),
    ("BRAP3",   "Bradespar",                      "stock", "materials",   "BRL", 206,     18.47,  3_804.64),
    ("ISAE3",   "Isa Energia",                    "stock", "utilities",   "BRL", 102,     33.15,  3_381.20),
    ("BRAP3F",  "Bradespar PN",                   "stock", "materials",   "BRL", 206,     18.47,  3_804.64),
]


# ─────────────────────────────────────────────────────────────────────────────
# API pública
# ─────────────────────────────────────────────────────────────────────────────

@st.cache_data(ttl=300)
def get_carteira() -> dict:
    """
    Retorna o dicionário completo de dados para a página Carteira.
    Cache de 5 minutos — mesma estratégia do get_visao_geral().

    Retorna dados mockados se MOCK_MODE=true.
    Tenta banco real se MOCK_MODE=false; em caso de falha usa mock + "mock_fallback".
    """
    if settings.MOCK_MODE:
        dados = _carteira_mock()
        dados["data_source"] = "mock"
        return dados

    try:
        dados = _carteira_real()
        dados["data_source"] = "real"
        return dados
    except Exception as exc:
        logger.warning(
            "[investimentos] Banco real falhou (%s: %s) — usando mock.",
            type(exc).__name__,
            str(exc)[:120],
        )
        dados = _carteira_mock()
        dados["data_source"] = "mock_fallback"
        return dados


# ─────────────────────────────────────────────────────────────────────────────
# MOCK
# ─────────────────────────────────────────────────────────────────────────────

def _carteira_mock() -> dict:
    """Constrói o dict de carteira a partir de dados mock estáticos."""
    total_inv = sum(r[7] for r in _MOCK_POSICOES_RAW)

    posicoes = []
    for row in _MOCK_POSICOES_RAW:
        ticker, nome, classe_raw, setor_raw, moeda, qty, pm, ti = row
        pct = round(ti / total_inv * 100, 2) if total_inv > 0 else 0.0
        posicoes.append({
            "ticker":          ticker,
            "nome":            nome,
            "classe":          _CLASS_LABEL.get(classe_raw, classe_raw.title()),
            "setor":           _SETOR_LABEL.get(setor_raw, setor_raw.title()),
            "moeda":           moeda,
            "quantidade":      float(qty),
            "preco_medio":     round(float(pm), 6),
            "total_investido": round(float(ti), 2),
            "preco_atual":     round(float(pm), 6),    # sem cotações = preço médio
            "valor_mercado":   round(float(ti), 2),    # sem cotações = custo histórico
            "rentab_pct":      0.0,
            "pct_carteira":    pct,
            "cor":             _CLASS_COR.get(classe_raw, "#718096"),
        })

    return {
        "total_investido":         round(total_inv, 2),
        "total_mercado":           round(total_inv, 2),
        "rentabilidade_total_pct": 0.0,
        "num_ativos":              len(posicoes),
        "cotacoes_disponiveis":    False,
        "posicoes":                posicoes,
        "por_classe":              _agregar_por_classe(posicoes),
        "por_setor":               _agregar_por_setor(posicoes),
        # data_source injetado pelo caller
    }


# ─────────────────────────────────────────────────────────────────────────────
# REAL — queries nas tabelas do Supabase
# ─────────────────────────────────────────────────────────────────────────────

# SQL isolado para facilitar manutenção e testes
_SQL_POSICOES = """
    SELECT
        pp.quantity,
        pp.average_price,
        pp.total_invested,
        a.ticker,
        a.name          AS asset_name,
        a.class         AS asset_class,
        a.sector,
        a.currency,
        aq.close        AS current_price
    FROM   portfolio_positions pp
    JOIN   assets a ON a.id = pp.asset_id
    LEFT JOIN LATERAL (
        SELECT close
        FROM   asset_quotes
        WHERE  asset_id = pp.asset_id
        ORDER  BY timestamp DESC
        LIMIT  1
    ) aq ON true
    WHERE  pp.user_id = :uid
    ORDER  BY pp.total_invested DESC
"""


def _carteira_real() -> dict:
    """
    Consulta portfolio_positions + assets + asset_quotes (LATERAL) e monta o dict.
    Retorna o mesmo schema que _carteira_mock().

    Lança RuntimeError em qualquer problema (engine ausente, sem dados, etc.).
    O caller (get_carteira) captura e faz fallback para mock.

    SEGURANÇA:
      - Sem credenciais no código.
      - Todas as queries filtradas por OWNER_USER_ID.
      - Somente SELECT — sem DDL nem DML de escrita.
    """
    from sqlalchemy import text
    from core.database import get_engine

    engine = get_engine()
    if engine is None:
        raise RuntimeError(
            "Engine indisponível — configure SUPABASE_UNIFICADO_URL "
            "no .env local ou em Streamlit Secrets."
        )

    owner = settings.OWNER_USER_ID
    if not owner:
        raise RuntimeError("OWNER_USER_ID não configurado — filtro de usuário inativo.")

    with engine.connect() as conn:
        rows = conn.execute(text(_SQL_POSICOES), {"uid": owner}).fetchall()

    if not rows:
        raise RuntimeError(
            "Nenhuma posição encontrada em portfolio_positions para este usuário. "
            "Verifique OWNER_USER_ID e confirme que 08_compute_portfolio_positions.py foi executado."
        )

    def _f(v) -> float:
        return float(v) if v is not None else 0.0

    cotacoes_disponiveis = any(r.current_price is not None for r in rows)

    total_investido = 0.0
    total_mercado   = 0.0
    posicoes        = []

    for r in rows:
        qty    = _f(r.quantity)
        pm     = _f(r.average_price)
        ti     = _f(r.total_invested)
        preco_atual = _f(r.current_price) if r.current_price is not None else pm
        vm     = round(qty * preco_atual, 2)
        rentab = round((vm - ti) / ti * 100, 2) if ti > 0 else 0.0

        classe_raw = r.asset_class or "other"
        setor_raw  = r.sector or "other"

        total_investido += ti
        total_mercado   += vm

        posicoes.append({
            "ticker":          r.ticker,
            "nome":            r.asset_name,
            "classe":          _CLASS_LABEL.get(classe_raw, classe_raw.title()),
            "setor":           _SETOR_LABEL.get(setor_raw, setor_raw.title()),
            "moeda":           r.currency or "BRL",
            "quantidade":      qty,
            "preco_medio":     pm,
            "total_investido": ti,
            "preco_atual":     preco_atual,
            "valor_mercado":   vm,
            "rentab_pct":      rentab,
            "pct_carteira":    0.0,    # calculado abaixo (requer total_mercado final)
            "cor":             _CLASS_COR.get(classe_raw, "#718096"),
        })

    # Preenche pct_carteira com base no total_mercado consolidado
    base = total_mercado if total_mercado > 0 else total_investido
    for p in posicoes:
        p["pct_carteira"] = round(p["valor_mercado"] / base * 100, 2) if base > 0 else 0.0

    rentabilidade_total = round(
        (total_mercado - total_investido) / total_investido * 100, 2
    ) if total_investido > 0 else 0.0

    return {
        "total_investido":         round(total_investido, 2),
        "total_mercado":           round(total_mercado, 2),
        "rentabilidade_total_pct": rentabilidade_total,
        "num_ativos":              len(posicoes),
        "cotacoes_disponiveis":    cotacoes_disponiveis,
        "posicoes":                posicoes,
        "por_classe":              _agregar_por_classe(posicoes),
        "por_setor":               _agregar_por_setor(posicoes),
        # data_source injetado pelo caller
    }


# ─────────────────────────────────────────────────────────────────────────────
# Helpers de agregação (sem dependência de fonte de dados)
# ─────────────────────────────────────────────────────────────────────────────

def _agregar_por_classe(posicoes: list) -> list:
    """
    Agrega posições por classe de ativo.
    Retorna lista ordenada por valor_mercado DESC.
    """
    buckets: dict[str, dict] = {}
    for p in posicoes:
        cls = p["classe"]
        if cls not in buckets:
            buckets[cls] = {
                "nome":            cls,
                "valor_mercado":   0.0,
                "total_investido": 0.0,
                "num_ativos":      0,
                "cor":             p["cor"],
            }
        buckets[cls]["valor_mercado"]   += p["valor_mercado"]
        buckets[cls]["total_investido"] += p["total_investido"]
        buckets[cls]["num_ativos"]      += 1

    total = sum(b["valor_mercado"] for b in buckets.values()) or 1.0
    resultado = []
    for b in sorted(buckets.values(), key=lambda x: x["valor_mercado"], reverse=True):
        ti = b["total_investido"]
        vm = b["valor_mercado"]
        rentab = round((vm - ti) / ti * 100, 2) if ti > 0 else 0.0
        resultado.append({
            **b,
            "pct_carteira": round(vm / total * 100, 1),
            "rentab_pct":   rentab,
        })
    return resultado


# ─────────────────────────────────────────────────────────────────────────────
# API pública — cashflow mensal (para página Investimentos)
# ─────────────────────────────────────────────────────────────────────────────

_MESES_PT_CF = {
    1: "Jan", 2: "Fev", 3: "Mar", 4: "Abr",
    5: "Mai", 6: "Jun", 7: "Jul", 8: "Ago",
    9: "Set", 10: "Out", 11: "Nov", 12: "Dez",
}

_SQL_CASHFLOW = """
    SELECT month_year, total_income, total_expenses_abs, net_cashflow
    FROM   v_monthly_cashflow
    WHERE  user_id = :uid
    ORDER  BY month_year DESC
    LIMIT  12
"""


@st.cache_data(ttl=300)
def get_cashflow_mensal() -> list:
    """
    Retorna os últimos 12 meses de cashflow em ordem cronológica.
    Cada item: {label, ano, mes, receitas, despesas, saldo}
    Uso: gráfico de barras na página Investimentos.
    """
    if settings.MOCK_MODE:
        return _cashflow_mock()
    try:
        return _cashflow_real()
    except Exception as exc:
        logger.warning("[investimentos] cashflow falhou (%s) — mock.", exc)
        return _cashflow_mock()


def _cashflow_mock() -> list:
    from datetime import date as _date
    hoje = _date.today()
    result = []
    for i in range(11, -1, -1):
        m = hoje.month - i
        y = hoje.year
        while m <= 0:
            m += 12
            y -= 1
        # Simula variação realista de cashflow
        receitas = 8_500.0 + (i % 3) * 250.0
        despesas = 3_400.0 + (i % 5) * 280.0
        result.append({
            "label":    f"{_MESES_PT_CF[m]}/{str(y)[-2:]}",
            "ano":      y,
            "mes":      m,
            "receitas": round(receitas, 2),
            "despesas": round(despesas, 2),
            "saldo":    round(receitas - despesas, 2),
        })
    return result


def _cashflow_real() -> list:
    from sqlalchemy import text
    from core.database import get_engine

    engine = get_engine()
    if engine is None:
        raise RuntimeError("Engine indisponível.")

    owner = settings.OWNER_USER_ID
    if not owner:
        raise RuntimeError("OWNER_USER_ID não configurado.")

    with engine.connect() as conn:
        rows = conn.execute(text(_SQL_CASHFLOW), {"uid": owner}).fetchall()

    result = []
    for r in reversed(rows):   # reversed → cronológico (mais antigo primeiro)
        my = r.month_year
        result.append({
            "label":    f"{_MESES_PT_CF[my.month]}/{str(my.year)[-2:]}",
            "ano":      my.year,
            "mes":      my.month,
            "receitas": float(r.total_income or 0),
            "despesas": float(r.total_expenses_abs or 0),
            "saldo":    float(r.net_cashflow or 0),
        })
    return result


def _agregar_por_setor(posicoes: list) -> list:
    """
    Agrega posições por setor.
    Retorna lista ordenada por valor_mercado DESC.
    """
    buckets: dict[str, float] = defaultdict(float)
    for p in posicoes:
        buckets[p["setor"]] += p["valor_mercado"]

    total = sum(buckets.values()) or 1.0
    return sorted(
        [
            {
                "nome":           setor,
                "valor_mercado":  round(vm, 2),
                "pct_carteira":   round(vm / total * 100, 1),
            }
            for setor, vm in buckets.items()
        ],
        key=lambda x: x["valor_mercado"],
        reverse=True,
    )


# ─────────────────────────────────────────────────────────────────────────────
# API pública — evolução patrimonial histórica
# ─────────────────────────────────────────────────────────────────────────────

_SQL_EVOLUCAO_TX = """
    SELECT
        DATE_TRUNC('month', transaction_date) AS mes,
        SUM(CASE WHEN type = 'buy'  THEN  quantity * unit_price
                 WHEN type = 'sell' THEN -(quantity * unit_price)
                 ELSE 0
        END) AS delta_investido
    FROM investment_transactions
    WHERE user_id = :uid
    GROUP BY 1
    ORDER BY 1
"""

_SQL_EVOLUCAO_DIV = """
    SELECT
        DATE_TRUNC('month', payment_date) AS mes,
        SUM(total_amount) AS delta_dividendos
    FROM dividends
    WHERE user_id = :uid
      AND payment_date IS NOT NULL
    GROUP BY 1
    ORDER BY 1
"""

_SQL_EVOLUCAO_RATIO = """
    SELECT
        COALESCE(SUM(pp.total_invested), 1) AS total_inv,
        COALESCE(SUM(pp.quantity * COALESCE(aq.close, pp.average_price)), 1) AS total_mkt
    FROM portfolio_positions pp
    LEFT JOIN LATERAL (
        SELECT close FROM asset_quotes WHERE asset_id = pp.asset_id
        ORDER BY timestamp DESC LIMIT 1
    ) aq ON true
    WHERE pp.user_id = :uid
"""


@st.cache_data(ttl=300)
def get_evolucao_patrimonial() -> dict:
    """
    Retorna série histórica mensal para o gráfico de Evolução Patrimonial.
    Schema: {data_source, snapshots, total_investido, total_mercado, total_dividendos}
    Cada snapshot: {label, mes_str, valor_investido, valor_mercado, valor_com_dividendos}
    valor_mercado é estimado: cum_investido × (total_mercado_atual / total_investido_atual).
    """
    if settings.MOCK_MODE:
        d = _evolucao_mock()
        d["data_source"] = "mock"
        return d
    try:
        d = _evolucao_real()
        d["data_source"] = "real"
        return d
    except Exception as exc:
        logger.warning("[investimentos] evolução falhou (%s) — mock.", exc)
        d = _evolucao_mock()
        d["data_source"] = "mock_fallback"
        return d


def _evolucao_real() -> dict:
    from sqlalchemy import text
    from core.database import get_engine

    engine = get_engine()
    if engine is None:
        raise RuntimeError("Engine indisponível.")
    owner = settings.OWNER_USER_ID
    if not owner:
        raise RuntimeError("OWNER_USER_ID não configurado.")

    with engine.connect() as conn:
        tx_rows   = conn.execute(text(_SQL_EVOLUCAO_TX),    {"uid": owner}).fetchall()
        div_rows  = conn.execute(text(_SQL_EVOLUCAO_DIV),   {"uid": owner}).fetchall()
        ratio_row = conn.execute(text(_SQL_EVOLUCAO_RATIO), {"uid": owner}).fetchone()

    total_inv_atual = float(ratio_row.total_inv or 1)
    total_mkt_atual = float(ratio_row.total_mkt or total_inv_atual)
    rentab_ratio    = total_mkt_atual / total_inv_atual

    tx_map  = {r.mes: float(r.delta_investido  or 0) for r in tx_rows}
    div_map = {r.mes: float(r.delta_dividendos or 0) for r in div_rows}

    all_months = sorted(set(tx_map) | set(div_map))
    if not all_months:
        raise RuntimeError("Sem transações de investimento.")

    cum_inv = 0.0
    cum_div = 0.0
    snapshots    = []
    fluxo_mensal = []
    for mes in all_months:
        delta    = tx_map.get(mes, 0.0)
        cum_inv += delta
        cum_div += div_map.get(mes, 0.0)
        cum_mkt  = round(max(cum_inv, 0) * rentab_ratio, 2)
        label    = f"{_MESES_PT_CF[mes.month]}/{str(mes.year)[-2:]}"
        mes_str  = mes.strftime("%Y-%m")
        snapshots.append({
            "label":               label,
            "mes_str":             mes_str,
            "valor_investido":     round(cum_inv, 2),
            "valor_mercado":       cum_mkt,
            "valor_com_dividendos": round(cum_mkt + cum_div, 2),
        })
        fluxo_mensal.append({
            "label":   label,
            "mes_str": mes_str,
            "aporte":  round(delta, 2),
            "ano":     mes.year,
            "mes":     mes.month,
        })

    return {
        "snapshots":        snapshots,
        "fluxo_mensal":     fluxo_mensal,
        "total_investido":  round(cum_inv, 2),
        "total_mercado":    round(total_mkt_atual, 2),
        "total_dividendos": round(cum_div, 2),
    }


def _evolucao_mock() -> dict:
    from datetime import date as _date
    hoje  = _date.today()
    start = hoje.year - 4

    cum_inv = 0.0
    cum_div = 0.0
    snapshots    = []
    fluxo_mensal = []
    for yr in range(start, hoje.year + 1):
        for mo in range(1, 13):
            if yr == hoje.year and mo > hoje.month:
                break
            age_frac = ((yr - start) * 12 + mo) / (4 * 12)
            delta    = 3_200.0 + (mo % 4) * 450.0
            cum_inv += delta
            cum_div += 320.0 + (mo % 3) * 110.0
            cum_mkt  = round(cum_inv * (1.0 + 0.18 * age_frac), 2)
            label    = f"{_MESES_PT_CF[mo]}/{str(yr)[-2:]}"
            mes_str  = f"{yr}-{mo:02d}"
            snapshots.append({
                "label":               label,
                "mes_str":             mes_str,
                "valor_investido":     round(cum_inv, 2),
                "valor_mercado":       cum_mkt,
                "valor_com_dividendos": round(cum_mkt + cum_div, 2),
            })
            fluxo_mensal.append({
                "label":   label,
                "mes_str": mes_str,
                "aporte":  round(delta, 2),
                "ano":     yr,
                "mes":     mo,
            })

    return {
        "snapshots":        snapshots,
        "fluxo_mensal":     fluxo_mensal,
        "total_investido":  round(cum_inv, 2),
        "total_mercado":    round(snapshots[-1]["valor_mercado"], 2) if snapshots else 0.0,
        "total_dividendos": round(cum_div, 2),
    }
