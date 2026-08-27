# -*- coding: utf-8 -*-
"""O gate de sobrevivencia da B3 era `strict_available: False` cravado.

Um criterio inalcancavel nunca e revisto: nenhuma ingestao, por melhor que
fosse, mudava o veredito, e por isso ninguem nunca soube o quanto faltava. Agora
ele compara cobertura medida contra um piso declarado -- e pode virar `True` no
dia em que a ingestao chegar la, que e a diferenca entre um gate e um literal.

O denominador e o ponto delicado: 1.912 cancelamentos de registro na CVM sao a
casca (Categoria B, companhia sem acao em bolsa, baixa anterior a 2010). A
populacao que importa sao 133 companhias. E a unidade tambem: 95 tickers
resolvidos nao sao 95 empresas -- sao 59, porque ON, PN e UNIT sao da mesma
companhia.
"""
from __future__ import annotations

import core.b3_validation as bv


def _com_cobertura(monkeypatch, cob):
    monkeypatch.setattr("core.survivorship_ingestion.cobertura_relevante",
                        lambda **k: cob)
    return bv._survivorship_status()


def test_o_gate_pode_virar_verdadeiro(monkeypatch):
    """A prova de que deixou de ser literal."""
    d = _com_cobertura(monkeypatch, {"relevantes": 133, "cobertas": 126,
                                     "share": 0.947, "tickers": 200})
    assert d["strict_available"] is True
    assert "completo" in d["reason"]


def test_abaixo_do_piso_reprova_dizendo_quanto_falta(monkeypatch):
    d = _com_cobertura(monkeypatch, {"relevantes": 133, "cobertas": 59,
                                     "share": 0.4436, "tickers": 95})
    assert d["strict_available"] is False
    assert "44%" in d["reason"] and "59 de 133" in d["reason"]


def test_sem_cadastro_em_cache_nao_afirma_reprovacao_por_medicao(monkeypatch):
    """Nao medido e diferente de medido e reprovado -- o gate diz qual dos dois."""
    d = _com_cobertura(monkeypatch, {"relevantes": 0, "cobertas": 0,
                                     "share": None, "tickers": 0})
    assert d["strict_available"] is False
    assert "nao medido" in d["reason"]


def test_o_bloqueador_carrega_o_motivo_medido():
    manifest = {"pit": {"strict_available": True},
                "survivorship": {"strict_available": False,
                                 "reason": "so 44% das companhias relevantes"}}
    pronto = bv.validation_readiness(manifest)
    assert pronto["ready"] is False
    assert "44%" in "; ".join(pronto["blockers"])


def test_piso_declarado_e_igual_ao_do_pit():
    """Baixar o piso para o gate passar seria rebaixar a regua, nao avancar."""
    assert bv.SURVIVORSHIP_SHARE_MINIMA == bv.PIT_SHARE_MINIMA == 0.90
