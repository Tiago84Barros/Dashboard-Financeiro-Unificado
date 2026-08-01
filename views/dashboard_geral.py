"""
views/dashboard_geral.py  — v3
Visão Geral consolidada: dados reais do DB, 3 domínios.

Layout:
  Row 1 — 2 cards: Fluxo Real do Mês · Investimentos
  Row 2 — Histórico 6 meses (Receitas × Despesas × Investimentos)
  Row 3 — Distribuição de despesas (ano) | Comparativo Ano a Ano
"""
from datetime import date as _date
from datetime import datetime as _datetime
from html import escape
from zoneinfo import ZoneInfo

import plotly.graph_objects as go
import streamlit as st

from core.b3_portfolio_model import load_active_b3_portfolio_model
from core.config import settings
from core.controle import get_gastos_categoria_anual, get_historico_anual
from core.financeiro import get_visao_geral
from core.investimentos import (
    get_carteira,
    get_cashflow_mensal,
    get_evolucao_patrimonial,
)
from core.utils import fmt_moeda, fmt_percentual

# Carteira-modelo de FIIs — recomputada com a mesma lógica da página Seleção de FIIs.
_FIIS_N_MAX = 10          # nº de FIIs na carteira-modelo (default da página)
_FIIS_MAX_W = 0.20        # teto por FII
_FIIS_MAX_TIPO = 0.50     # teto por tipo (tijolo/papel/fof/híbrido)

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

_DASHBOARD_STYLES = """
<style>
/* Dashboard Geral v4 — escopo próprio para permitir evolução/reversão isolada. */
.dg-shell {
    --dg-surface: rgba(18, 22, 33, 0.88);
    --dg-surface-strong: rgba(22, 27, 41, 0.96);
    --dg-border: rgba(148, 163, 184, 0.14);
    --dg-text: #F8FAFC;
    --dg-muted: #94A3B8;
    --dg-subtle: #64748B;
    margin-bottom: 0.35rem;
}
.dg-hero {
    position: relative;
    overflow: hidden;
    display: flex;
    justify-content: space-between;
    align-items: flex-end;
    gap: 1.5rem;
    padding: 1.75rem 1.9rem;
    margin: 0 0 1rem;
    border: 1px solid var(--dg-border);
    border-radius: 20px;
    background:
        radial-gradient(circle at 88% 8%, rgba(74,158,255,.20), transparent 34%),
        radial-gradient(circle at 8% 105%, rgba(0,200,150,.13), transparent 36%),
        linear-gradient(145deg, #171C2A 0%, #111620 58%, #10131C 100%);
    box-shadow: 0 18px 46px rgba(0,0,0,.27), inset 0 1px 0 rgba(255,255,255,.045);
}
.dg-hero::after {
    content: "";
    position: absolute;
    width: 230px;
    height: 230px;
    right: -120px;
    bottom: -155px;
    border: 1px solid rgba(74,158,255,.22);
    border-radius: 50%;
}
.dg-eyebrow {
    color: #60A5FA;
    font-size: .68rem;
    font-weight: 800;
    letter-spacing: .16em;
    text-transform: uppercase;
    margin-bottom: .55rem;
}
.dg-title {
    color: var(--dg-text);
    font-size: clamp(1.75rem, 3vw, 2.45rem);
    font-weight: 820;
    letter-spacing: -.045em;
    line-height: 1.03;
    margin: 0;
}
.dg-subtitle {
    color: var(--dg-muted);
    font-size: .88rem;
    line-height: 1.55;
    margin-top: .65rem;
    max-width: 660px;
}
.dg-hero-meta {
    position: relative;
    z-index: 1;
    display: flex;
    justify-content: flex-end;
    flex-wrap: wrap;
    gap: .45rem;
    min-width: 220px;
}
.dg-chip {
    display: inline-flex;
    align-items: center;
    gap: .42rem;
    min-height: 30px;
    padding: .32rem .7rem;
    border: 1px solid var(--dg-border);
    border-radius: 999px;
    background: rgba(15,23,42,.66);
    color: #CBD5E1;
    font-size: .72rem;
    font-weight: 700;
    white-space: nowrap;
}
.dg-chip-dot {
    width: 7px;
    height: 7px;
    border-radius: 50%;
    background: var(--chip-color, #4A9EFF);
    box-shadow: 0 0 0 4px color-mix(in srgb, var(--chip-color, #4A9EFF) 16%, transparent);
}
.dg-kpi-grid {
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: .78rem;
    margin: 0 0 1.4rem;
}
.dg-kpi {
    position: relative;
    min-width: 0;
    padding: 1rem 1.05rem 1.05rem;
    border: 1px solid var(--dg-border);
    border-radius: 15px;
    background: linear-gradient(155deg, var(--dg-surface-strong), var(--dg-surface));
    box-shadow: 0 8px 24px rgba(0,0,0,.18);
}
.dg-kpi::before {
    content: "";
    position: absolute;
    left: 1rem;
    right: 1rem;
    top: 0;
    height: 2px;
    background: linear-gradient(90deg, var(--kpi-color), transparent);
}
.dg-kpi-top {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: .6rem;
}
.dg-kpi-label {
    color: var(--dg-muted);
    font-size: .67rem;
    font-weight: 760;
    letter-spacing: .085em;
    text-transform: uppercase;
}
.dg-kpi-icon {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 28px;
    height: 28px;
    border: 1px solid color-mix(in srgb, var(--kpi-color) 38%, transparent);
    border-radius: 9px;
    background: color-mix(in srgb, var(--kpi-color) 11%, transparent);
    color: var(--kpi-color);
    font-size: .82rem;
}
.dg-kpi-value {
    overflow-wrap: anywhere;
    color: var(--dg-text);
    font-size: clamp(1.25rem, 2vw, 1.7rem);
    font-weight: 820;
    letter-spacing: -.035em;
    line-height: 1.08;
    margin-top: .7rem;
}
.dg-kpi-detail {
    color: var(--dg-subtle);
    font-size: .72rem;
    line-height: 1.35;
    margin-top: .46rem;
}
.dg-kpi-detail strong {
    color: var(--kpi-color);
    font-weight: 760;
}
.dg-section {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 1rem;
    margin: 1.7rem 0 .85rem;
}
.dg-section-main {
    display: flex;
    align-items: center;
    gap: .78rem;
    min-width: 0;
}
.dg-section-icon {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 34px;
    height: 34px;
    flex: 0 0 auto;
    border: 1px solid color-mix(in srgb, var(--section-color) 34%, transparent);
    border-radius: 10px;
    background: color-mix(in srgb, var(--section-color) 10%, transparent);
    font-size: .95rem;
}
.dg-section-title {
    color: #E2E8F0;
    font-size: 1rem;
    font-weight: 760;
    letter-spacing: -.015em;
}
.dg-section-subtitle {
    color: var(--dg-subtle);
    font-size: .75rem;
    line-height: 1.35;
    margin-top: .14rem;
}
.dg-section-rule {
    width: 86px;
    height: 1px;
    flex: 0 0 auto;
    background: linear-gradient(90deg, var(--section-color), transparent);
}
.dg-callout {
    display: flex;
    align-items: flex-start;
    gap: .75rem;
    padding: .85rem 1rem;
    margin: .85rem 0 1.35rem;
    border: 1px solid rgba(74,158,255,.18);
    border-radius: 12px;
    background: rgba(74,158,255,.055);
}
.dg-callout-icon {
    color: #60A5FA;
    font-size: 1rem;
    line-height: 1.4;
}
.dg-callout-copy {
    color: #94A3B8;
    font-size: .78rem;
    line-height: 1.5;
}
.dg-callout-copy strong { color: #E2E8F0; }
.dg-exec-detail {
    min-height: 246px;
    padding: .35rem .25rem .15rem;
}
.dg-chart-label {
    display: flex;
    align-items: center;
    gap: .48rem;
    color: var(--chart-color);
    font-size: .67rem;
    font-weight: 800;
    letter-spacing: .105em;
    text-transform: uppercase;
    margin: .05rem .15rem .2rem;
}

/* Containers nativos da página; evita divs HTML "abertas" entre elementos Streamlit. */
.st-key-dg_executive_card,
.st-key-dg_investment_card,
.st-key-dg_history_chart,
.st-key-dg_categories_chart,
.st-key-dg_yoy_chart,
.st-key-dg_allocation_chart,
.st-key-dg_geography_chart,
.st-key-dg_positions_chart,
.st-key-dg_evolution_chart {
    border-color: rgba(148,163,184,.14) !important;
    border-radius: 16px !important;
    background: linear-gradient(160deg, rgba(20,25,38,.94), rgba(14,17,26,.96));
    box-shadow: 0 8px 26px rgba(0,0,0,.18);
}

@media (max-width: 1100px) {
    .dg-kpi-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@media (max-width: 720px) {
    .dg-hero {
        align-items: flex-start;
        flex-direction: column;
        padding: 1.25rem 1.1rem;
        border-radius: 16px;
    }
    .dg-hero-meta { justify-content: flex-start; min-width: 0; }
    .dg-kpi-grid { grid-template-columns: 1fr; }
    .dg-section { align-items: flex-start; }
    .dg-section-rule { display: none; }
}
@media (prefers-reduced-motion: reduce) {
    .dg-shell *, .dg-shell *::before, .dg-shell *::after {
        scroll-behavior: auto !important;
        transition: none !important;
    }
}
</style>
"""


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS — Cards CSS
# ══════════════════════════════════════════════════════════════════════════════

def _render_dashboard_header(
    mes_ref: str,
    fonte_label: str,
    fonte_cor: str,
    atualizado_em: _date,
) -> None:
    """Cabeçalho executivo responsivo e autocontido do Dashboard Geral."""
    st.markdown(_DASHBOARD_STYLES, unsafe_allow_html=True)
    st.markdown(
        f"""
        <div class="dg-shell">
          <section class="dg-hero" aria-labelledby="dg-page-title">
            <div>
              <div class="dg-eyebrow">Visão financeira consolidada</div>
              <h1 class="dg-title" id="dg-page-title">Seu dinheiro, em perspectiva.</h1>
              <div class="dg-subtitle">
                Caixa, patrimônio e decisões de investimento reunidos em uma leitura
                executiva — do mês atual ao histórico da carteira.
              </div>
            </div>
            <div class="dg-hero-meta" aria-label="Contexto dos dados">
              <span class="dg-chip">
                <span class="dg-chip-dot" style="--chip-color:{escape(fonte_cor)}"></span>
                {escape(fonte_label)}
              </span>
              <span class="dg-chip">Período&nbsp;·&nbsp;{escape(mes_ref)}</span>
              <span class="dg-chip">Atualizado&nbsp;·&nbsp;{atualizado_em.strftime("%d/%m/%Y")}</span>
            </div>
          </section>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _kpi_html(
    label: str,
    value: str,
    detail: str,
    icon: str,
    color: str,
) -> str:
    return (
        f'<article class="dg-kpi" style="--kpi-color:{escape(color)}">'
        '<div class="dg-kpi-top">'
        f'<div class="dg-kpi-label">{escape(label)}</div>'
        f'<div class="dg-kpi-icon" aria-hidden="true">{escape(icon)}</div>'
        '</div>'
        f'<div class="dg-kpi-value">{escape(value)}</div>'
        f'<div class="dg-kpi-detail">{detail}</div>'
        '</article>'
    )


def _render_kpi_grid(
    pat: dict,
    receitas: float,
    despesas: float,
    investimentos: float,
    carteira: dict,
) -> None:
    """Quatro indicadores essenciais, em CSS Grid responsivo."""
    saldo = receitas - despesas - investimentos
    taxa = (saldo / receitas * 100) if receitas > 0 else 0.0
    delta_pat = float(pat.get("delta_mes_pct") or 0)
    rentab = float(carteira.get("rentabilidade_total_pct") or 0)
    saldo_cor = _COR_FLUXO if saldo >= 0 else _COR_NEGATIVO
    taxa_cor = _COR_FLUXO if taxa >= 30 else _COR_ALERTA if taxa >= 15 else _COR_NEGATIVO
    delta_cor = _COR_FLUXO if delta_pat >= 0 else _COR_NEGATIVO
    rentab_cor = _COR_FLUXO if rentab >= 0 else _COR_NEGATIVO

    cards = [
        _kpi_html(
            "Patrimônio total",
            fmt_moeda(float(pat.get("total") or 0)),
            f'<strong style="color:{delta_cor}">{delta_pat:+.1f}%</strong> no mês',
            "◆",
            _COR_PATRIMONIO,
        ),
        _kpi_html(
            "Saldo líquido do mês",
            fmt_moeda(saldo),
            f'Receitas menos despesas e aportes · <strong style="color:{saldo_cor}">'
            f'{"positivo" if saldo >= 0 else "negativo"}</strong>',
            "↗" if saldo >= 0 else "↘",
            saldo_cor,
        ),
        _kpi_html(
            "Taxa de poupança",
            fmt_percentual(taxa, sinal=False),
            f'Meta de referência: 30% · <strong style="color:{taxa_cor}">'
            f'{"atingida" if taxa >= 30 else "em acompanhamento"}</strong>',
            "%",
            taxa_cor,
        ),
        _kpi_html(
            "Rentabilidade da carteira",
            f"{rentab:+.2f}%",
            f'Sobre o custo consolidado · <strong style="color:{rentab_cor}">'
            f'{"resultado positivo" if rentab >= 0 else "resultado negativo"}</strong>',
            "⌁",
            rentab_cor,
        ),
    ]
    st.markdown(
        '<div class="dg-shell"><section class="dg-kpi-grid" '
        'aria-label="Indicadores financeiros principais">'
        + "".join(cards)
        + "</section></div>",
        unsafe_allow_html=True,
    )


def _titulo_secao(icone: str, titulo: str, subtitulo: str, cor: str) -> None:
    st.markdown(
        f"""
        <div class="dg-shell">
          <div class="dg-section" style="--section-color:{escape(cor)}">
            <div class="dg-section-main">
              <div class="dg-section-icon" aria-hidden="true">{escape(icone)}</div>
              <div>
                <div class="dg-section-title">{escape(titulo)}</div>
                <div class="dg-section-subtitle">{escape(subtitulo)}</div>
              </div>
            </div>
            <div class="dg-section-rule" aria-hidden="true"></div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _linha_kv(label: str, valor: str, cor_val: str = "#E2E8F0") -> str:
    return (
        '<div style="display:flex;justify-content:space-between;align-items:center;'
        'gap:10px;padding:5px 0;border-bottom:1px solid #1A1F2E;">'
        f'<span style="font-size:0.80rem;color:#9CA3AF;min-width:0;">{label}</span>'
        f'<span style="font-size:0.88rem;font-weight:700;color:{cor_val};'
        f'text-align:right;white-space:nowrap;">{valor}</span>'
        '</div>'
    )


def _barra(pct: float, cor: str) -> str:
    w = min(pct, 100)
    return f"""
    <div style="background:#1E2533;border-radius:4px;height:5px;overflow:hidden;margin-top:4px">
        <div style="background:{cor};width:{w:.0f}%;height:100%;border-radius:4px"></div>
    </div>"""


def _card(borda_cor: str, corpo_html: str) -> None:
    st.markdown(
        f'<div class="dg-shell"><div class="dg-exec-detail" '
        f'style="border-top:2px solid {escape(borda_cor)}">{corpo_html}</div></div>',
        unsafe_allow_html=True,
    )


def _label_card(texto: str, cor: str) -> str:
    return (f'<div style="font-size:0.65rem;font-weight:800;text-transform:uppercase;'
            f'letter-spacing:0.14em;color:{cor};margin-bottom:14px">{texto}</div>')


def _titulo_valor(label: str, valor: str, cor: str = "#E2E8F0") -> str:
    return (f'<div style="font-size:0.78rem;color:#718096;margin-bottom:2px">{label}</div>'
            f'<div style="font-size:1.75rem;font-weight:800;color:{cor};'
            f'letter-spacing:0;margin-bottom:14px;line-height:1">{valor}</div>')


def _divisor() -> str:
    return '<div style="border-top:1px solid #1E2533;margin:12px 0"></div>'


# ══════════════════════════════════════════════════════════════════════════════
# CARDS
# ══════════════════════════════════════════════════════════════════════════════

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
        + '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px;">'
        + '<span style="font-size:0.78rem;color:#718096">Taxa de poupança</span>'
        + f'<span style="font-size:0.88rem;font-weight:700;color:{cor_taxa}">'
        + f'{fmt_percentual(taxa, sinal=False)} '
        + '<span style="font-size:0.70rem;color:#4A5568">/ meta 30%</span></span></div>'
        + _barra(taxa_w, cor_taxa)
    )
    _card(_COR_FLUXO, corpo)


def _card_investimentos(pat: dict, classes: list, aportado_ano: float) -> None:
    total_inv = pat["investido"]
    n_classes = len(classes)

    linhas = ""
    for c in classes[:4]:
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


def _label_tesouro_codigo(ticker: str) -> str | None:
    codigo = (ticker or "").upper().strip()
    if not codigo:
        return None
    ano = "".join(ch for ch in codigo if ch.isdigit())
    ano = ano[-4:] if len(ano) >= 4 else ""
    if codigo.startswith("TSELIC"):
        return f"Tesouro Selic {ano}".strip()
    if codigo.startswith("TIPCA"):
        return f"Tesouro IPCA+ {ano}".strip()
    if codigo.startswith("TPRE"):
        return f"Tesouro Prefixado {ano}".strip()
    if codigo.startswith("TEDUCA"):
        return f"Tesouro Educa+ {ano}".strip()
    return None


def _is_rf_ou_tesouro(classe: str) -> bool:
    nome_lower = (classe or "").lower()
    return any(
        k in nome_lower
        for k in ("tesouro", "renda fixa", "fundo rf", "fundo renda fixa")
    )


def _short_asset_label(label: str, max_len: int = 30) -> str:
    texto = " ".join(str(label or "").split())
    if len(texto) <= max_len:
        return texto
    return texto[:max_len - 1].rstrip() + "…"


def _portfolio_position_label(pos: dict) -> str:
    ticker = str(pos.get("ticker") or "").upper().strip()
    nome = str(pos.get("nome") or "").strip()
    classe = str(pos.get("classe") or "")

    if "tesouro" in classe.lower():
        return _label_tesouro_codigo(ticker) or nome or ticker

    if _is_rf_ou_tesouro(classe):
        if nome and nome.upper() != ticker:
            return nome
        if ticker and not ticker[:1].isalpha():
            return f"{classe} {ticker}".strip()

    return ticker or nome


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
    labels = [_short_asset_label(_portfolio_position_label(p)) for p in top]
    valores = [float(p.get("valor_mercado") or 0) for p in top]
    pct = [float(p.get("pct_carteira") or 0) for p in top]
    cores = [p.get("cor", _COR_INVEST) for p in top]
    customdata = [
        [
            str(p.get("ticker") or ""),
            str(p.get("nome") or ""),
            str(p.get("classe") or ""),
            float(p.get("pct_carteira") or 0),
        ]
        for p in top
    ]
    fig = go.Figure(go.Bar(
        x=valores, y=labels, orientation="h",
        marker_color=cores,
        text=[f"{v:.1f}%" for v in pct],
        textposition="outside",
        customdata=customdata,
        hovertemplate=(
            "<b>%{y}</b><br>"
            "Ticker/código: %{customdata[0]}<br>"
            "Nome: %{customdata[1]}<br>"
            "Classe: %{customdata[2]}<br>"
            "Participação: %{customdata[3]:.1f}%<br>"
            "Valor: R$ %{x:,.2f}<extra></extra>"
        ),
    ))
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font_color=_COR_NEUTRO,
        margin={"t": 8, "b": 8, "l": 0, "r": 0}, height=285,
        xaxis={"gridcolor": "#1E2533", "tickformat": ",.0f", "tickprefix": "R$ "},
        yaxis={"showgrid": False, "automargin": True, "tickfont": {"size": 11}},
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


def _status_chip(texto: str, cor: str) -> str:
    return (
        f'<span style="display:inline-flex;align-items:center;border:1px solid {cor};'
        f'background:{cor}1F;color:{cor};border-radius:999px;padding:3px 9px;'
        f'font-size:0.70rem;font-weight:800;white-space:nowrap;flex-shrink:0;">{texto}</span>'
    )


def _modulo_card(
    numero: str,
    titulo: str,
    resumo: str,
    status: str,
    status_cor: str,
    linhas: list[tuple[str, str, str]],
    cor: str,
) -> str:
    rows = "".join(_linha_kv(label, valor, valor_cor) for label, valor, valor_cor in linhas)
    return (
        '<div style="background:linear-gradient(180deg,rgba(18,21,30,0.98) 0%,rgba(14,17,26,0.98) 100%);'
        f'border:1px solid rgba(255,255,255,0.07);border-left:4px solid {cor};'
        'border-radius:12px;padding:18px 18px 16px;min-height:230px;'
        'box-shadow:0 3px 16px rgba(0,0,0,0.32);">'
        '<div style="display:flex;justify-content:space-between;gap:10px;align-items:flex-start;">'
        '<div style="display:flex;gap:10px;align-items:center;min-width:0;">'
        f'<div style="width:28px;height:28px;border-radius:8px;background:{cor}22;color:{cor};'
        f'display:flex;align-items:center;justify-content:center;font-weight:900;'
        f'font-size:0.85rem;border:1px solid {cor}55;flex-shrink:0;">{numero}</div>'
        f'<div style="font-size:0.96rem;font-weight:850;color:#E2E8F0;line-height:1.2;min-width:0;">{titulo}</div>'
        '</div>'
        f'{_status_chip(status, status_cor)}'
        '</div>'
        f'<div style="font-size:0.78rem;color:#9CA3AF;line-height:1.42;margin:13px 0 12px;">{resumo}</div>'
        f'{rows}'
        '</div>'
    )


def _resumo_modelo_b3(modelo: dict) -> tuple[str, str, list[tuple[str, str, str]]]:
    items = modelo.get("items") or []
    if not items:
        return (
            "Sem carteira",
            _COR_NEUTRO,
            [
                ("Empresas selecionadas", "0", _COR_NEUTRO),
                ("Score médio", "N/D", _COR_NEUTRO),
                ("Referência ativa", "Não salva", _COR_ALERTA),
            ],
        )

    metrics = modelo.get("metrics_json") or {}
    status = "Revisar" if modelo.get("is_stale") else "Ativa"
    status_cor = _COR_ALERTA if modelo.get("is_stale") else _COR_FLUXO
    ano = modelo.get("ano_compra") or "ciclo atual"
    score_medio = float(metrics.get("score_medio") or 0)

    return (
        status,
        status_cor,
        [
            ("Empresas selecionadas", str(len(items)), _COR_PATRIMONIO),
            ("Score médio", f"{score_medio:.2f}", _COR_FLUXO),
            ("Ano/ciclo de compra", str(ano), _COR_NEUTRO),
        ],
    )


def _resumo_fiis(port: list[dict], salvo: bool) -> tuple[str, str, list[tuple[str, str, str]]]:
    if not port:
        return (
            "Sem carteira",
            _COR_NEUTRO,
            [
                ("FIIs selecionados", "0", _COR_NEUTRO),
                ("DY 12m ponderado", "N/D", _COR_NEUTRO),
                ("Origem", "Sem dados", _COR_ALERTA),
            ],
        )

    dy_w = sum((p.get("dy_12m") or 0) * p["peso"] for p in port)
    pvp_w = sum((p.get("pvp") or 0) * p["peso"] for p in port)
    status = "Salva" if salvo else "Sugerida"
    status_cor = _COR_FLUXO if salvo else _COR_ALERTA
    return (
        status,
        status_cor,
        [
            ("FIIs selecionados", str(len(port)), _COR_PATRIMONIO),
            ("DY 12m ponderado", f"{dy_w * 100:.1f}%", _COR_FLUXO),
            ("P/VP ponderado", f"{pvp_w:.2f}", _COR_ALERTA),
        ],
    )


def _secao_resumo_modulos(
    receitas_mes: float,
    despesas_mes: float,
    investimentos_mes: float,
    pat: dict,
    classes: list,
    aportado_ano: float,
    carteira: dict,
    modelo_b3: dict,
    fiis_port: list[dict],
    fiis_salvo: bool,
) -> None:
    saldo_mes = receitas_mes - despesas_mes - investimentos_mes
    taxa_poupanca = (saldo_mes / receitas_mes * 100) if receitas_mes > 0 else 0.0
    rentab = float(carteira.get("rentabilidade_total_pct") or 0)
    b3_status, b3_status_cor, b3_linhas = _resumo_modelo_b3(modelo_b3)
    fii_status, fii_status_cor, fii_linhas = _resumo_fiis(fiis_port, fiis_salvo)

    _titulo_secao(
        "🧭", "Resumo por área",
        "Organização lógica do app: caixa primeiro, carteira depois, seleção por critérios em seguida",
        _COR_PATRIMONIO,
    )

    col1, col2 = st.columns(2, gap="medium")
    with col1:
        st.markdown(
            _modulo_card(
                "1",
                "Controle Financeiro",
                "Fluxo do mês, despesas por categoria e comparação anual.",
                "Mensal",
                _COR_FLUXO if saldo_mes >= 0 else _COR_NEGATIVO,
                [
                    ("Receitas", fmt_moeda(receitas_mes), _COR_FLUXO),
                    ("Despesas", fmt_moeda(despesas_mes), _COR_NEGATIVO),
                    ("Saldo líquido", fmt_moeda(saldo_mes), _COR_FLUXO if saldo_mes >= 0 else _COR_NEGATIVO),
                    ("Taxa de poupança", fmt_percentual(taxa_poupanca, sinal=False), _COR_ALERTA if taxa_poupanca < 30 else _COR_FLUXO),
                ],
                _COR_FLUXO,
            ),
            unsafe_allow_html=True,
        )
    with col2:
        st.markdown(
            _modulo_card(
                "2",
                "Investimentos",
                "Carteira consolidada, classes de ativos, concentração e evolução patrimonial.",
                "Carteira",
                _COR_INVEST,
                [
                    ("Patrimônio investido", fmt_moeda(pat["investido"]), _COR_INVEST),
                    ("Aportado no ano", fmt_moeda(aportado_ano), _COR_FLUXO),
                    ("Classes de ativos", str(len(classes)), _COR_NEUTRO),
                    ("Rentabilidade", f"{rentab:+.2f}%", _COR_FLUXO if rentab >= 0 else _COR_NEGATIVO),
                ],
                _COR_INVEST,
            ),
            unsafe_allow_html=True,
        )

    st.markdown("<br>", unsafe_allow_html=True)

    col3, col4 = st.columns(2, gap="medium")
    with col3:
        st.markdown(
            _modulo_card(
                "3",
                "Empresas B3",
                "Carteira modelo de ações brasileiras definida na análise de empresas.",
                b3_status,
                b3_status_cor,
                b3_linhas,
                _COR_PATRIMONIO,
            ),
            unsafe_allow_html=True,
        )
    with col4:
        st.markdown(
            _modulo_card(
                "4",
                "Seleção de FIIs",
                "Carteira modelo de fundos imobiliários com diversificação por tipo.",
                fii_status,
                fii_status_cor,
                fii_linhas,
                _COR_ALERTA,
            ),
            unsafe_allow_html=True,
        )

    st.markdown("<br>", unsafe_allow_html=True)


def _grafico_container_open(icone: str, titulo: str, cor: str) -> None:
    st.markdown(
        f'<div class="dg-shell"><div class="dg-chart-label" '
        f'style="--chart-color:{escape(cor)}">'
        f'<span aria-hidden="true">{escape(icone)}</span>{escape(titulo)}</div></div>',
        unsafe_allow_html=True,
    )


def _grafico_container_close() -> None:
    """Compatibilidade local; containers de gráficos agora são nativos."""


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
        top1_label = _short_asset_label(_portfolio_position_label(top1), 24)
        st.markdown(_mini_metric("Top 5", f"{top5:.1f}%", f"Maior posição: {top1_label} ({top1['pct_carteira']:.1f}%)", _COR_NEGATIVO if top5 > 50 else _COR_FLUXO), unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    c1, c2 = st.columns([1.05, 0.95], gap="medium")
    with c1, st.container(border=True, key="dg_allocation_chart"):
        _grafico_container_open("🥧", "Alocação por classe", _COR_INVEST)
        st.plotly_chart(
            _fig_donut_classes(classes),
            width="stretch",
            config={"displayModeBar": False},
            key="dg_allocation_plot",
        )
    with c2, st.container(border=True, key="dg_geography_chart"):
        _grafico_container_open("🌎", "Brasil vs Exterior", _COR_PATRIMONIO)
        st.plotly_chart(
            _fig_br_exterior(br, ext),
            width="stretch",
            config={"displayModeBar": False},
            key="dg_geography_plot",
        )

    st.markdown("<br>", unsafe_allow_html=True)

    c3, c4 = st.columns(2, gap="medium")
    with c3, st.container(border=True, key="dg_positions_chart"):
        _grafico_container_open("🏆", "Maiores posições", _COR_ALERTA)
        st.plotly_chart(
            _fig_top_posicoes(posicoes),
            width="stretch",
            config={"displayModeBar": False},
            key="dg_positions_plot",
        )
    with c4, st.container(border=True, key="dg_evolution_chart"):
        _grafico_container_open("📈", "Evolução dos investimentos", _COR_FLUXO)
        if evolucao.get("snapshots"):
            st.plotly_chart(
                _fig_evolucao_investimentos(evolucao),
                width="stretch",
                config={"displayModeBar": False},
                key="dg_evolution_plot",
            )
        else:
            st.caption("Sem snapshots patrimoniais suficientes.")

    st.markdown("<br>", unsafe_allow_html=True)


def _secao_portfolio_modelo_b3(modelo: dict) -> None:
    items = modelo.get("items") or []
    if not items:
        return

    metrics = modelo.get("metrics_json") or {}
    ano = modelo.get("ano_compra") or "próximo ciclo"
    setores: dict[str, float] = {}
    for item in items:
        setor = item.get("setor") or "Sem setor"
        setores[setor] = setores.get(setor, 0.0) + float(item.get("weight") or 0)
    top_setor = max(setores.items(), key=lambda x: x[1]) if setores else ("Sem setor", 0.0)
    top_items = items[:8]
    tickers = ", ".join(i["ticker"] for i in top_items)
    criado = modelo.get("created_at")
    criado_txt = criado.strftime("%d/%m/%Y") if hasattr(criado, "strftime") else ""

    _titulo_secao(
        "🎯", "Portfólio B3 padrão",
        "Carteira modelo criada na seção Empresas B3 e definida pelo usuário", _COR_PATRIMONIO,
    )
    if modelo.get("is_stale"):
        st.warning(
            "Esta carteira foi criada com uma metodologia anterior e está "
            "marcada como desatualizada. Recalcule-a em Empresas B3 antes de "
            "usá-la como referência."
        )

    m1, m2, m3, m4 = st.columns(4, gap="small")
    with m1:
        st.markdown(_mini_metric("Empresas", str(len(items)), f"Para compra em {ano}", _COR_PATRIMONIO), unsafe_allow_html=True)
    with m2:
        st.markdown(_mini_metric("Score médio", f"{float(metrics.get('score_medio') or 0):.2f}", "Média das selecionadas", _COR_FLUXO), unsafe_allow_html=True)
    with m3:
        st.markdown(_mini_metric("Setor líder", f"{top_setor[1] * 100:.1f}%", str(top_setor[0])[:34], _COR_ALERTA), unsafe_allow_html=True)
    with m4:
        st.markdown(_mini_metric("Criado", criado_txt or "Ativo", "Modelo salvo no banco", _COR_INVEST), unsafe_allow_html=True)

    c1, c2 = st.columns([1.2, 0.8], gap="medium")
    with c1:
        st.markdown(
            f"""
            <div style="background:#12151E;border:1px solid #1E2533;border-radius:12px;
                        padding:16px 18px;">
                <div style="font-size:0.72rem;text-transform:uppercase;letter-spacing:0.12em;
                            color:#4A9EFF;font-weight:800;margin-bottom:10px;">Empresas selecionadas</div>
                <div style="font-size:1.05rem;font-weight:850;color:#E2E8F0;line-height:1.55;">
                    {tickers}
                </div>
                <div style="font-size:0.76rem;color:#9CA3AF;margin-top:10px;">
                    Substitui versões anteriores e vira a referência padrão de investimento B3.
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with c2:
        rows = ""
        for item in top_items[:5]:
            rows += _linha_kv(
                f"{item['ticker']} · {str(item.get('nome') or '')[:18]}",
                f"{float(item.get('weight') or 0) * 100:.1f}%",
                _COR_FLUXO,
            )
        st.markdown(
            f"""
            <div style="background:#12151E;border:1px solid #1E2533;border-radius:12px;
                        padding:16px 18px;">
                <div style="font-size:0.72rem;text-transform:uppercase;letter-spacing:0.12em;
                            color:#00C896;font-weight:800;margin-bottom:10px;">Pesos sugeridos</div>
                {rows}
            </div>
            """,
            unsafe_allow_html=True,
        )

    st.markdown("<br>", unsafe_allow_html=True)


def _fiis_carteira_modelo() -> tuple[list[dict], bool]:
    """
    Carteira-modelo de FIIs para o dashboard. Prioriza o modelo SALVO pelo usuário
    (Seleção de FIIs → 'Salvar carteira-modelo'); se não houver, recomputa a
    sugestão automática (ranking DY·P/VP·liquidez → diversificação por tipo).
    Retorna (items, salvo_pelo_usuario). [] se não houver FIIs / em qualquer falha.
    """
    # 1) Modelo salvo pelo usuário (a carteira que ele configurou).
    try:
        from core.fii_portfolio_model import load_active_fii_portfolio_model
        saved = load_active_fii_portfolio_model()
        if saved and saved.get("items"):
            return saved["items"], True
    except Exception:  # noqa: BLE001 - fronteira de fallback entre banco e modelo
        # Fix auditoria FII 2026-07: o fallback era SILENCIOSO — em erro de
        # banco o usuário via uma carteira recalculada achando que era a
        # salva. Agora o desvio é avisado.
        st.warning(
            "⚠️ Falha ao carregar a carteira-modelo de FIIs **salva** — "
            "exibindo sugestão **recalculada** com dados atuais, que pode "
            "diferir da que você salvou. Verifique a conexão com o banco."
        )
    # 2) Fallback: recomputa a sugestão automática.
    try:
        import core.market_read as _mr
        from data_pipeline.market import fii as _fz
        df = _mr.load_fiis()
    except Exception:  # noqa: BLE001 - fonte opcional; ausência mantém estado vazio
        return [], False
    if df is None or df.empty:
        return [], False
    score_input = [
        {
            "ticker": r["Ticker"], "price": r["Preço"], "dy_12m": r["DY_12m"],
            # P/VP efetivo com VPA CVM — consistente com a Seleção de FIIs
            "pvp": _fz.pvp_efetivo(r["Preço"], r.get("VPA"), r["P/VP"]),
            "liquidez_diaria": r["Liquidez_Diaria"],
        }
        for _, r in df.iterrows()
    ]
    ranked_rows = _fz.rank_fiis(score_input)
    if not ranked_rows:
        return [], False
    metadata = {r["Ticker"]: r for _, r in df.iterrows()}
    rows = [
        {
            **r,
            "tipo": metadata[r["ticker"]].get("Tipo"),
            "segmento": metadata[r["ticker"]].get("Segmento"),
        }
        for r in ranked_rows if r["ticker"] in metadata
    ]
    try:
        port = _fz.build_portfolio(rows, n_max=_FIIS_N_MAX, max_weight=_FIIS_MAX_W,
                                   max_tipo_frac=_FIIS_MAX_TIPO) or []
    except Exception:  # noqa: BLE001 - otimizador opcional não bloqueia o dashboard
        port = []
    return port, False


def _secao_fiis_sugeridos(port: list[dict] | None = None, salvo: bool = False) -> None:
    if port is None:
        port, salvo = _fiis_carteira_modelo()
    if not port:
        return
    subtitulo = ("Carteira-modelo de FIIs definida por você na Seleção de FIIs" if salvo
                 else "Triagem quantitativa automática — salve uma composição para fixá-la")

    dy_w = sum((p.get("dy_12m") or 0) * p["peso"] for p in port)
    pvp_w = sum((p.get("pvp") or 0) * p["peso"] for p in port)
    tipos: dict[str, float] = {}
    for p in port:
        tp = (p.get("tipo") or "—")
        tipos[tp] = tipos.get(tp, 0.0) + p["peso"]
    tipo_top = max(tipos.items(), key=lambda x: x[1]) if tipos else ("—", 0.0)

    _titulo_secao("🏬", "Carteira-modelo de Fundos Imobiliários", subtitulo, _COR_INVEST)

    m1, m2, m3, m4 = st.columns(4, gap="small")
    with m1:
        st.markdown(_mini_metric("FIIs", str(len(port)), "Carteira-modelo diversificada", _COR_PATRIMONIO), unsafe_allow_html=True)
    with m2:
        st.markdown(_mini_metric("DY 12m", f"{dy_w * 100:.1f}%", "Yield ponderado", _COR_FLUXO), unsafe_allow_html=True)
    with m3:
        st.markdown(_mini_metric("P/VP", f"{pvp_w:.2f}", "Preço/valor patrim. ponderado", _COR_ALERTA), unsafe_allow_html=True)
    with m4:
        st.markdown(_mini_metric("Tipo líder", f"{tipo_top[1] * 100:.0f}%", str(tipo_top[0]).capitalize(), _COR_INVEST), unsafe_allow_html=True)

    tickers = ", ".join(p["ticker"] for p in port)
    c1, c2 = st.columns([1.2, 0.8], gap="medium")
    with c1:
        st.markdown(
            f"""
            <div style="background:#12151E;border:1px solid #1E2533;border-radius:12px;
                        padding:16px 18px;">
                <div style="font-size:0.72rem;text-transform:uppercase;letter-spacing:0.12em;
                            color:#4A9EFF;font-weight:800;margin-bottom:10px;">FIIs selecionados</div>
                <div style="font-size:1.05rem;font-weight:850;color:#E2E8F0;line-height:1.55;">
                    {tickers}
                </div>
                <div style="font-size:0.76rem;color:#9CA3AF;margin-top:10px;">
                    Diversificada por tipo (tijolo · papel · fof · híbrido), com teto por FII e por tipo.
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with c2:
        rows_html = ""
        for p in port[:5]:
            seg = str(p.get("segmento") or p.get("tipo") or "")[:18]
            rows_html += _linha_kv(f"{p['ticker']} · {seg}", f"{p['peso'] * 100:.1f}%", _COR_FLUXO)
        st.markdown(
            f"""
            <div style="background:#12151E;border:1px solid #1E2533;border-radius:12px;
                        padding:16px 18px;">
                <div style="font-size:0.72rem;text-transform:uppercase;letter-spacing:0.12em;
                            color:#00C896;font-weight:800;margin-bottom:10px;">Pesos sugeridos</div>
                {rows_html}
            </div>
            """,
            unsafe_allow_html=True,
        )

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

def _load_decision_models() -> tuple[dict, list[dict], bool]:
    """Carrega modelos persistidos somente quando o app usa dados reais."""
    if settings.MOCK_MODE:
        return {}, [], False

    modelo_b3 = load_active_b3_portfolio_model()
    fiis_port, fiis_salvo = _fiis_carteira_modelo()
    return modelo_b3, fiis_port, fiis_salvo


def render() -> None:
    # ── Dados ─────────────────────────────────────────────────────────────────
    try:
        d = get_visao_geral()
    except NotImplementedError as exc:
        st.error(f"**Banco não configurado.** {exc}")
        return

    hoje          = _datetime.now(ZoneInfo("America/Cayenne")).date()
    hist_cashflow = get_cashflow_mensal()        # list[{ano, mes, receitas, despesas, investimentos}]
    hist_anual    = get_historico_anual()         # {anos, por_ano}
    carteira      = get_carteira()
    evolucao_inv  = get_evolucao_patrimonial()
    # O modo de demonstração não deve consultar nem combinar carteiras persistidas.
    modelo_b3, fiis_port, fiis_salvo = _load_decision_models()
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
    badge_label, badge_cor = (
        ("Dados reais",     _COR_FLUXO)    if fonte == "real" else
        ("Fallback demonstrativo", _COR_NEGATIVO) if fonte == "mock_fallback" else
        ("Dados de demonstração", _COR_ALERTA)
    )
    mes_ref = f"{_MESES_PT[hoje.month]} {hoje.year}"

    # ── Cabeçalho ──────────────────────────────────────────────────────────────
    _render_dashboard_header(mes_ref, badge_label, badge_cor, hoje)
    _render_kpi_grid(
        pat,
        receitas_mes,
        despesas_mes,
        investimentos_mes,
        carteira,
    )
    # ══════════════════════════════════════════════════════════════════════════
    # BLOCO 1 — Visão executiva
    # ══════════════════════════════════════════════════════════════════════════
    _titulo_secao(
        "⚡", "Visão executiva",
        "Resumo direto do caixa do mês e da carteira investida",
        _COR_PATRIMONIO,
    )

    col1, col2 = st.columns(2, gap="medium")
    with col1, st.container(border=True, key="dg_executive_card"):
        _card_fluxo(receitas_mes, despesas_mes, investimentos_mes)
    with col2, st.container(border=True, key="dg_investment_card"):
        _card_investimentos(pat, classes, aportado_ano)

    saldo_mes = receitas_mes - despesas_mes - investimentos_mes
    leitura = (
        "O caixa fechou positivo após despesas e aportes."
        if saldo_mes >= 0
        else "As saídas e os aportes superaram as receitas no período."
    )
    st.markdown(
        '<div class="dg-shell"><div class="dg-callout">'
        '<div class="dg-callout-icon" aria-hidden="true">◎</div>'
        f'<div class="dg-callout-copy"><strong>Leitura do mês.</strong> {escape(leitura)} '
        'Os valores acima são fatos do período; metas e modelos exibidos abaixo são '
        'referências analíticas, não garantias de resultado.</div></div></div>',
        unsafe_allow_html=True,
    )

    # ══════════════════════════════════════════════════════════════════════════
    # BLOCO 2 — Mapa dos módulos do app
    # ══════════════════════════════════════════════════════════════════════════
    _secao_resumo_modulos(
        receitas_mes,
        despesas_mes,
        investimentos_mes,
        pat,
        classes,
        aportado_ano,
        carteira,
        modelo_b3,
        fiis_port,
        fiis_salvo,
    )

    # ══════════════════════════════════════════════════════════════════════════
    # BLOCO 3 — Portfólio B3 modelo salvo pelo usuário + FIIs sugeridos
    # ══════════════════════════════════════════════════════════════════════════
    if modelo_b3.get("items") or fiis_port:
        _titulo_secao(
            "🎯", "Decisões de alocação",
            "Modelos que conectam análise de empresas e seleção de FIIs à carteira",
            _COR_ALERTA,
        )
        _secao_portfolio_modelo_b3(modelo_b3)
        _secao_fiis_sugeridos(fiis_port, fiis_salvo)

    # ══════════════════════════════════════════════════════════════════════════
    # BLOCO 4 — Raio X do portfólio investido
    # ══════════════════════════════════════════════════════════════════════════
    _secao_raio_x_portfolio(carteira, evolucao_inv, classes)

    # ══════════════════════════════════════════════════════════════════════════
    # BLOCO 5 — Histórico 6 meses
    # ══════════════════════════════════════════════════════════════════════════
    _titulo_secao(
        "💹", "Histórico mensal (6 meses)",
        "Receitas · Despesas · Investimentos por mês", _COR_FLUXO,
    )
    with st.container(border=True, key="dg_history_chart"):
        if hist6:
            st.plotly_chart(
                _fig_historico(hist6),
                width="stretch",
                config={"displayModeBar": False},
                key="dg_history_plot",
            )
        else:
            st.caption("Sem histórico disponível.")

    # ══════════════════════════════════════════════════════════════════════════
    # BLOCO 6 — Distribuição de despesas | Comparativo Ano a Ano
    # ══════════════════════════════════════════════════════════════════════════
    col_cats, col_yoy = st.columns(2, gap="medium")

    with col_cats:
        _titulo_secao(
            "🍕", f"Despesas por categoria ({ano_atual})",
            "Compras de cartão excluídas", _COR_NEGATIVO,
        )
        with st.container(border=True, key="dg_categories_chart"):
            if cats_ano:
                st.plotly_chart(
                    _fig_donut_cats(cats_ano),
                    width="stretch",
                    config={"displayModeBar": False},
                    key="dg_categories_plot",
                )
                total_cats = sum(c["gasto"] for c in cats_ano)
                for c in cats_ano[:5]:
                    pct = c["gasto"] / total_cats * 100 if total_cats else 0
                    st.markdown(
                        f'<div style="display:flex;justify-content:space-between;'
                        f'align-items:center;padding:4px 0;border-bottom:1px solid #1A1F2E;">'
                        f'<span style="font-size:0.79rem;color:#CBD5E0">'
                        f'{escape(str(c["nome"]))}</span>'
                        f'<span style="font-size:0.79rem;font-weight:600;color:#E2E8F0">'
                        f'{fmt_moeda(c["gasto"])} '
                        f'<span style="color:#64748B;font-size:0.70rem">{pct:.1f}%</span>'
                        f'</span></div>',
                        unsafe_allow_html=True,
                    )
                if len(cats_ano) > 5:
                    restante = sum(c["gasto"] for c in cats_ano[5:])
                    st.caption(
                        f"+ {len(cats_ano)-5} outras categorias · {fmt_moeda(restante)}"
                    )
            else:
                st.caption(f"Sem despesas registradas em {ano_atual}.")

    with col_yoy:
        anos = hist_anual.get("anos", [])
        _titulo_secao(
            "📅", "Comparativo Ano a Ano",
            "Receitas · Investimentos · Despesas acumuladas", _COR_PATRIMONIO,
        )
        with st.container(border=True, key="dg_yoy_chart"):
            if len(anos) >= 1:
                st.plotly_chart(
                    _fig_yoy(por_ano, anos),
                    width="stretch",
                    config={"displayModeBar": False},
                    key="dg_yoy_plot",
                )
                import pandas as pd
                rows = []
                for a in anos:
                    rows.append({
                        "Ano":           str(a),
                        "Receitas":      f"R$ {por_ano[a]['receitas']:,.2f}".replace(",","X").replace(".",",").replace("X","."),
                        "Investimentos": f"R$ {por_ano[a].get('investimentos',0.0):,.2f}".replace(",","X").replace(".",",").replace("X","."),
                        "Despesas":      f"R$ {por_ano[a]['despesas']:,.2f}".replace(",","X").replace(".",",").replace("X","."),
                    })
                st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
            else:
                st.caption("Sem histórico anual disponível.")
