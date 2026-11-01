"""Explicação do Rank-IC: uma casa só, leitura coerente com a escala."""
from __future__ import annotations

import math

import pytest

from core import glossario as g


@pytest.mark.parametrize("valor, rotulo", [
    (0.15, "forte"),
    (0.10, "forte"),
    (0.092, "moderado"),
    (0.05, "moderado"),
    (0.03, "fraco"),
    (0.0, "nulo"),
    (-0.02, "nulo"),
    (-0.05, "invertido"),
])
def test_faixas_de_leitura(valor, rotulo):
    assert g.faixa_rank_ic(valor)[0] == rotulo


@pytest.mark.parametrize("valor", [None, float("nan"), float("inf"), "x"])
def test_valor_invalido_nao_inventa_leitura(valor):
    assert g.faixa_rank_ic(valor) is None
    assert g.leitura_rank_ic(valor) == ""
    assert g.acerto_em_pares(valor) is None


def test_acerto_em_pares_e_moeda_no_zero_e_monotono():
    assert g.acerto_em_pares(0.0) == pytest.approx(0.5)
    assert g.acerto_em_pares(1.0) == pytest.approx(1.0)
    assert g.acerto_em_pares(-1.0) == pytest.approx(0.0, abs=1e-6)
    valores = [g.acerto_em_pares(x / 100) for x in range(-100, 101, 5)]
    assert all(b > a for a, b in zip(valores, valores[1:]))


def test_valor_da_tela_vira_frase_concreta():
    # O caso do print: +0,092 no universo da B3.
    texto = g.leitura_rank_ic(0.092, periodo="no ano seguinte")
    assert "+0.092" in texto and "moderado" in texto
    assert "53%" in texto and "no ano seguinte" in texto


def test_explicacao_diz_que_nao_e_retorno():
    texto = g.explicacao_rank_ic(0.092)
    assert "não é retorno" in texto.lower() or "NÃO diz" in texto
    assert "−1 a +1" in texto
    assert "Spearman" in texto


def test_faixas_em_ordem_decrescente():
    limites = [lim for lim, _, _ in g.FAIXAS_RANK_IC]
    assert limites == sorted(limites, reverse=True)
    assert all(math.isfinite(x) for x in limites)


def test_colunas_de_intervalo_nao_usam_mais_a_sigla_ic():
    # "IC 95%" colidia com o "IC" de Rank-IC na mesma tela.
    from pathlib import Path
    raiz = Path(__file__).resolve().parents[1]
    for arq in ("views/portfolio_b3_oos_carteira.py", "views/investimentos.py"):
        fonte = (raiz / arq).read_text(encoding="utf-8")
        assert '"IC 95%"' not in fonte, arq
        assert '"Intervalo 95%"' in fonte, arq


def test_intervalos_explicam_o_cruzar_o_zero():
    for texto in (g.INTERVALO_95_EXCESSO, g.INTERVALO_95_CORRELACAO):
        assert texto.startswith("Intervalo 95%:")
        assert "cruza o zero" in texto and "95% de confiança" in texto
