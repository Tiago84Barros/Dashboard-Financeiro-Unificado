"""Nota zero continua zero; nota ausente não vira 50.

``float(x or 50)`` fazia as duas coisas erradas ao mesmo tempo: a pior posição
entre os pares (0,0, alcançável no score B3 com cobertura cheia) aparecia como
nota neutra, e a ausência de nota virava um 50 que ninguém calculou — no radar
e na tese do dossiê B3, no card de entrada e na atribuição por trilha dos EUA, e
no texto que a LLM do relatório de carteira lia.
"""
from __future__ import annotations

import pandas as pd
import pytest

import core.llm_b3 as llm
import views.analise_portfolio_b3 as apb3
import views.empresas_b3 as b3
from core.b3_company_score import TRACK_LABELS
from core.portfolio_report_common import _nota_ou_nd
from core.us_advanced_lab import factor_contributions, normalize_weights


def test_trilha_zero_fica_e_ausente_sai():
    row = pd.Series({"score_quality": 0.0, "score_growth": None,
                     "score_solidity": float("nan"), "score_capital_efficiency": 70.0})
    assert b3._trilhas_observadas(row, TRACK_LABELS) == [
        ("Qualidade", 0.0), ("Efic. Capital", 70.0)]


def test_trilhas_de_linha_vazia():
    assert b3._trilhas_observadas(pd.Series(dtype=object), TRACK_LABELS) == []


def test_contribuicao_da_trilha_zero_e_negativa():
    w = normalize_weights()
    contrib = factor_contributions(pd.Series({"score_quality": 0.0}))
    qualidade = contrib[contrib["Trilha"] == "Qualidade"].iloc[0]
    assert qualidade["Pontuação"] == 0.0
    assert qualidade["Contribuição"] == pytest.approx(-50 * w["quality"])
    crescimento = contrib[contrib["Trilha"] == "Crescimento"].iloc[0]
    assert pd.isna(crescimento["Pontuação"])
    assert crescimento["Contribuição"] == 0.0  # como em build_entry_scores


def test_media_das_notas_ignora_ausencia():
    assert apb3._media_das_notas([{"score": 80}, {"score": None}, {}]) == 80.0
    assert apb3._media_das_notas([{"score": 0}, {"score": 60}]) == 30.0
    assert apb3._media_das_notas([{}, {"score": None}]) is None


def test_media_do_score_de_entrada_eua_ignora_ausencia():
    itens = [{"entry_score": 70}, {"entry_score": None}, {"entry_score": 0}]
    assert apb3._media_das_notas(itens, "entry_score") == 35.0


def test_texto_da_nota_para_a_llm():
    assert apb3._texto_nota("score quantitativo", None) == "score quantitativo indisponível"
    assert apb3._texto_nota("score quantitativo", 0.0) == "score quantitativo 0.0"


def test_resumo_da_carteira_nao_imprime_zero_para_ausente():
    assert _nota_ou_nd(None) == "N/D"
    assert _nota_ou_nd(0) == "0.0"


def _par(score_a):
    analise = {"perspectiva": "moderada", "score_qualitativo": 60, "confianca": 70}
    return [
        {"ticker": "AAAA3", "score": score_a, "alpha_selic": 0, "analise": dict(analise)},
        {"ticker": "BBBB4", "score": 60, "alpha_selic": 0, "analise": dict(analise)},
    ]


def test_sem_nota_quantitativa_a_qualitativa_responde_sozinha():
    # Antes: score None → 0 e a AAAA3 caía para o piso só por falta de dado.
    pesos = llm.redistribuir_pesos(_par(None))
    assert pesos["AAAA3"] == pytest.approx(pesos["BBBB4"])


def test_nota_quantitativa_zero_segue_pesando():
    pesos = llm.redistribuir_pesos(_par(0))
    assert pesos["AAAA3"] < pesos["BBBB4"]
