"""Bloco <lacunas> da LLM: sai da resposta e entra no log."""
import json
import types

import pytest

import core.llm_b3 as llm_b3
from core.lacunas import destino, registro
from core.lacunas.llm import (
    CODIGO_MALFORMADA,
    INSTRUCAO_LACUNAS,
    MAX_POR_RESPOSTA,
    extrair_lacunas,
    processar_resposta,
)


@pytest.fixture
def arquivo(tmp_path, monkeypatch):
    caminho = tmp_path / "eventos.jsonl"
    monkeypatch.setattr(destino, "ARQUIVO_LOCAL", caminho)
    monkeypatch.setenv("LACUNAS_DESTINO", "local")
    registro._limpar_vistas()
    yield caminho
    registro._limpar_vistas()


def _eventos(caminho):
    if not caminho.exists():
        return []
    return [json.loads(linha) for linha in caminho.read_text(encoding="utf-8").splitlines()]


RESPOSTA = (
    "A Selic está em 15%.\n\n"
    "<lacunas>\n"
    '{"codigo": "fii.vpa historico", "mensagem": "sem VPA de 2019", "entidade": "hglg11"}\n'
    '{"codigo": "macro.focus", "mensagem": "sem Focus da semana"}\n'
    "</lacunas>"
)


# ── extrair_lacunas ──────────────────────────────────────────────────────────

def test_sem_bloco_devolve_o_texto_intacto():
    assert extrair_lacunas("só texto") == ("só texto", [])


def test_bloco_sai_do_texto_e_vira_itens():
    limpo, itens = extrair_lacunas(RESPOSTA)
    assert limpo == "A Selic está em 15%."
    assert [i["codigo"] for i in itens] == ["fii.vpa historico", "macro.focus"]


def test_bloco_cortado_no_fim_tambem_sai():
    limpo, itens = extrair_lacunas('Texto.\n<lacunas>\n{"mensagem": "x"}')
    assert limpo == "Texto."
    assert itens == [{"mensagem": "x"}]


def test_lista_json_unica_e_aceita():
    _, itens = extrair_lacunas('<lacunas>[{"mensagem": "a"}, {"mensagem": "b"}]</lacunas>')
    assert [i["mensagem"] for i in itens] == ["a", "b"]


def test_linha_invalida_vira_malformada():
    _, itens = extrair_lacunas("<lacunas>\nfaltou o balanço\n</lacunas>")
    assert itens == [{"_malformada": "faltou o balanço"}]


def test_teto_por_resposta():
    linhas = "\n".join(json.dumps({"mensagem": f"m{i}"}) for i in range(30))
    _, itens = extrair_lacunas(f"<lacunas>\n{linhas}\n</lacunas>")
    assert len(itens) == MAX_POR_RESPOSTA


# ── processar_resposta ───────────────────────────────────────────────────────

def test_registra_com_codigo_normalizado_e_entidade(arquivo):
    limpo = processar_resposta(RESPOSTA, modulo="core/llm_fii.py:chat")
    assert "<lacunas>" not in limpo
    evs = _eventos(arquivo)
    assert [e["codigo"] for e in evs] == ["llm.fii.vpa_historico", "llm.macro.focus"]
    assert evs[0]["entidade"] == "HGLG11"
    assert {e["fonte"] for e in evs} == {"llm"}
    assert {e["modulo"] for e in evs} == {"core/llm_fii.py:chat"}


def test_malformada_vai_para_a_fila(arquivo):
    processar_resposta("ok\n<lacunas>\nfaltou tudo\n</lacunas>", modulo="m")
    (ev,) = _eventos(arquivo)
    assert ev["codigo"] == CODIGO_MALFORMADA


def test_item_sem_mensagem_e_ignorado(arquivo):
    processar_resposta('ok<lacunas>{"codigo": "x"}</lacunas>', modulo="m")
    assert _eventos(arquivo) == []


def test_falha_no_registro_nao_perde_a_resposta(arquivo, monkeypatch):
    import core.lacunas.llm as mod
    monkeypatch.setattr(mod, "registrar_lacuna", lambda *a, **k: 1 / 0)
    assert processar_resposta(RESPOSTA, modulo="m") == "A Selic está em 15%."


# ── _chat_complete ───────────────────────────────────────────────────────────

class _Cliente:
    def __init__(self, resposta):
        self.recebido = None
        self._resposta = resposta
        self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.recebido = kw
        msg = types.SimpleNamespace(message=types.SimpleNamespace(content=self._resposta))
        return types.SimpleNamespace(choices=[msg])


@pytest.fixture
def cliente(monkeypatch):
    c = _Cliente(RESPOSTA)
    monkeypatch.setattr(llm_b3, "_provider_chain", lambda _m=None: [("openai", c, "m")])
    return c


def _quem_chama_a_llm(mensagens, **kw):
    return llm_b3._chat_complete(mensagens, **kw)


def test_chat_complete_devolve_sem_bloco_e_registra_quem_chamou(arquivo, cliente):
    texto = _quem_chama_a_llm([{"role": "system", "content": "S"},
                               {"role": "user", "content": "P"}])
    assert texto == "A Selic está em 15%."
    evs = _eventos(arquivo)
    assert len(evs) == 2
    assert {e["modulo"] for e in evs} == {"tests/test_lacunas_llm.py:_quem_chama_a_llm"}


def test_instrucao_vai_no_system_sem_alterar_a_lista_original(arquivo, cliente):
    originais = [{"role": "system", "content": "S"}, {"role": "user", "content": "P"}]
    _quem_chama_a_llm(originais)
    enviado = cliente.recebido["messages"]
    assert enviado[0]["content"].startswith("S")
    assert INSTRUCAO_LACUNAS in enviado[0]["content"]
    assert originais[0]["content"] == "S"


def test_sem_system_a_instrucao_entra_como_system(arquivo, cliente):
    _quem_chama_a_llm([{"role": "user", "content": "P"}])
    enviado = cliente.recebido["messages"]
    assert enviado[0] == {"role": "system", "content": INSTRUCAO_LACUNAS}
    assert enviado[1] == {"role": "user", "content": "P"}


def test_json_mode_nao_pede_o_bloco(arquivo, cliente):
    _quem_chama_a_llm([{"role": "user", "content": "P"}], json_mode=True)
    assert all(INSTRUCAO_LACUNAS not in m["content"] for m in cliente.recebido["messages"])


def test_lambda_de_adaptacao_nao_vira_o_modulo(arquivo, cliente):
    chamar = lambda p: llm_b3._chat_complete([{"role": "user", "content": p}])  # noqa: E731

    def dono():
        return chamar("P")

    dono()
    assert {e["modulo"] for e in _eventos(arquivo)} == {"tests/test_lacunas_llm.py:dono"}
