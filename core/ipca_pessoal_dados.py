"""Leitura dos lançamentos para o IPCA Pessoal e a conta do mês.

Usa a conexão única do app (``core.database.get_engine``). A conta mora em
``core.ipca_pessoal``; a série do IBGE, em ``core.ipca_grupos``.
"""
from __future__ import annotations

import logging
from datetime import date

from core.config import settings
from core.ipca_grupos import ler_ipca_grupos_publicado
from core.ipca_pessoal import (
    Lancamento,
    ResultadoIpcaPessoal,
    calcular_ipca_pessoal,
    somar_meses,
)
from core.previstos import sql_realizado
from core.user_context import user_cache_data

logger = logging.getLogger(__name__)

# 13 meses fazem a janela; 24 dão folga para a janela recuar até o último mês
# publicado pelo IBGE.
MESES_LIDOS = 24

_SQL = """
    SELECT t.due_date,
           t.amount,
           COALESCE(c.name, '')              AS categoria,
           COALESCE(t.description, '')       AS descricao,
           COALESCE(t.type, '')              AS tipo,
           COALESCE(ac.type, '')             AS conta_tipo,
           COALESCE(t.source, '')            AS fonte,
           COALESCE(t.installment_total, 1)  AS parcela_total
    FROM   transactions t
    LEFT JOIN accounts   ac ON ac.id = t.account_id
    LEFT JOIN categories c  ON c.id  = t.category_id
    WHERE  t.user_id = :uid
      AND  t.amount < 0
      AND  t.due_date >= :inicio
      AND  t.due_date <  :fim
      AND  """ + sql_realizado("t", "ac")


@user_cache_data(ttl=300)
def carregar_lancamentos_ipca(ano: int, mes: int) -> list[Lancamento] | None:
    """Saídas realizadas dos 24 meses até ``ano/mes``; ``None`` se a leitura falha."""
    if settings.MOCK_MODE:
        return lancamentos_mock(ano, mes)
    pedido = date(ano, mes, 1)
    params = {"uid": settings.OWNER_USER_ID,
              "inicio": somar_meses(pedido, 1 - MESES_LIDOS),
              "fim": somar_meses(pedido, 1)}
    try:
        from sqlalchemy import text

        from core.database import get_engine

        with get_engine().connect() as conn:
            rows = conn.execute(text(_SQL), params).fetchall()
    except Exception as exc:  # noqa: BLE001 -- a tela nomeia a falha
        logger.warning("ipca_pessoal: leitura dos lançamentos falhou: %s", exc)
        return None
    return [
        Lancamento(data=r.due_date, valor=float(r.amount), categoria=r.categoria,
                   descricao=r.descricao, tipo=r.tipo, conta_tipo=r.conta_tipo,
                   fonte=r.fonte, parcela_total=int(r.parcela_total or 1))
        for r in rows
    ]


def ipca_pessoal_do_mes(ano: int, mes: int) -> ResultadoIpcaPessoal | None:
    """A conta para o mês escolhido; ``None`` quando a leitura dos lançamentos falha."""
    lancamentos = carregar_lancamentos_ipca(ano, mes)
    if lancamentos is None:
        return None
    return calcular_ipca_pessoal(lancamentos, ler_ipca_grupos_publicado(), ano, mes)


# Nomes que giram a cada 7 meses: nenhum aparece 3 vezes em 13 meses, então
# Alimentação e Transporte caem na estimativa oficial; Moradia usa os itens.
_LOJAS = ("Supermercado Bom Preço", "Atacadão Centro", "Feira do Produtor",
          "Mercadinho da Esquina", "Hortifruti Verde", "Açougue Boi Gordo",
          "Padaria Trigo Bom")
_POSTOS = ("Posto Shell Centro", "Posto Ipiranga Sul", "Uber", "Posto BR Norte",
           "Estacionamento Shopping", "99 Táxi", "Posto Ale Rodovia")


def lancamentos_mock(ano: int, mes: int) -> list[Lancamento]:
    """Cesta fictícia e determinística de 24 meses, para o modo de teste."""
    pedido = date(ano, mes, 1)
    saida: list[Lancamento] = []
    for k in range(MESES_LIDOS):
        m = somar_meses(pedido, k - (MESES_LIDOS - 1))
        saida += [
            Lancamento(date(m.year, m.month, 5), -round(800 * 1.005 ** k, 2),
                       "Moradia", "Condomínio Edifício Sol", "expense", "checking",
                       "manual"),
            Lancamento(date(m.year, m.month, 12), -round(180 * 1.01 ** k, 2),
                       "Moradia", "Conta de Luz", "expense", "checking", "manual"),
            Lancamento(date(m.year, m.month, 8), -1200.0, "Alimentação",
                       _LOJAS[k % len(_LOJAS)], "expense", "checking", "manual"),
            Lancamento(date(m.year, m.month, 15), -450.0, "Transporte",
                       _POSTOS[k % len(_POSTOS)], "expense", "checking", "manual"),
        ]
    return saida
