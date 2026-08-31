"""Razão que não existe não é dado que falta.

`div_if_den_positive` anula ROE, ROIC, conversão de caixa, dívida/EBITDA e
dívida/PL quando o denominador foi MEDIDO e veio <= 0. Isso está certo: razão
cujo denominador troca de sinal deixa de ser ordenável. O erro estava no que
vinha depois — a razão anulada chegava ao score como ausência, e ausência
derruba COBERTURA, que é o número que barra `decision_grade`. A empresa
deficitária apanhava duas vezes pelo mesmo prejuízo: uma no rank, puxada ao
neutro, e outra na cobertura, por um dado que ela entregou.

O que NÃO pode acontecer com a correção: a empresa sair com selo de decisão
porque suas indefinições foram perdoadas até não sobrar trilha para julgar. Até
0.7.2 quem impedia isso era o portão A-101 (marca de balanço quebrado), por
coincidência: ele travava justamente quem produz estas indefinições. Desde
0.8.0 quem impede é o piso de RESPONDIBILIDADE, que mede a coisa certa — a
fração das perguntas da trilha que ainda cabem na empresa. O último teste deste
arquivo é a trava, e ela continua valendo pelo motivo novo.
"""
from __future__ import annotations

import pandas as pd

import core.us_score as sc
from core.us_metrics import compute_company_metrics


def _series(**over):
    income = [{"fiscal_year": 2023, "revenue": 1000.0, "operating_income": 100.0,
               "net_income": 60.0, "ebitda": 150.0, "ebit": 100.0}]
    balance = [{"fiscal_year": 2023, "total_equity": 800.0, "total_debt": 500.0,
                "cash_and_equivalents": 100.0, "total_assets": 2000.0,
                "invested_capital": 900.0}]
    cashflow = [{"fiscal_year": 2023, "operating_cash_flow": 120.0,
                 "capital_expenditure": -40.0, "free_cash_flow": 80.0}]
    for campo, valor in over.items():
        for serie in (income, balance, cashflow):
            if campo in serie[0]:
                serie[0][campo] = valor
    return income, balance, cashflow


def _metrics(**over) -> dict:
    income, balance, cashflow = _series(**over)
    return compute_company_metrics(income, balance, cashflow, market_cap=1000.0)


def test_prejuizo_marca_a_razao_como_indefinida_e_nao_como_ausente():
    m = _metrics(net_income=-40.0, free_cash_flow=-80.0)
    assert m["cash_conversion"] is None
    assert "cash_conversion" in m["nm_metrics"]


def test_patrimonio_negativo_marca_as_duas_razoes_que_ele_anula():
    m = _metrics(total_equity=-200.0, net_income=-50.0)
    assert set(m["nm_metrics"]) >= {"roe", "debt_to_equity"}


def test_dado_que_nunca_chegou_continua_sendo_lacuna():
    """Sem EBITDA gravado, dívida/EBITDA falta — não é indefinição.

    A diferença importa: lacuna derruba cobertura (e deve), indefinição não.
    Confundir as duas transformaria falha de extração em isenção.
    """
    income, balance, cashflow = _series()
    income[0].pop("ebitda")
    income[0]["operating_income"] = None
    m = compute_company_metrics(income, balance, cashflow, market_cap=1000.0)
    assert m["net_debt_ebitda"] is None
    assert "net_debt_ebitda" not in m["nm_metrics"]


def test_empresa_saudavel_nao_tem_indefinicao():
    assert _metrics()["nm_metrics"] == ()


def _quadro(nm_preju: tuple[str, ...]) -> pd.DataFrame:
    linhas = []
    for i, sym in enumerate(("BOA1", "BOA2", "BOA3", "PREJU")):
        linhas.append({
            "symbol": sym, "sector": "Tech", "industry": "Tech",
            "gross_margin": 0.4 + i / 100, "operating_margin": 0.2,
            "net_margin": 0.1, "fcf_margin": 0.1, "cash_conversion": 1.0,
            "roe": 0.15, "roa": 0.08, "sbc_to_revenue": 0.02,
            "fcf_ex_sbc_margin": 0.08,
            "revenue_cagr_3y": 0.1, "revenue_cagr_5y": 0.1,
            "op_income_growth_3y": 0.1, "eps_growth_3y": 0.1,
            "fcf_growth_3y": 0.1,
            "net_debt_ebitda": 1.5, "interest_coverage": 8.0,
            "current_ratio": 2.0, "debt_to_equity": 0.5, "roic": 0.12,
            "earnings_yield": 0.06, "ev_ebit": 12.0, "ev_ebitda": 9.0,
            "fcf_yield": 0.05, "p_s": 2.0,
            "shareholder_yield": 0.03, "share_count_cagr_3y": -0.01,
            "impairment_flags": (), "nm_metrics": (),
        })
    quadro = pd.DataFrame(linhas)
    i = quadro.index[quadro["symbol"] == "PREJU"][0]
    for coluna in nm_preju:
        quadro.at[i, coluna] = None
    quadro.at[i, "nm_metrics"] = nm_preju
    return quadro


_ANULADAS = ("roe", "roic", "cash_conversion", "net_debt_ebitda",
             "debt_to_equity")


def test_indefinicao_nao_derruba_a_cobertura():
    """As mesmas 5 razões anuladas: como lacuna custavam cobertura, agora não."""
    com_marca = sc.score_cross_section(_quadro(_ANULADAS),
                                       min_group=2).set_index("symbol")
    sem_marca = _quadro(_ANULADAS)
    sem_marca["nm_metrics"] = [() for _ in range(len(sem_marca))]
    sem = sc.score_cross_section(sem_marca, min_group=2).set_index("symbol")
    assert com_marca.loc["PREJU", "coverage"] == 100.0
    assert sem.loc["PREJU", "coverage"] < 100.0
    assert com_marca.loc["PREJU", "score_confidence"] > \
        sem.loc["PREJU", "score_confidence"]


def test_trilha_inteiramente_indefinida_nao_vira_cobertura_cheia():
    """Solidez toda indefinida não é 'coberta': não sobrou nada mensurável.

    Denominador zero não pode virar 1,0 por acidente aritmético — seria a
    empresa sem nenhuma informação de solidez passando pelo piso da trilha.
    """
    quadro = _quadro(("net_debt_ebitda", "interest_coverage", "current_ratio",
                      "debt_to_equity"))
    scored = sc.score_cross_section(quadro, min_group=2).set_index("symbol")
    assert scored.loc["PREJU", "coverage_solidity"] == 0.0


def test_isentar_cobertura_nao_promove_trilha_quase_muda():
    """A trava: 2 das 4 métricas de Solidez anuladas = 50%, não é maioria.

    Antes de 0.8.0 este teste passava pela marca de balanço quebrado. Ela
    continua na linha — e continua sendo divulgada —, mas não é mais o que
    segura o selo. O que segura é a trilha ter deixado de ser perguntável.
    """
    quadro = _quadro(_ANULADAS)
    i = quadro.index[quadro["symbol"] == "PREJU"][0]
    quadro.at[i, "impairment_flags"] = ("patrimonio_liquido_negativo",
                                        "ebitda_nao_positivo")
    scored = sc.score_cross_section(quadro, min_group=2).set_index("symbol")
    assert scored.loc["PREJU", "score_status"] != "decision_grade"
