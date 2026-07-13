from core.fii_integrated_model import (INTEGRATED_MODEL_VERSION,
                                       IntegratedEligibilityPolicy,
                                       apply_integrated_eligibility)


def _row(ticker="AAAA11", fii_type="tijolo"):
    return {
        "ticker": ticker, "tipo": fii_type, "dy_12m": .10, "pvp": .92,
        "liquidez_diaria": 2_000_000, "history_months": 48,
        "max_drawdown": -.25, "region_count": 3, "property_count": 12,
        "multi_category": True,
    }


def test_integrated_eligibility_preserves_missing_values_as_exclusions():
    missing = _row("MISS11")
    missing["max_drawdown"] = None
    eligible, report = apply_integrated_eligibility(
        [_row(), missing], IntegratedEligibilityPolicy())

    assert [row["ticker"] for row in eligible] == ["AAAA11"]
    assert eligible[0]["integrated_model_version"] == INTEGRATED_MODEL_VERSION
    assert report["eligible_count"] == 1
    assert report["exclusion_counts"]["drawdown ausente"] == 1


def test_property_filters_apply_only_to_brick_funds():
    paper = _row("PAPR11", "papel")
    paper.update(region_count=None, property_count=None, multi_category=False)
    brick = _row("TIJO11")
    brick.update(region_count=1, property_count=4, multi_category=False)
    policy = IntegratedEligibilityPolicy(
        require_multi_region=True, require_min_properties=True,
        require_multicategory=True,
    )

    eligible, report = apply_integrated_eligibility([paper, brick], policy)

    assert [row["ticker"] for row in eligible] == ["PAPR11"]
    assert report["eligible_count"] == 1
    assert sum(report["exclusion_counts"].values()) == 3


def test_integrated_filters_reject_implausible_income_and_pvp():
    row = _row()
    row.update(dy_12m=.25, pvp=.40)

    eligible, report = apply_integrated_eligibility(
        [row], IntegratedEligibilityPolicy())

    assert not eligible
    assert "DY 12m acima do limite de plausibilidade" in report["exclusion_counts"]
    assert "P/VP fora da faixa de plausibilidade" in report["exclusion_counts"]
