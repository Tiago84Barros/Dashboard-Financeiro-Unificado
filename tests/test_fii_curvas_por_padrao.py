"""Separação das coortes 'padrão da casa' e 'exceção' no backtest de FIIs.

A curva de manchete soma as duas: as carteiras de exceção foram entregues e
omiti-las mediria uma estratégia que ninguém correu. O risco é o oposto --
creditar à metodologia um excesso que veio da concentração cedida. Estes
testes prendem a separação e o nome do drawdown, que não é o de uma
estratégia seguível.
"""
from __future__ import annotations

import math

import pandas as pd
import pytest

from core.fii_validation import _metricas_da_coorte


def _bloco(retornos, benchmarks):
    return pd.DataFrame({
        "portfolio_return": retornos,
        "benchmark_return": benchmarks,
        "turnover": [0.1] * len(retornos),
    })


def test_coorte_vazia_nao_inventa_metrica():
    assert _metricas_da_coorte(_bloco([], [])) == {"periods": 0}


def test_excesso_e_a_media_da_diferenca():
    m = _metricas_da_coorte(_bloco([0.02, 0.04], [0.01, 0.01]))
    assert m["periods"] == 2
    assert m["mean_excess"] == pytest.approx(0.02)
    assert m["annualized_turnover"] == pytest.approx(1.2)


def test_drawdown_sai_com_nome_de_concatenado():
    m = _metricas_da_coorte(_bloco([0.10, -0.20, 0.05], [0.0, 0.0, 0.0]))
    assert "max_drawdown" not in m
    assert m["max_drawdown_concatenado"] == pytest.approx(-0.20)


def test_um_periodo_nao_produz_information_ratio():
    m = _metricas_da_coorte(_bloco([0.03], [0.01]))
    assert math.isnan(m["information_ratio"])
