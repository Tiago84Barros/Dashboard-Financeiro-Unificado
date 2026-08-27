# -*- coding: utf-8 -*-
"""A-155: um gate que a primeira linha boa desarma nao e gate.

`strict_available` era `published_at_rows > 0`. Enquanto nenhuma linha podia ter
essa qualidade, dava False sempre e o defeito ficava invisivel. Assim que a
ingestao da CVM passou a produzi-la, UMA linha em 80 mil promoveria a base
inteira a "PIT estrito".
"""
from __future__ import annotations

from core.b3_validation import PIT_SHARE_MINIMA, validation_readiness


def _manifesto(share=None, strict=False, survivorship=False):
    pit = {"strict_available": strict}
    if share is not None:
        pit["annual_published_share"] = share
    return {"pit": pit, "survivorship": {"strict_available": survivorship}}


def test_pit_e_deslistadas_aprovados_liberam_a_validacao():
    r = validation_readiness(_manifesto(0.96, strict=True, survivorship=True))
    assert r["ready"] and r["blockers"] == []


def test_cobertura_parcial_nomeia_a_fatia_medida():
    """Bloqueador que nao diz o numero nao diz o que falta para sair dele."""
    (b,) = validation_readiness(_manifesto(0.42))["blockers"][:1]
    assert "42%" in b and f"{PIT_SHARE_MINIMA * 100:.0f}%" in b


def test_sem_medicao_alguma_mantem_a_mensagem_de_fonte_ausente():
    """Zero nao e '0% medido'; e 'a fonte nunca foi consultada'."""
    (b,) = validation_readiness(_manifesto(0.0))["blockers"][:1]
    assert "sem published_at" in b
    assert "%" not in b


def test_o_piso_e_menor_que_cem_por_cento():
    """A base da CVM comeca em 2010 e o exercicio corrente nao foi protocolado;
    exigir 100% seria exigir que fonte inexistente existisse."""
    assert 0.5 < PIT_SHARE_MINIMA < 1.0


def test_deslistadas_pendente_bloqueia_mesmo_com_pit_aprovado():
    r = validation_readiness(_manifesto(0.99, strict=True, survivorship=False))
    assert not r["ready"]
    assert r["blockers"] == ["universo historico de deslistadas incompleto"]


def test_manifesto_sem_bloco_pit_nao_quebra():
    assert validation_readiness({})["blockers"]
