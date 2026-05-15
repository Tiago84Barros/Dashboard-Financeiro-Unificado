"""
views/empresas_b3.py  — Dashboard Fundamentalista B3
Port das 3 páginas principais do App 1 (Dashboard-Financeiro):
  Tab 1 — Empresas por Setor   (listagem por setor + logos)
  Tab 2 — Análise de Empresa   (drilldown: crescimento, DRE, múltiplos)
  Tab 3 — Dividendos           (histórico yfinance, DY anual, simulador)

Banco de dados:  core.b3_db  →  SUPABASE_DB_URL_B3 (ou DATABASE_URL como fallback)
Preços:          yfinance     (sem dependência de DB)
Logos:           thefintz/icones-b3 CDN (público, sem auth)
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
import yfinance as yf

import core.b3_db as _db
from core.utils import fmt_moeda

# ── Constantes ────────────────────────────────────────────────────────────────
_CDN = "https://raw.githubusercontent.com/thefintz/icones-b3/main/icones"
_COR_POS = "#00C896"
_COR_NEG = "#FC5C7D"
_COR_ALT = "#F6C90E"
_COR_INF = "#4A9EFF"
_COR_NEU = "#9CA3AF"

# ── CSS ───────────────────────────────────────────────────────────────────────
_CSS = """
<style>
.b3-sector-hdr {
    font-size:0.60rem;font-weight:800;text-transform:uppercase;
    letter-spacing:.12em;color:#4A5568;border-bottom:1px solid #1E2533;
    padding-bottom:6px;margin:18px 0 10px;
}
.b3-card {
    background:#12151E;border:1px solid #1E2533;border-radius:12px;
    padding:14px 14px 10px;height:100%;transition:border-color .2s;
}
.b3-card:hover { border-color:rgba(0,200,150,.35); }
.b3-card-logo { width:36px;height:36px;border-radius:8px;object-fit:contain;
                background:rgba(255,255,255,.06);padding:3px;flex-shrink:0; }
.b3-card-ticker { font-size:0.88rem;font-weight:800;color:#E2E8F0; }
.b3-card-nome   { font-size:0.70rem;color:#718096;margin-top:1px;
                  overflow:hidden;text-overflow:ellipsis;white-space:nowrap; }
.b3-card-tag    { font-size:0.62rem;color:#4A5568; }
.b3-ind-card {
    background:#12151E;border:1px solid #1E2533;border-radius:10px;
    padding:12px 14px;margin-bottom:6px;
}
.b3-ind-label { font-size:0.60rem;font-weight:700;text-transform:uppercase;
                letter-spacing:.09em;color:#4A5568;margin-bottom:4px; }
.b3-ind-value { font-size:1.35rem;font-weight:800;line-height:1.1; }
.b3-ind-sub   { font-size:0.66rem;color:#4A5568;margin-top:3px; }
.div-card {
    background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.1);
    border-radius:14px;padding:14px 18px;margin-bottom:10px;
}
.div-card-title { font-size:11px;font-weight:700;opacity:.6;text-transform:uppercase;
                  letter-spacing:.06em;margin-bottom:4px; }
.div-card-value { font-size:26px;font-weight:900;line-height:1.1; }
.div-card-sub   { font-size:11px;opacity:.5;margin-top:3px; }
</style>
"""


# ══════════════════════════════════════════════════════════════════════════════
# Helpers de formatação
# ══════════════════════════════════════════════════════════════════════════════

def _fv(v, d: int = 2) -> str:
    try:
        if v is None or (isinstance(v, float) and (np.isnan(v) or np.isinf(v))):
            return "—"
        x = float(v)
        if abs(x) >= 1e9: return f"R$ {x/1e9:.2f}B"
        if abs(x) >= 1e6: return f"R$ {x/1e6:.2f}M"
        if abs(x) >= 1e3: return f"R$ {x/1e3:.1f}K"
        return f"{x:,.{d}f}"
    except Exception:
        return "—"


def _fp(v, d: int = 2) -> str:
    try:
        if v is None or (isinstance(v, float) and (np.isnan(v) or np.isinf(v))):
            return "—"
        return f"{float(v):.{d}f}%"
    except Exception:
        return "—"


def _fg(v) -> str:
    try:
        if v is None or (isinstance(v, float) and (np.isnan(v) or np.isinf(v))):
            return "—"
        return f"{float(v)*100:+.2f}%"
    except Exception:
        return "—"


def _logo_url(ticker: str) -> str:
    tk = ticker.strip().upper().replace(".SA", "")
    return f"{_CDN}/{tk}.png"


def _cor_val(v, invert: bool = False) -> str:
    try:
        x = float(v)
        if np.isnan(x) or np.isinf(x): return _COR_NEU
        pos = x > 0
        if invert: pos = not pos
        return _COR_POS if pos else _COR_NEG
    except Exception:
        return _COR_NEU


# ══════════════════════════════════════════════════════════════════════════════
# CAGR via regressão em log
# ══════════════════════════════════════════════════════════════════════════════

def _cagr(df: pd.DataFrame, col: str) -> float | None:
    try:
        if df is None or df.empty or col not in df.columns or "Data" not in df.columns:
            return None
        tmp = df[["Data", col]].copy()
        tmp["Data"] = pd.to_datetime(tmp["Data"], errors="coerce")
        tmp[col]    = pd.to_numeric(tmp[col], errors="coerce")
        tmp = tmp.dropna().query(f"`{col}` > 0").sort_values("Data")
        if len(tmp) < 2:
            return None
        X  = (tmp["Data"] - tmp["Data"].iloc[0]).dt.days / 365.25
        yL = np.log(tmp[col].values.astype(float))
        slope, _ = np.polyfit(X.values.astype(float), yL, deg=1)
        g = float(np.exp(slope) - 1.0)
        return g if np.isfinite(g) else None
    except Exception:
        return None


def _last_val(df: pd.DataFrame, col: str) -> float | None:
    try:
        if df is None or df.empty or col not in df.columns:
            return None
        s = pd.to_numeric(df[col], errors="coerce").dropna()
        return float(s.iloc[-1]) if not s.empty else None
    except Exception:
        return None


# ══════════════════════════════════════════════════════════════════════════════
# yfinance helpers
# ══════════════════════════════════════════════════════════════════════════════

@st.cache_data(ttl=600, show_spinner=False)
def _preco_atual(ticker: str) -> float | None:
    tk = ticker.strip().upper().replace(".SA", "")
    for var in [f"{tk}.SA", tk]:
        try:
            p = yf.Ticker(var).fast_info.last_price
            if p and float(p) > 0:
                return float(p)
        except Exception:
            pass
    return None


@st.cache_data(ttl=3600, show_spinner=False)
def _dividendos_yf(tickers: tuple) -> dict[str, pd.DataFrame]:
    result: dict[str, pd.DataFrame] = {}
    for tk_raw in tickers:
        tk = tk_raw.strip().upper().replace(".SA", "")
        for var in [f"{tk}.SA", tk]:
            try:
                s = yf.Ticker(var).dividends
                if s is not None and not s.empty:
                    idx = pd.to_datetime(s.index)
                    try:
                        idx = idx.tz_localize(None)
                    except Exception:
                        idx = idx.tz_convert(None)
                    df = pd.DataFrame({"Data": idx, "Dividendo": s.values, "Ticker": tk})
                    result[tk] = df.sort_values("Data")
                    break
            except Exception:
                continue
    return result


@st.cache_data(ttl=3600, show_spinner=False)
def _precos_mensais(tickers: tuple, start: str = "2018-01-01") -> dict[str, pd.Series]:
    result: dict[str, pd.Series] = {}
    for tk_raw in tickers:
        tk = tk_raw.strip().upper().replace(".SA", "")
        for var in [f"{tk}.SA", tk]:
            try:
                raw = yf.Ticker(var).history(start=start, auto_adjust=True)
                if raw is not None and not raw.empty and "Close" in raw.columns:
                    s = raw["Close"].copy()
                    try:
                        s.index = pd.to_datetime(s.index).tz_localize(None)
                    except Exception:
                        s.index = pd.to_datetime(s.index).tz_convert(None)
                    result[tk] = s.resample("ME").last().dropna()
                    break
            except Exception:
                continue
    return result


# ══════════════════════════════════════════════════════════════════════════════
# TAB 1 — Empresas por Setor
# ══════════════════════════════════════════════════════════════════════════════

def _tab_empresas(df_set: pd.DataFrame) -> None:
    busca = st.text_input("🔍 Buscar ticker (ex.: PETR4)", key="b3_busca",
                          placeholder="Digite e pressione Enter")

    if busca.strip():
        tk = busca.strip().upper().replace(".SA", "")
        st.session_state["b3_ticker_sel"] = tk
        st.info(f"Ticker **{tk}** selecionado. Vá para a aba **🔍 Análise de Empresa**.")
        return

    if df_set.empty:
        st.warning(
            "Tabela `setores` não encontrada no banco configurado. "
            "Configure `SUPABASE_DB_URL_B3` no `.env` ou nos secrets do Streamlit Cloud."
        )
        st.info("A aba **💵 Dividendos** funciona sem banco (apenas yfinance).")
        return

    for setor, grupo in df_set.groupby("SETOR"):
        st.markdown(f'<div class="b3-sector-hdr">{setor}</div>', unsafe_allow_html=True)
        grupo = grupo.reset_index(drop=True)
        for i in range(0, len(grupo), 4):
            cols = st.columns(4, gap="small")
            for j, (_, row) in enumerate(grupo.iloc[i:i+4].iterrows()):
                tk   = row["ticker"]
                nome = (row["nome_empresa"] or tk)[:28]
                sub  = row.get("SUBSETOR", "") or ""
                seg  = row.get("SEGMENTO",  "") or ""
                logo = _logo_url(tk)
                with cols[j]:
                    st.markdown(
                        f'<div class="b3-card">'
                        f'  <div style="display:flex;align-items:center;gap:10px;margin-bottom:8px;">'
                        f'    <img src="{logo}" class="b3-card-logo"'
                        f'         onerror="this.style.display=\'none\'">'
                        f'    <div style="overflow:hidden;">'
                        f'      <div class="b3-card-ticker">{tk}</div>'
                        f'      <div class="b3-card-nome">{nome}</div>'
                        f'    </div>'
                        f'  </div>'
                        f'  <div class="b3-card-tag">{sub}'
                        f'    {(" · " + seg) if seg else ""}'
                        f'  </div>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )
                    if st.button("Analisar", key=f"b3_btn_{tk}_{i}_{j}",
                                 use_container_width=True):
                        st.session_state["b3_ticker_sel"] = tk
                        st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# TAB 2 — Análise de Empresa
# ══════════════════════════════════════════════════════════════════════════════

def _ind_card(label: str, valor: str, sub: str, cor: str) -> str:
    return (
        f'<div class="b3-ind-card">'
        f'<div class="b3-ind-label">{label}</div>'
        f'<div class="b3-ind-value" style="color:{cor};">{valor}</div>'
        f'<div class="b3-ind-sub">{sub}</div>'
        f'</div>'
    )


def _build_indicators(mult: pd.Series) -> list[tuple]:
    def _g(key: str):
        if mult.empty: return None
        kn = key.lower().replace(" ", "").replace("_", "").replace(".", "")
        for k, v in mult.items():
            if kn in str(k).lower().replace(" ", "").replace("_", "").replace(".", ""):
                try:
                    x = float(v)
                    return x if np.isfinite(x) else None
                except Exception:
                    continue
        return None

    def _add(inds, lbl, v, sub, pct=True, inv=False, fmt_fn=None):
        if v is None:
            val, cor = "—", _COR_NEU
        else:
            val = fmt_fn(v) if fmt_fn else (_fp(v) if pct else f"{v:.2f}")
            cor = _cor_val(v, invert=inv)
        inds.append((lbl, val, sub, cor))

    inds: list[tuple] = []
    _add(inds, "Margem Líquida",     _g("Margem_Liquida"),         "% Lucro/Receita")
    _add(inds, "Margem Operacional", _g("Margem_Operacional"),      "% EBIT/Receita")
    _add(inds, "ROE",                _g("ROE"),                     "Retorno s/ PL")
    _add(inds, "ROA",                _g("ROA"),                     "Retorno s/ Ativos")
    _add(inds, "ROIC",               _g("ROIC"),                    "Retorno s/ Capital")
    _add(inds, "Dividend Yield",     _g("DY"),                      "Dividendos/Preço")
    _add(inds, "P/VP",
         _g("P_VP") or _g("PVP"),
         "Preço/Val. Patrimonial", pct=False, fmt_fn=lambda v: f"{v:.2f}x")
    _add(inds, "Payout",             _g("Payout"),                  "% Lucro distribuído")
    _add(inds, "P/L",
         _g("P_L") or _g("PL"),
         "Preço/Lucro", pct=False, fmt_fn=lambda v: f"{v:.1f}x")
    _add(inds, "Endividamento",      _g("Endividamento_Total"),     "Dív. Total/PL",    inv=True)
    _add(inds, "Alavancagem Fin.",   _g("Alavancagem_Financeira"),  "Ativos/PL",        inv=True)
    _add(inds, "Liquidez Corrente",
         _g("Liquidez_Corrente"),
         "Ativo Circ/Passivo Circ", pct=False, fmt_fn=lambda v: f"{v:.2f}x")
    return inds


def _tab_analise(df_set: pd.DataFrame) -> None:
    default_tk = st.session_state.get("b3_ticker_sel", "")
    col_inp, col_btn = st.columns([4, 1])
    with col_inp:
        ticker_raw = st.text_input(
            "Ticker da empresa", value=default_tk,
            key="b3_ticker_input",
            placeholder="Ex.: PETR4, BBAS3, WEGE3",
        )
    with col_btn:
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("Analisar", type="primary", use_container_width=True,
                     key="b3_btn_analisar") and ticker_raw.strip():
            st.session_state["b3_ticker_sel"] = (
                ticker_raw.strip().upper().replace(".SA", "")
            )
            st.rerun()

    tk = st.session_state.get("b3_ticker_sel", "").strip().upper().replace(".SA", "")
    if not tk:
        st.info("Digite um ticker acima e clique em **Analisar**.", icon="🔍")
        return

    # ── Header ────────────────────────────────────────────────────────────────
    preco     = _preco_atual(tk)
    info_row  = (df_set[df_set["ticker"] == tk].iloc[0]
                 if not df_set.empty and tk in df_set["ticker"].values else None)
    nome_emp  = info_row["nome_empresa"] if info_row is not None else tk
    setor     = info_row["SETOR"]    if info_row is not None else "—"
    subsetor  = info_row["SUBSETOR"] if info_row is not None else "—"

    col_logo, col_info, col_preco = st.columns([1, 5, 2])
    with col_logo:
        st.markdown(
            f'<img src="{_logo_url(tk)}" '
            f'style="width:64px;height:64px;border-radius:12px;object-fit:contain;'
            f'background:rgba(255,255,255,.06);padding:6px;margin-top:6px;" '
            f'onerror="this.style.display=\'none\'">',
            unsafe_allow_html=True,
        )
    with col_info:
        st.markdown(
            f'<h2 style="font-size:1.60rem;font-weight:800;color:#E2E8F0;margin:0 0 4px;">'
            f'{tk} — {nome_emp}</h2>'
            f'<div style="font-size:0.78rem;color:#718096;">{setor} · {subsetor}</div>',
            unsafe_allow_html=True,
        )
    with col_preco:
        preco_str = f"R$ {preco:,.2f}" if preco else "—"
        st.markdown(
            f'<div style="text-align:right;padding-top:8px;">'
            f'<div style="font-size:1.60rem;font-weight:800;color:{_COR_POS};">{preco_str}</div>'
            f'<div style="font-size:0.68rem;color:#4A5568;">Cotação (yfinance)</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

    st.markdown("<br>", unsafe_allow_html=True)

    # ── Dados do banco ────────────────────────────────────────────────────────
    with st.spinner(f"Carregando dados de {tk}…"):
        df_fin = _db.load_demonstracoes(tk)
        mult   = _db.load_multiplos(tk)

    sem_banco = df_fin.empty and mult.empty
    if sem_banco:
        st.warning(
            "Dados financeiros não encontrados. "
            "Configure `SUPABASE_DB_URL_B3` no `.env` para acessar DRE e múltiplos.",
            icon="⚠️",
        )
    else:
        # ── CAGR cards ────────────────────────────────────────────────────────
        st.markdown(
            '<div style="font-size:0.75rem;font-weight:700;color:#E2E8F0;'
            'margin-bottom:8px;">📈 Crescimento Médio Anual (CAGR)</div>',
            unsafe_allow_html=True,
        )
        c1, c2, c3, c4 = st.columns(4, gap="small")
        for col, lbl, field in [
            (c1, "Receita Líquida", "Receita_Liquida"),
            (c2, "EBIT",            "EBIT"),
            (c3, "Lucro Líquido",   "Lucro_Liquido"),
            (c4, "Dividendos",      "Dividendos"),
        ]:
            g = _cagr(df_fin, field)
            with col:
                st.markdown(
                    _ind_card(lbl, _fg(g), "Regressão log histórico",
                              _cor_val(g) if g is not None else _COR_NEU),
                    unsafe_allow_html=True,
                )

        st.markdown("<br>", unsafe_allow_html=True)

        # ── Valores recentes ──────────────────────────────────────────────────
        st.markdown(
            '<div style="font-size:0.75rem;font-weight:700;color:#E2E8F0;'
            'margin-bottom:8px;">📋 Último Exercício Disponível</div>',
            unsafe_allow_html=True,
        )
        r1, r2, r3, r4 = st.columns(4, gap="small")
        for col, lbl, field, inv in [
            (r1, "Receita Líquida",  "Receita_Liquida",  False),
            (r2, "EBIT",             "EBIT",             False),
            (r3, "Lucro Líquido",    "Lucro_Liquido",    False),
            (r4, "Dívida Líquida",   "Divida_Liquida",   True),
        ]:
            v = _last_val(df_fin, field)
            with col:
                st.markdown(
                    _ind_card(lbl, _fv(v), "Último registro no banco",
                              _cor_val(v, invert=inv) if v is not None else _COR_NEU),
                    unsafe_allow_html=True,
                )

        st.markdown("<br>", unsafe_allow_html=True)

        # ── Gráfico DRE histórico ─────────────────────────────────────────────
        if not df_fin.empty and "Data" in df_fin.columns:
            st.markdown(
                '<div style="font-size:0.75rem;font-weight:700;color:#E2E8F0;'
                'margin-bottom:8px;">📊 Demonstrações Financeiras — Histórico</div>',
                unsafe_allow_html=True,
            )
            candidatos = [
                ("Receita_Liquida", "Receita Líquida"), ("EBIT", "EBIT"),
                ("Lucro_Liquido", "Lucro Líquido"),
                ("Patrimonio_Liquido", "Patrimônio Líquido"),
                ("Divida_Liquida", "Dívida Líquida"), ("Divida_Total", "Dívida Total"),
                ("Ativo_Total", "Ativo Total"), ("Dividendos", "Dividendos"),
            ]
            disp = [(c, l) for c, l in candidatos if c in df_fin.columns]
            if disp:
                opcoes = [l for _, l in disp]
                deflt  = [x for x in ("Receita Líquida", "Lucro Líquido") if x in opcoes]
                sel    = st.multiselect("Indicadores", opcoes,
                                        default=deflt or opcoes[:2],
                                        key=f"b3_dre_sel_{tk}")
                if sel:
                    lbl2col = {l: c for c, l in disp}
                    cols_sel = [lbl2col[l] for l in sel if l in lbl2col]
                    plot = df_fin[["Data"] + cols_sel].copy()
                    for c in cols_sel:
                        plot[c] = pd.to_numeric(plot[c], errors="coerce")
                    melt = plot.melt("Data", value_vars=cols_sel,
                                    var_name="Indicador", value_name="Valor")
                    melt["Indicador"] = melt["Indicador"].map({c: l for c, l in disp})
                    fig = px.line(melt, x="Data", y="Valor", color="Indicador",
                                  markers=True,
                                  color_discrete_sequence=[
                                      _COR_POS, _COR_INF, _COR_ALT, _COR_NEG,
                                      "#9B59B6", "#E67E22", _COR_NEU,
                                  ])
                    fig.update_layout(
                        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                        font_color=_COR_NEU, height=340,
                        margin={"t": 10, "b": 10, "l": 0, "r": 0},
                        legend={"orientation": "h", "y": -0.18,
                                "bgcolor": "rgba(0,0,0,0)", "font": {"size": 11}},
                        yaxis={"showgrid": True, "gridcolor": "#1E2533"},
                        xaxis={"showgrid": False},
                    )
                    st.plotly_chart(fig, use_container_width=True,
                                    config={"displayModeBar": False},
                                    key=f"b3_dre_chart_{tk}")

    # ── Múltiplos ─────────────────────────────────────────────────────────────
    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown(
        '<div style="font-size:0.75rem;font-weight:700;color:#E2E8F0;'
        'margin-bottom:8px;">🔢 Múltiplos Fundamentalistas</div>',
        unsafe_allow_html=True,
    )
    indicadores = _build_indicators(mult if not sem_banco else pd.Series(dtype=object))
    if any(v != "—" for _, v, _, _ in indicadores):
        for i in range(0, len(indicadores), 4):
            chunk = indicadores[i:i+4]
            cols  = st.columns(4, gap="small")
            for j, (lbl, val, sub, cor) in enumerate(chunk):
                with cols[j]:
                    st.markdown(_ind_card(lbl, val, sub, cor), unsafe_allow_html=True)
    else:
        st.caption("Múltiplos não disponíveis — configure `SUPABASE_DB_URL_B3`.")


# ══════════════════════════════════════════════════════════════════════════════
# TAB 3 — Dividendos
# ══════════════════════════════════════════════════════════════════════════════

def _tab_dividendos() -> None:
    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown(
        '<p style="font-size:0.80rem;color:#9CA3AF;margin-bottom:16px;">'
        'Dados via <b style="color:#CBD5E0;">yfinance</b> — funciona sem banco. '
        'Informe os tickers desejados abaixo.</p>',
        unsafe_allow_html=True,
    )

    sel_default = st.session_state.get("b3_ticker_sel", "")
    tickers_raw = st.text_input(
        "Tickers (separados por vírgula)",
        value=sel_default,
        placeholder="Ex.: MXRF11, BBAS3, ITUB4, PETR4",
        key="b3_div_tickers",
    )
    tickers_list = [t.strip().upper().replace(".SA", "")
                    for t in tickers_raw.split(",") if t.strip()]
    if not tickers_list:
        st.info("Informe pelo menos um ticker acima.", icon="💵")
        return

    tickers_t = tuple(tickers_list)

    with st.spinner(f"Buscando dividendos de {len(tickers_list)} ativo(s)…"):
        divs_all   = _dividendos_yf(tickers_t)
        precos_all = _precos_mensais(tickers_t)

    d1, d2, d3, d4, d5 = st.tabs([
        "📅 Histórico", "📊 DY Anual", "🔄 Comparativo", "🗓️ Calendário", "🧮 Simulador",
    ])

    # ── Histórico ─────────────────────────────────────────────────────────────
    with d1:
        frames = [df for df in divs_all.values() if not df.empty]
        if not frames:
            st.info("Sem dados de dividendos encontrados.")
        else:
            df_hist = pd.concat(frames).sort_values("Data", ascending=False).copy()
            df_hist["Data"]     = df_hist["Data"].dt.strftime("%d/%m/%Y")
            df_hist["Dividendo (R$)"] = df_hist["Dividendo"].apply(
                lambda v: f"R$ {v:.4f}" if pd.notna(v) else "—"
            )
            st.dataframe(df_hist[["Data", "Ticker", "Dividendo (R$)"]],
                         hide_index=True, use_container_width=True)

    # ── DY Anual ──────────────────────────────────────────────────────────────
    with d2:
        rows = []
        for tk in tickers_list:
            if tk not in divs_all or tk not in precos_all:
                continue
            df_d = divs_all[tk].copy()
            df_d["Ano"] = pd.to_datetime(df_d["Data"]).dt.year
            anual_div   = df_d.groupby("Ano")["Dividendo"].sum()
            prices_df   = precos_all[tk].reset_index()
            prices_df.columns = ["Data", "Preco"]
            prices_df["Ano"]  = pd.to_datetime(prices_df["Data"]).dt.year
            anual_p     = prices_df.groupby("Ano")["Preco"].mean()
            dy_anual    = (anual_div / anual_p * 100).dropna()
            for ano, dy in dy_anual[dy_anual > 0].items():
                rows.append({"Ano": int(ano), "DY (%)": round(dy, 2), "Ticker": tk})
        if not rows:
            st.info("Sem dados de DY anual disponíveis.")
        else:
            df_dy = pd.DataFrame(rows)
            fig   = px.bar(df_dy, x="Ano", y="DY (%)", color="Ticker", barmode="group",
                           color_discrete_sequence=[_COR_POS, _COR_INF, _COR_ALT,
                                                    _COR_NEG, "#9B59B6"])
            fig.update_layout(
                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                font_color=_COR_NEU, height=320,
                margin={"t": 10, "b": 10, "l": 0, "r": 0},
                xaxis={"showgrid": False},
                yaxis={"showgrid": True, "gridcolor": "#1E2533", "ticksuffix": "%"},
                legend={"bgcolor": "rgba(0,0,0,0)"},
            )
            st.plotly_chart(fig, use_container_width=True,
                            config={"displayModeBar": False}, key="b3_dy_anual")

    # ── Comparativo DY 12M ────────────────────────────────────────────────────
    with d3:
        cutoff  = pd.Timestamp.today() - pd.DateOffset(months=12)
        rows_c  = []
        for tk in tickers_list:
            p = None
            try:
                p = yf.Ticker(f"{tk}.SA").fast_info.last_price
                if not p or float(p) <= 0: p = None
            except Exception:
                pass
            div12 = float(divs_all[tk][divs_all[tk]["Data"] >= cutoff]["Dividendo"].sum()
                          ) if tk in divs_all else 0.0
            dy    = div12 / float(p) * 100 if p and p > 0 and div12 > 0 else 0.0
            rows_c.append({"Ticker": tk, "DY 12M (%)": round(dy, 2),
                            "Divs 12M (R$)": round(div12, 4),
                            "Preço (R$)": round(float(p), 2) if p else None})
        df_cmp = pd.DataFrame(rows_c).sort_values("DY 12M (%)", ascending=False)
        fig_c  = px.bar(df_cmp, x="Ticker", y="DY 12M (%)",
                        color="DY 12M (%)", text="DY 12M (%)",
                        color_continuous_scale=["#FC5C7D", _COR_ALT, _COR_POS])
        fig_c.update_traces(texttemplate="%{text:.2f}%", textposition="outside")
        fig_c.update_layout(
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            font_color=_COR_NEU, height=320,
            margin={"t": 30, "b": 10, "l": 0, "r": 0},
            xaxis={"showgrid": False},
            yaxis={"showgrid": True, "gridcolor": "#1E2533", "ticksuffix": "%"},
            showlegend=False, coloraxis_showscale=False,
        )
        st.plotly_chart(fig_c, use_container_width=True,
                        config={"displayModeBar": False}, key="b3_dy_comp")
        st.dataframe(df_cmp, hide_index=True, use_container_width=True)

    # ── Calendário (12 meses) ─────────────────────────────────────────────────
    with d4:
        cutoff_cal = pd.Timestamp.today() - pd.DateOffset(months=12)
        cal_rows   = []
        for tk, df_d in divs_all.items():
            if df_d.empty: continue
            rec = df_d[df_d["Data"] >= cutoff_cal].copy()
            for _, row in rec.iterrows():
                cal_rows.append({
                    "Data": row["Data"].strftime("%d/%m/%Y"),
                    "Ticker": tk,
                    "Dividendo (R$)": f"R$ {row['Dividendo']:.4f}",
                })
        if not cal_rows:
            st.info("Sem proventos nos últimos 12 meses.")
        else:
            st.dataframe(
                pd.DataFrame(cal_rows).sort_values("Data", ascending=False),
                hide_index=True, use_container_width=True,
            )

    # ── Simulador de Renda Passiva ────────────────────────────────────────────
    with d5:
        st.markdown("**Estime a renda mensal com base no DY trailing 12M.**")
        patrimonio = st.number_input(
            "Patrimônio investido (R$)", min_value=1_000.0,
            value=100_000.0, step=5_000.0, key="b3_sim_patr",
        )
        reinvestir = st.checkbox("Projetar reinvestimento (10 anos)",
                                 value=False, key="b3_sim_reinv")

        cutoff_sim = pd.Timestamp.today() - pd.DateOffset(months=12)
        dys = []
        for tk in tickers_list:
            if tk not in divs_all: continue
            div12 = float(divs_all[tk][divs_all[tk]["Data"] >= cutoff_sim]["Dividendo"].sum())
            try:
                p = yf.Ticker(f"{tk}.SA").fast_info.last_price
                if p and float(p) > 0 and div12 > 0:
                    dys.append(div12 / float(p))
            except Exception:
                pass
        dy_medio  = float(np.mean(dys)) if dys else 0.06
        renda_aa  = patrimonio * dy_medio
        renda_mm  = renda_aa / 12

        ca, cb = st.columns(2)
        with ca:
            st.markdown(
                f'<div class="div-card"><div class="div-card-title">Renda Anual Estimada</div>'
                f'<div class="div-card-value" style="color:{_COR_POS};">'
                f'{fmt_moeda(renda_aa)}</div>'
                f'<div class="div-card-sub">DY médio: {dy_medio*100:.2f}% a.a.</div></div>',
                unsafe_allow_html=True,
            )
        with cb:
            st.markdown(
                f'<div class="div-card"><div class="div-card-title">Renda Mensal Estimada</div>'
                f'<div class="div-card-value" style="color:{_COR_ALT};">'
                f'{fmt_moeda(renda_mm)}</div>'
                f'<div class="div-card-sub">Renda anual ÷ 12</div></div>',
                unsafe_allow_html=True,
            )

        if reinvestir:
            patr = [patrimonio]
            for _ in range(10):
                patr.append(patr[-1] * (1 + dy_medio))
            fig_s = go.Figure(go.Scatter(
                x=list(range(1, 11)), y=patr[1:],
                mode="lines+markers",
                line={"color": _COR_POS, "width": 2.5},
                fill="tozeroy", fillcolor="rgba(0,200,150,.07)",
                hovertemplate="Ano %{x}<br>R$ %{y:,.0f}<extra></extra>",
            ))
            fig_s.update_layout(
                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                font_color=_COR_NEU, height=260,
                margin={"t": 10, "b": 10, "l": 0, "r": 0},
                xaxis={"showgrid": False, "title": "Anos"},
                yaxis={"showgrid": True, "gridcolor": "#1E2533",
                       "tickprefix": "R$ ", "tickformat": ",.0f"},
            )
            st.plotly_chart(fig_s, use_container_width=True,
                            config={"displayModeBar": False}, key="b3_sim_proj")
            st.caption(
                f"Reinvestimento mensal ao DY de {dy_medio*100:.2f}% a.a. "
                "— sem considerar variação de preço ou inflação."
            )


# ══════════════════════════════════════════════════════════════════════════════
# RENDER PRINCIPAL
# ══════════════════════════════════════════════════════════════════════════════

def render() -> None:
    st.markdown(_CSS, unsafe_allow_html=True)

    st.markdown(
        '<div style="display:flex;align-items:center;gap:12px;margin-bottom:4px;">'
        '<span style="font-size:2rem">📊</span>'
        '<h1 style="font-size:2rem;font-weight:800;color:#E2E8F0;margin:0;">'
        'Dashboard Fundamentalista B3</h1>'
        '</div>'
        '<p style="font-size:0.80rem;color:#9CA3AF;margin-bottom:20px;">'
        'Análise de empresas B3: demonstrações financeiras, múltiplos e dividendos. '
        '<b style="color:#CBD5E0;">Não constitui recomendação de investimento.</b>'
        '</p>',
        unsafe_allow_html=True,
    )

    with st.spinner("Carregando lista de empresas…"):
        df_set = _db.load_setores()

    if df_set.empty:
        st.caption(
            "⚠️ Banco do App 1 não configurado — configure `SUPABASE_DB_URL_B3` "
            "no `.env` ou nos secrets do Streamlit Cloud. "
            "Aba Dividendos funciona sem banco."
        )

    tab1, tab2, tab3 = st.tabs([
        "🏢 Empresas por Setor",
        "🔍 Análise de Empresa",
        "💵 Dividendos",
    ])

    with tab1:
        st.markdown("<br>", unsafe_allow_html=True)
        _tab_empresas(df_set)

    with tab2:
        st.markdown("<br>", unsafe_allow_html=True)
        _tab_analise(df_set)

    with tab3:
        _tab_dividendos()
