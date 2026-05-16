"""
views/empresas_b3.py  — Dashboard Fundamentalista B3
  Tab 1 — Empresas por Setor   (listagem por setor + logos)
  Tab 2 — Análise de Empresa   (drilldown: crescimento, DRE, múltiplos)

Banco de dados:  core.b3_db  →  SUPABASE_DB_URL_B3 (ou DATABASE_URL como fallback)
Preços:          yfinance     (sem dependência de DB)
Logos:           thefintz/icones-b3 CDN (público, sem auth)
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.express as px
import streamlit as st
import yfinance as yf

import core.b3_db as _db
import core.data_reconciliacao as _recon

# ── Constantes ────────────────────────────────────────────────────────────────
_CDN = "https://raw.githubusercontent.com/thefintz/icones-b3/main/icones"

# Limites máximos razoáveis em escala decimal para cada campo %.
# Se o valor do BD exceder o limite, assume-se que foi armazenado em escala raw %
# (ex: 7.13 em vez de 0.0713) e o valor é usado sem multiplicar por 100.
_MAX_DECIMAL_PCT: dict[str, float] = {
    "Margem_Liquida":     1.0,   # margens reais: -100% a +100%
    "Margem_Operacional": 1.0,
    "DY":                 1.0,   # yield > 100% é impossível
    "ROA":                1.0,   # ROA > 100% é praticamente impossível
    "ROE":                5.0,   # bancos podem ter ROE > 100%
    "ROIC":               3.0,
    "Payout":             5.0,   # pode superar 100% em anos de distribuição especial
}
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


# ══════════════════════════════════════════════════════════════════════════════
# yfinance helpers — dividendos e fundamentals
# ══════════════════════════════════════════════════════════════════════════════

@st.cache_data(ttl=3600, show_spinner=False)
def _yf_dividendos_anuais(ticker: str) -> pd.DataFrame:
    """Retorna DataFrame [Data, Dividendos] com totais anuais via yfinance."""
    tk = ticker.strip().upper().replace(".SA", "")
    for var in [f"{tk}.SA", tk]:
        try:
            divs = yf.Ticker(var).dividends
            if divs is not None and not divs.empty:
                if hasattr(divs.index, "tz") and divs.index.tz is not None:
                    divs.index = divs.index.tz_localize(None)
                anuais = divs.resample("YE").sum()
                anuais = anuais[anuais > 0]
                if not anuais.empty:
                    return pd.DataFrame({"Data": anuais.index, "Dividendos": anuais.values})
        except Exception:
            pass
    return pd.DataFrame()


@st.cache_data(ttl=600, show_spinner=False)
def _yf_multiplos_dividendos(ticker: str) -> dict[str, float]:
    """
    Retorna DY e Payout via yfinance com múltiplos caminhos de fallback.
    DY em escala decimal (0.05 = 5%), compatível com escala BD.
    """
    tk = ticker.strip().upper().replace(".SA", "")
    resultado: dict[str, float] = {}

    for var in [f"{tk}.SA", tk]:
        try:
            ytk  = yf.Ticker(var)
            info = ytk.info or {}

            # ── DY: 3 caminhos em ordem de confiabilidade ──────────────────
            dy: float | None = None

            # Caminho 1: campo direto do info
            for key in ("dividendYield", "trailingAnnualDividendYield"):
                raw = info.get(key)
                if raw is not None:
                    try:
                        v = float(raw)
                        if np.isfinite(v) and v >= 0:
                            dy = v
                            break
                    except (TypeError, ValueError):
                        pass

            # Caminho 2: trailingAnnualDividendRate / preço atual
            if dy is None:
                rate  = info.get("trailingAnnualDividendRate")
                price = info.get("currentPrice") or info.get("regularMarketPrice")
                if rate and price:
                    try:
                        r, p = float(rate), float(price)
                        if np.isfinite(r) and np.isfinite(p) and p > 0 and r > 0:
                            dy = r / p
                    except (TypeError, ValueError):
                        pass

            # Caminho 3: somar últimos 12 meses de dividendos históricos / preço
            if dy is None:
                try:
                    divs = ytk.dividends
                    if divs is not None and not divs.empty:
                        last12 = float(divs.last("365D").sum())
                        price  = info.get("currentPrice") or info.get("regularMarketPrice")
                        if price and last12 > 0:
                            p = float(price)
                            if np.isfinite(p) and p > 0:
                                dy = last12 / p
                except Exception:
                    pass

            if dy is not None:
                resultado["DY"] = dy

            # ── Payout ─────────────────────────────────────────────────────
            raw_po = info.get("payoutRatio")
            if raw_po is not None:
                try:
                    v = float(raw_po)
                    if np.isfinite(v) and 0 <= v <= 2:   # até 200% é razoável
                        resultado["Payout"] = v
                except (TypeError, ValueError):
                    pass

            if resultado:
                break  # encontrou dados, não precisa tentar sem .SA

        except Exception:
            continue

    return resultado


# ══════════════════════════════════════════════════════════════════════════════
# TAB 1 — Empresas por Setor
# ══════════════════════════════════════════════════════════════════════════════

def _tab_empresas(df_set: pd.DataFrame) -> None:
    busca = st.text_input("🔍 Buscar ticker (ex.: PETR4)", key="b3_busca",
                          placeholder="Digite e pressione Enter")

    if busca.strip():
        tk = busca.strip().upper().replace(".SA", "")
        st.session_state["b3_ticker_sel"] = tk
        st.session_state["b3_active_tab"] = 1
        st.rerun()

    if df_set.empty:
        st.warning(
            "Tabela `setores` não encontrada no banco configurado. "
            "Configure `SUPABASE_DB_URL_B3` no `.env` ou nos secrets do Streamlit Cloud."
        )
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
                        st.session_state["b3_active_tab"] = 1
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


def _build_indicators(mult: pd.Series, fontes: dict | None = None) -> list[tuple]:
    """
    Monta lista de indicadores para exibição em cards.
    mult: pd.Series na escala do BD (decimais para campos %).
    fontes: dict opcional com metadados de fonte (_fontes do reconciliacao).
    """
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

    def _g_pct(key: str):
        """Retorna valor % em escala display (×100 — BD armazena decimal).
        Se |v| excede o limite razoável para o campo, assume raw % e não multiplica."""
        v = _g(key)
        if v is None:
            return None
        threshold = _MAX_DECIMAL_PCT.get(key, 2.0)
        if abs(v) > threshold:
            return v   # BD armazenou em raw % — não duplicar a escala
        return v * 100.0

    def _badge(db_key: str) -> str:
        if not fontes:
            return ""
        src = fontes.get(db_key, "")
        return {"db_sobrescrito": " ⚠️", "web": " 🌐", "yfinance": " 📈"}.get(src, "")

    def _add(inds, lbl, v, sub, pct=True, inv=False, fmt_fn=None, db_key: str = ""):
        badge = _badge(db_key)
        lbl_b = lbl + badge
        if v is None:
            val, cor = "—", _COR_NEU
        else:
            val = fmt_fn(v) if fmt_fn else (_fp(v) if pct else f"{v:.2f}")
            cor = _cor_val(v, invert=inv)
        inds.append((lbl_b, val, sub, cor))

    inds: list[tuple] = []
    _add(inds, "Margem Líquida",     _g_pct("Margem_Liquida"),         "% Lucro/Receita",      db_key="Margem_Liquida")
    _add(inds, "Margem Operacional", _g_pct("Margem_Operacional"),      "% EBIT/Receita",       db_key="Margem_Operacional")
    _add(inds, "ROE",                _g_pct("ROE"),                     "Retorno s/ PL",        db_key="ROE")
    _add(inds, "ROA",                _g_pct("ROA"),                     "Retorno s/ Ativos",    db_key="ROA")
    _add(inds, "ROIC",               _g_pct("ROIC"),                    "Retorno s/ Capital",   db_key="ROIC")
    _add(inds, "Dividend Yield",     _g_pct("DY"),                      "Dividendos/Preço",     db_key="DY")
    _add(inds, "P/VP",
         _g("P/VP") or _g("PVP"),
         "Preço/Val. Patrimonial", pct=False, fmt_fn=lambda v: f"{v:.2f}x", db_key="P/VP")
    _add(inds, "Payout",             _g_pct("Payout"),                  "% Lucro distribuído",  db_key="Payout")
    _add(inds, "P/L",
         _g("P/L") or _g("PL"),
         "Preço/Lucro", pct=False, fmt_fn=lambda v: f"{v:.1f}x",   db_key="P/L")
    _add(inds, "Endividamento",      _g("Endividamento_Total"),          "Dív. Total/PL",
         pct=False, fmt_fn=lambda v: f"{v:.2f}x", inv=True,              db_key="Endividamento_Total")
    _add(inds, "Alavancagem Fin.",   _g("Alavancagem_Financeira"),       "Ativos/PL",
         pct=False, fmt_fn=lambda v: f"{v:.2f}x", inv=True)
    _add(inds, "Liquidez Corrente",
         _g("Liquidez_Corrente"),
         "Ativo Circ/Passivo Circ", pct=False, fmt_fn=lambda v: f"{v:.2f}x", db_key="Liquidez_Corrente")
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

    # ── Dados do banco + reconciliação + yfinance ────────────────────────────
    with st.spinner(f"Carregando dados de {tk}…"):
        df_fin        = _db.load_demonstracoes(tk)
        recon         = _recon.get_multiplos_reconciliados(tk)
        mult          = _recon.reconciliacao_to_series(recon)
        fontes_recon  = dict(recon.get("_fontes", {}))
        alertas_recon = recon.get("_alertas", [])
        yf_divs_mult  = _yf_multiplos_dividendos(tk)
        df_yf_divs    = _yf_dividendos_anuais(tk)

    # Patch: preenche DY e Payout ausentes com yfinance (escala decimal, igual ao BD)
    mult_dict = mult.to_dict() if not mult.empty else {}
    for field, val in yf_divs_mult.items():
        if mult_dict.get(field) is None:
            mult_dict[field] = val
            fontes_recon[field] = "yfinance"
    mult = pd.Series(mult_dict) if mult_dict else mult


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
            df_src = df_fin
            if field == "Dividendos" and (df_fin.empty or "Dividendos" not in df_fin.columns):
                df_src = df_yf_divs
            g = _cagr(df_src, field)
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
    indicadores = _build_indicators(
        mult if not sem_banco else pd.Series(dtype=object),
        fontes=fontes_recon if not sem_banco else None,
    )
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
# RENDER PRINCIPAL
# ══════════════════════════════════════════════════════════════════════════════

def render() -> None:
    st.markdown(_CSS, unsafe_allow_html=True)

    st.markdown(
        '<div style="display:flex;align-items:center;gap:12px;margin-bottom:4px;">'
        '<span style="font-size:2rem">🏢</span>'
        '<h1 style="font-size:2rem;font-weight:800;color:#E2E8F0;margin:0;">'
        'Empresas B3</h1>'
        '</div>'
        '<p style="font-size:0.80rem;color:#9CA3AF;margin-bottom:20px;">'
        'Análise fundamentalista de empresas listadas na B3. '
        '<b style="color:#CBD5E0;">Não constitui recomendação de investimento.</b>'
        '</p>',
        unsafe_allow_html=True,
    )

    with st.spinner("Carregando lista de empresas…"):
        df_set = _db.load_setores()

    if df_set.empty:
        st.caption(
            "⚠️ Banco não configurado — configure `SUPABASE_DB_URL_B3` "
            "no `.env` ou nos secrets do Streamlit Cloud."
        )

    active = st.session_state.get("b3_active_tab", 0)

    col_t1, col_t2, _ = st.columns([2, 2, 6])
    with col_t1:
        if st.button("🏢 Empresas por Setor", use_container_width=True,
                     type="primary" if active == 0 else "secondary",
                     key="b3_tab0"):
            st.session_state["b3_active_tab"] = 0
            st.rerun()
    with col_t2:
        if st.button("🔍 Análise de Empresa", use_container_width=True,
                     type="primary" if active == 1 else "secondary",
                     key="b3_tab1"):
            st.session_state["b3_active_tab"] = 1
            st.rerun()

    st.markdown("<hr style='margin:4px 0 16px;border-color:#1E2533;'>",
                unsafe_allow_html=True)

    if active == 0:
        _tab_empresas(df_set)
    else:
        _tab_analise(df_set)
