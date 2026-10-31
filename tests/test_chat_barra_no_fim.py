"""A barra de escrita de todo chat fica no fim da conversa.

Dentro de aba ou coluna o ``st.chat_input`` aparece onde é chamado. Os chats
o chamavam depois do histórico, mas escreviam a pergunta nova e a resposta
DEPOIS dele: a barra ficava acima da última troca e o usuário rolava para cima
para achá-la (relato de 29/10/2026, chat da carteira na aba Investimentos).

A conversa agora mora num container aberto antes da barra. Dois testes:
- estrutural: toda função do app que chama ``st.chat_input`` escreve as
  mensagens por esse container, para chat novo não repetir o defeito;
- de tela: o AppTest desenha o chat dentro de uma aba e confere que a barra é
  o último nó, depois da pergunta e da resposta.
"""
from __future__ import annotations

import ast
import pathlib
import sys

_RAIZ = pathlib.Path(__file__).resolve().parents[1]
if str(_RAIZ) not in sys.path:
    sys.path.insert(0, str(_RAIZ))

_FONTES = ("app.py", "design", "views", "pages", "core")


def _arquivos():
    for nome in _FONTES:
        caminho = _RAIZ / nome
        if caminho.is_file():
            yield caminho
        elif caminho.is_dir():
            yield from sorted(caminho.rglob("*.py"))


def _funcoes_com_chat_input():
    for arquivo in _arquivos():
        arvore = ast.parse(arquivo.read_text(encoding="utf-8"))
        for fn in ast.walk(arvore):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            entradas = [n for n in ast.walk(fn) if isinstance(n, ast.Call)
                        and isinstance(n.func, ast.Attribute)
                        and n.func.attr == "chat_input"]
            if entradas:
                yield arquivo.relative_to(_RAIZ).as_posix(), fn, entradas[0]


def _e_container(no) -> bool:
    return (isinstance(no, ast.Call) and isinstance(no.func, ast.Attribute)
            and no.func.attr == "container")


def _violacoes(fn, entrada) -> list[str]:
    conversas = {
        alvo.id for n in ast.walk(fn)
        if isinstance(n, ast.Assign) and _e_container(n.value)
        and n.lineno < entrada.lineno
        for alvo in n.targets if isinstance(alvo, ast.Name)
    }

    def _da_conversa(expr) -> bool:
        if isinstance(expr, ast.Name):
            return expr.id in conversas
        return (isinstance(expr, ast.Call) and isinstance(expr.func, ast.Attribute)
                and expr.func.attr == "chat_message"
                and isinstance(expr.func.value, ast.Name)
                and expr.func.value.id in conversas)

    dentro = {
        id(n) for w in ast.walk(fn) if isinstance(w, ast.With)
        and any(_da_conversa(item.context_expr) for item in w.items)
        for n in ast.walk(w)
    }
    erros = []
    for n in ast.walk(fn):
        if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)):
            continue
        if n.func.attr == "chat_message" and not _da_conversa(n):
            erros.append(f"linha {n.lineno}: chat_message fora do container da conversa")
        if (n.func.attr == "spinner" and n.lineno > entrada.lineno
                and id(n) not in dentro):
            erros.append(f"linha {n.lineno}: spinner da resposta abaixo da barra")
    return erros


def test_todo_chat_escreve_a_conversa_acima_da_barra():
    achados = list(_funcoes_com_chat_input())
    # Varredura cega passaria por limpa: os nove chats de 29/10 têm de aparecer.
    assert len(achados) >= 9, [f"{a}::{fn.name}" for a, fn, _ in achados]
    problemas = {
        f"{arquivo}::{fn.name}": erros
        for arquivo, fn, entrada in achados
        if (erros := _violacoes(fn, entrada))
    }
    assert not problemas, problemas


def test_a_regra_reprova_o_padrao_antigo():
    # Guarda contra regra que aprova tudo: o desenho de antes tem de reprovar.
    fonte = (
        "def chat():\n"
        "    for m in historico:\n"
        "        with st.chat_message(m['role']):\n"
        "            st.markdown(m['content'])\n"
        "    p = st.chat_input('x')\n"
        "    with st.chat_message('user'):\n"
        "        st.markdown(p)\n"
        "    with st.spinner('...'):\n"
        "        pass\n"
    )
    fn = ast.parse(fonte).body[0]
    entrada = next(n for n in ast.walk(fn) if isinstance(n, ast.Call)
                   and getattr(n.func, "attr", "") == "chat_input")
    assert len(_violacoes(fn, entrada)) == 3


# -- tela ---------------------------------------------------------------------

def _ordem(app) -> list[str]:
    """Mensagens e barra na ordem em que a página as desenha."""
    saida: list[str] = []

    def andar(no):
        if getattr(no, "type", None) in ("chat_message", "chat_input"):
            saida.append(no.type)
        filhos = getattr(no, "children", None) or {}
        for chave in sorted(filhos):
            andar(filhos[chave])

    andar(app._tree)
    return saida


def _historico_antigo():
    return [{"role": "user", "content": "Pergunta antiga"},
            {"role": "assistant", "content": "Resposta antiga"}]


def _memoria(monkeypatch, chat):
    """Histórico da sessão: a troca antiga aparece, e a nova fica para o rerun."""
    memoria = _historico_antigo()
    monkeypatch.setattr(chat, "load_chat_history", lambda *_a, **_k: memoria)
    monkeypatch.setattr(chat, "visible_chat_history",
                        lambda historico, *_a, **_k: list(historico))
    monkeypatch.setattr(chat, "save_chat_history", lambda *_a, **_k: None)


def _stub_carteira(monkeypatch):
    import design.chat_carteira as chat

    monkeypatch.setattr(chat, "llm_disponivel", lambda: True)
    monkeypatch.setattr(chat, "provedores_disponiveis", lambda: ["anthropic"])
    _memoria(monkeypatch, chat)
    monkeypatch.setattr(chat, "chat_com_carteira",
                        lambda _ctx, _hist, _perg, *, classe: "Resposta nova")
    monkeypatch.setattr(chat, "gerar_dossie_classe",
                        lambda _ctx, *, classe: "Dossiê novo")


def _app_carteira():
    import streamlit as st

    import design.chat_carteira as chat

    aba, = st.tabs(["Ações"])
    with aba:
        chat.render_chat_carteira(
            classe="acoes", tickers=["BBAS3"],
            build_context=lambda _p, *, valores_reais=False: "CTX")


def test_chat_da_carteira_pergunta_nova_fica_acima_da_barra(monkeypatch):
    from streamlit.testing.v1 import AppTest

    _stub_carteira(monkeypatch)
    app = AppTest.from_function(_app_carteira).run(timeout=30)
    assert not app.exception
    assert _ordem(app) == ["chat_message", "chat_message", "chat_input"]

    app.chat_input[0].set_value("Pergunta nova").run(timeout=30)
    assert not app.exception
    assert _ordem(app) == ["chat_message"] * 4 + ["chat_input"]
    textos = [m.markdown[0].value for m in app.chat_message]
    assert textos[-2:] == ["Pergunta nova", "Resposta nova"]


def test_chat_da_carteira_dossie_fica_acima_da_barra_e_ela_nao_some(monkeypatch):
    from streamlit.testing.v1 import AppTest

    _stub_carteira(monkeypatch)
    app = AppTest.from_function(_app_carteira).run(timeout=30)
    app.button(key="chat_carteira_acoes_dossie").click().run(timeout=30)
    assert not app.exception
    # Antes a barra nem era desenhada no rerun do dossiê.
    assert _ordem(app) == ["chat_message"] * 4 + ["chat_input"]
    assert "Dossiê novo" in app.chat_message[-1].markdown[0].value


def test_chat_do_ativo_pergunta_nova_fica_acima_da_barra(monkeypatch):
    from streamlit.testing.v1 import AppTest

    import core.contexto_mercado as contexto_mercado
    import design.chat_ativo as chat

    monkeypatch.setattr(chat, "llm_disponivel", lambda: True)
    monkeypatch.setattr(chat, "provedores_disponiveis", lambda: ["anthropic"])
    _memoria(monkeypatch, chat)
    monkeypatch.setattr(chat, "chat_com_ativo",
                        lambda _ctx, _hist, _perg, **_k: "Resposta nova")
    monkeypatch.setattr(chat.veredito, "anexar",
                        lambda contexto, *_a, **_k: (contexto, {}))
    monkeypatch.setattr(contexto_mercado, "bloco_contexto_mercado",
                        lambda *_a, **_k: "MERCADO")

    def _app():
        import streamlit as st

        import design.chat_ativo as chat

        aba, = st.tabs(["BBAS3"])
        with aba:
            chat.render_chat_ativo(mercado="b3", ticker="BBAS3",
                                   build_context=lambda _p: "CTX")

    app = AppTest.from_function(_app).run(timeout=30)
    assert not app.exception
    app.chat_input[0].set_value("Pergunta nova").run(timeout=30)
    assert not app.exception
    assert _ordem(app) == ["chat_message"] * 4 + ["chat_input"]
    textos = [m.markdown[0].value for m in app.chat_message]
    assert textos[-2:] == ["Pergunta nova", "Resposta nova"]
