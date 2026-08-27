# -*- coding: utf-8 -*-
"""A-149: ausencia de receita nao e ausencia de dado.

209 empresas elegiveis nunca registraram receita em exercicio nenhum -- biotech
clinica, mineradora em exploracao. A tela dizia a todas "cobertura
insuficiente", que o leitor entende como "nao sabemos". O correto e "sabemos, e
nao ha receita": muda a conduta, porque lacuna se resolve buscando dado e
ausencia de receita se analisa por caixa e runway.
"""
from __future__ import annotations

import pandas as pd

from core import portfolio_report_us as rel


def _fin(anos, receitas):
    return pd.DataFrame({"fiscal_year": anos, "revenue": receitas})


def test_biotech_sem_receita_em_ano_nenhum_e_pre_receita():
    assert rel.e_pre_receita(_fin([2021, 2022, 2023, 2024], [None, 0, None, 0]))


def test_um_ano_com_receita_ja_descaracteriza():
    assert not rel.e_pre_receita(_fin([2021, 2022, 2023], [0, 0, 1_500_000]))


def test_historico_curto_demais_e_lacuna_e_nao_fato():
    """Recem-listada com um filing lido pode so nao ter sido lida ainda."""
    assert not rel.e_pre_receita(_fin([2024], [None]))
    assert not rel.e_pre_receita(_fin([2023, 2024], [0, 0]))


def test_sem_coluna_de_receita_nao_afirma_nada():
    assert not rel.e_pre_receita(pd.DataFrame({"fiscal_year": [2021, 2022, 2023]}))
    assert not rel.e_pre_receita(None)
    assert not rel.e_pre_receita(pd.DataFrame())


def test_receita_negativa_nao_e_pre_receita():
    """Receita negativa e defeito de parser (A-142/A-143), nao empresa sem venda."""
    assert not rel.e_pre_receita(_fin([2021, 2022, 2023], [-10.0, 0, 0]))


def test_a_procedencia_declara_o_fato_em_vez_da_lacuna():
    texto = rel.build_company_provenance(
        _fin([2021, 2022, 2023, 2024], [0, 0, None, 0]),
        {"score_status": "screen_grade", "score_confidence": 31.0,
         "critical_missing": ["value", "quality"]},
    )
    assert "PRÉ-RECEITA" in texto
    assert "cobertura insuficiente" not in texto
    assert "Trilhas indefinidas por ausência de receita" in texto


def test_empresa_operacional_mantem_o_aviso_de_lacuna():
    texto = rel.build_company_provenance(
        _fin([2021, 2022, 2023], [0, 900_000_000, 950_000_000]),
        {"score_status": "screen_grade", "score_confidence": 40.0,
         "critical_missing": ["value"]},
    )
    assert "PRÉ-RECEITA" not in texto
    assert "cobertura insuficiente" in texto
    assert "Trilhas sem cobertura mínima" in texto


def test_decision_grade_nao_ganha_aviso_nenhum():
    texto = rel.build_company_provenance(
        _fin([2021, 2022, 2023], [1e9, 1.1e9, 1.2e9]),
        {"score_status": "decision_grade", "score_confidence": 88.0,
         "critical_missing": []},
    )
    assert "PRÉ-RECEITA" not in texto
    assert "ATENÇÃO" not in texto
