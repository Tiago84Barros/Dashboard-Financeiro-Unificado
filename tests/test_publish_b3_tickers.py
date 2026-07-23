from scripts import publish_b3_tickers_from_local as mod


def test_normalizes_and_requires_tickers(monkeypatch):
    try:
        mod.publish([])
    except ValueError as exc:
        assert "ticker" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("lista vazia deveria falhar")


def test_excluded_columns_protect_identity_and_raw_lineage():
    assert {"id", "created_at", "updated_at", "raw_payload_id"} <= mod.EXCLUDED_COLUMNS
    assert "calculated_metrics" in mod.TABLES


def test_column_query_excludes_generated_columns():
    class Result:
        def __iter__(self):
            return iter([("ticker", "NEVER"), ("event_date", "ALWAYS"), ("id", "NEVER")])

    class Connection:
        def execute(self, *_args, **_kwargs):
            return Result()

    assert mod._columns(Connection(), "dividends") == ["ticker"]
