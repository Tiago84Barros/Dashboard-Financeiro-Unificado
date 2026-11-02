"""DY sobre o custo na seção Posição dos cards de Ações e FIIs (Investimentos).

O número só serve para comparar com o DY da linha de cima se tiver o mesmo
numerador — o dividendo por ação dos últimos 12 meses — e trocar apenas o
denominador: cotação → preço médio.
"""
from __future__ import annotations

import pytest

import views.investimentos as inv

_FD = {"dy": 4.20, "cotacao": 20.0, "pl": 6.5, "pvp": 0.52}
_PI = {"price": 20.0, "change_pct": 0.0, "hist": None}


def test_mesmo_numerador_do_dy():
    # DPA 12m = 4,2% × 20 = 0,84 → 0,84 / 23,92 = 3,51%
    assert inv._dy_sobre_custo(4.20, 20.0, 23.92) == pytest.approx(0.84 / 23.92 * 100)


def test_comprado_mais_barato_rende_mais_que_o_dy():
    assert inv._dy_sobre_custo(5.0, 10.0, 5.0) == pytest.approx(10.0)


@pytest.mark.parametrize("dy, preco, pm", [
    (None, 20.0, 10.0), (0, 20.0, 10.0), (4.0, None, 10.0),
    (4.0, 20.0, 0), (4.0, 20.0, None), ("x", 20.0, 10.0),
])
def test_sem_dado_nao_inventa_numero(dy, preco, pm):
    assert inv._dy_sobre_custo(dy, preco, pm) is None


def test_card_de_acao_mostra_na_secao_posicao():
    pos = {"ticker": "BBAS3", "pct_carteira": 4.3,
           "total_investido": 23418, "preco_medio": 23.92}
    html = inv._stock_card_html(pos, _FD, _PI, [])
    i_pm = html.index("Preço médio")
    i_custo = html.index("DY sobre o custo")
    assert html.index("Posição") < i_pm < i_custo < html.index("Valuation")
    assert f"{0.84 / 23.92 * 100:.2f}%" in html


def test_card_de_fii_usa_custo_por_cota_sem_preco_medio():
    pos = {"ticker": "HGLG11", "pct_carteira": 3.0,
           "total_investido": 1000.0, "quantidade": 10}
    fd = {"dy": 8.0, "cotacao": 120.0}
    html = inv._fii_card_html(pos, fd, {"price": 120.0, "change_pct": 0.0}, 0.0, [])
    assert (html.index("Posição") < html.index("Cotas")
            < html.index("DY sobre o custo") < html.index("Indicadores"))
    assert "9.60%" in html  # 8% × 120 / 100


def test_sem_preco_medio_a_linha_some():
    pos = {"ticker": "BBAS3", "pct_carteira": 4.3, "total_investido": 0,
           "preco_medio": 0}
    assert "DY sobre o custo" not in inv._stock_card_html(pos, _FD, _PI, [])
