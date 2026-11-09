"""Tolerancia de 10x nao vale quando a escolha da fonte muda a decisao.

A arbitragem do A-133 so trocava a declarada pela fita oficial da B3 acima de
10x de desacordo. Abaixo disso, mantinha a declarada -- inclusive quando as
duas caiam em lados opostos do piso de liquidez. Declarada de R$ 5 mi contra
fita de R$ 0,6 mi com seis meses de lastro (8,3x) passava pelo piso de R$ 1 mi
negociando um decimo dele na bolsa.
"""
import pytest

from core.fii_integrated_model import IntegratedEligibilityPolicy
from core.fii_portfolio_v4 import PortfolioPolicy
from core.liquidez import FATOR_CONTRADICAO, PISO_DECISAO, liquidez_para_decisao

PISO = 1_000_000.0


def test_fontes_em_lados_opostos_do_piso_vale_a_fita():
    decisao = liquidez_para_decisao(5_000_000, 600_000, meses_observados=6, piso=PISO)

    assert decisao.razao < FATOR_CONTRADICAO
    assert decisao.origem == "fita_b3"
    assert decisao.valor == 600_000
    assert "lados opostos do piso" in decisao.motivo


def test_simetria_fita_acima_do_piso_e_declarada_abaixo():
    # Declarar baixo demais exclui um fundo que negocia: mesmo criterio.
    decisao = liquidez_para_decisao(400_000, 2_500_000, meses_observados=6, piso=PISO)

    assert decisao.origem == "fita_b3"
    assert decisao.valor == 2_500_000


@pytest.mark.parametrize(("declarada", "fita"), [
    (5_000_000, 1_200_000),   # 4,2x, as duas acima do piso
    (900_000, 150_000),       # 6x, as duas abaixo
])
def test_mesmo_lado_do_piso_mantem_a_tolerancia(declarada, fita):
    decisao = liquidez_para_decisao(declarada, fita, meses_observados=6, piso=PISO)

    assert decisao.origem == "declarada"
    assert decisao.valor == declarada


def test_sem_lastro_a_fita_nao_desmente_nem_no_piso():
    decisao = liquidez_para_decisao(5_000_000, 600_000, meses_observados=2, piso=PISO)

    assert decisao.origem == "declarada"


def test_sem_piso_o_comportamento_antigo_fica_igual():
    decisao = liquidez_para_decisao(5_000_000, 600_000, meses_observados=6)

    assert decisao.origem == "declarada"


def test_piso_da_arbitragem_e_o_mesmo_das_politicas():
    assert PISO_DECISAO == PortfolioPolicy().min_daily_liquidity
    assert PISO_DECISAO == IntegratedEligibilityPolicy().min_daily_liquidity
