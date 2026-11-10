"""Gêmeo de us_read._latest_shares em core.us_metrics e frame completo (10/11/2026).

Dados sintéticos. O frame é exercido de ponta a ponta com o banco substituído,
porque um nome indefinido em load_scoring_frame só foi pego por revisão.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date

import pandas as pd
import pytest

import core.us_read as ur
from core.us_metrics import acoes_em_circulacao, compute_company_metrics

SPLIT = [(date(2024, 2, 26), 3, 1)]
PRECO_EM = date(2026, 11, 6)


def _bal(ano, acoes, ref, entrega):
    return {"fiscal_year": ano, "shares_outstanding": acoes,
            "reference_date": ref, "available_at": entrega}


CASOS = {
    "velho": [_bal(2012, 3_418e6, date(2012, 1, 31), date(2012, 3, 27))],
    "split_depois": [_bal(2023, 2_700e6, date(2024, 1, 31), date(2024, 2, 20))],
    "recente": [_bal(2025, 500e6, date(2026, 3, 31), date(2026, 5, 10))],
    "split_ambiguo": [_bal(2023, 2_700e6, date(2024, 1, 31), date(2024, 3, 20))],
}


@pytest.mark.parametrize("nome", CASOS)
def test_us_metrics_e_us_read_dao_a_mesma_contagem(nome):
    rows = CASOS[nome]
    a = acoes_em_circulacao(rows, None, preco_em=PRECO_EM, splits=SPLIT)
    b = ur._latest_shares(rows, PRECO_EM, SPLIT)
    assert a == b


def test_acoes_velhas_demais_saem_nulas_mesmo_com_informado():
    rows = CASOS["velho"]
    assert acoes_em_circulacao(rows, 3_418e6, preco_em=PRECO_EM, splits=SPLIT) is None


def test_split_depois_da_entrega_e_ajustado():
    v = acoes_em_circulacao(CASOS["split_depois"], None, preco_em=date(2024, 11, 1),
                            splits=SPLIT)
    assert v == pytest.approx(8_100e6)


def test_derivado_nao_usa_contagem_crua_quando_a_idade_reprova():
    inc = [{"fiscal_year": 2012, "revenue": 1e9}]
    m = compute_company_metrics(inc, CASOS["velho"], [], price=100.0,
                                preco_em=PRECO_EM, splits=SPLIT)
    assert m.get("_market_cap") is None


# ---- frame completo -------------------------------------------------------

class _Eng:
    @contextmanager
    def connect(self):
        yield object()


def _fake_read_sql(q, conn, params=None):
    sql = str(q)
    if "FROM market_us.companies" in sql:
        return pd.DataFrame({"id": [1, 2], "symbol": ["NEW", "OLD"],
                             "name": ["N", "O"], "sector": ["S", "S"],
                             "industry": ["I", "I"]})
    if "income_statements" in sql:
        return pd.DataFrame([
            {"company_id": c, "fiscal_year": 2025, "revenue": 1e9, "net_income": 1e8}
            for c in (1, 2)])
    if "balance_sheets" in sql:
        return pd.DataFrame([
            {"company_id": 1, "fiscal_year": 2025, "shares_outstanding": 500e6,
             "reference_date": date(2026, 3, 31), "available_at": date(2026, 5, 10)},
            {"company_id": 2, "fiscal_year": 2012, "shares_outstanding": 3_418e6,
             "reference_date": date(2012, 1, 31), "available_at": date(2012, 3, 27)}])
    if "cash_flow_statements" in sql:
        return pd.DataFrame(columns=["company_id", "fiscal_year"])
    if "market_cap_history" in sql:
        return pd.DataFrame(columns=["symbol", "market_cap", "mc_date"])
    return pd.DataFrame({"symbol": ["NEW", "OLD"], "px": [10.0, 10.0],
                         "px_date": [PRECO_EM, PRECO_EM]})


def test_frame_completo_roda_e_aplica_idade_das_acoes(monkeypatch):
    monkeypatch.setattr(ur, "_engine", lambda: _Eng())
    monkeypatch.setattr(ur, "schema_ready", lambda: True)
    monkeypatch.setattr(ur.pd, "read_sql", _fake_read_sql)
    monkeypatch.setattr(ur, "_splits_por_simbolo", lambda conn: {})
    df = ur.load_scoring_frame().set_index("symbol")
    assert df.loc["NEW", "_market_cap"] == pytest.approx(5e9)
    assert pd.isna(df.loc["OLD", "_market_cap"])
