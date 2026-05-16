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
.b3-score-badge { display:inline-block;padding:2px 8px;border-radius:12px;
                  font-size:0.72rem;font-weight:700; }
.b3-score-high  { background:rgba(0,200,150,.15);color:#00C896; }
.b3-score-mid   { background:rgba(246,201,14,.15);color:#F6C90E; }
.b3-score-low   { background:rgba(252,92,125,.15);color:#FC5C7D; }
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


@st.cache_data(ttl=600, show_spinner=False)
def _yf_precos(ticker: str) -> pd.DataFrame:
    """Retorna histórico de preços ajustados via yfinance — colunas [Data, Preco]."""
    tk = ticker.strip().upper().replace(".SA", "")
    for var in [f"{tk}.SA", tk]:
        try:
            hist = yf.Ticker(var).history(period="max", auto_adjust=True)
            if hist is not None and not hist.empty:
                if hasattr(hist.index, "tz") and hist.index.tz is not None:
                    hist.index = hist.index.tz_localize(None)
                df = hist[["Close"]].reset_index()
                df.columns = ["Data", "Preco"]
                df["Data"] = pd.to_datetime(df["Data"])
                return df
        except Exception:
            pass
    return pd.DataFrame()


@st.cache_data(ttl=3600, show_spinner=False)
def _batch_yf_precos_mensais(tickers: tuple[str, ...], period: str = "5y") -> pd.DataFrame:
    """
    Preços mensais de fechamento para múltiplos tickers via yfinance.
    Retorna DataFrame com DatetimeIndex e colunas = tickers sem .SA.
    """
    if not tickers:
        return pd.DataFrame()
    tks_sa = [f"{t.strip().upper().replace('.SA', '')}.SA" for t in tickers]
    try:
        if len(tks_sa) == 1:
            raw = yf.download(tks_sa[0], period=period, interval="1mo",
                              auto_adjust=True, progress=False)
            if raw is None or raw.empty:
                return pd.DataFrame()
            tk_clean = tks_sa[0].replace(".SA", "").upper()
            close = pd.DataFrame({tk_clean: raw["Close"]})
        else:
            raw = yf.download(tks_sa, period=period, interval="1mo",
                              auto_adjust=True, progress=False)
            if raw is None or raw.empty:
                return pd.DataFrame()
            close = (
                raw["Close"].copy()
                if isinstance(raw.columns, pd.MultiIndex)
                else raw.copy()
            )
        if hasattr(close.index, "tz") and close.index.tz is not None:
            close.index = close.index.tz_localize(None)
        close.columns = [str(c).replace(".SA", "").strip().upper() for c in close.columns]
        return close.dropna(how="all")
    except Exception:
        return pd.DataFrame()


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
# Scoring e backtesting — Motor v2 (alinhado com App 1)
# ══════════════════════════════════════════════════════════════════════════════

# {indicador: (peso_relativo, melhor_alto)}
_PESOS_SETOR: dict[str, dict[str, tuple[float, bool]]] = {
    "financeiro": {
        "ROE": (30, True), "Margem_Liquida": (20, True), "DY": (20, True),
        "P/VP": (10, False), "Liquidez_Corrente": (10, True), "Endividamento_Total": (10, False),
    },
    "tecnologia": {
        "ROIC": (22, True), "Margem_Liquida": (18, True), "Margem_Operacional": (15, True),
        "ROE": (12, True), "EV_EBIT": (8, False),
        "ROE_slope_log": (13, True), "ROIC_slope_log": (12, True),
    },
    "energia": {
        "DY": (30, True), "ROE": (20, True), "Margem_Operacional": (20, True),
        "Endividamento_Total": (15, False), "P/VP": (15, False),
    },
    "industrial": {
        "ROIC": (18, True), "Margem_Operacional": (18, True), "ROE": (14, True),
        "EV_EBIT": (10, False), "Liquidez_Corrente": (10, True),
        "ROE_slope_log": (15, True), "Margem_Operacional_slope_log": (15, True),
    },
    "consumo ciclico": {
        "ROE": (18, True), "Margem_Liquida": (18, True), "ROIC": (14, True),
        "P/L": (10, False), "Endividamento_Total": (10, False),
        "ROE_slope_log": (15, True), "Margem_Liquida_slope_log": (15, True),
    },
    "consumo nao ciclico": {
        "DY": (25, True), "ROE": (25, True), "Margem_Liquida": (20, True),
        "Payout": (15, True), "Endividamento_Total": (15, False),
    },
    "materiais basicos": {
        "ROIC": (25, True), "Margem_Operacional": (25, True), "EV_EBIT": (20, False),
        "DY": (15, True), "Endividamento_Total": (15, False),
    },
    "petroleo": {
        "DY": (30, True), "Margem_Operacional": (25, True), "ROE": (20, True),
        "Endividamento_Total": (15, False), "P/VP": (10, False),
    },
    "saude": {
        "ROIC": (25, True), "Margem_Liquida": (25, True), "ROE": (20, True),
        "EV_EBIT": (15, False), "Liquidez_Corrente": (15, True),
    },
    "utilidade publica": {
        "DY": (35, True), "ROE": (20, True), "Endividamento_Total": (20, False),
        "Margem_Operacional": (15, True), "P/VP": (10, False),
    },
    "comunicacoes": {
        "ROE": (25, True), "Margem_Operacional": (25, True), "ROIC": (20, True),
        "EV_EBIT": (15, False), "Endividamento_Total": (15, False),
    },
    "bens industriais": {
        "ROIC": (30, True), "Margem_Operacional": (25, True), "ROE": (20, True),
        "Liquidez_Corrente": (15, True), "EV_EBIT": (10, False),
    },
}

_PESOS_GENERICO: dict[str, tuple[float, bool]] = {
    "ROE": (25, True), "ROIC": (20, True), "Margem_Liquida": (15, True),
    "DY": (10, True), "Endividamento_Total": (20, False), "Liquidez_Corrente": (10, True),
}

# Indicadores usados na tela comparativa: (coluna_db, label_display)
_COLS_COMP: list[tuple[str, str]] = [
    ("ROE", "ROE"), ("ROIC", "ROIC"),
    ("Margem_Liquida", "Margem Líq."), ("Margem_Operacional", "Margem Op."),
    ("DY", "DY"), ("P/L", "P/L"), ("P/VP", "P/VP"),
    ("EV_EBIT", "EV/EBIT"), ("Endividamento_Total", "Endiv."),
    ("Liquidez_Corrente", "Liquidez"),
]
_INV_LABELS: set[str] = {"Endiv.", "P/L", "P/VP", "EV/EBIT"}

_SLOPE_COLS: tuple[str, ...] = (
    "ROE", "ROIC", "Margem_Liquida", "Margem_Operacional",
)


def _compute_slope_log(s: pd.Series) -> float | None:
    """Slope da regressão log-linear — proxy de crescimento anualizado do indicador."""
    s = pd.to_numeric(s, errors="coerce").dropna()
    s = s[s > 0]
    if len(s) < 3:
        return None
    x = np.arange(len(s), dtype=float)
    try:
        slope, _ = np.polyfit(x, np.log(s.values), 1)
        return float(slope) if np.isfinite(slope) else None
    except Exception:
        return None


def _enrich_com_slopes(
    df_mult: pd.DataFrame,
    hist_batch: dict[str, pd.DataFrame],
) -> pd.DataFrame:
    """
    Acrescenta colunas {col}_slope_log ao df_mult calculadas do histórico.
    Colunas ausentes são silenciosamente ignoradas pelo scoring.
    """
    if not hist_batch or df_mult.empty:
        return df_mult
    slope_data: dict[str, dict[str, float]] = {}
    for tk, df_h in hist_batch.items():
        if df_h.empty:
            continue
        row: dict[str, float] = {}
        for c in _SLOPE_COLS:
            if c not in df_h.columns:
                continue
            v = _compute_slope_log(df_h[c])
            if v is not None:
                row[f"{c}_slope_log"] = v
        if row:
            slope_data[tk] = row
    if not slope_data:
        return df_mult
    df_sl = pd.DataFrame.from_dict(slope_data, orient="index")
    df_sl.index.name = "Ticker"
    return df_mult.merge(df_sl.reset_index(), on="Ticker", how="left")


def _norm_pesos(conf: dict[str, tuple[float, bool]]) -> dict[str, tuple[float, bool]]:
    total = sum(v[0] for v in conf.values()) or 1.0
    return {k: (v[0] / total, v[1]) for k, v in conf.items()}


def _get_pesos_setor(setor: str,
                     pesos_usuario: dict[str, float] | None = None
                     ) -> dict[str, tuple[float, bool]]:
    if pesos_usuario:
        conf: dict[str, tuple[float, bool]] = {}
        for k, v in pesos_usuario.items():
            if float(v) <= 0:
                continue
            melhor_alto = k not in ("P/L", "P/VP", "EV_EBIT", "P_FCO") and \
                          "endiv" not in k.lower()
            conf[k] = (float(v), melhor_alto)
        return _norm_pesos(conf) if conf else _norm_pesos(_PESOS_GENERICO)

    s_lower = (setor or "").lower()
    for key, conf in _PESOS_SETOR.items():
        if key in s_lower or any(part in s_lower for part in key.split()):
            return _norm_pesos(conf)
    return _norm_pesos(_PESOS_GENERICO)


def _winsorize_series(s: pd.Series, p_low: float = 0.05, p_high: float = 0.95) -> pd.Series:
    lo = s.quantile(p_low)
    hi = s.quantile(p_high)
    return s.clip(lower=lo, upper=hi)


def _percentile_score(s: pd.Series, melhor_alto: bool = True) -> pd.Series:
    result = pd.Series(0.5, index=s.index, dtype=float)
    valid  = s.notna()
    if valid.sum() >= 2:
        result[valid] = s[valid].rank(pct=True, ascending=melhor_alto)
    return result


def _resolve_group_col_df(df: pd.DataFrame, prefer: str = "SEGMENTO",
                           min_n: int = 3) -> str:
    for col in [prefer, "SUBSETOR", "SETOR"]:
        if col in df.columns:
            if df.groupby(col).size().median() >= min_n:
                return col
    for col in [prefer, "SUBSETOR", "SETOR"]:
        if col in df.columns:
            return col
    return prefer


def _select_n_heuristica(scores_desc: list[float], eps: float = 0.35) -> int:
    if len(scores_desc) < 2:
        return 1
    gap12 = scores_desc[0] - scores_desc[1]
    gap23 = scores_desc[1] - scores_desc[2] if len(scores_desc) >= 3 else 1.0
    if gap12 <= eps and gap23 <= eps:
        return 3
    if gap12 <= eps:
        return 2
    return 1


def _weights_from_scores(tickers: list[str], score_map: dict[str, float],
                          gamma: float = 0.90) -> dict[str, float]:
    scores = [score_map.get(tk, 0.0) for tk in tickers]
    mn  = min(scores)
    eps = 1e-6
    raw = [(max(s - mn, 0) + eps) ** gamma for s in scores]
    tot = sum(raw) or 1.0
    return {tk: r / tot for tk, r in zip(tickers, raw)}


def _apply_cap_soft(weights: dict[str, float],
                    cap: float = 0.25, soft: float = 0.05) -> dict[str, float]:
    w = {tk: v + (v - soft) * 0.5 if v > soft else v for tk, v in weights.items()}
    over = {tk: v - cap for tk, v in w.items() if v > cap}
    for tk in over:
        w[tk] = cap
    if over:
        surplus = sum(over.values())
        under   = [tk for tk in w if w[tk] < cap]
        if under:
            add = surplus / len(under)
            for tk in under:
                w[tk] = min(w[tk] + add, cap)
    tot = sum(w.values()) or 1.0
    return {tk: v / tot for tk, v in w.items()}


def _apply_crowding_penalty(
    df: pd.DataFrame,
    group_col: str,
    pvp_col: str = "P/VP",
    n_buckets: int = 5,
    max_pen: float = 0.10,
) -> pd.Series:
    """
    Penaliza empresas no bucket de P/VP mais congestionado dentro do grupo.
    Empresas em múltiplos muito populares recebem desconto de até max_pen=10%.
    """
    penalty = pd.Series(0.0, index=df.index)
    if pvp_col not in df.columns or group_col not in df.columns:
        return penalty

    uniform_share = 1.0 / n_buckets
    for _, idx in df.groupby(group_col).groups.items():
        g = pd.to_numeric(df.loc[idx, pvp_col], errors="coerce").dropna()
        if len(g) < n_buckets:
            continue
        try:
            buckets    = pd.qcut(g, q=n_buckets, duplicates="drop")
        except Exception:
            continue
        counts        = buckets.value_counts()
        crowded_label = counts.idxmax()
        crowd_frac    = counts.max() / len(g)
        if crowd_frac <= uniform_share:
            continue
        pen = min((crowd_frac - uniform_share) * max_pen * n_buckets, max_pen)
        in_crowd = buckets[buckets == crowded_label].index
        penalty.loc[in_crowd] = pen

    return penalty


def _score_universo(
    df_mult: pd.DataFrame,
    tickers_universo: list[str],
    pesos: dict[str, tuple[float, bool]],
    df_hist_batch: dict[str, pd.DataFrame] | None = None,
    group_col_prefer: str = "SEGMENTO",
) -> pd.DataFrame:
    """
    Score v2: winsorize → percentil intra-grupo → penalidade instabilidade (CV).
    pesos: {indicador: (peso_normalizado, melhor_alto)}
    """
    if df_mult.empty or not tickers_universo:
        return pd.DataFrame({"Ticker": tickers_universo, "score": 0.0, "ranking": 0})

    df = df_mult[df_mult["Ticker"].isin(tickers_universo)].copy()
    if df.empty:
        return pd.DataFrame({"Ticker": tickers_universo, "score": 0.0, "ranking": 0})

    group_col = _resolve_group_col_df(df, prefer=group_col_prefer)
    score = pd.Series(0.0, index=df.index)

    for col, (peso, melhor_alto) in pesos.items():
        if col not in df.columns or peso == 0:
            continue
        s = pd.to_numeric(df[col], errors="coerce")
        if s.notna().sum() < 2:
            continue
        s_win = _winsorize_series(s.dropna()).reindex(s.index)

        if group_col in df.columns:
            pct = pd.Series(np.nan, index=df.index, dtype=float)
            for _, idx in df.groupby(group_col).groups.items():
                g_s = s_win.loc[idx]
                if g_s.notna().sum() >= 2:
                    pct.loc[idx] = _percentile_score(g_s, melhor_alto)
                else:
                    pct.loc[idx] = _percentile_score(s_win, melhor_alto).loc[idx]
        else:
            pct = _percentile_score(s_win, melhor_alto)

        score += pct.fillna(0.5) * peso * 100.0

    df["score_raw"] = score

    # Penalidade de instabilidade via Coeficiente de Variação histórico
    if df_hist_batch:
        cv_pen = pd.Series(0.0, index=df.index)
        cands  = [c for c in ("ROE", "ROIC", "Margem_Liquida", "Margem_Operacional")
                  if c in df.columns]
        for i, row in df.iterrows():
            tk   = row["Ticker"]
            df_h = df_hist_batch.get(tk)
            if df_h is None or df_h.empty:
                continue
            pen = 0.0
            for c in cands:
                if c not in df_h.columns:
                    continue
                sh = pd.to_numeric(df_h[c], errors="coerce").dropna()
                if len(sh) < 3 or abs(sh.mean()) < 1e-9:
                    continue
                cv = sh.std() / abs(sh.mean())
                pen += (cv / (cv + 1)) ** 1.5 * 0.25 * 0.60
            cv_pen[i] = min(pen / max(len(cands), 1), 0.25)
        df["score_raw"] *= (1.0 - cv_pen)

    # Penalidade de crowding — desconta empresas no bucket de P/VP mais populoso do grupo
    crow_pen = _apply_crowding_penalty(df, group_col)
    df["score_raw"] *= (1.0 - crow_pen)

    df["score"]   = df["score_raw"].round(1)
    df["ranking"] = df["score"].rank(ascending=False, method="min").astype(int)
    return df.sort_values("score", ascending=False).reset_index(drop=True)


_GAMMA_GRID = (0.50, 0.75, 1.00, 1.25)
_CAP_GRID   = (0.20, 0.25, 0.30)
_SOFT_GRID  = (0.03, 0.05, 0.08)
_GAMMA_DEF, _CAP_DEF, _SOFT_DEF = 0.90, 0.25, 0.05
_CAL_SHRINK = 0.40   # shrinkage 40 % em direção ao default


def _calibrate_gamma_cap_soft(
    df_precos: pd.DataFrame,
    df_scored: pd.DataFrame,
    tickers_all: list[str],
    taxa_selic_aa: float,
    aporte: float = 1000.0,
    window_months: int = 36,
) -> tuple[float, float, float]:
    """
    Grid search 36 combinações (4γ × 3cap × 3soft) em janela de window_months.
    Objetivo: CAGR − 0.60×vol + 0.40×|MDD|.
    Aplica shrinkage 40 % em direção aos defaults (γ=0.90, cap=0.25, soft=0.05).
    Usa scores estáticos (rápido) — sem rebalanceamento anual durante a calibração.
    """
    if df_precos.empty or len(df_precos) < 6 or df_scored.empty:
        return _GAMMA_DEF, _CAP_DEF, _SOFT_DEF

    df_w     = df_precos.iloc[-window_months:].copy()
    score_map = dict(zip(df_scored["Ticker"], df_scored["score"]))
    tks_cand  = [tk for tk in df_scored["Ticker"].tolist()[:5] if tk in df_w.columns]
    if not tks_cand:
        return _GAMMA_DEF, _CAP_DEF, _SOFT_DEF

    best_obj  = -np.inf
    best_pars = (_GAMMA_DEF, _CAP_DEF, _SOFT_DEF)

    for gamma in _GAMMA_GRID:
        for cap in _CAP_GRID:
            for soft in _SOFT_GRID:
                w     = _apply_cap_soft(_weights_from_scores(tks_cand, score_map, gamma), cap, soft)
                cotas = {tk: 0.0 for tk in tks_cand}
                vals: list[float] = []

                for _, row in df_w.iterrows():
                    disp = [tk for tk in tks_cand
                            if pd.notna(row.get(tk)) and float(row.get(tk, 0) or 0) > 0]
                    if disp:
                        tw = sum(w.get(tk, 0.0) for tk in disp) or 1.0
                        for tk in disp:
                            cotas[tk] += aporte * w.get(tk, 0.0) / tw / float(row[tk])
                    vals.append(sum(
                        cotas[tk] * float(row[tk])
                        for tk in tks_cand
                        if pd.notna(row.get(tk)) and float(row.get(tk, 0) or 0) > 0
                    ))

                if len(vals) < 6 or vals[0] == 0:
                    continue
                s_vals = pd.Series(vals)
                rets   = s_vals.pct_change().dropna()
                n_yr   = len(vals) / 12
                cagr   = (vals[-1] / vals[0]) ** (1 / max(n_yr, 0.1)) - 1
                vol    = float(rets.std()) * (12 ** 0.5)
                cum    = (1 + rets).cumprod()
                mdd    = float(abs(((cum - cum.cummax()) / cum.cummax()).min()))
                obj    = cagr - 0.60 * vol + 0.40 * mdd
                if obj > best_obj:
                    best_obj  = obj
                    best_pars = (gamma, cap, soft)

    g = best_pars[0] * (1 - _CAL_SHRINK) + _GAMMA_DEF * _CAL_SHRINK
    c = best_pars[1] * (1 - _CAL_SHRINK) + _CAP_DEF   * _CAL_SHRINK
    s = best_pars[2] * (1 - _CAL_SHRINK) + _SOFT_DEF  * _CAL_SHRINK
    return round(g, 3), round(c, 3), round(s, 3)


_DY_MAX_DECIMAL = 1.0   # DY > 100% em escala decimal = dado contaminado
_DY_MAX_RAW_PCT = 50.0  # DY > 50% em escala raw % = dado contaminado


def _sanitize_dy(row: dict) -> dict:
    """Zera DY obviamente contaminado (ex-dividendo total ou erro de escala)."""
    dy = row.get("DY")
    if dy is None:
        return row
    try:
        v = float(dy)
        if v > _DY_MAX_RAW_PCT or (v < 1.0 and v > _DY_MAX_DECIMAL):
            row = dict(row)
            row["DY"] = None
    except (TypeError, ValueError):
        pass
    return row


def _score_historico_ano(
    df_hist_batch: dict[str, pd.DataFrame],
    tickers: list[str],
    ano_ref: int,
    pesos: dict[str, tuple[float, bool]],
    tk_grupos: dict[str, dict] | None = None,
    lag: int = 1,
) -> dict[str, float]:
    """
    Monta snapshot cross-sectional com dados até ano_ref − lag, depois pontua.
    Elimina look-ahead bias: score do ano N usa apenas dados até N−1.
    Retorna {ticker: score}.
    """
    ano_cutoff = ano_ref - lag
    rows = []
    for tk in tickers:
        df_h = df_hist_batch.get(tk)
        if df_h is None or df_h.empty or "Data" not in df_h.columns:
            continue
        df_h = df_h.copy()
        df_h["_ano"] = pd.to_datetime(df_h["Data"], errors="coerce").dt.year
        validos = df_h[df_h["_ano"] <= ano_cutoff]
        if validos.empty:
            continue
        row = validos.sort_values("_ano").iloc[-1].to_dict()
        row["Ticker"] = tk
        row = _sanitize_dy(row)
        if tk_grupos:
            row.update(tk_grupos.get(tk) or {})
        rows.append(row)

    if not rows:
        return {}

    df_snap = pd.DataFrame(rows)
    df_sc   = _score_universo(df_snap, tickers, pesos)
    if df_sc.empty or "score" not in df_sc.columns:
        return {}
    return dict(zip(df_sc["Ticker"], df_sc["score"]))


def _apply_decay_penalty(
    score_map: dict[str, float],
    anos_lideranca: dict[str, int],
    desconto_aa: float = 0.07,
    cap: float = 0.30,
) -> dict[str, float]:
    """Desconta scores de líderes consecutivos — força rotação de carteira."""
    return {
        tk: s * (1.0 - min(anos_lideranca.get(tk, 0) * desconto_aa, cap))
        for tk, s in score_map.items()
    }


def _simular_backtest(
    df_precos: pd.DataFrame,
    df_scored: pd.DataFrame,
    df_hist_batch: dict[str, pd.DataFrame],
    tickers_all: list[str],
    aporte: float,
    data_inicio: pd.Timestamp,
    taxa_selic_aa: float,
    pesos: dict[str, tuple[float, bool]],
    tk_grupos: dict[str, dict] | None,
    top_n_max: int = 5,
    usar_gamma: bool = True,
    gamma: float = _GAMMA_DEF,
    cap: float = _CAP_DEF,
    soft: float = _SOFT_DEF,
    selic_por_ano: dict[int, float] | None = None,
) -> tuple[pd.DataFrame, list[str], int]:
    """
    Simula aportes mensais com rebalanceamento anual e publication lag = 1.
    Score do ano N é calculado com dados até N−1 (sem look-ahead bias).
    selic_por_ano: taxa Selic real por ano (da tabela macro); fallback = taxa_selic_aa.
    Retorna (df_resultado, tickers_top_último_ano, n_efetivo_último_ano).
    """
    if df_precos.empty or not tickers_all:
        return pd.DataFrame(), [], 0

    df = df_precos[df_precos.index >= data_inicio].copy()
    if df.empty:
        return pd.DataFrame(), [], 0

    tks_all_valid = [tk for tk in tickers_all if tk in df.columns]

    # Fallback snapshot scores (caso histórico insuficiente)
    snap_scores = (
        dict(zip(df_scored["Ticker"], df_scored["score"]))
        if not df_scored.empty else {}
    )

    # Estado do backtest
    anos_lideranca: dict[str, int] = {}
    pesos_est: dict[str, float]    = {}
    tks_est_valid: list[str]       = []
    tickers_top_final: list[str]   = []
    n_efetivo_final: int           = 0
    ultimo_ano_rebal: int          = -1

    cotas_est   = {tk: 0.0 for tk in tks_all_valid}
    cotas_bench = {tk: 0.0 for tk in tks_all_valid}
    selic_acum  = 0.0
    rows: list[dict] = []

    for dt, row in df.iterrows():
        ano = dt.year
        # Selic anual: macro quando disponível, fallback ao parâmetro do usuário
        selic_aa_ano  = (selic_por_ano or {}).get(ano, taxa_selic_aa)
        taxa_mensal   = (1 + selic_aa_ano) ** (1 / 12) - 1
        selic_acum    = selic_acum * (1 + taxa_mensal) + aporte

        # ── Rebalanceamento anual com publication lag ──────────────────────
        if ano != ultimo_ano_rebal:
            ultimo_ano_rebal = ano

            # Calcula scores com dados até ano − 1
            score_map = _score_historico_ano(
                df_hist_batch, tks_all_valid, ano, pesos, tk_grupos, lag=1
            )
            if not score_map:
                score_map = snap_scores  # fallback snapshot

            # Penalidade de liderança consecutiva
            score_map = _apply_decay_penalty(score_map, anos_lideranca)

            # Ordenar e selecionar top-N
            ranked = sorted(
                [(tk, s) for tk, s in score_map.items() if tk in df.columns],
                key=lambda x: x[1], reverse=True
            )[:top_n_max]
            tks_ranked_yr = [tk for tk, _ in ranked]
            scores_yr     = [s  for _, s  in ranked]

            n_yr = (
                _select_n_heuristica(scores_yr)
                if usar_gamma and len(tks_ranked_yr) >= 2
                else min(len(tks_ranked_yr), top_n_max)
            )
            tickers_yr = tks_ranked_yr[:n_yr]

            if usar_gamma and len(tickers_yr) >= 2:
                pesos_est = _apply_cap_soft(
                    _weights_from_scores(tickers_yr, score_map, gamma), cap, soft
                )
            else:
                pesos_est = (
                    {tk: 1.0 / len(tickers_yr) for tk in tickers_yr}
                    if tickers_yr else {}
                )
            tks_est_valid = list(pesos_est.keys())

            # Atualiza contagem de anos consecutivos no topo
            lids = set(tickers_yr)
            for tk in list(anos_lideranca):
                if tk not in lids:
                    del anos_lideranca[tk]
            for tk in lids:
                anos_lideranca[tk] = anos_lideranca.get(tk, 0) + 1

            tickers_top_final = tickers_yr
            n_efetivo_final   = n_yr

        # ── Aportes mensais ────────────────────────────────────────────────
        est_disp = [tk for tk in tks_est_valid
                    if pd.notna(row.get(tk)) and float(row.get(tk, 0) or 0) > 0]
        if est_disp:
            total_w = sum(pesos_est.get(tk, 0.0) for tk in est_disp) or 1.0
            for tk in est_disp:
                cotas_est[tk] += aporte * pesos_est.get(tk, 0.0) / total_w / float(row[tk])

        all_disp = [tk for tk in tks_all_valid
                    if pd.notna(row.get(tk)) and float(row.get(tk, 0) or 0) > 0]
        if all_disp:
            por_tk = aporte / len(all_disp)
            for tk in all_disp:
                cotas_bench[tk] += por_tk / float(row[tk])

        val_est = sum(
            cotas_est[tk] * float(row[tk])
            for tk in tks_all_valid
            if tk in cotas_est and pd.notna(row.get(tk)) and float(row.get(tk, 0) or 0) > 0
        )
        val_bench = sum(
            cotas_bench[tk] * float(row[tk])
            for tk in tks_all_valid
            if pd.notna(row.get(tk)) and float(row.get(tk, 0) or 0) > 0
        )
        rows.append({"Data": dt, "Estratégia": val_est,
                     "Benchmark": val_bench, "Tesouro Selic": selic_acum})

    return pd.DataFrame(rows), tickers_top_final, n_efetivo_final


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


def _build_indicators(mult: pd.Series, fontes: dict | None = None,
                      grupo: str = "todos") -> list[tuple]:
    """
    Monta lista de indicadores para exibição em cards.
    grupo: 'rentabilidade' | 'valuation' | 'estrutura' | 'todos'
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
    r = grupo

    if r in ("rentabilidade", "todos"):
        _add(inds, "Margem Líquida",     _g_pct("Margem_Liquida"),    "% Lucro/Receita",    db_key="Margem_Liquida")
        _add(inds, "Margem Operacional", _g_pct("Margem_Operacional"), "% EBIT/Receita",     db_key="Margem_Operacional")
        _add(inds, "ROE",                _g_pct("ROE"),                "Retorno s/ PL",      db_key="ROE")
        _add(inds, "ROA",                _g_pct("ROA"),                "Retorno s/ Ativos",  db_key="ROA")
        _add(inds, "ROIC",               _g_pct("ROIC"),               "Retorno s/ Capital", db_key="ROIC")
        _add(inds, "Dividend Yield",     _g_pct("DY"),                 "Dividendos/Preço",   db_key="DY")

    if r in ("valuation", "todos"):
        _add(inds, "P/VP",
             _g("P/VP") or _g("PVP"),
             "Preço/Val. Patrimonial", pct=False, fmt_fn=lambda v: f"{v:.2f}x", db_key="P/VP")
        _add(inds, "P/L",
             _g("P/L") or _g("PL"),
             "Preço/Lucro", pct=False, fmt_fn=lambda v: f"{v:.1f}x", db_key="P/L")
        _add(inds, "EV/EBIT",
             _g("EV_EBIT") or _g("EV/EBIT"),
             "Valor Empresa/EBIT", pct=False, fmt_fn=lambda v: f"{v:.1f}x", db_key="EV_EBIT")
        _add(inds, "P/FCO",
             _g("P_FCO") or _g("P/FCO"),
             "Preço/Fluxo de Caixa", pct=False, fmt_fn=lambda v: f"{v:.1f}x", db_key="P_FCO")
        _add(inds, "Payout",             _g_pct("Payout"),             "% Lucro distribuído", db_key="Payout")

    if r in ("estrutura", "todos"):
        _add(inds, "Endividamento",
             _g("Endividamento_Total"),
             "Dív. Total/PL", pct=False, fmt_fn=lambda v: f"{v:.2f}x", inv=True,
             db_key="Endividamento_Total")
        _add(inds, "Alavancagem Fin.", _g("Alavancagem_Financeira"),
             "Ativos/PL", pct=False, fmt_fn=lambda v: f"{v:.2f}x", inv=True)
        _add(inds, "Liquidez Corrente",
             _g("Liquidez_Corrente"),
             "Ativo Circ/Passivo Circ", pct=False, fmt_fn=lambda v: f"{v:.2f}x",
             db_key="Liquidez_Corrente")

    return inds


def _plot_layout(height: int = 340) -> dict:
    return dict(
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font_color=_COR_NEU, height=height,
        margin={"t": 10, "b": 10, "l": 0, "r": 0},
        legend={"orientation": "h", "y": -0.18,
                "bgcolor": "rgba(0,0,0,0)", "font": {"size": 11}},
        yaxis={"showgrid": True, "gridcolor": "#1E2533"},
        xaxis={"showgrid": False},
    )


def _sec_hdr(titulo: str) -> None:
    st.markdown(
        f'<div style="font-size:0.75rem;font-weight:700;color:#E2E8F0;'
        f'margin:18px 0 8px;">{titulo}</div>',
        unsafe_allow_html=True,
    )


def _render_cards(indicadores: list[tuple], n_cols: int = 4) -> None:
    for i in range(0, len(indicadores), n_cols):
        chunk = indicadores[i:i+n_cols]
        cols = st.columns(n_cols, gap="small")
        for j, (lbl, val, sub, cor) in enumerate(chunk):
            with cols[j]:
                st.markdown(_ind_card(lbl, val, sub, cor), unsafe_allow_html=True)


def _tab_analise(df_set: pd.DataFrame) -> None:
    # ── Input ─────────────────────────────────────────────────────────────────
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
    preco    = _preco_atual(tk)
    info_row = (df_set[df_set["ticker"] == tk].iloc[0]
                if not df_set.empty and tk in df_set["ticker"].values else None)
    nome_emp = info_row["nome_empresa"] if info_row is not None else tk
    setor    = info_row["SETOR"]    if info_row is not None else "—"
    subsetor = info_row["SUBSETOR"] if info_row is not None else "—"

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

    # ── Carregamento de dados ─────────────────────────────────────────────────
    with st.spinner(f"Carregando dados de {tk}…"):
        df_fin       = _db.load_demonstracoes(tk)
        df_mult_hist = _db.load_multiplos_historico(tk)
        recon        = _recon.get_multiplos_reconciliados(tk)
        mult         = _recon.reconciliacao_to_series(recon)
        fontes_recon = dict(recon.get("_fontes", {}))
        yf_divs_mult = _yf_multiplos_dividendos(tk)
        df_yf_divs   = _yf_dividendos_anuais(tk)
        df_precos    = _yf_precos(tk)

    # Patch DY / Payout ausentes com yfinance
    mult_dict = mult.to_dict() if not mult.empty else {}
    for field, val in yf_divs_mult.items():
        if mult_dict.get(field) is None:
            mult_dict[field] = val
            fontes_recon[field] = "yfinance"
    mult = pd.Series(mult_dict) if mult_dict else mult

    sem_banco = df_fin.empty and mult.empty

    # ══════════════════════════════════════════════════════════════════════════
    # SEÇÃO 1 — Preço da Ação
    # ══════════════════════════════════════════════════════════════════════════
    if not df_precos.empty:
        _sec_hdr("📉 Preço da Ação")

        periodos = {"1A": 365, "3A": 1095, "5A": 1825, "Máx": None}
        sel_per = st.radio("Período", list(periodos.keys()),
                           index=3, horizontal=True, key=f"b3_per_{tk}")
        dias = periodos[sel_per]
        df_p = df_precos.copy()
        if dias:
            cutoff = df_p["Data"].max() - pd.Timedelta(days=dias)
            df_p = df_p[df_p["Data"] >= cutoff]

        fig_preco = px.line(df_p, x="Data", y="Preco",
                            color_discrete_sequence=[_COR_INF])
        fig_preco.update_traces(line_width=1.5)
        fig_preco.update_layout(**_plot_layout(280))
        fig_preco.update_layout(showlegend=False,
                                yaxis_title="Preço (R$)", xaxis_title="")
        st.plotly_chart(fig_preco, use_container_width=True,
                        config={"displayModeBar": False}, key=f"b3_preco_{tk}_{sel_per}")

        # Retorno anual
        _sec_hdr("📊 Retorno Anual do Preço")
        tmp = df_precos.copy()
        tmp["Ano"] = tmp["Data"].dt.year
        yr   = tmp.groupby("Ano")["Preco"].last()
        ret  = (yr.pct_change().dropna() * 100).reset_index()
        ret.columns = ["Ano", "Retorno"]
        ret["Ano"]      = ret["Ano"].astype(str)
        ret["Positivo"] = ret["Retorno"] >= 0
        ret["Texto"]    = ret["Retorno"].map(lambda v: f"{v:+.2f}%")
        ret_sorted = ret.sort_values("Ano")

        fig_ret = px.bar(
            ret_sorted, x="Retorno", y="Ano", orientation="h",
            color="Positivo",
            color_discrete_map={True: _COR_POS, False: _COR_NEG},
            text="Texto",
        )
        fig_ret.update_traces(textposition="outside", textfont_size=10)
        fig_ret.update_layout(**_plot_layout(max(250, len(ret_sorted) * 28)))
        fig_ret.update_layout(
            showlegend=False, xaxis_title="Retorno anual (%)", yaxis_title="Ano",
            xaxis={"showgrid": True, "gridcolor": "#1E2533", "zeroline": True,
                   "zerolinecolor": "#4A5568"},
        )
        st.plotly_chart(fig_ret, use_container_width=True,
                        config={"displayModeBar": False}, key=f"b3_ret_{tk}")

    # ══════════════════════════════════════════════════════════════════════════
    # SEÇÃO 2 — Crescimento e DRE (requer BD)
    # ══════════════════════════════════════════════════════════════════════════
    if not sem_banco:
        _sec_hdr("📈 Crescimento Médio Anual (CAGR)")
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

        if not df_fin.empty:
            _sec_hdr("📋 Último Exercício Disponível")
            r1, r2, r3, r4 = st.columns(4, gap="small")
            for col, lbl, field, inv in [
                (r1, "Receita Líquida", "Receita_Liquida", False),
                (r2, "EBIT",           "EBIT",            False),
                (r3, "Lucro Líquido",  "Lucro_Liquido",   False),
                (r4, "Dívida Líquida", "Divida_Liquida",  True),
            ]:
                v = _last_val(df_fin, field)
                with col:
                    st.markdown(
                        _ind_card(lbl, _fv(v), "Último registro no banco",
                                  _cor_val(v, invert=inv) if v is not None else _COR_NEU),
                        unsafe_allow_html=True,
                    )

        # Gráfico DRE histórico
        if not df_fin.empty and "Data" in df_fin.columns:
            _sec_hdr("📊 Demonstrações Financeiras — Histórico")
            cands_dre = [
                ("Receita_Liquida", "Receita Líquida"), ("EBIT", "EBIT"),
                ("Lucro_Liquido", "Lucro Líquido"), ("Patrimonio_Liquido", "Patrimônio Líquido"),
                ("Divida_Liquida", "Dívida Líquida"), ("Divida_Total", "Dívida Total"),
                ("Ativo_Total", "Ativo Total"), ("Dividendos", "Dividendos"),
            ]
            disp_dre = [(c, l) for c, l in cands_dre if c in df_fin.columns]
            if disp_dre:
                opcoes = [l for _, l in disp_dre]
                deflt  = [x for x in ("Receita Líquida", "Lucro Líquido") if x in opcoes]
                sel    = st.multiselect("Indicadores", opcoes, default=deflt or opcoes[:2],
                                        key=f"b3_dre_sel_{tk}")
                if sel:
                    lbl2col  = {l: c for c, l in disp_dre}
                    cols_sel = [lbl2col[l] for l in sel if l in lbl2col]
                    plot     = df_fin[["Data"] + cols_sel].copy()
                    for c in cols_sel:
                        plot[c] = pd.to_numeric(plot[c], errors="coerce")
                    melt = plot.melt("Data", value_vars=cols_sel,
                                     var_name="Indicador", value_name="Valor")
                    melt["Indicador"] = melt["Indicador"].map({c: l for c, l in disp_dre})
                    fig = px.line(melt, x="Data", y="Valor", color="Indicador", markers=True,
                                  color_discrete_sequence=[
                                      _COR_POS, _COR_INF, _COR_ALT, _COR_NEG,
                                      "#9B59B6", "#E67E22", _COR_NEU])
                    fig.update_layout(**_plot_layout(340))
                    st.plotly_chart(fig, use_container_width=True,
                                    config={"displayModeBar": False}, key=f"b3_dre_{tk}")

        # Histórico de Múltiplos (%) via tabela multiplos
        if not df_mult_hist.empty and "Data" in df_mult_hist.columns:
            _sec_hdr("📊 Gráfico de Múltiplos — Histórico")
            pct_cols = [c for c in ("Margem_Liquida", "Margem_Operacional",
                                     "ROE", "ROA", "ROIC", "DY", "Payout")
                        if c in df_mult_hist.columns]
            if pct_cols:
                opcoes_m = [c.replace("_", " ") for c in pct_cols]
                deflt_m  = opcoes_m[:2]
                sel_m    = st.multiselect("Indicadores (%)", opcoes_m, default=deflt_m,
                                          key=f"b3_mult_sel_{tk}")
                if sel_m:
                    m2col = {c.replace("_", " "): c for c in pct_cols}
                    sel_cols = [m2col[s] for s in sel_m if s in m2col]
                    pm = df_mult_hist[["Data"] + sel_cols].copy()
                    for c in sel_cols:
                        pm[c] = pd.to_numeric(pm[c], errors="coerce")
                        # Normaliza para display % (mesma lógica _g_pct)
                        th = _MAX_DECIMAL_PCT.get(c, 2.0)
                        pm[c] = pm[c].apply(
                            lambda v: v if (v != v or abs(v) > th) else v * 100.0)
                    melt_m = pm.melt("Data", value_vars=sel_cols,
                                      var_name="Indicador", value_name="Valor (%)")
                    melt_m["Indicador"] = melt_m["Indicador"].str.replace("_", " ")
                    fig_m = px.bar(melt_m, x="Data", y="Valor (%)", color="Indicador",
                                   barmode="group",
                                   color_discrete_sequence=[
                                       _COR_POS, _COR_INF, _COR_ALT, _COR_NEG,
                                       "#9B59B6", "#E67E22"])
                    fig_m.update_layout(**_plot_layout(300))
                    st.plotly_chart(fig_m, use_container_width=True,
                                    config={"displayModeBar": False}, key=f"b3_mhist_{tk}")

        # Fluxo de Caixa (condicional)
        fco_cols = [c for c in ("FCO", "FCI", "FCF", "Fluxo_Caixa_Operacional",
                                 "Fluxo_Caixa_Investimento", "Fluxo_Caixa_Livre")
                    if c in df_fin.columns]
        if fco_cols:
            _sec_hdr("💰 Fluxo de Caixa")
            labels_fco = {
                "FCO": "FCO (Operacional)", "FCI": "FCI (Investimento)",
                "FCF": "FCF (Livre)",
                "Fluxo_Caixa_Operacional": "FCO (Operacional)",
                "Fluxo_Caixa_Investimento": "FCI (Investimento)",
                "Fluxo_Caixa_Livre": "FCF (Livre)",
            }
            kpi_cols = st.columns(len(fco_cols), gap="small")
            for idx, fc in enumerate(fco_cols):
                v = _last_val(df_fin, fc)
                lbl = labels_fco.get(fc, fc)
                with kpi_cols[idx]:
                    st.markdown(
                        _ind_card(lbl, _fv(v), "Fonte: BD",
                                  _cor_val(v) if v is not None else _COR_NEU),
                        unsafe_allow_html=True,
                    )
            plot_fc = df_fin[["Data"] + fco_cols].copy()
            for c in fco_cols:
                plot_fc[c] = pd.to_numeric(plot_fc[c], errors="coerce")
            melt_fc = plot_fc.melt("Data", value_vars=fco_cols,
                                    var_name="Fluxo", value_name="Valor")
            melt_fc["Fluxo"] = melt_fc["Fluxo"].map(labels_fco)
            fig_fc = px.bar(melt_fc, x="Data", y="Valor", color="Fluxo", barmode="group",
                             color_discrete_sequence=[_COR_POS, _COR_NEG, _COR_INF])
            fig_fc.update_layout(**_plot_layout(300))
            st.plotly_chart(fig_fc, use_container_width=True,
                            config={"displayModeBar": False}, key=f"b3_fco_{tk}")

        # Estrutura de Capital e Dívida
        _cap_map = [
            ("Caixa",             ["Caixa", "Caixa_Equivalentes", "Disponibilidades"], False),
            ("Dívida CP",         ["Divida_CP", "Divida_Curto_Prazo"],                  True),
            ("Dívida LP",         ["Divida_LP", "Divida_Longo_Prazo"],                  True),
            ("Dívida Total",      ["Divida_Total"],                                      True),
            ("Dívida Líquida",    ["Divida_Liquida"],                                    True),
            ("Patrimônio Líquido",["Patrimonio_Liquido"],                                False),
        ]
        # resolve qual coluna usar para cada item
        _cap_disp = []
        for lbl, candidatos_c, inv in _cap_map:
            col_found = next((c for c in candidatos_c if c in df_fin.columns), None)
            if col_found:
                _cap_disp.append((lbl, col_found, inv))

        if _cap_disp:
            _sec_hdr("🏛️ Estrutura de Capital e Dívida")
            kpi_c = st.columns(len(_cap_disp), gap="small")
            for idx, (lbl, col_c, inv) in enumerate(_cap_disp):
                v = _last_val(df_fin, col_c)
                with kpi_c[idx]:
                    st.markdown(
                        _ind_card(lbl, _fv(v), "Último período disponível",
                                  _cor_val(v, invert=inv) if v is not None else _COR_NEU),
                        unsafe_allow_html=True,
                    )
            # Gráfico histórico: Caixa, Dívida CP, Dívida LP
            _chart_cap_cols = [c for lbl, c, _ in _cap_disp
                               if any(k in c for k in ("Caixa", "Divida_CP", "Divida_LP",
                                                        "Disponib", "Curto", "Longo"))]
            if _chart_cap_cols and "Data" in df_fin.columns:
                plot_cap = df_fin[["Data"] + _chart_cap_cols].copy()
                for c in _chart_cap_cols:
                    plot_cap[c] = pd.to_numeric(plot_cap[c], errors="coerce")
                lbl_map = {c: lbl for lbl, c, _ in _cap_disp}
                melt_cap = plot_cap.melt("Data", value_vars=_chart_cap_cols,
                                          var_name="Item", value_name="Valor (R$)")
                melt_cap["Item"] = melt_cap["Item"].map(lbl_map)
                fig_cap = px.bar(melt_cap, x="Data", y="Valor (R$)", color="Item",
                                  barmode="group",
                                  color_discrete_sequence=[_COR_POS, _COR_ALT, _COR_NEG])
                fig_cap.update_layout(**_plot_layout(300))
                st.plotly_chart(fig_cap, use_container_width=True,
                                config={"displayModeBar": False}, key=f"b3_cap_{tk}")

    elif df_precos.empty:
        st.warning("Dados financeiros não encontrados. Configure `SUPABASE_DB_URL_B3`.",
                   icon="⚠️")

    # ══════════════════════════════════════════════════════════════════════════
    # SEÇÃO 3 — Múltiplos Fundamentalistas (agrupados)
    # ══════════════════════════════════════════════════════════════════════════
    _sec_hdr("📐 Rentabilidade")
    inds_rent = _build_indicators(mult, fontes=fontes_recon, grupo="rentabilidade")
    if any(v != "—" for _, v, _, _ in inds_rent):
        _render_cards(inds_rent, n_cols=3)
    else:
        st.caption("Dados de rentabilidade não disponíveis.")

    _sec_hdr("💹 Valuation")
    inds_val = _build_indicators(mult, fontes=fontes_recon, grupo="valuation")
    if any(v != "—" for _, v, _, _ in inds_val):
        _render_cards(inds_val, n_cols=4)
    else:
        st.caption("Dados de valuation não disponíveis.")

    _sec_hdr("🏗️ Estrutura de Capital")
    inds_est = _build_indicators(mult, fontes=fontes_recon, grupo="estrutura")
    if any(v != "—" for _, v, _, _ in inds_est):
        _render_cards(inds_est, n_cols=3)
    else:
        st.caption("Dados de estrutura não disponíveis.")


# ══════════════════════════════════════════════════════════════════════════════
# TAB 3 — Análise Avançada
# ══════════════════════════════════════════════════════════════════════════════

def _tab_avancada(df_set: pd.DataFrame) -> None:
    if df_set.empty:
        st.warning("Banco não configurado. Configure `SUPABASE_DB_URL_B3`.")
        return

    # Dados base
    with st.spinner("Carregando múltiplos e histórico…"):
        df_mult_todos = _db.load_multiplos_todos()
        anos_hist     = _db.load_historico_anos()

    # ── FILTROS ──────────────────────────────────────────────────────────────
    _sec_hdr("⚙️ Filtros do Universo")
    fc1, fc2, fc3, fc4 = st.columns(4)

    setores  = ["Todos"] + sorted(s for s in df_set["SETOR"].unique() if s)
    sel_set  = fc1.selectbox("Setor",    setores,  key="b3_av_setor")

    df_s     = df_set if sel_set == "Todos" else df_set[df_set["SETOR"] == sel_set]
    subs     = ["Todos"] + sorted(s for s in df_s["SUBSETOR"].unique() if s)
    sel_sub  = fc2.selectbox("Subsetor", subs,     key="b3_av_sub")

    df_ss    = df_s if sel_sub == "Todos" else df_s[df_s["SUBSETOR"] == sel_sub]
    segs     = ["Todos"] + sorted(s for s in df_ss["SEGMENTO"].unique() if s)
    sel_seg  = fc3.selectbox("Segmento", segs,     key="b3_av_seg")

    perfis   = ["Todas", "Crescimento (<10 anos de histórico)", "Estabelecida (≥10 anos)"]
    sel_perf = fc4.selectbox("Perfil",   perfis,   key="b3_av_perf")

    with st.expander("⚖️ Pesos do Scoring"):
        usar_pesos_setor = st.checkbox(
            "Usar pesos calibrados por setor (recomendado)",
            value=True, key="b3_av_use_setor_w",
        )
        sp1, sp2, sp3 = st.columns(3)
        sp4, sp5, sp6 = st.columns(3)
        p_roe  = sp1.slider("ROE",            0, 50, 25, key="b3_av_proe",
                             disabled=usar_pesos_setor)
        p_roic = sp2.slider("ROIC",           0, 50, 20, key="b3_av_proic",
                             disabled=usar_pesos_setor)
        p_marg = sp3.slider("Margem Líquida", 0, 50, 15, key="b3_av_pmarg",
                             disabled=usar_pesos_setor)
        p_dy   = sp4.slider("DY",             0, 30, 10, key="b3_av_pdy",
                             disabled=usar_pesos_setor)
        p_div  = sp5.slider("Endividamento",  0, 30, 20, key="b3_av_pdiv",
                             disabled=usar_pesos_setor)
        p_liq  = sp6.slider("Liquidez",       0, 30, 10, key="b3_av_pliq",
                             disabled=usar_pesos_setor)

    pesos_usuario_raw: dict[str, float] | None = None if usar_pesos_setor else {
        "ROE": float(p_roe), "ROIC": float(p_roic), "Margem_Liquida": float(p_marg),
        "DY": float(p_dy), "Endividamento_Total": float(p_div),
        "Liquidez_Corrente": float(p_liq),
    }

    # Aplicar filtros
    df_filt  = df_ss if sel_seg == "Todos" else df_ss[df_ss["SEGMENTO"] == sel_seg]
    tks_uni  = df_filt["ticker"].tolist()

    if anos_hist:
        if "Crescimento" in sel_perf:
            tks_uni = [tk for tk in tks_uni if anos_hist.get(tk, 99) < 10]
        elif "Estabelecida" in sel_perf:
            tks_uni = [tk for tk in tks_uni if anos_hist.get(tk, 0) >= 10]

    _MAX_UNI = 40
    if len(tks_uni) > _MAX_UNI:
        st.info(
            f"Universo com **{len(tks_uni)}** empresas — limitado a {_MAX_UNI} "
            "para performance. Refine os filtros para resultados mais precisos."
        )
        tks_uni = tks_uni[:_MAX_UNI]

    if not tks_uni:
        st.info("Nenhuma empresa encontrada com os filtros selecionados.")
        return

    # Enriquecer df_mult com colunas de agrupamento vindas de df_set
    df_mult_enrich = df_mult_todos.copy()
    _gcols = [c for c in ("SETOR", "SUBSETOR", "SEGMENTO") if c in df_set.columns]
    _tk2g: dict[str, dict] = {}
    if _gcols:
        _tk2g = df_set.set_index("ticker")[_gcols].to_dict("index")
        for gc in _gcols:
            if gc not in df_mult_enrich.columns:
                df_mult_enrich[gc] = df_mult_enrich["Ticker"].map(
                    lambda t, g=gc: (_tk2g.get(t) or {}).get(g)
                )
    # Dicionário de grupos por ticker — passado ao backtest para scoring intra-grupo
    tk_grupos = {tk: _tk2g.get(tk, {}) for tk in tks_uni}

    # Carregar histórico para penalidade de instabilidade + slope_log
    with st.spinner("Calculando scoring v2…"):
        hist_batch = _db.load_multiplos_historico_batch(tuple(sorted(tks_uni)))
        selic_macro = _db.load_selic_macro()

    # Enriquecer com slope_log antes do scoring
    df_mult_enrich = _enrich_com_slopes(df_mult_enrich, hist_batch)

    # Pesos por setor ou manuais
    setor_prevalente = (
        df_filt["SETOR"].value_counts().idxmax()
        if "SETOR" in df_filt.columns and not df_filt.empty else ""
    )
    pesos_v2 = _get_pesos_setor(setor_prevalente, pesos_usuario_raw)

    # Scoring v2 — coluna de grupo: mais granular que o filtro atual
    group_prefer = (
        "SUBSETOR" if sel_seg != "Todos" else
        "SEGMENTO" if sel_sub != "Todos" else
        "SEGMENTO"
    )
    df_scored = _score_universo(
        df_mult_enrich, tks_uni, pesos_v2,
        df_hist_batch=hist_batch,
        group_col_prefer=group_prefer,
    )
    tk_info       = {row["ticker"]: row for _, row in df_filt.iterrows()}
    tks_com_score = set(df_scored["Ticker"].tolist()) if not df_scored.empty else set()
    tks_sem_mult  = [tk for tk in tks_uni if tk not in tks_com_score]

    # ── CARDS DO UNIVERSO ────────────────────────────────────────────────────
    _sec_hdr(f"🏢 Universo Filtrado — {len(tks_uni)} empresa(s)")
    show_tks = (df_scored["Ticker"].tolist() if not df_scored.empty else []) + tks_sem_mult

    for i in range(0, min(len(show_tks), 20), 4):
        cols_c = st.columns(4, gap="small")
        for j, tk in enumerate(show_tks[i:i+4]):
            info  = tk_info.get(tk, {})
            nome  = str(info.get("nome_empresa", tk) or tk)[:28]
            anos  = anos_hist.get(tk, "?")
            rank_row = df_scored[df_scored["Ticker"] == tk] if not df_scored.empty else pd.DataFrame()
            score    = float(rank_row["score"].iloc[0])   if not rank_row.empty else 0.0
            rank     = int(rank_row["ranking"].iloc[0])   if not rank_row.empty else "—"
            badge_cls = (
                "b3-score-high" if score >= 70 else
                "b3-score-mid"  if score >= 40 else
                "b3-score-low"
            )
            with cols_c[j]:
                st.markdown(
                    f'<div class="b3-card">'
                    f'<div style="display:flex;align-items:center;gap:8px;margin-bottom:6px;">'
                    f'<img src="{_logo_url(tk)}" class="b3-card-logo"'
                    f'     onerror="this.style.display=\'none\'">'
                    f'<div style="flex:1;overflow:hidden;">'
                    f'<div class="b3-card-ticker">#{rank} {tk}</div>'
                    f'<div class="b3-card-nome">{nome}</div>'
                    f'</div></div>'
                    f'<div style="display:flex;justify-content:space-between;align-items:center;">'
                    f'<span class="b3-score-badge {badge_cls}">Score {score:.0f}</span>'
                    f'<span class="b3-card-tag">{anos} anos DRE</span>'
                    f'</div></div>',
                    unsafe_allow_html=True,
                )

    # ── BACKTESTING ──────────────────────────────────────────────────────────
    st.markdown("<hr style='margin:20px 0;border-color:#1E2533;'>", unsafe_allow_html=True)
    _sec_hdr("📈 Simulação de Patrimônio — Aportes Mensais")

    bk1, bk2, bk3, bk4 = st.columns(4)
    aporte   = bk1.number_input(
        "Aporte mensal (R$)", 100.0, 50000.0, 1000.0, 100.0, key="b3_av_aporte"
    )
    top_n    = bk2.selectbox("Top-N Estratégia", [1, 3, 5, 10], index=1, key="b3_av_topn")
    per_opts = {"1 ano": "1y", "3 anos": "3y", "5 anos": "5y", "10 anos": "10y"}
    sel_per  = bk3.selectbox("Período", list(per_opts.keys()), index=2, key="b3_av_per")

    # Selic: usa média da tabela macro como default quando disponível
    _selic_macro_media = (
        float(np.mean(list(selic_macro.values()))) * 100
        if selic_macro else 10.75
    )
    taxa_sel = bk4.number_input(
        "Taxa Selic a.a. (%) — fallback",
        0.0, 50.0, round(_selic_macro_media, 2), 0.25, key="b3_av_selic",
        help="Quando a tabela macro do App 1 está disponível, cada ano usa a taxa real. "
             "Este valor é usado como fallback para anos sem cobertura.",
    )

    # Auto-calibração γ / cap / soft
    with st.expander("🔧 Auto-calibração de parâmetros (γ, cap, soft)"):
        st.caption(
            "Testa 36 combinações (4γ × 3cap × 3soft) nos últimos 36 meses de preços. "
            "Objetivo: CAGR − 0.60×vol + 0.40×MDD. Shrinkage 40 % ao default."
        )
        if st.button("⚙️ Calibrar agora", key="b3_av_btn_cal"):
            with st.spinner("Calibrando parâmetros…"):
                per_cal    = per_opts.get(sel_per, "3y")
                tks_cal    = tuple(sorted(tks_uni))
                df_prec_cal = _batch_yf_precos_mensais(tks_cal, period=per_cal)
                g_cal, c_cal, s_cal = _calibrate_gamma_cap_soft(
                    df_prec_cal, df_scored, tks_uni,
                    float(taxa_sel) / 100.0, float(aporte),
                )
            st.session_state["b3_av_gamma"] = g_cal
            st.session_state["b3_av_cap"]   = c_cal
            st.session_state["b3_av_soft"]  = s_cal
            st.success(f"γ={g_cal:.3f}  cap={c_cal:.3f}  soft={s_cal:.3f}")

        _g_cur = st.session_state.get("b3_av_gamma", _GAMMA_DEF)
        _c_cur = st.session_state.get("b3_av_cap",   _CAP_DEF)
        _s_cur = st.session_state.get("b3_av_soft",  _SOFT_DEF)
        st.caption(
            f"Parâmetros ativos: **γ={_g_cur:.3f}** · **cap={_c_cur:.3f}** · **soft={_s_cur:.3f}**"
        )

    if st.button("▶ Simular Backtest", type="primary", key="b3_av_btn_simular"):
        period_code = per_opts[sel_per]
        tks_tuple   = tuple(sorted(tks_uni))
        usar_gamma  = len(tks_uni) >= 5
        anos_map    = {"1y": 1, "3y": 3, "5y": 5, "10y": 10}
        data_inicio = pd.Timestamp.now() - pd.DateOffset(years=anos_map[period_code])

        with st.spinner("Baixando preços mensais… (pode levar alguns segundos)"):
            df_prec_m = _batch_yf_precos_mensais(tks_tuple, period=period_code)

        df_bt, tickers_top, n_efetivo = _simular_backtest(
            df_prec_m, df_scored, hist_batch, tks_uni,
            float(aporte), data_inicio, float(taxa_sel) / 100.0,
            pesos_v2, tk_grupos,
            top_n_max=int(top_n), usar_gamma=usar_gamma,
            gamma=st.session_state.get("b3_av_gamma", _GAMMA_DEF),
            cap=st.session_state.get("b3_av_cap",   _CAP_DEF),
            soft=st.session_state.get("b3_av_soft",  _SOFT_DEF),
            selic_por_ano=selic_macro or None,
        )
        st.session_state["b3_av_bt_df"]    = df_bt
        st.session_state["b3_av_bt_top"]   = tickers_top
        st.session_state["b3_av_bt_aport"] = float(aporte)
        st.session_state["b3_av_bt_n"]     = n_efetivo
        st.session_state["b3_av_bt_gamma"] = usar_gamma

    df_bt      = st.session_state.get("b3_av_bt_df",    pd.DataFrame())
    tks_top    = st.session_state.get("b3_av_bt_top",   [])
    aport_bt   = st.session_state.get("b3_av_bt_aport", 0.0)
    n_efetivo  = st.session_state.get("b3_av_bt_n",     0)
    bt_gamma   = st.session_state.get("b3_av_bt_gamma", False)

    if not df_bt.empty:
        colunas_bt = [c for c in ("Estratégia", "Benchmark", "Tesouro Selic")
                      if c in df_bt.columns]
        melt_bt = df_bt.melt("Data", value_vars=colunas_bt,
                              var_name="Carteira", value_name="Patrimônio (R$)")
        fig_bt = px.line(
            melt_bt, x="Data", y="Patrimônio (R$)", color="Carteira",
            color_discrete_map={
                "Estratégia":    _COR_POS,
                "Benchmark":     _COR_NEU,
                "Tesouro Selic": _COR_ALT,
            },
        )
        fig_bt.update_traces(line_width=2.0)
        fig_bt.update_layout(**_plot_layout(380))
        st.plotly_chart(fig_bt, use_container_width=True,
                        config={"displayModeBar": False}, key="b3_av_bt_chart")

        # KPI patrimônio final
        _sec_hdr("💰 Patrimônio Final")
        ultima      = df_bt.iloc[-1]
        total_aport = aport_bt * len(df_bt) if aport_bt > 0 else 1.0

        kpi_bt = st.columns(3, gap="small")
        for idx, (nome_c, cor_c) in enumerate([
            ("Estratégia", _COR_POS),
            ("Benchmark",  _COR_NEU),
            ("Tesouro Selic", _COR_ALT),
        ]):
            v       = float(ultima.get(nome_c, 0) or 0)
            ret_pct = (v / total_aport - 1) * 100 if total_aport > 0 else 0
            with kpi_bt[idx]:
                st.markdown(
                    _ind_card(
                        nome_c, _fv(v),
                        f"Retorno: {ret_pct:+.1f}% · Aportado: {_fv(total_aport)}",
                        cor_c,
                    ),
                    unsafe_allow_html=True,
                )
        if tks_top:
            modo = "Calibrado (γ-weighted)" if bt_gamma else "Padrão (equal-weight)"
            st.caption(
                f"Modo: **{modo}** · N selecionado: **{n_efetivo}** · "
                f"Estratégia: **{', '.join(tks_top)}**"
            )
    else:
        st.caption("Configure os parâmetros acima e clique **▶ Simular Backtest**.")

    # ── COMPARAÇÃO DE MÚLTIPLOS ───────────────────────────────────────────────
    st.markdown("<hr style='margin:20px 0;border-color:#1E2533;'>", unsafe_allow_html=True)
    _sec_hdr("📊 Comparação de Múltiplos Históricos")

    cm1, cm2 = st.columns([3, 2])
    sels_emp_m = cm1.multiselect(
        "Empresas",
        tks_uni,
        default=tks_uni[:min(5, len(tks_uni))],
        key="b3_av_emp_mult",
    )
    mult_ind_opts = [
        "P/L", "P/VP", "ROE", "ROIC", "DY", "EV_EBIT",
        "Margem_Liquida", "Margem_Operacional", "Payout",
        "Endividamento_Total", "Liquidez_Corrente",
    ]
    sel_ind_m = cm2.selectbox("Indicador", mult_ind_opts, key="b3_av_ind_m")

    if st.button("📈 Comparar Múltiplos", key="b3_av_btn_mult") and sels_emp_m:
        with st.spinner("Carregando histórico de múltiplos…"):
            batch_mh = _db.load_multiplos_historico_batch(tuple(sorted(sels_emp_m)))
        st.session_state["b3_av_mh_batch"] = batch_mh
        st.session_state["b3_av_mh_ind"]   = sel_ind_m

    batch_mh   = st.session_state.get("b3_av_mh_batch", {})
    ind_m_show = st.session_state.get("b3_av_mh_ind",   sel_ind_m)

    if batch_mh:
        rows_mh: list[pd.DataFrame] = []
        is_pct_m = ind_m_show in _MAX_DECIMAL_PCT
        for tk_mh, df_mh in batch_mh.items():
            if "Data" not in df_mh.columns or ind_m_show not in df_mh.columns:
                continue
            tmp = df_mh[["Data", ind_m_show]].copy()
            tmp[ind_m_show] = pd.to_numeric(tmp[ind_m_show], errors="coerce")
            if is_pct_m:
                th = _MAX_DECIMAL_PCT.get(ind_m_show, 2.0)
                tmp[ind_m_show] = tmp[ind_m_show].apply(
                    lambda v: v if (pd.isna(v) or abs(v) > th) else v * 100.0
                )
            tmp = tmp.rename(columns={ind_m_show: "Valor"})
            tmp["Empresa"] = tk_mh
            rows_mh.append(tmp)

        if rows_mh:
            df_comp_m = pd.concat(rows_mh, ignore_index=True)
            y_lbl = (
                f"{ind_m_show.replace('_', ' ')} (%)"
                if is_pct_m else
                f"{ind_m_show.replace('_', ' ')} (×)"
            )
            fig_cm = px.line(
                df_comp_m, x="Data", y="Valor", color="Empresa", markers=True,
                labels={"Valor": y_lbl},
                color_discrete_sequence=[
                    _COR_POS, _COR_INF, _COR_ALT, _COR_NEG,
                    "#9B59B6", "#E67E22", "#1ABC9C", _COR_NEU],
            )
            fig_cm.update_layout(**_plot_layout(380))
            fig_cm.update_layout(yaxis_title=y_lbl)
            st.plotly_chart(fig_cm, use_container_width=True,
                            config={"displayModeBar": False}, key="b3_av_mh_chart")
        else:
            st.caption("Indicador não disponível para as empresas selecionadas.")
    else:
        st.caption("Selecione empresas e indicador acima e clique **📈 Comparar Múltiplos**.")

    # ── COMPARAÇÃO DE DRE ────────────────────────────────────────────────────
    st.markdown("<hr style='margin:20px 0;border-color:#1E2533;'>", unsafe_allow_html=True)
    _sec_hdr("📋 Comparação de Demonstrações Financeiras")

    _dre_labels = {
        "Receita_Liquida":   "Receita Líquida",
        "EBIT":              "EBIT",
        "Lucro_Liquido":     "Lucro Líquido",
        "Patrimonio_Liquido":"Patrimônio Líquido",
        "Divida_Liquida":    "Dívida Líquida",
        "Divida_Total":      "Dívida Total",
        "Ativo_Total":       "Ativo Total",
        "Dividendos":        "Dividendos",
    }
    _lbl2col_dre = {v: k for k, v in _dre_labels.items()}

    cd1, cd2 = st.columns([2, 3])
    sel_dre_lbl = cd1.selectbox(
        "Item da DRE", list(_dre_labels.values()), key="b3_av_dre_item"
    )
    sels_emp_d = cd2.multiselect(
        "Empresas",
        tks_uni,
        default=tks_uni[:min(5, len(tks_uni))],
        key="b3_av_emp_dre",
    )

    if st.button("📋 Comparar DRE", key="b3_av_btn_dre") and sels_emp_d:
        dre_col_sel = _lbl2col_dre.get(sel_dre_lbl, "Receita_Liquida")
        with st.spinner("Carregando demonstrações financeiras…"):
            batch_dre = _db.load_demonstracoes_batch(tuple(sorted(sels_emp_d)))
        st.session_state["b3_av_dre_batch"] = batch_dre
        st.session_state["b3_av_dre_col"]   = dre_col_sel
        st.session_state["b3_av_dre_lbl"]   = sel_dre_lbl

    batch_dre = st.session_state.get("b3_av_dre_batch", {})
    dre_col_s = st.session_state.get("b3_av_dre_col",   "Receita_Liquida")
    dre_lbl_s = st.session_state.get("b3_av_dre_lbl",   "Receita Líquida")

    if batch_dre:
        rows_d: list[pd.DataFrame] = []
        for tk_d, df_d in batch_dre.items():
            if "Data" not in df_d.columns or dre_col_s not in df_d.columns:
                continue
            tmp = df_d[["Data", dre_col_s]].copy()
            tmp[dre_col_s] = pd.to_numeric(tmp[dre_col_s], errors="coerce")
            tmp["Ano"]     = tmp["Data"].dt.year.astype(str)
            tmp["Empresa"] = tk_d
            rows_d.append(tmp)

        if rows_d:
            df_comp_d = pd.concat(rows_d, ignore_index=True)
            fig_cd = px.bar(
                df_comp_d, x="Ano", y=dre_col_s, color="Empresa",
                barmode="group",
                labels={dre_col_s: f"{dre_lbl_s} (R$)"},
                color_discrete_sequence=[
                    _COR_POS, _COR_INF, _COR_ALT, _COR_NEG,
                    "#9B59B6", "#E67E22", "#1ABC9C", _COR_NEU],
            )
            fig_cd.update_layout(**_plot_layout(400))
            st.plotly_chart(fig_cd, use_container_width=True,
                            config={"displayModeBar": False}, key="b3_av_dre_chart")
        else:
            st.caption("Item de DRE não disponível para as empresas selecionadas.")
    else:
        st.caption("Selecione empresas e item da DRE acima e clique **📋 Comparar DRE**.")

    # ── QUADRO COMPARATIVO ───────────────────────────────────────────────────
    st.markdown("<hr style='margin:20px 0;border-color:#1E2533;'>", unsafe_allow_html=True)
    _sec_hdr("📊 Quadro Comparativo — Indicadores por Empresa")
    st.caption("Verde = top 25% · Vermelho = bottom 25% (considerando direção do indicador).")

    if not df_mult_todos.empty and tks_uni:
        df_qc = df_mult_todos[df_mult_todos["Ticker"].isin(tks_uni)].copy()
        cols_disp = [(c, lbl) for c, lbl in _COLS_COMP if c in df_qc.columns]
        if cols_disp:
            rows_qc = []
            for _, r in df_qc.iterrows():
                row_d: dict = {"Empresa": r["Ticker"]}
                for c, lbl in cols_disp:
                    row_d[lbl] = pd.to_numeric(r.get(c), errors="coerce")
                rows_qc.append(row_d)
            df_tbl = pd.DataFrame(rows_qc).set_index("Empresa")

            def _style_col(col: pd.Series) -> list[str]:
                is_inv = col.name in _INV_LABELS
                q25    = col.quantile(0.25)
                q75    = col.quantile(0.75)
                styles = []
                for v in col:
                    if pd.isna(v):
                        styles.append("")
                    elif (v >= q75 and not is_inv) or (v <= q25 and is_inv):
                        styles.append("background-color:rgba(0,200,150,.15);color:#00C896")
                    elif (v <= q25 and not is_inv) or (v >= q75 and is_inv):
                        styles.append("background-color:rgba(252,92,125,.15);color:#FC5C7D")
                    else:
                        styles.append("")
                return styles

            styled = df_tbl.style.apply(_style_col, axis=0).format(
                {lbl: "{:.2f}" for _, lbl in cols_disp}, na_rep="—"
            )
            st.dataframe(styled, use_container_width=True,
                         height=min(420, 50 + 35 * len(df_tbl)))
        else:
            st.caption("Nenhum indicador disponível para o universo selecionado.")
    else:
        st.caption("Sem dados para o universo selecionado.")

    # ── SCATTER PLOT — 2 INDICADORES ─────────────────────────────────────────
    st.markdown("<hr style='margin:20px 0;border-color:#1E2533;'>", unsafe_allow_html=True)
    _sec_hdr("🔭 Scatter Plot — Correlação entre Indicadores")
    st.caption("Linhas pontilhadas = mediana. Cores por score v2.")

    _sc_opts = [c for c, _ in _COLS_COMP
                if not df_mult_todos.empty and c in df_mult_todos.columns]
    if len(_sc_opts) >= 2:
        sc1, sc2 = st.columns(2)
        ind_x = sc1.selectbox("Eixo X", _sc_opts, index=0, key="b3_av_scx")
        ind_y = sc2.selectbox("Eixo Y", _sc_opts, index=min(1, len(_sc_opts) - 1),
                              key="b3_av_scy")

        if st.button("🔭 Gerar Scatter", key="b3_av_btn_scatter"):
            df_sc = df_mult_todos[df_mult_todos["Ticker"].isin(tks_uni)].copy()
            if ind_x in df_sc.columns and ind_y in df_sc.columns:
                df_sc[ind_x] = pd.to_numeric(df_sc[ind_x], errors="coerce")
                df_sc[ind_y] = pd.to_numeric(df_sc[ind_y], errors="coerce")
                df_sc[ind_x] = _winsorize_series(df_sc[ind_x].dropna()).reindex(df_sc.index)
                df_sc[ind_y] = _winsorize_series(df_sc[ind_y].dropna()).reindex(df_sc.index)
                df_sc = df_sc.dropna(subset=[ind_x, ind_y])
                if not df_scored.empty:
                    df_sc = df_sc.merge(df_scored[["Ticker", "score"]], on="Ticker", how="left")
                st.session_state["b3_av_sc_data"] = df_sc
                st.session_state["b3_av_sc_xy"]   = (ind_x, ind_y)

        sc_data = st.session_state.get("b3_av_sc_data", pd.DataFrame())
        sc_xy   = st.session_state.get("b3_av_sc_xy",   (ind_x, ind_y))
        if not sc_data.empty and sc_xy[0] in sc_data.columns and sc_xy[1] in sc_data.columns:
            med_x = sc_data[sc_xy[0]].median()
            med_y = sc_data[sc_xy[1]].median()
            color_arg = "score" if "score" in sc_data.columns else None
            color_kw  = ({"color_continuous_scale": [[0, _COR_NEG], [0.5, _COR_ALT],
                                                      [1, _COR_POS]]}
                         if color_arg else {})
            fig_sc = px.scatter(
                sc_data, x=sc_xy[0], y=sc_xy[1], text="Ticker",
                color=color_arg, **color_kw,
                labels={sc_xy[0]: sc_xy[0].replace("_", " "),
                        sc_xy[1]: sc_xy[1].replace("_", " ")},
            )
            fig_sc.add_vline(x=med_x, line_dash="dot", line_color="#4A5568")
            fig_sc.add_hline(y=med_y, line_dash="dot", line_color="#4A5568")
            fig_sc.update_traces(textposition="top center", textfont_size=9)
            fig_sc.update_layout(**_plot_layout(440))
            st.plotly_chart(fig_sc, use_container_width=True,
                            config={"displayModeBar": False}, key="b3_av_sc_chart")
        else:
            st.caption("Clique **🔭 Gerar Scatter** para plotar.")
    else:
        st.caption("Indicadores insuficientes para scatter.")

    # ── FCO / LUCRO — QUALIDADE DO RESULTADO ─────────────────────────────────
    st.markdown("<hr style='margin:20px 0;border-color:#1E2533;'>", unsafe_allow_html=True)
    _sec_hdr("💵 FCO / Lucro Líquido — Qualidade do Resultado")
    st.caption(
        "Ratio > 1: caixa operacional supera o lucro contábil (sinal de qualidade). "
        "Ratio < 0.5: lucro pode não estar se convertendo em caixa."
    )

    if st.button("💵 Calcular FCO/Lucro", key="b3_av_btn_fco"):
        with st.spinner("Carregando demonstrações…"):
            batch_fco = _db.load_demonstracoes_batch(tuple(sorted(tks_uni)))
        fco_rows: list[dict] = []
        for tk_f, df_f in batch_fco.items():
            fco_col = next((c for c in ("FCO", "Fluxo_Caixa_Operacional")
                            if c in df_f.columns), None)
            ll_col  = next((c for c in ("Lucro_Liquido",) if c in df_f.columns), None)
            if not fco_col or not ll_col or df_f.empty:
                continue
            last = df_f.sort_values("Data").iloc[-1] if "Data" in df_f.columns else df_f.iloc[-1]
            fco  = pd.to_numeric(last.get(fco_col), errors="coerce")
            ll   = pd.to_numeric(last.get(ll_col),  errors="coerce")
            if pd.notna(fco) and pd.notna(ll) and abs(ll) > 1e-6:
                fco_rows.append({"Empresa": tk_f, "FCO": fco, "Lucro": ll,
                                  "FCO/Lucro": fco / ll})
        st.session_state["b3_av_fco_rows"] = fco_rows

    fco_data = st.session_state.get("b3_av_fco_rows", [])
    if fco_data:
        df_fco = pd.DataFrame(fco_data).sort_values("FCO/Lucro", ascending=False)
        fig_fco = px.bar(
            df_fco, x="Empresa", y="FCO/Lucro",
            color="FCO/Lucro",
            color_continuous_scale=[[0, _COR_NEG], [0.33, _COR_ALT], [0.67, _COR_POS],
                                     [1, _COR_POS]],
            labels={"FCO/Lucro": "FCO / Lucro Líq."},
        )
        fig_fco.add_hline(y=1.0, line_dash="dot", line_color="#4A5568",
                          annotation_text="FCO = Lucro", annotation_position="top right")
        fig_fco.update_layout(**_plot_layout(360))
        st.plotly_chart(fig_fco, use_container_width=True,
                        config={"displayModeBar": False}, key="b3_av_fco_chart")

        # Mini-tabela com os valores
        df_fco_disp = df_fco[["Empresa", "FCO", "Lucro", "FCO/Lucro"]].copy()
        df_fco_disp["FCO"]      = df_fco_disp["FCO"].map(_fv)
        df_fco_disp["Lucro"]    = df_fco_disp["Lucro"].map(_fv)
        df_fco_disp["FCO/Lucro"] = df_fco_disp["FCO/Lucro"].map(lambda v: f"{v:.2f}x")
        st.dataframe(df_fco_disp.set_index("Empresa"), use_container_width=True,
                     height=min(300, 50 + 35 * len(df_fco_disp)))
    else:
        st.caption("Clique **💵 Calcular FCO/Lucro** para gerar o gráfico.")


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

    col_t1, col_t2, col_t3, _ = st.columns([2, 2, 2.5, 3.5])
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
    with col_t3:
        if st.button("🔬 Análise Avançada", use_container_width=True,
                     type="primary" if active == 2 else "secondary",
                     key="b3_tab2"):
            st.session_state["b3_active_tab"] = 2
            st.rerun()

    st.markdown("<hr style='margin:4px 0 16px;border-color:#1E2533;'>",
                unsafe_allow_html=True)

    if active == 0:
        _tab_empresas(df_set)
    elif active == 1:
        _tab_analise(df_set)
    else:
        _tab_avancada(df_set)
