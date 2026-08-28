"""Cadeia de provedores LLM com fallback OpenRouter -> OpenAI -> Gemini.

Estes testes ja falharam de um jeito instrutivo: quando o OpenRouter entrou na
cadeia, eles continuavam substituindo apenas OpenAI e Gemini pelo nome. O
provedor novo ficou de pe, e a suite passou a fazer chamada de rede de verdade,
com a chave de verdade -- as assercoes quebraram exibindo resposta de um modelo
ao vivo. Um teste que neutraliza provedores um a um pelo nome envelhece mal: ele
nao falha quando a cadeia cresce, ele vaza.

Por isso a neutralizacao aqui e por padrao e vem da propria cadeia
(`_PROVEDORES`), e ha um teste que compara essa lista com os getters existentes
no modulo. Adicionar provedor sem cobri-lo passa a dar teste vermelho, que e o
sinal barato, em vez de trafego de rede silencioso.
"""
import types

import pytest

import core.llm_b3 as llm

# Os getters de cliente da cadeia. Manter em sincronia com `_provider_chain`;
# `test_todos_os_getters_estao_neutralizados` cobra essa sincronia.
_PROVEDORES = ("_get_openrouter_client", "_get_openai_client",
               "_get_gemini_client")


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
    """Desliga TODOS os provedores; cada teste religa o que quer exercitar.

    Padrao seguro: um provedor que este arquivo nao conhece nao pode alcancar a
    rede a partir daqui.
    """
    monkeypatch.setattr(llm, "_gemini_model", lambda: "gemini-test")
    monkeypatch.setattr(llm, "_openrouter_model", lambda _m=None: "or-test")
    for getter in _PROVEDORES:
        monkeypatch.setattr(llm, getter, lambda: None)
    yield


def test_todos_os_getters_estao_neutralizados():
    """O defeito que originou esta lista: provedor novo, teste cego, rede viva.

    Se alguem adicionar `_get_algum_client` sem incluir em `_PROVEDORES`, este
    teste falha antes que a suite comece a chamar a API de verdade.
    """
    encontrados = {n for n in dir(llm)
                   if n.startswith("_get_") and n.endswith("_client")}
    assert encontrados == set(_PROVEDORES)


def test_openrouter_e_o_primeiro_da_cadeia(monkeypatch):
    """Ordem importa: o provedor gratuito atende primeiro e so cai para os
    pagos quando falha, que e a razao de ele ter entrado."""
    rc = _FakeClient(content='{"ok": 1}')
    oc = _FakeClient(content='{"nao": "deveria"}')
    monkeypatch.setattr(llm, "_get_openrouter_client", lambda: rc)
    monkeypatch.setattr(llm, "_get_openai_client", lambda: oc)
    out = llm._chat_complete([{"role": "user", "content": "x"}], json_mode=True)
    assert out == '{"ok": 1}'
    assert rc.calls == 1 and oc.calls == 0


def test_openai_atende_quando_openrouter_falha(monkeypatch):
    """Provedor gratuito cai -- e a cadeia de tres elos existe por isso."""
    rc = _FakeClient(error=RuntimeError("503 upstream"))
    oc = _FakeClient(content='{"ok": 1}')
    monkeypatch.setattr(llm, "_get_openrouter_client", lambda: rc)
    monkeypatch.setattr(llm, "_get_openai_client", lambda: oc)
    out = llm._chat_complete([{"role": "user", "content": "x"}], json_mode=True)
    assert out == '{"ok": 1}'
    assert rc.calls >= 1 and oc.calls == 1


def test_openai_primario_responde(monkeypatch):
    oc = _FakeClient(content='{"ok": 1}')
    monkeypatch.setattr(llm, "_get_openai_client", lambda: oc)
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
    rc = _FakeClient(error=RuntimeError("503"))
    oc = _FakeClient(error=RuntimeError("429"))
    gc = _FakeClient(error=RuntimeError("500"))
    monkeypatch.setattr(llm, "_get_openrouter_client", lambda: rc)
    monkeypatch.setattr(llm, "_get_openai_client", lambda: oc)
    monkeypatch.setattr(llm, "_get_gemini_client", lambda: gc)
    with pytest.raises(RuntimeError, match="Todos os provedores"):
        llm._chat_complete([{"role": "user", "content": "x"}])


def test_sem_provedor_configurado():
    assert llm.llm_disponivel() is False
    assert llm.provedores_disponiveis() == []
    with pytest.raises(RuntimeError, match="Nenhum provedor"):
        llm._chat_complete([{"role": "user", "content": "x"}])


def test_provedores_disponiveis_lista_ordem(monkeypatch):
    monkeypatch.setattr(llm, "_get_openrouter_client", lambda: _FakeClient(content="r"))
    monkeypatch.setattr(llm, "_get_openai_client", lambda: _FakeClient(content="a"))
    monkeypatch.setattr(llm, "_get_gemini_client", lambda: _FakeClient(content="b"))
    assert llm.provedores_disponiveis() == ["openrouter", "openai", "gemini"]
