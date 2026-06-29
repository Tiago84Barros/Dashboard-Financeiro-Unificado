"""Cadeia de provedores LLM com fallback OpenAI → Gemini (core.llm_b3)."""
import types

import pytest

import core.llm_b3 as llm


class _FakeMsg:
    def __init__(self, content): self.message = types.SimpleNamespace(content=content)


class _FakeResp:
    def __init__(self, content): self.choices = [_FakeMsg(content)]


class _FakeClient:
    """Cliente que responde com `content` ou levanta `error` (simula 429)."""
    def __init__(self, content=None, error=None):
        self.calls = 0
        self._content = content
        self._error = error
        self.chat = types.SimpleNamespace(
            completions=types.SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls += 1
        if self._error is not None:
            raise self._error
        return _FakeResp(self._content)


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    # neutraliza modelo do Gemini (evita ler config)
    monkeypatch.setattr(llm, "_gemini_model", lambda: "gemini-test")
    yield


def test_openai_primario_responde(monkeypatch):
    oc = _FakeClient(content='{"ok": 1}')
    monkeypatch.setattr(llm, "_get_openai_client", lambda: oc)
    monkeypatch.setattr(llm, "_get_gemini_client", lambda: None)
    assert llm._chat_complete([{"role": "user", "content": "x"}], json_mode=True) == '{"ok": 1}'
    assert oc.calls == 1


def test_fallback_para_gemini_quando_openai_falha(monkeypatch):
    oc = _FakeClient(error=RuntimeError("429 insufficient_quota"))
    gc = _FakeClient(content="resposta-gemini")
    monkeypatch.setattr(llm, "_get_openai_client", lambda: oc)
    monkeypatch.setattr(llm, "_get_gemini_client", lambda: gc)
    out = llm._chat_complete([{"role": "user", "content": "x"}], json_mode=False)
    assert out == "resposta-gemini"
    assert oc.calls >= 1 and gc.calls == 1


def test_erro_quando_todos_falham(monkeypatch):
    oc = _FakeClient(error=RuntimeError("429"))
    gc = _FakeClient(error=RuntimeError("500"))
    monkeypatch.setattr(llm, "_get_openai_client", lambda: oc)
    monkeypatch.setattr(llm, "_get_gemini_client", lambda: gc)
    with pytest.raises(RuntimeError, match="Todos os provedores"):
        llm._chat_complete([{"role": "user", "content": "x"}])


def test_sem_provedor_configurado(monkeypatch):
    monkeypatch.setattr(llm, "_get_openai_client", lambda: None)
    monkeypatch.setattr(llm, "_get_gemini_client", lambda: None)
    assert llm.llm_disponivel() is False
    assert llm.provedores_disponiveis() == []
    with pytest.raises(RuntimeError, match="Nenhum provedor"):
        llm._chat_complete([{"role": "user", "content": "x"}])


def test_provedores_disponiveis_lista_ordem(monkeypatch):
    monkeypatch.setattr(llm, "_get_openai_client", lambda: _FakeClient(content="a"))
    monkeypatch.setattr(llm, "_get_gemini_client", lambda: _FakeClient(content="b"))
    assert llm.provedores_disponiveis() == ["openai", "gemini"]
