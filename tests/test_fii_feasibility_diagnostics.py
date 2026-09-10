"""Regressões para a explicação de bloqueios da carteira de FIIs."""
from __future__ import annotations

import pandas as pd

import core.fii_portfolio_v4 as portfolio
from core.fii_methodology import MacroScenario
from core.fii_portfolio_v4 import PortfolioPolicy
from tests.test_fii_portfolio_v4 import _candidate
from views.fiis import _linhas_de_factibilidade


def test_bloqueio_da_pre_selecao_conserva_bandas_e_limites_aplicados(monkeypatch):
    """O bloqueio precisa manter a evidência usada pela interface."""
    rows = [_candidate(0, "tijolo"), _candidate(1, "papel")]
    monkeypatch.setattr(
        portfolio,
        "_candidate_pool",
        lambda *_args, **_kwargs: ([], {"reason": "inviável para teste"}),
    )

    result = portfolio.optimize_diligence_portfolio(
        rows,
        MacroScenario(selic=12, ipca=5),
        policy=PortfolioPolicy(max_assets=14, max_asset=.10),
    )

    diagnostics = result["feasibility_diagnostics"]
    assert diagnostics["candidate_count"] == 2
    assert diagnostics["effective_type_bands"]
    assert diagnostics["portfolio_limits"] == {
        "max_assets": 14,
        "min_asset_weight": .02,
        "max_asset": .10,
        "min_daily_liquidity": 1_000_000.0,
        "max_illiquid": .10,
        "max_weighted_uncertainty": .35,
    }


def test_tabelas_de_factibilidade_exibem_parametros_sem_inventar_dados():
    categories, controls = _linhas_de_factibilidade({
        "feasibility_diagnostics": {
            "available_by_type": {"tijolo": 5, "papel": 4},
            "effective_type_bands": {
                "tijolo": {"min": .40, "max": .60},
                "papel": {"min": .20, "max": .40},
            },
            "portfolio_limits": {"max_assets": 14, "max_asset": .10},
        },
    })

    assert categories.to_dict("records") == [
        {"Categoria": "Tijolo", "FIIs elegíveis": 5,
         "Piso da alocação": .40, "Teto da alocação": .60},
        {"Categoria": "Papel", "FIIs elegíveis": 4,
         "Piso da alocação": .20, "Teto da alocação": .40},
    ]
    assert controls.loc[0, "Valor"] == 14
    assert controls.loc[2, "Valor"] == .10
    assert pd.isna(controls.loc[3, "Valor"])
