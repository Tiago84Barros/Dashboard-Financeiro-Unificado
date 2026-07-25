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
