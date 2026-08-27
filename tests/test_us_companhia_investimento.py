# -*- coding: utf-8 -*-
"""A-147: BDC e fundo fechado disputavam ranking com companhia operacional.

A regra de universo ja sabia recusar "closed-end management investment
offices" -- so que esse texto vem do SIC, e a SEC devolve `sic` VAZIO para
toda companhia de investimento registrada. A regra existia e era cega: 40
fundos de credito (FS KKR, Hercules, Goldman Sachs BDC, Oaktree, Sixth
Street...) estavam na vitrine, sem receita e com "lucro" recorrente que na
verdade e investment income distribuido por obrigacao legal de RIC.

O sinal que a SEC de fato fornece e o FORMULARIO. Medido em 15 suspeitos
contra 10 controles operacionais: 14x0 de separacao, e o unico suspeito sem
marca era o Central Bancompany -- banco de verdade, que o sinal deixou passar.
"""
from __future__ import annotations

from core.us_instrumento import (MOTIVO_COMPANHIA_INVESTIMENTO,
                                 motivo_exclusao_ativo)
from data_pipeline.us.edgar import _e_companhia_de_investimento
from data_pipeline.us.normalize import map_profile


def _sub(*forms):
    return {"filings": {"recent": {"form": list(forms)}}}


# ── o sinal ──────────────────────────────────────────────────────────────────
def test_n54a_identifica_bdc():
    """N-54A e a eleicao formal de virar BDC sob o Investment Company Act."""
    assert _e_companhia_de_investimento(_sub("8-K", "N-54A", "10-K")) is True


def test_n2_identifica_fundo_fechado():
    assert _e_companhia_de_investimento(_sub("N-2", "N-2/A")) is True
    assert _e_companhia_de_investimento(_sub("N-2ASR")) is True


def test_relatorio_periodico_de_companhia_de_investimento_conta():
    assert _e_companhia_de_investimento(_sub("N-CSR")) is True
    assert _e_companhia_de_investimento(_sub("NPORT-P")) is True


def test_companhia_operacional_nao_arquiva_nenhum_deles():
    """Exxon, Morgan Stanley, HCA e Unisys: zero marcas nos 10 controles."""
    assert _e_companhia_de_investimento(
        _sub("10-K", "10-Q", "8-K", "DEF 14A", "S-3", "4")) is False


def test_banco_operacional_sem_marca_continua_dentro():
    """Central Bancompany caiu no mesmo balde por `sector` nulo; e banco."""
    assert _e_companhia_de_investimento(_sub("10-K", "S-1", "8-K")) is False


def test_submissions_vazio_ou_malformado_nao_quebra():
    for entrada in ({}, None, {"filings": {}}, {"filings": {"recent": {}}},
                    {"filings": {"recent": {"form": [None, ""]}}}):
        assert _e_companhia_de_investimento(entrada) is False


def test_nao_confunde_formulario_de_nome_parecido():
    """`4` e `S-1` comecam com letra; a checagem e por prefixo de formulario."""
    assert _e_companhia_de_investimento(_sub("N-CEN")) is False


# ── a travessia ate a regra ──────────────────────────────────────────────────
def test_map_profile_carrega_a_marca():
    perfil = map_profile({"symbol": "FSK", "companyName": "FS KKR Capital Corp",
                          "_investment_company": True})
    assert perfil["is_investment_company"] is True


def test_map_profile_sem_marca_e_falso_e_nao_none():
    perfil = map_profile({"symbol": "XOM", "companyName": "EXXON MOBIL CORP"})
    assert perfil["is_investment_company"] is False


def test_a_regra_de_universo_recusa_a_companhia_de_investimento():
    motivo = motivo_exclusao_ativo(
        "FSK", "common", None, (), name="FS KKR Capital Corp",
        is_investment_company=True)
    assert motivo == MOTIVO_COMPANHIA_INVESTIMENTO


def test_sem_a_marca_o_mesmo_ativo_passava():
    """Mede exatamente o buraco: `sector` nulo, nome de corporacao comum."""
    assert motivo_exclusao_ativo(
        "FSK", "common", None, (), name="FS KKR Capital Corp") is None


def test_companhia_operacional_nao_e_afetada():
    assert motivo_exclusao_ativo(
        "XOM", "common", "Petroleum Refining", (), name="EXXON MOBIL CORP",
        is_investment_company=False) is None


def test_gestora_de_recursos_continua_dentro():
    """Blackstone administra o veiculo, nao e o veiculo (regra do A-144)."""
    assert motivo_exclusao_ativo(
        "BX", "common", "Investment Advice", (), name="Blackstone Inc.",
        is_investment_company=False) is None
