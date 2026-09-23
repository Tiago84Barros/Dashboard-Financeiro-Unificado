"""
core/precos_medios_manuais.py
=============================
Preço médio que o próprio investidor declara, por ticker.

Existe porque nem sempre há fonte. A carteira tira o preço médio, nesta
ordem, das notas de negociação importadas e do extrato "Posição Detalhada"
da B3 -- e há ativo em que nenhuma das duas sabe o número. O BBAS3 é o caso
que originou este módulo: o relatório de Negociação da B3 começa em
nov/2019, as compras anteriores não existem em lugar nenhum, e o valor que
o app exibia como declarado pela B3 tinha sido DIGITADO À MÃO pelo usuário
numa planilha de posição. O app apresentava esse palpite com a mesma
autoridade de um número da custódia central.

A saída não é esconder o palpite: é rotulá-lo. Aqui ele entra por uma porta
própria, com procedência própria (`custo_fonte = "informado_pelo_usuario"`),
e a tela diz "informado por você". O número passa a ser rastreável até quem
o afirmou.

Armazenamento: tabela `investment_manual_costs` (migration 074). Fica fora
de `portfolio_positions` e dos snapshots de propósito -- as duas são
reescritas por inteiro a cada recálculo ou upload, e levariam a declaração
do usuário junto.
"""
from __future__ import annotations

import logging
import re

from sqlalchemy import text

from core.database import get_engine

logger = logging.getLogger(__name__)

CUSTO_FONTE = "informado_pelo_usuario"


def normalizar_ticker(ticker: str) -> str:
    """Ticker-base: maiúsculas, sem espaços e sem o sufixo F do fracionário.

    Delega para `core.investimentos._base_ticker` de propósito, em vez de
    reimplementar a regra: a chave gravada aqui precisa bater EXATAMENTE com
    a chave pela qual a carteira agrupa as posições (BBAS3F entra em BBAS3).
    Duas cópias da mesma regra divergem no dia em que uma das duas muda, e a
    declaração do usuário simplesmente deixaria de ser encontrada -- sem
    erro, sem aviso, com a tela voltando a exibir a fonte anterior.

    Import tardio para não fechar ciclo: `core.investimentos` importa este
    módulo no topo.
    """
    from core.investimentos import _base_ticker

    return _base_ticker(re.sub(r"\s+", "", str(ticker or "")))


_SQL_LISTAR = """
    SELECT ticker, average_price, note, updated_at
    FROM investment_manual_costs
    WHERE user_id = :uid
"""


def listar(user_id: str, conn=None) -> dict[str, dict]:
    """{ticker_base: {"preco_medio": float, "nota": str, "atualizado_em": ...}}.

    Devolve dict vazio -- nunca levanta -- quando a tabela ainda não existe
    no banco. A migration 074 é rodada à mão; até lá a carteira tem de
    continuar montando, só sem esta camada.

    `conn` reaproveita uma conexão já aberta. Quando ela vem de fora, a
    leitura roda dentro de um SAVEPOINT (`begin_nested`): sem ele, a tabela
    ausente -- o estado normal até a migration 074 ser rodada à mão -- não
    só falharia aqui, como deixaria a transação do chamador ABORTADA, e
    todas as consultas seguintes da carteira morreriam com
    "current transaction is aborted". O `except` capturaria a exceção e o
    plano B nunca entregaria número nenhum.
    """
    if not user_id:
        return {}
    try:
        if conn is not None:
            with conn.begin_nested():
                rows = conn.execute(text(_SQL_LISTAR), {"uid": user_id}).fetchall()
        else:
            with get_engine().connect() as own:
                rows = own.execute(text(_SQL_LISTAR), {"uid": user_id}).fetchall()
    except Exception as exc:  # noqa: BLE001
        logger.info("precos_medios_manuais.listar indisponivel: %s", exc)
        return {}

    return {
        normalizar_ticker(r[0]): {
            "preco_medio":   float(r[1] or 0),
            "nota":          r[2] or "",
            "atualizado_em": r[3],
        }
        for r in rows
        if float(r[1] or 0) > 0
    }


def salvar(user_id: str, ticker: str, preco_medio: float, nota: str = "") -> None:
    """Grava (ou substitui) a declaração do usuário para um ticker."""
    tk = normalizar_ticker(ticker)
    if not user_id or not tk:
        raise ValueError("usuário e ticker são obrigatórios")
    if not preco_medio or float(preco_medio) <= 0:
        raise ValueError("o preço médio precisa ser maior que zero")

    with get_engine().connect() as conn:
        with conn.begin():
            conn.execute(
                text("""
                    INSERT INTO investment_manual_costs
                        (user_id, ticker, average_price, note)
                    VALUES (:uid, :tk, :pm, :nota)
                    ON CONFLICT (user_id, ticker) DO UPDATE SET
                        average_price = EXCLUDED.average_price,
                        note          = EXCLUDED.note,
                        updated_at    = NOW()
                """),
                {"uid": user_id, "tk": tk, "pm": float(preco_medio),
                 "nota": (nota or "").strip() or None},
            )


def remover(user_id: str, ticker: str) -> bool:
    """Apaga a declaração. A carteira volta sozinha para a fonte anterior."""
    tk = normalizar_ticker(ticker)
    if not user_id or not tk:
        return False
    with get_engine().connect() as conn:
        with conn.begin():
            res = conn.execute(
                text("""
                    DELETE FROM investment_manual_costs
                    WHERE user_id = :uid AND ticker = :tk
                """),
                {"uid": user_id, "tk": tk},
            )
    return bool(res.rowcount)
