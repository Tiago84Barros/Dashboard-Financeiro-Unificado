"""
Sortino precisa medir o TAMANHO das perdas, nao a dispersao entre elas.

`core/us_backtest.performance_stats` calculava o denominador do Sortino como
`r[r < 0].std(ddof=1)` -- o desvio-padrao dos retornos negativos em torno da
media DELES, normalizado pela contagem de perdas. Downside deviation e outra
coisa: sqrt( (1/N) * soma de min(r - MAR, 0)^2 ), sobre todos os periodos.

Dois erros somados. Descentrar apaga o nivel da perda: uma serie que perde
exatamente -5% em todo mes ruim tem dispersao zero entre as perdas, e o app
devolvia `None` -- exibido como "—", isto e, "nao medivel" -- para uma serie
cujo risco de queda e perfeitamente mensuravel (Sortino padrao 0,400).
E normalizar por k-1 em vez de N troca o denominador.

Medido em 24/08/2026, 300 carteiras reais de 10 ativos e 60 meses:

  Sortino exibido: mediana 0,657   Sortino padrao: mediana 0,536
  razao: mediana 1,20x, maxima 1,75x
  exagerado em 222 das 300 carteiras

O numero aparece como cartao "Sortino" em `views/empresas_americanas.py`, ao
lado de Sharpe e Calmar, sem nota de que usava definicao propria.
"""
import numpy as np
import pandas as pd
import pytest

from core.us_backtest import performance_stats


def _sortino_padrao(r, ppy=12, mar=0.0):
    d = np.minimum(np.asarray(r, dtype=float) - mar, 0.0)
    dd = float(np.sqrt((d**2).mean()))
    return float((np.asarray(r, float) - mar).mean() / dd * np.sqrt(ppy)) if dd > 0 else None


def test_perdas_de_tamanho_identico_nao_sao_risco_zero():
    # Oito meses de +3% e quatro de -5%: ha risco de queda, e ele e mensuravel.
    r = pd.Series([0.03] * 8 + [-0.05] * 4)
    resultado = performance_stats(r)["sortino"]
    assert resultado is not None, (
        "perdas consistentes viravam dispersao zero e o cartao exibia '—'"
    )
    assert resultado == pytest.approx(_sortino_padrao(r), rel=1e-9)


def test_sortino_bate_com_a_definicao_padrao_em_serie_aleatoria():
    rng = np.random.default_rng(9)
    r = pd.Series(rng.normal(0.008, 0.05, 120))
    assert performance_stats(r)["sortino"] == pytest.approx(
        _sortino_padrao(r), rel=1e-9
    )


def test_alvo_acompanha_a_taxa_livre_de_risco_informada():
    # Com rf > 0 o deficit e medido contra rf, nao contra zero: meses de ganho
    # abaixo da taxa livre de risco tambem contam como queda.
    rng = np.random.default_rng(2)
    r = pd.Series(rng.normal(0.010, 0.04, 90))
    com_rf = performance_stats(r, rf=0.12)["sortino"]
    assert com_rf == pytest.approx(_sortino_padrao(r, mar=0.12 / 12), rel=1e-9)
    assert com_rf < performance_stats(r, rf=0.0)["sortino"]
