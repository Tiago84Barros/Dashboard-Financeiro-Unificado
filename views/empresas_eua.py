"""
pages/empresas_eua.py
Análise de empresas listadas em NYSE e NASDAQ via yfinance.

Status: Fase 5.x — integração yfinance pendente.
  - Ativos USD já cadastrados em `assets` serão exibidos aqui.
  - Fundamentalistas (P/E, EPS, market cap, margens) via yfinance em fase futura.

Dados: core/empresas.get_ativos() — filtra currency = 'USD'
"""
import pandas as pd
import streamlit as st

from core.empresas import get_ativos
from design.componentes import (
    badge_status,
    card_metrica,
    container_pagina,
    em_construcao,
    secao_titulo,
)


def render() -> None:
    container_pagina(
        "Empresas EUA",
        "Ativos internacionais — NYSE, NASDAQ e outras bolsas",
        "🌎",
    )

    d      = get_ativos()
    ativos = d["ativos"]

    # Filtra ativos em USD (internacionais)
    ativos_usd = [a for a in ativos if a["moeda"] == "USD"]

    # ── Badges ────────────────────────────────────────────────────────────────
    _fonte = d.get("data_source", "mock")
    _label, _tipo = (
        ("Dados reais",     "sucesso") if _fonte == "real" else
        ("Fallback (mock)", "erro")    if _fonte == "mock_fallback" else
        ("Modo mock",       "alerta")
    )
    col_b1, col_b2, *_ = st.columns([1, 1, 5])
    with col_b1:
        badge_status(_label, _tipo)
    with col_b2:
        badge_status(f"{len(ativos_usd)} ativo(s) USD", "info")

    st.markdown("<br>", unsafe_allow_html=True)

    if ativos_usd:
        # ── KPIs ──────────────────────────────────────────────────────────────
        classes_usd = {a["classe"] for a in ativos_usd}
        c1, c2, c3 = st.columns(3)
        with c1:
            card_metrica("Ativos USD", str(len(ativos_usd)))
        with c2:
            card_metrica("Classes", str(len(classes_usd)))
        with c3:
            com_cot = sum(1 for a in ativos_usd if a["tem_cotacao"])
            card_metrica("Com Cotações", str(com_cot))

        st.divider()

        # ── Tabela ────────────────────────────────────────────────────────────
        secao_titulo("Ativos Internacionais Cadastrados", "🌎")

        df = pd.DataFrame([
            {
                "Ticker":  a["ticker"],
                "Nome":    a["nome"][:40],
                "Classe":  a["classe"],
                "Setor":   a["setor"],
                "Bolsa":   a["exchange"],
                "Preço":   a["preco_atual"],
            }
            for a in ativos_usd
        ])

        st.dataframe(
            df,
            column_config={
                "Ticker": st.column_config.TextColumn("Ticker",  width="small"),
                "Nome":   st.column_config.TextColumn("Nome"),
                "Classe": st.column_config.TextColumn("Classe",  width="small"),
                "Setor":  st.column_config.TextColumn("Setor"),
                "Bolsa":  st.column_config.TextColumn("Bolsa",   width="small"),
                "Preço":  st.column_config.NumberColumn("Preço", format="$ %.2f"),
            },
            hide_index=True,
            width="stretch",
        )

        st.divider()

    else:
        st.info(
            "**Nenhum ativo USD cadastrado** na tabela `assets`. "
            "Os ativos internacionais serão exibidos aqui automaticamente "
            "quando importados com `currency = 'USD'`.",
            icon="🌎",
        )

    # ── O que está por vir ────────────────────────────────────────────────────
    st.markdown("""
### Análise Fundamentalista — EUA (Fase 5.x)

Funcionalidades planejadas:

| Indicador | Descrição | Fonte |
|-----------|-----------|-------|
| **P/E Ratio** | Preço / Lucro por ação | yfinance |
| **EPS** | Lucro por ação (TTM) | yfinance |
| **Market Cap** | Capitalização de mercado | yfinance |
| **Dividend Yield** | Dividendo anual / Preço | yfinance |
| **Gross Margin** | Margem bruta | yfinance |
| **Net Margin** | Margem líquida | yfinance |
| **Beta** | Volatilidade relativa ao S&P 500 | yfinance |
| **52w High/Low** | Máxima e mínima anuais | yfinance |

*Gráfico de preço histórico, comparativo com S&P 500 e NASDAQ.*
    """)

    em_construcao(
        "Fase 5.x",
        "Busca por ticker NYSE/NASDAQ, P/E, EPS, market cap e margens via yfinance.",
    )
