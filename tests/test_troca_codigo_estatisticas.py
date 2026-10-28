"""Estatística da fita com troca de código pendente (ELET3 -> AXIA3).

Enquanto o histórico não foi migrado, a mesma companhia tem a fita dividida
em dois códigos. Mediana de giro e contagem de exercícios não se dobram
depois de prontas — a mediana de 5 meses e a de 1 mês não dão a dos 6, e 8
anos sob um código e 3 sob o outro são 11, não max(8, 3). Estes testes rodam o
SQL de verdade num banco descartável do armazém local.
"""
from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, text

from core import b3_troca_codigo, market_read
from views.portfolio_b3 import _PREGOES_MES, _load_adtv, _load_volume_mensal_historico

_TROCA = (("ELET3", "AXIA3"),)

_DDL = """
CREATE SCHEMA market;
CREATE TABLE market.historical_prices (
    ticker text, date date, close numeric, adjusted_close numeric, volume numeric,
    UNIQUE (ticker, date));
CREATE TABLE market.income_statements (
    ticker text, period text, year int, UNIQUE (ticker, period, year));
"""


@pytest.fixture
def banco():
    try:
        from scripts.publish_fii_selection_from_local import _warehouse_url
        url = _warehouse_url()
    except Exception as erro:  # noqa: BLE001 - sem armazém, não medimos
        pytest.skip(f"sem armazém local ({erro})")
    nome = f"teste_troca_est_{uuid.uuid4().hex[:10]}"
    administrador = create_engine(url, isolation_level="AUTOCOMMIT")
    try:
        with administrador.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{nome}"'))
    except Exception as erro:  # noqa: BLE001
        administrador.dispose()
        pytest.skip(f"armazém local não respondeu ({erro})")
    engine = create_engine(url.rsplit("/", 1)[0] + f"/{nome}")
    with engine.begin() as conn:
        conn.exec_driver_sql(_DDL)
    try:
        # market_read guarda o engine em st.cache_resource: sem trocar o dele,
        # o 2º teste leria o banco descartável do 1º, já apagado
        with patch("core.database.get_engine", return_value=engine), \
                patch.object(market_read, "_engine", return_value=engine):
            yield engine
    finally:
        engine.dispose()
        with administrador.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{nome}" WITH (FORCE)'))
        administrador.dispose()


def _barra(engine, ticker, meses_atras, giro):
    """Uma barra mensal (close × volume = ``giro``) no 1º dia de um mês recente."""
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO market.historical_prices (ticker, date, close, volume)
            VALUES (:t, (date_trunc('month', CURRENT_DATE)
                         - make_interval(months => :k))::date, 1, :g)"""),
                     {"t": ticker, "k": meses_atras, "g": giro})


def _pregao(engine, ticker, data, close, volume):
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO market.historical_prices (ticker, date, close, volume) "
                          "VALUES (:t, :d, :c, :v)"),
                     {"t": ticker, "d": data, "c": close, "v": volume})


def _exercicios(engine, ticker, anos):
    with engine.begin() as conn:
        for ano in anos:
            conn.execute(text("INSERT INTO market.income_statements "
                              "VALUES (:t, 'annual', :a)"), {"t": ticker, "a": ano})


def test_adtv_tira_a_mediana_da_fita_unida(banco):
    # 5 meses de R$ 210 mi sob o código antigo + 1 de R$ 21 mi sob o atual
    for k in range(1, 6):
        _barra(banco, "ELET3", k, 210e6)
    _barra(banco, "AXIA3", 0, 21e6)
    _barra(banco, "PETR4.SA", 0, 42e6)

    unido = _load_adtv.__wrapped__(trocas=_TROCA)
    assert unido["AXIA3"] == pytest.approx(210e6 / _PREGOES_MES)   # R$ 10 mi/dia
    assert "ELET3" not in unido
    assert unido["PETR4"] == pytest.approx(42e6 / _PREGOES_MES)

    # sem a troca, cada código fica com a própria fita (o default não muda)
    separado = _load_adtv.__wrapped__()
    assert separado["AXIA3"] == pytest.approx(1e6)
    assert separado["ELET3"] == pytest.approx(10e6)


def test_adtv_na_mesma_data_fica_a_barra_do_codigo_atual(banco):
    # a migração descarta a linha do antigo que colide; a leitura faz igual,
    # senão o mês da troca contaria o giro duas vezes
    for k in range(0, 3):
        _barra(banco, "ELET3", k, 999e6)
        _barra(banco, "AXIA3", k, 21e6)

    assert _load_adtv.__wrapped__(trocas=_TROCA)["AXIA3"] == pytest.approx(1e6)


def test_giro_diario_tira_a_mediana_da_fita_unida(banco):
    for k in range(1, 6):
        _barra(banco, "ELET3", k, 210e6)
    _barra(banco, "AXIA3", 0, 21e6)
    _barra(banco, "ELET3", 0, 999e6)   # colide com a do atual: sai

    giro = market_read.load_giro_diario.__wrapped__(trocas=_TROCA)
    assert giro["AXIA3"] == pytest.approx(210e6 / market_read._PREGOES_POR_MES)
    assert "ELET3" not in giro
    assert market_read.load_giro_diario.__wrapped__()["AXIA3"] == pytest.approx(1e6)


def test_volume_mensal_soma_o_mes_da_troca_nas_duas_fitas(banco):
    # a elegibilidade por época tira a mediana destes meses: no mês da troca
    # o pregão do antigo e o do atual são dias diferentes e somam
    _pregao(banco, "ELET3", "2025-02-27", 30, 0.7e6)    # R$ 21 mi, mês só do antigo
    _pregao(banco, "ELET3", "2025-03-03", 40, 0.6e6)    # R$ 24 mi
    _pregao(banco, "ELET3", "2025-03-14", 45, 0.4e6)    # R$ 18 mi
    _pregao(banco, "AXIA3", "2025-03-17", 42, 0.5e6)    # R$ 21 mi
    _pregao(banco, "ELET3", "2025-04-01", 99, 1e6)      # colide com a do atual: sai
    _pregao(banco, "AXIA3", "2025-04-01", 50, 1.26e6)   # R$ 63 mi

    def _meses(df):
        return {(r.ticker, str(r.mes)): (float(r.financeiro), r.fechamento)
                for r in df.itertuples()}

    unido = _load_volume_mensal_historico.__wrapped__(2025, trocas=_TROCA)
    assert _meses(unido) == {
        ("AXIA3", "2025-02-01"): (pytest.approx(21e6), 30),
        ("AXIA3", "2025-03-01"): (pytest.approx(63e6), 42),
        ("AXIA3", "2025-04-01"): (pytest.approx(63e6), 50),
    }

    # sem a troca, cada código fica com o próprio mês; dobrar esses totais
    # prontos (o caminho antigo) deixava o mês da troca com R$ 21 mi
    separado = _load_volume_mensal_historico.__wrapped__(2025)
    assert _meses(separado)[("ELET3", "2025-03-01")][0] == pytest.approx(42e6)
    dobrado = b3_troca_codigo.dobrar_linhas(separado, dict(_TROCA))
    assert _meses(dobrado)[("AXIA3", "2025-03-01")][0] == pytest.approx(21e6)


def test_anos_de_balanco_somam_as_duas_fitas(banco):
    _exercicios(banco, "ELET3", range(2014, 2022))        # 8 exercícios
    _exercicios(banco, "AXIA3", [2021, 2022, 2023, 2024])  # 2021 nos dois: conta 1
    _exercicios(banco, "PETR4", range(2010, 2025))

    anos = market_read.load_historico_anos.__wrapped__(trocas=_TROCA)
    assert anos["AXIA3"] == 11
    assert "ELET3" not in anos
    assert anos["PETR4"] == 15
    assert market_read.load_historico_anos.__wrapped__() == {
        "ELET3": 8, "AXIA3": 4, "PETR4": 15}
