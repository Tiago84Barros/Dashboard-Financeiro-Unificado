"""
core/portfolio_chat_charts.py
Renderização SEGURA de gráficos acionados pelo chat de portfólio B3.

A LLM nunca gera código: ela apenas devolve uma diretiva estruturada
(``{"tipo", "metrica", "tickers", "titulo"}``). Este módulo valida a diretiva
contra uma whitelist (``CHART_REGISTRY``) e desenha o gráfico Plotly usando
SEMPRE dados reais do banco (``core.b3_db``) ou da sessão. Tipos desconhecidos
são ignorados e um gráfico que falhe não derruba o chat.

Padrão visual: Plotly (igual ao resto do app), tema escuro, fundo transparente.
"""
from __future__ import annotations

import logging

import pandas as pd
import streamlit as st

import core.b3_data as _db  # facade c/ feature flag MARKET_READ_SOURCE (default: legacy)
import core.market_read as _mr  # séries do market.* (preços mensais ajustados)

logger = logging.getLogger(__name__)

# ── Aparência comum ───────────────────────────────────────────────────────────

_LAYOUT = dict(
    template="plotly_dark",
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    margin=dict(l=10, r=10, t=44, b=10),
    height=360,
    font=dict(color="#C4CBD5"),
)
_ACCENT = "#00C896"
_PEER   = "#5B8DEF"

# ── Mapa de métricas amigáveis → coluna em public.multiplos ───────────────────
# load_multiplos_todos() expõe: P/L, P/VP, DY, ROE, ROA, ROIC,
# Margem_Liquida, Margem_Operacional, Endividamento_Total, Liquidez_Corrente,
# EV_EBIT, P_FCO, Payout
_METRIC_MAP = {
    "P/L": "P/L", "PL": "P/L", "PRECO/LUCRO": "P/L",
    "P/VP": "P/VP", "PVP": "P/VP",
    "DY": "DY", "DIVIDEND YIELD": "DY", "DIVIDENDYIELD": "DY",
    "ROE": "ROE", "ROA": "ROA", "ROIC": "ROIC",
    "MARGEM_LIQUIDA": "Margem_Liquida", "MARGEM LIQUIDA": "Margem_Liquida",
    "MARGEM LÍQUIDA": "Margem_Liquida", "MARGEM": "Margem_Liquida",
    "MARGEM_OPERACIONAL": "Margem_Operacional", "MARGEM OPERACIONAL": "Margem_Operacional",
    "ENDIVIDAMENTO_TOTAL": "Endividamento_Total", "ENDIVIDAMENTO": "Endividamento_Total",
    "DIVIDA": "Endividamento_Total", "DÍVIDA": "Endividamento_Total",
    "DIVIDA/EBITDA": "Endividamento_Total", "DÍVIDA LÍQUIDA/EBITDA": "Endividamento_Total",
    "LIQUIDEZ_CORRENTE": "Liquidez_Corrente", "LIQUIDEZ": "Liquidez_Corrente",
    "EV_EBIT": "EV_EBIT", "EV/EBIT": "EV_EBIT", "EV/EBITDA": "EV_EBIT", "EVEBITDA": "EV_EBIT",
    "P_FCO": "P_FCO", "P/FCO": "P_FCO",
    "PAYOUT": "Payout",
}

# métricas exibidas como percentual (valor decimal × 100)
_PCT_COLS = {"DY", "ROE", "ROA", "ROIC", "Margem_Liquida", "Margem_Operacional", "Payout"}
# métricas "quanto MENOR melhor" (para ordenação em ranking/peers)
_LOWER_BETTER = {"P/L", "P/VP", "EV_EBIT", "Endividamento_Total", "P_FCO"}

_LABEL = {
    "P/L": "P/L", "P/VP": "P/VP", "DY": "Dividend Yield", "ROE": "ROE", "ROA": "ROA",
    "ROIC": "ROIC", "Margem_Liquida": "Margem líquida", "Margem_Operacional": "Margem op.",
    "Endividamento_Total": "Endividamento", "Liquidez_Corrente": "Liquidez corrente",
    "EV_EBIT": "EV/EBIT", "P_FCO": "P/FCO", "Payout": "Payout",
}


def _resolve_metric(metrica: str | None) -> str | None:
    if not metrica:
        return None
    key = str(metrica).strip().upper()
    return _METRIC_MAP.get(key)


def _clean_tickers(tickers) -> list[str]:
    if not tickers:
        return []
    if isinstance(tickers, str):
        tickers = [tickers]
    out: list[str] = []
    for t in tickers:
        tk = str(t).strip().upper().replace(".SA", "")
        if tk and tk not in out:
            out.append(tk)
    return out


@st.cache_data(ttl=1800, show_spinner=False)
def _multiplos_todos() -> pd.DataFrame:
    """Múltiplos mais recentes de TODOS os tickers (cacheado)."""
    try:
        df = _db.load_multiplos_todos()
    except Exception as exc:  # pragma: no cover - rede/banco
        logger.warning("charts: load_multiplos_todos falhou: %s", exc)
        return pd.DataFrame()
    return df if isinstance(df, pd.DataFrame) else pd.DataFrame()


def _metric_series(col: str, tickers: list[str]) -> pd.DataFrame:
    """Retorna DataFrame [Ticker, valor] para a coluna pedida e tickers dados."""
    df = _multiplos_todos()
    if df.empty or "Ticker" not in df.columns or col not in df.columns:
        return pd.DataFrame(columns=["Ticker", col])
    sub = df[df["Ticker"].isin(tickers)][["Ticker", col]].copy()
    sub[col] = pd.to_numeric(sub[col], errors="coerce")
    sub = sub.dropna(subset=[col])
    if col in _PCT_COLS:
        # normaliza para % de exibição (mantém quando já parece percentual)
        sub[col] = sub[col].apply(lambda v: v * 100 if abs(v) <= 2.0 else v)
    return sub


def _fig_to_streamlit(fig, key: str | None = None) -> None:
    fig.update_layout(**_LAYOUT)
    st.plotly_chart(fig, use_container_width=True, key=key)


# ── Renderizadores individuais ────────────────────────────────────────────────

def render_comparison_chart(metric: str, tickers, title: str | None = None,
                            key: str | None = None) -> bool:
    """Barras comparando uma métrica entre os tickers informados."""
    import plotly.express as px

    col = _resolve_metric(metric)
    tks = _clean_tickers(tickers)
    if col is None:
        st.caption(f"⚠️ Métrica '{metric}' não disponível no banco para gráfico.")
        return False
    if len(tks) < 1:
        st.caption("⚠️ Sem tickers para comparar.")
        return False

    data = _metric_series(col, tks)
    if data.empty:
        st.caption(f"⚠️ Sem dados de {_LABEL.get(col, col)} no banco para {', '.join(tks)}.")
        return False

    data = data.sort_values(col, ascending=col in _LOWER_BETTER)
    suffix = "%" if col in _PCT_COLS else "x"
    data["txt"] = data[col].map(lambda v: f"{v:.1f}{suffix}")
    fig = px.bar(data, x="Ticker", y=col, text="txt",
                 title=title or f"{_LABEL.get(col, col)} — comparação")
    fig.update_traces(marker_color=_ACCENT, textposition="outside", cliponaxis=False)
    fig.update_yaxes(title=_LABEL.get(col, col))
    _fig_to_streamlit(fig, key=key)
    return True


def render_ranking_chart(metric: str, tickers=None, title: str | None = None,
                         top_n: int = 12, key: str | None = None) -> bool:
    """Ranking (barh) por uma métrica. Sem tickers → usa o universo inteiro."""
    import plotly.express as px

    col = _resolve_metric(metric)
    if col is None:
        st.caption(f"⚠️ Métrica '{metric}' não disponível para ranking.")
        return False

    df = _multiplos_todos()
    if df.empty or col not in df.columns:
        st.caption(f"⚠️ Sem dados de {_LABEL.get(col, col)} no banco.")
        return False

    tks = _clean_tickers(tickers)
    if tks:
        df = df[df["Ticker"].isin(tks)]
    data = df[["Ticker", col]].copy()
    data[col] = pd.to_numeric(data[col], errors="coerce")
    data = data.dropna(subset=[col])
    if data.empty:
        st.caption(f"⚠️ Sem valores válidos de {_LABEL.get(col, col)}.")
        return False
    if col in _PCT_COLS:
        data[col] = data[col].apply(lambda v: v * 100 if abs(v) <= 2.0 else v)

    ascending = col in _LOWER_BETTER
    data = data.sort_values(col, ascending=ascending).head(top_n)
    data = data.iloc[::-1]  # melhor no topo
    suffix = "%" if col in _PCT_COLS else "x"
    data["txt"] = data[col].map(lambda v: f"{v:.1f}{suffix}")
    fig = px.bar(data, x=col, y="Ticker", orientation="h", text="txt",
                 title=title or f"Ranking por {_LABEL.get(col, col)}")
    fig.update_traces(marker_color=_ACCENT, textposition="outside", cliponaxis=False)
    fig.update_xaxes(title=_LABEL.get(col, col))
    _fig_to_streamlit(fig, key=key)
    return True


def render_dividend_chart(tickers, title: str | None = None, key: str | None = None) -> bool:
    """Comparação de Dividend Yield."""
    return render_comparison_chart("DY", tickers, title or "Dividend Yield — comparação", key=key)


def render_sector_allocation_chart(model: dict, weights: dict[str, float],
                                   key: str | None = None) -> bool:
    """Pizza da composição por setor/segmento da carteira."""
    import plotly.express as px

    items = (model or {}).get("items", [])
    if not items:
        st.caption("⚠️ Carteira sem itens para compor a alocação setorial.")
        return False

    agg: dict[str, float] = {}
    for it in items:
        tk = str(it.get("ticker", "")).upper().replace(".SA", "")
        seg = str(it.get("setor") or it.get("segmento") or "—")
        w = weights.get(tk, float(it.get("weight") or 0))
        agg[seg] = agg.get(seg, 0.0) + (w or 0.0)
    data = pd.DataFrame({"Setor": list(agg.keys()), "Peso": list(agg.values())})
    data = data[data["Peso"] > 0]
    if data.empty:
        st.caption("⚠️ Sem pesos válidos para a alocação setorial.")
        return False

    fig = px.pie(data, names="Setor", values="Peso", hole=0.45,
                 title="Composição setorial da carteira")
    fig.update_traces(textposition="inside", textinfo="percent+label")
    _fig_to_streamlit(fig, key=key)
    return True


def render_company_vs_peers_chart(company: str, peers, metrics=None,
                                  title: str | None = None, key: str | None = None) -> bool:
    """Empresa da carteira vs pares (de fora) em uma ou mais métricas (barras agrupadas)."""
    import plotly.graph_objects as go

    comp = _clean_tickers([company])
    if not comp:
        st.caption("⚠️ Empresa de referência ausente.")
        return False
    comp_tk = comp[0]
    peer_tks = [t for t in _clean_tickers(peers) if t != comp_tk]
    if not peer_tks:
        st.caption("⚠️ Sem pares para comparar.")
        return False

    cols = [c for c in (_resolve_metric(m) for m in (metrics or ["ROE", "P/L", "DY"])) if c]
    cols = list(dict.fromkeys(cols)) or ["ROE"]
    all_tks = [comp_tk] + peer_tks
    df = _multiplos_todos()
    if df.empty:
        st.caption("⚠️ Sem múltiplos no banco para a comparação.")
        return False

    fig = go.Figure()
    drew = False
    for col in cols:
        ser = _metric_series(col, all_tks).set_index("Ticker")[col] if col in df.columns else pd.Series(dtype=float)
        if ser.empty:
            continue
        xs = [t for t in all_tks if t in ser.index]
        ys = [float(ser[t]) for t in xs]
        colors = [_ACCENT if t == comp_tk else _PEER for t in xs]
        fig.add_bar(name=_LABEL.get(col, col), x=xs, y=ys, marker_color=colors)
        drew = True
    if not drew:
        st.caption("⚠️ Sem dados válidos para empresa vs pares.")
        return False
    fig.update_layout(barmode="group",
                      title=title or f"{comp_tk} vs pares")
    _fig_to_streamlit(fig, key=key)
    return True


def _session_price_df() -> pd.DataFrame:
    """Preços históricos da Criação de Portfólio, se disponíveis na sessão."""
    df = st.session_state.get("pb3_precos_all")
    return df if isinstance(df, pd.DataFrame) and not df.empty else pd.DataFrame()


def _pivot_price_df(px_df: pd.DataFrame) -> pd.DataFrame:
    """Normaliza um DF de preços (longo ticker/date/close OU wide) para wide."""
    if px_df is None or px_df.empty:
        return pd.DataFrame()
    work = px_df.copy()
    cols_lower = {c.lower(): c for c in work.columns}
    tcol = next((cols_lower[k] for k in ("ticker", "papel", "symbol") if k in cols_lower), None)
    dcol = next((cols_lower[k] for k in ("date", "data") if k in cols_lower), None)
    pcol = next((cols_lower[k] for k in ("close", "fechamento", "preco", "adj_close", "price")
                 if k in cols_lower), None)
    if tcol and dcol and pcol:
        work[tcol] = work[tcol].astype(str).str.upper().str.replace(".SA", "", regex=False)
        wide = work.pivot_table(index=dcol, columns=tcol, values=pcol, aggfunc="last")
    else:
        wide = work.select_dtypes(include="number")
        wide.columns = [str(c).upper().replace(".SA", "") for c in wide.columns]
    return wide


def _precos_wide(tickers, price_df: pd.DataFrame | None = None) -> pd.DataFrame:
    """
    Preços mensais em formato wide (índice=data, colunas=tickers). Fonte:
    market.* (histórico completo, sem depender da Criação de Portfólio); só cai
    na sessão/price_df se o banco não tiver a série pedida.
    """
    tks = _clean_tickers(tickers)
    if tks:
        try:
            db = _mr.load_precos_mensais(tuple(tks))
            if isinstance(db, pd.DataFrame) and not db.empty:
                db.columns = [str(c).upper() for c in db.columns]
                if any(t in db.columns for t in tks):
                    return db
        except Exception as exc:  # pragma: no cover - rede/banco
            logger.warning("charts: load_precos_mensais falhou: %s", exc)
    fonte = price_df if isinstance(price_df, pd.DataFrame) and not price_df.empty else _session_price_df()
    return _pivot_price_df(fonte)


def render_correlation_chart(tickers=None, price_df: pd.DataFrame | None = None,
                             title: str | None = None, key: str | None = None) -> bool:
    """Heatmap de correlação dos retornos diários (usa preços da sessão)."""
    import plotly.express as px

    wide = _precos_wide(tickers, price_df)
    if wide.empty:
        st.caption("⚠️ Sem séries de preço no banco — correlação indisponível.")
        return False

    tks = _clean_tickers(tickers)
    if tks:
        keep = [c for c in wide.columns if c in tks]
        if len(keep) >= 2:
            wide = wide[keep]
    if wide.shape[1] < 2:
        st.caption("⚠️ Poucas séries para calcular correlação (mínimo 2 ativos).")
        return False

    rets = wide.astype(float).pct_change().dropna(how="all")
    corr = rets.corr()
    if corr.empty or corr.shape[0] < 2:
        st.caption("⚠️ Não foi possível calcular a matriz de correlação.")
        return False

    fig = px.imshow(corr, text_auto=".2f", zmin=-1, zmax=1,
                    color_continuous_scale="RdBu_r", aspect="auto",
                    title=title or "Correlação dos retornos")
    _fig_to_streamlit(fig, key=key)
    return True


def render_performance_chart(tickers, price_df: pd.DataFrame | None = None,
                             title: str | None = None, key: str | None = None) -> bool:
    """Desempenho histórico normalizado (base 100) — usa preços da sessão."""
    import plotly.graph_objects as go

    wide = _precos_wide(tickers, price_df)
    if wide.empty:
        st.caption("⚠️ Sem séries de preço no banco — desempenho histórico indisponível.")
        return False

    tks = _clean_tickers(tickers)
    if tks:
        keep = [c for c in wide.columns if c in tks]
        if keep:
            wide = wide[keep]
    if wide.shape[1] < 1:
        st.caption("⚠️ Nenhum ticker com série de preço para o desempenho.")
        return False

    fig = go.Figure()
    for c in wide.columns[:10]:
        s = pd.to_numeric(wide[c], errors="coerce").dropna()
        if len(s) < 2 or s.iloc[0] == 0:
            continue
        fig.add_scatter(x=s.index, y=(s / s.iloc[0] * 100.0), mode="lines", name=str(c))
    fig.update_layout(title=title or "Desempenho histórico (base 100)")
    fig.update_yaxes(title="Índice (base 100)")
    _fig_to_streamlit(fig, key=key)
    return True


def render_financials_chart(tickers, title: str | None = None, key: str | None = None) -> bool:
    """
    Receita Líquida × Lucro Líquido histórico (DRE anual) — barras por ano, em
    R$ milhões, a partir de market.income_statements (load_demonstracoes).
    """
    import plotly.graph_objects as go

    tks = _clean_tickers(tickers)
    if not tks:
        st.caption("⚠️ Sem ticker para o gráfico de receita × lucro.")
        return False
    tk = tks[0]  # um ativo por gráfico de DRE
    try:
        dfd = _db.load_demonstracoes(tk)
    except Exception as exc:  # pragma: no cover - rede/banco
        logger.warning("charts: load_demonstracoes(%s) falhou: %s", tk, exc)
        dfd = pd.DataFrame()
    if dfd is None or dfd.empty or "Data" not in dfd.columns:
        st.caption(f"⚠️ Sem DRE no banco para {tk}.")
        return False

    d = dfd.copy()
    d["Ano"] = pd.to_datetime(d["Data"], errors="coerce").dt.year
    d = d.dropna(subset=["Ano"]).sort_values("Ano")
    rec = pd.to_numeric(d.get("Receita_Liquida"), errors="coerce")
    luc = pd.to_numeric(d.get("Lucro_Liquido"), errors="coerce")
    if rec.dropna().empty and luc.dropna().empty:
        st.caption(f"⚠️ Sem receita/lucro líquido no banco para {tk}.")
        return False

    anos = d["Ano"].astype(int)
    fig = go.Figure()
    if not rec.dropna().empty:
        fig.add_bar(name="Receita líquida", x=anos, y=rec / 1e6, marker_color=_PEER,
                    text=(rec / 1e6).map(lambda v: f"{v:,.0f}" if pd.notna(v) else ""),
                    textposition="outside", cliponaxis=False)
    if not luc.dropna().empty:
        fig.add_bar(name="Lucro líquido", x=anos, y=luc / 1e6, marker_color=_ACCENT,
                    text=(luc / 1e6).map(lambda v: f"{v:,.0f}" if pd.notna(v) else ""),
                    textposition="outside", cliponaxis=False)
    fig.update_layout(barmode="group",
                      title=title or f"{tk} — Receita × Lucro líquido (R$ milhões)")
    fig.update_yaxes(title="R$ milhões")
    _fig_to_streamlit(fig, key=key)
    return True


# ── Whitelist / dispatcher ────────────────────────────────────────────────────

CHART_REGISTRY = {
    "comparison", "comparacao", "comparação",
    "ranking",
    "dividend", "dividendos", "dy",
    "sector_allocation", "alocacao_setorial", "setor",
    "company_vs_peers", "empresa_vs_pares", "vs_pares",
    "correlation", "correlacao", "correlação",
    "performance", "desempenho",
    "financials", "dre", "receita_lucro", "receita_x_lucro", "receita", "lucro",
}


def render_charts_from_directives(directives, meta: dict | None = None) -> int:
    """
    Renderiza os gráficos pedidos pela LLM. Cada diretiva é validada contra a
    whitelist; tipos desconhecidos são ignorados e erros são isolados por gráfico.
    Retorna o número de gráficos efetivamente desenhados.
    """
    if not directives:
        return 0
    meta = meta or {}
    model   = meta.get("model") or {}
    weights = meta.get("weights") or {}
    port_tks = meta.get("portfolio_tickers") or []

    drawn = 0
    for i, d in enumerate(directives):
        if not isinstance(d, dict):
            continue
        tipo = str(d.get("tipo", "")).strip().lower()
        if tipo not in CHART_REGISTRY:
            continue
        metric  = d.get("metrica") or d.get("metric")
        tickers = d.get("tickers") or d.get("ticker")
        titulo  = d.get("titulo") or d.get("title")
        ckey = f"apb3_chart_{i}_{tipo}"
        try:
            ok = False
            if tipo in ("comparison", "comparacao", "comparação"):
                ok = render_comparison_chart(metric or "ROE", tickers or port_tks, titulo, key=ckey)
            elif tipo == "ranking":
                ok = render_ranking_chart(metric or "ROE", tickers, titulo, key=ckey)
            elif tipo in ("dividend", "dividendos", "dy"):
                ok = render_dividend_chart(tickers or port_tks, titulo, key=ckey)
            elif tipo in ("sector_allocation", "alocacao_setorial", "setor"):
                ok = render_sector_allocation_chart(model, weights, key=ckey)
            elif tipo in ("company_vs_peers", "empresa_vs_pares", "vs_pares"):
                company = d.get("empresa") or d.get("company")
                if not company and tickers:
                    cl = _clean_tickers(tickers)
                    company = cl[0] if cl else None
                metrics = d.get("metricas") or d.get("metrics") or [metric] if metric else None
                ok = render_company_vs_peers_chart(company, tickers, metrics, titulo, key=ckey)
            elif tipo in ("correlation", "correlacao", "correlação"):
                ok = render_correlation_chart(tickers or port_tks, title=titulo, key=ckey)
            elif tipo in ("performance", "desempenho"):
                ok = render_performance_chart(tickers or port_tks, title=titulo, key=ckey)
            elif tipo in ("financials", "dre", "receita_lucro", "receita_x_lucro",
                          "receita", "lucro"):
                ok = render_financials_chart(tickers or port_tks, title=titulo, key=ckey)
            drawn += 1 if ok else 0
        except Exception as exc:  # isola falha por gráfico
            logger.warning("charts: falha ao renderizar diretiva %s: %s", d, exc)
            st.caption("⚠️ Não foi possível gerar um dos gráficos solicitados.")
    return drawn
