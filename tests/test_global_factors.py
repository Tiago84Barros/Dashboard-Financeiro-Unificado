"""Exposicao a fatores de risco por regressao."""
import numpy as np
import pandas as pd
import pytest

from core.global_portfolio.factors import (
    MIN_OBS_REGRESSAO,
    PROXIES,
    ROTULOS_FATOR,
    betas_do_ativo,
    exposicao_do_portfolio,
    series_de_fatores,
)


def _fatores(n=60):
    idx = pd.date_range("2021-01-31", periods=n, freq="ME")
    rng = np.random.default_rng(3)
    return pd.DataFrame(
        {f: rng.normal(0.008, 0.04, n) for f in ("mercado_br", "juros_nominais")},
        index=idx,
    )


def test_todo_fator_tem_rotulo():
    assert set(ROTULOS_FATOR) == set(PROXIES)


def test_proxies_sao_deterministicos():
    assert list(PROXIES) == sorted(PROXIES)


def test_beta_plantado_e_recuperado():
    f = _fatores()
    rng = np.random.default_rng(5)
    ativo = 1.5 * f["mercado_br"] + 0.3 * f["juros_nominais"] + rng.normal(0, 0.005, len(f))
    exp = {e.fator: e for e in betas_do_ativo(ativo, f)}
    assert exp["mercado_br"].beta == pytest.approx(1.5, abs=0.12)
    assert exp["juros_nominais"].beta == pytest.approx(0.3, abs=0.12)


def test_r2_alto_quando_os_fatores_explicam():
    f = _fatores()
    ativo = 1.2 * f["mercado_br"] + np.random.default_rng(1).normal(0, 0.002, len(f))
    assert betas_do_ativo(ativo, f)[0].r2 > 0.9


def test_beta_forte_e_marcado_significativo():
    f = _fatores()
    ativo = 1.5 * f["mercado_br"] + np.random.default_rng(2).normal(0, 0.005, len(f))
    exp = {e.fator: e for e in betas_do_ativo(ativo, f)}
    assert exp["mercado_br"].significativo is True


def test_ruido_puro_nao_e_significativo():
    f = _fatores()
    ativo = pd.Series(np.random.default_rng(9).normal(0, 0.05, len(f)), index=f.index)
    assert all(not e.significativo for e in betas_do_ativo(ativo, f))


def test_serie_curta_nao_estima_beta():
    f = _fatores(n=MIN_OBS_REGRESSAO - 1)
    ativo = 1.0 * f["mercado_br"]
    assert betas_do_ativo(ativo, f) == []


def test_exposicoes_ordenadas_por_beta_absoluto():
    f = _fatores()
    ativo = 0.2 * f["mercado_br"] + 1.4 * f["juros_nominais"]
    assert [e.fator for e in betas_do_ativo(ativo, f)][0] == "juros_nominais"


def test_exposicao_do_portfolio_pondera_os_ativos():
    f = _fatores()
    rng = np.random.default_rng(4)
    a = 2.0 * f["mercado_br"] + rng.normal(0, 0.004, len(f))
    b = 0.0 * f["mercado_br"] + rng.normal(0, 0.004, len(f))
    ret = pd.DataFrame({"A": a, "B": b}, index=f.index)
    exp = {e.fator: e for e in exposicao_do_portfolio(ret, {"A": 0.5, "B": 0.5}, f)}
    assert exp["mercado_br"].beta == pytest.approx(1.0, abs=0.15)


def test_series_de_fatores_monta_small_cap_como_spread():
    idx = pd.date_range("2021-01-31", periods=30, freq="ME")
    precos = pd.DataFrame(
        {t: np.linspace(100, 200, 30) for t in PROXIES.values()}, index=idx)
    precos["SMAL11"] = np.linspace(100, 300, 30)     # sobe mais que BOVA11
    f = series_de_fatores(loader=lambda tks: precos)
    assert "small_cap" in f.columns
    assert f["small_cap"].mean() > 0                  # spread positivo
    assert "mercado_br" in f.columns


def test_series_de_fatores_sem_dados_devolve_vazio():
    assert series_de_fatores(loader=lambda tks: pd.DataFrame()).empty
