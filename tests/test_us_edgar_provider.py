"""Testes do EdgarProvider e do YFinanceProvider (offline, sessão fake)."""
import pytest

import data_pipeline.us.edgar as ed
import data_pipeline.us.prices_yf as pyf
from data_pipeline.us.providers import MissingCredentialError


class FakeResp:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self._payload = payload

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, routes: dict):
        self.routes = routes          # substring da URL → payload | status
        self.calls = []

    def get(self, url, headers=None, timeout=None):
        self.calls.append({"url": url, "headers": headers})
        for frag, resp in self.routes.items():
            if frag in url:
                return resp if isinstance(resp, FakeResp) else FakeResp(200, resp)
        return FakeResp(404, None)


def _provider(routes, ua="Tiago Barros teste@example.com"):
    return ed.EdgarProvider(user_agent=ua, session=FakeSession(routes),
                            time_fn=lambda: 0.0, sleep_fn=lambda s: None)


def test_exige_user_agent():
    prov = _provider({}, ua="")
    with pytest.raises(MissingCredentialError):
        prov.get_universe(["NYSE"])


def test_user_agent_enviado_no_header():
    routes = {"company_tickers_exchange": {"fields": ["cik", "name", "ticker", "exchange"],
                                           "data": []}}
    prov = _provider(routes)
    prov.get_universe([])
    sess = prov.session
    assert sess.calls[0]["headers"]["User-Agent"] == "Tiago Barros teste@example.com"


def test_universe_filtra_por_bolsa():
    routes = {"company_tickers_exchange": {
        "fields": ["cik", "name", "ticker", "exchange"],
        "data": [[320193, "Apple Inc.", "AAPL", "Nasdaq"],
                 [1318605, "Tesla, Inc.", "TSLA", "Nasdaq"],
                 [70858, "Bank of America", "BAC", "NYSE"],
                 [999999, "OTC Co", "OTCX", ""]]}}
    prov = _provider(routes)
    nyse = prov.get_universe(["NYSE"])
    assert [r["symbol"] for r in nyse] == ["BAC"]
    assert nyse[0]["cik"] == "0000070858"
    todos = prov.get_universe([])           # sem filtro → todos, OTC sem bolsa incluso
    assert len(todos) == 4


def test_profile_via_submissions():
    routes = {
        "company_tickers.json": {"0": {"cik_str": 320193, "ticker": "AAPL",
                                       "title": "Apple Inc."}},
        "submissions/CIK0000320193": {
            "name": "Apple Inc.", "sic": "3571",
            "sicDescription": "Electronic Computers",
            "exchanges": ["Nasdaq"], "addresses": {"business": {"stateOrCountry": "CA"}},
        },
    }
    prov = _provider(routes)
    p = prov.get_profile("aapl")
    assert p["cik"] == "0000320193"
    assert p["symbol"] == "AAPL"
    assert p["exchangeShortName"] == "NASDAQ"
    assert p["industry"] == "Electronic Computers"


def test_statements_via_companyfacts_e_404():
    facts = {"cik": 320193, "facts": {"us-gaap": {
        "Revenues": {"units": {"USD": [
            {"end": "2023-12-31", "start": "2023-01-01", "val": 1200,
             "filed": "2024-02-14", "form": "10-K"}]}}}}}
    routes = {
        "company_tickers.json": {"0": {"cik_str": 320193, "ticker": "AAPL",
                                       "title": "Apple Inc."}},
        "companyfacts/CIK0000320193": facts,
    }
    prov = _provider(routes)
    rows = prov.get_income_statements("AAPL")
    assert rows and rows[0]["revenue"] == 1200 and rows[0]["symbol"] == "AAPL"
    # trimestral ainda não suportado → vazio explícito, não inventa
    assert prov.get_income_statements("AAPL", period="quarterly") == []
    # símbolo fora do mapa → sem dados
    assert prov.get_income_statements("ZZZZ") == []
    # key metrics não existem na SEC (projeto calcula) → vazio
    assert prov.get_key_metrics("AAPL") == []


# ── yfinance (factory fake, sem rede) ─────────────────────────────────────────
class FakeYFTicker:
    def __init__(self, df):
        self._df = df

    def history(self, **kwargs):
        return self._df


def _yf_df():
    import pandas as pd
    idx = pd.to_datetime(["2024-01-02", "2024-01-03"])
    return pd.DataFrame({
        "Open": [10.0, 11.0], "High": [11.0, 12.0], "Low": [9.5, 10.5],
        "Close": [10.5, 11.5], "Adj Close": [10.4, 11.4],
        "Volume": [1000, 1100], "Dividends": [0.0, 0.25],
        "Stock Splits": [0.0, 4.0],
    }, index=idx)


def test_yfinance_precos_dividendos_splits():
    calls = {"n": 0}

    def factory(s):
        calls["n"] += 1
        return FakeYFTicker(_yf_df())

    prov = pyf.YFinanceProvider(ticker_factory=factory, sleep_fn=lambda s: None)
    prices = prov.get_prices_daily("AAPL")
    assert len(prices) == 2 and prices[0]["adjClose"] == 10.4
    divs = prov.get_dividends("AAPL")
    assert len(divs) == 1 and divs[0]["dividend"] == 0.25   # dia 0.0 não vira registro
    splits = prov.get_splits("AAPL")
    assert len(splits) == 1 and splits[0]["numerator"] == 4.0
    assert calls["n"] == 1        # UM download por ticker, reaproveitado (não 3)


def test_yfinance_vazio_com_retry_sem_sleep_real():
    import pandas as pd
    slept = []
    prov = pyf.YFinanceProvider(ticker_factory=lambda s: FakeYFTicker(pd.DataFrame()),
                                retries=3, sleep_fn=slept.append)
    assert prov.get_prices_daily("AAPL") == []
    assert prov.get_dividends("AAPL") == []
    assert len(slept) == 2        # 3 tentativas → 2 esperas (backoff), sem dormir de verdade


# ── composição na ingestão ────────────────────────────────────────────────────
def test_composite_provider_delegacao():
    from data_pipeline.us.ingest import CompositeProvider

    class F:
        calls_made = 3
        def get_income_statements(self, s, period="annual", limit=20):
            return [{"revenue": 1}]
        def get_profile(self, s):
            return {"symbol": s}
    class M:
        calls_made = 2
        def get_prices_daily(self, s, start=None, end=None):
            return [{"date": "2024-01-02"}]

    c = CompositeProvider(F(), M())
    assert c.pre_normalized is True
    assert c.get_income_statements("AAPL")[0]["revenue"] == 1
    assert c.get_prices_daily("AAPL")[0]["date"] == "2024-01-02"
    assert c.calls_made == 5
