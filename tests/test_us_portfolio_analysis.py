import pandas as pd
import pytest

from core.us_macro import USMacroSnapshot, evaluate_macro
from core.us_portfolio_analysis import evaluate_portfolio, normalize_holdings


def _universe():
    rows = []
    for i, (symbol, sector, score) in enumerate([
        ("A", "Technology", 80), ("B", "Healthcare", 70),
        ("C", "Financial Services", 60), ("D", "Energy", 40),
    ]):
        rows.append({
            "symbol": symbol, "name": symbol, "sector": sector, "score": score,
            "score_quality": score, "score_growth": score - 2,
            "score_solidity": score + 1, "score_capital_efficiency": score,
            "score_valuation": score - 1, "score_shareholder": score + 2,
        })
    return pd.DataFrame(rows)


def test_normalize_holdings_accepts_percent_and_aggregates():
    raw = pd.DataFrame({"Ticker": ["a", "A", "b"], "Peso": [25, 25, 50]})
    out = normalize_holdings(raw)
    assert set(out["symbol"]) == {"A", "B"}
    assert out["weight"].sum() == pytest.approx(1.0)
    assert out.set_index("symbol").loc["A", "weight"] == pytest.approx(0.5)


def test_portfolio_evaluation_reports_score_diversification_and_missing():
    holdings = pd.DataFrame({"symbol": ["A", "B", "ZZZ"], "weight": [40, 40, 20]})
    result = evaluate_portfolio(holdings, _universe())
    assert result["ok"] is True
    assert result["score"] == pytest.approx(75.0)
    assert result["coverage_weight"] == pytest.approx(80.0)
    assert "ZZZ" in result["missing"]
    assert result["effective_assets"] > 2
    assert result["track_scores"]


def test_macro_regime_and_sector_impacts_are_bounded():
    favorable = evaluate_macro(USMacroSnapshot(
        fed_funds=2.5, cpi_yoy=2.0, real_gdp_yoy=3.0, unemployment=3.8,
        yield_curve_10y_2y=0.8, high_yield_spread=2.8,
    ))
    adverse = evaluate_macro(USMacroSnapshot(
        fed_funds=7.0, cpi_yoy=7.0, real_gdp_yoy=-2.0, unemployment=7.0,
        yield_curve_10y_2y=-1.0, high_yield_spread=8.0,
    ))
    assert favorable["score"] > adverse["score"]
    assert all(-10 <= value <= 10 for value in favorable["sector_impacts"].values())
