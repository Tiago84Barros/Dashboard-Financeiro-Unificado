"""
pages/controle_financeiro.py
Controle de receitas, despesas, categorias e orçamento mensal.
Origem: Tiago84Barros/Controle_Financeiro
Status: mock visual (Fase 2) — implementação completa na Fase 6
"""
import streamlit as st

from design.componentes import (
    card_metrica, container_pagina, secao_titulo,
    indicador_linha, em_construcao,
)
from core.utils import fmt_moeda, fmt_percentual


# ── Dados mockados ────────────────────────────────────────────────────────────
_MOCK = {
    "saldo_mes":        4_300.00,
    "receitas":         8_500.00,
    "despesas":         4_200.00,
    "orcamento_usado":  68.0,
    "categorias": [
        ("Moradia",          1_250.00, 1_500.00),
        ("Alimentação",        890.00, 1_000.00),
        ("Transporte",         410.00,   500.00),
        ("Saúde",              230.00,   400.00),
        ("Lazer",              280.00,   300.00),
        ("Assinaturas",        140.00,   150.00),
    ],
    "transacoes": [
        ("Salário",          "+R$ 8.500,00", "Receita"),
        ("Aluguel",          "-R$ 1.250,00", "Moradia"),
        ("Supermercado",     "-R$ 320,00",   "Alimentação"),
        ("Posto Combustível","-R$ 180,00",   "Transporte"),
        ("Academia",         "-R$ 99,00",    "Saúde"),
    ],
}


def render() -> None:
    container_pagina("Controle Financeiro", "Receitas, despesas e orçamento do mês", "💰")

    # ── KPIs ──────────────────────────────────────────────────────────────────
    col1, col2, col3 = st.columns(3)
    with col1:
        card_metrica(
            "Saldo do Mês",
            fmt_moeda(_MOCK["saldo_mes"]),
            delta=fmt_percentual(12.4),
            positivo=True,
        )
    with col2:
        card_metrica("Receitas", fmt_moeda(_MOCK["receitas"]))
    with col3:
        card_metrica(
            "Despesas",
            fmt_moeda(_MOCK["despesas"]),
            delta=fmt_percentual(5.0),
            positivo=False,
        )

    st.divider()

    col_cat, col_trans = st.columns([1, 1])

    # ── Orçamento por categoria ───────────────────────────────────────────────
    with col_cat:
        secao_titulo("Orçamento por Categoria", "📂",
                     f"Média geral utilizada: {fmt_percentual(_MOCK['orcamento_usado'], sinal=False)}")
        for nome, gasto, limite in _MOCK["categorias"]:
            pct = gasto / limite if limite else 0
            tipo_badge = "erro" if pct >= 0.90 else "alerta" if pct >= 0.75 else "sucesso"
            indicador_linha(
                nome,
                fmt_moeda(gasto),
                badge=f"{pct*100:.0f}%",
                tipo_badge=tipo_badge,
            )

    # ── Últimas transações ────────────────────────────────────────────────────
    with col_trans:
        secao_titulo("Últimas Transações", "🧾", "Dados de exemplo")
        for desc, valor, categoria in _MOCK["transacoes"]:
            cor = "#00C896" if valor.startswith("+") else "#FC5C7D"
            indicador_linha(
                f"{desc}  ·  *{categoria}*",
                valor,
                cor_valor=cor,
            )

        st.markdown("<br>", unsafe_allow_html=True)
        em_construcao("Fase 6", "Importação OFX/CSV e categorização automática.")
