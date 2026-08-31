# -*- coding: utf-8 -*-
"""Lucro bruto é receita menos custo — não é estimativa, é a definição.

Parte das empresas tagueia receita e custo das vendas e não tagueia o subtotal.
A margem bruta saía ausente, e ausência não é nota baixa: derruba a COBERTURA
da trilha de Qualidade e, por ela, a confiança. A empresa era barrada por um
número que os próprios demonstrativos dela já continham. Medido no armazém:
406 empresas com o par receita+custo e sem `gross_profit`.

O sinal do custo é o detalhe que morde: a SEC o publica positivo, mas há fonte
que o grava negativo. Somar sem olhar o sinal daria margem bruta acima de 100%
sem levantar erro nenhum.
"""
from core.us_metrics import compute_company_metrics


def _ano(**campos):
    return [{"fiscal_year": 2024, **campos}]


def test_deriva_lucro_bruto_quando_so_falta_o_subtotal():
    m = compute_company_metrics(
        _ano(revenue=1000.0, cost_of_revenue=600.0), [], [])
    assert m["gross_margin"] == 0.40
    assert m["_gross_derived"] is True


def test_custo_gravado_negativo_nao_infla_a_margem():
    m = compute_company_metrics(
        _ano(revenue=1000.0, cost_of_revenue=-600.0), [], [])
    assert m["gross_margin"] == 0.40


def test_subtotal_tagueado_vence_a_derivacao():
    """Se a empresa publicou o número, é o dela que vale."""
    m = compute_company_metrics(
        _ano(revenue=1000.0, gross_profit=350.0, cost_of_revenue=600.0), [], [])
    assert m["gross_margin"] == 0.35
    assert m["_gross_derived"] is False


def test_sem_custo_a_lacuna_continua_lacuna():
    """Derivar exige as duas pontas; inventar zero seria margem de 100%."""
    m = compute_company_metrics(_ano(revenue=1000.0), [], [])
    assert m["gross_margin"] is None
    assert m["_gross_derived"] is False
