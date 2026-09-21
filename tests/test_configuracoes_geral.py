# -*- coding: utf-8 -*-
"""A aba "Geral" concentra o que saiu da sidebar, e limpa o que promete limpar.

Três riscos, e os dois primeiros já morderam este repositório:

1. **O mapa de seções envelhecer calado.** Quem adiciona um chat novo não vem
   editar ``SECOES``; a tela seguiria oferecendo "Todas as seções" e deixando a
   nova de pé. Por isso os prefixos e as chaves de sessão são conferidos contra
   as chamadas REAIS no código-fonte (``memoria: lista-branca-perde-a-chave``).
2. **Apagar por prefixo pegar o prefixo vizinho.** ``apb3`` e ``apus`` só se
   separam pelo que vem depois; ``chat_ativo`` e ``chat_carteira`` compartilham
   o começo inteiro de ``chat_``.
3. **A sidebar continuar desenhando o que mudou de lugar.** Duas cópias do
   mesmo controle divergem (``memoria: guarda-duplicada-diverge``).
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from core import chat_repository as repo

RAIZ = Path(__file__).parents[1]
APP = RAIZ / "app.py"
CONFIG = RAIZ / "views/configuracoes.py"
FONTES = sorted((RAIZ / "views").glob("*.py")) + sorted((RAIZ / "design").glob("*.py"))

_CHAMADAS_DE_HISTORICO = {"load_chat_history", "save_chat_history",
                          "clear_chat_history"}


# -- leitura do código-fonte --------------------------------------------------

def _atribuicoes(arvore: ast.AST) -> dict[str, ast.AST]:
    """Nome -> valor, de qualquer nível: as chaves de sessão tanto são
    constantes de módulo (``_CHAT``) quanto locais (``hist_key``)."""
    mapa: dict[str, ast.AST] = {}
    for no in ast.walk(arvore):
        if isinstance(no, ast.Assign):
            for alvo in no.targets:
                if isinstance(alvo, ast.Name):
                    mapa[alvo.id] = no.value
    return mapa


def _texto_inicial(no: ast.AST, mapa: dict[str, ast.AST]) -> str | None:
    """Parte fixa do valor: o suficiente para casar com um prefixo declarado.

    ``f"chat_ativo_{mercado}_history"`` não tem valor estático, mas tem começo
    estático — e é pelo começo que a limpeza varre o ``session_state``.
    """
    if isinstance(no, ast.Constant) and isinstance(no.value, str):
        return no.value
    if isinstance(no, ast.Name):
        alvo = mapa.get(no.id)
        return _texto_inicial(alvo, mapa) if alvo is not None else None
    if isinstance(no, ast.JoinedStr):
        primeiro = no.values[0] if no.values else None
        if isinstance(primeiro, ast.Constant) and isinstance(primeiro.value, str):
            return primeiro.value
    return None


def _prefixos_no_codigo() -> set[str]:
    achados: set[str] = set()
    for caminho in FONTES:
        arvore = ast.parse(caminho.read_text(encoding="utf-8"))
        for no in ast.walk(arvore):
            if (isinstance(no, ast.Call) and isinstance(no.func, ast.Name)
                    and no.func.id == "conversation_key" and no.args):
                primeiro = no.args[0]
                if isinstance(primeiro, ast.Constant):
                    achados.add(primeiro.value)
    return achados


def _chaves_de_sessao_no_codigo() -> set[str]:
    achados: set[str] = set()
    for caminho in FONTES:
        arvore = ast.parse(caminho.read_text(encoding="utf-8"))
        mapa = _atribuicoes(arvore)
        for no in ast.walk(arvore):
            if not (isinstance(no, ast.Call) and isinstance(no.func, ast.Name)
                    and no.func.id in _CHAMADAS_DE_HISTORICO):
                continue
            for kw in no.keywords:
                if kw.arg != "session_key":
                    continue
                texto = _texto_inicial(kw.value, mapa)
                if texto:
                    achados.add(texto)
    return achados


# -- o mapa bate com o código -------------------------------------------------

def test_todo_chat_do_codigo_tem_secao_declarada():
    declarados = {s.prefixo for s in repo.SECOES}
    assert _prefixos_no_codigo() == declarados


def test_toda_chave_de_sessao_do_codigo_e_alcancada():
    declaradas = tuple(p for s in repo.SECOES for p in s.chaves_de_sessao)
    orfas = [chave for chave in _chaves_de_sessao_no_codigo()
             if not chave.startswith(declaradas)]
    assert not orfas, (
        f"chaves de sessao que a limpeza nao alcanca: {sorted(orfas)}")


def test_rotulos_nao_se_repetem():
    """Dois itens com o mesmo nome no selectbox e a escolha vira sorteio."""
    rotulos = [s.rotulo for s in repo.SECOES]
    assert len(set(rotulos)) == len(rotulos)


# -- apagar por prefixo -------------------------------------------------------

class _Conexao:
    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class _Engine:
    def begin(self):
        return _Conexao()

    def connect(self):
        return _Conexao()


@pytest.fixture
def chats(monkeypatch):
    """Conversas de mentira, com as chaves no formato real do repositório."""
    guardadas = {
        "apb3:aaa": {"messages": [1], "updated": 0},
        "apb3:bbb": {"messages": [2], "updated": 0},
        "apus:ccc": {"messages": [3], "updated": 0},
        "chat_ativo:ddd": {"messages": [4], "updated": 0},
        "chat_carteira:eee": {"messages": [5], "updated": 0},
        "controle_financeiro:fff": {"messages": [6], "updated": 0},
    }
    estado = {repo.FIELD: guardadas}
    monkeypatch.setattr(repo, "require_user", lambda: "u1")
    monkeypatch.setattr(repo, "_engine", lambda: _Engine())
    monkeypatch.setattr(repo, "locked_preferences", lambda conn, uid: estado)
    monkeypatch.setattr(repo, "write_preferences",
                        lambda conn, uid, extra: estado.update(extra))
    monkeypatch.setattr(repo, "contagens", lambda: {
        s.prefixo: sum(1 for c in estado[repo.FIELD]
                       if c.startswith(f"{s.prefixo}:"))
        for s in repo.SECOES})
    return estado[repo.FIELD]


def test_apagar_uma_secao_leva_todas_as_conversas_dela(chats):
    assert repo.clear_prefixo("apb3") == 2
    assert "apb3:aaa" not in chats and "apb3:bbb" not in chats


def test_apagar_apb3_nao_toca_em_apus(chats):
    repo.clear_prefixo("apb3")
    assert "apus:ccc" in chats


def test_chat_ativo_e_chat_carteira_nao_se_confundem(chats):
    """O começo é igual nos dois; o separador é o que os distingue."""
    assert repo.clear_prefixo("chat_ativo") == 1
    assert "chat_carteira:eee" in chats


def test_todas_as_secoes_apaga_tudo_numa_transacao(chats):
    apagadas = repo.clear_prefixos([s.prefixo for s in repo.SECOES])
    assert apagadas == 6
    assert chats == {}


def test_prefixo_sem_conversa_nao_apaga_nada(chats):
    assert repo.clear_prefixo("fii_portfolio") == 0
    assert len(chats) == 6


def test_limpar_sessao_tira_a_conversa_e_os_marcadores():
    secao = repo.secao_por_prefixo("apb3")
    estado = {
        "apb3_chat_history": [1],
        "_chat_memory_loaded_for:apb3_chat_history": True,
        "_chat_visible_start:apb3_chat_history": 0,
        "apus_chat_history": [2],
        "tema": "dark",
    }
    assert repo.limpar_sessao(secao, estado) == 3
    assert estado == {"apus_chat_history": [2], "tema": "dark"}


def test_limpar_sessao_alcanca_chave_com_sufixo_variavel():
    """``chat_ativo`` nomeia a chave por mercado: ``chat_ativo_b3_history``."""
    secao = repo.secao_por_prefixo("chat_ativo")
    estado = {"chat_ativo_b3_history": [1], "chat_ativo_us_history": [2],
              "chat_carteira_fii_history": [3]}
    assert repo.limpar_sessao(secao, estado) == 2
    assert list(estado) == ["chat_carteira_fii_history"]


# -- a sidebar não desenha mais o que mudou de lugar --------------------------

def _chamadas(no: ast.AST) -> set[str]:
    nomes = set()
    for filho in ast.walk(no):
        if isinstance(filho, ast.Call):
            alvo = filho.func
            if isinstance(alvo, ast.Name):
                nomes.add(alvo.id)
            elif isinstance(alvo, ast.Attribute):
                nomes.add(alvo.attr)
    return nomes


def test_a_sidebar_nao_tem_mais_tema_nem_saida():
    chamadas = _chamadas(ast.parse(APP.read_text(encoding="utf-8")))
    assert "render_theme_selector" not in chamadas
    assert "encerrar_sessao" not in chamadas
    # E o tema continua sendo APLICADO: mover a escolha não pode deixar o app
    # abrir sem tema nenhum.
    assert "aplicar_tema" in chamadas and "current_theme" in chamadas


def test_geral_e_a_primeira_aba_de_configuracoes():
    arvore = ast.parse(CONFIG.read_text(encoding="utf-8"))
    listas = [no.args[0] for no in ast.walk(arvore)
              if isinstance(no, ast.Call) and isinstance(no.func, ast.Attribute)
              and no.func.attr == "tabs" and no.args
              and isinstance(no.args[0], ast.List)]
    primeiros = [lista.elts[0].value for lista in listas
                 if lista.elts and isinstance(lista.elts[0], ast.Constant)]
    assert any(rotulo.endswith("Geral") for rotulo in primeiros)
    assert "render_geral" in _chamadas(arvore)
