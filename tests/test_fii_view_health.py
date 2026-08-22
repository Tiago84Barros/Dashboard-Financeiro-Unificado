from types import SimpleNamespace

import pandas as pd

from views.fiis import (
    _fii_data_health_metrics,
    _methodology_inputs_to_vitrine,
    _publication_gate_message,
    _selection_status_copy,
)


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


def test_snapshot_is_reused_as_visual_vitrine_without_second_database_shape():
    inputs = pd.DataFrame([{
        "ticker": "ABCD11", "name": "FII Sintético", "tipo": "tijolo",
        "sector": "Logística", "price": 100.0, "pvp": .9, "dy_12m": .1,
        "liquidez_diaria": 2_000_000,
    }])

    frame = _methodology_inputs_to_vitrine(inputs)

    assert frame.loc[0, "Ticker"] == "ABCD11"
    assert frame.loc[0, "Preço"] == 100.0
    assert frame.loc[0, "DY_12m"] == .1


def test_selection_footer_reflects_gate_state():
    pending = _selection_status_copy(validation_applicable=False, can_publish=True)
    approved = _selection_status_copy(validation_applicable=True, can_publish=True)

    assert "universo bruto de FIIs disponíveis" in pending["footer"]
    assert "atende ao gate vigente" in approved["footer"]
