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

import html

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
    us_logo_url,
)
from core.us_company_analysis import (
    annual_dividends,
    annual_price_returns,
    derive_metric_history,
    last_value,
    numeric_financials,
    regression_cagr,
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

_COR_POS = "#00C896"
_COR_NEG = "#FF5C7C"
_COR_INFO = "#4A9EFF"
_COR_ALT = "#FFC914"
_COR_NEU = "#718096"

_WEIGHTING_LABELS = {
    "score": "Pontuação fundamentalista",
    "equal": "Pesos iguais",
    "inverse_vol": "Inverso da volatilidade",
}


def _render_company_analysis_css() -> None:
    st.markdown("""
    <style>
    .us-ind-card{background:#12151E;border:1px solid #1E2533;border-radius:12px;
      padding:14px 16px;min-height:96px;margin-bottom:6px}
    .us-ind-label{font-size:.64rem;font-weight:800;letter-spacing:.10em;
      text-transform:uppercase;color:#52627D}
    .us-ind-value{font-size:1.45rem;font-weight:800;margin:5px 0 2px}
    .us-ind-sub{font-size:.66rem;color:#52627D}
    </style>
    """, unsafe_allow_html=True)


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


def _fmt_usd(value) -> str:
    try:
        v = float(value)
        if not np.isfinite(v):
            return "—"
    except (TypeError, ValueError):
        return "—"
    sign = "-" if v < 0 else ""
    n = abs(v)
    if n >= 1e12:
        return f"{sign}US$ {n / 1e12:.2f} tri"
    if n >= 1e9:
        return f"{sign}US$ {n / 1e9:.2f} bi"
    if n >= 1e6:
        return f"{sign}US$ {n / 1e6:.2f} mi"
    return f"{sign}US$ {n:,.2f}"


def _fmt_growth(value) -> str:
    try:
        v = float(value)
        return "—" if not np.isfinite(v) else f"{v:+.2%}"
    except (TypeError, ValueError):
        return "—"


def _value_color(value, *, invert: bool = False) -> str:
    try:
        v = float(value)
        if not np.isfinite(v):
            return _COR_NEU
        positive = v > 0
        if invert:
            positive = not positive
        return _COR_POS if positive else _COR_NEG
    except (TypeError, ValueError):
        return _COR_NEU


def _analysis_card(label: str, value: str, subtitle: str,
                   color: str = _COR_POS) -> str:
    return (
        '<div class="us-ind-card">'
        f'<div class="us-ind-label">{html.escape(label)}</div>'
        f'<div class="us-ind-value" style="color:{color}">{html.escape(value)}</div>'
        f'<div class="us-ind-sub">{html.escape(subtitle)}</div></div>'
    )


def _analysis_header(title: str) -> None:
    st.markdown(
        f'<div style="font-size:.75rem;font-weight:700;color:#E2E8F0;'
        f'margin:18px 0 8px">{title}</div>', unsafe_allow_html=True)


def _analysis_cards(items: list[tuple[str, str, str, str]], columns: int = 4) -> None:
    for start in range(0, len(items), columns):
        row = items[start:start + columns]
        cols = st.columns(columns, gap="small")
        for idx, (label, value, subtitle, color) in enumerate(row):
            with cols[idx]:
                st.markdown(_analysis_card(label, value, subtitle, color),
                            unsafe_allow_html=True)


def _company_plot_layout(height: int = 320) -> dict:
    return dict(
        template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)", font=dict(color="#CBD5E0"), height=height,
        margin=dict(l=8, r=18, t=10, b=22),
        legend=dict(orientation="h", y=-.18, bgcolor="rgba(0,0,0,0)"),
        yaxis=dict(showgrid=True, gridcolor="#1E2533"),
        xaxis=dict(showgrid=False),
    )


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
    _render_company_analysis_css()
    if _empty_if_offline(status, "Sem dados locais para analisar empresas.", "🔍"):
        return
    scored = us.scored_universe()
    if scored is None or scored.empty:
        estado_vazio("Sem empresas com demonstrações suficientes para análise.", "🔍")
        return
    scored = scored.copy()
    scored["symbol"] = scored["symbol"].astype(str).str.upper()
    valid_symbols = set(scored["symbol"])
    selected = str(st.session_state.get("us_selected_ticker", "") or "").upper()
    if "us_company_ticker_input" not in st.session_state:
        st.session_state["us_company_ticker_input"] = selected

    input_col, button_col = st.columns([4, 1])
    with input_col:
        ticker_raw = st.text_input(
            "Ticker da empresa", key="us_company_ticker_input",
            placeholder="Ex.: AAPL, MSFT, NVDA",
        )
    with button_col:
        st.markdown("<br>", unsafe_allow_html=True)
        analyze = st.button("Analisar", type="primary", use_container_width=True,
                            key="us_btn_analisar_empresa")
    if analyze:
        requested = ticker_raw.strip().upper()
        if requested in valid_symbols:
            st.session_state["us_selected_ticker"] = requested
            st.rerun()
        elif requested:
            st.warning("Ticker não encontrado no universo americano publicado.", icon="⚠️")

    symbol = str(st.session_state.get("us_selected_ticker", "") or "").upper()
    if not symbol:
        st.info("Digite um ticker acima e clique em **Analisar**.", icon="🔍")
        return
    match = scored[scored["symbol"] == symbol]
    if match.empty:
        st.warning("Ticker não encontrado no universo americano publicado.", icon="⚠️")
        return
    row = localize_us_company_frame(match).iloc[0]

    with st.spinner(f"Carregando dados de {symbol}…"):
        financials = numeric_financials(us.company_financials(symbol))
        market_data = us.company_market_data(symbol)
    prices = market_data.get("prices", pd.DataFrame()) if isinstance(market_data, dict) else pd.DataFrame()
    dividends = market_data.get("dividends", pd.DataFrame()) if isinstance(market_data, dict) else pd.DataFrame()
    if not prices.empty:
        prices = prices.copy()
        prices["date"] = pd.to_datetime(prices["date"], errors="coerce")
        prices["price"] = pd.to_numeric(prices["price"], errors="coerce")
        prices = prices.dropna().sort_values("date")
    history = derive_metric_history(financials, prices, dividends)
    dividend_history = annual_dividends(financials, dividends)
    current_price = last_value(prices.rename(columns={"price": "value"}), "value") \
        if not prices.empty else None

    logo_col, identity_col, price_col = st.columns([1, 5, 2])
    with logo_col:
        logo = us_logo_url(symbol)
        st.markdown(
            f'<img src="{html.escape(logo, quote=True)}" '
            'style="width:64px;height:64px;border-radius:12px;object-fit:contain;'
            'background:rgba(255,255,255,.06);padding:6px;margin-top:6px" '
            'onerror="this.style.display=\'none\'">', unsafe_allow_html=True)
    with identity_col:
        st.markdown(
            f'<h2 style="font-size:1.60rem;font-weight:800;color:#E2E8F0;margin:0 0 4px">'
            f'{html.escape(symbol)} — {html.escape(str(row.get("name", "")))}</h2>'
            f'<div style="font-size:.78rem;color:#718096">'
            f'{html.escape(str(row.get("sector", "—")))} · '
            f'{html.escape(str(row.get("industry", "—")))}</div>', unsafe_allow_html=True)
    with price_col:
        price_text = f"US$ {current_price:,.2f}" if current_price is not None else "—"
        st.markdown(
            '<div style="text-align:right;padding-top:8px">'
            f'<div style="font-size:1.60rem;font-weight:800;color:{_COR_POS}">'
            f'{price_text}</div><div style="font-size:.68rem;color:#4A5568">'
            'Cotação no warehouse/snapshot</div></div>', unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)
    _analysis_header("📉 Preço da Ação")
    if not prices.empty:
        periods = {"1A": 365, "3A": 1095, "5A": 1825, "Máx": None}
        selected_period = st.radio(
            "Período", list(periods), index=3, horizontal=True,
            key=f"us_price_period_{symbol}")
        chart_prices = prices.copy()
        days = periods[selected_period]
        if days:
            chart_prices = chart_prices[
                chart_prices["date"] >= chart_prices["date"].max() - pd.Timedelta(days=days)]
        fig = px.line(chart_prices, x="date", y="price",
                      color_discrete_sequence=[_COR_INFO])
        fig.update_traces(line_width=1.5)
        fig.update_layout(**_company_plot_layout(280), showlegend=False,
                          yaxis_title="Preço (US$)", xaxis_title="")
        st.plotly_chart(fig, use_container_width=True,
                        config={"displayModeBar": False}, key=f"us_price_{symbol}_{selected_period}")
    else:
        st.info("Histórico de preços ainda não publicado para este ticker.")

    _analysis_header("📊 Retorno Anual do Preço")
    annual_returns = annual_price_returns(prices)
    if not annual_returns.empty:
        annual_returns["Ano"] = annual_returns["Ano"].astype(str)
        annual_returns["Positivo"] = annual_returns["Retorno"] >= 0
        annual_returns["Texto"] = annual_returns["Retorno"].map(lambda value: f"{value:+.2f}%")
        fig = px.bar(
            annual_returns, x="Retorno", y="Ano", orientation="h", color="Positivo",
            color_discrete_map={True: _COR_POS, False: _COR_NEG}, text="Texto")
        fig.update_traces(textposition="outside", textfont_size=10)
        fig.update_layout(**_company_plot_layout(max(250, len(annual_returns) * 28)),
                          showlegend=False, xaxis_title="Retorno anual (%)", yaxis_title="Ano")
        st.plotly_chart(fig, use_container_width=True,
                        config={"displayModeBar": False}, key=f"us_returns_{symbol}")
    else:
        st.info("Retornos anuais serão exibidos após a publicação do histórico de preços.")

    _analysis_header("📈 Crescimento Médio Anual (CAGR)")
    cagr_items = []
    for label, field, source in (
        ("Receita Líquida", "revenue", financials),
        ("EBIT", "ebit", financials),
        ("Lucro Líquido", "net_income", financials),
        ("Dividendos", "dividends_per_share", dividend_history),
    ):
        growth = regression_cagr(source, field)
        cagr_items.append((label, _fmt_growth(growth), "Regressão log histórica",
                           _value_color(growth)))
    _analysis_cards(cagr_items)

    _analysis_header("📋 Último Exercício Disponível")
    latest_items = []
    for label, field, row_field, invert in (
        ("Receita Líquida", "revenue", "_revenue", False),
        ("EBIT", "ebit", "_ebit", False),
        ("Lucro Líquido", "net_income", "_net_income", False),
        ("Dívida Líquida", "net_debt", "_net_debt", True),
    ):
        value = last_value(financials, field)
        if value is None:
            value = row.get(row_field)
        latest_items.append((label, _fmt_usd(value), "Último registro SEC/GAAP",
                             _value_color(value, invert=invert)))
    _analysis_cards(latest_items)

    _analysis_header("📊 Demonstrações Financeiras — Histórico")
    statement_map = {
        "revenue": "Receita Líquida", "ebit": "EBIT", "ebitda": "EBITDA",
        "net_income": "Lucro Líquido", "total_equity": "Patrimônio Líquido",
        "net_debt": "Dívida Líquida", "total_debt": "Dívida Total",
        "total_assets": "Ativo Total",
    }
    statement_cols = [column for column in statement_map if column in financials
                      and financials[column].notna().any()]
    if statement_cols:
        labels = [statement_map[column] for column in statement_cols]
        defaults = [label for label in ("Receita Líquida", "Lucro Líquido") if label in labels]
        selected_labels = st.multiselect(
            "Indicadores", labels, default=defaults or labels[:2],
            key=f"us_statements_{symbol}")
        if selected_labels:
            reverse = {label: column for column, label in statement_map.items()}
            selected_cols = [reverse[label] for label in selected_labels]
            long = financials[["fiscal_year", *selected_cols]].melt(
                "fiscal_year", var_name="Indicador", value_name="Valor")
            long["Indicador"] = long["Indicador"].map(statement_map)
            fig = px.line(long, x="fiscal_year", y="Valor", color="Indicador", markers=True,
                          color_discrete_sequence=[_COR_POS, _COR_INFO, _COR_ALT, _COR_NEG])
            fig.update_layout(**_company_plot_layout(340),
                              xaxis_title="Ano fiscal", yaxis_title="US$ (valores absolutos)")
            st.plotly_chart(fig, use_container_width=True,
                            config={"displayModeBar": False}, key=f"us_statements_chart_{symbol}")
    else:
        st.info("Demonstrações históricas indisponíveis para este ticker.")

    _analysis_header("💰 Dividendos por ação")
    if not dividend_history.empty:
        fig = px.bar(dividend_history, x="fiscal_year", y="dividends_per_share",
                     color_discrete_sequence=[_COR_ALT])
        fig.update_layout(**_company_plot_layout(240),
                          xaxis_title="Ano fiscal", yaxis_title="US$/ação (soma anual)")
        st.plotly_chart(fig, use_container_width=True,
                        config={"displayModeBar": False}, key=f"us_dividends_{symbol}")
    else:
        st.info("Sem histórico de dividendos por ação publicado.")

    _analysis_header("📊 Gráfico de Múltiplos — Histórico")
    metric_labels = {
        "net_margin": "Margem Líquida", "operating_margin": "Margem Operacional",
        "roe": "ROE", "roa": "ROA", "roic": "ROIC", "fcf_margin": "Margem FCL",
        "dividend_yield": "Dividend Yield", "payout": "Payout",
    }
    metric_cols = [column for column in metric_labels if column in history
                   and history[column].notna().any()]
    if metric_cols:
        options = [metric_labels[column] for column in metric_cols]
        selected_metrics = st.multiselect(
            "Indicadores (%)", options, default=options[:2], key=f"us_metrics_history_{symbol}")
        if selected_metrics:
            reverse = {label: column for column, label in metric_labels.items()}
            selected_cols = [reverse[label] for label in selected_metrics]
            plot = history[["fiscal_year", *selected_cols]].copy()
            plot[selected_cols] = plot[selected_cols] * 100
            long = plot.melt("fiscal_year", var_name="Indicador", value_name="Valor (%)")
            long["Indicador"] = long["Indicador"].map(metric_labels)
            fig = px.bar(long, x="fiscal_year", y="Valor (%)", color="Indicador",
                         barmode="group", color_discrete_sequence=[_COR_POS, _COR_INFO,
                                                                    _COR_ALT, _COR_NEG])
            fig.update_layout(**_company_plot_layout(300), xaxis_title="Ano fiscal")
            st.plotly_chart(fig, use_container_width=True,
                            config={"displayModeBar": False}, key=f"us_metrics_chart_{symbol}")
    else:
        st.info("Histórico de margens e retornos ainda indisponível.")

    _analysis_header("💰 Fluxo de Caixa")
    cashflow_map = {
        "operating_cash_flow": "FCO (Operacional)",
        "investing_cash_flow": "FCI (Investimento)",
        "free_cash_flow": "FCL (Livre)",
    }
    cash_items = []
    for field, label in cashflow_map.items():
        value = last_value(financials, field)
        cash_items.append((label, _fmt_usd(value), "Fonte: SEC/GAAP",
                           _value_color(value)))
    _analysis_cards(cash_items, columns=3)
    cash_cols = [field for field in cashflow_map if field in financials
                 and financials[field].notna().any()]
    if cash_cols:
        long = financials[["fiscal_year", *cash_cols]].melt(
            "fiscal_year", var_name="Fluxo", value_name="Valor")
        long["Fluxo"] = long["Fluxo"].map(cashflow_map)
        fig = px.bar(long, x="fiscal_year", y="Valor", color="Fluxo", barmode="group",
                     color_discrete_sequence=[_COR_POS, _COR_NEG, _COR_INFO])
        fig.update_layout(**_company_plot_layout(300),
                          xaxis_title="Ano fiscal", yaxis_title="Valor (US$)")
        st.plotly_chart(fig, use_container_width=True,
                        config={"displayModeBar": False}, key=f"us_cashflow_{symbol}")

    _analysis_header("🏛️ Estrutura de Capital e Dívida")
    capital_map = (
        ("Caixa", "cash_and_equivalents", False), ("Dívida CP", "short_term_debt", True),
        ("Dívida LP", "long_term_debt", True), ("Dívida Total", "total_debt", True),
        ("Dívida Líquida", "net_debt", True), ("Patrimônio Líquido", "total_equity", False),
    )
    capital_items = []
    for label, field, invert in capital_map:
        value = last_value(financials, field)
        capital_items.append((label, _fmt_usd(value), "Último período disponível",
                              _value_color(value, invert=invert)))
    _analysis_cards(capital_items, columns=3)
    capital_chart_cols = [field for _, field, _ in capital_map[:3]
                          if field in financials and financials[field].notna().any()]
    if capital_chart_cols:
        label_by_field = {field: label for label, field, _ in capital_map}
        long = financials[["fiscal_year", *capital_chart_cols]].melt(
            "fiscal_year", var_name="Item", value_name="Valor")
        long["Item"] = long["Item"].map(label_by_field)
        fig = px.bar(long, x="fiscal_year", y="Valor", color="Item", barmode="group",
                     color_discrete_sequence=[_COR_POS, _COR_ALT, _COR_NEG])
        fig.update_layout(**_company_plot_layout(300),
                          xaxis_title="Ano fiscal", yaxis_title="Valor (US$)")
        st.plotly_chart(fig, use_container_width=True,
                        config={"displayModeBar": False}, key=f"us_capital_{symbol}")

    def latest_metric(name: str, row_name: str | None = None):
        value = last_value(history, name)
        if value is None and row_name:
            value = row.get(row_name)
        return value

    _analysis_header("📐 Rentabilidade")
    profitability = []
    for label, metric, row_metric, subtitle in (
        ("Margem Líquida", "net_margin", "net_margin", "% Lucro/Receita"),
        ("Margem Operacional", "operating_margin", "operating_margin", "% EBIT/Receita"),
        ("ROE", "roe", "roe", "Retorno s/ PL"), ("ROA", "roa", "roa", "Retorno s/ Ativos"),
        ("ROIC", "roic", "roic", "Retorno s/ Capital"),
        ("Dividend Yield", "dividend_yield", None, "Dividendos/Preço"),
    ):
        value = latest_metric(metric, row_metric)
        profitability.append((label, _fmt_pct(value, 2), subtitle, _value_color(value)))
    _analysis_cards(profitability, columns=3)

    _analysis_header("💹 Valuation")
    valuation = []
    for label, metric, row_metric, subtitle in (
        ("P/VP", "p_b", None, "Preço/Valor Patrimonial"),
        ("P/L", "pe", "pe", "Preço/Lucro"),
        ("EV/EBIT", "ev_ebit", "ev_ebit", "Valor Empresa/EBIT"),
        ("P/FCL", "p_fcf", "p_fcf", "Preço/Fluxo de Caixa Livre"),
    ):
        value = latest_metric(metric, row_metric)
        valuation.append((label, _fmt_ratio(value, 2), subtitle, _value_color(value)))
    payout = latest_metric("payout")
    valuation.append(("Payout", _fmt_pct(payout, 2), "% Lucro distribuído",
                      _value_color(payout)))
    _analysis_cards(valuation)

    _analysis_header("🏗️ Estrutura de Capital")
    structure = []
    for label, metric, row_metric, subtitle, invert in (
        ("Endividamento", "debt_to_equity", "debt_to_equity", "Dívida Total/PL", True),
        ("Alavancagem Fin.", "financial_leverage", None, "Ativos/PL", True),
        ("Liquidez Corrente", "current_ratio", "current_ratio",
         "Ativo Circ./Passivo Circ.", False),
    ):
        value = latest_metric(metric, row_metric)
        structure.append((label, _fmt_ratio(value, 2), subtitle,
                          _value_color(value, invert=invert)))
    _analysis_cards(structure, columns=3)

    _analysis_header("🏆 Score e critérios de avaliação")
    _render_score_dashboard(row)
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
    """Laboratório contínuo equivalente à Análise Avançada da B3."""
    if _empty_if_offline(status, "Sem dados locais para a análise avançada.", "🔬"):
        return
    scored = us.scored_universe()
    if scored is None or scored.empty:
        estado_vazio("Sem empresas com demonstrações suficientes para análise.", "🔬")
        return
    scored = scored.copy()
    scored["symbol"] = scored["symbol"].astype(str).str.upper()

    st.markdown(
        '<div style="background:rgba(56,189,248,.06);border-left:3px solid #38BDF8;'
        'border-radius:6px;padding:12px 16px;margin-bottom:12px;font-size:.84rem;'
        'color:#CBD5E1"><strong>🔬 Etapa 1 de 3 · Banco de testes por indústria.</strong> '
        'Escolha uma indústria e valide filtros, indicadores e pontuações antes de '
        'aplicá-los em escala. As etapas seguintes são <strong>Criação de Portfólio</strong> '
        'e <strong>Avaliação de Portfólio</strong>.</div>', unsafe_allow_html=True)
    st.info(
        "⚠️ **Ferramenta educacional — não é recomendação de investimento.** "
        "Pontuações, testes históricos e simulações usam dados históricos SEC/GAAP "
        "e premissas quantitativas. Rentabilidade passada não garante resultado futuro.")
    st.caption(
        "📅 O score usa o último exercício fiscal anual disponível e compara empresas "
        "por indústria. Fonte fundamentalista: SEC/GAAP; preços: vitrine local. "
        "Disponibilidade histórica é respeitada quando o painel ponto-no-tempo existe.")

    # ── Filtros do universo ────────────────────────────────────────────────
    _analysis_header("⚙️ Filtros do Universo")
    f1, f2, f3, f4 = st.columns(4)
    sectors = ["Todos"] + sorted(str(x) for x in scored.get("sector", pd.Series()).dropna().unique())
    with f1:
        sector = st.selectbox("Setor", sectors, key="us_lab_sector",
                              format_func=lambda x: x if x == "Todos" else translate_us_sector(x))
    sector_view = scored if sector == "Todos" else scored[scored["sector"] == sector]
    industries = ["Todas"] + sorted(str(x) for x in sector_view.get(
        "industry", pd.Series()).dropna().unique())
    with f2:
        industry = st.selectbox("Indústria", industries, key="us_lab_industry",
            format_func=lambda x: x if x == "Todas" else translate_us_industry(x))
    exchanges = ["Todas"] + (sorted(str(x) for x in scored["exchange"].dropna().unique())
                              if "exchange" in scored else [])
    with f3:
        exchange = st.selectbox("Bolsa", exchanges, key="us_lab_exchange")
    profiles = ["Todas", "Qualidade", "Crescimento", "Valor", "Renda", "Solidez"]
    with f4:
        profile = st.selectbox("Perfil", profiles, key="us_lab_profile")

    l1, l2, *_ = st.columns(4)
    with l1:
        liquidity = st.selectbox(
            "Liquidez mínima (negociação)",
            ["Sem filtro", "≥ US$ 1 milhão/dia", "≥ US$ 5 milhões/dia",
             "≥ US$ 20 milhões/dia"], key="us_lab_liquidity",
            help="Aplicado somente quando o volume financeiro médio estiver publicado.")
    with l2:
        min_coverage = st.selectbox("Cobertura mínima dos dados", [40, 50, 60, 70, 80],
                                    index=2, format_func=lambda x: f"≥ {x}%",
                                    key="us_lab_coverage")

    filtered = sector_view.copy()
    if industry != "Todas":
        filtered = filtered[filtered["industry"] == industry]
    if exchange != "Todas" and "exchange" in filtered:
        filtered = filtered[filtered["exchange"] == exchange]
    profile_col = {"Qualidade": "score_quality", "Crescimento": "score_growth",
                   "Valor": "score_valuation", "Renda": "score_shareholder",
                   "Solidez": "score_solidity"}.get(profile)
    if profile_col and profile_col in filtered:
        filtered = filtered[pd.to_numeric(filtered[profile_col], errors="coerce") >= 60]
    volume_col = next((c for c in ("avg_dollar_volume", "dollar_volume") if c in filtered), None)
    liquidity_floor = {"≥ US$ 1 milhão/dia": 1e6, "≥ US$ 5 milhões/dia": 5e6,
                       "≥ US$ 20 milhões/dia": 20e6}.get(liquidity)
    if liquidity_floor and volume_col:
        filtered = filtered[pd.to_numeric(filtered[volume_col], errors="coerce") >= liquidity_floor]
    elif liquidity_floor:
        st.caption("💧 Volume médio não está publicado nesta vitrine; o filtro de liquidez "
                   "permanece informativo e nenhuma empresa é excluída por dado ausente.")

    # ── Pesos e score de entrada ──────────────────────────────────────────
    from core.us_advanced_lab import DEFAULT_WEIGHTS, build_entry_scores
    with st.expander("⚖️ Pesos do Scoring"):
        st.caption("Pesos das seis trilhas americanas. Os valores são renormalizados para 100%.")
        weight_labels = {
            "quality": "Qualidade", "growth": "Crescimento", "solidity": "Solidez",
            "capital_efficiency": "Eficiência de capital", "valuation": "Avaliação",
            "shareholder": "Retorno ao acionista",
        }
        weight_cols = st.columns(3)
        custom_weights = {}
        for idx, (track, default) in enumerate(DEFAULT_WEIGHTS.items()):
            with weight_cols[idx % 3]:
                custom_weights[track] = st.slider(
                    weight_labels[track], 0, 50, int(default * 100), 1,
                    key=f"us_lab_weight_{track}")

    coverage = pd.to_numeric(filtered.get("coverage"), errors="coerce").fillna(0)
    excluded = filtered[coverage < min_coverage].copy()
    eligible = filtered[coverage >= min_coverage].copy()
    entry = build_entry_scores(eligible, custom_weights)

    # ── Qualidade, incerteza e análises opcionais ─────────────────────────
    with st.expander("🩺 Qualidade & saneamento dos dados (antes do ranking)"):
        q1, q2, q3, q4 = st.columns(4)
        with q1: card_metrica("Universo filtrado", f"{len(filtered)}")
        with q2: card_metrica("Elegíveis", f"{len(entry)}")
        with q3: card_metrica("Excluídas", f"{len(excluded)}")
        with q4:
            card_metrica("Cobertura média", f"{coverage.mean():.0f}%" if len(coverage) else "—")
        track_cov = []
        for col, label in _TRACK_LABELS.items():
            cov_col = col.replace("score_", "coverage_")
            if cov_col in filtered:
                track_cov.append({"Trilha": label,
                                  "Cobertura média (%)": pd.to_numeric(
                                      filtered[cov_col], errors="coerce").mean()})
        if track_cov:
            st.dataframe(pd.DataFrame(track_cov), hide_index=True, use_container_width=True)
        st.caption("Ausência não vira zero: recebe posição neutra no score e reduz a cobertura.")

    with st.expander("Empresas excluídas por completude de dados"):
        if excluded.empty:
            st.success("Nenhuma empresa excluída com o limite atual.")
        else:
            cols = [c for c in ("symbol", "name", "sector", "industry", "coverage") if c in excluded]
            show = localize_us_company_frame(excluded[cols]).rename(columns={
                "symbol": "Ticker", "name": "Nome", "sector": "Setor",
                "industry": "Indústria", "coverage": "Cobertura (%)"})
            st.dataframe(show, hide_index=True, use_container_width=True)

    with st.expander("Validação cross-source — SEC/GAAP × dados de mercado"):
        st.caption("Fundamentos são derivados das demonstrações SEC/GAAP; múltiplos exigem "
                   "também preço e ações em circulação. Divergências não são preenchidas com zero.")
        validation_rows = []
        for metric, label in (("pe", "P/L"), ("ev_ebitda", "EV/EBITDA"),
                              ("fcf_yield", "Retorno do FCL"),
                              ("shareholder_yield", "Retorno ao acionista")):
            if metric in filtered:
                validation_rows.append({"Indicador": label,
                    "Empresas com dado": int(filtered[metric].notna().sum()),
                    "Cobertura (%)": filtered[metric].notna().mean() * 100,
                    "Fontes necessárias": "SEC/GAAP + preço"})
        st.dataframe(pd.DataFrame(validation_rows), hide_index=True, use_container_width=True)

    with st.expander("📊 Análise de incerteza (bootstrap) — opcional"):
        if entry.empty:
            st.info("Sem empresa elegível para o bootstrap.")
        else:
            bt_symbol = st.selectbox("Empresa", entry["symbol"].tolist(), key="us_lab_boot_symbol")
            n_boot = st.select_slider("Reamostragens", [200, 500, 1000, 2000], value=1000,
                                      key="us_lab_boot_n")
            if st.button("Calcular intervalo", key="us_lab_boot_btn"):
                from core.us_advanced_lab import bootstrap_track_score
                result = bootstrap_track_score(
                    entry[entry["symbol"] == bt_symbol].iloc[0], custom_weights, n_boot)
                b1, b2, b3, b4 = st.columns(4)
                with b1: card_metrica("Média", f"{result['mean']:.1f}")
                with b2: card_metrica("Percentil 5", f"{result['p05']:.1f}")
                with b3: card_metrica("Percentil 95", f"{result['p95']:.1f}")
                with b4: card_metrica("Desvio", f"{result['std']:.1f}")

    with st.expander("🎯 Otimização pós-seleção — equivalente EUA"):
        st.caption("Aplica limites por ativo e setor sobre a pontuação de entrada; "
                   "a vitrine não inventa covariância quando não há histórico local.")
        if not entry.empty:
            opt_base = entry.copy()
            opt_base["score"] = opt_base["entry_score"]
            holdings, _ = _portfolio_controls(opt_base, "us_lab_opt")
            if holdings is not None and not holdings.empty:
                show = localize_us_company_frame(holdings).rename(columns={
                    "symbol": "Ticker", "name": "Nome", "sector": "Setor",
                    "weight": "Peso"})
                st.dataframe(show, hide_index=True, use_container_width=True)

    with st.expander("💰 Avaliação e retorno ao acionista"):
        cols = [c for c in ("symbol", "name", "pe", "ev_ebit", "ev_ebitda", "p_fcf",
                            "fcf_yield", "shareholder_yield", "score_valuation",
                            "score_shareholder") if c in entry]
        if cols:
            st.dataframe(entry[cols].head(50).rename(columns={
                "symbol": "Ticker", "name": "Nome", "pe": "P/L", "ev_ebit": "EV/EBIT",
                "ev_ebitda": "EV/EBITDA", "p_fcf": "P/FCL", "fcf_yield": "Retorno FCL",
                "shareholder_yield": "Retorno ao acionista",
                "score_valuation": "Score avaliação",
                "score_shareholder": "Score acionista"}), hide_index=True,
                use_container_width=True)

    with st.expander("🛡️ Resiliência histórica e saúde financeira — sensibilidade EUA"):
        health_cols = [c for c in ("symbol", "net_debt_ebitda", "interest_coverage",
                                   "current_ratio", "fcf_margin", "risk_penalty",
                                   "risk_driver") if c in entry]
        st.dataframe(entry[health_cols].head(50).rename(columns={
            "symbol": "Ticker", "net_debt_ebitda": "Dív. líq./EBITDA",
            "interest_coverage": "Cobertura de juros", "current_ratio": "Liquidez corrente",
            "fcf_margin": "Margem FCL", "risk_penalty": "Penalidade",
            "risk_driver": "Motivo"}), hide_index=True, use_container_width=True)
        st.markdown("**Cenário macroeconômico americano**")
        _render_macro_dashboard("us_lab_macro")

    with st.expander("🧬 Atribuição do score por trilha — explicabilidade"):
        if not entry.empty:
            explain_symbol = st.selectbox("Empresa", entry["symbol"].tolist(),
                                          key="us_lab_explain_symbol")
            from core.us_advanced_lab import factor_contributions
            contrib = factor_contributions(
                entry[entry["symbol"] == explain_symbol].iloc[0], custom_weights)
            fig = px.bar(contrib, x="Contribuição", y="Trilha", orientation="h",
                         color="Contribuição",
                         color_continuous_scale=[_COR_NEG, _COR_ALT, _COR_POS])
            fig.update_layout(**_PLOT_LAYOUT, height=300, coloraxis_showscale=False)
            st.plotly_chart(fig, use_container_width=True, key="us_lab_contributions")

    with st.expander("🔮 Black-Litterman — incorporar suas visões"):
        st.caption("Camada de cenário: ajusta a nota exibida sem reescrever os fundamentos. "
                   "A visão representa pontos de convicção entre −20 e +20.")
        if not entry.empty:
            view_symbols = st.multiselect("Empresas com visão", entry["symbol"].tolist(),
                default=entry["symbol"].head(min(3, len(entry))).tolist(),
                max_selections=5, key="us_lab_bl_symbols")
            adjusted = entry[["symbol", "entry_score"]].copy()
            adjusted["Visão"] = 0.0
            for symbol in view_symbols:
                delta = st.slider(f"Visão para {symbol}", -20, 20, 0, 1,
                                  key=f"us_lab_bl_{symbol}")
                adjusted.loc[adjusted["symbol"] == symbol, "Visão"] = delta
            adjusted["Score ajustado"] = (
                adjusted["entry_score"] + adjusted["Visão"] * .35).clip(0, 100).round(1)
            st.dataframe(adjusted.sort_values("Score ajustado", ascending=False).head(20)
                         .rename(columns={"symbol": "Ticker", "entry_score": "Score base"}),
                         hide_index=True, use_container_width=True)

    with st.expander("🔬 Diagnósticos avançados — Piotroski, Altman, Sloan e ROIC incremental"):
        _tab_analise_avancada(status)

    # ── Universo e score de entrada ───────────────────────────────────────
    _render_us_lab_universe(entry)
    _render_us_lab_entry(entry)
    _render_us_lab_backtest()
    _render_us_lab_comparisons(entry)
    _render_us_lab_methodology()


def _render_us_lab_universe(entry: pd.DataFrame) -> None:
    _analysis_header(f"🏢 Universo Filtrado — {len(entry)} empresa(s)")
    if entry.empty:
        estado_vazio("Nenhuma empresa atende aos filtros e à cobertura mínima.", "🏢")
        return
    top = entry.head(12)
    for start in range(0, len(top), 4):
        cols = st.columns(4, gap="small")
        for idx, (_, row) in enumerate(top.iloc[start:start + 4].iterrows()):
            with cols[idx]:
                symbol = html.escape(str(row.get("symbol", "")))
                name = html.escape(str(row.get("name") or symbol))
                score = float(row.get("score_base_adv", 0) or 0)
                years = row.get("_years")
                years_text = f"{int(years)} anos SEC" if pd.notna(years) else "histórico SEC"
                st.markdown(
                    '<div class="us-ind-card" style="min-height:126px">'
                    f'<div style="display:flex;gap:9px;align-items:center">'
                    f'<img src="{us_logo_url(symbol)}" style="width:42px;height:42px;'
                    'border-radius:8px;object-fit:contain;background:rgba(255,255,255,.05)" '
                    'onerror="this.style.display=\'none\'">'
                    f'<div><strong style="color:#E2E8F0">{symbol}</strong><br>'
                    f'<span style="font-size:.67rem;color:#718096">{name[:30]}</span></div></div>'
                    f'<div style="display:flex;justify-content:space-between;margin-top:14px">'
                    f'<span style="background:rgba(0,200,150,.12);color:#00C896;'
                    f'border-radius:14px;padding:3px 10px;font-size:.72rem;font-weight:800">'
                    f'Score {score:.0f}</span><span style="font-size:.64rem;color:#52627D">'
                    f'{years_text}</span></div></div>', unsafe_allow_html=True)


def _entry_detail_card(row: pd.Series) -> str:
    status = str(row.get("entry_status", "Observação"))
    colors = {"Aprovada": _COR_POS, "Observação": _COR_ALT, "Excluída": _COR_NEG}
    color = colors.get(status, _COR_NEU)
    base = float(row.get("score_base_adv", 0) or 0)
    entry = float(row.get("entry_score", 0) or 0)
    quality = float(row.get("score_quality", 50) or 50)
    growth = float(row.get("score_growth", 50) or 50)
    cash = float(row.get("cash_quality", 50) or 50)

    def bar(label: str, value: float, bar_color: str) -> str:
        width = max(0, min(100, value))
        return (f'<div style="display:grid;grid-template-columns:98px 1fr 34px;gap:8px;'
                f'align-items:center;font-size:.67rem;color:#718096;margin:8px 0">'
                f'<span>{label}</span><span style="background:#1E2533;height:7px;'
                f'border-radius:5px;overflow:hidden"><i style="display:block;width:{width:.0f}%;'
                f'height:100%;background:{bar_color};border-radius:5px"></i></span>'
                f'<strong style="color:#CBD5E1;text-align:right">{value:.0f}</strong></div>')

    return (
        f'<div style="background:#12151E;border:1px solid {color}66;border-radius:13px;'
        f'padding:16px;min-height:258px"><div style="display:flex;justify-content:space-between">'
        f'<strong style="color:#E2E8F0">{html.escape(str(row.get("symbol", "")))}</strong>'
        f'<span style="background:{color}18;color:{color};border:1px solid {color}66;'
        f'border-radius:7px;padding:4px 10px;font-size:.69rem;font-weight:800">{status}</span></div>'
        f'<div style="font-size:.65rem;color:#52627D;margin:5px 0 12px">'
        f'Base: {base:.0f} → Entrada: <b style="color:#E2E8F0">{entry:.0f}</b></div>'
        + bar("Qualidade", quality, _COR_INFO)
        + bar("Consistência", growth, _COR_POS)
        + bar("Caixa", cash, "#9B51E0")
        + f'<div style="font-size:.63rem;color:#52627D;margin-top:11px">'
          f'Penalidade: <b style="color:{_COR_NEG}">−{float(row.get("risk_penalty", 0)):.0f} pts</b><br>'
          f'{html.escape(str(row.get("risk_driver", "")))}</div></div>'
    )


def _render_us_lab_entry(entry: pd.DataFrame) -> None:
    if entry.empty:
        return
    _analysis_header("🎯 Score de Entrada — Composição Avançada")
    counts = entry["entry_status"].value_counts()
    c1, c2, c3, c4 = st.columns(4)
    with c1: card_metrica("Aprovadas", str(int(counts.get("Aprovada", 0))), accent=_COR_POS)
    with c2: card_metrica("Observação", str(int(counts.get("Observação", 0))), accent=_COR_ALT)
    with c3: card_metrica("Excluídas", str(int(counts.get("Excluída", 0))), accent=_COR_NEG)
    with c4: card_metrica("Score médio", f"{entry['entry_score'].mean():.1f}", accent=_COR_INFO)

    with st.expander("📊 Breakdown completo — todas as empresas"):
        cols = [c for c in ("symbol", "name", "sector", "industry", "score_base_adv",
                            "entry_score", "entry_status", "score_quality", "score_growth",
                            "cash_quality", "risk_penalty", "risk_driver", "coverage") if c in entry]
        show = localize_us_company_frame(entry[cols]).rename(columns={
            "symbol": "Ticker", "name": "Nome", "sector": "Setor", "industry": "Indústria",
            "score_base_adv": "Score base", "entry_score": "Score entrada",
            "entry_status": "Situação", "score_quality": "Qualidade",
            "score_growth": "Consistência", "cash_quality": "Caixa",
            "risk_penalty": "Penalidade", "risk_driver": "Motivo",
            "coverage": "Cobertura (%)"})
        st.dataframe(show, hide_index=True, use_container_width=True)

    _analysis_header("🔍 Detalhamento por Empresa")
    details = entry.head(12)
    for start in range(0, len(details), 3):
        cols = st.columns(3, gap="small")
        for idx, (_, row) in enumerate(details.iloc[start:start + 3].iterrows()):
            with cols[idx]:
                st.markdown(_entry_detail_card(row), unsafe_allow_html=True)


def _render_us_lab_backtest() -> None:
    st.divider()
    _analysis_header("📈 Simulação de Patrimônio e Teste Histórico")
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        monthly = st.number_input("Aporte mensal (US$)", min_value=0.0, value=500.0,
                                  step=100.0, key="us_lab_monthly")
    with c2:
        top_n = st.selectbox("Top-N Estratégia", [5, 10, 15, 20, 30], index=1,
                             key="us_lab_bt_topn")
    with c3:
        mode = st.selectbox("Ponderação", ["score", "equal"],
            format_func=lambda value: _WEIGHTING_LABELS[value], key="us_lab_bt_mode")
    with c4:
        fed = st.number_input("Fed Funds a.a. (%) — referência", 0.0, 15.0, 4.25, .25,
                              key="us_lab_bt_fed")

    if st.button("▶ Simular Backtest", type="primary", key="us_lab_bt_btn"):
        st.session_state["us_lab_bt_result"] = us.backtest(top_n=top_n, weighting=mode)
    result = st.session_state.get("us_lab_bt_result")
    if not result:
        st.caption("Configure os parâmetros e clique em ▶ Simular Backtest.")
    elif not result.get("ok"):
        st.info(result.get("reason", "Teste histórico indisponível."))
    else:
        p, ic = result["portfolio"], result["rank_ic"]
        stats = [
            ("Retorno anual", p.get("ann_return"), True),
            ("Volatilidade", p.get("volatility"), True),
            ("Queda máxima", p.get("max_drawdown"), True),
            ("Rank-IC médio", ic.get("mean"), False),
        ]
        cols = st.columns(4)
        for idx, (label, value, percent) in enumerate(stats):
            with cols[idx]:
                text = "—" if value is None else (f"{value*100:.2f}%" if percent else f"{value:.3f}")
                card_metrica(label, text)
        curve = list(result.get("equity_curve") or [])
        dates = list(result.get("dates") or [])
        if curve:
            capital, prev = 0.0, 1.0
            projected = []
            for value in curve:
                annual_return = float(value) / prev - 1
                capital = (capital + float(monthly) * 12) * (1 + annual_return)
                projected.append(capital)
                prev = float(value)
            plot = pd.DataFrame({"Data": dates, "Carteira (índice)": curve,
                                 "Patrimônio com aportes (US$)": projected})
            fig = px.line(plot, x="Data", y=["Carteira (índice)",
                                               "Patrimônio com aportes (US$)"], markers=True)
            fig.update_layout(**_PLOT_LAYOUT, height=340, legend_title_text="Série")
            st.plotly_chart(fig, use_container_width=True, key="us_lab_bt_curve")
        st.caption(f"Taxa do Fed informada ({fed:.2f}% a.a.) é referência de cenário; "
                   "o retorno exibido vem do painel ponto-no-tempo, sem substituição sintética.")


_US_COMPARE_METRICS = {
    "P/L": "pe", "EV/EBIT": "ev_ebit", "EV/EBITDA": "ev_ebitda", "P/FCL": "p_fcf",
    "Margem Líquida": "net_margin", "Margem Operacional": "operating_margin",
    "ROE": "roe", "ROIC": "roic", "Retorno do FCL": "fcf_yield",
    "Retorno ao acionista": "shareholder_yield",
}


def _render_us_lab_comparisons(entry: pd.DataFrame) -> None:
    if entry.empty:
        return
    st.divider()
    symbols = entry["symbol"].tolist()
    defaults = symbols[:min(5, len(symbols))]

    _analysis_header("📊 Comparação de Múltiplos e Indicadores")
    c1, c2 = st.columns([3, 2])
    with c1:
        selected = st.multiselect("Empresas", symbols, default=defaults, max_selections=5,
                                  key="us_lab_compare_symbols")
    with c2:
        label = st.selectbox("Indicador", list(_US_COMPARE_METRICS), key="us_lab_compare_metric")
    if st.button("📈 Comparar Múltiplos", key="us_lab_compare_btn") and selected:
        metric = _US_COMPARE_METRICS[label]
        current = entry[entry["symbol"].isin(selected)][["symbol", metric]].dropna()
        fig = px.bar(current, x="symbol", y=metric, color=metric,
                     color_continuous_scale=[_COR_POS, _COR_ALT, _COR_NEG])
        fig.update_layout(**_PLOT_LAYOUT, height=320, xaxis_title="Ticker", yaxis_title=label,
                          coloraxis_showscale=False)
        st.plotly_chart(fig, use_container_width=True, key="us_lab_compare_chart")

    _analysis_header("📋 Comparação de Demonstrações Financeiras")
    d1, d2 = st.columns([2, 3])
    statement_map = {"Receita Líquida": "revenue", "EBIT": "ebit", "EBITDA": "ebitda",
                     "Lucro Líquido": "net_income", "Fluxo de Caixa Operacional": "operating_cash_flow",
                     "Fluxo de Caixa Livre": "free_cash_flow", "Dívida Líquida": "net_debt"}
    with d1:
        statement_label = st.selectbox("Item da demonstração", list(statement_map),
                                       key="us_lab_statement")
    with d2:
        statement_symbols = st.multiselect("Empresas", symbols, default=defaults,
                                           max_selections=5, key="us_lab_statement_symbols")
    if st.button("📋 Comparar Demonstrações", key="us_lab_statement_btn"):
        records = []
        field = statement_map[statement_label]
        for symbol in statement_symbols:
            frame = us.company_financials(symbol)
            if frame is None or frame.empty or field not in frame:
                continue
            for _, row in frame[["fiscal_year", field]].dropna().iterrows():
                records.append({"Ano fiscal": int(row["fiscal_year"]), "Ticker": symbol,
                                "Valor": float(row[field])})
        if records:
            fig = px.line(pd.DataFrame(records), x="Ano fiscal", y="Valor", color="Ticker",
                          markers=True)
            fig.update_layout(**_PLOT_LAYOUT, height=360, yaxis_title=f"{statement_label} (US$)")
            st.plotly_chart(fig, use_container_width=True, key="us_lab_statement_chart")
        else:
            st.info("Sem histórico publicado para a seleção.")

    _analysis_header("📊 Quadro Comparativo — Indicadores por Empresa")
    display_cols = [c for c in ("symbol", "roe", "roic", "net_margin", "operating_margin",
                                "fcf_yield", "pe", "ev_ebit", "ev_ebitda",
                                "net_debt_ebitda", "current_ratio", "entry_score") if c in entry]
    labels = {"symbol": "Empresa", "roe": "ROE", "roic": "ROIC",
              "net_margin": "Margem Líq.", "operating_margin": "Margem Op.",
              "fcf_yield": "Retorno FCL", "pe": "P/L", "ev_ebit": "EV/EBIT",
              "ev_ebitda": "EV/EBITDA", "net_debt_ebitda": "Dív.Líq./EBITDA",
              "current_ratio": "Liquidez", "entry_score": "Score Entrada"}
    st.caption("Verde = top 25% · Vermelho = bottom 25%, respeitando a direção econômica.")
    st.dataframe(entry[display_cols].head(200).rename(columns=labels), hide_index=True,
                 use_container_width=True)

    _analysis_header("🔭 Scatter Plot — Correlação entre Indicadores")
    numeric_options = [label for label, col in _US_COMPARE_METRICS.items() if col in entry]
    s1, s2 = st.columns(2)
    with s1: x_label = st.selectbox("Eixo X", numeric_options, key="us_lab_scatter_x")
    with s2: y_label = st.selectbox("Eixo Y", numeric_options,
        index=min(1, len(numeric_options) - 1), key="us_lab_scatter_y")
    if st.button("🔭 Gerar Scatter", key="us_lab_scatter_btn"):
        x, y = _US_COMPARE_METRICS[x_label], _US_COMPARE_METRICS[y_label]
        fig = px.scatter(entry, x=x, y=y, color="entry_score", hover_name="symbol",
                         color_continuous_scale=[_COR_NEG, _COR_ALT, _COR_POS])
        fig.add_vline(x=pd.to_numeric(entry[x], errors="coerce").median(), line_dash="dot")
        fig.add_hline(y=pd.to_numeric(entry[y], errors="coerce").median(), line_dash="dot")
        fig.update_layout(**_PLOT_LAYOUT, height=420, xaxis_title=x_label, yaxis_title=y_label)
        st.plotly_chart(fig, use_container_width=True, key="us_lab_scatter_chart")

    _analysis_header("💵 FCO / Lucro Líquido — Qualidade do Resultado")
    st.caption("Razão > 1: caixa operacional tende a superar o lucro contábil. "
               "Razão < 0,5: conversão do lucro em caixa exige atenção.")
    if st.button("💵 Calcular FCO/Lucro", key="us_lab_cash_btn"):
        cash = entry[["symbol", "cash_conversion", "entry_score"]].dropna()
        if cash.empty:
            st.info("Conversão de caixa indisponível no universo atual.")
        else:
            fig = px.bar(cash.head(50), x="symbol", y="cash_conversion", color="entry_score",
                         color_continuous_scale=[_COR_NEG, _COR_ALT, _COR_POS])
            fig.add_hline(y=1, line_dash="dot", line_color=_COR_POS)
            fig.add_hline(y=.5, line_dash="dot", line_color=_COR_NEG)
            fig.update_layout(**_PLOT_LAYOUT, height=360, xaxis_title="Ticker",
                              yaxis_title="FCO / Lucro Líquido")
            st.plotly_chart(fig, use_container_width=True, key="us_lab_cash_chart")


def _render_us_lab_methodology() -> None:
    _analysis_header("🔬 Metodologia e referências científicas")
    st.markdown(
        "Este banco de testes replica o encadeamento metodológico da B3, adaptado a "
        "empresas americanas e demonstrações SEC/GAAP. Os resultados combinam tratamento "
        "de dados, pontuação relativa, risco, validação e construção de carteira.")
    with st.expander("📚 Ver todas as análises aplicadas e suas referências"):
        st.markdown("""
### 1 · Tratamento de dados

- **Último exercício anual disponível** — evita misturar trimestre parcial com ano fiscal fechado.
- **Universo ativo e negociável** — exclui registros inativos quando essa informação está publicada.
- **Ausência preservada** — dado faltante reduz cobertura e recebe posição neutra; nunca vira zero.
- **Winsorização 5%–95%** — reduz influência de outliers contábeis sem apagar observações.
- **Percentil intra-indústria** — compara modelos de negócio economicamente semelhantes.
- **Reconciliação SEC/GAAP × mercado** — múltiplos exigem fundamento, preço e ações em circulação.

### 2 · Pontuação e qualidade fundamentalista

- **Seis trilhas** — qualidade, crescimento, solidez, eficiência de capital, avaliação e retorno ao acionista.
- **Qualidade** — margens, conversão de caixa, ROE e ROA.
- **Crescimento** — CAGR de receita, lucro operacional, LPA e FCL em janelas de 3–5 anos.
- **Solidez** — dívida líquida/EBITDA, cobertura de juros, liquidez corrente e dívida/PL.
- **Eficiência de capital** — ROIC após imposto federal aproximado.
- **Avaliação** — earnings yield, EV/EBIT, EV/EBITDA, P/FCL e P/Vendas.
- **Retorno ao acionista** — dividendos + recompras − emissões, sobre valor de mercado.
- **Score de entrada** — nota base + qualidade + consistência + caixa − penalidades de risco.

### 3 · Risco, validação e explicabilidade

- **Penalidade financeira** — margem/FCL negativos, dívida alta, liquidez e cobertura de juros baixas.
- **Piotroski F-Score** — nove sinais de rentabilidade, alavancagem e eficiência.
- **Altman Z-Score** — zona segura, cinzenta ou de aflição quando os campos necessários existem.
- **Ajustes contábeis de Sloan** — diferença entre lucro e caixa sobre ativos.
- **ROIC incremental** — retorno produzido pelo capital novo.
- **Bootstrap das trilhas** — sensibilidade da pontuação à composição de fatores.
- **Atribuição aditiva** — contribuição de cada trilha em relação ao ponto neutro.
- **Cenário Fed/CPI/PIB/emprego** — leitura macro americana separada dos fundamentos observados.

### 4 · Portfólio e teste histórico

- **Limites por ativo e setor** — evita concentração excessiva.
- **Ponderação por score ou pesos iguais** — alternativas comparáveis e auditáveis.
- **Painel ponto-no-tempo** — o score de cada data usa somente informação então disponível.
- **Rank-IC** — correlação de Spearman entre classificação e retorno futuro.
- **Sharpe, Sortino, Calmar e drawdown** — retorno ajustado a risco e perdas de cauda.
- **Turnover** — mede o giro implícito dos rebalanceamentos.
- **Benchmark de pesos iguais** — comparação contra o próprio universo elegível.
- **Simulação com aportes** — aplica contribuições ao caminho histórico observado, sem retorno inventado.

Referências-base: Fama & French (1992, 2015), Piotroski (2000), Altman (1968),
Sloan (1996), Markowitz (1952), Black & Litterman (1992), Spearman (1904) e
documentação oficial SEC/US GAAP.
""")


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
