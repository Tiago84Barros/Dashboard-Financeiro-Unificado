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


def test_rental_share_enters_the_row_only_after_publication(monkeypatch):
    """A fração de imóvel para renda segue o mesmo corte PIT das métricas.

    Publicada depois da decisão, ela não existe para a carteira daquela data,
    e o fundo de obra continua na máscara de setor por cautela.
    """
    from core.fii_lookthrough import dimension_is_applicable

    prices = pd.DataFrame([
        {"ticker": "OBRA11", "date": day, "close": 100.0,
         "adjusted_close": 100.0, "volume": 1_000, "source": "test"}
        for day in ("2025-01-02", "2025-01-31", "2025-02-03", "2025-02-28")
    ])
    observations = pd.DataFrame([
        {"ticker": "OBRA11", "metric_name": "pct_imoveis_renda", "value_numeric": 0.0,
         "value_text": None, "value_json": None, "reference_date": "2025-01-31",
         "knowledge_at": "2025-02-10T00:00:00Z",
         "availability_quality": "verified_publication", "source": "cvm_informe_mensal"},
    ])
    funds = pd.DataFrame([{"ticker": "OBRA11", "tipo": "tijolo"}])
    rows_by_date: dict[str, dict] = {}

    def fake_score(rows, *, as_of, **_kwargs):
        rows_by_date[as_of.isoformat()] = rows[0]
        return [{
            **rows[0], "type_score": 50.0, "confidence": 0.8, "coverage": 0.9,
            "critical_coverage": 0.9, "components": {}, "score_inputs": {},
            "missing_metrics": [], "data_readiness_status": "ready",
        }]

    monkeypatch.setattr("data_pipeline.market.fii_pit.score_fiis_by_type", fake_score)
    reconstruct_snapshots(prices, pd.DataFrame(), observations, pd.DataFrame(), funds,
                          start="2025-01-31", end="2025-02-28")

    janeiro, fevereiro = rows_by_date["2025-01-31"], rows_by_date["2025-02-28"]
    assert "pct_imoveis_renda" not in janeiro
    assert dimension_is_applicable(janeiro, "sector") is True
    assert fevereiro["pct_imoveis_renda"] == 0.0
    assert dimension_is_applicable(fevereiro, "sector") is False
