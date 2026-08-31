# -*- coding: utf-8 -*-
"""O portão de estreia precisa excluir quem não existia -- e só ele.

Ver `core/us_primeira_negociacao`: o portão existia em `scoring_history` desde
sempre e nunca disparou, porque `assets.first_trade_date` era NULL nas 7.654
linhas. 11,5% das linhas do painel eram empresa que ainda não negociava na
data; depois da correção sobraram 41, todas sem `first_trade_date` derivável.
"""
from __future__ import annotations

from datetime import date

from core.us_primeira_negociacao import ja_negociava, primeira_negociacao


def test_primeira_negociacao_e_a_menor_barra():
    assert primeira_negociacao([date(2015, 3, 31), date(2013, 7, 31),
                                date(2020, 1, 31)]) == date(2013, 7, 31)


def test_sem_serie_nao_ha_data():
    assert primeira_negociacao([]) is None
    assert primeira_negociacao([None]) is None


def test_empresa_que_ainda_nao_estreou_fica_de_fora():
    assert not ja_negociava(date(2013, 7, 31), date(2010, 6, 30))


def test_empresa_ja_listada_entra():
    assert ja_negociava(date(2003, 1, 31), date(2010, 6, 30))


def test_estreia_no_proprio_dia_conta_como_negociavel():
    assert ja_negociava(date(2010, 6, 30), date(2010, 6, 30))


def test_duvida_nao_exclui():
    """Não ter série de preço não é prova de que o papel não existia.

    Inverter isto trocaria um viés por outro: em vez de incluir quem não
    existia, apagaria da amostra justamente quem tem dado pior -- e quem tem
    dado pior tende a ser quem morreu.
    """
    assert ja_negociava(None, date(1999, 1, 1))
