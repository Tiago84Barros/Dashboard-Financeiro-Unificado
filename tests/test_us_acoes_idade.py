"""Contagem de ações com idade máxima e ajuste de desdobramento (A2/A3, 10/11/2026).

Dados sintéticos. WMT real tinha ações só de 2012 e split 3:1 em 2024.
"""
from __future__ import annotations

from datetime import date

import pytest

from core.us_acoes import MAX_IDADE_ACOES_DIAS, acoes_na_base_do_preco, ultimas_acoes

SPLIT_3_1 = [(date(2024, 2, 26), 3, 1)]


def test_acoes_de_2012_com_preco_de_2026_nao_valem():
    assert acoes_na_base_do_preco(
        3_418e6, date(2012, 1, 31), date(2012, 3, 27), date(2026, 11, 9),
        SPLIT_3_1) is None


def test_ultimas_acoes_velhas_viram_none_e_valor_de_mercado_nulo():
    rows = [{"fiscal_year": 2012, "shares_outstanding": 3_418e6,
             "reference_date": date(2012, 1, 31), "available_at": date(2012, 3, 27)}]
    assert ultimas_acoes(rows, date(2026, 11, 9), SPLIT_3_1) is None


def test_idade_limite_e_inclusiva_em_18_meses():
    ref = date(2025, 1, 1)
    ok = date.fromordinal(ref.toordinal() + MAX_IDADE_ACOES_DIAS)
    fora = date.fromordinal(ref.toordinal() + MAX_IDADE_ACOES_DIAS + 1)
    assert acoes_na_base_do_preco(100, ref, ref, ok) == 100
    assert acoes_na_base_do_preco(100, ref, ref, fora) is None


def test_split_posterior_a_entrega_ajusta_pelo_fator():
    # balanço de jan/2024 (pré-split), preço de nov/2024 (pós-split)
    v = acoes_na_base_do_preco(2_700e6, date(2024, 1, 31), date(2024, 2, 20),
                               date(2024, 11, 1), SPLIT_3_1)
    assert v == pytest.approx(8_100e6)


def test_split_entre_referencia_e_entrega_e_ambiguo_e_anula():
    # referencia 31/01, entrega 15/03, split 26/02: a capa pode ou nao estar pos-split
    assert acoes_na_base_do_preco(
        2_700e6, date(2024, 1, 31), date(2024, 3, 15), date(2024, 11, 1),
        SPLIT_3_1) is None


def test_sem_splits_e_balanco_recente_devolve_as_proprias_acoes():
    assert acoes_na_base_do_preco(100, date(2026, 6, 30), date(2026, 8, 1),
                                  date(2026, 11, 9), []) == 100


def test_sem_data_de_preco_falha_fechado():
    rows = [{"shares_outstanding": 5, "reference_date": date(2026, 6, 30),
             "available_at": date(2026, 8, 1)}]
    assert ultimas_acoes(rows, None) is None


def test_ausencia_de_acoes_nao_vira_zero():
    assert acoes_na_base_do_preco(None, date(2026, 6, 30), None, date(2026, 11, 9)) is None
    assert acoes_na_base_do_preco(0, date(2026, 6, 30), None, date(2026, 11, 9)) is None


# ── us_read: o valor de mercado derivado usa a regra ──────────────────────────
def test_latest_shares_do_us_read_aplica_idade_e_split():
    from core.us_read import _latest_shares

    velho = [{"fiscal_year": 2012, "shares_outstanding": 3_418e6,
              "reference_date": date(2012, 1, 31), "available_at": date(2012, 3, 27)}]
    assert _latest_shares(velho, date(2026, 11, 9), SPLIT_3_1) is None
    recente = [{"fiscal_year": 2023, "shares_outstanding": 2_700e6,
                "reference_date": date(2024, 1, 31), "available_at": date(2024, 2, 20)}]
    assert _latest_shares(recente, date(2024, 11, 1), SPLIT_3_1) == pytest.approx(8_100e6)


def test_market_cap_derivado_de_acoes_velhas_e_nulo():
    from sqlalchemy import create_engine, event, text
    from sqlalchemy.pool import StaticPool

    from core.us_read import _latest_market_cap

    motor = create_engine("sqlite://", poolclass=StaticPool)

    @event.listens_for(motor, "connect")
    def _anexa(dbapi_conn, _record):
        dbapi_conn.execute("ATTACH DATABASE ':memory:' AS market_us")

    with motor.begin() as conn:
        conn.execute(text(
            "CREATE TABLE market_us.prices_daily (symbol TEXT, date TEXT, close REAL, "
            "adjusted_close REAL)"))
        conn.execute(text(
            "INSERT INTO market_us.prices_daily VALUES ('WMT','2026-11-06',100.0,100.0)"))
        conn.execute(text(
            "CREATE TABLE market_us.market_cap_history (symbol TEXT, date TEXT, market_cap REAL)"))
        conn.execute(text(
            "CREATE TABLE market_us.splits (symbol TEXT, split_date TEXT, "
            "numerator REAL, denominator REAL)"))
        conn.execute(text(
            "INSERT INTO market_us.splits VALUES ('WMT','2024-02-26',3,1)"))
    bal = [{"fiscal_year": 2012, "shares_outstanding": 3_418e6,
            "reference_date": date(2012, 1, 31), "available_at": date(2012, 3, 27)}]
    with motor.connect() as conn:
        assert _latest_market_cap(conn, "WMT", bal, [{"revenue": 6e11}]) is None
    motor.dispose()


def test_historico_velho_sem_acoes_validas_nao_vira_valor_de_mercado():
    """P2 do Codex: acoes de 2012 (derivado ausente) + historico de 60 dias = None."""
    from sqlalchemy import create_engine, event, text
    from sqlalchemy.pool import StaticPool

    from core.us_read import _latest_market_cap

    motor = create_engine("sqlite://", poolclass=StaticPool)

    @event.listens_for(motor, "connect")
    def _anexa(dbapi_conn, _record):
        dbapi_conn.execute("ATTACH DATABASE ':memory:' AS market_us")

    with motor.begin() as conn:
        conn.execute(text("CREATE TABLE market_us.prices_daily (symbol TEXT, date TEXT, "
                          "close REAL, adjusted_close REAL)"))
        conn.execute(text("INSERT INTO market_us.prices_daily VALUES "
                          "('OLD','2026-11-06',100.0,100.0)"))
        conn.execute(text("CREATE TABLE market_us.market_cap_history "
                          "(symbol TEXT, date TEXT, market_cap REAL)"))
        conn.execute(text("INSERT INTO market_us.market_cap_history VALUES "
                          "('OLD','2026-09-07',100000000.0)"))
        conn.execute(text("CREATE TABLE market_us.splits (symbol TEXT, split_date TEXT, "
                          "numerator REAL, denominator REAL)"))
    bal = [{"fiscal_year": 2012, "shares_outstanding": 20e6,
            "reference_date": date(2012, 1, 31), "available_at": date(2012, 3, 27)}]
    inc = [{"fiscal_year": 2025, "revenue": 100e6}]
    with motor.connect() as conn:
        assert _latest_market_cap(conn, "OLD", bal, inc) is None
        # com acoes validas, o derivado vale mais que o publicado de 60 dias
        bal_ok = [{"fiscal_year": 2025, "shares_outstanding": 2e6,
                   "reference_date": date(2026, 6, 30), "available_at": date(2026, 8, 1)}]
        assert _latest_market_cap(conn, "OLD", bal_ok, inc) == pytest.approx(200e6)
    motor.dispose()
