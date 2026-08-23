"""Achado A-101: razão com denominador de sinal variável não é ordenável.

Uma razão do tipo preço/lucro só é monótona enquanto o denominador é positivo.
Quando o lucro (ou EBITDA, ou patrimônio, ou capital investido) fica negativo, a
razão troca de sinal e o ranqueador a lê como "extremamente boa": EV/EBIT = -9
parece mais barato que 5, dívida/PL negativa parece menos alavancada que 0,8, e
FCF/lucro de uma empresa que perde caixa E lucro dá um número positivo que passa
por boa conversão.

Estes testes fixam duas regras:

1. valor cujo sinal não tem significado econômico é **ausente** (n/m), não um
   número — corrigido em core/us_metrics.py, na origem;
2. múltiplo é ranqueado pelo **yield** recíproco, que é monótono através do zero
   — corrigido em core/us_score.py e core/b3_company_score.py, no ranqueador.
"""
from __future__ import annotations

import pandas as pd

import core.us_score as sc
from core.b3_company_score import score_cross_section as score_b3
from core.us_metrics import compute_company_metrics

# ── B3 ────────────────────────────────────────────────────────────────────────

_B3_OPERACIONAL = {
    "ROE": .10, "ROA": .05, "Margem_Liquida": .05, "Margem_Operacional": .08,
    "ROIC": .08, "Endividamento_Total": 1.0, "Liquidez_Corrente": 1.5,
    "DY": .03, "Payout": .30,
}


def _b3_universo() -> pd.DataFrame:
    """Quatro empresas idênticas na operação; só o preço muda.

    PREJU dá prejuízo (múltiplos negativos). CARA é lucrativa e cara.
    """
    return pd.DataFrame([
        {"Ticker": "PREJU", **_B3_OPERACIONAL,
         "P/L": -12.0, "P/VP": 1.0, "EV_EBIT": -9.0, "P_FCO": -7.0},
        {"Ticker": "CARA", **_B3_OPERACIONAL,
         "P/L": 45.0, "P/VP": 6.0, "EV_EBIT": 38.0, "P_FCO": 30.0},
        {"Ticker": "BARATA", **_B3_OPERACIONAL,
         "P/L": 5.0, "P/VP": .7, "EV_EBIT": 4.0, "P_FCO": 3.0},
        {"Ticker": "MEDIA", **_B3_OPERACIONAL,
         "P/L": 12.0, "P/VP": 1.5, "EV_EBIT": 10.0, "P_FCO": 8.0},
    ])


def test_b3_deficitaria_nao_pontua_valuation_acima_de_lucrativa_cara():
    """O invariante que faltava: deficitária fica ABAIXO da mais cara lucrativa.

    A versão anterior deste teste (test_b3_company_score.py) afirmava apenas
    score_valuation == 50.0 e chamava isso de "não é barganha" — mas 50 é a
    mediana do corte, ou seja, mais barata que metade do universo.
    """
    scored = score_b3(_b3_universo()).set_index("Ticker")
    assert scored.loc["PREJU", "score_valuation"] <= scored.loc["CARA", "score_valuation"]


def test_b3_ordem_de_valuation_segue_o_preco_entre_as_lucrativas():
    scored = score_b3(_b3_universo()).set_index("Ticker")
    assert (scored.loc["BARATA", "score_valuation"]
            > scored.loc["MEDIA", "score_valuation"]
            > scored.loc["CARA", "score_valuation"])


def test_b3_deficitaria_nao_termina_acima_da_lucrativa_a_preco_justo():
    scored = score_b3(_b3_universo()).set_index("Ticker")
    assert scored.loc["PREJU", "score"] < scored.loc["MEDIA", "score"]


# ── EUA: ranqueamento ─────────────────────────────────────────────────────────

_US_OPERACIONAL = {
    "sector": "Industrials", "industry": "Machinery",
    "gross_margin": .30, "operating_margin": .10, "net_margin": .06,
    "fcf_margin": .08, "cash_conversion": 1.0, "roe": .12, "roa": .06,
    "sbc_to_revenue": .02, "fcf_ex_sbc_margin": .06,
    "revenue_cagr_3y": .05, "revenue_cagr_5y": .05, "op_income_cagr_3y": .05,
    "eps_cagr_3y": .05, "fcf_cagr_3y": .05,
    "net_debt_ebitda": 2.0, "interest_coverage": 6.0, "current_ratio": 1.5,
    "debt_to_equity": .8, "roic": .10,
    "shareholder_yield": .02, "share_count_cagr_3y": -.01,
}


def _us_universo() -> pd.DataFrame:
    return pd.DataFrame([
        {"symbol": "PREJU", **_US_OPERACIONAL, "earnings_yield": -.08,
         "ev_ebit": -9.0, "ev_ebitda": -7.0, "fcf_yield": -.05, "p_s": 1.0},
        {"symbol": "CARA", **_US_OPERACIONAL, "earnings_yield": .02,
         "ev_ebit": 38.0, "ev_ebitda": 25.0, "fcf_yield": .01, "p_s": 6.0},
        {"symbol": "BARATA", **_US_OPERACIONAL, "earnings_yield": .12,
         "ev_ebit": 5.0, "ev_ebitda": 4.0, "fcf_yield": .10, "p_s": .6},
        {"symbol": "MEDIA", **_US_OPERACIONAL, "earnings_yield": .06,
         "ev_ebit": 11.0, "ev_ebitda": 9.0, "fcf_yield": .05, "p_s": 1.8},
    ])


def test_us_ev_ebit_negativo_nao_e_ranqueado_como_barato():
    scored = sc.score_cross_section(_us_universo(), min_group=2).set_index("symbol")
    assert scored.loc["PREJU", "score_valuation"] <= scored.loc["CARA", "score_valuation"]


def test_us_deficitaria_nao_termina_acima_da_lucrativa_a_preco_justo():
    scored = sc.score_cross_section(_us_universo(), min_group=2).set_index("symbol")
    assert scored.loc["PREJU", "score"] < scored.loc["MEDIA", "score"]


def test_us_alavancagem_negativa_agora_so_pode_significar_caixa_liquido():
    """Por que LOWER_IS_BETTER voltou a ser seguro para net_debt_ebitda.

    Antes, a razão negativa era ambígua: podia ser caixa líquido (ótimo) ou
    EBITDA negativo (péssimo), e o ranqueador tratava as duas como ótimas. Com
    div_if_den_positive na origem, EBITDA não positivo vira ausência — então
    razão negativa só pode ser caixa líquido, e ranquear como melhor está certo.
    """
    df = _us_universo()
    df.loc[df["symbol"] == "BARATA", "net_debt_ebitda"] = -.5   # caixa líquido
    df.loc[df["symbol"] == "CARA", "net_debt_ebitda"] = 4.0
    scored = sc.score_cross_section(df, min_group=2).set_index("symbol")
    assert scored.loc["BARATA", "score_solidity"] > scored.loc["CARA", "score_solidity"]


# ── EUA: origem das métricas ──────────────────────────────────────────────────

def _series(**over):
    income = [{"fiscal_year": 2025, "revenue": 1000.0, "gross_profit": 300.0,
               "operating_income": 100.0, "ebit": 100.0, "ebitda": 150.0,
               "net_income": 60.0, "interest_expense": 10.0}]
    balance = [{"fiscal_year": 2025, "total_assets": 2000.0, "total_equity": 800.0,
                "total_debt": 500.0, "cash_and_equivalents": 100.0,
                "current_assets": 600.0, "current_liabilities": 400.0,
                "invested_capital": 1200.0, "shares_outstanding": 100.0}]
    cashflow = [{"fiscal_year": 2025, "operating_cash_flow": 120.0, "capex": -40.0,
                 "free_cash_flow": 80.0, "stock_based_compensation": 20.0}]
    for campo, valor in over.items():
        for serie in (income, balance, cashflow):
            if campo in serie[0]:
                serie[0][campo] = valor
    return income, balance, cashflow


def _metrics(**over) -> dict:
    income, balance, cashflow = _series(**over)
    return compute_company_metrics(income, balance, cashflow, market_cap=1000.0)


def test_roe_e_ausente_quando_o_patrimonio_e_negativo():
    """PL negativo com prejuízo produzia ROE positivo — um fato falso."""
    m = _metrics(total_equity=-200.0, net_income=-50.0)
    assert m["roe"] is None
    assert m["debt_to_equity"] is None


def test_roic_e_ausente_quando_o_capital_investido_e_negativo():
    m = _metrics(invested_capital=-300.0, net_income=-50.0)
    assert m["roic"] is None


def test_conversao_de_caixa_e_ausente_quando_nao_ha_lucro():
    """FCF -80 sobre lucro -40 dava 2,0 e passava por conversão excelente."""
    m = _metrics(net_income=-40.0, free_cash_flow=-80.0)
    assert m["cash_conversion"] is None


def test_alavancagem_e_ausente_quando_o_ebitda_nao_e_positivo():
    m = _metrics(ebitda=-50.0)
    assert m["net_debt_ebitda"] is None


def test_razoes_validas_continuam_sendo_calculadas():
    m = _metrics()
    assert m["roe"] == 60.0 / 800.0
    assert m["cash_conversion"] == 80.0 / 60.0
    assert m["net_debt_ebitda"] == (500.0 - 100.0) / 150.0
    assert m["debt_to_equity"] == 500.0 / 800.0
    assert m["roic"] is not None


def test_empresa_estruturalmente_comprometida_nunca_e_decision_grade():
    """Denominador quebrado não pode virar 'ausência' e ganhar selo de decisão.

    Anular as razões sem mais nada deixava a empresa em pior situação passando
    por "cobertura um pouco menor" — 5 métricas a menos de 22 ainda davam 84 de
    confiança e selo decision_grade.
    """
    df = _us_universo()
    df["impairment_flags"] = [() for _ in range(len(df))]
    for coluna in ("roe", "roic", "cash_conversion", "net_debt_ebitda",
                   "debt_to_equity"):
        df.loc[df["symbol"] == "PREJU", coluna] = None
    df.at[df.index[df["symbol"] == "PREJU"][0], "impairment_flags"] = (
        "patrimonio_liquido_negativo", "ebitda_nao_positivo")
    scored = sc.score_cross_section(df, min_group=2).set_index("symbol")
    assert scored.loc["PREJU", "score_status"] != "decision_grade"
    assert scored.loc["BARATA", "score_status"] == "decision_grade"


def test_coluna_de_comprometimento_ausente_nao_quebra_o_score():
    """Vitrine em snapshot antigo não tem a coluna; o score segue funcionando."""
    scored = sc.score_cross_section(_us_universo(), min_group=2)
    assert scored["score_status"].isin(
        {"decision_grade", "research_grade", "screen_grade"}).all()
