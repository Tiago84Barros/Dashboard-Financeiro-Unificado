# -*- coding: utf-8 -*-
"""Cota da brapi esgotada não pode sair como sucesso no refresh de ações.

Entre 30/10 e 07/11/2026 toda chamada `quote` da brapi voltou 429. O disjuntor
de `_run` parava o lote no terceiro ticker e só registrava um aviso: `daily`
devolvia ``{"tickers": 0, "erros": 3}``, o comando saía 0 e a rotina carimbava
a cadeia de ações em dia. O preço do armazém ficou parado em 29/10 sem uma
linha de erro no log.

A ingestão de FIIs já tinha essa correção (D2, `bloqueio_brapi` e
``erros=-1``). Aqui ela chega ao gêmeo das ações, com a mesma regra: o que foi
coletado antes do bloqueio fica gravado, e o comando sai com erro.
"""
import sys

import pytest

from data_pipeline.market import ingest


class _Conn:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Engine:
    def connect(self):
        return _Conn()

    def begin(self):
        return _Conn()


def _prepara(monkeypatch, comportamento):
    """``comportamento(tk)``: True grava, False levanta 429, None levanta outro erro."""
    chamados = []

    def falso_ingest_ticker(engine, tk, *, prog, **kw):
        chamados.append(tk)
        r = comportamento(tk)
        if r is True:
            prog["tickers"] += 1
            return
        prog["erros"] += 1
        if r is False:
            raise ingest.brapi.BrapiRateLimited("HTTP 429")
        raise ingest.brapi.BrapiError("HTTP 500")

    monkeypatch.setattr(ingest.repo, "reset_db_cols_cache", lambda: None)
    monkeypatch.setattr(ingest.repo, "schema_exists", lambda c: True)
    monkeypatch.setattr(ingest.repo, "load_cvm_to_ticker", lambda c: {})
    monkeypatch.setattr(ingest, "_carregar_trocas", lambda e: {})
    monkeypatch.setattr(ingest, "ingest_ticker", falso_ingest_ticker)
    monkeypatch.setattr(ingest.sched, "sleep_jittered", lambda **k: None)
    monkeypatch.delenv("MARKET_MAX_BLOCKS", raising=False)
    return chamados


TICKERS = [f"AAAA{i}" for i in range(1, 10)]


def test_todo_ticker_recusado_sai_como_erro(monkeypatch):
    chamados = _prepara(monkeypatch, lambda tk: False)
    prog = ingest._run(_Engine(), TICKERS, range_="1mo", full=False,
                       batch_label="daily", interval="1d")
    assert prog["bloqueio_brapi"] is True
    assert prog["erros"] == -1
    assert prog["tickers"] == 0
    assert len(chamados) == 3          # o disjuntor continua parando no 3º


def test_bloqueio_depois_de_coletar_guarda_o_coletado(monkeypatch):
    ok = set(TICKERS[:2])
    chamados = _prepara(monkeypatch, lambda tk: tk in ok)
    prog = ingest._run(_Engine(), TICKERS, range_="1mo", full=False,
                       batch_label="daily", interval="1d")
    assert prog["bloqueio_brapi"] is True and prog["erros"] == -1
    assert prog["tickers"] == 2
    assert prog["nao_tentados"] == len(TICKERS) - len(chamados)


def test_recusa_isolada_nao_aciona_o_bloqueio(monkeypatch):
    """Um 429 no meio, seguido de sucesso, zera a contagem: lote completo."""
    _prepara(monkeypatch, lambda tk: tk != "AAAA3")
    prog = ingest._run(_Engine(), TICKERS, range_="1mo", full=False,
                       batch_label="daily", interval="1d")
    assert not prog.get("bloqueio_brapi")
    assert prog["erros"] == 1
    assert prog["tickers"] == len(TICKERS) - 1


def test_erro_que_nao_e_cota_nao_aciona_o_bloqueio(monkeypatch):
    _prepara(monkeypatch, lambda tk: None)
    prog = ingest._run(_Engine(), TICKERS, range_="1mo", full=False,
                       batch_label="daily", interval="1d")
    assert not prog.get("bloqueio_brapi")
    assert prog["erros"] == len(TICKERS)


@pytest.mark.parametrize("comando", ["daily", "annual"])
@pytest.mark.parametrize("bloqueio,esperado", [(True, 1), (False, 0)])
def test_comando_sai_com_erro_no_bloqueio(monkeypatch, caplog, comando,
                                          bloqueio, esperado):
    import run_market_ingest as cli

    local = "postgresql://postgres:x@127.0.0.1:5433/postgres"
    for chave in ("SUPABASE_UNIFICADO_URL", "DATABASE_URL", "SUPABASE_DB_URL"):
        monkeypatch.setenv(chave, local)
    monkeypatch.setattr(cli, "_point_to_warehouse", lambda: True)
    prog = ingest._new_progress()
    if bloqueio:
        prog.update(erros=-1, bloqueio_brapi=True, nao_tentados=5)
    monkeypatch.setattr(ingest, comando, lambda *a, **k: dict(prog))
    monkeypatch.setattr(sys, "argv", ["run_market_ingest.py", comando,
                                      "--source", "market", "--warehouse"])
    assert cli.main() == esperado
    if bloqueio:
        texto = caplog.text
        assert "brapi" in texto and "429" in texto
        # a mensagem genérica de schema ausente mandaria procurar no lugar errado
        assert "schema market.* ausente" not in texto
