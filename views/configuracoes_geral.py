"""
views/configuracoes_geral.py
Aba "Geral" de Configurações — o que era da sidebar, mais o que não tinha lugar.

  🎨 Tema              — a preferência visual da conta
  👤 Trocar de usuário — encerrar a sessão deste navegador
  🧹 Memória da LLM    — apagar o histórico de conversa, por seção

Arquivo separado de ``views/configuracoes.py`` de propósito: aquele já passa de
1800 linhas, e três blocos novos ali só pioram um arquivo grande demais.
``configuracoes.py`` ganha a aba e a chamada; o conteúdo mora aqui.

O tema e a troca de usuário **saíram da sidebar** em 21/09/2026. Estavam
visíveis em toda tela, ocupando a barra que serve para navegar, e são ajustes
que se fazem uma vez.
"""
from __future__ import annotations

from html import escape

import streamlit as st

from core import chat_repository
from core.auth import encerrar_sessao

_CONFIRMA_SAIDA = "cfg_geral_confirma_saida"
_SECAO_LLM = "cfg_geral_secao_llm"
_TODAS = "__todas__"


def render() -> None:
    _render_tema()
    _render_trocar_usuario()
    _render_memoria_llm()


# -- tema ---------------------------------------------------------------------

def _render_tema() -> None:
    from design.theme_selector import render_theme_selector

    with st.container(border=True, key="cfg_geral_tema"):
        _cabecalho(
            "01", "🎨 Tema do aplicativo",
            "Claro ou escuro, salvo na sua conta e aplicado na hora.",
        )
        # A lógica de persistência não muda de lugar junto: o callback
        # ``_persist_choice`` resolve um bug documentado de rerun que revertia a
        # segunda troca de tema da sessão. Aqui só muda ONDE o widget aparece.
        render_theme_selector()


# -- trocar de usuário --------------------------------------------------------

def _render_trocar_usuario() -> None:
    from core.user_context import principal

    with st.container(border=True, key="cfg_geral_usuario"):
        _cabecalho(
            "02", "👤 Trocar de usuário",
            "Encerra a sessão deste navegador e volta para a tela de entrada.",
        )
        nome = str(principal().get("name") or principal().get("email") or "")
        # Sem classe CSS própria: texto do Streamlit já segue o token de tema, e
        # uma classe nova não declarada em ``_CONFIG_CSS`` ficaria sem estilo no
        # claro (``memoria: tema-claro-so-alcanca-o-que-passa-por-token``).
        st.markdown(f"Conectado como **{nome}**")
        # Confirmação explícita porque ``encerrar_sessao`` limpa o
        # ``session_state`` INTEIRO: o que estiver em andamento em qualquer
        # outra tela vai junto, e não há como desfazer com um segundo clique.
        confirmado = st.checkbox(
            "Entendi que a sessão atual será encerrada", key=_CONFIRMA_SAIDA)
        if st.button("Sair / trocar usuário", key="cfg_geral_sair",
                     type="primary", disabled=not confirmado):
            encerrar_sessao()


# -- memória da LLM -----------------------------------------------------------

def _render_memoria_llm() -> None:
    with st.container(border=True, key="cfg_geral_llm"):
        _cabecalho(
            "03", "🧹 Limpar histórico da LLM",
            "Escolha a seção cuja conversa deve ser apagada. Nada é apagado "
            "sem a escolha e o clique.",
        )
        try:
            contagens = chat_repository.contagens()
        except Exception as exc:  # noqa: BLE001
            st.error(f"Não foi possível ler o histórico de conversas: {exc}")
            return

        total = sum(contagens.values())
        opcoes = [s.prefixo for s in chat_repository.SECOES] + [_TODAS]

        def _rotulo(valor: str) -> str:
            if valor == _TODAS:
                return f"Todas as seções ({total})"
            secao = chat_repository.secao_por_prefixo(valor)
            nome = secao.rotulo if secao else valor
            return f"{nome} ({contagens.get(valor, 0)})"

        escolha = st.selectbox("Seção", opcoes, format_func=_rotulo,
                               key=_SECAO_LLM)
        alvo = ([s.prefixo for s in chat_repository.SECOES]
                if escolha == _TODAS else [escolha])
        quantas = sum(contagens.get(p, 0) for p in alvo)

        # O número aparece ANTES do clique. Uma seção guarda uma conversa por
        # contexto (conjunto de tickers, mês, cenário), então "limpar o
        # Controle Financeiro" pode ser uma conversa ou doze, e a diferença
        # entre as duas só é visível se alguém disser qual é.
        if quantas:
            st.caption(f"Serão apagadas {quantas} conversa(s). Não há como desfazer.")
        else:
            st.caption("Não há conversa gravada para esta escolha.")

        if st.button("🗑️ Apagar histórico", key="cfg_geral_limpar_llm",
                     type="primary", disabled=not quantas):
            try:
                apagadas = chat_repository.clear_prefixos(alvo)
            except Exception as exc:  # noqa: BLE001
                st.error(f"Nada foi apagado: {exc}")
                return
            # A sessão também, e não só o banco: a tela que estiver aberta com
            # o histórico em memória continuaria exibindo o que já morreu e o
            # regravaria na mensagem seguinte.
            for prefixo in alvo:
                secao = chat_repository.secao_por_prefixo(prefixo)
                if secao is not None:
                    chat_repository.limpar_sessao(secao, st.session_state)
            st.success(f"{apagadas} conversa(s) apagada(s).")
            st.rerun()


# -- apoio --------------------------------------------------------------------

def _cabecalho(indice: str, titulo: str, descricao: str) -> None:
    """Cabeçalho do bloco, num ``st.markdown`` só.

    Div aberta num bloco e fechada em outro vira moldura vazia, com o conteúdo
    caindo fora da borda (``memoria: card-css-bloco-unico-streamlit``).
    """
    st.markdown(
        '<div class="cfg-workflow-header">'
        f'<span class="cfg-workflow-index">{escape(indice)}</span>'
        '<div class="cfg-workflow-copy">'
        f'<div class="cfg-workflow-title">{escape(titulo)}</div>'
        f'<div class="cfg-workflow-description">{escape(descricao)}</div>'
        "</div></div>",
        unsafe_allow_html=True,
    )
