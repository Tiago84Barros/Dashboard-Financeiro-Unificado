"""
views/fiis.py — Seleção de FIIs (fundos imobiliários)

Metodologia em abas (como Empresas B3, com as particularidades de FII):
  1. Ranking        — score DY 12m · P/VP · liquidez (BRAPI + CVM).
  2. Carteira-modelo — seleção diversificada por tipo (tijolo/papel/fof/híbrido).
  3. Backtest        — retorno total (preço + proventos reinvestidos) da carteira.

Fonte nativa do market.* (BRAPI Pro + CVM Informe Mensal). FII não tem DRE/ROE.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

import core.market_read as _mr
from data_pipeline.market import fii as _fz
from data_pipeline.utils.date_utils import fmt_datetime_br


def render(show_header: bool = True) -> None:
    if show_header:
        st.markdown("## 🏬 Seleção de FIIs")
        st.caption("Ranking → carteira diversificada → backtest. Dados BRAPI Pro + "
                   "CVM (Informe Mensal de FIIs).")

    df = _mr.load_fiis()
    if df.empty:
        st.info("Ainda não há FIIs no banco. Rode `python run_market_ingest.py fiis` "
                "(+ `fiis-cvm`, `fiis-series`) para popular.")
        return
    ranked = df[df["Score"].notna()].sort_values("Score", ascending=False).reset_index(drop=True)

    t_rank, t_cart, t_bt = st.tabs(["📊 Ranking", "🧺 Carteira-modelo", "📈 Backtest"])
    with t_rank:
        _tab_ranking(df, ranked)
    with t_cart:
        _tab_carteira(ranked)
    with t_bt:
        _tab_backtest()


# ── Tab 1: Ranking ────────────────────────────────────────────────────────────

def _tab_ranking(df: pd.DataFrame, ranked: pd.DataFrame) -> None:
    fora = len(df) - len(ranked)
    c1, c2, c3, c4 = st.columns([2, 1.3, 1, 1])
    with c1:
        seg = st.selectbox("Segmento", ["(todos)"] + _mr.load_fii_segmentos(), index=0)
    with c2:
        tipos = ["(todos)"] + sorted(ranked["Tipo"].dropna().unique().tolist())
        tipo = st.selectbox("Tipo", tipos, index=0,
                            help="tijolo=imóveis · papel=CRI · fof=cotas de FII · híbrido")
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

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("FIIs no ranking", len(view))
    if not view.empty:
        k2.metric("🏆 Top", f"{view.iloc[0]['Ticker']}", f"{view.iloc[0]['Score']:.0f} pts")
        k3.metric("DY 12m mediano", f"{view['DY_12m'].median()*100:.1f}%")
        k4.metric("P/VP mediano", f"{view['P/VP'].median():.2f}")

    if view.empty:
        st.warning("Nenhum FII atende aos filtros.")
    else:
        show = view[["Ticker", "Nome", "Segmento", "Tipo", "Preço", "DY_12m",
                     "P/VP", "VPA", "Liquidez_Diaria", "Cotistas", "Score"]]
        st.dataframe(show, use_container_width=True, hide_index=True, column_config={
            "Nome": st.column_config.TextColumn("Nome", width="medium"),
            "Preço": st.column_config.NumberColumn("Preço", format="R$ %.2f"),
            "DY_12m": st.column_config.NumberColumn("DY 12m", format="percent"),
            "P/VP": st.column_config.NumberColumn("P/VP", format="%.2f"),
            "VPA": st.column_config.NumberColumn("VPA", format="R$ %.2f"),
            "Liquidez_Diaria": st.column_config.NumberColumn("Liquidez/dia", format="R$ %.0f"),
            "Cotistas": st.column_config.NumberColumn("Cotistas", format="%d"),
            "Score": st.column_config.ProgressColumn("Score", min_value=0, max_value=100, format="%.0f"),
        })
    ts = df["updated_at"].max() if "updated_at" in df.columns else None
    st.caption(f"Score = DY 12m (45%) + P/VP invertido (30%) + liquidez (25%). "
               f"{fora} fora do ranking (P/VP>1,30 · liquidez<R$200k/dia · DY>30%). "
               f"Atualizado: {fmt_datetime_br(ts) if ts is not None else '—'}.")


# ── Tab 2: Carteira-modelo ────────────────────────────────────────────────────

def _tab_carteira(ranked: pd.DataFrame) -> None:
    st.caption("Carteira diversificada a partir do ranking: pesos por score, com "
               "teto por FII e limite por tipo (evita concentração em um só segmento).")
    c1, c2, c3 = st.columns(3)
    n_max = c1.slider("Nº de FIIs", 4, 20, 10, 1)
    max_w = c2.slider("Máx. por FII (%)", 5, 40, 20, 5) / 100.0
    max_tp = c3.slider("Máx. por tipo (%)", 30, 100, 50, 10) / 100.0

    rows = [{"ticker": r["Ticker"], "score": r["Score"], "tipo": r["Tipo"],
             "liquidez_diaria": r["Liquidez_Diaria"], "dy_12m": r["DY_12m"],
             "pvp": r["P/VP"], "segmento": r["Segmento"]}
            for _, r in ranked.iterrows()]
    port = _fz.build_portfolio(rows, n_max=n_max, max_weight=max_w, max_tipo_frac=max_tp)
    if not port:
        st.warning("Sem FIIs elegíveis para a carteira.")
        st.session_state.pop("fii_port", None)
        return

    pf = pd.DataFrame(port)
    # KPIs ponderados
    dy_w = sum((p["dy_12m"] or 0) * p["peso"] for p in port)
    pvp_w = sum((p["pvp"] or 0) * p["peso"] for p in port)
    k1, k2, k3 = st.columns(3)
    k1.metric("Ativos", len(port))
    k2.metric("DY 12m (ponderado)", f"{dy_w*100:.1f}%")
    k3.metric("P/VP (ponderado)", f"{pvp_w:.2f}")

    cc1, cc2 = st.columns([2, 1])
    with cc1:
        show = pf[["ticker", "peso", "tipo", "segmento", "dy_12m", "pvp", "score"]]
        st.dataframe(show, use_container_width=True, hide_index=True, column_config={
            "ticker": "Ticker",
            "peso": st.column_config.NumberColumn("Peso", format="percent"),
            "tipo": "Tipo", "segmento": "Segmento",
            "dy_12m": st.column_config.NumberColumn("DY 12m", format="percent"),
            "pvp": st.column_config.NumberColumn("P/VP", format="%.2f"),
            "score": st.column_config.NumberColumn("Score", format="%.0f"),
        })
    with cc2:
        comp = pf.groupby("tipo")["peso"].sum().sort_values(ascending=False)
        st.caption("Composição por tipo")
        st.bar_chart(comp)

    # guarda p/ o backtest
    st.session_state["fii_port"] = {p["ticker"]: p["peso"] for p in port}


# ── Tab 3: Backtest ───────────────────────────────────────────────────────────

def _tab_backtest() -> None:
    weights = st.session_state.get("fii_port")
    if not weights:
        st.info("Monte a carteira na aba **Carteira-modelo** primeiro.")
        return
    st.caption("Retorno total (preço + proventos reinvestidos), buy-and-hold com "
               "os pesos da carteira-modelo, mensal.")
    series = _mr.load_fii_series(tuple(sorted(weights)))
    # adjusted_close já é RETORNO TOTAL (ajustado por proventos+splits) → não
    # somar dividendos de novo (evita dupla contagem).
    serie, met = _fz.backtest(weights, series.get("precos", {}), {})
    if serie.empty:
        st.warning("Sem série histórica suficiente para o backtest.")
        return
    k1, k2, k3 = st.columns(3)
    k1.metric("Retorno total", f"{met['retorno_total']*100:.1f}%" if met["retorno_total"] is not None else "—")
    k2.metric("CAGR", f"{met['cagr']*100:.1f}%" if met["cagr"] is not None else "—")
    k3.metric("Período", f"{met['anos']} anos · {met['n_ativos']} ativos")
    st.line_chart(serie.set_index("Data")["Carteira"])
    st.caption("Índice base 100 no início. Retorno total reinveste os proventos "
               "mensais. Não considera custos/impostos. Ferramenta educacional.")
