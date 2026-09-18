# -*- coding: utf-8 -*-
"""A-132: provento de magnitude implausivel ante o preco da EPOCA do evento.

Este modulo e a fonte unica da regra -- limiar, janela, SQL e predicado -- e
tambem do ARTEFATO que a leva para producao.

Por que existe um artefato. O denominador correto do check e o preco
NEGOCIADO no dia do evento, e a unica serie que o serve e a fita oficial da
B3, ``market.fii_b3_security_history``. Ela tem 243 MB e foi retirada do
Supabase de proposito (``warehouse/tables_armazem.txt``,
``warehouse/remote_cleanup.md``): a instancia publicada nao tem esse espaco.
A consequencia e que a consulta ao vivo **nunca** roda em producao -- ela
levanta ``ProgrammingError`` em toda execucao, e ate 18/09/2026 a tela
publicava por isso a frase "nao medido: ProgrammingError" no componente de
peso 0,35 da Selecao de FIIs. Duas coisas erradas nessa frase: ela soa
transitoria para um defeito permanente, e nomeia o passo errado -- nao ha
nada a reparar no banco, a tabela e que mora em outro lugar.

A saida e a mesma de ``core/us_survivorship.py``: quem tem o armazem mede e
grava; a tela em producao le a medicao gravada. Ausencia nao vira zero nem
vira 100 -- ``carregar_medicao`` devolve ``None`` e o componente declara que
saiu, que e a regra de ``core/confianca_secao.py``.

O artefato guarda a LISTA de fundos acusados, nao a contagem. A contagem
seria um numerador vindo do armazem sobre um denominador vindo da producao --
duas safras na mesma fracao, que e exatamente o defeito
[[medicao-comparou-safras-diferentes]]. Com a lista, producao conta quantos
dos acusados estao no PROPRIO universo investivel de hoje, e fracao inteira
passa a sair de uma fonte so.

Medir de novo:

    python scripts/medir_integridade_fii.py
"""
from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path
from typing import Any

from core.dividend_types import sql_apenas_renda

logger = logging.getLogger(__name__)

CAMINHO_MEDICAO = Path(__file__).resolve().parents[1] / "data" / "fii_integridade.json"

#: Fracao do preco da epoca acima da qual um unico provento e implausivel.
LIMIAR_PROVENTO_SOBRE_PRECO = 0.30

#: Janela, em dias, para achar o preco negociado em torno do ex-date. Precisa
#: cobrir feriado prolongado e fundo de baixissima liquidez sem passar perto de
#: um mes, que ja seria outra safra de preco.
JANELA_PRECO_EPOCA_DIAS = 10

# Validade da medicao gravada. Nao e prazo de conveniencia: eventos de renda
# novos chegam todo mes, e uma lista de acusados apurada ha muito tempo nao
# julgou nenhum deles. Vencida, a medicao NAO e publicada com desconto -- ela
# sai da media e diz que saiu, porque um numero velho apresentado como atual e
# pior do que a ausencia ([[aviso-que-envelhece-invertido]]).
VALIDADE_DIAS = 120

# --------------------------------------------------------------------------
# A serie de preco: por que a fita da B3 e nao `market.historical_prices`.
#
#    `historical_prices.close` e retroajustado por split. `amount` e R$/cota
#    bruto do dia do evento. Onde o fundo grupou, o denominador encolhe e o
#    rendimento parece enorme; onde desdobrou, some. A fita da B3 publica o
#    preco como ele foi negociado -- a mesma serie que `core.liquidez` usa para
#    contraditar o cadastro.
#
#    Medido no armazem local em 02/09/2026, sobre 21.151 eventos: 9 fundos
#    acusados viram 6, e so DOIS sao os mesmos. Saem sete falsos positivos
#    (BLMO11, CFII11, FYTO11, HGAG11, HGBS11, MCRE11, RDLI11) e entram quatro
#    que o preco ajustado escondia (BBFI11, HGPO11, PRSN11, TSNC11); ficam
#    FAMB11 e KNRE11. Onze dos treze fundos distintos estavam errados.
#
#    A troca CUSTA cobertura: 74,7% dos eventos julgados contra 79,5%, ou 1.029
#    eventos a menos. Nao e o candle mensal que se perde por estar fora da
#    janela de +-10 dias -- ele quase sempre cai dentro dela, porque ex-date de
#    FII se concentra na virada do mes. O que se perde e fundo que
#    `historical_prices` cobre e a fita da B3 nao. Julgar menos eventos com o
#    preco certo vale mais do que julgar mais com a moeda errada, e a cobertura
#    entra na evidencia justamente para esse desconto aparecer.
#
# Eventos sem preco na epoca NAO sao julgados -- nem limpos, nem sujos.
# Conta-los como limpos infla a integridade com ausencia de evidencia, que e o
# defeito A-124 em outra roupa.
# --------------------------------------------------------------------------

_SELECT_EVENTOS = f"""
WITH ev AS (
  SELECT d.ticker, d.amount,
         (SELECT h.close FROM market.fii_b3_security_history h
           WHERE h.ticker = d.ticker AND h.close > 0
             AND h.trade_date BETWEEN d.ex_date - {JANELA_PRECO_EPOCA_DIAS}
                                  AND d.ex_date + {JANELA_PRECO_EPOCA_DIAS}
           ORDER BY abs(h.trade_date - d.ex_date), h.trade_date,
                    h.collected_at DESC, h.id DESC LIMIT 1) AS px_epoca
    FROM market.dividends d
    JOIN market.fiis f ON f.ticker = d.ticker
   WHERE f.price > 0 AND d.amount > 0 AND d.ex_date IS NOT NULL
     AND {sql_apenas_renda('d.type')})
"""

SQL_PROVENTO_IMPLAUSIVEL = _SELECT_EVENTOS + f"""
SELECT count(*) AS total,
       count(*) FILTER (WHERE px_epoca IS NOT NULL) AS julgados,
       count(DISTINCT ticker) FILTER (
         WHERE px_epoca IS NOT NULL
           AND amount > {LIMIAR_PROVENTO_SOBRE_PRECO} * px_epoca) AS tickers_flag
  FROM ev
"""

#: Os mesmos eventos, devolvendo QUEM foi acusado. E o que vai para o artefato.
SQL_TICKERS_IMPLAUSIVEIS = _SELECT_EVENTOS + f"""
SELECT DISTINCT ticker
  FROM ev
 WHERE px_epoca IS NOT NULL
   AND amount > {LIMIAR_PROVENTO_SOBRE_PRECO} * px_epoca
 ORDER BY ticker
"""


def provento_implausivel(amount: float | None,
                         px_epoca: float | None) -> bool | None:
    """``None`` quando nao ha preco da epoca: o evento nao pode ser julgado."""
    if not px_epoca or px_epoca <= 0 or amount is None:
        return None
    return float(amount) > LIMIAR_PROVENTO_SOBRE_PRECO * float(px_epoca)


# ── o artefato ───────────────────────────────────────────────────────────────

def medir(engine) -> dict[str, Any]:
    """Roda o check onde a fita da B3 existe e devolve a medicao."""
    from sqlalchemy import text
    with engine.connect() as conn:
        linha = conn.execute(text(SQL_PROVENTO_IMPLAUSIVEL)).mappings().one()
        tickers = [r[0] for r in conn.execute(text(SQL_TICKERS_IMPLAUSIVEIS))]
    return {
        "medido_em": date.today().isoformat(),
        "fonte": "armazem local: market.fii_b3_security_history",
        "limiar_provento_sobre_preco": LIMIAR_PROVENTO_SOBRE_PRECO,
        "janela_preco_epoca_dias": JANELA_PRECO_EPOCA_DIAS,
        "eventos_total": int(linha["total"] or 0),
        "eventos_julgados": int(linha["julgados"] or 0),
        "tickers_flag": tickers,
    }


def medicao_coerente(medicao: dict[str, Any] | None) -> bool:
    """Contrato minimo antes de gravar ou de publicar.

    ``tickers_flag`` vazio e resultado legitimo -- nenhum fundo acusado --, mas
    ``eventos_julgados`` zerado nao e: significa que nenhum evento pode ser
    julgado, e nesse caso a medicao nao sustenta nota nenhuma. A contagem de
    acusados tambem nao pode passar do total de eventos julgados.
    """
    if not isinstance(medicao, dict):
        return False
    try:
        julgados = int(medicao["eventos_julgados"])
        total = int(medicao["eventos_total"])
        tickers = medicao["tickers_flag"]
        date.fromisoformat(str(medicao["medido_em"]))
    except (KeyError, TypeError, ValueError):
        return False
    if not isinstance(tickers, list) or any(not isinstance(t, str) for t in tickers):
        return False
    return 0 < julgados <= total and len(tickers) <= julgados


def gravar_medicao(medicao: dict[str, Any],
                   caminho: Path | str = CAMINHO_MEDICAO) -> Path:
    destino = Path(caminho)
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(
        json.dumps(medicao, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return destino


def carregar_medicao(caminho: Path | str = CAMINHO_MEDICAO
                     ) -> dict[str, Any] | None:
    """Ultima medicao gravada, ou ``None`` se nao houver uma utilizavel.

    Ausencia nao vira zero: sem medicao, o componente declara que nao foi
    medido, em vez de afirmar um numero que ninguem apurou.
    """
    try:
        dados = json.loads(Path(caminho).read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        logger.info("medicao de integridade FII indisponivel: %s", type(exc).__name__)
        return None
    return dados if medicao_coerente(dados) else None


def idade_dias(medicao: dict[str, Any] | None,
               hoje: date | None = None) -> int | None:
    if not medicao:
        return None
    try:
        medido = date.fromisoformat(str(medicao["medido_em"]))
    except (KeyError, TypeError, ValueError):
        return None
    return ((hoje or date.today()) - medido).days


def vencida(medicao: dict[str, Any] | None, hoje: date | None = None) -> bool:
    idade = idade_dias(medicao, hoje)
    return idade is None or idade > VALIDADE_DIAS


def flagrados_no_universo(conn, tickers: list[str]) -> int:
    """Quantos dos acusados estao no universo investivel DESTA base.

    O denominador da integridade e ``Universo.investivel``, contado em
    ``market.fiis`` da base que a tela le. O numerador tem de vir da mesma
    contagem, senao a fracao mistura o armazem com a producao.
    """
    if not tickers:
        return 0
    from sqlalchemy import text
    return int(conn.execute(text("""
        SELECT count(*) FROM market.fiis
         WHERE price > 0 AND ticker = ANY(:t)"""), {"t": list(tickers)}).scalar() or 0)
