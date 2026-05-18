"""
views/dashboard_geral.py  — v3
Visão Geral consolidada: dados reais do DB, 3 domínios.

Layout:
  Row 1 — 3 cards: Patrimônio & Saúde · Fluxo Real do Mês · Investimentos
  Row 2 — Histórico 6 meses (Receitas × Despesas × Investimentos)
  Row 3 — Distribuição de despesas (ano) | Comparativo Ano a Ano
"""
from datetime import date as _date

import plotly.graph_objects as go
import streamlit as st

from core.controle import get_gastos_categoria_anual, get_historico_anual
from core.financeiro import get_visao_geral
from core.investimentos import get_carteira, get_cashflow_mensal, get_evolucao_patrimonial
from core.utils import fmt_moeda, fmt_percentual
from design.componentes import badge_status, container_pagina

# ── Paleta ────────────────────────────────────────────────────────────────────
_COR_PATRIMONIO = "#4A9EFF"
_COR_FLUXO      = "#00C896"
_COR_INVEST     = "#9B59B6"
_COR_ALERTA     = "#F6C90E"
_COR_NEGATIVO   = "#FC5C7D"
_COR_NEUTRO     = "#9CA3AF"

_CORES_CAT = [
    "#4C9BE8", "#E84C9B", "#F5A623", "#2ECC71", "#A855F7",
    "#7C3AED", "#63cab7", "#E8C94C", "#4CE8D8", "#E8714C",
]

_MESES_PT = {
    1: "Jan", 2: "Fev", 3: "Mar", 4: "Abr",
    5: "Mai", 6: "Jun", 7: "Jul", 8: "Ago",
    9: "Set", 10: "Out", 11: "Nov", 12: "Dez",
}


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS — Cards CSS
# ══════════════════════════════════════════════════════════════════════════════

def _cor_score(s: int) -> str:
    return "#00C896" if s >= 80 else "#4A9EFF" if s >= 60 else "#F6C90E" if s >= 40 else "#FC5C7D"


def _label_score(s: int) -> str:
    return "Ótimo" if s >= 80 else "Bom" if s >= 60 else "Atenção" if s >= 40 else "Crítico"


def _titulo_secao(icone: str, titulo: str, subtitulo: str, cor: str) -> None:
    st.markdown(f"""
    <div style="display:flex;align-items:flex-start;gap:12px;margin-bottom:16px;margin-top:8px;">
        <div style="width:3px;min-height:36px;background:{cor};
                    border-radius:2px;flex-shrink:0;margin-top:2px"></div>
        <div>
            <div style="font-size:1.05rem;font-weight:700;color:#E2E8F0">
                {icone} {titulo}
            </div>
            <div style="font-size:0.78rem;color:#4A5568;margin-top:1px">{subtitulo}</div>
        </div>
    </div>
    """, unsafe_allow_html=True)


def _linha_kv(label: str, valor: str, cor_val: str = "#E2E8F0") -> str:
    return f"""
    <div style="display:flex;justify-content:space-between;align-items:center;
                padding:5px 0;border-bottom:1px solid #1A1F2E;">
        <span style="font-size:0.80rem;color:#9CA3AF">{label}</span>
        <span style="font-size:0.88rem;font-weight:700;color:{cor_val}">{valor}</span>
    </div>"""


def _barra(pct: float, cor: str) -> str:
    w = min(pct, 100)
    return f"""
    <div style="background:#1E2533;border-radius:4px;height:5px;overflow:hidden;margin-top:4px">
        <div style="background:{cor};width:{w:.0f}%;height:100%;border-radius:4px"></div>
    </div>"""


def _card(borda_cor: str, corpo_html: str) -> None:
    st.markdown(f"""
    <div style="background:linear-gradient(160deg,rgba(20,24,38,0.97) 0%,rgba(14,17,26,1) 100%);
                border:1px solid rgba(255,255,255,0.07);border-top:4px solid {borda_cor};
                border-radius:14px;padding:22px 20px 18px;min-height:280px;
                box-shadow:0 4px 24px rgba(0,0,0,0.5),inset 0 1px 0 rgba(255,255,255,0.04);">
        {corpo_html}
    </div>""", unsafe_allow_html=True)


def _label_card(texto: str, cor: str) -> str:
    return (f'<div style="font-size:0.65rem;font-weight:800;text-transform:uppercase;'
            f'letter-spacing:0.14em;color:{cor};margin-bottom:14px">{texto}</div>')


def _titulo_valor(label: str, valor: str, cor: str = "#E2E8F0") -> str:
    return (f'<div style="font-size:0.78rem;color:#718096;margin-bottom:2px">{label}</div>'
            f'<div style="font-size:1.75rem;font-weight:800;color:{cor};'
            f'letter-spacing:-0.02em;margin-bottom:14px;line-height:1">{valor}</div>')


def _divisor() -> str:
    return '<div style="border-top:1px solid #1E2533;margin:12px 0"></div>'


# ══════════════════════════════════════════════════════════════════════════════
# CARDS
# ══════════════════════════════════════════════════════════════════════════════

def _card_patrimonio(pat: dict) -> None:
    score   = pat["saude_score"]
    cor_s   = _cor_score(score)
    label_s = _label_score(score)
    corpo = (
        _label_card("💰 Patrimônio & Saúde", _COR_PATRIMONIO)
        + _titulo_valor("Patrimônio Total", fmt_moeda(pat["total"]))
        + _linha_kv("💳 Saldo bancário",     fmt_moeda(pat["saldo_bancario"]))
        + _linha_kv("📈 Patrimônio investido", fmt_moeda(pat["investido"]), _COR_INVEST)
        + _divisor()
        + f'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:5px;">'
        + f'<span style="font-size:0.78rem;color:#718096">Saúde Financeira</span>'
        + f'<span style="font-size:0.90rem;font-weight:800;color:{cor_s}">'
        + f'{score}/100 &nbsp;<span style="font-size:0.72rem">{label_s}</span></span></div>'
        + _barra(score, cor_s)
    )
    _card(_COR_PATRIMONIO, corpo)


def _card_fluxo(receitas: float, despesas: float, investimentos: float) -> None:
    saldo     = receitas - despesas - investimentos
    taxa      = round(saldo / receitas * 100, 1) if receitas > 0 else 0.0
    cor_saldo = _COR_FLUXO if saldo >= 0 else _COR_NEGATIVO
    cor_taxa  = _COR_FLUXO if taxa >= 30 else _COR_ALERTA if taxa >= 15 else _COR_NEGATIVO
    taxa_w    = min(taxa / 30.0 * 100, 100)

    corpo = (
        _label_card("📊 Fluxo Real do Mês", _COR_FLUXO)
        + _linha_kv("↑ Receitas",     fmt_moeda(receitas),     _COR_FLUXO)
        + _linha_kv("↓ Despesas",     fmt_moeda(despesas),     _COR_NEGATIVO)
        + _linha_kv("📈 Investido",   fmt_moeda(investimentos), _COR_INVEST)
        + _divisor()
        + _titulo_valor("Saldo do mês (líquido)", fmt_moeda(saldo), cor_saldo)
        + f'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px;">'
        + f'<span style="font-size:0.78rem;color:#718096">Taxa de poupança</span>'
        + f'<span style="font-size:0.88rem;font-weight:700;color:{cor_taxa}">'
        + f'{fmt_percentual(taxa, sinal=False)} '
        + f'<span style="font-size:0.70rem;color:#4A5568">/ meta 30%</span></span></div>'
        + _barra(taxa_w, cor_taxa)
    )
    _card(_COR_FLUXO, corpo)


def _card_investimentos(pat: dict, classes: list, aportado_ano: float) -> None:
    total_inv = pat["investido"]
    n_classes = len(classes)

    linhas = ""
    for c in classes[:4]:
        cor_r = _COR_FLUXO if c["rentab_mes_pct"] >= 0 else _COR_NEGATIVO
        rent_str = f'+{c["rentab_mes_pct"]:.1f}%' if c["rentab_mes_pct"] > 0 else f'{c["rentab_mes_pct"]:.1f}%'
        linhas += _linha_kv(
            f'<span style="display:inline-flex;align-items:center;gap:6px;">'
            f'<span style="width:7px;height:7px;border-radius:50%;background:{c["cor"]};'
            f'display:inline-block"></span>{c["nome"]}'
            f'<span style="font-size:0.72rem;color:#4A5568">{c["pct_carteira"]:.1f}%</span></span>',
            fmt_moeda(c["valor"]),
        )

    corpo = (
        _label_card("📈 Investimentos", _COR_INVEST)
        + _titulo_valor("Patrimônio Investido", fmt_moeda(total_inv), _COR_INVEST)
        + linhas
        + _divisor()
        + _linha_kv("Aportado em 2026", fmt_moeda(aportado_ano), _COR_FLUXO)
        + _linha_kv("Classes de ativos", str(n_classes))
    )
    _card(_COR_INVEST, corpo)


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS — Portfolio
# ══════════════════════════════════════════════════════════════════════════════

def _is_exterior_position(pos: dict) -> bool:
    pais = str(pos.get("pais") or pos.get("country") or "BR").upper()
    moeda = str(pos.get("moeda") or "BRL").upper()
    return pais not in ("", "BR") or moeda != "BRL"


def _split_br_ext(posicoes: list) -> tuple[float, float]:
    br = sum(float(p.get("valor_mercado") or 0) for p in posicoes if not _is_exterior_position(p))
    ext = sum(float(p.get("valor_mercado") or 0) for p in posicoes if _is_exterior_position(p))
    return round(br, 2), round(ext, 2)


def _n_efetivo(posicoes: list) -> float:
    if not posicoes:
        return 0.0
    hhi = sum((float(p.get("pct_carteira") or 0) / 100) ** 2 for p in posicoes)
    return round(1 / hhi, 1) if hhi > 0 else 0.0


def _fig_donut_classes(classes: list) -> go.Figure:
    nomes = [c["nome"] for c in classes]
    valores = [c["valor"] for c in classes]
    cores = [c.get("cor", _CORES_CAT[i % len(_CORES_CAT)]) for i, c in enumerate(classes)]
    fig = go.Figure(go.Pie(
        labels=nomes, values=valores, hole=0.58,
        marker={"colors": cores, "line": {"color": "#0E1117", "width": 2}},
        textinfo="percent",
        textfont={"size": 11},
        hovertemplate="<b>%{label}</b><br>R$ %{value:,.2f}<br>%{percent}<extra></extra>",
    ))
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)", font_color=_COR_NEUTRO,
        showlegend=True,
        legend={"orientation": "v", "font": {"size": 10}, "bgcolor": "rgba(0,0,0,0)", "x": 1.02},
        margin={"t": 6, "b": 6, "l": 0, "r": 0}, height=285,
    )
    return fig


def _fig_br_exterior(br: float, ext: float) -> go.Figure:
    labels = ["Brasil", "Exterior"]
    valores = [br, ext]
    fig = go.Figure(go.Bar(
        x=labels, y=valores,
        marker={
            "color": [_COR_FLUXO, _COR_PATRIMONIO],
            "opacity": 0.9,
            "line": {"color": ["#00FFBB", "#7BC8FF"], "width": 1.5},
        },
        text=[fmt_moeda(v) for v in valores],
        textposition="outside",
        textfont={"size": 12, "color": ["#00C896", "#4A9EFF"]},
        hovertemplate="<b>%{x}</b><br>R$ %{y:,.2f}<extra></extra>",
    ))
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font_color=_COR_NEUTRO,
        margin={"t": 30, "b": 8, "l": 0, "r": 0}, height=250,
        yaxis={"gridcolor": "#1E2533", "tickformat": ",.0f", "tickprefix": "R$ "},
        xaxis={"showgrid": False, "tickfont": {"size": 13, "color": "#E2E8F0"}},
        showlegend=False,
    )
    return fig


def _fig_top_posicoes(posicoes: list) -> go.Figure:
    top = sorted(posicoes, key=lambda p: float(p.get("valor_mercado") or 0), reverse=True)[:10]
    top = list(reversed(top))
    labels = [p["ticker"] for p in top]
    valores = [float(p.get("valor_mercado") or 0) for p in top]
    pct = [float(p.get("pct_carteira") or 0) for p in top]
    cores = [p.get("cor", _COR_INVEST) for p in top]
    fig = go.Figure(go.Bar(
        x=valores, y=labels, orientation="h",
        marker_color=cores,
        text=[f"{v:.1f}%" for v in pct],
        textposition="outside",
        hovertemplate="<b>%{y}</b><br>R$ %{x:,.2f}<extra></extra>",
    ))
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font_color=_COR_NEUTRO,
        margin={"t": 8, "b": 8, "l": 0, "r": 0}, height=285,
        xaxis={"gridcolor": "#1E2533", "tickformat": ",.0f", "tickprefix": "R$ "},
        yaxis={"showgrid": False},
        showlegend=False,
    )
    return fig


def _fig_evolucao_investimentos(evolucao: dict) -> go.Figure:
    snapshots = evolucao.get("snapshots", []) if isinstance(evolucao, dict) else []
    labels    = [s.get("label") for s in snapshots]
    mercado   = [s.get("valor_mercado", 0.0)   for s in snapshots]
    investido = [s.get("valor_investido", 0.0) for s in snapshots]
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=labels, y=mercado, name="Valor de mercado",
        mode="lines+markers", fill="tozeroy",
        line={"color": _COR_FLUXO, "width": 3},
        fillcolor="rgba(0,200,150,0.10)",
        marker={"size": 8, "color": _COR_FLUXO,
                "line": {"color": "#00FFBB", "width": 1.5}},
        hovertemplate="<b>%{x}</b><br>Mercado: R$ %{y:,.2f}<extra></extra>",
    ))
    investido_vis = [v for v in investido if v > 0]
    if investido_vis:
        fig.add_trace(go.Scatter(
            x=labels, y=investido, name="Custo histórico",
            mode="lines+markers",
            line={"color": _COR_INVEST, "width": 2, "dash": "dot"},
            marker={"size": 6, "color": _COR_INVEST},
            hovertemplate="<b>%{x}</b><br>Investido: R$ %{y:,.2f}<extra></extra>",
        ))
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font_color=_COR_NEUTRO,
        legend={"orientation": "h", "y": -0.20, "font": {"size": 11},
                "bgcolor": "rgba(0,0,0,0)"},
        margin={"t": 8, "b": 8, "l": 0, "r": 0}, height=285,
        yaxis={"gridcolor": "rgba(30,37,51,0.8)", "tickformat": ",.0f",
               "tickprefix": "R$ "},
        xaxis={"showgrid": False},
    )
    return fig


def _mini_metric(label: str, valor: str, detalhe: str, cor: str) -> str:
    return f"""
    <div style="background:linear-gradient(135deg,rgba(18,21,30,0.95) 0%,rgba(20,25,45,0.9) 100%);
                border:1px solid rgba(255,255,255,0.06);border-left:4px solid {cor};
                border-radius:12px;padding:16px 16px;min-height:100px;
                box-shadow:0 2px 12px rgba(0,0,0,0.35),inset 0 1px 0 rgba(255,255,255,0.03);">
        <div style="font-size:0.64rem;text-transform:uppercase;letter-spacing:0.14em;
                    color:{cor};font-weight:800;margin-bottom:10px;opacity:0.85">{label}</div>
        <div style="font-size:1.45rem;font-weight:900;color:{cor};line-height:1;
                    text-shadow:0 0 18px {cor}55">{valor}</div>
        <div style="font-size:0.74rem;color:#6B7280;margin-top:9px;line-height:1.3">{detalhe}</div>
    </div>
    """


def _grafico_container_open(icone: str, titulo: str, cor: str) -> None:
    st.markdown(f"""
    <div style="background:linear-gradient(180deg,rgba(22,26,40,0.95) 0%,rgba(14,17,26,0.98) 100%);
                border:1px solid rgba(255,255,255,0.07);border-top:3px solid {cor};
                border-radius:14px;padding:18px 18px 4px;
                box-shadow:0 4px 20px rgba(0,0,0,0.4);">
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px;">
            <span style="font-size:1rem">{icone}</span>
            <span style="font-size:0.72rem;font-weight:800;text-transform:uppercase;
                         letter-spacing:0.12em;color:{cor}">{titulo}</span>
        </div>
    """, unsafe_allow_html=True)


def _grafico_container_close() -> None:
    st.markdown("</div>", unsafe_allow_html=True)


def _secao_raio_x_portfolio(carteira: dict, evolucao: dict, classes: list) -> None:
    posicoes = carteira.get("posicoes", [])
    if not posicoes:
        return

    total = float(carteira.get("total_mercado") or 0)
    br, ext = _split_br_ext(posicoes)
    pct_ext = (ext / total * 100) if total else 0.0
    n_eff = _n_efetivo(posicoes)
    top1 = max(posicoes, key=lambda p: float(p.get("pct_carteira") or 0))
    top5 = sum(float(p.get("pct_carteira") or 0) for p in sorted(
        posicoes, key=lambda p: float(p.get("pct_carteira") or 0), reverse=True
    )[:5])
    rentab = float(carteira.get("rentabilidade_total_pct") or 0)

    _titulo_secao(
        "📌", "Raio X do portfólio investido",
        "Alocação, diversificação, concentração e evolução do patrimônio", _COR_INVEST,
    )

    m1, m2, m3, m4 = st.columns(4, gap="small")
    with m1:
        st.markdown(_mini_metric("Rentabilidade", f"{rentab:+.2f}%", "Sobre o custo consolidado", _COR_FLUXO if rentab >= 0 else _COR_NEGATIVO), unsafe_allow_html=True)
    with m2:
        st.markdown(_mini_metric("Exterior", f"{pct_ext:.1f}%", f"{fmt_moeda(ext)} em ativos globais", _COR_PATRIMONIO), unsafe_allow_html=True)
    with m3:
        st.markdown(_mini_metric("N efetivo", f"{n_eff:.1f}", "Ativos equivalentes por diversificação", _COR_ALERTA if n_eff < 10 else _COR_FLUXO), unsafe_allow_html=True)
    with m4:
        st.markdown(_mini_metric("Top 5", f"{top5:.1f}%", f"Maior posição: {top1['ticker']} ({top1['pct_carteira']:.1f}%)", _COR_NEGATIVO if top5 > 50 else _COR_FLUXO), unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    c1, c2 = st.columns([1.05, 0.95], gap="medium")
    with c1:
        _grafico_container_open("🥧", "Alocação por classe", _COR_INVEST)
        st.plotly_chart(_fig_donut_classes(classes), use_container_width=True, config={"displayModeBar": False})
        _grafico_container_close()
    with c2:
        _grafico_container_open("🌎", "Brasil vs Exterior", _COR_PATRIMONIO)
        st.plotly_chart(_fig_br_exterior(br, ext), use_container_width=True, config={"displayModeBar": False})
        _grafico_container_close()

    st.markdown("<br>", unsafe_allow_html=True)

    c3, c4 = st.columns(2, gap="medium")
    with c3:
        _grafico_container_open("🏆", "Maiores posições", _COR_ALERTA)
        st.plotly_chart(_fig_top_posicoes(posicoes), use_container_width=True, config={"displayModeBar": False})
        _grafico_container_close()
    with c4:
        _grafico_container_open("📈", "Evolução dos investimentos", _COR_FLUXO)
        if evolucao.get("snapshots"):
            st.plotly_chart(_fig_evolucao_investimentos(evolucao), use_container_width=True, config={"displayModeBar": False})
        else:
            st.caption("Sem snapshots patrimoniais suficientes.")
        _grafico_container_close()

    st.markdown("<br>", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# GRÁFICOS
# ══════════════════════════════════════════════════════════════════════════════

def _fig_historico(hist: list) -> go.Figure:
    """Barras Receitas+Despesas + linha Investimentos (últimos 6 meses)."""
    labels = [f"{_MESES_PT[h['mes']]}/{str(h['ano'])[2:]}" for h in hist]
    rec    = [h["receitas"]      for h in hist]
    desp   = [h["despesas"]      for h in hist]
    inv    = [h.get("investimentos", 0.0) for h in hist]

    fig = go.Figure()
    fig.add_trace(go.Bar(
        name="Receitas", x=labels, y=rec, marker_color=_COR_FLUXO, opacity=0.85,
        hovertemplate="<b>Receitas %{x}</b><br>R$ %{y:,.2f}<extra></extra>",
    ))
    fig.add_trace(go.Bar(
        name="Despesas", x=labels, y=desp, marker_color=_COR_NEGATIVO, opacity=0.85,
        hovertemplate="<b>Despesas %{x}</b><br>R$ %{y:,.2f}<extra></extra>",
    ))
    fig.add_trace(go.Scatter(
        name="Investimentos", x=labels, y=inv, mode="lines+markers",
        line={"color": _COR_INVEST, "width": 2.5},
        marker={"size": 7, "color": _COR_INVEST},
        hovertemplate="<b>Investido %{x}</b><br>R$ %{y:,.2f}<extra></extra>",
        yaxis="y2",
    ))
    fig.update_layout(
        barmode="group",
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font_color=_COR_NEUTRO,
        legend={"orientation": "h", "y": -0.20, "font": {"size": 11},
                "bgcolor": "rgba(0,0,0,0)"},
        margin={"t": 10, "b": 10, "l": 0, "r": 0}, height=290,
        yaxis={"gridcolor": "#1E2533", "tickformat": ",.0f", "tickprefix": "R$ ",
               "showgrid": True},
        yaxis2={"overlaying": "y", "side": "right", "showgrid": False,
                "tickformat": ",.0f", "tickprefix": "R$ "},
        xaxis={"showgrid": False},
    )
    return fig


def _fig_donut_cats(cats: list) -> go.Figure:
    nomes  = [c["nome"]  for c in cats[:8]]
    gastos = [c["gasto"] for c in cats[:8]]
    cores  = _CORES_CAT[:len(nomes)]
    fig = go.Figure(go.Pie(
        labels=nomes, values=gastos, hole=0.55,
        marker={"colors": cores, "line": {"color": "#0E1117", "width": 2}},
        textinfo="percent", textfont={"size": 10},
        hovertemplate="<b>%{label}</b><br>R$ %{value:,.2f}<br>%{percent}<extra></extra>",
    ))
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)", font_color=_COR_NEUTRO,
        showlegend=True,
        legend={"orientation": "v", "font": {"size": 10},
                "bgcolor": "rgba(0,0,0,0)", "x": 1.0},
        margin={"t": 8, "b": 8, "l": 0, "r": 0}, height=240,
    )
    return fig


def _fig_yoy(por_ano: dict, anos: list) -> go.Figure:
    rec  = [por_ano[a]["receitas"]      for a in anos]
    desp = [por_ano[a]["despesas"]      for a in anos]
    inv  = [por_ano[a].get("investimentos", 0.0) for a in anos]
    anos_str = [str(a) for a in anos]

    fig = go.Figure()
    fig.add_trace(go.Bar(name="Receitas",      x=anos_str, y=rec,
                         marker_color=_COR_FLUXO,    opacity=0.85,
                         hovertemplate="R$ %{y:,.2f}<extra></extra>"))
    fig.add_trace(go.Bar(name="Despesas",      x=anos_str, y=desp,
                         marker_color=_COR_NEGATIVO, opacity=0.85,
                         hovertemplate="R$ %{y:,.2f}<extra></extra>"))
    fig.add_trace(go.Bar(name="Investimentos", x=anos_str, y=inv,
                         marker_color=_COR_INVEST,   opacity=0.85,
                         hovertemplate="R$ %{y:,.2f}<extra></extra>"))
    fig.update_layout(
        barmode="group",
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font_color=_COR_NEUTRO,
        legend={"orientation": "h", "y": -0.20, "font": {"size": 11},
                "bgcolor": "rgba(0,0,0,0)"},
        margin={"t": 10, "b": 10, "l": 0, "r": 0}, height=260,
        yaxis={"gridcolor": "#1E2533", "tickformat": ",.0f", "tickprefix": "R$ ",
               "showgrid": True},
        xaxis={"showgrid": False},
    )
    return fig


# ══════════════════════════════════════════════════════════════════════════════
# RENDER PRINCIPAL
# ══════════════════════════════════════════════════════════════════════════════

def render() -> None:
    # ── Dados ─────────────────────────────────────────────────────────────────
    try:
        d = get_visao_geral()
    except NotImplementedError as exc:
        st.error(f"**Banco não configurado.** {exc}")
        return

    hoje          = _date.today()
    hist_cashflow = get_cashflow_mensal()        # list[{ano, mes, receitas, despesas, investimentos}]
    hist_anual    = get_historico_anual()         # {anos, por_ano}
    carteira      = get_carteira()
    evolucao_inv  = get_evolucao_patrimonial()
    ano_atual     = hoje.year
    cats_ano      = get_gastos_categoria_anual(ano_atual)

    # Mês atual no cashflow
    cur = next(
        (h for h in hist_cashflow if h["ano"] == hoje.year and h["mes"] == hoje.month),
        None,
    )
    receitas_mes      = cur["receitas"]                if cur else d["fluxo_mes"]["receitas"]
    despesas_mes      = cur["despesas"]                if cur else d["fluxo_mes"]["despesas"]
    investimentos_mes = cur.get("investimentos", 0.0)  if cur else 0.0

    # Últimos 6 meses em ordem cronológica
    hist6 = sorted(hist_cashflow, key=lambda h: (h["ano"], h["mes"]))[-6:]

    pat     = d["patrimonio"]
    classes = d["classes_ativo"]

    # Aportado no ano corrente (do histórico anual)
    por_ano       = hist_anual.get("por_ano", {})
    aportado_ano  = por_ano.get(ano_atual, {}).get("investimentos", 0.0)

    # ── Fonte de dados ─────────────────────────────────────────────────────────
    fonte = d.get("data_source", "mock")
    badge_label, badge_tipo = (
        ("Dados reais",     "sucesso") if fonte == "real" else
        ("Fallback (mock)", "erro")    if fonte == "mock_fallback" else
        ("Modo mock",       "alerta")
    )
    mes_ref = f"{_MESES_PT[hoje.month]} {hoje.year}"

    # ── Cabeçalho ──────────────────────────────────────────────────────────────
    container_pagina("Dashboard Geral", f"Visão consolidada · {mes_ref}", "📊")

    col_b1, col_b2, col_b3, *_ = st.columns([1, 1, 1, 4])
    with col_b1:
        badge_status(badge_label, badge_tipo)
    with col_b2:
        badge_status(mes_ref, "info")
    with col_b3:
        cor_s_badge = "sucesso" if pat["saude_score"] >= 60 else "alerta" if pat["saude_score"] >= 40 else "erro"
        badge_status(f"Score {pat['saude_score']}/100", cor_s_badge)

    st.markdown("<br>", unsafe_allow_html=True)

    # ══════════════════════════════════════════════════════════════════════════
    # BLOCO 1 — 3 Cards
    # ══════════════════════════════════════════════════════════════════════════
    col1, col2 = st.columns(2, gap="medium")
    with col1:
        _card_fluxo(receitas_mes, despesas_mes, investimentos_mes)
    with col2:
        _card_investimentos(pat, classes, aportado_ano)

    st.markdown("<br>", unsafe_allow_html=True)

    # ══════════════════════════════════════════════════════════════════════════
    # BLOCO 2 — Raio X do portfólio investido
    # ══════════════════════════════════════════════════════════════════════════
    _secao_raio_x_portfolio(carteira, evolucao_inv, classes)

    # ══════════════════════════════════════════════════════════════════════════
    # BLOCO 3 — Histórico 6 meses
    # ══════════════════════════════════════════════════════════════════════════
    _titulo_secao(
        "💹", "Histórico mensal (6 meses)",
        "Receitas · Despesas · Investimentos por mês", _COR_FLUXO,
    )
    st.markdown(
        '<div style="background:#12151E;border:1px solid #1E2533;'
        'border-radius:12px;padding:16px 16px 8px">',
        unsafe_allow_html=True,
    )
    if hist6:
        st.plotly_chart(_fig_historico(hist6), use_container_width=True,
                        config={"displayModeBar": False})
    else:
        st.caption("Sem histórico disponível.")
    st.markdown("</div>", unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # ══════════════════════════════════════════════════════════════════════════
    # BLOCO 4 — Distribuição de despesas | Comparativo Ano a Ano
    # ══════════════════════════════════════════════════════════════════════════
    col_cats, col_yoy = st.columns(2, gap="medium")

    with col_cats:
        _titulo_secao(
            "🍕", f"Despesas por categoria ({ano_atual})",
            "Compras de cartão excluídas", _COR_NEGATIVO,
        )
        st.markdown(
            '<div style="background:#12151E;border:1px solid #1E2533;'
            'border-radius:12px;padding:16px">',
            unsafe_allow_html=True,
        )
        if cats_ano:
            st.plotly_chart(_fig_donut_cats(cats_ano), use_container_width=True,
                            config={"displayModeBar": False})
            total_cats = sum(c["gasto"] for c in cats_ano)
            for c in cats_ano[:5]:
                pct = c["gasto"] / total_cats * 100 if total_cats else 0
                st.markdown(
                    f'<div style="display:flex;justify-content:space-between;'
                    f'align-items:center;padding:4px 0;border-bottom:1px solid #1A1F2E;">'
                    f'<span style="font-size:0.79rem;color:#CBD5E0">{c["nome"]}</span>'
                    f'<span style="font-size:0.79rem;font-weight:600;color:#E2E8F0">'
                    f'{fmt_moeda(c["gasto"])} '
                    f'<span style="color:#4A5568;font-size:0.70rem">{pct:.1f}%</span>'
                    f'</span></div>',
                    unsafe_allow_html=True,
                )
            if len(cats_ano) > 5:
                restante = sum(c["gasto"] for c in cats_ano[5:])
                st.markdown(
                    f'<div style="font-size:0.75rem;color:#4A5568;margin-top:6px">'
                    f'+ {len(cats_ano)-5} outras categorias · {fmt_moeda(restante)}</div>',
                    unsafe_allow_html=True,
                )
        else:
            st.caption(f"Sem despesas registradas em {ano_atual}.")
        st.markdown("</div>", unsafe_allow_html=True)

    with col_yoy:
        anos = hist_anual.get("anos", [])
        _titulo_secao(
            "📅", "Comparativo Ano a Ano",
            "Receitas · Investimentos · Despesas acumuladas", _COR_PATRIMONIO,
        )
        st.markdown(
            '<div style="background:#12151E;border:1px solid #1E2533;'
            'border-radius:12px;padding:16px">',
            unsafe_allow_html=True,
        )
        if len(anos) >= 1:
            st.plotly_chart(_fig_yoy(por_ano, anos), use_container_width=True,
                            config={"displayModeBar": False})
            import pandas as pd
            rows = []
            for a in anos:
                rows.append({
                    "Ano":           str(a),
                    "Receitas":      f"R$ {por_ano[a]['receitas']:,.2f}".replace(",","X").replace(".",",").replace("X","."),
                    "Investimentos": f"R$ {por_ano[a].get('investimentos',0.0):,.2f}".replace(",","X").replace(".",",").replace("X","."),
                    "Despesas":      f"R$ {por_ano[a]['despesas']:,.2f}".replace(",","X").replace(".",",").replace("X","."),
                })
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        else:
            st.caption("Sem histórico anual disponível.")
        st.markdown("</div>", unsafe_allow_html=True)
