# -*- coding: utf-8 -*-
"""Prejuízo persistente é evidência, não lacuna.

O CAGR não é definido com base ou ponta <= 0, e lucro operacional, LPA e fluxo
de caixa ficam negativos com frequência. O que saía dali era ``None`` -- e
``None`` não significa "cresceu pouco", significa "não há dado": ele derruba a
COBERTURA da trilha e, por ela, a confiança da empresa. Quem perdeu dinheiro
quatro anos seguidos era tratado como quem não publicou demonstração nenhuma.

Medido no armazém antes da troca: 1.159 das 1.976 empresas com par de anos
para lucro operacional caíam assim, e 1.289 das 2.271 para LPA. A maioria.
"""
from core.us_metrics import cagr, compute_company_metrics, symmetric_growth


def _anos(campo, valores, inicio=2021):
    return [{"fiscal_year": inicio + i, campo: v} for i, v in enumerate(valores)]


def test_cagr_continua_indefinido_atraves_do_zero():
    """Não é bug do CAGR -- taxa composta não existe com base negativa."""
    assert cagr(-50.0, 200.0, 3) is None
    assert cagr(100.0, -20.0, 3) is None


def test_taxa_simetrica_atravessa_o_zero_e_ordena_na_direcao_certa():
    recuperacao = symmetric_growth(-50.0, 200.0, 3)
    deterioracao = symmetric_growth(200.0, -50.0, 3)
    piora_dentro_do_prejuizo = symmetric_growth(-50.0, -150.0, 3)
    melhora_dentro_do_prejuizo = symmetric_growth(-150.0, -50.0, 3)
    assert recuperacao > 0 > deterioracao
    assert melhora_dentro_do_prejuizo > 0 > piora_dentro_do_prejuizo
    assert recuperacao == -deterioracao  # simétrica, e é disso que vem o nome


def test_taxa_simetrica_e_limitada_e_nao_explode_com_base_minuscula():
    """Base de 0,01 daria CAGR de milhares por cento e dominaria o ranque."""
    assert abs(symmetric_growth(0.01, 1000.0, 3)) <= 2.0 / 3 + 1e-9
    assert symmetric_growth(0.0, 0.0, 3) is None


def test_empresa_no_prejuizo_deixa_de_parecer_empresa_sem_dado():
    """O caso que motivou a troca: série completa, métrica ausente."""
    income = [{"fiscal_year": 2021, "operating_income": -80.0, "eps": -1.2},
              {"fiscal_year": 2022, "operating_income": -60.0, "eps": -0.9},
              {"fiscal_year": 2023, "operating_income": -30.0, "eps": -0.4},
              {"fiscal_year": 2024, "operating_income": -10.0, "eps": -0.1}]
    m = compute_company_metrics(income, [], [])
    assert m["op_income_growth_3y"] is not None
    assert m["op_income_growth_3y"] > 0  # o prejuízo encolheu; isso é melhora
    assert m["eps_growth_3y"] is not None


def test_receita_continua_em_cagr():
    """Receita não fica negativa; a taxa composta é definida e é a familiar."""
    m = compute_company_metrics(_anos("revenue", [100.0, 110.0, 121.0, 133.1]),
                                [], [])
    assert m["revenue_cagr_3y"] == cagr(100.0, 133.1, 3)
    assert abs(m["revenue_cagr_3y"] - 0.10) < 1e-9


def test_o_nome_antigo_nao_sobreviveu():
    """`*_cagr_3y` guardando taxa simétrica faria o leitor ler outra conta."""
    m = compute_company_metrics(_anos("operating_income", [10.0, 20.0]), [], [])
    assert "op_income_cagr_3y" not in m
    assert "eps_cagr_3y" not in m
    assert "fcf_cagr_3y" not in m
