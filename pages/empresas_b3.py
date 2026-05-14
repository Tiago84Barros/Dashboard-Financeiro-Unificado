"""
pages/empresas_b3.py
Listagem e consulta de ativos cadastrados no banco (B3 e mercado nacional).

Seções:
  1. KPIs     — Total de ativos, por classe, com/sem cotações
  2. Filtros  — Classe, setor e busca por ticker/nome
  3. Tabela   — Dataframe filtrável com ticker, nome, classe, setor, cotação

Nota:
  Múltiplos fundamentalistas (P/L, P/VP, DY, ROE) e dados setoriais serão
  adicionados na Fase 5.8 com a migração das tabelas `multiplos` e `setores`
  do App 1 (Dashboard Financeiro / yfinance).

Dados: core/empresas.get_ativos() — mock → real (Fase 5.7)
"""
import pandas as pd
import streamlit as st

from core.empresas import get_ativos
from core.utils import fmt_moeda
from design.componentes import (
    badge_status,
    card_metrica,
    container_pagina,
    indicador_linha,
    secao_titulo,
)


def render() -> None:
    container_pagina(
        "Empresas B3",
        "Ativos cadastrados na base — ações, FIIs, ETFs e renda fixa",
        "🏢",
    )

    d      = get_ativos()
    ativos = d["ativos"]

    # ── Badges ────────────────────────────────────────────────────────────────
    _fonte = d.get("data_source", "mock")
    _label, _tipo = (
        ("Dados reais",     "sucesso") if _fonte == "real" else
        ("Fallback (mock)", "erro")    if _fonte == "mock_fallback" else
        ("Modo mock",       "alerta")
    )
    col_b1, col_b2, col_b3, *_ = st.columns([1, 1, 1, 4])
    with col_b1:
        badge_status(_label, _tipo)
    with col_b2:
        badge_status(f"{d['total']} ativos", "info")
    with col_b3:
        sem_cot = d["total"] - d["com_cotacao"]
        badge_status(
            f"{d['com_cotacao']} c/ cotação" if d["com_cotacao"] else "Sem cotações",
            "sucesso" if d["com_cotacao"] > 0 else "alerta",
        )

    if d["com_cotacao"] == 0:
        st.info(
            "**Cotações não disponíveis.** Acesse **Configurações > Cotações** "
            "para importar cotações via yfinance para todos os ativos.",
            icon="📊",
        )

    st.markdown("<br>", unsafe_allow_html=True)

    # ══════════════════════════════════════════════════════════════════════════
    # SEÇÃO 1 — KPIs
    # ══════════════════════════════════════════════════════════════════════════
    por_classe = d.get("por_classe", [])
    num_classes = len(por_classe)
    num_setores = len(d.get("por_setor", []))

    col_kpis = st.columns(min(4, num_classes + 2))
    with col_kpis[0]:
        card_metrica("Total de Ativos", str(d["total"]),
                     ajuda="Ativos cadastrados na tabela `assets`.")
    with col_kpis[1]:
        card_metrica("Clases de Ativos", str(num_classes),
                     ajuda="Número de classes distintas (ações, FIIs, ETFs…).")
    if len(col_kpis) > 2:
        with col_kpis[2]:
            card_metrica("Setores", str(num_setores),
                         ajuda="Número de setores distintos cadastrados.")
    if len(col_kpis) > 3:
        with col_kpis[3]:
            card_metrica(
                "Com Cotações",
                str(d["com_cotacao"]),
                positivo=d["com_cotacao"] == d["total"],
                ajuda="Ativos com ao menos uma cotação em `asset_quotes`.",
            )

    st.divider()

    # ══════════════════════════════════════════════════════════════════════════
    # SEÇÃO 2 — Distribuição por classe + setor
    # ══════════════════════════════════════════════════════════════════════════
    col_cl, col_st = st.columns(2)

    with col_cl:
        secao_titulo("Por Classe", "🏷️")
        for cls in por_classe:
            indicador_linha(
                cls["classe"],
                str(cls["num_ativos"]),
                badge=f"{cls['num_ativos']} ativos",
                tipo_badge="neutro",
            )

    with col_st:
        secao_titulo("Por Setor", "🏭")
        for s in d.get("por_setor", [])[:8]:
            indicador_linha(
                s["setor"],
                str(s["num_ativos"]),
                badge=f"{s['num_ativos']}",
                tipo_badge="neutro",
            )

    st.divider()

    # ══════════════════════════════════════════════════════════════════════════
    # SEÇÃO 3 — Filtros + Tabela
    # ══════════════════════════════════════════════════════════════════════════
    secao_titulo("Todos os Ativos", "📋", "Filtros abaixo")

    col_f1, col_f2, col_f3 = st.columns(3)

    # Opções de filtro
    classes_disp = sorted({a["classe"] for a in ativos})
    setores_disp = sorted({a["setor"]  for a in ativos if a["setor"] != "Outros"})

    with col_f1:
        f_classe = st.multiselect(
            "Classe",
            classes_disp,
            default=[],
            placeholder="Todas as classes",
            key="b3_classe",
            label_visibility="collapsed",
        )
    with col_f2:
        f_setor = st.multiselect(
            "Setor",
            setores_disp,
            default=[],
            placeholder="Todos os setores",
            key="b3_setor",
            label_visibility="collapsed",
        )
    with col_f3:
        f_busca = st.text_input(
            "Buscar",
            placeholder="Ticker ou nome…",
            key="b3_busca",
            label_visibility="collapsed",
        )

    # Aplicar filtros
    filtrados = ativos
    if f_classe:
        filtrados = [a for a in filtrados if a["classe"] in f_classe]
    if f_setor:
        filtrados = [a for a in filtrados if a["setor"] in f_setor]
    if f_busca:
        q = f_busca.upper()
        filtrados = [
            a for a in filtrados
            if q in a["ticker"].upper() or q in a["nome"].upper()
        ]

    if not filtrados:
        st.caption("Nenhum ativo com os filtros selecionados.")
        return

    # Montar DataFrame
    df = pd.DataFrame([
        {
            "Ticker":  a["ticker"],
            "Nome":    a["nome"][:35],
            "Classe":  a["classe"],
            "Setor":   a["setor"],
            "Moeda":   a["moeda"],
            "Bolsa":   a["exchange"],
            "Preço":   a["preco_atual"],
            "Cotação": a["ultima_cotacao"].strftime("%d/%m/%y %H:%M")
                       if a["ultima_cotacao"] else "—",
        }
        for a in filtrados
    ])

    st.dataframe(
        df,
        column_config={
            "Ticker":  st.column_config.TextColumn("Ticker",  width="small"),
            "Nome":    st.column_config.TextColumn("Nome"),
            "Classe":  st.column_config.TextColumn("Classe",  width="small"),
            "Setor":   st.column_config.TextColumn("Setor"),
            "Moeda":   st.column_config.TextColumn("Moeda",   width="small"),
            "Bolsa":   st.column_config.TextColumn("Bolsa",   width="small"),
            "Preço":   st.column_config.NumberColumn("Preço", format="R$ %.2f"),
            "Cotação": st.column_config.TextColumn("Últ. Cot.", width="small"),
        },
        hide_index=True,
        use_container_width=True,
        height=min(40 + len(filtrados) * 36, 520),
    )

    st.caption(
        f"{len(filtrados)} ativo{'s' if len(filtrados) != 1 else ''} "
        f"{'(filtrado)' if len(filtrados) < d['total'] else '(total)'}"
    )

    st.divider()
    st.info(
        "**Análise fundamentalista (Fase 5.8)** — P/L, P/VP, Dividend Yield, ROE e "
        "comparativos setoriais via yfinance serão adicionados após a migração das "
        "tabelas `multiplos` e `setores` do App 1.",
        icon="🔬",
    )
