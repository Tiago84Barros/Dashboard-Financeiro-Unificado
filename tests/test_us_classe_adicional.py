# -*- coding: utf-8 -*-
"""A-151: uma companhia, uma linha no ranking.

54 tickers elegiveis eram classe adicional de empresa ja presente por outro
ticker. Nao chegavam a vitrine, mas por acidente: ninguem tinha ingerido
demonstracao sob eles. Como o EDGAR e por CIK, a proxima ingestao completa
gravaria as demonstracoes da Newtek sob NEWTP e a companhia ocuparia quatro
linhas com fundamento identico e preco de outro papel.
"""
from __future__ import annotations

from core.us_instrumento import (MOTIVO_CLASSE_ADICIONAL,
                                 classe_adicional_da_mesma_companhia,
                                 motivo_exclusao_ativo)

_SOUTHERN = ("SO", "SOJC", "SOJD", "SOJF", "SOMN")
_NEWTEK = ("NEWT", "NEWTH", "NEWTI", "NEWTP")


def _motivo(sym, irmaos, **kw):
    return motivo_exclusao_ativo(sym, "common", "Electric Services",
                                 irmaos, name="ACME Inc", **kw)


def test_baby_bond_sai_porque_a_ordinaria_ja_representa():
    assert classe_adicional_da_mesma_companhia("SOJC", _SOUTHERN) == "SO"
    assert _motivo("SOJC", _SOUTHERN) == MOTIVO_CLASSE_ADICIONAL.format(base="SO")


def test_a_ordinaria_permanece():
    assert classe_adicional_da_mesma_companhia("SO", _SOUTHERN) is None
    assert _motivo("SO", _SOUTHERN) is None


def test_todas_as_notas_da_newtek_saem_e_so_a_ordinaria_fica():
    fora = [s for s in _NEWTEK if classe_adicional_da_mesma_companhia(s, _NEWTEK)]
    assert fora == ["NEWTH", "NEWTI", "NEWTP"]


def test_preferencial_sem_hifen_e_pega_pelo_irmao_e_nao_pelo_sufixo():
    """HOVNP e a preferencial da Hovnanian; o regex de preferencial exige hifen."""
    assert classe_adicional_da_mesma_companhia("HOVNP", ("HOV", "HOVNP")) == "HOV"


def test_classe_ordinaria_legitima_tambem_sai_mas_pelo_motivo_honesto():
    """ZG e acao de verdade. Sai porque Z ja representa a Zillow -- a tela
    ranqueia companhia. Dizer 'nao e acao' seria o defeito do A-147."""
    motivo = _motivo("ZG", ("Z", "ZG"))
    assert motivo == MOTIVO_CLASSE_ADICIONAL.format(base="Z")
    assert "não é ação" not in motivo


def test_empresa_sem_irmao_nao_e_afetada():
    assert classe_adicional_da_mesma_companhia("AAPL", ("AAPL",)) is None
    assert classe_adicional_da_mesma_companhia("MSFT", ()) is None


def test_prefixo_de_outra_companhia_nao_conta():
    """`related_symbols` chega agrupado por company_id: CAT nao mede COST."""
    assert classe_adicional_da_mesma_companhia("COST", ("COST",)) is None


def test_nao_basta_comecar_igual_precisa_ser_mais_curto():
    assert classe_adicional_da_mesma_companhia("ABC", ("ABC", "ABCD")) is None


def test_base_que_e_warrant_nao_serve_de_base():
    """Excluir os dois deixaria a companhia sem nenhuma linha no universo."""
    assert classe_adicional_da_mesma_companhia("ACMEW-WT", ("ACME-WT", "ACMEW-WT")) is None


def test_evidencia_mais_especifica_vence_a_classe_adicional():
    """REIT continua saindo como REIT, nao como classe adicional."""
    motivo = motivo_exclusao_ativo("SOJC", "common", "Real Estate Investment Trust",
                                   _SOUTHERN, name="ACME Inc")
    assert "REIT" in motivo
