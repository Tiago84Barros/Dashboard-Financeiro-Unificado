"""Barra de conversa sobre uma classe da carteira, dentro da aba Análise.

Mesmo padrão do chat por ativo, com o histórico preso à classe: cada sub-aba
tem a sua conversa, e a de Ações não herda o contexto da de FIIs. A assinatura
inclui os tickers da classe — mudar a carteira reinicia a conversa, porque o
contexto que a LLM recebeu deixou de valer.
"""
from __future__ import annotations

from html import escape
from typing import Callable, Sequence

import streamlit as st

from core.chat_memory import (
    clear_chat_history,
    conversation_key,
    load_chat_history,
    save_chat_history,
    visible_chat_history,
)
from core.llm_b3 import llm_disponivel, provedores_disponiveis
from core.llm_carteira import chat_com_carteira
from core.llm_dossie_carteira import gerar_dossie_classe
from core.utils import escapar_cifrao

_PROVEDOR_LABEL = {"openai": "OpenAI", "gemini": "Gemini", "openrouter": "OpenRouter"}

_SUGESTOES: dict[str, tuple[str, ...]] = {
    "acoes": (
        "Onde esta parte da carteira está mais concentrada?",
        "Quais empresas puxam a média de P/L para cima?",
        "O que falta de dado para avaliar estas ações?",
    ),
    "fiis": (
        "Como estes fundos se comparam aos pares do mesmo tipo?",
        "O DY médio destes FIIs é sustentável pelo que se sabe?",
        "Quais riscos de vacância ou crédito aparecem aqui?",
    ),
    "tesouro": (
        "O prazo médio faz sentido para este perfil de indexadores?",
        "O que o juro real atual diz sobre IPCA+ contra Selic?",
        "Qual o efeito de vender antes do vencimento?",
    ),
    "exterior": (
        "Qual é a exposição cambial desta parte da carteira?",
        "O que não dá para avaliar por serem ETFs?",
        "Como esta parcela se relaciona com o restante da carteira?",
    ),
    "geral": (
        "A alocação entre classes está equilibrada para a renda que recebo?",
        "Onde está o maior risco de concentração da carteira?",
        "Qual classe deveria receber o próximo aporte, e por quê?",
    ),
}

_PLACEHOLDER = {
    "acoes": "Pergunte sobre suas ações — múltiplos, setores, concentração, riscos…",
    "fiis": "Pergunte sobre seus FIIs — P/VP, renda, vacância, tipo, pares…",
    "tesouro": "Pergunte sobre seus títulos — indexador, prazo, juro real, marcação…",
    "exterior": "Pergunte sobre o exterior — câmbio, composição, o que dá e o que não dá para avaliar…",
    "geral": "Pergunte sobre a carteira inteira — alocação, concentração, renda, próximo aporte…",
}

_TITULO = {"acoes": "suas ações", "fiis": "seus FIIs",
           "tesouro": "seu Tesouro Direto", "exterior": "sua posição no exterior",
           "geral": "sua carteira"}

_DOSSIE_LABEL = {
    "acoes": "Concentração setorial, comparação com pares da B3, substituições "
             "e plano de aporte.",
    "fiis": "Concentração por tipo e segmento, comparação com pares do mesmo "
            "tipo, substituições e plano de aporte.",
    "tesouro": "Concentração por indexador e vencimento, prazo contra objetivo, "
               "juro real e a escolha do próximo aporte.",
    "exterior": "Concentração setorial e cambial, comparação com pares do "
                "universo americano e plano de aporte.",
}


def _card_html(titulo: str, texto: str, accent: str) -> str:
    """Card CSS em UM único bloco — moldura e conteúdo nunca se separam."""
    return (
        f'<div style="background:var(--app-surface);border:1px solid var(--app-border);'
        f'border-left:3px solid {accent};border-radius:10px;padding:12px 14px;'
        f'margin:6px 0 12px;">'
        f'<div style="font-size:.80rem;font-weight:700;color:var(--app-text);'
        f'margin-bottom:4px;">{escape(titulo)}</div>'
        f'<div style="font-size:.75rem;color:var(--app-muted);line-height:1.5;">'
        f'{escape(texto)}</div>'
        f'</div>'
    )


def render_chat_carteira(
    *,
    classe: str,
    tickers: Sequence[str],
    build_context: Callable[..., str],
    sugestoes: Sequence[str] | None = None,
    # Roxo escurecido: sem token, ficava fixo, e #B084F6 rende 2,8:1
    # sobre a pagina clara -- ilegivel. #9B51E0 fica legivel nos dois.
    accent: str = "#9B51E0",
) -> None:
    """Desenha a barra de chat no fim de uma sub-aba da Análise do Portfólio.

    ``build_context`` recebe a pergunta e o sinalizador ``valores_reais`` e
    devolve o contexto auditável; só é chamado quando existe pergunta ou quando
    o dossiê é pedido, para não pagar o custo a cada rerun.
    """
    classe = str(classe or "acoes").lower()
    # A Visão Geral usa a mesma barra com a carteira inteira como escopo: o
    # toggle de reais passa a cobrir o consolidado, e o dossiê — roteiro de
    # doze seções escrito para UMA classe — não é oferecido.
    geral = classe == "geral"
    recorte = "da carteira inteira" if geral else "desta classe"
    presentes = tuple(sorted({str(t or "").strip().upper() for t in (tickers or ()) if t}))
    if not presentes:
        return

    st.markdown("---")
    st.markdown(f"#### 💬 Converse sobre {_TITULO.get(classe, 'esta classe')}")
    if not llm_disponivel():
        st.info("Nenhum provedor LLM configurado. Adicione OPENAI_API_KEY ou "
                "GEMINI_API_KEY para conversar sobre esta classe.")
        return

    provedores = provedores_disponiveis()
    if provedores:
        st.caption("Provedor disponível: " + ", ".join(
            _PROVEDOR_LABEL.get(p, p) for p in provedores))

    hist_key = f"chat_carteira_{classe}_history"
    sig_key = f"chat_carteira_{classe}_signature"
    signature = f"{classe}:{','.join(presentes)}"
    anterior = st.session_state.get(sig_key)
    memory_key = conversation_key("chat_carteira", signature)
    if anterior is not None and anterior != signature:
        st.session_state.pop(hist_key, None)
    st.session_state[sig_key] = signature

    # Reais são opt-in e ficam DESMARCADOS por padrão: o contexto trabalha com
    # percentuais. Marcado, vai o valor de mercado por posição desta classe —
    # nunca o patrimônio consolidado, nunca as outras classes. Na Visão Geral
    # o escopo É o consolidado, e o rótulo do toggle diz isso.
    valores_reais = st.checkbox(
        f"Enviar os valores em reais {recorte} à LLM",
        key=f"chat_carteira_{classe}_reais", value=False,
        help=("Desmarcado, a LLM vê só percentuais. Marcado, ela vê custo, "
              "valor de mercado, renda e o valor de cada posição."
              if geral else
              "Desmarcado, a LLM vê só percentuais. Marcado, ela vê o valor de "
              "mercado de cada posição desta classe — e só desta classe."))

    if geral:
        cartao = _card_html(
            f"Chat sobre a carteira inteira — {len(presentes)} ativo(s)",
            "A resposta usa o que esta aba mostra: alocação por classe e por "
            "setor, peso de cada posição, retorno mercado/custo e renda de 12 "
            "meses sobre o custo. "
            + ("Os valores em reais vão junto." if valores_reais else
               "Valores em reais e quantidades não são enviados.")
            + " Múltiplos e notas por ativo ficam nas sub-abas de cada classe.",
            accent,
        )
    else:
        cartao = _card_html(
            f"Chat focado em {len(presentes)} ativo(s) desta classe",
            "A resposta usa apenas o que esta aba carregou: composição em percentual, "
            "médias com a respectiva cobertura e as notas do universo do banco. "
            + ("Os valores em reais desta classe vão junto; o patrimônio total e as "
               "outras classes não."
               if valores_reais else
               "Valores em reais e quantidades não são enviados.")
            + " O que falta de dado é declarado em vez de preenchido.",
            accent,
        )
    st.markdown(cartao, unsafe_allow_html=True)

    col_dossie, _, col_limpar = st.columns([2, 3, 1])
    gerar = False
    if not geral:
        with col_dossie:
            gerar = st.button("📑 Gerar dossiê da classe",
                              key=f"chat_carteira_{classe}_dossie", width="stretch",
                              help=_DOSSIE_LABEL.get(classe, ""))
    with col_limpar:
        if st.button("🗑️ Limpar chat", key=f"chat_carteira_{classe}_clear",
                     width="stretch"):
            clear_chat_history(memory_key, session_key=hist_key)
            st.rerun()

    perguntas = tuple(sugestoes) if sugestoes else _SUGESTOES.get(classe, ())
    sugerida = None
    if perguntas:
        colunas = st.columns(len(perguntas))
        for i, pergunta in enumerate(perguntas):
            with colunas[i]:
                if st.button(pergunta, key=f"chat_carteira_{classe}_sug_{i}",
                             width="stretch"):
                    sugerida = pergunta

    historico = load_chat_history(memory_key, session_key=hist_key)
    for mensagem in visible_chat_history(historico, hist_key):
        with st.chat_message(mensagem["role"]):
            st.markdown(escapar_cifrao(mensagem["content"]))

    if gerar:
        pedido = "Gerar o dossiê completo desta classe."
        historico.append({"role": "user", "content": pedido})
        with st.chat_message("user"):
            st.markdown(pedido)
        with st.chat_message("assistant"):
            with st.spinner("Montando o dossiê — concentração, pares, "
                            "substituições, tributação e plano de aporte…"):
                try:
                    contexto = build_context(pedido, valores_reais=valores_reais)
                    resposta = gerar_dossie_classe(contexto, classe=classe)
                except Exception as exc:  # provedor fora do ar, timeout, dado ausente
                    resposta = f"Não foi possível gerar o dossiê agora: {exc}"
            st.markdown(escapar_cifrao(resposta))
            st.caption("Análise educacional baseada nos dados carregados nesta "
                       "aba. A decisão é sua.")
        historico.append({"role": "assistant", "content": resposta})
        save_chat_history(memory_key, historico, session_key=hist_key)
        return

    digitada = st.chat_input(
        _PLACEHOLDER.get(classe, _PLACEHOLDER["acoes"]),
        key=f"chat_carteira_{classe}_input",
    )
    pergunta = sugerida or digitada
    if not pergunta:
        return

    historico.append({"role": "user", "content": pergunta})
    with st.chat_message("user"):
        st.markdown(escapar_cifrao(pergunta))
    with st.chat_message("assistant"):
        with st.spinner("Consultando composição, médias e universo do banco…"):
            try:
                contexto = build_context(pergunta, valores_reais=valores_reais)
                resposta = chat_com_carteira(contexto, historico[:-1], pergunta,
                                             classe=classe)
            except Exception as exc:  # provedor fora do ar, timeout, dado ausente
                resposta = f"Não foi possível consultar a LLM neste momento: {exc}"
        st.markdown(escapar_cifrao(resposta))
        st.caption("Análise educacional baseada nos dados carregados nesta "
                   "aba. A decisão é sua.")
    historico.append({"role": "assistant", "content": resposta})
    save_chat_history(memory_key, historico, session_key=hist_key)
