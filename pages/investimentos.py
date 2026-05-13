"""
pages/investimentos.py
Visão geral de investimentos: patrimônio, rentabilidade e evolução.
Origem: Tiago84Barros/Dashboard-Investimentos
Status: mock visual (Fase 2) — implementação completa na Fase 5
"""
import streamlit as st
import plotly.graph_objects as go

from design.componentes import (
    card_metrica, container_pagina, secao_titulo,
    indicador_linha, em_construcao, badge_status,
)
from core.utils import fmt_moeda, fmt_percentual


# ── Dados mockados ────────────────────────────────────────────────────────────
_MOCK = {
    "total_investido":  75_150.00,
    "rentab_ano":       12.4,
    "dividendos_ano":    2_840.00,
    "ativos": [
        ("PETR4",   "Ações BR",    18_500.00,  +15.2),
        ("MXRF11",  "FII",         12_300.00,  + 8.7),
        ("IVVB11",  "ETF",         22_100.00,  +18.9),
        ("Tesouro", "Renda Fixa",  14_200.00,  +11.3),
        ("BTC",     "Cripto",       8_050.00,  -12.4),
    ],
    "meses": ["Dez", "Jan", "Fev", "Mar", "Abr", "Mai"],
    "patrimonio_hist": [62_000, 65_400, 67_200, 70_100, 73_500, 75_150],
}


def _grafico_evolucao(meses, valores) -> go.Figure:
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=meses,
        y=valores,
        mode="lines+markers",
        line={"color": "#00C896", "width": 2.5},
        marker={"color": "#00C896", "size": 7},
        fill="tozeroy",
        fillcolor="rgba(0,200,150,0.08)",
        name="Patrimônio",
        hovertemplate="R$ %{y:,.2f}<extra></extra>",
    ))
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font_color="#9CA3AF",
        showlegend=False,
        margin={"t": 10, "b": 20, "l": 0, "r": 0},
        yaxis={"gridcolor": "#1E2533", "tickformat": ",.0f", "tickprefix": "R$ "},
        xaxis={"showgrid": False},
        height=260,
    )
    return fig


def render() -> None:
    container_pagina("Investimentos", "Patrimônio, rentabilidade e alocação", "📈")

    badge_status("Modo mock", "alerta")
    st.markdown("<br>", unsafe_allow_html=True)

    # ── KPIs ──────────────────────────────────────────────────────────────────
    col1, col2, col3 = st.columns(3)
    with col1:
        card_metrica(
            "Total Investido",
            fmt_moeda(_MOCK["total_investido"]),
            delta=fmt_percentual(_MOCK["rentab_ano"]),
            positivo=True,
            ajuda="Soma do valor atual de todos os ativos",
        )
    with col2:
        card_metrica(
            "Rentabilidade no Ano",
            fmt_percentual(_MOCK["rentab_ano"]),
            positivo=True,
            ajuda="TWRR acumulado do ano corrente",
        )
    with col3:
        card_metrica(
            "Dividendos Recebidos",
            fmt_moeda(_MOCK["dividendos_ano"]),
            ajuda="Proventos acumulados no ano",
        )

    st.divider()

    col_graf, col_ativos = st.columns([2, 1])

    # ── Gráfico de evolução patrimonial ───────────────────────────────────────
    with col_graf:
        secao_titulo("Evolução do Patrimônio", "📊", "Últimos 6 meses")
        fig = _grafico_evolucao(_MOCK["meses"], _MOCK["patrimonio_hist"])
        st.plotly_chart(fig, use_container_width=True)

    # ── Posições por ativo ────────────────────────────────────────────────────
    with col_ativos:
        secao_titulo("Posições", "💼")
        for ticker, classe, valor, rentab in _MOCK["ativos"]:
            cor = "#00C896" if rentab >= 0 else "#FC5C7D"
            indicador_linha(
                f"**{ticker}** · {classe}",
                fmt_percentual(rentab),
                cor_valor=cor,
            )

    st.divider()
    em_construcao("Fase 5", "Cotações via yfinance, custo médio e carteira completa.")
