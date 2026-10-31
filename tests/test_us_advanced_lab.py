import pandas as pd

from core.us_advanced_lab import (
    bootstrap_track_score,
    build_entry_scores,
    factor_contributions,
    normalize_weights,
)


def _rows():
    return pd.DataFrame([
        {"symbol": "SAFE", "score_quality": 85, "score_growth": 70,
         "score_solidity": 90, "score_capital_efficiency": 80,
         "score_valuation": 65, "score_shareholder": 60,
         "fcf_margin": .20, "cash_conversion": 1.1, "fcf_yield": .06,
         "net_debt_ebitda": .5, "current_ratio": 1.5, "net_margin": .20,
         "interest_coverage": 12},
        {"symbol": "RISK", "score_quality": 25, "score_growth": 30,
         "score_solidity": 10, "score_capital_efficiency": 20,
         "score_valuation": 55, "score_shareholder": 15,
         "fcf_margin": -.10, "cash_conversion": -.5, "fcf_yield": -.03,
         "net_debt_ebitda": 6, "current_ratio": .5, "net_margin": -.12,
         "interest_coverage": .7},
    ])


def test_score_de_entrada_pune_risco_e_ordena():
    result = build_entry_scores(_rows()).set_index("symbol")
    assert result.loc["SAFE", "entry_score"] > result.loc["RISK", "entry_score"]
    assert result.loc["SAFE", "entry_status"] == "Aprovada"
    assert result.loc["RISK", "entry_status"] == "Excluída"
    assert result.loc["RISK", "risk_penalty"] == 25


def test_pesos_customizados_renormalizam():
    weights = normalize_weights({"quality": 50, "growth": 50, "valuation": 0})
    assert round(sum(weights.values()), 8) == 1
    assert weights["quality"] > weights["valuation"]


def test_atribuicao_e_bootstrap_sao_deterministicos():
    row = _rows().iloc[0]
    contrib = factor_contributions(row)
    assert len(contrib) == 8  # seis fundamentalistas + momento + P&D
    assert contrib["Contribuição"].sum() > 0
    first = bootstrap_track_score(row, n=500, seed=7)
    second = bootstrap_track_score(row, n=500, seed=7)
    assert first == second
    assert first["p05"] <= first["mean"] <= first["p95"]


def test_campos_ausentes_ficam_neutros():
    result = build_entry_scores(pd.DataFrame([{"symbol": "MISS"}]))
    assert result.iloc[0]["score_base_adv"] == 50
    assert result.iloc[0]["risk_penalty"] == 0

