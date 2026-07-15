from datetime import datetime, timezone

import pandas as pd
import pytest

from scripts.publish_fii_selection_snapshot import build_rows


def test_build_rows_is_deterministic_and_auditable():
    now = datetime(2026, 7, 15, 12, tzinfo=timezone.utc)
    frame = pd.DataFrame([{
        "ticker": "ABCD11.SA",
        "tipo": "tijolo",
        "dy_12m": 0.10,
        "pvp": 0.90,
        "liquidez_diaria": 1_000_000,
        "history_months": 36,
        "max_drawdown": -0.20,
        "vacancia_fisica": 0.05,
        "property_count": 8,
        "region_count": 2,
        "updated_at": pd.Timestamp("2026-07-14", tz="UTC"),
    }])

    rows = build_rows(frame, now=now)

    assert len(rows) == 1
    assert rows[0]["ticker"] == "ABCD11"
    assert rows[0]["as_of_date"] == now.date()
    assert len(rows[0]["payload_sha256"]) == 64
    assert '"ticker":"ABCD11"' in rows[0]["payload_json"]
    assert '"coverage_pct": 100.0' in rows[0]["coverage_json"]


def test_build_rows_rejects_duplicate_tickers():
    frame = pd.DataFrame([{"ticker": "ABCD11"}, {"ticker": "ABCD11.SA"}])

    with pytest.raises(ValueError, match="tickers duplicados"):
        build_rows(frame)


def test_build_rows_rejects_empty_warehouse():
    with pytest.raises(ValueError, match="zero inputs"):
        build_rows(pd.DataFrame())
