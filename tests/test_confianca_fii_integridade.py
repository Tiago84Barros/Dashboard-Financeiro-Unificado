# -*- coding: utf-8 -*-
"""A-132: o check de provento implausivel media duas coisas erradas.

Defeito 1 — acento. O filtro era ``upper(type) NOT IN ('AMORTIZACAO', ...)``,
mas o dado grava ``AMORTIZAÇÃO``. `upper('AMORTIZAÇÃO')` continua acentuado, de
modo que a exclusao NUNCA disparou: as 588 amortizacoes do Supabase entravam
todas como "provento implausivel". Amortizacao devolve capital e e grande por
construcao — e o proprio `core.dividend_types` ja existia para dizer isso.

Defeito 2 — safra. O check comparava `amount` (evento de 2018) com `f.price`
(preco de hoje). Um fundo que amortizou quase todo o capital negocia hoje por
uma fracao do que valia; RBDS11 exibia rendimento de 2018 em 900% do preco de
2026 sem nada de errado no dado. Preco precisa ser o da EPOCA do evento.

Defeito 3 (A-134) — moeda. O preco da epoca vinha de
``market.historical_prices``, que NAO e preco negociado: e preco retroajustado
por split. `amount` e R$/cota bruto do dia do evento. Onde o fundo grupou, o
denominador encolhe e o rendimento parece enorme; onde desdobrou, some. Medido
no armazem local em 02/09/2026: 9 fundos acusados viram 6, e so DOIS coincidem
— saem sete falsos positivos e entram quatro que o ajuste escondia.

Medido no Supabase em 26/08/2026: o check antigo acusava 66 fundos; com as duas
primeiras correcoes sobram 14, dos quais nenhum e amortizacao.
"""
from __future__ import annotations

import re

import pytest

from core import confianca_secao as cs
from core.dividend_types import eh_renda


def test_amortizacao_acentuada_nao_e_renda():
    """A premissa do defeito 1. Se isto falhar, o resto nao faz sentido."""
    assert eh_renda("AMORTIZAÇÃO") is False
    assert eh_renda("amortização") is False
    assert eh_renda("RENDIMENTO") is True


def test_sql_nao_reescreve_a_lista_de_tipos_a_mao():
    """O defeito 1 nasceu de duplicar a regra em vez de importa-la.

    `core.dividend_types.sql_apenas_renda()` e a fonte unica. Um literal
    'AMORTIZACAO' sem acento dentro do modulo e o proprio bug de volta.
    """
    sql = cs.SQL_PROVENTO_IMPLAUSIVEL
    assert "AMORTIZACAO" not in sql, "literal sem acento: a exclusao nao dispara"
    assert "AMORTIZAÇÃO" in sql, "deve vir de sql_apenas_renda()"


def test_sql_compara_com_o_preco_da_epoca_do_evento():
    """O defeito 2. Sem serie de preco datada, a comparacao e entre safras
    diferentes e o resultado nao significa nada."""
    sql = cs.SQL_PROVENTO_IMPLAUSIVEL
    assert re.search(r"ex_date\s*-\s*\d+", sql), "precisa de janela em torno do ex_date"
    assert "f.price" not in sql.split("px_epoca")[0], (
        "o preco de hoje nao pode ser o denominador do evento")


def test_sql_usa_o_preco_negociado_e_nao_o_retroajustado():
    """O defeito 3. `historical_prices.close` e retroajustado por split; usa-lo
    como denominador de um `amount` bruto compara duas moedas.

    A checagem e pelo nome da tabela de proposito: o valor certo e o errado sao
    ambos numeros plausiveis, entao nao existe assercao de comportamento que
    separe os dois sem replicar o banco inteiro. O que se pode fixar e de qual
    serie o preco sai.
    """
    sql = cs.SQL_PROVENTO_IMPLAUSIVEL
    assert "market.fii_b3_security_history" in sql, "preco tem que vir da fita da B3"
    assert "market.historical_prices" not in sql, (
        "serie retroajustada por split de volta ao denominador")


@pytest.mark.parametrize("amount, px, esperado", [
    (1.00, 10.00, False),   # 10% do preco: rendimento normal
    (3.50, 10.00, True),    # 35% do preco num evento so: implausivel
    (3.00, 10.00, False),   # exatamente no limiar nao acusa
])
def test_predicado_de_implausibilidade(amount, px, esperado):
    assert cs._provento_implausivel(amount, px) is esperado


def test_evento_sem_preco_da_epoca_nao_e_julgado():
    """A regra central do modulo aplicada aqui: o que nao foi medido nao vira
    'limpo'. Sao 10.018 dos 38.416 eventos — chama-los de limpos inflaria a
    integridade com ausencia de evidencia."""
    assert cs._provento_implausivel(99.0, None) is None
    assert cs._provento_implausivel(99.0, 0.0) is None


def test_cobertura_do_check_entra_na_evidencia():
    """Integridade apoiada em 74% dos eventos nao vale o mesmo que em 100%, e
    esconder isso seria a omissao que o modulo existe para evitar."""
    comp = cs._componente_integridade_fii(investivel=100, tickers_flag=4,
                                          julgados=740, total=1000)
    assert comp.pct == pytest.approx(96.0)
    assert "74" in comp.evidencia, comp.evidencia


def test_integridade_nao_medida_quando_nada_pode_ser_julgado():
    comp = cs._componente_integridade_fii(investivel=100, tickers_flag=0,
                                          julgados=0, total=1000)
    assert comp.pct is None
