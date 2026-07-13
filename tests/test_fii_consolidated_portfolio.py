import pandas as pd

from views.fiis import _merge_portfolio_views


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
