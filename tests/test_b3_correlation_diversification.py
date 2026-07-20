import numpy as np
import pandas as pd
import pytest

from core.b3_correlation_diversification import (
    average_pairwise_correlation,
    correlation_coverage,
    correlation_matrix,
    diversification_index,
    high_correlation_pairs,
    monthly_returns_for,
)


def _precos_sinteticos(n=36, seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2021-01-31", periods=n, freq="ME")
    commodity_factor = rng.normal(0, 0.05, n).cumsum()
    ruido_a = rng.normal(0, 0.02, n).cumsum()
    ruido_b = rng.normal(0, 0.02, n).cumsum()
    return pd.DataFrame(
        {
            # BRAP3/UNIP6/LEVE3: mesmo fator (correlacionadas entre si)
            "BRAP3": 100 * np.exp(commodity_factor + rng.normal(0, 0.01, n)),
            "UNIP6": 100 * np.exp(commodity_factor + rng.normal(0, 0.01, n)),
            "LEVE3": 100 * np.exp(commodity_factor + rng.normal(0, 0.01, n)),
            # WEGE3: fator independente
            "WEGE3": 100 * np.exp(ruido_a),
            "CEBR5": 100 * np.exp(ruido_b),
        },
        index=idx,
    )


def test_monthly_returns_for_filtra_colunas_ausentes():
    df = _precos_sinteticos()
    rets = monthly_returns_for(df, ["BRAP3", "AUSENTE3", "WEGE3"])

    assert list(rets.columns) == ["BRAP3", "WEGE3"]
    assert len(rets) == len(df) - 1  # perde a 1a linha (pct_change)


def test_monthly_returns_for_vazio_sem_precos():
    assert monthly_returns_for(pd.DataFrame(), ["X"]).empty
    assert monthly_returns_for(None, ["X"]).empty


def test_correlation_matrix_detecta_fator_comum():
    df = _precos_sinteticos()
    rets = monthly_returns_for(df, list(df.columns))
    corr = correlation_matrix(rets, min_obs=12)

    # BRAP3/UNIP6/LEVE3 compartilham o fator de commodities: correlação alta.
    assert corr.loc["BRAP3", "UNIP6"] > 0.6
    assert corr.loc["BRAP3", "LEVE3"] > 0.6
    # WEGE3 é independente: correlação baixa com o cluster de commodities.
    assert abs(corr.loc["BRAP3", "WEGE3"]) < 0.5


def test_correlation_matrix_exige_sobreposicao_minima():
    rets = pd.DataFrame({"A": [0.01, 0.02, None], "B": [0.01, None, 0.02]})
    corr = correlation_matrix(rets, min_obs=5)
    assert pd.isna(corr.loc["A", "B"])


def test_average_pairwise_correlation_ignora_diagonal():
    corr = pd.DataFrame([[1.0, 0.5], [0.5, 1.0]], columns=["A", "B"], index=["A", "B"])
    assert average_pairwise_correlation(corr) == pytest.approx(0.5)


def test_average_pairwise_correlation_vazio_retorna_zero():
    assert average_pairwise_correlation(pd.DataFrame()) == 0.0


def test_high_correlation_pairs_ordena_por_magnitude():
    corr = pd.DataFrame(
        [[1.0, 0.9, 0.1], [0.9, 1.0, -0.7], [0.1, -0.7, 1.0]],
        columns=["A", "B", "C"], index=["A", "B", "C"],
    )
    pairs = high_correlation_pairs(corr, threshold=0.5)

    assert pairs[0] == ("A", "B", 0.9)
    assert pairs[1] == ("B", "C", -0.7)
    assert len(pairs) == 2  # A-C (0.1) fica de fora


def test_correlation_coverage_conta_pares_com_dados_suficientes():
    df = _precos_sinteticos(n=36)
    rets = monthly_returns_for(df, list(df.columns))
    ok, total = correlation_coverage(rets, min_obs=18)
    assert total == 10  # C(5,2)
    assert ok == 10      # série sintética sem buracos


def test_diversification_index_concentrado_vs_espalhado():
    concentrado = diversification_index({"A": 1.0, "B": 0.0, "C": 0.0})
    espalhado = diversification_index({"A": 0.2, "B": 0.2, "C": 0.2, "D": 0.2, "E": 0.2})

    assert concentrado == pytest.approx(0.0)
    assert espalhado > concentrado
    assert espalhado == pytest.approx(0.8)


def test_diversification_index_vazio_retorna_zero():
    assert diversification_index({}) == 0.0
