"""A cota esgotada da brapi nao pode prender a cadeia de FIIs por 90 min (D2)."""

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


def test_breaker_persiste_os_fundos_que_deram_certo(monkeypatch):
    gravadas = []
    monkeypatch.setattr(fii_ingest, "_engine", lambda: _Engine())
    monkeypatch.setattr(fii_ingest, "_schema_ready", lambda c: True)
    monkeypatch.setattr(fii_ingest, "_score_metadata_ready", lambda c: True)
    monkeypatch.setattr(fii_ingest, "_vpa_map", lambda c: {})
    monkeypatch.setattr(fii_ingest.repo, "reset_db_cols_cache", lambda: None)
    monkeypatch.setattr(fii_ingest.repo, "save_raw_payload", lambda *a, **k: None)
    monkeypatch.setattr(fii_ingest.repo, "log_quality", lambda *a, **k: None)
    monkeypatch.setattr(fii_ingest.repo, "upsert",
                        lambda conn, tabela, rows: gravadas.extend(r["ticker"] for r in rows) or len(rows))
    monkeypatch.setattr(fii_ingest, "_snapshot_score_mensal", lambda *a, **k: 0)
    monkeypatch.setattr(fii_ingest, "registrar_universo_vivo", lambda *a, **k: 0)
    monkeypatch.setattr(brapi, "fetch_fund_list", lambda: [f"AAA{i:02d}11" for i in range(10)])
    monkeypatch.setattr(fii_ingest.sched, "with_backoff", lambda fn, **kw: fn())
    monkeypatch.setattr(fii_ingest.sched, "sleep_jittered", lambda **k: None)

    def cotacao(tk):
        if tk in ("AAA0011", "AAA0111", "AAA0211"):
            return {"symbol": tk}
        raise brapi.BrapiRateLimited("HTTP 429")

    monkeypatch.setattr(brapi, "fetch_quote_full", cotacao)
    monkeypatch.setattr(fii_ingest.fz, "compute_fii",
                        lambda q, ref: {"ticker": q["symbol"], "price": 10.0})
    prog = fii_ingest.ingest()
    assert prog["bloqueio_brapi"] is True and prog["erros"] == -1
    assert sorted(gravadas) == ["AAA0011", "AAA0111", "AAA0211"]
