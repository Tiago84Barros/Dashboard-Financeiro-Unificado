"""P/L spot do título (12/11/2026).

Na ação de companhia com unit, a brapi põe no topo do quote o LPA da unit
(``earningsPerShare``) e o ``priceEarnings`` = preço ÷ esse LPA. TAEE3 gravava
P/L 2,76 com LPA 1,573 e preço 13,20 (13,20 ÷ 1,573 = 8,39, o TTM do armazém).
"""
import pytest

import data_pipeline.market.normalize as nz
from core import brapi

# Números reais do armazém em 12/11/2026.
_TAEE3 = {
    "symbol": "TAEE3", "regularMarketPrice": 13.20, "priceEarnings": 2.76152933,
    "earningsPerShare": 4.7183692,
    "defaultKeyStatistics": {"trailingEps": 1.57278973, "priceToBook": 1.6864446},
}
_TAEE11 = {
    "symbol": "TAEE11", "regularMarketPrice": 39.74, "priceEarnings": 8.42240153,
    "earningsPerShare": 4.7183692,
    "defaultKeyStatistics": {"trailingEps": 4.7183692},
}


def _by(rows):
    return {r["metric_name"]: r for r in rows}


def test_acao_de_companhia_com_unit_usa_o_lpa_da_acao():
    by = _by(nz.metric_rows(_TAEE3))
    assert by["P/L"]["metric_value"] == pytest.approx(13.20 / 1.57278973)
    assert by["P/L"]["metric_value"] == pytest.approx(8.39, abs=0.01)
    assert by["P/L"]["calculation_method"] == brapi.METODO_PL_DO_TITULO
    assert by["LPA"]["metric_value"] == pytest.approx(1.57278973)


def test_p_l_vezes_lpa_fecha_com_o_preco():
    for q in (_TAEE3, _TAEE11):
        by = _by(nz.metric_rows(q))
        assert (by["P/L"]["metric_value"] * by["LPA"]["metric_value"]
                == pytest.approx(q["regularMarketPrice"]))


def test_unit_fica_como_a_brapi_dava():
    by = _by(nz.metric_rows(_TAEE11))
    assert by["P/L"]["metric_value"] == pytest.approx(8.42, abs=0.01)


def test_quote_diario_converte_o_lpa_da_unit_por_k():
    """Sem defaultKeyStatistics no diário: o LPA do topo (o da unit) é
    reconhecido pelo trailingEps do completo e dividido por k = 3, sem
    trocar o número da data do quote pelo de outra data."""
    diario = {k: v for k, v in _TAEE3.items() if k != "defaultKeyStatistics"}
    comp = {"sharesOutstanding": 1_033_496_721.0, "trailingEps": 1.6,
            "netIncomeToCommon": 1_650_000_000.0}
    by = _by(nz.metric_rows(diario, composicao=comp))
    assert by["LPA"]["metric_value"] == pytest.approx(4.7183692 / 3)
    assert by["P/L"]["metric_value"] == pytest.approx(13.20 / (4.7183692 / 3))


def test_quote_diario_coerente_fica_com_o_lpa_do_proprio_quote():
    diario = {"symbol": "PETR4", "regularMarketPrice": 40.0,
              "priceEarnings": 5.0, "earningsPerShare": 8.0}
    comp = {"sharesOutstanding": 1.0e10, "trailingEps": 7.5,
            "netIncomeToCommon": 7.5e10}
    by = _by(nz.metric_rows(diario, composicao=comp))
    assert by["LPA"]["metric_value"] == 8.0
    assert by["P/L"]["metric_value"] == 5.0


def test_destoa_sem_k_inteiro_nao_grava_lpa_nem_o_p_l_da_brapi():
    diario = {"symbol": "XPTO3", "regularMarketPrice": 10.0,
              "priceEarnings": 4.0, "earningsPerShare": 2.5}
    comp = {"sharesOutstanding": 1.0e6, "trailingEps": 1.0,
            "netIncomeToCommon": 1.0e6}
    by = _by(nz.metric_rows(diario, composicao=comp))
    assert "LPA" not in by and "P/L" not in by


def test_lpa_negativo_mantem_o_campo_da_brapi():
    q = {"symbol": "XPTO3", "regularMarketPrice": 10.0, "priceEarnings": -5.0,
         "earningsPerShare": -2.0, "defaultKeyStatistics": {"trailingEps": -2.0}}
    by = _by(nz.metric_rows(q))
    assert by["P/L"]["metric_value"] == -5.0
    assert by["P/L"]["calculation_method"] == brapi.METODO_PL_DA_BRAPI


def test_sem_preco_nao_usa_o_priceearnings_de_outro_titulo():
    q = {k: v for k, v in _TAEE3.items() if k != "regularMarketPrice"}
    assert brapi.pl_do_titulo(q) == (None, brapi.METODO_PL_DA_BRAPI)


def test_gemeo_current_fundamentals_usa_a_mesma_regra():
    """``core.data_healing`` vota com este P/L: tem de ser o do normalizador."""
    for q in (_TAEE3, _TAEE11):
        pl_norm = _by(nz.metric_rows(q))["P/L"]["metric_value"]
        assert brapi.current_fundamentals(q)["P/L"] == pytest.approx(pl_norm)


def test_data_healing_pede_o_modulo_do_lpa(monkeypatch):
    import core.data_healing as dh
    pedidos = []

    def falso(tk, **kw):
        pedidos.append(kw)
        return dict(_TAEE3)

    import pandas as pd

    from core import b3_db, fundamentus, status_invest
    monkeypatch.setattr(brapi, "fetch_quote", falso)
    monkeypatch.setattr(b3_db, "load_multiplos_todos", lambda: pd.DataFrame())
    monkeypatch.setattr(fundamentus, "batch_stocks", lambda tks: {})
    monkeypatch.setattr(status_invest, "fetch_stock_si", lambda tk: {})
    out = dh._collect_sources(("TAEE3",))
    assert "defaultKeyStatistics" in pedidos[0]["modules"]
    assert out["TAEE3"]["brapi"]["P/L"] == pytest.approx(8.39, abs=0.01)
