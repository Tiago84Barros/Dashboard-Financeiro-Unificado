"""
core/controle.py
Camada de serviço do controle financeiro — transações, categorias e orçamentos.

Padrão idêntico aos demais módulos core/:
  MOCK_MODE=true  → retorna dados mockados
  MOCK_MODE=false → tenta banco real; fallback para mock em qualquer erro

Tabelas consultadas (Fase 5.4):
  transactions  — receitas, despesas e transferências
  categories    — nome e tipo de cada categoria
  accounts      — nome das contas
  budgets       — limites de orçamento por categoria/mês (pode estar vazio)

Operações de escrita (Fase 5.4):
  inserir_transacao()  — INSERT em transactions com user_id obrigatório
  Uso de engine.begin() para transações atômicas.

Estratégia de mês:
  get_controle(ano, mes) — parâmetros para cache por mês.
  Quando não informados, usa o mês corrente.

Schema do dict retornado por get_controle():
  data_source         str
  mes_referencia      str   "Mai 2026"
  receitas            float
  despesas            float
  saldo_mes           float
  taxa_poupanca_pct   float
  num_transacoes      int
  categorias          list  [{nome, gasto, orcamento, pct_usado, tipo_badge}]
  transacoes          list  [{id, descricao, valor, valor_fmt, data, data_fmt,
                              tipo, status, categoria, conta, eh_receita}]

Schema do dict retornado por get_opcoes_formulario():
  categorias  list  [{id, nome, tipo}]
  contas      list  [{id, nome}]
"""
import logging
from datetime import date as _date
from typing import Optional

import streamlit as st

from core.config import settings

logger = logging.getLogger(__name__)

_MESES_PT = {
    1: "Jan", 2: "Fev", 3: "Mar", 4: "Abr",
    5: "Mai", 6: "Jun", 7: "Jul", 8: "Ago",
    9: "Set", 10: "Out", 11: "Nov", 12: "Dez",
}

# ── Mock ──────────────────────────────────────────────────────────────────────
_MOCK_CATS = [
    ("Moradia",      1_250.00, 1_500.00),
    ("Alimentação",    890.00, 1_000.00),
    ("Transporte",     410.00,   500.00),
    ("Saúde",          230.00,   400.00),
    ("Lazer",          280.00,   300.00),
    ("Assinaturas",    140.00,   150.00),
    ("Educação",       200.00,   250.00),
]

_MOCK_TRANS = [
    # (descricao, valor, tipo, data, categoria, conta)
    ("Salário",              8_500.00, "income",   "2026-05-05", "Salário",     "Conta Corrente"),
    ("Aluguel",             -1_250.00, "expense",  "2026-05-05", "Moradia",     "Conta Corrente"),
    ("Supermercado Extra",    -320.00, "expense",  "2026-05-07", "Alimentação", "Conta Corrente"),
    ("Posto Combustível",     -180.00, "expense",  "2026-05-08", "Transporte",  "Conta Corrente"),
    ("Netflix / Spotify",      -99.00, "expense",  "2026-05-10", "Assinaturas", "Conta Corrente"),
    ("Farmácia",               -85.00, "expense",  "2026-05-11", "Saúde",       "Conta Corrente"),
    ("Restaurante",           -145.00, "expense",  "2026-05-12", "Lazer",       "Conta Corrente"),
    ("Curso Python",          -200.00, "expense",  "2026-05-14", "Educação",    "Conta Corrente"),
    ("Supermercado",          -240.00, "expense",  "2026-05-14", "Alimentação", "Conta Corrente"),
    ("Transferência recebida",  750.00, "income",  "2026-05-14", "Outros",      "Conta Corrente"),
]

# ── SQL ───────────────────────────────────────────────────────────────────────
_SQL_TRANSACOES = """
    SELECT
        t.id::text,
        t.description,
        t.amount,
        t.due_date,
        t.payment_date,
        t.type,
        t.status,
        t.recurring,
        COALESCE(c.name, 'Sem categoria') AS category_name,
        COALESCE(ac.name, 'Sem conta')    AS account_name
    FROM   transactions t
    LEFT JOIN categories c  ON c.id  = t.category_id
    LEFT JOIN accounts   ac ON ac.id = t.account_id
    WHERE  t.user_id = :uid
      AND  EXTRACT(year  FROM t.due_date) = :ano
      AND  EXTRACT(month FROM t.due_date) = :mes
    ORDER  BY t.due_date DESC, t.created_at DESC
"""

_SQL_ORCAMENTOS = """
    SELECT
        c.name  AS category_name,
        b.amount_limit
    FROM   budgets b
    JOIN   categories c ON c.id = b.category_id
    WHERE  b.user_id    = :uid
      AND  b.month_year = :mes_inicio
"""

_SQL_CATEGORIAS = """
    SELECT id::text, name, type
    FROM   categories
    WHERE  user_id = :uid OR user_id IS NULL
    ORDER  BY name
"""

_SQL_CONTAS = """
    SELECT id::text, name
    FROM   accounts
    WHERE  user_id = :uid
    ORDER  BY name
"""

_SQL_INSERT_TX = """
    INSERT INTO transactions
        (user_id, account_id, category_id, description, amount,
         due_date, type, status, source)
    VALUES
        (:uid, :account_id, :category_id, :description, :amount,
         :due_date, :type, 'settled', 'manual')
"""


# ─────────────────────────────────────────────────────────────────────────────
# API pública — leitura
# ─────────────────────────────────────────────────────────────────────────────

@st.cache_data(ttl=60)
def get_controle(ano: int, mes: int) -> dict:
    """
    Retorna dados do mês (ano, mes) para a página Controle Financeiro.
    TTL=60s — cache curto para refletir inserções recentes.
    """
    if settings.MOCK_MODE:
        dados = _controle_mock(ano, mes)
        dados["data_source"] = "mock"
        return dados

    try:
        dados = _controle_real(ano, mes)
        dados["data_source"] = "real"
        return dados
    except Exception as exc:
        logger.warning(
            "[controle] Banco real falhou (%s: %s) — usando mock.",
            type(exc).__name__,
            str(exc)[:120],
        )
        dados = _controle_mock(ano, mes)
        dados["data_source"] = "mock_fallback"
        return dados


@st.cache_data(ttl=300)
def get_opcoes_formulario() -> dict:
    """
    Retorna categorias e contas disponíveis para o formulário de nova transação.
    Cache de 5 min (raramente mudam).
    Retorna dict vazio em caso de falha — não crasha a página.
    """
    if settings.MOCK_MODE:
        return _opcoes_mock()

    try:
        return _opcoes_real()
    except Exception as exc:
        logger.warning("[controle] Falha ao carregar opções do formulário: %s", exc)
        return _opcoes_mock()


# ─────────────────────────────────────────────────────────────────────────────
# API pública — escrita
# ─────────────────────────────────────────────────────────────────────────────

def inserir_transacao(
    descricao: str,
    valor: float,
    tipo: str,
    data: _date,
    categoria_id: Optional[str],
    conta_id: str,
) -> tuple[bool, str]:
    """
    Insere uma nova transação em `transactions`.

    Args:
        descricao:    Texto livre da transação.
        valor:        Valor absoluto positivo. O sinal é aplicado pelo tipo.
        tipo:         "income" ou "expense".
        data:         Data de competência (due_date).
        categoria_id: UUID da categoria (ou None).
        conta_id:     UUID da conta (obrigatório).

    Returns:
        (True, "")           → inserção bem-sucedida
        (False, mensagem)    → erro
    """
    if settings.MOCK_MODE:
        return False, "Modo mock ativo — INSERT não executado. Defina MOCK_MODE=false para inserir dados reais."

    try:
        from sqlalchemy import text
        from core.database import get_engine

        engine = get_engine()
        if engine is None:
            return False, "Banco não configurado — configure SUPABASE_UNIFICADO_URL."

        owner = settings.OWNER_USER_ID
        if not owner:
            return False, "OWNER_USER_ID não configurado."

        # Sinal: income = positivo, expense = negativo
        amount = abs(valor) if tipo == "income" else -abs(valor)

        with engine.begin() as conn:
            conn.execute(
                text(_SQL_INSERT_TX),
                {
                    "uid":          owner,
                    "account_id":   conta_id,
                    "category_id":  categoria_id,
                    "description":  descricao.strip(),
                    "amount":       amount,
                    "due_date":     data,
                    "type":         tipo,
                },
            )

        # Invalida cache do mês inserido para forçar reload
        get_controle.clear()
        return True, ""

    except Exception as exc:
        logger.error("[controle] Falha ao inserir transação: %s", exc)
        return False, str(exc)


# ─────────────────────────────────────────────────────────────────────────────
# MOCK
# ─────────────────────────────────────────────────────────────────────────────

def _controle_mock(ano: int, mes: int) -> dict:
    mes_ref = f"{_MESES_PT[mes]} {ano}"
    receitas = sum(v for _, v, t, *_ in _MOCK_TRANS if t == "income")
    despesas = sum(abs(v) for _, v, t, *_ in _MOCK_TRANS if t == "expense")
    saldo    = receitas - despesas
    taxa     = round(saldo / receitas * 100, 1) if receitas > 0 else 0.0

    # Categorias
    cats = []
    for nome, gasto, orc in _MOCK_CATS:
        pct = round(gasto / orc * 100, 1) if orc > 0 else 0.0
        tipo_badge = "erro" if pct >= 90 else "alerta" if pct >= 75 else "sucesso"
        cats.append({
            "nome": nome, "gasto": gasto, "orcamento": orc,
            "pct_usado": pct, "tipo_badge": tipo_badge,
        })

    # Transações
    trans = []
    for i, (desc, val, tipo, data_str, cat, conta) in enumerate(_MOCK_TRANS):
        data = _date.fromisoformat(data_str)
        eh_receita = val > 0
        trans.append({
            "id":          str(i + 1),
            "descricao":   desc,
            "valor":       val,
            "valor_fmt":   f"{'+ ' if eh_receita else '- '}R$ {abs(val):,.2f}".replace(",", ".").replace(".", ",", 1),
            "data":        data,
            "data_fmt":    data.strftime("%d/%m"),
            "tipo":        tipo,
            "status":      "settled",
            "categoria":   cat,
            "conta":       conta,
            "eh_receita":  eh_receita,
        })

    return {
        "mes_referencia":    mes_ref,
        "receitas":          receitas,
        "despesas":          despesas,
        "saldo_mes":         saldo,
        "taxa_poupanca_pct": taxa,
        "num_transacoes":    len(trans),
        "categorias":        cats,
        "transacoes":        trans,
    }


def _opcoes_mock() -> dict:
    return {
        "categorias": [
            {"id": str(i+1), "nome": n, "tipo": "expense"}
            for i, n in enumerate([
                "Moradia", "Alimentação", "Transporte", "Saúde",
                "Lazer", "Assinaturas", "Educação", "Outros",
            ])
        ] + [{"id": "0", "nome": "Salário", "tipo": "income"}],
        "contas": [{"id": "1", "nome": "Conta Corrente"}],
    }


# ─────────────────────────────────────────────────────────────────────────────
# REAL
# ─────────────────────────────────────────────────────────────────────────────

def _controle_real(ano: int, mes: int) -> dict:
    """
    Consulta transactions + categories + accounts + budgets para o mês dado.

    SEGURANÇA:
      - Somente SELECT.
      - Todas as queries filtradas por OWNER_USER_ID.
    """
    from sqlalchemy import text
    from core.database import get_engine

    engine = get_engine()
    if engine is None:
        raise RuntimeError("Engine indisponível.")

    owner = settings.OWNER_USER_ID
    if not owner:
        raise RuntimeError("OWNER_USER_ID não configurado.")

    mes_inicio = _date(ano, mes, 1)
    mes_ref    = f"{_MESES_PT[mes]} {ano}"

    with engine.connect() as conn:
        tx_rows = conn.execute(
            text(_SQL_TRANSACOES),
            {"uid": owner, "ano": ano, "mes": mes},
        ).fetchall()

        budget_rows = conn.execute(
            text(_SQL_ORCAMENTOS),
            {"uid": owner, "mes_inicio": mes_inicio},
        ).fetchall()

    def _f(v) -> float:
        return float(v) if v is not None else 0.0

    # KPIs
    receitas = sum(_f(r.amount) for r in tx_rows if r.type == "income")
    despesas = sum(abs(_f(r.amount)) for r in tx_rows if r.type == "expense")
    saldo    = round(receitas - despesas, 2)
    taxa     = round(saldo / receitas * 100, 1) if receitas > 0 else 0.0

    # Orçamentos mapeados
    budget_map: dict[str, float] = {r.category_name: _f(r.amount_limit) for r in budget_rows}

    # Categorias de despesa
    cat_gastos: dict[str, float] = {}
    for r in tx_rows:
        if r.type == "expense":
            cat = r.category_name
            cat_gastos[cat] = cat_gastos.get(cat, 0.0) + abs(_f(r.amount))

    categorias = []
    for nome, gasto in sorted(cat_gastos.items(), key=lambda x: x[1], reverse=True):
        orc = budget_map.get(nome, round(gasto * 1.2, 2))   # implicit budget if absent
        pct = round(gasto / orc * 100, 1) if orc > 0 else 0.0
        tipo_badge = "erro" if pct >= 90 else "alerta" if pct >= 75 else "sucesso"
        categorias.append({
            "nome": nome, "gasto": round(gasto, 2), "orcamento": orc,
            "pct_usado": pct, "tipo_badge": tipo_badge,
        })

    # Transações
    transacoes = []
    for r in tx_rows:
        val = _f(r.amount)
        eh_receita = val > 0
        data = r.due_date
        transacoes.append({
            "id":         r.id,
            "descricao":  r.description,
            "valor":      val,
            "valor_fmt":  (
                f"{'+ ' if eh_receita else '- '}R$ {abs(val):,.2f}"
                .replace(",", "X").replace(".", ",").replace("X", ".")
            ),
            "data":       data,
            "data_fmt":   data.strftime("%d/%m") if data else "—",
            "tipo":       r.type,
            "status":     r.status,
            "categoria":  r.category_name,
            "conta":      r.account_name,
            "eh_receita": eh_receita,
        })

    return {
        "mes_referencia":    mes_ref,
        "receitas":          round(receitas, 2),
        "despesas":          round(despesas, 2),
        "saldo_mes":         saldo,
        "taxa_poupanca_pct": taxa,
        "num_transacoes":    len(transacoes),
        "categorias":        categorias,
        "transacoes":        transacoes,
    }


def _opcoes_real() -> dict:
    """Carrega categorias e contas do banco para popular o formulário."""
    from sqlalchemy import text
    from core.database import get_engine

    engine = get_engine()
    if engine is None:
        raise RuntimeError("Engine indisponível.")

    owner = settings.OWNER_USER_ID
    if not owner:
        raise RuntimeError("OWNER_USER_ID não configurado.")

    with engine.connect() as conn:
        cat_rows  = conn.execute(text(_SQL_CATEGORIAS),  {"uid": owner}).fetchall()
        cont_rows = conn.execute(text(_SQL_CONTAS),      {"uid": owner}).fetchall()

    return {
        "categorias": [{"id": r.id, "nome": r.name, "tipo": r.type} for r in cat_rows],
        "contas":     [{"id": r.id, "nome": r.name}                  for r in cont_rows],
    }
