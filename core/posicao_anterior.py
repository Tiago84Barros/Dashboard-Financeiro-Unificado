"""
core/posicao_anterior.py
========================
Posição que o investidor já tinha antes do primeiro extrato de negociação.

O relatório de Negociação da B3 começa em nov/2019. Venda de ativo comprado
antes disso não tem custo, e o lucro dela ficava fora do ganho total e da
apuração do IR (`ir_renda_variavel._realizacoes` marca a parte como
``valor_sem_custo``). Quem sabe o número é o próprio investidor: a linha do
ativo em Bens e Direitos da declaração de IR.

Aqui ele declara quantidade e custo total por ticker, e `como_compras`
transforma isso numa compra sintética datada antes de qualquer nota -- o
cálculo de preço médio segue igual, só que agora começa do saldo certo.

Armazenamento: `investment_opening_positions` (schema 080), separada da
`investment_manual_costs` (074) de propósito: aquela é o preço médio da
posição atual; esta é o saldo de abertura.
"""
from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

from sqlalchemy import text

from core.database import get_engine
from core.precos_medios_manuais import normalizar_ticker

logger = logging.getLogger(__name__)

# Antes de qualquer nota: a agenda do IR ordena por data em texto.
DATA_ABERTURA = date(1900, 1, 1)

_DDL = (Path(__file__).resolve().parent.parent
        / "supabase_unificado" / "schema" / "080_posicao_anterior_extrato.sql")

_SQL_LISTAR = """
    SELECT ticker, quantity, total_cost, note, updated_at
    FROM investment_opening_positions
    WHERE user_id = :uid
"""


def listar(user_id: str, conn=None) -> dict[str, dict]:
    """{ticker_base: {"quantidade", "custo_total", "nota", "atualizado_em"}}.

    Dict vazio -- nunca levanta -- enquanto a tabela não existe. Com `conn`
    de fora, lê dentro de SAVEPOINT para não abortar a transação do chamador
    (mesmo motivo de `precos_medios_manuais.listar`).
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
        logger.info("posicao_anterior.listar indisponivel: %s", exc)
        return {}
    return {
        normalizar_ticker(r[0]): {
            "quantidade":    float(r[1] or 0),
            "custo_total":   float(r[2] or 0),
            "nota":          r[3] or "",
            "atualizado_em": r[4],
        }
        for r in rows
        if float(r[1] or 0) > 0 and float(r[2] or 0) > 0
    }


def como_compras(abertura: dict[str, dict], transacoes: list[dict]) -> list[dict]:
    """Compras sintéticas, em `DATA_ABERTURA`, para os tickers declarados.

    Só entra ticker que aparece nas negociações: é delas que sai a classe
    (ação, FII, ETF...) que decide a cesta do IR, e sem venda a abertura não
    muda resultado nenhum.
    """
    classe_por_ticker: dict[str, str] = {}
    for tx in transacoes:
        classe_por_ticker.setdefault(normalizar_ticker(tx.get("ticker")), tx.get("classe"))
    compras = []
    for tk, d in sorted(abertura.items()):
        if tk not in classe_por_ticker:
            continue
        qtd, custo = float(d["quantidade"]), float(d["custo_total"])
        compras.append({
            "transaction_date": DATA_ABERTURA, "ticker": tk,
            "classe": classe_por_ticker[tk], "type": "buy",
            "quantity": qtd, "unit_price": custo / qtd, "fees": 0,
        })
    return compras


def garantir_tabela(conn) -> None:
    """Roda o 080 (idempotente) para a tela funcionar sem passo manual."""
    conn.exec_driver_sql(_DDL.read_text(encoding="utf-8"))


def salvar(user_id: str, ticker: str, quantidade: float, custo_total: float,
           nota: str = "") -> None:
    """Grava (ou substitui) a posição de abertura de um ticker."""
    tk = normalizar_ticker(ticker)
    if not user_id or not tk:
        raise ValueError("usuário e ticker são obrigatórios")
    if not quantidade or float(quantidade) <= 0:
        raise ValueError("a quantidade precisa ser maior que zero")
    if not custo_total or float(custo_total) <= 0:
        raise ValueError("o custo total precisa ser maior que zero")

    with get_engine().connect() as conn:
        with conn.begin():
            garantir_tabela(conn)
            conn.execute(
                text("""
                    INSERT INTO investment_opening_positions
                        (user_id, ticker, quantity, total_cost, note)
                    VALUES (:uid, :tk, :q, :c, :nota)
                    ON CONFLICT (user_id, ticker) DO UPDATE SET
                        quantity   = EXCLUDED.quantity,
                        total_cost = EXCLUDED.total_cost,
                        note       = EXCLUDED.note,
                        updated_at = NOW()
                """),
                {"uid": user_id, "tk": tk, "q": float(quantidade),
                 "c": float(custo_total), "nota": (nota or "").strip() or None},
            )


def remover(user_id: str, ticker: str) -> bool:
    """Apaga a declaração; a venda volta a contar como sem custo."""
    tk = normalizar_ticker(ticker)
    if not user_id or not tk:
        return False
    with get_engine().connect() as conn:
        with conn.begin():
            res = conn.execute(
                text("""
                    DELETE FROM investment_opening_positions
                    WHERE user_id = :uid AND ticker = :tk
                """),
                {"uid": user_id, "tk": tk},
            )
    return bool(res.rowcount)
