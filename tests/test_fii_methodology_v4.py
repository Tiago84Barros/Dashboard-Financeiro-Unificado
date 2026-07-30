from datetime import date

from core.fii_methodology import (
    FORMULA_VERSION,
    METHODOLOGY_VERSION,
    MacroScenario,
    evaluate_publication_gate,
    score_fiis_by_type,
    tactical_type_bands,
)


def _full_tijolo(ticker: str, multiplier: float = 1.0) -> dict:
    return {
        "ticker": ticker, "tipo": "tijolo", "dy_12m": .09 * multiplier,
        "income_growth_per_share_3y": .04, "income_recurrence": .95, "pvp": .95,
        "liquidez_diaria": 3_000_000, "issuance_discipline": .9,
        "issuance_price_discipline": .9, "management_efficiency": .85, "fee_efficiency": .8,
        "conflict_alignment": .9, "mandate_adherence": .95,
        "cvm_event_quality": .9, "related_party_exposure": .02,
        "vacancia_fisica": .05, "vacancia_financeira": .04,
        "wault_anos": 5, "tenant_concentration": .12,
        "geographic_diversification": .8, "implied_cap_rate": .09, "asset_quality": .9,
        "contract_quality": .85, "lease_expiry_concentration_24m": .15, "leverage": .05,
        "history_months": 60, "data_consistency": 1, "updated_at": "2026-07-01",
        "metric_metadata": {},
    }


def test_scores_are_versioned_and_missing_never_becomes_neutral():
    complete = _full_tijolo("AAAA11", 1.1)
    incomplete = _full_tijolo("BBBB11")
    incomplete.pop("wault_anos")
    rows = score_fiis_by_type([complete, incomplete], as_of=date(2026, 7, 12),
                              validation_status="passed")
    by_ticker = {row["ticker"]: row for row in rows}
    assert by_ticker["AAAA11"]["methodology_version"] == METHODOLOGY_VERSION
    assert by_ticker["AAAA11"]["formula_version"] == FORMULA_VERSION
    assert "wault_anos" in by_ticker["BBBB11"]["missing_critical"]
    assert by_ticker["BBBB11"]["coverage"] < by_ticker["AAAA11"]["coverage"]
    assert by_ticker["BBBB11"]["critical_coverage"] < by_ticker["AAAA11"]["critical_coverage"]
    assert by_ticker["BBBB11"]["data_readiness_status"] == "ready"


def test_publication_gate_blocks_unvalidated_methodology():
    rows = score_fiis_by_type([_full_tijolo("AAAA11")], as_of=date(2026, 7, 12),
                              validation_status="unvalidated")
    gate = evaluate_publication_gate(rows, expected_universe=1, validation_status="unvalidated")
    assert not gate.can_publish_recommendation
    assert any("point-in-time" in reason for reason in gate.reasons)


def test_data_readiness_is_independent_from_global_pit_validation():
    row = _full_tijolo("AAAA11")
    row.update({"governance_disclosure_quality": 1, "governance_integrity": 1,
                "auditor_opinion_quality": 1})
    rows = score_fiis_by_type([row], as_of=date(2026, 7, 12),
                              validation_status="unvalidated")
    assert rows[0]["data_readiness_status"] == "ready"
    assert rows[0]["publication_status"] == "diligence_only"
    gate = evaluate_publication_gate(rows, expected_universe=1,
                                     validation_status="unvalidated")
    assert gate.validated_fraction == 1
    assert any("point-in-time" in reason for reason in gate.reasons)


def test_tactical_bands_change_with_selic_cycle():
    high = tactical_type_bands(MacroScenario(selic=15, ipca=4, selic_change_12m=1))
    easing = tactical_type_bands(MacroScenario(selic=10, ipca=4, selic_change_12m=-3))
    assert high["papel"][0] > easing["papel"][0]
    assert easing["tijolo"][0] > high["tijolo"][0]


def test_baseline_sensitivity_shocks_do_not_force_stress_regime():
    scenario = MacroScenario(
        selic=15, ipca=4, selic_change_12m=1,
        vacancy_shock=.08, credit_event_rate=.03,
    )

    assert tactical_type_bands(scenario)["papel"][0] == .30


def test_stale_snapshot_blocks_publication_even_with_passed_validation():
    rows = score_fiis_by_type([_full_tijolo("AAAA11")], as_of=date(2026, 7, 12),
                              validation_status="passed")
    gate = evaluate_publication_gate(
        rows, expected_universe=1, validation_status="passed",
        snapshot_as_of=date(2026, 7, 1), max_snapshot_age_days=4,
        as_of=date(2026, 7, 12),
    )

    assert not gate.can_publish_recommendation
    assert any("snapshot com 11 dias" in reason for reason in gate.reasons)


def test_missing_confidence_inputs_are_penalized_and_explicit_zero_is_preserved():
    missing = _full_tijolo("AAAA11")
    missing.pop("data_consistency")
    explicit_zero = _full_tijolo("BBBB11")
    explicit_zero["data_consistency"] = 0.0
    rows = score_fiis_by_type([missing, explicit_zero], as_of=date(2026, 7, 12),
                              validation_status="passed")
    by_ticker = {row["ticker"]: row for row in rows}

    assert "data_consistency_missing" in by_ticker["AAAA11"]["confidence_assumptions"]
    assert by_ticker["BBBB11"]["confidence"] < by_ticker["AAAA11"]["confidence"]


def test_compact_snapshot_metadata_matches_nominal_provenance():
    nominal = _full_tijolo("AAAA11")
    nominal["metric_metadata"] = {
        "dy_12m": {
            "available_at": "2026-07-01",
            "source_quality": .95,
            "reference_date": "2026-06-30",
            "source": "cvm_informe_mensal",
        },
    }
    compact = _full_tijolo("AAAA11")
    compact["metric_metadata"] = {
        "dy_12m": [
            "2026-07-01", .95, "2026-06-30", "cvm_informe_mensal",
        ],
    }

    nominal_score = score_fiis_by_type(
        [nominal], as_of=date(2026, 7, 12), validation_status="passed",
    )[0]
    compact_score = score_fiis_by_type(
        [compact], as_of=date(2026, 7, 12), validation_status="passed",
    )[0]

    assert compact_score["freshness_score"] == nominal_score["freshness_score"]
    assert compact_score["source_quality"] == nominal_score["source_quality"]
    assert compact_score["confidence"] == nominal_score["confidence"]


def test_malformed_metric_metadata_is_missing_instead_of_crashing():
    row = _full_tijolo("AAAA11")
    row["metric_metadata"] = {
        "dy_12m": "invalid",
        "pvp": [],
        "liquidez_diaria": None,
    }

    scored = score_fiis_by_type(
        [row], as_of=date(2026, 7, 12), validation_status="passed",
    )

    assert len(scored) == 1
    assert scored[0]["ticker"] == "AAAA11"
    assert 0 <= scored[0]["source_quality"] <= 1
