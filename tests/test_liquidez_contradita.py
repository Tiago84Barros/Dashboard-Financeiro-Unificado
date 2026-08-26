# -*- coding: utf-8 -*-
"""A-133: a liquidez declarada pela brapi contradiz a fita oficial da B3.

Medido em 26/08/2026 sobre os 306 FIIs aptos, comparando
``market.fiis.liquidez_diaria`` com o ADTV que
``data_pipeline.market.fii.liquidez_diaria_b3`` calcula a partir do volume
financeiro oficial em ``market.fii_b3_security_history``:

  SHPP11   declara 2.794.163/dia   fita diz       721/dia   3.874x
  VVRI11   declara    68.561/dia   fita diz       541/dia     127x
  ZIFI11   declara     3.861/dia   fita diz        86/dia      45x
  BICE11   declara     1.852/dia   fita diz        42/dia      44x

O app JA tem o estimador certo -- e ele e rigoroso: mediana de seis meses
fechados, mes incompleto excluido, mes sem negocio contando como zero. So que
ele so era chamado para PREENCHER LACUNA (``liquidity_candidates`` era o
conjunto dos ``Liquidez_Diaria`` nulos). Quando o cadastro trazia um numero,
por mais absurdo que fosse, ninguem conferia.

POR QUE ISSO E PERIGOSO E NAO SO IMPRECISO
------------------------------------------
O piso de liquidez existe (``policy.min_daily_liquidity``, aplicado em quatro
pontos de ``core/fii_portfolio_v4.py``). Ele funciona. O que o derrota nao e a
regra, e a entrada: SHPP11 declarando 2,79 milhoes passa por qualquer piso
razoavel. O investidor le que pode sair de uma posicao que, na fita da bolsa,
negocia setecentos reais por dia.

A DIRECAO DA PREFERENCIA
------------------------
Entre a estimativa de um agregador e o volume financeiro que a propria B3
publicou, a fita ganha. Nao por desconfianca do agregador, mas porque uma delas
e o registro do negocio e a outra e uma leitura dele.

Nao se descarta o ticker: substitui-se o numero. Descartar apagaria fundo
liquido de verdade cuja fita esteja apenas incompleta -- e por isso a regra
exige que a observacao tenha lastro (``observed_months``) antes de valer.
"""
from __future__ import annotations

import pytest

from core.liquidez import FATOR_CONTRADICAO, MESES_MINIMOS, liquidez_para_decisao


def test_sem_observacao_mantem_o_declarado():
    """Fita ausente nao e prova de nada. O declarado segue valendo."""
    r = liquidez_para_decisao(500_000.0, None, meses_observados=0)
    assert r.valor == 500_000.0
    assert r.origem == "declarada"


def test_observacao_curta_nao_derruba_o_declarado():
    """Dois meses de fita nao bastam para desmentir: pode ser lacuna de carga."""
    r = liquidez_para_decisao(500_000.0, 100.0, meses_observados=MESES_MINIMOS - 1)
    assert r.valor == 500_000.0
    assert r.origem == "declarada"


def test_shpp11_a_fita_ganha():
    """O caso medido: 2,79 mi declarados contra 721 observados."""
    r = liquidez_para_decisao(2_794_163.29, 721.34, meses_observados=6)
    assert r.valor == pytest.approx(721.34)
    assert r.origem == "fita_b3"
    assert r.razao == pytest.approx(2_794_163.29 / 721.34, rel=1e-6)


def test_divergencia_pequena_nao_move_nada():
    """Agregador e bolsa raramente batem na casa decimal. Ruido nao e conflito."""
    r = liquidez_para_decisao(100_000.0, 90_000.0, meses_observados=6)
    assert r.valor == 100_000.0
    assert r.origem == "declarada"


def test_o_fator_e_o_limite_e_ele_e_inclusivo():
    r = liquidez_para_decisao(FATOR_CONTRADICAO * 1_000.0, 1_000.0, meses_observados=6)
    assert r.origem == "fita_b3"


def test_declarada_muito_ABAIXO_da_fita_tambem_e_contradicao():
    """Subestimar liquidez exclui fundo negociavel do universo -- erro menos
    perigoso, mas erro. A regra e simetrica."""
    r = liquidez_para_decisao(1_000.0, 500_000.0, meses_observados=6)
    assert r.valor == 500_000.0
    assert r.origem == "fita_b3"


def test_declarada_ausente_usa_a_fita_sem_exigir_contradicao():
    """E o comportamento antigo de preenchimento de lacuna, preservado."""
    r = liquidez_para_decisao(None, 4_200.0, meses_observados=6)
    assert r.valor == 4_200.0
    assert r.origem == "fita_b3"


def test_nada_de_nada_devolve_ausente():
    r = liquidez_para_decisao(None, None, meses_observados=0)
    assert r.valor is None
    assert r.origem == "ausente"


def test_fita_zero_com_lastro_desmente_o_declarado():
    """Zero e um numero, nao uma falta: seis meses fechados sem um negocio."""
    r = liquidez_para_decisao(50_000.0, 0.0, meses_observados=6)
    assert r.valor == 0.0
    assert r.origem == "fita_b3"


def test_valor_negativo_e_descartado_como_invalido():
    r = liquidez_para_decisao(-1.0, None, meses_observados=0)
    assert r.valor is None and r.origem == "ausente"


def test_motivo_e_legivel_para_a_tela():
    r = liquidez_para_decisao(2_794_163.29, 721.34, meses_observados=6)
    assert "fita" in r.motivo.lower() and "3874" in r.motivo.replace(".", "")


def test_meses_ausente_nao_explode_e_nao_da_lastro():
    """O merge de `quality` deixa NaN em ticker que nao entrou na fita.

    A primeira versao fazia `int(meses or 0)` -- e NaN e truthy, entao ia
    inteiro para o `int()` e estourava `ValueError` na publicacao da vitrine.
    Ausencia de contagem e ausencia de lastro, nao erro.
    """
    for vazio in (float("nan"), None, "", "abc"):
        d = liquidez_para_decisao(2_794_163.0, 721.0, meses_observados=vazio)
        assert d.origem == "declarada", vazio
        assert "sem lastro" in d.motivo
