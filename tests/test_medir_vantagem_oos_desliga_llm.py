# -*- coding: utf-8 -*-
"""O desligamento do LLM na medição tem que cobrir provedor que ninguém previu.

Em 14/09/2026 a remedição da B3 (metodologia 2.26.0) estourou os 1800s do
``AppTest`` sem gravar nada. ``_desligar_llm`` zerava ``_get_openai_client`` e
``_get_gemini_client`` **por nome**; quando o OpenRouter entrou na cadeia de
``core.llm_b3`` ele não estava na lista -- e, por ir na frente, era justamente
quem respondia. Cada empresa da aba virava uma geração real pela rede.

A lista branca só cobre o que alguém lembrou de escrever nela
(``memoria: lista-branca-perde-a-chave-nao-prevista``). O teste abaixo não
confere os nomes dos provedores: confere a propriedade que importa -- depois de
desligar, a cadeia que ``_chat_complete`` consulta está vazia, qualquer que seja
o provedor configurado.
"""
from __future__ import annotations

import pytest

import core.llm_b3 as llm
from scripts.medir_vantagem_oos import _desligar_llm


@pytest.fixture(autouse=True)
def _restaura_cadeia():
    original = llm._provider_chain
    yield
    llm._provider_chain = original


def test_desligar_llm_esvazia_a_cadeia_qualquer_que_seja_o_provedor(monkeypatch):
    """Provedor inventado, que nenhuma lista branca conteria, também cai."""
    monkeypatch.setattr(
        llm, "_provider_chain",
        lambda *a, **k: [("provedor_novo_qualquer", object(), "modelo-x")])
    assert llm._provider_chain(), "pré-condição: a cadeia começa povoada"

    _desligar_llm()

    assert llm._provider_chain() == []
    assert llm.llm_disponivel() is False
    assert llm.provedores_disponiveis() == []


def test_chamada_falha_de_imediato_em_vez_de_ir_para_a_rede(monkeypatch):
    """Sem cadeia, `_chat_complete` levanta na hora -- não trava em backoff.

    Travar sem falhar é o pior dos dois mundos: a medição não termina e também
    não diz que não terminou.
    """
    def _explode(*a, **k):  # pragma: no cover - não deve ser alcançado
        raise AssertionError("a medição tentou falar com a rede")

    monkeypatch.setattr(
        llm, "_provider_chain",
        lambda *a, **k: [("fake", type("C", (), {"chat": _explode})(), "m")])
    _desligar_llm()

    with pytest.raises(RuntimeError):
        llm._call_llm("qualquer prompt")


def test_hook_ausente_falha_alto_em_vez_de_medir_pela_rede(monkeypatch):
    """Se `_provider_chain` sumir, a medição para -- não volta a pendurar.

    Degradar em silêncio aqui devolveria exatamente o defeito de 14/09: uma
    medição que parece rodar e passa meia hora falando com a rede.
    """
    monkeypatch.delattr(llm, "_provider_chain", raising=True)
    with pytest.raises(SystemExit, match="_provider_chain"):
        _desligar_llm()
