# -*- coding: utf-8 -*-
"""A-153: dois rotulos iguais medindo coisas diferentes.

O componente "Metodologia validada" da secao EUA media a IDADE DA VITRINE. A
pergunta "foi gerada pelo ranqueador de hoje?" e legitima, mas nao e "a formula
foi verificada fora da amostra?". Sob o rotulo errado o EUA tirava 100 nesse
item enquanto a B3, que le `validation_readiness` de verdade, tirava 50: a
classe que TEM a medicao pontuava pior que a que nao tinha.
"""
from __future__ import annotations

import core.confianca_secao as cs
from core.validacao_motor import EstadoValidacao


def _comp(secao, nome):
    return next((c for c in secao.componentes if c.nome == nome), None)


def _us(monkeypatch, estado):
    monkeypatch.setattr("core.validacao_motor.validacao_us", lambda *a, **k: estado)
    return cs.confianca_us()


def test_as_duas_perguntas_viraram_dois_componentes():
    s = cs.confianca_us()
    assert _comp(s, "Vitrine na versao corrente") is not None
    assert _comp(s, "Metodologia validada") is not None


def test_o_peso_do_bloco_foi_repartido_e_nao_ampliado():
    s = cs.confianca_us()
    bloco = (_comp(s, "Vitrine na versao corrente").peso
             + _comp(s, "Metodologia validada").peso)
    assert abs(bloco - 0.25) < 1e-9


def test_a_soma_dos_pesos_continua_um():
    s = cs.confianca_us()
    assert abs(sum(c.peso for c in s.componentes) - 1.0) < 1e-9


def test_vitrine_fresca_nao_afirma_mais_metodologia_validada(monkeypatch):
    """Vitrine gerada hoje com PIT pendente: um componente alto, outro baixo."""
    s = _us(monkeypatch, EstadoValidacao("Empresas Americanas", "0.5.0", False,
                                         ("sem score_vintages",)))
    assert _comp(s, "Metodologia validada").pct == 50.0
    assert "score_vintages" in _comp(s, "Metodologia validada").evidencia


def test_pit_aprovado_vale_cem(monkeypatch):
    s = _us(monkeypatch, EstadoValidacao("Empresas Americanas", "0.5.0", True))
    assert _comp(s, "Metodologia validada").pct == 100.0


def test_nao_apurado_nao_vira_zero_nem_cem(monkeypatch):
    """`pct=None` sai da media ponderada e se declara -- e a regra do modulo."""
    s = _us(monkeypatch, EstadoValidacao("Empresas Americanas", "0.5.0", None,
                                         detalhe="nao apurado: RuntimeError"))
    c = _comp(s, "Metodologia validada")
    assert c.pct is None and c.medido is False


def test_b3_e_eua_medem_a_mesma_coisa_sob_o_mesmo_rotulo():
    """Era o defeito: mesmo nome, uma lia PIT e a outra lia idade de vitrine."""
    for secao in (cs.confianca_b3(), cs.confianca_us()):
        c = _comp(secao, "Metodologia validada")
        assert c is not None and c.pct in (None, 50.0, 100.0)
