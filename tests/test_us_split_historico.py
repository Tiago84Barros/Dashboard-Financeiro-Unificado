"""Valor de mercado historico na mesma base do preco (split no meio do historico).

Dados sinteticos. O preco do armazem e retroajustado por desdobramento; as acoes
do balanco sao da epoca. Sem por as duas na mesma base, P/L, P/VP e P/FCF dos
anos anteriores ao split saem divididos pelo fator.
"""
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from core.us_acoes import acoes_base_atual, ultimas_acoes
from core.us_company_analysis import derive_metric_history
from data_pipeline.us.snapshot import compact_financials

SPLIT_10_1 = [(date(2024, 6, 10), 10, 1)]


def _series():
    inc = [{"fiscal_year": a, "revenue": 1e9, "net_income": ni}
           for a, ni in ((2023, 5e8), (2025, 5.5e8))]
    bal = [
        {"fiscal_year": 2023, "shares_outstanding": 100e6, "total_equity": 1e9,
         "reference_date": date(2023, 12, 31), "available_at": date(2024, 2, 20)},
        {"fiscal_year": 2025, "shares_outstanding": 1000e6, "total_equity": 1e9,
         "reference_date": date(2025, 12, 31), "available_at": date(2026, 2, 20)},
    ]
    cf = [{"fiscal_year": 2023, "free_cash_flow": 5e8, "dividends_paid": -1e8},
          {"fiscal_year": 2025, "free_cash_flow": 5.5e8, "dividends_paid": -1e8}]
    return inc, bal, cf


def _precos():
    # retroajustado: 2023 valia 500 na epoca, hoje aparece como 50
    return pd.DataFrame({"date": ["2023-12-29", "2025-12-31"], "price": [50.0, 55.0]})


def test_pl_fica_continuo_com_split_10_para_1_no_meio():
    inc, bal, cf = _series()
    fin = pd.DataFrame(compact_financials(inc, bal, cf, splits=SPLIT_10_1))
    h = derive_metric_history(fin, _precos()).set_index("fiscal_year")
    assert h.loc[2023, "pe"] == pytest.approx(100.0)
    assert h.loc[2025, "pe"] == pytest.approx(100.0)
    assert h.loc[2023, "p_fcf"] == pytest.approx(h.loc[2025, "p_fcf"])


def test_dps_fica_na_base_ajustada():
    inc, bal, cf = _series()
    fin = pd.DataFrame(compact_financials(inc, bal, cf, splits=SPLIT_10_1)
                       ).set_index("fiscal_year")
    assert fin.loc[2023, "dividends_per_share"] == pytest.approx(0.1)
    assert fin.loc[2025, "dividends_per_share"] == pytest.approx(0.1)


def test_split_entre_referencia_e_entrega_e_ambiguo_e_vira_ausencia():
    inc, bal, cf = _series()
    bal[0]["available_at"] = date(2024, 7, 1)      # entrega depois do split
    fin = pd.DataFrame(compact_financials(inc, bal, cf, splits=SPLIT_10_1))
    h = derive_metric_history(fin, _precos()).set_index("fiscal_year")
    assert pd.isna(h.loc[2023, "pe"])
    assert h.loc[2025, "pe"] == pytest.approx(100.0)


def test_sem_splits_informados_nada_muda():
    inc, bal, cf = _series()
    fin = pd.DataFrame(compact_financials(inc, bal, cf))
    assert "shares_outstanding_ajustada" not in fin


def test_equivalencia_com_a_contagem_do_us_read():
    _, bal, _ = _series()
    ultima = bal[-1]
    esperado = ultimas_acoes(bal, date(2026, 6, 1), SPLIT_10_1)
    assert acoes_base_atual(ultima, SPLIT_10_1) == pytest.approx(esperado)
    assert acoes_base_atual(bal[0], SPLIT_10_1) == pytest.approx(1000e6)


def test_caminho_do_armazem_e_o_da_vitrine_dao_a_mesma_serie():
    from core.us_read import _anexar_acoes_ajustadas

    inc, bal, cf = _series()
    vitrine = pd.DataFrame(compact_financials(inc, bal, cf, splits=SPLIT_10_1))
    armazem = pd.DataFrame([
        {"fiscal_year": b["fiscal_year"], "shares_outstanding": b["shares_outstanding"],
         "reference_date": b["reference_date"], "available_at": b["available_at"],
         "dividends_paid": c["dividends_paid"]} for b, c in zip(bal, cf)])
    armazem = _anexar_acoes_ajustadas(armazem, SPLIT_10_1)
    for col in ("shares_outstanding_ajustada", "dividends_per_share"):
        assert list(armazem[col]) == pytest.approx(list(vitrine[col]))


def test_vitrine_antiga_sem_a_coluna_continua_na_base_antiga(monkeypatch):
    import core.us_read as ur

    rows = [{"fiscal_year": 2023, "net_income": 5e8, "shares_outstanding": 100e6}]
    monkeypatch.setattr(ur, "_snapshot_json_for", lambda s, k: rows)
    df = ur.load_snapshot_financials("X")
    assert "shares_outstanding_ajustada" not in df
    h = derive_metric_history(df, pd.DataFrame(
        {"date": ["2023-12-29"], "price": [50.0]}))
    assert h.loc[0, "pe"] == pytest.approx(10.0)
    rows[0]["shares_outstanding_ajustada"] = None
    assert "shares_outstanding_ajustada" in ur.load_snapshot_financials("X")


def test_vitrine_antiga_pela_fachada_us_data_sai_preenchida(monkeypatch):
    import core.us_data as ud
    import core.us_read as ur

    rows = [{"fiscal_year": 2023, "net_income": 5e8, "shares_outstanding": 100e6}]
    monkeypatch.setattr(ud, "_use_snapshot", lambda: True)
    monkeypatch.setattr(ur, "_snapshot_json_for", lambda s, k: rows)
    fn = getattr(ud.company_financials, "__wrapped__", ud.company_financials)
    df = fn("X")
    h = derive_metric_history(df, pd.DataFrame(
        {"date": ["2023-12-29"], "price": [50.0]}))
    assert h.loc[0, "pe"] == pytest.approx(10.0)
