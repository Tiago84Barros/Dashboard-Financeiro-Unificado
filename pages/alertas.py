"""
pages/alertas.py
Central de alertas financeiros: orçamento, metas, mercado e IA.
Status: mock visual (Fase 2) — engine de regras na Fase 8
"""
import streamlit as st

from design.componentes import (
    container_pagina, secao_titulo, badge_status,
    em_construcao, card_metrica,
)


# ── Dados mockados ────────────────────────────────────────────────────────────
_ALERTAS = [
    {
        "tipo":      "alerta",
        "icone":     "⚠️",
        "titulo":    "Orçamento de Restaurantes quase no limite",
        "descricao": "Você usou 85% do orçamento de Restaurantes neste mês. "
                     "Restam R$ 45,00 de R$ 300,00.",
        "modulo":    "Controle Financeiro",
        "data":      "Hoje, 14h32",
    },
    {
        "tipo":      "info",
        "icone":     "📰",
        "titulo":    "SELIC subiu para 12,75% ao ano",
        "descricao": "O Copom elevou a taxa básica de juros em +0,25 p.p. "
                     "Considere revisar seus investimentos em renda fixa.",
        "modulo":    "Cenário Macroeconômico",
        "data":      "Ontem, 19h00",
    },
    {
        "tipo":      "sucesso",
        "icone":     "🎯",
        "titulo":    "Meta 'Reserva de Emergência' atingiu 65%",
        "descricao": "Você acumulou R$ 19.500,00 dos R$ 30.000,00 planejados. "
                     "Continue assim!",
        "modulo":    "Metas",
        "data":      "Ontem, 08h15",
    },
    {
        "tipo":      "alerta",
        "icone":     "📉",
        "titulo":    "Ativo BTC com queda de 12,4% no mês",
        "descricao": "Sua posição em Bitcoin acumula -12,4% nos últimos 30 dias. "
                     "Avalie se ainda está de acordo com sua estratégia.",
        "modulo":    "Investimentos",
        "data":      "3 dias atrás",
    },
    {
        "tipo":      "erro",
        "icone":     "🔴",
        "titulo":    "Banco de dados não configurado",
        "descricao": "SUPABASE_UNIFICADO_URL ausente. Configure no arquivo .env "
                     "(dev local) ou em Streamlit Secrets (Settings > Secrets) "
                     "para ativar os dados reais.",
        "modulo":    "Configurações",
        "data":      "Sempre",
    },
]

_TIPO_CORES = {
    "sucesso": ("Positivo",  "sucesso"),
    "alerta":  ("Atenção",   "alerta"),
    "erro":    ("Urgente",   "erro"),
    "info":    ("Informação","info"),
}


def render() -> None:
    container_pagina("Alertas", "Notificações e avisos importantes para suas finanças", "🔔")

    # ── Contadores por severidade ─────────────────────────────────────────────
    contagem = {"sucesso": 0, "alerta": 0, "erro": 0, "info": 0}
    for a in _ALERTAS:
        contagem[a["tipo"]] = contagem.get(a["tipo"], 0) + 1

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        card_metrica("Urgentes 🔴", str(contagem["erro"]),
                     positivo=False if contagem["erro"] > 0 else None)
    with col2:
        card_metrica("Atenção ⚠️", str(contagem["alerta"]))
    with col3:
        card_metrica("Informações ℹ️", str(contagem["info"]))
    with col4:
        card_metrica("Positivos ✅", str(contagem["sucesso"]), positivo=True)

    st.divider()

    badge_status("Modo mock", "alerta")
    badge_status("Engine de regras — Fase 8", "neutro")
    st.markdown("<br>", unsafe_allow_html=True)

    # ── Lista de alertas ───────────────────────────────────────────────────────
    secao_titulo("Alertas Recentes", "🔔")

    for alerta in _ALERTAS:
        texto_badge, tipo_badge = _TIPO_CORES.get(alerta["tipo"], ("Info", "info"))

        with st.container(border=True):
            col_icone, col_corpo = st.columns([0.5, 9.5])
            with col_icone:
                st.markdown(
                    f'<div style="font-size:1.8rem;text-align:center;'
                    f'padding-top:8px">{alerta["icone"]}</div>',
                    unsafe_allow_html=True,
                )
            with col_corpo:
                col_titulo, col_badge = st.columns([4, 1])
                with col_titulo:
                    st.markdown(f"**{alerta['titulo']}**")
                with col_badge:
                    badge_status(texto_badge, tipo_badge)
                st.caption(alerta["descricao"])
                st.markdown(
                    f'<span style="color:#4A5568;font-size:0.75rem">'
                    f'📁 {alerta["modulo"]} · 🕐 {alerta["data"]}</span>',
                    unsafe_allow_html=True,
                )

    st.divider()
    em_construcao("Fase 8", "Engine de 6 regras automáticas + análise por IA (OpenAI).")
