# -*- coding: utf-8 -*-
"""Falta de dado, pergunta que não cabe e veredito de balanço são três coisas.

Até 0.7.2 a tela dizia, para toda empresa sem `decision_grade`: *"a leitura
abaixo é limitada pelo que falta — não é veredicto sobre a empresa"*. Medido em
31/08/2026 sobre as 2.626 ativas, **731** estavam em `research_grade` com todas
as trilhas críticas cobertas e confiança >= 75. Não faltava nada. O que havia
era patrimônio líquido negativo, EBITDA não positivo ou capital investido
negativo — o oposto de uma lacuna.

Em 0.8.0 (A-160) a marca de balanço deixou de travar o selo, e o erro simétrico
passou a ser possível: silenciar a divulgação junto com o portão. Lowe's tem
patrimônio negativo, cobertura 100% e opinião firme — o que ela não tem é um
P/VP que signifique alguma coisa. Estes testes cobrem os dois lados: a marca
sai SEMPRE, e nunca mais como explicação de um selo ausente.
"""
from __future__ import annotations

from core.portfolio_report_us import motivo_do_grau


def test_marca_de_balanco_nao_explica_mais_o_selo_ausente():
    """Ela continua saindo em `marcas` — mas o `tipo` não é mais dela."""
    m = motivo_do_grau({"critical_missing": [], "unanswerable_tracks": [],
                        "impairment_flags": ["patrimonio_liquido_negativo"]})
    assert m["marcas"] == ["patrimonio_liquido_negativo"]
    assert m["tipo"] == "suficiente"
    assert "trava o selo" not in m["texto"]


def test_marca_viaja_junto_de_quem_ja_tem_o_selo():
    """O caso Lowe's: opinião firme e múltiplo sobre base negativa."""
    m = motivo_do_grau({"critical_missing": [], "unanswerable_tracks": [],
                        "impairment_flags": ["patrimonio_liquido_negativo"],
                        "score_status": "decision_grade"})
    assert m["marcas"] == ["patrimonio_liquido_negativo"]


def test_lacuna_continua_sendo_lacuna():
    m = motivo_do_grau({"critical_missing": ["valuation"],
                        "impairment_flags": []})
    assert m["tipo"] == "lacuna"
    assert "não é veredicto sobre a empresa" in m["texto"]


def test_trilha_muda_nao_e_lacuna():
    """Pergunta que não cabe na empresa não é resposta que se perdeu."""
    m = motivo_do_grau({"critical_missing": [],
                        "unanswerable_tracks": ["solidity"]})
    assert m["tipo"] == "muda"
    assert m["mudas"] == ["solidity"]
    assert "não dá para perguntar" in m["texto"]


def test_as_duas_juntas_dizem_as_duas_coisas():
    m = motivo_do_grau({"critical_missing": ["quality"],
                        "unanswerable_tracks": ["solidity"]})
    assert m["tipo"] == "ambos"
    assert "quality" in m["texto"] and "solidity" in m["texto"]


def test_sem_falta_e_sem_muda_o_motivo_e_a_cobertura_geral():
    """Existe: 237 empresas com trilhas críticas cheias e confiança < 75."""
    m = motivo_do_grau({"critical_missing": [], "impairment_flags": []})
    assert m["tipo"] == "suficiente"
    assert "cobertura geral" in m["texto"]


def test_vitrine_antiga_sem_a_coluna_nao_quebra():
    """Já houve drift de schema aqui; ausência da coluna volta ao de antes."""
    assert motivo_do_grau({"critical_missing": ["growth"]})["tipo"] == "lacuna"
    assert motivo_do_grau(None)["tipo"] == "lacuna"
    assert motivo_do_grau(None)["mudas"] == []


def test_prompt_divulga_o_balanco_mesmo_com_o_selo_de_decisao():
    """A divulgação é a parte que sempre foi verdadeira; ela não pode sumir."""
    from core.portfolio_report_us import build_company_provenance
    ctx = build_company_provenance(
        None,
        {"score_status": "decision_grade", "score_confidence": 100.0,
         "critical_missing": [], "unanswerable_tracks": [],
         "impairment_flags": ["patrimonio_liquido_negativo"]})
    assert "balanço estruturalmente quebrado" in ctx
    assert "não significativo" in ctx
    assert "Cobertura baixa não é empresa ruim" not in ctx


def test_prompt_de_trilha_muda_nao_pede_para_descrever_lacuna():
    from core.portfolio_report_us import build_company_provenance
    ctx = build_company_provenance(
        None,
        {"score_status": "research_grade", "score_confidence": 80.0,
         "critical_missing": [], "unanswerable_tracks": ["solidity"],
         "impairment_flags": []})
    assert "trilha muda" in ctx
    assert "Cobertura baixa não é empresa ruim" not in ctx


def test_prompt_de_quem_tem_lacuna_de_verdade_nao_muda():
    from core.portfolio_report_us import build_company_provenance
    ctx = build_company_provenance(
        None,
        {"score_status": "screen_grade", "score_confidence": 40.0,
         "critical_missing": ["valuation"], "impairment_flags": []})
    assert "Cobertura baixa não é empresa ruim" in ctx
