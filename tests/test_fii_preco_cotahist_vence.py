"""Cotacao de FII: o fechamento da fita da B3 (COTAHIST) vence a foto da brapi
quando e de pregao mais novo (10/11/2026). A cota gratuita da brapi acabou em
29/10 e nada promovia o close de `fii_b3_security_history` para
`market.fiis.price`. Regra unica com a leitura do #707: vence a observacao de
data mais nova. Dados sinteticos."""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from core import market_read
from data_pipeline.market import fii as fz
from data_pipeline.market import fii_ingest

_BRT = ZoneInfo("America/Sao_Paulo")


def _m(**kw):
    m = {"ticker": "FUNDO11", "price": 100.0, "pvp": 1.0, "dy_12m": 0.12,
         "liquidez_diaria": 1e6}
    m.update(kw)
    return m


# ── regra pura ───────────────────────────────────────────────────────────────

def test_fita_mais_nova_que_a_brapi_vence():
    out = fz.aplicar_fechamento_pregao(
        _m(), (dt.date(2026, 11, 6), 80.0), "2026-10-29T16:14:00.000Z", vpa=100.0)
    assert out["price"] == 80.0
    assert out["pvp"] == pytest.approx(0.8)          # preco / VPA
    assert out["dy_12m"] == pytest.approx(0.15)      # mesma renda / preco novo
    assert out["price_at"] == dt.datetime(2026, 11, 6, 18, 0, tzinfo=_BRT)
    assert out["price_source"] == "b3_cotahist"


def test_brapi_mais_nova_que_a_fita_fica():
    m = _m()
    out = fz.aplicar_fechamento_pregao(
        m, (dt.date(2026, 11, 6), 80.0), "2026-11-07T15:00:00Z", vpa=100.0)
    assert out == m


def test_sem_fita_nada_muda():
    m = _m()
    assert fz.aplicar_fechamento_pregao(m, None, "2026-10-29T16:14:00Z", vpa=100.0) == m


def test_fechamento_do_mesmo_dia_vence_a_cotacao_intradiaria():
    # brapi capturou 13:44 UTC (10:44 BRT); o close do mesmo pregao e posterior.
    out = fz.aplicar_fechamento_pregao(
        _m(price=6.0), (dt.date(2026, 10, 29), 9.0), "2026-10-29T13:44:00Z", vpa=None)
    assert out["price"] == 9.0


def test_pvp_sem_vpa_reescala_o_da_brapi():
    out = fz.aplicar_fechamento_pregao(
        _m(pvp=0.9), (dt.date(2026, 11, 6), 50.0), "2026-10-29T16:14:00Z", vpa=None)
    assert out["pvp"] == pytest.approx(0.45)


def test_sem_data_da_base_a_cotacao_presente_fica():
    m = _m()
    assert fz.aplicar_fechamento_pregao(m, (dt.date(2026, 11, 6), 80.0), None, vpa=100.0) == m


def test_sem_preco_na_base_a_fita_preenche_sem_inventar_dy():
    out = fz.aplicar_fechamento_pregao(
        _m(price=None, dy_12m=None, pvp=None), (dt.date(2026, 11, 6), 80.0), None, vpa=100.0)
    assert out["price"] == 80.0 and out["pvp"] == pytest.approx(0.8)
    assert out["dy_12m"] is None


def test_dy_fora_da_faixa_vira_ausente():
    out = fz.aplicar_fechamento_pregao(
        _m(price=100.0, dy_12m=0.9), (dt.date(2026, 11, 6), 10.0), "2026-10-29", vpa=None)
    assert out["dy_12m"] is None


def test_close_invalido_e_ignorado():
    m = _m()
    assert fz.aplicar_fechamento_pregao(m, (dt.date(2026, 11, 6), 0.0), "2026-10-29", vpa=1.0) == m


def test_compute_fii_data_a_cotacao_pela_hora_do_pregao_da_brapi():
    quote = {"symbol": "FUNDO11", "regularMarketPrice": 10.0,
             "regularMarketTime": "2026-10-29T16:14:00.000Z",
             "summaryProfile": {"sector": fz.FII_SECTOR}}
    m = fz.compute_fii(quote, dt.date(2026, 10, 29))
    assert m["price_at"] == dt.datetime(2026, 10, 29, 16, 14, tzinfo=dt.timezone.utc)


# ── uma regra so: leitura (#707) e escrita decidem igual ─────────────────────

@pytest.mark.parametrize("base", [
    "2026-10-29T16:14:00Z", "2026-11-06T20:59:00Z", "2026-11-06T21:01:00Z",
    "2026-11-07T12:00:00Z", None])
@pytest.mark.parametrize("pregao", [dt.date(2026, 10, 28), dt.date(2026, 11, 6)])
def test_leitura_e_escrita_usam_a_mesma_regra(base, pregao):
    fechamento_em = fz.fechamento_pregao_em(pregao)
    row = {"ticker": "FUNDO11", "price": 100.0, "price_at": base, "updated_at": None}
    obs = {"metric_name": "price", "knowledge_at": pd.Timestamp(fechamento_em),
           "reference_date": pd.Timestamp(pregao)}
    leitura = market_read._observacao_vence_base(row, "price", obs)
    escrita = fz.aplicar_fechamento_pregao(_m(), (pregao, 80.0), base, vpa=None)["price"] == 80.0
    regra = fz.mais_nova_vence(100.0, fz.instante(base), fechamento_em)
    assert leitura == escrita == regra


def test_mais_nova_vence_preenche_ausencia_e_nao_decide_sem_data():
    agora = dt.datetime(2026, 11, 6, tzinfo=dt.timezone.utc)
    assert fz.mais_nova_vence(None, None, None) is True
    assert fz.mais_nova_vence(float("nan"), agora, None) is True
    assert fz.mais_nova_vence(1.0, None, agora) is False
    assert fz.mais_nova_vence(1.0, agora, None) is False
    assert fz.mais_nova_vence(1.0, agora, agora) is False


# ── etapa que grava, sem brapi ───────────────────────────────────────────────

class _Res:
    rowcount = 1


class _Conn:
    def __init__(self, log):
        self.log = log

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, stmt, params=None):
        self.log.append((str(stmt), params))
        return _Res()


class _Engine:
    def __init__(self):
        self.log = []

    def connect(self):
        return _Conn(self.log)

    def begin(self):
        return _Conn(self.log)


def _preparar(monkeypatch, colunas):
    eng = _Engine()
    monkeypatch.setattr(fii_ingest, "_engine", lambda: eng)
    monkeypatch.setattr(fii_ingest.repo, "reset_db_cols_cache", lambda: None)
    monkeypatch.setattr(fii_ingest.repo, "_db_cols", lambda conn, t: set(colunas))
    monkeypatch.setattr(fii_ingest, "_linhas_cotacao_fii", lambda conn, com_price_at: [
        {"ticker": "VELHO11", "price": 100.0, "pvp": 1.0, "dy_12m": 0.12, "vpa": 100.0},
        {"ticker": "NOVO11", "price": 50.0, "pvp": 1.0, "dy_12m": 0.10, "vpa": 50.0},
        {"ticker": "SEMFITA11", "price": 10.0, "pvp": 1.0, "dy_12m": 0.10, "vpa": 10.0},
    ])
    monkeypatch.setattr(fii_ingest, "_datas_base_cotacao_fii", lambda conn, tickers: {
        "VELHO11": dt.datetime(2026, 10, 29, 16, 14, tzinfo=dt.timezone.utc),
        "NOVO11": dt.datetime(2026, 11, 7, 15, 0, tzinfo=dt.timezone.utc),
        "SEMFITA11": dt.datetime(2026, 10, 29, 16, 14, tzinfo=dt.timezone.utc)})
    monkeypatch.setattr(fii_ingest, "_fechamentos_pregao_fii", lambda conn, tickers: {
        "VELHO11": (dt.date(2026, 11, 6), 80.0), "NOVO11": (dt.date(2026, 11, 6), 40.0)})
    return eng


def _updates(eng):
    return [(sql, p) for sql, p in eng.log if sql.lstrip().upper().startswith("UPDATE")]


_COLS_SEM_088 = {"ticker", "price", "pvp", "dy_12m", "vpa", "updated_at"}


def test_etapa_grava_so_quem_a_fita_vence_e_sem_price_at_nao_quebra(monkeypatch):
    eng = _preparar(monkeypatch, _COLS_SEM_088)
    rep = fii_ingest.promover_fechamento_pregao()
    ups = _updates(eng)
    assert rep["status"] == "completed" and rep["atualizados"] == 1
    assert len(ups) == 1
    sql, params = ups[0]
    assert "price_at" not in sql
    assert params[0]["ticker"] == "VELHO11" and params[0]["price"] == 80.0
    assert params[0]["pvp"] == pytest.approx(0.8)


def test_etapa_grava_price_at_quando_a_088_existe(monkeypatch):
    eng = _preparar(monkeypatch, _COLS_SEM_088 | {"price_at"})
    fii_ingest.promover_fechamento_pregao()
    sql, params = _updates(eng)[0]
    assert "price_at" in sql
    assert params[0]["price_at"] == fz.fechamento_pregao_em(dt.date(2026, 11, 6))


def test_dry_run_mede_sem_gravar(monkeypatch):
    eng = _preparar(monkeypatch, _COLS_SEM_088)
    rep = fii_ingest.promover_fechamento_pregao(dry_run=True)
    assert _updates(eng) == []
    assert rep["atualizados"] == 0 and rep["mudariam"] == 1
    assert rep["amostra"][0]["ticker"] == "VELHO11"


def test_escrita_e_leitura_concordam_no_mesmo_fundo(monkeypatch):
    # O que a etapa grava e o que a leitura do #707 aceitaria como observacao.
    eng = _preparar(monkeypatch, _COLS_SEM_088 | {"price_at"})
    fii_ingest.promover_fechamento_pregao()
    gravados = {p["ticker"] for _, ps in _updates(eng) for p in ps}
    for ticker, base, pregao in (
            ("VELHO11", "2026-10-29T16:14:00Z", dt.date(2026, 11, 6)),
            ("NOVO11", "2026-11-07T15:00:00Z", dt.date(2026, 11, 6))):
        obs = {"knowledge_at": pd.Timestamp(fz.fechamento_pregao_em(pregao))}
        leitura = market_read._observacao_vence_base(
            {"price": 1.0, "price_at": base}, "price", obs)
        assert leitura == (ticker in gravados)


# ── a etapa esta na cadeia, depois da fita e da CVM, antes de `fiis` ─────────

def test_cadeia_fii_promove_a_fita_antes_da_brapi():
    from core import publicacao_agenda as pa
    comandos = [p[1] for p in pa._CADEIA_FII if p[0] == "run_market_ingest.py"]
    assert "fiis-cotahist" in comandos
    i = comandos.index("fiis-cotahist")
    assert comandos.index("fiis-cvm") < i < comandos.index("fiis")
    assert comandos.index("fiis-confidence") < i
    # a fita (alvo b3_pregao) roda antes da cadeia de FIIs
    chaves = [a.chave for a in pa.ALVOS]
    assert chaves.index("b3_pregao") < chaves.index("fii_ingest")


def test_runner_aceita_o_comando(monkeypatch):
    import sys

    import run_market_ingest
    chamadas = []
    monkeypatch.setattr(fii_ingest, "promover_fechamento_pregao",
                        lambda dry_run=False: chamadas.append(dry_run) or {
                            "status": "completed", "atualizados": 0})
    monkeypatch.setattr(sys, "argv", ["run_market_ingest.py", "fiis-cotahist", "--dry-run"])
    assert run_market_ingest.main() == 0
    assert chamadas == [True]


# -- revisao do Codex no #713 -------------------------------------------------

def test_etapa_nao_regrava_close_ja_promovido(monkeypatch):
    # Sem a 088 a data-base continua a da brapi; o close ja gravado nao pode
    # disparar UPDATE de novo (o gatilho renovaria `updated_at` sem dado novo).
    eng = _preparar(monkeypatch, _COLS_SEM_088)
    monkeypatch.setattr(fii_ingest, "_linhas_cotacao_fii", lambda conn, com_price_at: [
        {"ticker": "VELHO11", "price": 80.0, "pvp": 0.8, "dy_12m": 0.15, "vpa": 100.0}])
    rep = fii_ingest.promover_fechamento_pregao()
    assert _updates(eng) == []
    assert rep["mudariam"] == 0 and rep["atualizados"] == 0
    assert rep["ja_no_fechamento"] == 1


def test_update_da_etapa_so_regrava_preco_diferente(monkeypatch):
    eng = _preparar(monkeypatch, _COLS_SEM_088)
    fii_ingest.promover_fechamento_pregao()
    sql, _ = _updates(eng)[0]
    assert "IS DISTINCT FROM" in sql


class _ConnNested(_Conn):
    def begin_nested(self):
        return self


def test_indicadores_dy_invalidado_pelo_preco_novo_vai_nulo(monkeypatch):
    monkeypatch.setattr(fii_ingest, "_fechamentos_pregao_fii", lambda conn, tickers: {
        "FUNDO11": (dt.date(2026, 11, 6), 10.0)})
    saida = fii_ingest._fita_nos_indicadores(
        _ConnNested([]), [{"ticker": "FUNDO11", "price": 100.0, "pvp": 1.0,
                           "dy_12m": 0.6, "vpa": 100.0}],
        dt.datetime(2026, 10, 29, 16, 14, tzinfo=dt.timezone.utc))
    assert saida[0]["price"] == 10.0
    assert "dy_12m" in saida[0] and saida[0]["dy_12m"] is None


def test_indicadores_sem_troca_nao_ganham_chave_nula(monkeypatch):
    monkeypatch.setattr(fii_ingest, "_fechamentos_pregao_fii", lambda conn, tickers: {})
    u = {"ticker": "FUNDO11", "price": 100.0, "vpa": 100.0}
    saida = fii_ingest._fita_nos_indicadores(
        _ConnNested([]), [u], dt.datetime(2026, 11, 7, tzinfo=dt.timezone.utc))
    assert "dy_12m" not in saida[0] and "pvp" not in saida[0]


def test_upsert_por_colunas_nao_anula_coluna_de_outra_linha(monkeypatch):
    # `_upsert` tira as colunas da primeira linha: lote heterogeneo gravaria
    # NULL nas colunas que uma linha nao trouxe, ou perderia as que so ela trouxe.
    chamadas = []
    monkeypatch.setattr(fii_ingest.repo, "_upsert",
                        lambda conn, table, rows, **kw: chamadas.append(rows) or len(rows))
    n = fii_ingest.repo.upsert_por_colunas(None, "fiis", [
        {"ticker": "A11", "price": 1.0},
        {"ticker": "B11", "price": 2.0, "dy_12m": None},
        {"ticker": "C11", "price": 3.0}])
    assert n == 3
    assert sorted(sorted(r[0]) for r in chamadas) == [
        ["dy_12m", "price", "ticker"], ["price", "ticker"]]
    assert [r["ticker"] for lote in chamadas for r in lote if "dy_12m" in r] == ["B11"]
    for lote in chamadas:
        assert len({frozenset(r) for r in lote}) == 1


def test_data_base_ignora_backfill_do_historico():
    # Observacao `indicators/history` carrega a hora da coleta para meses
    # passados; ela nunca gravou `market.fiis.price` e nao pode datar a cotacao.
    log = []

    class _C(_Conn):
        def execute(self, stmt, params=None):
            log.append(str(stmt))

            class _R:
                def fetchall(self):
                    return []
            return _R()

    fii_ingest._datas_base_cotacao_fii(_C([]), ["FUNDO11"])
    sql = " ".join(log[0].split())
    assert "metadata_json->>'endpoint' = 'indicators'" in sql


def _mesmo_preco_pregao_novo(monkeypatch, colunas, price_at):
    # Gravado: 100 no pregao de 05/11. Fita: 100 de novo no pregao de 09/11.
    eng = _preparar(monkeypatch, colunas)
    linha = {"ticker": "VELHO11", "price": 100.0, "pvp": 1.0, "dy_12m": 0.12, "vpa": 100.0}
    if price_at is not None:
        linha["price_at"] = price_at
    monkeypatch.setattr(fii_ingest, "_linhas_cotacao_fii",
                        lambda conn, com_price_at: [dict(linha)])
    monkeypatch.setattr(fii_ingest, "_datas_base_cotacao_fii", lambda conn, tickers: {
        "VELHO11": fz.fechamento_pregao_em(dt.date(2026, 11, 5))})
    monkeypatch.setattr(fii_ingest, "_fechamentos_pregao_fii", lambda conn, tickers: {
        "VELHO11": (dt.date(2026, 11, 9), 100.0)})
    return eng, fii_ingest.promover_fechamento_pregao()


def test_com_price_at_mesmo_preco_em_pregao_novo_avanca_a_data(monkeypatch):
    # Com a 088, a data e dado: o preco igual nao pode deixar `price_at` no
    # pregao velho, senao a leitura publica a data velha e aceita observacoes
    # mais velhas que o close que de fato venceu.
    eng, rep = _mesmo_preco_pregao_novo(
        monkeypatch, _COLS_SEM_088 | {"price_at"}, fz.fechamento_pregao_em(dt.date(2026, 11, 5)))
    ups = _updates(eng)
    assert len(ups) == 1
    sql, params = ups[0]
    assert params[0]["price_at"] == fz.fechamento_pregao_em(dt.date(2026, 11, 9))
    assert params[0]["price"] == 100.0
    # o predicado do UPDATE deixa passar a data nova com o mesmo preco
    assert "price_at IS DISTINCT FROM" in " ".join(sql.split())
    assert rep["atualizados"] == 1 and rep["ja_no_fechamento"] == 0
    # data que avanca sem preco novo nao entra na variacao de preco
    assert rep["so_data"] == 1 and rep["amostra"] == []


def test_com_price_at_nulo_mesmo_preco_grava_a_data(monkeypatch):
    eng, rep = _mesmo_preco_pregao_novo(monkeypatch, _COLS_SEM_088 | {"price_at"}, None)
    ups = _updates(eng)
    assert len(ups) == 1
    assert ups[0][1][0]["price_at"] == fz.fechamento_pregao_em(dt.date(2026, 11, 9))


def test_sem_price_at_mesmo_preco_em_pregao_novo_nao_grava(monkeypatch):
    eng, rep = _mesmo_preco_pregao_novo(monkeypatch, _COLS_SEM_088, None)
    assert _updates(eng) == []
    assert rep["ja_no_fechamento"] == 1 and rep["atualizados"] == 0
