import pytest

from data_pipeline.market.fii_project_reconciliation import values_conflict


def test_project_reconciliation_uses_both_absolute_and_relative_tolerance():
    conflict, absolute, relative = values_conflict(
        100_000_000, 106_000_000,
        absolute_tolerance=1_000_000,
        relative_tolerance=.05,
    )
    assert conflict is True
    assert absolute == pytest.approx(6_000_000)
    assert relative == pytest.approx(6_000_000 / 106_000_000)


def test_project_reconciliation_ignores_small_or_missing_variation():
    assert values_conflict(
        100_000_000, 100_500_000,
        absolute_tolerance=1_000_000,
        relative_tolerance=.05,
    )[0] is False
    assert values_conflict(
        None, 100_500_000,
        absolute_tolerance=1_000_000,
        relative_tolerance=.05,
    ) == (False, None, None)
