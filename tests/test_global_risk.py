"""Volatilidade, VaR, CVaR e drawdown do patrimonio."""
import numpy as np
import pandas as pd
import pytest

from core.global_portfolio.risk import (
    contribuicao_marginal,
    metricas_de_risco,
    retorno_do_portfolio,
)


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


# ---------------------------------------------------------------------------
# contribuicao_marginal
# ---------------------------------------------------------------------------

def _retornos_multifator(n=90, seed=21):
    """Quatro ativos com um fator comum e cargas diferentes — nao e o
    toy de dois ativos: as correlacoes cruzadas nao sao todas iguais."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2018-01-31", periods=n, freq="ME")
    fator = rng.normal(0, 0.04, n)
    a = 0.8 * fator + rng.normal(0, 0.02, n)
    b = 0.5 * fator + rng.normal(0, 0.03, n)
    c = 0.2 * fator + rng.normal(0, 0.05, n)
    d = -0.3 * fator + rng.normal(0, 0.025, n)
    return pd.DataFrame({"A": a, "B": b, "C": c, "D": d}, index=idx)


def _sigma_p_esperado(ret: pd.DataFrame, pesos: dict) -> float:
    """Sigma_p calculado de forma independente da implementacao, para o
    teste nao virar tautologia: cov bruta (sem passar por _covariancia_confiavel)
    e pesos normalizados manualmente."""
    cov = ret.cov(ddof=1)
    w = np.array([pesos[c] for c in cov.columns], dtype=float)
    w = w / w.sum()
    return float(np.sqrt(w @ cov.to_numpy() @ w))


def test_contribuicao_marginal_soma_fecha_em_sigma_p_com_ativos_correlacionados():
    ret = _retornos_multifator()
    pesos = {"A": 0.4, "B": 0.3, "C": 0.2, "D": 0.1}
    resultado = contribuicao_marginal(ret, pesos)

    assert set(resultado) == {"A", "B", "C", "D"}
    soma = sum(r.contribuicao for r in resultado.values())
    assert soma == pytest.approx(_sigma_p_esperado(ret, pesos), abs=1e-9)


def test_contribuicao_marginal_razao_distingue_ativo_mais_arriscado_que_o_peso():
    # Com pesos iguais, a razao contribuicao/peso de A menos a de B e exatamente
    # 0.5*(var_A - var_B)/sigma_p — identidade algebrica da formula de MCR para
    # dois ativos, valida para a covariancia AMOSTRAL usada, nao uma esperanca
    # estatistica. O ativo de maior variancia amostral tem razao maior, sem
    # depender de sorte na semente (a asserção sobre a variancia confirma a
    # premissa antes de checar a consequencia).
    rng = np.random.default_rng(4)
    n = 90
    idx = pd.date_range("2018-01-31", periods=n, freq="ME")
    fator = rng.normal(0, 0.03, n)
    a = fator + rng.normal(0, 0.06, n)   # vol idiossincratica alta
    b = fator + rng.normal(0, 0.01, n)   # vol idiossincratica baixa
    ret = pd.DataFrame({"A": a, "B": b}, index=idx)
    assert ret["A"].var(ddof=1) > ret["B"].var(ddof=1)

    resultado = contribuicao_marginal(ret, {"A": 0.5, "B": 0.5})
    assert resultado["A"].razao > resultado["B"].razao


def test_contribuicao_marginal_ativo_sem_serie_suficiente_fica_ausente():
    # C tem apenas 10 observacoes validas (abaixo do piso de 18): tem que
    # sair do resultado, nunca aparecer com contribuicao 0.0.
    idx = pd.date_range("2018-01-31", periods=90, freq="ME")
    rng = np.random.default_rng(5)
    ret = _retornos_multifator()[["A", "B"]].copy()
    curta = np.full(90, np.nan)
    curta[:10] = rng.normal(0, 0.05, 10)
    ret["C"] = curta
    ret.index = idx

    pesos = {"A": 0.4, "B": 0.4, "C": 0.2}
    resultado = contribuicao_marginal(ret, pesos)
    assert "C" not in resultado
    assert set(resultado) == {"A", "B"}


def test_contribuicao_marginal_serie_inteiramente_nan_fica_ausente():
    ret = _retornos_multifator()[["A", "B"]].copy()
    ret["C"] = np.nan
    pesos = {"A": 0.4, "B": 0.4, "C": 0.2}
    resultado = contribuicao_marginal(ret, pesos)
    assert "C" not in resultado


def _retornos_com_sobreposicao_curta():
    """A e B com 24 meses; C com 26 meses, mas so 5 em comum com A e B —
    mesmo fixture de tests/test_global_correlation.py (nao compartilhado
    entre arquivos de teste por convencao deste projeto)."""
    rng = np.random.default_rng(7)
    idx = pd.date_range("2021-01-31", periods=45, freq="ME")
    a = np.full(45, np.nan)
    b = np.full(45, np.nan)
    c = np.full(45, np.nan)
    a[:24] = rng.normal(0, 0.05, 24)
    b[:24] = rng.normal(0, 0.05, 24)
    c[19:] = rng.normal(0, 1.00, 26)
    return pd.DataFrame({"A": a, "B": b, "C": c}, index=idx)


def test_contribuicao_marginal_sobreposicao_curta_exclui_sem_zerar_os_demais():
    ret = _retornos_com_sobreposicao_curta()
    pesos = {"A": 0.4, "B": 0.4, "C": 0.2}
    resultado = contribuicao_marginal(ret, pesos)

    assert "C" not in resultado
    assert set(resultado) == {"A", "B"}
    so_ab = ret[["A", "B"]]
    esperado = contribuicao_marginal(so_ab, {"A": 0.5, "B": 0.5})
    assert resultado["A"].contribuicao == pytest.approx(esperado["A"].contribuicao)
    assert resultado["B"].contribuicao == pytest.approx(esperado["B"].contribuicao)


def test_contribuicao_marginal_peso_zero_da_contribuicao_zero_e_razao_none():
    ret = _retornos_multifator()[["A", "B", "C"]]
    pesos = {"A": 0.5, "B": 0.5, "C": 0.0}
    resultado = contribuicao_marginal(ret, pesos)

    assert "C" in resultado, "peso zero e um fato do portfolio, nao dado ausente"
    assert resultado["C"].contribuicao == pytest.approx(0.0, abs=1e-12)
    assert resultado["C"].razao is None
    soma = sum(r.contribuicao for r in resultado.values())
    assert soma == pytest.approx(_sigma_p_esperado(ret, {"A": 0.5, "B": 0.5, "C": 0.0}), abs=1e-9)


def test_contribuicao_marginal_um_ativo_so_devolve_vazio():
    ret = _retornos_multifator()[["A"]]
    assert contribuicao_marginal(ret, {"A": 1.0}) == {}


def test_contribuicao_marginal_quadro_vazio_devolve_vazio():
    assert contribuicao_marginal(pd.DataFrame(), {}) == {}


def test_contribuicao_marginal_sem_peso_devolve_vazio():
    ret = _retornos_multifator()
    assert contribuicao_marginal(ret, {}) == {}
    assert contribuicao_marginal(ret, {"A": 0.0, "B": 0.0, "C": 0.0, "D": 0.0}) == {}


def test_contribuicao_marginal_e_deterministica_independente_da_ordem():
    ret = _retornos_multifator()
    pesos = {"A": 0.4, "B": 0.3, "C": 0.2, "D": 0.1}

    resultado1 = contribuicao_marginal(ret, pesos)
    ret_embaralhado = ret[["D", "B", "A", "C"]]
    pesos_embaralhado = {"D": 0.1, "C": 0.2, "A": 0.4, "B": 0.3}
    resultado2 = contribuicao_marginal(ret_embaralhado, pesos_embaralhado)

    assert list(resultado1) == list(resultado2) == sorted(resultado1)
    for simbolo in resultado1:
        assert resultado1[simbolo].contribuicao == pytest.approx(resultado2[simbolo].contribuicao)
        assert resultado1[simbolo].razao == pytest.approx(resultado2[simbolo].razao)
