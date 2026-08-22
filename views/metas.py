"""
pages/metas.py
Acompanhamento de metas financeiras: progresso, aporte e prazo.

Seções:
  1. KPIs     — Metas Ativas, Total Acumulado, Progresso Geral
  2. Cards    — Uma card por meta com barra de progresso e status
  3. Atualizar — Expander para atualizar o valor acumulado de uma meta
  4. Nova Meta — Expander para cadastrar nova meta

Dados: core/metas.get_metas() — mock → real (Fase 5.5)
"""
from datetime import date as _date

import streamlit as st

from core.metas import (
    _TIPO_LABEL,
    atualizar_progresso,
    get_metas,
    inserir_meta,
)
from core.utils import fmt_moeda, fmt_percentual
from design.componentes import (
    badge_status,
    barra_progresso,
    card_metrica,
    container_pagina,
    secao_titulo,
)


def render() -> None:
    container_pagina("Metas", "Acompanhe o progresso dos seus objetivos financeiros", "🎯")

    d     = get_metas()
    metas = d["metas"]

    # ── Badges ────────────────────────────────────────────────────────────────
    _fonte = d.get("data_source", "mock")
    _label, _tipo = (
        ("Dados reais",     "sucesso") if _fonte == "real" else
        ("Fallback (mock)", "erro")    if _fonte == "mock_fallback" else
        ("Modo mock",       "alerta")
    )
    col_b1, col_b2, col_b3, col_b4, *_ = st.columns([1, 1, 1, 1, 3])
    with col_b1:
        badge_status(_label, _tipo)
    with col_b2:
        badge_status(f"{d['total_metas']} metas", "info")
    if d["metas_atras"] > 0:
        with col_b3:
            badge_status(f"{d['metas_atras']} atrasada{'s' if d['metas_atras'] > 1 else ''}", "erro")
    if d["metas_concl"] > 0:
        with col_b4:
            badge_status(f"{d['metas_concl']} concluída{'s' if d['metas_concl'] > 1 else ''}", "sucesso")

    if d["total_metas"] == 0:
        st.info(
            "**Nenhuma meta cadastrada.** Use o formulário abaixo para criar sua primeira meta.",
            icon="🎯",
        )
        _form_nova_meta()
        return

    st.markdown("<br>", unsafe_allow_html=True)

    # ══════════════════════════════════════════════════════════════════════════
    # SEÇÃO 1 — KPIs
    # ══════════════════════════════════════════════════════════════════════════
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        card_metrica("Metas Ativas", str(d["metas_ativas"]))
    with c2:
        card_metrica("Total Acumulado", fmt_moeda(d["total_atual"]))
    with c3:
        card_metrica("Total Objetivo", fmt_moeda(d["total_alvo"]))
    with c4:
        card_metrica(
            "Progresso Geral",
            fmt_percentual(d["pct_geral"], sinal=False),
            positivo=d["pct_geral"] >= 50,
        )

    st.divider()

    # ══════════════════════════════════════════════════════════════════════════
    # SEÇÃO 2 — Cards de metas
    # ══════════════════════════════════════════════════════════════════════════
    secao_titulo("Seus Objetivos", "🎯")

    for meta in metas:
        texto_status, tipo_status = meta["status_badge"]

        with st.container(border=True):
            col_info, col_aporte = st.columns([3, 1])

            with col_info:
                st.markdown(f"### {meta['icone']} {meta['nome']}")
                col_s1, col_s2, *_ = st.columns([1, 1, 5])
                with col_s1:
                    badge_status(texto_status, tipo_status)
                with col_s2:
                    badge_status(meta["tipo_label"], "neutro")

                st.markdown("<br>", unsafe_allow_html=True)
                barra_progresso(
                    f"Meta: {fmt_moeda(meta['alvo'])} · Prazo: {meta['prazo_fmt']}",
                    meta["atual"],
                    meta["alvo"],
                    fmt_valor=fmt_moeda(meta["atual"]),
                    fmt_total=fmt_moeda(meta["alvo"]),
                )

            with col_aporte:
                card_metrica(
                    "Aporte sugerido",
                    fmt_moeda(meta["aporte"]) if meta["aporte"] > 0 else "—",
                    ajuda="Valor mensal necessário para atingir a meta no prazo.",
                )
                faltando = max(0.0, meta["alvo"] - meta["atual"])
                if faltando > 0:
                    st.caption(f"Faltam {fmt_moeda(faltando)}")
                else:
                    st.caption("✅ Meta atingida!")

        st.markdown("", unsafe_allow_html=True)

    st.divider()

    # ══════════════════════════════════════════════════════════════════════════
    # SEÇÃO 3 — Atualizar progresso
    # ══════════════════════════════════════════════════════════════════════════
    with st.expander("📝 Atualizar progresso de uma meta", expanded=False):
        _form_atualizar_progresso(metas)

    # ══════════════════════════════════════════════════════════════════════════
    # SEÇÃO 4 — Nova meta
    # ══════════════════════════════════════════════════════════════════════════
    with st.expander("➕ Cadastrar nova meta", expanded=False):
        _form_nova_meta()


# ── Formulário: atualizar progresso ──────────────────────────────────────────

def _form_atualizar_progresso(metas: list) -> None:
    if not metas:
        st.caption("Nenhuma meta disponível.")
        return

    nomes   = [m["nome"]  for m in metas]
    ids     = [m["id"]    for m in metas]
    atuais  = [m["atual"] for m in metas]

    col1, col2 = st.columns(2)
    with col1:
        meta_idx = st.selectbox(
            "Meta",
            range(len(nomes)),
            format_func=lambda i: nomes[i],
            key="meta_upd_idx",
        )
    with col2:
        novo_valor = st.number_input(
            "Novo valor acumulado (R$)",
            min_value=0.0,
            value=float(atuais[meta_idx]),
            step=100.0,
            format="%.2f",
            key="meta_upd_valor",
        )

    col_btn, _ = st.columns([1, 3])
    with col_btn:
        if st.button("💾 Salvar progresso", type="primary", width="stretch", key="btn_upd_meta"):
            ok, msg = atualizar_progresso(ids[meta_idx], novo_valor)
            if ok:
                st.success(f"✅ Progresso de '{nomes[meta_idx]}' atualizado para {fmt_moeda(novo_valor)}.")
                st.rerun()
            else:
                st.error(f"Erro: {msg}")


# ── Formulário: nova meta ─────────────────────────────────────────────────────

def _form_nova_meta() -> None:
    tipos_opcoes = list(_TIPO_LABEL.keys())

    with st.form("form_nova_meta", clear_on_submit=True):
        col1, col2 = st.columns(2)

        with col1:
            nome = st.text_input(
                "Nome da meta *",
                max_chars=150,
                placeholder="Ex: Reserva de Emergência",
            )
            tipo_idx = st.selectbox(
                "Tipo *",
                range(len(tipos_opcoes)),
                format_func=lambda i: _TIPO_LABEL[tipos_opcoes[i]],
            )
            alvo = st.number_input(
                "Valor objetivo (R$) *",
                min_value=1.0,
                step=500.0,
                format="%.2f",
            )

        with col2:
            atual = st.number_input(
                "Valor já acumulado (R$)",
                min_value=0.0,
                step=100.0,
                format="%.2f",
                help="Quanto você já tem guardado para esta meta.",
            )
            usar_prazo = st.checkbox("Definir prazo", value=True)
            prazo = None
            if usar_prazo:
                prazo = st.date_input(
                    "Prazo *",
                    value=_date(_date.today().year + 1, 12, 31),
                    min_value=_date.today(),
                    format="DD/MM/YYYY",
                )

        submitted = st.form_submit_button("💾 Criar meta", width="stretch")

        if submitted:
            erros = []
            if not nome or not nome.strip():
                erros.append("Nome é obrigatório.")
            if alvo <= 0:
                erros.append("Valor objetivo deve ser maior que zero.")

            if erros:
                for e in erros:
                    st.error(e)
            else:
                ok, msg = inserir_meta(
                    nome=nome,
                    tipo=tipos_opcoes[tipo_idx],
                    alvo=alvo,
                    atual=atual,
                    prazo=prazo if usar_prazo else None,
                )
                if ok:
                    st.success("✅ Meta criada com sucesso!")
                else:
                    st.error(f"Erro ao criar meta: {msg}")
