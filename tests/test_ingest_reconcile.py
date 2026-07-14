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


# ── renormalize: o backfill sem rede também reconcilia ───────────────────────
# Sem isto, um renormalize regravava os assets órfãos que o ingest evita
# (grava sob o símbolo divergente da brapi em vez do ticker-B3 requisitado).

class _Result:
    def __init__(self, rows=None):
        self._rows = rows or []

    def fetchall(self):
        return self._rows

    def scalar(self):
        return None


class _FakeConn:
    """Conn/transação fake: só serve o SELECT dos payloads e registra os
    demais execute() (para inspecionar o INSERT em market.ticker_alias)."""
    def __init__(self, payload_rows):
        self._payload_rows = payload_rows
        self.executed: list[tuple[str, dict]] = []

    def execute(self, clause, params=None):
        sql = str(clause)
        self.executed.append((sql, params or {}))
        if "brapi_raw_payloads" in sql:
            return _Result(self._payload_rows)
        return _Result([])

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeEngine:
    def __init__(self, conn):
        self._conn = conn

    def connect(self):
        return self._conn

    def begin(self):
        return self._conn


def _full_data(sym):
    d = _data(sym)
    d["companies"] = [{"codigo_cvm": None, "name": "X"}]
    return d


def _patch_renormalize(monkeypatch, payload_rows, data):
    conn = _FakeConn(payload_rows)
    upserts: dict[str, list] = {}

    monkeypatch.setattr(ig, "_engine", lambda: _FakeEngine(conn))
    monkeypatch.setattr(ig.repo, "reset_db_cols_cache", lambda: None)
    monkeypatch.setattr(ig.repo, "schema_exists", lambda c: True)
    monkeypatch.setattr(ig.repo, "load_cvm_to_ticker", lambda c: {"ELET3": 2437})
    monkeypatch.setattr(ig.repo, "company_id_by_codigo", lambda c, cod: 1)
    monkeypatch.setattr(ig.repo, "append_metric_vintages", lambda c, rows: None)

    def _upsert(c, table, rows):
        upserts.setdefault(table, []).extend(rows or [])
        return len(rows or [])
    monkeypatch.setattr(ig.repo, "upsert", _upsert)
    monkeypatch.setattr(ig.nz, "normalize_all", lambda quote: data)
    return conn, upserts


def test_renormalize_forca_ticker_b3_e_registra_alias(monkeypatch):
    # payload salvo sob ELET3 (coluna = ticker-B3 requisitado), mas o
    # normalizador devolve o símbolo divergente AXIA3.
    rows = [("ELET3", 99, {"results": [{"symbol": "AXIA3"}]})]
    conn, upserts = _patch_renormalize(monkeypatch, rows, _full_data("AXIA3"))

    prog = ig.renormalize()
    assert prog["erros"] == 0 and prog["tickers"] == 1

    # tudo regravado sob ELET3, nunca AXIA3
    for table in ("assets", "historical_prices", "income_statements",
                  "balance_sheets", "cash_flow_statements", "dividends",
                  "calculated_metrics"):
        assert all(r["ticker"] == "ELET3" for r in upserts[table]), table

    # alias brapi->B3 registrado com o codigo_cvm da coluna
    alias = [p for s, p in conn.executed if "INSERT INTO market.ticker_alias" in s]
    assert alias, "renormalize deveria registrar o alias divergente"
    assert alias[0] == {"s": "AXIA3", "b": "ELET3", "c": 2437}


def test_renormalize_sem_divergencia_nao_registra_alias(monkeypatch):
    rows = [("ELET3", 99, {"results": [{"symbol": "ELET3"}]})]
    conn, upserts = _patch_renormalize(monkeypatch, rows, _full_data("ELET3"))

    ig.renormalize()
    assert all(r["ticker"] == "ELET3" for r in upserts["assets"])
    assert not any("market.ticker_alias" in s for s, _ in conn.executed)
