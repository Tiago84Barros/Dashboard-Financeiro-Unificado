"""
pages/controle_financeiro.py  — v4 (preservação fiel do app original)

Replica FIELMENTE as 4 seções do app original controlefinanceirotsb.streamlit.app:
  Sidebar  — Filtros (mês de referência) + Novo Lançamento de conta
               (Tipo: entrada|saída|investimento, Categoria, Data, Valor,
                Descrição, Salvar)
  Tabs     — Dashboard | Análises | Tabelas | Cartão de Crédito

Adições do app unificado preservadas (não existiam no original):
  - Pizza de despesas na aba Análises
  - Orçamento vs Realizado (overlay)
  - Barras de progresso por categoria
  - (Taxa de poupança mensal histórica removida)

Novas funcionalidades implementadas na Fase 5.1:
  - Dashboard: seção "Últimos Lançamentos" com modo leitura + edição
  - Análises: Comparativo Ano a Ano (YOY)
  - Análises: Evolução do Patrimônio Investido (ano a ano)
  - Tabelas: filtros por Tipo/Categoria/Ano/Mês/Dia/texto + totais
  - Cartão: filtro por payment_type quando dados disponíveis

Dados: core/controle + core/investimentos.get_cashflow_mensal()
"""
from datetime import date as _date, timedelta
import html
import re
import unicodedata

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from core.controle import (
    get_controle, get_opcoes_formulario, inserir_transacao,
    atualizar_transacao, atualizar_transacao_cartao,
    get_contas_cartao_credito,
    get_historico_anual, get_transacoes_filtradas,
    get_gastos_cartao_mensal,
    get_gastos_categoria_anual,
    get_transacoes_cartao_credito,
    definir_categoria_transacao_cartao, add_card_category_rule,
)
from core.card_categorization import categorias_disponiveis, REVIEW_SENTINEL
from core.investimentos import get_cashflow_mensal, get_evolucao_patrimonial
from core.utils import fmt_moeda, fmt_percentual
from design.componentes import badge_status, barra_progresso, container_pagina

# Chat "Analista Financeiro Pessoal" (aba Análises) — importado localmente na
# função de render para não pesar no carregamento das demais abas/reruns.

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

_FORMAS_PGTO_SAIDA = ["Conta"]
_FORMAS_PGTO_TODOS = ["Conta"]
_MANUAL_CARD_TERMS = ("cartao", "credito", "fatura")
# Categoria de transferência permitida no lançamento manual (pagamento mensal da
# fatura a partir da conta) — não é consumo de cartão, então é isenta do bloqueio.
_MANUAL_CARD_ALLOWED = {"pagamento de cartao"}
_CC_IMPORTED_SOURCES = {"csv"}

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
    "Renda Fixa", "Renda Variável", "Exterior", "Reserva de Despesa", "Outros",
]

_CORES_CAT = [
    "#FC5C7D", "#F6C90E", "#4A9EFF", "#00C896", "#9B59B6",
    "#FF6B35", "#1ABC9C", "#E67E22", "#3498DB", "#E91E63",
]


def _tipo_tx_label(tx: dict) -> str:
    return tx.get("tipo_label") or ("entrada" if tx.get("eh_receita") else "saída")


def _cor_tx(tx: dict) -> str:
    tipo = tx.get("tipo_fluxo")
    if tipo == "income":
        return _COR_RECEITA
    if tipo == "investment":
        return _COR_INVEST
    if tipo == "transfer":
        return _COR_NEUTRO
    return _COR_DESPESA


def _norm_ascii(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    return text.encode("ascii", "ignore").decode("ascii").casefold()


def _is_manual_card_related_text(value: object) -> bool:
    """True para texto relacionado a cartao/fatura no lancamento manual.

    A categoria de pagamento mensal da fatura ("Pagamento de Cartão") é permitida:
    representa a transferência da conta que quita a fatura, não o consumo do cartão.
    """
    text = _norm_ascii(value)
    if text in _MANUAL_CARD_ALLOWED:
        return False
    return any(term in text for term in _MANUAL_CARD_TERMS)


def _is_credit_card_invoice_source(value: object) -> bool:
    return str(value or "").strip().casefold() in _CC_IMPORTED_SOURCES


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


def _fig_historico(historico: list, fluxo_inv: dict | None = None) -> go.Figure:
    h6 = historico[-6:] if len(historico) >= 6 else historico
    meses         = [h["label"]    for h in h6]
    receitas      = [h["receitas"] for h in h6]
    despesas      = [h["despesas"] for h in h6]
    investimentos = [h.get("investimentos", 0.0) for h in h6]

    fig = go.Figure()
    for nome, vals, cor, dash in [
        ("Receitas",      receitas,      _COR_RECEITA, "solid"),
        ("Despesas",      despesas,      _COR_DESPESA, "solid"),
        ("Investimentos", investimentos, _COR_INVEST,  "solid"),
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

    # 2) Forma de pagamento fixa: lançamentos manuais são sempre de conta.
    if st.session_state.get("cf_sb_forma") != "Conta":
        st.session_state["cf_sb_forma"] = "Conta"
    st.sidebar.selectbox(
        "Forma de pagamento",
        _FORMAS_PGTO_SAIDA,
        key="cf_sb_forma",
        disabled=True,
        help="Compras de cartão de crédito entram somente pelo upload da fatura CSV.",
    )

    # 3) FORMULÁRIO (limpa após salvar)
    with st.sidebar.form("form_nova_tx", clear_on_submit=True):

        # Categorias pré-definidas por tipo (mais opções do DB como fallback)
        if t_type == "entrada":
            cat_preset = _CAT_ENTRADA
        elif t_type == "saida":
            cat_preset = _CAT_SAIDA
        else:
            cat_preset = _CAT_INVESTIMENTO

        cat_idx = st.selectbox(
            "Categoria",
            range(len(cat_preset)),
            format_func=lambda i: cat_preset[i],
            key="cf_sb_cat",
        )
        cat_escolhida = cat_preset[cat_idx]

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

        descricao = st.text_area(
            "Descrição (opcional)", height=60, key="cf_sb_desc",
        )

        submitted = st.form_submit_button("Salvar lançamento", use_container_width=True)

    if submitted:
        if valor <= 0:
            st.sidebar.error("Informe um valor maior que zero.")
            return

        categoria_final = cat_escolhida
        if not categoria_final:
            st.sidebar.error("Informe a categoria.")
            return
        if _is_manual_card_related_text(categoria_final) or _is_manual_card_related_text(descricao):
            st.sidebar.error("Cartão de crédito deve ser lançado somente por upload da fatura CSV.")
            return

        # Resolve conta de movimentação (fluxo de caixa). Exclui cartão e prioriza
        # a conta corrente (checking): sem isso, cai na 1ª conta em ordem alfabética
        # — que pode ser uma conta de investimento (ex.: "B3 - Carteira Consolidada")
        # e não tem nada a ver com o fluxo de caixa manual.
        opcoes  = get_opcoes_formulario()
        def _tipo_conta(c: dict) -> str:
            return (c.get("tipo") or c.get("type") or "").strip().lower()
        contas   = [c for c in opcoes.get("contas", []) if _tipo_conta(c) != "credit_card"]
        conta_id = next(
            (c["id"] for c in contas if _tipo_conta(c) == "checking"),
            contas[0]["id"] if contas else None,
        )
        if not conta_id:
            st.sidebar.warning("Nenhuma conta de movimentação configurada.")
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

def _tab_dashboard(d: dict, historico: list, fluxo_inv: dict,
                   investido_mes: float = 0.0) -> None:
    receitas     = d["receitas"]
    despesas     = d["despesas"]
    # Saldo = receitas - despesas - investimentos (alinhado com isolado)
    saldo        = round(receitas - despesas - investido_mes, 2)
    # Comprometida = (despesas + investimentos) / receitas (alinhado com isolado)
    comprometido = round((despesas + investido_mes) / receitas * 100, 1) if receitas > 0 else 0.0
    cor_saldo    = _COR_RECEITA if saldo >= 0 else _COR_DESPESA
    cor_comp     = (
        _COR_RECEITA if comprometido < 60 else
        "#F6C90E"    if comprometido < 80 else
        _COR_DESPESA
    )

    desc_saldo = f"{'Sobrou' if saldo >= 0 else 'Déficit'} dinheiro este mês."
    if investido_mes > 0:
        desc_saldo += f" Investido no mês: {fmt_moeda(investido_mes)}"

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
            desc_saldo,
            cor_saldo,
        ), unsafe_allow_html=True)
    with c4:
        st.markdown(_kpi_card(
            "Renda Comprometida",
            fmt_percentual(comprometido, sinal=False),
            "Considera despesas + investimentos em relação à renda do mês.",
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
        _secao_titulo("📈", "Histórico de 6 meses (Receitas × Despesas × Investimentos)")
        if historico:
            st.plotly_chart(_fig_historico(historico, fluxo_inv),
                            use_container_width=True,
                            config={"displayModeBar": False})
        else:
            st.caption("Histórico não disponível.")

        # ── Tabela resumo 6 meses ─────────────────────────────────────────────
        ultimos6 = historico[-6:] if len(historico) >= 6 else historico
        if ultimos6:
            import pandas as pd
            rows_t = []
            for h in ultimos6:
                inv = h.get("investimentos", 0.0)
                rows_t.append({
                    "Mês":           h["label"],
                    "Receitas":      fmt_moeda(h["receitas"]),
                    "Despesas":      fmt_moeda(h["despesas"]),
                    "Investimentos": fmt_moeda(inv),
                })
            df_hist = pd.DataFrame(rows_t)
            st.dataframe(
                df_hist,
                column_config={
                    "Mês":           st.column_config.TextColumn("Mês",           width="small"),
                    "Receitas":      st.column_config.TextColumn("Receitas"),
                    "Despesas":      st.column_config.TextColumn("Despesas"),
                    "Investimentos": st.column_config.TextColumn("Investimentos"),
                },
                hide_index=True,
                use_container_width=True,
            )

    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown("<hr style='border-color:#1E2533;'>", unsafe_allow_html=True)

    # ── Últimos Lançamentos (igual ao original) ───────────────────────────────
    _secao_titulo("📋", "Últimos Lançamentos")

    # Apenas lançamentos inseridos manualmente (source='manual'). Faturas do
    # cartão (source='csv') e extrato bancário (source='import') não entram aqui —
    # vivem nas suas próprias abas e não são fluxo de caixa manual do mês.
    txs = [t for t in d["transacoes"] if (t.get("source") or "manual") == "manual"]
    if not txs:
        st.caption("Nenhum lançamento manual cadastrado ainda.")
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
            cor = _cor_tx(tx)
            tipo_label = _tipo_tx_label(tx)
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
        contas_db = opcoes.get("contas", [])
        cat_nomes = [c["nome"] for c in cats_db]
        conta_nomes = [c["nome"] for c in contas_db]

        # Prepara DataFrame para edição
        import pandas as pd
        rows_edit = []
        for tx in txs[:30]:
            rows_edit.append({
                "ID":        tx["id"],
                "Tipo":      _tipo_tx_label(tx),
                "Categoria": tx["categoria"],
                "Data":      tx["data"],
                "Valor":     abs(tx["valor"]),
                "Descrição": tx["descricao"],
                "Conta":     tx["conta"],
            })
        df_edit = pd.DataFrame(rows_edit)

        with st.form("form_editor_lancamentos", clear_on_submit=False):
            edited = st.data_editor(
                df_edit,
                num_rows="fixed",
                hide_index=True,
                key="editor_lancamentos",
                column_config={
                    "ID":      None,   # oculto na edição (preservado para o save)
                    "Tipo": st.column_config.SelectboxColumn(
                        "Tipo",
                        options=["entrada", "saída", "investimento", "transferência"],
                    ),
                    "Conta": st.column_config.SelectboxColumn(
                        "Conta",
                        options=conta_nomes if conta_nomes else ["Sem conta"],
                    ),
                    "Categoria": st.column_config.SelectboxColumn(
                        "Categoria",
                        options=cat_nomes if cat_nomes else ["Sem categoria"],
                    ),
                    "Data":    st.column_config.DateColumn("Data"),
                    "Valor":   st.column_config.NumberColumn("Valor (R$)", format="%.2f", step=0.01),
                    "Descrição": st.column_config.TextColumn("Descrição"),
                },
            )
            salvar_edicoes = st.form_submit_button("Salvar alterações", type="primary")

        if salvar_edicoes:
            erros = []
            ok_count = 0
            for i, row in edited.iterrows():
                orig = df_edit.iloc[i]
                row_data = pd.to_datetime(row["Data"]).date() if pd.notna(row["Data"]) else None
                orig_data = pd.to_datetime(orig["Data"]).date() if pd.notna(orig["Data"]) else None
                campos_mudaram = (
                    row["Descrição"] != orig["Descrição"]
                    or row["Categoria"] != orig["Categoria"]
                    or row["Tipo"] != orig["Tipo"]
                    or row["Conta"] != orig["Conta"]
                    or abs(row["Valor"] - orig["Valor"]) > 0.001
                    or row_data != orig_data
                )
                if campos_mudaram:
                    # Resolve category_id
                    cat_m = next((c for c in cats_db if c["nome"] == row["Categoria"]), None)
                    cat_id = cat_m["id"] if cat_m else None
                    conta_m = next((c for c in contas_db if c["nome"] == row["Conta"]), None)
                    if not conta_m:
                        erros.append(f"ID {row['ID']}: conta inválida ou não encontrada.")
                        continue
                    tipo_tx = {
                        "entrada": "income",
                        "saída": "expense",
                        "investimento": "investment",
                        "transferência": "transfer",
                    }.get(row["Tipo"], "expense")
                    sinal = 1.0 if tipo_tx in ("income", "transfer") else -1.0
                    ok, msg = atualizar_transacao(
                        tx_id=str(row["ID"]),
                        descricao=str(row["Descrição"]),
                        valor=sinal * abs(float(row["Valor"])),
                        data=row_data,
                        categoria_id=cat_id,
                        conta_id=conta_m["id"],
                        tipo=tipo_tx,
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

def _fig_barras_categoria_anual(cats: list) -> go.Figure:
    """Barras horizontais de gastos anuais por categoria (ordem decrescente)."""
    cats_ord = sorted(cats, key=lambda c: c["gasto"], reverse=True)
    nomes  = [c["nome"]  for c in cats_ord]
    gastos = [c["gasto"] for c in cats_ord]
    total  = sum(gastos) or 1
    pcts   = [round(g / total * 100, 1) for g in gastos]
    cores  = _CORES_CAT[:len(cats_ord)]
    fig = go.Figure(go.Bar(
        x=gastos, y=nomes, orientation="h",
        marker_color=cores, opacity=0.88,
        hovertemplate=(
            "<b>%{y}</b><br>R$ %{x:,.2f}"
            "<br>%{customdata:.1f}% do total<extra></extra>"
        ),
        customdata=pcts,
    ))
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font_color=_COR_NEUTRO,
        margin={"t": 10, "b": 10, "l": 0, "r": 10}, height=max(240, len(cats_ord) * 28),
        xaxis={"showgrid": True, "gridcolor": "#1E2533",
               "tickformat": ",.0f", "tickprefix": "R$ "},
        yaxis={"showgrid": False, "autorange": "reversed"},
    )
    return fig


def _tab_analises(
    d: dict, historico: list, hist_anual: dict,
    gastos_cartao: dict, investido_mes: float = 0.0,
    evolucao: dict | None = None, ano_ref: int | None = None,
    mes_ref: int | None = None,
) -> None:
    receitas = d["receitas"]
    despesas = d["despesas"]
    cats     = d["categorias"]

    # saldo e taxa de poupança subtraem investimentos (igual ao isolado)
    saldo         = round(receitas - despesas - investido_mes, 2)
    taxa_poupanca = round((receitas - despesas - investido_mes) / receitas * 100, 1) \
                    if receitas > 0 else 0.0
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
            "Receitas − Despesas − Investimentos no período selecionado.",
            _COR_RECEITA if saldo >= 0 else _COR_DESPESA,
        ), unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # ── Distribuição de despesas anual ────────────────────────────────────────
    _secao_titulo("🍕", "Distribuição de despesas por categoria (anual)")
    anos_disp = hist_anual.get("anos", [_date.today().year])
    _ano_dist = st.selectbox(
        "Ano de referência",
        sorted(anos_disp, reverse=True),
        key="cf_dist_ano_sel",
    )
    cats_anuais = get_gastos_categoria_anual(_ano_dist)
    if cats_anuais:
        col_pizza, col_barras = st.columns(2, gap="medium")
        with col_pizza:
            _pizza_cats = [{"nome": c["nome"], "gasto": c["gasto"],
                            "orcamento": 0.0, "pct_usado": 0.0, "tipo_badge": ""}
                           for c in cats_anuais]
            st.plotly_chart(_fig_pizza_cats(_pizza_cats), use_container_width=True,
                            config={"displayModeBar": False})
        with col_barras:
            st.plotly_chart(_fig_barras_categoria_anual(cats_anuais),
                            use_container_width=True, config={"displayModeBar": False})

        import pandas as pd
        total_anual = sum(c["gasto"] for c in cats_anuais)
        rows_dist = [
            {
                "Categoria": c["nome"],
                "Gasto (R$)": f"R$ {c['gasto']:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
                "% do total": f"{c['gasto'] / total_anual * 100:.1f}%",
            }
            for c in cats_anuais
        ]
        st.dataframe(pd.DataFrame(rows_dist), use_container_width=True, hide_index=True)
    else:
        st.caption(f"Sem despesas registradas em {_ano_dist}.")

    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown("<hr style='border-color:#1E2533;'>", unsafe_allow_html=True)

    # ── Comparativo Ano a Ano (YOY) — do app original ─────────────────────────
    _secao_titulo("📅", "Comparativo Ano a Ano")
    if hist_anual.get("data_source") == "real_error":
        st.warning(
            "Não foi possível carregar o histórico real do banco — os dados de "
            "demonstração foram desativados para não exibir valores fictícios.\n\n"
            f"Detalhe técnico: {hist_anual.get('error', 'erro desconhecido')}"
        )
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

    # ── Gastos com pagamento de cartão (mensal) — lançamentos manuais ─────────
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
        # Densifica o eixo: mostra todos os meses do ano (jan → dez, ou jan → mês
        # atual no ano corrente) com R$ 0 onde não houve lançamento manual, dando
        # continuidade visual sem puxar dados do CSV (fluxo futuro).
        _por_mes = {int(item["mes"]): float(item["total"]) for item in dados_cartao}
        _ano_int = int(_ano_sel_str)
        _mes_fim = (_date.today().month
                    if _ano_int == _date.today().year else 12)
        _mes_fim = max(_mes_fim, max(_por_mes) if _por_mes else 1)
        dados_cartao = [
            {"mes": m, "label": f"{m:02d}/{_ano_int}", "total": round(_por_mes.get(m, 0.0), 2)}
            for m in range(1, _mes_fim + 1)
        ]
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

    # ── Analista Financeiro Pessoal (chat com IA) ─────────────────────────────
    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown("<hr style='border-color:#1E2533;'>", unsafe_allow_html=True)
    _render_chat_financeiro(
        d, historico, hist_anual, gastos_cartao, investido_mes,
        evolucao or {}, ano_ref or _date.today().year, mes_ref or _date.today().month,
    )


def _aviso_ancoragem(resposta: str, contexto: str) -> str:
    """Alerta quando a IA cita número que não se ancora nos dados enviados.

    Auditoria 2026-07 (§12.8): os prompts proíbem inventar valores, mas nada
    verificava a saída. A checagem é determinística e conservadora — só avisa,
    nunca esconde a resposta, e ignora números derivados do próprio contexto.
    """
    try:
        from core.llm_grounding import check_grounding
        relatorio = check_grounding(resposta or "", contexto or "")
    except Exception:                      # verificação nunca derruba o chat
        return ""
    if not relatorio.ungrounded:
        return ""
    citados = ", ".join(claim.raw for claim in relatorio.ungrounded[:4])
    return (f"⚠️ Confira antes de usar: {citados} — "
            f"{'este valor não foi encontrado' if len(relatorio.ungrounded) == 1 else 'estes valores não foram encontrados'} "
            "nos dados enviados à IA.")


def _render_chat_financeiro(
    d: dict, historico: list, hist_anual: dict, gastos_cartao: dict,
    investido_mes: float, evolucao: dict, ano_ref: int, mes_ref: int,
) -> None:
    """
    Chat "Analista Financeiro Pessoal": conversa em linguagem natural sobre os
    dados REAIS do usuário (mesmas funções filtradas por OWNER_USER_ID). Mantém
    o histórico da sessão e pode gerar gráficos a partir das séries reais.
    """
    from core.llm_b3 import llm_disponivel, provedores_disponiveis
    from core.llm_financeiro import chat_com_financas, parse_chart_directives
    from core.llm_context_financeiro import build_financas_chat_context
    from core.financeiro_chat_charts import (
        render_financas_charts, infer_financas_chart_directives,
    )

    _secao_titulo("🤖", "Analista Financeiro Pessoal (IA)")
    st.markdown(
        '<p style="color:#718096;font-size:0.82rem;margin-top:2px;margin-bottom:12px;">'
        'Converse em linguagem natural sobre suas receitas, despesas, investimentos, '
        'saldo e fluxo de caixa. A IA usa <b style="color:#E2E8F0">apenas os seus dados</b> '
        '(mês selecionado, histórico mensal e anual, categorias e cartão), mostra os '
        'cálculos e sinaliza quando algo é estimativa ou projeção.</p>',
        unsafe_allow_html=True,
    )

    if not llm_disponivel():
        st.info(
            "IA indisponível: nenhum provedor LLM configurado. Defina `OPENAI_API_KEY` "
            "e/ou `GEMINI_API_KEY` no `.env` local ou em Streamlit Secrets."
        )
        return

    provider_labels = {"openai": "OpenAI", "gemini": "Gemini"}
    st.caption("Provedor(es): " + ", ".join(
        provider_labels.get(p, p) for p in provedores_disponiveis()))

    # Reinicia o histórico quando o mês selecionado muda (o contexto muda junto).
    _ctx_sig = f"{ano_ref}-{mes_ref}-{d.get('data_source')}"
    if st.session_state.get("cf_chat_ctx_sig") not in (None, _ctx_sig):
        st.session_state.pop("cf_chat_history", None)
    st.session_state["cf_chat_ctx_sig"] = _ctx_sig

    # Sugestões de perguntas iniciais
    suggestions = [
        "Quais categorias mais comprometem minha renda?",
        "Onde posso reduzir gastos sem afetar despesas essenciais?",
        "Quanto posso investir mensalmente mantendo o orçamento equilibrado?",
        "Projete meu saldo para os próximos seis meses.",
        "Compare meus gastos dos últimos três meses.",
        "Simule o impacto de uma redução de 15% nas despesas não essenciais.",
    ]
    suggested_input = None
    _sug_cols = st.columns(3)
    for i, q in enumerate(suggestions):
        with _sug_cols[i % 3]:
            if st.button(q, key=f"cf_chat_sug_{i}", use_container_width=True):
                suggested_input = q

    _, _clr = st.columns([5, 1])
    with _clr:
        if st.button("🗑️ Limpar", key="cf_chat_clear", use_container_width=True):
            st.session_state.pop("cf_chat_history", None)
            st.rerun()

    history: list[dict] = st.session_state.get("cf_chat_history", [])
    for msg in history:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
            for direc in msg.get("_charts", []) or []:
                try:
                    render_financas_charts([direc], msg.get("_chart_meta", {}))
                except Exception:
                    pass

    # O st.chat_input é SEMPRE renderizado (nunca em short-circuit): se ficasse
    # atrás de `suggested_input or ...`, clicar numa sugestão pulava a renderização
    # do campo e a caixa de digitar sumia até o próximo rerun.
    typed_input = st.chat_input("Pergunte sobre suas finanças…", key="cf_chat_input")
    user_input = suggested_input or typed_input
    if not user_input:
        return

    history.append({"role": "user", "content": user_input})
    with st.chat_message("user"):
        st.markdown(user_input)

    with st.chat_message("assistant"):
        chart_directives: list[dict] = []
        chart_meta: dict = {}
        aviso_ancoragem = ""
        with st.spinner("Analisando seus dados financeiros…"):
            try:
                cats_anual = get_gastos_categoria_anual(ano_ref)
                context, chart_meta = build_financas_chat_context(
                    user_question=user_input,
                    dados_mes=d,
                    historico=historico,
                    hist_anual=hist_anual,
                    gastos_categoria_anual=cats_anual,
                    gastos_cartao=gastos_cartao,
                    evolucao=evolucao,
                    investido_mes=investido_mes,
                    ano_ref=ano_ref,
                    mes_ref=mes_ref,
                )
                resposta_raw = chat_com_financas(context, history[:-1], user_input)
                resposta, chart_directives = parse_chart_directives(resposta_raw)
                if not chart_directives:
                    chart_directives = infer_financas_chart_directives(user_input, chart_meta)
                aviso_ancoragem = _aviso_ancoragem(resposta, context)
            except Exception as exc:
                resposta = f"Não foi possível consultar a IA agora: {exc}"
        st.markdown(resposta)
        if aviso_ancoragem:
            st.caption(aviso_ancoragem)
        desenhados = 0
        if chart_directives:
            try:
                desenhados = render_financas_charts(chart_directives, chart_meta)
            except Exception as exc:
                st.caption(f"⚠️ Não foi possível gerar os gráficos: {exc}")
        st.caption("Análise educacional baseada nos seus dados; não é recomendação "
                   "de investimento nem garantia de resultado.")

    msg_assistant = {"role": "assistant", "content": resposta}
    if desenhados and chart_directives:
        msg_assistant["_charts"] = chart_directives[:2]
        msg_assistant["_chart_meta"] = chart_meta
    history.append(msg_assistant)
    st.session_state["cf_chat_history"] = history


# ══════════════════════════════════════════════════════════════════════════════
# TAB 3 — Tabelas
# ══════════════════════════════════════════════════════════════════════════════

def _editor_lancamentos(txs: list, form_key: str, editor_key: str, limit: int = 200) -> None:
    """Editor in-place de lançamentos: data_editor + gravação via atualizar_transacao.
    Grava apenas as linhas alteradas. Mesmo padrão dos Últimos Lançamentos."""
    opcoes      = get_opcoes_formulario()
    cats_db     = opcoes.get("categorias", [])
    contas_db   = opcoes.get("contas", [])
    cat_nomes   = [c["nome"] for c in cats_db]
    conta_nomes = [c["nome"] for c in contas_db]

    rows_edit = [
        {
            "ID":        tx["id"],
            "Tipo":      _tipo_tx_label(tx),
            "Categoria": tx["categoria"],
            "Data":      tx["data"],
            "Valor":     abs(tx["valor"]),
            "Descrição": tx["descricao"],
            "Conta":     tx["conta"],
        }
        for tx in txs[:limit]
    ]
    df_edit = pd.DataFrame(rows_edit)

    with st.form(form_key, clear_on_submit=False):
        edited = st.data_editor(
            df_edit,
            num_rows="fixed",
            hide_index=True,
            use_container_width=True,
            key=editor_key,
            column_config={
                "ID": None,
                "Tipo": st.column_config.SelectboxColumn(
                    "Tipo", options=["entrada", "saída", "investimento", "transferência"],
                ),
                "Conta": st.column_config.SelectboxColumn(
                    "Conta", options=conta_nomes if conta_nomes else ["Sem conta"],
                ),
                "Categoria": st.column_config.SelectboxColumn(
                    "Categoria", options=cat_nomes if cat_nomes else ["Sem categoria"],
                ),
                "Data":  st.column_config.DateColumn("Data"),
                "Valor": st.column_config.NumberColumn("Valor (R$)", format="%.2f", step=0.01),
                "Descrição": st.column_config.TextColumn("Descrição"),
            },
        )
        salvar = st.form_submit_button("Salvar alterações", type="primary")

    if not salvar:
        return

    erros, ok_count = [], 0
    for i, row in edited.iterrows():
        orig = df_edit.iloc[i]
        row_data  = pd.to_datetime(row["Data"]).date() if pd.notna(row["Data"]) else None
        orig_data = pd.to_datetime(orig["Data"]).date() if pd.notna(orig["Data"]) else None
        campos_mudaram = (
            row["Descrição"] != orig["Descrição"]
            or row["Categoria"] != orig["Categoria"]
            or row["Tipo"] != orig["Tipo"]
            or row["Conta"] != orig["Conta"]
            or abs(row["Valor"] - orig["Valor"]) > 0.001
            or row_data != orig_data
        )
        if not campos_mudaram:
            continue
        cat_m   = next((c for c in cats_db if c["nome"] == row["Categoria"]), None)
        cat_id  = cat_m["id"] if cat_m else None
        conta_m = next((c for c in contas_db if c["nome"] == row["Conta"]), None)
        if not conta_m:
            erros.append(f"ID {row['ID']}: conta inválida ou não encontrada.")
            continue
        tipo_tx = {
            "entrada": "income", "saída": "expense",
            "investimento": "investment", "transferência": "transfer",
        }.get(row["Tipo"], "expense")
        sinal = 1.0 if tipo_tx in ("income", "transfer") else -1.0
        ok, msg = atualizar_transacao(
            tx_id=str(row["ID"]),
            descricao=str(row["Descrição"]),
            valor=sinal * abs(float(row["Valor"])),
            data=row_data,
            categoria_id=cat_id,
            conta_id=conta_m["id"],
            tipo=tipo_tx,
        )
        if ok:
            ok_count += 1
        else:
            erros.append(f"ID {row['ID']}: {msg}")

    if ok_count > 0:
        st.success(f"✅ {ok_count} lançamento(s) atualizado(s).")
        st.rerun()
    for e in erros:
        st.error(e)
    if ok_count == 0 and not erros:
        st.info("Nenhuma alteração detectada.")


def _tab_tabelas(d: dict) -> None:
    """
    Consulta de lançamentos com filtros completos (Tipo, Categoria, Ano, Mês, Dia, Texto).
    Replica fielmente o módulo Consulta_Tabelas.py do app original.
    """
    _secao_titulo("🔍", "Consulta de lançamentos")

    # 1) Tipo — radio fora do form (igual ao original)
    aba = st.radio(
        "Tipo de lançamento",
        ["Todos", "Receitas", "Despesas", "Investimentos"],
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
    total_rec      = sum(abs(t["valor"]) for t in txs_f if t.get("tipo_fluxo") == "income")
    total_desp     = sum(abs(t["valor"]) for t in txs_f if t.get("tipo_fluxo") == "expense")
    total_inv      = sum(abs(t["valor"]) for t in txs_f if t.get("tipo_fluxo") == "investment")

    col_s1, col_s2, col_s3, col_s4 = st.columns(4, gap="small")
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
    with col_s4:
        st.markdown(_kpi_card(
            "Investimentos", fmt_moeda(total_inv),
            "Aportes no filtro aplicado",
            _COR_INVEST,
        ), unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    if not txs_f:
        st.caption("Nenhum lançamento com os filtros aplicados.")
        _render_bank_statement_section(f_ano, f_mes)
        return

    # ── Edição in-place das linhas filtradas ──────────────────────────────────
    edit_mode = st.checkbox(
        "✏️ Habilitar edição das linhas filtradas",
        key="tab_edit_mode",
        help="Edite Tipo, Categoria, Data, Valor, Descrição e Conta dos lançamentos filtrados.",
    )
    if edit_mode:
        st.info(
            "Edite os campos desejados e clique **Salvar alterações**. "
            "A edição opera sobre o resultado filtrado (até 200 linhas)."
        )
        _editor_lancamentos(txs_f, form_key="form_editor_tabelas", editor_key="editor_tabelas", limit=200)
        _render_bank_statement_section(f_ano, f_mes)
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
        cor = _cor_tx(tx)
        tipo_label = _tipo_tx_label(tx)
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

    _render_bank_statement_section(f_ano, f_mes)


def _fmt_date_ui(value: object) -> str:
    return value.strftime("%d/%m/%Y") if hasattr(value, "strftime") else "-"


def _editor_extratos(rows: list, categories: list) -> None:
    """Editor in-place dos movimentos de extrato importados.
    Permite editar Descrição, Direção, Valor e Categoria e grava via
    update_bank_statement_movement (sincroniza a transação publicada)."""
    from core.bank_statement_import import update_bank_statement_movement

    cat_by_name = {c["nome"]: c for c in categories}
    options = ["Pendente"] + list(cat_by_name.keys())

    def _cat_label(r: dict) -> str:
        name = (
            r.get("categoria_confirmada_nome")
            or r.get("categoria_nome")
            or r.get("categoria_sugerida_texto")
        )
        return name if name in cat_by_name else "Pendente"

    df = pd.DataFrame([
        {
            "ID": row.get("id"),
            "Data": _fmt_date_ui(row.get("data_movimento")),
            "Banco": row.get("banco") or "-",
            "Descrição": row.get("descricao_original") or "",
            "Direção": row.get("direcao") or "saida",
            "Categoria": _cat_label(row),
            "Status": row.get("status_classificacao") or "pendente",
            "Valor (R$)": float(row.get("valor") or 0.0),
        }
        for row in rows
    ])

    with st.form("form_editor_extratos", clear_on_submit=False):
        edited = st.data_editor(
            df,
            num_rows="fixed",
            hide_index=True,
            use_container_width=True,
            key="editor_extratos",
            column_config={
                "ID": None,
                "Data":   st.column_config.TextColumn("Data", disabled=True),
                "Banco":  st.column_config.TextColumn("Banco", disabled=True),
                "Status": st.column_config.TextColumn("Status", disabled=True),
                "Descrição": st.column_config.TextColumn("Descrição", width="large"),
                "Direção": st.column_config.SelectboxColumn("Direção", options=["entrada", "saida"], required=True),
                "Categoria": st.column_config.SelectboxColumn("Categoria", options=options, required=True),
                "Valor (R$)": st.column_config.NumberColumn("Valor (R$)", format="R$ %.2f", step=0.01),
            },
        )
        salvar = st.form_submit_button("Salvar alterações dos extratos", type="primary")

    if not salvar:
        return

    erros, ok_count = [], 0
    for i, row in edited.iterrows():
        orig = df.iloc[i]
        mudou = (
            row["Descrição"] != orig["Descrição"]
            or row["Direção"] != orig["Direção"]
            or row["Categoria"] != orig["Categoria"]
            or abs(float(row["Valor (R$)"]) - float(orig["Valor (R$)"])) > 0.001
        )
        if not mudou:
            continue
        cat = cat_by_name.get(str(row["Categoria"]))
        ok, msg = update_bank_statement_movement(
            movement_id=str(row["ID"]),
            category_id=cat["id"] if cat else None,
            descricao=str(row["Descrição"]),
            valor=float(row["Valor (R$)"]),
            direcao=str(row["Direção"]),
        )
        if ok:
            ok_count += 1
        else:
            erros.append(f"ID {row['ID']}: {msg}")

    if ok_count > 0:
        st.success(f"✅ {ok_count} movimento(s) de extrato atualizado(s).")
        st.rerun()
    for e in erros:
        st.error(e)
    if ok_count == 0 and not erros:
        st.info("Nenhuma alteração detectada.")


def _render_bank_statement_section(ano: int | None, mes: int | None) -> None:
    """Fila operacional dos extratos bancarios importados, sem upload nesta aba."""
    try:
        from core.bank_statement_import import (
            confirm_bank_statement_movement,
            get_bank_statement_categories,
            get_bank_statement_review_rows,
        )
    except Exception as exc:
        st.caption(f"Extratos bancarios indisponiveis: {exc}")
        return

    st.divider()
    _secao_titulo("🏦", "Extratos bancários importados")
    st.caption("O upload fica em Configurações > Extratos Bancários. Aqui entram conferência, filtros e confirmação.")

    col_s, col_b, col_d, col_c = st.columns(4, gap="small")
    with col_s:
        status = st.selectbox(
            "Status do extrato",
            ["Todos", "pendente", "sugerida", "confirmada"],
            key="bank_tx_status_filter",
        )

    rows = get_bank_statement_review_rows(status=status, ano=ano, mes=mes, limit=400)
    bancos = sorted({str(row.get("banco") or "") for row in rows if row.get("banco")})
    directions = sorted({str(row.get("direcao") or "") for row in rows if row.get("direcao")})
    categories_in_rows = sorted({
        str(row.get("categoria_confirmada_nome") or row.get("categoria_nome") or row.get("categoria_sugerida_texto") or "Pendente")
        for row in rows
    })

    with col_b:
        bank_filter = st.selectbox("Banco", ["Todos"] + bancos, key="bank_tx_bank_filter")
    with col_d:
        direction_filter = st.selectbox("Direção", ["Todos"] + directions, key="bank_tx_direction_filter")
    with col_c:
        category_filter = st.selectbox("Categoria", ["Todas"] + categories_in_rows, key="bank_tx_category_filter")

    filtered = []
    for row in rows:
        category_label = str(row.get("categoria_confirmada_nome") or row.get("categoria_nome") or row.get("categoria_sugerida_texto") or "Pendente")
        if bank_filter != "Todos" and row.get("banco") != bank_filter:
            continue
        if direction_filter != "Todos" and row.get("direcao") != direction_filter:
            continue
        if category_filter != "Todas" and category_label != category_filter:
            continue
        filtered.append(row)

    if not filtered:
        st.caption("Nenhuma movimentação bancária importada para os filtros atuais.")
        return

    # ── Edição in-place dos extratos importados ───────────────────────────────
    edit_extratos = st.checkbox(
        "✏️ Habilitar edição dos extratos",
        key="bank_tx_edit_mode",
        help="Edite Descrição, Direção, Valor e Categoria dos lançamentos importados.",
    )
    if edit_extratos:
        cats_edit = get_bank_statement_categories()
        if not cats_edit:
            st.warning("Categorias indisponíveis para editar extratos.")
            return
        st.info(
            "Edite os campos e clique **Salvar alterações dos extratos**. "
            "Definir uma categoria confirma o lançamento e publica/atualiza no Controle Financeiro."
        )
        _editor_extratos(filtered, cats_edit)
        return

    df = pd.DataFrame([
        {
            "Data": _fmt_date_ui(row.get("data_movimento")),
            "Banco": row.get("banco"),
            "Descrição": row.get("descricao_original"),
            "Direção": row.get("direcao"),
            "Categoria": row.get("categoria_confirmada_nome") or row.get("categoria_nome") or row.get("categoria_sugerida_texto") or "Pendente",
            "Status": row.get("status_classificacao"),
            "Valor (R$)": row.get("valor"),
        }
        for row in filtered
    ])
    st.dataframe(
        df,
        hide_index=True,
        use_container_width=True,
        column_config={"Valor (R$)": st.column_config.NumberColumn("Valor (R$)", format="R$ %.2f")},
    )

    editable = [row for row in filtered if row.get("status_classificacao") in {"pendente", "sugerida"}]
    if not editable:
        st.caption("Todos os lançamentos desse filtro já estão confirmados.")
        return

    categories = get_bank_statement_categories()
    if not categories:
        st.warning("Categorias indisponíveis para revisar extratos.")
        return

    with st.expander("Revisar e confirmar classificação"):
        selected_idx = st.selectbox(
            "Lançamento",
            range(len(editable)),
            format_func=lambda idx: (
                f"{_fmt_date_ui(editable[idx].get('data_movimento'))} · "
                f"{fmt_moeda(editable[idx].get('valor') or 0.0)} · "
                f"{str(editable[idx].get('descricao_original') or '')[:72]}"
            ),
            key="bank_tx_review_selected",
        )
        selected = editable[selected_idx]
        category_idx = st.selectbox(
            "Categoria real",
            range(len(categories)),
            format_func=lambda idx: f"{categories[idx]['nome']} ({categories[idx]['tipo']})",
            key="bank_tx_review_category",
        )
        save_rule = st.checkbox("Salvar regra para próximas importações", value=True, key="bank_tx_review_save_rule")
        keyword = st.text_input(
            "Palavra-chave",
            value=str(selected.get("descricao_original") or "")[:80],
            key="bank_tx_review_keyword",
        )
        if st.button("Confirmar lançamento importado", type="primary", use_container_width=True, key="bank_tx_review_confirm"):
            ok, msg = confirm_bank_statement_movement(
                selected["id"],
                categories[category_idx]["id"],
                save_rule=save_rule,
                palavra_chave=keyword,
            )
            if ok:
                st.success("Classificação confirmada.")
                st.rerun()
            st.error(msg or "Falha ao confirmar classificação.")


# ══════════════════════════════════════════════════════════════════════════════
# TAB 4 — Cartão de Crédito
# ══════════════════════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════════════════════
# TAB 4 - NOVO PAINEL ANALITICO DO CARTAO
# ══════════════════════════════════════════════════════════════════════════════

_CC_FEE_TERMS = ("anuidade", "iof", "juros", "multa", "tarifa", "encargo")
_CC_PAYMENT_TERMS = ("pag fatura", "pagamento fatura", "pagamento de cartao", "boleto fatura")
_CC_REFUND_TERMS = ("estorno", "credito", "creditos", "reembolso", "cashback")
_CC_MERCHANT_PREFIXES = {
    "ec", "ecommerce", "mp", "mercadopago", "pag", "pagseguro", "ifd", "paypal",
    "br", "www", "loja", "compra", "debito", "credito",
}
_CC_MERCHANT_SUFFIXES = {
    "sa", "s", "a", "ltda", "me", "epp", "eireli", "brasil", "br",
}
def _norm_ui_text(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = text.encode("ascii", "ignore").decode("ascii")
    text = text.replace("_", " ").replace("-", " ").strip().casefold()
    return " ".join(text.split())


def _safe(value: object) -> str:
    return html.escape(str(value if value is not None else "-"))


def _to_timestamp(value: object) -> object:
    if value is None or value == "":
        return pd.NaT
    try:
        return pd.to_datetime(value)
    except Exception:
        return pd.NaT


def _extract_card_final(description: object) -> str:
    desc = str(description or "")
    match = re.search(r"Cart\S*\s+(\d{4})", desc, flags=re.IGNORECASE)
    return match.group(1) if match else "Nao informado"


def _clean_card_description(description: object) -> str:
    desc = str(description or "").strip()
    if " | Compra " in desc:
        desc = desc.split(" | Compra ", 1)[0].strip()
    desc = re.sub(r"\s*\|\s*Cart\S*\s+\d{4}.*$", "", desc, flags=re.IGNORECASE).strip()
    desc = re.sub(r"\s*\|\s*Parcela\s+.*$", "", desc, flags=re.IGNORECASE).strip()
    return desc or "Sem descricao"


def _normalize_merchant_name(description: object) -> str:
    merchant = _clean_card_description(description)
    text = _norm_ui_text(merchant)
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    tokens = [t for t in text.split() if t]

    while tokens and tokens[0] in _CC_MERCHANT_PREFIXES:
        tokens.pop(0)
    while tokens and (tokens[-1] in _CC_MERCHANT_SUFFIXES or tokens[-1].isdigit()):
        tokens.pop()

    cleaned = []
    for token in tokens:
        if token.isdigit() or (len(token) <= 1 and token not in {"i"}):
            continue
        cleaned.append(token)

    if len(cleaned) >= 4 and cleaned[-1] in {"loja", "site", "app"}:
        cleaned = cleaned[:-1]
    return " ".join(cleaned[:6]) or _norm_ui_text(merchant) or "sem descricao"


def _best_merchant_label(values: pd.Series) -> str:
    clean = [str(v or "").strip() for v in values if str(v or "").strip()]
    if not clean:
        return "Sem descricao"
    counts = pd.Series(clean).value_counts()
    top_count = counts.iloc[0]
    candidates = [idx for idx, value in counts.items() if value == top_count]
    return sorted(candidates, key=lambda s: (-len(s), s))[0]


# ── Assinaturas: nomes canônicos por marca ────────────────────────────────────
# A operadora muda o descritor do mesmo serviço entre meses (ex.: "CLAUDE.AI
# SUBSCRIPTION SA" vs "ANTHROPIC* CLAUDE SUB SA"), o que quebra a detecção de
# recorrência por estabelecimento. Este mapa canoniza variantes conhecidas para
# um nome único, e também identifica marcas de assinatura fora da categoria
# "Assinaturas & Serviços digitais" (ex.: Wellhub, que fica em Saúde).
# (palavra-chave em MAIÚSCULO sem acento) -> nome exibido
_SUBSCRIPTION_BRANDS = [
    ("ANTHROPIC", "Claude (Anthropic)"), ("CLAUDE", "Claude (Anthropic)"),
    ("CHATGPT", "OpenAI"), ("OPENAI", "OpenAI"),
    ("IFOOD", "iFood Club"), ("LIVELO", "Clube Livelo"), ("SMILES", "Smiles"),
    ("WELLHUB", "Wellhub"), ("GYMPASS", "Wellhub"),
    ("SUPABASE", "Supabase"), ("BRAPI", "Brapi"), ("JUSBRASIL", "JusBrasil"),
    ("GOOGLE", "Google"), ("SPOTIFY", "Spotify"), ("NETFLIX", "Netflix"),
    ("DISNEY", "Disney+"), ("AMAZON PRIME", "Amazon Prime"), ("PRIME VIDEO", "Prime Video"),
    ("YOUTUBE", "YouTube Premium"), ("NIO FIBRA", "Nio Fibra"), ("MICROSOFT", "Microsoft"),
    ("APPLE", "Apple"), ("ICLOUD", "Apple iCloud"),
]


def _subscription_brand(description: object) -> str | None:
    """Nome canônico da assinatura se a descrição casar uma marca conhecida."""
    up = _norm_ui_text(description).upper()
    for kw, nome in _SUBSCRIPTION_BRANDS:
        if kw in up:
            return nome
    return None


def _prepare_subscriptions(df: pd.DataFrame) -> list[dict]:
    """
    Lista consolidada de ASSINATURAS/serviços recorrentes, agrupada por marca
    canônica. Fonte confiável: tudo na categoria 'Assinaturas & Serviços digitais'
    MAIS qualquer compra cuja descrição case uma marca de assinatura conhecida
    (ex.: Wellhub, que fica em Saúde). Agrega as linhas de IOF junto do principal.
    """
    if df.empty:
        return []
    compras = df[df["tipo_lancamento"] == "compra"].copy()
    if compras.empty:
        return []
    marca = compras["descricao"].map(_subscription_brand)
    eh_assinatura_cat = compras["categoria"] == "Assinaturas & Serviços digitais"
    # Guarda anti-marketplace: uma compra PARCELADA (installment_total > 1) sem
    # marca de assinatura conhecida quase nunca é assinatura — é compra avulsa
    # que pode ter caído na categoria por engano. Só entra por marca conhecida.
    nao_parcelada = ~compras["is_parcelada"].fillna(False).astype(bool)
    base = compras[(eh_assinatura_cat & nao_parcelada) | marca.notna()].copy()
    if base.empty:
        return []
    base["_marca"] = base["descricao"].map(
        lambda d: _subscription_brand(d) or _clean_card_description(d))

    # Referência de "atividade": ordinal (ano*12+mês) da fatura MAIS RECENTE entre
    # TODAS as compras do cartão. Uma assinatura é ATIVA se sua última cobrança
    # está na fatura mais recente ou na anterior (gap <= 1). Assim, assinaturas
    # canceladas/pontuais (ex.: um mês só, há vários meses) não são contadas como
    # "em uso" — evita inflar o total mensal atual.
    def _ord(ano, mes) -> int:
        return int(ano) * 12 + int(mes)

    comp_ok = compras.dropna(subset=["ano_vencimento", "mes_vencimento"])
    global_last = int((comp_ok["ano_vencimento"].astype(int) * 12
                       + comp_ok["mes_vencimento"].astype(int)).max()) if not comp_ok.empty else 0

    linhas = []
    for nome, grupo in base.groupby("_marca"):
        g_ok = grupo.dropna(subset=["ano_vencimento", "mes_vencimento"])
        meses = sorted({int(m) for m in grupo["mes_vencimento"].dropna()})
        total = float(grupo["valor_fatura"].sum())
        n_meses = len(meses) or 1
        cat = grupo["categoria"].mode()
        last_ord = int((g_ok["ano_vencimento"].astype(int) * 12
                        + g_ok["mes_vencimento"].astype(int)).max()) if not g_ok.empty else 0
        ativa = bool(global_last and (global_last - last_ord) <= 1)
        ult_ano, ult_mes = (last_ord // 12, last_ord % 12) if last_ord else (0, 0)
        linhas.append({
            "Assinatura": str(nome),
            "Categoria": str(cat.iloc[0]) if not cat.empty else "-",
            "Total (R$)": round(total, 2),
            "Meses": n_meses,
            "Media mensal": round(total / n_meses, 2),
            "Lancamentos": int(len(grupo)),
            "Ativa": ativa,
            "Ultima cobranca": f"{_MESES_PT.get(ult_mes, '')}/{ult_ano}" if last_ord else "-",
        })
    # Ativas primeiro, depois por custo mensal.
    linhas.sort(key=lambda x: (not x["Ativa"], -x["Media mensal"]))
    return linhas


def _classify_card_movement(tx: dict) -> str:
    tipo_fluxo = tx.get("tipo_fluxo")
    text = _norm_ui_text(f"{tx.get('categoria', '')} {tx.get('descricao', '')}")

    if tipo_fluxo == "expense":
        if any(term in text for term in _CC_FEE_TERMS):
            return "tarifa"
        return "compra"
    if any(term in text for term in _CC_PAYMENT_TERMS):
        return "pagamento"
    if any(term in text for term in _CC_REFUND_TERMS):
        return "estorno"
    if tipo_fluxo == "transfer":
        return "ajuste"
    return "ajuste"


def _movement_invoice_value(movement: str, amount: float) -> float:
    value = abs(float(amount or 0.0))
    if movement in {"compra", "tarifa"}:
        return value
    if movement in {"pagamento", "estorno", "ajuste"}:
        return -value
    return float(amount or 0.0)


def _annotate_card_recurrence(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["recorrencia_qtd"] = 0
    df["recorrencia_meses"] = 0
    df["recorrencia_valor_medio"] = 0.0
    df["recorrencia_cv"] = 0.0
    df["recorrencia_status"] = "-"
    df["possivel_recorrente"] = False

    compras = df[(df["tipo_lancamento"] == "compra") & (df["estabelecimento_norm"] != "sem descricao")].copy()
    if compras.empty:
        return df

    stats = (
        compras.groupby("estabelecimento_norm", as_index=False)
        .agg(
            recorrencia_qtd=("id", "count"),
            recorrencia_meses=("ano_mes", "nunique"),
            recorrencia_valor_medio=("valor_fatura", "mean"),
            recorrencia_valor_std=("valor_fatura", "std"),
            categoria_ref=("categoria", lambda s: s.mode().iloc[0] if not s.mode().empty else s.iloc[0]),
        )
    )
    stats["recorrencia_valor_std"] = stats["recorrencia_valor_std"].fillna(0.0)
    stats["recorrencia_cv"] = (
        stats["recorrencia_valor_std"] / stats["recorrencia_valor_medio"].replace(0, pd.NA)
    ).fillna(0.0)

    def _status(row: pd.Series) -> str:
        cat_norm = _norm_ui_text(row["categoria_ref"])
        is_subscription = any(term in cat_norm for term in ("assinatura", "streaming", "recorrente"))
        stable_value = float(row["recorrencia_cv"]) <= 0.25
        if int(row["recorrencia_meses"]) >= 3 or (int(row["recorrencia_meses"]) >= 2 and stable_value):
            return "recorrente"
        if int(row["recorrencia_qtd"]) >= 3 and stable_value:
            return "recorrente"
        if int(row["recorrencia_meses"]) >= 2 or (is_subscription and int(row["recorrencia_qtd"]) >= 1):
            return "possivel"
        return "-"

    stats["recorrencia_status"] = stats.apply(_status, axis=1)
    stats["possivel_recorrente"] = stats["recorrencia_status"].isin(["recorrente", "possivel"])

    merged = df.merge(
        stats[[
            "estabelecimento_norm", "recorrencia_qtd", "recorrencia_meses",
            "recorrencia_valor_medio", "recorrencia_cv", "recorrencia_status",
            "possivel_recorrente",
        ]],
        on="estabelecimento_norm",
        how="left",
        suffixes=("", "_calc"),
    )
    merged["recorrencia_qtd"] = merged["recorrencia_qtd_calc"].fillna(merged["recorrencia_qtd"]).astype(int)
    merged["recorrencia_meses"] = merged["recorrencia_meses_calc"].fillna(merged["recorrencia_meses"]).astype(int)
    merged["recorrencia_valor_medio"] = merged["recorrencia_valor_medio_calc"].fillna(merged["recorrencia_valor_medio"]).astype(float)
    merged["recorrencia_cv"] = merged["recorrencia_cv_calc"].fillna(merged["recorrencia_cv"]).astype(float)
    merged["recorrencia_status"] = merged["recorrencia_status_calc"].fillna(merged["recorrencia_status"])
    merged["possivel_recorrente"] = merged["possivel_recorrente_calc"].where(
        merged["possivel_recorrente_calc"].notna(),
        merged["possivel_recorrente"],
    ).astype(bool)
    return merged.drop(columns=[
        "recorrencia_qtd_calc", "recorrencia_meses_calc", "recorrencia_valor_medio_calc",
        "recorrencia_cv_calc", "recorrencia_status_calc", "possivel_recorrente_calc",
    ])


def _card_rows_dataframe(transacoes: list[dict]) -> pd.DataFrame:
    rows = []
    for tx in transacoes or []:
        if tx.get("account_type") != "credit_card":
            continue
        if not _is_credit_card_invoice_source(tx.get("source")):
            continue

        due_ts = _to_timestamp(tx.get("data") or tx.get("due_date"))
        purchase_ts = _to_timestamp(tx.get("data_compra") or tx.get("payment_date") or tx.get("data"))
        if pd.isna(purchase_ts):
            purchase_ts = due_ts

        movement = _classify_card_movement(tx)
        amount = float(tx.get("valor") or 0.0)
        invoice_value = _movement_invoice_value(movement, amount)
        inst_current = int(tx.get("installment_current") or 1)
        inst_total = int(tx.get("installment_total") or 1)
        desc = str(tx.get("descricao") or "")
        estabelecimento = _clean_card_description(desc)
        estabelecimento_norm = _normalize_merchant_name(desc)

        rows.append({
            "id": tx.get("id"),
            "data_vencimento": due_ts,
            "data_compra": purchase_ts,
            "ano_vencimento": int(due_ts.year) if not pd.isna(due_ts) else None,
            "mes_vencimento": int(due_ts.month) if not pd.isna(due_ts) else None,
            "ano_mes": due_ts.strftime("%Y-%m") if not pd.isna(due_ts) else "-",
            "mes_label": f"{_MESES_PT[int(due_ts.month)]}/{int(due_ts.year)}" if not pd.isna(due_ts) else "-",
            "descricao": desc,
            "estabelecimento_raw": estabelecimento,
            "estabelecimento": estabelecimento,
            "estabelecimento_norm": estabelecimento_norm,
            "categoria": tx.get("categoria") or "Sem categoria",
            "conta": tx.get("conta") or "Sem cartao",
            "final_cartao": _extract_card_final(desc),
            "tipo_lancamento": movement,
            "valor_original": amount,
            "valor_abs": abs(amount),
            "valor_fatura": round(invoice_value, 2),
            "installment_current": inst_current,
            "installment_total": inst_total,
            "installment_label": f"{inst_current}/{inst_total}" if inst_total > 1 else "Unica",
            "installment_group": tx.get("installment_group") or tx.get("id"),
            "is_parcelada": inst_total > 1,
            "parcelas_restantes": max(inst_total - inst_current, 0),
            "source": tx.get("source") or "manual",
            "status": tx.get("status") or "-",
        })

    df = pd.DataFrame(rows)
    if df.empty:
        return df

    df["valor_pendente_estimado"] = df["valor_fatura"].clip(lower=0) * df["parcelas_restantes"]
    return _annotate_card_recurrence(df)


def _render_card_filters(df: pd.DataFrame, selected_year: int, selected_month: int) -> dict:
    years = sorted([int(y) for y in df["ano_vencimento"].dropna().unique()], reverse=True)
    year_options: list[object] = ["Todos"] + years
    default_year = selected_year if selected_year in years else (years[0] if years else "Todos")

    if default_year == "Todos":
        available_months = sorted(int(m) for m in df["mes_vencimento"].dropna().unique())
    else:
        available_months = sorted(
            int(m) for m in df.loc[df["ano_vencimento"] == int(default_year), "mes_vencimento"].dropna().unique()
        )
    default_month = selected_month if selected_month in available_months else (available_months[-1] if available_months else selected_month)
    month_labels = {f"{m:02d} - {_MESES_PT[m]}": m for m in range(1, 13)}
    month_options = ["Todos"] + list(month_labels.keys())
    default_month_label = next((label for label, month in month_labels.items() if month == default_month), "Todos")

    card_options = ["Todos"] + sorted(v for v in df["final_cartao"].dropna().unique() if v)
    cat_options = ["Todas"] + sorted(v for v in df["categoria"].dropna().unique() if v)
    type_options = ["Todos", "compra", "tarifa", "estorno", "pagamento", "ajuste"]

    c1, c2, c3, c4 = st.columns(4, gap="small")
    with c1:
        year = st.selectbox("Ano", year_options, index=year_options.index(default_year), key="cc_filter_year")
    with c2:
        month = st.selectbox(
            "Mes de referencia",
            month_options,
            index=month_options.index(default_month_label) if default_month_label in month_options else 0,
            key="cc_filter_month",
        )
    with c3:
        card = st.selectbox("Final do cartao", card_options, key="cc_filter_card")
    with c4:
        category = st.selectbox("Categoria", cat_options, key="cc_filter_category")

    c5, c6, c7, c8 = st.columns([1.2, 1.8, 1.2, 1.3], gap="small")
    with c5:
        movement = st.selectbox("Tipo de lancamento", type_options, key="cc_filter_type")
    with c6:
        search = st.text_input("Buscar por descricao", placeholder="Ex: mercado, smiles, anuidade...", key="cc_filter_search")
    with c7:
        only_installments = st.checkbox("Apenas parceladas", key="cc_filter_installments")
    with c8:
        min_value = st.number_input("Valor minimo", min_value=0.0, value=0.0, step=50.0, key="cc_filter_min_value")

    return {
        "year": year,
        "month": month_labels.get(month),
        "card": card,
        "category": category,
        "movement": movement,
        "search": search,
        "only_installments": only_installments,
        "min_value": float(min_value or 0.0),
    }


def _apply_card_filters(df: pd.DataFrame, filters: dict) -> pd.DataFrame:
    out = df.copy()
    if filters["year"] != "Todos":
        out = out[out["ano_vencimento"] == int(filters["year"])]
    if filters["month"]:
        out = out[out["mes_vencimento"] == int(filters["month"])]
    if filters["card"] != "Todos":
        out = out[out["final_cartao"] == filters["card"]]
    if filters["category"] != "Todas":
        out = out[out["categoria"] == filters["category"]]
    if filters["movement"] != "Todos":
        out = out[out["tipo_lancamento"] == filters["movement"]]
    if filters["search"]:
        needle = _norm_ui_text(filters["search"])
        out = out[out["descricao"].map(_norm_ui_text).str.contains(needle, na=False, regex=False)]
    if filters["only_installments"]:
        out = out[out["is_parcelada"]]
    if filters["min_value"] > 0:
        out = out[out["valor_abs"] >= filters["min_value"]]
    return out


def _prepare_category_analysis(df: pd.DataFrame) -> pd.DataFrame:
    compras = df[df["tipo_lancamento"] == "compra"]
    if compras.empty:
        return pd.DataFrame(columns=["Categoria", "Total (R$)", "Transacoes", "Ticket medio", "% compras"])
    total = compras["valor_fatura"].sum() or 1.0
    out = (
        compras.groupby("categoria", as_index=False)
        .agg(total=("valor_fatura", "sum"), transacoes=("id", "count"), ticket=("valor_fatura", "mean"))
        .sort_values("total", ascending=False)
    )
    out["pct"] = out["total"] / total * 100
    return out.rename(columns={
        "categoria": "Categoria",
        "total": "Total (R$)",
        "transacoes": "Transacoes",
        "ticket": "Ticket medio",
        "pct": "% compras",
    })


def _prepare_merchant_analysis(df: pd.DataFrame) -> pd.DataFrame:
    compras = df[df["tipo_lancamento"] == "compra"]
    if compras.empty:
        return pd.DataFrame(columns=[
            "Estabelecimento", "Categoria principal", "Total (R$)", "Transacoes",
            "Maior compra", "% compras", "Recorrencia", "Meses recorrentes",
        ])
    total = compras["valor_fatura"].sum() or 1.0
    out = (
        compras.groupby("estabelecimento_norm", as_index=False)
        .agg(
            estabelecimento=("estabelecimento", _best_merchant_label),
            categoria=("categoria", lambda s: s.mode().iloc[0] if not s.mode().empty else s.iloc[0]),
            total=("valor_fatura", "sum"),
            transacoes=("id", "count"),
            maior=("valor_fatura", "max"),
            recorrencia=("recorrencia_status", lambda s: "recorrente" if "recorrente" in set(s) else "possivel" if "possivel" in set(s) else "-"),
            meses_recorrentes=("recorrencia_meses", "max"),
        )
        .sort_values("total", ascending=False)
    )
    out["pct"] = out["total"] / total * 100
    return out.rename(columns={
        "estabelecimento": "Estabelecimento",
        "categoria": "Categoria principal",
        "total": "Total (R$)",
        "transacoes": "Transacoes",
        "maior": "Maior compra",
        "recorrencia": "Recorrencia",
        "meses_recorrentes": "Meses recorrentes",
        "pct": "% compras",
    })[[
        "Estabelecimento", "Categoria principal", "Total (R$)", "Transacoes",
        "Maior compra", "% compras", "Recorrencia", "Meses recorrentes",
    ]]


def _prepare_recurring_analysis(df: pd.DataFrame) -> pd.DataFrame:
    base = df[(df["tipo_lancamento"] == "compra") & (df["possivel_recorrente"])].copy()
    if base.empty:
        return pd.DataFrame(columns=[
            "Estabelecimento", "Categoria principal", "Recorrencia",
            "Meses", "Transacoes", "Valor medio", "Total (R$)",
        ])
    out = (
        base.groupby("estabelecimento_norm", as_index=False)
        .agg(
            estabelecimento=("estabelecimento", _best_merchant_label),
            categoria=("categoria", lambda s: s.mode().iloc[0] if not s.mode().empty else s.iloc[0]),
            recorrencia=("recorrencia_status", lambda s: "recorrente" if "recorrente" in set(s) else "possivel"),
            meses=("recorrencia_meses", "max"),
            transacoes=("id", "count"),
            valor_medio=("valor_fatura", "mean"),
            total=("valor_fatura", "sum"),
        )
    )
    out["_ordem"] = out["recorrencia"].map({"recorrente": 0, "possivel": 1}).fillna(2)
    out = out.sort_values(["_ordem", "total"], ascending=[True, False]).drop(columns=["_ordem"])
    return out.rename(columns={
        "estabelecimento": "Estabelecimento",
        "categoria": "Categoria principal",
        "recorrencia": "Recorrencia",
        "meses": "Meses",
        "transacoes": "Transacoes",
        "valor_medio": "Valor medio",
        "total": "Total (R$)",
    })[[
        "Estabelecimento", "Categoria principal", "Recorrencia",
        "Meses", "Transacoes", "Valor medio", "Total (R$)",
    ]]


def _prepare_future_invoice_projection(df: pd.DataFrame, months: int = 6) -> pd.DataFrame:
    parcelas = df[
        (df["tipo_lancamento"] == "compra")
        & (df["is_parcelada"])
        & (df["parcelas_restantes"] > 0)
    ].copy()
    if parcelas.empty:
        return pd.DataFrame(columns=["Mes", "Valor projetado", "Parcelas futuras"])

    rows = []
    for _, row in parcelas.iterrows():
        due = row["data_vencimento"]
        if pd.isna(due):
            continue
        remaining = min(int(row["parcelas_restantes"]), int(months))
        for offset in range(1, remaining + 1):
            future = due + pd.DateOffset(months=offset)
            rows.append({
                "ano_mes": future.strftime("%Y-%m"),
                "Mes": f"{_MESES_PT[int(future.month)]}/{int(future.year)}",
                "Valor projetado": float(row["valor_fatura"]),
                "Parcelas futuras": 1,
            })
    if not rows:
        return pd.DataFrame(columns=["Mes", "Valor projetado", "Parcelas futuras"])
    return (
        pd.DataFrame(rows)
        .groupby(["ano_mes", "Mes"], as_index=False)
        .agg({"Valor projetado": "sum", "Parcelas futuras": "sum"})
        .sort_values("ano_mes")
        [["Mes", "Valor projetado", "Parcelas futuras"]]
    )


def _prepare_installment_analysis(df: pd.DataFrame) -> pd.DataFrame:
    parcelas = df[(df["tipo_lancamento"] == "compra") & (df["is_parcelada"])].copy()
    if parcelas.empty:
        return pd.DataFrame(columns=[
            "Estabelecimento", "Categoria", "Final", "Parcela atual",
            "Total parcelas", "Valor no mes", "Restantes", "Pendente estimado",
        ])
    out = (
        parcelas.groupby("installment_group", as_index=False)
        .agg(
            estabelecimento=("estabelecimento", "first"),
            categoria=("categoria", "first"),
            final=("final_cartao", "first"),
            parcela_atual=("installment_current", "max"),
            total_parcelas=("installment_total", "max"),
            valor_mes=("valor_fatura", "sum"),
            restantes=("parcelas_restantes", "max"),
            pendente=("valor_pendente_estimado", "sum"),
        )
        .sort_values("pendente", ascending=False)
    )
    return out.rename(columns={
        "estabelecimento": "Estabelecimento",
        "categoria": "Categoria",
        "final": "Final",
        "parcela_atual": "Parcela atual",
        "total_parcelas": "Total parcelas",
        "valor_mes": "Valor no mes",
        "restantes": "Restantes",
        "pendente": "Pendente estimado",
    }).drop(columns=["installment_group"], errors="ignore")


def _prepare_non_consumption(df: pd.DataFrame) -> pd.DataFrame:
    base = df[df["tipo_lancamento"].isin(["tarifa", "estorno", "pagamento", "ajuste"])].copy()
    if base.empty:
        return pd.DataFrame(columns=["Data", "Tipo", "Descricao", "Categoria", "Valor (R$)"])
    base["Data"] = base["data_compra"].dt.strftime("%d/%m/%Y")
    base["Tipo"] = base["tipo_lancamento"].str.title()
    base["Descricao"] = base["estabelecimento"]
    base["Categoria"] = base["categoria"]
    base["Valor (R$)"] = base["valor_abs"]
    return base[["Data", "Tipo", "Descricao", "Categoria", "Valor (R$)"]].sort_values("Valor (R$)", ascending=False)


def _summary_credit_card(df: pd.DataFrame) -> dict:
    compras = df[df["tipo_lancamento"] == "compra"]
    tarifas = df[df["tipo_lancamento"] == "tarifa"]
    estornos = df[df["tipo_lancamento"] == "estorno"]
    pagamentos = df[df["tipo_lancamento"] == "pagamento"]
    parceladas = compras[compras["is_parcelada"]]

    maior = compras.sort_values("valor_fatura", ascending=False).head(1)
    cat = _prepare_category_analysis(df).head(1)
    return {
        "total_compras": round(float(compras["valor_fatura"].sum()), 2),
        "total_liquido": round(float(df["valor_fatura"].sum()), 2),
        "ticket_medio": round(float(compras["valor_fatura"].mean()), 2) if not compras.empty else 0.0,
        "maior_valor": round(float(maior["valor_fatura"].iloc[0]), 2) if not maior.empty else 0.0,
        "maior_desc": str(maior["estabelecimento"].iloc[0]) if not maior.empty else "-",
        "parceladas_total": round(float(parceladas["valor_fatura"].sum()), 2),
        "parceladas_qtd": int(len(parceladas)),
        "tarifas": round(float(tarifas["valor_fatura"].sum()), 2),
        "estornos": round(float(estornos["valor_abs"].sum()), 2),
        "pagamentos": round(float(pagamentos["valor_abs"].sum()), 2),
        "categoria_dominante": str(cat["Categoria"].iloc[0]) if not cat.empty else "-",
        "categoria_pct": round(float(cat["% compras"].iloc[0]), 1) if not cat.empty else 0.0,
        "compras_qtd": int(len(compras)),
    }


def _prepare_annual_card_totals(df: pd.DataFrame) -> pd.DataFrame:
    cols = ["Ano", "Compras reais (R$)", "Tarifas (R$)", "Total (R$)"]
    base = df[df["tipo_lancamento"].isin(["compra", "tarifa"])].dropna(subset=["ano_vencimento"]).copy()
    if base.empty:
        return pd.DataFrame(columns=cols)

    annual = (
        base.groupby(["ano_vencimento", "tipo_lancamento"])["valor_fatura"]
        .sum()
        .unstack(fill_value=0.0)
        .reset_index()
        .rename(columns={"ano_vencimento": "Ano", "compra": "Compras reais (R$)", "tarifa": "Tarifas (R$)"})
    )
    for col in ["Compras reais (R$)", "Tarifas (R$)"]:
        if col not in annual.columns:
            annual[col] = 0.0
    annual["Total (R$)"] = annual["Compras reais (R$)"] + annual["Tarifas (R$)"]
    annual["Ano"] = annual["Ano"].astype(int)
    return annual[cols].sort_values("Ano", ascending=False).reset_index(drop=True)


def _render_summary_cards(df: pd.DataFrame) -> None:
    s = _summary_credit_card(df)
    c1, c2, c3, c4 = st.columns(4, gap="small")
    with c1:
        st.markdown(_kpi_card("Total de compras reais", fmt_moeda(s["total_compras"]), f"{s['compras_qtd']} compra(s) no filtro.", _COR_DESPESA), unsafe_allow_html=True)
    with c2:
        color = _COR_DESPESA if s["total_liquido"] >= 0 else _COR_RECEITA
        st.markdown(_kpi_card("Total liquido da fatura", fmt_moeda(s["total_liquido"]), "Compras + tarifas - estornos - pagamentos.", color), unsafe_allow_html=True)
    with c3:
        st.markdown(_kpi_card("Ticket medio", fmt_moeda(s["ticket_medio"]), "Exclui pagamentos, estornos e tarifas.", _COR_NEUTRO), unsafe_allow_html=True)
    with c4:
        st.markdown(_kpi_card("Maior compra", fmt_moeda(s["maior_valor"]), _safe(s["maior_desc"][:42]), "#F6C90E"), unsafe_allow_html=True)

    c5, c6, c7, c8 = st.columns(4, gap="small")
    with c5:
        st.markdown(_kpi_card("Compras parceladas", fmt_moeda(s["parceladas_total"]), f"{s['parceladas_qtd']} lancamento(s) parcelado(s).", _COR_INVEST), unsafe_allow_html=True)
    with c6:
        st.markdown(_kpi_card("Tarifas e encargos", fmt_moeda(s["tarifas"]), "Anuidade, IOF, juros, multa e tarifas.", "#F6C90E"), unsafe_allow_html=True)
    with c7:
        st.markdown(_kpi_card("Estornos", fmt_moeda(s["estornos"]), "Creditos abatidos na fatura.", _COR_RECEITA), unsafe_allow_html=True)
    with c8:
        st.markdown(_kpi_card("Categoria dominante", _safe(s["categoria_dominante"][:24]), f"{s['categoria_pct']:.1f}% das compras reais.", _COR_NEUTRO), unsafe_allow_html=True)


def _fig_donut_categoria(cat_df: pd.DataFrame) -> go.Figure:
    chart = cat_df[["Categoria", "Total (R$)", "% compras"]].copy()
    if len(chart) > 7:
        top = chart.head(6)
        other = pd.DataFrame([{
            "Categoria": "Outros",
            "Total (R$)": chart.iloc[6:]["Total (R$)"].sum(),
            "% compras": chart.iloc[6:]["% compras"].sum(),
        }])
        chart = pd.concat([top, other], ignore_index=True)
    fig = go.Figure(go.Pie(
        labels=chart["Categoria"],
        values=chart["Total (R$)"],
        hole=0.62,
        marker={"colors": _CORES_CAT[:len(chart)]},
        textinfo="percent",
        hovertemplate="<b>%{label}</b><br>R$ %{value:,.2f}<br>%{percent}<extra></extra>",
    ))
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font_color=_COR_NEUTRO,
        margin={"t": 8, "b": 8, "l": 8, "r": 8},
        height=330,
        showlegend=True,
        legend={"orientation": "h", "y": -0.08},
    )
    return fig


def _fig_horizontal_bar(df: pd.DataFrame, label_col: str, value_col: str, color: str, height: int = 330) -> go.Figure:
    chart = df.head(10).sort_values(value_col, ascending=True)
    fig = go.Figure(go.Bar(
        x=chart[value_col],
        y=chart[label_col],
        orientation="h",
        marker_color=color,
        opacity=0.88,
        hovertemplate="<b>%{y}</b><br>R$ %{x:,.2f}<extra></extra>",
    ))
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font_color=_COR_NEUTRO,
        margin={"t": 8, "b": 8, "l": 0, "r": 8},
        height=height,
        xaxis={"showgrid": True, "gridcolor": "#1E2533", "tickformat": ",.0f", "tickprefix": "R$ "},
        yaxis={"showgrid": False},
    )
    return fig


def _fig_monthly_evolution(df: pd.DataFrame) -> go.Figure:
    compras = df[df["tipo_lancamento"] == "compra"]
    monthly = (
        compras.groupby(["ano_mes", "mes_label"], as_index=False)
        .agg(total=("valor_fatura", "sum"))
        .sort_values("ano_mes")
    )
    fig = go.Figure(go.Scatter(
        x=monthly["mes_label"],
        y=monthly["total"],
        mode="lines+markers",
        line={"color": _COR_INVEST, "width": 2.8},
        marker={"size": 8},
        fill="tozeroy",
        fillcolor="rgba(74,158,255,0.10)",
        hovertemplate="<b>%{x}</b><br>R$ %{y:,.2f}<extra></extra>",
    ))
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font_color=_COR_NEUTRO,
        margin={"t": 8, "b": 8, "l": 0, "r": 8},
        height=320,
        xaxis={"showgrid": False},
        yaxis={"showgrid": True, "gridcolor": "#1E2533", "tickformat": ",.0f", "tickprefix": "R$ "},
    )
    return fig


def _fig_future_projection(projection_df: pd.DataFrame) -> go.Figure:
    fig = go.Figure(go.Bar(
        x=projection_df["Mes"],
        y=projection_df["Valor projetado"],
        marker_color=_COR_INVEST,
        opacity=0.88,
        customdata=projection_df["Parcelas futuras"],
        hovertemplate="<b>%{x}</b><br>R$ %{y:,.2f}<br>%{customdata} parcela(s)<extra></extra>",
    ))
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font_color=_COR_NEUTRO,
        margin={"t": 8, "b": 8, "l": 0, "r": 8},
        height=280,
        xaxis={"showgrid": False},
        yaxis={"showgrid": True, "gridcolor": "#1E2533", "tickformat": ",.0f", "tickprefix": "R$ "},
    )
    return fig


def _fig_non_consumption(df: pd.DataFrame) -> go.Figure:
    base = df[df["tipo_lancamento"].isin(["tarifa", "estorno", "pagamento", "ajuste"])]
    agg = (
        base.groupby("tipo_lancamento", as_index=False)
        .agg(total=("valor_abs", "sum"))
        .sort_values("total", ascending=True)
    )
    colors = {
        "tarifa": "#F6C90E",
        "estorno": _COR_RECEITA,
        "pagamento": _COR_NEUTRO,
        "ajuste": _COR_INVEST,
    }
    fig = go.Figure(go.Bar(
        x=agg["total"],
        y=agg["tipo_lancamento"].str.title(),
        orientation="h",
        marker_color=[colors.get(v, _COR_NEUTRO) for v in agg["tipo_lancamento"]],
        hovertemplate="<b>%{y}</b><br>R$ %{x:,.2f}<extra></extra>",
    ))
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font_color=_COR_NEUTRO,
        margin={"t": 8, "b": 8, "l": 0, "r": 8},
        height=260,
        xaxis={"showgrid": True, "gridcolor": "#1E2533", "tickformat": ",.0f", "tickprefix": "R$ "},
        yaxis={"showgrid": False},
    )
    return fig


def _render_money_dataframe(df: pd.DataFrame, money_cols: list[str], pct_cols: list[str] | None = None) -> None:
    pct_cols = pct_cols or []
    column_config = {
        col: st.column_config.NumberColumn(col, format="R$ %.2f")
        for col in money_cols
        if col in df.columns
    }
    for col in pct_cols:
        if col in df.columns:
            column_config[col] = st.column_config.NumberColumn(col, format="%.1f%%")
    st.dataframe(df, hide_index=True, use_container_width=True, column_config=column_config)


def _render_credit_card_insights(
    df: pd.DataFrame,
    projection_df: pd.DataFrame | None = None,
) -> None:
    if df.empty:
        st.info("Nao ha lancamentos no filtro atual para gerar insights.")
        return

    s = _summary_credit_card(df)
    insights = []
    if s["total_compras"] <= 0 and s["total_liquido"] < 0:
        insights.append(("info", "O filtro atual mostra mais abatimentos/pagamentos do que consumo real."))
    if s["categoria_pct"] >= 40:
        insights.append(("warning", f"A categoria {_safe(s['categoria_dominante'])} concentra {s['categoria_pct']:.1f}% das compras reais."))
    if s["tarifas"] > 0:
        insights.append(("warning", f"Foram identificados {fmt_moeda(s['tarifas'])} em tarifas/encargos. Vale conferir anuidade, IOF ou juros."))
    if s["total_compras"] > 0 and s["parceladas_total"] / s["total_compras"] >= 0.30:
        share = s["parceladas_total"] / s["total_compras"] * 100
        insights.append(("warning", f"Compras parceladas representam {share:.1f}% das compras reais do filtro."))

    if projection_df is not None and not projection_df.empty:
        peak = projection_df.sort_values("Valor projetado", ascending=False).iloc[0]
        insights.append(("info", f"Parcelas ja contratadas projetam {fmt_moeda(peak['Valor projetado'])} para {peak['Mes']}."))

    recorrentes = _prepare_recurring_analysis(df)
    if not recorrentes.empty:
        top = recorrentes.iloc[0]
        insights.append(("info", f"Gasto recorrente detectado: {_safe(top['Estabelecimento'])} aparece em {int(top['Meses'])} mes(es), somando {fmt_moeda(top['Total (R$)'])}."))
    if not insights:
        insights.append(("success", "Nenhum alerta relevante no filtro atual. A fatura esta bem segmentada entre consumo, ajustes e parcelas."))

    for level, message in insights[:5]:
        if level == "warning":
            st.warning(message)
        elif level == "success":
            st.success(message)
        else:
            st.info(message)


def _editor_cartao_detalhado(detail: pd.DataFrame) -> None:
    """
    Editor in-place da fatura de cartão: st.data_editor + gravação via
    atualizar_transacao_cartao. Grava apenas as linhas alteradas.

    Campos editáveis (armazenados no banco): Vencimento, Compra, Descrição,
    Categoria, Cartão, Valor, Parcela atual/total e Status. As colunas derivadas
    (Tipo, Recorrência, Final, Valor fatura) são recalculadas automaticamente na
    próxima leitura a partir desses campos, por isso não aparecem aqui.
    """
    opcoes    = get_opcoes_formulario()
    cats_db   = opcoes.get("categorias", []) or []
    contas_db = get_contas_cartao_credito() or []
    cat_nomes   = sorted({c["nome"] for c in cats_db} | set(detail["categoria"].dropna()))
    conta_nomes = sorted({c["nome"] for c in contas_db} | set(detail["conta"].dropna()))
    status_opcoes = sorted(set(detail["status"].dropna()) | {"settled", "pending"})

    rows_edit = []
    for _, tx in detail.iterrows():
        venc = tx["data_vencimento"]
        comp = tx["data_compra"]
        rows_edit.append({
            "ID":           tx["id"],
            "Vencimento":   venc.date() if pd.notna(venc) else None,
            "Compra":       comp.date() if pd.notna(comp) else None,
            "Descrição":    str(tx.get("descricao") or ""),
            "Categoria":    tx.get("categoria") or "Sem categoria",
            "Cartão":       tx.get("conta") or "Sem cartao",
            "Valor":        round(abs(float(tx.get("valor_original") or 0.0)), 2),
            "Parc. atual":  int(tx.get("installment_current") or 1),
            "Parc. total":  int(tx.get("installment_total") or 1),
            "Status":       tx.get("status") or "settled",
        })
    df_edit = pd.DataFrame(rows_edit)

    st.caption(
        "Edite qualquer campo e clique em **Salvar alterações**. O valor é a "
        "magnitude da compra (o sinal original é preservado). Colunas derivadas "
        "(Tipo, Recorrência, Final, Valor fatura) recalculam sozinhas."
    )

    with st.form("cc_detail_editor_form", clear_on_submit=False):
        edited = st.data_editor(
            df_edit,
            num_rows="fixed",
            hide_index=True,
            use_container_width=True,
            key="cc_detail_editor",
            column_config={
                "ID": None,
                "Vencimento": st.column_config.DateColumn("Vencimento", format="DD/MM/YYYY"),
                "Compra":     st.column_config.DateColumn("Compra", format="DD/MM/YYYY"),
                "Descrição":  st.column_config.TextColumn("Descrição"),
                "Categoria":  st.column_config.SelectboxColumn(
                    "Categoria", options=cat_nomes if cat_nomes else ["Sem categoria"]),
                "Cartão":     st.column_config.SelectboxColumn(
                    "Cartão", options=conta_nomes if conta_nomes else ["Sem cartao"]),
                "Valor":      st.column_config.NumberColumn("Valor (R$)", format="%.2f", step=0.01, min_value=0.0),
                "Parc. atual": st.column_config.NumberColumn("Parc. atual", format="%d", step=1, min_value=1),
                "Parc. total": st.column_config.NumberColumn("Parc. total", format="%d", step=1, min_value=1),
                "Status":     st.column_config.SelectboxColumn("Status", options=status_opcoes),
            },
        )
        salvar = st.form_submit_button("Salvar alterações", type="primary")

    if not salvar:
        return

    erros, ok_count = [], 0
    for i, row in edited.iterrows():
        orig = df_edit.iloc[i]
        row_venc = pd.to_datetime(row["Vencimento"]).date() if pd.notna(row["Vencimento"]) else None
        orig_venc = pd.to_datetime(orig["Vencimento"]).date() if pd.notna(orig["Vencimento"]) else None
        row_comp = pd.to_datetime(row["Compra"]).date() if pd.notna(row["Compra"]) else None
        orig_comp = pd.to_datetime(orig["Compra"]).date() if pd.notna(orig["Compra"]) else None

        mudou = (
            row["Descrição"] != orig["Descrição"]
            or row["Categoria"] != orig["Categoria"]
            or row["Cartão"] != orig["Cartão"]
            or row["Status"] != orig["Status"]
            or abs(float(row["Valor"]) - float(orig["Valor"])) > 0.001
            or int(row["Parc. atual"]) != int(orig["Parc. atual"])
            or int(row["Parc. total"]) != int(orig["Parc. total"])
            or row_venc != orig_venc
            or row_comp != orig_comp
        )
        if not mudou:
            continue

        if row_venc is None:
            erros.append(f"Linha {i + 1}: 'Vencimento' é obrigatório.")
            continue
        conta_m = next((c for c in contas_db if c["nome"] == row["Cartão"]), None)
        if not conta_m:
            erros.append(f"Linha {i + 1}: cartão inválido ou não encontrado.")
            continue
        cat_m = next((c for c in cats_db if c["nome"] == row["Categoria"]), None)
        cat_id = cat_m["id"] if cat_m else None

        # Preserva o sinal original do amount; edita apenas a magnitude.
        orig_amount = float(detail.iloc[i].get("valor_original") or 0.0)
        sinal = -1.0 if orig_amount < 0 else 1.0
        novo_amount = sinal * abs(float(row["Valor"]))

        ok, msg = atualizar_transacao_cartao(
            tx_id=str(row["ID"]),
            descricao=str(row["Descrição"]),
            valor=novo_amount,
            data_vencimento=row_venc,
            data_compra=row_comp,
            categoria_id=cat_id,
            conta_id=conta_m["id"],
            installment_current=int(row["Parc. atual"]),
            installment_total=int(row["Parc. total"]),
            status=str(row["Status"]),
        )
        if ok:
            ok_count += 1
        else:
            erros.append(f"Linha {i + 1} (ID {row['ID']}): {msg}")

    if ok_count > 0:
        st.success(f"✅ {ok_count} lançamento(s) atualizado(s).")
        st.rerun()
    for e in erros:
        st.error(e)
    if ok_count == 0 and not erros:
        st.info("Nenhuma alteração detectada.")


def _render_cartao_a_revisar(df_all: pd.DataFrame) -> None:
    """
    Painel de revisão dos lançamentos que o importador não soube categorizar
    (categoria = 'A revisar'). O usuário escolhe a categoria correta e, opcional-
    mente, cria uma regra (estabelecimento → categoria) que passa a valer nas
    próximas faturas automaticamente.
    """
    if "categoria" not in df_all.columns:
        return
    pend = df_all[df_all["categoria"] == REVIEW_SENTINEL].copy()
    if pend.empty:
        return

    pend = pend.sort_values("valor_abs", ascending=False)
    n = len(pend)
    st.markdown(
        f'<div style="background:#2A1A12;border:1px solid #7A4A28;border-radius:10px;'
        f'padding:12px 16px;margin-bottom:12px;">'
        f'<span style="font-size:0.95rem;font-weight:800;color:#F6C90E;">'
        f'🏷️ {n} lançamento(s) a categorizar</span>'
        f'<span style="color:#B8A98C;font-size:0.8rem;">'
        f' — o importador não reconheceu o estabelecimento. Escolha a categoria e, '
        f'se marcar “criar regra”, as próximas faturas desse estabelecimento entram '
        f'nela automaticamente.</span></div>',
        unsafe_allow_html=True,
    )

    opcoes_cat = [REVIEW_SENTINEL] + categorias_disponiveis()
    id_to_merchant = {str(r["id"]): str(r.get("estabelecimento") or r.get("descricao") or "")
                      for _, r in pend.iterrows()}

    rows = [{
        "ID":         str(r["id"]),
        "Data":       r["data_compra"].date() if pd.notna(r["data_compra"]) else None,
        "Descrição":  str(r.get("estabelecimento") or r.get("descricao") or ""),
        "Valor":      round(abs(float(r.get("valor_original") or 0.0)), 2),
        "Categoria":  REVIEW_SENTINEL,
        "Criar regra": True,
    } for _, r in pend.iterrows()]
    df_edit = pd.DataFrame(rows)

    with st.form("cc_revisar_form", clear_on_submit=False):
        edited = st.data_editor(
            df_edit,
            hide_index=True,
            use_container_width=True,
            num_rows="fixed",
            key="cc_revisar_editor",
            column_config={
                "ID": None,
                "Data":  st.column_config.DateColumn("Compra", format="DD/MM/YYYY", disabled=True),
                "Descrição": st.column_config.TextColumn("Estabelecimento", disabled=True),
                "Valor": st.column_config.NumberColumn("Valor (R$)", format="%.2f", disabled=True),
                "Categoria": st.column_config.SelectboxColumn("Categoria", options=opcoes_cat, required=True),
                "Criar regra": st.column_config.CheckboxColumn(
                    "Criar regra", help="Aprende estabelecimento → categoria para as próximas faturas."),
            },
        )
        salvar = st.form_submit_button("Salvar categorizações", type="primary")

    if not salvar:
        return

    ok_count, regras, erros = 0, 0, []
    for _, row in edited.iterrows():
        cat = str(row["Categoria"])
        if cat == REVIEW_SENTINEL or cat not in opcoes_cat:
            continue  # ainda não definido — ignora
        tx_id = str(row["ID"])
        ok, msg = definir_categoria_transacao_cartao(tx_id, cat)
        if not ok:
            erros.append(f"{row['Descrição'][:30]}: {msg}")
            continue
        ok_count += 1
        if bool(row["Criar regra"]):
            kw = id_to_merchant.get(tx_id, "")
            if kw:
                r_ok, _r_msg = add_card_category_rule(kw, cat)
                if r_ok:
                    regras += 1

    if ok_count:
        extra = f" · {regras} regra(s) aprendida(s)" if regras else ""
        st.success(f"✅ {ok_count} lançamento(s) categorizado(s){extra}.")
        st.rerun()
    for e in erros:
        st.error(e)
    if ok_count == 0 and not erros:
        st.info("Escolha uma categoria (≠ “A revisar”) em pelo menos uma linha.")


def _tab_cartao(d: dict, selected_year: int, selected_month: int) -> None:
    st.markdown(
        '<h2 style="font-size:1.45rem;font-weight:800;color:#E2E8F0;margin-bottom:0;">'
        'Cartao de Credito</h2>'
        '<p style="color:#718096;font-size:0.86rem;margin-top:4px;">'
        'Controle mensal da fatura, categorias de consumo, parcelas, estornos e tarifas.</p>',
        unsafe_allow_html=True,
    )

    all_cc_txs = get_transacoes_cartao_credito()
    df_all = _card_rows_dataframe(all_cc_txs)

    if df_all.empty:
        st.info("Importe uma fatura em Configurações > Fatura do Cartão para visualizar os indicadores e graficos do cartao.")
        return

    # Itens que o importador não soube classificar → o usuário define a categoria.
    _render_cartao_a_revisar(df_all)

    _secao_titulo("Filtros", "Cabecalho e filtros")
    filters = _render_card_filters(df_all, selected_year, selected_month)
    df = _apply_card_filters(df_all, filters)

    if df.empty:
        st.warning("Nenhum lancamento encontrado para os filtros selecionados.")
        return

    _secao_titulo("Resumo", "Resumo executivo da fatura")
    _render_summary_cards(df)
    st.markdown("<br>", unsafe_allow_html=True)

    cat_df = _prepare_category_analysis(df)
    merchant_df = _prepare_merchant_analysis(df)

    _secao_titulo("Graficos", "Graficos principais")
    col_cat, col_top = st.columns(2, gap="medium")
    with col_cat:
        st.markdown("**Distribuicao dos gastos por categoria**")
        if cat_df.empty:
            st.caption("Sem compras reais no filtro atual.")
        else:
            st.plotly_chart(_fig_donut_categoria(cat_df), use_container_width=True, config={"displayModeBar": False})
    with col_top:
        st.markdown("**Categorias que mais pesaram na fatura**")
        if cat_df.empty:
            st.caption("Sem categorias de consumo para exibir.")
        else:
            st.plotly_chart(_fig_horizontal_bar(cat_df, "Categoria", "Total (R$)", _COR_DESPESA), use_container_width=True, config={"displayModeBar": False})

    st.markdown("**Maiores gastos por estabelecimento**")
    if merchant_df.empty:
        st.caption("Sem estabelecimentos de compra para exibir.")
    else:
        st.plotly_chart(_fig_horizontal_bar(merchant_df, "Estabelecimento", "Total (R$)", _COR_INVEST, height=360), use_container_width=True, config={"displayModeBar": False})

    st.markdown("<br>", unsafe_allow_html=True)
    _secao_titulo("Parcelas", "Compras parceladas")
    installment_df = _prepare_installment_analysis(df)
    projection_df = _prepare_future_invoice_projection(df)
    if installment_df.empty:
        st.caption("Nenhuma compra parcelada no filtro atual.")
    else:
        col_parc_chart, col_parc_table = st.columns([1, 1.25], gap="medium")
        with col_parc_chart:
            st.plotly_chart(_fig_horizontal_bar(installment_df, "Estabelecimento", "Pendente estimado", _COR_INVEST, height=320), use_container_width=True, config={"displayModeBar": False})
        with col_parc_table:
            _render_money_dataframe(installment_df.head(12), ["Valor no mes", "Pendente estimado"])
        st.markdown("**Projecao de faturas futuras pelas parcelas restantes**")
        if projection_df.empty:
            st.caption("Nao ha parcelas futuras a projetar no filtro atual.")
        else:
            col_proj_chart, col_proj_table = st.columns([1, 1], gap="medium")
            with col_proj_chart:
                st.plotly_chart(_fig_future_projection(projection_df), use_container_width=True, config={"displayModeBar": False})
            with col_proj_table:
                _render_money_dataframe(projection_df, ["Valor projetado"])

    st.markdown("<br>", unsafe_allow_html=True)
    _secao_titulo("Ajustes", "Tarifas, estornos e pagamentos")
    non_df = _prepare_non_consumption(df)
    if non_df.empty:
        st.caption("Nenhuma tarifa, estorno, pagamento ou ajuste no filtro atual.")
    else:
        col_non_chart, col_non_table = st.columns([1, 1.35], gap="medium")
        with col_non_chart:
            st.plotly_chart(_fig_non_consumption(df), use_container_width=True, config={"displayModeBar": False})
        with col_non_table:
            _render_money_dataframe(non_df.head(20), ["Valor (R$)"])

    st.markdown("<br>", unsafe_allow_html=True)
    _secao_titulo("Evolucao", "Evolucao mensal dos gastos no cartao")
    monthly_points = df[df["tipo_lancamento"] == "compra"]["ano_mes"].nunique()
    if monthly_points < 2:
        st.caption("Ainda nao ha meses suficientes para comparar a evolucao.")
    else:
        st.plotly_chart(_fig_monthly_evolution(df), use_container_width=True, config={"displayModeBar": False})

    st.markdown("<br>", unsafe_allow_html=True)
    with st.expander("Tabela detalhada de lancamentos", expanded=False):
        detail = df.sort_values(["data_vencimento", "data_compra"], ascending=[False, False]).copy()

        modo_edicao = st.toggle(
            "✏️ Editar lançamentos",
            key="cc_detail_edit_mode",
            help="Ative para editar qualquer campo diretamente na tabela e salvar no banco.",
        )

        if modo_edicao:
            _editor_cartao_detalhado(detail)
        else:
            detail["Vencimento"] = detail["data_vencimento"].dt.strftime("%d/%m/%Y")
            detail["Compra"] = detail["data_compra"].dt.strftime("%d/%m/%Y")
            detail["Tipo"] = detail["tipo_lancamento"].str.title()
            detail["Valor fatura"] = detail["valor_fatura"]
            detail["Recorrencia"] = detail["recorrencia_status"].str.title()
            cols = [
                "Vencimento", "Compra", "final_cartao", "Tipo", "estabelecimento",
                "categoria", "installment_label", "Recorrencia", "Valor fatura", "source",
            ]
            display = detail[cols].rename(columns={
                "final_cartao": "Final",
                "estabelecimento": "Descricao",
                "categoria": "Categoria",
                "installment_label": "Parcela",
                "source": "Origem",
            })
            _render_money_dataframe(display, ["Valor fatura"])

    st.markdown("<br>", unsafe_allow_html=True)
    _secao_titulo("Insights", "Insights automaticos")
    _render_credit_card_insights(df, projection_df)

    # ── Analista Financeiro do Cartão (chat com IA) ───────────────────────────
    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown("<hr style='border-color:#1E2533;'>", unsafe_allow_html=True)
    _render_chat_cartao(df, df_all, filters)


def _evolucao_compras_mensal(df: pd.DataFrame) -> list[dict]:
    """Série [{label, total}] das compras do cartão por mês de vencimento."""
    compras = df[df["tipo_lancamento"] == "compra"].copy()
    if compras.empty:
        return []
    grp = (compras.groupby("ano_mes", as_index=False)
           .agg(total=("valor_fatura", "sum"),
                label=("mes_label", "first")))
    grp = grp.sort_values("ano_mes")
    return [{"label": str(r["label"]), "total": round(float(r["total"]), 2)}
            for _, r in grp.iterrows()]


def _render_chat_cartao(df: pd.DataFrame, df_all: pd.DataFrame, filters: dict) -> None:
    """
    Chat "Analista Financeiro do Cartão": conversa em linguagem natural sobre a
    FATURA (compras, categorias, estabelecimentos, parcelas, recorrências,
    projeções). Usa apenas os dados do usuário (filtrados por OWNER_USER_ID) e o
    mesmo protocolo de gráficos do chat de Análises.

    `df` = lançamentos filtrados (contexto principal); `df_all` = todas as faturas
    (usado só para a lista de assinaturas, que deve ser completa independentemente
    do filtro de mês/cartão).
    """
    from core.llm_b3 import llm_disponivel, provedores_disponiveis
    from core.llm_financeiro import chat_com_cartao, parse_chart_directives
    from core.llm_context_financeiro import build_cartao_chat_context
    from core.financeiro_chat_charts import (
        render_financas_charts, infer_cartao_chart_directives,
    )

    _secao_titulo("🤖", "Analista Financeiro do Cartão (IA)")
    st.markdown(
        '<p style="color:#718096;font-size:0.82rem;margin-top:2px;margin-bottom:12px;">'
        'Converse sobre a sua <b style="color:#E2E8F0">fatura</b>: categorias, '
        'estabelecimentos, assinaturas recorrentes, parcelas e projeções. A IA usa '
        'apenas os lançamentos do cartão (respeitando os filtros acima), mostra os '
        'cálculos e sinaliza quando algo é estimativa.</p>',
        unsafe_allow_html=True,
    )

    if not llm_disponivel():
        st.info(
            "IA indisponível: nenhum provedor LLM configurado. Defina `OPENAI_API_KEY` "
            "e/ou `GEMINI_API_KEY` no `.env` local ou em Streamlit Secrets."
        )
        return

    provider_labels = {"openai": "OpenAI", "gemini": "Gemini"}
    st.caption("Provedor(es): " + ", ".join(
        provider_labels.get(p, p) for p in provedores_disponiveis()))

    # Descrição do filtro ativo + assinatura de contexto (reinicia o chat se mudar).
    anos = sorted({int(a) for a in df["ano_vencimento"].dropna().unique()})
    filtro_label = (f"{len(df)} lançamento(s)"
                    + (f"; vencimentos {anos[0]}–{anos[-1]}" if anos else "")
                    + (f"; cartão final {filters.get('card')}" if filters.get("card") else ""))
    _ctx_sig = f"{filtro_label}|{round(float(df['valor_fatura'].sum()), 2)}"
    if st.session_state.get("cc_chat_ctx_sig") not in (None, _ctx_sig):
        st.session_state.pop("cc_chat_history", None)
    st.session_state["cc_chat_ctx_sig"] = _ctx_sig

    suggestions = [
        "Quais assinaturas recorrentes eu tenho e quanto somam por mês?",
        "Onde posso cortar na fatura sem afetar o essencial?",
        "Quanto das minhas compras é essencial vs não essencial?",
        "Projete minhas próximas faturas pelas parcelas em aberto.",
        "Quais estabelecimentos mais pesaram na fatura?",
        "Simule o impacto de cortar 20% dos gastos não essenciais.",
    ]
    suggested_input = None
    _sug_cols = st.columns(3)
    for i, q in enumerate(suggestions):
        with _sug_cols[i % 3]:
            if st.button(q, key=f"cc_chat_sug_{i}", use_container_width=True):
                suggested_input = q

    _, _clr = st.columns([5, 1])
    with _clr:
        if st.button("🗑️ Limpar", key="cc_chat_clear", use_container_width=True):
            st.session_state.pop("cc_chat_history", None)
            st.rerun()

    history: list[dict] = st.session_state.get("cc_chat_history", [])
    for msg in history:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
            for direc in msg.get("_charts", []) or []:
                try:
                    render_financas_charts([direc], msg.get("_chart_meta", {}))
                except Exception:
                    pass

    typed_input = st.chat_input("Pergunte sobre a sua fatura…", key="cc_chat_input")
    user_input = suggested_input or typed_input
    if not user_input:
        return

    history.append({"role": "user", "content": user_input})
    with st.chat_message("user"):
        st.markdown(user_input)

    with st.chat_message("assistant"):
        chart_directives: list[dict] = []
        chart_meta: dict = {}
        aviso_ancoragem = ""
        with st.spinner("Analisando a sua fatura…"):
            try:
                context, chart_meta = build_cartao_chat_context(
                    user_question=user_input,
                    resumo=_summary_credit_card(df),
                    categorias=_prepare_category_analysis(df).to_dict("records"),
                    estabelecimentos=_prepare_merchant_analysis(df).to_dict("records"),
                    parcelas=_prepare_installment_analysis(df).to_dict("records"),
                    projecao=_prepare_future_invoice_projection(df).to_dict("records"),
                    nao_consumo=_prepare_non_consumption(df).to_dict("records"),
                    evolucao_mensal=_evolucao_compras_mensal(df),
                    recorrentes=_prepare_recurring_analysis(df).to_dict("records"),
                    # Assinaturas: SEMPRE de todas as faturas (df_all), para a lista
                    # ser completa mesmo com filtro de mês/cartão ativo.
                    assinaturas=_prepare_subscriptions(df_all),
                    filtro_label=filtro_label,
                )
                resposta_raw = chat_com_cartao(context, history[:-1], user_input)
                resposta, chart_directives = parse_chart_directives(resposta_raw)
                if not chart_directives:
                    chart_directives = infer_cartao_chart_directives(user_input, chart_meta)
                aviso_ancoragem = _aviso_ancoragem(resposta, context)
            except Exception as exc:
                resposta = f"Não foi possível consultar a IA agora: {exc}"
        st.markdown(resposta)
        if aviso_ancoragem:
            st.caption(aviso_ancoragem)
        desenhados = 0
        if chart_directives:
            try:
                desenhados = render_financas_charts(chart_directives, chart_meta)
            except Exception as exc:
                st.caption(f"⚠️ Não foi possível gerar os gráficos: {exc}")
        st.caption("Análise educacional baseada nos seus dados; não é recomendação "
                   "de investimento nem garantia de resultado.")

    msg_assistant = {"role": "assistant", "content": resposta}
    if desenhados and chart_directives:
        msg_assistant["_charts"] = chart_directives[:2]
        msg_assistant["_chart_meta"] = chart_meta
    history.append(msg_assistant)
    st.session_state["cc_chat_history"] = history


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


def _indice_periodo_com_dados(opcoes: list[dict], historico: list[dict]) -> int:
    """Retorna o índice do mês mais recente realmente presente no histórico."""
    indices = {
        (int(opcao["ano"]), int(opcao["mes"])): indice
        for indice, opcao in enumerate(opcoes)
    }
    periodos_disponiveis: list[tuple[int, int]] = []
    for item in historico:
        try:
            periodo = (int(item["ano"]), int(item["mes"]))
        except (KeyError, TypeError, ValueError):
            continue
        if periodo in indices:
            periodos_disponiveis.append(periodo)

    if not periodos_disponiveis:
        return 0
    return indices[max(periodos_disponiveis)]


def render() -> None:
    # ── Seletor de mês no header ──────────────────────────────────────────────
    opcoes     = _opcoes_mes(12)
    periodos   = [(o["ano"], o["mes"]) for o in opcoes]
    labels_mes = {(o["ano"], o["mes"]): o["label"] for o in opcoes}
    historico  = get_cashflow_mensal()
    indice_padrao = _indice_periodo_com_dados(opcoes, historico)

    container_pagina(
        "Controle Financeiro",
        "Acompanhe renda, despesas, aportes e saldo mensal em uma única visão.",
        "💰",
        metadados=[("Atualizado", _date.today().strftime("%d/%m/%Y"))],
    )

    with st.container(border=True, key="cf_period_filter"):
        col_contexto, col_mes = st.columns([3, 1], vertical_alignment="center")
        with col_contexto:
            st.markdown("**Período de análise**")
            st.caption("Selecione o mês usado nos indicadores, gráficos e lançamentos.")
        with col_mes:
            st.caption("Mês de referência")
            periodo_selecionado = st.selectbox(
                "Mês",
                periodos,
                index=indice_padrao,
                format_func=lambda periodo: labels_mes[periodo],
                key="cf_periodo_ref",
                label_visibility="collapsed",
            )

    ano_selecionado, mes_selecionado = periodo_selecionado
    sel = next(
        opcao for opcao in opcoes
        if opcao["ano"] == ano_selecionado and opcao["mes"] == mes_selecionado
    )
    d   = get_controle(sel["ano"], sel["mes"])

    periodo_tem_dados = any(
        item.get("ano") == sel["ano"] and item.get("mes") == sel["mes"]
        for item in historico
    )
    if not periodo_tem_dados and d.get("data_source") == "real":
        st.info(
            f"Não há lançamentos liquidados em {sel['label']}. "
            "Os indicadores permanecem zerados até existirem movimentações nesse período."
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
    hist_anual   = get_historico_anual()
    evolucao     = get_evolucao_patrimonial()
    fluxo_inv    = {(f["ano"], f["mes"]): f["aporte"] for f in evolucao.get("fluxo_mensal", [])}

    # Investido no mês selecionado (de transfers para categorias de investimento)
    investido_mes = next(
        (h["investimentos"] for h in historico
         if h["ano"] == sel["ano"] and h["mes"] == sel["mes"]),
        0.0,
    )

    # Gastos com Pagamento de Cartão (apenas lançamentos manuais) — {ano_str: [items]}
    _ano_ref = sel["ano"]
    _anos_hist = hist_anual.get("anos", [_ano_ref])
    gastos_cartao: dict = {"todos": []}
    for _a in _anos_hist:
        _dados_a = get_gastos_cartao_mensal(_a)
        gastos_cartao[str(_a)] = _dados_a
        gastos_cartao["todos"].extend(_dados_a)

    # ── Sub-navegação (persistente entre reruns) ──────────────────────────────
    # st.tabs não expõe `key` e perde a aba ativa quando um widget interno
    # (ex.: filtro de categoria em Tabelas) dispara rerun — voltava sempre para
    # Dashboard. O segmented_control guarda a seção em session_state e sobrevive.
    _SECOES = ["📊  Dashboard", "📈  Análises", "🧾  Tabelas", "💳  Cartão de Crédito"]
    secao = st.segmented_control(
        "Seção",
        _SECOES,
        key="cf_secao_ativa",
        default=_SECOES[0],
        label_visibility="collapsed",
    ) or _SECOES[0]

    if secao == _SECOES[1]:
        _tab_analises(d, historico, hist_anual, gastos_cartao, investido_mes,
                      evolucao, sel["ano"], sel["mes"])
    elif secao == _SECOES[2]:
        _tab_tabelas(d)
    elif secao == _SECOES[3]:
        _tab_cartao(d, sel["ano"], sel["mes"])
    else:
        _tab_dashboard(d, historico, fluxo_inv, investido_mes)
