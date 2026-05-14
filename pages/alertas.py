"""
pages/alertas.py
Central de alertas financeiros gerados a partir de dados reais.

Seções:
  1. Contadores por severidade (Urgentes, Atenção, Info, Positivos)
  2. Lista de alertas com cards formatados

Regras implementadas (Fase 5.6):
  R1 — Orçamentos próximos/estourados (v_budget_usage_mtd)
  R2 — Metas com alto progresso ou prazo próximo (financial_goals)
  R3 — asset_quotes vazia (sem cotações)
  R4 — budgets vazia (sem orçamentos reais)
  R5 — Saldo mensal negativo ou taxa de poupança alta (v_monthly_cashflow)

Dados: core/alertas.get_alertas() — mock → real (Fase 5.6)
"""
import streamlit as st

from core.alertas import get_alertas
from design.componentes import (
    badge_status,
    card_metrica,
    container_pagina,
    secao_titulo,
)

_TIPO_CORES = {
    "sucesso": ("Positivo",   "sucesso"),
    "alerta":  ("Atenção",    "alerta"),
    "erro":    ("Urgente",    "erro"),
    "info":    ("Informação", "info"),
}


def render() -> None:
    container_pagina("Alertas", "Notificações e avisos importantes para suas finanças", "🔔")

    d        = get_alertas()
    alertas  = d["alertas"]
    contagem = d["contagem"]

    # ── Badges ────────────────────────────────────────────────────────────────
    _fonte = d.get("data_source", "mock")
    _label, _tipo = (
        ("Dados reais",     "sucesso") if _fonte == "real" else
        ("Fallback (mock)", "erro")    if _fonte == "mock_fallback" else
        ("Modo mock",       "alerta")
    )
    col_b1, col_b2, *_ = st.columns([1, 1, 5])
    with col_b1:
        badge_status(_label, _tipo)
    with col_b2:
        badge_status(f"{len(alertas)} alertas", "neutro")

    st.markdown("<br>", unsafe_allow_html=True)

    # ══════════════════════════════════════════════════════════════════════════
    # SEÇÃO 1 — Contadores
    # ══════════════════════════════════════════════════════════════════════════
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        card_metrica(
            "Urgentes 🔴",
            str(contagem["erro"]),
            positivo=False if contagem["erro"] > 0 else None,
        )
    with c2:
        card_metrica("Atenção ⚠️", str(contagem["alerta"]))
    with c3:
        card_metrica("Informações ℹ️", str(contagem["info"]))
    with c4:
        card_metrica(
            "Positivos ✅",
            str(contagem["sucesso"]),
            positivo=True if contagem["sucesso"] > 0 else None,
        )

    st.divider()

    # ══════════════════════════════════════════════════════════════════════════
    # SEÇÃO 2 — Lista de alertas
    # ══════════════════════════════════════════════════════════════════════════
    secao_titulo("Alertas Recentes", "🔔")

    if not alertas:
        st.info("Nenhum alerta no momento.", icon="✅")
        return

    # Ordenar: erro → alerta → info → sucesso
    _ORDEM = {"erro": 0, "alerta": 1, "info": 2, "sucesso": 3}
    alertas_sorted = sorted(alertas, key=lambda a: _ORDEM.get(a["tipo"], 4))

    for alerta in alertas_sorted:
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

    if _fonte != "real":
        st.info(
            "**Engine de alertas** — Ative os dados reais configurando "
            "`MOCK_MODE=false` no `.env` ou Streamlit Secrets para receber "
            "alertas calculados automaticamente a partir dos seus dados.",
            icon="🔔",
        )
    else:
        st.caption(
            "Alertas calculados automaticamente · "
            "Engine v1.0 — Fase 5.6 · Próxima versão: análise por IA (Fase 8)"
        )
