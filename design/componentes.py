"""
design/componentes.py
Componentes reutilizáveis de interface para o Dashboard Financeiro Unificado.

Uso padrão em cada página:
    from design.componentes import container_pagina, card_metrica, estado_vazio
    def render():
        container_pagina("Título", "Subtítulo opcional")
        col1, col2 = st.columns(2)
        with col1:
            card_metrica("Saldo", "R$ 12.300,00", delta="+5,2%", positivo=True)
"""
from html import escape

import streamlit as st

# ══════════════════════════════════════════════════════════════════
# Estrutura de página
# ══════════════════════════════════════════════════════════════════

def container_pagina(
    titulo: str,
    subtitulo: str = "",
    icone: str = "",
    metadados: list[tuple[str, str]] | None = None,
    eyebrow: str = "Dashboard Financeiro",
) -> None:
    """
    Cabeçalho padrão de página com título, ícone e subtítulo opcionals.
    Deve ser a primeira chamada em cada render().
    """
    meta_html = "".join(
        '<span class="app-page-meta">'
        f'<small>{escape(str(label))}</small>{escape(str(valor))}'
        "</span>"
        for label, valor in (metadados or [])
        if valor not in (None, "")
    )
    icon_html = (
        f'<span class="app-page-icon" aria-hidden="true">{escape(icone)}</span>'
        if icone else ""
    )
    subtitle_html = (
        f'<p class="app-page-subtitle">{escape(subtitulo)}</p>'
        if subtitulo else ""
    )
    meta_group = (
        f'<div class="app-page-meta-group" aria-label="Contexto da página">{meta_html}</div>'
        if meta_html else ""
    )
    st.markdown(
        '<section class="app-page-hero">'
        '<div class="app-page-copy">'
        f'<div class="app-page-eyebrow">{escape(eyebrow)}</div>'
        '<div class="app-page-title-row">'
        f'{icon_html}<h1>{escape(titulo)}</h1>'
        "</div>"
        f"{subtitle_html}</div>{meta_group}</section>",
        unsafe_allow_html=True,
    )


def secao_titulo(titulo: str, icone: str = "", subtitulo: str = "") -> None:
    """Cabeçalho de seção dentro de uma página."""
    icon_html = (
        f'<span class="app-section-icon" aria-hidden="true">{escape(icone)}</span>'
        if icone else ""
    )
    subtitle_html = (
        f'<div class="app-section-subtitle">{escape(subtitulo)}</div>'
        if subtitulo else ""
    )
    st.markdown(
        '<div class="app-section-heading">'
        f'{icon_html}<div><div class="app-section-title">{escape(titulo)}</div>'
        f"{subtitle_html}</div></div>",
        unsafe_allow_html=True,
    )


# ══════════════════════════════════════════════════════════════════
# Indicadores / KPIs
# ══════════════════════════════════════════════════════════════════

def card_metrica(
    titulo: str,
    valor: str,
    delta: str | None = None,
    positivo: bool | None = None,
    ajuda: str | None = None,
    accent: str | None = None,
) -> None:
    """
    Card de KPI em CSS (não usa st.metric) — visual coeso com o restante do app.

    Args:
        titulo:   Rótulo do indicador (ex: "Patrimônio Total")
        valor:    Valor já formatado (ex: "R$ 87.450,00")
        delta:    Variação formatada (ex: "+5,2%") ou None para omitir
        positivo: True → verde, False → vermelho, None → neutro (cor do delta)
        ajuda:    Tooltip de ajuda (via atributo title do card)
        accent:   Cor da borda-esquerda; default deriva de `positivo` (neutro=azul)
    """
    cor_delta = "#00C896" if positivo is True else "#FC5C7D" if positivo is False else "#9CA3AF"
    accent = accent or ("#00C896" if positivo is True
                        else "#FC5C7D" if positivo is False else "#4A9EFF")
    delta_html = (
        f'<div class="app-kpi-delta" style="color:{cor_delta}">{escape(delta)}</div>'
        if delta else ""
    )
    ajuda_attr = f' title="{escape(ajuda, quote=True)}"' if ajuda else ""
    st.markdown(
        f'<div class="app-kpi-card"{ajuda_attr} style="--app-kpi-accent:{accent}">'
        f'<div class="app-kpi-label">{escape(titulo)}</div>'
        f'<div class="app-kpi-value">{escape(valor)}</div>'
        f'{delta_html}</div>',
        unsafe_allow_html=True,
    )


# ══════════════════════════════════════════════════════════════════
# Badges e status
# ══════════════════════════════════════════════════════════════════

def badge_status(texto: str, tipo: str = "info") -> None:
    """
    Badge colorido inline.
    tipo: 'sucesso' | 'alerta' | 'erro' | 'info' | 'neutro'
    """
    paleta = {
        "sucesso": ("#00C896", "rgba(0,200,150,0.12)"),
        "alerta":  ("#F6C90E", "rgba(246,201,14,0.12)"),
        "erro":    ("#FC5C7D", "rgba(252,92,125,0.12)"),
        "info":    ("#4A9EFF", "rgba(74,158,255,0.12)"),
        "neutro":  ("#9CA3AF", "rgba(156,163,175,0.12)"),
    }
    cor_texto, cor_fundo = paleta.get(tipo, paleta["info"])
    st.markdown(
        f'<span class="app-status-badge" style="--badge-color:{cor_texto};'
        f'--badge-bg:{cor_fundo}">{escape(texto)}</span>',
        unsafe_allow_html=True,
    )


def indicador_linha(
    label: str,
    valor: str,
    cor_valor: str = "#F7FAFC",
    badge: str | None = None,
    tipo_badge: str = "info",
) -> None:
    """
    Linha de indicador: 'Label ........... Valor  [badge]'
    Útil para listas de resumo dentro de cards.
    """
    badge_html = ""
    if badge:
        paleta = {
            "sucesso": "#00C896", "alerta": "#F6C90E",
            "erro": "#FC5C7D", "info": "#4A9EFF", "neutro": "#9CA3AF",
        }
        c = paleta.get(tipo_badge, "#4A9EFF")
        badge_html = f'<span style="color:{c};font-size:0.75rem;font-weight:600;margin-left:8px">{badge}</span>'

    st.markdown(
        f"""<div style="display:flex;justify-content:space-between;
            align-items:center;padding:6px 0;border-bottom:1px solid #1E2533;">
            <span style="color:#9CA3AF;font-size:0.88rem">{label}</span>
            <span style="color:{cor_valor};font-weight:600;font-size:0.92rem">
                {valor}{badge_html}
            </span>
        </div>""",
        unsafe_allow_html=True,
    )


# ══════════════════════════════════════════════════════════════════
# Estados de feedback
# ══════════════════════════════════════════════════════════════════

def estado_vazio(mensagem: str = "Nenhum dado disponível.", icone: str = "📭") -> None:
    """Estado vazio para seções sem dados."""
    st.markdown(
        f"""<div class="empty-state">
            <div class="empty-icon">{icone}</div>
            <div class="empty-text">{mensagem}</div>
        </div>""",
        unsafe_allow_html=True,
    )


def mensagem_erro(titulo: str, detalhe: str = "") -> None:
    """Mensagem de erro formatada."""
    corpo = f"**{titulo}**"
    if detalhe:
        corpo += f"\n\n{detalhe}"
    st.error(corpo)


def mensagem_aviso(titulo: str, detalhe: str = "") -> None:
    """Mensagem de aviso/atenção formatada."""
    corpo = f"**{titulo}**"
    if detalhe:
        corpo += f"\n\n{detalhe}"
    st.warning(corpo)


def em_construcao(fase: str, descricao: str = "") -> None:
    """
    Placeholder padrão para módulos ainda não implementados.
    Substitui o st.info() genérico dos stubs.
    """
    st.info(
        f"**Módulo em construção** — aguarda {fase}."
        + (f"\n\n{descricao}" if descricao else ""),
        icon="🔧",
    )


# ══════════════════════════════════════════════════════════════════
# Barra de progresso com rótulos
# ══════════════════════════════════════════════════════════════════

def barra_progresso(
    label: str,
    valor_atual: float,
    valor_total: float,
    fmt_valor: str | None = None,
    fmt_total: str | None = None,
) -> None:
    """
    Barra de progresso com label, valores e percentual.

    Args:
        label:       Nome do item (ex: "Reserva de Emergência")
        valor_atual: Valor acumulado
        valor_total: Meta/total
        fmt_valor:   String formatada do valor atual (ou None para exibir raw)
        fmt_total:   String formatada do valor total
    """
    pct = min(valor_atual / valor_total, 1.0) if valor_total > 0 else 0
    pct_display = f"{pct * 100:.1f}%".replace(".", ",")

    v_str = fmt_valor or f"{valor_atual:.2f}".replace(".", ",")
    t_str = fmt_total or f"{valor_total:.2f}".replace(".", ",")

    col_label, col_pct = st.columns([3, 1])
    with col_label:
        st.markdown(
            f'<span style="font-size:0.88rem;color:#CBD5E0">{label}</span>',
            unsafe_allow_html=True,
        )
    with col_pct:
        st.markdown(
            f'<span style="font-size:0.88rem;font-weight:600;'
            f'color:#00C896;float:right">{pct_display}</span>',
            unsafe_allow_html=True,
        )

    st.progress(pct)
    st.caption(f"{v_str} de {t_str}")


# ══════════════════════════════════════════════════════════════════
# Alertas compactos (para dashboard — versão resumida)
# ══════════════════════════════════════════════════════════════════

def card_alerta_resumo(
    tipo: str,
    icone: str,
    titulo: str,
    descricao: str,
    modulo: str = "",
) -> None:
    """
    Card compacto de alerta para o Dashboard Geral.
    Diferente do card completo de pages/alertas.py — mais denso e sem ação.

    tipo: 'sucesso' | 'alerta' | 'erro' | 'info'
    """
    paleta_borda = {
        "sucesso": "#00C896",
        "alerta":  "#F6C90E",
        "erro":    "#FC5C7D",
        "info":    "#4A9EFF",
    }
    paleta_fundo = {
        "sucesso": "rgba(0,200,150,0.06)",
        "alerta":  "rgba(246,201,14,0.06)",
        "erro":    "rgba(252,92,125,0.06)",
        "info":    "rgba(74,158,255,0.06)",
    }
    borda = paleta_borda.get(tipo, "#4A9EFF")
    fundo = paleta_fundo.get(tipo, "rgba(74,158,255,0.06)")
    modulo_html = (
        f'<div style="font-size:0.72rem;color:#4A5568;margin-top:4px">📁 {modulo}</div>'
        if modulo else ""
    )
    st.markdown(
        f"""<div style="
            background:{fundo};
            border-left:3px solid {borda};
            border-radius:0 8px 8px 0;
            padding:10px 14px;
            margin-bottom:8px;
        ">
            <div style="font-size:0.92rem;font-weight:600;color:#E2E8F0">
                {icone} {titulo}
            </div>
            <div style="font-size:0.80rem;color:#9CA3AF;margin-top:3px">
                {descricao}
            </div>
            {modulo_html}
        </div>""",
        unsafe_allow_html=True,
    )


# ══════════════════════════════════════════════════════════════════
# Próximos passos (ações recomendadas numeradas)
# ══════════════════════════════════════════════════════════════════

def card_proximo_passo(
    numero: int,
    titulo: str,
    descricao: str,
    urgencia: str = "media",
    modulo: str = "",
) -> None:
    """
    Card de próximo passo financeiro com número, urgência e módulo-destino.

    urgencia: 'alta' | 'media' | 'baixa'
    """
    cores_urgencia = {
        "alta":  ("#FC5C7D", "Alta"),
        "media": ("#F6C90E", "Média"),
        "baixa": ("#4A9EFF", "Baixa"),
    }
    cor, label_urgencia = cores_urgencia.get(urgencia, cores_urgencia["media"])
    modulo_html = (
        f'<span style="color:#4A5568;font-size:0.72rem">→ {modulo}</span>'
        if modulo else ""
    )
    st.markdown(
        f"""<div style="
            display:flex;
            gap:14px;
            align-items:flex-start;
            padding:10px 14px;
            background:#1A1F2E;
            border:1px solid #2D3748;
            border-radius:10px;
            margin-bottom:8px;
        ">
            <div style="
                min-width:32px;height:32px;
                background:{cor};
                color:#0E1117;
                border-radius:50%;
                display:flex;align-items:center;justify-content:center;
                font-weight:800;font-size:0.9rem;
                flex-shrink:0;margin-top:2px;
            ">{numero}</div>
            <div>
                <div style="font-size:0.92rem;font-weight:600;color:#E2E8F0">
                    {titulo}
                    <span style="
                        font-size:0.68rem;font-weight:600;
                        color:{cor};margin-left:8px;
                        vertical-align:middle;
                    ">{label_urgencia}</span>
                </div>
                <div style="font-size:0.80rem;color:#9CA3AF;margin-top:3px">
                    {descricao}
                </div>
                {modulo_html}
            </div>
        </div>""",
        unsafe_allow_html=True,
    )


# ══════════════════════════════════════════════════════════════════
# Score de saúde financeira
# ══════════════════════════════════════════════════════════════════

def score_saude(score: int, label: str = "Saúde Financeira") -> None:
    """
    Exibe o score de saúde financeira (0–100) com cor e classificação.

    0–39  → Crítico  (vermelho)
    40–59 → Atenção  (amarelo)
    60–79 → Bom      (azul)
    80–100→ Ótimo    (verde)
    """
    if score >= 80:
        cor, classificacao = "#00C896", "Ótimo"
    elif score >= 60:
        cor, classificacao = "#4A9EFF", "Bom"
    elif score >= 40:
        cor, classificacao = "#F6C90E", "Atenção"
    else:
        cor, classificacao = "#FC5C7D", "Crítico"

    st.markdown(
        f"""<div style="
            text-align:center;
            background:#1A1F2E;
            border:1px solid #2D3748;
            border-radius:12px;
            padding:20px 16px;
        ">
            <div style="font-size:0.72rem;font-weight:600;text-transform:uppercase;
                        letter-spacing:0.08em;color:#718096;margin-bottom:8px">
                {label}
            </div>
            <div style="font-size:3rem;font-weight:800;color:{cor};line-height:1">
                {score}
            </div>
            <div style="font-size:0.85rem;font-weight:600;color:{cor};margin-top:4px">
                {classificacao}
            </div>
            <div style="
                background:#2D3748;border-radius:4px;height:6px;
                margin-top:12px;overflow:hidden;
            ">
                <div style="
                    background:{cor};width:{score}%;height:100%;
                    border-radius:4px;transition:width 0.5s;
                "></div>
            </div>
        </div>""",
        unsafe_allow_html=True,
    )
