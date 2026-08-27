# -*- coding: utf-8 -*-
"""A-140: o modulo americano analisa ACOES.

Regra do usuario. REIT, fundo, SPAC, preferencial, warrant e unit nao disputam
ranking com companhia operacional: a comparacao relativa por industria pressupoe
estrutura economica comparavel, e o REIT distribui por obrigacao legal, deprecia
imovel contra o lucro e alavanca por desenho -- fica barato em P/L e caro em
divida/EBITDA pelos dois motivos errados.
"""
from __future__ import annotations

import pandas as pd
import pytest

from core.us_instrumento import (
    MOTIVO_REIT,
    MOTIVO_TIPO_NAO_CONFIRMADO,
    e_reit,
    motivo_exclusao_ativo,
)


def test_flag_do_cadastro_identifica_reit():
    assert e_reit(is_reit=True)
    assert e_reit(security_type="reit")


def test_descricao_sic_identifica_reit():
    assert e_reit(sector="Real Estate Investment Trusts")
    assert e_reit(industry="Real Estate Investment Trusts")


def test_nome_resgata_o_reit_que_a_flag_perde():
    """Medido: `is_reit` cobre 128 de ~148; AOMD escapa das duas outras fontes."""
    assert e_reit(name="Angel Oak Mortgage REIT, Inc.", sector="Real Estate")
    assert e_reit(name="Whitestone Realty Trust")


def test_operadora_imobiliaria_nao_e_reit():
    """JLL, RE/MAX e Compass sao prestadoras de servico -- acoes comuns."""
    for nome, setor in [
        ("JONES LANG LASALLE INC", "Real Estate Agents & Managers (For Others)"),
        ("RE/MAX Holdings, Inc.", "Real Estate Agents & Managers (For Others)"),
        ("Forestar Group Inc.", "Land Subdividers & Developers"),
    ]:
        assert not e_reit(name=nome, sector=setor), nome
        assert motivo_exclusao_ativo("XXXX", "common", setor, name=nome) is None


def test_reit_e_excluido_com_motivo_auditavel():
    assert motivo_exclusao_ativo("O", "reit", "Real Estate Investment Trusts",
                                 name="REALTY INCOME CORP") == MOTIVO_REIT


def test_setor_generico_sem_sic_fica_como_nao_confirmado():
    """20 linhas rotuladas so "Real Estate": ~13 sao REIT, o resto operadora.

    O cadastro nao distingue as duas, entao chamar de REIT seria inventar
    identidade e deixar passar contaminaria o ranking com metade REIT. Sai do
    universo declarando exatamente isso.
    """
    motivo = motivo_exclusao_ativo("BEEP", "common", "Real Estate",
                                   name="Mobile Infrastructure Corp")
    assert motivo == MOTIVO_TIPO_NAO_CONFIRMADO
    assert motivo != MOTIVO_REIT, "nao afirmar identidade que o dado nao sustenta"


def test_acao_comum_continua_passando():
    for sym, setor, nome in [
        ("AAPL", "Electronic Computers", "Apple Inc."),
        ("MTB", "State Commercial Banks", "M&T BANK CORP"),
        ("RGLD", "Mineral Royalty Traders", "ROYAL GOLD INC"),
        ("LAW", "Services-Prepackaged Software", "CS Disco Inc"),
    ]:
        assert motivo_exclusao_ativo(sym, "common", setor, name=nome) is None, sym


def test_regras_antigas_de_instrumento_preservadas():
    """A-140 acrescentou o REIT; nao pode ter apagado o que ja filtrava."""
    assert motivo_exclusao_ativo("ABCD", "common", "Blank Checks")
    assert motivo_exclusao_ativo("F-PB", "common", "Automobiles")
    assert motivo_exclusao_ativo("GRAF-WT", "common", "Aircraft")
    assert motivo_exclusao_ativo("KDKRW", "common", "Software")
    assert motivo_exclusao_ativo("SPAQ", "spac", "Software")


def test_leitura_da_vitrine_remove_o_que_nao_e_acao():
    """O filtro roda na leitura porque a vitrine publicada e anterior a regra."""
    from core.us_read import _apenas_acoes

    df = pd.DataFrame([
        {"symbol": "AAPL", "name": "Apple Inc.", "sector": "Electronic Computers",
         "industry": "Electronic Computers", "security_type": "common", "is_reit": False},
        {"symbol": "O", "name": "REALTY INCOME CORP",
         "sector": "Real Estate Investment Trusts",
         "industry": "REIT", "security_type": "reit", "is_reit": True},
        {"symbol": "BEEP", "name": "Mobile Infrastructure Corp",
         "sector": "Real Estate", "industry": "Real Estate",
         "security_type": "common", "is_reit": False},
    ])
    assert list(_apenas_acoes(df)["symbol"]) == ["AAPL"]


def test_filtro_de_leitura_e_inofensivo_em_frame_vazio():
    from core.us_read import _apenas_acoes

    assert _apenas_acoes(pd.DataFrame()).empty


@pytest.mark.parametrize("vazio", [None, "", "   "])
def test_campos_ausentes_nao_excluem_por_acidente(vazio):
    assert motivo_exclusao_ativo("AAPL", vazio, vazio, name=vazio) is None
