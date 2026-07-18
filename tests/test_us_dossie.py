"""Testes do dossiê determinístico EUA (classificação, red flags, montagem)."""
import core.us_dossie as ud


def _m(**over):
    base = {"_years": 8, "_net_income": 100, "_equity": 500, "_fcf": 80,
            "revenue_cagr_3y": 0.05, "operating_margin": 0.15, "net_margin": 0.10,
            "net_debt_ebitda": 1.5, "roic": 0.15}
    base.update(over)
    return base


def test_classify_inadequada_por_historico():
    label, _ = ud.classify_company(_m(_years=2))
    assert label == "inadequada"


def test_classify_pl_negativo():
    label, _ = ud.classify_company(_m(_equity=-10))
    assert label == "inadequada"


def test_classify_assimetrica():
    label, _ = ud.classify_company(_m(revenue_cagr_3y=0.30, operating_margin=0.2,
                                      net_debt_ebitda=1.0))
    assert label == "assimetrica"


def test_classify_crescimento():
    label, _ = ud.classify_company(_m(revenue_cagr_3y=0.15, net_margin=0.10))
    assert label == "crescimento"


def test_classify_turnaround():
    label, _ = ud.classify_company(_m(net_margin=-0.05, _fcf=50, _net_income=-20,
                                      revenue_cagr_3y=None))
    assert label == "turnaround"


def test_classify_ciclica():
    label, _ = ud.classify_company(_m(revenue_cagr_3y=0.03, roic=0.05, net_margin=0.08),
                                   sector="Energy")
    assert label == "ciclica"


def test_classify_consolidada():
    label, _ = ud.classify_company(_m(revenue_cagr_3y=0.05, net_margin=0.12,
                                      _fcf=100, net_debt_ebitda=2.0))
    assert label == "consolidada"


def test_red_flags():
    flags = ud.red_flags(_m(net_debt_ebitda=5, interest_coverage=1.5, _fcf=-10,
                            _equity=-5))
    joined = " ".join(flags)
    assert "Alavancagem alta" in joined
    assert "Cobertura de juros" in joined
    assert "negativo" in joined.lower()


def test_assemble_dossie_e_texto():
    income = [{"fiscal_year": y, "revenue": r, "operating_income": r * 0.2,
               "ebit": r * 0.2, "ebitda": r * 0.25, "net_income": r * 0.12,
               "gross_profit": r * 0.5, "interest_expense": 5, "eps": 1.0}
              for y, r in [(2021, 1000), (2022, 1200), (2023, 1440)]]
    balance = [{"fiscal_year": 2023, "total_assets": 2000, "total_equity": 1000,
                "total_debt": 400, "cash_and_equivalents": 200,
                "current_assets": 700, "current_liabilities": 300,
                "invested_capital": 1200, "shares_outstanding": 100}]
    cashflow = [{"fiscal_year": 2023, "operating_cash_flow": 200, "capex": -40,
                 "free_cash_flow": 160, "dividends_paid": -30}]
    d = ud.assemble_dossie("aapl", name="Apple", sector="Technology",
                           industry="Consumer Electronics", income=income,
                           balance=balance, cashflow=cashflow, market_cap=3000)
    assert d["symbol"] == "AAPL"
    assert d["classification"] in ("assimetrica", "crescimento", "consolidada")
    assert d["metrics"]["net_margin"] is not None
    txt = ud.dossie_to_text(d)
    assert "CLASSIFICAÇÃO" in txt and "AVALIAÇÃO" in txt
    assert "Tecnologia / Eletrônicos de Consumo" in txt


def test_dossie_to_text_erro():
    assert "INDISPONÍVEL" in ud.dossie_to_text({"erro": "sem dados"})
