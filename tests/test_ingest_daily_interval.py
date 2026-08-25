# -*- coding: utf-8 -*-
"""O refresh diário precisa pedir velas DIÁRIAS.

Regressão que custou caro: `daily()` chamava a brapi com range=1mo e sem
interval. O default da API é mensal, então a resposta trazia duas velas — as
bordas do mês — em vez dos ~22 pregões. Nenhum erro, nenhum ticker perdido, e
mesmo assim o preço mais novo do universo travava na última borda de mês. Com
`interval="1d"` a mesma chamada devolveu 22 linhas até o pregão de hoje.
"""
import contextlib

from data_pipeline.market import ingest


class _EngineFalsa:
    """Payload vazio ainda registra o raw payload; a engine só precisa existir."""

    @contextlib.contextmanager
    def begin(self):
        yield None


def _captura(monkeypatch):
    chamadas = []

    def falso_fetch_quote(tk, **kw):
        chamadas.append(("quote", tk, kw))
        return None          # payload vazio encerra o ticker sem tocar no banco

    def falso_fetch_quote_full(tk, **kw):
        chamadas.append(("full", tk, kw))
        return None

    monkeypatch.setattr(ingest.brapi, "fetch_quote", falso_fetch_quote)
    monkeypatch.setattr(ingest.brapi, "fetch_quote_full", falso_fetch_quote_full)
    monkeypatch.setattr(ingest.repo, "save_raw_payload",
                        lambda *a, **k: None)
    return chamadas


def test_ingest_ticker_repassa_o_intervalo(monkeypatch):
    chamadas = _captura(monkeypatch)
    prog = ingest._new_progress()
    ingest.ingest_ticker(_EngineFalsa(), "ABEV3", range_="1mo", full=False,
                         cvm_map={}, prog=prog, interval="1d")
    assert chamadas[0][2]["interval"] == "1d"
    assert chamadas[0][2]["range_"] == "1mo"


def test_intervalo_acompanha_o_modo_full(monkeypatch):
    chamadas = _captura(monkeypatch)
    prog = ingest._new_progress()
    ingest.ingest_ticker(_EngineFalsa(), "ABEV3", range_="1y", full=True,
                         cvm_map={}, prog=prog, interval="1d")
    assert chamadas[0][0] == "full"
    assert chamadas[0][2]["interval"] == "1d"


def test_daily_pede_vela_diaria(monkeypatch):
    """O teste que faltava: sem ele, o default mensal passa em silêncio."""
    vistos = {}

    def falso_run(engine, tickers, **kw):
        vistos.update(kw)
        return ingest._new_progress()

    monkeypatch.setattr(ingest, "_engine", lambda: object())
    monkeypatch.setattr(ingest, "_run", falso_run)
    ingest.daily(tickers=["ABEV3"])
    assert vistos["interval"] == "1d"
    assert vistos["range_"] == "1mo"
