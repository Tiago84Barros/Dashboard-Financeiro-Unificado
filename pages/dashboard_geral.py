"""
pages/dashboard_geral.py
Visão Geral consolidada das finanças pessoais.

Seções:
  1. KPIs linha 1  — patrimônio total, saldo, receitas, despesas
  2. KPIs linha 2  — patrimônio investido, rentabilidade, economia, taxa de poupança
  3. Fluxo de caixa (gráfico barras) + Resumo mensal
  4. Resumo de investimentos (donut + tabela de classes)
  5. Alertas principais
  6. Próximos passos financeiros

Origem: Tiago84Barros/Dashboard
Dados:  core/financeiro.get_visao_geral() — mock (Fase 3) → real (Fase 4)
"""
import streamlit as st
import plotly.graph_objects as go

from core.financeiro import get_visao_geral
from core.utils import fmt_moeda, fmt_percentual, delta_str
from design.componentes import (
    badge_status,
    barra_progresso,
    card_alerta_resumo,
    card_metrica,
    card_proximo_passo,
    container_pagina,
    indicador_linha,
    score_saude,
    secao_titulo,
)


# ── Helpers de gráfico ────────────────────────────────────────────────────────

def _fig_fluxo(historico: list) -> go.Figure:
    """Gráfico de barras agrupadas: receitas vs despesas por mês."""
    meses    = [h["mes"] for h in historico]
    receitas = [h["receitas"] for h in historico]
    despesas = [h["despesas"] for h in historico]

    fig = go.Figure()
    fig.add_trace(go.Bar(
        name="Receitas", x=meses, y=receitas,
        marker_color="#00C896",
        text=[f"R$ {v/1000:.1f}K" for v in receitas],
        textposition="outside",
        textfont={"size": 10, "color": "#CBD5E0"},
    ))
    fig.add_trace(go.Bar(
        name="Despesas", x=meses, y=despesas,
        marker_color="#FC5C7D",
        text=[f"R$ {v/1000:.1f}K" for v in despesas],
        textposition="outside",
        textfont={"size": 10, "color": "#CBD5E0"},
    ))
    fig.update_layout(
        barmode="group",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font_color="#9CA3AF",
        legend={"orientation": "h", "y": -0.22, "font": {"size": 12}},
        margin={"t": 24, "b": 8, "l": 0, "r": 0},
        yaxis={"gridcolor": "#1E2533", "tickformat": ",.0f", "tickprefix": "R$ "},
        xaxis={"showgrid": False},
        height=300,
    )
    return fig


def _fig_classes(classes: list) -> go.Figure:
    """Donut chart com distribuição das classes de ativos."""
    nomes = [c["nome"] for c in classes]
    vals  = [c["valor"] for c in classes]
    cores = [c["cor"] for c in classes]

    fig = go.Figure(go.Pie(
        labels=nomes,
        values=vals,
        hole=0.55,
        marker={"colors": cores, "line": {"color": "#0E1117", "width": 2}},
        textinfo="percent",
        textfont={"size": 11},
        hovertemplate="<b>%{label}</b><br>R$ %{value:,.2f}<br>%{percent}<extra></extra>",
    ))
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        font_color="#9CA3AF",
        showlegend=True,
        legend={
            "orientation": "v",
            "x": 1.02, "y": 0.5,
            "font": {"size": 11},
        },
        margin={"t": 10, "b": 10, "l": 0, "r": 0},
        height=240,
    )
    return fig


# ── Render principal ──────────────────────────────────────────────────────────

def render() -> None:
    # Busca dados (mock ou real, controlado por MOCK_MODE)
    try:
        d = get_visao_geral()
    except NotImplementedError as exc:
        st.error(
            f"**Banco de dados não configurado.** {exc}\n\n"
            "Configure `SUPABASE_UNIFICADO_URL` no `.env` local ou em "
            "**Streamlit Secrets** (Settings > Secrets)."
        )
        return

    pat  = d["patrimonio"]
    flx  = d["fluxo_mes"]
    hist = d["historico_mensal"]
    cats = d["categorias_despesa"]
    port = d["portfolio"]
    cls  = d["classes_ativo"]

    # ── Cabeçalho ─────────────────────────────────────────────────────────────
    container_pagina(
        "Dashboard Geral",
        f"Visão consolidada · {d['mes_referencia']}",
        "📊",
    )

    # ── Indicador de fonte de dados ───────────────────────────────────────
    _fonte = d.get("data_source", "mock")
    if _fonte == "real":
        _badge_label = "Dados reais"
        _badge_tipo  = "sucesso"
    elif _fonte == "mock_fallback":
        _badge_label = "Fallback (mock)"
        _badge_tipo  = "erro"
    else:
        _badge_label = "Modo mock"
        _badge_tipo  = "alerta"

    col_b1, col_b2, col_b3, *_ = st.columns([1, 1, 1, 4])
    with col_b1:
        badge_status(_badge_label, _badge_tipo)
    with col_b2:
        badge_status(d["mes_referencia"], "info")
    with col_b3:
        badge_status(f"Score {pat['saude_score']}/100", "sucesso")
    st.markdown("<br>", unsafe_allow_html=True)

    # ══════════════════════════════════════════════════════════════════════════
    # SEÇÃO 1 — KPIs principais (linha 1)
    # ══════════════════════════════════════════════════════════════════════════
    c1, c2, c3, c4 = st.columns(4)

    delta_rec, pos_rec = delta_str(flx["receitas"], flx["receitas_mes_anterior"])
    delta_desp, pos_desp = delta_str(flx["despesas"], flx["despesas_mes_anterior"])

    with c1:
        card_metrica(
            "Patrimônio Total",
            fmt_moeda(pat["total"]),
            delta=fmt_percentual(pat["delta_mes_pct"]),
            positivo=True,
            ajuda="Investimentos + saldo bancário. Variação vs mês anterior.",
        )
    with c2:
        card_metrica(
            "Saldo Disponível",
            fmt_moeda(pat["saldo_bancario"]),
            ajuda="Total em contas correntes e poupança",
        )
    with c3:
        card_metrica(
            "Receitas do Mês",
            fmt_moeda(flx["receitas"]),
            delta=delta_rec,
            positivo=pos_rec,
            ajuda="Total de entradas no mês corrente",
        )
    with c4:
        card_metrica(
            "Despesas do Mês",
            fmt_moeda(flx["despesas"]),
            delta=delta_desp,
            positivo=not pos_desp if pos_desp is not None else None,
            ajuda="Total de saídas no mês corrente",
        )

    st.markdown("<br>", unsafe_allow_html=True)

    # ══════════════════════════════════════════════════════════════════════════
    # SEÇÃO 2 — KPIs secundários (linha 2)
    # ══════════════════════════════════════════════════════════════════════════
    c5, c6, c7, c8 = st.columns(4)

    delta_rentab, pos_rentab = delta_str(
        port["rentabilidade_mes_pct"],
        port["rentabilidade_mes_anterior_pct"],
    )
    delta_econ, pos_econ = delta_str(flx["economia"], flx["economia_mes_anterior"])

    with c5:
        card_metrica(
            "Patrimônio Investido",
            fmt_moeda(pat["investido"]),
            delta=fmt_percentual(port["rentabilidade_ano_pct"]),
            positivo=True,
            ajuda="Valor atual de todos os ativos. Delta = rentabilidade no ano.",
        )
    with c6:
        card_metrica(
            "Rentabilidade Mês",
            fmt_percentual(port["rentabilidade_mes_pct"]),
            delta=delta_rentab,
            positivo=pos_rentab,
            ajuda="Retorno da carteira no mês vs mês anterior",
        )
    with c7:
        card_metrica(
            "Economia do Mês",
            fmt_moeda(flx["economia"]),
            delta=delta_econ,
            positivo=pos_econ,
            ajuda="Receitas − Despesas do mês corrente",
        )
    with c8:
        card_metrica(
            "Taxa de Poupança",
            fmt_percentual(flx["taxa_poupanca_pct"], sinal=False),
            delta=fmt_percentual(
                flx["taxa_poupanca_pct"] - flx["taxa_poupanca_anterior"], casas=1
            ),
            positivo=flx["taxa_poupanca_pct"] >= flx["taxa_poupanca_anterior"],
            ajuda="(Receitas − Despesas) / Receitas × 100. Meta: ≥ 30%.",
        )

    st.divider()

    # ══════════════════════════════════════════════════════════════════════════
    # SEÇÃO 3 — Fluxo de caixa + Resumo mensal
    # ══════════════════════════════════════════════════════════════════════════
    col_graf, col_resumo = st.columns([3, 1])

    with col_graf:
        secao_titulo("Fluxo de Caixa", "💹", "Receitas vs Despesas — últimos 6 meses")
        st.plotly_chart(_fig_fluxo(hist), use_container_width=True)

    with col_resumo:
        secao_titulo("Resumo do Mês", "📋")
        st.metric(
            "Meses de Reserva",
            f"{flx['meses_reserva']:.1f}×",
            help=f"Meta: {flx['meta_meses_reserva']:.0f}× as despesas mensais",
        )
        st.markdown("<br>", unsafe_allow_html=True)
        score_saude(pat["saude_score"])
        st.markdown("<br>", unsafe_allow_html=True)
        st.markdown(
            f'<div style="font-size:0.78rem;color:#718096;margin-top:4px">'
            f'💸 Maior gasto: <b style="color:#E2E8F0">{flx["maior_categoria"]}</b>'
            f' · {fmt_moeda(flx["maior_categoria_valor"])}</div>',
            unsafe_allow_html=True,
        )
        st.markdown(
            f'<div style="font-size:0.78rem;color:#F6C90E;margin-top:4px">'
            f'⚠️ Em alerta: <b>{flx["categoria_alerta"]}</b>'
            f' ({flx["categoria_alerta_pct"]:.0f}% do limite)</div>',
            unsafe_allow_html=True,
        )

    st.divider()

    # ══════════════════════════════════════════════════════════════════════════
    # SEÇÃO 4 — Investimentos + Orçamento por categoria
    # ══════════════════════════════════════════════════════════════════════════
    col_inv, col_orc = st.columns(2)

    with col_inv:
        secao_titulo(
            "Resumo de Investimentos",
            "📈",
            f"{port['num_ativos']} ativos · dividendos: {fmt_moeda(port['dividendos_mes'])} no mês",
        )

        # Donut de classes
        st.plotly_chart(_fig_classes(cls), use_container_width=True)

        # Rentabilidade por classe
        st.markdown(
            '<div style="font-size:0.75rem;font-weight:700;text-transform:uppercase;'
            'letter-spacing:0.06em;color:#4A5568;margin-bottom:4px">Rentabilidade por Classe</div>',
            unsafe_allow_html=True,
        )
        for classe in cls:
            cor = "#00C896" if classe["rentab_mes_pct"] >= 0 else "#FC5C7D"
            indicador_linha(
                classe["nome"],
                fmt_percentual(classe["rentab_mes_pct"]),
                cor_valor=cor,
                badge=f'{classe["pct_carteira"]:.1f}%',
                tipo_badge="alerta" if classe["pct_carteira"] > 25 else "neutro",
            )

    with col_orc:
        secao_titulo(
            "Orçamento por Categoria",
            "📂",
            f"Maior gasto: {flx['maior_categoria']} · Alerta: {flx['categoria_alerta']}",
        )
        for cat in cats:
            barra_progresso(
                cat["nome"],
                cat["gasto"],
                cat["orcamento"],
                fmt_valor=fmt_moeda(cat["gasto"]),
                fmt_total=fmt_moeda(cat["orcamento"]),
            )

    st.divider()

    # ══════════════════════════════════════════════════════════════════════════
    # SEÇÃO 5 — Alertas + SEÇÃO 6 — Próximos passos
    # ══════════════════════════════════════════════════════════════════════════
    col_alertas, col_passos = st.columns(2)

    with col_alertas:
        secao_titulo("Alertas Principais", "🔔", "Acompanhe os pontos de atenção")
        for alerta in d["alertas"]:
            card_alerta_resumo(
                tipo=alerta["tipo"],
                icone=alerta["icone"],
                titulo=alerta["titulo"],
                descricao=alerta["descricao"],
                modulo=alerta["modulo"],
            )

    with col_passos:
        secao_titulo("Próximos Passos", "🎯", "Ações recomendadas para este mês")
        for passo in d["proximos_passos"]:
            card_proximo_passo(
                numero=passo["numero"],
                titulo=passo["titulo"],
                descricao=passo["descricao"],
                urgencia=passo["urgencia"],
                modulo=passo["modulo"],
            )
