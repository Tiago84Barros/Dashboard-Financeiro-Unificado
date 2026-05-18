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
  inserir_transacao()      — INSERT em transactions com user_id obrigatório
  atualizar_transacao()    — UPDATE de campos editáveis (Fase 5.1)
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

Schema do dict retornado por get_historico_anual():
  data_source  str
  anos         list[int]
  por_ano      dict[int, {receitas, despesas, saldo}]

Schema da list retornada por get_transacoes_filtradas():
  lista de dicts idêntica ao schema de 'transacoes' em get_controle()
  extra: ano, mes, dia (ints)
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
        COALESCE(c.name, 'Sem categoria')  AS category_name,
        COALESCE(ac.name, 'Sem conta')     AS account_name,
        COALESCE(ac.type, '')              AS account_type
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

_SQL_UPDATE_TX = """
    UPDATE transactions
    SET    description = :description,
           category_id = :category_id,
           amount      = :amount,
           due_date    = :due_date
    WHERE  id       = :tx_id::uuid
      AND  user_id  = :uid::uuid
"""

_SQL_HISTORICO_ANUAL = """
    SELECT
        EXTRACT(YEAR FROM t.due_date)::int AS ano,
        CASE
            WHEN t.type IN ('income', 'entrada')                    THEN 'income'
            WHEN t.type IN ('investment', 'investimento')           THEN 'investment'
            WHEN t.type = 'transfer'
                 AND c.name IN (
                     'Renda Fixa','Renda Variavel','Renda Variável',
                     'Exterior','Aporte em Investimento'
                 )                                                  THEN 'investment'
            ELSE 'expense'
        END                                AS bucket,
        SUM(t.amount)                      AS total
    FROM   transactions t
    LEFT JOIN accounts   a ON a.id = t.account_id
    LEFT JOIN categories c ON c.id = t.category_id
    WHERE  t.user_id = :uid
      AND (
            t.type IN ('income', 'entrada', 'investment', 'investimento')
            OR (t.type IN ('expense', 'saida')
                AND COALESCE(a.type, '') != 'credit_card')
            OR (t.type = 'transfer'
                AND c.name IN (
                    'Renda Fixa','Renda Variavel','Renda Variável',
                    'Exterior','Aporte em Investimento'
                ))
      )
    GROUP  BY ano, bucket
    ORDER  BY ano, bucket
"""

_SQL_GASTOS_CARTAO_MENSAL = """
    SELECT
        EXTRACT(YEAR  FROM t.due_date)::int  AS ano,
        EXTRACT(MONTH FROM t.due_date)::int  AS mes,
        SUM(ABS(t.amount))                   AS total
    FROM   transactions t
    LEFT JOIN categories c ON c.id = t.category_id
    WHERE  t.user_id = :uid
      AND  c.name    = 'Pagamento de Cartão'
    GROUP  BY ano, mes
    ORDER  BY ano, mes
"""

_SQL_GASTOS_CATEGORIA_ANUAL = """
    SELECT
        COALESCE(c.name, 'Sem categoria')  AS category_name,
        ABS(SUM(t.amount))                 AS total_spent
    FROM   transactions t
    LEFT JOIN accounts   a ON a.id = t.account_id
    LEFT JOIN categories c ON c.id = t.category_id
    WHERE  t.user_id = :uid
      AND  EXTRACT(YEAR FROM t.due_date)::int = :ano
      AND  t.type IN ('expense', 'saida')
      AND  t.amount < 0
      AND  COALESCE(a.type, '') != 'credit_card'
    GROUP  BY c.name
    ORDER  BY total_spent DESC
"""

_SQL_TRANSACOES_FILTRADAS = """
    SELECT
        t.id::text,
        t.description,
        t.amount,
        t.due_date,
        t.type,
        t.status,
        COALESCE(c.name, 'Sem categoria') AS category_name,
        COALESCE(ac.name, 'Sem conta')    AS account_name,
        EXTRACT(year  FROM t.due_date)::int AS ano,
        EXTRACT(month FROM t.due_date)::int AS mes,
        EXTRACT(day   FROM t.due_date)::int AS dia
    FROM   transactions t
    LEFT JOIN categories c  ON c.id  = t.category_id
    LEFT JOIN accounts   ac ON ac.id = t.account_id
    WHERE  t.user_id = :uid
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


def atualizar_transacao(
    tx_id: str,
    descricao: str,
    valor: float,
    data: _date,
    categoria_id: Optional[str],
) -> tuple[bool, str]:
    """
    Atualiza campos editáveis de uma transação existente.
    Preserva user_id como filtro para segurança.
    """
    if settings.MOCK_MODE:
        return False, "Modo mock ativo — UPDATE não executado."

    try:
        from sqlalchemy import text
        from core.database import get_engine

        engine = get_engine()
        if engine is None:
            return False, "Banco não configurado."

        owner = settings.OWNER_USER_ID
        if not owner:
            return False, "OWNER_USER_ID não configurado."

        with engine.begin() as conn:
            conn.execute(
                text(_SQL_UPDATE_TX),
                {
                    "tx_id":       tx_id,
                    "uid":         owner,
                    "description": descricao.strip(),
                    "category_id": categoria_id,
                    "amount":      valor,
                    "due_date":    data,
                },
            )

        get_controle.clear()
        return True, ""

    except Exception as exc:
        logger.error("[controle] Falha ao atualizar transação: %s", exc)
        return False, str(exc)


@st.cache_data(ttl=300)
def get_historico_anual() -> dict:
    """
    Retorna receitas e despesas agrupadas por ano (todos os anos disponíveis).
    Para a seção 'Comparativo Ano a Ano' e 'Patrimônio Investido'.
    TTL=5min — dados históricos mudam pouco.
    """
    if settings.MOCK_MODE:
        d = _historico_anual_mock()
        d["data_source"] = "mock"
        return d

    try:
        d = _historico_anual_real()
        d["data_source"] = "real"
        return d
    except Exception as exc:
        logger.warning(
            "[controle] get_historico_anual falhou (%s) — usando mock.", type(exc).__name__
        )
        d = _historico_anual_mock()
        d["data_source"] = "mock_fallback"
        return d


@st.cache_data(ttl=300)
def get_gastos_categoria_anual(ano: int) -> list:
    """
    Retorna lista de {nome, gasto} com despesas por categoria no ano informado.
    Exclui compras de cartão de crédito (account_type='credit_card').
    TTL=5min — dados históricos mudam pouco.
    """
    _mock = [
        {"nome": "Moradia",       "gasto": 15_000.00},
        {"nome": "Alimentação",   "gasto":  9_600.00},
        {"nome": "Transporte",    "gasto":  4_800.00},
        {"nome": "Saúde",         "gasto":  2_400.00},
        {"nome": "Lazer",         "gasto":  3_200.00},
        {"nome": "Assinaturas",   "gasto":  1_500.00},
        {"nome": "Educação",      "gasto":  2_000.00},
    ]

    if settings.MOCK_MODE:
        return _mock

    try:
        from sqlalchemy import text
        from core.database import get_engine

        engine = get_engine()
        if engine is None:
            raise RuntimeError("Engine indisponível.")

        owner = settings.OWNER_USER_ID
        if not owner:
            raise RuntimeError("OWNER_USER_ID não configurado.")

        with engine.connect() as conn:
            rows = conn.execute(
                text(_SQL_GASTOS_CATEGORIA_ANUAL), {"uid": owner, "ano": ano}
            ).fetchall()

        return [{"nome": r.category_name, "gasto": round(float(r.total_spent), 2)}
                for r in rows]

    except Exception as exc:
        logger.warning(
            "[controle] get_gastos_categoria_anual(%s) falhou (%s) — usando mock.",
            ano, type(exc).__name__,
        )
        return _mock


@st.cache_data(ttl=60)
def get_transacoes_filtradas(
    tipo: str = "Todos",
    categoria: str = "Todas",
    ano: Optional[int] = None,
    mes: Optional[int] = None,
    dia: Optional[int] = None,
    texto: str = "",
) -> list:
    """
    Retorna transações filtradas para a aba Tabelas.
    Aplica filtros opcionais em Python (sobre lista completa do usuário).
    """
    if settings.MOCK_MODE:
        return _transacoes_mock_filtradas(tipo, categoria, ano, mes, dia, texto)

    try:
        return _transacoes_real_filtradas(tipo, categoria, ano, mes, dia, texto)
    except Exception as exc:
        logger.warning("[controle] get_transacoes_filtradas falhou (%s) — usando mock.", type(exc).__name__)
        return _transacoes_mock_filtradas(tipo, categoria, ano, mes, dia, texto)


# ─────────────────────────────────────────────────────────────────────────────
# GASTOS COM PAGAMENTO DE CARTÃO (MENSAL) — mock + real
# ─────────────────────────────────────────────────────────────────────────────

@st.cache_data(ttl=300)
def get_gastos_cartao_mensal(ano: int) -> list:
    """
    Retorna gastos mensais na categoria 'Pagamento de Cartão' para o ano dado.
    Cada item: {"mes": int, "label": str, "total": float}
    """
    if settings.MOCK_MODE:
        return _gastos_cartao_mock(ano)
    try:
        return _gastos_cartao_real(ano)
    except Exception as exc:
        logger.warning("[controle] get_gastos_cartao_mensal falhou (%s) — usando mock.", type(exc).__name__)
        return _gastos_cartao_mock(ano)


def _gastos_cartao_mock(ano: int) -> list:
    from datetime import date as _dt
    ano_atual = _dt.today().year
    dados = _MOCK_GASTOS_CARTAO if ano == ano_atual else {}
    return [
        {"mes": m, "label": f"{m:02d}/{ano}", "total": round(v, 2)}
        for m, v in sorted(dados.items())
    ]


def _gastos_cartao_real(ano: int) -> list:
    from sqlalchemy import text
    from core.database import get_engine

    engine = get_engine()
    if engine is None:
        raise RuntimeError("Engine indisponível.")

    owner = settings.OWNER_USER_ID
    if not owner:
        raise RuntimeError("OWNER_USER_ID não configurado.")

    with engine.connect() as conn:
        rows = conn.execute(
            text(_SQL_GASTOS_CARTAO_MENSAL),
            {"uid": owner},
        ).fetchall()

    return [
        {
            "mes":   int(r.mes),
            "label": f"{int(r.mes):02d}/{int(r.ano)}",
            "total": round(float(r.total), 2),
        }
        for r in rows
        if int(r.ano) == ano
    ]


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

    def _is_cc(r) -> bool:
        """Compra no cartão de crédito: não é despesa do mês (será paga na fatura)."""
        return getattr(r, "account_type", "") == "credit_card"

    # KPIs — cartão de crédito excluído das despesas (igual ao app isolado)
    receitas = sum(_f(r.amount) for r in tx_rows if r.type == "income")
    despesas = sum(
        abs(_f(r.amount)) for r in tx_rows
        if r.type == "expense" and not _is_cc(r)
    )
    saldo    = round(receitas - despesas, 2)
    taxa     = round(saldo / receitas * 100, 1) if receitas > 0 else 0.0

    # Orçamentos mapeados
    budget_map: dict[str, float] = {r.category_name: _f(r.amount_limit) for r in budget_rows}

    # Categorias de despesa — idem, sem compras de cartão de crédito
    cat_gastos: dict[str, float] = {}
    for r in tx_rows:
        if r.type == "expense" and not _is_cc(r):
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
            "id":           r.id,
            "descricao":    r.description,
            "valor":        val,
            "valor_fmt":    (
                f"{'+ ' if eh_receita else '- '}R$ {abs(val):,.2f}"
                .replace(",", "X").replace(".", ",").replace("X", ".")
            ),
            "data":         data,
            "data_fmt":     data.strftime("%d/%m") if data else "—",
            "tipo":         r.type,
            "status":       r.status,
            "categoria":    r.category_name,
            "conta":        r.account_name,
            "account_type": getattr(r, "account_type", ""),
            "eh_receita":   eh_receita,
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


# ─────────────────────────────────────────────────────────────────────────────
# HISTÓRICO ANUAL — mock + real
# ─────────────────────────────────────────────────────────────────────────────

_MOCK_YOY = {
    2024: {"receitas": 95_000.0, "despesas": 68_000.0, "investimentos": 12_000.0},
    2025: {"receitas": 108_000.0, "despesas": 74_000.0, "investimentos": 28_409.0},
    2026: {"receitas": 42_500.0, "despesas": 30_000.0, "investimentos": 10_200.0},
}

# Mock mensal de "Pagamento de Cartão" para o ano atual
_MOCK_GASTOS_CARTAO = {
    1: 11_416.54, 2: 10_158.53, 3: 6_867.12,
    4: 11_266.55, 5: 3_025.57,
}


def _historico_anual_mock() -> dict:
    anos = sorted(_MOCK_YOY.keys())
    por_ano = {
        a: {
            **_MOCK_YOY[a],
            "saldo": round(
                _MOCK_YOY[a]["receitas"]
                - _MOCK_YOY[a]["despesas"]
                - _MOCK_YOY[a]["investimentos"],
                2,
            ),
        }
        for a in anos
    }
    return {"anos": anos, "por_ano": por_ano}


def _historico_anual_real() -> dict:
    from sqlalchemy import text
    from core.database import get_engine

    engine = get_engine()
    if engine is None:
        raise RuntimeError("Engine indisponível.")

    owner = settings.OWNER_USER_ID
    if not owner:
        raise RuntimeError("OWNER_USER_ID não configurado.")

    with engine.connect() as conn:
        rows = conn.execute(text(_SQL_HISTORICO_ANUAL), {"uid": owner}).fetchall()

    por_ano: dict[int, dict] = {}
    for r in rows:
        a = int(r.ano)
        if a not in por_ano:
            por_ano[a] = {"receitas": 0.0, "despesas": 0.0, "investimentos": 0.0}
        bucket = (r.bucket or "").lower()
        if bucket == "income":
            por_ano[a]["receitas"] += float(r.total)
        elif bucket == "expense":
            por_ano[a]["despesas"] += abs(float(r.total))
        elif bucket == "investment":
            por_ano[a]["investimentos"] += abs(float(r.total))

    for a in por_ano:
        por_ano[a]["saldo"] = round(
            por_ano[a]["receitas"]
            - por_ano[a]["despesas"]
            - por_ano[a]["investimentos"],
            2,
        )
        por_ano[a]["receitas"]      = round(por_ano[a]["receitas"], 2)
        por_ano[a]["despesas"]      = round(por_ano[a]["despesas"], 2)
        por_ano[a]["investimentos"] = round(por_ano[a]["investimentos"], 2)

    anos = sorted(por_ano.keys())
    return {"anos": anos, "por_ano": por_ano}


# ─────────────────────────────────────────────────────────────────────────────
# TRANSAÇÕES FILTRADAS — mock + real (para aba Tabelas)
# ─────────────────────────────────────────────────────────────────────────────

def _tx_to_dict(tx: dict) -> dict:
    """Garante campos extras (ano, mes, dia) a partir de data."""
    d = tx.get("data")
    if d is not None:
        tx.setdefault("ano", d.year)
        tx.setdefault("mes", d.month)
        tx.setdefault("dia", d.day)
    return tx


def _transacoes_mock_filtradas(
    tipo: str, categoria: str, ano: Optional[int],
    mes: Optional[int], dia: Optional[int], texto: str,
) -> list:
    """Filtra o mock de transações."""
    from datetime import date as _dt

    raw = []
    for i, (desc, val, tp, data_str, cat, conta) in enumerate(_MOCK_TRANS):
        data = _dt.fromisoformat(data_str)
        eh_r = val > 0
        raw.append({
            "id": str(i + 1),
            "descricao": desc,
            "valor": val,
            "valor_fmt": (
                f"{'+ ' if eh_r else '- '}R$ {abs(val):,.2f}"
                .replace(",", "X").replace(".", ",").replace("X", ".")
            ),
            "data": data,
            "data_fmt": data.strftime("%d/%m/%Y"),
            "tipo": tp,
            "status": "settled",
            "categoria": cat,
            "conta": conta,
            "eh_receita": eh_r,
            "ano": data.year,
            "mes": data.month,
            "dia": data.day,
        })

    return _filtrar_transacoes(raw, tipo, categoria, ano, mes, dia, texto)


def _transacoes_real_filtradas(
    tipo: str, categoria: str, ano: Optional[int],
    mes: Optional[int], dia: Optional[int], texto: str,
) -> list:
    from sqlalchemy import text
    from core.database import get_engine

    engine = get_engine()
    if engine is None:
        raise RuntimeError("Engine indisponível.")

    owner = settings.OWNER_USER_ID
    if not owner:
        raise RuntimeError("OWNER_USER_ID não configurado.")

    # Busca todas as transações do usuário (sem filtro de mês)
    with engine.connect() as conn:
        rows = conn.execute(
            text(_SQL_TRANSACOES_FILTRADAS + " ORDER BY t.due_date DESC, t.created_at DESC"),
            {"uid": owner},
        ).fetchall()

    txs = []
    for r in rows:
        val = float(r.amount) if r.amount is not None else 0.0
        eh_r = val > 0
        data = r.due_date
        txs.append({
            "id": r.id,
            "descricao": r.description,
            "valor": val,
            "valor_fmt": (
                f"{'+ ' if eh_r else '- '}R$ {abs(val):,.2f}"
                .replace(",", "X").replace(".", ",").replace("X", ".")
            ),
            "data": data,
            "data_fmt": data.strftime("%d/%m/%Y") if data else "—",
            "tipo": r.type,
            "status": r.status,
            "categoria": r.category_name,
            "conta": r.account_name,
            "eh_receita": eh_r,
            "ano": int(r.ano) if r.ano else None,
            "mes": int(r.mes) if r.mes else None,
            "dia": int(r.dia) if r.dia else None,
        })

    return _filtrar_transacoes(txs, tipo, categoria, ano, mes, dia, texto)


def _filtrar_transacoes(
    txs: list, tipo: str, categoria: str,
    ano: Optional[int], mes: Optional[int], dia: Optional[int], texto: str,
) -> list:
    """Aplica filtros em memória."""
    out = txs

    if tipo == "Receitas":
        out = [t for t in out if t["eh_receita"]]
    elif tipo == "Despesas":
        out = [t for t in out if not t["eh_receita"]]

    if categoria and categoria != "Todas":
        out = [t for t in out if t["categoria"] == categoria]

    if ano is not None:
        out = [t for t in out if t.get("ano") == ano]

    if mes is not None:
        out = [t for t in out if t.get("mes") == mes]

    if dia is not None:
        out = [t for t in out if t.get("dia") == dia]

    if texto:
        txt_low = texto.lower()
        out = [t for t in out if txt_low in (t["descricao"] or "").lower()]

    return out
