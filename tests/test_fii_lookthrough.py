from core.fii_lookthrough import (
    has_observed_dimension,
    summarize_lookthrough_coverage,
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
