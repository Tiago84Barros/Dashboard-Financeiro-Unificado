"""
pages/dashboard_geral.py  — v2 (redesign visual Fase 5)
Visão Geral consolidada: 3 domínios separados em cards CSS.

Layout:
  Linha 1 — 3 cards CSS (Patrimônio · Fluxo do Mês · Investimentos)
  Linha 2 — Gráfico Cashflow histórico  +  Despesas por categoria
  Linha 3 — Alocação de investimentos   +  Alertas & Próximos Passos

Design:
  Cada card usa HTML/CSS puro para controle total do visual.
  Sem st.metric() solto — tudo agrupado em containers temáticos.
  Cores de domínio: azul (patrimônio), verde (fluxo), roxo (investimentos).
"""
import streamlit as st
import plotly.graph_objects as go

from core.financeiro import get_visao_geral
from core.utils import fmt_moeda, fmt_percentual
from design.componentes import (
    badge_status,
    barra_progresso,
    card_alerta_resumo,
    card_proximo_passo,
    container_pagina,
    indicador_linha,
    secao_titulo,
)

# ── Paleta de cores de domínio ────────────────────────────────────────────────
_COR_PATRIMONIO  = "#4A9EFF"   # azul
_COR_FLUXO       = "#00C896"   # verde
_COR_INVEST      = "#9B59B6"   # roxo
_COR_ALERTA      = "#F6C90E"   # âmbar
_COR_NEGATIVO    = "#FC5C7D"   # vermelho


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS — Cards CSS
# ══════════════════════════════════════════════════════════════════════════════

def _cor_score(score: int) -> str:
    if score >= 80: return "#00C896"
    if score >= 60: return "#4A9EFF"
    if score >= 40: return "#F6C90E"
    return "#FC5C7D"


def _label_score(score: int) -> str:
    if score >= 80: return "Ótimo"
    if score >= 60: return "Bom"
    if score >= 40: return "Atenção"
    return "Crítico"


def _card_patrimonio(pat: dict, flx: dict) -> None:
    """Card azul — Patrimônio Total, Saldo, Investido, Score de saúde."""
    score = pat["saude_score"]
    cor_s = _cor_score(score)
    label_s = _label_score(score)
    score_w = min(score, 100)

    st.markdown(f"""
    <div style="
        background:#12151E;
        border:1px solid #1E2533;
        border-top:3px solid {_COR_PATRIMONIO};
        border-radius:12px;
        padding:22px 20px 18px;
        height:100%;
        min-height:260px;
    ">
        <div style="font-size:0.65rem;font-weight:800;text-transform:uppercase;
                    letter-spacing:0.14em;color:{_COR_PATRIMONIO};margin-bottom:14px;">
            💰 Patrimônio & Saúde
        </div>

        <!-- Valor principal -->
        <div style="font-size:0.78rem;color:#718096;margin-bottom:3px">Patrimônio Total</div>
        <div style="font-size:1.85rem;font-weight:800;color:#E2E8F0;
                    letter-spacing:-0.02em;margin-bottom:14px;line-height:1">
            {fmt_moeda(pat["total"])}
        </div>

        <!-- Linhas secundárias -->
        <div style="display:flex;justify-content:space-between;
                    align-items:center;margin-bottom:8px;">
            <span style="font-size:0.80rem;color:#9CA3AF">💳 Saldo bancário</span>
            <span style="font-size:0.93rem;font-weight:700;color:#E2E8F0">
                {fmt_moeda(pat["saldo_bancario"])}
            </span>
        </div>
        <div style="display:flex;justify-content:space-between;
                    align-items:center;margin-bottom:14px;">
            <span style="font-size:0.80rem;color:#9CA3AF">📈 Patrimônio investido</span>
            <span style="font-size:0.93rem;font-weight:700;color:{_COR_INVEST}">
                {fmt_moeda(pat["investido"])}
            </span>
        </div>

        <!-- Divisor -->
        <div style="border-top:1px solid #1E2533;margin-bottom:14px"></div>

        <!-- Score de saúde -->
        <div style="display:flex;justify-content:space-between;
                    align-items:center;margin-bottom:6px;">
            <span style="font-size:0.78rem;color:#718096">Saúde Financeira</span>
            <span style="font-size:0.90rem;font-weight:800;color:{cor_s}">
                {score}/100 &nbsp;
                <span style="font-size:0.72rem;font-weight:600">{label_s}</span>
            </span>
        </div>
        <div style="background:#1E2533;border-radius:4px;height:6px;overflow:hidden">
            <div style="background:{cor_s};width:{score_w}%;height:100%;
                        border-radius:4px;transition:width 0.4s"></div>
        </div>
    </div>
    """, unsafe_allow_html=True)


def _card_fluxo(flx: dict) -> None:
    """Card verde — Receitas, Despesas, Economia, Taxa de Poupança."""
    taxa = flx["taxa_poupanca_pct"]
    # barra: meta = 30%, mostra proporção até 100%
    taxa_w = min(taxa / 30.0 * 100, 100)
    cor_taxa = _COR_FLUXO if taxa >= 30 else (_COR_ALERTA if taxa >= 15 else _COR_NEGATIVO)

    saldo = flx["economia"]
    cor_saldo = _COR_FLUXO if saldo >= 0 else _COR_NEGATIVO

    st.markdown(f"""
    <div style="
        background:#12151E;
        border:1px solid #1E2533;
        border-top:3px solid {_COR_FLUXO};
        border-radius:12px;
        padding:22px 20px 18px;
        height:100%;
        min-height:260px;
    ">
        <div style="font-size:0.65rem;font-weight:800;text-transform:uppercase;
                    letter-spacing:0.14em;color:{_COR_FLUXO};margin-bottom:14px;">
            📊 Fluxo do Mês
        </div>

        <!-- Receitas e Despesas -->
        <div style="display:flex;justify-content:space-between;
                    align-items:center;margin-bottom:8px;">
            <span style="font-size:0.80rem;color:#9CA3AF">↑ Receitas</span>
            <span style="font-size:0.93rem;font-weight:700;color:{_COR_FLUXO}">
                {fmt_moeda(flx["receitas"])}
            </span>
        </div>
        <div style="display:flex;justify-content:space-between;
                    align-items:center;margin-bottom:14px;">
            <span style="font-size:0.80rem;color:#9CA3AF">↓ Despesas</span>
            <span style="font-size:0.93rem;font-weight:700;color:{_COR_NEGATIVO}">
                {fmt_moeda(flx["despesas"])}
            </span>
        </div>

        <!-- Divisor -->
        <div style="border-top:1px solid #1E2533;margin-bottom:12px"></div>

        <!-- Economia (valor principal secundário) -->
        <div style="font-size:0.78rem;color:#718096;margin-bottom:3px">Economia do mês</div>
        <div style="font-size:1.50rem;font-weight:800;color:{cor_saldo};
                    margin-bottom:14px;line-height:1">
            {fmt_moeda(saldo)}
        </div>

        <!-- Taxa de poupança + barra -->
        <div style="display:flex;justify-content:space-between;
                    align-items:center;margin-bottom:5px;">
            <span style="font-size:0.78rem;color:#718096">Taxa de poupança</span>
            <span style="font-size:0.88rem;font-weight:700;color:{cor_taxa}">
                {fmt_percentual(taxa, sinal=False)} <span style="font-size:0.70rem;color:#4A5568">/ meta 30%</span>
            </span>
        </div>
        <div style="background:#1E2533;border-radius:4px;height:6px;overflow:hidden">
            <div style="background:{cor_taxa};width:{taxa_w:.0f}%;height:100%;
                        border-radius:4px;transition:width 0.4s"></div>
        </div>
    </div>
    """, unsafe_allow_html=True)


def _card_investimentos(pat: dict, port: dict, flx: dict) -> None:
    """Card roxo — Patrimônio Investido, Rentabilidade, Dividendos, Reserva."""
    reserva = flx["meses_reserva"]
    cor_reserva = _COR_FLUXO if reserva >= 6 else (_COR_ALERTA if reserva >= 3 else _COR_NEGATIVO)
    reserva_w = min(reserva / 6.0 * 100, 100)

    rent = port["rentabilidade_mes_pct"]
    cor_rent = _COR_FLUXO if rent > 0 else (_COR_NEGATIVO if rent < 0 else "#9CA3AF")
    rent_str = fmt_percentual(rent) if rent != 0 else "0,00%"

    st.markdown(f"""
    <div style="
        background:#12151E;
        border:1px solid #1E2533;
        border-top:3px solid {_COR_INVEST};
        border-radius:12px;
        padding:22px 20px 18px;
        height:100%;
        min-height:260px;
    ">
        <div style="font-size:0.65rem;font-weight:800;text-transform:uppercase;
                    letter-spacing:0.14em;color:{_COR_INVEST};margin-bottom:14px;">
            📈 Investimentos
        </div>

        <!-- Valor principal -->
        <div style="font-size:0.78rem;color:#718096;margin-bottom:3px">Patrimônio Investido</div>
        <div style="font-size:1.85rem;font-weight:800;color:{_COR_INVEST};
                    letter-spacing:-0.02em;margin-bottom:14px;line-height:1">
            {fmt_moeda(pat["investido"])}
        </div>

        <!-- Linhas secundárias -->
        <div style="display:flex;justify-content:space-between;
                    align-items:center;margin-bottom:8px;">
            <span style="font-size:0.80rem;color:#9CA3AF">Rentabilidade mês</span>
            <span style="font-size:0.93rem;font-weight:700;color:{cor_rent}">{rent_str}</span>
        </div>
        <div style="display:flex;justify-content:space-between;
                    align-items:center;margin-bottom:8px;">
            <span style="font-size:0.80rem;color:#9CA3AF">💵 Dividendos mês</span>
            <span style="font-size:0.93rem;font-weight:700;color:#E2E8F0">
                {fmt_moeda(port["dividendos_mes"])}
            </span>
        </div>
        <div style="display:flex;justify-content:space-between;
                    align-items:center;margin-bottom:14px;">
            <span style="font-size:0.80rem;color:#9CA3AF">Ativos na carteira</span>
            <span style="font-size:0.93rem;font-weight:700;color:#E2E8F0">
                {port["num_ativos"]}
            </span>
        </div>

        <!-- Divisor -->
        <div style="border-top:1px solid #1E2533;margin-bottom:12px"></div>

        <!-- Reserva de emergência -->
        <div style="display:flex;justify-content:space-between;
                    align-items:center;margin-bottom:5px;">
            <span style="font-size:0.78rem;color:#718096">🛡️ Reserva de emergência</span>
            <span style="font-size:0.88rem;font-weight:700;color:{cor_reserva}">
                {reserva:.1f}× <span style="font-size:0.70rem;color:#4A5568">/ meta 6×</span>
            </span>
        </div>
        <div style="background:#1E2533;border-radius:4px;height:6px;overflow:hidden">
            <div style="background:{cor_reserva};width:{reserva_w:.0f}%;height:100%;
                        border-radius:4px;transition:width 0.4s"></div>
        </div>
    </div>
    """, unsafe_allow_html=True)


def _titulo_secao_css(icone: str, titulo: str, subtitulo: str, cor: str) -> None:
    """Título de seção com barra lateral colorida."""
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


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS — Gráficos
# ══════════════════════════════════════════════════════════════════════════════

def _fig_fluxo(historico: list) -> go.Figure:
    meses    = [h["mes"] for h in historico]
    receitas = [h["receitas"] for h in historico]
    despesas = [h["despesas"] for h in historico]
    economias = [h["economia"] for h in historico]

    fig = go.Figure()

    fig.add_trace(go.Bar(
        name="Receitas",
        x=meses, y=receitas,
        marker_color=_COR_FLUXO,
        opacity=0.85,
        hovertemplate="<b>Receitas %{x}</b><br>R$ %{y:,.2f}<extra></extra>",
    ))
    fig.add_trace(go.Bar(
        name="Despesas",
        x=meses, y=despesas,
        marker_color=_COR_NEGATIVO,
        opacity=0.85,
        hovertemplate="<b>Despesas %{x}</b><br>R$ %{y:,.2f}<extra></extra>",
    ))
    fig.add_trace(go.Scatter(
        name="Economia",
        x=meses, y=economias,
        mode="lines+markers",
        line={"color": _COR_PATRIMONIO, "width": 2.5},
        marker={"size": 7, "color": _COR_PATRIMONIO},
        hovertemplate="<b>Economia %{x}</b><br>R$ %{y:,.2f}<extra></extra>",
        yaxis="y2",
    ))

    fig.update_layout(
        barmode="group",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font_color="#9CA3AF",
        legend={
            "orientation": "h", "y": -0.18,
            "font": {"size": 11},
            "bgcolor": "rgba(0,0,0,0)",
        },
        margin={"t": 10, "b": 10, "l": 0, "r": 0},
        height=280,
        yaxis={
            "gridcolor":  "#1E2533",
            "tickformat": ",.0f",
            "tickprefix": "R$ ",
            "showgrid":   True,
        },
        yaxis2={
            "overlaying": "y",
            "side":       "right",
            "showgrid":   False,
            "tickformat": ",.0f",
            "tickprefix": "R$ ",
        },
        xaxis={"showgrid": False},
    )
    return fig


def _fig_classes(classes: list) -> go.Figure:
    nomes = [c["nome"] for c in classes]
    vals  = [c["valor"] for c in classes]
    cores = [c["cor"] for c in classes]

    fig = go.Figure(go.Pie(
        labels=nomes,
        values=vals,
        hole=0.58,
        marker={"colors": cores, "line": {"color": "#0E1117", "width": 2}},
        textinfo="percent",
        textfont={"size": 11},
        hovertemplate="<b>%{label}</b><br>R$ %{value:,.2f}<br>%{percent}<extra></extra>",
    ))
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        font_color="#9CA3AF",
        showlegend=False,
        margin={"t": 8, "b": 8, "l": 0, "r": 0},
        height=220,
    )
    return fig


# ══════════════════════════════════════════════════════════════════════════════
# RENDER PRINCIPAL
# ══════════════════════════════════════════════════════════════════════════════

def render() -> None:
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

    # ── Badges de status ──────────────────────────────────────────────────────
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
        badge_status(d["mes_referencia"], "info")
    with col_b3:
        cor_s = "sucesso" if pat["saude_score"] >= 60 else "alerta" if pat["saude_score"] >= 40 else "erro"
        badge_status(f"Score {pat['saude_score']}/100", cor_s)

    st.markdown("<br>", unsafe_allow_html=True)

    # ══════════════════════════════════════════════════════════════════════════
    # BLOCO 1 — 3 Cards CSS de domínio
    # ══════════════════════════════════════════════════════════════════════════
    col1, col2, col3 = st.columns(3, gap="medium")

    with col1:
        _card_patrimonio(pat, flx)

    with col2:
        _card_fluxo(flx)

    with col3:
        _card_investimentos(pat, port, flx)

    st.markdown("<br>", unsafe_allow_html=True)

    # ══════════════════════════════════════════════════════════════════════════
    # BLOCO 2 — Cashflow histórico + Despesas por categoria
    # ══════════════════════════════════════════════════════════════════════════
    _titulo_secao_css("💹", "Fluxo de Caixa & Despesas", "Histórico mensal e distribuição de gastos", _COR_FLUXO)

    col_graf, col_desp = st.columns([3, 2], gap="medium")

    with col_graf:
        st.markdown(
            '<div style="background:#12151E;border:1px solid #1E2533;'
            'border-radius:12px;padding:16px 16px 8px">',
            unsafe_allow_html=True,
        )
        st.plotly_chart(_fig_fluxo(hist), use_container_width=True, config={"displayModeBar": False})
        st.markdown('</div>', unsafe_allow_html=True)

    with col_desp:
        st.markdown(
            '<div style="background:#12151E;border:1px solid #1E2533;'
            'border-radius:12px;padding:18px 16px;">',
            unsafe_allow_html=True,
        )
        st.markdown(
            f'<div style="font-size:0.68rem;font-weight:800;text-transform:uppercase;'
            f'letter-spacing:0.12em;color:{_COR_NEGATIVO};margin-bottom:14px">'
            f'💸 DESPESAS POR CATEGORIA</div>',
            unsafe_allow_html=True,
        )

        if cats:
            for cat in cats:
                pct = cat.get("pct_usado", 0.0) or (
                    cat["gasto"] / cat["orcamento"] * 100
                    if cat.get("orcamento", 0) > 0 else 0.0
                )
                cor_pct = (
                    _COR_NEGATIVO if pct >= 90 else
                    _COR_ALERTA   if pct >= 70 else
                    _COR_FLUXO
                )
                st.markdown(f"""
                <div style="margin-bottom:10px">
                    <div style="display:flex;justify-content:space-between;
                                font-size:0.80rem;color:#CBD5E0;margin-bottom:4px">
                        <span>{cat['nome']}</span>
                        <span style="color:{cor_pct};font-weight:700">{pct:.0f}%</span>
                    </div>
                    <div style="background:#1E2533;border-radius:3px;height:5px;overflow:hidden">
                        <div style="background:{cor_pct};width:{min(pct,100):.0f}%;
                                    height:100%;border-radius:3px"></div>
                    </div>
                    <div style="font-size:0.72rem;color:#4A5568;margin-top:2px;text-align:right">
                        {fmt_moeda(cat['gasto'])} / {fmt_moeda(cat['orcamento'])}
                    </div>
                </div>
                """, unsafe_allow_html=True)

            # Maior gasto em destaque
            st.markdown(
                f'<div style="border-top:1px solid #1E2533;padding-top:10px;margin-top:4px;'
                f'font-size:0.78rem;color:#4A5568">'
                f'Maior gasto: <b style="color:#E2E8F0">{flx["maior_categoria"]}</b>'
                f' · {fmt_moeda(flx["maior_categoria_valor"])}</div>',
                unsafe_allow_html=True,
            )
        else:
            st.caption("Sem despesas registradas neste mês.")

        st.markdown('</div>', unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # ══════════════════════════════════════════════════════════════════════════
    # BLOCO 3 — Alocação de investimentos + Alertas & Próximos Passos
    # ══════════════════════════════════════════════════════════════════════════
    _titulo_secao_css("📈", "Alocação & Estratégia", "Como seu capital está distribuído e o que fazer", _COR_INVEST)

    col_aloc, col_acao = st.columns([1, 1], gap="medium")

    # ── Alocação ──────────────────────────────────────────────────────────────
    with col_aloc:
        st.markdown(
            '<div style="background:#12151E;border:1px solid #1E2533;'
            'border-radius:12px;padding:18px 16px;">',
            unsafe_allow_html=True,
        )
        st.markdown(
            f'<div style="font-size:0.68rem;font-weight:800;text-transform:uppercase;'
            f'letter-spacing:0.12em;color:{_COR_INVEST};margin-bottom:4px">'
            f'🏷️ DISTRIBUIÇÃO POR CLASSE</div>',
            unsafe_allow_html=True,
        )

        if cls:
            st.plotly_chart(_fig_classes(cls), use_container_width=True, config={"displayModeBar": False})

            # Tabela de classes
            for classe in cls:
                cor_r = _COR_FLUXO if classe["rentab_mes_pct"] >= 0 else _COR_NEGATIVO
                st.markdown(f"""
                <div style="display:flex;justify-content:space-between;align-items:center;
                            padding:6px 0;border-bottom:1px solid #1A1F2E;">
                    <div style="display:flex;align-items:center;gap:8px">
                        <div style="width:8px;height:8px;border-radius:50%;
                                    background:{classe['cor']};flex-shrink:0"></div>
                        <span style="font-size:0.82rem;color:#CBD5E0">{classe['nome']}</span>
                    </div>
                    <div style="text-align:right">
                        <span style="font-size:0.82rem;font-weight:700;color:#E2E8F0">
                            {classe['pct_carteira']:.1f}%
                        </span>
                        <span style="font-size:0.75rem;color:{cor_r};margin-left:8px">
                            {fmt_percentual(classe['rentab_mes_pct'])}
                        </span>
                    </div>
                </div>
                """, unsafe_allow_html=True)

            # Maior concentração
            maior = cls[0] if cls else None
            if maior and maior["pct_carteira"] > 25:
                st.markdown(
                    f'<div style="margin-top:10px;padding:8px 10px;'
                    f'background:rgba(246,201,14,0.08);border-left:3px solid {_COR_ALERTA};'
                    f'border-radius:0 6px 6px 0;font-size:0.78rem;color:#9CA3AF">'
                    f'⚠️ <b style="color:{_COR_ALERTA}">{maior["nome"]}</b> representa '
                    f'{maior["pct_carteira"]:.1f}% da carteira.</div>',
                    unsafe_allow_html=True,
                )
        else:
            st.caption("Sem dados de alocação disponíveis.")

        st.markdown('</div>', unsafe_allow_html=True)

    # ── Alertas + Próximos Passos ─────────────────────────────────────────────
    with col_acao:
        # Alertas
        st.markdown(
            '<div style="background:#12151E;border:1px solid #1E2533;'
            'border-radius:12px;padding:18px 16px;margin-bottom:12px">',
            unsafe_allow_html=True,
        )
        st.markdown(
            f'<div style="font-size:0.68rem;font-weight:800;text-transform:uppercase;'
            f'letter-spacing:0.12em;color:{_COR_ALERTA};margin-bottom:12px">'
            f'🔔 ALERTAS DO MÊS</div>',
            unsafe_allow_html=True,
        )
        for alerta in d["alertas"]:
            card_alerta_resumo(
                tipo=alerta["tipo"],
                icone=alerta["icone"],
                titulo=alerta["titulo"],
                descricao=alerta["descricao"],
                modulo=alerta.get("modulo", ""),
            )
        st.markdown('</div>', unsafe_allow_html=True)

        # Próximos Passos
        st.markdown(
            '<div style="background:#12151E;border:1px solid #1E2533;'
            'border-radius:12px;padding:18px 16px;">',
            unsafe_allow_html=True,
        )
        st.markdown(
            f'<div style="font-size:0.68rem;font-weight:800;text-transform:uppercase;'
            f'letter-spacing:0.12em;color:{_COR_PATRIMONIO};margin-bottom:12px">'
            f'🎯 PRÓXIMOS PASSOS</div>',
            unsafe_allow_html=True,
        )
        for passo in d["proximos_passos"]:
            card_proximo_passo(
                numero=passo["numero"],
                titulo=passo["titulo"],
                descricao=passo["descricao"],
                urgencia=passo["urgencia"],
                modulo=passo.get("modulo", ""),
            )
        st.markdown('</div>', unsafe_allow_html=True)
