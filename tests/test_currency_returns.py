from types import SimpleNamespace

import pytest

import core.investimentos as investimentos
from core.currency_returns import decompor_retorno_cambial, retorno_em_brl


def test_retorno_brl_inclui_ativo_cambio_e_interacao():
    retorno = retorno_em_brl(110.0, 100.0, 6.0, 5.0)
    partes = decompor_retorno_cambial(0.10, 0.20)
    assert retorno == pytest.approx(0.32)
    assert partes == pytest.approx(
        {"ativo": 0.10, "cambio": 0.20, "interacao": 0.02, "total_brl": 0.32}
    )


def test_retorno_brl_indisponivel_sem_cambio_da_compra():
    assert retorno_em_brl(110.0, 100.0, 6.0, None) is None


def _carteira_vazia():
    return {
        "posicoes": [],
        "total_investido": 0.0,
        "total_mercado": 0.0,
        "num_ativos": 0,
        "rentabilidade_total_pct": 0.0,
        "por_classe": [],
        "por_setor": [],
    }


def test_extra_usd_expoe_retorno_local_sem_inventar_retorno_brl(monkeypatch):
    carteira = _carteira_vazia()
    row = SimpleNamespace(
        ticker="SPY",
        quantity=1.0,
        average_price=100.0,
        total_invested=100.0,
        current_price=110.0,
        usd_brl_rate=6.0,
        asset_name="SPY",
        currency="USD",
        sector=None,
    )
    investimentos._adicionar_extras_ao_snapshot(carteira, [row])
    pos = carteira["posicoes"][0]

    assert pos["valor_mercado"] == pytest.approx(660.0)
    assert pos["rentab_pct"] == pytest.approx(10.0)
    assert pos["rentab_moeda"] == "USD"
    assert pos["rentab_brl_pct"] is None
    assert pos["retorno_brl_disponivel"] is False
    assert carteira["rentabilidade_total_disponivel"] is False


def test_extra_usd_sem_cambio_atual_nao_recebe_valor_fabricado(monkeypatch):
    carteira = _carteira_vazia()
    row = SimpleNamespace(
        ticker="SPY",
        quantity=1.0,
        average_price=100.0,
        total_invested=100.0,
        current_price=110.0,
        usd_brl_rate=None,
        asset_name="SPY",
        currency="USD",
        sector=None,
    )
    monkeypatch.setattr(investimentos, "_get_usd_brl_live", lambda: None)
    investimentos._adicionar_extras_ao_snapshot(carteira, [row])

    assert carteira["posicoes"] == []
    assert any("USD/BRL" in aviso for aviso in carteira["avisos_dados"])


def test_extra_usd_com_cambio_da_compra_publica_retorno_brl():
    """Com o câmbio da aquisição em mãos, o custo deixa de ser convertido pela
    taxa de hoje — e o retorno em BRL passa a existir e a diferir do retorno
    em USD. É o caso que o card "Retorno Mercado/Custo" espera para acender.
    """
    carteira = _carteira_vazia()
    row = SimpleNamespace(
        ticker="SPY",
        quantity=1.0,
        average_price=100.0,
        total_invested=100.0,
        current_price=110.0,
        usd_brl_rate=6.0,
        asset_name="SPY",
        currency="USD",
        sector=None,
    )
    fx_compra = {"SPY": {"taxa_media": 5.0, "cobertura": 1.0}}
    investimentos._adicionar_extras_ao_snapshot(carteira, [row], fx_compra)
    pos = carteira["posicoes"][0]

    assert pos["fx_rate_compra"] == pytest.approx(5.0)
    assert pos["custo_fonte"] == "cambio_historico_compras"
    assert pos["custo_estimado"] is False
    # Custo a 5,00 (compra) e mercado a 6,00 (hoje): 500 de custo, 660 de mercado.
    assert pos["total_investido"] == pytest.approx(500.0)
    assert pos["valor_mercado"] == pytest.approx(660.0)
    assert pos["rentab_pct"] == pytest.approx(10.0)          # em USD
    assert pos["rentab_brl_pct"] == pytest.approx(32.0)      # em BRL
    assert pos["retorno_brl_disponivel"] is True
    assert carteira["rentabilidade_total_disponivel"] is True
    assert carteira["posicoes_sem_cambio_historico"] == []


def test_cobertura_parcial_mantem_o_card_apagado_e_nomeia_o_ticker():
    """Metade das compras sem câmbio não vira um custo meio histórico: cai no
    caminho estimado, e a posição aparece nomeada para o aviso da tela."""
    carteira = _carteira_vazia()
    row = SimpleNamespace(
        ticker="IEMG",
        quantity=1.0,
        average_price=100.0,
        total_invested=100.0,
        current_price=110.0,
        usd_brl_rate=6.0,
        asset_name="IEMG",
        currency="USD",
        sector=None,
    )
    fx_compra = {"IEMG": {"taxa_media": 5.0, "cobertura": 0.5}}
    investimentos._adicionar_extras_ao_snapshot(carteira, [row], fx_compra)
    pos = carteira["posicoes"][0]

    assert pos["fx_rate_compra"] is None
    assert pos["custo_fonte"] == "cambio_atual_estimado"
    assert pos["retorno_brl_disponivel"] is False
    assert carteira["rentabilidade_total_disponivel"] is False
    assert carteira["posicoes_sem_cambio_historico"] == ["IEMG"]
