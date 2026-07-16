"""Testes do provedor FMP (offline): rate-limit, budget, backoff, mascaramento."""
import pytest

import data_pipeline.us.providers as pv


class FakeResp:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self._payload = payload if payload is not None else []

    def json(self):
        return self._payload


class FakeSession:
    """Devolve respostas de uma fila; registra as chamadas."""
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append({"url": url, "params": params})
        r = self._responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


class Clock:
    def __init__(self):
        self.t = 0.0
        self.slept = []

    def time(self):
        return self.t

    def sleep(self, secs):
        self.slept.append(secs)
        self.t += secs


def _provider(session, clock=None, **kw):
    clock = clock or Clock()
    return pv.FmpProvider(api_key="SECRET_KEY", session=session,
                          time_fn=clock.time, sleep_fn=clock.sleep, **kw), clock


# ── credencial / budget ───────────────────────────────────────────────────────
def test_missing_credential_raises():
    prov = pv.FmpProvider(api_key="", session=FakeSession([]))
    with pytest.raises(pv.MissingCredentialError):
        prov.get_profile("AAPL")


def test_budget_exceeded():
    sess = FakeSession([FakeResp(200, [{"symbol": "AAPL"}])])
    prov, _ = _provider(sess, budget=pv.Budget(limit=1))
    prov.get_profile("AAPL")            # gasta 1
    with pytest.raises(pv.BudgetExceededError):
        prov.get_profile("AAPL")        # estoura


def test_budget_remaining():
    b = pv.Budget(limit=3)
    b.charge(); b.charge()
    assert b.remaining() == 1
    assert pv.Budget().remaining() is None  # ilimitado


# ── rate limiter ──────────────────────────────────────────────────────────────
def test_rate_limiter_dorme_quando_esgota():
    clock = Clock()
    rl = pv.RateLimiter(rate=2, per=60.0, time_fn=clock.time, sleep_fn=clock.sleep)
    rl.acquire(); rl.acquire()          # consome os 2 tokens sem dormir
    assert clock.slept == []
    rl.acquire()                        # terceiro: precisa dormir
    assert clock.slept and clock.slept[-1] > 0


# ── backoff / retries ─────────────────────────────────────────────────────────
def test_retry_em_500_depois_sucesso():
    sess = FakeSession([FakeResp(500), FakeResp(200, [{"symbol": "AAPL"}])])
    prov, clock = _provider(sess)
    out = prov.get_profile("AAPL")
    assert out == {"symbol": "AAPL"}
    assert clock.slept                  # houve backoff


def test_429_esgota_e_levanta_ratelimit():
    sess = FakeSession([FakeResp(429), FakeResp(429), FakeResp(429), FakeResp(429)])
    prov, _ = _provider(sess, max_retries=4)
    with pytest.raises(pv.RateLimitError):
        prov.get_income_statements("AAPL")


def test_endpoint_e_params_corretos():
    sess = FakeSession([FakeResp(200, [{"revenue": 1}])])
    prov, _ = _provider(sess)
    prov.get_income_statements("AAPL", "annual", 5)
    call = sess.calls[-1]
    assert "income-statement/AAPL" in call["url"]
    assert call["params"]["period"] == "annual" and call["params"]["limit"] == 5
    assert call["params"]["apikey"] == "SECRET_KEY"


def test_prices_desembrulha_historical():
    sess = FakeSession([FakeResp(200, {"historical": [{"date": "2024-01-02", "close": 1}]})])
    prov, _ = _provider(sess)
    rows = prov.get_prices_daily("AAPL")
    assert rows and rows[0]["close"] == 1


def test_mask_remove_chave():
    assert pv._mask("erro em ?apikey=SECRET_KEY", "SECRET_KEY") == "erro em ?apikey=***"


def test_estimate_calls():
    est = pv.estimate_calls(10)
    assert est["symbols"] == 10
    assert est["estimated_calls"] == 1 + 10 * pv.CALLS_PER_SYMBOL_FULL
