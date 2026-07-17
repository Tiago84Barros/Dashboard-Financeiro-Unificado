"""Testes do score de assimetria (Empresas Fora da Curva)."""
import pytest

import core.us_asymmetry as ua


def test_build_trajectory():
    income = [{"fiscal_year": y, "revenue": r, "operating_income": oi}
              for y, r, oi in [(2020, 1000, 100), (2021, 1200, 150),
                               (2022, 1500, 225), (2023, 2000, 340)]]
    balance = [{"fiscal_year": 2020, "shares_outstanding": 100},
               {"fiscal_year": 2023, "shares_outstanding": 95}]
    cashflow = [{"fiscal_year": y, "free_cash_flow": f, "stock_based_compensation": s}
                for y, f, s in [(2020, 50, 40), (2021, 80, 60),
                                (2022, 120, 80), (2023, 180, 100)]]
    t = ua.build_trajectory(income, balance, cashflow)
    assert t["op_margin_trend"] == pytest.approx(0.17 - 0.10)   # margem expandiu
    assert t["revenue_growth_persistence"] == 1.0               # todo ano cresceu
    assert t["shares_change"] == pytest.approx(-0.05)           # recompra
    assert t["sbc_to_revenue"] == pytest.approx(100 / 2000)
    assert t["fcf_positive_ratio"] == 1.0


def test_score_asymmetry_alta():
    m = {"revenue_cagr_3y": 0.28, "revenue_cagr_5y": 0.20, "roic": 0.22,
         "net_debt_ebitda": 0.5, "_fcf": 180, "fcf_cagr_3y": 0.30, "_market_cap": 1e9}
    t = {"op_margin_trend": 0.06, "revenue_growth_persistence": 1.0,
         "shares_change": -0.05, "sbc_to_revenue": 0.05, "fcf_positive_ratio": 1.0}
    r = ua.score_asymmetry(m, t)
    assert r["asymmetry_score"] >= 70
    assert r["risk_class"] == "média"
    assert r["confidence"] == 100
    assert r["stage"] in ("early", "scaling")
    assert r["suggested_position_pct"] <= 3.0
    assert any("Crescimento" in s for s in r["positive_signals"])
    assert r["risks"] == []


def test_score_asymmetry_baixa_com_riscos():
    m = {"revenue_cagr_3y": 0.30, "revenue_cagr_5y": 0.10, "roic": 0.03,
         "net_debt_ebitda": 4.0, "_fcf": -50, "fcf_cagr_3y": None, "_market_cap": 5e8}
    t = {"op_margin_trend": -0.05, "revenue_growth_persistence": 0.5,
         "shares_change": 0.30, "sbc_to_revenue": 0.25, "fcf_positive_ratio": 0.0}
    r = ua.score_asymmetry(m, t)
    assert r["risk_class"] == "muito alta"
    assert len(r["risks"]) >= 3
    assert "Crescimento sem retorno sobre capital" in r["risks"]
    assert r["asymmetry_score"] < r["confidence"]   # penalizado pelos riscos


def test_confidence_reflete_dados_faltantes():
    m = {"revenue_cagr_3y": None, "revenue_cagr_5y": None, "roic": None,
         "net_debt_ebitda": None, "_fcf": None}
    r = ua.score_asymmetry(m, {})
    assert r["confidence"] == 0
    assert set(r["missing_data"]) == {"revenue_cagr_3y", "revenue_cagr_5y",
                                      "roic", "net_debt_ebitda", "fcf"}


def test_classify_stage():
    assert ua.classify_stage({"revenue_cagr_3y": 0.35, "_market_cap": 1e9}, {}) == "early"
    assert ua.classify_stage({"revenue_cagr_3y": 0.22, "_fcf": 10}, {}) == "scaling"
    assert ua.classify_stage({"revenue_cagr_3y": 0.05}, {}) == "mature"
