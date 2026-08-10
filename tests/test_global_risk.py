"""Volatilidade, VaR, CVaR e drawdown do patrimonio."""
import numpy as np
import pandas as pd
import pytest

from core.global_portfolio.risk import metricas_de_risco, retorno_do_portfolio


def _retornos(n=60, vol=0.05, seed=13):
    idx = pd.date_range("2021-01-31", periods=n, freq="ME")
    rng = np.random.default_rng(seed)
    return pd.DataFrame(
        {"A": rng.normal(0.01, vol, n), "B": rng.normal(0.01, vol, n)}, index=idx)


def test_retorno_do_portfolio_e_media_ponderada_linha_a_linha():
    ret = _retornos()
    serie = retorno_do_portfolio(ret, {"A": 0.5, "B": 0.5})
    esperado = ret.mean(axis=1)
    assert np.allclose(serie.to_numpy(), esperado.to_numpy())


def test_volatilidade_anual_e_mensal_vezes_raiz_de_doze():
    r = metricas_de_risco(_retornos(), {"A": 0.5, "B": 0.5})
    assert r.vol_anual == pytest.approx(r.vol_mensal * np.sqrt(12))


def test_volatilidade_cresce_com_a_dispersao():
    calmo = metricas_de_risco(_retornos(vol=0.02), {"A": .5, "B": .5})
    agitado = metricas_de_risco(_retornos(vol=0.10), {"A": .5, "B": .5})
    assert agitado.vol_mensal > calmo.vol_mensal


def test_var_e_cvar_sao_perdas_positivas_e_cvar_e_pior():
    r = metricas_de_risco(_retornos(), {"A": 0.5, "B": 0.5})
    assert r.var_95 > 0
    assert r.cvar_95 >= r.var_95


def test_drawdown_de_serie_sempre_positiva_e_zero():
    idx = pd.date_range("2021-01-31", periods=30, freq="ME")
    ret = pd.DataFrame({"A": [0.01] * 30}, index=idx)
    r = metricas_de_risco(ret, {"A": 1.0})
    assert r.drawdown_max == pytest.approx(0.0, abs=1e-9)


def test_drawdown_captura_a_queda_conhecida():
    idx = pd.date_range("2021-01-31", periods=4, freq="ME")
    # +0%, -20%, +0%, +0%  -> drawdown maximo 20%
    ret = pd.DataFrame({"A": [0.0, -0.20, 0.0, 0.0]}, index=idx)
    from core.global_portfolio.risk import _drawdown_maximo
    assert _drawdown_maximo(ret["A"]) == pytest.approx(0.20)


def test_serie_curta_devolve_none():
    assert metricas_de_risco(_retornos(n=10), {"A": .5, "B": .5}) is None


def test_sem_peso_devolve_none():
    assert metricas_de_risco(_retornos(), {}) is None


def test_quadro_vazio_devolve_none():
    assert metricas_de_risco(pd.DataFrame(), {}) is None
    assert retorno_do_portfolio(pd.DataFrame(), {}) is None


def test_mes_com_ativo_ausente_renormaliza_em_vez_de_puxar_a_zero():
    # B falta em um mes: o retorno do portfolio nesse mes deve vir so de A
    # (renormalizado), nao ser arrastado para perto de zero como faria um
    # fillna(0.0) antes da media ponderada.
    idx = pd.date_range("2021-01-31", periods=3, freq="ME")
    ret = pd.DataFrame(
        {"A": [0.10, 0.10, 0.10], "B": [0.10, np.nan, 0.10]}, index=idx)
    serie = retorno_do_portfolio(ret, {"A": 0.5, "B": 0.5})
    # mes 0 e 2: media de A e B = 0.10; mes 1: so A conta -> 0.10 (nao 0.05)
    assert serie.to_numpy() == pytest.approx([0.10, 0.10, 0.10])


def test_mes_com_todos_os_ativos_ausentes_e_descartado():
    idx = pd.date_range("2021-01-31", periods=3, freq="ME")
    ret = pd.DataFrame(
        {"A": [0.10, np.nan, 0.10], "B": [0.10, np.nan, 0.10]}, index=idx)
    serie = retorno_do_portfolio(ret, {"A": 0.5, "B": 0.5})
    assert len(serie) == 2
    assert idx[1] not in serie.index
