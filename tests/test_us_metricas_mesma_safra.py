# -*- coding: utf-8 -*-
"""Numerador e denominador saem do MESMO exercicio (dados sinteticos).

`_latest` devolvia, campo a campo, o ultimo ano nao nulo. Empresa que deixou de
tagueiar `GrossProfit` ficava com lucro bruto de um ano antigo dividido pela
receita do ano corrente (margem bruta de 0,77% numa empresa de ~50%), e PL
nulo no ultimo exercicio reaproveitava um PL velho no ROE.
"""
import math
from datetime import date

import pandas as pd

from core.us_metrics import (compute_company_metrics, market_cap_com_origem,
                             market_cap_confiavel)


def test_margem_bruta_deriva_do_custo_do_ano_de_referencia():
    inc = [{"fiscal_year": 2009, "revenue": 100.0, "gross_profit": 0.77},
           {"fiscal_year": 2025, "revenue": 1000.0, "gross_profit": None,
            "cost_of_revenue": 500.0}]
    m = compute_company_metrics(inc, [], [])
    assert m["gross_margin"] == 0.5
    assert m["_gross_derived"] is True


def test_roe_nulo_se_patrimonio_do_ano_de_referencia_e_nulo():
    inc = [{"fiscal_year": 2023, "net_income": 10.0, "revenue": 100.0},
           {"fiscal_year": 2025, "net_income": 16.0, "revenue": 120.0}]
    bal = [{"fiscal_year": 2023, "total_equity": 100.0},
           {"fiscal_year": 2025, "total_equity": None}]
    m = compute_company_metrics(inc, bal, [])
    assert m["roe"] is None


def test_balanco_de_outro_fim_de_exercicio_nao_entra():
    inc = [{"fiscal_year": 2025, "net_income": 16.0, "revenue": 120.0}]
    bal = [{"fiscal_year": 2024, "total_equity": 100.0, "total_assets": 500.0}]
    m = compute_company_metrics(inc, bal, [])
    assert m["roe"] is None and m["roa"] is None


def test_payout_usa_dividendo_do_mesmo_ano_do_lucro():
    inc = [{"fiscal_year": 2024, "net_income": 100.0, "revenue": 500.0},
           {"fiscal_year": 2025, "net_income": 100.0, "revenue": 500.0}]
    cf = [{"fiscal_year": 2024, "dividends_paid": -80.0},
          {"fiscal_year": 2025, "dividends_paid": None}]
    assert compute_company_metrics(inc, [], cf)["payout_ratio"] is None


def test_ano_de_referencia_ignora_ano_so_com_balanco():
    inc = [{"fiscal_year": 2024, "net_income": 10.0, "revenue": 100.0}]
    bal = [{"fiscal_year": 2024, "total_equity": 50.0},
           {"fiscal_year": 2025, "total_equity": 999.0}]
    assert compute_company_metrics(inc, bal, [])["roe"] == 0.2


def test_equivalencia_pandas_nan_e_dict_none():
    inc_d = [{"fiscal_year": 2009, "revenue": 100.0, "gross_profit": 0.77,
              "net_income": 5.0},
             {"fiscal_year": 2025, "revenue": 1000.0, "gross_profit": None,
              "cost_of_revenue": 500.0, "net_income": 16.0}]
    bal_d = [{"fiscal_year": 2023, "total_equity": 100.0},
             {"fiscal_year": 2025, "total_equity": None}]
    a = compute_company_metrics(inc_d, bal_d, [])
    inc_p = pd.DataFrame(inc_d).to_dict("records")     # None vira NaN
    bal_p = pd.DataFrame(bal_d).to_dict("records")
    assert math.isnan(inc_p[1]["gross_profit"])
    b = compute_company_metrics(inc_p, bal_p, [])
    for k in ("gross_margin", "roe", "net_margin", "_gross_derived"):
        assert a[k] == b[k], k


# --- valor de mercado publicado velho ----------------------------------------

def test_publicado_de_90_dias_vale_o_dobro_do_derivado_perde():
    hoje = date(2026, 11, 10)
    velho = date(2026, 8, 12)
    v, origem = market_cap_com_origem(
        2e9, 1e9, preco=10.0, publicado_em=velho, preco_em=hoje)
    assert v == 1e9 and origem == "derivado_publicado_velho"
    assert market_cap_confiavel(
        2e9, 1e9, 10.0, publicado_em=velho, preco_em=hoje) == 1e9


def test_publicado_velho_sem_derivado_e_lacuna():
    assert market_cap_confiavel(
        2e9, None, 10.0, publicado_em=date(2026, 8, 1),
        preco_em=date(2026, 11, 10)) is None


def test_publicado_recente_e_sem_datas_seguem_valendo():
    hoje = date(2026, 11, 10)
    assert market_cap_confiavel(
        1.2e9, 1e9, 10.0, publicado_em=date(2026, 10, 20), preco_em=hoje) == 1.2e9
    assert market_cap_confiavel(1.2e9, 1e9, 10.0) == 1.2e9     # compat


def test_dpa_usa_acoes_do_mesmo_exercicio_do_dividendo():
    inc = [{"fiscal_year": 2024, "net_income": 100.0, "revenue": 500.0}]
    bal = [{"fiscal_year": 2024, "shares_outstanding": 1e6},
           {"fiscal_year": 2025, "shares_outstanding": 2e6}]
    cf = [{"fiscal_year": 2024, "dividends_paid": -1e6},
          {"fiscal_year": 2025, "dividends_paid": -2e6}]
    m = compute_company_metrics(inc, bal, cf, shares=2e6)
    assert m["dividends_per_share"] == 1.0


def test_dpa_lacuna_sem_contagem_do_exercicio():
    inc = [{"fiscal_year": 2025, "net_income": 100.0, "revenue": 500.0}]
    bal = [{"fiscal_year": 2024, "shares_outstanding": 1e6}]
    cf = [{"fiscal_year": 2025, "dividends_paid": -1e6}]
    m = compute_company_metrics(inc, bal, cf, shares=1e6)
    assert m["dividends_per_share"] is None
