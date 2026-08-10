"""Correlacao, redundancia e diversificacao real."""
import numpy as np
import pandas as pd
import pytest

from core.global_portfolio.correlation import (
    LIMIAR_REDUNDANCIA,
    apostas_efetivas,
    correlacao_media,
    matriz,
    pares_redundantes,
    razao_diversificacao,
)


def _retornos_com_correlacao(rho: float, n: int = 60, cols=("A", "B")):
    rng = np.random.default_rng(11)
    base = rng.normal(0, 0.05, n)
    ruido = rng.normal(0, 0.05, n)
    b = rho * base + np.sqrt(max(0.0, 1 - rho ** 2)) * ruido
    idx = pd.date_range("2021-01-31", periods=n, freq="ME")
    return pd.DataFrame({cols[0]: base, cols[1]: b}, index=idx)


def test_matriz_recupera_a_correlacao_plantada():
    ret = _retornos_com_correlacao(0.9)
    assert matriz(ret).loc["A", "B"] == pytest.approx(0.9, abs=0.08)


def test_pares_redundantes_encontra_o_par_alto():
    ret = _retornos_com_correlacao(0.95)
    pares = pares_redundantes(ret)
    assert len(pares) == 1
    a, b, c = pares[0]
    assert {a, b} == {"A", "B"}
    assert c > LIMIAR_REDUNDANCIA


def test_pares_redundantes_ignora_correlacao_baixa():
    assert pares_redundantes(_retornos_com_correlacao(0.1)) == []


def test_correlacao_media_de_ativos_independentes_e_proxima_de_zero():
    # n=500 aqui, nao 60 como nos vizinhos: o erro padrao da correlacao amostral
    # entre series independentes e ~1/sqrt(n). Em n=60 (~0.13) a semente 11
    # sorteia 0,333 por acaso puro — quase 3 desvios do zero — e derruba o
    # teste sem nenhum defeito na implementacao. Em n=500 (~0.045) a tolerancia
    # de 0.15 fica a mais de 3 desvios-padrao, entao o teste passa a medir o
    # que o nome promete. Nao "tidy" isso de volta para 60.
    assert correlacao_media(_retornos_com_correlacao(0.0, n=500)) == pytest.approx(0.0, abs=0.15)


def test_razao_de_diversificacao_e_um_quando_tudo_e_identico():
    ret = _retornos_com_correlacao(1.0)
    r = razao_diversificacao(ret, {"A": 0.5, "B": 0.5})
    assert r == pytest.approx(1.0, abs=0.02)


def test_razao_de_diversificacao_cresce_com_independencia():
    identico = razao_diversificacao(_retornos_com_correlacao(0.99), {"A": .5, "B": .5})
    independente = razao_diversificacao(_retornos_com_correlacao(0.0), {"A": .5, "B": .5})
    assert independente > identico


def test_apostas_efetivas_de_dois_independentes_se_aproxima_de_dois():
    ret = _retornos_com_correlacao(0.0)
    assert apostas_efetivas(ret, {"A": 0.5, "B": 0.5}) == pytest.approx(2.0, abs=0.4)


def test_apostas_efetivas_de_dois_identicos_se_aproxima_de_um():
    ret = _retornos_com_correlacao(1.0)
    assert apostas_efetivas(ret, {"A": 0.5, "B": 0.5}) == pytest.approx(1.0, abs=0.3)


def test_um_ativo_so_nao_levanta():
    ret = _retornos_com_correlacao(0.5)[["A"]]
    assert pares_redundantes(ret) == []
    assert correlacao_media(ret) is None
    assert razao_diversificacao(ret, {"A": 1.0}) is None


def _retornos_com_sobreposicao_curta():
    """A e B com 24 meses; C com 26 meses, mas so 5 em comum com A e B.

    Todos os tres passam no piso individual de 18 observacoes — o que falha e a
    sobreposicao PAR A PAR (5 meses), exatamente o caso que a matriz de
    correlacao ja recusa. C tem variancia 20x maior para que, se entrasse no
    calculo, o resultado mudasse de forma visivel.
    """
    rng = np.random.default_rng(7)
    idx = pd.date_range("2021-01-31", periods=45, freq="ME")
    a = np.full(45, np.nan)
    b = np.full(45, np.nan)
    c = np.full(45, np.nan)
    a[:24] = rng.normal(0, 0.05, 24)
    b[:24] = rng.normal(0, 0.05, 24)
    c[19:] = rng.normal(0, 1.00, 26)
    return pd.DataFrame({"A": a, "B": b, "C": c}, index=idx)


def test_sobreposicao_curta_nao_contamina_diversificacao():
    ret = _retornos_com_sobreposicao_curta()
    pesos = {"A": 0.4, "B": 0.4, "C": 0.2}

    razao = razao_diversificacao(ret, pesos)
    apostas = apostas_efetivas(ret, pesos)

    assert razao is not None and not np.isnan(razao)
    assert apostas is not None and not np.isnan(apostas)
    # C e descartado: o resultado tem de ser identico ao de A e B sozinhos, com
    # os pesos renormalizados (0,4/0,4 -> 0,5/0,5).
    so_ab = ret[["A", "B"]]
    assert razao == pytest.approx(razao_diversificacao(so_ab, {"A": 0.5, "B": 0.5}))
    assert apostas == pytest.approx(apostas_efetivas(so_ab, {"A": 0.5, "B": 0.5}))


def test_sem_nenhum_par_confiavel_devolve_none():
    # A nos 24 primeiros meses, B nos 24 ultimos: zero sobreposicao.
    idx = pd.date_range("2021-01-31", periods=48, freq="ME")
    rng = np.random.default_rng(3)
    a = np.full(48, np.nan)
    b = np.full(48, np.nan)
    a[:24] = rng.normal(0, 0.05, 24)
    b[24:] = rng.normal(0, 0.05, 24)
    ret = pd.DataFrame({"A": a, "B": b}, index=idx)
    assert razao_diversificacao(ret, {"A": 0.5, "B": 0.5}) is None
    assert apostas_efetivas(ret, {"A": 0.5, "B": 0.5}) is None


def test_quadro_vazio_nao_levanta():
    vazio = pd.DataFrame()
    assert matriz(vazio).empty
    assert pares_redundantes(vazio) == []
    assert correlacao_media(vazio) is None
    assert apostas_efetivas(vazio, {}) is None
