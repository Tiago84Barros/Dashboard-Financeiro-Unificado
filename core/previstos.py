"""
Lançamentos previstos: contas a pagar e a receber.

Um previsto é uma linha de ``transactions`` com ``status = 'pending'`` numa
conta de movimentação (não cartão). O schema aceita ``pending`` desde a 002;
faltava o app usar. O cartão fica de fora da definição de propósito: a fatura
CSV já usa ``status`` para parcela paga ou não, e é outro fluxo, com tela
própria.

Regra que sustenta o resto: **previsto não é dinheiro**. Ele não entra no
saldo (``v_account_balance`` já soma só ``settled``), nem nos totais do mês,
nem no orçamento, nem nos gastos por categoria. Ele entra em dois lugares: na
aba "A pagar e receber" e na conciliação do extrato, que dá baixa nele quando
o banco mostra o pagamento.

Dar baixa muda ``status`` para ``settled`` e põe ``due_date`` na data em que
o dinheiro saiu ou entrou. O app inteiro lê ``due_date`` como a data do
movimento de caixa (saldo por data, mês do Dashboard, conciliação), então o
vencimento original não fica guardado depois da baixa.
"""
from __future__ import annotations

import calendar
import logging
import uuid
from datetime import date, timedelta
from typing import Optional

from core.config import settings

logger = logging.getLogger(__name__)

STATUS_PREVISTO = "pending"
TIPOS_CONTA_MOVIMENTACAO = ("checking", "savings", "digital_wallet")
MAX_REPETICOES = 60
PRAZO_PROXIMOS = 30


def sql_realizado(t: str = "t", conta: str = "ac") -> str:
    """Predicado SQL que tira os previstos de uma consulta de realizado.

    ``t``: alias de ``transactions``; ``conta``: alias de ``accounts`` já
    juntado na consulta. Linha pendente de cartão continua passando -- não é
    previsto, é parcela de fatura, e as telas do cartão a tratam.
    """
    return (f"NOT ({t}.status = '{STATUS_PREVISTO}' "
            f"AND COALESCE({conta}.type, '') <> 'credit_card')")


# ─────────────────────────────────────────────────────────────────────────────
# Funções puras
# ─────────────────────────────────────────────────────────────────────────────

def conta_escolhida_para_previsto(contas: list[dict], tipo_de) -> str | None:
    """Id da conta onde o previsto é gravado: conta corrente primeiro, depois
    qualquer outra de movimentação. ``tipo_de(conta)`` devolve o tipo."""
    aceitas = [c for c in contas if tipo_de(c) in TIPOS_CONTA_MOVIMENTACAO]
    preferida = next((c for c in aceitas if tipo_de(c) == "checking"), None)
    escolhida = preferida or (aceitas[0] if aceitas else None)
    return escolhida["id"] if escolhida else None


def datas_mensais(primeiro: date, repeticoes: int) -> list[date]:
    """Vencimentos mensais a partir de ``primeiro``. Dia 31 em mês curto cai
    no último dia daquele mês, e volta ao 31 no mês seguinte que o tiver."""
    n = max(1, min(int(repeticoes or 1), MAX_REPETICOES))
    out: list[date] = []
    for k in range(n):
        mes0 = primeiro.month - 1 + k
        ano, mes = primeiro.year + mes0 // 12, mes0 % 12 + 1
        dia = min(primeiro.day, calendar.monthrange(ano, mes)[1])
        out.append(date(ano, mes, dia))
    return out


def situacao(vencimento: date, hoje: date) -> str:
    """'vencido', 'hoje' ou 'em N dias'."""
    dias = (vencimento - hoje).days
    if dias < 0:
        return "vencido"
    if dias == 0:
        return "hoje"
    return f"em {dias} dia{'s' if dias > 1 else ''}"


def resumir(previstos: list[dict], hoje: date, prazo: int = PRAZO_PROXIMOS) -> dict:
    """Totais da aba: vencidos e os que vencem até ``prazo`` dias.

    ``valor`` vem com sinal (saída negativa). A pagar e a receber são somados
    separados em módulo, para o card não mostrar "a pagar: -R$ 500".
    """
    limite = hoje + timedelta(days=prazo)
    vencidos = [p for p in previstos if p["vencimento"] < hoje]
    proximos = [p for p in previstos if hoje <= p["vencimento"] <= limite]
    return {
        "n_vencidos": len(vencidos),
        "total_vencido": round(sum(p["valor"] for p in vencidos), 2),
        "a_pagar": round(sum(-p["valor"] for p in proximos if p["valor"] < 0), 2),
        "a_receber": round(sum(p["valor"] for p in proximos if p["valor"] > 0), 2),
        "prazo": prazo,
    }


def projetar_saldo(saldo_atual: float, previstos: list[dict], hoje: date,
                   ate: date) -> list[dict]:
    """Saldo depois de cada previsto até ``ate``, em ordem de vencimento.

    Vencido entra como se fosse pago hoje: se ainda não saiu da conta, vai
    sair. Previsto depois de ``ate`` fica fora, e também o de conta que não
    entra em ``saldo_atual`` (``na_projecao`` falso): somar o compromisso de
    uma conta cujo saldo não está na base inventaria um rombo.
    """
    eventos = sorted(
        (p for p in previstos
         if p["vencimento"] <= ate and p.get("na_projecao", True)),
        key=lambda p: (max(p["vencimento"], hoje), p["valor"] > 0, p.get("descricao") or ""),
    )
    saldo = float(saldo_atual)
    out = []
    for p in eventos:
        saldo = round(saldo + float(p["valor"]), 2)
        out.append({**p, "data_efeito": max(p["vencimento"], hoje), "saldo_apos": saldo})
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Banco
# ─────────────────────────────────────────────────────────────────────────────

_SQL_CONTA_MOVIMENTACAO = """
    SELECT id::text, COALESCE(type, '') AS type
    FROM accounts
    WHERE id = CAST(:account_id AS uuid) AND user_id = CAST(:uid AS uuid)
"""

# Categoria só se for do sistema (user_id nulo) ou do próprio dono: a chave
# estrangeira não cobra o dono, e o mesmo filtro protege _SQL_INSERT_TX.
_SQL_CATEGORIA_PERMITIDA = """
    SELECT 1 FROM categories
    WHERE id = CAST(:category_id AS uuid)
      AND (user_id IS NULL OR user_id = CAST(:uid AS uuid))
"""

_SQL_INSERIR = """
    INSERT INTO transactions
        (user_id, account_id, category_id, description, amount, due_date,
         type, status, source, installment_current, installment_total,
         installment_group)
    VALUES
        (CAST(:uid AS uuid), CAST(:account_id AS uuid), CAST(:category_id AS uuid),
         :description, :amount, :due_date, :type, 'pending', 'manual',
         :parcela, :total, CAST(:grupo AS uuid))
"""

_SQL_LISTAR = f"""
    SELECT t.id::text, t.description, t.amount, t.due_date, t.type,
           t.category_id::text, COALESCE(c.name, 'Sem categoria') AS categoria,
           COALESCE(ac.name, 'Sem conta') AS conta,
           (COALESCE(ac.type, '') IN ('checking', 'savings', 'digital_wallet')
            AND COALESCE(ac.active, TRUE)) AS na_projecao,
           COALESCE(t.installment_current, 1) AS parcela,
           COALESCE(t.installment_total, 1) AS total_parcelas
    FROM transactions t
    LEFT JOIN categories c ON c.id = t.category_id
                          AND (c.user_id IS NULL OR c.user_id = t.user_id)
    LEFT JOIN accounts ac ON ac.id = t.account_id
    WHERE t.user_id = CAST(:uid AS uuid)
      AND NOT ({sql_realizado()})
    ORDER BY t.due_date, t.created_at
"""

_SQL_BAIXA = """
    UPDATE transactions t
    SET status = 'settled', due_date = :data, payment_date = :data,
        amount = COALESCE(:valor, t.amount)
    FROM accounts ac
    WHERE t.id = CAST(:tx_id AS uuid)
      AND t.user_id = CAST(:uid AS uuid)
      AND t.status = 'pending'
      AND ac.id = t.account_id
      AND COALESCE(ac.type, '') <> 'credit_card'
"""

_SQL_EXCLUIR = """
    DELETE FROM transactions t
    USING accounts ac
    WHERE t.id = CAST(:tx_id AS uuid)
      AND t.user_id = CAST(:uid AS uuid)
      AND t.status = 'pending'
      AND ac.id = t.account_id
      AND COALESCE(ac.type, '') <> 'credit_card'
"""

_SQL_SALDO_MOVIMENTACAO = """
    SELECT COALESCE(SUM(current_balance), 0) AS saldo
    FROM v_account_balance
    WHERE user_id = CAST(:uid AS uuid)
      AND account_type IN ('checking', 'savings', 'digital_wallet')
      AND COALESCE(active, TRUE)
"""


def _engine_e_dono():
    from core.database import get_engine
    engine = get_engine()
    owner = settings.OWNER_USER_ID
    if engine is None or not owner:
        return None, None
    return engine, str(owner)


def _limpar_caches() -> None:
    try:
        from core.controle import _clear_controle_caches
        _clear_controle_caches()
    except Exception:  # noqa: BLE001
        logger.debug("[previstos] caches do controle não limpos", exc_info=True)


def criar_previstos(
    descricao: str,
    valor: float,
    tipo: str,
    vencimento: date,
    categoria_id: Optional[str],
    conta_id: str,
    repeticoes: int = 1,
) -> tuple[bool, str]:
    """Grava um previsto, ou uma série mensal de ``repeticoes`` previstos.

    Sinal igual ao do lançamento manual: entrada positiva, o resto negativo.
    Série leva ``installment_group`` e ``n de N`` para a tela mostrar "3/12".
    """
    if settings.MOCK_MODE:
        return False, "Modo mock ativo — INSERT não executado."
    engine, owner = _engine_e_dono()
    if engine is None:
        return False, "Banco não configurado."
    if float(valor or 0) <= 0:
        return False, "Informe um valor maior que zero."

    from sqlalchemy import text

    amount = abs(float(valor)) if tipo == "income" else -abs(float(valor))
    datas = datas_mensais(vencimento, repeticoes)
    grupo = str(uuid.uuid4()) if len(datas) > 1 else None
    try:
        with engine.begin() as conn:
            conta = conn.execute(text(_SQL_CONTA_MOVIMENTACAO),
                                 {"account_id": conta_id, "uid": owner}).fetchone()
            if not conta:
                return False, "Conta não encontrada."
            if conta.type == "credit_card":
                return False, "Compras de cartão de crédito entram pela fatura CSV."
            if conta.type not in TIPOS_CONTA_MOVIMENTACAO:
                return False, ("Previsto precisa de conta corrente, poupança ou carteira "
                               "digital: é o saldo delas que a projeção usa.")
            if categoria_id and not conn.execute(
                    text(_SQL_CATEGORIA_PERMITIDA),
                    {"category_id": categoria_id, "uid": owner}).fetchone():
                return False, "Categoria indisponível para este usuário."
            for i, d in enumerate(datas, start=1):
                conn.execute(text(_SQL_INSERIR), {
                    "uid": owner, "account_id": conta_id, "category_id": categoria_id,
                    "description": descricao.strip(), "amount": amount, "due_date": d,
                    "type": tipo, "parcela": i, "total": len(datas), "grupo": grupo,
                })
    except Exception as exc:  # noqa: BLE001
        logger.error("[previstos] falha ao gravar: %s", exc)
        return False, str(exc)
    _limpar_caches()
    return True, ""


def listar_previstos() -> list[dict]:
    """Todos os previstos em aberto do usuário, por vencimento."""
    if settings.MOCK_MODE:
        return []
    engine, owner = _engine_e_dono()
    if engine is None:
        return []
    from sqlalchemy import text
    with engine.connect() as conn:
        rows = conn.execute(text(_SQL_LISTAR), {"uid": owner}).fetchall()
    return [{
        "id": r.id, "descricao": r.description or "", "valor": float(r.amount or 0),
        "vencimento": r.due_date, "tipo": r.type, "categoria_id": r.category_id,
        "categoria": r.categoria, "conta": r.conta,
        "na_projecao": bool(r.na_projecao),
        "parcela": int(r.parcela or 1), "total_parcelas": int(r.total_parcelas or 1),
    } for r in rows]


def saldo_contas_movimentacao() -> float | None:
    """Saldo realizado somado das contas de movimentação (sem cartão)."""
    if settings.MOCK_MODE:
        return None
    engine, owner = _engine_e_dono()
    if engine is None:
        return None
    from sqlalchemy import text
    with engine.connect() as conn:
        row = conn.execute(text(_SQL_SALDO_MOVIMENTACAO), {"uid": owner}).fetchone()
    return round(float(row.saldo or 0), 2) if row else None


def dar_baixa(tx_id: str, data_pagamento: date,
              valor_pago: float | None = None) -> tuple[bool, str]:
    """Marca o previsto como pago em ``data_pagamento``.

    ``valor_pago`` (módulo) troca o valor quando o pago difere do previsto --
    juros, multa, desconto. O sinal do previsto é mantido.
    """
    if settings.MOCK_MODE:
        return False, "Modo mock ativo — baixa não executada."
    engine, owner = _engine_e_dono()
    if engine is None:
        return False, "Banco não configurado."
    from sqlalchemy import text
    novo_valor = None
    try:
        with engine.begin() as conn:
            if valor_pago is not None:
                atual = conn.execute(text(
                    "SELECT amount FROM transactions WHERE id = CAST(:id AS uuid) "
                    "AND user_id = CAST(:uid AS uuid)"), {"id": tx_id, "uid": owner}).fetchone()
                if atual is not None:
                    sinal = -1.0 if float(atual.amount or 0) < 0 else 1.0
                    novo_valor = round(sinal * abs(float(valor_pago)), 2)
            res = conn.execute(text(_SQL_BAIXA), {
                "tx_id": tx_id, "uid": owner, "data": data_pagamento, "valor": novo_valor})
            if res.rowcount == 0:
                return False, "Previsto não encontrado ou já baixado."
    except Exception as exc:  # noqa: BLE001
        logger.error("[previstos] falha na baixa: %s", exc)
        return False, str(exc)
    _limpar_caches()
    return True, ""


def excluir_previsto(tx_id: str) -> tuple[bool, str]:
    """Apaga um previsto em aberto. Lançamento pago nunca é apagado por aqui."""
    if settings.MOCK_MODE:
        return False, "Modo mock ativo — exclusão não executada."
    engine, owner = _engine_e_dono()
    if engine is None:
        return False, "Banco não configurado."
    from sqlalchemy import text
    try:
        with engine.begin() as conn:
            res = conn.execute(text(_SQL_EXCLUIR), {"tx_id": tx_id, "uid": owner})
            if res.rowcount == 0:
                return False, "Previsto não encontrado ou já baixado."
    except Exception as exc:  # noqa: BLE001
        logger.error("[previstos] falha ao excluir: %s", exc)
        return False, str(exc)
    _limpar_caches()
    return True, ""
