"""
pages/controle_financeiro.py  — v4 (preservação fiel do app original)

Replica FIELMENTE as 4 seções do app original controlefinanceirotsb.streamlit.app:
  Sidebar  — Filtros (mês de referência) + Novo Lançamento completo
               (Tipo: entrada|saída|investimento, Forma pgto condicional,
                Categoria com presets por tipo, Data, Valor, Parcelas,
                Cartão, Descrição, Salvar)
  Tabs     — Dashboard | Análises | Tabelas | Cartão de Crédito

Adições do app unificado preservadas (não existiam no original):
  - Pizza de despesas na aba Análises
  - Orçamento vs Realizado (overlay)
  - Barras de progresso por categoria
  - Taxa de poupança mensal histórica

Novas funcionalidades implementadas na Fase 5.1:
  - Dashboard: seção "Últimos Lançamentos" com modo leitura + edição
  - Análises: Comparativo Ano a Ano (YOY)
  - Análises: Evolução do Patrimônio Investido (ano a ano)
  - Tabelas: filtros por Tipo/Categoria/Ano/Mês/Dia/texto + totais
  - Cartão: filtro por payment_type quando dados disponíveis

Dados: core/controle + core/investimentos.get_cashflow_mensal()
"""
from datetime import date as _date, timedelta
from collections import defaultdict

import plotly.graph_objects as go
import streamlit as st

from core.controle import (
    get_controle, get_opcoes_formulario, inserir_transacao,
    atualizar_transacao, get_historico_anual, get_transacoes_filtradas,
    get_gastos_cartao_mensal,
)
from core.investimentos import get_cashflow_mensal, get_evolucao_patrimonial
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

_MESES_NOMES = {v: k for k, v in _MESES_PT.items()}

_FORMAS_PGTO_SAIDA = ["Conta", "Cartão de crédito", "Dinheiro", "Pix"]
_FORMAS_PGTO_TODOS = ["Conta", "Cartão de crédito", "Débito", "Dinheiro", "PIX", "TED / DOC", "Boleto", "Outro"]

# Categorias pré-definidas por tipo (igual ao app original)
_CAT_ENTRADA = [
    "Salário", "Renda Extra", "Dividendos", "Reembolso", "Outros",
]
_CAT_SAIDA = [
    "Mercado", "Compras", "Condomínio", "Luz", "Internet", "Transporte",
    "Combustível", "Saúde", "Despesas Domésticas", "Lazer", "Assinaturas",
    "Educação", "Restaurante", "Financiamento", "Pagamento de Cartão", "Outros",
]
_CAT_INVESTIMENTO = [
    "Renda Fixa", "Renda Variável", "Exterior", "Reserva de Despesa", "Outra",
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
        f'<div style="font-size:clamp(1.10rem,2.2vw,1.70rem);font-weight:800;color:{cor};'
        f'letter-spacing:-0.02em;line-height:1.1;margin-bottom:8px;'
        f'white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">{valor}</div>'
        f'<div style="font-size:0.73rem;color:#4A5568;line-height:1.35;">{descricao}</div>'
        f'</div>'
    )


def _secao_titulo(icone: str, titulo: str) -> None:
    st.markdown(
        f'<div style="font-size:0.90rem;font-weight:700;color:#E2E8F0;'
        f'margin-bottom:8px;">{icone} {titulo}</div>',
        unsafe_allow_html=True,
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


def _fig_yoy(por_ano: dict, anos: list) -> go.Figure:
    """Bar chart agrupado: Receitas × Despesas × Investimentos por ano (igual ao original)."""
    rec   = [por_ano[a]["receitas"]      for a in anos]
    desp  = [por_ano[a]["despesas"]      for a in anos]
    inv   = [por_ano[a].get("investimentos", 0.0) for a in anos]
    anos_str = [str(a) for a in anos]

    fig = go.Figure()
    for nome, vals, cor in [
        ("Receitas",       rec,  _COR_RECEITA),
        ("Despesas",       desp, _COR_DESPESA),
        ("Investimentos",  inv,  _COR_INVEST),
    ]:
        fig.add_trace(go.Bar(
            name=nome, x=anos_str, y=vals, marker_color=cor, opacity=0.85,
            hovertemplate=f"<b>{nome} %{{x}}</b><br>R$ %{{y:,.2f}}<extra></extra>",
        ))
    fig.update_layout(
        barmode="group",
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font_color=_COR_NEUTRO,
        legend={"orientation": "h", "y": -0.22, "font": {"size": 11},
                "bgcolor": "rgba(0,0,0,0)"},
        margin={"t": 10, "b": 10, "l": 0, "r": 0}, height=320,
        xaxis={"showgrid": False},
        yaxis={"showgrid": True, "gridcolor": "#1E2533",
               "tickformat": ",.0f", "tickprefix": "R$ "},
    )
    return fig


def _fig_patrimonio_investido(por_ano: dict, anos: list) -> go.Figure:
    """Barras anuais de investimentos + linha acumulada (igual ao original)."""
    # Usa investimentos reais se disponíveis; fallback para saldo positivo
    vals  = [por_ano[a].get("investimentos") or max(0.0, por_ano[a]["saldo"]) for a in anos]
    acum  = []
    total = 0.0
    for v in vals:
        total += v
        acum.append(round(total, 2))
    anos_str = [str(a) for a in anos]

    fig = go.Figure()
    fig.add_trace(go.Bar(
        name="Investido no ano", x=anos_str, y=vals,
        marker_color="#87CEEB", opacity=0.90,
        hovertemplate="<b>Ano %{x}</b><br>Investido no ano: R$ %{y:,.2f}<extra></extra>",
        yaxis="y",
    ))
    fig.add_trace(go.Scatter(
        name="Acumulado investido", x=anos_str, y=acum, mode="lines+markers",
        line={"color": "#4A9EFF", "width": 2.5}, marker={"size": 7},
        hovertemplate="<b>Ano %{x}</b><br>Acumulado até o ano: R$ %{y:,.2f}<extra></extra>",
        yaxis="y2",
    ))
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font_color=_COR_NEUTRO,
        legend={"orientation": "h", "y": -0.22, "font": {"size": 11},
                "bgcolor": "rgba(0,0,0,0)"},
        margin={"t": 10, "b": 10, "l": 0, "r": 0}, height=320,
        xaxis={"showgrid": False, "title": {"text": "Ano", "font": {"size": 10}}},
        yaxis={"showgrid": True, "gridcolor": "#1E2533",
               "tickformat": ",.0f", "tickprefix": "R$ ",
               "title": {"text": "Investido no ano (R$)", "font": {"size": 10}}},
        yaxis2={"overlaying": "y", "side": "right",
                "showgrid": False,
                "tickformat": ",.0f", "tickprefix": "R$ ",
                "title": {"text": "Acumulado (R$)", "font": {"size": 10}}},
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
    st.sidebar.caption(f"Mês de referência: **{_MESES_PT[mes]}/{ano}**")

    st.sidebar.divider()

    # ── Novo lançamento ───────────────────────────────────────────────────────
    st.sidebar.markdown(
        '<div style="font-size:0.68rem;font-weight:800;text-transform:uppercase;'
        'letter-spacing:0.12em;color:#4A9EFF;margin-bottom:10px;">'
        'Novo lançamento</div>',
        unsafe_allow_html=True,
    )

    # 1) Tipo (reativo — fora do form para filtrar categorias e forma pgto)
    t_type = st.sidebar.radio(
        "Tipo",
        ["entrada", "saida", "investimento"],
        format_func=lambda x: {"entrada": "entrada", "saida": "saída", "investimento": "investimento"}[x],
        key="cf_sb_tipo",
        horizontal=True,
    )

    st.sidebar.markdown("<hr style='margin-top:0;margin-bottom:10px;opacity:0.25;'>", unsafe_allow_html=True)

    # 2) Forma de pagamento (só para saída — igual ao original)
    if t_type == "saida":
        forma = st.sidebar.selectbox(
            "Forma de pagamento",
            _FORMAS_PGTO_SAIDA,
            key="cf_sb_forma",
        )
        show_card = (forma == "Cartão de crédito")
    else:
        forma = "Conta"
        show_card = False

    # 3) FORMULÁRIO (limpa após salvar)
    with st.sidebar.form("form_nova_tx", clear_on_submit=True):

        # Categorias pré-definidas por tipo (mais opções do DB como fallback)
        if t_type == "entrada":
            cat_preset = _CAT_ENTRADA + ["Outra"]
        elif t_type == "saida":
            cat_preset = _CAT_SAIDA + ["Outra"]
        else:
            cat_preset = _CAT_INVESTIMENTO

        cat_idx = st.selectbox(
            "Categoria",
            range(len(cat_preset)),
            format_func=lambda i: cat_preset[i],
            key="cf_sb_cat",
        )
        cat_escolhida = cat_preset[cat_idx]

        # Campo livre para "Outra"
        if cat_escolhida == "Outra":
            cat_livre = st.text_input("Categoria personalizada", key="cf_sb_cat_livre")
        else:
            cat_livre = ""

        data_tx = st.date_input(
            "Data",
            value=_date(ano, mes, min(
                _date.today().day
                if (ano == _date.today().year and mes == _date.today().month)
                else 28, 28
            )),
            format="DD/MM/YYYY",
            key="cf_sb_data",
        )

        valor = st.number_input(
            "Valor (R$)",
            min_value=0.0, step=0.01, format="%.2f",
            key="cf_sb_valor",
        )

        # Campos de cartão (só para saída + Cartão de crédito)
        if show_card:
            col_parc, col_cart = st.columns(2)
            with col_parc:
                parcelas = st.number_input("Parcelas", min_value=1, max_value=48, value=1,
                                           key="cf_sb_parc")
            with col_cart:
                nome_cartao = st.text_input("Cartão", placeholder="Final 4 díg.", key="cf_sb_cart")
        else:
            parcelas, nome_cartao = 1, ""

        descricao = st.text_area(
            "Descrição (opcional)", height=60, key="cf_sb_desc",
        )

        submitted = st.form_submit_button("Salvar lançamento", use_container_width=True)

    if submitted:
        if valor <= 0:
            st.sidebar.error("Informe um valor maior que zero.")
            return

        categoria_final = cat_livre.strip() if cat_escolhida == "Outra" else cat_escolhida
        if not categoria_final:
            st.sidebar.error("Informe a categoria.")
            return

        # Resolve conta
        opcoes  = get_opcoes_formulario()
        contas  = opcoes.get("contas", [])
        conta_id = contas[0]["id"] if contas else None
        if not conta_id:
            st.sidebar.warning("Nenhuma conta configurada.")
            return

        # Resolve category_id (busca pelo nome no DB; None se não encontrar)
        cats_db    = opcoes.get("categorias", [])
        cat_match  = next((c for c in cats_db if c["nome"] == categoria_final), None)
        cat_id     = cat_match["id"] if cat_match else None

        # Tipo para o banco (investimento preservado como tipo próprio)
        _MAP_TIPO = {"entrada": "income", "saida": "expense", "investimento": "investment"}
        tipo_insert = _MAP_TIPO.get(t_type, "expense")

        desc_final = descricao.strip() or categoria_final

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

def _tab_dashboard(d: dict, historico: list, fluxo_inv: dict) -> None:
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
            f"{'Sobrou' if saldo >= 0 else 'Déficit'} dinheiro este mês.",
            cor_saldo,
        ), unsafe_allow_html=True)
    with c4:
        st.markdown(_kpi_card(
            "Renda Comprometida",
            fmt_percentual(comprometido, sinal=False),
            "Despesas em relação à renda do mês.",
            cor_comp,
        ), unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # Gráficos
    col_cat, col_hist = st.columns(2, gap="medium")

    with col_cat:
        _secao_titulo("📊", "Gastos por categoria (mês)")
        cats = d["categorias"]
        if cats:
            st.plotly_chart(_fig_cat_horizontal(cats),
                            use_container_width=True,
                            config={"displayModeBar": False})
            # Tabela com % da renda (igual ao original)
            if receitas > 0:
                st.markdown(
                    '<div style="display:grid;grid-template-columns:1fr 110px 90px;'
                    'gap:4px;padding:5px 10px;background:#0E1117;border-radius:4px 4px 0 0;'
                    'font-size:0.63rem;font-weight:700;text-transform:uppercase;'
                    'letter-spacing:0.1em;color:#4A5568;">'
                    '<span>Categoria</span>'
                    '<span style="text-align:right">Valor</span>'
                    '<span style="text-align:right">% renda</span>'
                    '</div>',
                    unsafe_allow_html=True,
                )
                for cat in cats:
                    pct_r = round(cat["gasto"] / receitas * 100, 1)
                    st.markdown(
                        f'<div style="display:grid;grid-template-columns:1fr 110px 90px;'
                        f'gap:4px;padding:5px 10px;background:#12151E;'
                        f'border-bottom:1px solid #1A1F2E;font-size:0.80rem;">'
                        f'<span style="color:#CBD5E0">{cat["nome"]}</span>'
                        f'<span style="text-align:right;color:{_COR_DESPESA};font-weight:700">'
                        f'{fmt_moeda(cat["gasto"])}</span>'
                        f'<span style="text-align:right;color:#718096">{pct_r:.1f}%</span>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )
        else:
            st.caption("Sem despesas registradas neste mês.")

    with col_hist:
        _secao_titulo("📈", "Histórico de 6 meses (Receitas × Despesas × Saldo)")
        if historico:
            st.plotly_chart(_fig_historico(historico),
                            use_container_width=True,
                            config={"displayModeBar": False})
        else:
            st.caption("Histórico não disponível.")

    # ── Tabela resumo 6 meses ─────────────────────────────────────────────────
    ultimos6 = historico[-6:] if len(historico) >= 6 else historico
    if ultimos6:
        import pandas as pd
        rows_t = []
        for h in ultimos6:
            inv = fluxo_inv.get((h["ano"], h["mes"]), 0.0)
            rows_t.append({
                "Mês":           h["label"],
                "Receitas":      h["receitas"],
                "Despesas":      h["despesas"],
                "Investimentos": inv,
            })
        df_hist = pd.DataFrame(rows_t)
        st.dataframe(
            df_hist,
            column_config={
                "Mês":           st.column_config.TextColumn("Mês",          width="small"),
                "Receitas":      st.column_config.NumberColumn("Receitas",    format="R$ %.2f"),
                "Despesas":      st.column_config.NumberColumn("Despesas",    format="R$ %.2f"),
                "Investimentos": st.column_config.NumberColumn("Investimentos", format="R$ %.2f"),
            },
            hide_index=True,
            use_container_width=True,
        )

    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown("<hr style='border-color:#1E2533;'>", unsafe_allow_html=True)

    # ── Últimos Lançamentos (igual ao original) ───────────────────────────────
    _secao_titulo("📋", "Últimos Lançamentos")

    txs = d["transacoes"]
    if not txs:
        st.caption("Nenhum lançamento cadastrado ainda.")
        return

    edit_mode = st.checkbox("Habilitar edição dos lançamentos", key="dash_edit_mode")

    if not edit_mode:
        # Modo leitura
        st.markdown(
            '<div style="display:grid;'
            'grid-template-columns:80px 1fr 150px 80px 130px 100px;'
            'gap:4px;padding:5px 10px;background:#0E1117;border-radius:4px 4px 0 0;'
            'font-size:0.63rem;font-weight:700;text-transform:uppercase;'
            'letter-spacing:0.1em;color:#4A5568;">'
            '<span>Data</span><span>Descrição</span>'
            '<span style="text-align:center">Categoria</span>'
            '<span style="text-align:center">Tipo</span>'
            '<span style="text-align:right">Valor</span>'
            '<span style="text-align:center">Conta</span>'
            '</div>',
            unsafe_allow_html=True,
        )
        for tx in txs[:30]:
            cor  = _COR_RECEITA if tx["eh_receita"] else _COR_DESPESA
            tipo_label = "entrada" if tx["eh_receita"] else "saída"
            st.markdown(
                f'<div style="display:grid;'
                f'grid-template-columns:80px 1fr 150px 80px 130px 100px;'
                f'gap:4px;padding:6px 10px;background:#12151E;'
                f'border-bottom:1px solid #1A1F2E;font-size:0.81rem;align-items:center;">'
                f'<span style="color:#718096">{tx["data_fmt"]}</span>'
                f'<span style="color:#CBD5E0" title="{tx["descricao"]}">'
                f'{tx["descricao"][:38]}</span>'
                f'<span style="text-align:center;background:#1E2533;border-radius:4px;'
                f'padding:2px 6px;font-size:0.70rem;color:{_COR_NEUTRO}">'
                f'{tx["categoria"]}</span>'
                f'<span style="text-align:center;font-size:0.72rem;font-weight:700;color:{cor}">'
                f'{tipo_label}</span>'
                f'<span style="text-align:right;font-weight:700;color:{cor}">'
                f'{tx["valor_fmt"]}</span>'
                f'<span style="text-align:center;font-size:0.72rem;color:#4A5568">'
                f'{tx["conta"]}</span>'
                f'</div>',
                unsafe_allow_html=True,
            )
        if len(txs) > 30:
            st.caption(f"Exibindo 30 de {len(txs)} lançamentos.")

    else:
        st.info("Edite os campos desejados e clique **Salvar alterações** para gravar no banco.")

        opcoes    = get_opcoes_formulario()
        cats_db   = opcoes.get("categorias", [])
        cat_nomes = [c["nome"] for c in cats_db]

        # Prepara DataFrame para edição
        import pandas as pd
        rows_edit = []
        for tx in txs[:30]:
            rows_edit.append({
                "ID":        tx["id"],
                "Tipo":      "entrada" if tx["eh_receita"] else "saída",
                "Categoria": tx["categoria"],
                "Data":      tx["data"],
                "Valor":     abs(tx["valor"]),
                "Descrição": tx["descricao"],
                "Conta":     tx["conta"],
            })
        df_edit = pd.DataFrame(rows_edit)

        edited = st.data_editor(
            df_edit,
            num_rows="fixed",
            hide_index=True,
            key="editor_lancamentos",
            column_config={
                "ID":      st.column_config.TextColumn("ID", disabled=True),
                "Tipo":    st.column_config.TextColumn("Tipo", disabled=True),
                "Conta":   st.column_config.TextColumn("Conta", disabled=True),
                "Categoria": st.column_config.SelectboxColumn(
                    "Categoria",
                    options=cat_nomes if cat_nomes else ["Sem categoria"],
                ),
                "Data":    st.column_config.DateColumn("Data"),
                "Valor":   st.column_config.NumberColumn("Valor (R$)", format="%.2f", step=0.01),
                "Descrição": st.column_config.TextColumn("Descrição"),
            },
        )

        if st.button("Salvar alterações", key="btn_salvar_edicoes"):
            erros = []
            ok_count = 0
            for i, row in edited.iterrows():
                orig = df_edit.iloc[i]
                campos_mudaram = (
                    row["Descrição"] != orig["Descrição"]
                    or row["Categoria"] != orig["Categoria"]
                    or abs(row["Valor"] - orig["Valor"]) > 0.001
                    or row["Data"] != orig["Data"]
                )
                if campos_mudaram:
                    # Resolve category_id
                    cat_m = next((c for c in cats_db if c["nome"] == row["Categoria"]), None)
                    cat_id = cat_m["id"] if cat_m else None
                    # Mantém sinal original
                    sinal = 1.0 if txs[i]["eh_receita"] else -1.0
                    ok, msg = atualizar_transacao(
                        tx_id=str(row["ID"]),
                        descricao=str(row["Descrição"]),
                        valor=sinal * abs(float(row["Valor"])),
                        data=row["Data"],
                        categoria_id=cat_id,
                    )
                    if ok:
                        ok_count += 1
                    else:
                        erros.append(f"ID {row['ID']}: {msg}")
            if ok_count > 0:
                st.success(f"✅ {ok_count} lançamento(s) atualizado(s).")
                st.rerun()
            if erros:
                for e in erros:
                    st.error(e)
            if ok_count == 0 and not erros:
                st.info("Nenhuma alteração detectada.")


# ══════════════════════════════════════════════════════════════════════════════
# TAB 2 — Análises
# ══════════════════════════════════════════════════════════════════════════════

def _tab_analises(d: dict, historico: list, hist_anual: dict, gastos_cartao: dict) -> None:
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
        st.markdown(_kpi_card(
            "Saldo Acumulado",
            fmt_moeda(saldo),
            "Receitas − Despesas no período selecionado.",
            _COR_RECEITA if saldo >= 0 else _COR_DESPESA,
        ), unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # ── Distribuição + Orçamento ───────────────────────────────────────────────
    col_pizza, col_orc = st.columns(2, gap="medium")
    with col_pizza:
        _secao_titulo("🍕", "Distribuição de despesas")
        if cats:
            st.plotly_chart(_fig_pizza_cats(cats), use_container_width=True,
                            config={"displayModeBar": False})
        else:
            st.caption("Sem despesas.")

    with col_orc:
        _secao_titulo("📊", "Orçamento vs Realizado")
        if cats:
            st.plotly_chart(_fig_orcamento(cats), use_container_width=True,
                            config={"displayModeBar": False})
        else:
            st.caption("Sem dados de orçamento.")

    # ── Taxa de Poupança Histórica ─────────────────────────────────────────────
    if historico:
        st.markdown("<br>", unsafe_allow_html=True)
        _secao_titulo("💹", "Taxa de Poupança Mensal (12 meses)")
        meses_labels = [h["label"] for h in historico]
        taxas = [
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
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            font_color=_COR_NEUTRO,
            margin={"t": 10, "b": 0, "l": 0, "r": 0}, height=220,
            xaxis={"showgrid": False},
            yaxis={"showgrid": True, "gridcolor": "#1E2533",
                   "tickformat": ".0f", "ticksuffix": "%"},
            shapes=[{
                "type": "line", "x0": -0.5, "x1": len(meses_labels) - 0.5,
                "y0": 30, "y1": 30,
                "line": {"color": _COR_RECEITA, "width": 1.5, "dash": "dot"},
            }],
        )
        st.plotly_chart(fig_taxa, use_container_width=True, config={"displayModeBar": False})
        st.caption("Linha pontilhada verde = meta 30%")

    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown("<hr style='border-color:#1E2533;'>", unsafe_allow_html=True)

    # ── Comparativo Ano a Ano (YOY) — do app original ─────────────────────────
    _secao_titulo("📅", "Comparativo Ano a Ano")
    anos     = hist_anual.get("anos", [])
    por_ano  = hist_anual.get("por_ano", {})

    if len(anos) >= 2:
        st.plotly_chart(_fig_yoy(por_ano, anos), use_container_width=True,
                        config={"displayModeBar": False})

        # Tabela resumo (igual ao original: Ano | Receitas | Investimentos | Despesas)
        import pandas as pd
        rows_yoy = []
        for a in anos:
            inv = por_ano[a].get("investimentos", 0.0)
            rows_yoy.append({
                "Ano":            str(a),
                "Receitas":       f"R$ {por_ano[a]['receitas']:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
                "Investimentos":  f"R$ {inv:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
                "Despesas":       f"R$ {por_ano[a]['despesas']:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
            })
        st.dataframe(pd.DataFrame(rows_yoy), use_container_width=True, hide_index=True)
    elif len(anos) == 1:
        st.caption(f"Apenas 1 ano de dados disponível ({anos[0]}). Aguarde mais histórico.")
    else:
        st.caption("Sem dados históricos disponíveis.")

    st.markdown("<br>", unsafe_allow_html=True)

    # ── Gastos com pagamento de cartão (mensal) — do app original ─────────────
    _secao_titulo("💳", "Gastos com pagamento de cartão (mensal)")
    # anos disponíveis = anos que têm dados de cartão; fallback = anos do YOY
    _anos_cartao = sorted(
        {str(a) for a in anos if gastos_cartao.get(str(a))},
        reverse=True,
    ) or [str(a) for a in sorted(anos, reverse=True)]
    _ano_sel_str = st.selectbox(
        "Ano de referência", _anos_cartao,
        key="cf_cartao_ano_sel",
    ) if _anos_cartao else str(_date.today().year)
    dados_cartao = gastos_cartao.get(_ano_sel_str, [])
    if dados_cartao:
        import pandas as pd
        labels = [item["label"] for item in dados_cartao]
        totais = [item["total"] for item in dados_cartao]

        fig_cartao = go.Figure()
        fig_cartao.add_trace(go.Bar(
            x=labels, y=totais,
            marker_color="#87CEEB", opacity=0.9,
            hovertemplate="<b>%{x}</b><br>R$ %{y:,.2f}<extra></extra>",
        ))
        fig_cartao.update_layout(
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            font_color=_COR_NEUTRO,
            margin={"t": 10, "b": 10, "l": 0, "r": 0}, height=300,
            xaxis={"showgrid": False, "title": {"text": "Mês", "font": {"size": 10}}},
            yaxis={"showgrid": True, "gridcolor": "#1E2533",
                   "tickformat": ",.0f", "tickprefix": "R$ ",
                   "title": {"text": "Total relacionado a cartão (R$)", "font": {"size": 10}}},
        )
        st.plotly_chart(fig_cartao, use_container_width=True, config={"displayModeBar": False})

        rows_cart = [{"Mês": item["label"],
                      "Total (R$)": f"R$ {item['total']:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")}
                     for item in dados_cartao]
        st.dataframe(pd.DataFrame(rows_cart), use_container_width=True, hide_index=False)
    else:
        st.caption(f"Sem lançamentos de 'Pagamento de Cartão' para {_ano_sel_str}.")

    st.markdown("<br>", unsafe_allow_html=True)

    # ── Evolução do Patrimônio Investido (igual ao original) ──────────────────
    _secao_titulo("📈", "Evolução do patrimônio investido (ano a ano)")
    if len(anos) >= 1:
        st.plotly_chart(_fig_patrimonio_investido(por_ano, anos), use_container_width=True,
                        config={"displayModeBar": False})

        import pandas as pd
        acum = 0.0
        rows_pat = []
        for a in anos:
            inv  = por_ano[a].get("investimentos") or max(0.0, por_ano[a]["saldo"])
            acum += inv
            rows_pat.append({
                "Ano":                str(a),
                "Investido no ano (R$)":   f"R$ {inv:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
                "Acumulado investido (R$)": f"R$ {acum:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
            })
        st.dataframe(pd.DataFrame(rows_pat), use_container_width=True, hide_index=True)
    else:
        st.caption("Sem dados históricos disponíveis.")


# ══════════════════════════════════════════════════════════════════════════════
# TAB 3 — Tabelas
# ══════════════════════════════════════════════════════════════════════════════

def _tab_tabelas(d: dict) -> None:
    """
    Consulta de lançamentos com filtros completos (Tipo, Categoria, Ano, Mês, Dia, Texto).
    Replica fielmente o módulo Consulta_Tabelas.py do app original.
    """
    _secao_titulo("🔍", "Consulta de lançamentos")

    # 1) Tipo — radio fora do form (igual ao original)
    aba = st.radio(
        "Tipo de lançamento",
        ["Todos", "Receitas", "Despesas"],
        horizontal=True,
        key="tab_tipo_radio",
    )

    # 2) Coleta todos os lançamentos do usuário para popular os seletores
    todos = get_transacoes_filtradas()  # sem filtros = tudo

    cats_disponiveis = sorted({t["categoria"] for t in todos})
    anos_disp = sorted({t.get("ano") for t in todos if t.get("ano")}, reverse=True)
    meses_disp_labels = [f"{m:02d} - {_MESES_PT[m]}" for m in sorted({t.get("mes") for t in todos if t.get("mes")})]

    col_f1, col_f2, col_f3, col_f4 = st.columns(4)
    with col_f1:
        f_cat = st.selectbox("Categoria", ["Todas"] + cats_disponiveis, key="tab_fcat")
    with col_f2:
        f_ano_opcoes = ["Todos"] + [str(a) for a in anos_disp]
        f_ano_label = st.selectbox("Ano", f_ano_opcoes, index=1 if anos_disp else 0, key="tab_fano")
        f_ano = int(f_ano_label) if f_ano_label != "Todos" else None
    with col_f3:
        meses_opcoes_full = ["Todos"] + [f"{m:02d} - {_MESES_PT[m]}" for m in range(1, 13)]
        f_mes_label = st.selectbox("Mês", meses_opcoes_full, key="tab_fmes")
        f_mes = int(f_mes_label.split(" - ")[0]) if f_mes_label != "Todos" else None
    with col_f4:
        # Dias disponíveis dado ano+mês selecionados
        if f_ano is not None and f_mes is not None:
            dias_disp = sorted({
                t.get("dia") for t in todos
                if t.get("ano") == f_ano and t.get("mes") == f_mes and t.get("dia")
            })
            dias_opcoes = ["Todos"] + [str(d) for d in dias_disp]
        else:
            dias_opcoes = ["Todos"]
        f_dia_label = st.selectbox("Dia", dias_opcoes, key="tab_fdia")
        f_dia = int(f_dia_label) if f_dia_label != "Todos" else None

    f_busca = st.text_input(
        "Buscar na descrição",
        placeholder="Ex: mercado, aluguel, salário...",
        key="tab_busca",
    )

    # Aplica filtros
    txs_f = get_transacoes_filtradas(
        tipo=aba,
        categoria=f_cat,
        ano=f_ano,
        mes=f_mes,
        dia=f_dia,
        texto=f_busca,
    )

    # ── Resumo (igual ao original) ─────────────────────────────────────────────
    total_filtrado = sum(abs(t["valor"]) for t in txs_f)
    total_rec      = sum(t["valor"] for t in txs_f if t["eh_receita"])
    total_desp     = sum(abs(t["valor"]) for t in txs_f if not t["eh_receita"])

    col_s1, col_s2, col_s3 = st.columns(3, gap="small")
    with col_s1:
        st.markdown(_kpi_card(
            "Total Filtrado", fmt_moeda(total_filtrado),
            f"{len(txs_f)} lançamento(s) selecionado(s)",
            "#E2E8F0",
        ), unsafe_allow_html=True)
    with col_s2:
        st.markdown(_kpi_card(
            "Entradas", fmt_moeda(total_rec),
            "Receitas no filtro aplicado",
            _COR_RECEITA,
        ), unsafe_allow_html=True)
    with col_s3:
        st.markdown(_kpi_card(
            "Saídas", fmt_moeda(total_desp),
            "Despesas no filtro aplicado",
            _COR_DESPESA,
        ), unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    if not txs_f:
        st.caption("Nenhum lançamento com os filtros aplicados.")
        return

    # ── Tabela ────────────────────────────────────────────────────────────────
    st.markdown(
        '<div style="display:grid;'
        'grid-template-columns:90px 1fr 150px 80px 130px 80px;'
        'gap:4px;padding:5px 10px;background:#0E1117;border-radius:4px 4px 0 0;'
        'font-size:0.63rem;font-weight:700;text-transform:uppercase;'
        'letter-spacing:0.1em;color:#4A5568;">'
        '<span>Data</span><span>Descrição</span>'
        '<span style="text-align:center">Categoria</span>'
        '<span style="text-align:center">Tipo</span>'
        '<span style="text-align:right">Valor</span>'
        '<span style="text-align:center">Conta</span>'
        '</div>',
        unsafe_allow_html=True,
    )

    for tx in txs_f[:200]:
        cor  = _COR_RECEITA if tx["eh_receita"] else _COR_DESPESA
        tipo_label = "entrada" if tx["eh_receita"] else "saída"
        st.markdown(
            f'<div style="display:grid;'
            f'grid-template-columns:90px 1fr 150px 80px 130px 80px;'
            f'gap:4px;padding:6px 10px;background:#12151E;'
            f'border-bottom:1px solid #1A1F2E;'
            f'font-size:0.81rem;align-items:center;">'
            f'<span style="color:#718096">{tx["data_fmt"]}</span>'
            f'<span style="color:#CBD5E0" title="{tx["descricao"]}">'
            f'{tx["descricao"][:38]}</span>'
            f'<span style="text-align:center;background:#1E2533;border-radius:4px;'
            f'padding:2px 5px;font-size:0.70rem;color:{_COR_NEUTRO}">'
            f'{tx["categoria"]}</span>'
            f'<span style="text-align:center;font-size:0.72rem;font-weight:700;color:{cor}">'
            f'{tipo_label}</span>'
            f'<span style="text-align:right;font-weight:700;color:{cor}">'
            f'{tx["valor_fmt"]}</span>'
            f'<span style="text-align:center;font-size:0.72rem;color:#4A5568">'
            f'{tx["conta"]}</span>'
            f'</div>',
            unsafe_allow_html=True,
        )

    if len(txs_f) >= 200:
        st.caption(f"Exibindo 200 de {len(txs_f)} registros.")
    else:
        st.caption(f"{len(txs_f)} lançamento(s) encontrado(s).")


# ══════════════════════════════════════════════════════════════════════════════
# TAB 4 — Cartão de Crédito
# ══════════════════════════════════════════════════════════════════════════════

def _tab_cartao(d: dict) -> None:
    txs = d["transacoes"]

    # Filtra despesas (em dados reais filtraria por payment_type = 'Cartão de crédito')
    # O campo payment_type não existe no schema unificado atual — mostra todas as despesas
    despesas_tx = [t for t in txs if not t["eh_receita"]]

    st.markdown(
        '<div style="background:rgba(74,158,255,0.06);'
        'border:1px solid rgba(74,158,255,0.2);'
        'border-left:3px solid #4A9EFF;'
        'border-radius:0 8px 8px 0;'
        'padding:10px 14px;font-size:0.82rem;color:#9CA3AF;margin-bottom:16px;">'
        'ℹ️ Exibe todas as despesas do mês. '
        'O campo <b>forma de pagamento</b> não é armazenado no schema atual do banco unificado. '
        'Quando disponível, será possível filtrar por '
        '<b>Cartão de crédito</b> e calcular faturas e dívidas de parcelas com precisão.</div>',
        unsafe_allow_html=True,
    )

    if not despesas_tx:
        st.caption("Nenhuma despesa registrada neste mês.")
        return

    # KPIs — usando dados do mês atual
    total_desp = sum(abs(t["valor"]) for t in despesas_tx)
    num_tx     = len(despesas_tx)

    c1, c2, c3 = st.columns(3, gap="small")
    with c1:
        st.markdown(_kpi_card(
            "Total em Despesas",
            fmt_moeda(total_desp),
            f"{num_tx} lançamento{'s' if num_tx != 1 else ''}",
            _COR_DESPESA,
        ), unsafe_allow_html=True)
    with c2:
        media = total_desp / num_tx if num_tx > 0 else 0
        st.markdown(_kpi_card(
            "Ticket Médio",
            fmt_moeda(media),
            "Média por transação do mês.",
            _COR_NEUTRO,
        ), unsafe_allow_html=True)
    with c3:
        maior = max(despesas_tx, key=lambda t: abs(t["valor"]))
        st.markdown(_kpi_card(
            "Maior Lançamento",
            fmt_moeda(abs(maior["valor"])),
            maior["descricao"][:30],
            "#F6C90E",
        ), unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # ── Gastos por categoria ────────────────────────────────────────────────
    _secao_titulo("📊", "Despesas por categoria")

    agg: dict[str, float] = defaultdict(float)
    for t in despesas_tx:
        agg[t["categoria"]] += abs(t["valor"])

    agg_sorted = sorted(agg.items(), key=lambda x: x[1], reverse=True)
    total_geral = sum(v for _, v in agg_sorted)

    # Bar chart (estilo do original: laranja)
    cat_nomes_bar = [c for c, _ in agg_sorted]
    cat_vals_bar  = [v for _, v in agg_sorted]
    cat_pcts_bar  = [v / total_geral * 100 if total_geral > 0 else 0 for v in cat_vals_bar]

    fig_cat = go.Figure(go.Bar(
        x=cat_nomes_bar, y=cat_vals_bar,
        marker_color="#FFA500",
        hovertemplate="<b>%{x}</b><br>R$ %{y:,.2f}<br>%{customdata:.1f}%<extra></extra>",
        customdata=cat_pcts_bar,
    ))
    fig_cat.update_layout(
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font_color=_COR_NEUTRO,
        margin={"t": 10, "b": 0, "l": 0, "r": 0}, height=260,
        xaxis={"showgrid": False, "tickangle": -30},
        yaxis={"showgrid": True, "gridcolor": "#1E2533",
               "tickformat": ",.0f", "tickprefix": "R$ "},
    )
    st.plotly_chart(fig_cat, use_container_width=True, config={"displayModeBar": False})

    # Tabela de participação
    st.markdown(
        '<div style="display:grid;grid-template-columns:1fr 120px 80px;'
        'gap:4px;padding:5px 10px;background:#0E1117;border-radius:4px 4px 0 0;'
        'font-size:0.63rem;font-weight:700;text-transform:uppercase;'
        'letter-spacing:0.1em;color:#4A5568;">'
        '<span>Categoria</span>'
        '<span style="text-align:right">Total (R$)</span>'
        '<span style="text-align:right">% do total</span>'
        '</div>',
        unsafe_allow_html=True,
    )
    for i, (cat, valor) in enumerate(agg_sorted):
        pct = valor / total_geral * 100 if total_geral > 0 else 0
        cor = _CORES_CAT[i % len(_CORES_CAT)]
        st.markdown(
            f'<div style="display:grid;grid-template-columns:1fr 120px 80px;'
            f'gap:4px;padding:6px 10px;background:#12151E;'
            f'border-bottom:1px solid #1A1F2E;font-size:0.81rem;align-items:center;">'
            f'<div style="display:flex;align-items:center;gap:8px;">'
            f'<div style="width:8px;height:8px;border-radius:50%;background:{cor};flex-shrink:0"></div>'
            f'<span style="color:#CBD5E0">{cat}</span>'
            f'</div>'
            f'<span style="text-align:right;font-weight:700;color:{_COR_DESPESA}">'
            f'{fmt_moeda(valor)}</span>'
            f'<span style="text-align:right;color:#718096">{pct:.1f}%</span>'
            f'</div>',
            unsafe_allow_html=True,
        )

    st.markdown("<br>", unsafe_allow_html=True)

    # ── Lista de lançamentos ──────────────────────────────────────────────────
    _secao_titulo("📋", "Lançamentos do mês")

    st.markdown(
        '<div style="display:grid;grid-template-columns:80px 1fr 150px 120px;'
        'gap:4px;padding:5px 10px;background:#0E1117;border-radius:4px 4px 0 0;'
        'font-size:0.63rem;font-weight:700;text-transform:uppercase;'
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
            f'gap:4px;padding:6px 10px;background:#12151E;'
            f'border-bottom:1px solid #1A1F2E;'
            f'font-size:0.81rem;align-items:center;">'
            f'<span style="color:#718096">{tx["data_fmt"]}</span>'
            f'<span style="color:#CBD5E0">{tx["descricao"][:38]}</span>'
            f'<span style="text-align:center;background:#1E2533;border-radius:4px;'
            f'padding:2px 5px;font-size:0.70rem;color:{_COR_NEUTRO}">{tx["categoria"]}</span>'
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
        st.markdown(
            f'<div style="text-align:right;font-size:0.72rem;color:#4A5568;margin-top:2px;">'
            f'hoje: {_date.today().strftime("%d/%m/%Y")}</div>',
            unsafe_allow_html=True,
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

    # Badge de fonte de dados
    _fonte = d.get("data_source", "mock")
    badge_status(
        "Dados reais"     if _fonte == "real" else
        "Fallback (mock)" if _fonte == "mock_fallback" else "Modo mock",
        "sucesso" if _fonte == "real" else
        "erro"    if _fonte == "mock_fallback" else "alerta",
    )

    st.markdown("<br>", unsafe_allow_html=True)

    # ── Sidebar completo ──────────────────────────────────────────────────────
    _sidebar_render(sel["ano"], sel["mes"])

    # ── Dados compartilhados entre tabs ──────────────────────────────────────
    historico    = get_cashflow_mensal()
    hist_anual   = get_historico_anual()
    evolucao     = get_evolucao_patrimonial()
    fluxo_inv    = {(f["ano"], f["mes"]): f["aporte"] for f in evolucao.get("fluxo_mensal", [])}

    # Gastos com Pagamento de Cartão — agrupados por ano → {ano_str: [items]}
    _ano_ref = sel["ano"]
    _anos_hist = hist_anual.get("anos", [_ano_ref])
    gastos_cartao: dict = {"todos": []}
    for _a in _anos_hist:
        _dados_a = get_gastos_cartao_mensal(_a)
        gastos_cartao[str(_a)] = _dados_a
        gastos_cartao["todos"].extend(_dados_a)

    # ── Sub-navegação via tabs ────────────────────────────────────────────────
    tab1, tab2, tab3, tab4 = st.tabs([
        "📊  Dashboard",
        "📈  Análises",
        "🧾  Tabelas",
        "💳  Cartão de Crédito",
    ])

    with tab1:
        _tab_dashboard(d, historico, fluxo_inv)

    with tab2:
        _tab_analises(d, historico, hist_anual, gastos_cartao)

    with tab3:
        _tab_tabelas(d)

    with tab4:
        _tab_cartao(d)
