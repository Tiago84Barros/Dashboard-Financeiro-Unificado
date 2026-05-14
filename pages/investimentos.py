"""
pages/investimentos.py
Dashboard de investimentos: visão geral de patrimônio, cashflow e alocação.

Seções:
  1. KPIs       — Total Investido, Valor de Mercado, Rentabilidade, Dividendos, Ativos
  2. Cashflow   — Gráfico de barras: receitas vs despesas mensais (últimos 12 meses)
  3. Alocação   — Distribuição por classe de ativo (indicador_linha)
  4. Posições   — Tabela com top posições da carteira
  5. Navegação  — Links rápidos para Carteira e Proventos

Dados:
  core/investimentos.get_carteira()       — posições e alocação
  core/investimentos.get_cashflow_mensal() — receitas/despesas mensais
  core/proventos.get_proventos()          — dividendos do ano/mês

Origem: Tiago84Barros/Dashboard-Investimentos (App 2) — Fase 5.3
"""
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from core.investimentos import get_carteira, get_cashflow_mensal
from core.proventos import get_proventos
from core.utils import fmt_moeda, fmt_percentual
from design.componentes import (
    badge_status,
    card_metrica,
    container_pagina,
    indicador_linha,
    secao_titulo,
)


# ── Gráfico de cashflow mensal ────────────────────────────────────────────────

def _fig_cashflow(cashflow: list) -> go.Figure:
    labels   = [c["label"]    for c in cashflow]
    receitas = [c["receitas"] for c in cashflow]
    despesas = [c["despesas"] for c in cashflow]
    saldos   = [c["saldo"]    for c in cashflow]

    fig = go.Figure()

    fig.add_trace(go.Bar(
        name="Receitas",
        x=labels, y=receitas,
        marker_color="#00C896",
        opacity=0.85,
        hovertemplate="<b>Receitas %{x}</b><br>R$ %{y:,.2f}<extra></extra>",
    ))

    fig.add_trace(go.Bar(
        name="Despesas",
        x=labels, y=despesas,
        marker_color="#FC5C7D",
        opacity=0.85,
        hovertemplate="<b>Despesas %{x}</b><br>R$ %{y:,.2f}<extra></extra>",
    ))

    fig.add_trace(go.Scatter(
        name="Saldo",
        x=labels, y=saldos,
        mode="lines+markers",
        line={"color": "#4A9EFF", "width": 2},
        marker={"color": "#4A9EFF", "size": 6},
        hovertemplate="<b>Saldo %{x}</b><br>R$ %{y:,.2f}<extra></extra>",
        yaxis="y2",
    ))

    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font_color="#9CA3AF",
        barmode="group",
        legend={"orientation": "h", "y": -0.15},
        margin={"t": 20, "b": 40, "l": 0, "r": 0},
        height=300,
        yaxis={
            "gridcolor":  "#1E2533",
            "tickformat": ",.0f",
            "tickprefix": "R$ ",
        },
        yaxis2={
            "overlaying":  "y",
            "side":        "right",
            "showgrid":    False,
            "tickformat":  ",.0f",
            "tickprefix":  "R$ ",
            "title":       "Saldo",
        },
        xaxis={"showgrid": False},
    )
    return fig


# ── Render principal ──────────────────────────────────────────────────────────

def render() -> None:
    carteira  = get_carteira()
    cashflow  = get_cashflow_mensal()
    proventos = get_proventos()

    container_pagina("Investimentos", "Patrimônio, rentabilidade e cashflow", "📈")

    # ── Badges ────────────────────────────────────────────────────────────────
    _fonte = carteira.get("data_source", "mock")
    _label, _tipo = (
        ("Dados reais",     "sucesso") if _fonte == "real" else
        ("Fallback (mock)", "erro")    if _fonte == "mock_fallback" else
        ("Modo mock",       "alerta")
    )

    col_b1, col_b2, col_b3, col_b4, *_ = st.columns([1, 1, 1, 1, 3])
    with col_b1:
        badge_status(_label, _tipo)
    with col_b2:
        badge_status(f"{carteira['num_ativos']} ativos", "info")
    with col_b3:
        badge_status(
            "Cotações OK" if carteira["cotacoes_disponiveis"] else "Sem cotações",
            "sucesso" if carteira["cotacoes_disponiveis"] else "alerta",
        )
    with col_b4:
        badge_status(f"{proventos['num_eventos']} proventos", "neutro")

    # Aviso de cotações ausentes
    if not carteira["cotacoes_disponiveis"]:
        st.info(
            "**Cotações não disponíveis** — `asset_quotes` está vazia. "
            "Acesse **Configurações > Cotações** para atualizar via yfinance. "
            "Rentabilidade exibida como 0% até as cotações serem importadas.",
            icon="📊",
        )

    st.markdown("<br>", unsafe_allow_html=True)

    # ══════════════════════════════════════════════════════════════════════════
    # SEÇÃO 1 — KPIs
    # ══════════════════════════════════════════════════════════════════════════
    c1, c2, c3, c4, c5 = st.columns(5)

    with c1:
        card_metrica(
            "Total Investido",
            fmt_moeda(carteira["total_investido"]),
            ajuda="Custo histórico total — soma de (quantidade × preço médio) de todas as posições.",
        )
    with c2:
        card_metrica(
            "Valor de Mercado",
            fmt_moeda(carteira["total_mercado"]),
            positivo=True if carteira["total_mercado"] > carteira["total_investido"] else
                     (False if carteira["total_mercado"] < carteira["total_investido"] else None),
            ajuda="Valor atual da carteira. Igual ao investido quando cotações não estão disponíveis.",
        )
    with c3:
        rentab = carteira["rentabilidade_total_pct"]
        card_metrica(
            "Rentabilidade Total",
            fmt_percentual(rentab),
            positivo=rentab > 0 if rentab != 0 else None,
            ajuda="(Valor de Mercado − Total Investido) / Total Investido. Requer cotações.",
        )
    with c4:
        card_metrica(
            "Dividendos no Ano",
            fmt_moeda(proventos["total_ano"]),
            positivo=True if proventos["total_ano"] > 0 else None,
            ajuda="Total de proventos com payment_date no ano corrente.",
        )
    with c5:
        card_metrica(
            "Ativos na Carteira",
            str(carteira["num_ativos"]),
            ajuda="Número de posições ativas em portfolio_positions.",
        )

    st.divider()

    # ══════════════════════════════════════════════════════════════════════════
    # SEÇÃO 2 — Cashflow mensal + Alocação por classe
    # ══════════════════════════════════════════════════════════════════════════
    col_graf, col_aloc = st.columns([3, 2])

    with col_graf:
        secao_titulo(
            "Cashflow Mensal",
            "📊",
            "Receitas, despesas e saldo — últimos 12 meses",
        )
        if cashflow:
            st.plotly_chart(_fig_cashflow(cashflow), use_container_width=True)
        else:
            st.caption("Sem dados de cashflow disponíveis.")

    with col_aloc:
        secao_titulo("Alocação por Classe", "🏷️")
        por_classe = carteira.get("por_classe", [])
        if por_classe:
            for cls in por_classe:
                indicador_linha(
                    cls["nome"],
                    f"{cls['pct_carteira']:.1f}%",
                    cor_valor=cls["cor"],
                    badge=f"{cls['num_ativos']} ativo{'s' if cls['num_ativos'] != 1 else ''}",
                    tipo_badge="neutro",
                )
        else:
            st.caption("Sem dados de alocação.")

        # Cashflow do mês atual
        if cashflow:
            cf_atual = cashflow[-1]
            st.divider()
            secao_titulo("Mês Atual", "📅")
            col_r, col_d = st.columns(2)
            with col_r:
                st.metric("Receitas", fmt_moeda(cf_atual["receitas"]))
            with col_d:
                st.metric("Despesas", fmt_moeda(cf_atual["despesas"]))
            saldo = cf_atual["saldo"]
            cor_saldo = "#00C896" if saldo >= 0 else "#FC5C7D"
            st.markdown(
                f'<div style="text-align:center;font-size:0.9rem;color:{cor_saldo};'
                f'font-weight:600;margin-top:4px">Saldo: {fmt_moeda(saldo)}</div>',
                unsafe_allow_html=True,
            )

    st.divider()

    # ══════════════════════════════════════════════════════════════════════════
    # SEÇÃO 3 — Top posições
    # ══════════════════════════════════════════════════════════════════════════
    secao_titulo("Maiores Posições", "💼", "Top 10 por valor investido")

    posicoes = carteira.get("posicoes", [])
    if posicoes:
        top10 = posicoes[:10]
        df = pd.DataFrame([
            {
                "Ticker":    p["ticker"],
                "Nome":      p["nome"][:30],
                "Classe":    p["classe"],
                "Qtd.":      p["quantidade"],
                "PM (R$)":   p["preco_medio"],
                "Investido": p["total_investido"],
                "Mercado":   p["valor_mercado"],
                "Rentab.%":  p["rentab_pct"],
                "% Cart.":   p["pct_carteira"],
            }
            for p in top10
        ])

        st.dataframe(
            df,
            column_config={
                "Ticker":    st.column_config.TextColumn("Ticker",    width="small"),
                "Nome":      st.column_config.TextColumn("Nome"),
                "Classe":    st.column_config.TextColumn("Classe",    width="small"),
                "Qtd.":      st.column_config.NumberColumn("Qtd.",    format="%.2f"),
                "PM (R$)":   st.column_config.NumberColumn("PM",      format="R$ %.2f"),
                "Investido": st.column_config.NumberColumn("Investido", format="R$ %.2f"),
                "Mercado":   st.column_config.NumberColumn("Mercado",   format="R$ %.2f"),
                "Rentab.%":  st.column_config.NumberColumn("Rent.%",   format="%.2f%%"),
                "% Cart.":   st.column_config.NumberColumn("% Cart.",  format="%.1f%%"),
            },
            hide_index=True,
            use_container_width=True,
            height=min(40 + len(top10) * 36, 420),
        )
        st.caption(
            f"Mostrando {len(top10)} de {len(posicoes)} posições · "
            "Acesse **Carteira** no menu para a visão completa com filtros."
        )
    else:
        st.info(
            "Nenhuma posição encontrada. Verifique se o ETL de posições foi executado "
            "(`scripts/08_compute_portfolio_positions.py`).",
            icon="💼",
        )

    st.divider()

    # ── Links rápidos ─────────────────────────────────────────────────────────
    col_info1, col_info2 = st.columns(2)
    with col_info1:
        st.info(
            "**💼 Carteira** — Visão detalhada por ativo com filtros, "
            "gráficos de donut por classe/setor e tabela completa.",
            icon="💼",
        )
    with col_info2:
        st.info(
            "**💵 Proventos** — Histórico de dividendos, JCP e rendimentos FII "
            f"· {proventos['num_eventos']} eventos de {proventos['num_ativos']} ativos.",
            icon="💵",
        )
