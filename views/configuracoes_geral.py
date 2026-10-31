"""
views/configuracoes_geral.py
Aba "Geral" de Configurações — o que era da sidebar, mais o que não tinha lugar.

  🎨 Tema              — a preferência visual da conta
  🏷️ Categorias        — criar, retirar e devolver as opções do lançamento
                         manual do Controle Financeiro
  👤 Trocar de usuário — encerrar a sessão deste navegador
  🧹 Memória da LLM    — apagar o histórico de conversa, por seção

Arquivo separado de ``views/configuracoes.py`` de propósito: aquele já passa de
1800 linhas, e três blocos novos ali só pioram um arquivo grande demais.
``configuracoes.py`` ganha a aba e a chamada; o conteúdo mora aqui.

O tema e a troca de usuário **saíram da sidebar** em 21/09/2026. Estavam
visíveis em toda tela, ocupando a barra que serve para navegar, e são ajustes
que se fazem uma vez.

A 🎯 Estratégia e o 🌎 Cenário de Investimentos **saíram daqui** em 27/09/2026:
são configurados e alterados na própria aba Investimentos → Inteligência dos
Ativos, que depende deles.
"""
from __future__ import annotations

from html import escape

import streamlit as st

from core import categorias as cat_repo
from core import chat_repository
from core.auth import encerrar_sessao
from design.lacunas import aviso_lacuna, falha_de_acao

_CONFIRMA_SAIDA = "cfg_geral_confirma_saida"
_SECAO_LLM = "cfg_geral_secao_llm"
_TODAS = "__todas__"
_TIPO_CAT = "cfg_geral_tipo_categoria"
_NOME_CAT = "cfg_geral_nome_categoria"
_RECADO_CAT = "cfg_geral_recado_categoria"


def render() -> None:
    st.caption("A Estratégia e o Cenário de Investimentos se configuram em "
               "Investimentos → Inteligência dos Ativos.")
    _render_tema()
    _render_categorias()
    _render_trocar_usuario()
    _render_memoria_llm()


# -- tema ---------------------------------------------------------------------

def _render_tema() -> None:
    from design.theme_selector import render_theme_selector

    with st.container(border=True, key="cfg_geral_tema"):
        _cabecalho(
            "01", "🎨 Tema do aplicativo",
            "Claro ou escuro: muda só quando você escolhe, e fica salvo na sua conta.",
        )
        # A lógica de persistência não muda de lugar junto: o callback
        # ``_persist_choice`` resolve um bug documentado de rerun que revertia a
        # segunda troca de tema da sessão. Aqui só muda ONDE o widget aparece.
        render_theme_selector()


# -- categorias do Controle Financeiro ----------------------------------------

_ROTULO_TIPO = {"entrada": "Entrada", "saida": "Saída",
                "investimento": "Investimento"}


def _render_categorias() -> None:
    """Criar, retirar e devolver as categorias do lançamento manual.

    Retirar, nunca apagar: a opção some do SEU seletor e os lançamentos antigos
    continuam classificados nela. Vale para a categoria de sistema também —
    ela é de todos, e por isso o que se retira é a opção de quem pediu, não a
    linha do banco. Apagar levaria a classificação junto, sem volta.
    """
    with st.container(border=True, key="cfg_geral_categorias"):
        _cabecalho(
            "02", "🏷️ Categorias do Controle Financeiro",
            "As opções que aparecem ao lançar entrada, saída ou investimento.",
        )
        # O recado da ação anterior: ``st.rerun()`` logo depois do clique
        # apagava o ``st.success`` antes de alguém conseguir lê-lo.
        recado = st.session_state.pop(_RECADO_CAT, None)
        if recado:
            st.success(recado)

        tipo = st.selectbox(
            "Tipo", list(cat_repo.TIPOS), key=_TIPO_CAT,
            format_func=lambda t: _ROTULO_TIPO.get(t, t),
        )
        rotulo = _ROTULO_TIPO.get(tipo, tipo)
        try:
            todas = cat_repo.listar(tipo, com_retiradas=True)
        except Exception as exc:  # noqa: BLE001
            falha_de_acao("Não foi possível ler as categorias.", exc)
            return
        no_seletor = [c for c in todas if not c["retirada"]]
        retiradas = [c for c in todas if c["retirada"]]

        # Categoria oferecida pelo seletor mas ausente do banco grava
        # lançamento SEM categoria. Antes isso acontecia calado; aqui tem nome.
        sem_banco = [c["nome"] for c in no_seletor if c["id"] is None]
        if sem_banco:
            aviso_lacuna(
                "Categorias ainda não existem no banco e gravam lançamento sem "
                f"categoria: {', '.join(sem_banco)}. Rode a migration 072 "
                "(`supabase_unificado/schema/072_categorias.sql`) no Supabase.",
                codigo="tela.configuracoes.categorias_fora_do_banco",
            )

        st.caption(f"{len(no_seletor)} categoria(s) no seletor de {rotulo}: "
                   + ", ".join(c["nome"] for c in no_seletor))

        nome = st.text_input("Nova categoria", key=_NOME_CAT,
                             placeholder="Ex.: Previdência")
        if st.button("➕ Criar categoria", key="cfg_geral_criar_cat",
                     type="primary", disabled=not nome.strip()):
            ok, msg = cat_repo.criar(nome, tipo)
            _depois_da_acao(ok, msg, _NOME_CAT)

        _render_retirar(tipo, rotulo, no_seletor)
        _render_devolver(tipo, rotulo, retiradas)


def _render_retirar(tipo: str, rotulo: str, no_seletor: list[dict]) -> None:
    candidatas = [c for c in no_seletor if c["id"]]
    if not candidatas:
        return
    nomes = {c["id"]: c["nome"] for c in candidatas}
    # A chave leva o tipo: trocar de Entrada para Saída com ids de Entrada
    # ainda escolhidos no widget faria o Streamlit oferecer o que não existe.
    alvos = st.multiselect(
        "Retirar do seletor", list(nomes), key=f"cfg_geral_retirar_{tipo}",
        format_func=lambda cid: nomes.get(cid, cid),
        placeholder=f"Escolha as categorias de {rotulo} que você não usa",
    )
    # Uma tem de sobrar: sem nenhuma, o lançamento é gravado sem categoria.
    todas_escolhidas = bool(alvos) and len(alvos) >= len(candidatas)
    if st.button("➖ Retirar categoria", key="cfg_geral_retirar_btn",
                 disabled=not alvos or todas_escolhidas):
        ok, msg = cat_repo.retirar(alvos, tipo)
        _depois_da_acao(ok, msg, f"cfg_geral_retirar_{tipo}")
    if todas_escolhidas:
        st.caption(f"Deixe ao menos uma categoria em {rotulo}: sem nenhuma, o "
                   "lançamento seria gravado sem categoria.")
    else:
        st.caption("Retirar só tira a opção do seu lançamento. Os lançamentos "
                   "antigos continuam com a categoria, os totais não mudam e "
                   "ela pode voltar quando você quiser.")


def _render_devolver(tipo: str, rotulo: str, retiradas: list[dict]) -> None:
    if not retiradas:
        return
    nomes = {c["id"]: c["nome"] for c in retiradas}
    alvos = st.multiselect(
        f"Retiradas de {rotulo} ({len(retiradas)})", list(nomes),
        key=f"cfg_geral_devolver_{tipo}",
        format_func=lambda cid: nomes.get(cid, cid),
        placeholder="Escolha as que devem voltar ao seletor",
    )
    if st.button("↩️ Devolver ao seletor", key="cfg_geral_devolver_btn",
                 disabled=not alvos):
        ok, msg = cat_repo.devolver(alvos)
        _depois_da_acao(ok, msg, f"cfg_geral_devolver_{tipo}")


def _depois_da_acao(ok: bool, msg: str, chave_widget: str) -> None:
    """Erro fica na tela; acerto vira recado e redesenha o bloco.

    A escolha do widget sai junto: depois de retirar, os ids escolhidos não
    estão mais entre as opções, e o Streamlit recusaria o valor velho.
    """
    if not ok:
        st.error(msg)
        return
    st.session_state[_RECADO_CAT] = msg
    st.session_state.pop(chave_widget, None)
    st.rerun()


# -- trocar de usuário --------------------------------------------------------

def _render_trocar_usuario() -> None:
    from core.user_context import principal

    with st.container(border=True, key="cfg_geral_usuario"):
        _cabecalho(
            "03", "👤 Trocar de usuário",
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
            "04", "🧹 Limpar histórico da LLM",
            "Escolha a seção cuja conversa deve ser apagada. Nada é apagado "
            "sem a escolha e o clique.",
        )
        try:
            contagens = chat_repository.contagens()
        except Exception as exc:  # noqa: BLE001
            falha_de_acao("Não foi possível ler o histórico de conversas.", exc)
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
                falha_de_acao("Nada foi apagado.", exc)
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
