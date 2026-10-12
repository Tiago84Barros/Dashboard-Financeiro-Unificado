"""Configurações é uma tela só: quem não administra vê as mesmas oito abas.

Até 12/10/2026 quem não era administrador recebia outra tela — título próprio e
três abas. O pedido foi explícito: mesmo layout, todas as abas. O que separa as
contas passa a ser o DADO que cada guarda de ``core`` entrega, nunca o que a
tela desenha.
"""
from __future__ import annotations

import contextlib

import pytest

from views import configuracoes

ROTULOS_ESPERADOS = [
    "⚙️ Geral",
    "🎯 Grau de Confiança",
    "🧭 Restrições",
    "🔁 Atualização de dados",
    "🔄 Dados de mercado",
    "🗄️ Banco de dados",
    "🔒 Segurança",
    "📚 Documentação",
]


class _FakeSt:
    """Só o suficiente para ver quais abas a tela abriu, e em que ordem."""

    def __init__(self) -> None:
        self.rotulos: list[str] = []
        self.titulos: list[str] = []

    def tabs(self, rotulos):
        self.rotulos.extend(rotulos)
        return [contextlib.nullcontext() for _ in rotulos]

    def title(self, texto, **_kw):
        self.titulos.append(str(texto))

    def __getattr__(self, _nome):
        def _ignora(*_a, **_k):
            return None
        return _ignora


@pytest.fixture
def tela(monkeypatch):
    """Roda ``configuracoes.render`` com os corpos das abas dublados.

    O que está sob teste é o roteamento — quais abas existem e qual corpo entra
    em cada uma. Deixar os corpos reais rodarem traria banco e rede para dentro
    de um teste de layout.
    """
    falso = _FakeSt()
    monkeypatch.setattr(configuracoes, "st", falso)
    monkeypatch.setattr("core.user_context.require_user", lambda: "u1")

    chamadas: list[str] = []
    for nome in ("container_pagina", "_render_tab_intro", "render_geral",
                 "_render_confianca", "_render_restricoes",
                 "_render_atualizacao_de_dados", "_render_atualizacao",
                 "_render_banco", "_render_seguranca", "_render_documentacao"):
        monkeypatch.setattr(configuracoes, nome,
                            lambda *a, _n=nome, **k: chamadas.append(_n))

    def _rodar(*, admin: bool):
        monkeypatch.setattr(
            "core.user_context.principal",
            lambda: {"id": "admin-1" if admin else "outra-pessoa"})
        monkeypatch.setattr("core.config.settings.ADMIN_USER_ID", "admin-1",
                            raising=False)
        falso.rotulos.clear()
        chamadas.clear()
        configuracoes.render()
        return list(falso.rotulos), list(chamadas)

    return falso, _rodar


def test_quem_nao_administra_ve_as_mesmas_oito_abas(tela):
    _falso, rodar = tela
    assert rodar(admin=False)[0] == ROTULOS_ESPERADOS


def test_as_abas_de_quem_administra_e_de_quem_nao_administra_sao_as_mesmas(tela):
    """A prova de que a tela não se bifurca mais: a comparação, não a lista.

    Um teste que só olhasse a lista do não-admin passaria se alguém, amanhã,
    desse abas a mais ao administrador.
    """
    _falso, rodar = tela
    assert rodar(admin=False)[0] == rodar(admin=True)[0]


def test_nenhuma_aba_fica_sem_corpo(tela):
    """Oito abas abertas e oito corpos chamados — rótulo sem conteúdo é pior
    do que aba escondida: promete e não entrega."""
    _falso, rodar = tela
    rotulos, chamadas = rodar(admin=False)
    corpos = [c for c in chamadas if c not in ("container_pagina",
                                               "_render_tab_intro")]
    assert len(rotulos) == len(corpos) == 8
    assert corpos == ["render_geral", "_render_confianca", "_render_restricoes",
                      "_render_atualizacao_de_dados", "_render_atualizacao",
                      "_render_banco", "_render_seguranca",
                      "_render_documentacao"]


def test_a_tela_nao_tem_mais_titulo_proprio_de_nao_admin(tela):
    """``st.title("Minhas configurações")`` era a marca da tela bifurcada."""
    falso, rodar = tela
    rodar(admin=False)
    assert falso.titulos == []


def test_painel_de_restricoes_desenha_para_quem_nao_administra(monkeypatch):
    """A aba 🧭 Restrições saía vazia: ``render`` devolvia antes de desenhar.

    O que ela mostra descreve o app — limitação de dado, erro, detalhe técnico
    — e não a conta de ninguém.
    """
    from core.lacunas import painel
    from views import configuracoes_restricoes as restr

    vazio = painel.Painel(itens=[], fontes={"local": "ok (0 registros)"},
                          gerado_em="2026-10-12T12:00:00+00:00")
    monkeypatch.setattr(restr, "_carregar", lambda: vazio)
    monkeypatch.setattr("core.user_context.principal",
                        lambda: {"id": "outra-pessoa"})
    monkeypatch.setattr("core.config.settings.ADMIN_USER_ID", "admin-1",
                        raising=False)

    desenhou: list[object] = []
    monkeypatch.setattr(restr, "render_painel", lambda p: desenhou.append(p))

    restr.render()
    assert desenhou == [vazio]
