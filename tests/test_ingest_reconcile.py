"""Reconciliação de ticker na ingestão: grava sob o ticker-B3 requisitado,
não o símbolo divergente que a brapi possa devolver."""
from data_pipeline.market import ingest as ig


def _data(sym):
    return {
        "companies": [{"codigo_cvm": 2437}],  # sem 'ticker' → intocado
        "assets": [{"ticker": sym, "asset_type": "stock"}],
        "historical_prices": [{"ticker": sym, "date": "2025-01-01", "close": 10}],
        "income_statements": [{"ticker": sym, "year": 2024}],
        "balance_sheets": [{"ticker": sym, "year": 2024}],
        "cash_flow_statements": [{"ticker": sym, "year": 2024}],
        "dividends": [{"ticker": sym, "amount": 1.0}],
        "calculated_metrics": [{"ticker": sym, "metric_name": "ROE"}],
    }


def test_reconcile_forca_ticker_b3_e_detecta_divergente():
    data = _data("AXIA3")
    sym = ig._reconcile_ticker(data, "ELET3")
    assert sym == "AXIA3"
    for k in ("assets", "historical_prices", "income_statements",
              "balance_sheets", "cash_flow_statements", "dividends",
              "calculated_metrics"):
        assert all(r["ticker"] == "ELET3" for r in data[k]), k
    assert data["companies"][0]["codigo_cvm"] == 2437  # company intocada


def test_reconcile_sem_divergencia_retorna_none():
    data = _data("BBAS3")
    assert ig._reconcile_ticker(data, "BBAS3") is None
    assert data["assets"][0]["ticker"] == "BBAS3"


def test_reconcile_ignora_sufixo_sa():
    data = _data("AXIA3.SA")
    assert ig._reconcile_ticker(data, "ELET3") == "AXIA3"
    assert data["historical_prices"][0]["ticker"] == "ELET3"
