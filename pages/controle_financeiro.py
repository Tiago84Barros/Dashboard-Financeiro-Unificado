"""
pages/controle_financeiro.py  — v3 (sub-seções completas)

Replica as 4 seções do app original controlefinanceirotsb.streamlit.app:
  Sidebar  — Filtros (mês de referência) + Novo Lançamento completo
               (Tipo: entrada|saída|investimento, Forma pgto, Categoria,
                Data, Valor, Parcelas, Cartão, Descrição, Salvar)
  Tabs     — Dashboard | Análises | Tabelas | Cartão de Crédito

Dados: core/controle + core/investimentos.get_cashflow_mensal()
"""
from datetime import date as _date, timedelta
from collections import defaultdict

import plotly.graph_objects as go
import streamlit as st

from core.controle import get_controle, get_opcoes_formulario, inserir_transacao
from core.investimentos import get_cashflow_mensal
from core.utils import fmt_moeda, fmt_percentual
from design.componentes import badge_status, barra_progresso

# ── Paleta ────────────────────────────────────────────────────────────────────
_COR_RECEITA = "#00C896"
_COR_DESPESA = "#FC5C7D"
_COR_INVEST  = "#4A9EFF"
_COR_NEUTRO  = "#9CA3AF"

_MESES_PT = {
    1: "Jan", 2: "Fev", 3: "Mar", 4: "Abr",
    5: "Mai", 6: "Jun", 7: "Jul", 8: "Ago",
    9: "Set", 10: "Out", 11: "Nov", 12: "Dez",
}

_FORMAS_PGTO = [
    "Cartão de crédito", "Débito", "PIX", "Dinheiro",
    "TED / DOC", "Boleto", "Outro",
]

_CORES_CAT = [
    "#FC5C7D", "#F6C90E", "#4A9EFF", "#00C896", "#9B59B6",
    "#FF6B35", "#1ABC9C", "#E67E22", "#3498DB", "#E91E63",
]


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS — Cards e gráficos
# ══════════════════════════════════════════════════════════════════════════════

def _kpi_card(titulo: str, valor: str, descricao: str, cor: str) -> str:
    return (
        f'<div style="background:#12151E;border:1px solid #1E2533;'
        f'border-radius:10px;padding:20px 18px 16px;height:100%;">'
        f'<div style="font-size:0.62rem;font-weight:800;text-transform:uppercase;'
        f'letter-spacing:0.14em;color:#718096;margin-bottom:10px;">{titulo}</div>'
        f'<div style="font-size:1.70rem;font-weight:800;color:{cor};'
        f'letter-spacing:-0.02em;line-height:1;margin-bottom:8px;">{valor}</div>'
        f'<div style="font-size:0.73rem;color:#4A5568;line-height:1.35;">{descricao}</div>'
        f'</div>'
    )


def _fig_cat_horizontal(cats: list) -> go.Figure:
    nomes  = [c["nome"]  for c in cats]
    gastos = [c["gasto"] for c in cats]
    fig = go.Figure(go.Bar(
        x=gastos, y=nomes, orientation="h",
        marker_color=_COR_DESPESA, opacity=0.85,
        hovertemplate="<b>%{y}</b><br>R$ %{x:,.2f}<extra></extra>",
    ))
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font_color=_COR_NEUTRO,
        margin={"t": 10, "b": 0, "l": 0, "r": 8}, height=300,
        xaxis={"showgrid": True, "gridcolor": "#1E2533",
               "tickformat": ",.0f", "tickprefix": "R$ "},
        yaxis={"showgrid": False, "autorange": "reversed"},
    )
    return fig


def _fig_historico(historico: list) -> go.Figure:
    h6 = historico[-6:] if len(historico) >= 6 else historico
    meses    = [h["label"]    for h in h6]
    receitas = [h["receitas"] for h in h6]
    despesas = [h["despesas"] for h in h6]
    saldos   = [h["saldo"]    for h in h6]

    fig = go.Figure()
    for nome, vals, cor, dash in [
        ("Receitas", receitas, _COR_RECEITA, "solid"),
        ("Despesas", despesas, _COR_DESPESA, "solid"),
        ("Saldo",    saldos,   _COR_INVEST,  "dot"),
    ]:
        fig.add_trace(go.Scatter(
            name=nome, x=meses, y=vals, mode="lines+markers",
            line={"color": cor, "width": 2.5, "dash": dash},
            marker={"size": 7},
            hovertemplate=f"<b>{nome} %{{x}}</b><br>R$ %{{y:,.2f}}<extra></extra>",
        ))
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font_color=_COR_NEUTRO,
        legend={"orientation": "h", "y": -0.22, "font": {"size": 11},
                "bgcolor": "rgba(0,0,0,0)"},
        margin={"t": 10, "b": 10, "l": 0, "r": 0}, height=310,
        yaxis={"showgrid": True, "gridcolor": "#1E2533",
               "tickformat": ",.0f", "tickprefix": "R$ "},
        xaxis={"showgrid": False},
    )
    return fig


def _fig_pizza_cats(cats: list) -> go.Figure:
    nomes  = [c["nome"]  for c in cats]
    gastos = [c["gasto"] for c in cats]
    cores  = _CORES_CAT[:len(cats)]
    fig = go.Figure(go.Pie(
        labels=nomes, values=gastos, hole=0.50,
        marker={"colors": cores, "line": {"color": "#0E1117", "width": 2}},
        textinfo="percent", textfont={"size": 11},
        hovertemplate="<b>%{label}</b><br>R$ %{value:,.2f}<br>%{percent}<extra></extra>",
    ))
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)", font_color=_COR_NEUTRO,
        showlegend=True,
        legend={"font": {"size": 11}, "bgcolor": "rgba(0,0,0,0)"},
        margin={"t": 10, "b": 10, "l": 0, "r": 0}, height=300,
    )
    return fig


def _fig_orcamento(cats: list) -> go.Figure:
    nomes    = [c["nome"]     for c in cats]
    gastos   = [c["gasto"]    for c in cats]
    orcs     = [c["orcamento"] for c in cats]
    cores    = [
        _COR_DESPESA if c["pct_usado"] >= 90 else
        "#F6C90E"    if c["pct_usado"] >= 70 else
        _COR_RECEITA for c in cats
    ]
    fig = go.Figure()
    fig.add_trace(go.Bar(
        name="Orçamento", x=nomes, y=orcs,
        marker_color="#1E2533", marker_line_color="#2D3748", marker_line_width=1,
        hovertemplate="<b>%{x}</b><br>Orçamento: R$ %{y:,.2f}<extra></extra>",
    ))
    fig.add_trace(go.Bar(
        name="Gasto", x=nomes, y=gastos, marker_color=cores,
        opacity=0.9,
        hovertemplate="<b>%{x}</b><br>Gasto: R$ %{y:,.2f}<extra></extra>",
    ))
    fig.update_layout(
        barmode="overlay",
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font_color=_COR_NEUTRO,
        legend={"orientation": "h", "y": -0.22, "font": {"size": 11},
                "bgcolor": "rgba(0,0,0,0)"},
        margin={"t": 10, "b": 10, "l": 0, "r": 0}, height=300,
        xaxis={"showgrid": False, "tickangle": -30},
        yaxis={"showgrid": True, "gridcolor": "#1E2533",
               "tickformat": ",.0f", "tickprefix": "R$ "},
    )
    return fig


# ══════════════════════════════════════════════════════════════════════════════
# SIDEBAR — Filtros + Formulário
# ══════════════════════════════════════════════════════════════════════════════

def _sidebar_render(ano: int, mes: int) -> None:
    """Sidebar completo: Filtros + Novo Lançamento."""

    # ── Filtros ───────────────────────────────────────────────────────────────
    st.sidebar.markdown(
        '<div style="font-size:0.68rem;font-weight:800;text-transform:uppercase;'
        'letter-spacing:0.12em;color:#9CA3AF;margin-bottom:8px;margin-top:4px;">'
        'Filtros</div>',
        unsafe_allow_html=True,
    )
    # A data de referência é gerenciada pelo seletor principal — mostra só leitura
    st.sidebar.caption(f"Mês de referência: **{_MESES_PT[mes]}/{ano}**")

    st.sidebar.divider()

    # ── Novo lançamento ───────────────────────────────────────────────────────
    st.sidebar.markdown(
        '<div style="font-size:0.68rem;font-weight:800;text-transform:uppercase;'
        'letter-spacing:0.12em;color:#4A9EFF;margin-bottom:10px;">'
        'Novo lançamento</div>',
        unsafe_allow_html=True,
    )

    tipo_opcoes  = ["saida", "entrada", "investimento"]
    tipo_labels  = {"saida": "saída", "entrada": "entrada", "investimento": "investimento"}

    tipo = st.sidebar.radio(
        "Tipo",
        tipo_opcoes,
        format_func=lambda x: tipo_labels[x],
        key="cf_sb_tipo",
        horizontal=False,
    )

    # Forma de pagamento
    forma = st.sidebar.selectbox(
        "Forma de pagamento",
        _FORMAS_PGTO,
        key="cf_sb_forma",
    )

    # Categorias filtradas por tipo
    opcoes    = get_opcoes_formulario()
    cats      = opcoes.get("categorias", [])
    contas    = opcoes.get("contas", [])

    tipo_db   = "income" if tipo == "entrada" else "expense"
    cats_tipo = [c for c in cats if c["tipo"] == tipo_db or c["tipo"] == "transfer"]
    if not cats_tipo:
        cats_tipo = cats  # fallback: mostra todas
    cat_nomes = [c["nome"] for c in cats_tipo] or ["(sem categoria)"]
    cat_ids   = [c["id"]   for c in cats_tipo] or [None]

    cat_idx = st.sidebar.selectbox(
        "Categoria",
        range(len(cat_nomes)),
        format_func=lambda i: cat_nomes[i],
        key="cf_sb_cat",
    )

    data_tx = st.sidebar.date_input(
        "Data",
        value=_date(ano, mes, min(
            _date.today().day
            if (ano == _date.today().year and mes == _date.today().month)
            else 28, 28
        )),
        key="cf_sb_data",
    )

    valor = st.sidebar.number_input(
        "Valor (R$)",
        min_value=0.0, step=0.01, format="%.2f",
        key="cf_sb_valor",
    )

    col_parc, col_cart = st.sidebar.columns(2)
    with col_parc:
        st.number_input("Parcelas", min_value=1, max_value=48, value=1,
                        key="cf_sb_parc")
    with col_cart:
        st.text_input("Cartão", placeholder="Final 4 díg.", key="cf_sb_cart")

    descricao = st.sidebar.text_area(
        "Descrição (opcional)", height=80, key="cf_sb_desc",
    )

    st.sidebar.markdown("<br>", unsafe_allow_html=True)

    if st.sidebar.button("Salvar lançamento", use_container_width=True, type="primary"):
        if valor <= 0:
            st.sidebar.error("Informe um valor maior que zero.")
            return

        conta_id = contas[0]["id"] if contas else None
        if not conta_id:
            st.sidebar.warning("Nenhuma conta configurada.")
            return

        cat_id     = cat_ids[cat_idx] if cat_ids and cat_ids[cat_idx] else None
        desc_final = descricao.strip() or cat_nomes[cat_idx]

        # investimento → expense com categoria especial
        tipo_insert = "income" if tipo == "entrada" else "expense"

        ok, msg = inserir_transacao(
            descricao=desc_final,
            valor=valor,
            tipo=tipo_insert,
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
# TAB 1 — Dashboard
# ══════════════════════════════════════════════════════════════════════════════

def _tab_dashboard(d: dict, historico: list) -> None:
    receitas     = d["receitas"]
    despesas     = d["despesas"]
    saldo        = d["saldo_mes"]
    comprometido = round(despesas / receitas * 100, 1) if receitas > 0 else 0.0
    cor_saldo    = _COR_RECEITA if saldo >= 0 else _COR_DESPESA
    cor_comp     = (
        _COR_RECEITA if comprometido < 60 else
        "#F6C90E"    if comprometido < 80 else
        _COR_DESPESA
    )

    # 4 KPI cards
    c1, c2, c3, c4 = st.columns(4, gap="small")
    with c1:
        st.markdown(_kpi_card(
            "Renda do Mês", fmt_moeda(receitas),
            "Somatório de todas as entradas no período selecionado.",
            _COR_RECEITA,
        ), unsafe_allow_html=True)
    with c2:
        st.markdown(_kpi_card(
            "Despesas do Mês", fmt_moeda(despesas),
            "Somatório de todas as saídas no período.",
            _COR_DESPESA,
        ), unsafe_allow_html=True)
    with c3:
        st.markdown(_kpi_card(
            "Saldo Líquido do Mês", fmt_moeda(saldo),
            f"{'Sobrou' if saldo >= 0 else 'Déficit'} dinheiro este mês. "
            f"Investido no mês: R$ 0,00",
            cor_saldo,
        ), unsafe_allow_html=True)
    with c4:
        st.markdown(_kpi_card(
            "Renda Comprometida",
            fmt_percentual(comprometido, sinal=False),
            "Considera despesas em relação à renda do mês.",
            cor_comp,
        ), unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # Gráficos
    col_cat, col_hist = st.columns(2, gap="medium")

    with col_cat:
        st.markdown(
            '<div style="font-size:0.90rem;font-weight:700;color:#E2E8F0;'
            'margin-bottom:8px;">Gastos por categoria (mês)</div>',
            unsafe_allow_html=True,
        )
        cats = d["categorias"]
        if cats:
            st.plotly_chart(_fig_cat_horizontal(cats),
                            use_container_width=True,
                            config={"displayModeBar": False})
        else:
            st.caption("Sem despesas registradas neste mês.")

    with col_hist:
        st.markdown(
            '<div style="font-size:0.90rem;font-weight:700;color:#E2E8F0;'
            'margin-bottom:8px;">Histórico de 6 meses '
            '(Receitas x Despesas x Saldo)</div>',
            unsafe_allow_html=True,
        )
        if historico:
            st.plotly_chart(_fig_historico(historico),
                            use_container_width=True,
                            config={"displayModeBar": False})
        else:
            st.caption("Histórico não disponível.")


# ══════════════════════════════════════════════════════════════════════════════
# TAB 2 — Análises
# ══════════════════════════════════════════════════════════════════════════════

def _tab_analises(d: dict, historico: list) -> None:
    receitas = d["receitas"]
    despesas = d["despesas"]
    saldo    = d["saldo_mes"]
    cats     = d["categorias"]

    # ── Métricas de análise ────────────────────────────────────────────────────
    taxa_poupanca = d.get("taxa_poupanca_pct", 0.0)
    maior_cat     = cats[0] if cats else None

    col_m1, col_m2, col_m3, col_m4 = st.columns(4, gap="small")
    with col_m1:
        st.markdown(_kpi_card(
            "Taxa de Poupança",
            fmt_percentual(taxa_poupanca, sinal=False),
            "Meta recomendada: 30% da renda.",
            _COR_RECEITA if taxa_poupanca >= 30 else
            "#F6C90E" if taxa_poupanca >= 15 else _COR_DESPESA,
        ), unsafe_allow_html=True)
    with col_m2:
        st.markdown(_kpi_card(
            "Despesa Média / Dia",
            fmt_moeda(despesas / 30),
            f"Baseado em 30 dias · {d['num_transacoes']} lançamentos",
            _COR_NEUTRO,
        ), unsafe_allow_html=True)
    with col_m3:
        maior_pct = maior_cat["pct_usado"] if maior_cat else 0
        st.markdown(_kpi_card(
            "Maior Categoria",
            maior_cat["nome"] if maior_cat else "—",
            f"{fmt_moeda(maior_cat['gasto'])} ({maior_pct:.0f}% do orçamento)"
            if maior_cat else "Sem dados.",
            _COR_DESPESA if maior_pct >= 90 else
            "#F6C90E"    if maior_pct >= 70 else _COR_RECEITA,
        ), unsafe_allow_html=True)
    with col_m4:
        rel_invest = 0.0  # placeholder — sem dados de investimento por transação
        st.markdown(_kpi_card(
            "Saldo Acumulado",
            fmt_moeda(saldo),
            "Receitas − Despesas no período selecionado.",
            _COR_RECEITA if saldo >= 0 else _COR_DESPESA,
        ), unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # ── Gráficos de análise ────────────────────────────────────────────────────
    col_pizza, col_orc = st.columns(2, gap="medium")

    with col_pizza:
        st.markdown(
            '<div style="font-size:0.90rem;font-weight:700;color:#E2E8F0;'
            'margin-bottom:8px;">Distribuição de despesas</div>',
            unsafe_allow_html=True,
        )
        if cats:
            st.plotly_chart(_fig_pizza_cats(cats),
                            use_container_width=True,
                            config={"displayModeBar": False})
        else:
            st.caption("Sem despesas.")

    with col_orc:
        st.markdown(
            '<div style="font-size:0.90rem;font-weight:700;color:#E2E8F0;'
            'margin-bottom:8px;">Orçamento vs Realizado</div>',
            unsafe_allow_html=True,
        )
        if cats:
            st.plotly_chart(_fig_orcamento(cats),
                            use_container_width=True,
                            config={"displayModeBar": False})
        else:
            st.caption("Sem dados de orçamento.")

    st.markdown("<br>", unsafe_allow_html=True)

    # ── Orçamento por categoria detalhado ─────────────────────────────────────
    st.markdown(
        '<div style="font-size:0.90rem;font-weight:700;color:#E2E8F0;'
        'margin-bottom:12px;">Orçamento por categoria</div>',
        unsafe_allow_html=True,
    )
    if cats:
        for cat in cats:
            barra_progresso(
                cat["nome"],
                cat["gasto"],
                cat["orcamento"],
                fmt_valor=fmt_moeda(cat["gasto"]),
                fmt_total=fmt_moeda(cat["orcamento"]),
            )
    else:
        st.caption("Nenhuma despesa por categoria neste mês.")

    # ── Tendência histórica ────────────────────────────────────────────────────
    if historico:
        st.markdown("<br>", unsafe_allow_html=True)
        st.markdown(
            '<div style="font-size:0.90rem;font-weight:700;color:#E2E8F0;'
            'margin-bottom:8px;">Evolução mensal (12 meses)</div>',
            unsafe_allow_html=True,
        )
        # Barra: taxa de poupança por mês
        meses_labels = [h["label"]    for h in historico]
        taxas        = [
            round(h["saldo"] / h["receitas"] * 100, 1)
            if h["receitas"] > 0 else 0.0
            for h in historico
        ]
        cores_taxa = [
            _COR_RECEITA if t >= 30 else "#F6C90E" if t >= 15 else _COR_DESPESA
            for t in taxas
        ]
        fig_taxa = go.Figure(go.Bar(
            x=meses_labels, y=taxas, marker_color=cores_taxa,
            hovertemplate="<b>%{x}</b><br>Taxa: %{y:.1f}%<extra></extra>",
        ))
        fig_taxa.update_layout(
            title_text="Taxa de Poupança Mensal (%)",
            title_font={"size": 12, "color": "#9CA3AF"},
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            font_color=_COR_NEUTRO,
            margin={"t": 30, "b": 0, "l": 0, "r": 0}, height=220,
            xaxis={"showgrid": False},
            yaxis={"showgrid": True, "gridcolor": "#1E2533",
                   "tickformat": ".0f", "ticksuffix": "%"},
            shapes=[{"type": "line", "x0": -0.5, "x1": len(meses_labels) - 0.5,
                     "y0": 30, "y1": 30,
                     "line": {"color": _COR_RECEITA, "width": 1.5, "dash": "dot"}}],
        )
        st.plotly_chart(fig_taxa, use_container_width=True,
                        config={"displayModeBar": False})
        st.caption("Linha pontilhada verde = meta 30%")


# ══════════════════════════════════════════════════════════════════════════════
# TAB 3 — Tabelas
# ══════════════════════════════════════════════════════════════════════════════

def _tab_tabelas(d: dict) -> None:
    txs = d["transacoes"]

    # ── Filtros ────────────────────────────────────────────────────────────────
    st.markdown(
        '<div style="font-size:0.90rem;font-weight:700;color:#E2E8F0;'
        'margin-bottom:12px;">Lançamentos do período</div>',
        unsafe_allow_html=True,
    )

    col_f1, col_f2, col_f3 = st.columns([1, 1, 2])
    with col_f1:
        f_tipo = st.selectbox(
            "Tipo",
            ["Todos", "Receitas", "Despesas"],
            key="tab_ftipo",
            label_visibility="collapsed",
        )
    with col_f2:
        cats_disponiveis = sorted({t["categoria"] for t in txs})
        f_cat = st.selectbox(
            "Categoria",
            ["Todas"] + cats_disponiveis,
            key="tab_fcat",
            label_visibility="collapsed",
        )
    with col_f3:
        f_busca = st.text_input(
            "Busca",
            placeholder="Pesquisar na descrição...",
            key="tab_busca",
            label_visibility="collapsed",
        )

    # Aplicar filtros
    txs_f = txs
    if f_tipo == "Receitas":
        txs_f = [t for t in txs_f if t["eh_receita"]]
    elif f_tipo == "Despesas":
        txs_f = [t for t in txs_f if not t["eh_receita"]]
    if f_cat != "Todas":
        txs_f = [t for t in txs_f if t["categoria"] == f_cat]
    if f_busca:
        txs_f = [t for t in txs_f if f_busca.lower() in t["descricao"].lower()]

    # Totais dos filtrados
    total_rec = sum(t["valor"] for t in txs_f if t["eh_receita"])
    total_desp = sum(abs(t["valor"]) for t in txs_f if not t["eh_receita"])

    col_s1, col_s2, col_s3, *_ = st.columns([1, 1, 1, 3])
    col_s1.metric("Registros", len(txs_f))
    col_s2.metric("Entradas", fmt_moeda(total_rec))
    col_s3.metric("Saídas", fmt_moeda(total_desp))

    st.markdown("<br>", unsafe_allow_html=True)

    if not txs_f:
        st.caption("Nenhum lançamento com os filtros aplicados.")
        return

    # ── Tabela completa ────────────────────────────────────────────────────────
    st.markdown(
        '<div style="display:grid;'
        'grid-template-columns:80px 1fr 150px 80px 130px 100px;'
        'gap:6px;padding:7px 12px;background:#0E1117;'
        'border-radius:6px 6px 0 0;'
        'font-size:0.65rem;font-weight:700;text-transform:uppercase;'
        'letter-spacing:0.1em;color:#4A5568;">'
        '<span>Data</span>'
        '<span>Descrição</span>'
        '<span style="text-align:center">Categoria</span>'
        '<span style="text-align:center">Tipo</span>'
        '<span style="text-align:right">Valor</span>'
        '<span style="text-align:center">Conta</span>'
        '</div>',
        unsafe_allow_html=True,
    )

    for tx in txs_f:
        cor  = _COR_RECEITA if tx["eh_receita"] else _COR_DESPESA
        tipo_badge = "entrada" if tx["eh_receita"] else "saída"
        cor_badge  = _COR_RECEITA if tx["eh_receita"] else _COR_DESPESA
        st.markdown(
            f'<div style="display:grid;'
            f'grid-template-columns:80px 1fr 150px 80px 130px 100px;'
            f'gap:6px;padding:8px 12px;background:#12151E;'
            f'border-bottom:1px solid #1A1F2E;'
            f'font-size:0.82rem;align-items:center;">'
            f'<span style="color:#718096">{tx["data_fmt"]}</span>'
            f'<span style="color:#CBD5E0" title="{tx["descricao"]}">'
            f'{tx["descricao"][:40]}</span>'
            f'<span style="text-align:center;background:#1E2533;'
            f'border-radius:4px;padding:2px 6px;'
            f'font-size:0.72rem;color:{_COR_NEUTRO}">{tx["categoria"]}</span>'
            f'<span style="text-align:center;font-size:0.72rem;'
            f'font-weight:700;color:{cor_badge}">{tipo_badge}</span>'
            f'<span style="text-align:right;font-weight:700;color:{cor}">'
            f'{tx["valor_fmt"]}</span>'
            f'<span style="text-align:center;font-size:0.75rem;'
            f'color:#4A5568">{tx["conta"]}</span>'
            f'</div>',
            unsafe_allow_html=True,
        )

    if len(txs_f) >= 50:
        st.caption(f"Exibindo {len(txs_f)} registros.")


# ══════════════════════════════════════════════════════════════════════════════
# TAB 4 — Cartão de Crédito
# ══════════════════════════════════════════════════════════════════════════════

def _tab_cartao(d: dict) -> None:
    txs = d["transacoes"]

    # Filtra apenas despesas (em app real filtraria por forma_pagamento = cartão)
    # Por ora, mostra despesas agrupadas como se fossem do cartão
    despesas_tx = [t for t in txs if not t["eh_receita"]]

    st.markdown(
        '<div style="background:rgba(74,158,255,0.06);'
        'border:1px solid rgba(74,158,255,0.2);'
        'border-left:3px solid #4A9EFF;'
        'border-radius:0 8px 8px 0;'
        'padding:10px 14px;font-size:0.82rem;color:#9CA3AF;margin-bottom:16px;">'
        'ℹ️ Exibe todas as despesas do mês. '
        'Quando o banco real estiver conectado, será possível filtrar '
        'por <b>forma de pagamento = Cartão de crédito</b> '
        'e agrupar por número do cartão e parcelas.</div>',
        unsafe_allow_html=True,
    )

    if not despesas_tx:
        st.caption("Nenhuma despesa registrada neste mês.")
        return

    # Sumário de cartão
    total_cartao = sum(abs(t["valor"]) for t in despesas_tx)
    num_tx       = len(despesas_tx)

    c1, c2, c3, *_ = st.columns([1, 1, 1, 3])
    with c1:
        st.markdown(_kpi_card(
            "Total em Despesas",
            fmt_moeda(total_cartao),
            f"{num_tx} lançamento{'s' if num_tx != 1 else ''}",
            _COR_DESPESA,
        ), unsafe_allow_html=True)
    with c2:
        media = total_cartao / num_tx if num_tx > 0 else 0
        st.markdown(_kpi_card(
            "Ticket Médio",
            fmt_moeda(media),
            "Média por transação.",
            _COR_NEUTRO,
        ), unsafe_allow_html=True)
    with c3:
        # Maior despesa avulsa
        maior = max(despesas_tx, key=lambda t: abs(t["valor"]))
        st.markdown(_kpi_card(
            "Maior Lançamento",
            fmt_moeda(abs(maior["valor"])),
            maior["descricao"][:30],
            "#F6C90E",
        ), unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # ── Agrupamento por categoria ──────────────────────────────────────────────
    st.markdown(
        '<div style="font-size:0.90rem;font-weight:700;color:#E2E8F0;'
        'margin-bottom:12px;">Despesas por categoria</div>',
        unsafe_allow_html=True,
    )

    agg: dict[str, float] = defaultdict(float)
    for t in despesas_tx:
        agg[t["categoria"]] += abs(t["valor"])

    agg_sorted = sorted(agg.items(), key=lambda x: x[1], reverse=True)

    for i, (cat, valor) in enumerate(agg_sorted):
        pct = valor / total_cartao * 100 if total_cartao > 0 else 0
        cor = _CORES_CAT[i % len(_CORES_CAT)]
        st.markdown(
            f'<div style="display:flex;justify-content:space-between;'
            f'align-items:center;padding:8px 0;'
            f'border-bottom:1px solid #1A1F2E;font-size:0.83rem;">'
            f'<div style="display:flex;align-items:center;gap:10px;">'
            f'<div style="width:8px;height:8px;border-radius:50%;'
            f'background:{cor};flex-shrink:0"></div>'
            f'<span style="color:#CBD5E0">{cat}</span>'
            f'</div>'
            f'<div style="text-align:right;">'
            f'<span style="font-weight:700;color:{_COR_DESPESA}">'
            f'{fmt_moeda(valor)}</span>'
            f'<span style="font-size:0.72rem;color:#4A5568;margin-left:8px;">'
            f'{pct:.1f}%</span>'
            f'</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

    st.markdown("<br>", unsafe_allow_html=True)

    # ── Lista de lançamentos ───────────────────────────────────────────────────
    st.markdown(
        '<div style="font-size:0.90rem;font-weight:700;color:#E2E8F0;'
        'margin-bottom:10px;">Lançamentos</div>',
        unsafe_allow_html=True,
    )

    st.markdown(
        '<div style="display:grid;grid-template-columns:80px 1fr 150px 120px;'
        'gap:6px;padding:7px 12px;background:#0E1117;border-radius:6px 6px 0 0;'
        'font-size:0.65rem;font-weight:700;text-transform:uppercase;'
        'letter-spacing:0.1em;color:#4A5568;">'
        '<span>Data</span><span>Descrição</span>'
        '<span style="text-align:center">Categoria</span>'
        '<span style="text-align:right">Valor</span>'
        '</div>',
        unsafe_allow_html=True,
    )

    for tx in despesas_tx[:50]:
        st.markdown(
            f'<div style="display:grid;grid-template-columns:80px 1fr 150px 120px;'
            f'gap:6px;padding:8px 12px;background:#12151E;'
            f'border-bottom:1px solid #1A1F2E;'
            f'font-size:0.82rem;align-items:center;">'
            f'<span style="color:#718096">{tx["data_fmt"]}</span>'
            f'<span style="color:#CBD5E0">{tx["descricao"][:40]}</span>'
            f'<span style="text-align:center;background:#1E2533;'
            f'border-radius:4px;padding:2px 6px;'
            f'font-size:0.72rem;color:{_COR_NEUTRO}">{tx["categoria"]}</span>'
            f'<span style="text-align:right;font-weight:700;color:{_COR_DESPESA}">'
            f'{tx["valor_fmt"]}</span>'
            f'</div>',
            unsafe_allow_html=True,
        )


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
        result.append({"label": f"{_MESES_PT[m]}/{y}", "ano": y, "mes": m})
    return result


def render() -> None:
    # ── Seletor de mês no header ──────────────────────────────────────────────
    opcoes     = _opcoes_mes(12)
    labels_mes = [o["label"] for o in opcoes]

    col_title, col_mes = st.columns([3, 1])
    with col_title:
        st.markdown(
            '<h1 style="font-size:2rem;font-weight:800;color:#E2E8F0;margin:0;">'
            '💰 Controle Financeiro</h1>',
            unsafe_allow_html=True,
        )
    with col_mes:
        idx = st.selectbox(
            "Mês",
            range(len(labels_mes)),
            format_func=lambda i: labels_mes[i],
            key="cf_mes_idx",
            label_visibility="collapsed",
        )

    sel = opcoes[idx]
    d   = get_controle(sel["ano"], sel["mes"])

    # Subtítulo
    st.markdown(
        f'<p style="color:#718096;font-size:0.88rem;margin-top:3px;margin-bottom:0;">'
        f'Visão geral de <b style="color:#E2E8F0">'
        f'{sel["mes"]:02d}/{sel["ano"]}</b>'
        f'&nbsp;•&nbsp; acompanhe renda, despesas e saldo em tempo real</p>',
        unsafe_allow_html=True,
    )

    # Badges
    _fonte = d.get("data_source", "mock")
    col_b1, col_b2, *_ = st.columns([1, 1, 6])
    with col_b1:
        badge_status(
            "Dados reais"     if _fonte == "real" else
            "Fallback (mock)" if _fonte == "mock_fallback" else "Modo mock",
            "sucesso" if _fonte == "real" else
            "erro"    if _fonte == "mock_fallback" else "alerta",
        )
    with col_b2:
        badge_status(f"Mês atual: {_date.today().strftime('%d/%m/%Y')}", "neutro")

    st.markdown("<br>", unsafe_allow_html=True)

    # ── Sidebar completo ──────────────────────────────────────────────────────
    _sidebar_render(sel["ano"], sel["mes"])

    # ── Cashflow histórico (compartilhado entre tabs) ─────────────────────────
    historico = get_cashflow_mensal()

    # ── Sub-navegação via tabs ────────────────────────────────────────────────
    tab1, tab2, tab3, tab4 = st.tabs([
        "📊  Dashboard",
        "📈  Análises",
        "🧾  Tabelas",
        "💳  Cartão de Crédito",
    ])

    with tab1:
        _tab_dashboard(d, historico)

    with tab2:
        _tab_analises(d, historico)

    with tab3:
        _tab_tabelas(d)

    with tab4:
        _tab_cartao(d)
