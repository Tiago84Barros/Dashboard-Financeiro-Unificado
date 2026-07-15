import datetime as dt

from data_pipeline.market.fii_ingest import _latest_fii_payloads


class _Result:
    def __init__(self, *, scalar=None, rows=None):
        self._scalar = scalar
        self._rows = rows or []

    def scalar(self):
        return self._scalar

    def fetchall(self):
        return self._rows


class _BatchConnection:
    def __init__(self):
        self.calls = []
        self.batch_start = dt.datetime(2026, 7, 14, tzinfo=dt.timezone.utc)

    def execute(self, statement, params=None):
        self.calls.append((str(statement), params))
        if len(self.calls) == 1:
            return _Result(scalar=self.batch_start)
        return _Result(rows=[
            ("FIIA11", {"results": [{"symbol": "FIIA11"}]}),
            ("ETFA11", {"results": [{"symbol": "ETFA11"}]}),
            ("FIIA11", {"results": [{"symbol": "FIIA11", "stale": True}]}),
        ])


def test_latest_fii_payloads_uses_latest_batch_and_keeps_all_candidates():
    conn = _BatchConnection()

    rows = _latest_fii_payloads(conn)

    assert [ticker for ticker, _ in rows] == ["FIIA11", "ETFA11"]
    assert conn.calls[1][1] == {"batch_start": conn.batch_start}
    assert "endpoint='quote_fii_full'" in conn.calls[1][0]
