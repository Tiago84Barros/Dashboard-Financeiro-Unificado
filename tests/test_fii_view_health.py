from types import SimpleNamespace

import pandas as pd

from views.fiis import _fii_data_health_metrics, _publication_gate_message


def test_health_metrics_separate_completeness_from_readiness():
    inputs = pd.DataFrame([{
        "ticker": "ABCD11",
        "tipo": "tijolo",
        "dy_12m": .10,
        "pvp": .90,
        "liquidez_diaria": 1_000_000,
        "history_months": 36,
        "max_drawdown": -.20,
        "vacancia_fisica": .05,
        "property_count": 8,
        "region_count": 2,
        "snapshot_metadata": {"schema_version": "fii_selection_inputs.v1",
                              "coverage": {"coverage_pct": 90.0}},
    }])
    scored = [{"data_readiness_status": "insufficient"}]
    gate = SimpleNamespace(median_confidence=.6652)

    metrics = _fii_data_health_metrics(
        pd.DataFrame([{"Ticker": "ABCD11"}]),
        pd.DataFrame([{"Ticker": "ABCD11"}]),
        inputs,
        scored,
        gate,
    )

    assert metrics["snapshot_rows"] == 1
    assert metrics["required_coverage"] == .9
    assert metrics["ready_count"] == 0
    assert metrics["confidence_qualified_count"] == 0
    assert metrics["median_confidence"] == .6652
    assert metrics["snapshot_version"] == "fii_selection_inputs.v1"


def test_publication_message_uses_explicit_counts_and_thresholds():
    metrics = {
        "scoreable_rows": 381,
        "ready_count": 0,
        "ready_fraction": 0.0,
        "confidence_qualified_count": 0,
        "confidence_qualified_fraction": 0.0,
        "median_confidence": .6652,
    }
    gate = SimpleNamespace(
        reasons=("fundos com dados suficientes 0% abaixo de 80%",
                 "confiança mediana 67% abaixo de 75%",
                 "backtest point-in-time/robustez estatística ainda não aprovado"),
        universe_coverage=.9695,
    )

    message = _publication_gate_message(metrics, gate)

    assert "prontidão metodológica 0/381 (0.0%; mínimo 80%)" in message
    assert "confiança ≥75%: 0/381 (0.0%)" in message
    assert "confiança mediana 66.5% (mínimo 75%)" in message
    assert "backtest point-in-time/robustez estatística pendente" in message
