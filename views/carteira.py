"""
pages/carteira.py
Carteira de investimentos: posições, alocação por classe/setor e custo médio.

Seções:
  1. KPIs    — total investido, valor de mercado, rentabilidade total, nº ativos
  2. Alocação — donut por classe + donut por setor com tabelas de resumo
  3. Posições — tabela completa de todas as posições com filtro interativo

Origem: Tiago84Barros/Dashboard-Investimentos (App 2)
Dados:  core/investimentos.get_carteira() — mock → real (Fase 5.1)
"""
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from core.investimentos import get_carteira
from core.utils import fmt_moeda, fmt_percentual
from design.componentes import (
    badge_status,
    card_metrica,
    container_pagina,
    indicador_linha,
    secao_titulo,
)


# ── Helpers de gráfico ────────────────────────────────────────────────────────

def _fig_donut(labels: list, values: list, cores: list, titulo_centro: str = "") -> go.Figure:
    """Donut chart genérico (classe ou setor)."""
    fig = go.Figure(go.Pie(
        labels=labels,
        values=values,
        hole=0.58,
        marker={"colors": cores, "line": {"color": "#0E1117", "width": 2}},
        textinfo="percent",
        textfont={"size": 11},
        hovertemplate="<b>%{label}</b><br>R$ %{value:,.2f}<br>%{percent}<extra></extra>",
    ))
    if titulo_centro:
        fig.add_annotation(
            text=titulo_centro,
            x=0.5, y=0.5,
            font={"size": 11, "color": "#9CA3AF"},
            showarrow=False,
        )
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        font_color="#9CA3AF",
        showlegend=True,
        legend={
            "orientation": "v",
            "x": 1.02, "y": 0.5,
            "font": {"size": 11},
        },
        margin={"t": 10, "b": 10, "l": 0, "r": 0},
        height=240,
    )
    return fig


def _cores_setor(setores: list) -> list:
    """Paleta de cores para setores (cíclica)."""
    PALETA = [
        "#4A9EFF", "#00C896", "#F6C90E", "#9B59B6",
        "#FC5C7D", "#FF6B35", "#718096", "#48BB78",
        "#ED8936", "#38B2AC",
    ]
    return [PALETA[i % len(PALETA)] for i in range(len(setores))]


# ── Tabela de posições ────────────────────────────────────────────────────────

def _build_df_posicoes(posicoes: list, cotacoes: bool) -> pd.DataFrame:
    """Monta DataFrame formatado para exibição na tabela de posições."""
    linhas = []
    for p in posicoes:
        linhas.append({
            "Ticker":          p["ticker"],
            "Nome":            p["nome"],
            "Classe":          p["classe"],
            "Setor":           p["setor"],
            "Qtd.":            p["quantidade"],
            "Preço Médio":     p["preco_medio"],
            "P. Atual":        p["preco_atual"],
            "Total Investido": p["total_investido"],
            "Valor Mercado":   p["valor_mercado"],
            "Rentab. %":       p["rentab_pct"],
            "% Carteira":      p["pct_carteira"],
        })
    return pd.DataFrame(linhas)


# ── Render principal ──────────────────────────────────────────────────────────

def render() -> None:
    # ── Dados ─────────────────────────────────────────────────────────────────
    d          = get_carteira()
    posicoes   = d["posicoes"]
    por_classe = d["por_classe"]
    por_setor  = d["por_setor"]
    cotacoes   = d["cotacoes_disponiveis"]

    # ── Cabeçalho ─────────────────────────────────────────────────────────────
    container_pagina(
        "Carteira",
        f"Posições, alocação e custo médio · {d['num_ativos']} ativos",
        "💼",
    )

    # ── Badges ────────────────────────────────────────────────────────────────
    _fonte = d.get("data_source", "mock")
    if _fonte == "real":
        _badge_label, _badge_tipo = "Dados reais", "sucesso"
    elif _fonte == "mock_fallback":
        _badge_label, _badge_tipo = "Fallback (mock)", "erro"
    else:
        _badge_label, _badge_tipo = "Modo mock", "alerta"

    col_b1, col_b2, col_b3, col_b4, *_ = st.columns([1, 1, 1, 1, 3])
    with col_b1:
        badge_status(_badge_label, _badge_tipo)
    with col_b2:
        badge_status(f"{d['num_ativos']} ativos", "info")
    with col_b3:
        badge_status(
            "Cotações ativas" if cotacoes else "Sem cotações",
            "sucesso" if cotacoes else "alerta",
        )
    with col_b4:
        badge_status(f"{len(por_classe)} classes", "neutro")

    # ── Aviso sem cotações ────────────────────────────────────────────────────
    if not cotacoes:
        st.info(
            "**Cotações de mercado não disponíveis** — os valores de mercado e a "
            "rentabilidade exibidos são estimativas pelo preço médio de aquisição (custo histórico). "
            "Alimente a tabela `asset_quotes` via yfinance para ativar valores reais.",
            icon="📈",
        )

    st.markdown("<br>", unsafe_allow_html=True)

    # ══════════════════════════════════════════════════════════════════════════
    # SEÇÃO 1 — KPIs
    # ══════════════════════════════════════════════════════════════════════════
    c1, c2, c3, c4 = st.columns(4)

    delta_mercado = d["total_mercado"] - d["total_investido"]
    delta_pct_str = fmt_percentual(d["rentabilidade_total_pct"])
    delta_pos     = d["rentabilidade_total_pct"] >= 0 if cotacoes else None

    with c1:
        card_metrica(
            "Total Investido",
            fmt_moeda(d["total_investido"]),
            ajuda="Custo histórico total: soma de (quantidade × preço médio) para todas as posições.",
        )
    with c2:
        card_metrica(
            "Valor de Mercado",
            fmt_moeda(d["total_mercado"]),
            delta=fmt_moeda(delta_mercado) if cotacoes else "sem cotações",
            positivo=delta_pos,
            ajuda=(
                "Soma do valor atual de todas as posições pelo preço de mercado. "
                "Requer cotações em asset_quotes."
                if not cotacoes
                else "Soma do valor atual de todas as posições."
            ),
        )
    with c3:
        card_metrica(
            "Rentabilidade Total",
            fmt_percentual(d["rentabilidade_total_pct"], sinal=False),
            delta=delta_pct_str if cotacoes else None,
            positivo=delta_pos,
            ajuda="(Valor Mercado − Total Investido) / Total Investido × 100.",
        )
    with c4:
        card_metrica(
            "Ativos na Carteira",
            str(d["num_ativos"]),
            ajuda="Número de posições com quantidade > 0 em portfolio_positions.",
        )

    st.divider()

    # ══════════════════════════════════════════════════════════════════════════
    # SEÇÃO 2 — Alocação por Classe e por Setor
    # ══════════════════════════════════════════════════════════════════════════
    col_cls, col_set = st.columns(2)

    # ── Coluna 1: Por Classe ──────────────────────────────────────────────────
    with col_cls:
        secao_titulo("Alocação por Classe", "📊")

        if por_classe:
            fig_cls = _fig_donut(
                labels=[c["nome"] for c in por_classe],
                values=[c["valor_mercado"] for c in por_classe],
                cores=[c["cor"] for c in por_classe],
                titulo_centro=f"{len(por_classe)} classes",
            )
            st.plotly_chart(fig_cls, use_container_width=True)

            # Tabela de classes
            st.markdown(
                '<div style="font-size:0.75rem;font-weight:700;text-transform:uppercase;'
                'letter-spacing:0.06em;color:#4A5568;margin-bottom:4px">Resumo por Classe</div>',
                unsafe_allow_html=True,
            )
            for cls in por_classe:
                cor_rentab = "#00C896" if cls["rentab_pct"] >= 0 else "#FC5C7D"
                rentab_txt = fmt_percentual(cls["rentab_pct"]) if cotacoes else "—"
                indicador_linha(
                    f"{cls['nome']} · {cls['num_ativos']} ativo{'s' if cls['num_ativos'] != 1 else ''}",
                    fmt_moeda(cls["valor_mercado"]),
                    cor_valor="#E2E8F0",
                    badge=f"{cls['pct_carteira']:.1f}%",
                    tipo_badge="alerta" if cls["pct_carteira"] > 40 else "neutro",
                )
                st.markdown(
                    f'<div style="text-align:right;font-size:0.75rem;'
                    f'color:{cor_rentab};margin-top:-4px;margin-bottom:4px">'
                    f'rentab. {rentab_txt}</div>',
                    unsafe_allow_html=True,
                )
        else:
            st.caption("Nenhuma classe encontrada.")

    # ── Coluna 2: Por Setor ───────────────────────────────────────────────────
    with col_set:
        secao_titulo("Alocação por Setor", "🏭")

        if por_setor:
            cores_set = _cores_setor(por_setor)
            fig_set = _fig_donut(
                labels=[s["nome"] for s in por_setor],
                values=[s["valor_mercado"] for s in por_setor],
                cores=cores_set,
                titulo_centro=f"{len(por_setor)} setores",
            )
            st.plotly_chart(fig_set, use_container_width=True)

            # Tabela de setores
            st.markdown(
                '<div style="font-size:0.75rem;font-weight:700;text-transform:uppercase;'
                'letter-spacing:0.06em;color:#4A5568;margin-bottom:4px">Resumo por Setor</div>',
                unsafe_allow_html=True,
            )
            for setor, cor in zip(por_setor, cores_set):
                indicador_linha(
                    setor["nome"],
                    fmt_moeda(setor["valor_mercado"]),
                    cor_valor="#E2E8F0",
                    badge=f"{setor['pct_carteira']:.1f}%",
                    tipo_badge="neutro",
                )
        else:
            st.caption("Nenhum setor encontrado.")

    st.divider()

    # ══════════════════════════════════════════════════════════════════════════
    # SEÇÃO 3 — Tabela de Posições
    # ══════════════════════════════════════════════════════════════════════════
    secao_titulo(
        "Posições",
        "📋",
        f"Todas as posições · ordenado por total investido DESC",
    )

    # Filtros rápidos
    classes_disponiveis = sorted({p["classe"] for p in posicoes})
    col_f1, col_f2, _ = st.columns([2, 2, 3])
    with col_f1:
        filtro_classe = st.multiselect(
            "Filtrar por classe",
            options=classes_disponiveis,
            default=[],
            placeholder="Todas as classes",
            key="carteira_filtro_classe",
            label_visibility="collapsed",
        )
    with col_f2:
        setores_disponiveis = sorted({p["setor"] for p in posicoes})
        filtro_setor = st.multiselect(
            "Filtrar por setor",
            options=setores_disponiveis,
            default=[],
            placeholder="Todos os setores",
            key="carteira_filtro_setor",
            label_visibility="collapsed",
        )

    # Aplica filtros
    posicoes_filtradas = posicoes
    if filtro_classe:
        posicoes_filtradas = [p for p in posicoes_filtradas if p["classe"] in filtro_classe]
    if filtro_setor:
        posicoes_filtradas = [p for p in posicoes_filtradas if p["setor"] in filtro_setor]

    if not posicoes_filtradas:
        st.caption("Nenhuma posição encontrada com os filtros selecionados.")
        return

    df = _build_df_posicoes(posicoes_filtradas, cotacoes)

    st.dataframe(
        df,
        column_config={
            "Ticker": st.column_config.TextColumn("Ticker", width="small"),
            "Nome":   st.column_config.TextColumn("Nome"),
            "Classe": st.column_config.TextColumn("Classe", width="small"),
            "Setor":  st.column_config.TextColumn("Setor"),
            "Qtd.": st.column_config.NumberColumn(
                "Qtd.",
                format="%.2f",
                width="small",
            ),
            "Preço Médio": st.column_config.NumberColumn(
                "Preço Médio",
                format="R$ %.4f",
            ),
            "P. Atual": st.column_config.NumberColumn(
                "P. Atual",
                format="R$ %.4f",
                help="Cotação mais recente em asset_quotes. Igual ao preço médio quando sem cotações.",
            ),
            "Total Investido": st.column_config.NumberColumn(
                "Total Investido",
                format="R$ %.2f",
            ),
            "Valor Mercado": st.column_config.NumberColumn(
                "Valor Mercado",
                format="R$ %.2f",
            ),
            "Rentab. %": st.column_config.NumberColumn(
                "Rentab. %",
                format="%.2f%%",
                help="(Valor Mercado − Total Investido) / Total Investido × 100.",
            ),
            "% Carteira": st.column_config.NumberColumn(
                "% Carteira",
                format="%.2f%%",
                help="Participação no valor total de mercado da carteira.",
            ),
        },
        hide_index=True,
        use_container_width=True,
        height=min(40 + len(posicoes_filtradas) * 36, 560),
    )

    # ── Rodapé com totais da seleção ──────────────────────────────────────────
    if filtro_classe or filtro_setor:
        total_selecionado = sum(p["total_investido"] for p in posicoes_filtradas)
        mercado_selecionado = sum(p["valor_mercado"] for p in posicoes_filtradas)
        pct_selecionado = round(
            total_selecionado / d["total_investido"] * 100, 1
        ) if d["total_investido"] > 0 else 0.0
        st.caption(
            f"Seleção: {len(posicoes_filtradas)} posições · "
            f"Investido {fmt_moeda(total_selecionado)} · "
            f"Mercado {fmt_moeda(mercado_selecionado)} · "
            f"{pct_selecionado}% da carteira"
        )
