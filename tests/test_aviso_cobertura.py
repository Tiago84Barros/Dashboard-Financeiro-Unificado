# -*- coding: utf-8 -*-
"""A-154: cobertura declarada onde a recomendacao aparece.

`core.universo_decisao` media as tres populacoes desde A-125 e o unico
consumidor era o relatorio de confianca. Quem via o ranking nao sabia por
quantos ativos a nota falava.
"""
from __future__ import annotations

import design.componentes as dc
from core.universo_decisao import Universo


class _Cap:
    def __init__(self):
        self.textos = []

    def __call__(self, txt):
        self.textos.append(txt)


def _render(monkeypatch, universo=None, erro=None):
    cap = _Cap()
    monkeypatch.setattr(dc.st, "caption", cap)

    def fake(_modulo):
        if erro is not None:
            raise erro
        return universo

    monkeypatch.setattr(dc, "_universo_cacheado", fake)
    dc.aviso_cobertura_do_universo("us")
    return cap.textos


def test_declara_numerador_denominador_e_preco(monkeypatch):
    u = Universo("Empresas Americanas", 2831, 2831, 874,
                 notas=("gate: score_status = decision_grade",))
    (txt,) = _render(monkeypatch, u)
    assert "874 de 2831" in txt
    assert "31%" in txt
    assert "1957 descartados" in txt


def test_casca_de_cadastro_nao_e_confundida_com_descarte(monkeypatch):
    """nominal -> investivel nunca foi ativo; nao pode contar como perda."""
    u = Universo("Selecao de FIIs", 1200, 432, 349)
    (txt,) = _render(monkeypatch, u)
    assert "349 de 432" in txt
    assert "768 cascas" in txt
    assert "768 descartados" not in txt


def test_universo_abaixo_do_piso_diz_isso_em_vez_de_percentual(monkeypatch):
    u = Universo("Empresas B3", 400, 380, 12)
    (txt,) = _render(monkeypatch, u)
    assert "abaixo do piso" in txt


def test_fonte_fora_do_ar_nao_derruba_a_tela(monkeypatch):
    """Cobertura e contexto da recomendacao, nao a recomendacao."""
    assert _render(monkeypatch, erro=RuntimeError("sem banco")) == []


def test_universo_vazio_nao_afirma_cobertura_zero(monkeypatch):
    """0 de 0 nao e 'nao cobrimos nada'; e 'nao medimos'."""
    assert _render(monkeypatch, Universo("Empresas B3", 0, 0, 0)) == []


def test_a_nota_do_gate_preserva_siglas(monkeypatch):
    """`.capitalize()` minusculizava o resto: DY, P/VP e B3 viravam dy, p/vp, b3."""
    u = Universo("Selecao de FIIs", 1200, 432, 349,
                 notas=("gate: preco, DY, P/VP e liquidez, arbitrada contra a B3",))
    (txt,) = _render(monkeypatch, u)
    assert "DY, P/VP" in txt and "B3" in txt
    assert txt.count("Gate:") == 1
