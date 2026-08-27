# -*- coding: utf-8 -*-
"""A-139: a metodologia por setor lia GICS onde o dado guarda SIC.

`SECTOR_TRACK_OVERRIDES` e `_sector_confidence_penalty` chaveiam em
"Financial Services" e "Real Estate". A vitrine guarda a descricao SIC do
formulario da SEC -- "State Commercial Banks", "Real Estate Investment Trusts".
Medido no armazem: "Financial Services" batia em zero das 2.831 linhas. Os
pesos economicamente justificados existiam, estavam documentados, e nunca
haviam executado.
"""
from __future__ import annotations

import pytest

from core.us_score import (
    DEFAULT_TRACK_WEIGHTS,
    _sector_confidence_penalty,
    _weights_for,
    sector_group,
)

FINANCEIRAS = [
    "State Commercial Banks",
    "National Commercial Banks",
    "Finance Services",
    "Fire, Marine & Casualty Insurance",
    "Commodity Contracts Brokers & Dealers",
    "Savings Institution, Federally Chartered",
    "Savings Institutions, Not Federally Chartered",
    "Investment Advice",
    "Security Brokers, Dealers & Flotation Companies",
    "Security & Commodity Brokers, Dealers, Exchanges & Services",
    "Insurance Agents, Brokers & Service",
    "Life Insurance",
    "Personal Credit Institutions",
    "Mortgage Bankers & Loan Correspondents",
    "Loan Brokers",
    "Functions Related To Depository Banking, NEC",
    "Services-Consumer Credit Reporting, Collection Agencies",
    "Investors, NEC",
    "Federal & Federally-Sponsored Credit Agencies",
    "Financial Services",  # o rotulo GICS, quando a fonte ja o entrega
]

# Falsos positivos reais: casaram com um regex ingenuo de "dealer"/"estate".
NAO_FINANCEIRAS = [
    "Retail-Auto Dealers & Gasoline Stations",
    "Retail-Lumber & Other Building Materials Dealers",
    "Services-Prepackaged Software",
    "Pharmaceutical Preparations",
    "Real Estate Agents & Managers (For Others)",
    "Real Estate Operators (No Developers) & Lessors",
]


@pytest.mark.parametrize("sic", FINANCEIRAS)
def test_descricao_sic_financeira_vira_grupo_financeiro(sic):
    assert sector_group(sic) == "Financial Services"


@pytest.mark.parametrize("sic", NAO_FINANCEIRAS)
def test_setor_nao_financeiro_preserva_o_proprio_rotulo(sic):
    assert sector_group(sic) == sic


@pytest.mark.parametrize("sic", ["Real Estate Investment Trusts", "Real Estate"])
def test_reit_nao_ganha_grupo_proprio_no_score(sic):
    """A-140: REIT sai do universo ANTES do score; nao existe peso de REIT.

    O tratamento por reponderacao existiu e foi removido -- o modulo americano
    analisa acoes, e quem decide isso e core/us_instrumento.py.
    """
    assert sector_group(sic) != "Financial Services"
    assert _weights_for(sic) == _weights_for("Services-Prepackaged Software")


def test_corretora_imobiliaria_e_acao_operacional_comum():
    """Corretora imobiliaria (JLL, RE/MAX) tem lucro e EBIT legiveis."""
    assert _weights_for("Real Estate Agents & Managers (For Others)") ==         _weights_for("Services-Prepackaged Software")


def test_pesos_do_banco_deixam_de_ser_os_padrao():
    padrao = _weights_for("Services-Prepackaged Software")
    banco = _weights_for("State Commercial Banks")
    assert banco != padrao
    assert banco == _weights_for("Financial Services")
    assert banco["solidity"] < padrao["solidity"], (
        "alavancagem contabil de banco nao se compara a de industria"
    )
    assert pytest.approx(sum(banco.values()), abs=1e-9) == 1.0


def test_penalidade_de_confianca_alcanca_o_sic():
    assert _sector_confidence_penalty("State Commercial Banks") == 0.85
    assert _sector_confidence_penalty("Services-Prepackaged Software") == 1.0
    assert _sector_confidence_penalty("Retail-Auto Dealers & Gasoline Stations") == 1.0


def test_setor_ausente_nao_explode_e_cai_no_padrao():
    for vazio in (None, "", "   ", float("nan")):
        assert _weights_for(vazio) == {
            k: v / sum(DEFAULT_TRACK_WEIGHTS.values())
            for k, v in DEFAULT_TRACK_WEIGHTS.items()
        }
        assert _sector_confidence_penalty(vazio) == 1.0
