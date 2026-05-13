"""
pages/metas.py
Acompanhamento de metas financeiras: progresso, aporte necessário e prazo.
Status: mock visual (Fase 2) — implementação completa na Fase 7
"""
import streamlit as st

from design.componentes import (
    container_pagina, secao_titulo, barra_progresso,
    badge_status, em_construcao, card_metrica,
)
from core.utils import fmt_moeda, fmt_percentual


# ── Dados mockados ────────────────────────────────────────────────────────────
_METAS = [
    {
        "nome":          "Reserva de Emergência",
        "icone":         "🛡️",
        "atual":         19_500.00,
        "total":         30_000.00,
        "prazo":         "Dez 2026",
        "aporte_mensal": 1_750.00,
        "status":        "em_andamento",
    },
    {
        "nome":          "Viagem — Europa",
        "icone":         "✈️",
        "atual":          6_000.00,
        "total":         15_000.00,
        "prazo":         "Jul 2027",
        "aporte_mensal": 750.00,
        "status":        "em_andamento",
    },
    {
        "nome":          "Troca do Carro",
        "icone":         "🚗",
        "atual":          5_500.00,
        "total":         25_000.00,
        "prazo":         "Dez 2027",
        "aporte_mensal": 1_200.00,
        "status":        "em_andamento",
    },
    {
        "nome":          "Fundo de Investimento",
        "icone":         "📈",
        "atual":         75_150.00,
        "total":        100_000.00,
        "prazo":         "Jun 2027",
        "aporte_mensal": 2_000.00,
        "status":        "em_andamento",
    },
]

_STATUS_BADGE = {
    "concluida":     ("Concluída", "sucesso"),
    "em_andamento":  ("Em andamento", "info"),
    "atrasada":      ("Atrasada", "erro"),
    "pausada":       ("Pausada", "neutro"),
}


def render() -> None:
    container_pagina("Metas", "Acompanhe o progresso dos seus objetivos financeiros", "🎯")

    # ── Resumo rápido ──────────────────────────────────────────────────────────
    total_metas = len(_METAS)
    total_acumulado = sum(m["atual"] for m in _METAS)
    total_objetivo = sum(m["total"] for m in _METAS)
    pct_geral = total_acumulado / total_objetivo if total_objetivo else 0

    col1, col2, col3 = st.columns(3)
    with col1:
        card_metrica("Metas Ativas", str(total_metas))
    with col2:
        card_metrica("Total Acumulado", fmt_moeda(total_acumulado))
    with col3:
        card_metrica(
            "Progresso Geral",
            fmt_percentual(pct_geral * 100, sinal=False),
            positivo=pct_geral >= 0.5,
        )

    st.divider()
    badge_status("Modo mock", "alerta")
    st.markdown("<br>", unsafe_allow_html=True)

    # ── Cards de cada meta ────────────────────────────────────────────────────
    secao_titulo("Seus Objetivos", "🎯")

    for meta in _METAS:
        with st.container(border=True):
            col_info, col_aporte = st.columns([3, 1])

            with col_info:
                texto_status, tipo_status = _STATUS_BADGE.get(
                    meta["status"], ("Desconhecido", "neutro")
                )
                st.markdown(
                    f"### {meta['icone']} {meta['nome']}",
                )
                badge_status(texto_status, tipo_status)
                st.markdown("<br>", unsafe_allow_html=True)
                barra_progresso(
                    f"Meta: {fmt_moeda(meta['total'])} · Prazo: {meta['prazo']}",
                    meta["atual"],
                    meta["total"],
                    fmt_valor=fmt_moeda(meta["atual"]),
                    fmt_total=fmt_moeda(meta["total"]),
                )

            with col_aporte:
                st.metric(
                    "Aporte mensal",
                    fmt_moeda(meta["aporte_mensal"]),
                    help="Valor necessário para atingir a meta no prazo",
                )
                meses_restantes = (meta["total"] - meta["atual"]) / meta["aporte_mensal"]
                st.caption(f"~{meses_restantes:.0f} meses restantes")

        st.markdown("", unsafe_allow_html=True)

    st.divider()
    em_construcao("Fase 7", "Projeção de prazo, simulação de aportes e alertas de desvio.")
