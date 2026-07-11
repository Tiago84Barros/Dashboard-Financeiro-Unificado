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
    DESCONTINUADO (2026-07). O saneamento manual gravava em public.multiplos
    (Fundamentus/Status Invest), tabela legada DESATIVADA por injetar dados
    financeiros contraditórios. Os fundamentos agora vêm exclusivamente do
    market.* (brapi), fonte única — não há mais o que sanear por scraping.
    Mantido como no-op informativo para não quebrar as telas que o chamam.
    """
    st.caption(
        "🩺 Saneamento manual descontinuado — os fundamentos vêm agora "
        "exclusivamente do banco alimentado pela API da Brapi (market.*). "
        "Valores ausentes são tratados como N/D (rank neutro), sem reconstrução "
        "por fontes externas."
    )
