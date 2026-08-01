"""Testes das métricas fundamentalistas EUA (puras)."""
import pytest

import core.us_metrics as um


def test_safe_div():
    assert um.safe_div(10, 2) == 5
    assert um.safe_div(10, 0) is None      # denominador zero → None (não infinito)
    assert um.safe_div(None, 2) is None
    assert um.safe_div(10, None) is None


def test_cagr():
    assert um.cagr(100, 144, 2) == pytest.approx(0.2)
    assert um.cagr(0, 100, 2) is None      # base <= 0 indefinido
    assert um.cagr(100, -50, 2) is None
    assert um.cagr(100, 100, 0) is None


def _synthetic():
    income = [
        {"fiscal_year": 2021, "revenue": 1000, "gross_profit": 400,
         "operating_income": 200, "ebit": 200, "ebitda": 250, "net_income": 150,
         "interest_expense": 10, "eps": 1.5},
        {"fiscal_year": 2022, "revenue": 1200, "gross_profit": 500,
         "operating_income": 250, "ebit": 250, "ebitda": 300, "net_income": 180,
         "interest_expense": 11, "eps": 1.8},
        {"fiscal_year": 2023, "revenue": 1440, "gross_profit": 600,
         "operating_income": 300, "ebit": 300, "ebitda": 360, "net_income": 216,
         "interest_expense": 12, "eps": 2.16},
    ]
    balance = [{"fiscal_year": 2023, "total_assets": 2000, "total_equity": 1000,
                "total_debt": 500, "cash_and_equivalents": 200,
                "current_assets": 800, "current_liabilities": 400,
                "invested_capital": 1300, "shares_outstanding": 100}]
    cashflow = [{"fiscal_year": 2023, "operating_cash_flow": 260, "capex": -60,
                 "free_cash_flow": 200, "dividends_paid": -50,
                 "stock_repurchase": -30, "stock_issuance": 0}]
    return income, balance, cashflow


def test_compute_company_metrics():
    inc, bal, cf = _synthetic()
    m = um.compute_company_metrics(inc, bal, cf, market_cap=3000)
    assert m["gross_margin"] == pytest.approx(600 / 1440)
    assert m["net_margin"] == pytest.approx(0.15)
    assert m["roe"] == pytest.approx(0.216)
    assert m["roic"] == pytest.approx(300 * 0.79 / 1300, rel=1e-6)
    assert m["revenue_cagr_5y"] == pytest.approx(0.2)     # 1000→1440 em 2 anos
    assert m["net_debt_ebitda"] == pytest.approx(300 / 360)   # net_debt derivado
    assert m["interest_coverage"] == pytest.approx(25.0)
    assert m["current_ratio"] == pytest.approx(2.0)
    assert m["pe"] == pytest.approx(3000 / 216)
    assert m["ev_ebit"] == pytest.approx(3300 / 300)
    assert m["fcf_yield"] == pytest.approx(200 / 3000)
    assert m["shareholder_yield"] == pytest.approx((50 + 30) / 3000)


def test_aceita_decimal_do_postgres():
    """NUMERIC do Postgres chega como Decimal — não pode quebrar (float/Decimal)."""
    from decimal import Decimal
    inc = [{"fiscal_year": 2023, "revenue": Decimal("1440"),
            "net_income": Decimal("216"), "operating_income": Decimal("300"),
            "ebit": Decimal("300"), "ebitda": Decimal("360")}]
    bal = [{"fiscal_year": 2023, "total_equity": Decimal("1000"),
            "total_debt": Decimal("500"), "cash_and_equivalents": Decimal("200"),
            "total_assets": Decimal("2000")}]
    m = um.compute_company_metrics(inc, bal, [], market_cap=Decimal("3000"))
    assert m["net_margin"] == pytest.approx(0.15)
    assert m["ev_ebit"] == pytest.approx((3000 + 500 - 200) / 300)   # aritmética direta
    assert m["pe"] == pytest.approx(3000 / 216)


def test_no_zero_fill_quando_denominador_ausente():
    inc = [{"fiscal_year": 2023, "revenue": None, "net_income": 100}]
    m = um.compute_company_metrics(inc, [], [])
    assert m["net_margin"] is None     # revenue ausente → None, não zero
    assert m["roe"] is None            # sem equity


def test_fcf_derivado_de_ocf_capex():
    inc, bal, _ = _synthetic()
    cf = [{"fiscal_year": 2023, "operating_cash_flow": 260, "capex": -60}]
    m = um.compute_company_metrics(inc, bal, cf, market_cap=3000)
    assert m["fcf_margin"] == pytest.approx(200 / 1440)   # 260 + (-60)


def test_ebitda_derivado_de_operating_income_e_depreciacao():
    inc = [{"fiscal_year": 2023, "operating_income": 300, "ebit": 300,
            "ebitda": None, "net_income": 200}]
    bal = [{"fiscal_year": 2023, "total_debt": 500,
            "cash_and_equivalents": 100}]
    cf = [{"fiscal_year": 2023, "depreciation_and_amortization": 60}]
    m = um.compute_company_metrics(inc, bal, cf, market_cap=3000)
    assert m["_ebitda"] == 360
    assert m["_ebitda_derived"] is True
    assert m["net_debt_ebitda"] == pytest.approx(400 / 360)


# ── SBC e diluição (score v0.5.0, auditoria 2026-07) ─────────────────────────

def test_sbc_entra_como_qualidade_dos_lucros():
    inc, bal, cf = _synthetic()
    cf[0]["stock_based_compensation"] = 144      # 10% da receita de 1440
    m = um.compute_company_metrics(inc, bal, cf, market_cap=3000)
    assert m["sbc_to_revenue"] == pytest.approx(0.10)
    # FCF ex-SBC absorve a despesa paga em participação: 200 - 144 = 56
    assert m["fcf_ex_sbc_margin"] == pytest.approx(56 / 1440)
    # a margem GAAP segue inflada — é justamente o contraste que o score usa
    assert m["fcf_margin"] == pytest.approx(200 / 1440)


def test_sbc_ausente_nao_vira_zero():
    inc, bal, cf = _synthetic()           # sem stock_based_compensation
    m = um.compute_company_metrics(inc, bal, cf, market_cap=3000)
    assert m["sbc_to_revenue"] is None    # ausência ≠ "não paga SBC"
    assert m["fcf_ex_sbc_margin"] is None


def test_sbc_negativo_no_cf_e_tratado_em_modulo():
    inc, bal, cf = _synthetic()
    cf[0]["stock_based_compensation"] = -144   # sinal varia por filer
    m = um.compute_company_metrics(inc, bal, cf, market_cap=3000)
    assert m["sbc_to_revenue"] == pytest.approx(0.10)
    assert m["fcf_ex_sbc_margin"] == pytest.approx(56 / 1440)


def test_diluicao_pela_contagem_de_acoes():
    inc, _bal, cf = _synthetic()
    # emissão líquida: 100 -> 121 em 2 anos = +10% a.a.
    bal = [{"fiscal_year": 2021, "shares_outstanding": 100},
           {"fiscal_year": 2023, "shares_outstanding": 121, "total_equity": 1000}]
    m = um.compute_company_metrics(inc, bal, cf, market_cap=3000)
    assert m["share_count_cagr_3y"] == pytest.approx(0.10)
    # recompra efetiva devolve valor negativo (menor é melhor no ranking)
    bal[1]["shares_outstanding"] = 81
    m2 = um.compute_company_metrics(inc, bal, cf, market_cap=3000)
    assert m2["share_count_cagr_3y"] == pytest.approx(-0.10)


def test_metricas_novas_tem_direcao_correta_no_ranking():
    assert "sbc_to_revenue" in um.LOWER_IS_BETTER
    assert "share_count_cagr_3y" in um.LOWER_IS_BETTER
    assert "fcf_ex_sbc_margin" not in um.LOWER_IS_BETTER
