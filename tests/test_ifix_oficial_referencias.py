"""IFIX oficial mensal da B3 e as referências do job diário de cotações."""
from __future__ import annotations

import sys
import types
from datetime import date

import pandas as pd
from sqlalchemy import create_engine, text

import data_pipeline.jobs.update_b3_quotes as job
from data_pipeline.market import ifix_oficial as ifix

# ── IFIX oficial ─────────────────────────────────────────────────────────────

def test_janela_corta_no_ultimo_mes_encerrado():
    assert ifix.janela_meses_fechados(date(2026, 10, 4)) == (date(2025, 9, 1), date(2026, 9, 30))
    assert ifix.janela_meses_fechados(date(2026, 1, 31), 3) == (date(2025, 10, 1), date(2025, 12, 31))
    assert ifix.janela_meses_fechados(date(2026, 3, 1), 1) == (date(2026, 2, 1), date(2026, 2, 28))


def test_parse_exclui_o_mes_aberto_e_lixo():
    payload = [
        {"month": 8, "year": 2026, "indexClosingRate": "3762.79"},
        {"month": 9, "year": 2026, "indexClosingRate": 3755.22},
        {"month": 10, "year": 2026, "indexClosingRate": 3770.0},  # mês aberto
        {"month": 7, "year": 2026, "indexClosingRate": None},
        {"month": 6, "year": 2026, "indexClosingRate": -1},
        "lixo",
    ]
    start, end = ifix.janela_meses_fechados(date(2026, 10, 4))
    rows = ifix.parse_ifix_mensal(payload, start=start, end=end)
    assert rows == [{"date": date(2026, 8, 31), "value": 3762.79},
                    {"date": date(2026, 9, 30), "value": 3755.22}]
    assert ifix.parse_ifix_mensal({"erro": 1}, start=start, end=end) == []


def test_atualizar_nunca_levanta(monkeypatch):
    def _cai(*_a, **_k):
        raise ConnectionError("B3 fora do ar")

    monkeypatch.setattr(ifix, "baixar_ifix_mensal", _cai)
    out = ifix.atualizar_ifix_oficial(object(), hoje=date(2026, 10, 4))
    assert out["status"] == "failed" and out["rows"] == 0
    assert "B3 fora do ar" in out["error"] and out["fim"] == "2026-09-30"

    monkeypatch.setattr(ifix, "baixar_ifix_mensal", lambda *_a: ([], "h"))
    assert ifix.atualizar_ifix_oficial(object(), hoje=date(2026, 10, 4))["status"] == "empty"


def test_fonte_da_leitura_e_a_mesma_do_etl():
    import core.carteira_atribuicao as ca

    assert ca._FONTE_IFIX_OFICIAL == ifix.FONTE


def test_fii_pit_reexporta_as_funcoes_antigas():
    from data_pipeline.market import fii_pit

    assert fii_pit._parse_b3_ifix_monthly is ifix.parse_ifix_mensal
    assert fii_pit._ingest_b3_ifix_monthly is ifix.ingerir_ifix_mensal


# ── Referências no job de cotações ───────────────────────────────────────────

def test_periodo_do_ativo():
    assert job._periodo_do_ativo("PETR4", "5d", set()) == "5d"
    assert job._periodo_do_ativo("XFIX11", "5d", set()) == "3mo"
    assert job._periodo_do_ativo("xfix11", "5d", {"XFIX11"}) == "1y"


def test_referencia_e_elegivel():
    row = types.SimpleNamespace(ticker="XFIX11", currency="BRL", asset_class="etf")
    assert job._is_quote_eligible(row)


def _engine(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 'q.db'}")
    with eng.begin() as c:
        c.execute(text("""CREATE TABLE assets (id INTEGER PRIMARY KEY, ticker TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL, "class" TEXT NOT NULL, currency TEXT DEFAULT 'BRL',
            exchange TEXT, ativo BOOLEAN DEFAULT 1)"""))
        c.execute(text("""CREATE TABLE asset_quotes (asset_id TEXT, timestamp TIMESTAMP,
            open REAL, high REAL, low REAL, close REAL, volume REAL,
            UNIQUE (asset_id, timestamp))"""))
        c.execute(text("""INSERT INTO assets (ticker,name,"class",currency)
            VALUES ('PETR4','Petrobras','stock','BRL')"""))
    return eng


def test_garantir_referencias_cria_reativa_e_e_idempotente(tmp_path):
    eng = _engine(tmp_path)
    cols = {"id", "ticker", "name", "class", "currency", "exchange", "ativo"}
    with eng.begin() as c:
        assert job._garantir_referencias(c, cols) == {"XFIX11"}
    with eng.begin() as c:
        assert job._garantir_referencias(c, cols) == set()
        c.execute(text("UPDATE assets SET ativo = 0 WHERE ticker = 'XFIX11'"))
    with eng.begin() as c:
        assert job._garantir_referencias(c, cols) == {"XFIX11"}
        row = c.execute(text("""SELECT name,"class",currency,exchange,ativo
            FROM assets WHERE ticker='XFIX11'""")).one()
    assert tuple(row) == ("TREND ETF IFIX (referencia dos FIIs)", "etf", "BRL", "B3", 1)
    assert job._garantir_referencias(None, {"ticker"}) == set()


def _rodar(monkeypatch, eng, historicos):
    chamadas = []

    def _download(ticker, period, **_k):
        chamadas.append((ticker, period))
        return historicos.get(ticker, pd.DataFrame())

    monkeypatch.setitem(sys.modules, "yfinance", types.SimpleNamespace(download=_download))
    import data_pipeline.utils.db_utils as db_utils

    monkeypatch.setattr(db_utils, "get_pipeline_engine", lambda: eng)
    monkeypatch.setattr(ifix, "atualizar_ifix_oficial",
                        lambda _e: {"status": "failed", "rows": 0, "error": "B3 fora"})
    monkeypatch.setattr(job.time, "sleep", lambda _s: None)
    return job.run(periodo="5d", apenas_desatualizados=False), chamadas


def _hist(*closes):
    idx = pd.date_range("2026-09-28", periods=len(closes), freq="D")
    return pd.DataFrame({"Open": closes, "High": closes, "Low": closes,
                         "Close": closes, "Volume": [1.0] * len(closes)}, index=idx)


def test_run_baixa_a_referencia_nova_com_um_ano_e_ifix_falho_nao_derruba(monkeypatch, tmp_path):
    eng = _engine(tmp_path)
    res, chamadas = _rodar(monkeypatch, eng, {"XFIX11.SA": _hist(10.0, 10.1),
                                              "PETR4.SA": _hist(30.0)})
    assert res["status"] == "success"
    assert res["referencias_novas"] == ["XFIX11"]
    assert res["ifix_oficial"]["status"] == "failed"
    assert ("XFIX11.SA", "1y") in chamadas and ("PETR4.SA", "5d") in chamadas
    assert res["records_inserted"] == 3

    res2, chamadas2 = _rodar(monkeypatch, eng, {"XFIX11.SA": _hist(10.2)})
    assert res2["referencias_novas"] == []
    assert ("XFIX11.SA", "3mo") in chamadas2


def test_run_nao_desativa_a_referencia_sem_dados(monkeypatch, tmp_path):
    eng = _engine(tmp_path)
    res, _ = _rodar(monkeypatch, eng, {})
    assert res["status"] == "success" and res["records_inserted"] == 0
    with eng.connect() as c:
        ativos = dict(c.execute(text("SELECT ticker, ativo FROM assets")).fetchall())
    assert ativos == {"PETR4": 0, "XFIX11": 1}


def test_run_segue_quando_garantir_referencias_falha(monkeypatch, tmp_path):
    eng = _engine(tmp_path)

    def _cai(*_a):
        raise RuntimeError("sem permissao")

    monkeypatch.setattr(job, "_garantir_referencias", _cai)
    res, chamadas = _rodar(monkeypatch, eng, {"PETR4.SA": _hist(30.0)})
    assert res["status"] == "success" and "sem permissao" in res["referencias_erro"]
    assert chamadas == [("PETR4.SA", "5d")]
