"""
A intensidade de shrinkage precisa medir a mesma coisa no numerador e no
denominador.

`ledoit_wolf_shrinkage(target="diagonal")` encolhe S na direcao de
F = diag(S). O alvo preserva a diagonal, entao o unico efeito do alpha e
encolher as CORRELACOES: alpha = 1 devolve uma matriz diagonal, isto e,
declara que os ativos sao independentes -- exatamente o que
`core/markowitz.py` foi escrito para impedir (o proprio cabecalho cita
BBAS3 + ITUB4 + SANB3 com rho ~ 0,85).

O alpha e a razao b2/d2. Com alvo diagonal, `d2 = ||S - F||^2` e zero na
diagonal por construcao: mede so massa fora dela. Mas `b2`, o erro de
amostragem de S, era somado sobre a MATRIZ INTEIRA -- diagonal inclusa.
Numerador e denominador passavam a medir suportes diferentes, e a variancia
das variancias (que o alvo nem encolhe) empurrava o alpha para cima.
Schafer & Strimmer (2005), citado no docstring da propria funcao, restringe
as duas somas as entradas fora da diagonal.

Medido em 24/08/2026 contra retornos mensais reais da B3:

  178 cestas de mesmo subsetor, 8 ativos, T mediano 63 meses
    alpha do app:        mediana 1,000 -- cravado em 1,000 em 121 das 178
    alpha pela receita:  mediana 0,413 -- cravado em 1,000 em 6 das 178
    diferenca de peso num ativo: mediana 3,60 pp, maxima 14,93 pp

Ou seja: em 2 de cada 3 cestas o otimizador recebia um mundo diagonal.
O risco realizado 24 meses a frente ficou empatado (5,596% x 5,618% ao mes),
porque o dano se concentra onde a correlacao e fraca -- nas cestas com
rho >= 0,45 o alpha do app nem cravava. O defeito e de estimador, nao de
resultado; mas a UI exibe o numero como "Ledoit-Wolf" e o modulo promete
tratar a redundancia que alpha = 1 apaga.
"""
import numpy as np
import pytest

from core.markowitz import ledoit_wolf_shrinkage


def _retornos_com_um_ativo_volatil(rng, T=120, K=6, rho=0.5):
    """K ativos correlacionados + 1 independente com volatilidade 10x."""
    corr = np.full((K, K), rho)
    np.fill_diagonal(corr, 1.0)
    base = rng.multivariate_normal(np.zeros(K), corr * 0.05**2, T)
    return np.c_[base, rng.normal(0.0, 0.5, T)]


def test_ativo_volatil_e_independente_nao_pode_apagar_as_correlacoes():
    # O 7o ativo nao adiciona estrutura de correlacao nenhuma -- so variancia.
    # Com o alvo diagonal essa variancia nao e encolhida, entao ela nao pode
    # decidir o quanto as correlacoes dos outros seis sao encolhidas.
    rng = np.random.default_rng(7)
    ret = _retornos_com_um_ativo_volatil(rng)

    _, alpha_com_volatil = ledoit_wolf_shrinkage(ret, "diagonal")
    _, alpha_sem_volatil = ledoit_wolf_shrinkage(ret[:, :-1], "diagonal")

    assert alpha_com_volatil < 0.999, (
        "alpha cravado em 1 zera TODAS as correlacoes: o otimizador passa a "
        f"ver ativos independentes (alpha={alpha_com_volatil:.3f})"
    )
    assert alpha_com_volatil - alpha_sem_volatil < 0.50, (
        "um unico ativo independente nao pode multiplicar a intensidade de "
        f"shrinkage ({alpha_sem_volatil:.3f} -> {alpha_com_volatil:.3f})"
    )


def test_alpha_diagonal_nao_depende_da_escala_de_um_ativo_isolado():
    # Reescalar um ativo muda a correlacao dele com ninguem: a matriz de
    # correlacao e invariante. Com alvo diagonal, o alpha tambem deveria ser.
    rng = np.random.default_rng(3)
    corr = np.full((5, 5), 0.4)
    np.fill_diagonal(corr, 1.0)
    ret = rng.multivariate_normal(np.zeros(5), corr * 0.04**2, 150)

    _, alpha_original = ledoit_wolf_shrinkage(ret, "diagonal")
    inflado = ret.copy()
    inflado[:, 0] *= 20.0
    _, alpha_inflado = ledoit_wolf_shrinkage(inflado, "diagonal")

    assert alpha_inflado == pytest.approx(alpha_original, abs=0.05), (
        "a intensidade mudou por causa da escala de um ativo, nao da "
        f"estrutura de correlacao ({alpha_original:.3f} -> {alpha_inflado:.3f})"
    )


def test_alvo_identity_continua_somando_a_matriz_inteira():
    # Com F = m*I o alvo TAMBEM altera a diagonal, entao a formula original de
    # Ledoit & Wolf (2004) soma a matriz inteira nos dois lados. A correcao do
    # alvo diagonal nao pode contaminar este caminho.
    rng = np.random.default_rng(11)
    ret = rng.normal(0.0, 0.05, size=(80, 5))
    X = ret - ret.mean(axis=0, keepdims=True)
    T, K = ret.shape
    S = (X.T @ X) / T
    F = (np.trace(S) / K) * np.eye(K)
    d2 = float(((S - F) ** 2).sum()) / K
    b2 = sum(float(((X[t][:, None] @ X[t][None, :] - S) ** 2).sum())
             for t in range(T)) / (T**2 * K)
    esperado = float(np.clip(min(b2, d2) / d2, 0.0, 1.0))

    _, alpha = ledoit_wolf_shrinkage(ret, "identity")
    assert alpha == pytest.approx(esperado, rel=1e-9)


def test_intensidade_continua_caindo_com_T():
    # Propriedade de Ledoit-Wolf preservada pela correcao: mais observacoes,
    # menos shrinkage.
    rng = np.random.default_rng(5)
    corr = np.full((6, 6), 0.5)
    np.fill_diagonal(corr, 1.0)

    def alpha(T):
        ret = rng.multivariate_normal(np.zeros(6), corr * 0.05**2, T)
        return ledoit_wolf_shrinkage(ret, "diagonal")[1]

    assert alpha(1200) < alpha(60)
