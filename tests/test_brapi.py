from datetime import datetime, timezone

import core.brapi as brapi
import pytest


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


def test_dedup_cash_dividends_classe_mista():
    # Caso CEB (Fato Relevante CVM 12/08/2025): PNA=1.665745, PNB=1.832319.
    # No payload da CEBR5 a brapi mescla o valor da PNB via fonte CSV
    # (remarks='csv:payment_date_estimated') com o MESMO assetIssued da PNA.
    items = [
        {"lastDatePrior": "2025-09-09T03:00:00.000Z", "rate": 1.665745,
         "label": "DIVIDENDO", "remarks": "", "paymentDate": "2025-09-17T03:00:00.000Z"},
        {"lastDatePrior": "2025-09-09T03:00:00.000Z", "rate": 1.832319,
         "label": "DIVIDENDO", "remarks": "csv:payment_date_estimated",
         "paymentDate": "2025-09-09T03:00:00.000Z"},
        # órfã da CSV (sem par confirmado na mesma data-ex/label) → permanece
        {"lastDatePrior": "2020-05-05T03:00:00.000Z", "rate": 0.5,
         "label": "JCP", "remarks": "csv:payment_date_estimated",
         "paymentDate": "2020-05-05T03:00:00.000Z"},
        # duas parcelas CONFIRMADAS na mesma data-ex/label → ambas ficam
        {"lastDatePrior": "2024-04-25T03:00:00.000Z", "rate": 1.0,
         "label": "DIVIDENDO", "remarks": "", "paymentDate": "2024-05-20T03:00:00.000Z"},
        {"lastDatePrior": "2024-04-25T03:00:00.000Z", "rate": 0.7,
         "label": "DIVIDENDO", "remarks": "", "paymentDate": "2024-06-20T03:00:00.000Z"},
    ]
    kept = brapi.dedup_cash_dividends(items)
    rates = sorted(d["rate"] for d in kept)
    assert rates == [0.5, 0.7, 1.0, 1.665745]  # 1.832319 (eco da PNB) caiu


def test_dedup_cash_dividends_eco_cross_date():
    # Caso AXIA5 (payload real 2026-07): confirmada ex=2025-08-05 rate=2.43036
    # e DOIS ecos CSV com data-ex deslocada p/ 2025-08-15 — a cópia do próprio
    # evento (regra B) e o valor da AXIA6 no mesmo slot (regra C).
    items = [
        {"lastDatePrior": "2025-08-05T03:00:00.000Z", "rate": 2.43036,
         "label": "DIVIDENDO", "remarks": "", "paymentDate": "2025-08-28T03:00:00.000Z"},
        {"lastDatePrior": "2025-08-15T03:00:00.000Z", "rate": 2.4303634,
         "label": "DIVIDENDO", "remarks": "csv:payment_date_estimated",
         "paymentDate": "2025-08-15T03:00:00.000Z"},
        {"lastDatePrior": "2025-08-15T03:00:00.000Z", "rate": 1.9334791,
         "label": "DIVIDENDO", "remarks": "csv:payment_date_estimated",
         "paymentDate": "2025-08-15T03:00:00.000Z"},
        # órfã CSV fora da janela de 15 dias → permanece
        {"lastDatePrior": "2025-04-29T03:00:00.000Z", "rate": 0.1110415,
         "label": "DIVIDENDO", "remarks": "csv:payment_date_estimated",
         "paymentDate": "2025-04-29T03:00:00.000Z"},
    ]
    kept = brapi.dedup_cash_dividends(items)
    assert sorted(d["rate"] for d in kept) == [0.1110415, 2.43036]


def test_dedup_cash_dividends_nao_remove_legitimos():
    # confirmadas próximas com rates distintos (parcelas) ficam TODAS; CSV com
    # rate diferente de qualquer confirmada na janela (sem cluster B) também
    # fica — não há âncora provando que é eco.
    items = [
        {"lastDatePrior": "2025-03-10T03:00:00.000Z", "rate": 1.0,
         "label": "JCP", "remarks": "", "paymentDate": "2025-03-20T03:00:00.000Z"},
        {"lastDatePrior": "2025-03-18T03:00:00.000Z", "rate": 0.98,
         "label": "JCP", "remarks": "", "paymentDate": "2025-03-28T03:00:00.000Z"},
        {"lastDatePrior": "2025-03-14T03:00:00.000Z", "rate": 0.5,
         "label": "JCP", "remarks": "csv:payment_date_estimated",
         "paymentDate": "2025-03-14T03:00:00.000Z"},
        # label distinto não ancora: DIVIDENDO ~igual a JCP confirmado fica
        {"lastDatePrior": "2025-03-12T03:00:00.000Z", "rate": 1.0000005,
         "label": "DIVIDENDO", "remarks": "csv:payment_date_estimated",
         "paymentDate": "2025-03-12T03:00:00.000Z"},
    ]
    kept = brapi.dedup_cash_dividends(items)
    assert sorted(d["rate"] for d in kept) == [0.5, 0.98, 1.0, 1.0000005]


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


def test_fetch_fii_v2_validates_endpoint_and_batch(monkeypatch):
    class Response:
        status_code = 200

        @staticmethod
        def json():
            return {"fiis": [{"symbol": "KNRI11"}]}

    monkeypatch.setattr("requests.get", lambda *args, **kwargs: Response())
    result = brapi.fetch_fii_v2("indicators", ["knri11"])
    assert result["fiis"][0]["symbol"] == "KNRI11"
    with pytest.raises(ValueError):
        brapi.fetch_fii_v2("unknown", ["KNRI11"])
    with pytest.raises(ValueError):
        brapi.fetch_fii_v2("indicators", [f"F{i}11" for i in range(21)])


def test_fii_v2_invalid_token_is_not_treated_as_rate_limit(monkeypatch):
    class Response:
        status_code = 401
        headers = {}

        @staticmethod
        def json():
            return {"error": True, "code": "INVALID_TOKEN"}

    monkeypatch.setattr("requests.get", lambda *args, **kwargs: Response())
    with pytest.raises(brapi.BrapiAuthError):
        brapi.fetch_fii_v2("list", None)


def test_fii_v2_pagination_merges_all_rows(monkeypatch):
    class Response:
        headers = {}
        status_code = 200

        def __init__(self, page):
            self.page = page

        def json(self):
            return {"reports": [{"symbol": f"F{self.page}"}],
                    "pagination": {"page": self.page, "totalPages": 2,
                                   "hasNextPage": self.page < 2}}

    monkeypatch.setattr(
        "requests.get",
        lambda *args, **kwargs: Response(int((kwargs.get("params") or {}).get("page", 1))))
    response = brapi.fetch_fii_v2_all_pages("reports", "KNRI11")
    assert [row["symbol"] for row in response.payload["reports"]] == ["F1", "F2"]
