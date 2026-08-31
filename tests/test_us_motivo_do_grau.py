# -*- coding: utf-8 -*-
"""Falta de dado e balanço quebrado não podem falar com a mesma voz.

Até aqui a tela dizia, para toda empresa sem `decision_grade`: *"a leitura
abaixo é limitada pelo que falta — não é veredicto sobre a empresa"*. Medido em
31/08/2026 sobre as 2.626 ativas, **731** estavam em `research_grade` com todas
as trilhas críticas cobertas e confiança >= 75. Não faltava nada. O que havia
era patrimônio líquido negativo, EBITDA não positivo ou capital investido
negativo — o oposto de uma lacuna.

Dizer "não sei" onde a análise diz "sei, e é ruim" transforma reprovação em
dúvida, e dúvida o investidor resolve sozinho, para o lado que já queria.
"""
from __future__ import annotations

from core.portfolio_report_us import motivo_do_grau


def test_balanco_quebrado_com_dado_completo_nao_e_lacuna():
    m = motivo_do_grau({"critical_missing": [],
                        "impairment_flags": ["patrimonio_liquido_negativo"]})
    assert m["tipo"] == "balanco"
    assert "completos" in m["texto"]
    assert "patrimônio líquido negativo" in m["texto"]
    assert "limitada pelo que falta" not in m["texto"]


def test_lacuna_continua_sendo_lacuna():
    m = motivo_do_grau({"critical_missing": ["valuation"],
                        "impairment_flags": []})
    assert m["tipo"] == "lacuna"
    assert "não é veredicto sobre a empresa" in m["texto"]


def test_as_duas_juntas_dizem_as_duas_coisas():
    m = motivo_do_grau({"critical_missing": ["quality"],
                        "impairment_flags": ["ebitda_nao_positivo"]})
    assert m["tipo"] == "ambos"
    assert "quality" in m["texto"] and "EBITDA não positivo" in m["texto"]


def test_sem_marca_e_sem_falta_o_motivo_e_a_cobertura_geral():
    """Existe: 237 empresas com trilhas críticas cheias e confiança < 75."""
    m = motivo_do_grau({"critical_missing": [], "impairment_flags": []})
    assert m["tipo"] == "suficiente"
    assert "cobertura geral" in m["texto"]


def test_vitrine_antiga_sem_a_coluna_nao_quebra():
    """Já houve drift de schema aqui; ausência da coluna volta ao de antes."""
    assert motivo_do_grau({"critical_missing": ["growth"]})["tipo"] == "lacuna"
    assert motivo_do_grau(None)["tipo"] == "lacuna"


def test_prompt_da_empresa_para_de_dizer_que_o_dado_falta():
    """A instrução ao analista é onde o erro custava mais caro."""
    from core.portfolio_report_us import build_company_provenance
    ctx = build_company_provenance(
        None,
        {"score_status": "research_grade", "score_confidence": 82.0,
         "critical_missing": [],
         "impairment_flags": ["patrimonio_liquido_negativo"]})
    assert "balanço estruturalmente quebrado" in ctx
    assert "Cobertura baixa não é empresa ruim" not in ctx


def test_prompt_de_quem_tem_lacuna_de_verdade_nao_muda():
    from core.portfolio_report_us import build_company_provenance
    ctx = build_company_provenance(
        None,
        {"score_status": "screen_grade", "score_confidence": 40.0,
         "critical_missing": ["valuation"], "impairment_flags": []})
    assert "Cobertura baixa não é empresa ruim" in ctx
