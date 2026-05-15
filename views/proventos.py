"""
pages/proventos.py
Histórico de dividendos, JCP e rendimentos de FII.

Seções:
  1. KPIs     — total do mês, total do ano, nº de ativos, total histórico
  2. Gráfico  — barras mensais de proventos recebidos
  3. Resumo   — por ativo (top N) e por tipo de provento
  4. Tabela   — todos os eventos com filtro por ticker / tipo / período

Origem: Tiago84Barros/Dashboard-Investimentos (App 2)
Dados:  core/proventos.get_proventos() — mock → real (Fase 5.2)
"""
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from core.proventos import get_proventos
from core.utils import fmt_moeda, fmt_percentual
from design.componentes import (
    badge_status,
    card_metrica,
    container_pagina,
    indicador_linha,
    secao_titulo,
)


# ── Gráfico de barras mensais ─────────────────────────────────────────────────

def _fig_historico(historico: list) -> go.Figure:
    labels = [h["label"] for h in historico]
    values = [h["total"] for h in historico]

    cores = ["#9B59B6" if i < len(historico) - 1 else "#00C896" for i in range(len(historico))]

    fig = go.Figure(go.Bar(
        x=labels,
        y=values,
        marker_color=cores,
        text=[f"R$ {v:,.0f}" for v in values],
        textposition="outside",
        textfont={"size": 10, "color": "#CBD5E0"},
        hovertemplate="<b>%{x}</b><br>R$ %{y:,.2f}<extra></extra>",
    ))
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font_color="#9CA3AF",
        margin={"t": 28, "b": 8, "l": 0, "r": 0},
        yaxis={
            "gridcolor": "#1E2533",
            "tickformat": ",.0f",
            "tickprefix": "R$ ",
            "showgrid": True,
        },
        xaxis={"showgrid": False},
        height=280,
        showlegend=False,
    )
    return fig


# ── Render principal ──────────────────────────────────────────────────────────

def render() -> None:
    d          = get_proventos()
    eventos    = d["eventos"]
    hist       = d["historico_mensal"]
    por_ativo  = d["por_ativo"]
    por_tipo   = d["por_tipo"]

    # ── Cabeçalho ─────────────────────────────────────────────────────────────
    container_pagina(
        "Proventos",
        f"Dividendos, JCP e rendimentos de FII · {d['num_eventos']} eventos · {d['num_ativos']} ativos",
        "💵",
    )

    # ── Badges ────────────────────────────────────────────────────────────────
    _fonte = d.get("data_source", "mock")
    _badge_label, _badge_tipo = (
        ("Dados reais",     "sucesso") if _fonte == "real" else
        ("Fallback (mock)", "erro")    if _fonte == "mock_fallback" else
        ("Modo mock",       "alerta")
    )

    col_b1, col_b2, col_b3, *_ = st.columns([1, 1, 1, 4])
    with col_b1:
        badge_status(_badge_label, _badge_tipo)
    with col_b2:
        badge_status(f"{d['num_ativos']} ativos", "info")
    with col_b3:
        badge_status(f"{d['num_eventos']} eventos", "neutro")

    if d["num_eventos"] == 0:
        st.info(
            "**Nenhum provento registrado.** A tabela `dividends` está vazia "
            "para este usuário. Importe os dados via App 2 ou script.",
            icon="💵",
        )
        return

    st.markdown("<br>", unsafe_allow_html=True)

    # ══════════════════════════════════════════════════════════════════════════
    # SEÇÃO 1 — KPIs
    # ══════════════════════════════════════════════════════════════════════════
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        card_metrica(
            "Proventos do Mês",
            fmt_moeda(d["total_mes"]),
            positivo=True if d["total_mes"] > 0 else None,
            ajuda="Total de proventos com payment_date no mês corrente.",
        )
    with c2:
        card_metrica(
            "Proventos do Ano",
            fmt_moeda(d["total_ano"]),
            positivo=True if d["total_ano"] > 0 else None,
            ajuda="Total de proventos com payment_date no ano corrente.",
        )
    with c3:
        card_metrica(
            "Total Histórico",
            fmt_moeda(d["total_historico"]),
            ajuda="Soma de todos os proventos registrados na base.",
        )
    with c4:
        card_metrica(
            "Ativos c/ Proventos",
            str(d["num_ativos"]),
            ajuda="Número de ativos distintos com ao menos um provento registrado.",
        )

    st.divider()

    # ══════════════════════════════════════════════════════════════════════════
    # SEÇÃO 2 — Gráfico histórico mensal
    # ══════════════════════════════════════════════════════════════════════════
    secao_titulo(
        "Histórico Mensal",
        "📊",
        "Proventos recebidos por mês (barra mais clara = mês atual)",
    )
    if hist:
        st.plotly_chart(_fig_historico(hist), use_container_width=True)
    else:
        st.caption("Sem dados históricos suficientes para o gráfico.")

    st.divider()

    # ══════════════════════════════════════════════════════════════════════════
    # SEÇÃO 3 — Por ativo e por tipo
    # ══════════════════════════════════════════════════════════════════════════
    col_ativo, col_tipo = st.columns(2)

    with col_ativo:
        secao_titulo("Por Ativo", "💼", "Ordenado por total recebido")
        for a in por_ativo[:10]:
            indicador_linha(
                f"{a['ticker']} · {a['nome'][:28]}",
                fmt_moeda(a["total"]),
                cor_valor="#E2E8F0",
                badge=f"{a['pct']:.1f}%",
                tipo_badge="alerta" if a["pct"] > 30 else "neutro",
            )
            st.markdown(
                f'<div style="text-align:right;font-size:0.73rem;color:#4A5568;'
                f'margin-top:-4px;margin-bottom:4px">'
                f'{a["classe"]} · {a["num_eventos"]} evento{"s" if a["num_eventos"] != 1 else ""}'
                f'</div>',
                unsafe_allow_html=True,
            )

    with col_tipo:
        secao_titulo("Por Tipo de Provento", "🏷️")
        _CORES_TIPO = {
            "reit_income":  "#9B59B6",
            "dividend":     "#00C896",
            "jcp":          "#F6C90E",
            "amortization": "#4A9EFF",
            "other":        "#718096",
        }
        for t in por_tipo:
            cor = _CORES_TIPO.get(t["tipo"], "#718096")
            indicador_linha(
                t["label"],
                fmt_moeda(t["total"]),
                cor_valor=cor,
                badge=f"{t['pct']:.1f}%",
                tipo_badge="neutro",
            )
            st.markdown(
                f'<div style="text-align:right;font-size:0.73rem;color:#4A5568;'
                f'margin-top:-4px;margin-bottom:4px">'
                f'{t["num_eventos"]} evento{"s" if t["num_eventos"] != 1 else ""}'
                f'</div>',
                unsafe_allow_html=True,
            )

    st.divider()

    # ══════════════════════════════════════════════════════════════════════════
    # SEÇÃO 4 — Tabela de eventos
    # ══════════════════════════════════════════════════════════════════════════
    secao_titulo("Todos os Eventos", "📋", "Filtros de busca abaixo")

    # Filtros
    col_f1, col_f2, col_f3 = st.columns(3)
    tickers_disp = sorted({e["ticker"] for e in eventos})
    tipos_disp   = sorted({e["label_tipo"] for e in eventos})

    with col_f1:
        f_ticker = st.multiselect(
            "Ticker", tickers_disp, default=[], placeholder="Todos",
            key="prov_ticker", label_visibility="collapsed",
        )
    with col_f2:
        f_tipo = st.multiselect(
            "Tipo", tipos_disp, default=[], placeholder="Todos os tipos",
            key="prov_tipo", label_visibility="collapsed",
        )
    with col_f3:
        anos_disp = sorted({e["payment_date"].year for e in eventos if e["payment_date"]}, reverse=True)
        f_ano = st.selectbox(
            "Ano", ["Todos"] + [str(a) for a in anos_disp],
            key="prov_ano", label_visibility="collapsed",
        )

    # Aplicar filtros
    ev_filtrados = eventos
    if f_ticker:
        ev_filtrados = [e for e in ev_filtrados if e["ticker"] in f_ticker]
    if f_tipo:
        ev_filtrados = [e for e in ev_filtrados if e["label_tipo"] in f_tipo]
    if f_ano != "Todos":
        ev_filtrados = [e for e in ev_filtrados if e["payment_date"] and e["payment_date"].year == int(f_ano)]

    if not ev_filtrados:
        st.caption("Nenhum evento com os filtros selecionados.")
        return

    df = pd.DataFrame([
        {
            "Data Pgto":       e["payment_date"].strftime("%d/%m/%Y") if e["payment_date"] else "—",
            "Ticker":          e["ticker"],
            "Nome":            e["nome"],
            "Tipo":            e["label_tipo"],
            "Classe":          e["classe"],
            "Qtd.":            e["quantity"],
            "R$/Unit":         e["amount_per_unit"],
            "Total (R$)":      e["total_amount"],
        }
        for e in ev_filtrados
    ])

    st.dataframe(
        df,
        column_config={
            "Data Pgto":  st.column_config.TextColumn("Data Pgto",  width="small"),
            "Ticker":     st.column_config.TextColumn("Ticker",     width="small"),
            "Nome":       st.column_config.TextColumn("Nome"),
            "Tipo":       st.column_config.TextColumn("Tipo",       width="small"),
            "Classe":     st.column_config.TextColumn("Classe",     width="small"),
            "Qtd.":       st.column_config.NumberColumn("Qtd.",     format="%.2f",   width="small"),
            "R$/Unit":    st.column_config.NumberColumn("R$/Unit",  format="R$ %.4f"),
            "Total (R$)": st.column_config.NumberColumn("Total",    format="R$ %.2f"),
        },
        hide_index=True,
        use_container_width=True,
        height=min(40 + len(ev_filtrados) * 36, 480),
    )

    total_filtrado = sum(e["total_amount"] for e in ev_filtrados)
    st.caption(
        f"{len(ev_filtrados)} evento{'s' if len(ev_filtrados) != 1 else ''} · "
        f"Total: {fmt_moeda(total_filtrado)}"
    )
