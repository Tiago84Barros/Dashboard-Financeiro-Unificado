# -*- coding: utf-8 -*-
"""A regra point-in-time por campo não pode consultar o futuro (A-159).

A regra antiga carimbava a linha com o arquivamento mais tardio entre seus
campos. Um campo que só estreou anos depois escondia a linha inteira em toda
safra anterior — e só quem sobreviveu tem anos seguintes em que estrear tags
novas. Medido na coorte de 2012: cobertura 36% para sobreviventes contra 51%
para quem sumiu.
"""
from datetime import date

import pytest

from core import us_pit
from data_pipeline.us import scoring_history as sh
from data_pipeline.us.edgar_facts import (
    _build_rows,
    derivar_balance,
    derivar_cashflow,
)


def _linha_2012():
    """Exercício de 2012 cujo `net_income` só foi tagueado no 10-K de 2015."""
    return {
        "fiscal_year": 2012,
        "available_at": date(2015, 3, 1),
        "revenue": 100.0,
        "net_income": 7.0,
        "filed_at": {"revenue": "2013-02-28", "net_income": "2015-03-01"},
    }


def test_campo_nao_espera_o_arquivamento_mais_tardio():
    vis = us_pit.visiveis([_linha_2012()], date(2013, 6, 30),
                          regra=us_pit.REGRA_CAMPO)
    assert len(vis) == 1, "o que ja era publico em 2013 nao pode sumir"
    assert vis[0]["revenue"] == 100.0
    assert vis[0]["net_income"] is None, "ainda nao existia em 2013-06-30"


def test_regra_por_linha_apaga_o_exercicio_inteiro():
    """O comportamento antigo, preservado como referência do que se corrigiu."""
    assert us_pit.visiveis([_linha_2012()], date(2013, 6, 30),
                           regra=us_pit.REGRA_LINHA) == []


def test_linha_sem_filed_at_mantem_a_regra_antiga():
    """Dado ingerido antes da migration 054 não pode fingir procedência."""
    antiga = {k: v for k, v in _linha_2012().items() if k != "filed_at"}
    assert us_pit.visiveis([antiga], date(2013, 6, 30),
                           regra=us_pit.REGRA_CAMPO) == []
    assert len(us_pit.visiveis([antiga], date(2015, 6, 30),
                               regra=us_pit.REGRA_CAMPO)) == 1


def test_nenhum_campo_conhecido_some_a_linha():
    assert us_pit.visiveis([_linha_2012()], date(2012, 12, 31),
                           regra=us_pit.REGRA_CAMPO) == []


def test_filed_at_em_texto_nao_degrada_para_a_regra_por_linha():
    """JSONB volta como dict no Postgres e como texto em outros drivers.

    Se `filed_map` não aceitasse as duas formas, a regra cairia para a antiga
    conforme o backend — sem erro nenhum, que é como o viés se instalou.
    """
    linha = dict(_linha_2012())
    linha["filed_at"] = '{"revenue": "2013-02-28", "net_income": "2015-03-01"}'
    vis = us_pit.visiveis([linha], date(2013, 6, 30), regra=us_pit.REGRA_CAMPO)
    assert len(vis) == 1 and vis[0]["net_income"] is None


def test_nome_antigo_do_cache_offline_continua_lido():
    linha = dict(_linha_2012())
    linha["_filed"] = linha.pop("filed_at")
    assert len(us_pit.visiveis([linha], date(2013, 6, 30),
                               regra=us_pit.REGRA_CAMPO)) == 1


@pytest.mark.parametrize("derivado,derivar,linha", [
    ("free_cash_flow", derivar_cashflow, {
        "available_at": date(2015, 3, 1),
        "operating_cash_flow": 5.0, "capex": -3.0, "free_cash_flow": 2.0,
        "filed_at": {"operating_cash_flow": "2013-02-28", "capex": "2015-03-01"}}),
    ("invested_capital", derivar_balance, {
        "available_at": date(2015, 3, 1),
        "total_equity": 50.0, "short_term_debt": 10.0, "long_term_debt": 20.0,
        "cash_and_equivalents": 5.0, "total_debt": 30.0, "invested_capital": 75.0,
        "filed_at": {"total_equity": "2013-02-28",
                     "short_term_debt": "2015-03-01",
                     "long_term_debt": "2015-03-01",
                     "cash_and_equivalents": "2013-02-28"}}),
])
def test_derivado_nao_atravessa_a_mascara(derivado, derivar, linha):
    """Look-ahead dentro da correção de look-ahead.

    Enquanto a máscara tinha uma cópia própria das derivações, ela derivava
    menos campos que a ingestão: estes dois sobreviviam com o valor calculado
    sobre o insumo que ainda não era público.
    """
    vis = us_pit.visiveis([linha], date(2013, 6, 30),
                          regra=us_pit.REGRA_CAMPO, derivar=derivar)
    assert vis[0][derivado] is None


def test_visible_rows_da_producao_usa_a_regra_por_campo():
    """O ponto de consumo: é aqui que a safra histórica decide quem pontua."""
    assert len(sh.visible_rows([_linha_2012()], date(2013, 6, 30))) == 1


def test_build_rows_registra_o_arquivamento_de_cada_campo():
    coletado = {
        "revenue": {"2012-12-31": {"val": 100, "filed": "2013-02-28"}},
        "net_income": {"2012-12-31": {"val": 7, "filed": "2015-03-01"}},
    }
    linha = _build_rows(coletado, "ACME")[0]
    assert linha["filed_at"] == {"revenue": "2013-02-28",
                                 "net_income": "2015-03-01"}
    assert linha["available_at"] == date(2015, 3, 1), "available_at inalterado"


def test_filed_at_fica_fora_do_content_hash():
    """Contaminar o hash faria a base inteira parecer alterada na reingestão."""
    coletado = {"revenue": {"2012-12-31": {"val": 100, "filed": "2013-02-28"}}}
    linha = _build_rows(coletado, "ACME")[0]
    sem_procedencia = {k: v for k, v in linha.items()
                       if k not in ("content_hash", "filed_at")}
    from data_pipeline.us.normalize import content_hash
    assert linha["content_hash"] == content_hash(sem_procedencia)
