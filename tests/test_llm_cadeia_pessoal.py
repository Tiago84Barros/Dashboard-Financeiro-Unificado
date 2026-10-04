"""Dado pessoal não vai para modelo gratuito.

A cadeia padrão põe o OpenRouter com um modelo ``:free`` na frente -- escolha
medida para análise de mercado. Mas os chats de finanças pessoais e de cartão
mandam transações e fatura, os da carteira mandam posições, e a entrevista da
estratégia e o Portfolio Fit mandam política e renda do usuário. Modelo
gratuito costuma pagar-se com os prompts; esses chats usam a cadeia
``pessoal``, que tira os elos ``:free`` e mantém a ordem dos demais.

Os chats de mercado continuam com a cadeia inteira.
"""
import pytest

import core.llm_b3 as llm
from tests.test_llm_provider_fallback import _PROVEDORES, _FakeClient

_GRATIS = "nvidia/nemotron-3-super-120b-a12b:free"


@pytest.fixture(autouse=True)
def _sem_rede(monkeypatch):
    monkeypatch.setattr(llm, "_gemini_model", lambda: "gemini-test")
    monkeypatch.setattr(llm, "_openrouter_model", lambda _m=None: _GRATIS)
    for getter in _PROVEDORES:
        monkeypatch.setattr(llm, getter, lambda: None)
    llm._limpar_estado_provedores()
    yield
    llm._limpar_estado_provedores()


def _tres(monkeypatch):
    rc, oc, gc = (_FakeClient(content="or"), _FakeClient(content="oa"),
                  _FakeClient(content="ge"))
    monkeypatch.setattr(llm, "_get_openrouter_client", lambda: rc)
    monkeypatch.setattr(llm, "_get_openai_client", lambda: oc)
    monkeypatch.setattr(llm, "_get_gemini_client", lambda: gc)
    return rc, oc, gc


def test_cadeia_pessoal_tira_modelo_free_e_mantem_a_ordem(monkeypatch):
    _tres(monkeypatch)
    assert [n for n, _c, _m in llm._provider_chain()] == ["openrouter", "openai", "gemini"]
    assert [n for n, _c, _m in llm._provider_chain(pessoal=True)] == ["openai", "gemini"]


def test_cadeia_pessoal_mantem_openrouter_com_modelo_pago(monkeypatch):
    _tres(monkeypatch)
    monkeypatch.setattr(llm, "_openrouter_model", lambda _m=None: "anthropic/claude-x")
    assert [n for n, _c, _m in llm._provider_chain(pessoal=True)] == [
        "openrouter", "openai", "gemini"]


def test_chat_pessoal_nao_chama_o_modelo_gratuito(monkeypatch):
    rc, oc, _gc = _tres(monkeypatch)
    assert llm._chat_complete([{"role": "user", "content": "x"}], pessoal=True) == "oa"
    assert rc.calls == 0 and oc.calls == 1
    assert llm.ultimo_modelo().startswith("openai/")


def test_so_provedor_gratuito_recusa_em_vez_de_vazar(monkeypatch):
    rc = _FakeClient(content="or")
    monkeypatch.setattr(llm, "_get_openrouter_client", lambda: rc)
    with pytest.raises(RuntimeError, match="gratuito"):
        llm._chat_complete([{"role": "user", "content": "x"}], pessoal=True)
    assert rc.calls == 0


# ── quem manda dado pessoal usa a cadeia pessoal; quem fala de mercado, não ──

def _captura(monkeypatch, modulo):
    chamadas = []

    def falso(messages, **kwargs):
        chamadas.append(kwargs)
        return '{"ok": 1}'

    monkeypatch.setattr(modulo, "_chat_complete", falso)
    return chamadas


def test_chat_de_financas_e_de_cartao_usam_a_cadeia_pessoal(monkeypatch):
    from core import llm_financeiro
    chamadas = _captura(monkeypatch, llm_financeiro)
    llm_financeiro.chat_com_financas("ctx", [], "gastei muito?")
    llm_financeiro.chat_com_cartao("ctx", [], "e a fatura?")
    assert [c.get("pessoal") for c in chamadas] == [True, True]


def test_chat_e_dossie_da_carteira_usam_a_cadeia_pessoal(monkeypatch):
    from core import llm_carteira, llm_dossie_carteira
    chamadas = _captura(monkeypatch, llm_carteira)
    chamadas_dossie = _captura(monkeypatch, llm_dossie_carteira)
    llm_carteira.chat_com_carteira("ctx", [], "e aí?", classe="acoes")
    llm_dossie_carteira.gerar_dossie_classe("ctx", classe="acoes")
    assert chamadas[0].get("pessoal") is True
    assert chamadas_dossie[0].get("pessoal") is True


def test_entrevista_da_estrategia_usa_a_cadeia_pessoal(monkeypatch):
    from core import llm_estrategia
    chamadas = _captura(monkeypatch, llm_estrategia)
    llm_estrategia.proxima_etapa({}, [], "quero aposentar aos 50",
                                 contexto="renda 12 meses")
    assert chamadas and chamadas[0].get("pessoal") is True


def test_portfolio_fit_usa_a_cadeia_pessoal(monkeypatch):
    from core.inteligencia_ativos import leitura_llm
    chamadas = _captura(monkeypatch, llm)
    leitura_llm._chamar_padrao([{"role": "user", "content": "x"}])
    assert chamadas[0].get("pessoal") is True


def test_chats_de_mercado_seguem_com_a_cadeia_inteira(monkeypatch):
    from core import llm_fii
    chamadas = _captura(monkeypatch, llm)
    chamadas_fii = _captura(monkeypatch, llm_fii)
    llm.chat_com_portfolio("ctx", [], "e a carteira B3?")
    llm_fii.chat_com_fiis("ctx", [], "e os FIIs?")
    assert not chamadas[0].get("pessoal") and not chamadas_fii[0].get("pessoal")
