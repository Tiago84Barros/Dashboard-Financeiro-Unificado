"""
views/fiis.py — Seleção de FIIs (fundos imobiliários)

Metodologia em abas (como Empresas B3, com as particularidades de FII):
  1. Ranking        — score DY 12m · P/VP · liquidez (BRAPI + CVM).
  2. Busca de ativo — detalhe por FII: histórico (P/VP·VPA), vacância, composição
                      e carteira de imóveis com região (tijolo/logística).
  3. Carteira-modelo — seleção diversificada por tipo (tijolo/papel/fof/híbrido).
  4. Backtest        — retorno total (preço + proventos reinvestidos) da carteira.

Fonte nativa do market.* (BRAPI Pro + CVM Informe Mensal). FII não tem DRE/ROE.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

import core.market_read as _mr
from data_pipeline.market import fii as _fz
from data_pipeline.utils.date_utils import fmt_datetime_br

# Metadados por tipo de FII: emoji, rótulo e cor de destaque do card.
_TIPO_META = {
    "tijolo":  ("🧱", "Tijolo",     "#00C896"),
    "papel":   ("📄", "Papel/CRI",  "#4A9EFF"),
    "fof":     ("🏢", "FoF",        "#B084F6"),
    "hibrido": ("🔀", "Híbrido",    "#F6C90E"),
}
_TIPO_ORDER = ["tijolo", "papel", "fof", "hibrido"]
_TIPO_OUTROS = ("🏦", "Outros", "#9CA3AF")

_CSS = """
<style>
.fii-hdr { display:flex;align-items:baseline;gap:10px;font-size:1.25rem;font-weight:800;
           text-transform:uppercase;letter-spacing:.04em;color:#E2E8F0;
           border-bottom:2px solid #1E2533;padding-bottom:8px;margin:24px 0 14px; }
.fii-hdr .cnt { font-size:0.74rem;color:#4A5568;font-weight:700;letter-spacing:.06em; }
/* KPIs do topo (cards CSS) */
.fii-kpi { background:#12151E;border:1px solid #1E2533;border-radius:10px;
           padding:12px 15px;margin-bottom:6px;border-left:3px solid #00C896; }
.fii-kpi .lbl { font-size:0.62rem;font-weight:700;text-transform:uppercase;
                letter-spacing:.08em;color:#4A5568;margin-bottom:5px; }
.fii-kpi .val { font-size:1.6rem;font-weight:800;line-height:1.05;color:#E2E8F0; }
.fii-kpi .sub { font-size:0.68rem;font-weight:700;margin-top:4px; }
.fii-card { background:#12151E;border:1px solid #1E2533;border-radius:12px;
            padding:12px 14px 10px;height:100%;transition:border-color .2s; }
.fii-card:hover { border-color:rgba(0,200,150,.35); }
.fii-top { display:flex;justify-content:space-between;align-items:center;margin-bottom:4px; }
.fii-tk  { font-size:0.95rem;font-weight:800;color:#E2E8F0;letter-spacing:.02em; }
.fii-score { font-size:0.66rem;font-weight:800;padding:2px 8px;border-radius:10px; }
.fii-nome { font-size:0.68rem;color:#718096;margin-bottom:2px;
            overflow:hidden;text-overflow:ellipsis;white-space:nowrap; }
.fii-seg  { font-size:0.62rem;color:#4A5568;margin-bottom:8px;
            overflow:hidden;text-overflow:ellipsis;white-space:nowrap; }
.fii-mini { display:flex;gap:12px;border-top:1px solid #1A2130;padding-top:7px; }
.fii-mini .lbl { display:block;font-size:0.54rem;font-weight:700;text-transform:uppercase;
                 letter-spacing:.06em;color:#4A5568; }
.fii-mini .val { display:block;font-size:0.86rem;font-weight:800;color:#E2E8F0;line-height:1.2; }
.fii-sc-high { background:rgba(0,200,150,.15);color:#00C896; }
.fii-sc-mid  { background:rgba(246,201,14,.15);color:#F6C90E; }
.fii-sc-low  { background:rgba(252,92,125,.15);color:#FC5C7D; }
</style>
"""

_TABS = ["📊 Ranking", "🔎 Busca de ativo", "🧺 Carteira-modelo", "📈 Backtest"]


def render(show_header: bool = True) -> None:
    if show_header:
        st.markdown("## 🏬 Seleção de FIIs")
        st.caption("Ranking (cards por tipo) → busca por ativo → carteira → backtest. "
                   "Dados BRAPI Pro + CVM (Informe Mensal de FIIs).")
    st.markdown(_CSS, unsafe_allow_html=True)

    df = _mr.load_fiis()
    if df.empty:
        st.info("Ainda não há FIIs no banco. Rode `python run_market_ingest.py fiis` "
                "(+ `fiis-cvm`, `fiis-series`) para popular.")
        return
    ranked = df[df["Score"].notna()].sort_values("Score", ascending=False).reset_index(drop=True)

    # Abas por botão (permitem trocar de aba programaticamente — ex.: card → Busca).
    active = st.session_state.get("fii_active_tab", 0)
    cols = st.columns(len(_TABS))
    for i, (c, lab) in enumerate(zip(cols, _TABS)):
        with c:
            if st.button(lab, use_container_width=True, key=f"fii_tab{i}",
                         type="primary" if active == i else "secondary"):
                st.session_state["fii_active_tab"] = i
                st.rerun()
    st.markdown("<hr style='margin:4px 0 16px;border-color:#1E2533;'>", unsafe_allow_html=True)

    if active == 0:
        _tab_ranking(df, ranked)
    elif active == 1:
        _tab_busca(df)
    elif active == 2:
        _tab_carteira(ranked)
    else:
        _tab_backtest()


# ── Tab 1: Ranking (cards por tipo) ───────────────────────────────────────────

def _score_cls(score) -> str:
    if score is None or pd.isna(score):
        return "fii-sc-mid"
    return "fii-sc-high" if score >= 66 else "fii-sc-mid" if score >= 33 else "fii-sc-low"


def _fii_card_html(row: pd.Series) -> str:
    tk = row["Ticker"]
    nome = str(row.get("Nome") or tk)[:34]
    seg = str(row.get("Segmento") or "—")[:30]
    score = row.get("Score")
    dy, pvp, preco = row.get("DY_12m"), row.get("P/VP"), row.get("Preço")
    _, _, color = _TIPO_META.get(str(row.get("Tipo") or "").lower(), _TIPO_OUTROS)
    sc_txt = f"{score:.0f}" if pd.notna(score) else "—"
    dy_txt = f"{dy*100:.1f}%" if pd.notna(dy) else "—"
    pvp_txt = f"{pvp:.2f}" if pd.notna(pvp) else "—"
    pr_txt = f"R$ {preco:.2f}" if pd.notna(preco) else "—"
    return (
        f'<div class="fii-card" style="border-top:3px solid {color};">'
        f'  <div class="fii-top"><span class="fii-tk">{tk}</span>'
        f'    <span class="fii-score {_score_cls(score)}">{sc_txt}</span></div>'
        f'  <div class="fii-nome" title="{nome}">{nome}</div>'
        f'  <div class="fii-seg">{seg}</div>'
        f'  <div class="fii-mini">'
        f'    <div><span class="lbl">DY 12m</span><span class="val">{dy_txt}</span></div>'
        f'    <div><span class="lbl">P/VP</span><span class="val">{pvp_txt}</span></div>'
        f'    <div><span class="lbl">Preço</span><span class="val">{pr_txt}</span></div>'
        f'  </div>'
        f'</div>'
    )


def _kpi_html(label: str, value, sub: str | None = None,
              sub_color: str = "#00C896", accent: str = "#00C896") -> str:
    """Card CSS de KPI (rótulo, valor grande e sub opcional)."""
    sub_html = f'<div class="sub" style="color:{sub_color};">{sub}</div>' if sub else ""
    return (f'<div class="fii-kpi" style="border-left-color:{accent};">'
            f'<div class="lbl">{label}</div><div class="val">{value}</div>{sub_html}</div>')


def _comp_tipo_chart(pf: pd.DataFrame) -> None:
    """Barras horizontais da composição por tipo, rotuladas em PERCENTUAL."""
    import plotly.express as px
    comp = (pf.groupby("tipo")["peso"].sum() * 100).sort_values(ascending=True)
    fig = px.bar(x=comp.values, y=comp.index, orientation="h",
                 text=[f"{v:.0f}%" for v in comp.values])
    fig.update_traces(textposition="outside", marker_color="#4A9EFF", cliponaxis=False)
    fig.update_layout(height=200, margin=dict(l=0, r=28, t=4, b=0),
                      xaxis=dict(visible=False, range=[0, max(float(comp.max()) * 1.18, 1)]),
                      yaxis_title=None, plot_bgcolor="rgba(0,0,0,0)",
                      paper_bgcolor="rgba(0,0,0,0)", font_color="#CBD5E0")
    st.plotly_chart(fig, use_container_width=True)


def _render_grupo(tipo_key: str, grupo: pd.DataFrame) -> None:
    """Cabeçalho do tipo + grade de cards (4 por linha), com botão Analisar."""
    emoji, label, color = _TIPO_META.get(tipo_key, _TIPO_OUTROS)
    st.markdown(
        f'<div class="fii-hdr" style="border-color:{color}55;">'
        f'<span>{emoji} {label}</span><span class="cnt">· {len(grupo)} FIIs</span></div>',
        unsafe_allow_html=True)
    grupo = grupo.reset_index(drop=True)
    for i in range(0, len(grupo), 4):
        cols = st.columns(4, gap="small")
        for j, (_, row) in enumerate(grupo.iloc[i:i + 4].iterrows()):
            with cols[j]:
                st.markdown(_fii_card_html(row), unsafe_allow_html=True)
                if st.button("Analisar 🔎", key=f"fii_an_{row['Ticker']}",
                             use_container_width=True):
                    st.session_state["fii_sel_ticker"] = row["Ticker"]
                    st.session_state["fii_active_tab"] = 1
                    st.rerun()


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
    k1.markdown(_kpi_html("FIIs no ranking", len(view), accent="#4A9EFF"),
                unsafe_allow_html=True)
    if not view.empty:
        top = view.iloc[0]
        k2.markdown(_kpi_html("🏆 Top", top["Ticker"], f"↑ {top['Score']:.0f} pts",
                              accent="#F6C90E"), unsafe_allow_html=True)
        k3.markdown(_kpi_html("DY 12m mediano", f"{view['DY_12m'].median()*100:.1f}%"),
                    unsafe_allow_html=True)
        k4.markdown(_kpi_html("P/VP mediano", f"{view['P/VP'].median():.2f}",
                              accent="#B084F6"), unsafe_allow_html=True)

    if view.empty:
        st.warning("Nenhum FII atende aos filtros.")
    else:
        tipo_lower = view["Tipo"].astype(str).str.lower()
        vistos: set[str] = set()
        for tk_tipo in _TIPO_ORDER:
            grupo = view[tipo_lower == tk_tipo]
            if not grupo.empty:
                _render_grupo(tk_tipo, grupo)
                vistos.add(tk_tipo)
        resto = view[~tipo_lower.isin(vistos)]      # tipos None/desconhecidos
        if not resto.empty:
            _render_grupo("__outros__", resto)
        with st.expander("📋 Ver como tabela"):
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


# ── Tab 2: Busca de ativo (detalhe por FII) ───────────────────────────────────

def _fmt_pct(v) -> str:
    return f"{v*100:.1f}%" if v is not None and pd.notna(v) else "—"


def _fmt_money(v, dec: int = 2) -> str:
    return f"R$ {v:,.{dec}f}" if v is not None and pd.notna(v) else "—"


_TIPOS_TIJOLO = {"tijolo", "hibrido"}


def _tab_busca(df: pd.DataFrame) -> None:
    st.caption("Selecione um FII para ver o histórico fundamentalista, vacância, "
               "composição e a carteira de imóveis (com a região, p/ tijolo/logística).")

    opts = df.sort_values("Score", ascending=False, na_position="last")
    labels = {f"{r['Ticker']} — {r['Nome']}": r["Ticker"] for _, r in opts.iterrows()}
    if not labels:
        st.info("Sem FIIs no banco.")
        return
    # pré-seleção vinda de um card da aba Ranking (botão "Analisar")
    keys = list(labels.keys())
    pre = st.session_state.pop("fii_sel_ticker", None)
    idx = next((i for i, (_, t) in enumerate(labels.items()) if t == pre), 0) if pre else 0
    sel = st.selectbox("FII", keys, index=idx)
    tk = labels[sel]

    d = _mr.load_fii_one(tk)
    if d is None or d.empty:
        st.warning(f"Sem dados para {tk}.")
        return
    tipo = (d.get("Tipo") or "").strip().lower()

    # ── Cabeçalho / KPIs ──────────────────────────────────────────────────────
    st.markdown(f"### {d.get('Ticker')} · {d.get('Nome') or ''}")
    tags = [t for t in (d.get("Tipo"), d.get("Segmento"), d.get("Gestao")) if t]
    if tags:
        st.caption(" · ".join(str(t) for t in tags))

    pl = d.get("Patrimonio")
    cot = (f"{int(d['Cotistas']):,}".replace(",", ".")
           if pd.notna(d.get("Cotistas")) else "—")
    vac = d.get("Vacancia")
    if tipo in _TIPOS_TIJOLO:
        ref = d.get("Vacancia_Ref")
        vac_val = _fmt_pct(vac)
        vac_sub = f"Status Invest{' · ' + str(ref) if ref else ''}"
    else:
        vac_val, vac_sub = "n/a", "não se aplica (papel/FoF)"

    r1 = st.columns(4)
    r1[0].markdown(_kpi_html("Preço", _fmt_money(d.get("Preço"))), unsafe_allow_html=True)
    r1[1].markdown(_kpi_html("P/VP", f"{d.get('P/VP'):.2f}" if pd.notna(d.get("P/VP")) else "—",
                             accent="#B084F6"), unsafe_allow_html=True)
    r1[2].markdown(_kpi_html("DY 12m", _fmt_pct(d.get("DY_12m")), accent="#00C896"),
                   unsafe_allow_html=True)
    r1[3].markdown(_kpi_html("VPA", _fmt_money(d.get("VPA")), accent="#4A9EFF"),
                   unsafe_allow_html=True)
    r2 = st.columns(4)
    r2[0].markdown(_kpi_html("Patrimônio", _fmt_money(pl / 1e9, 2) + " bi" if pd.notna(pl) else "—"),
                   unsafe_allow_html=True)
    r2[1].markdown(_kpi_html("Cotistas", cot, accent="#4A9EFF"), unsafe_allow_html=True)
    r2[2].markdown(_kpi_html("Vacância", vac_val, sub=vac_sub, sub_color="#4A5568",
                             accent="#F6C90E"), unsafe_allow_html=True)
    r2[3].markdown(_kpi_html("Liquidez/dia", _fmt_money(d.get("Liquidez_Diaria"), 0)),
                   unsafe_allow_html=True)

    st.divider()

    # ── Histórico fundamentalista (P/VP e VPA mensais) ────────────────────────
    st.markdown("#### 📈 Histórico fundamentalista")
    met = _mr.load_fii_metrics_mensal(tk)
    if met.empty:
        st.info("Sem série mensal da CVM para este FII (rode `fiis-metrics`). "
                "Abaixo, ainda assim, o histórico de preço e proventos.")
    else:
        c1, c2 = st.columns(2)
        with c1:
            st.caption("P/VP mensal (preço bruto ÷ VPA)")
            pvp_serie = met.dropna(subset=["P/VP"])[["Data", "P/VP"]]
            if pvp_serie.empty:
                st.caption("— sem preço bruto casado com o VPA.")
            else:
                st.line_chart(pvp_serie.set_index("Data"))
        with c2:
            st.caption("VPA mensal (valor patrimonial da cota)")
            st.line_chart(met[["Data", "VPA"]].dropna().set_index("Data"))

    # proventos (série) — sempre que houver
    series = _mr.load_fii_series((tk,))
    divs = (series.get("dividendos") or {}).get(tk) or []
    if divs:
        dv = pd.DataFrame(divs, columns=["Data", "Provento"])
        dv["Data"] = pd.to_datetime(dv["Data"], errors="coerce")
        dv = dv.dropna(subset=["Data"]).set_index("Data").sort_index()
        st.caption("Proventos pagos por cota (R$)")
        st.bar_chart(dv["Provento"])

    st.divider()

    # ── Composição de ativos ──────────────────────────────────────────────────
    comp = {"Imóveis": d.get("Pct_Imoveis"), "Papel/CRI": d.get("Pct_Papel"),
            "Caixa": d.get("Pct_Caixa"), "Cotas de FII": d.get("Pct_Fundos")}
    comp = {k: float(v) for k, v in comp.items() if v is not None and pd.notna(v)}
    if comp:
        st.markdown("#### 🧩 Composição de ativos")
        st.caption("Participação por classe de ativo sobre o ativo total (CVM).")
        st.bar_chart(pd.Series(comp, name="Composição"))

    st.divider()

    # ── Imóveis do fundo ──────────────────────────────────────────────────────
    st.markdown("#### 🏢 Imóveis do fundo")
    if tipo and tipo not in _TIPOS_TIJOLO:
        st.info(f"Este FII é do tipo **{tipo}** — não detém imóveis diretamente "
                "(carteira de papel/cotas). Sem lista de imóveis.")
        return
    imoveis = _mr.load_fii_imoveis(tk)
    n = d.get("Num_Imoveis")
    n_val = int(n) if pd.notna(n) else (len(imoveis) if not imoveis.empty else 0)
    regioes = imoveis["Região"].dropna().nunique() if not imoveis.empty else 0
    area_tot = (f"{imoveis['Área_m2'].sum():,.0f}".replace(",", ".")
                if (not imoveis.empty and imoveis["Área_m2"].notna().any()) else "—")
    cols = st.columns(3)
    cols[0].markdown(_kpi_html("Nº de imóveis", n_val, accent="#00C896"), unsafe_allow_html=True)
    cols[1].markdown(_kpi_html("Regiões", regioes or "—", accent="#B084F6"), unsafe_allow_html=True)
    cols[2].markdown(_kpi_html("Área total (m²)", area_tot, accent="#4A9EFF"), unsafe_allow_html=True)
    if not imoveis.empty:
        st.dataframe(imoveis, use_container_width=True, hide_index=True, column_config={
            "Imóvel": st.column_config.TextColumn("Imóvel", width="medium"),
            "Área_m2": st.column_config.NumberColumn("Área (m²)", format="%.0f"),
            "Vacância": st.column_config.NumberColumn("Vacância", format="percent"),
            "Pct_Receita": st.column_config.NumberColumn("% Receita", format="percent"),
        })
        # resumo por região (relevante p/ logística/tijolo)
        if imoveis["Região"].notna().any():
            por_reg = imoveis.groupby("Região").size().sort_values(ascending=False)
            st.caption("Imóveis por região")
            st.bar_chart(por_reg)
    else:
        st.info("Ainda não há imóveis coletados para este FII. Rode "
                "`python run_market_ingest.py fiis-imoveis` (coleta best-effort por scraping).")


# ── Tab 3: Carteira-modelo ────────────────────────────────────────────────────

def _tab_carteira(ranked: pd.DataFrame) -> None:
    modo = st.radio("Método de seleção",
                    ["🎯 Qualidade diversificada", "📊 Score padrão (DY·P/VP·liquidez)"],
                    horizontal=True, key="fii_cart_modo")
    st.divider()
    if modo.startswith("🎯"):
        _carteira_qualidade()
    else:
        _carteira_score(ranked)


def _carteira_score(ranked: pd.DataFrame) -> None:
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
    k1.markdown(_kpi_html("Ativos", len(port), accent="#4A9EFF"), unsafe_allow_html=True)
    k2.markdown(_kpi_html("DY 12m (ponderado)", f"{dy_w*100:.1f}%", accent="#00C896"),
                unsafe_allow_html=True)
    k3.markdown(_kpi_html("P/VP (ponderado)", f"{pvp_w:.2f}", accent="#B084F6"),
                unsafe_allow_html=True)

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
        st.caption("Composição por tipo (%)")
        _comp_tipo_chart(pf)

    # guarda p/ o backtest
    st.session_state["fii_port"] = {p["ticker"]: p["peso"] for p in port}


def _carteira_qualidade() -> None:
    st.caption("Seleção por **qualidade**: tijolos diversificados (multi-região, "
               "multi-inquilino, multi-setorial) + **papel e FoF para descorrelacionar** "
               "(oscilam por juros/CDI-IPCA, não pelo ciclo imobiliário). Líquidos, bons "
               "dividendos e bom crescimento/baixo drawdown. P/VP entra como desconto, mas "
               "com peso pequeno. Teto por tipo evita concentração em um só.")
    q = _mr.load_fii_quality()
    if q.empty:
        st.info("Sem dados de FIIs. Rode a ingestão (`fiis`, `fiis-cvm`, `fiis-series`, "
                "`fiis-metrics`, `fiis-imoveis`).")
        return

    c1, c2, c3, c4b = st.columns(4)
    liq_min = c1.slider("Liquidez mín. (R$ mi/dia)", 0.0, 20.0, 1.0, 0.5) * 1e6
    dd_max = c2.slider("Drawdown máx. tolerado (%)", 10, 60, 35, 5,
                       help="Descarta FIIs cuja pior queda pico→vale foi maior que isso.")
    dy_min = c3.slider("DY 12m mín. (%)", 0.0, 20.0, 8.0, 0.5)
    hist_min = c4b.slider("Histórico mín. (meses)", 0, 60, 24, 6,
                          help="Exige track record mínimo — dá credibilidade a Cresc./Pior "
                               "queda. Fundos com amostra menor que isso são descartados.")
    c4, c5, c6, c7 = st.columns(4)
    n_max = c4.slider("Nº de FIIs", 4, 20, 10, 1)
    max_w = c5.slider("Máx. por FII (%)", 5, 40, 20, 5) / 100.0
    max_tp = c6.slider("Máx. por tipo (%)", 20, 100, 40, 10) / 100.0
    min_tp = c7.slider("Mín. por tipo", 0, 3, 1, 1,
                       help="Garante ao menos N FIIs de cada tipo presente — força o mix "
                            "(tijolo + papel + FoF descorrelacionados).")
    st.markdown("**Critérios de diversificação** (desmarque para relaxar se sobrarem poucos FIIs)")
    g1, g2, g3, g4 = st.columns(4)
    exig_pvp = g1.checkbox("P/VP < 1", value=True)
    exig_reg = g2.checkbox("Multi-região (≥2)", value=True)
    exig_inq = g3.checkbox("Multi-inquilino (≥8 imóveis)", value=True)
    exig_set = g4.checkbox("Multi-setorial", value=True)

    # ── filtros duros ─────────────────────────────────────────────────────────
    f = q.copy()
    f = f[f["Tipo"].isin(["tijolo", "hibrido", "papel", "fof"])]   # exige tipo definido
    f = f[f["Liquidez_Diaria"].fillna(0) >= liq_min]
    f = f[f["DY_12m"].fillna(0) * 100 >= dy_min]
    f = f[f["Max_Drawdown"].fillna(0.0) >= -(dd_max / 100.0)]
    f = f[f["Hist_Meses"].fillna(0) >= hist_min]       # track record mínimo (credibilidade)
    if exig_pvp:
        f = f[f["P/VP"].fillna(9) < 1.0]
    # Os critérios "multi-*" (região/inquilino/setorial) SÓ se aplicam a FIIs de
    # tijolo/híbrido (que têm imóveis). Papel e FoF passam direto — eles entram de
    # propósito para DESCORRELACIONAR a carteira (papel segue juros/CDI-IPCA, não o
    # ciclo imobiliário do tijolo).
    brick = f["Tipo"].isin(["tijolo", "hibrido"])
    keep = pd.Series(True, index=f.index)
    if exig_reg:
        keep &= (~brick) | (f["N_Regioes"].fillna(0) >= 2)
    if exig_inq:
        keep &= (~brick) | (f["Num_Imoveis"].fillna(0) >= 8)
    if exig_set:
        keep &= (~brick) | (f["Multi_Setorial"])
    f = f[keep]

    st.caption(f"**{len(f)}** FIIs atendem aos critérios (de {len(q)} no universo).")
    if f.empty:
        st.warning("Nenhum FII passou. Relaxe algum critério (ex.: desmarque um dos "
                   "'multi-', reduza o DY mín. ou aumente o drawdown tolerado).")
        st.session_state.pop("fii_port", None)
        return

    # ── score de qualidade (DY domina; P/VP é minoritário) ────────────────────
    def _rk(s, higher=True):
        r = s.rank(pct=True)
        r = r.fillna(r.median() if r.notna().any() else 0.5)
        return r if higher else (1.0 - r)

    # diversificação: tijolo/híbrido pela carteira de imóveis; papel/FoF por base
    # (FoF é multi-ativo por natureza; papel diversifica por CRIs/indexadores).
    div_brick = (0.4 * (f["N_Regioes"].fillna(0).clip(upper=5) / 5.0)
                 + 0.3 * (f["Num_Imoveis"].fillna(0).clip(upper=40) / 40.0)
                 + 0.3 * f["Multi_Setorial"].astype(float))
    div = div_brick.where(f["Tipo"].isin(["tijolo", "hibrido"]),
                          f["Tipo"].map({"fof": 0.70, "papel": 0.55}).fillna(0.5))
    cresc = 0.6 * _rk(f["CAGR"]) + 0.4 * _rk(f["Max_Drawdown"])  # CAGR↑ e drawdown menos negativo↑
    score = 100.0 * (0.35 * _rk(f["DY_12m"])      # bons dividendos (principal)
                     + 0.25 * cresc               # crescimento / baixo drawdown
                     + 0.25 * div                 # diversificação (multi-*)
                     + 0.10 * _rk(f["Liquidez_Diaria"])
                     + 0.05 * _rk(f["P/VP"], higher=False))  # desconto P/VP (menor peso)
    f = f.assign(Qualidade=score)

    rows = [{"ticker": r["Ticker"], "score": r["Qualidade"], "tipo": r["Tipo"],
             "liquidez_diaria": r["Liquidez_Diaria"], "dy_12m": r["DY_12m"],
             "pvp": r["P/VP"], "segmento": r["Segmento"]}
            for _, r in f.iterrows()]
    port = _fz.build_portfolio(rows, n_max=n_max, max_weight=max_w,
                               max_tipo_frac=max_tp, liq_min=liq_min, min_por_tipo=min_tp)
    if not port:
        st.warning("Sem FIIs elegíveis após a diversificação por tipo. Relaxe os critérios.")
        st.session_state.pop("fii_port", None)
        return

    pf = pd.DataFrame(port).merge(
        f[["Ticker", "Liquidez_Diaria", "CAGR", "Max_Drawdown", "Hist_Meses",
           "N_Regioes", "Num_Imoveis"]],
        left_on="ticker", right_on="Ticker", how="left")

    dy_w = sum((p["dy_12m"] or 0) * p["peso"] for p in port)
    pvp_w = sum((p["pvp"] or 0) * p["peso"] for p in port)
    dd_w = float((pf["Max_Drawdown"].fillna(0) * pf["peso"]).sum())
    liq_min_port = float(pf["Liquidez_Diaria"].min())   # elo mais fraco de liquidez
    n_tipos = pf["tipo"].nunique()
    # rentabilidade anual TOTAL (cota + proventos) = CAGR ponderado (renormaliza
    # sobre os fundos com histórico suficiente).
    _cm = pf["CAGR"].notna()
    cagr_w = (float((pf.loc[_cm, "CAGR"] * pf.loc[_cm, "peso"]).sum() / pf.loc[_cm, "peso"].sum())
              if _cm.any() else None)

    r1 = st.columns(3)
    r1[0].markdown(_kpi_html("Rent. anual (total)",
                             f"{cagr_w*100:.1f}%" if cagr_w is not None else "—",
                             accent="#00C896", sub="cota + proventos, média a.a.",
                             sub_color="#4A5568"), unsafe_allow_html=True)
    r1[1].markdown(_kpi_html("Rent. dividendos (DY 12m)", f"{dy_w*100:.1f}%",
                             accent="#00C896"), unsafe_allow_html=True)
    r1[2].markdown(_kpi_html("P/VP (ponderado)", f"{pvp_w:.2f}", accent="#B084F6"),
                   unsafe_allow_html=True)
    weights = {p["ticker"]: p["peso"] for p in port}
    n_ef = _fz.effective_n(weights)
    r2 = st.columns(4)
    r2[0].markdown(_kpi_html("Ativos", f"{len(port)} · {n_tipos} tipos", accent="#4A9EFF"),
                   unsafe_allow_html=True)
    r2[1].markdown(_kpi_html("Nº efetivo", f"{n_ef:.1f}" if n_ef else "—", accent="#B084F6",
                             sub="diversificação real (1/Σpeso²)", sub_color="#4A5568"),
                   unsafe_allow_html=True)
    r2[2].markdown(_kpi_html("Drawdown médio", f"{dd_w*100:.0f}%", accent="#F6C90E"),
                   unsafe_allow_html=True)
    r2[3].markdown(_kpi_html("Liquidez mín.", f"R$ {liq_min_port/1e6:.1f} mi/dia",
                             accent="#4A9EFF", sub="menor liquidez da carteira",
                             sub_color="#4A5568"), unsafe_allow_html=True)

    cc1, cc2 = st.columns([2.6, 1])
    with cc1:
        show = pf[["ticker", "peso", "tipo", "segmento", "Liquidez_Diaria", "dy_12m", "pvp",
                   "CAGR", "Max_Drawdown", "Hist_Meses", "N_Regioes", "Num_Imoveis", "score"]]
        st.dataframe(show, use_container_width=True, hide_index=True, column_config={
            "ticker": "Ticker",
            "peso": st.column_config.NumberColumn("Peso", format="percent"),
            "tipo": "Tipo", "segmento": "Segmento",
            "Liquidez_Diaria": st.column_config.NumberColumn("Liquidez/dia", format="R$ %.0f"),
            "dy_12m": st.column_config.NumberColumn("DY 12m", format="percent"),
            "pvp": st.column_config.NumberColumn("P/VP", format="%.2f"),
            "CAGR": st.column_config.NumberColumn("Cresc. a.a.", format="percent",
                help="Crescimento anualizado pela regressão linear de ln(preço) — usa todo "
                     "o histórico, robusto a pontas atípicas."),
            "Max_Drawdown": st.column_config.NumberColumn("Pior queda", format="percent"),
            "Hist_Meses": st.column_config.NumberColumn("Hist. (m)", format="%d",
                help="Meses de histórico que embasam Cresc./Pior queda — quanto mais, "
                     "mais confiável o dado."),
            "N_Regioes": st.column_config.NumberColumn("Regiões", format="%d"),
            "Num_Imoveis": st.column_config.NumberColumn("Imóveis", format="%d"),
            "score": st.column_config.ProgressColumn("Qualidade", min_value=0, max_value=100,
                                                     format="%.0f"),
        })
    with cc2:
        st.caption("Composição por tipo (%) · descorrelação")
        _comp_tipo_chart(pf)
        if n_tipos == 1:
            st.caption("⚠️ Só um tipo — reduza o 'Máx. por tipo' ou relaxe um critério "
                       "para incluir papel/FoF.")

    st.caption("Peso da qualidade: DY 35% · crescimento/drawdown 25% · diversificação 25% · "
               "liquidez 10% · desconto P/VP 5%. Papel/FoF entram para descorrelacionar "
               "(critérios multi-* valem só p/ tijolo/híbrido).")

    # ── Diversificação: risco × nº de fundos ──────────────────────────────────
    precos = _mr.load_precos_mensais(tuple(sorted(weights)))
    rets = precos.pct_change() if not precos.empty else pd.DataFrame()
    curve = _fz.risk_curve(rets, weights) if not rets.empty else []
    st.markdown("#### 📉 Risco × nº de fundos")
    if len(curve) >= 2:
        cdf = pd.DataFrame(curve)
        cdf["Volatilidade anual (%)"] = cdf["vol"] * 100
        import plotly.express as px
        fig = px.line(cdf, x="n", y="Volatilidade anual (%)", markers=True)
        fig.update_traces(line_color="#00C896", marker_color="#00C896")
        fig.update_layout(height=280, margin=dict(l=0, r=10, t=6, b=0),
                          xaxis=dict(title="Nº de fundos na carteira", dtick=1,
                                     tickmode="linear", gridcolor="#1E2533"),
                          yaxis=dict(title="Volatilidade anual (%)", gridcolor="#1E2533"),
                          plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
                          font_color="#CBD5E0")
        st.caption(f"Volatilidade da carteira ao adicionar fundos (por peso), na janela "
                   f"histórica disponível de cada subconjunto. Onde a curva **achata**, "
                   f"incluir mais FIIs quase não reduz risco — é o indício do nº ideal. "
                   f"Nº efetivo atual: **{n_ef:.1f}** de {len(port)}.")
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.caption("Sem histórico suficiente entre os fundos selecionados para traçar a "
                   "curva (alguns são recentes demais).")

    # ── Correlação entre os FIIs ──────────────────────────────────────────────
    order = [t for t in pf.sort_values(["tipo", "peso"], ascending=[True, False])["ticker"]
             if t in getattr(rets, "columns", [])]
    if len(order) >= 2 and not rets.empty:
        corr = rets[order].corr()             # pares completos (tolera históricos distintos)
        if corr.notna().to_numpy().sum() > len(order):   # há correlações fora da diagonal
            avg_c = _fz.mean_correlation(corr)
            st.markdown("#### 🔗 Correlação entre os FIIs")
            st.caption(
                "Correlação dos retornos mensais. **Verde = baixa** correlação (bom "
                "diversificador — oscila diferente); **vermelho = alta** (andam juntos). "
                + (f"Correlação média da carteira: **{avg_c:.2f}** "
                   "(quanto menor, mais diversificada). " if avg_c is not None else "")
                + "Fundos agrupados por tipo — blocos vermelhos entre fundos do mesmo tipo "
                  "são esperados; procure pares verdes para descorrelacionar.")
            import plotly.express as px
            fig = px.imshow(corr, text_auto=".2f", zmin=-1, zmax=1, aspect="auto",
                            color_continuous_scale="RdYlGn_r")
            fig.update_layout(height=max(320, 34 * len(order)),
                              margin=dict(l=0, r=0, t=6, b=0),
                              paper_bgcolor="rgba(0,0,0,0)", font_color="#CBD5E0",
                              coloraxis_colorbar=dict(title="corr"))
            fig.update_xaxes(side="bottom", tickangle=-45)
            st.plotly_chart(fig, use_container_width=True)

    st.session_state["fii_port"] = weights


# ── Tab 4: Backtest ───────────────────────────────────────────────────────────

def _tab_backtest() -> None:
    weights = st.session_state.get("fii_port")
    if not weights:
        st.info("Monte a carteira na aba **Carteira-modelo** primeiro.")
        return
    st.caption("Retorno total (preço + proventos reinvestidos), buy-and-hold com "
               "os pesos da carteira-modelo, mensal.")
    bench_nome = "IFIX (XFIX11)"   # a brapi não tem histórico do IFIX puro; XFIX11 (ETF) o replica
    series = _mr.load_fii_series(tuple(sorted(weights)))
    bench = _mr.load_fii_series(("XFIX11",)).get("precos", {}).get("XFIX11")
    # adjusted_close já é RETORNO TOTAL (ajustado por proventos+splits) → não
    # somar dividendos de novo (evita dupla contagem). XFIX11 é retorno total.
    serie, met = _fz.backtest(weights, series.get("precos", {}), {},
                              benchmark=bench, benchmark_nome=bench_nome)
    if serie.empty:
        st.warning("Sem série histórica suficiente para o backtest.")
        return
    bret = met.get("bench_retorno")
    alpha = (met["retorno_total"] - bret) if (met["retorno_total"] is not None and bret is not None) else None
    cart_val = f"{met['retorno_total']*100:.1f}%" if met["retorno_total"] is not None else "—"
    cart_sub = f"CAGR {met['cagr']*100:.1f}%" if met["cagr"] is not None else None
    alfa_val = f"{alpha*100:+.1f} p.p." if alpha is not None else "—"
    alfa_accent = "#00C896" if (alpha or 0) >= 0 else "#FC5C7D"
    k1, k2, k3, k4 = st.columns(4)
    k1.markdown(_kpi_html("Carteira (total)", cart_val, cart_sub, accent="#00C896"),
                unsafe_allow_html=True)
    k2.markdown(_kpi_html("IFIX (benchmark)", f"{bret*100:.1f}%" if bret is not None else "—",
                          accent="#9CA3AF"), unsafe_allow_html=True)
    k3.markdown(_kpi_html("Alfa vs IFIX", alfa_val, accent=alfa_accent), unsafe_allow_html=True)
    k4.markdown(_kpi_html("Período", f"{met['anos']} anos · {met['n_ativos']} ativos",
                          accent="#4A9EFF"), unsafe_allow_html=True)
    cols = [c for c in ("Carteira", bench_nome) if c in serie.columns]
    st.line_chart(serie.set_index("Data")[cols])
    st.caption("Índice base 100 no início da janela comum. Retorno total (cota + "
               "proventos); o benchmark é o IFIX replicado pelo ETF XFIX11 (retorno "
               "total de FIIs). Sem custos/impostos. Ferramenta educacional.")
