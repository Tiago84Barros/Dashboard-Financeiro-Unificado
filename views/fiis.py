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

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Preço", _fmt_money(d.get("Preço")))
    k2.metric("P/VP", f"{d.get('P/VP'):.2f}" if pd.notna(d.get("P/VP")) else "—")
    k3.metric("DY 12m", _fmt_pct(d.get("DY_12m")))
    k4.metric("VPA", _fmt_money(d.get("VPA")))
    k5, k6, k7, k8 = st.columns(4)
    pl = d.get("Patrimonio")
    k5.metric("Patrimônio", _fmt_money(pl / 1e9, 2) + " bi" if pd.notna(pl) else "—")
    k6.metric("Cotistas", f"{int(d['Cotistas']):,}".replace(",", ".")
              if pd.notna(d.get("Cotistas")) else "—")
    vac = d.get("Vacancia")
    if tipo in _TIPOS_TIJOLO:
        k7.metric("Vacância", _fmt_pct(vac),
                  help="Vacância média (Status Invest). " +
                       (f"Coleta: {d.get('Vacancia_Ref')}" if d.get("Vacancia_Ref") else ""))
    else:
        k7.metric("Vacância", "n/a", help="Vacância não se aplica a FIIs de papel/FoF.")
    k8.metric("Liquidez/dia", _fmt_money(d.get("Liquidez_Diaria"), 0))

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
    cols = st.columns(3)
    cols[0].metric("Nº de imóveis", int(n) if pd.notna(n) else (len(imoveis) or "—"))
    if not imoveis.empty:
        regioes = imoveis["Região"].dropna().nunique()
        cols[1].metric("Regiões", regioes or "—")
        if imoveis["Área_m2"].notna().any():
            cols[2].metric("Área total (m²)", f"{imoveis['Área_m2'].sum():,.0f}".replace(",", "."))
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
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Carteira (total)", f"{met['retorno_total']*100:.1f}%" if met["retorno_total"] is not None else "—",
              f"CAGR {met['cagr']*100:.1f}%" if met["cagr"] is not None else None)
    k2.metric("IFIX (benchmark)", f"{bret*100:.1f}%" if bret is not None else "—")
    k3.metric("Alfa vs IFIX", f"{alpha*100:+.1f} p.p." if alpha is not None else "—")
    k4.metric("Período", f"{met['anos']} anos · {met['n_ativos']} ativos")
    cols = [c for c in ("Carteira", bench_nome) if c in serie.columns]
    st.line_chart(serie.set_index("Data")[cols])
    st.caption("Índice base 100 no início da janela comum. Retorno total (cota + "
               "proventos); o benchmark é o IFIX replicado pelo ETF XFIX11 (retorno "
               "total de FIIs). Sem custos/impostos. Ferramenta educacional.")
