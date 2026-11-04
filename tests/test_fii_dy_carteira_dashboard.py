"""DY 12m ponderado da carteira de FIIs no Dashboard Geral.

O cartão calculava ``sum((dy_12m or 0) * peso)`` e multiplicava por 100:
DY ausente entrava como zero e rebaixava a carteira inteira, e DY gravado em
ponto (11,0) virava 1.100%.
"""
import pytest

from core.fii_presentation import dy_da_carteira
from views.dashboard_geral import _resumo_fiis


def test_media_aritmetica_ponderada_em_fracao():
    assert dy_da_carteira([(.10, .5), (.12, .5)]) == pytest.approx(.11)
    assert dy_da_carteira([(.10, .25), (.14, .75)]) == pytest.approx(.13)


def test_dy_em_ponto_e_em_fracao_valem_o_mesmo():
    assert dy_da_carteira([(11.0, .5), (.13, .5)]) == pytest.approx(.12)


@pytest.mark.parametrize("ausente", [None, float("nan"), "abc", -0.05])
def test_dy_ausente_ou_invalido_fica_fora_e_o_peso_renormaliza(ausente):
    # Antes: (0 * .5 + .12 * .5) = 6%, metade do rendimento real medido.
    assert dy_da_carteira([(ausente, .5), (.12, .5)]) == pytest.approx(.12)


def test_dy_zero_medido_continua_contando():
    # Zero medido é dado (fundo que não distribuiu), não ausência.
    assert dy_da_carteira([(0.0, .5), (.12, .5)]) == pytest.approx(.06)


def test_sem_dy_medido_e_none():
    assert dy_da_carteira([(None, .5), (None, .5)]) is None
    assert dy_da_carteira([]) is None


def _linha(linhas, rotulo):
    return next(valor for nome, valor, _ in linhas if nome == rotulo)


def test_cartao_do_dashboard_nao_rebaixa_por_ausencia_nem_infla_por_escala():
    carteira = [
        {"ticker": "AAA11", "peso": .5, "dy_12m": 11.0, "pvp": .9},
        {"ticker": "BBB11", "peso": .3, "dy_12m": .13, "pvp": 1.0},
        {"ticker": "CCC11", "peso": .2, "dy_12m": None, "pvp": 1.1},
    ]
    _, _, linhas = _resumo_fiis(carteira, salvo=True)

    # (.11*.5 + .13*.3) / .8 = 11,75%
    assert _linha(linhas, "DY 12m ponderado") == "11.8%"


def test_cartao_sem_nenhum_dy_mostra_nd():
    carteira = [{"ticker": "AAA11", "peso": 1.0, "dy_12m": None, "pvp": None}]
    _, _, linhas = _resumo_fiis(carteira, salvo=False)

    assert _linha(linhas, "DY 12m ponderado") == "N/D"
    assert _linha(linhas, "P/VP ponderado") == "N/D"
