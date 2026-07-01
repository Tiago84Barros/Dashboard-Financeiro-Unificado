"""
views/data_quality_panel.py
Componentes de UI reutilizáveis para transparência e saneamento de dados,
usados na Análise Avançada e na Criação de Portfólio.

- render_quality_report: mostra outliers, empresas incompletas, duplicados e
  campos críticos ausentes (sem mascarar nada).
- render_healing_panel: botão "🩺 Sanear dados" → pré-visualização dry-run
  (Fundamentus/Status Invest, ≥2 fontes) e, sob confirmação, gravação no banco
  com backup + auditoria.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

import core.data_quality as _dq
import core.data_healing as _heal
from design.componentes import card_metrica


def render_quality_report(df: pd.DataFrame, key_prefix: str = "dq") -> dict:
    """Mostra o relatório de qualidade do DataFrame de múltiplos. Retorna o relatório."""
    if df is None or df.empty:
        st.caption("Sem dados para auditar a qualidade.")
        return {}
    rep = _dq.generate_data_quality_report(df)
    insuf = rep.get("empresas_insuficientes", [])
    out = rep.get("outliers", [])
    dup = rep.get("duplicados", [])
    sem_setor = rep.get("sem_setor", [])

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        card_metrica("Outliers", len(out), accent="#F6C90E")
    with c2:
        card_metrica("Incompletas", len(insuf), accent="#FC5C7D")
    with c3:
        card_metrica("Duplicados", len(dup), accent="#F6C90E")
    with c4:
        card_metrica("Sem setor", len(sem_setor), accent="#9CA3AF")

    if insuf:
        st.warning(
            f"⚠️ {len(insuf)} empresa(s) com campos críticos ausentes — "
            f"não devem ser ranqueadas como completas: {', '.join(insuf[:15])}"
            + (" …" if len(insuf) > 15 else "")
        )
    if out:
        with st.expander(f"🔎 {len(out)} indicador(es) fora de faixa coerente (tratados como N/D)"):
            st.dataframe(pd.DataFrame(out), use_container_width=True, hide_index=True)
    if dup:
        st.caption(f"Tickers duplicados: {', '.join(dup[:20])}")
    if sem_setor:
        st.caption(f"Sem setor/segmento: {', '.join(sem_setor[:20])}")
    if not (insuf or out or dup or sem_setor):
        st.success("✅ Sem inconsistências críticas detectadas neste conjunto.")
    return rep


def render_healing_panel(tickers, key_prefix: str = "heal") -> None:
    """
    Botão de saneamento persistente. Fluxo seguro: pré-visualizar (dry-run) →
    revisar → aplicar (grava no banco com backup + auditoria).
    """
    tks = [str(t).upper().replace(".SA", "") for t in (tickers or []) if t]
    if not tks:
        return
    st.markdown("##### 🩺 Saneamento de dados (cruza Fundamentus + Status Invest e grava no banco)")
    st.caption(
        "Busca o dado faltante/divergente em ≥2 fontes; em divergência prioriza "
        "Fundamentus/Status Invest. Pré-visualize antes de gravar."
    )
    prev_key = f"{key_prefix}_preview"
    col1, col2 = st.columns([1, 1])
    with col1:
        if st.button("🔎 Pré-visualizar saneamento", key=f"{key_prefix}_btn_prev",
                     use_container_width=True):
            with st.spinner("Consultando Fundamentus e Status Invest…"):
                try:
                    st.session_state[prev_key] = _heal.preview_healing(tks)
                except Exception as exc:
                    st.error(f"Falha ao pré-visualizar: {exc}")
                    st.session_state[prev_key] = pd.DataFrame()

    preview = st.session_state.get(prev_key)
    if preview is None:
        return
    if preview.empty:
        st.info("Nada a corrigir: dados já consistentes ou sem corroboração de ≥2 fontes.")
        return

    grav = preview[preview["Acao"].isin(["corrigido", "preenchido"])]
    revisar = preview[~preview["Acao"].isin(["corrigido", "preenchido"])]
    st.dataframe(preview, use_container_width=True, hide_index=True)
    st.caption(f"{len(grav)} gravável(is) (≥2 fontes) · {len(revisar)} para revisão manual.")

    if not grav.empty:
        with col2:
            confirm = st.checkbox("Confirmo gravar as correções no banco",
                                  key=f"{key_prefix}_confirm")
            if st.button("✅ Aplicar no banco (com backup + auditoria)",
                         key=f"{key_prefix}_apply", type="primary",
                         disabled=not confirm, use_container_width=True):
                with st.spinner("Gravando correções (backup + auditoria)…"):
                    out = _heal.apply_healing(preview)
                if out.get("erro"):
                    st.error(f"Erro ao gravar: {out['erro']}")
                else:
                    st.success(
                        f"✅ {out.get('gravados', 0)} valor(es) corrigido(s) no banco "
                        f"({out.get('backupados', 0)} backupados). Recarregue para ver atualizado."
                    )
                    st.session_state.pop(prev_key, None)
