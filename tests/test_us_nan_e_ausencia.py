# -*- coding: utf-8 -*-
"""NaN não é valor: o quadro que decide a nota lia NULL como número.

`load_scoring_frame` monta as séries com pandas, e `to_dict("records")`
devolve `float('nan')` onde o Postgres tinha NULL. O dossiê monta as mesmas
séries com `dict(r._mapping)`, onde NULL vira `None`. Como `nan is None` é
falso, toda derivação guardada por `is None` — EBITDA, fluxo de caixa livre,
dívida líquida, capital investido, lucro bruto — ficava desligada exatamente
no caminho que produz o score, e ligada no que só exibe.

O sintoma que denunciou: em 30/08/2026, 21 empresas estavam gravadas como
`decision_grade` com `impairment_flags` não vazio na própria linha. O portão de
balanço quebrado (A-101) lia o quadro, onde `ebitda` era NaN e nenhuma marca
existia; o dossiê gravado ao lado lia `None`, derivava EBITDA negativo e
marcava. Verificador e escritor discordando sobre a mesma empresa.
"""
from core.us_metrics import compute_company_metrics

NAN = float("nan")


def test_nan_no_lugar_de_null_nao_desliga_a_derivacao_de_ebitda():
    """O caso real: `ebitda` NULL na base, D&A e lucro operacional presentes."""
    income = [{"fiscal_year": 2024, "revenue": 100.0,
               "operating_income": -150.0, "ebitda": NAN}]
    cashflow = [{"fiscal_year": 2024, "depreciation_and_amortization": 36.0}]
    m = compute_company_metrics(income, [], cashflow)
    assert m["_ebitda"] == -114.0
    assert m["_ebitda_derived"] is True


def test_portao_de_balanco_quebrado_volta_a_enxergar_a_empresa():
    income = [{"fiscal_year": 2024, "operating_income": -150.0, "ebitda": NAN}]
    cashflow = [{"fiscal_year": 2024, "depreciation_and_amortization": 36.0}]
    assert "ebitda_nao_positivo" in compute_company_metrics(
        income, [], cashflow)["impairment_flags"]


def test_nan_nao_vira_zero_nem_valor_proprio():
    """Tratar NaN como ausência, não como número: margem some, não zera."""
    m = compute_company_metrics(
        [{"fiscal_year": 2024, "revenue": NAN, "net_income": 50.0}], [], [])
    assert m["_revenue"] is None
    assert m["net_margin"] is None


def test_serie_de_crescimento_ignora_o_ano_com_nan():
    """Um NaN no meio não pode virar ponta da janela de crescimento."""
    income = [{"fiscal_year": 2021, "revenue": 100.0},
              {"fiscal_year": 2022, "revenue": NAN},
              {"fiscal_year": 2023, "revenue": 110.0},
              {"fiscal_year": 2024, "revenue": 133.1}]
    m = compute_company_metrics(income, [], [])
    assert abs(m["revenue_cagr_3y"] - 0.10) < 1e-9
