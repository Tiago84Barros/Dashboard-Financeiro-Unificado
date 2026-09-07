from datetime import datetime, timedelta, timezone

from core.macro_data.schedule import observation_due


def test_new_or_stale_series_is_due_using_utc_intervals():
    now = datetime(2026, 9, 2, 12, tzinfo=timezone.utc)
    assert observation_due(None, "monthly", now=now)
    assert observation_due(now - timedelta(hours=25), "monthly", now=now)
    assert not observation_due(now - timedelta(hours=23), "monthly", now=now)


def test_annual_series_is_not_redownloaded_by_hourly_runner():
    now = datetime(2026, 9, 2, 12, tzinfo=timezone.utc)
    assert not observation_due(now - timedelta(days=6), "annual", now=now)
    assert observation_due(now - timedelta(days=8), "annual", now=now)


def test_unknown_frequency_uses_conservative_daily_interval():
    now = datetime(2026, 9, 2, 12, tzinfo=timezone.utc)
    assert not observation_due(now - timedelta(hours=23), "unmapped", now=now)
    assert observation_due(now - timedelta(hours=24), "unmapped", now=now)
