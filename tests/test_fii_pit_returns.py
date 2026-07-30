import pandas as pd
import pytest

from datetime import date

from data_pipeline.market.fii_pit import (VALIDATION_PROTOCOL_VERSION, _monthly_returns,
                                         _macro_scenarios, _parse_b3_ifix_monthly)


def test_monthly_returns_uses_close_and_dividends_when_adjustment_factor_is_rewritten():
    prices = pd.DataFrame([
        {"ticker": "XPML11", "date": "2025-12-01", "close": 108.06, "adjusted_close": 14.0435},
        {"ticker": "XPML11", "date": "2026-01-01", "close": 110.75, "adjusted_close": 106.1491},
    ])
    dividends = pd.DataFrame([
        {"ticker": "XPML11", "event_date": "2026-01-15", "ex_date": None,
         "payment_date": None, "amount": .92},
    ])
    result = _monthly_returns(prices, dividends, as_of="2026-02-15")
    assert len(result) == 1
    expected = 110.75 / 108.06 - 1 + .92 / 108.06
    assert result.iloc[0]["total_return"] == pytest.approx(expected)
    assert result.iloc[0]["return_method"] == "close_plus_dividends"


def test_monthly_returns_excludes_current_incomplete_month():
    prices = pd.DataFrame([
        {"ticker": "A11", "date": "2026-05-01", "close": 100, "adjusted_close": 100},
        {"ticker": "A11", "date": "2026-06-01", "close": 101, "adjusted_close": 101},
        {"ticker": "A11", "date": "2026-07-01", "close": 102, "adjusted_close": 102},
    ])
    result = _monthly_returns(prices, as_of="2026-07-16")
    assert result["date"].max() == pd.Timestamp("2026-06-30")
    assert len(result) == 1


def test_monthly_returns_uses_adjusted_value_for_split_like_close_jump():
    prices = pd.DataFrame([
        {"ticker": "SPLT11", "date": "2026-04-01", "close": 100, "adjusted_close": 10},
        {"ticker": "SPLT11", "date": "2026-05-01", "close": 10, "adjusted_close": 10.2},
    ])
    result = _monthly_returns(prices, as_of="2026-06-15")
    assert result.iloc[0]["total_return"] == pytest.approx(.02)
    assert result.iloc[0]["return_method"] == "adjusted_split_fallback"


def test_parse_b3_ifix_monthly_filters_range_and_invalid_values():
    payload = [
        {"month": 12, "year": 2023, "indexClosingRate": 3311.43},
        {"month": 1, "year": 2024, "indexClosingRate": 3330.12},
        {"month": 2, "year": 2024, "indexClosingRate": None},
    ]
    rows = _parse_b3_ifix_monthly(
        payload, start=date(2024, 1, 1), end=date(2024, 12, 31),
    )
    assert rows == [{"date": date(2024, 1, 31), "value": 3330.12}]
    assert VALIDATION_PROTOCOL_VERSION == "fii-pit-robust-optimizer-3.3.0"


def test_macro_scenario_uses_only_month_closed_before_decision(monkeypatch):
    macro = pd.DataFrame({
        "data": pd.to_datetime([
            "2024-01-31", "2025-01-31", "2025-02-28",
        ], utc=True),
        "selic": [11.0, 13.0, 14.0],
        "ipca": [4.0, 4.5, 5.0],
    })
    monkeypatch.setattr(
        "data_pipeline.market.fii_pit.pd.read_sql",
        lambda *_args, **_kwargs: macro.copy(),
    )

    scenarios = _macro_scenarios(object(), [pd.Timestamp("2025-02-28")])

    assert scenarios["2025-02-28"]["selic"] == 13.0
    assert scenarios["2025-02-28"]["ipca"] == 4.5
    assert scenarios["2025-02-28"]["selic_change_12m"] == 2.0
