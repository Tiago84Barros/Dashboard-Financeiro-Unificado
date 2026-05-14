"""
pages/controle_financeiro.py
Controle de receitas, despesas, categorias e orçamento mensal.

Seções:
  1. Seletor de mês + KPIs
  2. Orçamento por categoria + Últimas transações
  3. Formulário para adicionar nova transação (expander)

Origem: Tiago84Barros/Controle_Financeiro (App 3)
Dados:  core/controle.get_controle() — mock → real (Fase 5.4)
"""
from datetime import date as _date

import streamlit as st

from core.controle import get_controle, get_opcoes_formulario, inserir_transacao
from core.utils import fmt_moeda, fmt_percentual, delta_str
from design.componentes import (
    badge_status,
    barra_progresso,
    card_metrica,
    container_pagina,
    indicador_linha,
    secao_titulo,
)

_MESES_PT_FULL = {
    1: "Janeiro", 2: "Fevereiro", 3: "Março",    4: "Abril",
    5: "Maio",    6: "Junho",     7: "Julho",     8: "Agosto",
    9: "Setembro",10: "Outubro",  11: "Novembro", 12: "Dezembro",
}
_MESES_PT_CURTO = {
    1: "Jan", 2: "Fev", 3: "Mar", 4: "Abr",
    5: "Mai", 6: "Jun", 7: "Jul", 8: "Ago",
    9: "Set", 10: "Out", 11: "Nov", 12: "Dez",
}


def _opcoes_mes(n: int = 12) -> list[dict]:
    """Gera os últimos N meses como opções de seleção."""
    hoje = _date.today()
    result = []
    for i in range(n):
        month = hoje.month - i
        year  = hoje.year
        while month <= 0:
            month += 12
            year  -= 1
        result.append({
            "label": f"{_MESES_PT_CURTO[month]} {year}",
            "ano":   year,
            "mes":   month,
        })
    return result


def render() -> None:
    container_pagina("Controle Financeiro", "Receitas, despesas e orçamento", "💰")

    # ── Seletor de mês ────────────────────────────────────────────────────────
    opcoes     = _opcoes_mes(12)
    labels_mes = [o["label"] for o in opcoes]

    col_sel, *_ = st.columns([2, 5])
    with col_sel:
        idx = st.selectbox(
            "Mês de referência",
            range(len(labels_mes)),
            format_func=lambda i: labels_mes[i],
            key="cf_mes_idx",
            label_visibility="collapsed",
        )

    sel = opcoes[idx]
    d   = get_controle(sel["ano"], sel["mes"])

    # ── Badges ────────────────────────────────────────────────────────────────
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
        badge_status(f"{d['num_transacoes']} lançamentos", "neutro")

    if d["num_transacoes"] == 0 and _fonte != "mock":
        st.info(
            f"**Nenhum lançamento encontrado em {d['mes_referencia']}.** "
            "Adicione uma nova transação abaixo ou selecione outro mês.",
            icon="📭",
        )

    st.markdown("<br>", unsafe_allow_html=True)

    # ══════════════════════════════════════════════════════════════════════════
    # SEÇÃO 1 — KPIs
    # ══════════════════════════════════════════════════════════════════════════
    c1, c2, c3, c4 = st.columns(4)

    saldo_pos = d["saldo_mes"] >= 0

    with c1:
        card_metrica(
            "Saldo do Mês",
            fmt_moeda(d["saldo_mes"]),
            positivo=saldo_pos,
            ajuda="Receitas − Despesas do mês selecionado.",
        )
    with c2:
        card_metrica(
            "Receitas",
            fmt_moeda(d["receitas"]),
            positivo=True if d["receitas"] > 0 else None,
            ajuda="Total de entradas (type = income) no mês.",
        )
    with c3:
        card_metrica(
            "Despesas",
            fmt_moeda(d["despesas"]),
            positivo=False if d["despesas"] > 0 else None,
            ajuda="Total de saídas (type = expense) no mês.",
        )
    with c4:
        card_metrica(
            "Taxa de Poupança",
            fmt_percentual(d["taxa_poupanca_pct"], sinal=False),
            positivo=d["taxa_poupanca_pct"] >= 30,
            ajuda="(Receitas − Despesas) / Receitas × 100. Meta: ≥ 30%.",
        )

    st.divider()

    # ══════════════════════════════════════════════════════════════════════════
    # SEÇÃO 2 — Orçamento + Transações
    # ══════════════════════════════════════════════════════════════════════════
    col_orc, col_tx = st.columns([1, 1])

    # ── Orçamento por categoria ───────────────────────────────────────────────
    with col_orc:
        cats = d["categorias"]
        secao_titulo(
            "Orçamento por Categoria",
            "📂",
            f"{len(cats)} categorias com gastos",
        )
        if cats:
            for cat in cats:
                barra_progresso(
                    cat["nome"],
                    cat["gasto"],
                    cat["orcamento"],
                    fmt_valor=fmt_moeda(cat["gasto"]),
                    fmt_total=fmt_moeda(cat["orcamento"]),
                )
        else:
            st.caption("Nenhuma despesa por categoria neste mês.")

    # ── Transações ────────────────────────────────────────────────────────────
    with col_tx:
        txs = d["transacoes"]
        secao_titulo(
            "Lançamentos",
            "🧾",
            f"{d['num_transacoes']} registro{'s' if d['num_transacoes'] != 1 else ''}",
        )

        # Filtro rápido receita/despesa
        col_ftipo, _ = st.columns([2, 3])
        with col_ftipo:
            f_tipo = st.selectbox(
                "Tipo",
                ["Todos", "Receitas", "Despesas"],
                key="cf_ftipo",
                label_visibility="collapsed",
            )

        txs_filtradas = txs
        if f_tipo == "Receitas":
            txs_filtradas = [t for t in txs if t["eh_receita"]]
        elif f_tipo == "Despesas":
            txs_filtradas = [t for t in txs if not t["eh_receita"]]

        if txs_filtradas:
            for tx in txs_filtradas[:15]:
                cor = "#00C896" if tx["eh_receita"] else "#FC5C7D"
                indicador_linha(
                    f'{tx["data_fmt"]} · {tx["descricao"][:30]}',
                    tx["valor_fmt"],
                    cor_valor=cor,
                    badge=tx["categoria"],
                    tipo_badge="neutro",
                )
            if len(txs_filtradas) > 15:
                st.caption(f"… e mais {len(txs_filtradas) - 15} lançamentos")
        else:
            st.caption("Nenhum lançamento com o filtro selecionado.")

    st.divider()

    # ══════════════════════════════════════════════════════════════════════════
    # SEÇÃO 3 — Adicionar transação
    # ══════════════════════════════════════════════════════════════════════════
    with st.expander("➕ Adicionar nova transação", expanded=False):
        _form_nova_transacao(sel["ano"], sel["mes"])


# ── Formulário de nova transação ──────────────────────────────────────────────

def _form_nova_transacao(ano: int, mes: int) -> None:
    """Formulário para inserção de nova transação via INSERT."""
    opcoes = get_opcoes_formulario()
    cats   = opcoes.get("categorias", [])
    contas = opcoes.get("contas", [])

    if not contas:
        st.warning("Nenhuma conta cadastrada. Cadastre uma conta antes de adicionar transações.")
        return

    with st.form("form_nova_tx", clear_on_submit=True):
        col1, col2 = st.columns(2)

        with col1:
            tipo = st.selectbox(
                "Tipo *",
                ["expense", "income"],
                format_func=lambda x: "Despesa" if x == "expense" else "Receita",
            )
            descricao = st.text_input("Descrição *", max_chars=255, placeholder="Ex: Supermercado")
            valor = st.number_input(
                "Valor (R$) *",
                min_value=0.01,
                step=0.01,
                format="%.2f",
                help="Digite o valor absoluto — o sinal é aplicado automaticamente pelo tipo.",
            )

        with col2:
            # Filtra categorias pelo tipo selecionado
            cats_tipo = [c for c in cats if c["tipo"] == tipo or c["tipo"] == "transfer"]
            cat_nomes = [c["nome"] for c in cats_tipo]
            cat_ids   = [c["id"]   for c in cats_tipo]

            cat_idx = st.selectbox(
                "Categoria",
                range(len(cat_nomes)),
                format_func=lambda i: cat_nomes[i] if cat_nomes else "—",
            ) if cat_nomes else None

            conta_nomes = [c["nome"] for c in contas]
            conta_ids   = [c["id"]   for c in contas]
            conta_idx = st.selectbox(
                "Conta *",
                range(len(conta_nomes)),
                format_func=lambda i: conta_nomes[i],
            )

            data_tx = st.date_input(
                "Data *",
                value=_date(ano, mes, 1),
                min_value=_date(ano, mes, 1),
                max_value=_date(ano, mes, 28) if mes == 2 else _date(ano, mes, 30),
            )

        submitted = st.form_submit_button("💾 Salvar transação", use_container_width=True)

        if submitted:
            # Validações
            erros = []
            if not descricao or not descricao.strip():
                erros.append("Descrição é obrigatória.")
            if valor <= 0:
                erros.append("Valor deve ser maior que zero.")
            if not contas:
                erros.append("Nenhuma conta disponível.")

            if erros:
                for e in erros:
                    st.error(e)
            else:
                categoria_id = cat_ids[cat_idx] if cat_idx is not None and cat_ids else None
                conta_id     = conta_ids[conta_idx]

                ok, msg = inserir_transacao(
                    descricao=descricao,
                    valor=valor,
                    tipo=tipo,
                    data=data_tx,
                    categoria_id=categoria_id,
                    conta_id=conta_id,
                )
                if ok:
                    st.success("✅ Transação adicionada com sucesso! Recarregue a página para ver.")
                else:
                    st.error(f"Erro ao salvar: {msg}")
