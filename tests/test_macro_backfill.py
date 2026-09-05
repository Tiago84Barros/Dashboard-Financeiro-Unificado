from datetime import date

import pytest

from core.macro_data.backfill import parse_backfill_period


def test_backfill_defaults_to_2010_and_today():
    assert parse_backfill_period(None, None, today=date(2026, 9, 2)) == (
        date(2010, 1, 1),
        date(2026, 9, 2),
    )


def test_backfill_rejects_invalid_or_reversed_periods():
    with pytest.raises(ValueError):
        parse_backfill_period("2012-01-01", "2011-01-01")
    with pytest.raises(ValueError):
        parse_backfill_period("not-a-date", "2020-01-01")
