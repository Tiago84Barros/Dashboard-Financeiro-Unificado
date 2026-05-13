"""
pages/dashboard_geral.py
Visão consolidada das finanças: patrimônio, saldo, receitas, despesas e evolução.
Origem: Tiago84Barros/Dashboard
Status: mock visual (Fase 2) — dados reais na Fase 3
"""
import streamlit as st
import plotly.graph_objects as go

from design.componentes import card_metrica, container_pagina, secao_titulo, badge_status
from core.utils import fmt_moeda, fmt_percentual


# ── Dados mockados ────────────────────────────────────────────────────────────
# Substituir por core/financeiro.py na Fase 3
_MOCK = {
    "patrimonio_total":    87_450.00,
    "patrimonio_delta":     5.2,
    "saldo_disponivel":    12_300.00,
    "saldo_delta":         -2.1,
    "receitas_mes":         8_500.00,
    "despesas_mes":         4_200.00,
    "taxa_poupança":       50.6,
    "meses": ["Dez", "Jan", "Fev", "Mar", "Abr", "Mai"],
    "receitas_hist": [7_800, 8_100, 7_500, 8_900, 8_200, 8_500],
    "despesas_hist": [4_500, 3_900, 4_100, 4_800, 4_000, 4_200],
}


def _grafico_fluxo(meses, receitas, despesas) -> go.Figure:
    """Gráfico de barras agrupadas: receitas vs despesas."""
    fig = go.Figure()
    fig.add_trace(go.Bar(
        name="Receitas",
        x=meses,
        y=receitas,
        marker_color="#00C896",
        text=[f"R$ {v/1000:.1f}K" for v in receitas],
        textposition="outside",
        textfont={"size": 11, "color": "#CBD5E0"},
    ))
    fig.add_trace(go.Bar(
        name="Despesas",
        x=meses,
        y=despesas,
        marker_color="#FC5C7D",
        text=[f"R$ {v/1000:.1f}K" for v in despesas],
        textposition="outside",
        textfont={"size": 11, "color": "#CBD5E0"},
    ))
    fig.update_layout(
        barmode="group",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font_color="#9CA3AF",
        legend={"orientation": "h", "y": -0.2},
        margin={"t": 20, "b": 20, "l": 0, "r": 0},
        yaxis={"gridcolor": "#1E2533", "tickformat": ",.0f", "tickprefix": "R$ "},
        xaxis={"showgrid": False},
        height=320,
    )
    return fig


def render() -> None:
    container_pagina("Dashboard Geral", "Visão consolidada das suas finanças", "📊")

    # ── Linha de badges de status ─────────────────────────────────────────────
    col_b1, col_b2, *_ = st.columns([1, 1, 4])
    with col_b1:
        badge_status("Modo mock", "alerta")
    with col_b2:
        badge_status("Dados de exemplo", "info")
    st.markdown("<br>", unsafe_allow_html=True)

    # ── KPIs — linha 1 ────────────────────────────────────────────────────────
    col1, col2, col3, col4 = st.columns(4)

    with col1:
        card_metrica(
            "Patrimônio Total",
            fmt_moeda(_MOCK["patrimonio_total"]),
            delta=fmt_percentual(_MOCK["patrimonio_delta"]),
            positivo=True,
            ajuda="Investimentos + saldo bancário",
        )
    with col2:
        card_metrica(
            "Saldo Disponível",
            fmt_moeda(_MOCK["saldo_disponivel"]),
            delta=fmt_percentual(_MOCK["saldo_delta"]),
            positivo=False,
            ajuda="Total em contas correntes e poupança",
        )
    with col3:
        card_metrica(
            "Receitas do Mês",
            fmt_moeda(_MOCK["receitas_mes"]),
            ajuda="Total de entradas no mês corrente",
        )
    with col4:
        card_metrica(
            "Despesas do Mês",
            fmt_moeda(_MOCK["despesas_mes"]),
            delta=fmt_percentual(5.0),
            positivo=False,
            ajuda="Total de saídas no mês corrente",
        )

    st.divider()

    # ── Gráfico de fluxo de caixa ─────────────────────────────────────────────
    col_graf, col_taxa = st.columns([3, 1])

    with col_graf:
        secao_titulo("Fluxo de Caixa", "💹", "Receitas vs Despesas — últimos 6 meses")
        fig = _grafico_fluxo(
            _MOCK["meses"],
            _MOCK["receitas_hist"],
            _MOCK["despesas_hist"],
        )
        st.plotly_chart(fig, use_container_width=True)

    with col_taxa:
        secao_titulo("Resumo do Mês", "📋")
        st.metric(
            "Taxa de Poupança",
            fmt_percentual(_MOCK["taxa_poupança"]),
            help="(Receitas − Despesas) / Receitas × 100",
        )
        st.metric(
            "Sobra Mensal",
            fmt_moeda(_MOCK["receitas_mes"] - _MOCK["despesas_mes"]),
        )
        st.metric(
            "Meses de Reserva",
            "2,9×",
            help="Saldo disponível / Despesa média mensal",
        )
