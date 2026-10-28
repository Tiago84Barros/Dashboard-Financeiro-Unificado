"""Modelo gratuito que some não derruba o chat, e a tela diz o porquê certo.

Em 26/10/2026 o endpoint do ``nemotron-3-super-120b-a12b:free`` sumiu na NVIDIA
(404 "Function not found") com o modelo ainda listado no OpenRouter. O elo
inteiro caía, a OpenAI sem crédito caía em seguida, e o chat dizia "limite de
uso ou falta de crédito" -- o motivo do segundo provedor, não o do primeiro.
"""
from __future__ import annotations

import types

import pytest

import core.llm_b3 as llm
from core.llm_falha import descrever_falha_llm
from tests.test_llm_provider_fallback import _PROVEDORES

_GRATIS = "nvidia/nemotron-3.5-super-vl:free"


class _Cliente:
    """Guarda os kwargs de cada chamada e responde como o modelo ``real``."""

    def __init__(self, content="ok", real=None, error=None):
        self.kwargs: list[dict] = []
        self._content, self._real, self._error = content, real, error
        self.chat = types.SimpleNamespace(
            completions=types.SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.kwargs.append(kwargs)
        if self._error is not None:
            raise self._error
        msg = types.SimpleNamespace(message=types.SimpleNamespace(
            content=self._content, tool_calls=None))
        return types.SimpleNamespace(choices=[msg], model=self._real)


@pytest.fixture(autouse=True)
def _sem_rede(monkeypatch):
    monkeypatch.setattr(llm, "_gemini_model", lambda: "gemini-test")
    for getter in _PROVEDORES:
        monkeypatch.setattr(llm, getter, lambda: None)
    llm._limpar_estado_provedores()
    yield
    llm._limpar_estado_provedores()


def test_padrao_nao_e_mais_o_modelo_que_sumiu():
    assert "nemotron-3-super-120b" not in llm._OPENROUTER_MODEL_DEFAULT
    assert llm._OPENROUTER_MODEL_DEFAULT in llm._OPENROUTER_RESERVA_GRATUITA
    assert all(m.endswith(":free") for m in llm._OPENROUTER_RESERVA_GRATUITA)


def test_modelo_gratuito_do_openrouter_vai_com_a_reserva(monkeypatch):
    rc = _Cliente(real="nvidia/nemotron-3-ultra-550b-a55b:free")
    monkeypatch.setattr(llm, "_get_openrouter_client", lambda: rc)
    monkeypatch.setattr(llm, "_openrouter_model", lambda _m=None: "dono/morto:free")

    assert llm._chat_complete([{"role": "user", "content": "oi"}]) == "ok"
    modelos = rc.kwargs[0]["extra_body"]["models"]
    assert modelos[0] == "dono/morto:free"
    assert modelos[1:] == list(llm._OPENROUTER_RESERVA_GRATUITA)[:2]
    assert len(modelos) <= 3  # teto do OpenRouter
    # a auditoria registra quem respondeu, não quem foi pedido
    assert llm.ultimo_modelo() == "openrouter/nvidia/nemotron-3-ultra-550b-a55b:free"


def test_reserva_nao_repete_o_modelo_pedido(monkeypatch):
    rc = _Cliente()
    monkeypatch.setattr(llm, "_get_openrouter_client", lambda: rc)
    monkeypatch.setattr(llm, "_openrouter_model", lambda _m=None: _GRATIS)
    llm._chat_complete([{"role": "user", "content": "oi"}], json_mode=True)
    modelos = rc.kwargs[0]["extra_body"]["models"]
    assert modelos.count(_GRATIS) == 1
    assert rc.kwargs[0]["response_format"] == {"type": "json_object"}


def test_modelo_pago_e_outros_provedores_nao_ganham_reserva_gratuita(monkeypatch):
    rc, oc = _Cliente(error=RuntimeError("caiu")), _Cliente()
    monkeypatch.setattr(llm, "_get_openrouter_client", lambda: rc)
    monkeypatch.setattr(llm, "_get_openai_client", lambda: oc)
    monkeypatch.setattr(llm, "_openrouter_model", lambda _m=None: "anthropic/pago")
    llm._chat_complete([{"role": "user", "content": "oi"}])
    assert "extra_body" not in rc.kwargs[0]
    assert "extra_body" not in oc.kwargs[0]
    assert llm.ultimo_modelo() == "openai/gpt-4o-mini"


def test_reserva_tambem_vale_no_laco_de_ferramentas(monkeypatch):
    from core.llm_ferramentas import Ferramenta

    rc = _Cliente()
    monkeypatch.setattr(llm, "_get_openrouter_client", lambda: rc)
    monkeypatch.setattr(llm, "_openrouter_model", lambda _m=None: _GRATIS)
    ferramenta = Ferramenta("serie", "x", {"type": "object", "properties": {}},
                            lambda **_: "1")
    llm._chat_complete([{"role": "user", "content": "oi"}], ferramentas=[ferramenta])
    assert "tools" in rc.kwargs[0]
    assert rc.kwargs[0]["extra_body"]["models"][0] == _GRATIS


def test_mensagem_da_cadeia_nomeia_cada_provedor():
    exc = RuntimeError(
        "Todos os provedores LLM falharam — "
        "openrouter(nvidia/nemotron-3-super-120b-a12b:free): Error code: 404 - "
        "{'error': {'message': 'Provider returned error', 'code': 404}} | "
        "openai(gpt-4o-mini): Error code: 429 - insufficient_quota | "
        "gemini(gemini-test): Request timed out.")
    msg = descrever_falha_llm(exc)
    assert msg == ("nenhum provedor de IA respondeu (OpenRouter: modelo fora do ar; "
                   "OpenAI: limite de uso ou falta de crédito; Gemini: demorou demais)")
    assert "insufficient_quota" not in msg and "nemotron" not in msg


def test_um_provedor_so_com_404_diz_modelo_fora_do_ar():
    assert descrever_falha_llm(RuntimeError("Error code: 404 - not found")) == \
        "o modelo de IA escolhido está fora do ar"
