import pandas as pd

from data_pipeline.market.fii_pit import (
    _features_as_of,
    _monthly_market_features,
    reconstruct_snapshots,
)


def test_reconstruction_respects_knowledge_at_and_builds_monthly_scores():
    dates = pd.date_range("2023-01-02", "2025-03-31", freq="B")
    prices = pd.DataFrame({
        "ticker": "TEST11", "date": dates, "close": 100.0,
        "adjusted_close": 100.0 + pd.Series(range(len(dates))), "volume": 20_000,
        "source": "b3_cotahist",
    })
    dividends = pd.DataFrame({
        "ticker": ["TEST11"] * 24,
        "event_date": pd.date_range("2023-04-15", periods=24, freq="ME"),
        "ex_date": [None] * 24, "payment_date": [None] * 24, "amount": [1.0] * 24,
    })
    observations = pd.DataFrame([
        {"ticker": "TEST11", "metric_name": "nav_per_share", "value_numeric": 110,
         "value_text": None, "value_json": None, "reference_date": "2024-01-31",
         "knowledge_at": "2024-02-15T23:59:59Z", "availability_quality": "verified_publication",
         "source": "cvm_informe_mensal"},
    ])
    exposures = pd.DataFrame([
        {"ticker": "TEST11", "exposure_type": "asset_class", "exposure_name": "real_estate",
         "exposure_weight": .9, "reference_date": "2024-01-31",
         "knowledge_at": "2024-02-15T23:59:59Z", "availability_quality": "verified_publication",
         "source": "cvm_informe_mensal"},
    ])
    funds = pd.DataFrame([{"ticker": "TEST11", "tipo": "papel"}])
    snapshots = reconstruct_snapshots(prices, dividends, observations, exposures, funds,
                                      start="2024-01-31", end="2024-03-31")
    assert snapshots
    january = [row for row in snapshots if row["reference_date"] == "2024-01-31"]
    february = [row for row in snapshots if row["reference_date"] == "2024-02-29"]
    assert january and january[0]["availability_quality"] == "first_observed_proxy"
    assert february and february[0]["fii_type"] == "tijolo"
    assert "pvp" in february[0]["inputs_json"]


def test_pit_liquidity_converts_monthly_bar_volume_to_daily_unit():
    prices = pd.DataFrame({
        "ticker": ["TEST11"] * 6,
        "date": pd.date_range("2026-01-31", periods=6, freq="ME"),
        "close": [10.0] * 6,
        "adjusted_close": [10.0] * 6,
        "volume": [21_000.0] * 6,
        "source": ["brapi_legacy_quote"] * 6,
    })
    bundle = _monthly_market_features(prices, pd.DataFrame())["TEST11"]

    result = _features_as_of(bundle, pd.Timestamp("2026-06-30"))

    assert result["liquidez_diaria"] == 10_000.0
    assert result["liquidity_method"] == "monthly_financial_volume_div_21"


def test_pit_liquidity_keeps_daily_b3_volume_in_daily_unit():
    dates = pd.date_range("2026-04-01", periods=63, freq="B")
    prices = pd.DataFrame({
        "ticker": ["TEST11"] * len(dates),
        "date": dates,
        "close": [10.0] * len(dates),
        "adjusted_close": [10.0] * len(dates),
        "volume": [21_000.0] * len(dates),
        "source": ["b3_cotahist"] * len(dates),
    })
    bundle = _monthly_market_features(prices, pd.DataFrame())["TEST11"]

    result = _features_as_of(bundle, dates.max())

    assert result["liquidez_diaria"] == 210_000.0
    assert result["liquidity_method"] == "daily_financial_volume_median_63"
