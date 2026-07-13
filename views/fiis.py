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

import math
from html import escape

import pandas as pd
import streamlit as st

import core.market_read as _mr
from core.fii_methodology import (MacroScenario, classify_macro_regime,
                                 evaluate_publication_gate, score_fiis_by_type)
from core.fii_portfolio_v4 import PortfolioPolicy, optimize_diligence_portfolio
from core.fii_selection_explanations import build_selection_explanations
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
.fii-info-card { background:linear-gradient(145deg,#12151E,#10131B);border:1px solid #1E2533;
                 border-left:3px solid #4A9EFF;border-radius:12px;padding:13px 15px;
                 margin:8px 0 12px;color:#A0AEC0;font-size:.78rem;line-height:1.5; }
.fii-info-card .title { color:#E2E8F0;font-size:.66rem;font-weight:800;
                        text-transform:uppercase;letter-spacing:.08em;margin-bottom:4px; }
.fii-scenario-grid { display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px;
                     margin:5px 0 12px; }
.fii-scenario { background:#12151E;border:1px solid #1E2533;border-radius:10px;
                padding:10px 12px; }
.fii-scenario .name { color:#718096;font-size:.61rem;font-weight:750;text-transform:uppercase;
                      letter-spacing:.06em;white-space:nowrap; }
.fii-scenario .impact { color:#E2E8F0;font-size:1.02rem;font-weight:850;margin-top:3px; }
.fii-scenario.pos { border-top:2px solid #00C896; }
.fii-scenario.pos .impact { color:#00C896; }
.fii-scenario.neg { border-top:2px solid #FC5C7D; }
.fii-scenario.neg .impact { color:#FC5C7D; }
.fii-selection-card { background:linear-gradient(145deg,#12151E,#10131B);border:1px solid #1E2533;
                      border-top:3px solid #00C896;border-radius:12px;margin:7px 0 10px;
                      overflow:hidden; }
.fii-selection-card summary { cursor:pointer;list-style:none;padding:13px 14px; }
.fii-selection-card summary::-webkit-details-marker { display:none; }
.fii-selection-card summary:hover { background:rgba(255,255,255,.018); }
.fii-selection-head { display:flex;align-items:center;justify-content:space-between;gap:8px; }
.fii-selection-ticker { color:#E2E8F0;font-size:1rem;font-weight:850; }
.fii-selection-rank { color:#00C896;background:rgba(0,200,150,.12);border-radius:12px;
                      padding:3px 8px;font-size:.64rem;font-weight:800;white-space:nowrap; }
.fii-selection-meta { color:#718096;font-size:.68rem;margin-top:3px; }
.fii-selection-body { border-top:1px solid #1E2533;padding:11px 14px 13px;color:#A0AEC0;
                      font-size:.74rem;line-height:1.45; }
.fii-selection-body .section { color:#CBD5E0;font-size:.62rem;font-weight:800;
                               text-transform:uppercase;letter-spacing:.07em;margin:8px 0 3px; }
.fii-selection-body ul { margin:3px 0 6px;padding-left:18px; }
.fii-selection-body li { margin-bottom:3px; }
.fii-selection-caveat { background:rgba(246,201,14,.07);border:1px solid rgba(246,201,14,.18);
                        border-radius:8px;padding:7px 9px;margin-top:7px;color:#D6C56E; }
@media (max-width: 800px) { .fii-scenario-grid { grid-template-columns:repeat(2,minmax(0,1fr)); } }
</style>
"""

_TABS = ["📊 Diligência", "🔎 Busca de ativo", "🧺 Carteira-modelo", "📈 Retrospectiva"]


def render(show_header: bool = True) -> None:
    if show_header:
        st.markdown("## Seleção de FIIs — Lista de Diligência")
        st.caption("Metodologia v4 específica por tipo. Enquanto a validação point-in-time "
                   "não for aprovada, a saída não é recomendação definitiva nem Carteira Modelo.")
    st.markdown(_CSS, unsafe_allow_html=True)

    df = _mr.load_fiis()
    if df.empty:
        st.info("Ainda não há FIIs no banco. Rode `python run_market_ingest.py fiis` "
                "(+ `fiis-cvm`, `fiis-series`) para popular.")
        return
    df = df.copy()
    # P/VP efetivo (fix auditoria FII 2026-07): preço ÷ VPA CVM quando
    # disponível — a MESMA fonte da aba Busca; senão o priceToBook da brapi.
    # Score e exibição passam a usar o mesmo número.
    if "VPA" in df.columns:
        df["P/VP"] = [
            _fz.pvp_efetivo(p, v, b)
            for p, v, b in zip(df["Preço"], df["VPA"], df["P/VP"])
        ]
    inputs = _mr.load_fii_methodology_inputs()
    validation = _mr.load_fii_validation_status()
    scored_v4 = score_fiis_by_type(
        inputs.to_dict("records") if not inputs.empty else [],
        validation_status="passed" if validation.get("status") == "passed" else "unvalidated",
    )
    score_map = {r["ticker"]: r for r in scored_v4}
    df["Score"] = df["Ticker"].map(lambda ticker: (score_map.get(ticker) or {}).get("type_score"))
    df["Confiança"] = df["Ticker"].map(lambda ticker: (score_map.get(ticker) or {}).get("confidence"))
    df["Cobertura"] = df["Ticker"].map(lambda ticker: (score_map.get(ticker) or {}).get("coverage"))
    df["Status_Publicação"] = df["Ticker"].map(
        lambda ticker: (score_map.get(ticker) or {}).get("publication_status", "diligence_only"))
    gate = evaluate_publication_gate(
        scored_v4, expected_universe=len(df),
        validation_status="passed" if validation.get("status") == "passed" else "unvalidated",
    )
    st.session_state["fii_publication_gate"] = gate
    if gate.can_publish_recommendation:
        st.success("Critérios de cobertura, confiança e validação atendidos.")
    else:
        st.warning("Publicação bloqueada: " + " · ".join(gate.reasons))
    ranked = df[df["Score"].notna()].sort_values(
        "Score", ascending=False
    ).reset_index(drop=True)

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
    tk = escape(str(row["Ticker"]))
    nome = escape(str(row.get("Nome") or tk)[:34], quote=True)
    seg = escape(str(row.get("Segmento") or "—")[:30])
    score = row.get("Score")
    dy, pvp = row.get("DY_12m"), row.get("P/VP")
    confidence, coverage = row.get("Confiança"), row.get("Cobertura")
    _, _, color = _TIPO_META.get(str(row.get("Tipo") or "").lower(), _TIPO_OUTROS)
    sc_txt = f"{score:.0f}" if pd.notna(score) else "—"
    dy_txt = f"{dy*100:.1f}%" if pd.notna(dy) else "—"
    pvp_txt = f"{pvp:.2f}" if pd.notna(pvp) else "—"
    conf_txt = f"{confidence:.0%}" if pd.notna(confidence) else "—"
    cov_txt = f"{coverage:.0%}" if pd.notna(coverage) else "—"
    return (
        f'<div class="fii-card" style="border-top:3px solid {color};">'
        f'  <div class="fii-top"><span class="fii-tk">{tk}</span>'
        f'    <span class="fii-score {_score_cls(score)}">{sc_txt}</span></div>'
        f'  <div class="fii-nome" title="{nome}">{nome}</div>'
        f'  <div class="fii-seg">{seg}</div>'
        f'  <div class="fii-mini">'
        f'    <div><span class="lbl">DY 12m</span><span class="val">{dy_txt}</span></div>'
        f'    <div><span class="lbl">P/VP</span><span class="val">{pvp_txt}</span></div>'
        f'    <div><span class="lbl">Conf.</span><span class="val">{conf_txt}</span></div>'
        f'    <div><span class="lbl">Cob.</span><span class="val">{cov_txt}</span></div>'
        f'  </div>'
        f'</div>'
    )


def _kpi_html(label: str, value, sub: str | None = None,
              sub_color: str = "#00C896", accent: str = "#00C896") -> str:
    """Card CSS de KPI (rótulo, valor grande e sub opcional)."""
    label, value = escape(str(label)), escape(str(value))
    sub_html = (
        f'<div class="sub" style="color:{sub_color};">{escape(str(sub))}</div>'
        if sub else ""
    )
    return (f'<div class="fii-kpi" style="border-left-color:{accent};">'
            f'<div class="lbl">{label}</div><div class="val">{value}</div>{sub_html}</div>')


def _info_card_html(title: str, body: str, *, accent: str = "#4A9EFF") -> str:
    return (f'<div class="fii-info-card" style="border-left-color:{accent};">'
            f'<div class="title">{escape(title)}</div>{escape(body)}</div>')


def _scenario_cards_html(values: dict[str, float]) -> str:
    cards = []
    for name, value in values.items():
        css = "pos" if float(value) >= 0 else "neg"
        label = str(name).replace("_", " ").title()
        cards.append(
            f'<div class="fii-scenario {css}"><div class="name">{escape(label)}</div>'
            f'<div class="impact">{float(value):+.1%}</div></div>'
        )
    return '<div class="fii-scenario-grid">' + "".join(cards) + "</div>"


def _selection_card_html(explanation: dict, *, expanded: bool = False) -> str:
    ticker = escape(str(explanation.get("ticker") or "—"))
    fii_type = str(explanation.get("tipo") or "").lower()
    _, type_label, color = _TIPO_META.get(fii_type, _TIPO_OUTROS)
    strengths = "".join(
        f"<li>{escape(str(reason))}</li>" for reason in explanation.get("strengths") or []
    ) or "<li>Sem destaque quantitativo adicional.</li>"
    caveats = explanation.get("caveats") or []
    caveat_html = ""
    if caveats:
        caveat_html = (
            '<div class="section">Pontos que exigem diligência</div>'
            '<div class="fii-selection-caveat">' + "<br>".join(
                "• " + escape(str(caveat)) for caveat in caveats
            ) + "</div>"
        )
    open_attr = " open" if expanded else ""
    return (
        f'<details class="fii-selection-card" style="border-top-color:{color};"{open_attr}>'
        '<summary><div class="fii-selection-head">'
        f'<span class="fii-selection-ticker">{ticker}</span>'
        f'<span class="fii-selection-rank">#{int(explanation.get("rank") or 0)} de '
        f'{int(explanation.get("peer_count") or 0)} · top {int(explanation.get("top_percent") or 0)}%</span>'
        '</div>'
        f'<div class="fii-selection-meta">{escape(type_label)} · peso '
        f'{float(explanation.get("weight") or 0):.1%}</div></summary>'
        '<div class="fii-selection-body"><div class="section">Destaques perante os pares</div>'
        f'<ul>{strengths}</ul><div class="section">Papel na seleção</div>'
        f'<div>{escape(str(explanation.get("role") or "—"))}</div>{caveat_html}</div></details>'
    )


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


def _merge_portfolio_views(primary: pd.DataFrame | None,
                           complementary: pd.DataFrame | None) -> pd.DataFrame:
    """Une as seleções v4 e complementar sem duplicar ativos ou métricas comuns."""
    frames = [frame.copy() for frame in (primary, complementary)
              if frame is not None and not frame.empty]
    if not frames:
        return pd.DataFrame()
    merged = frames[0]
    for extra in frames[1:]:
        merged = merged.merge(extra, on="Ticker", how="outer", suffixes=("", "__extra"))
        for column in list(merged.columns):
            if not column.endswith("__extra"):
                continue
            base = column.removesuffix("__extra")
            if base in merged.columns:
                merged[base] = merged[base].combine_first(merged[column])
                merged = merged.drop(columns=column)
            else:
                merged = merged.rename(columns={column: base})
    preferred = [
        "Ticker", "Tipo", "Segmento", "Peso v4", "Peso complementar",
        "Score v4", "Score complementar", "Confiança", "Cobertura", "DY 12m",
        "P/VP", "Liquidez/dia", "Cresc. a.a.", "Pior queda", "Hist. (m)",
        "Regiões", "Imóveis", "Status",
    ]
    ordered = [column for column in preferred if column in merged.columns]
    ordered.extend(column for column in merged.columns if column not in ordered)
    sort_columns = [column for column in ("Peso v4", "Peso complementar")
                    if column in merged.columns]
    result = merged[ordered]
    if sort_columns:
        result = result.sort_values(by=sort_columns, ascending=False, na_position="last")
    return result.reset_index(drop=True)


def _render_portfolio_table(slot, primary: pd.DataFrame | None,
                            complementary: pd.DataFrame | None = None) -> None:
    show = _merge_portfolio_views(primary, complementary)
    if show.empty:
        return
    slot.dataframe(show, use_container_width=True, hide_index=True, column_config={
        "Peso v4": st.column_config.NumberColumn(format="percent"),
        "Peso complementar": st.column_config.NumberColumn(format="percent"),
        "Score v4": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.1f"),
        "Score complementar": st.column_config.ProgressColumn(
            min_value=0, max_value=100, format="%.1f"),
        "Confiança": st.column_config.ProgressColumn(min_value=0, max_value=1, format="percent"),
        "Cobertura": st.column_config.ProgressColumn(min_value=0, max_value=1, format="percent"),
        "DY 12m": st.column_config.NumberColumn(format="percent"),
        "P/VP": st.column_config.NumberColumn(format="%.2f"),
        "Liquidez/dia": st.column_config.NumberColumn(format="R$ %.0f"),
        "Cresc. a.a.": st.column_config.NumberColumn(format="percent"),
        "Pior queda": st.column_config.NumberColumn(format="percent"),
        "Hist. (m)": st.column_config.NumberColumn(format="%d"),
        "Regiões": st.column_config.NumberColumn(format="%d"),
        "Imóveis": st.column_config.NumberColumn(format="%d"),
    })


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
                         "P/VP", "VPA", "Liquidez_Diaria", "Cotistas", "Score",
                         "Confiança", "Cobertura", "Status_Publicação"]]
            st.dataframe(show, use_container_width=True, hide_index=True, column_config={
                "Nome": st.column_config.TextColumn("Nome", width="medium"),
                "Preço": st.column_config.NumberColumn("Preço", format="R$ %.2f"),
                "DY_12m": st.column_config.NumberColumn("DY 12m", format="percent"),
                "P/VP": st.column_config.NumberColumn("P/VP", format="%.2f"),
                "VPA": st.column_config.NumberColumn("VPA", format="R$ %.2f"),
                "Liquidez_Diaria": st.column_config.NumberColumn("Liquidez/dia", format="R$ %.0f"),
                "Cotistas": st.column_config.NumberColumn("Cotistas", format="%d"),
                "Score": st.column_config.ProgressColumn("Score", min_value=0, max_value=100, format="%.0f"),
                "Confiança": st.column_config.ProgressColumn("Confiança", min_value=0, max_value=1, format="percent"),
                "Cobertura": st.column_config.ProgressColumn("Cobertura", min_value=0, max_value=1, format="percent"),
                "Status_Publicação": "Status",
            })
    ts = df["updated_at"].max() if "updated_at" in df.columns else None
    st.caption(f"Metodologia v4.1.0: comparação somente dentro de cada categoria; "
               f"dados ausentes reduzem cobertura e confiança, sem imputação neutra. "
               f"{fora} fundos ficaram sem score por tipo ausente/inválido. "
               f"Atualizado: {fmt_datetime_br(ts) if ts is not None else '—'}. "
               "As limitações e métricas críticas ausentes impedem o uso como recomendação.")


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
    # Transparência de defasagem (fix auditoria FII 2026-07): a decisão usa
    # dados CVM do último informe publicado e vacância de scraping — o
    # usuário precisa ver de QUANDO são antes de montar a carteira.
    _avisos = []
    if "cvm_ref_date" in ranked.columns:
        _ref_cvm = pd.to_datetime(ranked["cvm_ref_date"], errors="coerce").max()
        if pd.notna(_ref_cvm):
            _avisos.append(f"dados CVM (VPA/PL/composição) do informe de "
                           f"**{_ref_cvm.strftime('%m/%Y')}**")
    if "vacancia_ref_date" in ranked.columns:
        _ref_vac = pd.to_datetime(ranked["vacancia_ref_date"], errors="coerce").max()
        if pd.notna(_ref_vac):
            _avisos.append(f"vacância coletada por scraping em "
                           f"**{_ref_vac.strftime('%d/%m/%Y')}** (data da coleta, "
                           "não do dado)")
    if _avisos:
        st.markdown(_info_card_html(
            "Defasagem das fontes",
            " · ".join(item.replace("**", "") for item in _avisos) +
            ". Preço, DY e liquidez vêm da última ingestão Brapi.",
            accent="#F6C90E",
        ), unsafe_allow_html=True)
    primary_table = _carteira_v4()
    st.divider()
    st.subheader("Análises complementares preservadas")
    st.markdown(_info_card_html(
        "Visões anteriores preservadas",
        "As análises continuam disponíveis integralmente para comparação, retrospectiva "
        "e auditoria. Elas não substituem os gates da metodologia v4.",
        accent="#B084F6",
    ), unsafe_allow_html=True)
    modo = st.radio(
        "Método de seleção complementar",
        ["🎯 Qualidade diversificada", "📊 Score padrão (DY·P/VP·liquidez)"],
        horizontal=True, key="fii_cart_modo",
    )
    if modo.startswith("🎯"):
        _carteira_qualidade(primary_table)
    else:
        _carteira_score(ranked, primary_table)


def _carteira_v4():
    st.subheader("Carteira de Diligência v4")
    st.markdown(_info_card_html(
        "Como a carteira é construída",
        "Otimização de renda, qualidade, confiança e perdas em cenários. As bandas táticas "
        "variam por regime e o rebalanceamento é orientado por eventos.",
        accent="#00C896",
    ), unsafe_allow_html=True)
    c1, c2, c3, c4 = st.columns(4)
    selic = c1.number_input("Selic (%)", 0.0, 30.0, 15.0, .25)
    ipca = c2.number_input("IPCA (%)", -2.0, 20.0, 4.5, .25)
    delta = c3.number_input("Δ Selic 12m (p.p.)", -15.0, 15.0, 0.0, .25)
    n_assets = c4.slider("Máx. de ativos", 8, 20, 12)
    s1, s2 = st.columns(2)
    vacancy_shock = s1.slider("Choque de vacância (%)", 0.0, 20.0, 8.0, 1.0) / 100
    credit_event = s2.slider("Eventos de crédito (%)", 0.0, 10.0, 3.0, .5) / 100
    scenario = MacroScenario(selic=selic, ipca=ipca, selic_change_12m=delta,
                             vacancy_shock=vacancy_shock, credit_event_rate=credit_event)
    st.markdown(_info_card_html(
        "Regime quantitativo",
        classify_macro_regime(scenario).replace("_", " ").title(),
        accent="#4A9EFF",
    ), unsafe_allow_html=True)

    inputs = _mr.load_fii_methodology_inputs()
    validation = _mr.load_fii_validation_status()
    scored = score_fiis_by_type(inputs.to_dict("records") if not inputs.empty else [],
                                validation_status="passed" if validation.get("status") == "passed" else "unvalidated")
    result = optimize_diligence_portfolio(scored, scenario, policy=PortfolioPolicy(max_assets=n_assets))
    if not result.get("items"):
        st.error("Não foi possível construir uma carteira factível: " +
                 " · ".join(result.get("blockers") or []))
        return None
    if result.get("can_publish"):
        st.success("Carteira apta à publicação segundo os gates vigentes.")
    else:
        st.warning("Rascunho não publicável: " + " · ".join(result.get("blockers") or []))
    items = result["items"]
    show = pd.DataFrame([{
        "Ticker": item["ticker"], "Tipo": item["tipo"],
        "Segmento": item.get("sector"), "Peso v4": item["weight"],
        "Score v4": item["type_score"], "Confiança": item["confidence"],
        "Cobertura": item["coverage"], "DY 12m": item.get("dy_12m"),
        "P/VP": item.get("pvp"), "Liquidez/dia": item.get("liquidez_diaria"),
        "Status": item["publication_status"],
    } for item in items])
    table_slot = st.empty()
    _render_portfolio_table(table_slot, show)
    valid_pvp = [(float(item["pvp"]), float(item["weight"])) for item in items
                 if item.get("pvp") is not None and pd.notna(item.get("pvp"))]
    pvp_weight = sum(weight for _, weight in valid_pvp)
    weighted_pvp = (sum(value * weight for value, weight in valid_pvp) / pvp_weight
                    if pvp_weight else None)
    k1, k2, k3, k4, k5 = st.columns(5)
    k1.markdown(_kpi_html("Ativos selecionados", len(items), accent="#4A9EFF"),
                unsafe_allow_html=True)
    k2.markdown(_kpi_html("Renda esperada", f"{result['expected_yield']:.1%}"),
                unsafe_allow_html=True)
    k3.markdown(_kpi_html("P/VP ponderado",
                          f"{weighted_pvp:.2f}" if weighted_pvp is not None else "—",
                          accent="#B084F6"), unsafe_allow_html=True)
    k4.markdown(_kpi_html("Número efetivo", f"{result['effective_assets']:.1f}",
                          accent="#F6C90E"), unsafe_allow_html=True)
    k5.markdown(_kpi_html("Dimensões sem cobertura",
                          len(result.get("unresolved_dimensions") or []),
                          accent="#FC5C7D"), unsafe_allow_html=True)
    comp_left, comp_right = st.columns([2, 1])
    with comp_left:
        st.markdown(_info_card_html(
            "Cenários estruturais",
            "Sensibilidades quantitativas para comparação; não representam previsões.",
            accent="#4A9EFF",
        ), unsafe_allow_html=True)
        st.markdown(_scenario_cards_html(result["scenario_returns"]), unsafe_allow_html=True)
    with comp_right:
        st.caption("Composição por tipo (%)")
        _comp_tipo_chart(pd.DataFrame([
            {"tipo": item["tipo"], "peso": item["weight"]} for item in items
        ]))
    regime = classify_macro_regime(scenario)
    explanations = build_selection_explanations(items, scored, regime=regime)
    st.markdown("#### Por que estes FIIs avançaram para a seleção")
    st.markdown(_info_card_html(
        "Critério de comparação",
        "Comparação exclusiva com fundos do mesmo tipo. A seleção combina score (45%), "
        "confiança (30%), renda (25%), diversificação e perdas nos cenários de estresse. "
        "Os destaques são prioridades de diligência, não recomendações de compra.",
        accent="#00C896",
    ), unsafe_allow_html=True)
    explanation_cols = st.columns(2)
    for index, explanation in enumerate(explanations):
        with explanation_cols[index % 2]:
            st.markdown(_selection_card_html(explanation, expanded=index < 2),
                        unsafe_allow_html=True)
    port = [{"ticker": item["ticker"], "peso": item["weight"], "tipo": item["tipo"],
             "score": item["type_score"], "dy_12m": item.get("dy_12m"),
             "pvp": item.get("pvp"), "segmento": item.get("sector")}
            for item in items]
    st.session_state["fii_port"] = {item["ticker"]: item["weight"] for item in items}
    _render_save_portfolio(
        port, {"metodo": "fii_v4", "regime": classify_macro_regime(scenario),
               "scenario": scenario.__dict__, "policy": result.get("policy")},
        {"expected_yield": result["expected_yield"], "effective_assets": result["effective_assets"],
         "scenario_returns": result["scenario_returns"]}, key="fii_save_model_v4")
    return table_slot, show


def _render_save_portfolio(port: list[dict], params: dict, metrics: dict,
                           *, key: str) -> None:
    """Oferece a mesma persistência para qualquer método de seleção."""
    st.markdown("---")
    cs1, cs2 = st.columns([3, 1])
    with cs1:
        st.markdown(_info_card_html(
            "Publicação da carteira-modelo",
            "Ao salvar, o Dashboard Geral passa a usar exatamente estes ativos e pesos. "
            "A gravação só é liberada quando os gates de cobertura, consistência, "
            "atualização e validação forem aprovados.",
            accent="#F6C90E",
        ), unsafe_allow_html=True)
    with cs2:
        gate = st.session_state.get("fii_publication_gate")
        can_publish = bool(gate and gate.can_publish_recommendation)
        if st.button("💾 Salvar carteira-modelo", use_container_width=True,
                     type="primary", key=key, disabled=not can_publish,
                     help=None if can_publish else "Publicação bloqueada pela metodologia v4"):
            try:
                from core.fii_portfolio_model import save_fii_portfolio_model
                save_fii_portfolio_model(port, params, metrics)
                st.success("Carteira-modelo de FIIs salva e disponível no Dashboard Geral.")
            except Exception as exc:
                st.error(f"Não foi possível salvar: {exc}")


def _carteira_score(ranked: pd.DataFrame, primary_table=None) -> None:
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
    try:
        port = _fz.build_portfolio(
            rows, n_max=n_max, max_weight=max_w, max_tipo_frac=max_tp
        )
    except ValueError as exc:
        st.warning(str(exc))
        st.session_state.pop("fii_port", None)
        return
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

    complementary = pf.rename(columns={
        "ticker": "Ticker", "peso": "Peso complementar", "tipo": "Tipo",
        "segmento": "Segmento", "dy_12m": "DY 12m", "pvp": "P/VP",
        "score": "Score complementar",
    })[["Ticker", "Peso complementar", "Tipo", "Segmento", "DY 12m", "P/VP",
        "Score complementar"]]
    if primary_table:
        _render_portfolio_table(primary_table[0], primary_table[1], complementary)
    else:
        _render_portfolio_table(st.empty(), None, complementary)

    # guarda p/ o backtest
    st.session_state["fii_port"] = {p["ticker"]: p["peso"] for p in port}

    _render_save_portfolio(
        port,
        {
            "metodo": "score_padrao", "score_version": _fz.SCORE_VERSION,
            "n_max": n_max, "max_weight": max_w, "max_tipo_frac": max_tp,
        },
        {"dy_ponderado": dy_w, "pvp_ponderado": pvp_w, "n_ativos": len(port)},
        key="fii_save_model_score",
    )


def _carteira_qualidade(primary_table=None) -> None:
    st.caption("Triagem multifator retrospectiva: renda, trajetória da cota, liquidez e "
               "proxies de diversificação. Papel e FoF ampliam o mix de exposições, mas "
               "não há garantia de descorrelação. Os retornos históricos são usados para "
               "diagnóstico — não constituem validação fora da amostra.")
    q = _mr.load_fii_quality()
    if q.empty:
        st.info("Sem dados de FIIs. Rode a ingestão (`fiis`, `fiis-cvm`, `fiis-series`, "
                "`fiis-metrics`, `fiis-imoveis`).")
        return
    bricks_all = q[q["Tipo"].isin(["tijolo", "hibrido"])]
    if not bricks_all.empty:
        property_coverage = float(bricks_all["Num_Imoveis"].fillna(0).gt(0).mean())
        region_coverage = float(bricks_all["N_Regioes"].fillna(0).gt(0).mean())
        if min(property_coverage, region_coverage) < 0.80:
            st.warning(
                "Cobertura patrimonial incompleta: "
                f"{property_coverage:.0%} dos FIIs de tijolo/híbridos têm imóveis "
                f"identificados e {region_coverage:.0%} têm região identificada. "
                "Filtros patrimoniais podem excluir fundos por ausência de dado."
            )

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
    exig_inq = g3.checkbox("Mín. de 8 imóveis", value=True)
    exig_set = g4.checkbox("Multicategoria/híbrido", value=True)

    # ── filtros duros ─────────────────────────────────────────────────────────
    f = q.copy()
    f = f[f["Tipo"].isin(["tijolo", "hibrido", "papel", "fof"])]   # exige tipo definido
    f = f[f["Liquidez_Diaria"].fillna(0) >= liq_min]
    f = f[f["DY_12m"].between(max(dy_min / 100.0, 1e-12), 0.20, inclusive="both")]
    f = f[f["P/VP"].between(0.55, 1.30, inclusive="both")]
    f = f[f["Max_Drawdown"].fillna(0.0) >= -(dd_max / 100.0)]
    f = f[f["Hist_Meses"].fillna(0) >= hist_min]       # track record mínimo (credibilidade)
    if exig_pvp:
        f = f[f["P/VP"].fillna(9) < 1.0]
    # Os critérios patrimoniais só se aplicam a tijolo/híbrido. Número de imóveis
    # não mede inquilinos e a classificação multicategoria não prova diversificação
    # econômica; por isso a interface os apresenta explicitamente como proxies.
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

    # Diversificação observável para tijolo/híbrido. Sem decomposição de carteira
    # de CRIs/cotas, papel e FoF recebem valor neutro — não um bônus arbitrário.
    div_brick = (0.4 * (f["N_Regioes"].fillna(0).clip(upper=5) / 5.0)
                 + 0.3 * (f["Num_Imoveis"].fillna(0).clip(upper=40) / 40.0)
                 + 0.3 * f["Multi_Setorial"].astype(float))
    div = div_brick.where(f["Tipo"].isin(["tijolo", "hibrido"]), 0.5)
    cresc = 0.6 * _rk(f["CAGR"]) + 0.4 * _rk(f["Max_Drawdown"])  # CAGR↑ e drawdown menos negativo↑
    pvp_distance = f["P/VP"].map(
        lambda value: abs(math.log(value / 0.90)) if value and value > 0 else None
    )
    score = 100.0 * (0.35 * _rk(f["DY_12m"])      # bons dividendos (principal)
                     + 0.25 * cresc               # crescimento / baixo drawdown
                     + 0.25 * div                 # diversificação (multi-*)
                     + 0.10 * _rk(f["Liquidez_Diaria"])
                     + 0.05 * _rk(pvp_distance, higher=False))
    f = f.assign(Qualidade=score)

    rows = [{"ticker": r["Ticker"], "score": r["Qualidade"], "tipo": r["Tipo"],
             "liquidez_diaria": r["Liquidez_Diaria"], "dy_12m": r["DY_12m"],
             "pvp": r["P/VP"], "segmento": r["Segmento"]}
            for _, r in f.iterrows()]
    try:
        port = _fz.build_portfolio(
            rows, n_max=n_max, max_weight=max_w, max_tipo_frac=max_tp,
            liq_min=liq_min, min_por_tipo=min_tp,
        )
    except ValueError as exc:
        st.warning(str(exc))
        st.session_state.pop("fii_port", None)
        return
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
    r1[0].markdown(_kpi_html("CAGR médio dos fundos",
                             f"{cagr_w*100:.1f}%" if cagr_w is not None else "—",
                             accent="#00C896", sub="média ponderada; não é CAGR da carteira",
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
    r2[2].markdown(_kpi_html("Drawdown médio dos fundos", f"{dd_w*100:.0f}%",
                             accent="#F6C90E"),
                   unsafe_allow_html=True)
    r2[3].markdown(_kpi_html("Liquidez mín.", f"R$ {liq_min_port/1e6:.1f} mi/dia",
                             accent="#4A9EFF", sub="menor liquidez da carteira",
                             sub_color="#4A5568"), unsafe_allow_html=True)

    complementary = pf.rename(columns={
        "ticker": "Ticker", "peso": "Peso complementar", "tipo": "Tipo",
        "segmento": "Segmento", "Liquidez_Diaria": "Liquidez/dia", "dy_12m": "DY 12m",
        "pvp": "P/VP", "CAGR": "Cresc. a.a.", "Max_Drawdown": "Pior queda",
        "Hist_Meses": "Hist. (m)", "N_Regioes": "Regiões", "Num_Imoveis": "Imóveis",
        "score": "Score complementar",
    })[["Ticker", "Peso complementar", "Tipo", "Segmento", "Liquidez/dia", "DY 12m",
        "P/VP", "Cresc. a.a.", "Pior queda", "Hist. (m)", "Regiões", "Imóveis",
        "Score complementar"]]
    if primary_table:
        _render_portfolio_table(primary_table[0], primary_table[1], complementary)
    else:
        _render_portfolio_table(st.empty(), None, complementary)
    if n_tipos == 1:
        st.caption("⚠️ Só um tipo — reduza o 'Máx. por tipo' ou relaxe um critério "
                   "para incluir papel/FoF.")

    st.caption("Peso da qualidade: DY 35% · trajetória histórica 25% · proxies de "
               "diversificação 25% · liquidez 10% · proximidade do P/VP a 0,90 5%. "
               "Para papel/FoF, sem decomposição dos lastros, a diversidade é neutra.")
    st.session_state["fii_port"] = weights
    _render_save_portfolio(
        port,
        {
            "metodo": "qualidade_retrospectiva", "score_version": _fz.SCORE_VERSION,
            "n_max": n_max, "max_weight": max_w, "max_tipo_frac": max_tp,
            "min_por_tipo": min_tp, "liquidez_min": liq_min,
            "dy_min": dy_min / 100.0, "drawdown_max": dd_max / 100.0,
            "historico_min_meses": hist_min, "pvp_abaixo_1": exig_pvp,
            "min_2_regioes": exig_reg, "min_8_imoveis": exig_inq,
            "multicategoria_hibrido": exig_set,
        },
        {
            "dy_ponderado": dy_w, "pvp_ponderado": pvp_w,
            "cagr_medio_fundos": cagr_w, "drawdown_medio_fundos": dd_w,
            "n_ativos": len(port), "numero_efetivo": n_ef,
        },
        key="fii_save_model_quality",
    )

    # ── Carteira vs mercado + risco × nº de fundos ────────────────────────────
    precos = _mr.load_precos_mensais(tuple(sorted(weights)))
    rets = precos.pct_change(fill_method=None) if not precos.empty else pd.DataFrame()
    port_cols = [t for t in weights if t in getattr(rets, "columns", [])]
    common = rets[port_cols].dropna() if port_cols else pd.DataFrame()
    mkt = _mr.load_mercado_retorno_mensal()

    def _ann(series):
        s = series.dropna()
        if len(s) < 6:
            return None
        anos = len(s) / 12.0
        cum = float((1 + s).prod() - 1)
        return (1 + cum) ** (1 / anos) - 1 if anos > 0.5 else None

    def _vol(series):
        s = series.dropna()
        return float(s.std(ddof=0) * (12 ** 0.5)) if len(s) >= 6 else None

    ifix_vol = None
    if len(common) >= 6:
        tot = sum(weights[t] for t in port_cols) or 1.0
        port_ret = sum(common[t] * (weights[t] / tot) for t in port_cols)
        comparison = port_ret.rename("Carteira").to_frame()
        if not mkt.empty:
            comparison = comparison.join(mkt[["IFIX", "Universo"]], how="inner")
        comparison = comparison.dropna(how="any")
        ann_port = _ann(comparison["Carteira"]) if not comparison.empty else None
        ann_ifix = _ann(comparison["IFIX"]) if "IFIX" in comparison else None
        ann_uni = _ann(comparison["Universo"]) if "Universo" in comparison else None
        ifix_vol = _vol(comparison["IFIX"]) if "IFIX" in comparison else None
        alpha = (ann_port - ann_ifix) if (ann_port is not None and ann_ifix is not None) else None
        st.markdown("#### 📊 Carteira vs mercado")
        vc = st.columns(4)
        vc[0].markdown(_kpi_html("Sua carteira", f"{ann_port*100:.1f}%" if ann_port is not None else "—",
                                 accent="#00C896", sub=f"a.a. · {len(comparison)} meses",
                                 sub_color="#4A5568"), unsafe_allow_html=True)
        vc[1].markdown(_kpi_html("IFIX", f"{ann_ifix*100:.1f}%" if ann_ifix is not None else "—",
                                 accent="#9CA3AF", sub="índice de FIIs", sub_color="#4A5568"),
                       unsafe_allow_html=True)
        vc[2].markdown(_kpi_html("Mercado (mediana)", f"{ann_uni*100:.1f}%" if ann_uni is not None else "—",
                                 accent="#9CA3AF", sub="universo de FIIs", sub_color="#4A5568"),
                       unsafe_allow_html=True)
        vc[3].markdown(_kpi_html("vs IFIX", f"{alpha*100:+.1f} p.p." if alpha is not None else "—",
                                 accent="#00C896" if (alpha or 0) >= 0 else "#FC5C7D",
                                 sub="retorno acima/abaixo", sub_color="#4A5568"),
                       unsafe_allow_html=True)
        st.caption("Comparação retrospectiva das posições atuais, com todas as séries na "
                   "mesma janela. A seleção usa parte desse próprio histórico; portanto, "
                   "o resultado é diagnóstico in-sample e não evidência preditiva.")

    st.markdown("#### 📉 Risco × nº de fundos")
    curve = _fz.risk_curve(rets, weights) if not rets.empty else []
    if len(curve) >= 2:
        cdf = pd.DataFrame(curve)
        cdf["Volatilidade anual (%)"] = cdf["vol"] * 100
        import plotly.express as px
        fig = px.line(cdf, x="n", y="Volatilidade anual (%)", markers=True)
        fig.update_traces(line_color="#00C896", marker_color="#00C896")
        if ifix_vol is not None:
            fig.add_hline(y=ifix_vol * 100, line_dash="dash", line_color="#FC5C7D",
                          annotation_text=f"Vol. do mercado (IFIX) {ifix_vol*100:.1f}%",
                          annotation_position="top right", annotation_font_color="#FC5C7D")
        fig.update_layout(height=300, margin=dict(l=0, r=10, t=6, b=0),
                          xaxis=dict(title="Nº de fundos na carteira", dtick=1,
                                     tickmode="linear", gridcolor="#1E2533"),
                          yaxis=dict(title="Volatilidade anual (%)", gridcolor="#1E2533"),
                          plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
                          font_color="#CBD5E0")
        _meses_win = int(cdf["meses"].iloc[0]) if "meses" in cdf else 0
        st.caption(f"Volatilidade da carteira ao adicionar fundos (por peso), na mesma janela "
                   f"comum de {_meses_win} meses. A tracejada é a **volatilidade do mercado "
                   f"(IFIX)** — sua curva tende a ela conforme diversifica: **poucos FIIs = "
                   f"acima da linha** (risco específico extra, aposta concentrada); **fundos "
                   f"suficientes = na linha** (você replica o risco do mercado). Nº efetivo: "
                   f"**{n_ef:.1f}** de {len(port)}.")
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.caption("Sem histórico suficiente entre os fundos selecionados para traçar a curva.")

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
    st.caption("Retrospectiva buy-and-hold das posições e pesos atuais. Há viés de "
               "sobrevivência e de seleção: não é um backtest point-in-time nem uma "
               "validação fora da amostra.")
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
    st.caption("Índice base 100 na mesma janela efetivamente disponível para carteira e "
               "XFIX11. Retorno total ajustado, sem custos ou impostos. O resultado mostra "
               "como a carteira atual teria se comportado; não reproduz decisões históricas.")
