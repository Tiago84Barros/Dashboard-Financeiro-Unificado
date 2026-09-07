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

from core.llm_b3 import llm_disponivel, provedores_disponiveis
from core.llm_carteira import chat_com_carteira

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
}

_PLACEHOLDER = {
    "acoes": "Pergunte sobre suas ações — múltiplos, setores, concentração, riscos…",
    "fiis": "Pergunte sobre seus FIIs — P/VP, renda, vacância, tipo, pares…",
    "tesouro": "Pergunte sobre seus títulos — indexador, prazo, juro real, marcação…",
    "exterior": "Pergunte sobre o exterior — câmbio, composição, o que dá e o que não dá para avaliar…",
}

_TITULO = {"acoes": "suas ações", "fiis": "seus FIIs",
           "tesouro": "seu Tesouro Direto", "exterior": "sua posição no exterior"}


def _card_html(titulo: str, texto: str, accent: str) -> str:
    """Card CSS em UM único bloco — moldura e conteúdo nunca se separam."""
    return (
        f'<div style="background:#151A24;border:1px solid #232A36;'
        f'border-left:3px solid {accent};border-radius:10px;padding:12px 14px;'
        f'margin:6px 0 12px;">'
        f'<div style="font-size:.80rem;font-weight:700;color:#E2E8F0;'
        f'margin-bottom:4px;">{escape(titulo)}</div>'
        f'<div style="font-size:.75rem;color:#8B95A5;line-height:1.5;">'
        f'{escape(texto)}</div>'
        f'</div>'
    )


def render_chat_carteira(
    *,
    classe: str,
    tickers: Sequence[str],
    build_context: Callable[[str], str],
    sugestoes: Sequence[str] | None = None,
    accent: str = "#B084F6",
) -> None:
    """Desenha a barra de chat no fim de uma sub-aba da Análise do Portfólio.

    ``build_context`` recebe a pergunta e devolve o contexto auditável; só é
    chamado quando existe pergunta, para não pagar o custo a cada rerun.
    """
    classe = str(classe or "acoes").lower()
    presentes = tuple(sorted({str(t or "").strip().upper() for t in (tickers or ()) if t}))
    if not presentes:
        return

    st.markdown("---")
    st.markdown(f"#### 💬 Converse sobre {_TITULO.get(classe, 'esta classe')}")
    st.markdown(_card_html(
        f"Chat focado em {len(presentes)} ativo(s) desta classe",
        "A resposta usa apenas o que esta aba carregou: composição em percentual, "
        "médias com a respectiva cobertura e as notas do universo do banco. "
        "Valores em reais e quantidades não são enviados, e o que falta de dado "
        "é declarado em vez de preenchido.",
        accent,
    ), unsafe_allow_html=True)

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
    if anterior is not None and anterior != signature:
        st.session_state.pop(hist_key, None)
        st.caption("O histórico foi reiniciado porque os ativos desta classe mudaram.")
    st.session_state[sig_key] = signature

    _, col_limpar = st.columns([5, 1])
    with col_limpar:
        if st.button("🗑️ Limpar chat", key=f"chat_carteira_{classe}_clear",
                     width="stretch"):
            st.session_state.pop(hist_key, None)
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

    historico: list[dict] = st.session_state.get(hist_key, [])
    for mensagem in historico:
        with st.chat_message(mensagem["role"]):
            st.markdown(mensagem["content"])

    digitada = st.chat_input(
        _PLACEHOLDER.get(classe, _PLACEHOLDER["acoes"]),
        key=f"chat_carteira_{classe}_input",
    )
    pergunta = sugerida or digitada
    if not pergunta:
        return

    historico.append({"role": "user", "content": pergunta})
    with st.chat_message("user"):
        st.markdown(pergunta)
    with st.chat_message("assistant"):
        with st.spinner("Consultando composição, médias e universo do banco…"):
            try:
                contexto = build_context(pergunta)
                resposta = chat_com_carteira(contexto, historico[:-1], pergunta,
                                             classe=classe)
            except Exception as exc:  # provedor fora do ar, timeout, dado ausente
                resposta = f"Não foi possível consultar a LLM neste momento: {exc}"
        st.markdown(resposta)
        st.caption("Análise educacional baseada nos dados disponíveis; "
                   "não constitui recomendação de compra ou venda.")
    historico.append({"role": "assistant", "content": resposta})
    st.session_state[hist_key] = historico
