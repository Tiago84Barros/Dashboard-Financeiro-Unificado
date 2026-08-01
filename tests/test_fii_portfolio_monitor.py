from datetime import date

import pytest

from core.fii_portfolio_monitor import build_fii_portfolio_monitor

STRATEGY = "fii_integrated_robust_optimizer.v6.4"
METHODOLOGY = "6.4.0"


def _validation(strategy: str = STRATEGY) -> dict:
    return {
        "status": "passed",
        "metrics": {"strategy_id": strategy, "backtest": {"periods": 65}},
    }


def _items(left: float = .6) -> list[dict]:
    return [
        {"ticker": "AAAA11", "weight": left},
        {"ticker": "BBBB11", "weight": 1 - left},
    ]


def _saved(items: list[dict] | None = None) -> dict:
    return {
        "items": items or _items(),
        "params_json": {
            "methodology_version": METHODOLOGY,
            "strategy_id": STRATEGY,
        },
    }


def _coverage() -> dict:
    return {
        "sector": {"coverage": 1.0},
        "issuer": {"coverage": 1.0},
        "tenant": {"coverage": .85},
        "debtor": {"coverage": .80},
        "indexer": {"coverage": .95},
        "region": {"coverage": .90},
    }


def test_monitor_is_ok_when_versions_freshness_coverage_and_drift_align():
    result = build_fii_portfolio_monitor(
        current_items=_items(),
        saved_model=_saved(),
        snapshot_as_of="2026-07-28",
        validation=_validation(),
        expected_strategy_id=STRATEGY,
        expected_methodology_version=METHODOLOGY,
        gate_can_publish=True,
        dimension_coverage=_coverage(),
        now=date(2026, 7, 29),
    )

    assert result["status"] == "ok"
    assert result["metrics"]["snapshot_age_days"] == 1
    assert result["metrics"]["turnover"] == pytest.approx(0)


def test_monitor_blocks_stale_snapshot_wrong_validation_and_required_coverage():
    coverage = _coverage()
    coverage["issuer"] = {"coverage": .50}
    result = build_fii_portfolio_monitor(
        current_items=_items(),
        saved_model=_saved(),
        snapshot_as_of="2026-05-01",
        validation=_validation("legacy.strategy"),
        expected_strategy_id=STRATEGY,
        expected_methodology_version=METHODOLOGY,
        gate_can_publish=True,
        dimension_coverage=coverage,
        now=date(2026, 7, 29),
    )

    blocked = {
        item["code"] for item in result["checks"] if item["status"] == "blocked"
    }
    assert result["status"] == "blocked"
    assert {"snapshot_freshness", "validation_strategy", "coverage_issuer"} <= blocked


def test_monitor_keeps_unobserved_supplementary_dimensions_as_warning():
    coverage = _coverage()
    coverage.pop("tenant")
    result = build_fii_portfolio_monitor(
        current_items=_items(),
        saved_model=_saved(),
        snapshot_as_of="2026-07-28",
        validation=_validation(),
        expected_strategy_id=STRATEGY,
        expected_methodology_version=METHODOLOGY,
        gate_can_publish=True,
        dimension_coverage=coverage,
        now=date(2026, 7, 29),
    )

    tenant = next(
        item for item in result["checks"] if item["code"] == "coverage_tenant"
    )
    assert result["status"] == "warning"
    assert tenant["status"] == "warning"
    assert tenant["details"]["coverage"] is None


def test_monitor_flags_material_drift_for_human_review():
    result = build_fii_portfolio_monitor(
        current_items=_items(.30),
        saved_model=_saved(_items(.60)),
        snapshot_as_of="2026-07-28",
        validation=_validation(),
        expected_strategy_id=STRATEGY,
        expected_methodology_version=METHODOLOGY,
        gate_can_publish=True,
        dimension_coverage=_coverage(),
        now=date(2026, 7, 29),
    )

    drift = next(
        item for item in result["checks"] if item["code"] == "portfolio_drift"
    )
    assert result["status"] == "warning"
    assert drift["details"]["turnover"] == pytest.approx(.30)
    assert drift["details"]["max_asset_weight_drift"] == pytest.approx(.30)
