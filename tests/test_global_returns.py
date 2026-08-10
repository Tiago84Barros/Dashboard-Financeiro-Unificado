"""Retornos mensais dos ativos da carteira e relatorio de cobertura."""
import numpy as np
import pandas as pd
import pytest

from core.global_portfolio.returns import MIN_OBS, retornos_mensais


def _posicoes():
    return pd.DataFrame([
        {"asset_class": "b3", "symbol": "PETR4", "weight_global": 0.3},
        {"asset_class": "fii", "symbol": "HGLG11", "weight_global": 0.3},
        {"asset_class": "us", "symbol": "AAPL", "weight_global": 0.4},
    ])


def _precos(tickers, meses=36):
    idx = pd.date_range("2023-01-31", periods=meses, freq="ME")
    rng = np.random.default_rng(7)
    return pd.DataFrame(
        {t: 100 * np.cumprod(1 + rng.normal(0.01, 0.05, meses)) for t in tickers},
        index=idx,
    )


def _loader(disponiveis=("PETR4", "HGLG11"), meses=36):
    def carregar(tickers):
        alvo = [t for t in tickers if t in disponiveis]
        return _precos(alvo, meses) if alvo else pd.DataFrame()
    return carregar


def test_us_nunca_entra_na_busca_de_precos():
    pedidos = []

    def espiao(tickers):
        pedidos.append(tuple(sorted(tickers)))
        return _precos([t for t in tickers if t != "AAPL"])

    retornos_mensais(_posicoes(), loader=espiao)
    assert pedidos == [("HGLG11", "PETR4")]


def test_us_aparece_como_sem_serie():
    _, cob = retornos_mensais(_posicoes(), loader=_loader())
    assert cob.simbolos_sem_serie == ("AAPL",)
    assert cob.simbolos_com_serie == ("HGLG11", "PETR4")


def test_peso_coberto_e_a_fracao_com_serie():
    _, cob = retornos_mensais(_posicoes(), loader=_loader())
    assert cob.peso_coberto == pytest.approx(0.6)


def test_retornos_sao_variacao_percentual_mensal():
    ret, _ = retornos_mensais(_posicoes(), loader=_loader(meses=25))
    assert list(ret.columns) == ["HGLG11", "PETR4"]
    assert len(ret) == 24            # 25 precos -> 24 retornos
    assert ret.abs().max().max() < 1.0


def test_simbolo_com_serie_curta_e_descartado():
    ret, cob = retornos_mensais(_posicoes(), loader=_loader(meses=MIN_OBS))
    # MIN_OBS precos -> MIN_OBS-1 retornos, abaixo do piso
    assert ret.empty
    assert cob.simbolos_com_serie == ()
    assert set(cob.simbolos_sem_serie) == {"AAPL", "HGLG11", "PETR4"}


def test_meses_reflete_o_tamanho_do_quadro():
    ret, cob = retornos_mensais(_posicoes(), loader=_loader(meses=30))
    assert cob.meses == len(ret) == 29


def test_quadro_vazio_nao_levanta():
    vazio = pd.DataFrame(columns=["asset_class", "symbol", "weight_global"])
    ret, cob = retornos_mensais(vazio, loader=_loader())
    assert ret.empty
    assert cob.peso_coberto == 0.0
    assert cob.simbolos_com_serie == ()


def test_colunas_em_ordem_deterministica():
    ret, _ = retornos_mensais(_posicoes(), loader=_loader(("HGLG11", "PETR4")))
    assert list(ret.columns) == sorted(ret.columns)
