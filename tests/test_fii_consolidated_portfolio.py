import pandas as pd
import pytest

from views.fiis import _fii_specific_metrics_frame, _merge_portfolio_views


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
