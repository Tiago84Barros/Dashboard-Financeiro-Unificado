# -*- coding: utf-8 -*-
"""Qual safra de ``calculated_metric_vintages`` o histórico anual B3 lê.

Até 24/11/2026 a leitura ficava com o cálculo gravado PRIMEIRO
(``recorded_at ASC``). O reprocess de 12/11 regravou as métricas corrigidas
com o mesmo ``available_at`` das antigas, e a aba e as medições seguiram com
os números velhos. A regra agora: primeira publicação, cálculo mais novo dela.

Os testes de SQL rodam contra um Postgres qualquer, só com ``VALUES`` (nada é
gravado). Sem ``B3_TEST_DATABASE_URL`` eles são pulados: o CI não tem Postgres.

    DFU_TESTES_PERMITEM_REDE=1 B3_TEST_DATABASE_URL=postgresql://... pytest ...
"""
from __future__ import annotations

import os

import pandas as pd
import pytest

import core.market_read as mr

URL = os.environ.get("B3_TEST_DATABASE_URL")


def test_ordem_pega_a_primeira_publicacao_e_o_calculo_mais_novo():
    ordem = mr.ORDEM_DA_SAFRA_VIGENTE
    assert "recorded_at ASC" not in ordem
    assert ordem.index("migration_baseline") < ordem.index("available_at ASC")
    assert ordem.index("available_at ASC") < ordem.index("recorded_at DESC")


def test_leitor_anual_usa_a_regra_unica(monkeypatch):
    consultas: list[str] = []

    def _q(sql, params=None):
        consultas.append(sql)
        if "to_regclass" in sql:
            return pd.DataFrame({"ok": [True]})
        return pd.DataFrame()

    monkeypatch.setattr(mr, "_q_b3", _q)
    mr._annual_long("cm.ticker = :tk", {"tk": "AAAA3"})
    sql = consultas[-1]
    assert mr.ORDEM_DA_SAFRA_VIGENTE in sql
    assert "recorded_at ASC" not in sql


# (ticker, ano, métrica, valor, available_at, qualidade, recorded_at, confiança)
_LINHAS = [
    # 1) Mesma publicação, dois cálculos: vale o mais novo (o corrigido).
    ("AAAA3", 2014, "DY", 0.157, "2015-03-20", "published_at", "2026-07-10", 90),
    ("AAAA3", 2014, "DY", 0.024, "2015-03-20", "published_at", "2026-11-12", 90),
    # 2) Reapresentação depois: a primeira publicação segura a posição, com o
    #    cálculo mais novo DELA.
    ("BBBB3", 2016, "P/L", 10.0, "2017-03-25", "published_at", "2026-07-10", 90),
    ("BBBB3", 2016, "P/L", 11.0, "2017-03-25", "published_at", "2026-11-12", 90),
    ("BBBB3", 2016, "P/L", 30.0, "2018-08-01", "published_at", "2026-11-13", 90),
    # 3) Data real vence o baseline, mesmo o baseline sendo gravado depois.
    ("CCCC3", 2018, "ROE", 0.20, "2019-03-30", "first_seen_proxy", "2026-07-12", 90),
    ("CCCC3", 2018, "ROE", 0.90, "2026-07-06", "migration_baseline", "2026-11-12", 90),
    # 4) Só baseline: o cálculo mais novo, com available_at anulado.
    ("DDDD3", 2019, "ROE", 0.10, "2026-07-06", "migration_baseline", "2026-07-06", 90),
    ("DDDD3", 2019, "ROE", 0.12, "2026-07-06", "migration_baseline", "2026-11-12", 90),
    # 5) Confiança baixa não entra, mesmo sendo o cálculo mais novo.
    ("EEEE3", 2020, "DY", 0.05, "2021-03-20", "published_at", "2026-07-10", 90),
    ("EEEE3", 2020, "DY", 0.50, "2021-03-20", "published_at", "2026-11-12", 50),
]


def _fonte_values() -> str:
    linhas = ",\n".join(
        f"('{t}', 'annual', {a}, 0, '{m}', {v}::float8, TIMESTAMPTZ '{av}', '{q}', "
        f"TIMESTAMPTZ '{r}', {c})"
        for t, a, m, v, av, q, r, c in _LINHAS
    )
    return (
        "(SELECT * FROM (VALUES\n" + linhas + "\n) AS x(ticker, period, year, quarter, "
        "metric_name, metric_value, available_at, availability_quality, recorded_at, "
        "confidence_score)) AS safras"
    )


@pytest.fixture
def vigentes():
    if not URL:
        pytest.skip("sem B3_TEST_DATABASE_URL (Postgres para consulta só de leitura)")
    from sqlalchemy import create_engine, text

    engine = create_engine(URL)
    try:
        with engine.connect() as conn:
            linhas = conn.execute(text(mr.sql_safra_vigente(_fonte_values()))).mappings().all()
    finally:
        engine.dispose()
    return {(r["ticker"], r["metric_name"]): dict(r) for r in linhas}


def test_mesma_publicacao_vale_o_calculo_mais_novo(vigentes):
    assert vigentes[("AAAA3", "DY")]["metric_value"] == pytest.approx(0.024)


def test_reapresentacao_nao_desloca_a_primeira_publicacao(vigentes):
    linha = vigentes[("BBBB3", "P/L")]
    assert linha["metric_value"] == pytest.approx(11.0)
    assert str(linha["available_at"]).startswith("2017-03-25")


def test_data_real_vence_o_baseline(vigentes):
    linha = vigentes[("CCCC3", "ROE")]
    assert linha["metric_value"] == pytest.approx(0.20)
    assert linha["availability_quality"] == "first_seen_proxy"


def test_so_baseline_pega_o_mais_novo_sem_data(vigentes):
    linha = vigentes[("DDDD3", "ROE")]
    assert linha["metric_value"] == pytest.approx(0.12)
    assert linha["available_at"] is None


def test_confianca_baixa_fica_fora(vigentes):
    assert vigentes[("EEEE3", "DY")]["metric_value"] == pytest.approx(0.05)
