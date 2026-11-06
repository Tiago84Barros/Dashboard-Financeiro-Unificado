"""Testes dos helpers PIT (visible_rows, forward returns, painel anual)."""
from datetime import date

import pandas as pd
import pytest

import data_pipeline.us.scoring_history as sh


def test_visible_rows_filtra_por_available_at():
    rows = [
        {"fiscal_year": 2020, "available_at": date(2021, 3, 1), "revenue": 100},
        {"fiscal_year": 2021, "available_at": date(2022, 3, 1), "revenue": 120},
        {"fiscal_year": 2022, "available_at": None, "revenue": 140},   # sem data → oculto
    ]
    vis = sh.visible_rows(rows, date(2021, 6, 30))
    assert [r["fiscal_year"] for r in vis] == [2020]   # 2021 ainda não publicado; None oculto
    vis2 = sh.visible_rows(rows, date(2022, 6, 30))
    assert [r["fiscal_year"] for r in vis2] == [2020, 2021]


def test_forward_returns_from_monthly():
    monthly = pd.DataFrame({
        "symbol": ["A", "A", "A"],
        "month_end": ["2020-01-31", "2020-02-29", "2020-03-31"],
        "adjusted_close": [100.0, 110.0, 121.0],
    })
    fwd = sh.forward_returns_from_monthly(monthly)
    assert len(fwd) == 2
    assert fwd.iloc[0]["fwd_return"] == pytest.approx(0.10)
    assert fwd.iloc[1]["fwd_return"] == pytest.approx(0.10)


def test_build_annual_panel():
    vint = pd.DataFrame({"as_of_date": ["2020-06-30"], "symbol": ["A"], "score": [75.0]})
    # preço em jun/2020 = 100; jun/2021 = 130 → fwd 12m = +30%
    monthly = pd.DataFrame({
        "symbol": ["A", "A"],
        "month_end": ["2020-06-30", "2021-06-30"],
        "adjusted_close": [100.0, 130.0],
    })
    panel = sh.build_annual_panel(vint, monthly, horizon_months=12)
    assert len(panel) == 1
    assert panel.iloc[0]["fwd_return"] == pytest.approx(0.30)
    assert panel.iloc[0]["score"] == 75.0


def test_build_annual_panel_sem_preco_futuro():
    vint = pd.DataFrame({"as_of_date": ["2020-06-30"], "symbol": ["A"], "score": [75.0]})
    monthly = pd.DataFrame({"symbol": ["A"], "month_end": ["2020-06-30"],
                            "adjusted_close": [100.0]})
    assert sh.build_annual_panel(vint, monthly).empty   # sem preço futuro → sem linha


def test_annual_asof_dates():
    ds = sh.annual_asof_dates(2020, 2022)
    assert ds == [date(2020, 6, 30), date(2021, 6, 30), date(2022, 6, 30)]


def test_filtro_por_simbolo_entra_na_mesma_sql_da_derivacao():
    # Uma SQL só, filtrada por substituição: uma cópia à parte divergiria na
    # primeira correção da derivação.
    from data_pipeline.us import scoring_history as sh

    deriva = sh._so_simbolos(sh._SQL_DERIVA_MENSAL,
                             "WHERE COALESCE(adjusted_close, close) IS NOT NULL",
                             "symbol")
    poda = sh._so_simbolos(sh._SQL_PODA_MES_PARCIAL,
                           "WHERE p.source = 'derived'", "p.symbol")
    assert "IS NOT NULL AND symbol IN :simbolos" in deriva
    assert "'derived' AND p.symbol IN :simbolos" in poda
    assert deriva.replace(" AND symbol IN :simbolos", "") == sh._SQL_DERIVA_MENSAL
