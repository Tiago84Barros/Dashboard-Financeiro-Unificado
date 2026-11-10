"""A cota esgotada da brapi nao pode prender a cadeia de FIIs por 90 min (D2)."""
from contextlib import contextmanager

import core.brapi as brapi
from data_pipeline.market import fii_ingest


class _Conn:
    def __enter__(self): return self
    def __exit__(self, *a): return False


class _Engine:
    def connect(self): return _Conn()
    def begin(self): return _Conn()


def test_ingest_aborta_apos_recusas_seguidas(monkeypatch):
    chamadas = []
    monkeypatch.setattr(fii_ingest, "_engine", lambda: _Engine())
    monkeypatch.setattr(fii_ingest, "_schema_ready", lambda c: True)
    monkeypatch.setattr(fii_ingest, "_score_metadata_ready", lambda c: True)
    monkeypatch.setattr(fii_ingest, "_vpa_map", lambda c: {})
    monkeypatch.setattr(fii_ingest.repo, "reset_db_cols_cache", lambda: None)
    monkeypatch.setattr(brapi, "fetch_fund_list", lambda: [f"AAA{i:02d}11" for i in range(50)])

    def com_backoff(fn, **kw):
        return fn()

    def cotacao(tk):
        chamadas.append(tk)
        raise brapi.BrapiRateLimited("HTTP 429")

    monkeypatch.setattr(fii_ingest.sched, "with_backoff", com_backoff)
    monkeypatch.setattr(fii_ingest.sched, "sleep_jittered", lambda **k: None)
    monkeypatch.setattr(brapi, "fetch_quote_full", cotacao)
    prog = fii_ingest.ingest()
    assert prog["bloqueio_brapi"] is True and prog["erros"] == -1
    assert len(chamadas) == fii_ingest.LIMITE_RECUSAS_SEGUIDAS
