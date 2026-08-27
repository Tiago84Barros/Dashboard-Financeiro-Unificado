# -*- coding: utf-8 -*-
"""A-155: a terceira qualidade de disponibilidade, a unica que sustenta backtest.

`migration_baseline` diz "nao sei quando ficou disponivel". `first_seen_proxy`
diz "foi a primeira vez que EU vi" -- mede o dia em que o ETL rodou. Se o ETL
rodou hoje, o proxy afirma que o balanco de 2019 ficou disponivel hoje, e o
backtest compra o exercicio inteiro no dia 1o de janeiro dele.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone

import data_pipeline.market.repository as repo


class _Result:
    def __init__(self, valor=None, linhas=()):
        self._valor, self._linhas = valor, linhas
        self.rowcount = 0

    def scalar(self):
        return self._valor

    def fetchall(self):
        return list(self._linhas)


class _Conn:
    """Responde por trecho de SQL; guarda o payload do INSERT final."""

    def __init__(self, *, tem_publicacao=True, publicacoes=(), first_seen=(),
                 cutoff=None):
        self.tem_publicacao = tem_publicacao
        self.publicacoes, self.first_seen, self.cutoff = publicacoes, first_seen, cutoff
        self.payload = None

    def execute(self, stmt, params=None):
        sql = str(stmt)
        if "calculated_metric_vintages') IS NOT NULL" in sql:
            return _Result(True)
        if "cvm_filing_publications') IS NOT NULL" in sql:
            return _Result(self.tem_publicacao)
        if "FROM market.cvm_filing_publications p" in sql:
            return _Result(linhas=self.publicacoes)
        if "MAX(first_seen_at)" in sql:
            return _Result(linhas=self.first_seen)
        if "pipeline_cutovers') IS NOT NULL" in sql:
            return _Result(self.cutoff is not None)
        if "SELECT cutoff_at" in sql:
            return _Result(self.cutoff)
        self.payload = json.loads(params["payload"])
        return _Result()


def _linha(ticker="ELET3", year=2019):
    return {"ticker": ticker, "period": "annual", "year": year, "quarter": 0,
            "metric_name": "roe", "metric_value": 1.0,
            "calculation_method": "m", "source": "s", "confidence_score": 80}


def _rodar(conn, rows=None):
    repo.append_metric_vintages(conn, rows or [_linha()])
    return conn.payload[0]


_HOJE = datetime(2026, 8, 27, tzinfo=timezone.utc)
_ONTEM = datetime(2015, 1, 1, tzinfo=timezone.utc)


def test_dt_receb_da_cvm_vira_published_at():
    conn = _Conn(publicacoes=[("ELET3", 2019, date(2020, 3, 30))])
    saida = _rodar(conn)
    assert saida["availability_quality"] == "published_at"
    assert saida["available_at"].startswith("2020-03-30")


def test_a_publicacao_vence_o_proxy_mesmo_sendo_mais_tardia():
    """Reapresentacao adia a disponibilidade. Adiantar e o erro que o PIT pega."""
    conn = _Conn(publicacoes=[("ELET3", 2019, date(2021, 8, 11))],
                 first_seen=[("ELET3", 2019, _ONTEM)], cutoff=_ONTEM)
    saida = _rodar(conn)
    assert saida["availability_quality"] == "published_at"
    assert saida["available_at"].startswith("2021-08-11")


def test_sem_linha_na_cvm_o_proxy_continua_valendo_e_se_declara():
    conn = _Conn(publicacoes=[], first_seen=[("ELET3", 2019, _HOJE)], cutoff=_ONTEM)
    assert _rodar(conn)["availability_quality"] == "first_seen_proxy"


def test_banco_sem_a_migration_052_continua_operando():
    """Tabela ausente nao pode derrubar a ingestao; degrada para o proxy."""
    conn = _Conn(tem_publicacao=False, first_seen=[("ELET3", 2019, _HOJE)],
                 cutoff=_ONTEM)
    assert _rodar(conn)["availability_quality"] == "first_seen_proxy"


def test_publicacao_de_outro_exercicio_nao_serve():
    """Ano errado seria pior que proxy: afirma uma data que nao e daquele dado."""
    conn = _Conn(publicacoes=[("ELET3", 2020, date(2021, 3, 30))],
                 first_seen=[("ELET3", 2019, _HOJE)], cutoff=_ONTEM)
    assert _rodar(conn)["availability_quality"] == "first_seen_proxy"


def test_publicacao_de_outro_ticker_nao_serve():
    conn = _Conn(publicacoes=[("PETR4", 2019, date(2020, 3, 30))],
                 first_seen=[("ELET3", 2019, _HOJE)], cutoff=_ONTEM)
    assert _rodar(conn)["availability_quality"] == "first_seen_proxy"


def test_metrica_trimestral_nao_recebe_data_de_dfp_anual():
    conn = _Conn(publicacoes=[("ELET3", 2019, date(2020, 3, 30))])
    linha = {**_linha(), "period": "quarterly", "quarter": 3}
    saida = _rodar(conn, [linha])
    assert saida["availability_quality"] != "published_at"
    assert saida["available_at"] is None
