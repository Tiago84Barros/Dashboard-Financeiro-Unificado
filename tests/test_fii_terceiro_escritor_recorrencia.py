"""O terceiro escritor de ``income_recurrence``: o endpoint ``reports``.

`fii_v2.normalize_reports` gravava ``income_recurrence`` e
``portfolio_income_recurrence`` — os MESMOS nomes que a decisão lê — a partir
de uma série de ``monthlyDividendYield``, com uma cópia local da fórmula
(mínimo de 6 meses em vez de 12) e sem calendário nenhum:

    values = [value for value in yields if value is not None and value >= 0]

Um mês sem relatório, ou com relatório sem o campo, **desaparecia da série** em
vez de entrar como quebra. É o R1 espelhado: R1 contava ausência como zero
(punitivo), este contava ausência como nada (absolvente), e ``positive_share``
é estruturalmente incapaz de ver a lacuna. Medido em 13/09/2026 no armazém
local, esse caminho vencia em 9 tickers de ``market.fiis`` — e passaria a
vencer em mais 12 depois da correção de R1, porque ausência não derruba
presença na leitura por ``knowledge_at``.

A saída escolhida não é unificar: ``monthlyDividendYield`` é uma razão entre
renda e preço, não renda. O coeficiente de variação de um yield carrega a
variação do preço junto, então ele não responde à pergunta que a definição
única define, por mais calendário que se lhe dê. O que não pode continuar é
responder a outra pergunta **sob o mesmo nome**. Ele passa a ter o seu.
"""
from __future__ import annotations

from datetime import datetime, timezone

from core.fii_methodology import COMMON_METRICS, TYPE_METRICS
from data_pipeline.market import fii_v2

NOMES_DA_DECISAO = {"income_recurrence", "portfolio_income_recurrence"}


def _payload(meses: int, yields: list[float] | None = None) -> dict:
    valores = yields if yields is not None else [0.008] * meses
    return {"reports": [
        {"symbol": "TEST11", "referenceDate": f"2026-{mes:02d}-01",
         "monthlyDividendYield": valor}
        for mes, valor in zip(range(1, meses + 1), valores)
    ], "collectedAt": datetime(2026, 9, 20, tzinfo=timezone.utc).isoformat()}


def _metricas(payload: dict) -> set[str]:
    return {linha["metric_name"] for linha in fii_v2.normalize_reports(payload)}


def test_reports_nao_grava_sob_o_nome_que_a_decisao_le():
    """O nome é o que separa as duas grandezas. Enquanto ele for o mesmo, o
    fundo sem linha derivada recebe a regularidade do yield relatado como se
    fosse a recorrência da renda — e ela pesa 0,08, crítica."""
    assert not _metricas(_payload(12)) & NOMES_DA_DECISAO


def test_a_regularidade_do_yield_relatado_continua_observavel():
    """Separar o nome não é apagar a evidência: ela continua gravada, com a
    fórmula e a contagem de meses no metadado, apenas fora da decisão."""
    assert fii_v2.REPORTED_DY_REGULARITY_METRIC in _metricas(_payload(12))


def test_a_metrica_separada_nao_entra_em_nenhum_score():
    definicoes = {definicao.key for definicao in COMMON_METRICS}
    for grupo in TYPE_METRICS.values():
        definicoes |= {definicao.key for definicao in grupo}
    assert fii_v2.REPORTED_DY_REGULARITY_METRIC not in definicoes, (
        "a regularidade do yield relatado voltou a pontuar; ela mede outra "
        "grandeza e não tem calendário")
