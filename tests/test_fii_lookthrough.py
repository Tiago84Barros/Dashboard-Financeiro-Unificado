from core.fii_lookthrough import (
    dimension_is_applicable,
    has_observed_dimension,
    normalized_dimension_mapping,
    summarize_lookthrough_coverage,
    supplementary_evidence_score,
)


def test_lookthrough_counts_only_explicit_named_exposures():
    rows = [
        {
            "ticker": "TIJO11", "tipo": "tijolo", "sector": "Logística",
            "regions": {"SP": .7, "MG": .3},
            "tenant_concentration": .25,
        },
        {
            "ticker": "PAPR11", "tipo": "papel",
            "issuers": {"Emissor A": .6, "Emissor B": .4},
            "indexers": {"IPCA": 1.0},
            "debtor_diversification": .4,
        },
    ]

    result = summarize_lookthrough_coverage(rows)

    assert result["dimensions"]["sector"]["coverage"] == 1
    assert result["dimensions"]["issuer"]["coverage"] == 1
    assert result["dimensions"]["region"]["coverage"] == 1
    assert result["dimensions"]["indexer"]["coverage"] == 1
    assert result["dimensions"]["tenant"]["coverage"] == 0
    assert result["dimensions"]["debtor"]["coverage"] == 0
    assert has_observed_dimension(rows[0], "tenant") is False
    assert has_observed_dimension(rows[1], "debtor") is False


def test_lookthrough_required_dimensions_fail_closed():
    result = summarize_lookthrough_coverage([
        {"ticker": "TIJO11", "tipo": "tijolo"},
        {"ticker": "PAPR11", "tipo": "papel"},
    ])

    assert result["required_ready"] is False
    assert set(result["required_blockers"]) == {"sector", "issuer"}
    assert result["dimensions"]["tenant"]["source_limitation"]


def test_lookthrough_ignores_invalid_and_non_positive_weights():
    row = {
        "ticker": "PAPR11",
        "tipo": "papel",
        "issuers": {"": .5, "Emissor": 0, "Outro": "inválido"},
    }

    assert has_observed_dimension(row, "issuer") is False


def test_regions_are_canonicalized_and_aggregated_by_ibge_macroregion():
    row = {
        "ticker": "TIJO11",
        "tipo": "tijolo",
        "regions": {"SP": .40, "Sudeste": .30, "RJ": .30, "Inválida": .50},
    }

    assert normalized_dimension_mapping(row, "region") == {"Sudeste": 1.0}
    assert has_observed_dimension(row, "region") is True


def test_hybrid_dimension_applicability_follows_material_economic_exposure():
    property_hybrid = {
        "ticker": "PROP11", "tipo": "hibrido",
        "pct_imoveis": .26, "pct_papel": 0.0,
        "regions": {"SP": 1.0},
    }
    credit_hybrid = {
        "ticker": "CRED11", "tipo": "hibrido",
        "pct_imoveis": 0.0, "pct_papel": .66,
        "debtors": {"Devedor": 1.0},
        "indexers": {"IPCA": 1.0},
    }

    assert dimension_is_applicable(property_hybrid, "region") is True
    assert dimension_is_applicable(property_hybrid, "debtor") is False
    assert dimension_is_applicable(property_hybrid, "indexer") is False
    assert dimension_is_applicable(credit_hybrid, "region") is False
    assert dimension_is_applicable(credit_hybrid, "tenant") is False
    assert dimension_is_applicable(credit_hybrid, "debtor") is True
    assert supplementary_evidence_score(property_hybrid) == 1.0
    assert supplementary_evidence_score(credit_hybrid) == 1.0


def test_missing_hybrid_composition_preserves_conservative_applicability():
    row = {"ticker": "HBRD11", "tipo": "hibrido"}

    assert dimension_is_applicable(row, "region") is True
    assert dimension_is_applicable(row, "debtor") is True
