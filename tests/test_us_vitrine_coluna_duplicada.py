"""Coluna promovida a física continua existindo dentro do JSON de origem.

`load_snapshot_scored` expande `metrics` ao lado das colunas da vitrine. Quando
uma chave do JSON vira coluna física -- foi o caso de `impairment_flags` na
migration 061 -- o `concat` cru devolve DUAS colunas com o mesmo rótulo. Nada
levanta exceção: `linha["impairment_flags"]` deixa de ser lista e vira Series,
e quem consome imprime as duas. A tela de Avaliação de Portfólio chegou a
mostrar "o balanço: ['patrimonio_liquido_negativo'],
['patrimonio_liquido_negativo']" -- o nome cru, repetido, no lugar do rótulo em
português.
"""
from __future__ import annotations

import pandas as pd

from core.portfolio_report_us import motivo_do_grau
from core.us_read import _concat_sem_colidir


def test_coluna_repetida_nao_sobrevive_a_juncao():
    fisica = pd.DataFrame({"symbol": ["BRBR"],
                           "impairment_flags": [["patrimonio_liquido_negativo"]]})
    do_json = pd.DataFrame({"roe": [0.2],
                            "impairment_flags": [["patrimonio_liquido_negativo"]]})
    out = _concat_sem_colidir([fisica, do_json])
    assert list(out.columns).count("impairment_flags") == 1
    assert out["impairment_flags"].iloc[0] == ["patrimonio_liquido_negativo"]
    assert out["roe"].iloc[0] == 0.2


def test_a_coluna_fisica_ganha_do_eco_do_json():
    """A publicação escreve a coluna; o JSON é a cópia mais velha dela."""
    fisica = pd.DataFrame({"impairment_flags": [["ebitda_nao_positivo"]]})
    do_json = pd.DataFrame({"impairment_flags": [["desatualizado"]]})
    out = _concat_sem_colidir([fisica, do_json])
    assert out["impairment_flags"].iloc[0] == ["ebitda_nao_positivo"]


def test_sem_colisao_nada_muda():
    a = pd.DataFrame({"symbol": ["X"]})
    b = pd.DataFrame({"roe": [0.1]})
    assert list(_concat_sem_colidir([a, b]).columns) == ["symbol", "roe"]


def test_o_texto_da_tela_volta_a_ser_o_rotulo_e_nao_o_nome_cru():
    """O sintoma visível é aqui: sem a correção, a marca chegava aninhada."""
    fisica = pd.DataFrame({"critical_missing": [[]],
                           "impairment_flags": [["patrimonio_liquido_negativo"]]})
    do_json = pd.DataFrame({"impairment_flags": [["patrimonio_liquido_negativo"]]})
    linha = _concat_sem_colidir([fisica, do_json]).iloc[0]
    texto = motivo_do_grau({"critical_missing": list(linha["critical_missing"]),
                            "impairment_flags": list(linha["impairment_flags"])})["texto"]
    assert "patrimônio líquido negativo" in texto
    assert "[" not in texto
