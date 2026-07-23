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
import io
import re
import uuid
from collections import Counter
from datetime import date as _date
from typing import Optional
import unicodedata

import streamlit as st

from core.config import settings
from core.import_guard import acquire_transaction_import_lock

logger = logging.getLogger(__name__)

_MESES_PT = {
    1: "Jan", 2: "Fev", 3: "Mar", 4: "Abr",
    5: "Mai", 6: "Jun", 7: "Jul", 8: "Ago",
    9: "Set", 10: "Out", 11: "Nov", 12: "Dez",
}

_INVESTMENT_CATEGORY_KEYS = frozenset({
    "investimento",
    "investimentos",
    "aporte investimento",
    "aporte em investimento",
    "renda fixa",
    "renda variavel",
    "exterior",
    "reserva de despesa",
    "tesouro direto",
    "acoes",
    "acao",
    "fiis",
    "fii",
    "fundos imobiliarios",
    "fundo imobiliario",
    "cripto",
    "criptoativos",
    "criptomoedas",
})

_INVESTMENT_CATEGORY_SQL = (
    "'Investimento','Investimentos','Aporte em Investimento',"
    "'Renda Fixa','Renda Variavel','Renda Variável','Exterior',"
    "'Reserva de Despesa','Tesouro Direto','Ações','Acoes','FIIs','FII',"
    "'Fundos Imobiliários','Fundos Imobiliarios','Cripto','Criptoativos','Criptomoedas'"
)


def _norm_text(value: object) -> str:
    """Normaliza texto de categoria/tipo para comparação robusta."""
    if value is None:
        return ""
    text = unicodedata.normalize("NFKD", str(value))
    text = text.encode("ascii", "ignore").decode("ascii")
    text = text.replace("_", " ").replace("-", " ").strip().casefold()
    return " ".join(text.split())


def is_investment_category(category: object) -> bool:
    """Retorna True para categorias financeiras que representam aporte/investimento."""
    norm = _norm_text(category)
    if not norm:
        return False
    return norm in _INVESTMENT_CATEGORY_KEYS or (
        "invest" in norm and "dividendo" not in norm
    )


def canonical_transaction_type(
    tipo: object,
    categoria: object = "",
    amount: object = None,
) -> str:
    """
    Classifica uma transação de forma canônica.

    Compatível com dados antigos (`entrada`/`saida`), novos (`income`/`expense`)
    e lançamentos de investimento salvos como `transfer` com categoria financeira.
    """
    raw = _norm_text(tipo)

    if raw in {"investment", "investimento", "investimentos"} or is_investment_category(categoria):
        return "investment"
    if raw in {"income", "entrada", "receita", "receitas"}:
        return "income"
    if raw in {"expense", "saida", "despesa", "despesas"}:
        return "expense"
    if raw in {"transfer", "transferencia", "transferencias"}:
        return "transfer"

    if amount is not None:
        try:
            return "income" if float(amount) >= 0 else "expense"
        except (TypeError, ValueError):
            pass
    return "expense"


def transaction_type_label(tipo_fluxo: str) -> str:
    return {
        "income": "entrada",
        "expense": "saída",
        "investment": "investimento",
        "transfer": "transferência",
    }.get(tipo_fluxo, "saída")


def format_transaction_amount(valor: float, tipo_fluxo: str) -> str:
    prefix = "+ " if tipo_fluxo == "income" else "- " if tipo_fluxo == "expense" else ""
    return (
        f"{prefix}R$ {abs(float(valor or 0)):,.2f}"
        .replace(",", "X").replace(".", ",").replace("X", ".")
    )

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
        t.source,
        t.recurring,
        COALESCE(t.installment_current, 1) AS installment_current,
        COALESCE(t.installment_total, 1)   AS installment_total,
        t.installment_group::text          AS installment_group,
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
    SELECT id::text, name, type
    FROM   accounts
    WHERE  user_id = :uid
    ORDER  BY name
"""

_SQL_ACCOUNT_FOR_MANUAL_INSERT = """
    SELECT id::text, COALESCE(type, '') AS type
    FROM   accounts
    WHERE  id = CAST(:account_id AS uuid)
      AND  user_id = CAST(:uid AS uuid)
    LIMIT 1
"""

_SQL_CONTAS_CARTAO = """
    SELECT id::text, name
    FROM   accounts
    WHERE  user_id = :uid
      AND  type = 'credit_card'
      AND  active = TRUE
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

_SQL_INSERT_INVOICE_TX = """
    INSERT INTO transactions
        (user_id, account_id, category_id, description, amount,
         due_date, payment_date, type, status, source,
         installment_current, installment_total, installment_group)
    VALUES
        (:uid, :account_id, :category_id, :description, :amount,
         :due_date, :payment_date, :type, 'settled', 'csv',
         :installment_current, :installment_total,
         CAST(:installment_group AS uuid))
"""

_SQL_UPDATE_TX = """
    UPDATE transactions
    SET    description = :description,
           category_id = CAST(:category_id AS uuid),
           account_id  = CAST(:account_id AS uuid),
           amount      = :amount,
           due_date    = :due_date,
           type        = :type
    WHERE  id       = CAST(:tx_id AS uuid)
      AND  user_id  = CAST(:uid AS uuid)
"""

# Edição de lançamentos da fatura de cartão (aba Cartão de Crédito). Atualiza os
# campos armazenados que o usuário vê/edita na tabela detalhada. NÃO altera `type`
# nem `source` (preserva a natureza CSV do lançamento e a lógica de fluxo futuro).
_SQL_UPDATE_TX_CARTAO = """
    UPDATE transactions
    SET    description         = :description,
           category_id         = CAST(:category_id AS uuid),
           account_id          = CAST(:account_id AS uuid),
           amount              = :amount,
           due_date            = :due_date,
           payment_date        = :payment_date,
           installment_current = :installment_current,
           installment_total   = :installment_total,
           status              = :status
    WHERE  id       = CAST(:tx_id AS uuid)
      AND  user_id  = CAST(:uid AS uuid)
"""

_SQL_HISTORICO_ANUAL = f"""
    SELECT
        EXTRACT(YEAR FROM t.due_date)::int AS ano,
        SUM(CASE
            WHEN t.type IN ('income', 'entrada')
                 AND COALESCE(c.name, '') NOT IN ({_INVESTMENT_CATEGORY_SQL})
                THEN t.amount
            ELSE 0
        END)                               AS total_income,
        SUM(CASE
            WHEN t.type IN ('expense', 'saida')
                 AND COALESCE(a.type, '') != 'credit_card'
                 AND COALESCE(c.name, '') NOT IN ({_INVESTMENT_CATEGORY_SQL})
                THEN ABS(t.amount)
            ELSE 0
        END)                               AS total_expenses_abs,
        SUM(CASE
            WHEN t.type IN ('investment', 'investimento')
                 OR COALESCE(c.name, '') IN ({_INVESTMENT_CATEGORY_SQL})
                THEN ABS(t.amount)
            ELSE 0
        END)                               AS total_investments
    FROM   transactions t
    LEFT JOIN accounts   a ON a.id = t.account_id
    LEFT JOIN categories c ON c.id = t.category_id
    WHERE  t.user_id = :uid
      AND  t.status  = 'settled'
      AND  (
               t.type IN ('income', 'entrada', 'expense', 'saida',
                          'investment', 'investimento', 'transfer')
               OR COALESCE(c.name, '') IN ({_INVESTMENT_CATEGORY_SQL})
           )
    GROUP  BY EXTRACT(YEAR FROM t.due_date)::int
    ORDER  BY ano
"""

_SQL_GASTOS_CARTAO_MENSAL = """
    SELECT
        EXTRACT(YEAR  FROM t.due_date)::int  AS ano,
        EXTRACT(MONTH FROM t.due_date)::int  AS mes,
        SUM(ABS(t.amount))                   AS total
    FROM   transactions t
    LEFT JOIN categories c ON c.id = t.category_id
    LEFT JOIN accounts   a ON a.id = t.account_id
    WHERE  t.user_id = :uid
      AND  c.name    = 'Pagamento de Cartão'
      -- Fluxo de caixa: pagamentos que SAÍRAM da conta (lançamento manual OU
      -- extrato bancário source='import'). Exclui apenas a fatura CSV do cartão
      -- (account_type='credit_card' + source='csv'), que é fluxo FUTURO e vive só
      -- na aba Cartão de Crédito. As duas naturezas não se misturam.
      AND  NOT (COALESCE(a.type, '') = 'credit_card' AND COALESCE(t.source, '') = 'csv')
    GROUP  BY ano, mes
    ORDER  BY ano, mes
"""

_SQL_HISTORICO_CC_MENSAL = """
    SELECT
        EXTRACT(YEAR  FROM t.due_date)::int AS ano,
        EXTRACT(MONTH FROM t.due_date)::int AS mes,
        SUM(ABS(t.amount))                  AS total
    FROM   transactions t
    LEFT JOIN accounts a ON a.id = t.account_id
    WHERE  t.user_id = :uid
      AND  t.type IN ('expense', 'saida')
      AND  COALESCE(a.type, '') = 'credit_card'
      AND  COALESCE(t.source, '') = 'csv'
    GROUP  BY ano, mes
    ORDER  BY ano, mes
"""

_SQL_TRANSACOES_CARTAO = """
    SELECT
        t.id::text,
        t.description,
        t.amount,
        t.due_date,
        t.payment_date,
        t.type,
        t.status,
        t.source,
        COALESCE(t.installment_current, 1) AS installment_current,
        COALESCE(t.installment_total, 1)   AS installment_total,
        t.installment_group::text          AS installment_group,
        COALESCE(c.name, 'Sem categoria')  AS category_name,
        COALESCE(ac.name, 'Sem conta')     AS account_name,
        COALESCE(ac.type, '')              AS account_type
    FROM   transactions t
    LEFT JOIN accounts   ac ON ac.id = t.account_id
    LEFT JOIN categories c  ON c.id  = t.category_id
    WHERE  t.user_id = :uid
      AND  COALESCE(ac.type, '') = 'credit_card'
      AND  COALESCE(t.source, '') = 'csv'
    ORDER  BY t.due_date DESC, COALESCE(t.payment_date, t.due_date) DESC, t.created_at DESC
"""

_SQL_DIVIDAS_CC = """
    SELECT
        t.installment_group                                    AS grupo,
        COALESCE(ac.name, 'Sem cartão')                        AS cartao,
        COALESCE(c.name,  'Sem categoria')                     AS categoria,
        MIN(t.due_date)                                         AS data_compra,
        SUM(ABS(t.amount))                                      AS total_compra,
        MAX(t.installment_total)                                AS total_parcelas,
        SUM(CASE WHEN t.status = 'settled' THEN 1 ELSE 0 END)::int AS parcelas_pagas,
        MAX(t.description)                                      AS descricao
    FROM   transactions t
    LEFT JOIN accounts   ac ON ac.id = t.account_id
    LEFT JOIN categories c  ON c.id  = t.category_id
    WHERE  t.user_id = :uid
      AND  COALESCE(ac.type, '') = 'credit_card'
      AND  COALESCE(t.source, '') = 'csv'
      AND  t.installment_group IS NOT NULL
      AND  t.installment_total > 1
    GROUP  BY t.installment_group, ac.name, c.name
    ORDER  BY MIN(t.due_date) DESC
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


@st.cache_data(ttl=300)
def get_contas_cartao_credito() -> list[dict]:
    """Retorna contas do tipo cartão de crédito disponíveis para importação."""
    if settings.MOCK_MODE:
        return [{"id": "cc-mock", "nome": "Cartão de Crédito"}]

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
            rows = conn.execute(text(_SQL_CONTAS_CARTAO), {"uid": owner}).fetchall()
        return [{"id": r.id, "nome": r.name} for r in rows]
    except Exception as exc:
        logger.warning("[controle] Falha ao carregar contas de cartão: %s", exc)
        return []


# ─────────────────────────────────────────────────────────────────────────────
# API pública — escrita
# ─────────────────────────────────────────────────────────────────────────────
def _clear_controle_caches() -> None:
    """Invalida caches afetados por inserções ou edições financeiras."""
    for cached_name in (
        "get_controle",
        "get_transacoes_filtradas",
        "get_historico_anual",
        "get_gastos_categoria_anual",
        "get_gastos_cartao_mensal",
        "get_historico_cc_mensal",
        "get_transacoes_cartao_credito",
        "get_dividas_cc",
    ):
        cached_fn = globals().get(cached_name)
        if hasattr(cached_fn, "clear"):
            cached_fn.clear()
    try:
        from core.investimentos import get_cashflow_mensal

        if hasattr(get_cashflow_mensal, "clear"):
            get_cashflow_mensal.clear()
    except Exception:
        pass


_FATURA_COLUNAS_OBRIGATORIAS = [
    "Data de Compra",
    "Nome no Cartão",
    "Final do Cartão",
    "Categoria",
    "Descrição",
    "Parcela",
    "Valor (em US$)",
    "Cotação (em R$)",
    "Valor (em R$)",
]


def _parse_money(value: object) -> float:
    if value is None:
        return 0.0
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none"}:
        return 0.0
    text = text.replace("R$", "").replace("US$", "").replace(" ", "")
    if "," in text and "." in text:
        text = text.replace(".", "").replace(",", ".")
    elif "," in text:
        text = text.replace(",", ".")
    try:
        return round(float(text), 2)
    except ValueError:
        return 0.0


def _parse_installment(value: object) -> tuple[int, int, str]:
    raw = str(value or "").strip()
    norm = _norm_text(raw)
    if not raw or norm in {"unica", "unico", "1", "1 1"}:
        return 1, 1, raw or "Única"
    match = re.search(r"(\d+)\s*/\s*(\d+)", raw)
    if not match:
        return 1, 1, raw
    current = max(1, int(match.group(1)))
    total = max(current, int(match.group(2)))
    return current, total, raw


def _invoice_category(raw_category: object, description: object, value_brl: float,
                      user_rules: list | None = None) -> str:
    """
    Categoria de um lançamento da fatura usando a taxonomia limpa (13 categorias).
    Delega a core.card_categorization.classify — fonte única de regras, também
    usada pelo script scripts/recategorizar_cartao.py. `user_rules` são as regras
    aprendidas pelo usuário (palavra-chave -> categoria) e têm prioridade sobre as
    internas. Sem regra e sem catálogo, retorna o sentinela "A revisar".
    """
    from core.card_categorization import classify
    categoria, _regra = classify(raw_category, description, value_brl, user_rules=user_rules)
    return categoria


def _invoice_tx_type(value_brl: float, description: object) -> str:
    desc_norm = _norm_text(description)
    # Pagamentos da fatura → transfer
    if any(p in desc_norm for p in (
        "pag fatura", "pagamento fatura", "inclusao de pagamento",
        "inclusão de pagamento", "debito em conta", "pagto debito",
    )):
        return "transfer"
    if value_brl < 0 or "estorno" in desc_norm:
        return "transfer"
    return "expense"


def _invoice_description(description: object, purchase_date: _date, card_final: object, parcel_label: str) -> str:
    desc = " ".join(str(description or "Sem descrição").split())
    final = str(card_final or "").strip()
    parts = [desc, f"Compra {purchase_date.strftime('%d/%m/%Y')}"]
    if final:
        parts.append(f"Cartão {final}")
    if parcel_label:
        parts.append(f"Parcela {parcel_label}")
    return " | ".join(parts)[:255]


def _invoice_group_uuid(card_final: object, description: object, purchase_date: _date, installment_total: int) -> str | None:
    if installment_total <= 1:
        return None
    key = "|".join([
        "app4-card-invoice",
        _norm_text(card_final),
        _norm_text(description),
        purchase_date.isoformat(),
        str(installment_total),
    ])
    return str(uuid.uuid5(uuid.NAMESPACE_URL, key))


def _read_invoice_csv(file_bytes: bytes):
    import pandas as pd

    last_exc: Exception | None = None
    for encoding in ("utf-8-sig", "utf-8", "latin1", "cp1252"):
        try:
            df = pd.read_csv(io.BytesIO(file_bytes), sep=";", dtype=str, encoding=encoding)
            df.columns = [str(c).strip() for c in df.columns]
            return df
        except Exception as exc:
            last_exc = exc
    raise ValueError(f"Não foi possível ler o CSV da fatura: {last_exc}")


def parse_fatura_cartao_csv(file_bytes: bytes, vencimento: _date,
                            user_rules: list | None = None) -> dict:
    """
    Lê o CSV da fatura e retorna linhas normalizadas sem gravar no banco.

    A data de compra fica em payment_date; due_date representa o vencimento da fatura,
    que é o eixo usado pela aba Cartão de Crédito.

    `user_rules`: regras aprendidas pelo usuário (palavra-chave -> categoria). Quando
    None, usa apenas as regras internas (não acessa banco — seguro em testes/CLI).
    """
    import pandas as pd

    df = _read_invoice_csv(file_bytes)
    missing = [c for c in _FATURA_COLUNAS_OBRIGATORIAS if c not in df.columns]
    if missing:
        return {
            "ok": False,
            "errors": [f"Colunas obrigatórias ausentes: {', '.join(missing)}"],
            "rows": [],
            "summary": {},
        }

    rows: list[dict] = []
    errors: list[str] = []

    for idx, raw in df.iterrows():
        line_no = int(idx) + 2
        try:
            purchase_ts = pd.to_datetime(
                raw["Data de Compra"],
                dayfirst=True,
                errors="raise",
            )
            purchase_date = purchase_ts.date()
        except Exception:
            errors.append(f"Linha {line_no}: data de compra inválida.")
            continue

        value_brl = _parse_money(raw["Valor (em R$)"])
        value_usd = _parse_money(raw["Valor (em US$)"])
        fx_brl = _parse_money(raw["Cotação (em R$)"])
        inst_current, inst_total, inst_label = _parse_installment(raw["Parcela"])
        category = _invoice_category(raw["Categoria"], raw["Descrição"], value_brl, user_rules)
        tx_type = _invoice_tx_type(value_brl, raw["Descrição"])
        amount = -abs(value_brl) if tx_type == "expense" else abs(value_brl)
        description = _invoice_description(
            raw["Descrição"],
            purchase_date,
            raw["Final do Cartão"],
            inst_label,
        )

        rows.append({
            "line_no": line_no,
            "purchase_date": purchase_date,
            "due_date": vencimento,
            "cardholder": str(raw["Nome no Cartão"] or "").strip(),
            "card_final": str(raw["Final do Cartão"] or "").strip(),
            "raw_category": str(raw["Categoria"] or "").strip(),
            "category": category,
            "description_raw": str(raw["Descrição"] or "").strip(),
            "description": description,
            "installment_label": inst_label,
            "installment_current": inst_current,
            "installment_total": inst_total,
            "value_usd": value_usd,
            "fx_brl": fx_brl,
            "value_brl": value_brl,
            "amount": amount,
            "type": tx_type,
            "installment_group": _invoice_group_uuid(
                raw["Final do Cartão"],
                raw["Descrição"],
                purchase_date,
                inst_total,
            ),
        })

    total_purchases = round(sum(r["value_brl"] for r in rows if r["value_brl"] > 0), 2)
    total_credits = round(abs(sum(r["value_brl"] for r in rows if r["value_brl"] < 0)), 2)
    summary = {
        "rows": len(rows),
        "errors": len(errors),
        "due_date": vencimento,
        "total_purchases": total_purchases,
        "total_credits": total_credits,
        "net_total": round(sum(r["value_brl"] for r in rows), 2),
        "cards": sorted({r["card_final"] for r in rows if r["card_final"]}),
        "installments": sum(1 for r in rows if r["installment_total"] > 1),
    }

    return {"ok": len(rows) > 0 and not errors, "errors": errors, "rows": rows, "summary": summary}


def _category_type_for_invoice_row(row: dict) -> str:
    if row["type"] == "expense":
        return "expense"
    return "transfer"


def _get_or_create_category(conn, owner: str, name: str, cat_type: str) -> str | None:
    from sqlalchemy import text

    clean = (name or "Outros").strip() or "Outros"
    # Uma única trava por usuário evita que duas faturas concorrentes criem
    # categorias homônimas antes de enxergarem o commit uma da outra.
    acquire_transaction_import_lock(conn, "category-upsert", owner)
    row = conn.execute(
        text("""
            SELECT id::text
            FROM categories
            WHERE LOWER(name) = LOWER(:name)
              AND (user_id = :uid OR user_id IS NULL)
            ORDER BY CASE WHEN user_id = :uid THEN 0 ELSE 1 END
            LIMIT 1
        """),
        {"name": clean, "uid": owner},
    ).fetchone()
    if row:
        return row.id

    created = conn.execute(
        text("""
            INSERT INTO categories (user_id, name, type)
            VALUES (:uid, :name, :type)
            RETURNING id::text
        """),
        {"uid": owner, "name": clean[:100], "type": cat_type},
    ).fetchone()
    return created.id if created else None


def _invoice_fingerprint(row: dict, account_id: str) -> tuple:
    return (
        account_id,
        row["due_date"].isoformat(),
        row["purchase_date"].isoformat(),
        round(float(row["amount"]), 2),
        _norm_text(row["description"]),
        _norm_text(row["category"]),
        int(row["installment_current"]),
        int(row["installment_total"]),
    )


_PAYMENT_TERMS_NORM = (
    "pag fatura", "pagamento fatura",
    "inclusao de pagamento", "inclusão de pagamento",
    "debito em conta", "pagto debito",
)


def corrigir_classificacao_pagamentos_fatura(account_id: str) -> int:
    """
    Corrige registros CSV de cartão de crédito que foram importados com a
    categoria 'Créditos e Estornos' mas na verdade são pagamentos da fatura
    ('Inclusão de Pagamento', 'Pag Fatura Boleto', etc.).

    Atualiza a categoria para 'Pagamento de Cartão' e o tipo para 'transfer'.
    Retorna o número de registros corrigidos.
    """
    from sqlalchemy import text as _t
    from core.database import get_engine

    engine = get_engine()
    if engine is None:
        return 0
    owner = settings.OWNER_USER_ID
    if not owner:
        return 0

    # Condição OR para todos os termos de pagamento (sem acentos — busca em lower)
    conditions_plain = " OR ".join(
        f"LOWER(description) LIKE '%{term}%'"
        for term in _PAYMENT_TERMS_NORM
    )

    try:
        with engine.begin() as conn:
            # 1. Usa _get_or_create_category — mesma função confiável do importer
            pay_cat_id = _get_or_create_category(
                conn, owner, "Pagamento de Cartão", "expense"
            )
            if not pay_cat_id:
                logger.warning("[corrigir] não conseguiu obter categoria 'Pagamento de Cartão'")
                return 0

            # 2. Conta quantos registros vão ser corrigidos (para retorno confiável)
            count_row = conn.execute(_t(f"""
                SELECT COUNT(*) AS n
                FROM transactions
                WHERE account_id = CAST(:account_id AS uuid)
                  AND user_id    = CAST(:uid AS uuid)
                  AND source     = 'csv'
                  AND amount     < 0
                  AND (category_id IS NULL
                       OR category_id != CAST(:cat_id AS uuid))
                  AND ({conditions_plain})
            """), {"uid": owner, "account_id": account_id, "cat_id": pay_cat_id}).fetchone()

            to_fix = int(count_row.n) if count_row else 0
            if to_fix == 0:
                return 0

            # 3. Atualiza registros
            conn.execute(_t(f"""
                UPDATE transactions
                SET category_id = CAST(:cat_id AS uuid),
                    type        = 'transfer'
                WHERE account_id = CAST(:account_id AS uuid)
                  AND user_id    = CAST(:uid AS uuid)
                  AND source     = 'csv'
                  AND amount     < 0
                  AND (category_id IS NULL
                       OR category_id != CAST(:cat_id AS uuid))
                  AND ({conditions_plain})
            """), {"uid": owner, "account_id": account_id, "cat_id": pay_cat_id})

        _clear_controle_caches()
        return to_fix
    except Exception as exc:
        logger.warning("[controle] corrigir_classificacao_pagamentos_fatura: %s", exc)
        return 0


def limpar_transacoes_cartao(account_id: str, due_date: _date | None = None) -> int:
    """
    Remove os lançamentos CSV do cartão de crédito informado.

    - Se due_date for fornecido, apaga somente os registros da fatura
      com aquele vencimento.
    - Se due_date for None, apaga TODOS os registros CSV do cartão.

    Retorna o número de registros apagados.
    """
    from sqlalchemy import text as _t
    from core.database import get_engine

    engine = get_engine()
    if engine is None:
        return 0
    owner = settings.OWNER_USER_ID
    if not owner:
        return 0

    date_filter = "AND due_date = :due_date" if due_date else ""
    params: dict = {"uid": owner, "account_id": account_id}
    if due_date:
        params["due_date"] = due_date

    try:
        with engine.begin() as conn:
            count_row = conn.execute(_t(f"""
                SELECT COUNT(*) AS n FROM transactions
                WHERE account_id = CAST(:account_id AS uuid)
                  AND user_id    = CAST(:uid AS uuid)
                  AND source     = 'csv'
                  {date_filter}
            """), params).fetchone()
            total = int(count_row.n) if count_row else 0
            if total == 0:
                return 0
            conn.execute(_t(f"""
                DELETE FROM transactions
                WHERE account_id = CAST(:account_id AS uuid)
                  AND user_id    = CAST(:uid AS uuid)
                  AND source     = 'csv'
                  {date_filter}
            """), params)
        _clear_controle_caches()
        return total
    except Exception as exc:
        logger.warning("[controle] limpar_transacoes_cartao: %s", exc)
        return 0


def importar_fatura_cartao_csv(file_bytes: bytes, vencimento: _date, account_id: str) -> dict:
    """Importa a fatura para `transactions`, com deduplicação idempotente por contagem."""
    if settings.MOCK_MODE:
        return {"ok": False, "message": "Modo mock ativo — importação não executada."}

    # Regras aprendidas pelo usuário (fora do begin() da importação; degrada p/ []).
    parsed = parse_fatura_cartao_csv(file_bytes, vencimento, get_card_category_rules())
    if parsed.get("errors"):
        return {"ok": False, "message": "; ".join(parsed["errors"][:5])}
    if not parsed["rows"]:
        return {"ok": False, "message": "; ".join(parsed.get("errors") or ["Fatura sem linhas válidas."])}

    from sqlalchemy import text
    from core.database import get_engine

    engine = get_engine()
    if engine is None:
        return {"ok": False, "message": "Banco não configurado."}

    owner = settings.OWNER_USER_ID
    if not owner:
        return {"ok": False, "message": "OWNER_USER_ID não configurado."}

    rows = parsed["rows"]
    inserted = 0
    skipped = 0

    # Corrige registros anteriores classificados incorretamente como estornos
    corrigir_classificacao_pagamentos_fatura(account_id)

    with engine.begin() as conn:
        # Serializa importações da mesma fatura. Sem este lock, dois cliques ou
        # reruns concorrentes podem consultar a ausência das linhas ao mesmo
        # tempo e inserir dois lotes completos antes de qualquer commit.
        acquire_transaction_import_lock(
            conn,
            "credit-card-invoice",
            owner,
            account_id,
            vencimento,
            file_bytes,
        )

        account = conn.execute(
            text("""
                SELECT id::text, name, type
                FROM accounts
                WHERE id = CAST(:account_id AS uuid)
                  AND user_id = CAST(:uid AS uuid)
                LIMIT 1
            """),
            {"account_id": account_id, "uid": owner},
        ).fetchone()
        if not account:
            return {"ok": False, "message": "Conta de cartão não encontrada."}
        if account.type != "credit_card":
            return {"ok": False, "message": "A conta selecionada não é do tipo cartão de crédito."}

        existing_rows = conn.execute(
            text("""
                SELECT
                    t.description,
                    t.amount,
                    t.due_date,
                    t.payment_date,
                    COALESCE(c.name, 'Sem categoria') AS category_name,
                    COALESCE(t.installment_current, 1) AS installment_current,
                    COALESCE(t.installment_total, 1) AS installment_total
                FROM transactions t
                LEFT JOIN categories c ON c.id = t.category_id
                WHERE t.user_id = CAST(:uid AS uuid)
                  AND t.account_id = CAST(:account_id AS uuid)
                  AND t.due_date = :due_date
                  AND COALESCE(t.source, '') = 'csv'
            """),
            {"uid": owner, "account_id": account_id, "due_date": vencimento},
        ).fetchall()

        existing = Counter()
        for item in existing_rows:
            purchase_date = item.payment_date or item.due_date
            existing[(
                account_id,
                item.due_date.isoformat(),
                purchase_date.isoformat(),
                round(float(item.amount or 0), 2),
                _norm_text(item.description),
                _norm_text(item.category_name),
                int(item.installment_current or 1),
                int(item.installment_total or 1),
            )] += 1

        seen = Counter()
        category_cache: dict[tuple[str, str], str | None] = {}

        for row in rows:
            fp = _invoice_fingerprint(row, account_id)
            seen[fp] += 1
            if seen[fp] <= existing.get(fp, 0):
                skipped += 1
                continue

            cat_key = (_norm_text(row["category"]), _category_type_for_invoice_row(row))
            category_id = category_cache.get(cat_key)
            if cat_key not in category_cache:
                category_id = _get_or_create_category(
                    conn,
                    owner,
                    row["category"],
                    _category_type_for_invoice_row(row),
                )
                category_cache[cat_key] = category_id

            conn.execute(
                text(_SQL_INSERT_INVOICE_TX),
                {
                    "uid": owner,
                    "account_id": account_id,
                    "category_id": category_id,
                    "description": row["description"],
                    "amount": row["amount"],
                    "due_date": row["due_date"],
                    "payment_date": row["purchase_date"],
                    "type": row["type"],
                    "installment_current": row["installment_current"],
                    "installment_total": row["installment_total"],
                    "installment_group": row["installment_group"],
                },
            )
            inserted += 1

    _clear_controle_caches()
    summary = dict(parsed["summary"])
    summary.update({"inserted": inserted, "skipped": skipped})
    return {
        "ok": True,
        "message": f"{inserted} lançamento(s) importado(s); {skipped} duplicado(s) ignorado(s).",
        "summary": summary,
        "errors": parsed.get("errors", []),
    }


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
            account = conn.execute(
                text(_SQL_ACCOUNT_FOR_MANUAL_INSERT),
                {"account_id": conta_id, "uid": owner},
            ).fetchone()
            if not account:
                return False, "Conta nÃ£o encontrada."
            if account.type == "credit_card":
                return False, "Compras de cartÃ£o de crÃ©dito devem ser importadas pela fatura CSV."

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

        # Invalida caches para forçar reload nas abas afetadas.
        _clear_controle_caches()
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
    conta_id: str,
    tipo: str,
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
                    "account_id":  conta_id,
                    "amount":      valor,
                    "due_date":    data,
                    "type":        tipo,
                },
            )

        _clear_controle_caches()
        return True, ""

    except Exception as exc:
        logger.error("[controle] Falha ao atualizar transação: %s", exc)
        return False, str(exc)


def atualizar_transacao_cartao(
    tx_id: str,
    descricao: str,
    valor: float,
    data_vencimento: _date,
    data_compra: Optional[_date],
    categoria_id: Optional[str],
    conta_id: str,
    installment_current: int,
    installment_total: int,
    status: str,
) -> tuple[bool, str]:
    """
    Atualiza os campos editáveis de um lançamento da fatura de cartão de crédito
    (aba Cartão de Crédito → tabela detalhada). Filtra por OWNER_USER_ID.

    `valor` é o amount ASSINADO já resolvido pela view (mantém o sinal original).
    Preserva type, source e installment_group.
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

        inst_total = max(1, int(installment_total or 1))
        inst_current = min(max(1, int(installment_current or 1)), inst_total)
        status_norm = (str(status or "settled").strip() or "settled")

        with engine.begin() as conn:
            conn.execute(
                text(_SQL_UPDATE_TX_CARTAO),
                {
                    "tx_id":               tx_id,
                    "uid":                 owner,
                    "description":         str(descricao or "").strip(),
                    "category_id":         categoria_id,
                    "account_id":          conta_id,
                    "amount":              float(valor),
                    "due_date":            data_vencimento,
                    "payment_date":        data_compra,
                    "installment_current": inst_current,
                    "installment_total":   inst_total,
                    "status":              status_norm,
                },
            )

        _clear_controle_caches()
        return True, ""

    except Exception as exc:
        logger.error("[controle] Falha ao atualizar transação de cartão: %s", exc)
        return False, str(exc)


# ─────────────────────────────────────────────────────────────────────────────
# Regras de categoria APRENDIDAS pelo usuário (cartão de crédito)
# Quando o importador não sabe classificar um estabelecimento, o usuário define a
# categoria na tela; a escolha vira uma regra (palavra_chave -> categoria) que
# passa a valer automaticamente nas próximas faturas.
# ─────────────────────────────────────────────────────────────────────────────

_SQL_ENSURE_CARD_RULES = """
    CREATE TABLE IF NOT EXISTS card_category_rules (
        id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        user_id       uuid NOT NULL,
        palavra_chave text NOT NULL,
        category_name text NOT NULL,
        created_at    timestamptz NOT NULL DEFAULT now(),
        updated_at    timestamptz NOT NULL DEFAULT now(),
        UNIQUE (user_id, palavra_chave)
    )
"""


def _ensure_card_rules_table(conn) -> None:
    from sqlalchemy import text
    conn.execute(text(_SQL_ENSURE_CARD_RULES))


def get_card_category_rules() -> list[tuple[str, str]]:
    """
    Regras (palavra_chave, categoria) aprendidas pelo usuário, ordenadas da mais
    específica (chave mais longa) para a mais curta. Defensiva: retorna [] se não
    houver banco/owner ou se a tabela ainda não existir (nunca lança).
    """
    if settings.MOCK_MODE or not settings.OWNER_USER_ID:
        return []
    try:
        from sqlalchemy import text
        from core.database import get_engine

        engine = get_engine()
        if engine is None:
            return []
        owner = settings.OWNER_USER_ID
        with engine.connect() as conn:
            rows = conn.execute(
                text("""
                    SELECT palavra_chave, category_name
                    FROM   card_category_rules
                    WHERE  user_id = CAST(:uid AS uuid)
                    ORDER BY length(palavra_chave) DESC
                """),
                {"uid": owner},
            ).fetchall()
        return [(r.palavra_chave, r.category_name) for r in rows]
    except Exception as exc:
        logger.info("[controle] get_card_category_rules indisponível (%s).",
                    type(exc).__name__)
        return []


def add_card_category_rule(palavra_chave: str, category_name: str) -> tuple[bool, str]:
    """Cria/atualiza uma regra do usuário. A chave é normalizada em MAIÚSCULO."""
    if settings.MOCK_MODE:
        return False, "Modo mock ativo."
    from core.card_categorization import _norm_up, CATEGORY_TYPE

    chave = _norm_up(palavra_chave)
    if not chave:
        return False, "Palavra-chave vazia."
    if category_name not in CATEGORY_TYPE:
        return False, f"Categoria inválida: {category_name}"
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
            _ensure_card_rules_table(conn)
            conn.execute(
                text("""
                    INSERT INTO card_category_rules (user_id, palavra_chave, category_name)
                    VALUES (CAST(:uid AS uuid), :kw, :cat)
                    ON CONFLICT (user_id, palavra_chave)
                    DO UPDATE SET category_name = EXCLUDED.category_name,
                                  updated_at    = now()
                """),
                {"uid": owner, "kw": chave, "cat": category_name},
            )
        return True, ""
    except Exception as exc:
        logger.error("[controle] add_card_category_rule falhou: %s", exc)
        return False, str(exc)


def definir_categoria_transacao_cartao(tx_id: str, category_name: str) -> tuple[bool, str]:
    """
    Define a categoria de UM lançamento de cartão (usado na revisão de itens não
    catalogados). Cria/reutiliza a categoria pelo nome e atualiza category_id.
    Não altera transaction.type.
    """
    if settings.MOCK_MODE:
        return False, "Modo mock ativo."
    from core.card_categorization import CATEGORY_TYPE
    if category_name not in CATEGORY_TYPE:
        return False, f"Categoria inválida: {category_name}"
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
            cat_id = _get_or_create_category(
                conn, owner, category_name, CATEGORY_TYPE.get(category_name, "expense"))
            if not cat_id:
                return False, "Falha ao obter/criar a categoria."
            conn.execute(
                text("""
                    UPDATE transactions
                    SET    category_id = CAST(:cid AS uuid)
                    WHERE  id      = CAST(:tid AS uuid)
                      AND  user_id = CAST(:uid AS uuid)
                """),
                {"cid": cat_id, "tid": tx_id, "uid": owner},
            )
        _clear_controle_caches()
        return True, ""
    except Exception as exc:
        logger.error("[controle] definir_categoria_transacao_cartao falhou: %s", exc)
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
        # Em modo real NAO caímos no mock: números financeiros fictícios
        # mascarariam o problema (ex.: "histórico de 2024" que não existe).
        # Devolve vazio e sinaliza o erro real para a UI exibir.
        logger.warning(
            "[controle] get_historico_anual falhou — sem fallback mock em modo real.",
            exc_info=True,
        )
        return {"anos": [], "por_ano": {}, "data_source": "real_error", "error": str(exc)}


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

    except Exception:
        logger.warning(
            "[controle] get_gastos_categoria_anual(%s) falhou — sem fallback mock.",
            ano, exc_info=True,
        )
        return []


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
    except Exception:
        logger.warning("[controle] get_gastos_cartao_mensal falhou — sem fallback mock.", exc_info=True)
        return []


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


@st.cache_data(ttl=300)
def get_gastos_cartao_fatura_mensal(ano: int) -> list:
    """
    Retorna gastos mensais de faturas de cartão importadas via CSV para o ano dado.
    Fonte única de verdade: transactions com account_type='credit_card' e source='csv'.
    Cada item: {"mes": int, "label": str, "total": float}
    """
    if settings.MOCK_MODE:
        return []
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
                text(_SQL_HISTORICO_CC_MENSAL), {"uid": owner}
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
    except Exception as exc:
        logger.warning("[controle] get_gastos_cartao_fatura_mensal falhou (%s).", type(exc).__name__)
        return []


# ─────────────────────────────────────────────────────────────────────────────
# HISTÓRICO CC + DÍVIDAS — API pública
# ─────────────────────────────────────────────────────────────────────────────

@st.cache_data(ttl=300)
def get_historico_cc_mensal() -> list:
    """
    Retorna uso mensal das contas de cartão de crédito (account_type='credit_card').
    Cada item: {"ano", "mes", "label", "total"}
    """
    if settings.MOCK_MODE:
        return []
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
                text(_SQL_HISTORICO_CC_MENSAL), {"uid": owner}
            ).fetchall()

        return [
            {
                "ano":   int(r.ano),
                "mes":   int(r.mes),
                "label": _MESES_PT[int(r.mes)],
                "total": round(float(r.total), 2),
            }
            for r in rows
        ]
    except Exception as exc:
        logger.warning("[controle] get_historico_cc_mensal falhou (%s).", type(exc).__name__)
        return []


@st.cache_data(ttl=60)
def get_transacoes_cartao_credito() -> list:
    """Retorna lancamentos de contas de cartao com metadados de fatura e parcela."""
    if settings.MOCK_MODE:
        return []

    try:
        from sqlalchemy import text
        from core.database import get_engine

        engine = get_engine()
        if engine is None:
            raise RuntimeError("Engine indisponivel.")
        owner = settings.OWNER_USER_ID
        if not owner:
            raise RuntimeError("OWNER_USER_ID nao configurado.")

        with engine.connect() as conn:
            rows = conn.execute(text(_SQL_TRANSACOES_CARTAO), {"uid": owner}).fetchall()

        txs = []
        for r in rows:
            val = float(r.amount) if r.amount is not None else 0.0
            tipo_fluxo = canonical_transaction_type(r.type, r.category_name, val)
            data = r.due_date
            compra = getattr(r, "payment_date", None) or data
            txs.append({
                "id": r.id,
                "descricao": r.description,
                "valor": val,
                "valor_fmt": format_transaction_amount(val, tipo_fluxo),
                "data": data,
                "data_fmt": data.strftime("%d/%m/%Y") if data else "-",
                "tipo": r.type,
                "tipo_fluxo": tipo_fluxo,
                "tipo_label": transaction_type_label(tipo_fluxo),
                "status": r.status,
                "source": getattr(r, "source", None) or "manual",
                "categoria": r.category_name,
                "conta": r.account_name,
                "account_type": getattr(r, "account_type", ""),
                "payment_date": getattr(r, "payment_date", None),
                "data_compra": compra,
                "installment_current": int(getattr(r, "installment_current", 1) or 1),
                "installment_total": int(getattr(r, "installment_total", 1) or 1),
                "installment_group": getattr(r, "installment_group", None),
                "eh_receita": tipo_fluxo == "income",
                "eh_despesa": tipo_fluxo == "expense",
                "eh_investimento": tipo_fluxo == "investment",
            })
        return txs
    except Exception as exc:
        logger.warning("[controle] get_transacoes_cartao_credito falhou (%s).", type(exc).__name__)
        return []


@st.cache_data(ttl=60)
def get_dividas_cc() -> list:
    """
    Retorna parcelamentos agrupados por installment_group (account_type='credit_card').
    Cada item: {grupo, cartao, categoria, data_compra, total_compra,
                total_parcelas, parcelas_pagas, parcelas_restantes, is_ativa, descricao}
    """
    def _enrich(d: dict) -> dict:
        tp = d["total_parcelas"]
        pp = d["parcelas_pagas"]
        return {**d, "parcelas_restantes": tp - pp, "is_ativa": pp < tp}

    if settings.MOCK_MODE:
        return []

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
                text(_SQL_DIVIDAS_CC), {"uid": owner}
            ).fetchall()

        result = []
        for r in rows:
            tp = int(r.total_parcelas) if r.total_parcelas else 1
            pp = int(r.parcelas_pagas) if r.parcelas_pagas else 0
            result.append(_enrich({
                "grupo":          str(r.grupo),
                "cartao":         r.cartao,
                "categoria":      r.categoria,
                "data_compra":    r.data_compra,
                "total_compra":   round(float(r.total_compra), 2),
                "total_parcelas": tp,
                "parcelas_pagas": pp,
                "descricao":      r.descricao or "",
            }))
        return result

    except Exception as exc:
        logger.warning("[controle] get_dividas_cc falhou (%s).", type(exc).__name__)
        return []


# ─────────────────────────────────────────────────────────────────────────────
# MOCK
# ─────────────────────────────────────────────────────────────────────────────

def _controle_mock(ano: int, mes: int) -> dict:
    mes_ref = f"{_MESES_PT[mes]} {ano}"
    receitas = sum(
        v for desc, v, t, _data, cat, _conta in _MOCK_TRANS
        if canonical_transaction_type(t, cat, v) == "income"
    )
    despesas = sum(
        abs(v) for desc, v, t, _data, cat, _conta in _MOCK_TRANS
        if canonical_transaction_type(t, cat, v) == "expense"
    )
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
        tipo_fluxo = canonical_transaction_type(tipo, cat, val)
        trans.append({
            "id":          str(i + 1),
            "descricao":   desc,
            "valor":       val,
            "valor_fmt":   format_transaction_amount(val, tipo_fluxo),
            "data":        data,
            "data_fmt":    data.strftime("%d/%m"),
            "tipo":        tipo,
            "tipo_fluxo":   tipo_fluxo,
            "tipo_label":   transaction_type_label(tipo_fluxo),
            "status":      "settled",
            "categoria":   cat,
            "conta":       conta,
            "eh_receita":  tipo_fluxo == "income",
            "eh_despesa":  tipo_fluxo == "expense",
            "eh_investimento": tipo_fluxo == "investment",
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
        "contas": [{"id": "1", "nome": "Conta Corrente", "tipo": "checking"}],
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
    receitas = sum(
        _f(r.amount) for r in tx_rows
        if canonical_transaction_type(r.type, r.category_name, r.amount) == "income"
    )
    despesas = sum(
        abs(_f(r.amount)) for r in tx_rows
        if canonical_transaction_type(r.type, r.category_name, r.amount) == "expense"
        and not _is_cc(r)
    )
    saldo    = round(receitas - despesas, 2)
    taxa     = round(saldo / receitas * 100, 1) if receitas > 0 else 0.0

    # Orçamentos mapeados
    budget_map: dict[str, float] = {r.category_name: _f(r.amount_limit) for r in budget_rows}

    # Categorias de despesa — idem, sem compras de cartão de crédito
    cat_gastos: dict[str, float] = {}
    for r in tx_rows:
        if canonical_transaction_type(r.type, r.category_name, r.amount) == "expense" and not _is_cc(r):
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
        tipo_fluxo = canonical_transaction_type(r.type, r.category_name, val)
        data = r.due_date
        transacoes.append({
            "id":           r.id,
            "descricao":    r.description,
            "valor":        val,
            "valor_fmt":    format_transaction_amount(val, tipo_fluxo),
            "data":         data,
            "data_fmt":     data.strftime("%d/%m") if data else "—",
            "tipo":         r.type,
            "tipo_fluxo":    tipo_fluxo,
            "tipo_label":    transaction_type_label(tipo_fluxo),
            "status":       r.status,
            "source":       getattr(r, "source", None) or "manual",
            "categoria":    r.category_name,
            "conta":        r.account_name,
            "account_type": getattr(r, "account_type", ""),
            "payment_date": getattr(r, "payment_date", None),
            "data_compra":  getattr(r, "payment_date", None) or data,
            "installment_current": int(getattr(r, "installment_current", 1) or 1),
            "installment_total": int(getattr(r, "installment_total", 1) or 1),
            "installment_group": getattr(r, "installment_group", None),
            "eh_receita":   tipo_fluxo == "income",
            "eh_despesa":   tipo_fluxo == "expense",
            "eh_investimento": tipo_fluxo == "investment",
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
        "contas":     [{"id": r.id, "nome": r.name, "tipo": r.type}   for r in cont_rows],
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

# Mock histórico mensal de compras no cartão de crédito
_MOCK_HIST_CC = {
    1: 7_800.00, 2: 4_280.00, 3: 4_000.00,
    4: 1_200.00, 5: 1_100.00,
}

# Mock de parcelamentos (dívidas no cartão)
_MOCK_DIVIDAS: list[dict] = [
    {
        "grupo": "mock-g1", "cartao": "Cartão C6", "categoria": "Outros",
        "data_compra": _date(2026, 5, 14), "total_compra": 5_600.00,
        "total_parcelas": 10, "parcelas_pagas": 0, "descricao": "Compra parcelada",
    },
    {
        "grupo": "mock-g2", "cartao": "Cartão C6", "categoria": "Outros",
        "data_compra": _date(2025, 12, 8), "total_compra": 3_952.35,
        "total_parcelas": 5, "parcelas_pagas": 5, "descricao": "Passagem da r...",
    },
    {
        "grupo": "mock-g3", "cartao": "Cartão C6", "categoria": "Outros",
        "data_compra": _date(2025, 11, 8), "total_compra": 2_349.00,
        "total_parcelas": 2, "parcelas_pagas": 2, "descricao": "Tablet",
    },
]


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
        por_ano[a] = {
            "receitas":      round(float(r.total_income        or 0), 2),
            "despesas":      round(float(r.total_expenses_abs  or 0), 2),
            "investimentos": round(float(r.total_investments   or 0), 2),
        }

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
        tipo_fluxo = canonical_transaction_type(tp, cat, val)
        raw.append({
            "id": str(i + 1),
            "descricao": desc,
            "valor": val,
            "valor_fmt": format_transaction_amount(val, tipo_fluxo),
            "data": data,
            "data_fmt": data.strftime("%d/%m/%Y"),
            "tipo": tp,
            "tipo_fluxo": tipo_fluxo,
            "tipo_label": transaction_type_label(tipo_fluxo),
            "status": "settled",
            "categoria": cat,
            "conta": conta,
            "eh_receita": tipo_fluxo == "income",
            "eh_despesa": tipo_fluxo == "expense",
            "eh_investimento": tipo_fluxo == "investment",
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
        tipo_fluxo = canonical_transaction_type(r.type, r.category_name, val)
        data = r.due_date
        txs.append({
            "id": r.id,
            "descricao": r.description,
            "valor": val,
            "valor_fmt": format_transaction_amount(val, tipo_fluxo),
            "data": data,
            "data_fmt": data.strftime("%d/%m/%Y") if data else "—",
            "tipo": r.type,
            "tipo_fluxo": tipo_fluxo,
            "tipo_label": transaction_type_label(tipo_fluxo),
            "status": r.status,
            "categoria": r.category_name,
            "conta": r.account_name,
            "eh_receita": tipo_fluxo == "income",
            "eh_despesa": tipo_fluxo == "expense",
            "eh_investimento": tipo_fluxo == "investment",
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
        out = [t for t in out if t.get("tipo_fluxo") == "income"]
    elif tipo == "Despesas":
        out = [t for t in out if t.get("tipo_fluxo") == "expense"]
    elif tipo == "Investimentos":
        out = [t for t in out if t.get("tipo_fluxo") == "investment"]

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
