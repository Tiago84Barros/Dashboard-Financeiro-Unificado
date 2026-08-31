"""SPAC que declara o SIC do alvo não é companhia daquele setor (A-157).

`_SETORES_NAO_OPERACIONAIS` pega quem se declara "Blank Checks" — 336 dos 346
ativos com nome de aquisição, medido em 31/08/2026. Os outros 10 declaram o SIC
da INDÚSTRIA QUE PRETENDEM COMPRAR e chegavam à tela como companhia dela:
Eureka Acquisition Corp em "Water Transportation", Digital Asset Acquisition
Corp em "State Commercial Banks". Casca sem operação disputando ranking com
transportadora e com banco, todas pontuando ~50 — a mediana do mercado.

A identificação é uma CONJUNÇÃO. Nome sozinho excluiria operadora com
"Acquisition" na razão social; ausência de receita sozinha excluiria as 160
elegíveis pré-receita (biotecnologia, mineração júnior).
"""
from __future__ import annotations

import pandas as pd

from core.us_instrumento import (
    MOTIVO_SPAC_SEM_OPERACAO,
    e_spac_sem_operacao,
    motivo_exclusao_ativo,
)
from core.us_read import _receita_apurada


def test_casca_com_sic_do_alvo_sai_do_universo():
    """O caso real: SPAC classificado como transporte marítimo."""
    motivo = motivo_exclusao_ativo("EURK", "common", "Water Transportation",
                                   name="Eureka Acquisition Corp",
                                   tem_receita=False)
    assert motivo == MOTIVO_SPAC_SEM_OPERACAO


def test_operadora_com_receita_fica_mesmo_com_o_nome():
    assert motivo_exclusao_ativo("IPCX", "common", "Services-Prepackaged Software",
                                 name="Inflection Point Acquisition Corp. III",
                                 tem_receita=True) is None


def test_pre_receita_sem_nome_de_veiculo_fica():
    """Biotecnologia sem receita é empresa; o nome é o que separa."""
    assert motivo_exclusao_ativo("XBIO", "common", "Pharmaceutical Preparations",
                                 name="Xenetic Biosciences, Inc.",
                                 tem_receita=False) is None


def test_duvida_nao_exclui():
    """Sem apuração de receita não há evidência de ausência."""
    assert motivo_exclusao_ativo("FVN", "common", "Services-Computer Integrated",
                                 name="Future Vision II Acquisition Corp.",
                                 tem_receita=None) is None
    assert e_spac_sem_operacao(name="Future Vision II Acquisition Corp.") is False


def test_cheque_em_branco_declarado_continua_saindo_pelo_setor():
    assert motivo_exclusao_ativo("ABCD", "common", "Blank Checks",
                                 name="Qualquer Corp") is not None


def _linha(**kw):
    return pd.Series(kw)


def test_receita_apurada_distingue_nao_apurado_de_sem_receita():
    assert _receita_apurada(_linha(_receita_json=None)) is None
    assert _receita_apurada(_linha(_receita_json="")) is False
    assert _receita_apurada(_linha(_receita_json="0")) is False
    assert _receita_apurada(_linha(_receita_json="416161000000.0")) is True


def test_sem_a_coluna_auxiliar_cai_no_bloco_de_metricas():
    assert _receita_apurada(_linha(metrics={"_revenue": 10.0})) is True
    assert _receita_apurada(_linha(metrics={"_revenue": None})) is False
    assert _receita_apurada(_linha(metrics=None)) is None
    assert _receita_apurada(_linha(symbol="X")) is None
