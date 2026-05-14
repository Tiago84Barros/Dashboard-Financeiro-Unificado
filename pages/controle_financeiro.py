"""
pages/controle_financeiro.py  — v2 (layout original replicado)

Layout replicado do app controlefinanceirotsb.streamlit.app:
  Sidebar    — formulário de lançamento (Forma pgto / Categoria / Data /
               Valor / Parcelas+Cartão / Descrição / Salvar)
  Main area  — cabeçalho + badge data + seletor mês
               4 cards CSS (Renda · Despesas · Saldo Líquido · Comprometimento)
               Gráfico horizontal categorias | Gráfico histórico 6 meses
               Tabela de lançamentos

Dados: core/controle.get_controle() + core/investimentos.get_cashflow_mensal()
"""
from datetime import date as _date

import plotly.graph_objects as go
import streamlit as st

from core.controle import get_controle, get_opcoes_formulario, inserir_transacao
from core.investimentos import get_cashflow_mensal
from core.utils import fmt_moeda, fmt_percentual
from design.componentes import badge_status, container_pagina

# ── Paleta ────────────────────────────────────────────────────────────────────
_COR_RECEITA  = "#00C896"
_COR_DESPESA  = "#FC5C7D"
_COR_SALDO    = "#4A9EFF"
_COR_NEUTRO   = "#9CA3AF"

_MESES_PT_CURTO = {
    1: "Jan", 2: "Fev", 3: "Mar", 4: "Abr",
    5: "Mai", 6: "Jun", 7: "Jul", 8: "Ago",
    9: "Set", 10: "Out", 11: "Nov", 12: "Dez",
}


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS — Cards CSS
# ══════════════════════════════════════════════════════════════════════════════

def _kpi_card(titulo: str, valor: str, descricao: str, cor_valor: str) -> str:
    """Retorna HTML de um card KPI (sem comentários HTML)."""
    return f"""
<div style="background:#12151E;border:1px solid #1E2533;border-radius:10px;
            padding:20px 18px 16px;height:100%;">
    <div style="font-size:0.62rem;font-weight:800;text-transform:uppercase;
                letter-spacing:0.14em;color:#718096;margin-bottom:10px;">
        {titulo}
    </div>
    <div style="font-size:1.70rem;font-weight:800;color:{cor_valor};
                letter-spacing:-0.02em;line-height:1;margin-bottom:8px;">
        {valor}
    </div>
    <div style="font-size:0.73rem;color:#4A5568;line-height:1.35;">
        {descricao}
    </div>
</div>"""


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS — Gráficos
# ══════════════════════════════════════════════════════════════════════════════

def _fig_categorias(cats: list) -> go.Figure:
    """Gráfico de barras horizontal — gastos por categoria."""
    nomes  = [c["nome"]  for c in cats]
    gastos = [c["gasto"] for c in cats]

    fig = go.Figure(go.Bar(
        x=gastos,
        y=nomes,
        orientation="h",
        marker_color=_COR_DESPESA,
        opacity=0.85,
        hovertemplate="<b>%{y}</b><br>R$ %{x:,.2f}<extra></extra>",
    ))
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font_color="#9CA3AF",
        margin={"t": 16, "b": 0, "l": 0, "r": 16},
        height=320,
        xaxis={
            "showgrid":   True,
            "gridcolor":  "#1E2533",
            "tickformat": ",.0f",
            "tickprefix": "R$ ",
        },
        yaxis={"showgrid": False, "autorange": "reversed"},
    )
    return fig


def _fig_historico(historico: list) -> go.Figure:
    """Gráfico de linhas — histórico 6 meses (Receitas × Despesas × Saldo)."""
    h6 = historico[-6:] if len(historico) >= 6 else historico
    meses    = [h["label"]    for h in h6]
    receitas = [h["receitas"] for h in h6]
    despesas = [h["despesas"] for h in h6]
    saldos   = [h["saldo"]    for h in h6]

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        name="Receitas",
        x=meses, y=receitas,
        mode="lines+markers",
        line={"color": _COR_RECEITA, "width": 2.5},
        marker={"size": 7},
        hovertemplate="<b>Receitas %{x}</b><br>R$ %{y:,.2f}<extra></extra>",
    ))
    fig.add_trace(go.Scatter(
        name="Despesas",
        x=meses, y=despesas,
        mode="lines+markers",
        line={"color": _COR_DESPESA, "width": 2.5},
        marker={"size": 7},
        hovertemplate="<b>Despesas %{x}</b><br>R$ %{y:,.2f}<extra></extra>",
    ))
    fig.add_trace(go.Scatter(
        name="Saldo",
        x=meses, y=saldos,
        mode="lines+markers",
        line={"color": _COR_SALDO, "width": 2, "dash": "dot"},
        marker={"size": 6},
        hovertemplate="<b>Saldo %{x}</b><br>R$ %{y:,.2f}<extra></extra>",
    ))
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font_color="#9CA3AF",
        legend={
            "orientation": "h", "y": -0.20,
            "font": {"size": 11},
            "bgcolor": "rgba(0,0,0,0)",
        },
        margin={"t": 16, "b": 10, "l": 0, "r": 0},
        height=320,
        yaxis={
            "showgrid":   True,
            "gridcolor":  "#1E2533",
            "tickformat": ",.0f",
            "tickprefix": "R$ ",
        },
        xaxis={"showgrid": False},
    )
    return fig


# ══════════════════════════════════════════════════════════════════════════════
# SIDEBAR — Formulário de lançamento
# ══════════════════════════════════════════════════════════════════════════════

def _sidebar_form(ano: int, mes: int) -> None:
    """Formulário lateral replicando o app original."""
    st.sidebar.markdown(
        '<div style="font-size:0.68rem;font-weight:800;text-transform:uppercase;'
        'letter-spacing:0.12em;color:#4A9EFF;margin-bottom:12px;margin-top:8px;">'
        'Novo Lançamento</div>',
        unsafe_allow_html=True,
    )

    opcoes = get_opcoes_formulario()
    cats   = opcoes.get("categorias", [])
    contas = opcoes.get("contas", [])

    # Tipo (Receita / Despesa) — determina cor e filtra categorias
    tipo = st.sidebar.selectbox(
        "Forma de pagamento",
        ["expense", "income"],
        format_func=lambda x: "Despesa" if x == "expense" else "Receita",
        key="cf_sb_tipo",
    )

    cats_tipo  = [c for c in cats if c["tipo"] == tipo or c["tipo"] == "transfer"]
    cat_nomes  = [c["nome"] for c in cats_tipo] or ["(sem categoria)"]
    cat_ids    = [c["id"]   for c in cats_tipo] or [None]

    cat_idx = st.sidebar.selectbox(
        "Categoria",
        range(len(cat_nomes)),
        format_func=lambda i: cat_nomes[i],
        key="cf_sb_cat",
    )

    data_tx = st.sidebar.date_input(
        "Data",
        value=_date(ano, mes, min(_date.today().day if ano == _date.today().year
                                  and mes == _date.today().month else 1, 28)),
        key="cf_sb_data",
    )

    valor = st.sidebar.number_input(
        "Valor (R$)",
        min_value=0.0,
        step=0.01,
        format="%.2f",
        key="cf_sb_valor",
    )

    col_parc, col_cart = st.sidebar.columns(2)
    with col_parc:
        st.number_input("Parcelas", min_value=1, max_value=48, value=1, key="cf_sb_parc")
    with col_cart:
        st.text_input("Cartão", placeholder="Final", key="cf_sb_cart")

    descricao = st.sidebar.text_area(
        "Descrição (opcional)",
        height=80,
        key="cf_sb_desc",
    )

    st.sidebar.markdown("<br>", unsafe_allow_html=True)

    if st.sidebar.button("Salvar lançamento", use_container_width=True, type="primary"):
        if valor <= 0:
            st.sidebar.error("Informe um valor maior que zero.")
            return

        cat_id   = cat_ids[cat_idx] if cat_ids[cat_idx] else None
        conta_id = contas[0]["id"] if contas else None

        if not conta_id:
            st.sidebar.warning("Nenhuma conta configurada.")
            return

        desc_final = descricao.strip() if descricao.strip() else cat_nomes[cat_idx]

        ok, msg = inserir_transacao(
            descricao=desc_final,
            valor=valor,
            tipo=tipo,
            data=data_tx,
            categoria_id=cat_id,
            conta_id=conta_id,
        )
        if ok:
            st.sidebar.success("✅ Lançamento salvo!")
            st.rerun()
        else:
            st.sidebar.error(f"Erro: {msg}")


# ══════════════════════════════════════════════════════════════════════════════
# RENDER PRINCIPAL
# ══════════════════════════════════════════════════════════════════════════════

def _opcoes_mes(n: int = 12) -> list:
    hoje = _date.today()
    result = []
    for i in range(n):
        m = hoje.month - i
        y = hoje.year
        while m <= 0:
            m += 12
            y -= 1
        result.append({"label": f"{_MESES_PT_CURTO[m]}/{y}", "ano": y, "mes": m})
    return result


def render() -> None:
    # ── Seletor de mês (antes de qualquer dado) ───────────────────────────────
    opcoes     = _opcoes_mes(12)
    labels_mes = [o["label"] for o in opcoes]

    # ── Header ────────────────────────────────────────────────────────────────
    col_title, col_date = st.columns([3, 1])
    with col_title:
        st.markdown(
            '<h1 style="font-size:2rem;font-weight:800;color:#E2E8F0;margin:0;">💰 Controle Financeiro</h1>',
            unsafe_allow_html=True,
        )
    with col_date:
        idx = st.selectbox(
            "Mês",
            range(len(labels_mes)),
            format_func=lambda i: labels_mes[i],
            key="cf_mes_idx",
            label_visibility="collapsed",
        )

    sel = opcoes[idx]
    d   = get_controle(sel["ano"], sel["mes"])

    # Subtítulo dinâmico
    mes_num = sel["mes"]
    ano_num = sel["ano"]
    st.markdown(
        f'<p style="color:#718096;font-size:0.90rem;margin-top:2px;margin-bottom:0;">'
        f'Visão geral de <b style="color:#E2E8F0">{mes_num:02d}/{ano_num}</b> '
        f'&nbsp;•&nbsp; acompanhe renda, despesas e saldo em tempo real</p>',
        unsafe_allow_html=True,
    )

    # Badge data atual
    _fonte = d.get("data_source", "mock")
    col_b1, col_b2, *_ = st.columns([1, 1, 6])
    with col_b1:
        badge_status(
            "Dados reais" if _fonte == "real" else
            "Fallback (mock)" if _fonte == "mock_fallback" else "Modo mock",
            "sucesso" if _fonte == "real" else
            "erro"    if _fonte == "mock_fallback" else "alerta",
        )
    with col_b2:
        badge_status(f"Mês atual: {_date.today().strftime('%d/%m/%Y')}", "neutro")

    st.markdown("<br>", unsafe_allow_html=True)

    # ── Sidebar form ─────────────────────────────────────────────────────────
    _sidebar_form(sel["ano"], sel["mes"])

    # ══════════════════════════════════════════════════════════════════════════
    # BLOCO 1 — 4 Cards KPI
    # ══════════════════════════════════════════════════════════════════════════
    receitas    = d["receitas"]
    despesas    = d["despesas"]
    saldo       = d["saldo_mes"]
    comprometido = round(despesas / receitas * 100, 1) if receitas > 0 else 0.0
    cor_saldo   = _COR_RECEITA if saldo >= 0 else _COR_DESPESA

    c1, c2, c3, c4 = st.columns(4, gap="small")
    with c1:
        st.markdown(_kpi_card(
            "Renda do Mês",
            fmt_moeda(receitas),
            "Somatório de todas as entradas no período selecionado.",
            _COR_RECEITA,
        ), unsafe_allow_html=True)
    with c2:
        st.markdown(_kpi_card(
            "Despesas do Mês",
            fmt_moeda(despesas),
            "Somatório de todas as saídas no período.",
            _COR_DESPESA,
        ), unsafe_allow_html=True)
    with c3:
        st.markdown(_kpi_card(
            "Saldo Líquido do Mês",
            fmt_moeda(saldo),
            f"{'Sobrou' if saldo >= 0 else 'Déficit'} dinheiro este mês. "
            f"Transações: {d['num_transacoes']}",
            cor_saldo,
        ), unsafe_allow_html=True)
    with c4:
        cor_comp = (
            _COR_RECEITA if comprometido < 60 else
            "#F6C90E"    if comprometido < 80 else
            _COR_DESPESA
        )
        st.markdown(_kpi_card(
            "Renda Comprometida",
            fmt_percentual(comprometido, sinal=False),
            "Despesas em relação à renda do mês.",
            cor_comp,
        ), unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # ══════════════════════════════════════════════════════════════════════════
    # BLOCO 2 — Gráficos
    # ══════════════════════════════════════════════════════════════════════════
    col_cat, col_hist = st.columns(2, gap="medium")

    with col_cat:
        st.markdown(
            '<div style="font-size:0.95rem;font-weight:700;color:#E2E8F0;margin-bottom:10px;">'
            'Gastos por categoria (mês)</div>',
            unsafe_allow_html=True,
        )
        cats = d["categorias"]
        if cats:
            st.plotly_chart(
                _fig_categorias(cats),
                use_container_width=True,
                config={"displayModeBar": False},
            )
        else:
            st.caption("Sem despesas registradas neste mês.")

    with col_hist:
        st.markdown(
            '<div style="font-size:0.95rem;font-weight:700;color:#E2E8F0;margin-bottom:10px;">'
            'Histórico de 6 meses (Receitas x Despesas x Saldo)</div>',
            unsafe_allow_html=True,
        )
        historico = get_cashflow_mensal()
        if historico:
            st.plotly_chart(
                _fig_historico(historico),
                use_container_width=True,
                config={"displayModeBar": False},
            )
        else:
            st.caption("Histórico não disponível.")

    st.markdown("<br>", unsafe_allow_html=True)

    # ══════════════════════════════════════════════════════════════════════════
    # BLOCO 3 — Tabela de lançamentos
    # ══════════════════════════════════════════════════════════════════════════
    st.markdown(
        '<div style="font-size:0.95rem;font-weight:700;color:#E2E8F0;margin-bottom:10px;">'
        f'Lançamentos — {d["mes_referencia"]} '
        f'<span style="font-size:0.78rem;font-weight:400;color:#4A5568;">'
        f'({d["num_transacoes"]} registro{"s" if d["num_transacoes"] != 1 else ""})</span>'
        '</div>',
        unsafe_allow_html=True,
    )

    txs = d["transacoes"]

    # Filtro rápido
    col_f1, col_f2, *_ = st.columns([1, 1, 5])
    with col_f1:
        f_tipo = st.selectbox(
            "Tipo",
            ["Todos", "Receitas", "Despesas"],
            key="cf_ftipo",
            label_visibility="collapsed",
        )
    with col_f2:
        f_busca = st.text_input(
            "Buscar",
            placeholder="Filtrar descrição...",
            key="cf_busca",
            label_visibility="collapsed",
        )

    if f_tipo == "Receitas":
        txs = [t for t in txs if t["eh_receita"]]
    elif f_tipo == "Despesas":
        txs = [t for t in txs if not t["eh_receita"]]
    if f_busca:
        txs = [t for t in txs if f_busca.lower() in t["descricao"].lower()]

    if txs:
        # Cabeçalho da tabela
        st.markdown(
            '<div style="display:grid;grid-template-columns:70px 1fr 140px 120px 100px;'
            'gap:8px;padding:6px 12px;background:#0E1117;border-radius:6px 6px 0 0;'
            'font-size:0.68rem;font-weight:700;text-transform:uppercase;'
            'letter-spacing:0.1em;color:#4A5568;margin-bottom:1px;">'
            '<span>Data</span><span>Descrição</span>'
            '<span style="text-align:center">Categoria</span>'
            '<span style="text-align:right">Valor</span>'
            '<span style="text-align:center">Conta</span>'
            '</div>',
            unsafe_allow_html=True,
        )

        for tx in txs[:30]:
            cor  = _COR_RECEITA if tx["eh_receita"] else _COR_DESPESA
            st.markdown(
                f'<div style="display:grid;grid-template-columns:70px 1fr 140px 120px 100px;'
                f'gap:8px;padding:8px 12px;background:#12151E;border-bottom:1px solid #1E2533;'
                f'font-size:0.82rem;align-items:center;">'
                f'<span style="color:#718096">{tx["data_fmt"]}</span>'
                f'<span style="color:#CBD5E0">{tx["descricao"][:35]}</span>'
                f'<span style="text-align:center;background:#1E2533;border-radius:4px;'
                f'padding:2px 6px;font-size:0.72rem;color:#9CA3AF">{tx["categoria"]}</span>'
                f'<span style="text-align:right;font-weight:700;color:{cor}">{tx["valor_fmt"]}</span>'
                f'<span style="text-align:center;font-size:0.75rem;color:#4A5568">{tx["conta"]}</span>'
                f'</div>',
                unsafe_allow_html=True,
            )

        if len(txs) > 30:
            st.caption(f"Mostrando 30 de {len(txs)} lançamentos.")
    else:
        st.caption("Nenhum lançamento encontrado com os filtros aplicados.")
