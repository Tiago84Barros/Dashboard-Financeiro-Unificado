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
import html
import re
import unicodedata

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from core.controle import (
    get_controle, get_opcoes_formulario, inserir_transacao,
    atualizar_transacao, get_historico_anual, get_transacoes_filtradas,
    get_gastos_cartao_mensal, get_gastos_categoria_anual,
    get_transacoes_cartao_credito,
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

_FORMAS_PGTO_SAIDA = ["Conta"]
_FORMAS_PGTO_TODOS = ["Conta"]
_MANUAL_CARD_TERMS = ("cartao", "credito", "fatura")
_CC_IMPORTED_SOURCES = {"csv"}

# Categorias pré-definidas por tipo (igual ao app original)
_CAT_ENTRADA = [
    "Salário", "Renda Extra", "Dividendos", "Reembolso", "Outros",
]
_CAT_SAIDA = [
    "Mercado", "Compras", "Condomínio", "Luz", "Internet", "Transporte",
    "Combustível", "Saúde", "Despesas Domésticas", "Lazer", "Assinaturas",
    "Educação", "Restaurante", "Financiamento", "Outros",
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
    """True para texto relacionado a cartao/fatura no lancamento manual."""
    text = _norm_ascii(value)
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

        # Resolve conta
        opcoes  = get_opcoes_formulario()
        contas  = [
            c for c in opcoes.get("contas", [])
            if c.get("tipo") != "credit_card" and c.get("type") != "credit_card"
        ]
        conta_id = contas[0]["id"] if contas else None
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

    # ── Taxa de Poupança Histórica ─────────────────────────────────────────────
    if historico:
        st.markdown("<br>", unsafe_allow_html=True)
        _secao_titulo("💹", "Taxa de Poupança Mensal (12 meses)")
        meses_labels = [h["label"] for h in historico]
        taxas = [
            round(
                (h["receitas"] - h["despesas"] - h.get("investimentos", 0.0))
                / h["receitas"] * 100,
                1,
            )
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

    _secao_titulo("Filtros", "Cabecalho e filtros")
    filters = _render_card_filters(df_all, selected_year, selected_month)
    df = _apply_card_filters(df_all, filters)

    if df.empty:
        st.warning("Nenhum lancamento encontrado para os filtros selecionados.")
        return

    _secao_titulo("Totais", "Totais anuais e mensais")

    annual_filters = filters.copy()
    annual_filters["year"] = "Todos"
    annual_filters["month"] = None
    df_anual = _apply_card_filters(df_all, annual_filters)
    anual = _prepare_annual_card_totals(df_anual)

    df_mes_compras = df[df["tipo_lancamento"] == "compra"]
    df_mes_tarifas = df[df["tipo_lancamento"] == "tarifa"]
    compras_mes = float(df_mes_compras["valor_fatura"].sum())
    tarifas_mes = float(df_mes_tarifas["valor_fatura"].sum())
    total_mes = compras_mes + tarifas_mes
    ticket_medio = compras_mes / len(df_mes_compras) if len(df_mes_compras) > 0 else 0.0
    n_transacoes = len(df_mes_compras)

    mes_num = filters.get("month")
    ano_fil = filters.get("year", "")
    if mes_num:
        mes_nome = _MESES_PT.get(int(mes_num), str(mes_num))
        label_mes = f"{mes_nome}/{str(ano_fil)[-2:]}" if ano_fil != "Todos" else mes_nome
    else:
        label_mes = str(ano_fil) if ano_fil != "Todos" else "Todos os meses"

    col_anual, col_mensal = st.columns([1.4, 1], gap="large")

    with col_anual:
        st.markdown("##### 📅 Total por ano")
        if anual.empty:
            st.caption("Sem dados anuais.")
        else:
            _render_money_dataframe(
                anual,
                ["Compras reais (R$)", "Tarifas (R$)", "Total (R$)"],
            )

    with col_mensal:
        st.markdown(f"##### 🗓️ Período filtrado — {label_mes}")
        st.markdown(
            f"""
<div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:8px;">
  <div style="background:#111827;border:1px solid #1F2937;border-radius:8px;padding:12px 14px;">
    <div style="font-size:0.68rem;font-weight:800;letter-spacing:.10em;color:#7890B2;text-transform:uppercase;">Compras reais</div>
    <div style="font-size:1.18rem;font-weight:900;color:#FC5C7D;margin-top:6px;">{fmt_moeda(compras_mes)}</div>
    <div style="font-size:0.72rem;color:#52607A;margin-top:4px;">{n_transacoes} transações</div>
  </div>
  <div style="background:#111827;border:1px solid #1F2937;border-radius:8px;padding:12px 14px;">
    <div style="font-size:0.68rem;font-weight:800;letter-spacing:.10em;color:#7890B2;text-transform:uppercase;">Ticket médio</div>
    <div style="font-size:1.18rem;font-weight:900;color:#E2E8F0;margin-top:6px;">{fmt_moeda(ticket_medio)}</div>
    <div style="font-size:0.72rem;color:#52607A;margin-top:4px;">por compra</div>
  </div>
  <div style="background:#111827;border:1px solid #1F2937;border-radius:8px;padding:12px 14px;">
    <div style="font-size:0.68rem;font-weight:800;letter-spacing:.10em;color:#7890B2;text-transform:uppercase;">Tarifas</div>
    <div style="font-size:1.18rem;font-weight:900;color:#F6C90E;margin-top:6px;">{fmt_moeda(tarifas_mes)}</div>
    <div style="font-size:0.72rem;color:#52607A;margin-top:4px;">anuidade, IOF, encargos</div>
  </div>
  <div style="background:#111827;border:1px solid #1F2937;border-radius:8px;padding:12px 14px;">
    <div style="font-size:0.68rem;font-weight:800;letter-spacing:.10em;color:#7890B2;text-transform:uppercase;">Total do mês</div>
    <div style="font-size:1.18rem;font-weight:900;color:#FC5C7D;margin-top:6px;">{fmt_moeda(total_mes)}</div>
    <div style="font-size:0.72rem;color:#52607A;margin-top:4px;">compras + tarifas</div>
  </div>
</div>
""",
            unsafe_allow_html=True,
        )

    st.markdown("<br>", unsafe_allow_html=True)
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

    # Investido no mês selecionado (de transfers para categorias de investimento)
    investido_mes = next(
        (h["investimentos"] for h in historico
         if h["ano"] == sel["ano"] and h["mes"] == sel["mes"]),
        0.0,
    )

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
        _tab_dashboard(d, historico, fluxo_inv, investido_mes)

    with tab2:
        _tab_analises(d, historico, hist_anual, gastos_cartao, investido_mes)

    with tab3:
        _tab_tabelas(d)

    with tab4:
        _tab_cartao(d, sel["ano"], sel["mes"])
