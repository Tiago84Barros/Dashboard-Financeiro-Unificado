"""
views/configuracoes_geral.py
Aba "Geral" de Configurações — o que era da sidebar, mais o que não tinha lugar.

  🎯 Estratégia        — a política de investimentos (premissa da análise)
  🎨 Tema              — a preferência visual da conta
  🏷️ Categorias        — as opções do lançamento manual do Controle Financeiro
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

from core import categorias as cat_repo
from core import chat_repository
from core.auth import encerrar_sessao

_CONFIRMA_SAIDA = "cfg_geral_confirma_saida"
_SECAO_LLM = "cfg_geral_secao_llm"
_TODAS = "__todas__"
_TIPO_CAT = "cfg_geral_tipo_categoria"
_NOME_CAT = "cfg_geral_nome_categoria"


def render() -> None:
    render_estrategia_bloco()
    _render_tema()
    _render_categorias()
    _render_trocar_usuario()
    _render_memoria_llm()


# -- estratégia de investimentos ----------------------------------------------

def render_estrategia_bloco() -> None:
    """Bloco da estratégia. Público: a aba Geral do não-admin mostra só ele."""
    from views.configuracoes_estrategia import render as render_estrategia

    with st.container(border=True, key="cfg_geral_estrategia"):
        _cabecalho(
            "01", "🎯 Estratégia de Investimentos",
            "O que você pretende construir com seu patrimônio. É a premissa "
            "que a IA usa para analisar a carteira e cada ativo.",
        )
        render_estrategia()


# -- tema ---------------------------------------------------------------------

def _render_tema() -> None:
    from design.theme_selector import render_theme_selector

    with st.container(border=True, key="cfg_geral_tema"):
        _cabecalho(
            "02", "🎨 Tema do aplicativo",
            "Claro ou escuro, salvo na sua conta e aplicado na hora.",
        )
        # A lógica de persistência não muda de lugar junto: o callback
        # ``_persist_choice`` resolve um bug documentado de rerun que revertia a
        # segunda troca de tema da sessão. Aqui só muda ONDE o widget aparece.
        render_theme_selector()


# -- categorias do Controle Financeiro ----------------------------------------

_ROTULO_TIPO = {"entrada": "Entrada", "saida": "Saída",
                "investimento": "Investimento"}


def _render_categorias() -> None:
    """Criar e arquivar as categorias do lançamento manual.

    Arquivar, nunca apagar: a linha some do seletor e os lançamentos antigos
    continuam classificados nela. Apagar levaria a classificação junto, e não
    há como voltar atrás.
    """
    with st.container(border=True, key="cfg_geral_categorias"):
        _cabecalho(
            "03", "🏷️ Categorias do Controle Financeiro",
            "As opções que aparecem ao lançar entrada, saída ou investimento.",
        )
        tipo = st.selectbox(
            "Tipo", list(cat_repo.TIPOS), key=_TIPO_CAT,
            format_func=lambda t: _ROTULO_TIPO.get(t, t),
        )
        try:
            atuais = cat_repo.listar(tipo)
        except Exception as exc:  # noqa: BLE001
            st.error(f"Não foi possível ler as categorias: {exc}")
            return

        # Categoria oferecida pelo seletor mas ausente do banco grava
        # lançamento SEM categoria. Antes isso acontecia calado; aqui tem nome.
        sem_banco = [c["nome"] for c in atuais if c["id"] is None]
        if sem_banco:
            st.warning(
                "Estas ainda não existem no banco e gravam lançamento sem "
                f"categoria: {', '.join(sem_banco)}. Rode a migration 072 "
                "(`supabase_unificado/schema/072_categorias.sql`) no Supabase."
            )

        st.caption(f"{len(atuais)} categoria(s) em {_ROTULO_TIPO.get(tipo, tipo)}: "
                   + ", ".join(c["nome"] for c in atuais))

        nome = st.text_input("Nova categoria", key=_NOME_CAT,
                             placeholder="Ex.: Previdência")
        if st.button("➕ Criar categoria", key="cfg_geral_criar_cat",
                     type="primary", disabled=not nome.strip()):
            ok, msg = cat_repo.criar(nome, tipo)
            (st.success if ok else st.error)(msg)
            if ok:
                st.rerun()

        minhas = [c for c in atuais if c.get("minha") and c["id"]]
        if minhas:
            alvo = st.selectbox(
                "Arquivar uma categoria criada por você",
                [c["id"] for c in minhas], key=f"cfg_geral_arquivar_{tipo}",
                format_func=lambda cid: next(
                    c["nome"] for c in minhas if c["id"] == cid),
            )
            if st.button("📦 Arquivar", key="cfg_geral_arquivar_btn"):
                ok, msg = cat_repo.arquivar(alvo)
                (st.success if ok else st.error)(msg)
                if ok:
                    st.rerun()
        else:
            st.caption("Você ainda não criou nenhuma categoria deste tipo. "
                       "As de sistema não podem ser arquivadas.")


# -- trocar de usuário --------------------------------------------------------

def _render_trocar_usuario() -> None:
    from core.user_context import principal

    with st.container(border=True, key="cfg_geral_usuario"):
        _cabecalho(
            "04", "👤 Trocar de usuário",
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
            "05", "🧹 Limpar histórico da LLM",
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
