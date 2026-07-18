"""
views/empresas_americanas.py
Seção Empresas Americanas (NYSE/Nasdaq/AMEX) — inspirada em Empresas B3 / FIIs.

OFFLINE-FIRST: lê SÓ o warehouse local (core.us_data). Nunca chama API externa
(SEC EDGAR/yfinance são só de ingestão). Sem dados, mostra estado vazio e
instruções de sincronização — a UI não quebra.

Módulo alinhado ao contrato de navegação de Empresas B3: empresas por setor,
análise individual, análise avançada, criação/simulação de portfólio e avaliação
de carteira. A inteligência usa SEC/GAAP e contexto macroeconômico americano.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

import core.us_data as us
from core.market_companies import (
    filter_market_companies,
    localize_us_company_frame,
    normalize_us_companies,
    translate_us_industry,
    translate_us_sector,
)
from core.us_methodology import (
    US_ASYMMETRY_SCORE_VERSION,
    US_FUNDAMENTAL_SCORE_VERSION,
)
from design.componentes import (
    badge_status,
    card_metrica,
    estado_vazio,
    secao_titulo,
)
from design.market_companies import (
    render_company_search,
    render_market_css,
    render_market_tabs,
    render_sector_grid,
)

_PLOT_LAYOUT = dict(
    template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)", font=dict(color="#CBD5E0"),
    margin=dict(l=18, r=18, t=42, b=18),
)

_WEIGHTING_LABELS = {
    "score": "Pontuação fundamentalista",
    "equal": "Pesos iguais",
    "inverse_vol": "Inverso da volatilidade",
}


def render() -> None:
    render_market_css()
    st.markdown(
        '<div style="display:flex;align-items:center;gap:12px;margin-bottom:4px;">'
        '<span style="font-size:2rem">🌎</span>'
        '<h1 style="font-size:2rem;font-weight:800;color:#E2E8F0;margin:0;">'
        'Empresas Americanas</h1></div>'
        '<p style="font-size:0.80rem;color:#9CA3AF;margin-bottom:20px;">'
        'Análise fundamentalista de empresas listadas nos Estados Unidos e construção '
        'quantitativa de portfólios aplicáveis com dados SEC/GAAP.</p>',
        unsafe_allow_html=True,
    )

    status = us.data_status()
    active = render_market_tabs(state_key="us_active_tab", key_prefix="us")

    if active == 0:
        _tab_empresas_setor(status)
    elif active == 1:
        _tab_empresa(status)
    elif active == 2:
        _tab_avancada_unificada(status)
    elif active == 3:
        _tab_criacao_portfolio(status)
    else:
        _tab_avaliacao_portfolio(status)


def _empty_if_offline(status: dict, message: str, icon: str) -> bool:
    if status.get("offline"):
        estado_vazio(message, icon)
        return True
    return False


def _score_label(value) -> tuple[str, str]:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "Sem classificação", "neutro"
    if v >= 75:
        return "Excelente", "sucesso"
    if v >= 65:
        return "Forte", "info"
    if v >= 50:
        return "Neutra", "neutro"
    if v >= 35:
        return "Fraca", "alerta"
    return "Crítica", "erro"


def _fmt_pct(value, decimals: int = 1) -> str:
    try:
        v = float(value)
        return "—" if not np.isfinite(v) else f"{v * 100:.{decimals}f}%"
    except (TypeError, ValueError):
        return "—"


def _fmt_ratio(value, decimals: int = 1) -> str:
    try:
        v = float(value)
        return "—" if not np.isfinite(v) else f"{v:.{decimals}f}×"
    except (TypeError, ValueError):
        return "—"


def _tab_empresas_setor(status: dict) -> None:
    if _empty_if_offline(status, "Sem dados locais para listar as empresas.", "🏢"):
        _tab_sincronizacao(status)
        return
    companies = normalize_us_companies(us.companies(limit=5000))
    if companies is None or companies.empty:
        estado_vazio("Nenhuma ação americana válida encontrada na vitrine.", "🌎")
        return
    query = render_company_search(
        label="🔍 Buscar ticker (ex.: AAPL)",
        placeholder="Digite e pressione Enter", key="us_company_search",
    )
    if query:
        ticker_query = query.upper()
        if ticker_query in set(companies["ticker"]):
            st.session_state["us_selected_ticker"] = ticker_query
            st.session_state["us_active_tab"] = 1
            st.rerun()
        companies = filter_market_companies(companies, query)
    render_sector_grid(
        companies, key_prefix="us", selected_ticker=st.session_state.get("us_selected_ticker"),
        selected_state_key="us_selected_ticker", active_state_key="us_active_tab",
    )


def _company_selector(scored: pd.DataFrame, key: str) -> str | None:
    if scored is None or scored.empty:
        return None
    options = scored["symbol"].dropna().astype(str).tolist()
    names = scored.set_index("symbol").get("name", pd.Series(dtype=object)).to_dict()
    selected = st.session_state.get("us_selected_ticker")
    index = options.index(selected) if selected in options else 0
    value = st.selectbox("Empresa", options, index=index,
                         format_func=lambda x: f"{x} — {names.get(x, '')}", key=key)
    st.session_state["us_selected_ticker"] = value
    return value


def _render_score_dashboard(row: pd.Series) -> None:
    label, tipo = _score_label(row.get("score"))
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        card_metrica("Pontuação fundamentalista", f"{row.get('score', 0):.1f}/100")
        badge_status(label, tipo)
    with c2:
        card_metrica("Cobertura", f"{row.get('coverage', 0):.0f}%")
    with c3:
        card_metrica("ROIC", _fmt_pct(row.get("roic")))
    with c4:
        card_metrica("Retorno do fluxo de caixa livre", _fmt_pct(row.get("fcf_yield")))
    tracks = [(label_, float(row.get(col, 50))) for col, label_ in _TRACK_LABELS.items()]
    fig = go.Figure(go.Scatterpolar(
        r=[x[1] for x in tracks] + [tracks[0][1]],
        theta=[x[0] for x in tracks] + [tracks[0][0]], fill="toself",
        line=dict(color="#4A9EFF"), fillcolor="rgba(74,158,255,.22)",
    ))
    fig.update_layout(**_PLOT_LAYOUT, height=360,
                      polar=dict(radialaxis=dict(range=[0, 100], gridcolor="#2D3748"),
                                 bgcolor="rgba(0,0,0,0)"), showlegend=False)
    st.plotly_chart(fig, use_container_width=True, key=f"us_radar_{row.get('symbol')}")


def _tab_empresa(status: dict) -> None:
    if _empty_if_offline(status, "Sem dados locais para analisar empresas.", "🔍"):
        return
    scored = us.scored_universe()
    symbol = _company_selector(scored, "us_company_analysis")
    if not symbol:
        return
    row = localize_us_company_frame(
        scored[scored["symbol"] == symbol]
    ).iloc[0]
    secao_titulo(f"{symbol} — {row.get('name', '')}", "🔍",
                 f"{row.get('sector', '—')} / {row.get('industry', '—')}")
    _render_score_dashboard(row)

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        card_metrica("Margem operacional", _fmt_pct(row.get("operating_margin")))
    with c2:
        card_metrica("Margem líquida", _fmt_pct(row.get("net_margin")))
    with c3:
        card_metrica("Dív. líquida / EBITDA", _fmt_ratio(row.get("net_debt_ebitda")))
    with c4:
        card_metrica("Cresc. receita 3a", _fmt_pct(row.get("revenue_cagr_3y")))

    financials = us.company_financials(symbol)
    if financials is not None and not financials.empty:
        tabs = st.tabs(["Resultados", "Margens e crescimento", "Caixa e balanço"])
        with tabs[0]:
            value_cols = [c for c in ("revenue", "net_income", "ebitda") if c in financials]
            long = financials.melt("fiscal_year", value_vars=value_cols,
                                   var_name="Indicador", value_name="USD")
            long["Indicador"] = long["Indicador"].map({
                "revenue": "Receita", "net_income": "Lucro líquido", "ebitda": "EBITDA",
            }).fillna(long["Indicador"])
            fig = px.bar(
                long, x="fiscal_year", y="USD", color="Indicador", barmode="group",
                labels={"fiscal_year": "Ano fiscal", "USD": "Valor (USD)"},
            )
            fig.update_layout(**_PLOT_LAYOUT, height=390)
            st.plotly_chart(fig, use_container_width=True, key=f"us_fin_{symbol}")
        with tabs[1]:
            margin_rows = []
            for _, r in financials.iterrows():
                rev = pd.to_numeric(r.get("revenue"), errors="coerce")
                if pd.notna(rev) and rev:
                    margin_rows.append({"Ano": r["fiscal_year"],
                        "Margem líquida": pd.to_numeric(r.get("net_income"), errors="coerce") / rev,
                        "Margem EBITDA": pd.to_numeric(r.get("ebitda"), errors="coerce") / rev})
            if margin_rows:
                fig = px.line(pd.DataFrame(margin_rows).melt("Ano", var_name="Indicador",
                              value_name="Valor"), x="Ano", y="Valor", color="Indicador", markers=True)
                fig.update_layout(**_PLOT_LAYOUT, height=360, yaxis_tickformat=".1%")
                st.plotly_chart(fig, use_container_width=True, key=f"us_margin_{symbol}")
        with tabs[2]:
            value_cols = [c for c in ("free_cash_flow", "total_equity", "total_debt") if c in financials]
            long = financials.melt("fiscal_year", value_vars=value_cols,
                                   var_name="Indicador", value_name="USD")
            long["Indicador"] = long["Indicador"].map({
                "free_cash_flow": "Fluxo de caixa livre",
                "total_equity": "Patrimônio líquido", "total_debt": "Dívida total",
            }).fillna(long["Indicador"])
            fig = px.line(
                long, x="fiscal_year", y="USD", color="Indicador", markers=True,
                labels={"fiscal_year": "Ano fiscal", "USD": "Valor (USD)"},
            )
            fig.update_layout(**_PLOT_LAYOUT, height=360)
            st.plotly_chart(fig, use_container_width=True, key=f"us_cash_{symbol}")
    with st.expander("📄 Dossiê, classificação e critérios avançados"):
        _render_dossie_for(symbol)


def _render_dossie_for(symbol: str) -> None:
    d = us.dossie(symbol)
    if d.get("erro"):
        st.info(d["erro"])
        return
    label, tipo = _CLASS_BADGE.get(d.get("classification"), ("—", "neutro"))
    badge_status(label, tipo)
    st.caption(d.get("classification_reason", ""))
    for flag in d.get("red_flags", []):
        st.warning(flag)
    notes = d.get("notes", {})
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Tese / pontos fortes**")
        for item in notes.get("tese", []):
            st.markdown(f"- {item}")
    with c2:
        st.markdown("**Condições de invalidação**")
        for item in notes.get("condicoes_invalidacao", []):
            st.markdown(f"- {item}")


def _macro_controls(key_prefix: str = "us_macro") -> dict:
    from core.us_macro import USMacroSnapshot, evaluate_macro
    st.caption("Cenário macro ajustável. Os valores são premissas de simulação; "
               "a interface não consulta fontes externas em tempo real.")
    c1, c2, c3 = st.columns(3)
    with c1:
        fed = st.number_input("Juros básicos do Fed %", 0.0, 15.0, 4.25, 0.25,
                              key=f"{key_prefix}_fed")
        gdp = st.number_input("PIB real a/a %", -10.0, 15.0, 2.0, 0.1, key=f"{key_prefix}_gdp")
    with c2:
        cpi = st.number_input("Inflação ao consumidor (CPI) a/a %", -2.0, 20.0, 2.5, 0.1,
                              key=f"{key_prefix}_cpi")
        unemp = st.number_input("Desemprego %", 2.0, 20.0, 4.2, 0.1, key=f"{key_prefix}_unemp")
    with c3:
        curve = st.number_input("Curva 10Y–2Y (p.p.)", -5.0, 5.0, 0.25, 0.05,
                                key=f"{key_prefix}_curve")
        spread = st.number_input("Spread de crédito de alto rendimento %", 1.0, 20.0, 3.5, 0.1,
                                 key=f"{key_prefix}_spread")
    return evaluate_macro(USMacroSnapshot(fed, cpi, gdp, unemp, curve, spread))


def _render_macro_dashboard(key_prefix: str = "us_macro") -> dict:
    macro = _macro_controls(key_prefix)
    c1, c2 = st.columns([1, 3])
    with c1:
        card_metrica("Regime macro EUA", f"{macro['score']:.0f}/100")
        st.caption(macro["regime"])
    with c2:
        drivers = pd.DataFrame({"Fator": list(macro["drivers"]),
                                "Impacto": list(macro["drivers"].values())})
        fig = px.bar(drivers, x="Impacto", y="Fator", orientation="h",
                     color="Impacto", color_continuous_scale=["#FC5C7D", "#F6C90E", "#00C896"])
        fig.update_layout(**_PLOT_LAYOUT, height=280, coloraxis_showscale=False)
        st.plotly_chart(fig, use_container_width=True, key=f"{key_prefix}_chart")
    return macro


def _tab_avancada_unificada(status: dict) -> None:
    tabs = st.tabs(["Pontuação e classificação", "Comparação", "Indicadores avançados",
                    "Teste histórico", "Cenário dos EUA", "Dados e metodologia"])
    with tabs[0]:
        _tab_analise_fundamentalista(status)
    with tabs[1]:
        _tab_comparacao_empresas(status)
    with tabs[2]:
        _tab_analise_avancada(status)
    with tabs[3]:
        _tab_backtests(status)
    with tabs[4]:
        secao_titulo("Ambiente macroeconômico americano", "🏛️")
        _render_macro_dashboard("us_macro_adv")
        st.info("Leitura setorial: juros afetam empresas de crescimento e fundos "
                "imobiliários americanos (REITs); a inclinação da curva e os spreads "
                "afetam bancos e crédito; emprego e PIB afetam "
                "consumo cíclico; inflação altera margens e poder de preço.")
    with tabs[5]:
        sub = st.tabs(["Qualidade", "Sincronização", "Metodologia"])
        with sub[0]:
            _tab_qualidade()
        with sub[1]:
            _tab_sincronizacao(status)
        with sub[2]:
            _tab_metodologia()


def _tab_comparacao_empresas(status: dict) -> None:
    if _empty_if_offline(status, "Sem dados locais para comparar.", "⚖️"):
        return
    scored = us.scored_universe()
    if scored is None or scored.empty:
        return
    options = scored["symbol"].astype(str).tolist()
    selected = st.multiselect("Selecione de 2 a 6 empresas", options,
                              default=options[:min(3, len(options))], max_selections=6,
                              key="us_compare_symbols")
    if len(selected) < 2:
        st.info("Selecione ao menos duas empresas.")
        return
    peers = localize_us_company_frame(scored[scored["symbol"].isin(selected)])
    table_cols = [c for c in ("symbol", "name", "sector", "industry", "score",
        "gross_margin", "operating_margin", "roic", "revenue_cagr_3y",
        "net_debt_ebitda", "pe", "ev_ebitda", "fcf_yield", "shareholder_yield") if c in peers]
    st.dataframe(peers[table_cols].rename(columns={
        "symbol": "Ticker", "name": "Nome", "sector": "Setor", "industry": "Indústria",
        "score": "Pontuação", "gross_margin": "Margem bruta", "operating_margin": "Margem op.",
        "roic": "ROIC", "revenue_cagr_3y": "Cresc. receita 3a", "net_debt_ebitda": "DL/EBITDA",
        "pe": "P/L", "ev_ebitda": "EV/EBITDA", "fcf_yield": "Retorno do FCL",
        "shareholder_yield": "Retorno ao acionista"}), hide_index=True, use_container_width=True)
    tracks = peers[["symbol", *_TRACK_LABELS]].melt(
        "symbol", var_name="Trilha", value_name="Pontuação")
    tracks["Trilha"] = tracks["Trilha"].map(_TRACK_LABELS)
    fig = px.bar(
        tracks, x="Trilha", y="Pontuação", color="symbol", barmode="group",
        labels={"symbol": "Ticker"},
    )
    fig.update_layout(**_PLOT_LAYOUT, height=420, yaxis_range=[0, 100])
    st.plotly_chart(fig, use_container_width=True, key="us_compare_tracks")
    with st.expander("Comparação por indústria"):
        _tab_comparacao_industria(status)


# ── Análise Fundamentalista (score por setor/indústria) ───────────────────────
_TRACK_LABELS = {
    "score_quality": "Qualidade", "score_growth": "Crescimento",
    "score_solidity": "Solidez", "score_capital_efficiency": "Efic. Capital",
    "score_valuation": "Avaliação", "score_shareholder": "Retorno ao acionista",
}


def _tab_analise_fundamentalista(status: dict) -> None:
    if status.get("offline"):
        estado_vazio("Sem dados locais para calcular a pontuação.", "📊")
        return
    scored = us.scored_universe()
    if scored is None or scored.empty:
        estado_vazio("Sem empresas com demonstrações suficientes para a pontuação.", "📊")
        return
    secao_titulo("Pontuação fundamentalista — relativa por indústria", "🏆")
    st.caption("Winsorização + percentil intra-indústria nas 6 trilhas de fatores. "
               "Ausência = neutro. A pontuação não é garantia de retorno.")
    scored = localize_us_company_frame(scored)
    setores = ["(todos)"] + sorted(x for x in scored["sector"].dropna().unique())
    sel = st.selectbox("Setor", setores, key="us_score_sector")
    view = scored if sel == "(todos)" else scored[scored["sector"] == sel]
    cols = ["symbol", "name", "sector", "industry", "score",
            *_TRACK_LABELS.keys(), "coverage"]
    cols = [c for c in cols if c in view.columns]
    show = view[cols].head(200).rename(columns={
        "symbol": "Ticker", "name": "Nome", "sector": "Setor",
        "industry": "Indústria", "score": "Pontuação", "coverage": "Cobertura %",
        **_TRACK_LABELS})
    st.dataframe(show, hide_index=True, use_container_width=True)


# ── Análise Avançada (Piotroski / Altman / Sloan / ROIC incremental) ──────────
_F_LABEL = {
    "roa_positivo": "ROA positivo", "cfo_positivo": "Caixa operacional positivo",
    "roa_crescente": "ROA crescente", "accruals_saudaveis": "CFO > lucro (accruals)",
    "alavancagem_caiu": "Alavancagem de longo prazo caiu",
    "liquidez_subiu": "Liquidez corrente subiu",
    "sem_emissao_acoes": "Sem emissão de ações",
    "margem_bruta_subiu": "Margem bruta subiu", "giro_ativos_subiu": "Giro de ativos subiu",
}
_ZONE_TIPO = {"segura": "sucesso", "cinzenta": "alerta", "aflição": "erro"}


def _tab_analise_avancada(status: dict) -> None:
    if status.get("offline"):
        estado_vazio("Sem dados locais para a análise avançada.", "🔬")
        return
    symbol = st.text_input("Ticker (ex.: AAPL)", key="us_adv_symbol").strip().upper()
    if not symbol:
        st.info("Digite um ticker para os indicadores de Piotroski, Altman, ajustes "
                "contábeis de Sloan e retorno incremental sobre capital.")
        return
    snap = us.advanced_snapshot(symbol)
    if not snap:
        estado_vazio(f"Sem histórico local para {symbol}.", "🔬")
        return

    secao_titulo(
        f"{symbol} — {snap.get('name') or ''}", "🔬",
        translate_us_sector(snap.get("sector"), snap.get("industry")),
    )
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        f = snap.get("f_score")
        card_metrica("Piotroski F-Score",
                     "—" if f is None else f"{f}/{snap.get('f_evaluable', 9)}",
                     ajuda="Critérios atendidos entre os avaliáveis (máx. 9)")
    with c2:
        z = snap.get("z_score")
        card_metrica("Altman Z-Score", "—" if z is None else f"{z:.2f}")
    with c3:
        a = snap.get("sloan_accruals")
        card_metrica("Ajustes contábeis (Sloan)", "—" if a is None else f"{a:.3f}",
                     ajuda="(Lucro − CFO)/ativos médios. Menor é melhor.")
    with c4:
        ir = snap.get("incremental_roic")
        card_metrica("ROIC incremental", "—" if ir is None else f"{ir*100:.1f}%",
                     ajuda="ΔNOPAT / Δcapital investido — retorno do capital novo")

    if snap.get("z_zone"):
        badge_status(f"Zona {snap['z_zone']}", _ZONE_TIPO.get(snap["z_zone"], "neutro"))
    elif snap.get("z_score") is None:
        st.caption("Indicador Z indisponível: exige lucros acumulados e valor de mercado "
                   "na base de dados (atualize os fundamentos após a migração 043).")

    if snap.get("f_partial"):
        st.caption(f"⚠️ F-Score parcial: {snap.get('f_evaluable')} de 9 critérios "
                   "puderam ser avaliados. Critérios sem dado **não** contam como "
                   "atendidos.")

    secao_titulo("Critérios de Piotroski", "✅")
    for key, val in (snap.get("f_signals") or {}).items():
        icon = "✅" if val is True else "❌" if val is False else "➖"
        st.markdown(f"{icon} {_F_LABEL.get(key, key)}"
                    + ("  *(sem dado)*" if val is None else ""))


# ── Comparação por Indústria ──────────────────────────────────────────────────
def _tab_comparacao_industria(status: dict) -> None:
    if status.get("offline"):
        estado_vazio("Sem dados locais para comparar.", "🏭")
        return
    scored = us.scored_universe()
    if scored is None or scored.empty or "industry" not in scored.columns:
        estado_vazio("Sem empresas suficientes para comparação por indústria.", "🏭")
        return
    industrias = sorted(x for x in scored["industry"].dropna().unique())
    if not industrias:
        estado_vazio("Nenhuma indústria classificada nos dados locais.", "🏭")
        return
    ind = st.selectbox(
        "Indústria", industrias, key="us_cmp_industry",
        format_func=translate_us_industry,
    )
    import core.us_score as _score
    peers = _score.industry_comparison(scored, ind)
    if peers.empty:
        estado_vazio("Sem pares nesta indústria.", "🏭")
        return
    secao_titulo(f"{translate_us_industry(ind)} — {len(peers)} empresa(s)", "🏭")
    show_cols = ["symbol", "name", "score", "score_quality", "score_growth",
                 "score_valuation", "gross_margin", "roic", "net_debt_ebitda",
                 "revenue_cagr_3y"]
    show_cols = [c for c in show_cols if c in peers.columns]
    st.dataframe(peers[show_cols].rename(columns={
        "symbol": "Ticker", "name": "Nome", "score": "Pontuação",
        "score_quality": "Qualidade", "score_growth": "Crescimento",
        "score_valuation": "Avaliação", "gross_margin": "Margem bruta",
        "roic": "ROIC", "net_debt_ebitda": "DL/EBITDA",
        "revenue_cagr_3y": "Cresc.Rec 3a"}),
        hide_index=True, use_container_width=True)


# ── Dossiê determinístico ─────────────────────────────────────────────────────
_CLASS_BADGE = {
    "consolidada": ("Consolidada", "sucesso"), "crescimento": ("Crescimento", "info"),
    "assimetrica": ("Assimétrica", "alerta"), "turnaround": ("Recuperação", "alerta"),
    "ciclica": ("Cíclica", "neutro"), "inadequada": ("Inadequada", "erro"),
}


def _tab_dossie(status: dict) -> None:
    if status.get("offline"):
        estado_vazio("Sem dados locais para montar o dossiê.", "📄")
        return
    symbol = st.text_input("Ticker (ex.: AAPL)", key="us_dossie_symbol").strip().upper()
    if not symbol:
        st.info("Digite um ticker para o dossiê determinístico (offline).")
        return
    d = us.dossie(symbol)
    if d.get("erro"):
        estado_vazio(f"{symbol}: {d['erro']}", "📄")
        return

    label, tipo = _CLASS_BADGE.get(d.get("classification"), ("—", "neutro"))
    secao_titulo(
        f"{symbol} — {d.get('name') or ''}", "📄",
        f"{translate_us_sector(d.get('sector'), d.get('industry'))} / "
        f"{translate_us_industry(d.get('industry') or d.get('sector'))}",
    )
    cb1, cb2, *_ = st.columns([1, 1, 4])
    with cb1:
        badge_status(label, tipo)
    with cb2:
        if d.get("score") is not None:
            badge_status(f"Pontuação {d['score']}", "info")
    st.caption(d.get("classification_reason", ""))

    m = d.get("metrics", {})

    def _p(x):
        return "—" if x is None else f"{x*100:.1f}%"

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        card_metrica("Margem líquida", _p(m.get("net_margin")))
    with c2:
        card_metrica("ROIC", _p(m.get("roic")))
    with c3:
        v = m.get("net_debt_ebitda")
        card_metrica("Dív.líq/EBITDA", "—" if v is None else f"{v:.1f}×")
    with c4:
        card_metrica("Cresc. receita 3a", _p(m.get("revenue_cagr_3y")))

    if d.get("red_flags"):
        secao_titulo("Sinais de alerta", "🚩")
        for f in d["red_flags"]:
            st.markdown(f"- {f}")

    notes = d.get("notes", {})
    if notes.get("tese") or notes.get("condicoes_invalidacao"):
        colt, coli = st.columns(2)
        with colt:
            st.markdown("**Tese**")
            for t in notes.get("tese", []):
                st.markdown(f"- {t}")
        with coli:
            st.markdown("**Condições de invalidação**")
            for c in notes.get("condicoes_invalidacao", []):
                st.markdown(f"- {c}")

    with st.expander("Dossiê completo (texto determinístico)"):
        import core.us_dossie as _dos
        st.code(_dos.dossie_to_text(d), language="text")


def _portfolio_controls(scored: pd.DataFrame, prefix: str):
    from core.us_portfolio import PortfolioConstraints, build_portfolio
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        top_n = st.slider("Nº de ativos", 5, 40, 20, key=f"{prefix}_topn")
    with c2:
        maxw = st.slider("Peso máx/ativo %", 5, 40, 10, key=f"{prefix}_maxw") / 100
    with c3:
        maxs = st.slider("Peso máx/setor %", 15, 60, 30, key=f"{prefix}_maxs") / 100
    with c4:
        mode = st.selectbox("Ponderação", ["score", "equal", "inverse_vol"],
                            format_func=lambda value: _WEIGHTING_LABELS[value],
                            key=f"{prefix}_mode")
    constraints = PortfolioConstraints(top_n=top_n, max_weight=maxw,
        max_sector_weight=maxs, weighting=mode, max_assets=top_n, min_assets=min(5, top_n))
    return build_portfolio(scored, constraints), constraints


def _tab_criacao_portfolio(status: dict) -> None:
    if _empty_if_offline(status, "Sem dados locais para montar a carteira.", "🚀"):
        return
    scored = us.scored_universe()
    if scored is None or scored.empty:
        estado_vazio("Sem empresas com pontuação elegível.", "🚀")
        return
    secao_titulo("Criação de Portfólio", "🚀")
    st.caption("Seleção por pontuação e diversificação, adaptada a indústrias e setores "
               "americanos. Pesos respeitam tetos por ativo e setor.")
    with st.expander("⚙️ Parâmetros", expanded=True):
        holdings, constraints = _portfolio_controls(scored, "us_create")
        capital = st.number_input("Capital para simulação (USD)", min_value=100.0,
                                  value=10000.0, step=500.0, key="us_create_capital")
    if holdings.empty:
        estado_vazio("Nenhum ativo elegível com as restrições atuais.", "🚀")
        return
    holdings = localize_us_company_frame(holdings)
    holdings["allocation_usd"] = holdings["weight"] * capital
    holdings["weight_pct"] = holdings["weight"] * 100
    if st.button("🚀 Rodar Criação de Portfólio", type="primary", key="us_create_run"):
        st.session_state["us_portfolio_model"] = holdings
        st.success("Carteira-modelo criada e disponível em Avaliação de Portfólio.")
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        card_metrica("Ativos", str(len(holdings)))
    with c2:
        card_metrica("Pontuação média", f"{np.average(holdings['score'], weights=holdings['weight']):.1f}")
    with c3:
        card_metrica("Maior posição", f"{holdings['weight_pct'].max():.1f}%")
    with c4:
        card_metrica("Capital simulado", f"US$ {capital:,.2f}")
    show = holdings[[c for c in ("symbol", "name", "sector", "industry", "score",
                                 "weight_pct", "allocation_usd") if c in holdings]].rename(columns={
        "symbol": "Ticker", "name": "Nome", "sector": "Setor", "industry": "Indústria",
        "score": "Pontuação", "weight_pct": "Peso %", "allocation_usd": "Alocação em USD"})
    st.dataframe(show, hide_index=True, use_container_width=True)
    c1, c2 = st.columns(2)
    with c1:
        fig = px.pie(
            holdings, names="symbol", values="weight", hole=.48,
            title="Alocação por ativo", labels={"symbol": "Ticker", "weight": "Peso"},
        )
        fig.update_layout(**_PLOT_LAYOUT, height=400)
        st.plotly_chart(fig, use_container_width=True, key="us_create_pie")
    with c2:
        sector = holdings.groupby("sector", dropna=False)["weight"].sum().reset_index()
        fig = px.bar(
            sector, x="sector", y="weight", title="Alocação por setor",
            labels={"sector": "Setor", "weight": "Peso"},
        )
        fig.update_layout(**_PLOT_LAYOUT, height=400, yaxis_tickformat=".0%")
        st.plotly_chart(fig, use_container_width=True, key="us_create_sector")
    with st.expander("🧪 Teste histórico sem viés temporal"):
        _tab_backtests(status)


def _tab_avaliacao_portfolio(status: dict) -> None:
    if _empty_if_offline(status, "Sem dados locais para avaliar a carteira.", "🧠"):
        return
    from core.us_portfolio_analysis import evaluate_portfolio
    scored = us.scored_universe()
    if scored is None or scored.empty:
        return
    secao_titulo("Avaliação de Portfólio", "🧠")
    mode = st.radio("Origem da carteira", ["Carteira-modelo criada", "Carteira personalizada"],
                    horizontal=True, key="us_eval_mode")
    if mode == "Carteira-modelo criada" and "us_portfolio_model" in st.session_state:
        base = st.session_state["us_portfolio_model"][["symbol", "weight"]].copy()
        base["weight"] *= 100
    else:
        options = scored["symbol"].astype(str).tolist()
        default = options[:min(8, len(options))]
        selected = st.multiselect("Ativos da carteira", options, default=default,
                                  key="us_eval_assets")
        if not selected:
            st.info("Selecione os ativos a avaliar.")
            return
        base = pd.DataFrame({"symbol": selected, "weight": 100 / len(selected)})
    edited = st.data_editor(base, hide_index=True, use_container_width=True,
                            column_config={"symbol": st.column_config.TextColumn("Ticker", disabled=True),
                                           "weight": st.column_config.NumberColumn("Peso %", min_value=0.0)},
                            key="us_eval_editor")
    with st.expander("🏛️ Cenário macroeconômico (Fed / inflação / atividade)"):
        macro = _render_macro_dashboard("us_macro_eval")
    result = evaluate_portfolio(edited, scored, macro)
    if not result.get("ok"):
        st.warning(result.get("reason", "Não foi possível avaliar a carteira."))
        return
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        card_metrica("Pontuação da carteira", f"{result['adjusted_score']:.1f}/100",
                     delta=f"Macro {result['macro_adjustment']:+.1f}")
    with c2:
        card_metrica("Diversificação", f"{result['diversification_score']:.0f}/100")
    with c3:
        card_metrica("Ativos efetivos", f"{result['effective_assets']:.1f}",
                     ajuda="Inverso do HHI; captura concentração real dos pesos")
    with c4:
        card_metrica("Cobertura avaliada", f"{result['coverage_weight']:.1f}%")
    label, tipo = _score_label(result["adjusted_score"])
    badge_status(f"Classificação: {label}", tipo)
    for alert in result["alerts"]:
        st.warning(alert)
    if result["missing"]:
        st.info("Sem pontuação: " + ", ".join(result["missing"]))
    c1, c2 = st.columns(2)
    with c1:
        tracks = pd.DataFrame({"Trilha": [_TRACK_LABELS.get(k, k) for k in result["track_scores"]],
                               "Pontuação": list(result["track_scores"].values())})
        fig = px.bar(tracks, x="Trilha", y="Pontuação", color="Pontuação",
                     color_continuous_scale=["#FC5C7D", "#F6C90E", "#00C896"])
        fig.update_layout(**_PLOT_LAYOUT, height=380, yaxis_range=[0, 100], coloraxis_showscale=False)
        st.plotly_chart(fig, use_container_width=True, key="us_eval_tracks")
    with c2:
        sectors = result["sector_weights"].rename("weight").reset_index()
        sectors.columns = ["Setor", "Peso"]
        sectors["Setor"] = sectors["Setor"].map(translate_us_sector)
        sectors = sectors.groupby("Setor", as_index=False)["Peso"].sum()
        fig = px.pie(sectors, names="Setor", values="Peso", hole=.45,
                     title="Exposição setorial")
        fig.update_layout(**_PLOT_LAYOUT, height=380)
        st.plotly_chart(fig, use_container_width=True, key="us_eval_sectors")
    positions = localize_us_company_frame(result["positions"])
    positions["weight"] *= 100
    st.dataframe(positions.rename(columns={
        "symbol": "Ticker", "name": "Nome", "sector": "Setor", "weight": "Peso %",
        "score": "Pontuação", "classification": "Classificação", "action": "Ação sugerida",
        "vs_universe_median": "vs. mediana"}), hide_index=True, use_container_width=True)


# ── Criação de Portfólio ──────────────────────────────────────────────────────
def _tab_portfolio(status: dict) -> None:
    if status.get("offline"):
        estado_vazio("Sem dados locais para montar a carteira.", "📦")
        return
    scored = us.scored_universe()
    if scored is None or scored.empty:
        estado_vazio("Sem empresas com pontuação para compor a carteira.", "📦")
        return
    from core.us_portfolio import PortfolioConstraints, build_portfolio

    secao_titulo("Carteira-modelo americana", "📦")
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        top_n = st.slider("Nº de ativos", 5, 40, 20, key="us_pf_topn")
    with c2:
        maxw = st.slider("Peso máx/ativo %", 5, 50, 10, key="us_pf_maxw") / 100
    with c3:
        maxs = st.slider("Peso máx/setor %", 15, 60, 30, key="us_pf_maxs") / 100
    with c4:
        wmode = st.selectbox(
            "Ponderação", ["score", "equal"],
            format_func=lambda value: _WEIGHTING_LABELS[value], key="us_pf_wmode",
        )

    holdings = build_portfolio(scored, PortfolioConstraints(
        top_n=top_n, max_weight=maxw, max_sector_weight=maxs, weighting=wmode,
        max_assets=top_n, min_assets=min(5, top_n)))
    if holdings.empty:
        estado_vazio("Nenhum ativo elegível com as restrições atuais.", "📦")
        return

    holdings = localize_us_company_frame(holdings)
    show = holdings.copy()
    show["weight"] = (show["weight"] * 100).round(2)
    st.dataframe(show.rename(columns={
        "symbol": "Ticker", "name": "Nome", "sector": "Setor",
        "industry": "Indústria", "score": "Pontuação", "weight": "Peso %"}),
        hide_index=True, use_container_width=True)

    if "sector" in holdings.columns:
        secao_titulo("Alocação por setor", "🧩")
        alloc = (holdings.groupby("sector")["weight"].sum() * 100).round(1) \
            .sort_values(ascending=False)
        st.dataframe(alloc.rename("Peso %").reset_index().rename(
            columns={"sector": "Setor"}), hide_index=True, use_container_width=True)
    st.caption("Limites iterativos por posição e setor (heurística de projeção, não "
               "otimizador de média-variância). Índices de referência (S&P 500, Nasdaq-100 "
               "e Russell 2000) e a carteira de pesos iguais entram no teste histórico "
               "quando houver dados suficientes.")


# ── Testes históricos sem viés temporal ───────────────────────────────────────
def _tab_backtests(status: dict) -> None:
    secao_titulo("Teste histórico com janela móvel — ponto no tempo", "🧪")
    st.caption("Pontuações recalculadas em cada data usando apenas informações já "
               "disponíveis, evitando antecipação indevida. Requer histórico PIT: `python run_us_ingest.py "
               "score-history --warehouse`.")
    c1, c2 = st.columns(2)
    with c1:
        top_n = st.slider("Top N por período", 5, 40, 20, key="us_bt_topn")
    with c2:
        wmode = st.selectbox(
            "Ponderação", ["score", "equal"],
            format_func=lambda value: _WEIGHTING_LABELS[value], key="us_bt_wmode",
        )

    res = us.backtest(top_n=top_n, weighting=wmode)
    if not res.get("ok"):
        estado_vazio(res.get("reason", "teste histórico indisponível"), "🧪")
        return

    ic = res["rank_ic"]
    p = res["portfolio"]

    def _p(x, mult=100, suf="%"):
        return "—" if x is None else f"{x*mult:.2f}{suf}"

    secao_titulo(f"Correlação entre classificação e retorno ({res['n_periods']} períodos)", "📐")
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        card_metrica("Correlação média", "—" if ic["mean"] is None else f"{ic['mean']:.3f}")
    with c2:
        card_metrica("Estatística t", "—" if ic["t_stat"] is None else f"{ic['t_stat']:.2f}")
    with c3:
        card_metrica("p-valor", "—" if ic["p_value"] is None else f"{ic['p_value']:.3f}")
    with c4:
        card_metrica("Taxa de acerto", _p(ic["hit_rate"]))

    secao_titulo("Desempenho da carteira versus pesos iguais", "📈")
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        card_metrica("Retorno anual.", _p(p["ann_return"]))
    with c2:
        card_metrica("Excesso sobre pesos iguais", _p(res.get("excess_ann_vs_ew")))
    with c3:
        card_metrica("Sharpe", "—" if p["sharpe"] is None else f"{p['sharpe']:.2f}")
    with c4:
        card_metrica("Queda máxima", _p(p["max_drawdown"]))
    c5, c6, c7, c8 = st.columns(4)
    with c5:
        card_metrica("Sortino", "—" if p["sortino"] is None else f"{p['sortino']:.2f}")
    with c6:
        card_metrica("Calmar", "—" if p["calmar"] is None else f"{p['calmar']:.2f}")
    with c7:
        card_metrica("Volatilidade", _p(p["volatility"]))
    with c8:
        card_metrica("Giro médio da carteira", _p(res.get("avg_turnover")))

    if res.get("equity_curve"):
        secao_titulo("Curva de capital", "📉")
        curve = pd.DataFrame({"Curva": res["equity_curve"]},
                             index=res.get("dates"))
        st.line_chart(curve)


# ── Qualidade dos Dados ───────────────────────────────────────────────────────
def _tab_qualidade() -> None:
    if not us.schema_ready():
        estado_vazio("Estrutura de dados `market_us` ainda não aplicada.", "🔌")
        return
    secao_titulo("Auditoria de qualidade", "🩺")
    df = us.quality_audit(limit=200)
    if df is None or df.empty:
        st.info("Nenhum registro de auditoria ainda. Rode `python run_us_ingest.py "
                "validate --warehouse` após a carga.")
        return
    show = df.rename(columns={
        "created_at": "Data", "symbol": "Ticker", "table_name": "Tabela",
        "check_name": "Verificação", "severity": "Severidade",
        "passed": "Aprovado", "detail": "Detalhes",
    })
    if "Severidade" in show:
        show["Severidade"] = show["Severidade"].replace({
            "info": "Informação", "warning": "Aviso", "error": "Erro", "critical": "Crítica",
        })
    if "Aprovado" in show:
        show["Aprovado"] = show["Aprovado"].map(
            lambda value: "Sim" if isinstance(value, (bool, np.bool_)) and bool(value)
            else "Não" if isinstance(value, (bool, np.bool_)) else value
        )
    st.dataframe(show, hide_index=True, use_container_width=True)


# ── Sincronização ─────────────────────────────────────────────────────────────
def _tab_sincronizacao(status: dict) -> None:
    secao_titulo("Sincronização de Dados Americanos", "🔄")
    st.markdown(
        "A ingestão roda **fora da interface**, por linha de comando, gravando no "
        "**armazém de dados local** (Postgres em `127.0.0.1:5433`). Fonte padrão: "
        "**SEC EDGAR** (fundamentos, gratuita — exige `SEC_USER_AGENT` no `.env` "
        "com nome e e-mail) + **yfinance** (preços). Credenciais/identificação são "
        "usadas **apenas** pela CLI — nunca pela interface.")

    runs = us.ingestion_runs()
    if runs is not None and not runs.empty:
        st.markdown("**Execuções recentes**")
        show_runs = runs.rename(columns={
            "run_key": "Execução", "domain": "Domínio", "status": "Situação",
            "calls_made": "Chamadas realizadas", "rows_written": "Linhas gravadas",
            "started_at": "Início", "finished_at": "Término",
        })
        if "Situação" in show_runs:
            show_runs["Situação"] = show_runs["Situação"].replace({
                "running": "Em execução", "success": "Concluída", "completed": "Concluída",
                "failed": "Falhou", "error": "Erro", "partial": "Parcial",
            })
        if "Domínio" in show_runs:
            show_runs["Domínio"] = show_runs["Domínio"].replace({
                "universe": "Universo", "fundamentals": "Fundamentos", "prices": "Preços",
                "bootstrap": "Carga inicial", "daily": "Atualização diária",
                "snapshot": "Vitrine", "validation": "Validação",
            })
        st.dataframe(show_runs, hide_index=True, use_container_width=True)
    else:
        st.caption("Nenhuma execução de ingestão registrada ainda.")

    st.markdown("**Comandos** (rodar no terminal, na raiz do projeto):")
    st.code(
        "# 1) aplicar a estrutura local (idempotente)\n"
        "python run_us_ingest.py init-schema --warehouse\n\n"
        "# 2) testar chave + conexão\n"
        "python run_us_ingest.py test --warehouse --json\n\n"
        "# 3) estimar a carga ANTES de baixar (simulação, sem rede)\n"
        "python run_us_ingest.py estimate --tickers AAPL MSFT NVDA\n\n"
        "# 4) carregar o cadastro do universo (NYSE/Nasdaq/AMEX)\n"
        "python run_us_ingest.py universe --warehouse --limit 200\n\n"
        "# 5) carga histórica de um lote pequeno\n"
        "python run_us_ingest.py bootstrap --warehouse --tickers AAPL MSFT --years 20 --json\n\n"
        "# 6) retomar após falha / atualizar\n"
        "python run_us_ingest.py resume --warehouse\n"
        "python run_us_ingest.py daily  --warehouse --tickers AAPL MSFT\n\n"
        "# 7) auditar qualidade\n"
        "python run_us_ingest.py validate --warehouse --json",
        language="bash")

    st.markdown("**Publicar a vitrine** (para o deploy no Streamlit Cloud mostrar "
                "os dados). Só a vitrine compacta vai para o Supabase; os "
                "históricos pesados ficam no armazém de dados local.")
    st.code(
        "# 8) construir a vitrine no armazém de dados local (sem rede)\n"
        "python run_us_ingest.py snapshot --warehouse --json\n\n"
        "# 9) publicar a vitrine no Supabase (conexão direta, não o pooler)\n"
        "python scripts/publish_us_snapshot.py `\n"
        '  --source-url "postgresql://postgres:<senha>@127.0.0.1:5433/postgres" `\n'
        '  --target-url "<SUPABASE_UNIFICADO_URL>" --dry-run   # confira, depois sem --dry-run',
        language="powershell")
    st.caption("Testes históricos e a sincronização rodam **só localmente** — a vitrine "
               "traz pontuação, dossiê, assimetria e análise avançada já calculados, "
               "mas o teste sem viés temporal precisa do histórico completo no armazém local.")

    st.markdown("**Ou rodar a aplicação localmente** contra o armazém de dados (mostra tudo, "
                "sem publicar):")
    st.code(
        '$env:SUPABASE_UNIFICADO_URL = "postgresql://postgres:<senha>@127.0.0.1:5433/postgres"\n'
        "streamlit run app.py",
        language="powershell")
    if not status.get("schema_ready"):
        if status.get("db_is_local"):
            st.warning("A estrutura `market_us` ainda não existe no armazém local. Rode o passo 1.")
        else:
            st.info("Este deploy lê o Supabase e **ainda não tem a vitrine publicada**. "
                    "Rode os passos 8–9 na sua máquina para popular a nuvem, ou rode "
                    "a aplicação localmente (regra do projeto: o histórico pesado fica "
                    "exclusivamente no armazenamento local; só a vitrine compacta vai ao Supabase).",
                    icon="☁️")


# ── Metodologia ───────────────────────────────────────────────────────────────
def _tab_metodologia() -> None:
    secao_titulo("Metodologia — Empresas Americanas", "📚")
    st.markdown(f"""
**Fonte e armazenamento.** Fundamentos: **SEC EDGAR** (relatórios 10-K em XBRL —
dados públicos e de domínio público; a data de protocolo vira `available_at`, o
padrão-ouro para análises em cada ponto do tempo). Preços: **yfinance**. Ambas acessadas só na
ingestão. Todo histórico vive no **armazém de dados local** (`market_us.*`), isolado do
B3/FII. A interface prioriza a operação local: lê o banco e funciona sem rede
após a carga.

**Identidade.** A empresa é identificada por **CIK** (não pelo ticker, que é
reutilizado/renomeado). O histórico de símbolos fica em `market_us.ticker_aliases`;
o histórico de uma empresa **não é apagado** ao trocar de ticker.

**Ponto no tempo.** Cada fato financeiro guarda `reference_date` (fim do período),
`published_date` (protocolo) e `available_at` (quando era conhecível). Os testes
históricos filtram por `available_at` — nunca por data de ingestão — para evitar
antecipação indevida. Empresas **deslistadas** permanecem no universo histórico,
evitando o viés de sobrevivência.

**Normalização.** Ausência nunca vira zero; unidades e períodos (anual/trimestral/
TTM) são rotulados explicitamente; divergência entre ticker solicitado e retornado
é rejeitada, não gravada sob o símbolo errado.

**Pontuação.** Fundamentalista v{US_FUNDAMENTAL_SCORE_VERSION}, relativa por
setor/indústria, versionada por ponto no tempo em `market_us.score_vintages`. **Não** é
garantia de retorno.

**Macro EUA.** O cenário separa a pontuação fundamentalista do ajuste de regime e
considera juros básicos do Fed, inflação ao consumidor (CPI), PIB real, desemprego,
curva dos títulos do Tesouro de 10 e 2 anos e spread de crédito de alto rendimento.
As fontes oficiais recomendadas para o processo de dados são Federal Reserve/FRED,
BLS e BEA. Valores digitados na interface são premissas de
simulação, nunca apresentados como cotações oficiais em tempo real.

**Regulação, contabilidade e tributação.** A interpretação segue US GAAP e as
divulgações à SEC, incluindo relevância de recompras, remuneração baseada em ações,
diluição, redução ao valor recuperável e ativos intangíveis. Fundos imobiliários
americanos (REITs) e instituições financeiras usam pesos setoriais próprios.
Retenção de imposto na fonte, residência fiscal, imposto sucessório e tratados
dependem do investidor e não alteram automaticamente a pontuação da companhia. Risco
político, fiscal, antitruste e mudanças regulatórias entram como contexto de
cenário, sem conclusão jurídica ou tributária individual.

**Escopo desta seção.** Aqui mora a análise fundamentalista e a carteira-modelo:
empresas avaliadas pelo que já entregam (qualidade, crescimento, solidez,
eficiência de capital, avaliação e retorno ao acionista). A trilha de **retorno
assimétrico** (pontuação v{US_ASYMMETRY_SCORE_VERSION}) vive numa **seção própria no
menu — "Empresas Fora da Curva"** — porque tem propósito, tolerância a erro e
tamanho de posição diferentes. Não misture as duas leituras.

> ℹ️ A fonte padrão é a SEC EDGAR porque os dados são de **domínio público** —
> sem licença restritiva sobre o armazenamento local. Os Termos da FMP (fonte
> opcional, desativada por padrão) proíbem cópia/armazenamento sem autorização
> escrita e exigem apagar os dados ao encerrar a assinatura; só a use com
> licença compatível.
""")
