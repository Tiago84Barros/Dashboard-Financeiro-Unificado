"""Valor isolado no grupo vale o neutro nos EUA (gêmeo do achado D2, 0.9.1).

O percentil de uma métrica que só UMA empresa do grupo tem saía 1,0 -- ou 0,0
nas métricas em que menor é melhor --, nota de ponta tirada de uma observação
sem par. Na B3 a regra entrou na 2.33.0; aqui ela vale nos três percentis dos
EUA: o do score por indústria e os dois da escala 0–100 (qualidade de caixa,
momento e P&D).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import core.us_score as sc
from core.b3_company_score import _winsorized_percentile as b3_percentil
from core.us_advanced_lab import _percentile as lab_percentil
from core.us_estilos import _percentil as estilos_percentil
from core.us_methodology import US_FUNDAMENTAL_SCORE_VERSION


def test_versao_sobe_porque_a_nota_muda():
    assert US_FUNDAMENTAL_SCORE_VERSION == "0.9.1"


@pytest.mark.parametrize("valores", [
    [3.0, None, None, None],
    [None, -7.0, None],
    [42.0],
])
def test_um_valor_so_e_neutro_no_score_por_industria(valores):
    out = sc._winsorized_percentile(pd.Series(valores, dtype=float))
    assert (out == sc.NEUTRAL).all()


def test_dois_valores_continuam_ordenados():
    out = sc._winsorized_percentile(pd.Series([1.0, 2.0, None]))
    assert out.iloc[1] > out.iloc[0]
    assert out.iloc[2] == sc.NEUTRAL


def _industria(n: int = 5) -> pd.DataFrame:
    rows = []
    for i in range(n):
        rows.append({"symbol": f"S{i}", "sector": "Tech", "industry": "Software",
                     "gross_margin": 0.30 + 0.1 * i, "roe": 0.05 + 0.04 * i,
                     "net_debt_ebitda": 4.0 - 0.7 * i})
    return pd.DataFrame(rows)


@pytest.mark.parametrize("metrica,valor", [
    ("interest_coverage", 12.0),     # maior é melhor: antes saía 1,0
    ("debt_to_equity", 0.4),         # menor é melhor: antes saía 0,0
    ("ev_ebitda", 9.0),              # recíproco: antes saía 1,0
])
def test_metrica_isolada_em_industria_grande_fica_no_neutro(metrica, valor):
    df = _industria()
    df[metrica] = np.nan
    df.loc[2, metrica] = valor
    ranked = sc._rank_within(df, "industry", min_group=4)
    assert ranked.loc[2, metrica] == pytest.approx(sc.NEUTRAL)
    assert (ranked[metrica] == sc.NEUTRAL).all()


def test_metrica_isolada_nao_tira_a_pior_do_fundo():
    """A pior da indústria com a única cobertura de juros do grupo: antes esse
    valor valia 1,0 e a empurrava para cima; neutro, a ordem é a das métricas
    que têm par."""
    sem = sc.score_cross_section(_industria(), min_group=4)
    com_df = _industria()
    com_df["interest_coverage"] = np.nan
    com_df.loc[0, "interest_coverage"] = 50.0      # S0 é a pior
    com = sc.score_cross_section(com_df, min_group=4)
    assert list(sem["symbol"]) == list(com["symbol"])
    assert list(com["symbol"])[-1] == "S0"


def test_valor_isolado_vale_menos_que_o_extremo_de_antes(monkeypatch):
    """Mesma entrada com o percentil antigo (rank puro) dá nota maior à pior:
    é o salto que a 0.9.1 tira."""
    com_df = _industria()
    com_df["interest_coverage"] = np.nan
    com_df.loc[0, "interest_coverage"] = 50.0
    novo = sc.score_cross_section(com_df, min_group=4).set_index("symbol")
    original = sc._winsorized_percentile

    def antigo(s, lower=0.05, upper=0.95):
        if s.dropna().shape[0] == 1:
            return s.rank(pct=True).fillna(sc.NEUTRAL)
        return original(s, lower, upper)

    monkeypatch.setattr(sc, "_winsorized_percentile", antigo)
    velho = sc.score_cross_section(com_df, min_group=4).set_index("symbol")
    assert float(novo.loc["S0", "score"]) < float(velho.loc["S0", "score"])


def test_equivalente_ao_percentil_da_b3():
    """Os dois motores dão a mesma nota ao valor isolado e ao grupo com par."""
    for valores in ([5.0, None, None], [1.0, 4.0, 2.0, 8.0, 3.0]):
        s = pd.Series(valores, dtype=float)
        us = sc._winsorized_percentile(s)
        b3 = b3_percentil(s, higher_is_better=True)
        assert us.values == pytest.approx(b3.values)


def test_percentil_do_laboratorio_isolado_e_50():
    assert (lab_percentil(pd.Series([np.nan, 0.12, np.nan]), True) == 50.0).all()
    assert (lab_percentil(pd.Series([np.inf, 0.12]), False) == 50.0).all()
    ordenado = lab_percentil(pd.Series([0.1, 0.3]), True)
    assert ordenado.iloc[1] > ordenado.iloc[0]


def test_percentil_de_estilo_isolado_e_50_e_ausencia_continua_ausente():
    out = estilos_percentil(pd.Series([np.nan, 0.25, np.nan]))
    assert out.iloc[1] == 50.0
    assert out.iloc[[0, 2]].isna().all()
    assert estilos_percentil(pd.Series([np.nan, np.nan])).isna().all()
    ordenado = estilos_percentil(pd.Series([0.1, np.nan, 0.3]))
    assert ordenado.iloc[2] > ordenado.iloc[0] and np.isnan(ordenado.iloc[1])
