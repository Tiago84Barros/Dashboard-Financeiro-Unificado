import pandas as pd

from data_pipeline.market.fii_pit import reconstruct_snapshots


def test_reconstruct_snapshots_uses_latest_observation_and_exposure(monkeypatch):
    prices = pd.DataFrame([
        {"ticker": "TEST11", "date": "2025-01-02", "close": 100.0,
         "adjusted_close": 100.0, "volume": 1_000, "source": "test"},
        {"ticker": "TEST11", "date": "2025-01-31", "close": 101.0,
         "adjusted_close": 101.0, "volume": 1_100, "source": "test"},
    ])
    observations = pd.DataFrame([
        {"ticker": "TEST11", "metric_name": "nav_per_share", "value_numeric": 90.0,
         "value_text": None, "value_json": None, "reference_date": "2024-12-01",
         "knowledge_at": "2024-12-05T00:00:00Z", "availability_quality": "verified_publication",
         "source": "old"},
        {"ticker": "TEST11", "metric_name": "nav_per_share", "value_numeric": 100.0,
         "value_text": None, "value_json": None, "reference_date": "2024-12-31",
         "knowledge_at": "2025-01-10T00:00:00Z", "availability_quality": "verified_publication",
         "source": "latest"},
    ])
    exposures = pd.DataFrame([
        {"ticker": "TEST11", "exposure_type": "asset_class", "exposure_name": "credit",
         "exposure_weight": 0.20, "reference_date": "2024-12-01",
         "knowledge_at": "2024-12-05T00:00:00Z", "availability_quality": "verified_publication",
         "source": "old"},
        {"ticker": "TEST11", "exposure_type": "asset_class", "exposure_name": "credit",
         "exposure_weight": 0.80, "reference_date": "2024-12-31",
         "knowledge_at": "2025-01-10T00:00:00Z", "availability_quality": "verified_publication",
         "source": "latest"},
        {"ticker": "TEST11", "exposure_type": "manager", "exposure_name": "Gestora PIT",
         "exposure_weight": 1.0, "reference_date": "2024-12-31",
         "knowledge_at": "2025-01-10T00:00:00Z", "availability_quality": "verified_publication",
         "source": "latest"},
    ])
    funds = pd.DataFrame([{"ticker": "TEST11", "tipo": "tijolo"}])

    def fake_score(rows, **_kwargs):
        assert rows[0]["nav_per_share"] == 100.0
        assert rows[0]["tipo"] == "papel"
        assert rows[0]["manager"] == "Gestora PIT"
        return [{
            **rows[0], "type_score": 50.0, "confidence": 0.8, "coverage": 0.9,
            "critical_coverage": 0.9, "components": {}, "score_inputs": {},
            "missing_metrics": [], "data_readiness_status": "ready",
        }]

    monkeypatch.setattr("data_pipeline.market.fii_pit.score_fiis_by_type", fake_score)

    snapshots = reconstruct_snapshots(
        prices, pd.DataFrame(), observations, exposures, funds,
        start="2025-01-01", end="2025-01-31",
    )

    assert len(snapshots) == 1
    assert snapshots[0]["ticker"] == "TEST11"
    assert snapshots[0]["fii_type"] == "papel"
    assert snapshots[0]["portfolio_input_json"]["manager"] == "Gestora PIT"
