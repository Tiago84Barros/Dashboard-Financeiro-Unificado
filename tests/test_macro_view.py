from datetime import date, datetime, timezone

from views.macro_internacional import _display_temporal


def test_display_temporal_normalizes_dates_and_datetimes_to_text():
    assert _display_temporal(date(2026, 9, 3)) == "2026-09-03"
    assert _display_temporal(
        datetime(2026, 9, 3, 12, 30, tzinfo=timezone.utc)
    ) == "2026-09-03T12:30:00+00:00"


def test_display_temporal_keeps_empty_state_explicit():
    assert _display_temporal(None) == "—"
    assert _display_temporal("") == "—"
