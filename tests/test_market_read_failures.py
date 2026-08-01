import datetime as dt
import time

import pandas as pd
import pytest

from core import database, market_read


@pytest.fixture(autouse=True)
def _disable_workspace_snapshot_artifact(monkeypatch):
    def unavailable():
        frame = pd.DataFrame()
        frame.attrs["load_error"] = "snapshot_artifact_unavailable"
        return frame

    monkeypatch.setattr(market_read, "_load_fii_snapshot_artifact", unavailable)


def test_query_failure_returns_explicit_safe_state(monkeypatch):
    class BrokenEngine:
        def connect(self):
            raise RuntimeError("sensitive connection details")

    monkeypatch.setattr(market_read, "_engine", lambda: BrokenEngine())

    frame = market_read._q("SELECT secret FROM private_table", {"token": "secret"})

    assert isinstance(frame, pd.DataFrame)
    assert frame.empty
    assert frame.attrs["load_error"] == "query_failed"
    assert frame.attrs["error_type"] == "RuntimeError"


def test_snapshot_reader_uses_supavisor_transaction_mode():
    parsed = database._fii_snapshot_connection_url(
        "postgresql://reader:password@aws-0-region.pooler.supabase.com:5432/postgres"
    )

    assert parsed.drivername == "postgresql+psycopg2"
    assert parsed.port == 6543
    assert parsed.username == "reader"


def test_snapshot_reader_expands_compact_metric_provenance():
    payload = {
        "ticker": "ABCD11",
        "metric_metadata": {
            "dy_12m": ["2026-07-29T00:00:00+00:00", 0.95, "2026-06-30", "brapi"],
        },
    }

    expanded = market_read._expand_snapshot_payload(payload)

    assert expanded["metric_metadata"]["dy_12m"] == {
        "available_at": "2026-07-29T00:00:00+00:00",
        "source_quality": 0.95,
        "reference_date": "2026-06-30",
        "source": "brapi",
    }


class _Connection:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class _Engine:
    def connect(self):
        return _Connection()


def _snapshot_frame(size: int = 3, *, age_days: int = 0) -> pd.DataFrame:
    rows = []
    today = dt.datetime.now(dt.timezone.utc).date()
    for index in range(size):
        ticker = f"T{index:03}11"
        payload = {"ticker": ticker, "score": index / 10}
        rows.append({
            "ticker": ticker,
            "payload_json": payload,
            "as_of_date": today - dt.timedelta(days=age_days),
            "available_at": today,
            "knowledge_at": today,
            "reference_date": today,
            "vintage": "test",
            "source": "synthetic",
            "quality_status": "published",
            "schema_version": "fii_selection_inputs.v2",
            "generated_at": pd.Timestamp.now(tz="UTC"),
            "payload_sha256": market_read._snapshot_payload_digest(payload),
            "coverage_json": {"coverage_pct": 100},
        })
    return pd.DataFrame(rows)


def test_fii_snapshot_uses_compact_pages_and_preserves_all_rows(monkeypatch):
    calls = []
    pages = [_snapshot_frame(394)]

    def read_page(statement, connection, params):
        calls.append(dict(params))
        return pages.pop(0)

    market_read._reset_fii_snapshot_memory_cache()
    monkeypatch.setattr(market_read, "_fii_snapshot_engine", lambda: _Engine())
    monkeypatch.setattr(market_read.pd, "read_sql_query", read_page)

    frame = market_read._load_fii_selection_snapshot()

    assert len(frame) == 394
    assert frame.attrs["snapshot_source"] == "database"
    assert frame.attrs["snapshot_read_attempts"] == 1
    assert [call["page_size"] for call in calls] == [500]
    assert calls[0]["after_ticker"] == ""


def test_fii_snapshot_retries_once_then_succeeds(monkeypatch):
    calls = 0

    def read_page(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise TimeoutError("synthetic timeout")
        return _snapshot_frame()

    market_read._reset_fii_snapshot_memory_cache()
    monkeypatch.setattr(market_read, "_fii_snapshot_engine", lambda: _Engine())
    monkeypatch.setattr(market_read.pd, "read_sql_query", read_page)

    frame = market_read._load_fii_selection_snapshot()

    assert len(frame) == 3
    assert calls == 2
    assert frame.attrs["snapshot_read_attempts"] == 2
    assert frame.attrs["snapshot_fallback"] is False


def test_fii_snapshot_uses_only_fresh_verified_memory_fallback(monkeypatch):
    pages = [_snapshot_frame()]
    market_read._reset_fii_snapshot_memory_cache()
    monkeypatch.setattr(market_read, "_fii_snapshot_engine", lambda: _Engine())
    monkeypatch.setattr(
        market_read.pd, "read_sql_query",
        lambda *args, **kwargs: pages.pop(0),
    )
    assert len(market_read._load_fii_selection_snapshot()) == 3

    monkeypatch.setattr(
        market_read.pd, "read_sql_query",
        lambda *args, **kwargs: (_ for _ in ()).throw(TimeoutError("offline")),
    )
    fallback = market_read._load_fii_selection_snapshot()

    assert len(fallback) == 3
    assert fallback.attrs["snapshot_source"] == "last_good_memory"
    assert fallback.attrs["snapshot_fallback"] is True
    assert fallback.attrs["snapshot_read_attempts"] == 2
    assert "load_error" not in fallback.attrs


def test_fii_snapshot_rejects_expired_memory_fallback(monkeypatch):
    market_read._reset_fii_snapshot_memory_cache()
    market_read._remember_fii_snapshot(_snapshot_frame())
    market_read._FII_SNAPSHOT_LAST_GOOD_AT = (
        dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=901)
    )
    monkeypatch.setattr(market_read, "_fii_snapshot_engine", lambda: _Engine())
    monkeypatch.setattr(
        market_read.pd, "read_sql_query",
        lambda *args, **kwargs: (_ for _ in ()).throw(TimeoutError("offline")),
    )

    frame = market_read._load_fii_selection_snapshot()

    assert frame.empty
    assert frame.attrs["load_error"] == "snapshot_query_failed"


def test_fii_snapshot_rejects_stale_or_corrupted_publication(monkeypatch):
    market_read._reset_fii_snapshot_memory_cache()
    stale = _snapshot_frame(age_days=5)
    monkeypatch.setattr(market_read, "_fii_snapshot_engine", lambda: _Engine())
    monkeypatch.setattr(
        market_read.pd, "read_sql_query", lambda *args, **kwargs: stale,
    )
    result = market_read._load_fii_selection_snapshot()
    assert result.attrs["load_error"] == "snapshot_stale"

    corrupted = _snapshot_frame()
    corrupted.loc[0, "payload_sha256"] = "0" * 64
    monkeypatch.setattr(
        market_read.pd, "read_sql_query", lambda *args, **kwargs: corrupted,
    )
    result = market_read._load_fii_selection_snapshot()
    assert result.attrs["load_error"] == "snapshot_hash_invalid"


def test_fii_snapshot_rejects_duplicate_tickers(monkeypatch):
    duplicate = _snapshot_frame()
    duplicate.loc[1, "ticker"] = duplicate.loc[0, "ticker"]
    market_read._reset_fii_snapshot_memory_cache()
    monkeypatch.setattr(market_read, "_fii_snapshot_engine", lambda: _Engine())
    monkeypatch.setattr(
        market_read.pd, "read_sql_query", lambda *args, **kwargs: duplicate,
    )

    result = market_read._load_fii_selection_snapshot()

    assert result.attrs["load_error"] == "snapshot_tickers_invalid"


def test_fii_snapshot_enforces_total_application_deadline(monkeypatch):
    def delayed_page(*args, **kwargs):
        time.sleep(0.05)
        return _snapshot_frame()

    market_read._reset_fii_snapshot_memory_cache()
    monkeypatch.setattr(market_read, "_fii_snapshot_engine", lambda: _Engine())
    monkeypatch.setattr(market_read, "_FII_SNAPSHOT_DEADLINE_SECONDS", 0.01)
    monkeypatch.setattr(market_read.pd, "read_sql_query", delayed_page)

    result = market_read._load_fii_selection_snapshot()

    assert result.empty
    assert result.attrs["load_error"] == "snapshot_deadline_exceeded"
    job = market_read._FII_SNAPSHOT_JOB
    assert job is not None
    job[0].join(timeout=1)
    market_read._reset_fii_snapshot_memory_cache()


def test_fii_snapshot_prefers_verified_artifact_without_waiting_for_database(monkeypatch):
    artifact = _snapshot_frame()
    artifact.attrs["snapshot_source"] = "local_verified_artifact"
    monkeypatch.setattr(
        market_read, "_load_fii_snapshot_artifact", lambda: artifact,
    )
    monkeypatch.setattr(market_read, "_fii_snapshot_engine", lambda: None)

    result = market_read._load_fii_selection_snapshot()

    assert len(result) == 3
    assert result.attrs["snapshot_source"] == "local_verified_artifact"


def test_failed_methodology_input_is_not_retained_in_streamlit_cache(monkeypatch):
    class CachedLoader:
        clear_calls = 0

        def __call__(self, **kwargs):
            frame = pd.DataFrame()
            frame.attrs["load_error"] = "snapshot_query_failed"
            return frame

        def clear(self):
            self.clear_calls += 1

    loader = CachedLoader()
    monkeypatch.setattr(market_read, "_load_fii_methodology_inputs_cached", loader)

    result = market_read.load_fii_methodology_inputs()

    assert result.attrs["load_error"] == "snapshot_query_failed"
    assert loader.clear_calls == 1
