"""
pages/investimentos.py  — v3 (layout original replicado)

Replica as 4 seções do app dashboard-investimentos.streamlit.app:
  Tabs: Dashboard | Histórico | Carteira | Análise

Dashboard:
  6 KPIs — Patrimônio Total, BR Brasil, Exterior, Resultado Acumulado,
            Renda Recebida 12M, Dividend Yield 12M
  Card   — Nº de Ativos + N Efetivo (diversificação)
  Visão Geral — Estado da Carteira (análise qualitativa) + Evolução Patrimonial
  Análise de Risco — Radar de Risco + Ações Sugeridas
  Distribuição — Donut por classe + Barras por classe

Dados: core/investimentos + core/proventos
"""
from datetime import date as _date

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from core.investimentos import get_carteira, get_cashflow_mensal, get_evolucao_patrimonial
from core.proventos import get_proventos
from core.utils import fmt_moeda, fmt_percentual
from design.componentes import badge_status

# ── Paleta ────────────────────────────────────────────────────────────────────
_COR_POSITIVO = "#00C896"
_COR_NEGATIVO = "#FC5C7D"
_COR_INFO     = "#4A9EFF"
_COR_ALERTA   = "#F6C90E"
_COR_NEUTRO   = "#9CA3AF"
_COR_ROXO     = "#9B59B6"


@st.cache_data(ttl=86400)
def _get_logos(tickers: tuple) -> dict:
    """Busca logo URLs via brapi.dev (batch). Cache 24h. Retorna {ticker: url}."""
    import requests
    if not tickers:
        return {}
    try:
        symbols = ",".join(t for t in tickers if t)
        r = requests.get(
            f"https://brapi.dev/api/quote/{symbols}",
            params={"fundamental": "false", "dividends": "false"},
            timeout=10,
        )
        if not r.ok:
            return {}
        logos = {}
        for item in r.json().get("results", []):
            sym = item.get("symbol", "")
            url = (item.get("logourl") or item.get("logo_url") or "").strip()
            if sym and url.startswith("http"):
                logos[sym] = url
        return logos
    except Exception:
        return {}


# ── Fatores macro e coeficientes por classe ───────────────────────────────────
_MACRO_FATORES = [
    "Brasil / Risco Fiscal",
    "Selic / CDI / Juros",
    "Bolsa Brasil",
    "Câmbio / Dólar",
    "Renda Variável EUA",
    "Inflação / IPCA",
]

# Chaves em minúsculas (substring match contra cls["nome"].lower())
# Valores: [brasil, selic, bolsa_br, cambio, rv_eua, ipca]
_MACRO_COEF: dict[str, list] = {
    "ações":      [0.90, 0.20, 0.85, 0.10, 0.10, 0.30],
    "fii":        [0.85, 0.65, 0.25, 0.05, 0.05, 0.40],
    "fundo imob": [0.85, 0.65, 0.25, 0.05, 0.05, 0.40],
    "renda fixa": [0.60, 0.95, 0.05, 0.05, 0.05, 0.55],
    "tesouro":    [0.55, 0.95, 0.03, 0.03, 0.03, 0.65],
    "bdr":        [0.20, 0.10, 0.15, 0.80, 0.85, 0.10],
    "etf":        [0.20, 0.10, 0.20, 0.75, 0.80, 0.10],
    "cripto":     [0.20, 0.05, 0.15, 0.50, 0.60, 0.15],
    "default":    [0.55, 0.35, 0.35, 0.25, 0.20, 0.30],
}


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS — métricas e computed fields
# ══════════════════════════════════════════════════════════════════════════════

def _calc_n_efetivo(posicoes: list) -> float:
    """N efetivo = 1 / Σwi² (índice inverso de Herfindahl)."""
    if not posicoes:
        return 0.0
    hhi = sum((p["pct_carteira"] / 100) ** 2 for p in posicoes)
    return round(1 / hhi, 1) if hhi > 0 else 0.0


def _split_br_ext(posicoes: list) -> tuple:
    """Retorna (valor_br, valor_ext) separados por moeda."""
    br  = sum(p["valor_mercado"] for p in posicoes if p.get("moeda", "BRL") == "BRL")
    ext = sum(p["valor_mercado"] for p in posicoes if p.get("moeda", "BRL") != "BRL")
    return round(br, 2), round(ext, 2)


def _estado_carteira(carteira: dict, n_efetivo: float, pct_ext: float) -> list:
    """Gera análise qualitativa da carteira — lista de dicts {tipo, titulo, texto}."""
    analise  = []
    total    = carteira["total_mercado"]
    por_cls  = carteira.get("por_classe", [])
    rentab   = carteira["rentabilidade_total_pct"]

    # Diversificação
    if n_efetivo >= 10:
        analise.append({"tipo": "positivo",
                        "titulo": "POSITIVO",
                        "texto": f"Boa diversificação — N efetivo = {n_efetivo} ativos equivalentes."})
    elif n_efetivo >= 5:
        analise.append({"tipo": "info",
                        "titulo": "INFO",
                        "texto": f"Diversificação moderada — N efetivo = {n_efetivo} ativos equivalentes."})
    else:
        analise.append({"tipo": "alerta",
                        "titulo": "ATENÇÃO",
                        "texto": f"Baixa diversificação — N efetivo = {n_efetivo}. Considere ampliar para ≥ 10."})

    # Maior classe
    if por_cls:
        maior_cls = max(por_cls, key=lambda c: c["pct_carteira"])
        if maior_cls["pct_carteira"] <= 40:
            analise.append({"tipo": "info",
                            "titulo": "INFO",
                            "texto": f"Maior classe: {maior_cls['nome']} com "
                                     f"{maior_cls['pct_carteira']:.1f}% — dentro do limite de 40%."})
        else:
            analise.append({"tipo": "alerta",
                            "titulo": "ATENÇÃO",
                            "texto": f"Concentração alta: {maior_cls['nome']} com "
                                     f"{maior_cls['pct_carteira']:.1f}% (limite recomendado: 40%)."})

    # Exposição exterior
    if pct_ext > 0:
        analise.append({"tipo": "info",
                        "titulo": "INFO",
                        "texto": f"Exposição internacional: {pct_ext:.1f}% do patrimônio em moeda estrangeira."})

    # FIIs
    fii = next((c for c in por_cls if "FII" in c["nome"] or "fii" in c["nome"].lower()), None)
    if fii:
        if fii["pct_carteira"] > 0:
            analise.append({"tipo": "info",
                            "titulo": "INFO",
                            "texto": f"FIIs com {fii['pct_carteira']:.2f}% de peso — participação no portfólio."})

    # Resultado
    if rentab > 0:
        analise.append({"tipo": "positivo",
                        "titulo": "POSITIVO",
                        "texto": f"Portfólio com resultado positivo de +{rentab:.2f}% sobre o custo."})
    elif rentab < 0:
        analise.append({"tipo": "negativo",
                        "titulo": "ATENÇÃO",
                        "texto": f"Portfólio em queda de {rentab:.2f}% sobre o custo histórico."})
    else:
        analise.append({"tipo": "info",
                        "titulo": "INFO",
                        "texto": "Rentabilidade em 0% — importe cotações em Configurações > Cotações."})

    return analise


def _acoes_sugeridas(carteira: dict, n_efetivo: float, dy: float) -> list:
    """Gera lista de ações sugeridas baseadas nos dados do portfólio."""
    sugestoes = []
    por_cls   = carteira.get("por_classe", [])

    if carteira.get("posicoes"):
        sugestoes.append({
            "cor":   _COR_INFO,
            "tag":   "📋",
            "titulo": "Acompanhe os fundamentos dos ativos com maior peso",
            "texto":  "Revise regularmente os resultados trimestrais, payout e perspectivas dos ativos de maior participação.",
        })

    if not carteira["cotacoes_disponiveis"]:
        sugestoes.append({
            "cor":   _COR_ALERTA,
            "tag":   "⚡",
            "titulo": "Importe cotações para calcular rentabilidade real",
            "texto":  "Acesse Configurações > Cotações e execute a atualização via yfinance.",
        })

    if n_efetivo < 10:
        sugestoes.append({
            "cor":   _COR_ALERTA,
            "tag":   "📊",
            "titulo": "Considere aumentar a diversificação",
            "texto":  f"N efetivo atual: {n_efetivo}. Meta recomendada: ≥ 10 ativos equivalentes.",
        })

    if dy < 3 and dy >= 0:
        sugestoes.append({
            "cor":   _COR_NEUTRO,
            "tag":   "💵",
            "titulo": "Dividend Yield abaixo de 3%",
            "texto":  "Avalie incluir mais ativos pagadores de dividendos (FIIs, ações dividendeiras).",
        })

    if not sugestoes:
        sugestoes.append({
            "cor":   _COR_POSITIVO,
            "tag":   "✅",
            "titulo": "Portfólio bem estruturado",
            "texto":  "Continue monitorando e rebalanceando conforme a estratégia.",
        })

    return sugestoes


def _calc_dependencias_macro(por_classe: list) -> list:
    """Retorna exposição estimada (%) a cada fator macro, ponderada pela alocação."""
    if not por_classe:
        return []

    acumulado = [0.0] * len(_MACRO_FATORES)
    for cls in por_classe:
        nome_lower = cls["nome"].lower()
        coefs = _MACRO_COEF["default"]
        for key, vals in _MACRO_COEF.items():
            if key != "default" and key in nome_lower:
                coefs = vals
                break
        w = cls["pct_carteira"] / 100
        for i, c in enumerate(coefs):
            acumulado[i] += w * c * 100

    return [
        {"fator": f, "exposicao": round(acumulado[i], 1)}
        for i, f in enumerate(_MACRO_FATORES)
    ]


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS — Cards CSS (sem comentários HTML)
# ══════════════════════════════════════════════════════════════════════════════

def _kpi_macro(titulo: str, valor: str, sub: str, cor: str) -> str:
    """Card compacto para 7 colunas (macro)."""
    return (
        f'<div style="background:#12151E;border:1px solid #1E2533;'
        f'border-radius:8px;padding:13px 11px 9px;height:100%;">'
        f'<div style="font-size:0.54rem;font-weight:800;text-transform:uppercase;'
        f'letter-spacing:0.11em;color:#4A5568;margin-bottom:5px;">{titulo}</div>'
        f'<div style="font-size:1.15rem;font-weight:800;color:{cor};'
        f'letter-spacing:-0.02em;line-height:1.1;margin-bottom:3px;'
        f'white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">{valor}</div>'
        f'<div style="font-size:0.62rem;color:#4A5568;line-height:1.3;">{sub}</div>'
        f'</div>'
    )


def _kpi(titulo: str, valor: str, sub: str, cor: str, tag: str = "") -> str:
    tag_html = (
        f'<span style="font-size:0.65rem;font-weight:700;padding:1px 5px;'
        f'border-radius:3px;background:rgba(0,200,150,0.15);'
        f'color:{_COR_POSITIVO};margin-bottom:4px;display:inline-block;">{tag}</span><br>'
        if tag else ""
    )
    return (
        f'<div style="background:#12151E;border:1px solid #1E2533;'
        f'border-radius:10px;padding:18px 16px 14px;height:100%;">'
        f'<div style="font-size:0.60rem;font-weight:800;text-transform:uppercase;'
        f'letter-spacing:0.13em;color:#4A5568;margin-bottom:8px;">{titulo}</div>'
        f'{tag_html}'
        f'<div style="font-size:1.60rem;font-weight:800;color:{cor};'
        f'letter-spacing:-0.02em;line-height:1.1;margin-bottom:6px;">{valor}</div>'
        f'<div style="font-size:0.72rem;color:#4A5568;line-height:1.3;">{sub}</div>'
        f'</div>'
    )


def _estado_item(tipo: str, titulo: str, texto: str) -> str:
    cores = {
        "positivo": (_COR_POSITIVO, "✓"),
        "negativo": (_COR_NEGATIVO, "✗"),
        "alerta":   (_COR_ALERTA,   "⚠"),
        "info":     (_COR_INFO,     "i"),
    }
    cor, icone = cores.get(tipo, (_COR_NEUTRO, "·"))
    return (
        f'<div style="border-left:3px solid {cor};padding:8px 12px;'
        f'margin-bottom:8px;background:rgba(255,255,255,0.02);border-radius:0 6px 6px 0;">'
        f'<div style="font-size:0.65rem;font-weight:800;text-transform:uppercase;'
        f'letter-spacing:0.1em;color:{cor};margin-bottom:3px;">'
        f'{icone} {titulo}</div>'
        f'<div style="font-size:0.80rem;color:#CBD5E0;">{texto}</div>'
        f'</div>'
    )


def _secao_titulo_orig(icone: str, titulo: str, sub: str = "") -> None:
    """Título de seção estilo app original (ícone grande + texto)."""
    sub_html = (
        f'<div style="font-size:0.80rem;color:#718096;margin-top:2px;">{sub}</div>'
        if sub else ""
    )
    st.markdown(
        f'<div style="display:flex;align-items:center;gap:10px;'
        f'margin-top:24px;margin-bottom:12px;">'
        f'<span style="font-size:1.5rem">{icone}</span>'
        f'<div>'
        f'<span style="font-size:1.30rem;font-weight:800;color:#E2E8F0;">{titulo}</span>'
        f'{sub_html}'
        f'</div>'
        f'</div>',
        unsafe_allow_html=True,
    )


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS — Gráficos
# ══════════════════════════════════════════════════════════════════════════════

def _fig_evolucao(cashflow: list, total_atual: float) -> go.Figure:
    """Simula evolução patrimonial acumulando saldos mensais de trás para frente."""
    h = list(reversed(cashflow))      # mais recente primeiro
    vals = [total_atual]
    for c in h[:-1]:
        vals.append(vals[-1] - c["saldo"])  # desfaz cada mês

    vals  = list(reversed(vals))
    h     = list(reversed(h))
    meses = [c["label"] for c in h]

    fig = go.Figure(go.Scatter(
        x=meses, y=vals,
        mode="lines",
        line={"color": _COR_POSITIVO, "width": 2.5},
        fill="tozeroy",
        fillcolor="rgba(0,200,150,0.07)",
        hovertemplate="<b>%{x}</b><br>R$ %{y:,.0f}<extra></extra>",
    ))
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font_color=_COR_NEUTRO,
        margin={"t": 8, "b": 0, "l": 0, "r": 0},
        height=280,
        yaxis={"showgrid": True, "gridcolor": "#1E2533",
               "tickformat": ",.0f", "tickprefix": "R$ "},
        xaxis={"showgrid": False},
        showlegend=False,
    )
    return fig


def _fig_radar(carteira: dict, n_efetivo: float, pct_ext: float) -> go.Figure:
    """Radar de risco com 5 dimensões."""
    posicoes = carteira.get("posicoes", [])
    total    = carteira["total_mercado"]
    por_cls  = carteira.get("por_classe", [])

    # Maior posição individual
    max_pos = max((p["pct_carteira"] for p in posicoes), default=0)
    # Maior classe
    max_cls = max((c["pct_carteira"] for c in por_cls), default=0)
    # FII pct
    fii = next((c for c in por_cls if "FII" in c["nome"]), None)
    fii_pct = fii["pct_carteira"] if fii else 0

    # Normaliza 0-10 (10 = máximo risco para concentração, 10 = máximo diversificação para n_efetivo)
    dim_conc_ativo  = min(max_pos / 40 * 10, 10)          # alto = risco
    dim_conc_setor  = min(max_cls / 50 * 10, 10)           # alto = risco
    dim_cambial     = min(pct_ext / 30 * 10, 10)           # alto = exposição
    dim_fii         = min(fii_pct / 30 * 10, 10)           # informativo
    dim_diversif    = min(n_efetivo / 20 * 10, 10)         # alto = menos risco

    categorias = [
        "Concentração<br>Ativo Individual",
        "Concentração<br>Setorial",
        "Exposição<br>Cambial",
        "Exposição<br>FIIs",
        "Diversificação",
    ]
    valores = [dim_conc_ativo, dim_conc_setor, dim_cambial, dim_fii, dim_diversif]
    valores_closed = valores + [valores[0]]   # fecha o polígono
    cats_closed    = categorias + [categorias[0]]

    fig = go.Figure(go.Scatterpolar(
        r=valores_closed,
        theta=cats_closed,
        fill="toself",
        fillcolor="rgba(74,158,255,0.15)",
        line={"color": _COR_INFO, "width": 2},
        hovertemplate="<b>%{theta}</b><br>%{r:.1f}/10<extra></extra>",
    ))
    fig.update_layout(
        polar={
            "radialaxis": {
                "visible": True, "range": [0, 10],
                "gridcolor": "#1E2533", "linecolor": "#1E2533",
                "tickcolor": _COR_NEUTRO, "tickfont": {"size": 9},
            },
            "angularaxis": {"linecolor": "#1E2533", "gridcolor": "#1E2533",
                            "tickfont": {"size": 9, "color": _COR_NEUTRO}},
            "bgcolor": "rgba(0,0,0,0)",
        },
        paper_bgcolor="rgba(0,0,0,0)",
        font_color=_COR_NEUTRO,
        margin={"t": 20, "b": 20, "l": 30, "r": 30},
        height=280,
        showlegend=False,
    )
    return fig


def _fig_donut_classes(por_classe: list) -> go.Figure:
    nomes = [c["nome"]          for c in por_classe]
    vals  = [c["valor_mercado"] for c in por_classe]
    cores = [c["cor"]           for c in por_classe]

    fig = go.Figure(go.Pie(
        labels=nomes, values=vals, hole=0.52,
        marker={"colors": cores, "line": {"color": "#0E1117", "width": 2}},
        textinfo="percent", textfont={"size": 11},
        hovertemplate="<b>%{label}</b><br>R$ %{value:,.0f}<br>%{percent}<extra></extra>",
    ))
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        font_color=_COR_NEUTRO,
        legend={"font": {"size": 10}, "bgcolor": "rgba(0,0,0,0)"},
        margin={"t": 8, "b": 8, "l": 0, "r": 0},
        height=320,
    )
    return fig


def _fig_barras_classes(por_classe: list) -> go.Figure:
    nomes = [c["nome"]          for c in por_classe]
    vals  = [c["valor_mercado"] for c in por_classe]
    cores = [c["cor"]           for c in por_classe]

    # Rótulos sobre as barras
    texts = [f"R$ {v/1000:.0f}k" if v >= 1000 else fmt_moeda(v) for v in vals]

    fig = go.Figure(go.Bar(
        x=nomes, y=vals, marker_color=cores,
        text=texts, textposition="outside",
        textfont={"size": 10, "color": "#E2E8F0"},
        hovertemplate="<b>%{x}</b><br>R$ %{y:,.0f}<extra></extra>",
    ))
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font_color=_COR_NEUTRO,
        margin={"t": 30, "b": 10, "l": 0, "r": 0}, height=320,
        xaxis={"showgrid": False, "tickangle": -30},
        yaxis={"showgrid": True, "gridcolor": "#1E2533",
               "tickformat": ",.0f", "tickprefix": "R$ "},
        showlegend=False,
    )
    return fig


def _fig_cashflow_hist(cashflow: list) -> go.Figure:
    labels   = [c["label"]    for c in cashflow]
    receitas = [c["receitas"] for c in cashflow]
    despesas = [c["despesas"] for c in cashflow]
    saldos   = [c["saldo"]    for c in cashflow]

    fig = go.Figure()
    fig.add_trace(go.Bar(name="Receitas", x=labels, y=receitas,
                         marker_color=_COR_POSITIVO, opacity=0.85,
                         hovertemplate="<b>Receitas %{x}</b><br>R$ %{y:,.2f}<extra></extra>"))
    fig.add_trace(go.Bar(name="Despesas", x=labels, y=despesas,
                         marker_color=_COR_NEGATIVO, opacity=0.85,
                         hovertemplate="<b>Despesas %{x}</b><br>R$ %{y:,.2f}<extra></extra>"))
    fig.add_trace(go.Scatter(name="Saldo", x=labels, y=saldos,
                             mode="lines+markers",
                             line={"color": _COR_INFO, "width": 2},
                             marker={"size": 6},
                             yaxis="y2",
                             hovertemplate="<b>Saldo %{x}</b><br>R$ %{y:,.2f}<extra></extra>"))
    fig.update_layout(
        barmode="group",
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font_color=_COR_NEUTRO,
        legend={"orientation": "h", "y": -0.18, "font": {"size": 11},
                "bgcolor": "rgba(0,0,0,0)"},
        margin={"t": 10, "b": 10, "l": 0, "r": 0}, height=320,
        yaxis={"showgrid": True, "gridcolor": "#1E2533",
               "tickformat": ",.0f", "tickprefix": "R$ "},
        yaxis2={"overlaying": "y", "side": "right", "showgrid": False,
                "tickformat": ",.0f", "tickprefix": "R$ "},
        xaxis={"showgrid": False},
    )
    return fig


def _fig_aportes(fluxo: list, visao: str) -> go.Figure:
    """Barras de aportes mensais (últimos 12) ou anuais (histórico completo)."""
    if visao == "Mensal":
        data   = fluxo[-12:]
        labels = [d["label"]  for d in data]
        vals   = [d["aporte"] for d in data]
    else:
        agg: dict[int, float] = {}
        for d in fluxo:
            agg[d["ano"]] = round(agg.get(d["ano"], 0.0) + d["aporte"], 2)
        labels = [str(k) for k in sorted(agg)]
        vals   = [agg[k] for k in sorted(agg)]

    cores = [_COR_POSITIVO if v >= 0 else _COR_NEGATIVO for v in vals]
    texts = [
        f"R$ {v/1000:.1f}k" if abs(v) >= 1000 else f"R$ {v:.0f}"
        for v in vals
    ]

    fig = go.Figure(go.Bar(
        x=labels, y=vals,
        marker_color=cores,
        text=texts,
        textposition="outside",
        textfont={"size": 10, "color": "#E2E8F0"},
        hovertemplate="<b>%{x}</b><br>Aporte líquido: R$ %{y:,.0f}<extra></extra>",
    ))
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font_color=_COR_NEUTRO,
        margin={"t": 30, "b": 0, "l": 0, "r": 0},
        height=300,
        xaxis={"showgrid": False, "tickangle": -30 if visao == "Mensal" else 0},
        yaxis={"showgrid": True, "gridcolor": "#1E2533",
               "tickformat": ",.0f", "tickprefix": "R$ "},
        showlegend=False,
    )
    return fig


def _fig_evolucao_patrimonial(snapshots: list) -> go.Figure:
    """Três linhas: Valor de Mercado, Com Dividendos, Valor Investido."""
    labels    = [s["label"]                for s in snapshots]
    investido = [s["valor_investido"]      for s in snapshots]
    mercado   = [s["valor_mercado"]        for s in snapshots]
    com_div   = [s["valor_com_dividendos"] for s in snapshots]

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=labels, y=com_div,
        name="Com Dividendos",
        mode="lines",
        line={"color": _COR_ALERTA, "width": 2},
        hovertemplate="<b>%{x}</b><br>c/ Dividendos: R$ %{y:,.0f}<extra></extra>",
    ))
    fig.add_trace(go.Scatter(
        x=labels, y=mercado,
        name="Valor de Mercado",
        mode="lines",
        line={"color": _COR_POSITIVO, "width": 2.5},
        fill="tozeroy",
        fillcolor="rgba(0,200,150,0.06)",
        hovertemplate="<b>%{x}</b><br>Mercado: R$ %{y:,.0f}<extra></extra>",
    ))
    fig.add_trace(go.Scatter(
        x=labels, y=investido,
        name="Valor Investido",
        mode="lines",
        line={"color": _COR_NEUTRO, "width": 1.5, "dash": "dot"},
        hovertemplate="<b>%{x}</b><br>Investido: R$ %{y:,.0f}<extra></extra>",
    ))
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font_color=_COR_NEUTRO,
        legend={"orientation": "h", "y": -0.15, "font": {"size": 11},
                "bgcolor": "rgba(0,0,0,0)"},
        margin={"t": 10, "b": 10, "l": 0, "r": 0},
        height=380,
        yaxis={"showgrid": True, "gridcolor": "#1E2533",
               "tickformat": ",.0f", "tickprefix": "R$ "},
        xaxis={"showgrid": False},
    )
    return fig


def _fig_dependencias_macro(deps: list) -> go.Figure:
    """Gráfico de barras horizontais — exposição macro do portfólio."""
    fatores = [d["fator"]    for d in deps]
    valores = [d["exposicao"] for d in deps]
    cores   = [
        _COR_NEGATIVO if v >= 70 else
        _COR_ALERTA   if v >= 50 else
        _COR_INFO
        for v in valores
    ]

    fig = go.Figure(go.Bar(
        x=valores, y=fatores,
        orientation="h",
        marker_color=cores,
        text=[f"{v:.1f}%" for v in valores],
        textposition="outside",
        textfont={"size": 11, "color": "#E2E8F0"},
        hovertemplate="<b>%{y}</b><br>Exposição: %{x:.1f}%<extra></extra>",
    ))
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font_color=_COR_NEUTRO,
        margin={"t": 10, "b": 10, "l": 0, "r": 70},
        height=260,
        xaxis={"showgrid": True, "gridcolor": "#1E2533",
               "range": [0, 115], "ticksuffix": "%"},
        yaxis={"showgrid": False, "automargin": True},
        showlegend=False,
    )
    return fig


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS — Dados externos (BCB + yfinance, cache 30 min)
# ══════════════════════════════════════════════════════════════════════════════

@st.cache_data(ttl=1800)
def _get_macro_dados() -> dict:
    """Busca indicadores macro: BCB (SELIC, IPCA) + yfinance (câmbio, bolsas)."""
    import requests  # já é dep do streamlit
    import yfinance as yf

    dados = {
        "selic":     14.75,
        "ipca_12m":  4.80,
        "cdi_12m":   14.65,
        "usdbrl":    5.75,
        "ibovespa":  130000.0,
        "sp500":     5500.0,
        "ifix":      3200.0,
    }

    # BCB: Meta SELIC (série 4189)
    try:
        r = requests.get(
            "https://api.bcb.gov.br/dados/serie/bcdata.sgs.4189/dados/ultimos/1?formato=json",
            timeout=5,
        )
        if r.ok and r.json():
            dados["selic"] = float(r.json()[0]["valor"].replace(",", "."))
    except Exception:
        pass

    # BCB: IPCA acumulado 12M (série 13522)
    try:
        r = requests.get(
            "https://api.bcb.gov.br/dados/serie/bcdata.sgs.13522/dados/ultimos/1?formato=json",
            timeout=5,
        )
        if r.ok and r.json():
            dados["ipca_12m"] = float(r.json()[0]["valor"].replace(",", "."))
    except Exception:
        pass

    # CDI ≈ SELIC − 0,10 p.p.
    dados["cdi_12m"] = round(dados["selic"] - 0.10, 2)

    # yfinance: câmbio e bolsas
    for sym, key in [
        ("USDBRL=X", "usdbrl"),
        ("^BVSP",    "ibovespa"),
        ("^GSPC",    "sp500"),
    ]:
        try:
            hist = yf.Ticker(sym).history(period="5d")
            if not hist.empty:
                dados[key] = round(float(hist["Close"].iloc[-1]), 2)
        except Exception:
            pass

    return dados


# ══════════════════════════════════════════════════════════════════════════════
# TABS
# ══════════════════════════════════════════════════════════════════════════════

def _tab_dashboard(carteira: dict, proventos: dict, cashflow: list) -> None:
    posicoes   = carteira.get("posicoes", [])
    por_classe = carteira.get("por_classe", [])

    n_efetivo   = _calc_n_efetivo(posicoes)
    br, ext     = _split_br_ext(posicoes)
    total       = carteira["total_mercado"]
    invest      = carteira["total_investido"]
    resultado   = round(total - invest, 2)
    rentab_pct  = carteira["rentabilidade_total_pct"]
    pct_br      = round(br  / total * 100, 2) if total > 0 else 0
    pct_ext     = round(ext / total * 100, 2) if total > 0 else 0
    dy          = round(proventos["total_ano"] / total * 100, 2) if total > 0 else 0

    cor_res = _COR_POSITIVO if resultado >= 0 else _COR_NEGATIVO

    # ── Linha 1: 4 KPI cards ─────────────────────────────────────────────────
    c1, c2, c3, c4 = st.columns(4, gap="small")
    with c1:
        st.markdown(_kpi(
            "Patrimônio Total", fmt_moeda(total),
            f"{carteira['num_ativos']} posições ativas",
            "#E2E8F0",
        ), unsafe_allow_html=True)
    with c2:
        st.markdown(_kpi(
            "BR Brasil", fmt_moeda(br),
            f"{pct_br:.2f}% do patrimônio",
            _COR_POSITIVO,
        ), unsafe_allow_html=True)
    with c3:
        st.markdown(_kpi(
            "Exterior", fmt_moeda(ext),
            f"{pct_ext:.2f}% do patrimônio",
            _COR_INFO,
        ), unsafe_allow_html=True)
    with c4:
        st.markdown(_kpi(
            "Resultado Acumulado",
            f"{'+' if resultado >= 0 else ''}{fmt_moeda(resultado)}",
            f"{'+' if rentab_pct >= 0 else ''}{rentab_pct:.2f}%",
            cor_res,
        ), unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # ── Linha 2: 3 KPI cards ─────────────────────────────────────────────────
    c5, c6, c7 = st.columns(3, gap="small")
    with c5:
        st.markdown(_kpi(
            "Renda Recebida (12M)", fmt_moeda(proventos["total_ano"]),
            "Proventos dos últimos 12 meses",
            _COR_ALERTA,
        ), unsafe_allow_html=True)
    with c6:
        st.markdown(_kpi(
            "Dividend Yield (12M)",
            fmt_percentual(dy, sinal=False),
            "Renda 12m / Patrimônio atual",
            _COR_ROXO,
        ), unsafe_allow_html=True)
    with c7:
        st.markdown(_kpi(
            "Nº de Ativos",
            str(carteira["num_ativos"]),
            f"N efetivo: {n_efetivo}",
            "#E2E8F0",
        ), unsafe_allow_html=True)

    # ── Visão Geral ───────────────────────────────────────────────────────────
    _secao_titulo_orig("🗂️", "Visão Geral")

    col_estado, col_evolucao = st.columns([1, 1], gap="medium")

    with col_estado:
        st.markdown(
            '<div style="font-size:0.83rem;font-weight:700;color:#E2E8F0;'
            'margin-bottom:10px;">🌐 Estado da Carteira</div>',
            unsafe_allow_html=True,
        )
        analise = _estado_carteira(carteira, n_efetivo, pct_ext)
        for item in analise:
            st.markdown(_estado_item(item["tipo"], item["titulo"], item["texto"]),
                        unsafe_allow_html=True)

    with col_evolucao:
        st.markdown(
            '<div style="font-size:0.83rem;font-weight:700;color:#E2E8F0;'
            'margin-bottom:10px;">📐 Evolução Patrimonial</div>',
            unsafe_allow_html=True,
        )
        if cashflow:
            st.plotly_chart(_fig_evolucao(cashflow, total),
                            use_container_width=True,
                            config={"displayModeBar": False})
            st.caption("Evolução estimada com base no fluxo de caixa mensal acumulado.")
        else:
            st.caption("Sem dados de fluxo de caixa.")

    # ── Análise de Risco ──────────────────────────────────────────────────────
    _secao_titulo_orig("🎯", "Análise de Risco")

    col_radar, col_acoes = st.columns([1, 1], gap="medium")

    with col_radar:
        st.markdown(
            '<div style="font-size:0.83rem;font-weight:700;color:#E2E8F0;'
            'margin-bottom:8px;">🎯 Radar de Risco</div>',
            unsafe_allow_html=True,
        )
        st.plotly_chart(_fig_radar(carteira, n_efetivo, pct_ext),
                        use_container_width=True,
                        config={"displayModeBar": False})
        st.caption("0 = mínimo · 10 = máximo para cada dimensão")

    with col_acoes:
        st.markdown(
            '<div style="font-size:0.83rem;font-weight:700;color:#E2E8F0;'
            'margin-bottom:8px;">✅ Ações Sugeridas</div>',
            unsafe_allow_html=True,
        )
        sugestoes = _acoes_sugeridas(carteira, n_efetivo, dy)
        for s in sugestoes:
            st.markdown(
                f'<div style="border-left:3px solid {s["cor"]};'
                f'padding:10px 12px;margin-bottom:10px;'
                f'background:rgba(255,255,255,0.02);border-radius:0 6px 6px 0;">'
                f'<div style="font-size:0.78rem;font-weight:700;color:{s["cor"]};'
                f'margin-bottom:3px;">{s["tag"]} {s["titulo"]}</div>'
                f'<div style="font-size:0.78rem;color:#9CA3AF;">{s["texto"]}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )

    # ── Distribuição do Portfólio ─────────────────────────────────────────────
    _secao_titulo_orig(
        "🟠", "Distribuição do Portfólio",
        "Por tipo de investimento. Valores de mercado atuais.",
    )

    if por_classe:
        col_donut, col_barras = st.columns([1, 1], gap="medium")
        with col_donut:
            st.markdown(
                '<div style="font-size:0.83rem;color:#9CA3AF;'
                'margin-bottom:8px;">Por tipo de investimento</div>',
                unsafe_allow_html=True,
            )
            st.plotly_chart(_fig_donut_classes(por_classe),
                            use_container_width=True,
                            config={"displayModeBar": False})
        with col_barras:
            st.markdown(
                '<div style="font-size:0.83rem;color:#9CA3AF;'
                'margin-bottom:8px;">Valores de mercado por tipo</div>',
                unsafe_allow_html=True,
            )
            st.plotly_chart(_fig_barras_classes(por_classe),
                            use_container_width=True,
                            config={"displayModeBar": False})
    else:
        st.caption("Sem dados de alocação por classe.")

    # ── Cenário Macroeconômico ─────────────────────────────────────────────────
    _secao_titulo_orig(
        "🌐", "Cenário Macroeconômico",
        "Dados atualizados a cada 30 minutos",
    )

    macro = _get_macro_dados()
    cm1, cm2, cm3, cm4, cm5, cm6, cm7 = st.columns(7, gap="small")
    with cm1:
        st.markdown(_kpi_macro("SELIC", f"{macro['selic']:.2f}%",
                               "Meta SELIC a.a.", _COR_NEGATIVO),
                    unsafe_allow_html=True)
    with cm2:
        st.markdown(_kpi_macro("IPCA 12M", f"{macro['ipca_12m']:.2f}%",
                               "Acumulado 12 meses", _COR_ALERTA),
                    unsafe_allow_html=True)
    with cm3:
        st.markdown(_kpi_macro("CDI 12M", f"{macro['cdi_12m']:.2f}%",
                               "Taxa CDI anual", _COR_ALERTA),
                    unsafe_allow_html=True)
    with cm4:
        st.markdown(_kpi_macro("USD / BRL", f"R$ {macro['usdbrl']:.4f}",
                               "Câmbio atual", _COR_INFO),
                    unsafe_allow_html=True)
    with cm5:
        ibov_k = macro["ibovespa"] / 1000
        st.markdown(_kpi_macro("IBOVESPA", f"{ibov_k:,.1f}k",
                               "Índice Bovespa (pts)", _COR_POSITIVO),
                    unsafe_allow_html=True)
    with cm6:
        sp_k = macro["sp500"] / 1000
        st.markdown(_kpi_macro("S&P 500", f"{sp_k:,.1f}k",
                               "Índice S&P 500 (pts)", _COR_POSITIVO),
                    unsafe_allow_html=True)
    with cm7:
        ifix_k = macro["ifix"] / 1000
        st.markdown(_kpi_macro("IFIX", f"{ifix_k:,.3f}k",
                               "Índice de FIIs", _COR_ROXO),
                    unsafe_allow_html=True)

    st.caption(
        "Fontes: BCB API (SELIC, IPCA) · Yahoo Finance (câmbio, bolsas). "
        "IFIX exibe valor de referência estático."
    )

    # ── Dependências Macro do Portfólio ────────────────────────────────────────
    _secao_titulo_orig(
        "📐", "Dependências Macro do Portfólio",
        "Exposição estimada por fator macroeconômico — baseado na alocação por classe",
    )

    deps = _calc_dependencias_macro(por_classe)
    if deps:
        fator_max = max(deps, key=lambda d: d["exposicao"])
        if fator_max["exposicao"] >= 60:
            st.warning(
                f"⚠️ Alta dependência em **{fator_max['fator']}** "
                f"({fator_max['exposicao']:.1f}%) — considere diversificar para reduzir concentração.",
            )

        col_chart, col_leg = st.columns([2, 1], gap="medium")
        with col_chart:
            st.plotly_chart(_fig_dependencias_macro(deps),
                            use_container_width=True,
                            config={"displayModeBar": False})
        with col_leg:
            st.markdown(
                '<div style="font-size:0.78rem;color:#718096;padding-top:16px;">'
                '<b style="color:#CBD5E0;">Como interpretar</b><br><br>'
                'Cada barra mostra quanto o portfólio pode ser afetado por um fator '
                'macroeconômico, ponderado pela composição por classe de ativo.<br><br>'
                f'<span style="color:{_COR_NEGATIVO};">■</span> ≥ 70% — alta exposição<br>'
                f'<span style="color:{_COR_ALERTA};">■</span> 50–70% — moderada<br>'
                f'<span style="color:{_COR_INFO};">■</span> &lt; 50% — controlada'
                '</div>',
                unsafe_allow_html=True,
            )
        st.caption(
            "Exposição estimada por coeficientes fixos por classe. "
            "Valores indicativos — não constituem recomendação de investimento."
        )
    else:
        st.caption("Sem dados de alocação para calcular dependências macro.")


def _tab_historico(cashflow: list, proventos: dict, evolucao: dict) -> None:
    st.markdown("<br>", unsafe_allow_html=True)

    snapshots = evolucao.get("snapshots", [])

    # ── Evolução Patrimonial ──────────────────────────────────────────────────
    _secao_titulo_orig(
        "📈", "Evolução Patrimonial",
        "Valor de Mercado e total com Dividendos acumulados — histórico completo",
    )
    if snapshots:
        # KPI summary row
        ck1, ck2, ck3 = st.columns(3, gap="small")
        with ck1:
            st.markdown(_kpi(
                "Valor de Mercado Atual", fmt_moeda(evolucao["total_mercado"]),
                "Estimativa baseada na rentabilidade atual", _COR_POSITIVO,
            ), unsafe_allow_html=True)
        with ck2:
            st.markdown(_kpi(
                "Total Com Dividendos",
                fmt_moeda(evolucao["total_mercado"] + evolucao["total_dividendos"]),
                "Mercado + proventos históricos acumulados", _COR_ALERTA,
            ), unsafe_allow_html=True)
        with ck3:
            st.markdown(_kpi(
                "Total Investido (custo)",
                fmt_moeda(evolucao["total_investido"]),
                "Custo histórico acumulado líquido (compras − vendas)", _COR_NEUTRO,
            ), unsafe_allow_html=True)

        st.markdown("<br>", unsafe_allow_html=True)
        st.plotly_chart(_fig_evolucao_patrimonial(snapshots),
                        use_container_width=True,
                        config={"displayModeBar": False})
        st.caption(
            "Valor de Mercado estimado: custo acumulado × rentabilidade atual da carteira. "
            "Com Dividendos = Mercado + proventos históricos recebidos."
        )
    else:
        st.info("Sem dados históricos de transações para exibir.", icon="📈")

    st.markdown("<br>", unsafe_allow_html=True)

    # Proventos — seleção Mensal / Anual
    hist_prov = proventos.get("historico_mensal", [])

    col_prov_title, col_prov_vis = st.columns([3, 1])
    with col_prov_title:
        _secao_titulo_orig("💵", "Proventos", "Dividendos e rendimentos recebidos")
    with col_prov_vis:
        st.markdown("<br>", unsafe_allow_html=True)
        visao_prov = st.selectbox(
            "Visualização",
            ["Mensal", "Anual"],
            key="inv_hist_prov_visao",
            label_visibility="collapsed",
        )

    if hist_prov:
        if visao_prov == "Mensal":
            labels_p = [h["label"] for h in hist_prov]
            vals_p   = [h["total"] for h in hist_prov]
        else:
            # Agrega por ano
            agg: dict[int, float] = {}
            for h in hist_prov:
                agg[h["ano"]] = agg.get(h["ano"], 0.0) + h["total"]
            labels_p = [str(a) for a in sorted(agg)]
            vals_p   = [round(agg[a], 2) for a in sorted(agg)]

        fig_prov = go.Figure(go.Bar(
            x=labels_p, y=vals_p,
            marker_color=_COR_ALERTA, opacity=0.85,
            text=[f"R$ {v:,.0f}".replace(",", ".") for v in vals_p],
            textposition="outside",
            textfont={"size": 10, "color": "#E2E8F0"},
            hovertemplate="<b>%{x}</b><br>R$ %{y:,.2f}<extra></extra>",
        ))
        fig_prov.update_layout(
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            font_color=_COR_NEUTRO,
            margin={"t": 30, "b": 0, "l": 0, "r": 0}, height=260,
            xaxis={"showgrid": False},
            yaxis={"showgrid": True, "gridcolor": "#1E2533",
                   "tickformat": ",.0f", "tickprefix": "R$ "},
            showlegend=False,
        )
        st.plotly_chart(fig_prov, use_container_width=True,
                        config={"displayModeBar": False})

        # Totalizador por ano abaixo do gráfico (sempre visível)
        if visao_prov == "Anual":
            total_exibido = sum(vals_p)
            st.markdown(
                f'<div style="font-size:0.78rem;color:{_COR_ALERTA};'
                f'font-weight:700;text-align:right;margin-top:-8px;">'
                f'Total no período: R$ {total_exibido:,.2f}'.replace(",", "X").replace(".", ",").replace("X", ".") +
                f'</div>',
                unsafe_allow_html=True,
            )
        else:
            total_exibido = sum(vals_p)
            st.caption(
                f"Total no período: **R$ {total_exibido:,.2f}**".replace(",", "X").replace(".", ",").replace("X", ".")
                + f" · {len(hist_prov)} meses"
            )
    else:
        st.caption("Sem histórico de proventos.")

    # ── Aportes por Período ───────────────────────────────────────────────────
    fluxo_mensal = evolucao.get("fluxo_mensal", [])
    if fluxo_mensal:
        st.markdown("<br>", unsafe_allow_html=True)

        col_ap_t, col_ap_v = st.columns([3, 1])
        with col_ap_t:
            _secao_titulo_orig(
                "💰", "Aportes por Período",
                "Valor líquido investido por mês (últimos 12) ou por ano (histórico)",
            )
        with col_ap_v:
            st.markdown("<br>", unsafe_allow_html=True)
            visao_ap = st.selectbox(
                "Período",
                ["Mensal", "Anual"],
                key="hist_aportes_visao",
                label_visibility="collapsed",
            )

        st.plotly_chart(
            _fig_aportes(fluxo_mensal, visao_ap),
            use_container_width=True,
            config={"displayModeBar": False},
        )

        # Totalizador
        if visao_ap == "Mensal":
            dados_vis = fluxo_mensal[-12:]
            total_vis = sum(d["aporte"] for d in dados_vis)
            st.caption(
                f"Total aportado nos últimos 12 meses: **{fmt_moeda(total_vis)}** "
                f"· Valores negativos indicam meses com resgates líquidos."
            )
        else:
            total_vis = sum(d["aporte"] for d in fluxo_mensal)
            anos = sorted({d["ano"] for d in fluxo_mensal})
            st.caption(
                f"Total aportado ({anos[0]}–{anos[-1]}): **{fmt_moeda(total_vis)}** "
                f"· {len(anos)} ano(s) de histórico."
            )


def _header_classe(cls_info: dict, renda_cls: float) -> None:
    cor  = cls_info.get("cor", "#718096")
    n    = cls_info["num_ativos"]
    vm   = cls_info["valor_mercado"]
    ti   = cls_info["total_investido"]
    pct  = cls_info["pct_carteira"]
    resultado = vm + renda_cls - ti
    rsc  = resultado / ti * 100 if ti > 0 else 0.0
    cor_rsc = _COR_POSITIVO if rsc >= 0 else _COR_NEGATIVO
    seta_rsc = "▲" if rsc >= 0 else "▼"
    st.markdown(
        f'<div style="border-left:4px solid {cor};padding:8px 16px;'
        f'background:rgba(255,255,255,0.02);border-radius:0 8px 8px 0;'
        f'margin:24px 0 14px;display:flex;align-items:center;gap:14px;flex-wrap:wrap;">'
        f'<span style="font-size:1.05rem;font-weight:800;color:#E2E8F0;">{cls_info["nome"]}</span>'
        f'<span style="font-size:0.75rem;color:#718096;">'
        f'{n} ativo{"s" if n != 1 else ""} · {fmt_moeda(vm)} · {pct:.1f}% da carteira · '
        f'<span style="color:{cor_rsc};">Ret. s/ custo {seta_rsc} {abs(rsc):.2f}%</span>'
        f'</span>'
        f'</div>',
        unsafe_allow_html=True,
    )


def _card_ativo(pos: dict, renda: float, logo_url: str = "") -> str:
    cor      = pos["cor"]
    rentab   = pos["rentab_pct"]
    cor_r    = _COR_POSITIVO if rentab >= 0 else _COR_NEGATIVO
    seta_r   = "▲" if rentab >= 0 else "▼"
    cor_vm   = _COR_POSITIVO if pos["valor_mercado"] >= pos["total_investido"] else _COR_NEGATIVO
    dot_cor  = _COR_POSITIVO if pos["preco_atual"] >= pos["preco_medio"] else _COR_NEGATIVO
    ti       = pos["total_investido"]
    rsc      = (pos["valor_mercado"] + renda - ti) / ti * 100 if ti > 0 else 0.0
    cor_rsc  = _COR_POSITIVO if rsc >= 0 else _COR_NEGATIVO

    dot = (f'<span style="display:inline-block;width:7px;height:7px;border-radius:50%;'
           f'background:{dot_cor};margin-left:3px;vertical-align:middle;"></span>')

    metricas = [
        ("Peso na carteira",  f"{pos['pct_carteira']:.2f}%",       "#CBD5E0"),
        ("Quantidade",        f"{pos['quantidade']:,.0f}".replace(",", "."), "#CBD5E0"),
        (f"Preço atual {dot}", fmt_moeda(pos["preco_atual"]),        "#CBD5E0"),
        ("Preço médio",       fmt_moeda(pos["preco_medio"]),         "#CBD5E0"),
        ("Custo investido",   fmt_moeda(ti),                         "#CBD5E0"),
        ("Valor de mercado",  fmt_moeda(pos["valor_mercado"]),       cor_vm),
        ("Resultado total",   f"{seta_r} {abs(rentab):.2f}%",       cor_r),
        ("Renda recebida",    fmt_moeda(renda),                      _COR_ALERTA),
        ("Retorno s/ custo",  f"{rsc:.2f}%",                        cor_rsc),
    ]
    rows_html = "".join(
        f'<div style="display:flex;justify-content:space-between;padding:4px 0;'
        f'border-bottom:1px solid #1A1F2E;font-size:0.76rem;">'
        f'<span style="color:#718096;">{lbl}</span>'
        f'<span style="color:{cv};font-weight:600;">{val}</span>'
        f'</div>'
        for lbl, val, cv in metricas
    )
    initials   = pos["ticker"][:5]
    nome_curto = pos["nome"][:22] if len(pos["nome"]) > 22 else pos["nome"]
    if logo_url:
        avatar_html = (
            f'<div style="width:40px;height:40px;border-radius:8px;overflow:hidden;'
            f'flex-shrink:0;background:#1E2533;display:flex;align-items:center;'
            f'justify-content:center;">'
            f'<img src="{logo_url}" style="width:36px;height:36px;object-fit:contain;" '
            f'alt="{pos["ticker"]}">'
            f'</div>'
        )
    else:
        avatar_html = (
            f'<div style="width:40px;height:40px;border-radius:8px;background:{cor};'
            f'flex-shrink:0;display:flex;align-items:center;justify-content:center;'
            f'font-size:0.60rem;font-weight:800;color:#fff;">{initials}</div>'
        )
    return (
        f'<div style="background:#12151E;border:1px solid #1E2533;border-radius:12px;'
        f'padding:16px;margin-bottom:6px;">'
        f'<div style="display:flex;align-items:center;gap:10px;margin-bottom:10px;">'
        f'{avatar_html}'
        f'<div style="overflow:hidden;">'
        f'<div style="font-size:0.83rem;font-weight:800;color:#E2E8F0;'
        f'white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">{nome_curto}</div>'
        f'<div style="font-size:0.68rem;color:#718096;">{pos["ticker"]}</div>'
        f'</div></div>'
        f'<span style="font-size:0.58rem;font-weight:700;padding:2px 7px;border-radius:4px;'
        f'background:{cor}33;color:{cor};text-transform:uppercase;letter-spacing:0.08em;'
        f'display:inline-block;margin-bottom:8px;">{pos["classe"]}</span>'
        f'<div>{rows_html}</div>'
        f'</div>'
    )


def _tab_carteira(carteira: dict, proventos: dict) -> None:
    from collections import defaultdict as _dd
    posicoes   = carteira.get("posicoes", [])
    por_classe = carteira.get("por_classe", [])

    # Renda recebida por ticker
    renda_por_ticker: dict[str, float] = {
        a["ticker"]: a["total"]
        for a in proventos.get("por_ativo", [])
    }
    # Renda total por classe (para header)
    renda_por_classe: dict[str, float] = _dd(float)
    for p in posicoes:
        renda_por_classe[p["classe"]] += renda_por_ticker.get(p["ticker"], 0.0)

    st.markdown("<br>", unsafe_allow_html=True)

    if not carteira["cotacoes_disponiveis"]:
        st.info(
            "**Cotações não disponíveis** — importe cotações em "
            "**Configurações > Cotações** para ver rentabilidade real.",
            icon="📊",
        )

    # ── KPIs resumo ──────────────────────────────────────────────────────────
    c1, c2, c3, c4 = st.columns(4, gap="small")
    with c1:
        st.markdown(_kpi("Total Investido", fmt_moeda(carteira["total_investido"]),
                         "Custo histórico acumulado.", "#E2E8F0"),
                    unsafe_allow_html=True)
    with c2:
        st.markdown(_kpi("Valor de Mercado", fmt_moeda(carteira["total_mercado"]),
                         "Valor atual da carteira.", _COR_INFO),
                    unsafe_allow_html=True)
    with c3:
        rent = carteira["rentabilidade_total_pct"]
        st.markdown(_kpi("Rentabilidade Total",
                         fmt_percentual(rent),
                         "(Mercado − Custo) / Custo.",
                         _COR_POSITIVO if rent >= 0 else _COR_NEGATIVO),
                    unsafe_allow_html=True)
    with c4:
        st.markdown(_kpi("Ativos na Carteira",
                         str(carteira["num_ativos"]),
                         f"N efetivo: {_calc_n_efetivo(posicoes)}",
                         _COR_NEUTRO),
                    unsafe_allow_html=True)

    if not posicoes:
        st.info("Nenhuma posição encontrada. Execute o ETL de posições.", icon="💼")
        return

    # Busca logos em lote (cache 24h) — falha silenciosa
    tickers_tuple = tuple(p["ticker"] for p in posicoes)
    logos = _get_logos(tickers_tuple)

    # ── Cards agrupados por classe ────────────────────────────────────────────
    pos_por_classe: dict[str, list] = _dd(list)
    for p in posicoes:
        pos_por_classe[p["classe"]].append(p)

    for cls_info in por_classe:
        cls_nome   = cls_info["nome"]
        cls_pos    = pos_por_classe.get(cls_nome, [])
        if not cls_pos:
            continue
        renda_cls  = renda_por_classe.get(cls_nome, 0.0)
        _header_classe(cls_info, renda_cls)

        for i in range(0, len(cls_pos), 4):
            chunk = cls_pos[i:i + 4]
            cols  = st.columns(4, gap="small")
            for j, pos in enumerate(chunk):
                renda_p = renda_por_ticker.get(pos["ticker"], 0.0)
                with cols[j]:
                    st.markdown(_card_ativo(pos, renda_p, logos.get(pos["ticker"], "")), unsafe_allow_html=True)
            if i + 4 < len(cls_pos):
                st.markdown("<br>", unsafe_allow_html=True)


def _tab_analise(carteira: dict, proventos: dict) -> None:
    posicoes   = carteira.get("posicoes", [])
    por_classe = carteira.get("por_classe", [])
    por_setor  = carteira.get("por_setor",  [])
    n_efetivo  = _calc_n_efetivo(posicoes)

    st.markdown("<br>", unsafe_allow_html=True)
    _secao_titulo_orig("📊", "Análise de Concentração")

    col_cls, col_set = st.columns(2, gap="medium")

    with col_cls:
        st.markdown(
            '<div style="font-size:0.83rem;font-weight:700;color:#E2E8F0;'
            'margin-bottom:10px;">Por Classe de Ativo</div>',
            unsafe_allow_html=True,
        )
        for cls in por_classe:
            barra_cor = (
                _COR_NEGATIVO if cls["pct_carteira"] >= 50 else
                _COR_ALERTA   if cls["pct_carteira"] >= 35 else
                _COR_POSITIVO
            )
            w = min(cls["pct_carteira"], 100)
            st.markdown(
                f'<div style="margin-bottom:10px;">'
                f'<div style="display:flex;justify-content:space-between;'
                f'font-size:0.80rem;color:#CBD5E0;margin-bottom:4px;">'
                f'<span style="display:flex;align-items:center;gap:6px;">'
                f'<span style="width:8px;height:8px;border-radius:50%;'
                f'background:{cls["cor"]};display:inline-block"></span>'
                f'{cls["nome"]}</span>'
                f'<span style="font-weight:700;color:{barra_cor}">'
                f'{cls["pct_carteira"]:.1f}%</span>'
                f'</div>'
                f'<div style="background:#1E2533;border-radius:3px;height:5px;">'
                f'<div style="background:{cls["cor"]};width:{w:.0f}%;'
                f'height:100%;border-radius:3px;"></div>'
                f'</div>'
                f'<div style="font-size:0.70rem;color:#4A5568;text-align:right;'
                f'margin-top:2px;">{fmt_moeda(cls["valor_mercado"])}'
                f' · {cls["num_ativos"]} ativo{"s" if cls["num_ativos"] != 1 else ""}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )

    with col_set:
        st.markdown(
            '<div style="font-size:0.83rem;font-weight:700;color:#E2E8F0;'
            'margin-bottom:10px;">Por Setor</div>',
            unsafe_allow_html=True,
        )
        if por_setor:
            total_setor = sum(s["valor_mercado"] for s in por_setor) or 1
            for s in por_setor[:8]:
                pct_s = round(s["valor_mercado"] / total_setor * 100, 1)
                w     = min(pct_s, 100)
                barra_cor = _COR_NEGATIVO if pct_s >= 40 else _COR_ALERTA if pct_s >= 25 else _COR_INFO
                st.markdown(
                    f'<div style="margin-bottom:10px;">'
                    f'<div style="display:flex;justify-content:space-between;'
                    f'font-size:0.80rem;color:#CBD5E0;margin-bottom:4px;">'
                    f'<span>{s["nome"]}</span>'
                    f'<span style="font-weight:700;color:{barra_cor}">{pct_s:.1f}%</span>'
                    f'</div>'
                    f'<div style="background:#1E2533;border-radius:3px;height:5px;">'
                    f'<div style="background:{barra_cor};width:{w:.0f}%;'
                    f'height:100%;border-radius:3px;"></div>'
                    f'</div>'
                    f'<div style="font-size:0.70rem;color:#4A5568;text-align:right;'
                    f'margin-top:2px;">{fmt_moeda(s["valor_mercado"])}</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
        else:
            st.caption("Sem dados de setor.")

    st.markdown("<br>", unsafe_allow_html=True)
    _secao_titulo_orig("💵", "Proventos por Ativo")

    por_ativo = proventos.get("por_ativo", [])
    if por_ativo:
        df_prov = pd.DataFrame([{
            "Ticker":       a["ticker"],
            "Proventos":    a["total"],
            "Eventos":      a["num_eventos"],
            "Último":       a.get("ultimo_pagamento") or "—",
        } for a in por_ativo[:20]])
        st.dataframe(
            df_prov,
            column_config={
                "Ticker":    st.column_config.TextColumn("Ticker"),
                "Proventos": st.column_config.NumberColumn("Proventos", format="R$ %.2f"),
                "Eventos":   st.column_config.NumberColumn("Eventos",   format="%d"),
                "Último":    st.column_config.TextColumn("Último Pagamento"),
            },
            hide_index=True,
            use_container_width=True,
        )
    else:
        st.caption("Sem dados de proventos por ativo.")


# ══════════════════════════════════════════════════════════════════════════════
# RENDER PRINCIPAL
# ══════════════════════════════════════════════════════════════════════════════

def render() -> None:
    carteira  = get_carteira()
    cashflow  = get_cashflow_mensal()
    proventos = get_proventos()
    evolucao  = get_evolucao_patrimonial()

    # ── Header ────────────────────────────────────────────────────────────────
    col_title, col_date = st.columns([3, 1])
    with col_title:
        st.markdown(
            '<div style="display:flex;align-items:center;gap:12px;">'
            '<span style="font-size:2rem">📈</span>'
            '<h1 style="font-size:2rem;font-weight:800;color:#E2E8F0;margin:0;">'
            'Investimentos</h1>'
            '</div>',
            unsafe_allow_html=True,
        )
    with col_date:
        st.markdown(
            f'<div style="text-align:right;padding-top:6px;">'
            f'<div style="font-size:0.60rem;text-transform:uppercase;'
            f'letter-spacing:0.1em;color:#4A5568;">Última Atualização</div>'
            f'<div style="font-size:1.00rem;font-weight:700;color:{_COR_POSITIVO};">'
            f'{_date.today().strftime("%d/%m/%Y")}</div>'
            f'<div style="font-size:0.70rem;color:#4A5568;">Modo mock</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

    # Badges
    _fonte = carteira.get("data_source", "mock")
    col_b1, col_b2, col_b3, *_ = st.columns([1, 1, 1, 4])
    with col_b1:
        badge_status(
            "Dados reais"     if _fonte == "real" else
            "Fallback (mock)" if _fonte == "mock_fallback" else "Modo mock",
            "sucesso" if _fonte == "real" else
            "erro"    if _fonte == "mock_fallback" else "alerta",
        )
    with col_b2:
        badge_status(
            "Cotações OK" if carteira["cotacoes_disponiveis"] else "Sem cotações",
            "sucesso" if carteira["cotacoes_disponiveis"] else "alerta",
        )
    with col_b3:
        badge_status(f"{proventos['num_eventos']} proventos", "neutro")

    st.markdown("<br>", unsafe_allow_html=True)

    # ── Sub-navegação via tabs ────────────────────────────────────────────────
    tab1, tab2, tab3, tab4 = st.tabs([
        "📊  Dashboard",
        "📈  Histórico",
        "💼  Carteira",
        "🔍  Análise",
    ])

    with tab1:
        _tab_dashboard(carteira, proventos, cashflow)

    with tab2:
        _tab_historico(cashflow, proventos, evolucao)

    with tab3:
        _tab_carteira(carteira, proventos)

    with tab4:
        _tab_analise(carteira, proventos)
