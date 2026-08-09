"""Concentracao por ativo, setor, pais, moeda e classe."""
import pandas as pd
import pytest

from core.global_portfolio.concentration import (
    DIMENSOES,
    gini,
    hhi,
    numero_efetivo,
    por_dimensao,
    resumo,
    top_n,
)


def _df():
    return pd.DataFrame([
        {"asset_class": "b3", "symbol": "PETR4", "sector": "energy",
         "country": "BR", "currency": "BRL", "weight_global": 0.4},
        {"asset_class": "b3", "symbol": "ITUB4", "sector": "financials",
         "country": "BR", "currency": "BRL", "weight_global": 0.3},
        {"asset_class": "us", "symbol": "AAPL", "sector": "technology",
         "country": "US", "currency": "USD", "weight_global": 0.2},
        {"asset_class": "fii", "symbol": "HGLG11", "sector": "real_estate",
         "country": "BR", "currency": "BRL", "weight_global": 0.1},
    ])


def test_hhi_de_carteira_igualmente_dividida():
    # 4 posicoes de 25% -> HHI = 4 * 0.0625 = 0.25
    assert hhi(pd.Series([0.25] * 4)) == pytest.approx(0.25)


def test_hhi_de_posicao_unica_e_um():
    assert hhi(pd.Series([1.0])) == pytest.approx(1.0)


def test_numero_efetivo_e_o_inverso_do_hhi():
    # 4 posicoes iguais -> numero efetivo 4
    assert numero_efetivo(pd.Series([0.25] * 4)) == pytest.approx(4.0)


def test_numero_efetivo_sem_peso_e_zero():
    assert numero_efetivo(pd.Series([0.0, 0.0])) == 0.0


def test_hhi_conhecido_da_carteira_do_teste():
    # 0.4^2 + 0.3^2 + 0.2^2 + 0.1^2 = 0.16+0.09+0.04+0.01 = 0.30
    assert hhi(_df()["weight_global"]) == pytest.approx(0.30)


def test_top_n_soma_as_maiores():
    assert top_n(_df(), 2) == pytest.approx(0.7)
    assert top_n(_df(), 10) == pytest.approx(1.0)


def test_gini_de_carteira_igual_e_zero():
    assert gini(pd.Series([0.25] * 4)) == pytest.approx(0.0, abs=1e-9)


def test_gini_cresce_com_a_desigualdade():
    assert gini(pd.Series([0.97, 0.01, 0.01, 0.01])) > gini(pd.Series([0.4, 0.3, 0.2, 0.1]))


def test_por_dimensao_agrupa_e_conta():
    saida = por_dimensao(_df(), "country")
    br = saida[saida["country"] == "BR"].iloc[0]
    assert br["peso"] == pytest.approx(0.8)
    assert br["n_ativos"] == 3


def test_por_dimensao_ordena_por_peso_decrescente():
    saida = por_dimensao(_df(), "sector")
    assert saida["peso"].tolist() == sorted(saida["peso"].tolist(), reverse=True)


def test_resumo_cobre_todas_as_dimensoes():
    saida = resumo(_df())
    assert set(saida) == set(DIMENSOES)


def test_resumo_aponta_o_maior_de_cada_dimensao():
    saida = resumo(_df())
    assert saida["country"]["maior_nome"] == "BR"
    assert saida["country"]["maior_peso"] == pytest.approx(0.8)
    assert saida["symbol"]["maior_nome"] == "PETR4"


def test_dataframe_vazio_nao_quebra():
    vazio = pd.DataFrame(columns=["asset_class", "symbol", "sector",
                                  "country", "currency", "weight_global"])
    saida = resumo(vazio)
    assert saida["symbol"]["numero_efetivo"] == 0.0
    assert saida["symbol"]["maior_nome"] is None
