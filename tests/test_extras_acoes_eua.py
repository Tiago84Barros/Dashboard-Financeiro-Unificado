"""Ação americana da Nomad é "Ações EUA", não "ETF Internacional".

Com o rótulo de ETF, a Inteligência dos Ativos lia MELI pelo catálogo de ETF
e mostrava fundamentos, valuation e pares vazios.
"""
from types import SimpleNamespace

import pytest

import core.investimentos as investimentos
from core import classe_exterior, stress_tests
from core.global_portfolio import carteira_real
from core.inteligencia_ativos import calculos, fundamentos


def _carteira_vazia():
    return {"posicoes": [], "total_investido": 0.0, "total_mercado": 0.0,
            "num_ativos": 0, "rentabilidade_total_pct": 0.0,
            "por_classe": [], "por_setor": []}


def _linha(ticker, asset_class):
    return SimpleNamespace(
        ticker=ticker, quantity=1.0, average_price=100.0,
        total_invested=100.0, current_price=110.0, usd_brl_rate=6.0,
        asset_name=ticker, asset_class=asset_class, currency="USD",
        sector=None)


def _posicao(ticker, asset_class):
    carteira = _carteira_vazia()
    investimentos._adicionar_extras_ao_snapshot(
        carteira, [_linha(ticker, asset_class)])
    return carteira["posicoes"][0]


def test_acao_americana_vira_acoes_eua_e_e_lida_como_acao():
    pos = _posicao("MELI", "stock")
    assert pos["classe"] == "Ações EUA"
    assert fundamentos.tipo_do_ativo(pos["classe"], pos["moeda"]) \
        == fundamentos.ACAO


@pytest.mark.parametrize("asset_class", ["etf", "etf_intl", None, ""])
def test_etf_e_classe_desconhecida_seguem_etf_internacional(asset_class):
    pos = _posicao("SPY", asset_class)
    assert pos["classe"] == "ETF Internacional"
    assert fundamentos.tipo_do_ativo(pos["classe"], pos["moeda"]) \
        == fundamentos.ETF


def test_acoes_eua_entra_nos_mapas_de_classe():
    pos = _posicao("MELI", "stock")
    assert calculos.CLASSE_POLITICA["Ações EUA"] == "exterior"
    assert stress_tests._CLASSE_TO_SHOCK["Ações EUA"] == "shock_etf_intl"
    assert carteira_real.classe_global(pos) == "us"
    assert "Ações EUA" in investimentos._CLASSES_COM_PROVENTO


# ── Cadastro com classe errada ──────────────────────────────────────────────
# O importador de PDF da Nomad gravava todo ativo como "etf", e o cadastro
# nunca é reescrito: a classe do banco não basta para separar ação de ETF.

def _posicao_nomeada(ticker, asset_class, nome):
    linha = _linha(ticker, asset_class)
    linha.asset_name = nome
    carteira = _carteira_vazia()
    investimentos._adicionar_extras_ao_snapshot(carteira, [linha])
    return carteira


def test_acao_cadastrada_como_etf_vira_acoes_eua_no_grafico_por_classe():
    carteira = _posicao_nomeada("MELI", "etf", "MERCADOLIBRE INC")
    assert carteira["posicoes"][0]["classe"] == "Ações EUA"
    nomes = {c["nome"] for c in carteira["por_classe"]}
    assert nomes == {"Ações EUA"}


@pytest.mark.parametrize("ticker,nome,classe,esperado", [
    ("MELI", "MELI", "etf", "stock_us"),                 # universo da SEC
    ("XYZQ", "Some Company Inc", "etf", "stock_us"),     # sufixo de empresa
    ("XYZQ", "XYZQ", "stock", "stock_us"),               # cadastro de ação
    ("SPY", "SPY", "stock", "etf_intl"),                 # ETF conhecido
    ("XYZQ", "Some Bond ETF", "stock", "etf_intl"),      # nome de fundo
    ("XYZQ", "XYZQ", "etf", "etf_intl"),                 # sem sinal: ETF
    ("XYZQ", None, None, "etf_intl"),
])
def test_classe_ativo_usd(ticker, nome, classe, esperado):
    assert classe_exterior.classe_ativo_usd(
        ticker, nome, classe, universo={"MELI", "AAPL"}) == esperado


def test_universo_publicado_tem_acoes_e_nao_tem_etfs():
    universo = classe_exterior.universo_acoes_eua()
    assert {"MELI", "AAPL"} <= universo
    assert not {"SPY", "IEFA", "SGOV"} & universo


@pytest.mark.parametrize("raw,ticker,nome,esperado", [
    ("stock", "AAPL", "Apple Inc", "stock_us"),
    ("etf", "MELI", "MercadoLibre Inc", "stock_us"),
    ("etf", "SPY", "SPDR S&P 500 ETF Trust", "etf_intl"),
])
def test_snapshot_do_exterior_separa_acao_de_etf(raw, ticker, nome, esperado):
    assert investimentos._class_key_from_snapshot(raw, ticker, "US", nome) \
        == esperado
