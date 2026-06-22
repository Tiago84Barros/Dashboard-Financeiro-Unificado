from datetime import date

import data_pipeline.market.repository as repo


def test_dedup_dividends_by_event_date():
    rows = [
        {"ticker": "BBDC3", "payment_date": date(2025, 3, 10), "ex_date": None,
         "amount": 0.5, "type": "JCP"},
        {"ticker": "BBDC3", "payment_date": date(2025, 3, 10), "ex_date": None,
         "amount": 0.5, "type": "JCP"},  # duplicata exata
        {"ticker": "BBDC3", "payment_date": date(2025, 6, 10), "ex_date": None,
         "amount": 0.7, "type": "DIVIDENDO"},
    ]
    out = repo._dedup("dividends", rows)
    assert len(out) == 2  # duplicata removida


def test_dedup_dividends_rounds_amount_to_db_scale():
    # dois amounts que arredondam ao mesmo NUMERIC(18,6) → mesma chave (caso BBDC3)
    rows = [
        {"ticker": "BBDC3", "payment_date": date(2025, 3, 10), "ex_date": None,
         "amount": 0.1234561, "type": "JCP"},
        {"ticker": "BBDC3", "payment_date": date(2025, 3, 10), "ex_date": None,
         "amount": 0.1234562, "type": "JCP"},
    ]
    assert len(repo._dedup("dividends", rows)) == 1


def test_dedup_statements_by_period():
    rows = [
        {"ticker": "X", "period": "annual", "year": 2025, "quarter": 0, "revenue": 1},
        {"ticker": "X", "period": "annual", "year": 2025, "quarter": 0, "revenue": 2},  # dup → último vence
        {"ticker": "X", "period": "quarterly", "year": 2025, "quarter": 3, "revenue": 3},
    ]
    out = repo._dedup("income_statements", rows)
    assert len(out) == 2
    annual = [r for r in out if r["period"] == "annual"][0]
    assert annual["revenue"] == 2  # último vence


def test_dedup_noop_for_unknown_table():
    rows = [{"a": 1}, {"a": 1}]
    assert repo._dedup("brapi_raw_payloads", rows) == rows
