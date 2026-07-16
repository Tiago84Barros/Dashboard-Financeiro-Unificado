import pytest

from data_pipeline.market.fii_ingest import (
    _governance_alignment_score,
    _mandate_adherence_score,
)


def test_mandate_adherence_uses_public_asset_composition():
    assert _mandate_adherence_score(
        "tijolo", {"real_estate": .80, "cash": .20}
    ) == 1.0
    assert _mandate_adherence_score(
        "papel", {"cri": .60, "cash": .40}
    ) == pytest.approx(.75)
    assert _mandate_adherence_score(
        "fof", {"fund_holdings": .40, "cash": .60}
    ) is None


def test_hybrid_mandate_requires_two_structural_classes():
    assert _mandate_adherence_score(
        "hibrido", {"real_estate": .50, "cri": .40, "cash": .10}
    ) == 1.0
    assert _mandate_adherence_score(
        "hibrido", {"real_estate": .90, "cash": .10}
    ) == 0.0


def test_governance_alignment_requires_observed_public_coverage():
    score, coverage = _governance_alignment_score({
        "governance_disclosure_quality": .80,
        "auditor_opinion_quality": 1.0,
    })
    assert coverage == .50
    assert score == pytest.approx(.88)

    score, coverage = _governance_alignment_score({
        "governance_disclosure_quality": .80,
    })
    assert score is None
    assert coverage == .30
