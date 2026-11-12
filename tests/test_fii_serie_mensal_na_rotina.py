"""A série mensal da CVM (``fiis-metrics``) roda na cadeia noturna de FIIs.

Até 12/11/2026 o comando não estava em cadeia nenhuma: só rodava à mão, e
``market.fii_metrics_monthly`` parou em 05/2026 para 282 dos 325 fundos. O
P/VP histórico e a série de VPA da tela pararam junto, sem erro em lugar
nenhum. A guarda de frescor do publicador é testada em
``test_vitrines_local_first.py``.
"""
from __future__ import annotations

import sys
from datetime import date

import core.cvm_fii as cvm
from data_pipeline.market import fii_ingest


class _Resultado:
    def __init__(self, linhas):
        self._linhas = linhas

    def fetchall(self):
        return self._linhas


class _Conn:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, *a, **k):
        return _Resultado([("AAAA11", "11.111.111/0001-11")])


class _Engine:
    def connect(self):
        return _Conn()

    def begin(self):
        return _Conn()


def _preparar(monkeypatch, informe):
    gravadas = []
    monkeypatch.setattr(fii_ingest, "_engine", lambda: _Engine())
    monkeypatch.setattr(fii_ingest, "_schema_ready", lambda c: True)
    monkeypatch.setattr(cvm, "fetch_informe", informe)
    monkeypatch.setattr(
        cvm, "parse_informe_monthly",
        lambda data, year: {"11111111000111": [
            {"ref_month": date(year, 9, 1), "vpa": 10.0}]})
    monkeypatch.setattr(
        fii_ingest.repo, "upsert",
        lambda conn, tabela, linhas: gravadas.append((tabela, linhas)) or len(linhas))
    return gravadas


def test_cadeia_fii_traz_a_serie_mensal_antes_da_brapi():
    from core import publicacao_agenda as pa
    comandos = [p[1] for p in pa._CADEIA_FII if p[0] == "run_market_ingest.py"]
    assert "fiis-metrics" in comandos
    # `fiis` sai com 1 quando a cota da brapi esgota e aborta o resto da
    # cadeia: a série da CVM, que não depende da brapi, tem de vir antes.
    assert comandos.index("fiis-cvm") < comandos.index("fiis-metrics") < comandos.index("fiis")
    # e o arquivo da série mensal é publicado depois da cadeia que a carrega
    chaves = [a.chave for a in pa.ALVOS]
    assert chaves.index("fii_ingest") < chaves.index("fii_metrics_monthly")
    assert chaves.index("fii_ingest") < chaves.index("valuation_historico")


def test_cvm_fora_do_ar_nao_e_serie_renovada(monkeypatch):
    gravadas = _preparar(monkeypatch, lambda year: None)
    rep = fii_ingest.backfill_metrics_monthly()
    assert rep["anos"] == [] and rep["erros"] == -1
    assert "CVM" in rep["motivo"]
    assert gravadas == []


def test_um_ano_lido_basta_e_grava_so_ticker_conhecido(monkeypatch):
    pedidos = []
    # so o segundo ano pedido responde (o do ano corrente ainda nao existe na
    # CVM nos primeiros dias de janeiro)
    gravadas = _preparar(
        monkeypatch,
        lambda year: pedidos.append(year) or (b"zip" if len(pedidos) == 2 else None))
    rep = fii_ingest.backfill_metrics_monthly()
    assert rep["erros"] == 0 and len(rep["anos"]) == 1
    assert len(gravadas) == 1 and gravadas[0][0] == "fii_metrics_monthly"
    assert [linha["ticker"] for linha in gravadas[0][1]] == ["AAAA11"]
    assert rep["gravados"] == 1


def test_runner_sai_com_1_e_nao_carimba_metodologia(monkeypatch):
    import run_market_ingest
    carimbos = []
    monkeypatch.setattr(fii_ingest, "backfill_metrics_monthly",
                        lambda: {"anos": [], "linhas": 0, "gravados": 0, "erros": -1})
    monkeypatch.setattr(fii_ingest, "snapshot_methodology_v4",
                        lambda: carimbos.append(1) or {"status": "saved"})
    monkeypatch.setattr(sys, "argv", ["run_market_ingest.py", "fiis-metrics"])
    assert run_market_ingest.main() == 1
    assert carimbos == []
