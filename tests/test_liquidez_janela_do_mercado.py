# -*- coding: utf-8 -*-
"""A-135: a janela de liquidez seguia o ticker, nao o mercado.

`liquidez_diaria_b3` ancorava os seis meses fechados no ULTIMO PREGAO DO
PROPRIO FUNDO. Para quem negocia, os dois sao a mesma coisa. Para quem parou de
negociar, nao: a janela para junto e o numero publicado vira a liquidez que o
fundo TEVE, com a cara de liquidez que ele TEM.

Medido em 18/09/2026, com a fita oficial completa ate 14/07/2026 (17 arquivos
COTAHIST, todos `completed`, 330 a 366 tickers por mes ate o fim), 36 fundos
investiveis publicavam numero assim:

    BBPO11   R$ 1.480.894/dia   janela encerrada em 08/2023
    IRDM11   R$ 3.294.362/dia   janela encerrada em 09/2025
    RBRF11   R$ 1.993.996/dia   janela encerrada em 09/2025
    FEXC11   R$   978.706/dia   janela encerrada em 10/2022
    BZLI11   R$    79.141/dia   janela encerrada em 07/2019

Os tres primeiros passavam folgados no piso de R$ 1 milhao/dia da politica:
entravam em carteira como liquidos sem ter negociado por nove meses a tres
anos. O piso existe exatamente para impedir isso, e era derrotado pela entrada,
nao pela regra -- o mesmo padrao do A-133, agora no eixo do tempo.

A correcao ancora a janela no ultimo pregao da FITA INTEIRA. Com isso, mes sem
negocio dentro da janela deixa de ser lacuna e passa a ser observacao, e o
valor pode ser `0.0`. Zero medido e um fato: o fundo nao negociou. E o fato que
protege o investidor, porque nenhum piso de liquidez o aprova.

O que NAO pode acontecer junto (e por isso cada guarda abaixo existe): fundo
recem-listado nao negociou nos meses em que ainda nao existia, e transformar
essa ausencia em zero seria condenar o novo pelo que ele nao teve chance de
fazer. Por isso mes anterior a primeira observacao sai da janela em vez de
entrar como zero, e o piso de lastro passa a contar MESES LISTADOS, nao meses
com negocio -- confundir os dois e o que fazia "nao negociou" ficar
indistinguivel de "nao deu para olhar".
"""
import ast
import datetime as dt
from pathlib import Path

import pytest

import data_pipeline.market.fii as fii
from core.liquidez import liquidez_para_decisao

_REF = "2026-07-14"  # ultimo pregao da fita inteira


def _mes(ano, mes, volume, dias=(5, 15, 25)):
    return [(f"{ano:04d}-{mes:02d}-{dia:02d}", volume) for dia in dias]


def _historico(meses, volume_mes):
    """Um fundo que negociou `volume_mes` em cada um dos `meses` dados."""
    linhas = []
    for ano, mes in meses:
        linhas += _mes(ano, mes, volume_mes / 3.0)
    return linhas


# --- a janela segue o mercado ----------------------------------------------

def test_fundo_que_parou_de_negociar_nao_publica_a_liquidez_de_antes():
    # 1 milhao/dia em 2023 e nada depois. A fita vai ate julho de 2026.
    history = _historico([(2023, m) for m in range(1, 7)], 21_000_000.0)

    r = fii.liquidez_diaria_b3(history, referencia=_REF)

    assert r["value"] == 0.0
    assert r["observed_months"] == 0
    assert r["lastro_months"] == 6


def test_sem_referencia_a_janela_ainda_segue_o_ticker():
    """O caminho antigo continua existindo -- e e ele que produzia o numero
    errado. A guarda documenta a diferenca: mesma serie, 1 milhao/dia."""
    history = _historico([(2023, m) for m in range(1, 7)], 21_000_000.0)

    r = fii.liquidez_diaria_b3(history)

    assert r["value"] == pytest.approx(1_000_000.0)
    assert r["lastro_months"] == r["observed_months"]


def test_o_zero_medido_e_datado_pela_fita_e_nao_pelo_ultimo_negocio():
    """Sem negocio na janela nao ha pregao para carimbar. A data que sustenta o
    zero e ate quando a fita foi observada -- herdar a do cadastro faria a
    linhagem do zero apontar para a fonte que ele desmente."""
    history = _historico([(2023, m) for m in range(1, 7)], 21_000_000.0)

    r = fii.liquidez_diaria_b3(history, referencia=_REF)

    assert r["available_at"] == dt.date(2026, 7, 14)


# --- recem-listado nao e fundo parado --------------------------------------

def test_mes_anterior_a_listagem_nao_entra_como_zero():
    """Listado em marco, negociou todo mes desde entao. A mediana e dos quatro
    meses listados (250/dia); contar janeiro e fevereiro como zero daria 150."""
    history = (_mes(2026, 3, 700.0) + _mes(2026, 4, 1_400.0)
               + _mes(2026, 5, 2_100.0) + _mes(2026, 6, 2_800.0))

    r = fii.liquidez_diaria_b3(history, referencia=_REF)

    assert r["value"] == pytest.approx(250.0)
    assert r["lastro_months"] == 4


def test_recem_listado_sem_lastro_nao_vira_zero():
    """Dois meses de vida nao sustentam afirmacao nenhuma sobre liquidez --
    nem a de que ela existe, nem a de que ela e nula."""
    history = _mes(2026, 5, 700.0) + _mes(2026, 6, 700.0)

    r = fii.liquidez_diaria_b3(history, referencia=_REF)

    assert r["value"] is None
    assert r["lastro_months"] == 2


def test_ticker_fora_da_fita_continua_sem_numero():
    """Ausencia de dado e ausencia de negocio nao sao a mesma coisa. Fundo que
    nao aparece na fita nao foi observado, e sobre ele nao ha zero a declarar."""
    r = fii.liquidez_diaria_b3([], referencia=_REF)

    assert r["value"] is None
    assert r["lastro_months"] == 0


# --- o zero precisa sobreviver a arbitragem --------------------------------

def test_o_lastro_do_zero_derruba_a_liquidez_declarada():
    """A fita observou seis meses sem negocio; o cadastro anuncia 1,48
    milhao/dia. Se a arbitragem recebesse MESES COM NEGOCIO (zero) em vez do
    lastro, leria "sem lastro para desmentir" e manteria o numero do cadastro
    -- que e exatamente o que o A-135 corrige."""
    history = _historico([(2023, m) for m in range(1, 7)], 21_000_000.0)
    r = fii.liquidez_diaria_b3(history, referencia=_REF)

    d = liquidez_para_decisao(1_480_894.37, r["value"],
                              meses_observados=r["lastro_months"])

    assert d.origem == "fita_b3"
    assert d.valor == 0.0


# --- a fiacao, que e onde isto ja se perdeu antes --------------------------

def test_quem_le_a_fita_passa_a_ancora_do_mercado():
    """`core.market_read` e o unico consumidor em producao. Medir certo e
    chamar errado publicaria o numero velho de novo, sem erro visivel -- por
    isso a guarda e sobre a chamada, nao sobre o resultado."""
    fonte = Path(__file__).resolve().parents[1] / "core" / "market_read.py"
    arvore = ast.parse(fonte.read_text(encoding="utf-8"))

    chamadas = [
        no for no in ast.walk(arvore)
        if isinstance(no, ast.Call)
        and isinstance(no.func, ast.Attribute)
        and no.func.attr == "liquidez_diaria_b3"
    ]

    assert chamadas, "market_read deixou de medir a liquidez da fita"
    for chamada in chamadas:
        assert any(kw.arg == "referencia" for kw in chamada.keywords), (
            "chamada sem `referencia`: a janela voltaria a seguir o ticker")


def test_quem_le_a_fita_consome_o_lastro_e_nao_os_meses_com_negocio():
    fonte = (Path(__file__).resolve().parents[1] / "core" / "market_read.py"
             ).read_text(encoding="utf-8")

    assert "lastro_months" in fonte, (
        "market_read voltaria a alimentar a arbitragem com meses de negocio, "
        "e todo zero medido seria descartado como falta de lastro")
