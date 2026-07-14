import pandas as pd

from data_pipeline.market.fii_pit import reconstruct_snapshots


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
