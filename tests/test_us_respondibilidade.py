# -*- coding: utf-8 -*-
"""O que trava o selo passa a ser a pergunta que não cabe, não o balanço.

A marca de balanço quebrado nasceu em A-101 como portão contra um caminho que
0.7.1 fechou na raiz. Ela sobreviveu ao próprio motivo: em 31/08/2026 travava
1.023 das 2.618 empresas da vitrine, entre elas Lowe's, Altria, Cardinal Health
e Bath & Body Works com cobertura 100% e confiança 100%. Patrimônio negativo
ali é estrutura de capital escolhida — recompra acumulada —, não defeito de
dado. Negar opinião sobre a Lowe's era o erro de A-101 com o sinal trocado.

No lugar entra um piso que fecha um buraco real: `coverage` responde "das
perguntas RESPONDÍVEIS, quantas foram respondidas", então a trilha em que
sobrou uma única pergunta respondível, respondida, marca 100%. É "quem pergunta
menos tira nota maior" uma camada abaixo. `answerability` mede a fração das
métricas que a METODOLOGIA define e a empresa consegue ter; o piso é a maioria
estrita.

O piso morde: 83 das 2.618 empresas têm alguma trilha crítica em 50%, e 68
delas passariam por todos os outros critérios. Portão que não reprova ninguém
seria carimbo — foi assim que `gate-que-so-dava-false` entrou na base.
"""
from __future__ import annotations

import pandas as pd

import core.us_score as sc

_SOLIDEZ = ("net_debt_ebitda", "interest_coverage", "current_ratio",
            "debt_to_equity")


def _quadro() -> pd.DataFrame:
    linhas = []
    for i, sym in enumerate(("BOA1", "BOA2", "BOA3", "ALVO")):
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
    return pd.DataFrame(linhas)


def _com_indefinidas(*metricas: str) -> pd.DataFrame:
    quadro = _quadro()
    i = quadro.index[quadro["symbol"] == "ALVO"][0]
    for coluna in metricas:
        quadro.at[i, coluna] = None
    quadro.at[i, "nm_metrics"] = tuple(metricas)
    return quadro


def _scored(quadro: pd.DataFrame) -> pd.DataFrame:
    return sc.score_cross_section(quadro, min_group=2).set_index("symbol")


def test_lowes_volta_a_ter_opiniao():
    """Marca de balanço, cobertura cheia, nenhuma trilha muda: selo de decisão."""
    quadro = _quadro()
    i = quadro.index[quadro["symbol"] == "ALVO"][0]
    quadro.at[i, "impairment_flags"] = ("patrimonio_liquido_negativo",)
    scored = _scored(quadro)
    assert scored.loc["ALVO", "score_status"] == "decision_grade"
    assert list(scored.loc["ALVO", "unanswerable_tracks"]) == []


def test_maioria_estrita_passa():
    """3 das 4 métricas de Solidez respondíveis = 75%: a trilha foi julgada."""
    scored = _scored(_com_indefinidas(_SOLIDEZ[0]))
    assert scored.loc["ALVO", "answerability_solidity"] == 75.0
    assert list(scored.loc["ALVO", "unanswerable_tracks"]) == []
    assert scored.loc["ALVO", "score_status"] == "decision_grade"


def test_metade_exata_nao_passa():
    """Empate não é maioria: trilha julgada por metade não foi julgada."""
    scored = _scored(_com_indefinidas(*_SOLIDEZ[:2]))
    assert scored.loc["ALVO", "answerability_solidity"] == 50.0
    assert list(scored.loc["ALVO", "unanswerable_tracks"]) == ["solidity"]
    assert scored.loc["ALVO", "score_status"] != "decision_grade"


def test_cobertura_cheia_nao_disfarca_trilha_quase_muda():
    """O buraco que o piso fecha: 1 pergunta sobrou, foi respondida, 100%."""
    scored = _scored(_com_indefinidas(*_SOLIDEZ[:3]))
    assert scored.loc["ALVO", "coverage_solidity"] == 100.0
    assert scored.loc["ALVO", "answerability_solidity"] == 25.0
    assert scored.loc["ALVO", "score_status"] != "decision_grade"


def test_metrica_que_a_vitrine_nem_publica_conta_como_nao_respondivel():
    """Omiti-la do denominador repetiria o erro que este piso corrige."""
    quadro = _quadro().drop(columns=list(_SOLIDEZ[:3]))
    scored = _scored(quadro)
    assert scored.loc["ALVO", "answerability_solidity"] == 25.0
    assert list(scored.loc["ALVO", "unanswerable_tracks"]) == ["solidity"]


def test_quadro_sem_nm_metrics_nao_quebra():
    """Vitrine publicada antes de 0.7.1 não tem a coluna; ausência != muda."""
    quadro = _quadro().drop(columns=["nm_metrics"])
    scored = _scored(quadro)
    assert list(scored.loc["ALVO", "unanswerable_tracks"]) == []
    assert scored.loc["ALVO", "answerability_solidity"] == 100.0
