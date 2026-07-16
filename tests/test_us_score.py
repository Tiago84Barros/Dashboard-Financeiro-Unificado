"""Testes do score fundamentalista relativo por indústria."""
import pandas as pd
import pytest

import core.us_score as sc


def _frame():
    # 5 empresas, mesma indústria; A domina, E é a pior.
    base = {"sector": "Technology", "industry": "Software"}
    rows = [
        {"symbol": "A", **base, "gross_margin": 0.80, "operating_margin": 0.35,
         "net_margin": 0.28, "roe": 0.30, "roic": 0.25, "revenue_cagr_3y": 0.30,
         "net_debt_ebitda": 0.2, "pe": 12, "fcf_yield": 0.08, "shareholder_yield": 0.04},
        {"symbol": "B", **base, "gross_margin": 0.65, "operating_margin": 0.25,
         "net_margin": 0.18, "roe": 0.22, "roic": 0.18, "revenue_cagr_3y": 0.20,
         "net_debt_ebitda": 1.0, "pe": 18, "fcf_yield": 0.05, "shareholder_yield": 0.02},
        {"symbol": "C", **base, "gross_margin": 0.55, "operating_margin": 0.18,
         "net_margin": 0.12, "roe": 0.15, "roic": 0.12, "revenue_cagr_3y": 0.10,
         "net_debt_ebitda": 2.0, "pe": 25, "fcf_yield": 0.03, "shareholder_yield": 0.01},
        {"symbol": "D", **base, "gross_margin": 0.45, "operating_margin": 0.10,
         "net_margin": 0.06, "roe": 0.09, "roic": 0.07, "revenue_cagr_3y": 0.04,
         "net_debt_ebitda": 3.5, "pe": 35, "fcf_yield": 0.01, "shareholder_yield": 0.0},
        {"symbol": "E", **base, "gross_margin": 0.30, "operating_margin": 0.02,
         "net_margin": -0.02, "roe": 0.01, "roic": 0.01, "revenue_cagr_3y": -0.05,
         "net_debt_ebitda": 5.0, "pe": 60, "fcf_yield": -0.02, "shareholder_yield": 0.0},
    ]
    return pd.DataFrame(rows)


def test_score_ordena_e_faixa_0_100():
    scored = sc.score_cross_section(_frame(), min_group=3)
    assert list(scored["symbol"])[0] == "A"        # melhor no topo
    assert list(scored["symbol"])[-1] == "E"        # pior no fim
    assert scored["score"].between(0, 100).all()
    assert scored.iloc[0]["score"] > scored.iloc[-1]["score"]


def test_lower_is_better_invertido():
    # menor P/L e menor alavancagem devem ajudar valuation/solidez de A
    scored = sc.score_cross_section(_frame(), min_group=3)
    a = scored[scored["symbol"] == "A"].iloc[0]
    e = scored[scored["symbol"] == "E"].iloc[0]
    assert a["score_valuation"] > e["score_valuation"]
    assert a["score_solidity"] > e["score_solidity"]


def test_missing_neutro_nao_quebra():
    df = _frame()
    df.loc[df["symbol"] == "C", "roic"] = None       # ausência
    scored = sc.score_cross_section(df, min_group=3)
    assert not scored.empty and scored["score"].notna().all()


def test_empty_frame():
    out = sc.score_cross_section(pd.DataFrame())
    assert out.empty


def test_industry_comparison():
    scored = sc.score_cross_section(_frame(), min_group=3)
    peers = sc.industry_comparison(scored, "Software")
    assert len(peers) == 5 and list(peers["symbol"])[0] == "A"
    assert sc.industry_comparison(scored, "Inexistente").empty


def test_weights_renormalizam_e_override_setor():
    w = sc._weights_for("Real Estate")
    assert sum(w.values()) == pytest.approx(1.0)
    assert w["shareholder"] > sc.DEFAULT_TRACK_WEIGHTS["shareholder"]  # REIT dá mais peso
    w2 = sc._weights_for(None)
    assert sum(w2.values()) == pytest.approx(1.0)
