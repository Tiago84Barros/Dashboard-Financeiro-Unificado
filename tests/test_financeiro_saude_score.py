"""Regressão do componente de orçamento em calcular_saude_score.

A fórmula estava invertida: o parâmetro recebe a contagem de categorias
ESTOURADAS (ruim), mas o cálculo original tratava esse número como se fosse
categorias dentro do limite (bom), premiando quem estoura tudo e zerando quem
respeita tudo.
"""
from core.financeiro import calcular_saude_score


def test_nenhuma_categoria_estourada_da_pontuacao_maxima_de_orcamento():
    score_sem_orcamento = calcular_saude_score(
        taxa_poupanca=0,
        meses_reserva=0,
        categorias_estouradas=0,
        total_categorias=7,
        rentabilidade_positiva=False,
    )
    assert score_sem_orcamento == 20  # 20 pts cheios do componente de orçamento


def test_todas_categorias_estouradas_zera_pontuacao_de_orcamento():
    score_com_orcamento = calcular_saude_score(
        taxa_poupanca=0,
        meses_reserva=0,
        categorias_estouradas=7,
        total_categorias=7,
        rentabilidade_positiva=False,
    )
    assert score_com_orcamento == 0


def test_exemplo_documentado_status_fase_4():
    assert calcular_saude_score(50, 15, 0, 7, False) == 90
