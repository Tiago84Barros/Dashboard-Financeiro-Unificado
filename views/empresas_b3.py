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

import html as _html
import json
import re

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
import yfinance as yf

import core.b3_db as _db
from core.config import settings
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
/* Avançada */
.av-filter-chip {
    display:inline-block;background:#1A1F2E;border:1px solid #2D3748;
    border-radius:20px;padding:4px 12px;font-size:0.72rem;color:#CBD5E0;
    margin:2px;cursor:pointer;transition:all .15s;
}
.rank-bar-wrap { background:#1E2533;border-radius:6px;height:8px;overflow:hidden;margin-top:4px; }
.rank-bar-fill { height:8px;border-radius:6px; }
/* Portfólio */
.score-badge {
    display:inline-flex;align-items:center;justify-content:center;
    width:44px;height:44px;border-radius:50%;font-size:0.85rem;font-weight:800;
    flex-shrink:0;
}
/* IA */
.ia-block {
    background:#0F1117;border:1px solid #1E2533;border-radius:12px;
    padding:16px 18px;margin-bottom:12px;
}
.ia-block-title { font-size:0.68rem;font-weight:800;text-transform:uppercase;
                  letter-spacing:.1em;color:#4A5568;margin-bottom:8px; }
.ia-block-body  { font-size:0.82rem;color:#CBD5E0;line-height:1.6; }
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
# TAB 4 — ANÁLISE AVANÇADA
# ══════════════════════════════════════════════════════════════════════════════

_INDS_DISP = {
    "P/L":               ("P/L", False),    # (coluna_db, menor_é_melhor)
    "P/VP":              ("P/VP", False),
    "DY (%)":            ("DY", True),
    "ROE (%)":           ("ROE", True),
    "ROIC (%)":          ("ROIC", True),
    "Margem Líq (%)":    ("Margem_Liquida", True),
    "Margem Op (%)":     ("Margem_Operacional", True),
    "EV/EBIT":           ("EV_EBIT", False),
    "P/FCO":             ("P_FCO", False),
    "Liq. Corrente":     ("Liquidez_Corrente", True),
    "Endividamento":     ("Endividamento_Total", False),
}


def _tab_avancada(df_set: pd.DataFrame) -> None:
    st.markdown("### 📊 Análise Avançada")
    st.caption("Filtre por setor e compare múltiplos fundamentalistas entre empresas.")

    if df_set.empty:
        st.info("Banco de dados não configurado — configure `SUPABASE_DB_URL` no `.env`.")
        return

    # ── Filtros ────────────────────────────────────────────────────────────────
    c1, c2, c3 = st.columns(3)
    with c1:
        setores = ["Todos"] + sorted(df_set["SETOR"].dropna().unique().tolist())
        setor_sel = st.selectbox("Setor", setores, key="av_setor")
    df_f = df_set if setor_sel == "Todos" else df_set[df_set["SETOR"] == setor_sel]
    with c2:
        subs = ["Todos"] + sorted(df_f["SUBSETOR"].dropna().unique().tolist())
        sub_sel = st.selectbox("Subsetor", subs, key="av_sub")
    df_f = df_f if sub_sel == "Todos" else df_f[df_f["SUBSETOR"] == sub_sel]
    with c3:
        segs = ["Todos"] + sorted(df_f["SEGMENTO"].dropna().unique().tolist())
        seg_sel = st.selectbox("Segmento", segs, key="av_seg")
    df_f = df_f if seg_sel == "Todos" else df_f[df_f["SEGMENTO"] == seg_sel]

    tickers_filtrados = df_f["ticker"].str.upper().str.replace(".SA","",regex=False).unique().tolist()
    st.caption(f"{len(tickers_filtrados)} empresas no filtro selecionado.")

    # ── Carregar múltiplos ─────────────────────────────────────────────────────
    with st.spinner("Carregando múltiplos…"):
        mult_todos = _db.load_multiplos_todos()

    if mult_todos.empty:
        st.warning("Tabela `multiplos` não encontrada no banco.")
        return

    mult = mult_todos[mult_todos["Ticker"].isin(tickers_filtrados)].copy()
    # join com nome da empresa
    nome_map = df_f.set_index("ticker")["nome_empresa"].to_dict()
    mult["Empresa"] = mult["Ticker"].map(nome_map).fillna(mult["Ticker"])

    if mult.empty:
        st.info("Nenhum dado de múltiplos para o filtro selecionado.")
        return

    st.divider()

    # ── Tabela de ranking ──────────────────────────────────────────────────────
    st.markdown("#### 📋 Tabela Comparativa")
    ind_disp_label = st.selectbox(
        "Ordenar por", list(_INDS_DISP.keys()), index=3, key="av_ord"
    )
    col_ord, ord_asc = _INDS_DISP[ind_disp_label]

    cols_tabela = ["Ticker", "Empresa", "P/L", "P/VP", "DY", "ROE",
                   "ROIC", "Margem_Liquida", "EV_EBIT", "Liquidez_Corrente"]
    cols_exist = [c for c in cols_tabela if c in mult.columns]
    tbl = mult[cols_exist].copy()
    if col_ord in tbl.columns:
        tbl = tbl.sort_values(col_ord, ascending=ord_asc, na_position="last")

    tbl_show = tbl.rename(columns={
        "DY": "DY%", "ROE": "ROE%", "ROIC": "ROIC%",
        "Margem_Liquida": "Marg.Liq%", "EV_EBIT": "EV/EBIT",
        "Liquidez_Corrente": "Liq.Corr",
    })
    pct_cols = [c for c in ["DY%","ROE%","ROIC%","Marg.Liq%"] if c in tbl_show.columns]
    for c in pct_cols:
        tbl_show[c] = tbl_show[c] * 100  # converte para %
    st.dataframe(
        tbl_show.set_index("Ticker").style
            .format({c: "{:.1f}%" for c in pct_cols}, na_rep="—")
            .format({c: "{:.1f}x" for c in ["P/L","P/VP","EV/EBIT","Liq.Corr"]
                     if c in tbl_show.columns}, na_rep="—")
            .background_gradient(subset=pct_cols, cmap="RdYlGn",
                                  vmin=0, vmax=30)
            .background_gradient(subset=[c for c in ["P/L","P/VP"] if c in tbl_show.columns],
                                  cmap="RdYlGn_r", vmin=0, vmax=30),
        use_container_width=True,
        height=420,
    )

    st.divider()

    # ── Gráfico de barras — ranking por indicador ──────────────────────────────
    st.markdown("#### 📊 Ranking por Indicador")
    ca, cb = st.columns([2, 1])
    with ca:
        ind_bar = st.selectbox("Indicador", list(_INDS_DISP.keys()), index=3, key="av_bar")
    with cb:
        top_n = st.number_input("Top N empresas", min_value=5, max_value=50,
                                value=20, step=5, key="av_topn")

    col_bar, asc_bar = _INDS_DISP[ind_bar]
    if col_bar in mult.columns:
        bar_df = mult[["Ticker", "Empresa", col_bar]].dropna().copy()
        bar_df = bar_df.sort_values(col_bar, ascending=asc_bar).head(int(top_n))
        pct_bar = "%" if ind_bar in ("DY (%)","ROE (%)","ROIC (%)","Margem Líq (%)","Margem Op (%)") else ""
        if pct_bar:
            bar_df[col_bar] = bar_df[col_bar] * 100
        fig = px.bar(
            bar_df, x="Ticker", y=col_bar, text=col_bar,
            color=col_bar,
            color_continuous_scale=["#FC5C7D","#F6C90E","#00C896"] if not asc_bar
                                  else ["#00C896","#F6C90E","#FC5C7D"],
            labels={col_bar: ind_bar},
            height=380,
        )
        fig.update_traces(
            texttemplate=f"%{{text:.1f}}{pct_bar}",
            textposition="outside",
        )
        fig.update_layout(
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            font_color="#9CA3AF", coloraxis_showscale=False,
            margin={"t": 20, "b": 40, "l": 0, "r": 0},
            xaxis={"showgrid": False},
            yaxis={"showgrid": True, "gridcolor": "#1E2533"},
        )
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False},
                        key="av_bar_fig")

    st.divider()

    # ── Scatter — cruzamento de dois indicadores ───────────────────────────────
    st.markdown("#### 🔵 Análise de Dispersão")
    sc1, sc2, sc3 = st.columns(3)
    with sc1:
        eixo_x = st.selectbox("Eixo X", list(_INDS_DISP.keys()), index=0, key="av_sx")
    with sc2:
        eixo_y = st.selectbox("Eixo Y", list(_INDS_DISP.keys()), index=2, key="av_sy")
    with sc3:
        eixo_s = st.selectbox("Tamanho", list(_INDS_DISP.keys()), index=3, key="av_ss")

    col_x = _INDS_DISP[eixo_x][0]
    col_y = _INDS_DISP[eixo_y][0]
    col_s = _INDS_DISP[eixo_s][0]
    cols_sc = [c for c in [col_x, col_y, col_s, "Ticker", "Empresa"] if c in mult.columns]
    sc_df = mult[cols_sc].dropna(subset=[c for c in [col_x, col_y] if c in cols_sc]).copy()

    if len(sc_df) > 1:
        # Linhas de mediana
        med_x = sc_df[col_x].median() if col_x in sc_df else None
        med_y = sc_df[col_y].median() if col_y in sc_df else None

        # converter pct
        for c_label, c_col in [(eixo_x, col_x), (eixo_y, col_y)]:
            if "%" in c_label and c_col in sc_df.columns:
                sc_df[c_col] = sc_df[c_col] * 100
        if col_s in sc_df.columns and "%" in eixo_s:
            sc_df[col_s] = sc_df[col_s].abs() * 100

        size_col = col_s if col_s in sc_df.columns else None
        fig_sc = px.scatter(
            sc_df, x=col_x, y=col_y,
            size=size_col if size_col else None,
            text="Ticker",
            labels={col_x: eixo_x, col_y: eixo_y},
            color_discrete_sequence=[_COR_INF],
            height=440,
        )
        fig_sc.update_traces(textposition="top center", textfont_size=9)
        if med_x is not None:
            pct_x = 100 if "%" in eixo_x else 1
            fig_sc.add_vline(x=med_x * pct_x, line_dash="dash",
                             line_color="#4A5568", annotation_text="Mediana X")
        if med_y is not None:
            pct_y = 100 if "%" in eixo_y else 1
            fig_sc.add_hline(y=med_y * pct_y, line_dash="dash",
                             line_color="#4A5568", annotation_text="Mediana Y")
        fig_sc.update_layout(
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            font_color="#9CA3AF",
            margin={"t": 20, "b": 40, "l": 0, "r": 0},
            xaxis={"showgrid": True, "gridcolor": "#1E2533"},
            yaxis={"showgrid": True, "gridcolor": "#1E2533"},
        )
        st.plotly_chart(fig_sc, use_container_width=True,
                        config={"displayModeBar": False}, key="av_scatter")
    else:
        st.info("Dados insuficientes para dispersão com o filtro atual.")

    st.divider()

    # ── Histórico de múltiplos — empresa única ─────────────────────────────────
    st.markdown("#### 📈 Evolução Histórica de Múltiplos")
    col_tk, col_ind = st.columns(2)
    with col_tk:
        tk_hist = st.selectbox(
            "Empresa", sorted(mult["Ticker"].dropna().tolist()), key="av_hist_tk"
        )
    with col_ind:
        ind_hist = st.multiselect(
            "Indicadores", list(_INDS_DISP.keys()),
            default=["ROE (%)", "ROIC (%)"],
            key="av_hist_ind",
        )

    if tk_hist and ind_hist:
        with st.spinner(f"Carregando histórico {tk_hist}…"):
            h_df = _db.load_multiplos_historico(tk_hist)
        if h_df.empty:
            st.info("Sem histórico disponível para este ticker.")
        else:
            fig_h = go.Figure()
            for lbl in ind_hist:
                c_h, _ = _INDS_DISP[lbl]
                if c_h in h_df.columns:
                    vals = pd.to_numeric(h_df[c_h], errors="coerce")
                    if "%" in lbl:
                        vals = vals * 100
                    fig_h.add_trace(go.Scatter(
                        x=h_df["Data"], y=vals,
                        name=lbl, mode="lines+markers",
                        line={"width": 2},
                    ))
            fig_h.update_layout(
                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                font_color="#9CA3AF", height=340, legend={"orientation": "h"},
                margin={"t": 20, "b": 20, "l": 0, "r": 0},
                xaxis={"showgrid": False},
                yaxis={"showgrid": True, "gridcolor": "#1E2533"},
            )
            st.plotly_chart(fig_h, use_container_width=True,
                            config={"displayModeBar": False}, key="av_hist_fig")


# ══════════════════════════════════════════════════════════════════════════════
# TAB 5 — RANKING & PORTFÓLIO
# ══════════════════════════════════════════════════════════════════════════════

_SCORE_PESOS = {
    "ROE":              (0.25, True),    # (peso, maior_é_melhor)
    "ROIC":             (0.25, True),
    "DY":               (0.20, True),
    "Margem_Liquida":   (0.15, True),
    "Margem_Operacional": (0.10, True),
    "Endividamento_Total": (0.05, False),  # menor é melhor
}


def _calcular_score(df: pd.DataFrame) -> pd.DataFrame:
    """Calcula score composto 0-100 por percentil rank ponderado."""
    out = df.copy()
    total_rank = pd.Series(0.0, index=df.index)
    total_peso = 0.0
    for col, (peso, maior_melhor) in _SCORE_PESOS.items():
        if col not in df.columns:
            continue
        s = pd.to_numeric(df[col], errors="coerce")
        valid = s.notna()
        if valid.sum() < 2:
            continue
        pct = s.rank(pct=True, na_option="keep")
        if not maior_melhor:
            pct = 1 - pct
        total_rank += pct.fillna(0.0) * peso
        total_peso += peso
    if total_peso > 0:
        out["Score"] = (total_rank / total_peso) * 100
    else:
        out["Score"] = np.nan
    return out


def _tab_portfolio(df_set: pd.DataFrame) -> None:
    st.markdown("### 🏆 Ranking & Portfólio")
    st.caption(
        "Score composto baseado em ROE, ROIC, DY, Margens e Endividamento. "
        "**Não constitui recomendação de investimento.**"
    )

    if df_set.empty:
        st.info("Banco de dados não configurado.")
        return

    with st.spinner("Carregando múltiplos…"):
        mult_todos = _db.load_multiplos_todos()

    if mult_todos.empty:
        st.warning("Tabela `multiplos` não encontrada no banco.")
        return

    # ── Filtros ────────────────────────────────────────────────────────────────
    c1, c2 = st.columns([2, 1])
    with c1:
        setores = ["Todos"] + sorted(df_set["SETOR"].dropna().unique().tolist())
        setor_sel = st.selectbox("Filtrar por Setor", setores, key="pf_setor")
    with c2:
        top_n = st.number_input("Top N por setor", 3, 20, 5, key="pf_topn")

    df_f2 = df_set if setor_sel == "Todos" else df_set[df_set["SETOR"] == setor_sel]
    tks_filtrados = df_f2["ticker"].str.upper().str.replace(".SA","",regex=False).tolist()

    mult = mult_todos[mult_todos["Ticker"].isin(tks_filtrados)].copy()
    if mult.empty:
        st.info("Sem dados para o filtro selecionado.")
        return

    nome_map  = df_f2.set_index("ticker")["nome_empresa"].to_dict()
    setor_map = df_f2.set_index("ticker")["SETOR"].to_dict()
    mult["Empresa"] = mult["Ticker"].map(nome_map).fillna(mult["Ticker"])
    mult["Setor"]   = mult["Ticker"].map(setor_map).fillna("—")

    # ── Calcular score ─────────────────────────────────────────────────────────
    mult = _calcular_score(mult)
    mult_sorted = mult.sort_values("Score", ascending=False, na_position="last")

    # ── KPIs gerais ────────────────────────────────────────────────────────────
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Empresas analisadas", len(mult_sorted))
    k2.metric("Score médio", f"{mult_sorted['Score'].mean():.1f}" if not mult_sorted.empty else "—")
    k3.metric(
        "Maior DY médio",
        f"{mult_sorted['DY'].mean()*100:.1f}%" if "DY" in mult_sorted.columns else "—"
    )
    k4.metric(
        "ROE médio",
        f"{mult_sorted['ROE'].mean()*100:.1f}%" if "ROE" in mult_sorted.columns else "—"
    )
    st.divider()

    # ── Tabela de ranking global ───────────────────────────────────────────────
    st.markdown("#### 🥇 Ranking Global")
    top_global = mult_sorted.head(int(top_n * 3) if setor_sel == "Todos" else int(top_n * 5))

    disp_cols = ["Ticker", "Empresa", "Setor", "Score"]
    pct_extra = []
    for c in ["DY", "ROE", "ROIC", "Margem_Liquida"]:
        if c in top_global.columns:
            top_global[c] = pd.to_numeric(top_global[c], errors="coerce") * 100
            disp_cols.append(c)
            pct_extra.append(c)
    fmt_map = {c: "{:.1f}%" for c in pct_extra}
    fmt_map["Score"] = "{:.1f}"

    st.dataframe(
        top_global[disp_cols].reset_index(drop=True).style
            .format(fmt_map, na_rep="—")
            .bar(subset=["Score"], color=["#FC5C7D", "#00C896"], vmin=0, vmax=100),
        use_container_width=True,
        height=380,
    )

    st.divider()

    # ── Top por setor ──────────────────────────────────────────────────────────
    st.markdown("#### 🏅 Melhores por Setor")
    setores_presentes = mult_sorted["Setor"].dropna().unique().tolist()
    setores_presentes = [s for s in setores_presentes if s != "—"]

    cols_setor = st.columns(min(3, max(1, len(setores_presentes))))
    for i, setor in enumerate(setores_presentes):
        col_idx = i % len(cols_setor)
        top_s = mult_sorted[mult_sorted["Setor"] == setor].head(int(top_n))
        with cols_setor[col_idx]:
            st.markdown(
                f'<div class="b3-sector-hdr">{_html.escape(setor)}</div>',
                unsafe_allow_html=True,
            )
            for _, row in top_s.iterrows():
                score = row.get("Score", 0)
                cor = _COR_POS if score >= 60 else (_COR_ALT if score >= 40 else _COR_NEG)
                st.markdown(
                    f'<div style="display:flex;align-items:center;gap:10px;'
                    f'margin-bottom:8px;padding:8px;background:#12151E;'
                    f'border-radius:8px;border:1px solid #1E2533;">'
                    f'<img src="{_logo_url(row["Ticker"])}" width="28" height="28" '
                    f'style="border-radius:6px;background:rgba(255,255,255,.06);" '
                    f'onerror="this.style.display=\'none\'">'
                    f'<div style="flex:1;min-width:0;">'
                    f'<div style="font-size:0.82rem;font-weight:800;color:#E2E8F0;">'
                    f'{_html.escape(str(row["Ticker"]))}</div>'
                    f'<div style="font-size:0.66rem;color:#718096;overflow:hidden;'
                    f'text-overflow:ellipsis;white-space:nowrap;">'
                    f'{_html.escape(str(row["Empresa"]))}</div>'
                    f'</div>'
                    f'<span style="font-size:0.78rem;font-weight:800;color:{cor};">'
                    f'{score:.0f}</span>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

    st.divider()

    # ── Distribuição de score ──────────────────────────────────────────────────
    st.markdown("#### 📊 Distribuição de Score por Setor")
    if not mult_sorted.empty and "Score" in mult_sorted.columns:
        fig_box = px.box(
            mult_sorted.dropna(subset=["Score", "Setor"]),
            x="Setor", y="Score", color="Setor",
            points="outliers",
            height=380,
            color_discrete_sequence=px.colors.qualitative.Set2,
        )
        fig_box.update_layout(
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            font_color="#9CA3AF", showlegend=False,
            margin={"t": 20, "b": 60, "l": 0, "r": 0},
            xaxis={"showgrid": False, "tickangle": -30},
            yaxis={"showgrid": True, "gridcolor": "#1E2533"},
        )
        st.plotly_chart(fig_box, use_container_width=True,
                        config={"displayModeBar": False}, key="pf_box")

    st.divider()

    # ── Alocação sugerida (pie) ────────────────────────────────────────────────
    st.markdown("#### 🥧 Alocação Sugerida — Top Empresas")
    st.caption(
        "Distribuição baseada no score. Selecione quantas empresas incluir:"
    )
    top_pie = st.slider("Número de empresas", 5, min(30, len(mult_sorted)), 10,
                        key="pf_pie")
    top_alloc = mult_sorted.head(int(top_pie))[["Ticker", "Score", "Setor"]].copy()
    top_alloc["Score"] = top_alloc["Score"].clip(lower=1)
    total_score = top_alloc["Score"].sum()
    top_alloc["Peso%"] = (top_alloc["Score"] / total_score * 100).round(1)

    ca2, cb2 = st.columns([1, 1])
    with ca2:
        fig_pie = px.pie(
            top_alloc, values="Peso%", names="Ticker",
            color_discrete_sequence=px.colors.qualitative.Set3,
            height=340,
        )
        fig_pie.update_traces(textposition="inside", textinfo="percent+label")
        fig_pie.update_layout(
            paper_bgcolor="rgba(0,0,0,0)", font_color="#9CA3AF",
            margin={"t": 20, "b": 0, "l": 0, "r": 0},
            showlegend=False,
        )
        st.plotly_chart(fig_pie, use_container_width=True,
                        config={"displayModeBar": False}, key="pf_pie_fig")
    with cb2:
        st.dataframe(
            top_alloc[["Ticker", "Setor", "Peso%"]].reset_index(drop=True),
            use_container_width=True, height=340,
        )

    # ── Salvar seleção para aba IA ─────────────────────────────────────────────
    if st.button("💾 Usar esta seleção na Análise IA", key="pf_salvar"):
        st.session_state["b3_portfolio_tickers"] = top_alloc["Ticker"].tolist()
        st.success(
            f"✅ {len(top_alloc)} tickers salvos. "
            "Vá para a aba **🤖 Análise IA** para analisar."
        )


# ══════════════════════════════════════════════════════════════════════════════
# TAB 6 — ANÁLISE COM IA
# ══════════════════════════════════════════════════════════════════════════════

def _chamar_openai(prompt_sistema: str, prompt_usuario: str,
                   modelo: str = "gpt-4o-mini") -> str:
    """Chama OpenAI e retorna o texto de resposta. Lança exceção em caso de erro."""
    from openai import OpenAI  # lazy import
    client = OpenAI(api_key=settings.openai_api_key)
    resp = client.chat.completions.create(
        model=modelo,
        messages=[
            {"role": "system", "content": prompt_sistema},
            {"role": "user",   "content": prompt_usuario},
        ],
        temperature=0.3,
        max_tokens=2000,
        response_format={"type": "json_object"},
    )
    return resp.choices[0].message.content or ""


def _resumo_financeiro(ticker: str) -> str:
    """Monta um resumo textual dos dados financeiros de uma empresa para enviar à IA."""
    mult = _db.load_multiplos(ticker)
    df_dre = _db.load_demonstracoes(ticker)

    linhas: list[str] = [f"=== {ticker} ==="]

    if not mult.empty:
        def _m(k): return f"{float(mult[k])*100:.1f}%" if k in mult.index and pd.notna(mult[k]) else "N/D"
        def _mx(k): return f"{float(mult[k]):.2f}x" if k in mult.index and pd.notna(mult[k]) else "N/D"
        linhas += [
            f"DY: {_m('DY')}  |  ROE: {_m('ROE')}  |  ROIC: {_m('ROIC')}",
            f"P/L: {_mx('P/L')}  |  P/VP: {_mx('P/VP')}  |  EV/EBIT: {_mx('EV_EBIT')}",
            f"Margem Líquida: {_m('Margem_Liquida')}  |  Margem Op: {_m('Margem_Operacional')}",
            f"Endividamento: {_m('Endividamento_Total')}  |  Liq. Corrente: {_mx('Liquidez_Corrente')}",
        ]
    else:
        linhas.append("Múltiplos: sem dados")

    if not df_dre.empty:
        ult = df_dre.iloc[-1]
        cagr_rec  = _cagr(df_dre, "Receita_Liquida")
        cagr_luc  = _cagr(df_dre, "Lucro_Liquido")
        cagr_ebit = _cagr(df_dre, "EBIT")
        linhas.append(f"Anos de histórico: {len(df_dre)}")
        if cagr_rec  is not None: linhas.append(f"CAGR Receita: {cagr_rec*100:.1f}%a.a.")
        if cagr_luc  is not None: linhas.append(f"CAGR Lucro: {cagr_luc*100:.1f}%a.a.")
        if cagr_ebit is not None: linhas.append(f"CAGR EBIT: {cagr_ebit*100:.1f}%a.a.")
    else:
        linhas.append("DRE: sem dados")

    return "\n".join(linhas)


_PROMPT_SYS_ANALISE = """
Você é um analista de ações brasileiro especializado em análise fundamentalista de empresas B3.
Analise os dados financeiros fornecidos e retorne um JSON com exatamente estas chaves:
{
  "resumo": "Parágrafo curto com visão geral da empresa baseada nos números",
  "pontos_fortes": ["lista de até 3 pontos positivos com base nos dados"],
  "riscos": ["lista de até 3 riscos ou fragilidades identificadas"],
  "valuation": "Breve comentário sobre se parece cara, justa ou barata pelos múltiplos",
  "qualidade_negocio": "Nota qualitativa: Excelente / Boa / Regular / Fraca — justificada",
  "monitorar": ["lista de até 2 indicadores para acompanhar"]
}
Baseie-se APENAS nos dados fornecidos. Não invente informações. Seja objetivo e conciso.
""".strip()


@st.cache_data(ttl=1800, show_spinner=False)
def _analise_ia_cached(ticker: str, modelo: str) -> str:
    resumo = _resumo_financeiro(ticker)
    return _chamar_openai(
        _PROMPT_SYS_ANALISE,
        f"Analise a seguinte empresa B3:\n\n{resumo}",
        modelo=modelo,
    )


def _tab_ia(df_set: pd.DataFrame) -> None:
    st.markdown("### 🤖 Análise com Inteligência Artificial")

    tem_openai = bool(settings.openai_api_key)

    if not tem_openai:
        st.warning(
            "**OpenAI API Key não configurada.** "
            "Para usar esta aba, adicione `OPENAI_API_KEY` ao `.env` "
            "ou aos secrets do Streamlit Cloud."
        )
        with st.expander("Como configurar"):
            st.code("OPENAI_API_KEY=sk-...", language="bash")
            st.caption(
                "Acesse [platform.openai.com](https://platform.openai.com/api-keys) "
                "para obter a chave."
            )
        return

    st.caption(
        "A IA analisa os múltiplos e histórico de DRE de cada empresa. "
        "**Não constitui recomendação de investimento.**"
    )

    # ── Seleção de tickers ─────────────────────────────────────────────────────
    tickers_pre = st.session_state.get("b3_portfolio_tickers", [])
    if df_set.empty:
        todas_tickers: list[str] = []
    else:
        todas_tickers = sorted(
            df_set["ticker"].str.upper().str.replace(".SA","",regex=False).tolist()
        )

    col_tk2, col_mod = st.columns([3, 1])
    with col_tk2:
        tickers_sel = st.multiselect(
            "Selecione as empresas para analisar",
            options=todas_tickers,
            default=[t for t in tickers_pre if t in todas_tickers][:10],
            max_selections=10,
            key="ia_tickers",
        )
    with col_mod:
        modelo = st.selectbox(
            "Modelo",
            ["gpt-4o-mini", "gpt-4o", "gpt-4-turbo"],
            index=0,
            key="ia_modelo",
        )

    if not tickers_sel:
        st.info(
            "Selecione empresas acima ou use a aba **🏆 Ranking & Portfólio** "
            "para salvar uma seleção automática."
        )
        return

    if st.button(f"🔍 Analisar {len(tickers_sel)} empresa(s)", type="primary",
                 key="ia_analisar"):
        st.session_state["ia_resultados"] = {}
        erros: list[str] = []

        prog = st.progress(0, text="Iniciando análise…")
        for i, tk in enumerate(tickers_sel):
            prog.progress((i + 1) / len(tickers_sel), text=f"Analisando {tk}…")
            try:
                resultado_json = _analise_ia_cached(tk, modelo)
                st.session_state["ia_resultados"][tk] = json.loads(resultado_json)
            except Exception as exc:
                erros.append(f"{tk}: {exc}")
                st.session_state["ia_resultados"][tk] = {"_erro": str(exc)}
        prog.empty()
        if erros:
            st.warning(f"Erros em {len(erros)} empresa(s): " + " | ".join(erros))

    # ── Exibir resultados ──────────────────────────────────────────────────────
    resultados: dict = st.session_state.get("ia_resultados", {})
    if not resultados:
        return

    _QUAL_COR = {
        "Excelente": _COR_POS, "Boa": _COR_INF,
        "Regular": _COR_ALT,   "Fraca": _COR_NEG,
    }

    for tk in [t for t in tickers_sel if t in resultados]:
        res = resultados[tk]
        if "_erro" in res:
            with st.expander(f"❌ {tk} — erro"):
                st.error(res["_erro"])
            continue

        qual = str(res.get("qualidade_negocio", ""))
        qual_cor = next(
            (_QUAL_COR[k] for k in _QUAL_COR if k in qual), _COR_NEU
        )
        qual_curta = next((k for k in _QUAL_COR if k in qual), qual[:10])

        with st.expander(f"📈 {tk} — {qual_curta}", expanded=(len(tickers_sel) == 1)):
            hdr1, hdr2 = st.columns([3, 1])
            with hdr1:
                st.markdown(
                    f'<div style="display:flex;align-items:center;gap:10px;margin-bottom:8px;">'
                    f'<img src="{_logo_url(tk)}" width="36" height="36" '
                    f'style="border-radius:8px;background:rgba(255,255,255,.06);" '
                    f'onerror="this.style.display=\'none\'">'
                    f'<span style="font-size:1.2rem;font-weight:800;color:#E2E8F0;">{tk}</span>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
            with hdr2:
                st.markdown(
                    f'<div style="text-align:center;padding:8px;background:#12151E;'
                    f'border-radius:8px;border:2px solid {qual_cor};">'
                    f'<div style="font-size:0.60rem;color:#4A5568;text-transform:uppercase;">Qualidade</div>'
                    f'<div style="font-size:0.90rem;font-weight:800;color:{qual_cor};">{qual_curta}</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

            # Resumo
            if "resumo" in res:
                st.markdown(
                    f'<div class="ia-block">'
                    f'<div class="ia-block-title">📋 Resumo</div>'
                    f'<div class="ia-block-body">{_html.escape(res["resumo"])}</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

            # Valuation
            if "valuation" in res:
                st.markdown(
                    f'<div class="ia-block">'
                    f'<div class="ia-block-title">💰 Valuation</div>'
                    f'<div class="ia-block-body">{_html.escape(res["valuation"])}</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

            c_pos, c_neg = st.columns(2)
            with c_pos:
                if res.get("pontos_fortes"):
                    st.markdown(
                        f'<div class="ia-block" style="border-left:3px solid {_COR_POS};">'
                        f'<div class="ia-block-title">✅ Pontos Fortes</div>'
                        + "".join(
                            f'<div class="ia-block-body" style="margin-bottom:4px;">• {_html.escape(p)}</div>'
                            for p in res["pontos_fortes"]
                        )
                        + "</div>",
                        unsafe_allow_html=True,
                    )
            with c_neg:
                if res.get("riscos"):
                    st.markdown(
                        f'<div class="ia-block" style="border-left:3px solid {_COR_NEG};">'
                        f'<div class="ia-block-title">⚠️ Riscos</div>'
                        + "".join(
                            f'<div class="ia-block-body" style="margin-bottom:4px;">• {_html.escape(p)}</div>'
                            for p in res["riscos"]
                        )
                        + "</div>",
                        unsafe_allow_html=True,
                    )

            if res.get("monitorar"):
                st.markdown(
                    f'<div class="ia-block">'
                    f'<div class="ia-block-title">👁️ Monitorar</div>'
                    + "".join(
                        f'<div class="ia-block-body" style="margin-bottom:4px;">📌 {_html.escape(m)}</div>'
                        for m in res["monitorar"]
                    )
                    + "</div>",
                    unsafe_allow_html=True,
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

    tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
        "🏢 Empresas por Setor",
        "🔍 Análise de Empresa",
        "💵 Dividendos",
        "📊 Análise Avançada",
        "🏆 Ranking & Portfólio",
        "🤖 Análise IA",
    ])

    with tab1:
        st.markdown("<br>", unsafe_allow_html=True)
        _tab_empresas(df_set)

    with tab2:
        st.markdown("<br>", unsafe_allow_html=True)
        _tab_analise(df_set)

    with tab3:
        _tab_dividendos()

    with tab4:
        st.markdown("<br>", unsafe_allow_html=True)
        _tab_avancada(df_set)

    with tab5:
        st.markdown("<br>", unsafe_allow_html=True)
        _tab_portfolio(df_set)

    with tab6:
        st.markdown("<br>", unsafe_allow_html=True)
        _tab_ia(df_set)
