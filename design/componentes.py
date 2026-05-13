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
import streamlit as st


# ══════════════════════════════════════════════════════════════════
# Estrutura de página
# ══════════════════════════════════════════════════════════════════

def container_pagina(titulo: str, subtitulo: str = "", icone: str = "") -> None:
    """
    Cabeçalho padrão de página com título, ícone e subtítulo opcionals.
    Deve ser a primeira chamada em cada render().
    """
    titulo_completo = f"{icone} {titulo}" if icone else titulo
    st.markdown(
        f'<h1 style="margin-bottom:0">{titulo_completo}</h1>',
        unsafe_allow_html=True,
    )
    if subtitulo:
        st.caption(subtitulo)
    st.divider()


def secao_titulo(titulo: str, icone: str = "", subtitulo: str = "") -> None:
    """Cabeçalho de seção dentro de uma página."""
    label = f"{icone} {titulo}" if icone else titulo
    st.subheader(label)
    if subtitulo:
        st.caption(subtitulo)


# ══════════════════════════════════════════════════════════════════
# Indicadores / KPIs
# ══════════════════════════════════════════════════════════════════

def card_metrica(
    titulo: str,
    valor: str,
    delta: str = None,
    positivo: bool = None,
    ajuda: str = None,
) -> None:
    """
    Card de KPI baseado em st.metric com tema aplicado via CSS.

    Args:
        titulo:   Rótulo do indicador (ex: "Patrimônio Total")
        valor:    Valor já formatado (ex: "R$ 87.450,00")
        delta:    Variação formatada (ex: "+5,2%") ou None para omitir
        positivo: True → verde, False → vermelho, None → neutro
        ajuda:    Tooltip de ajuda (aparece no ícone ?)
    """
    delta_color = "off"
    if positivo is True:
        delta_color = "normal"
    elif positivo is False:
        delta_color = "inverse"

    st.metric(
        label=titulo,
        value=valor,
        delta=delta,
        delta_color=delta_color,
        help=ajuda,
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
        f"""<span style="
            background:{cor_fundo};
            color:{cor_texto};
            border:1px solid {cor_texto};
            border-radius:20px;
            padding:3px 12px;
            font-size:0.78rem;
            font-weight:600;
            display:inline-block;
        ">{texto}</span>""",
        unsafe_allow_html=True,
    )


def indicador_linha(
    label: str,
    valor: str,
    cor_valor: str = "#F7FAFC",
    badge: str = None,
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
    fmt_valor: str = None,
    fmt_total: str = None,
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
