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
    "fii":          "FII",
    "stock":        "Ações BR",
    "fixed_income": "Renda Fixa",
    "renda_fixa":   "Renda Fixa",
    "tesouro":      "Tesouro Direto",
    "fundo_rf":     "Fundo RF",
    "etf":          "ETF",
    "etf_br":       "ETF Brasil",
    "etf_intl":     "ETF Internacional",
    "bdr":          "BDR",
    "crypto":       "Cripto",
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

# SQL isolado para facilitar manutenção e testes.
# fx.close traz a cotação USD/BRL mais recente do ativo sintético USDBRL
# populado por `data_pipeline/jobs/update_fx_rates.py`. Quando o ativo
# da posição é em USD, a camada Python multiplica os valores por essa
# cotação. Quando o ativo USDBRL ainda não existe (banco novo), o LEFT JOIN
# devolve NULL e o Python usa rate=1.0 (sem conversão).
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
        aq.close        AS current_price,
        fx.close        AS usd_brl_rate
    FROM   portfolio_positions pp
    JOIN   assets a ON a.id = pp.asset_id
    LEFT JOIN LATERAL (
        SELECT close
        FROM   asset_quotes
        WHERE  asset_id = pp.asset_id
        ORDER  BY timestamp DESC
        LIMIT  1
    ) aq ON true
    LEFT JOIN LATERAL (
        SELECT aq2.close
        FROM   asset_quotes aq2
        JOIN   assets a2 ON a2.id = aq2.asset_id
        WHERE  a2.ticker = 'USDBRL'
        ORDER  BY aq2.timestamp DESC
        LIMIT  1
    ) fx ON true
    WHERE  pp.user_id = :uid
    ORDER  BY pp.total_invested DESC
"""

_SQL_POSICOES_SNAPSHOT = """
    WITH latest_source AS (
        SELECT source_system, source_table, MAX(report_date) AS report_date
        FROM portfolio_position_snapshots
        WHERE user_id = :uid
        GROUP BY source_system, source_table
    )
    SELECT
        pps.quantity,
        pps.market_price,
        pps.market_value,
        pps.invested_value,
        pps.asset_type,
        pps.asset_name,
        pps.currency,
        pps.country,
        a.ticker,
        a.class AS asset_class,
        a.sector
    FROM portfolio_position_snapshots pps
    JOIN latest_source ls
      ON ls.source_system = pps.source_system
     AND ls.source_table = pps.source_table
     AND ls.report_date = pps.report_date
    JOIN assets a ON a.id = pps.asset_id
    WHERE pps.user_id = :uid
    ORDER BY pps.market_value DESC
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
        has_snapshots = bool(conn.execute(text("""
            SELECT EXISTS (
                SELECT 1
                FROM information_schema.tables
                WHERE table_schema = 'public'
                  AND table_name = 'portfolio_position_snapshots'
            )
        """)).scalar())
        if has_snapshots:
            rows = conn.execute(text(_SQL_POSICOES_SNAPSHOT), {"uid": owner}).fetchall()
            if rows:
                return _montar_carteira_snapshot(rows)
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

        # Conversão USD → BRL para posições internacionais (Nomad, BDR USD).
        # fx_rate vem do ativo sintético USDBRL (LATERAL JOIN no SQL acima).
        # Sem cotação USD/BRL no banco ainda, mantém rate=1.0 e exibe em USD.
        ccy = (r.currency or "BRL").upper()
        fx_rate = _f(getattr(r, "usd_brl_rate", None)) or 1.0
        if ccy == "USD" and fx_rate > 0:
            preco_atual *= fx_rate
            pm *= fx_rate
            ti *= fx_rate

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
            "moeda":           ccy,
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


def _base_ticker(ticker: str) -> str:
    t = (ticker or "").upper().strip()
    if t.endswith("11F"):
        return t[:-1]
    if t.endswith("F") and len(t) > 4:
        return t[:-1]
    return t


def _class_key_from_snapshot(raw_type: str | None, ticker: str, country: str | None) -> str:
    raw = (raw_type or "").strip().lower()
    t = (ticker or "").upper().strip()
    c = (country or "BR").upper().strip()
    if c not in ("", "BR"):
        return "etf_intl" if raw == "etf" else raw or "other"
    if raw == "tesouro":
        return "tesouro"
    if raw in {"renda_fixa", "fixed_income"}:
        if t.startswith(("CDB", "LCI", "LCA", "CRI", "CRA")):
            return "renda_fixa"
        return "fundo_rf"
    if raw == "fii":
        return "fii"
    if raw == "etf":
        return "etf_br"
    return raw or "other"


def _montar_carteira_snapshot(rows: list) -> dict:
    """Monta a carteira real a partir do ultimo snapshot XP, como o app isolado."""
    total_investido = 0.0
    total_mercado = 0.0
    posicoes = []

    for r in rows:
        qty = float(r.quantity or 0)
        vm = float(r.market_value or 0)
        if qty <= 0 or vm <= 0:
            continue

        ticker = _base_ticker(r.ticker)
        classe_raw = _class_key_from_snapshot(r.asset_type, ticker, r.country)
        # O relatorio XP e a fonte mais fiel para classes sem transacao granular
        # no App4 (Tesouro, CDBs, fundos). Quando nao informa custo, usa mercado.
        ti = float(r.invested_value or 0) or vm
        preco_atual = float(r.market_price or 0) or (vm / qty if qty > 0 else 0.0)
        preco_medio = ti / qty if qty > 0 else 0.0
        rentab = round((vm - ti) / ti * 100, 2) if ti > 0 else 0.0

        total_investido += ti
        total_mercado += vm
        posicoes.append({
            "ticker":          ticker,
            "nome":            r.asset_name or ticker,
            "classe":          _CLASS_LABEL.get(classe_raw, classe_raw.title()),
            "setor":           _SETOR_LABEL.get(r.sector or "other", (r.sector or "other").title()),
            "moeda":           r.currency or "BRL",
            "pais":            r.country or "BR",
            "quantidade":      qty,
            "preco_medio":     round(preco_medio, 6),
            "total_investido": round(ti, 2),
            "preco_atual":     round(preco_atual, 6),
            "valor_mercado":   round(vm, 2),
            "rentab_pct":      rentab,
            "pct_carteira":    0.0,
            "cor":             _CLASS_COR.get(classe_raw, "#718096"),
        })

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
        "cotacoes_disponiveis":    True,
        "posicoes":                posicoes,
        "por_classe":              _agregar_por_classe(posicoes),
        "por_setor":               _agregar_por_setor(posicoes),
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

_SQL_INVEST_MENSAL = """
    SELECT
        DATE_TRUNC('month', t.due_date)::DATE AS month_year,
        SUM(ABS(t.amount))                    AS total_inv
    FROM transactions t
    JOIN categories c ON c.id = t.category_id
    WHERE t.user_id = :uid
      AND t.status  = 'settled'
      AND (
          t.type = 'investment'
          OR (t.type = 'transfer'
              AND c.name IN (
                  'Renda Fixa', 'Renda Variavel', 'Renda Variável',
                  'Exterior', 'Aporte em Investimento'
              ))
      )
    GROUP BY DATE_TRUNC('month', t.due_date)::DATE
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
        receitas      = 8_500.0 + (i % 3) * 250.0
        despesas      = 3_400.0 + (i % 5) * 280.0
        investimentos = 5_000.0 + (i % 4) * 500.0
        result.append({
            "label":         f"{_MESES_PT_CF[m]}/{str(y)[-2:]}",
            "ano":           y,
            "mes":           m,
            "receitas":      round(receitas, 2),
            "despesas":      round(despesas, 2),
            "saldo":         round(receitas - despesas, 2),
            "investimentos": round(investimentos, 2),
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
        rows     = conn.execute(text(_SQL_CASHFLOW),        {"uid": owner}).fetchall()
        inv_rows = conn.execute(text(_SQL_INVEST_MENSAL),   {"uid": owner}).fetchall()

    from datetime import date as _date
    inv_by_month: dict[_date, float] = {
        r.month_year: float(r.total_inv or 0) for r in inv_rows
    }

    result = []
    for r in reversed(rows):
        my  = r.month_year
        result.append({
            "label":         f"{_MESES_PT_CF[my.month]}/{str(my.year)[-2:]}",
            "ano":           my.year,
            "mes":           my.month,
            "receitas":      float(r.total_income or 0),
            "despesas":      float(r.total_expenses_abs or 0),
            "saldo":         float(r.net_cashflow or 0),
            "investimentos": round(inv_by_month.get(my, 0.0), 2),
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

_SQL_EVOLUCAO_SNAPSHOTS = """
    WITH xp_snaps AS (
        SELECT
            report_date,
            SUM(market_value)              AS vm,
            SUM(COALESCE(invested_value, 0)) AS vi
        FROM portfolio_position_snapshots
        WHERE user_id = :uid AND source_system = 'app2'
        GROUP BY report_date
    ),
    other_latest AS (
        SELECT
            SUM(market_value)              AS vm,
            SUM(COALESCE(invested_value, 0)) AS vi
        FROM portfolio_position_snapshots
        WHERE user_id = :uid
          AND source_system != 'app2'
          AND report_date = (
              SELECT MAX(report_date)
              FROM portfolio_position_snapshots
              WHERE user_id = :uid AND source_system != 'app2'
          )
    ),
    latest_xp_date AS (
        SELECT MAX(report_date) AS d FROM xp_snaps
    )
    SELECT
        x.report_date AS mes,
        x.vm + CASE WHEN x.report_date = l.d
                    THEN COALESCE(o.vm, 0)
                    ELSE 0
               END AS valor_mercado,
        x.vi + CASE WHEN x.report_date = l.d
                    THEN COALESCE(o.vi, 0)
                    ELSE 0
               END AS valor_investido_snapshot
    FROM xp_snaps x
    CROSS JOIN (SELECT COALESCE(vm, 0) AS vm, COALESCE(vi, 0) AS vi FROM other_latest) o
    CROSS JOIN latest_xp_date l
    ORDER BY x.report_date
"""


@st.cache_data(ttl=300)
def get_evolucao_patrimonial() -> dict:
    """
    Retorna série histórica mensal para o gráfico de Evolução Patrimonial.
    Schema: {data_source, snapshots, total_investido, total_mercado, total_dividendos}
    Cada snapshot: {label, mes_str, valor_investido, valor_mercado, valor_com_dividendos}

    Quando portfolio_position_snapshots existir (caminho preferencial):
      - Usa snapshots XP (source_system='app2') como série histórica.
      - Para o snapshot mais recente, soma automaticamente os valores
        de outras fontes (Nomad etc.) ao invés de criar pontos separados.
    Fallback: investment_transactions + ratio de rentabilidade atual.
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
        has_snapshots = bool(conn.execute(text("""
            SELECT EXISTS (
                SELECT 1
                FROM information_schema.tables
                WHERE table_schema = 'public'
                  AND table_name = 'portfolio_position_snapshots'
            )
        """)).scalar())
        if has_snapshots:
            snap_rows = conn.execute(text(_SQL_EVOLUCAO_SNAPSHOTS), {"uid": owner}).fetchall()
            if snap_rows:
                div_rows = conn.execute(text(_SQL_EVOLUCAO_DIV), {"uid": owner}).fetchall()
                return _montar_evolucao_snapshot(snap_rows, div_rows)
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


def _montar_evolucao_snapshot(snap_rows: list, div_rows: list) -> dict:
    div_map = {r.mes: float(r.delta_dividendos or 0) for r in div_rows}
    snapshots = []
    fluxo_mensal = []
    cum_div = 0.0
    prev_value = None

    for r in snap_rows:
        mes = r.mes
        vm = float(r.valor_mercado or 0)
        cum_div += sum(
            total for data, total in div_map.items()
            if data.year == mes.year and data.month == mes.month
        )
        label = f"{_MESES_PT_CF[mes.month]}/{str(mes.year)[-2:]}"
        mes_str = mes.strftime("%Y-%m")
        snapshots.append({
            "label":               label,
            "mes_str":             mes_str,
            "valor_investido":     round(float(r.valor_investido_snapshot or 0), 2),
            "valor_mercado":       round(vm, 2),
            "valor_com_dividendos": round(vm + cum_div, 2),
        })
        fluxo_mensal.append({
            "label":   label,
            "mes_str": mes_str,
            "aporte":  round(vm - prev_value, 2) if prev_value is not None else round(vm, 2),
            "ano":     mes.year,
            "mes":     mes.month,
        })
        prev_value = vm

    latest = snapshots[-1] if snapshots else {}
    return {
        "snapshots":        snapshots,
        "fluxo_mensal":     fluxo_mensal,
        "total_investido":  latest.get("valor_investido", 0.0),
        "total_mercado":    latest.get("valor_mercado", 0.0),
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
