"""Risco da carteira explicado para quem não é do mercado.

Os cards dizem o que medem em palavras simples (a fórmula vai para o detalhe
técnico) e "Como ler estes números" explica cada medida com o valor da
própria carteira.
"""
from __future__ import annotations

import re
from datetime import date

from core import carteira_risco as cr
from views import investimentos_risco as vr


def _risco(**sobre) -> dict:
    base = {
        "disponivel": True, "frequencia": "diária",
        "inicio": date(2026, 4, 14), "fim": date(2026, 10, 29), "n_dias": 140,
        "twr": -0.05, "cdi_periodo": 0.08, "vol_anual": 0.15,
        "vol_benchmark": 0.20, "max_drawdown": 0.12, "drawdown_atual": 0.06,
        "pico_data": date(2026, 8, 15), "vale_data": date(2026, 9, 10),
        "var_1d": 0.014, "var_1m": 0.042, "sharpe": -0.8, "beta": 0.6,
        "tolerancia_pct": None, "alerta": cr.alerta_drawdown(0.12, 0.06, None),
    }
    base.update(sobre)
    return base


def _texto(risco: dict) -> dict[str, str]:
    return dict(vr.explicacoes(risco))


def test_explica_as_seis_medidas_e_o_grafico():
    titulos = [t for t, _ in vr.explicacoes(_risco())]
    for sigla in ("TWR", "volatilidade", "drawdown", "VaR", "Sharpe", "beta"):
        assert any(sigla in t for t in titulos), sigla
    assert titulos[-1] == "O gráfico"


def test_explicacao_usa_os_valores_da_carteira():
    tx = _texto(_risco())
    assert "R\\$ 95,00" in tx["Rendimento (TWR)"]
    assert "R\\$ 108,00" in tx["Rendimento (TWR)"]
    assert "15,0%" in tx["Oscilação (volatilidade anual)"]
    assert "BOVA11" in tx["Oscilação (volatilidade anual)"]
    assert "20,0%" in tx["Oscilação (volatilidade anual)"]
    dd = tx["Queda máxima (drawdown)"]
    assert "12,0%" in dd and "15/08/2026" in dd and "10/09/2026" in dd
    assert "R\\$ 88,00" in dd and "6,0% abaixo" in dd
    assert "não declarou" in dd
    var = tx["Perda em dia ruim (VaR 95%)"]
    assert "1,40%" in var and "R\\$ 1,40 a cada" in var and "4,20%" in var
    assert "19 de cada 20" in var and "Não é a perda máxima" in var
    assert "-0,80" in tx["Retorno por risco (índice de Sharpe)"]
    assert "abaixo do CDI na média dos dias" in tx["Retorno por risco (índice de Sharpe)"]
    beta = tx["Sensibilidade à bolsa (beta)"]
    assert "0,60" in beta and "cerca de 6,0%" in beta


def test_todo_cifrao_sai_escapado_para_nao_virar_latex():
    # Dois "$" soltos no mesmo parágrafo viram fórmula no st.markdown.
    for titulo, texto in vr.explicacoes(_risco()):
        assert not re.search(r"(?<!\\)\$", texto), titulo


def test_medida_ausente_explica_sem_inventar_valor():
    vazio = _risco(twr=None, cdi_periodo=None, vol_anual=None, vol_benchmark=None,
                   max_drawdown=None, drawdown_atual=None, var_1d=None, var_1m=None,
                   sharpe=None, beta=None)
    for titulo, texto in vr.explicacoes(vazio):
        assert texto and "Na sua carteira" not in texto, titulo


def test_leituras_de_sharpe_e_beta_nos_tres_lados():
    assert vr._leitura_sharpe(-0.2) == "abaixo do CDI na média dos dias"
    assert "pouco" in vr._leitura_sharpe(0.5)
    assert "folga" in vr._leitura_sharpe(1.4)
    for s in (-0.2, 0.5, 1.4):
        assert "superou" not in vr._leitura_sharpe(s)


def test_sharpe_positivo_com_acumulado_abaixo_do_cdi_nao_diz_que_superou():
    # Sharpe é média do excesso DIÁRIO; TWR e CDI são acumulados. Com vaivém
    # forte os dois discordam: +2% e -1,95% alternados, CDI de 0,02% ao dia.
    rets = [0.02 if i % 2 == 0 else -0.0195 for i in range(20)]
    cdi = [0.0002] * 20
    s, tw = cr.sharpe(rets, cdi), cr.twr(rets)
    cdi_periodo = (1.0002 ** 20) - 1.0
    assert s > 0 and tw < cdi_periodo

    r = _risco(sharpe=s, twr=tw, cdi_periodo=cdi_periodo)
    sh = _texto(r)["Retorno por risco (índice de Sharpe)"]
    card = vr.cartoes(r)[4]
    assert "superou" not in sh and "superou" not in card
    assert "acima do CDI na média dos dias" in card
    assert "no acumulado do período" in sh and "menos que o CDI" in sh
    assert not re.search(r"(?<!\\)\$", sh)


def test_sharpe_que_concorda_com_o_acumulado_nao_ganha_ressalva():
    sh = _texto(_risco())["Retorno por risco (índice de Sharpe)"]
    assert "no acumulado do período" not in sh
    assert "direção contrária" in vr._leitura_beta(-0.3)
    assert "cerca de 3,0%" in vr._leitura_beta(-0.3)
    assert "subir ou cair cerca de 12,0%" in vr._leitura_beta(1.2)


def test_carteira_no_recorde_e_tolerancia_declarada():
    tx = _texto(_risco(max_drawdown=0.0, drawdown_atual=0.0, tolerancia_pct=15.0))
    dd = tx["Queda máxima (drawdown)"]
    assert "nunca ficou abaixo" in dd and "no maior valor" in dd
    assert "declarou aceitar é de 15%" in dd


def test_cards_dizem_o_que_medem_sem_formula():
    cards = vr.cartoes(_risco())
    assert len(cards) == 6
    juntos = " ".join(cards)
    for nome in ("Rendimento · TWR", "Oscilação · volatilidade anual",
                 "Queda máxima · drawdown", "Perda em dia ruim · VaR 95%",
                 "Retorno por risco · Sharpe", "Sensibilidade à bolsa · beta"):
        assert nome in juntos
    assert "√252" not in juntos and "excesso diário" not in juntos
    assert "o CDI rendeu 8,00%" in juntos
    assert "BOVA11 no período: 20,0%" in juntos
    assert "1 dia em cada 20" in juntos
    assert "Ibovespa ±10% → carteira ±6,0%" in juntos
    for card in cards:
        assert card.count("<div") == card.count("</div>")


def test_card_de_beta_negativo_diz_o_sentido():
    juntos = " ".join(vr.cartoes(_risco(beta=-0.3)))
    assert "Ibovespa +10% → carteira −3,0%" in juntos


def test_volatilidade_da_bolsa_vai_para_o_prompt():
    bloco = cr.bloco_risco_para_prompt(_risco(cobertura={}, excluidos=[]))
    assert "Volatilidade anualizada: 15.0% | BOVA11 no mesmo período: 20.0%" in bloco
