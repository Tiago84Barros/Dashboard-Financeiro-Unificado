# -*- coding: utf-8 -*-
"""A recusa da brapi chega ao log e ao JSON com o que a brapi disse.

De 29/10 a 13/11/2026 o `market-refresh` recebeu 429 em toda cotação, e o log
só dizia "HTTP 429". Sem o corpo e sem os cabeçalhos de cota, não dava para
separar cota mensal esgotada, limite por minuto e plano vencido. A regra vale
para os quatro pontos de `core/brapi.py` que levantam a recusa e para os três
disjuntores que a consomem (ações, FIIs e histórico de DY).
"""
import pytest

import core.brapi as brapi
from data_pipeline.market import fii_ingest, ingest

TOKEN = "tok-secreto-123456"


class _Resp:
    def __init__(self, status=429, body=None, text="", headers=None):
        self.status_code = status
        self._body = body
        self.text = text
        self.headers = headers or {}

    def json(self):
        if self._body is None:
            raise ValueError("sem json")
        return self._body


_CORPO_COTA = {"error": True, "code": "RATE_LIMIT_EXCEEDED",
               "message": "Você atingiu o limite de requisições do seu plano."}
_CABECALHOS = {"X-RateLimit-Limit": "150000", "X-RateLimit-Remaining": "0",
               "Retry-After": "3600", "Content-Type": "application/json"}


@pytest.fixture(autouse=True)
def _token(monkeypatch):
    monkeypatch.setenv("BRAPI_TOKEN", TOKEN)


# ── motivo_recusa ─────────────────────────────────────────────────────────────

def test_motivo_traz_corpo_e_cabecalhos_de_cota():
    m = brapi.motivo_recusa(_Resp(body=_CORPO_COTA, headers=_CABECALHOS))
    assert m.startswith("HTTP 429 | ")
    assert "RATE_LIMIT_EXCEEDED" in m and "limite de requisições" in m
    # cabeçalho lido sem diferenciar maiúsculas; o que não é de cota fica fora
    assert "x-ratelimit-remaining=0" in m and "x-ratelimit-limit=150000" in m
    assert "retry-after=3600" in m
    assert "content-type" not in m
    # `error: true` não vira texto
    assert "True" not in m


def test_motivo_nunca_expoe_o_token():
    corpo = {"message": f"token {TOKEN} sem cota; url ?token={TOKEN}&x=1 "
                        f"Authorization: Bearer {TOKEN}"}
    m = brapi.motivo_recusa(_Resp(body=corpo))
    assert TOKEN not in m
    assert "***" in m


def test_motivo_mascara_outro_token_no_texto(monkeypatch):
    monkeypatch.delenv("BRAPI_TOKEN")
    monkeypatch.setattr(brapi, "_token", lambda: "")
    m = brapi.motivo_recusa(_Resp(body=None, text="GET /quote?token=abcDEF987&range=max"))
    assert "abcDEF987" not in m and "token=***" in m


def test_motivo_de_corpo_texto_e_truncado_em_uma_linha():
    m = brapi.motivo_recusa(_Resp(body=None, text="linha1\n" + "x" * 1000))
    assert "\n" not in m
    assert m.endswith("…")
    assert len(m) < 400


def test_motivo_sem_corpo_nem_cabecalho_fica_so_com_o_status():
    assert brapi.motivo_recusa(_Resp(body=None, text="")) == "HTTP 429"


# ── os quatro pontos que levantam a recusa ────────────────────────────────────

def _responde(monkeypatch, resp):
    monkeypatch.setattr("requests.get", lambda *a, **k: resp)


@pytest.mark.parametrize("chamada", [
    lambda: brapi.fetch_quote("PETR4"),
    lambda: brapi.fetch_quote_full("MXRF11"),
    lambda: brapi.fetch_list(),
    lambda: brapi.fetch_fund_list(),
    lambda: brapi.fetch_fii_v2("indicators", ["KNRI11"]),
], ids=["quote", "quote_full", "list", "fund_list", "fii_v2"])
def test_429_carrega_o_motivo_em_todo_caminho(monkeypatch, chamada):
    _responde(monkeypatch, _Resp(body=_CORPO_COTA, headers=_CABECALHOS))
    with pytest.raises(brapi.BrapiRateLimited) as exc:
        chamada()
    texto = str(exc.value)
    assert texto.startswith("HTTP 429")
    assert "RATE_LIMIT_EXCEEDED" in texto and "x-ratelimit-remaining=0" in texto
    assert TOKEN not in texto
    assert exc.value.retry_after == 3600.0


@pytest.mark.parametrize("status", [401, 403])
def test_quote_401_403_segue_retentavel_e_diz_o_motivo(monkeypatch, status):
    _responde(monkeypatch, _Resp(status=status,
                                 body={"error": True, "code": "INVALID_TOKEN"}))
    with pytest.raises(brapi.BrapiRateLimited) as exc:
        brapi.fetch_quote("PETR4")
    assert str(exc.value) == f"HTTP {status} | INVALID_TOKEN"


def test_fii_v2_401_segue_como_erro_de_autenticacao_com_o_codigo(monkeypatch):
    _responde(monkeypatch, _Resp(status=401, body={"error": True, "code": "INVALID_TOKEN",
                                                   "message": "Token inválido"}))
    with pytest.raises(brapi.BrapiAuthError) as exc:
        brapi.fetch_fii_v2("list", None)
    assert not isinstance(exc.value, brapi.BrapiRateLimited)
    assert str(exc.value) == "HTTP 401 | INVALID_TOKEN / Token inválido"


# ── os três disjuntores levam o motivo ao resultado ───────────────────────────

MOTIVO = "HTTP 429 | RATE_LIMIT_EXCEEDED | x-ratelimit-remaining=0"


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


def test_disjuntor_de_fiis_poe_o_motivo_no_json(monkeypatch):
    monkeypatch.setattr(fii_ingest, "_engine", lambda: _Engine())
    monkeypatch.setattr(fii_ingest, "_schema_ready", lambda c: True)
    monkeypatch.setattr(fii_ingest, "_score_metadata_ready", lambda c: True)
    monkeypatch.setattr(fii_ingest, "_vpa_map", lambda c: {})
    monkeypatch.setattr(fii_ingest.repo, "reset_db_cols_cache", lambda: None)
    monkeypatch.setattr(brapi, "fetch_fund_list", lambda: [f"AAA{i:02d}11" for i in range(9)])
    monkeypatch.setattr(fii_ingest.sched, "with_backoff", lambda fn, **kw: fn())
    monkeypatch.setattr(fii_ingest.sched, "sleep_jittered", lambda **k: None)

    def recusa(tk):
        raise brapi.BrapiRateLimited(MOTIVO)

    monkeypatch.setattr(brapi, "fetch_quote_full", recusa)
    prog = fii_ingest.ingest()
    assert prog["bloqueio_brapi"] is True
    assert prog["motivo_bloqueio"] == MOTIVO


def test_disjuntor_de_acoes_poe_o_motivo_no_json(monkeypatch):
    def recusa(engine, tk, *, prog, **kw):
        prog["erros"] += 1
        raise ingest.brapi.BrapiRateLimited(MOTIVO)

    monkeypatch.setattr(ingest.repo, "reset_db_cols_cache", lambda: None)
    monkeypatch.setattr(ingest.repo, "schema_exists", lambda c: True)
    monkeypatch.setattr(ingest.repo, "load_cvm_to_ticker", lambda c: {})
    monkeypatch.setattr(ingest, "_carregar_trocas", lambda e: {})
    monkeypatch.setattr(ingest, "ingest_ticker", recusa)
    monkeypatch.setattr(ingest.sched, "sleep_jittered", lambda **k: None)
    monkeypatch.delenv("MARKET_MAX_BLOCKS", raising=False)
    prog = ingest._run(_Engine(), [f"AAAA{i}" for i in range(1, 10)], range_="1mo",
                       full=False, batch_label="daily", interval="1d")
    assert prog["bloqueio_brapi"] is True
    assert prog["motivo_bloqueio"] == MOTIVO


def test_disjuntor_do_historico_de_dy_poe_o_motivo_na_mensagem(monkeypatch):
    import core.data_healing as healing
    from data_pipeline.jobs import update_brapi_history as job
    from data_pipeline.utils import db_utils

    monkeypatch.setattr(job, "_enabled", lambda: True)
    monkeypatch.setattr(db_utils, "get_pipeline_engine", lambda: _Engine())
    monkeypatch.setattr(job, "_ensure_state", lambda conn: None)
    monkeypatch.setattr(healing, "_ensure_aux_tables", lambda conn: None)
    monkeypatch.setattr(job, "_select_pending", lambda conn, n, d: ["AAAA3", "BBBB3", "CCCC3"])
    monkeypatch.setattr(job._sched, "with_backoff", lambda fn, **kw: fn())
    monkeypatch.setattr(job._sched, "sleep_jittered", lambda **k: None)

    def recusa(tk, *a, **k):
        raise brapi.BrapiRateLimited(MOTIVO)

    monkeypatch.setattr(brapi, "fetch_quote", recusa)
    res = job.run()
    assert res["status"] == "partial_success"
    assert MOTIVO in res["error_message"]


def test_benchmark_devolve_o_motivo_da_recusa(monkeypatch):
    monkeypatch.setattr(fii_ingest, "_engine", lambda: _Engine())

    def recusa(*a, **k):
        raise brapi.BrapiRateLimited(MOTIVO)

    monkeypatch.setattr(brapi, "fetch_quote", recusa)
    rep = fii_ingest.ingest_benchmark("XFIX11")
    assert rep["erros"] == -1 and rep["motivo"] == MOTIVO


def test_validate_guarda_o_motivo_no_relatorio_do_ticker(monkeypatch):
    monkeypatch.setattr(ingest, "_engine", lambda: _Engine())
    monkeypatch.setattr(ingest.repo, "schema_exists", lambda c: False)
    monkeypatch.setattr(ingest.repo, "load_cvm_to_ticker", lambda c: {})
    monkeypatch.setattr(ingest.sched, "with_backoff", lambda fn, **kw: fn())

    def recusa(*a, **k):
        raise brapi.BrapiRateLimited(MOTIVO)

    monkeypatch.setattr(brapi, "fetch_quote_full", recusa)
    rep = ingest.validate(["PETR4"], persist=False)
    assert rep["tickers"]["PETR4"]["erro"] == f"rate_limited: {MOTIVO}"
