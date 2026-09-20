"""Barra de conversa com a LLM sobre o ativo aberto na tela de análise.

Mesmo padrão de UX dos chats de carteira (Criação/Avaliação de Portfólio e
Seleção de FIIs), mas com o histórico preso ao ticker: trocar de ativo reinicia
a conversa, porque o contexto que a LLM recebeu deixou de valer.
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
from core.llm_ativo import chat_com_ativo
from core.llm_b3 import llm_disponivel, provedores_disponiveis
from core.utils import escapar_cifrao

_PROVEDOR_LABEL = {"openai": "OpenAI", "gemini": "Gemini", "openrouter": "OpenRouter"}

_SUGESTOES_PADRAO: dict[str, tuple[str, ...]] = {
    "b3": (
        "Quais são os principais riscos desta empresa hoje?",
        "Como ela se compara aos pares do mesmo segmento?",
        "O que os números não mostram sobre esta empresa?",
    ),
    "us": (
        "Quais são os principais riscos desta empresa hoje?",
        "Como ela se compara aos pares da mesma indústria?",
        "O que sustenta (ou não) o score desta empresa?",
    ),
    "fii": (
        "Quais são os principais riscos deste fundo hoje?",
        "Como ele se compara a fundos do mesmo tipo?",
        "O que o P/VP e o DY deste fundo escondem?",
    ),
}

_PLACEHOLDER = {
    "b3": "Pergunte sobre {tk} — resultados, múltiplos, pares, riscos…",
    "us": "Pergunte sobre {tk} — demonstrações, múltiplos, pares, riscos…",
    "fii": "Pergunte sobre {tk} — carteira, vacância, renda, pares…",
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


def render_chat_ativo(
    *,
    mercado: str,
    ticker: str,
    build_context: Callable[[str], str],
    nome: str = "",
    sugestoes: Sequence[str] | None = None,
    # Roxo escurecido: sem token, ficava fixo, e #B084F6 rende 2,8:1
    # sobre a pagina clara -- ilegivel. #9B51E0 fica legivel nos dois.
    accent: str = "#9B51E0",
) -> None:
    """Desenha a barra de chat no fim da aba de análise de um ativo.

    `build_context` recebe a pergunta do usuário e devolve o contexto auditável;
    só é chamado quando há pergunta, para não pagar o custo em cada rerun.
    """
    mercado = str(mercado or "b3").lower()
    tk = str(ticker or "").strip().upper()
    if not tk:
        return

    rotulo = f"{tk} — {nome}" if nome else tk
    st.markdown("---")
    st.markdown(f"#### 💬 Converse sobre {tk}")
    st.markdown(_card_html(
        f"Chat focado em {rotulo}",
        "Pergunte sobre resultados, múltiplos, endividamento, pares, riscos e o que "
        "falta de dado. A resposta usa apenas o que este app carregou sobre o ativo "
        "e diz explicitamente quando uma informação não está disponível.",
        accent,
    ), unsafe_allow_html=True)

    if not llm_disponivel():
        st.info("Nenhum provedor LLM configurado. Adicione OPENAI_API_KEY ou "
                "GEMINI_API_KEY para conversar sobre o ativo.")
        return

    provedores = provedores_disponiveis()
    if provedores:
        st.caption("Provedor disponível: " + ", ".join(
            _PROVEDOR_LABEL.get(p, p) for p in provedores))

    hist_key = f"chat_ativo_{mercado}_history"
    sig_key = f"chat_ativo_{mercado}_signature"
    signature = f"{mercado}:{tk}"
    anterior = st.session_state.get(sig_key)
    memory_key = conversation_key("chat_ativo", signature)
    if anterior is not None and anterior != signature:
        # Cada ativo tem sua própria conversa persistida; trocar de ativo não
        # apaga a anterior nem a mistura com o contexto atual.
        st.session_state.pop(hist_key, None)
    st.session_state[sig_key] = signature

    _, col_limpar = st.columns([5, 1])
    with col_limpar:
        if st.button("🗑️ Limpar chat", key=f"chat_ativo_{mercado}_clear",
                     width="stretch"):
            clear_chat_history(memory_key, session_key=hist_key)
            st.rerun()

    perguntas = tuple(sugestoes) if sugestoes else _SUGESTOES_PADRAO.get(
        mercado, _SUGESTOES_PADRAO["b3"])
    sugerida = None
    if perguntas:
        colunas = st.columns(len(perguntas))
        for i, pergunta in enumerate(perguntas):
            with colunas[i]:
                if st.button(pergunta, key=f"chat_ativo_{mercado}_sug_{i}",
                             width="stretch"):
                    sugerida = pergunta

    historico = load_chat_history(memory_key, session_key=hist_key)
    for mensagem in visible_chat_history(historico, hist_key):
        with st.chat_message(mensagem["role"]):
            st.markdown(escapar_cifrao(mensagem["content"]))

    digitada = st.chat_input(
        _PLACEHOLDER.get(mercado, _PLACEHOLDER["b3"]).format(tk=tk),
        key=f"chat_ativo_{mercado}_input",
    )
    pergunta = sugerida or digitada
    if not pergunta:
        return

    historico.append({"role": "user", "content": pergunta})
    with st.chat_message("user"):
        st.markdown(escapar_cifrao(pergunta))
    with st.chat_message("assistant"):
        with st.spinner(f"Consultando os dados de {tk}, pares e qualidade…"):
            try:
                contexto = build_context(pergunta)
                resposta = chat_com_ativo(contexto, historico[:-1], pergunta,
                                          mercado=mercado, ticker=tk)
            except Exception as exc:  # provedor fora do ar, timeout, dado ausente
                resposta = f"Não foi possível consultar a LLM neste momento: {exc}"
        st.markdown(escapar_cifrao(resposta))
        st.caption("Análise educacional baseada nos dados disponíveis; "
                   "não constitui recomendação de compra ou venda.")
    historico.append({"role": "assistant", "content": resposta})
    save_chat_history(memory_key, historico, session_key=hist_key)
