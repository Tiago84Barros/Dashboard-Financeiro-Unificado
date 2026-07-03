from datetime import datetime, timezone

import core.brapi as brapi


def _ts(y, m, d):
    return int(datetime(y, m, d, tzinfo=timezone.utc).timestamp())


# Quote sintético no formato real da brapi (results[0]).
_QUOTE = {
    "symbol": "PETR4",
    "regularMarketPrice": 40.0,
    "priceEarnings": 5.0,
    "earningsPerShare": 8.0,
    "dividendsData": {
        "cashDividends": [
            {"paymentDate": "2024-05-20T03:00:00.000Z", "rate": 2.0, "label": "DIVIDENDO"},
            {"paymentDate": "2024-11-20T03:00:00.000Z", "rate": 1.0, "label": "JCP"},
            {"paymentDate": "2025-06-20T03:00:00.000Z", "rate": 3.0, "label": "DIVIDENDO"},
        ]
    },
    "historicalDataPrice": [
        {"date": _ts(2024, 12, 30), "close": 30.0, "adjustedClose": 30.0},
        {"date": _ts(2025, 12, 30), "close": 40.0, "adjustedClose": 40.0},
    ],
}


def test_parse_cash_dividends():
    divs = brapi.parse_cash_dividends(_QUOTE)
    assert len(divs) == 3
    assert divs[0]["date"].year == 2024 and divs[0]["rate"] == 2.0
    # ordenado por data
    assert divs[0]["date"] <= divs[-1]["date"]


def test_annual_dividends():
    agg = brapi.annual_dividends(_QUOTE)
    assert agg[2024] == 3.0   # 2.0 + 1.0
    assert agg[2025] == 3.0


def test_annual_year_end_prices():
    px = brapi.annual_year_end_prices(_QUOTE)
    assert px[2024] == 30.0 and px[2025] == 40.0


def test_annual_dy():
    dy = brapi.annual_dy(_QUOTE)
    # 2024: 3.0/30.0 = 0.10 ; 2025: 3.0/40.0 = 0.075
    assert abs(dy[2024] - 0.10) < 1e-9
    assert abs(dy[2025] - 0.075) < 1e-9


def test_annual_dy_ignores_out_of_range():
    q = {"dividendsData": {"cashDividends": [
            {"paymentDate": "2025-01-10T03:00:00.000Z", "rate": 50.0}]},
         "historicalDataPrice": [{"date": _ts(2025, 12, 30), "close": 10.0}]}
    # 50/10 = 5.0 (500%) → fora da faixa coerente → ignorado
    assert brapi.annual_dy(q) == {}


def test_current_fundamentals():
    f = brapi.current_fundamentals(_QUOTE)
    assert f["P/L"] == 5.0
    # DY trailing depende de "hoje"; só garantimos que P/L está presente
    assert "P/L" in f


def test_rate_limited_classification():
    assert brapi.is_rate_limited(brapi.BrapiRateLimited("429")) is True
    assert brapi.is_rate_limited(brapi.BrapiError("500")) is False


class _FakeResp:
    def __init__(self, status):
        self.status_code = status

    def json(self):
        return {"results": []}


def test_fetch_quote_401_403_retentavel(monkeypatch):
    """401/403 (throttling do Pro) sobem como BrapiRateLimited p/ o backoff retentar."""
    import pytest
    import requests
    for status in (401, 403, 429):
        monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResp(status))
        with pytest.raises(brapi.BrapiRateLimited):
            brapi.fetch_quote("HGLG11")


def test_fetch_quote_500_retorna_none(monkeypatch):
    """Erros não-retentáveis (ex.: 500) continuam devolvendo None (sem exceção)."""
    import requests
    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResp(500))
    assert brapi.fetch_quote("HGLG11") is None
