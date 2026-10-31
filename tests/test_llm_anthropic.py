"""Elo Claude (Anthropic) da cadeia de LLM.

O crédito é pago e finito: o Claude atende só quem está na tela (sessão do
navegador) e só o que o usuário pediu (``sob_demanda``); sem crédito, sai da
cadeia por 15 minutos e o Gemini responde. O protocolo é outro -- system
separado, sem ``temperature``, ferramentas com ``input_schema`` --, e a
tradução fica em ``core.llm_anthropic``. Nada aqui toca a rede: o cliente é
falso e o ``conftest`` desliga o getter real em toda a suíte.
"""
from __future__ import annotations

import copy
import types

import pytest

import core.llm_anthropic as la
import core.llm_b3 as llm
import core.llm_ferramentas as lf
from core.llm_falha import descrever_falha_llm
from core.llm_ferramentas import Ferramenta
from tests.test_llm_provider_fallback import _PROVEDORES

_MODELO = "claude-sonnet-5-5"
_SESSAO_REAL = llm._sessao_interativa

_ECO = Ferramenta("eco", "devolve o argumento",
                  {"type": "object", "properties": {"x": {"type": "string"}}},
                  lambda x="": f"resultado de {x}")


def _texto(conteudo):
    return types.SimpleNamespace(type="text", text=conteudo)


def _uso(i, nome, entrada):
    return types.SimpleNamespace(type="tool_use", id=f"t{i}", name=nome, input=entrada)


def _resp(*blocos, parada="end_turn"):
    return types.SimpleNamespace(
        content=list(blocos), model=_MODELO, stop_reason=parada,
        usage=types.SimpleNamespace(input_tokens=10, output_tokens=5,
                                    cache_read_input_tokens=0,
                                    cache_creation_input_tokens=0))


class _Fluxo:
    def __init__(self, resp):
        self._resp = resp

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self._resp


class _Claude:
    """Cliente falso da Anthropic: segue o roteiro (resposta ou exceção) na ordem."""

    def __init__(self, *roteiro, erro=None):
        self.pedidos: list[dict] = []
        self._roteiro = list(roteiro)
        self._erro = erro
        self.messages = types.SimpleNamespace(stream=self._stream)

    def _stream(self, **pedido):
        # Cópia: o laço de ferramentas continua acrescentando à mesma lista.
        self.pedidos.append(copy.deepcopy(pedido))
        item = self._erro if self._erro is not None else self._roteiro.pop(0)
        if isinstance(item, Exception):
            raise item
        return _Fluxo(item)


class _Gemini:
    """Elo de reserva no protocolo da OpenAI, sem chamar ferramenta."""

    def __init__(self, content="do gemini"):
        self.calls = 0
        self._content = content
        self.chat = types.SimpleNamespace(
            completions=types.SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls += 1
        msg = types.SimpleNamespace(content=self._content, tool_calls=None)
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg)],
                                     model=None)


class _ErroApi(Exception):
    def __init__(self, msg, status):
        super().__init__(msg)
        self.status_code = status


@pytest.fixture(autouse=True)
def _cadeia(monkeypatch):
    monkeypatch.setattr(llm, "_gemini_model", lambda: "gemini-test")
    monkeypatch.setattr(llm, "_anthropic_model", lambda: _MODELO)
    monkeypatch.setattr(llm, "_anthropic_effort", lambda: "medium")
    for getter in _PROVEDORES:
        monkeypatch.setattr(llm, getter, lambda: None)
    monkeypatch.setattr(llm, "_sessao_interativa", lambda: True)
    llm._limpar_estado_provedores()
    yield
    llm._limpar_estado_provedores()


def _ligar(monkeypatch, claude, gemini=None):
    gemini = gemini or _Gemini()
    monkeypatch.setattr(llm, "_get_anthropic_client", lambda: claude)
    monkeypatch.setattr(llm, "_get_gemini_client", lambda: gemini)
    return gemini


def _pergunta(texto="pergunta"):
    return [{"role": "system", "content": "SYS"}, {"role": "user", "content": texto}]


# ── quem entra na cadeia, e quando ───────────────────────────────────────────

def test_claude_vai_na_frente_so_em_sessao_do_navegador(monkeypatch):
    _ligar(monkeypatch, _Claude())
    assert [n for n, _c, _m in llm._provider_chain()] == ["anthropic", "gemini"]
    monkeypatch.setattr(llm, "_sessao_interativa", lambda: False)
    assert [n for n, _c, _m in llm._provider_chain()] == ["gemini"]


def test_sessao_do_navegador_e_o_contexto_de_execucao_do_streamlit(monkeypatch):
    """Rotina noturna, script e thread solta não têm contexto: o lote não gasta
    o crédito, e o portão PIT continua nos provedores em que foi medido."""
    from streamlit.runtime import scriptrunner

    assert _SESSAO_REAL() is False
    monkeypatch.setattr(scriptrunner, "get_script_run_ctx",
                        lambda suppress_warning=False: object())
    assert _SESSAO_REAL() is True


def test_dado_pessoal_pode_ir_ao_claude(monkeypatch):
    """A cadeia pessoal tira só modelo gratuito; o Claude é pago."""
    _ligar(monkeypatch, _Claude())
    assert [n for n, _c, _m in llm._provider_chain(pessoal=True)] == ["anthropic", "gemini"]


def test_chamada_automatica_da_tela_nao_gasta_o_credito(monkeypatch):
    claude = _Claude(_resp(_texto("do claude")))
    gemini = _ligar(monkeypatch, claude)
    assert llm._chat_complete(_pergunta(), sob_demanda=False) == "do gemini"
    assert claude.pedidos == [] and gemini.calls == 1


def test_explicacao_do_painel_de_mercado_e_chamada_automatica(monkeypatch):
    """Ela sai a cada recarga da página, sem o usuário pedir."""
    import core.inteligencia.llm as intel
    from tests.test_inteligencia_llm import painel_completo

    pedidos = []

    def _falso(messages, **kw):
        pedidos.append(kw)
        raise RuntimeError("fora do ar")

    monkeypatch.setattr(llm, "llm_disponivel", lambda: True)
    monkeypatch.setattr(llm, "_chat_complete", _falso)
    intel.explicar(painel_completo())
    assert pedidos and all(kw.get("sob_demanda") is False for kw in pedidos)


# ── a resposta ───────────────────────────────────────────────────────────────

def test_claude_responde_e_o_gemini_nao_e_chamado(monkeypatch):
    claude = _Claude(_resp(_texto("do claude")))
    gemini = _ligar(monkeypatch, claude)
    msgs = [{"role": "system", "content": "SYS1"}, {"role": "system", "content": "SYS2"},
            {"role": "user", "content": "pergunta"}]
    assert llm._chat_complete(msgs, temperature=0.2) == "do claude"
    assert gemini.calls == 0
    assert llm.ultimo_modelo() == f"anthropic/{_MODELO}"
    pedido = claude.pedidos[0]
    assert pedido["model"] == _MODELO
    assert "temperature" not in pedido  # o Sonnet 5.5 recusa com 400
    assert pedido["system"].startswith("SYS1") and "SYS2" in pedido["system"]
    assert pedido["messages"] == [{"role": "user", "content": "pergunta"}]
    assert pedido["output_config"] == {"effort": "medium"}
    assert pedido["cache_control"] == {"type": "ephemeral"}
    assert "tools" not in pedido


@pytest.mark.parametrize("erro", [
    _ErroApi("Your credit balance is too low to access the Anthropic API.", 400),
    _ErroApi("You have reached your specified API usage limits.", 400),
    _ErroApi("Payment required", 402),
    _ErroApi("invalid x-api-key", 401),
], ids=["credito", "limite-de-gasto", "402", "chave"])
def test_sem_credito_o_claude_sai_e_o_gemini_responde(monkeypatch, erro):
    claude = _Claude(erro=erro)
    gemini = _ligar(monkeypatch, claude)
    assert llm._chat_complete(_pergunta()) == "do gemini"
    assert llm.ultimo_modelo().startswith("gemini/")
    # Pausado: a pergunta seguinte nem tenta o Claude.
    assert llm._chat_complete(_pergunta("outra")) == "do gemini"
    assert len(claude.pedidos) == 1 and gemini.calls == 2


def test_falha_passageira_passa_a_vez_sem_pausar_o_claude(monkeypatch):
    claude = _Claude(erro=_ErroApi("Overloaded", 529))
    gemini = _ligar(monkeypatch, claude)
    assert llm._chat_complete(_pergunta()) == "do gemini"
    assert llm._chat_complete(_pergunta("outra")) == "do gemini"
    assert len(claude.pedidos) == 2 and gemini.calls == 2


def test_recusa_do_claude_e_falha_do_elo(monkeypatch):
    claude = _Claude(_resp(parada="refusal"))
    _ligar(monkeypatch, claude)
    assert llm._chat_complete(_pergunta()) == "do gemini"


def test_resposta_vazia_e_falha_do_elo(monkeypatch):
    claude = _Claude(_resp(_texto("  ")))
    _ligar(monkeypatch, claude)
    assert llm._chat_complete(_pergunta()) == "do gemini"


# ── modo JSON: não há response_format, há instrução e conferência ────────────

def test_modo_json_vira_instrucao_e_a_resposta_e_conferida(monkeypatch):
    claude = _Claude(_resp(_texto('```json\n{"ok": 1}\n```')))
    _ligar(monkeypatch, claude)
    assert llm._chat_complete(_pergunta(), json_mode=True) == '{"ok": 1}'
    pedido = claude.pedidos[0]
    assert la._INSTRUCAO_JSON in pedido["system"]
    assert "response_format" not in pedido


def test_json_que_nao_parseia_passa_a_vez(monkeypatch):
    claude = _Claude(_resp(_texto("não sei responder em JSON")))
    _ligar(monkeypatch, claude, _Gemini(content='{"g": 1}'))
    assert llm._chat_complete(_pergunta(), json_mode=True) == '{"g": 1}'


def test_extrair_json_tira_cerca_e_texto_em_volta():
    assert la.extrair_json('Segue:\n{"a": {"b": 2}}\nfim') == '{"a": {"b": 2}}'
    with pytest.raises(ValueError):
        la.extrair_json('{"a": }')
    with pytest.raises(ValueError):
        la.extrair_json("sem objeto")


# ── tradução das mensagens ───────────────────────────────────────────────────

def test_separar_junta_os_system_e_comeca_e_termina_no_usuario():
    system, conversa = la.separar([
        {"role": "system", "content": "A"},
        {"role": "assistant", "content": "olá"},
        {"role": "system", "content": "B"},
        {"role": "user", "content": "   "},
        {"role": "tool", "content": "x"},
        {"role": "user", "content": "pergunta"},
        {"role": "assistant", "content": "rascunho"},
    ])
    assert system == "A\n\nB"
    assert conversa == [{"role": "user", "content": la._ABERTURA},
                        {"role": "assistant", "content": "olá"},
                        {"role": "user", "content": "pergunta"},
                        {"role": "assistant", "content": "rascunho"},
                        {"role": "user", "content": la._CONTINUAR}]


def test_separar_sem_conversa_ainda_abre_com_o_usuario():
    system, conversa = la.separar([{"role": "system", "content": "só contexto"}])
    assert system == "só contexto"
    assert conversa == [{"role": "user", "content": la._ABERTURA}]


# ── ferramentas ──────────────────────────────────────────────────────────────

def test_laco_de_ferramentas_devolve_o_content_intacto(monkeypatch):
    pensamento = types.SimpleNamespace(type="thinking", thinking="...", signature="sig")
    chamada = _uso(1, "eco", {"x": "ITUB4"})
    claude = _Claude(_resp(pensamento, chamada, parada="tool_use"),
                     _resp(_texto("resposta final")))
    _ligar(monkeypatch, claude)
    assert llm._chat_complete(_pergunta(), ferramentas=[_ECO]) == "resposta final"

    primeiro, segundo = claude.pedidos
    assert primeiro["tools"] == [{"name": "eco", "description": "devolve o argumento",
                                  "input_schema": dict(_ECO.parametros)}]
    assert lf.REGRA_FERRAMENTAS in primeiro["system"]
    assert "tool_choice" not in primeiro and "tool_choice" not in segundo
    assistente, resultado = segundo["messages"][-2:]
    assert assistente == {"role": "assistant", "content": [pensamento, chamada]}
    assert resultado == {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t1", "content": "resultado de ITUB4"}]}
    assert llm.ultimas_ferramentas() == [
        {"nome": "eco", "argumentos": '{"x": "ITUB4"}', "chars": len("resultado de ITUB4")}]


def test_teto_de_chamadas_e_ultima_rodada_sem_ferramenta(monkeypatch):
    monkeypatch.setattr(llm, "MAX_RODADAS_FERRAMENTA", 1)
    monkeypatch.setattr(llm, "MAX_CHAMADAS_POR_RODADA", 1)
    claude = _Claude(_resp(_uso(1, "eco", {"x": "A"}), _uso(2, "eco", {"x": "B"}),
                           parada="tool_use"),
                     _resp(_texto("fim")))
    _ligar(monkeypatch, claude)
    assert llm._chat_complete(_pergunta(), ferramentas=[_ECO]) == "fim"

    primeiro, segundo = claude.pedidos
    resultados = segundo["messages"][-1]["content"]
    assert resultados[0]["content"] == "resultado de A"
    assert "teto de 1" in resultados[1]["content"]
    assert "tool_choice" not in primeiro
    assert segundo["tool_choice"] == {"type": "none"}
    assert [u["nome"] for u in llm.ultimas_ferramentas()] == ["eco"]


def test_ferramentas_recusadas_saem_e_a_resposta_vem_sem_elas(monkeypatch):
    claude = _Claude(_ErroApi("tools: parâmetro inválido", 400),
                     _resp(_texto("sem ferramentas")))
    gemini = _ligar(monkeypatch, claude)
    assert llm._chat_complete(_pergunta(), ferramentas=[_ECO]) == "sem ferramentas"
    assert "tools" in claude.pedidos[0] and "tools" not in claude.pedidos[1]
    assert ("anthropic", _MODELO) in llm._SEM_TOOLS
    assert llm.ultimas_ferramentas() == [] and gemini.calls == 0


def test_sem_credito_no_laco_de_ferramentas_nao_repete_sem_elas(monkeypatch):
    claude = _Claude(erro=_ErroApi("Your credit balance is too low", 400))
    gemini = _ligar(monkeypatch, claude)
    assert llm._chat_complete(_pergunta(), ferramentas=[_ECO]) == "do gemini"
    assert len(claude.pedidos) == 1 and gemini.calls == 1
    assert ("anthropic", _MODELO) not in llm._SEM_TOOLS


# ── o que a tela diz ─────────────────────────────────────────────────────────

def test_tela_nomeia_o_claude_sem_credito():
    exc = RuntimeError("Todos os provedores LLM falharam — "
                       f"anthropic({_MODELO}): Your credit balance is too low | "
                       "gemini(gemini-test): 503 unavailable")
    motivo = descrever_falha_llm(exc)
    assert "Claude: limite de uso ou falta de crédito" in motivo
    assert "Gemini: falhou" in motivo


@pytest.mark.parametrize("modulo", ["design.chat_ativo", "design.chat_carteira"])
def test_chat_nomeia_todo_provedor_da_cadeia(modulo):
    # A produção listou "Provedor disponível: anthropic, OpenAI, Gemini".
    import importlib

    from core.llm_falha import _NOME_PROVEDOR

    rotulos = importlib.import_module(modulo)._PROVEDOR_LABEL
    assert {p: rotulos.get(p) for p in _NOME_PROVEDOR} == _NOME_PROVEDOR


def test_so_a_chave_da_anthropic_ja_liga_a_ia():
    from core.config import Settings

    so_claude = types.SimpleNamespace(ANTHROPIC_API_KEY="k", OPENROUTER_API_KEY="",
                                      OPENAI_API_KEY="", GEMINI_API_KEY="")
    assert Settings.has_anthropic.fget(so_claude)
    assert Settings.has_llm.fget(so_claude)
