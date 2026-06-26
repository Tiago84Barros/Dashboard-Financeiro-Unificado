"""
views/fiis.py — Seleção de FIIs (fundos imobiliários)

Ranking de "bons FIIs" a partir de market.fiis (BRAPI Pro): score combina
DY 12m (↑), P/VP (↓) e liquidez diária (↑). Sem DRE/ROE (não se aplica a FII).
Fonte nova, nativa do market.* — não passa pela flag de cutover de ações.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

import core.market_read as _mr
from data_pipeline.utils.date_utils import fmt_datetime_br


def render(show_header: bool = True) -> None:
    if show_header:
        st.markdown("## 🏬 Seleção de FIIs")
        st.caption("Ranking por DY 12m · P/VP · liquidez (dados BRAPI Pro). "
                   "FII não tem DRE/ROE — a seleção usa rendimento, valor patrimonial e liquidez.")

    df = _mr.load_fiis()
    if df.empty:
        st.info("Ainda não há FIIs no banco. Rode `python run_market_ingest.py fiis` "
                "para popular `market.fiis` (ou aguarde o agendamento).")
        return

    # Só os elegíveis (com score) entram no ranking; o resto fica fora.
    ranked = df[df["Score"].notna()].copy()
    fora = len(df) - len(ranked)

    # ── Filtros ───────────────────────────────────────────────────────────────
    c1, c2, c3, c4 = st.columns([2, 1.3, 1, 1])
    with c1:
        segs = ["(todos)"] + _mr.load_fii_segmentos()
        seg = st.selectbox("Segmento", segs, index=0)
    with c2:
        tipos = ["(todos)"] + sorted(ranked["Tipo"].dropna().unique().tolist())
        tipo = st.selectbox("Tipo (CVM)", tipos, index=0,
                            help="tijolo = imóveis · papel = CRI/recebíveis · fof = cotas de FII · híbrido")
    with c3:
        dy_min = st.slider("DY 12m mín. (%)", 0.0, 20.0, 0.0, 0.5)
    with c4:
        pvp_max = st.slider("P/VP máx.", 0.5, 1.5, 1.3, 0.05)

    view = ranked.copy()
    if seg != "(todos)":
        view = view[view["Segmento"] == seg]
    if tipo != "(todos)":
        view = view[view["Tipo"] == tipo]
    view = view[(view["DY_12m"].fillna(0) * 100 >= dy_min) & (view["P/VP"].fillna(99) <= pvp_max)]
    view = view.sort_values("Score", ascending=False).reset_index(drop=True)

    # ── KPIs ──────────────────────────────────────────────────────────────────
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("FIIs no ranking", len(view))
    if not view.empty:
        top = view.iloc[0]
        k2.metric("🏆 Top score", f"{top['Ticker']}", f"{top['Score']:.0f} pts")
        k3.metric("DY 12m mediano", f"{view['DY_12m'].median()*100:.1f}%")
        k4.metric("P/VP mediano", f"{view['P/VP'].median():.2f}")

    # ── Tabela ────────────────────────────────────────────────────────────────
    if view.empty:
        st.warning("Nenhum FII atende aos filtros.")
    else:
        show = view[["Ticker", "Nome", "Segmento", "Tipo", "Preço", "DY_12m",
                     "P/VP", "VPA", "Liquidez_Diaria", "Cotistas", "Score"]].copy()
        st.dataframe(
            show, use_container_width=True, hide_index=True,
            column_config={
                "Nome": st.column_config.TextColumn("Nome", width="medium"),
                "Tipo": st.column_config.TextColumn("Tipo", help="tijolo/papel/fof/híbrido (CVM)"),
                "Preço": st.column_config.NumberColumn("Preço", format="R$ %.2f"),
                "DY_12m": st.column_config.NumberColumn("DY 12m", format="percent"),
                "P/VP": st.column_config.NumberColumn("P/VP", format="%.2f"),
                "VPA": st.column_config.NumberColumn("VPA", format="R$ %.2f", help="Valor patrimonial/cota (CVM)"),
                "Liquidez_Diaria": st.column_config.NumberColumn("Liquidez/dia", format="R$ %.0f"),
                "Cotistas": st.column_config.NumberColumn("Cotistas", format="%d"),
                "Score": st.column_config.ProgressColumn("Score", min_value=0, max_value=100,
                                                         format="%.0f"),
            },
        )

    # ── Rodapé: metodologia + frescor ─────────────────────────────────────────
    ts = df["updated_at"].max() if "updated_at" in df.columns else None
    st.caption(
        f"Score = DY 12m (45%) + P/VP invertido (30%) + liquidez (25%), por percentil. "
        f"Filtros base: P/VP ≤ 1,30 · liquidez ≥ R$ 200 mil/dia · DY ≤ 30% "
        f"({fora} fora do ranking). Segmento/Tipo/VPA/cotistas: CVM (Informe Mensal de FIIs); "
        f"preço/DY/liquidez: BRAPI. Vacância não consta no informe estruturado da CVM. "
        f"Atualizado: {fmt_datetime_br(ts) if ts is not None else '—'} (horário de Brasília)."
    )
