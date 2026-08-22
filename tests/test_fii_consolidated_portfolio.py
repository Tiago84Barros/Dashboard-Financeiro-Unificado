import pandas as pd
import pytest

from views.fiis import (
    _fii_specific_metrics_frame,
    _merge_portfolio_views,
    _portfolio_return_correlation,
    _quality_portfolio_view,
)


def test_merge_portfolio_views_consolidates_rows_and_preserves_both_methods():
    primary = pd.DataFrame([
        {"Ticker": "AAAA11", "Tipo": "papel", "Peso v4": .60, "Score v4": 80,
         "DY 12m": .12},
        {"Ticker": "BBBB11", "Tipo": "tijolo", "Peso v4": .40, "Score v4": 70,
         "DY 12m": .09},
    ])
    complementary = pd.DataFrame([
        {"Ticker": "AAAA11", "Tipo": "papel", "Peso complementar": .45,
         "Score complementar": 91, "Pior queda": -.18},
        {"Ticker": "CCCC11", "Tipo": "fof", "Peso complementar": .55,
         "Score complementar": 75, "Pior queda": -.22},
    ])

    result = _merge_portfolio_views(primary, complementary)

    assert result["Ticker"].tolist() == ["AAAA11", "BBBB11", "CCCC11"]
    assert result["Ticker"].is_unique
    aaaa = result.set_index("Ticker").loc["AAAA11"]
    assert aaaa["Peso v4"] == .60
    assert aaaa["Peso complementar"] == .45
    assert aaaa["Score v4"] == 80
    assert aaaa["Score complementar"] == 91
    assert aaaa["Pior queda"] == -.18


def test_merge_portfolio_views_accepts_only_one_available_method():
    complementary = pd.DataFrame([
        {"Ticker": "AAAA11", "Peso complementar": 1.0, "Score complementar": 80},
    ])

    result = _merge_portfolio_views(None, complementary)

    assert result.to_dict("records") == complementary.to_dict("records")


def test_specific_metrics_are_applied_only_to_the_appropriate_fii_type():
    inputs = pd.DataFrame([
        {"ticker": "BRIK11", "tipo": "tijolo", "portfolio_item_count": 12,
         "property_count": 10, "vacancia_fisica": .07,
         "property_diversification": .82, "region_count": 3,
         "regions": {"Sudeste": .7, "Sul": .3}},
        {"ticker": "PAPR11", "tipo": "papel", "portfolio_item_count": 25,
         "financial_asset_count": 24, "issuer_diversification": .76,
         "issuers": {"A": .6, "B": .4}},
        {"ticker": "FOFS11", "tipo": "fof", "portfolio_item_count": 8,
         "holdings": {"FII A": .5, "FII B": .3, "FII C": .2}},
    ])

    result = _fii_specific_metrics_frame(inputs).set_index("Ticker")

    assert result.loc["BRIK11", "Qtd. ativos"] == 12
    assert result.loc["BRIK11", "Vacância"] == .07
    assert result.loc["BRIK11", "Divers. imóveis"] == .82
    assert result.loc["BRIK11", "Divers. regiões"] == pytest.approx(.42)
    assert pd.isna(result.loc["BRIK11", "Divers. papel"])
    assert result.loc["PAPR11", "Qtd. papéis"] == 24
    assert result.loc["PAPR11", "Divers. papel"] == .76
    assert pd.isna(result.loc["PAPR11", "Vacância"])
    assert result.loc["FOFS11", "Qtd. fundos"] == 3
    assert result.loc["FOFS11", "Divers. FoF"] == pytest.approx(.62)
    assert pd.isna(result.loc["FOFS11", "Qtd. papéis"])


def test_quality_portfolio_view_removes_duplicate_ticker_key():
    pf = pd.DataFrame([{
        "ticker": "AAAA11", "Ticker": "AAAA11", "peso": 1.0, "tipo": "papel",
        "segmento": "CRI", "Liquidez_Diaria": 2_000_000, "dy_12m": .12,
        "pvp": .95, "CAGR": .05, "Max_Drawdown": -.20, "Hist_Meses": 30,
        "N_Regioes": 0, "Num_Imoveis": 0, "score": 75,
    }])

    result = _quality_portfolio_view(pf)

    assert result.columns.tolist().count("Ticker") == 1
    assert result.loc[0, "Ticker"] == "AAAA11"
    assert "Peso" in result and "Score" in result
    assert "Peso complementar" not in result and "Score complementar" not in result


def test_portfolio_correlation_requires_twelve_monthly_returns_per_fund():
    dates = pd.date_range("2025-01-31", periods=14, freq="ME")
    prices = pd.DataFrame({
        "AAAA11": [100 + i for i in range(14)],
        "BBBB11": [80 + 2 * i for i in range(14)],
        "SHORT11": [None] * 10 + [50, 51, 52, 53],
    }, index=dates)

    returns, corr = _portfolio_return_correlation(
        prices, ["AAAA11", "BBBB11", "SHORT11"], min_months=12)

    assert not returns.empty
    assert corr.columns.tolist() == ["AAAA11", "BBBB11"]
    assert corr.loc["AAAA11", "BBBB11"] > .99
