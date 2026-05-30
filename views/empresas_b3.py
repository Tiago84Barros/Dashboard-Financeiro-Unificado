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
/* Score de Entrada */
.b3-entrada-aprovada   { background:rgba(0,200,150,.12);border:1px solid rgba(0,200,150,.30);
                          color:#00C896;border-radius:6px;padding:3px 10px;
                          font-size:0.72rem;font-weight:800; }
.b3-entrada-observacao { background:rgba(246,201,14,.12);border:1px solid rgba(246,201,14,.30);
                          color:#F6C90E;border-radius:6px;padding:3px 10px;
                          font-size:0.72rem;font-weight:800; }
.b3-entrada-excluida   { background:rgba(252,92,125,.12);border:1px solid rgba(252,92,125,.30);
                          color:#FC5C7D;border-radius:6px;padding:3px 10px;
                          font-size:0.72rem;font-weight:800; }
.b3-engine-row { display:flex;align-items:center;gap:6px;margin-bottom:4px;
                 font-size:0.74rem; }
.b3-engine-bar-bg { flex:1;background:#1E2533;border-radius:4px;height:6px;overflow:hidden; }
.b3-engine-bar-fill { height:100%;border-radius:4px;transition:width .3s; }
.b3-fator-pos { color:#00C896;font-size:0.68rem;font-weight:700; }
.b3-fator-neg { color:#FC5C7D;font-size:0.68rem;font-weight:700; }
.b3-fator-neu { color:#718096;font-size:0.68rem; }
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


@st.cache_data(ttl=86400, show_spinner=False)
def _batch_yf_liquidez(tickers: tuple[str, ...]) -> dict[str, float]:
    """
    Liquidez média diária negociada (R$/dia) dos últimos ~3 meses via yfinance.

    Calcula a MEDIANA de (Close × Volume) por pregão — robusta a dias atípicos.
    Liquidez muda lentamente, então cache de 24h é suficiente.

    Returns:
      {ticker_sem_sa: liquidez_mediana_diaria_rs}
    """
    if not tickers:
        return {}
    tks_sa = [f"{t.strip().upper().replace('.SA', '')}.SA" for t in tickers]
    out: dict[str, float] = {}
    try:
        if len(tks_sa) == 1:
            raw = yf.download(tks_sa[0], period="3mo", interval="1d",
                              auto_adjust=True, progress=False)
            if raw is None or raw.empty:
                return {}
            tk = tks_sa[0].replace(".SA", "").upper()
            fin = (raw["Close"] * raw["Volume"]).dropna()
            if not fin.empty:
                out[tk] = float(fin.median())
        else:
            raw = yf.download(tks_sa, period="3mo", interval="1d",
                              auto_adjust=True, progress=False)
            if raw is None or raw.empty:
                return {}
            if isinstance(raw.columns, pd.MultiIndex):
                close = raw["Close"]
                vol   = raw["Volume"]
                fin   = (close * vol)
                for col in fin.columns:
                    s = fin[col].dropna()
                    tk = str(col).replace(".SA", "").strip().upper()
                    if not s.empty:
                        out[tk] = float(s.median())
    except Exception:
        return out
    return out


def _prerank_tickers(df_mult: pd.DataFrame, tickers: list[str]) -> list[str]:
    """
    Ordena tickers por um proxy fundamental barato (sem histórico/slopes).

    Usado para escolher QUAIS empresas manter quando o universo excede o
    teto — em vez de truncar por ordem alfabética/de aparição, mantém as
    de melhores fundamentos disponíveis. Composto: percentis de ROE, ROIC,
    DY, Margem_Liquida (alto=bom) e Endividamento_Total (baixo=bom).

    Tickers ausentes em df_mult vão para o fim (preservados).
    """
    cols_hi = ("ROE", "ROIC", "DY", "Margem_Liquida")
    sub = df_mult[df_mult["Ticker"].isin(tickers)].copy()
    if sub.empty:
        return list(tickers)
    score = pd.Series(0.0, index=sub.index)
    usados = 0
    for c in cols_hi:
        if c not in sub.columns:
            continue
        v = pd.to_numeric(sub[c], errors="coerce")
        if v.notna().sum() < 2:
            continue
        score += v.rank(pct=True).fillna(0.5)
        usados += 1
    if "Endividamento_Total" in sub.columns:
        v = pd.to_numeric(sub["Endividamento_Total"], errors="coerce")
        if v.notna().sum() >= 2:
            score += (1.0 - v.rank(pct=True).fillna(0.5))
            usados += 1
    if usados == 0:
        return list(tickers)
    sub["_prerank"] = score
    ordered = sub.sort_values("_prerank", ascending=False)["Ticker"].tolist()
    missing = [t for t in tickers if t not in set(ordered)]
    return ordered + missing


def _aplicar_diversificacao_setorial(
    tickers_ranked: list[str],
    group_map:      dict[str, str],
    top_n:          int,
    max_por_setor:  int,
) -> list[str]:
    """
    Seleciona top-N respeitando teto de ações por setor.

    Percorre os tickers já ordenados (melhor→pior); inclui enquanto o setor
    não estourou o teto. Se o teto impedir atingir top_n (ex.: poucos setores),
    completa com os excedentes melhor-ranqueados para não devolver carteira
    menor que o solicitado.
    """
    if max_por_setor <= 0:
        return tickers_ranked[:top_n]
    sel: list[str] = []
    overflow: list[str] = []
    cont: dict[str, int] = {}
    for tk in tickers_ranked:
        setor = group_map.get(tk) or "—"
        if cont.get(setor, 0) < max_por_setor:
            sel.append(tk)
            cont[setor] = cont.get(setor, 0) + 1
        else:
            overflow.append(tk)
        if len(sel) >= top_n:
            break
    if len(sel) < top_n:
        sel += overflow[: top_n - len(sel)]
    return sel[:top_n]


@st.cache_data(ttl=3600, show_spinner=False)
def _yf_trailing12m_divs(ticker: str) -> float:
    """
    Soma de dividendos por ação pagos nos últimos 12 meses via yfinance (R$/ação).
    Fallback: soma do último ano disponível no histórico.
    Evita .last() depreciado no pandas 2.x — usa filtro explícito por data.
    """
    tk_clean = ticker.strip().upper().replace(".SA", "")
    for var in [f"{tk_clean}.SA", tk_clean]:
        try:
            divs = yf.Ticker(var).dividends
            if divs is None or divs.empty:
                continue
            if hasattr(divs.index, "tz") and divs.index.tz is not None:
                divs.index = divs.index.tz_localize(None)
            divs.index = pd.to_datetime(divs.index)
            cutoff = pd.Timestamp.now() - pd.Timedelta(days=365)
            trail = float(divs[divs.index >= cutoff].sum())
            if trail > 0:
                return trail
            # fallback: último ano com dados
            last_yr = int(divs.index.year.max())
            total_yr = float(divs[divs.index.year == last_yr].sum())
            if total_yr > 0:
                return total_yr
        except Exception:
            pass
    return 0.0


@st.cache_data(ttl=3600, show_spinner=False)
def _batch_yf_dividendos_mensais(tickers: tuple[str, ...], period: str = "10y") -> dict[str, pd.Series]:
    """
    Dividendos mensais por ação para múltiplos tickers via yfinance.
    Retorna dict {ticker_sem_SA: pd.Series(DatetimeIndex mensal, float R$/ação)}.
    Múltiplos pagamentos no mesmo mês são somados.
    """
    resultado: dict[str, pd.Series] = {}
    if not tickers:
        return resultado
    for tk in tickers:
        tk_clean = tk.strip().upper().replace(".SA", "")
        for var in [f"{tk_clean}.SA", tk_clean]:
            try:
                divs = yf.Ticker(var).dividends
                if divs is not None and not divs.empty:
                    if hasattr(divs.index, "tz") and divs.index.tz is not None:
                        divs.index = divs.index.tz_localize(None)
                    mensais = divs.resample("ME").sum()
                    mensais = mensais[mensais > 0]
                    if not mensais.empty:
                        resultado[tk_clean] = mensais
                        break
            except Exception:
                pass
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
# Scoring e backtesting — Motor v2 (alinhado com App 1)
# ══════════════════════════════════════════════════════════════════════════════

_DIV_POR_ACAO_MAX = 20.0  # teto sanitizador: acima disso é provavelmente total contábil


def _div_mes_sanitizado(
    s: "pd.Series | None",
    ano: int,
    mes: int,
    ticker: str,
    px_ref: float | None = None,
) -> float:
    """
    Dividendo mensal por ação (R$/ação) com sanitização idêntica ao App1.
    Zera se: série vazia, valor > _DIV_POR_ACAO_MAX, ou > 50% do preço de referência.
    """
    if s is None or s.empty:
        return 0.0
    try:
        val = float(s[(s.index.year == ano) & (s.index.month == mes)].sum())
    except Exception:
        return 0.0
    if not np.isfinite(val) or val <= 0:
        return 0.0
    if val > _DIV_POR_ACAO_MAX:
        return 0.0
    if px_ref is not None and np.isfinite(px_ref) and px_ref > 0:
        if val > 0.5 * px_ref:
            return 0.0
    return val


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

_FUND_FALLBACK_MAP: dict[str, str] = {
    "pl": "P/L",
    "pvp": "P/VP",
    "dy": "DY",
    "roe": "ROE",
    "roic": "ROIC",
    "marg_liq": "Margem_Liquida",
    "marg_ebit": "Margem_Operacional",
    "ev_ebit": "EV_EBIT",
    "liq_corr": "Liquidez_Corrente",
    "div_brut_patrim": "Endividamento_Total",
}

_PCT_SCORE_FIELDS: set[str] = {
    "DY", "ROE", "ROIC", "ROA", "Margem_Liquida",
    "Margem_Operacional", "Payout",
}

_SCORE_RANGES: dict[str, tuple[float | None, float | None]] = {
    "DY": (0.000001, 0.50),
    "ROE": (-3.0, 5.0),
    "ROIC": (-2.0, 3.0),
    "ROA": (-1.0, 1.5),
    "Margem_Liquida": (-2.0, 2.0),
    "Margem_Operacional": (-2.0, 2.0),
    "Payout": (-2.0, 5.0),
    "P/L": (0.01, 200.0),
    "P/VP": (0.01, 50.0),
    "EV_EBIT": (0.01, 200.0),
    "P_FCO": (0.01, 200.0),
    "Endividamento_Total": (0.0, 20.0),
    "Liquidez_Corrente": (0.0, 20.0),
}


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


def _score_value_usable(field: str, value: object) -> bool:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return False
    if not np.isfinite(x):
        return False
    lo, hi = _SCORE_RANGES.get(field, (None, None))
    if lo is not None and x < lo:
        return False
    if hi is not None and x > hi:
        return False
    return True


def _clean_score_inputs(df: pd.DataFrame, cols: list[str] | tuple[str, ...] | None = None) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy()
    target_cols = cols or [c for c in _SCORE_RANGES if c in out.columns]
    for col in target_cols:
        if col not in out.columns:
            continue
        vals = pd.to_numeric(out[col], errors="coerce")
        out[col] = vals.where(vals.map(lambda v, c=col: _score_value_usable(c, v)))
    return out


@st.cache_data(ttl=3600, show_spinner=False)
def _fundamentus_fallback_canonico(tickers: tuple[str, ...]) -> dict[str, dict[str, float]]:
    fund_fmt = _recon.batch_fund_fmt(tuple(sorted(set(tickers))))
    out: dict[str, dict[str, float]] = {}
    for tk, raw in fund_fmt.items():
        vals: dict[str, float] = {}
        for fund_key, db_key in _FUND_FALLBACK_MAP.items():
            v = raw.get(fund_key)
            try:
                x = float(v)
            except (TypeError, ValueError):
                continue
            if not np.isfinite(x):
                continue
            vals[db_key] = x / 100.0 if db_key in _PCT_SCORE_FIELDS else x
        if vals:
            out[str(tk).upper().replace(".SA", "")] = vals
    return out


def _enrich_multiplos_fallback_web(
    df_mult: pd.DataFrame,
    tickers: list[str],
    cols: list[str] | tuple[str, ...],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Substitui ausentes/outliers do snapshot por Fundamentus em lote.
    Retorna o DataFrame enriquecido e uma auditoria resumida por ticker/campo.
    """
    if df_mult.empty or not tickers:
        return df_mult, pd.DataFrame()

    out = df_mult.copy()
    out["Ticker"] = out["Ticker"].astype(str).str.replace(".SA", "", regex=False).str.upper()
    web = _fundamentus_fallback_canonico(tuple(tickers))
    audit_rows: list[dict] = []

    for tk in tickers:
        vals = web.get(str(tk).upper().replace(".SA", ""), {})
        if not vals:
            continue
        idx = out.index[out["Ticker"] == tk]
        if len(idx) == 0:
            continue
        i = idx[0]
        for col in cols:
            if col not in out.columns or col not in vals:
                continue
            old = out.at[i, col]
            new = vals.get(col)
            if not _score_value_usable(col, new):
                continue
            if not _score_value_usable(col, old):
                out.at[i, col] = float(new)
                audit_rows.append({
                    "Ticker": tk,
                    "Indicador": col,
                    "Antes": old,
                    "Depois": float(new),
                    "Fonte": "Fundamentus",
                })

    out = _clean_score_inputs(out, cols)
    return out, pd.DataFrame(audit_rows)


def _cross_source_audit_dataframe(
    df_mult_raw: pd.DataFrame,
    tickers: list[str],
    indicadores: list[str] | tuple[str, ...],
) -> pd.DataFrame:
    """Build Banco x Fundamentus cross-source audit rows for the UI."""
    if df_mult_raw.empty or not tickers:
        return pd.DataFrame()

    from core.cross_source import compare_indicators, consensus_value

    base = df_mult_raw.copy()
    if "Ticker" not in base.columns:
        return pd.DataFrame()
    base["Ticker"] = base["Ticker"].astype(str).str.replace(".SA", "", regex=False).str.upper()
    db_by_ticker = base.drop_duplicates("Ticker").set_index("Ticker").to_dict("index")
    web_by_ticker = _fundamentus_fallback_canonico(tuple(tickers))

    rows: list[dict] = []
    for tk in tickers:
        db_row = db_by_ticker.get(str(tk).upper(), {})
        web_row = web_by_ticker.get(str(tk).upper(), {})
        for ind in indicadores:
            values = {"Banco": db_row.get(ind), "Fundamentus": web_row.get(ind)}
            flag = compare_indicators(tk, ind, values)
            if flag.n_fontes < 2:
                continue
            consensus = consensus_value(values)
            rows.append({
                "Ticker": tk,
                "Indicador": ind,
                "Banco": values["Banco"],
                "Fundamentus": values["Fundamentus"],
                "Consenso": consensus,
                "Spread %": round(flag.spread_rel * 100.0, 1),
                "Severidade": flag.severidade,
            })

    if not rows:
        return pd.DataFrame()
    sev_order = {"critical": 0, "warn": 1, "ok": 2}
    out = pd.DataFrame(rows)
    out["_ord"] = out["Severidade"].map(sev_order).fillna(3)
    return (
        out.sort_values(["_ord", "Spread %", "Ticker"], ascending=[True, False, True])
        .drop(columns=["_ord"])
        .reset_index(drop=True)
    )


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


def _impute_with_group_median(
    df: pd.DataFrame,
    col: str,
    group_col: str | None = None,
) -> pd.Series:
    """
    Fix banca M5 parcial (2026-05-25): imputacao por mediana do grupo
    em substituicao ao default 0.5 fixo.

    Para indicador 'col' do DataFrame df:
      - NaN intra-grupo recebe mediana do grupo (se group_col fornecido
        e o grupo tem >= 3 obs validas)
      - NaN sem grupo identificavel recebe mediana global
      - Se ainda NaN (grupo todo vazio), mantem NaN — caller decide
        se aplica 0.5 ou descarta

    Reduz vies de 'missing-as-neutral': empresa sem dado de Margem
    Liquida em setor onde a media e 25% NAO deveria receber percentil
    0.5 (= mediano BR), mas sim percentil da mediana do seu setor.
    """
    if col not in df.columns:
        return pd.Series(float("nan"), index=df.index, dtype=float)
    s = pd.to_numeric(df[col], errors="coerce").copy()
    if s.notna().all():
        return s

    # Tentativa 1: imputar com mediana do grupo
    if group_col and group_col in df.columns:
        for _, idx in df.groupby(group_col).groups.items():
            grupo = s.loc[idx]
            n_valid = grupo.notna().sum()
            if n_valid >= 3:
                med_grupo = grupo.median()
                s.loc[idx] = grupo.fillna(med_grupo)

    # Tentativa 2: o que sobrar (NaN), tenta mediana global
    if s.isna().any():
        med_global = s.median()
        if pd.notna(med_global):
            s = s.fillna(med_global)

    return s


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
        if counts.empty:
            continue
        crowded_label = counts.idxmax()
        crowd_frac    = counts.max() / len(g)
        if crowd_frac <= uniform_share:
            continue
        pen = min((crowd_frac - uniform_share) * max_pen * n_buckets, max_pen)
        in_crowd = buckets[buckets == crowded_label].index
        penalty.loc[in_crowd] = pen

    return penalty


def _macro_for_year(
    macro_by_year: dict[int, dict[str, float]] | None,
    ano_ref: int | None = None,
) -> dict[str, float]:
    """Seleciona o regime macro disponivel sem olhar anos futuros no backtest."""
    if not macro_by_year:
        return {}
    years = sorted(int(y) for y in macro_by_year)
    if not years:
        return {}
    if ano_ref is None:
        return macro_by_year.get(years[-1], {}) or {}
    eligible = [y for y in years if y <= int(ano_ref)]
    if not eligible:
        return {}
    return macro_by_year.get(eligible[-1], {}) or {}


def _quality_rank(df: pd.DataFrame, col: str, high_good: bool = True) -> pd.Series:
    """Rank de qualidade 0..1 para ajustes contextuais pequenos."""
    if col not in df.columns:
        return pd.Series(0.5, index=df.index, dtype=float)
    s = pd.to_numeric(df[col], errors="coerce")
    if s.notna().sum() < 2:
        return pd.Series(0.5, index=df.index, dtype=float)
    return _percentile_score(_winsorize_series(s.dropna()).reindex(s.index), high_good).fillna(0.5)


def _macro_score_adjustment(
    df: pd.DataFrame,
    macro_context: dict[str, float] | None,
    max_abs: float = 0.05,
) -> pd.Series:
    """
    Ajuste pequeno por regime macro. Ele interage com caracteristicas da empresa
    (divida, liquidez, margens e crescimento), evitando que macro vire fator
    dominante igual para todo o segmento.
    """
    adj = pd.Series(0.0, index=df.index, dtype=float)
    if df.empty or not macro_context:
        return adj

    selic = macro_context.get("selic")
    real = macro_context.get("juros_real_ex_ante")
    ipca = macro_context.get("ipca")
    icc_delta = macro_context.get("icc_delta")

    debt_quality = _quality_rank(df, "Endividamento_Total", high_good=False)
    liq_quality = _quality_rank(df, "Liquidez_Corrente", high_good=True)
    margin_cols = [c for c in ("Margem_Operacional", "Margem_Liquida") if c in df.columns]
    margin_quality = (
        pd.concat([_quality_rank(df, c, True) for c in margin_cols], axis=1).mean(axis=1)
        if margin_cols else pd.Series(0.5, index=df.index, dtype=float)
    )
    growth_cols = [c for c in ("Receita_slope_log", "Lucro_slope_log", "ROE_slope_log", "ROIC_slope_log") if c in df.columns]
    growth_quality = (
        pd.concat([_quality_rank(df, c, True) for c in growth_cols], axis=1).mean(axis=1)
        if growth_cols else pd.Series(0.5, index=df.index, dtype=float)
    )

    high_rates = False
    if real is not None and pd.notna(real):
        high_rates = float(real) >= 5.0
    if selic is not None and pd.notna(selic):
        high_rates = high_rates or float(selic) >= 0.12
    if high_rates:
        adj += 0.030 * (debt_quality - 0.5)
        adj += 0.018 * (liq_quality - 0.5)

    if ipca is not None and pd.notna(ipca) and float(ipca) >= 4.5:
        adj += 0.022 * (margin_quality - 0.5)

    if icc_delta is not None and pd.notna(icc_delta):
        if float(icc_delta) < 0:
            adj += 0.014 * (margin_quality - 0.5)
            adj += 0.014 * (debt_quality - 0.5)
        elif float(icc_delta) > 0:
            adj += 0.016 * (growth_quality - 0.5)

    return adj.clip(lower=-max_abs, upper=max_abs)


def _score_universo(
    df_mult: pd.DataFrame,
    tickers_universo: list[str],
    pesos: dict[str, tuple[float, bool]],
    df_hist_batch: dict[str, pd.DataFrame] | None = None,
    group_col_prefer: str = "SEGMENTO",
    macro_context: dict[str, float] | None = None,
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
    df = _clean_score_inputs(df, list(pesos.keys()))

    group_col = _resolve_group_col_df(df, prefer=group_col_prefer)
    score = pd.Series(0.0, index=df.index)

    # M5 completo (banca 2026-05-23): MICE sobre o DataFrame inteiro antes do
    # loop — usa correlações entre indicadores para imputar NaN com mais precisão
    # que a mediana de grupo. _impute_with_group_median abaixo serve de fallback
    # residual para qualquer NaN que sobrar após MICE.
    try:
        from core.mice_imputer import mice_impute_panel
        df = mice_impute_panel(
            df,
            indicator_cols=list(pesos.keys()),
            group_col=group_col,
        )
    except Exception:
        pass

    for col, (peso, melhor_alto) in pesos.items():
        if col not in df.columns or peso == 0:
            continue
        # Fix banca M5 parcial (2026-05-25): imputa NaN com mediana do
        # grupo (substitui 0.5 fixo implicito no .fillna(0.5) abaixo).
        s = _impute_with_group_median(df, col, group_col)
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

    # ── Ajustes de resiliência histórica + saúde financeira ──────────────
    # A: bônus para empresa temporariamente abaixo da própria média histórica
    #    mas acima da mediana setorial — sinal de oportunidade, não fraqueza.
    # B: penalidade de risco de sobrevivência (balanço frágil).
    # C: bônus de valuation abaixo do próprio histórico sem deterioração de
    #    fundamentais — margem de segurança à la Graham, adaptada ao Brasil.
    try:
        from core.resilience_score import (
            historical_reversion_adj,
            financial_health_penalty,
            valuation_history_adj,
        )
        _scale = float(df["score_raw"].mean()) or 1.0

        if df_hist_batch:
            # A — reversão à média histórica
            _hist_bonus = historical_reversion_adj(df, df_hist_batch, group_col)
            df["score_raw"] += _hist_bonus * _scale
            df["hist_bonus"] = (_hist_bonus * 100).round(1)

            # C — desconto de valuation vs histórico próprio
            _val_bonus = valuation_history_adj(df, df_hist_batch)
            df["score_raw"] += _val_bonus * _scale
            df["val_hist_bonus"] = (_val_bonus * 100).round(1)
        else:
            df["hist_bonus"]     = 0.0
            df["val_hist_bonus"] = 0.0

        # B — saúde financeira (independe de histórico)
        _health_pen = financial_health_penalty(df)
        df["score_raw"]     *= (1.0 - _health_pen)
        df["health_penalty"] = (_health_pen * 100).round(1)

    except Exception:
        df.setdefault("hist_bonus",     0.0)
        df.setdefault("val_hist_bonus", 0.0)
        df.setdefault("health_penalty", 0.0)

    # Penalidade de crowding — desconta empresas no bucket de P/VP mais populoso do grupo
    crow_pen = _apply_crowding_penalty(df, group_col)
    df["score_raw"] *= (1.0 - crow_pen)

    macro_adj = _macro_score_adjustment(df, macro_context)
    df["macro_adj"] = macro_adj
    df["score_raw"] *= (1.0 + macro_adj)

    df["score"]   = df["score_raw"].round(1)
    df["ranking"] = df["score"].rank(ascending=False, method="min").astype(int)
    df["score_version"] = SCORE_VERSION  # fix banca A7
    return df.sort_values("score", ascending=False).reset_index(drop=True)


def _score_universo_bootstrap(
    df_mult: pd.DataFrame,
    tickers_universo: list[str],
    pesos: dict[str, tuple[float, bool]],
    df_hist_batch: dict[str, pd.DataFrame] | None = None,
    group_col_prefer: str = "SEGMENTO",
    macro_context: dict[str, float] | None = None,
    n_bootstrap: int = 200,
    seed: int = 42,
) -> pd.DataFrame:
    """
    Fix banca A3 parcial (2026-05-23): bootstrap dos scores.

    Reamostra com reposicao o universo de empresas n_bootstrap vezes
    (preservando a estrutura de grupos via stratified bootstrap), recalcula
    o score em cada amostra e devolve estatisticas:
      • score_mean    — media bootstrap
      • score_p05     — percentil 5 (lower bound do IC 90%)
      • score_p95     — percentil 95 (upper bound do IC 90%)
      • score_std     — desvio padrao bootstrap (incerteza pontual)

    Util para reportar quao confiavel e o score de cada empresa — uma
    empresa com IC [60, 85] e MENOS confiavel do que outra com IC [72, 75]
    mesmo que ambas tenham score_mean similar.

    Custo computacional: O(n_bootstrap × score_universo). Para n=200 e
    universo de 30 empresas, ~1-3 segundos. Acionar apenas sob demanda
    (nao no hot path do streamlit).
    """
    if df_mult.empty or not tickers_universo or n_bootstrap < 10:
        return pd.DataFrame({"Ticker": tickers_universo, "score_mean": 0.0,
                             "score_p05": 0.0, "score_p95": 0.0, "score_std": 0.0})

    rng = np.random.default_rng(seed)
    df = df_mult[df_mult["Ticker"].isin(tickers_universo)].copy()
    n = len(df)
    if n < 5:
        # Universo muito pequeno: bootstrap não converge
        sc = _score_universo(df_mult, tickers_universo, pesos, df_hist_batch,
                              group_col_prefer, macro_context)
        return pd.DataFrame({
            "Ticker":     sc["Ticker"],
            "score_mean": sc["score"],
            "score_p05":  sc["score"],
            "score_p95":  sc["score"],
            "score_std":  0.0,
        })

    # Acumula scores por ticker ao longo das amostras
    scores_acc: dict[str, list[float]] = {tk: [] for tk in tickers_universo}

    for _ in range(n_bootstrap):
        idx_sample = rng.choice(df.index, size=n, replace=True)
        df_sample = df.loc[idx_sample].drop_duplicates(subset=["Ticker"]).copy()
        ticks_sample = df_sample["Ticker"].tolist()
        if len(ticks_sample) < 3:
            continue
        sc = _score_universo(df_sample, ticks_sample, pesos, df_hist_batch,
                              group_col_prefer, macro_context)
        for _, row in sc.iterrows():
            tk = row["Ticker"]
            if tk in scores_acc:
                scores_acc[tk].append(float(row["score"]))

    rows = []
    for tk in tickers_universo:
        scs = scores_acc.get(tk, [])
        if len(scs) < 5:
            rows.append({"Ticker": tk, "score_mean": 0.0,
                         "score_p05": 0.0, "score_p95": 0.0, "score_std": 0.0})
            continue
        arr = np.array(scs)
        rows.append({
            "Ticker":     tk,
            "score_mean": float(arr.mean().round(1)),
            "score_p05":  float(np.percentile(arr, 5).round(1)),
            "score_p95":  float(np.percentile(arr, 95).round(1)),
            "score_std":  float(arr.std().round(2)),
        })

    return pd.DataFrame(rows).sort_values("score_mean", ascending=False).reset_index(drop=True)


# ══════════════════════════════════════════════════════════════════════════════
# Versionamento do Score (fix banca A7 — 2026-05-23)
# Permite auditoria histórica e A/B test de mudanças no algoritmo.
# Incrementar nas seguintes situações:
#   - Mudança em pesos da composição (60/20/10/10)
#   - Mudança em _PESOS_SETOR
#   - Mudança nos engines (Quality/Risk/Consistency/Cash Quality)
#   - Mudança na ordem ou nos multiplicadores (CV, crowding, macro)
# Cada versão registra changelog abaixo.
# ══════════════════════════════════════════════════════════════════════════════
SCORE_VERSION = "2.18.0"
SCORE_VERSION_CHANGELOG = {
    "2.0.0": "Score base com percentil intra-grupo + 4 engines secundarios.",
    "2.1.0": (
        "Banca examinadora 2026-05-23: "
        "(C1) fix sinal MDD na calibracao; "
        "(C2) overhead transacao no objetivo via CostConfig; "
        "(C5 parcial) Risk Engine com penalidades graduadas (nao mais binarias); "
        "(A7) versionamento explicito do score."
    ),
    "2.2.0": (
        "Banca examinadora rodada 2 (2026-05-25): "
        "(C2c) custos de transacao plugados em _simular_backtest; "
        "(A4) novo modulo core/stress_tests.py com 5 cenarios historicos BR "
        "(Subprime 2008, Janeiro 2015, Joesley 2017, COVID 2020, CDS 2002); "
        "(A1 parcial) _calibrate_walk_forward com k-folds expanding windows "
        "+ mediana entre folds; "
        "(M5 parcial) _impute_with_group_median substitui 0.5 fixo por "
        "mediana setorial na imputacao de NaN."
    ),
    "2.3.0": (
        "Banca examinadora rodada 5 (2026-05-25): "
        "(C4ui) expander 'Otimizacao Markowitz pos-selecao' na Empresas B3 "
        "com slider alpha hibrido (score x min-variance) + cap configuravel; "
        "(A1c) _calibrate_walk_forward agora suporta purge_months + "
        "embargo_months (purged k-fold a la Lopez de Prado 2018); "
        "(C3 parcial) novo modulo core/survivorship.py com 28 tickers BR "
        "delisted 2010-2025 + helpers para flag de cobertura/bias."
    ),
    "2.4.0": (
        "Banca examinadora rodada 6 (2026-05-25): "
        "(C4cov) _simular_backtest aceita use_markowitz + markowitz_alpha + "
        "markowitz_lookback_m — pesos do rebalance anual combinam score com "
        "min-variance restrita (covariancia historica via Ledoit-Wolf); "
        "(A6 MVP) novo modulo core/cross_source.py com compare_indicators, "
        "batch_validate e consensus_value para detectar divergencia entre "
        "fontes fundamentalistas (severidades ok/warn/critical, threshold "
        "configuravel por indicador); "
        "(M2 MVP) novo modulo core/correlations.py com ewma_correlation_matrix, "
        "ewma_volatility e correlation_regime_score — proxy de DCC-GARCH "
        "via EWMA com halflife configuravel (default 60 dias uteis), "
        "captura time-varying correlations sem overhead de otimizacao ML."
    ),
    "2.5.0": (
        "Banca examinadora rodada 7 (2026-05-25): "
        "(M4) novo modulo core/shapley_xai.py — atribuicao quantitativa "
        "exata via Shapley values dos 5 engines (score_base, q_bonus, "
        "c_bonus, cq_bonus, r_penalty); enumeracao completa de 32 coalicoes "
        "(2^5), satisfaz axiomas de Lundberg & Lee 2017 (efficiency, "
        "symmetry, dummy, additivity); "
        "(C3c parcial) core/survivorship_ingestion.py — carregamento de "
        "JSON/CSV em data_imports/delisted/ para EXTENDER a lista curada "
        "sem editar core/survivorship.py; stubs de scrape B3/CVM com "
        "graceful failure (anti-bot impede scraping ingenuo); "
        "(M3 MVP) novo modulo core/black_litterman.py — formalismo "
        "Bayesiano de Black-Litterman 1991/92 para combinar prior de "
        "equilibrio com views do usuario (absolute ou relative) com "
        "confidence configuravel; covariancia Idzorek 2005; helpers "
        "posterior_returns, posterior_covariance, bl_combined_optimization."
    ),
    "2.6.0": (
        "Banca examinadora rodada 8 (2026-05-26): "
        "(M4ui) expander 'Atribuicao Shapley do score' na Empresas B3 com "
        "waterfall chart plotly mostrando contribuicao exata de cada engine "
        "por ticker selecionavel — substitui explainability qualitativa por "
        "decomposicao quantitativa rigorosa; "
        "(M3ui) expander 'Black-Litterman — incorporar suas views' na "
        "Empresas B3 com form para inserir views absolute/relative + "
        "confidence + tau, tabela comparativa prior vs posterior; integra "
        "core/black_litterman.py via bl_combined_optimization."
    ),
    "2.7.0": (
        "Banca examinadora rodada 9 (2026-05-26): "
        "(C5) Risk Engine usa logit calibravel em core/risk_logit.py, "
        "expondo probabilidade de distress, penalidade suavizada e driver "
        "dominante; (A6c) UI de validacao cross-source na Empresas B3 "
        "compara Banco x Fundamentus e mostra severidade ok/warn/critical."
    ),
    "2.8.0": (
        "Banca examinadora rodada 10 (2026-05-26): "
        "(A5) pesos setoriais podem ser calibrados por Fama-MacBeth MVP "
        "em core/fama_macbeth.py e misturados aos pesos manuais; "
        "(A6c+) validacao cross-source passa a ter historico/cache; "
        "(C3cc) survivorship ingestion baixa cadastro oficial CVM, usa "
        "aliases auditaveis e suporta cache B3 local."
    ),
    "2.9.0": (
        "Banca examinadora rodada 11 (2026-05-27): "
        "(M2c MVP) novo modulo core/copulas.py — Gaussian copula via "
        "ranks empiricos (fit_gaussian_copula) + tail dependence index "
        "empirico (tail_dependence_index) usando Acklam 2003 approx para "
        "Phi^-1 sem scipy; compare_pearson_vs_copula expoe 'crisis index' "
        "= quanto Pearson subestima risco de cauda em pares de ativos; "
        "sample_gaussian_copula para Monte Carlo simulation de cenarios "
        "com tail dependence preservada. Captura fenomeno bem documentado "
        "de 'correlacao que vai a 1 em estresse' (Joesley 2017, COVID 2020) "
        "que Pearson ignora por hipotese de normalidade multivariada. "
        "Referencia: Embrechts, McNeil & Straumann 2002, 'Correlation "
        "and Dependence in Risk Management'."
    ),
    "2.10.0": (
        "Banca examinadora rodada 12 (2026-05-27): "
        "(A5+) rolling 5y window em core/fama_macbeth.py — "
        "estimate_fama_macbeth_rolling com janelas expanding de "
        "window_years (default 5y, step 1y) + agregacao por mediana entre "
        "janelas; FamaMacbethRollingResult expoe weight_dispersion "
        "(IQR/std) por indicador para detectar estabilidade temporal; "
        "as_static_result() para compatibilidade com blend_with_base_weights; "
        "(M3+) novo apply_bl_to_markowitz em core/black_litterman.py — "
        "fecha o ciclo Bayesiano: posterior_returns + covariance anualizada "
        "alimentam mean-variance solver com projecao no simplex restrito "
        "via locking + redistribuicao proporcional (sem cvxpy); novo "
        "_project_capped_simplex robusto a cap nao-respeitado; "
        "anualizacao de Sigma via periods_per_year para Sharpe realista "
        "(antes daria 17.24 com cov diaria, agora 1.07 com cov anual); "
        "(C3cc+) novo modulo core/survivorship_prices.py — reconstrucao "
        "de precos historicos pos-delisting via SurvivorshipPriceConfig "
        "(strategies: linear_decay, step_drop, recovery_opa); "
        "RECOVERY_BY_MOTIVO calibrado em Altman & Hotchkiss 2006 "
        "(falencia=0%, RJ=15-20%, OPA=+20%); augment_prices_with_delisted "
        "adiciona colunas reconstruidas ao df_precos preservando obs reais; "
        "summary_reconstruction para auditoria pre-backtest."
    ),
    "2.11.0": (
        "Banca examinadora rodada 13 (2026-05-27): "
        "(M3++) UI integrada do apply_bl_to_markowitz no expander "
        "Black-Litterman da Empresas B3 — alem da tabela prior vs "
        "posterior, agora mostra pesos otimos (BL -> mean-variance) com "
        "sliders de cap e aversao a risco (delta); KPIs de retorno "
        "esperado, volatilidade anualizada e Sharpe implicito do "
        "portfolio resultante; tabela de pesos ordenada por peso."
    ),
}


# ══════════════════════════════════════════════════════════════════════════════
# CAMADAS AVANÇADAS — Score de Entrada
# Evolução do score_base (v2) sem substituí-lo.
# Fórmula:
#   score_entrada = score_base*0.60 + q_bonus*0.20 + c_bonus*0.10 + cq_bonus*0.10
#                   − risk_penalty
#   clamped [0, 100]
# ══════════════════════════════════════════════════════════════════════════════

def _quality_engine(df: pd.DataFrame) -> pd.Series:
    """
    Quality Engine: avalia indicadores complementares ao score_base.
    Usa P_FCO, ROA e coerência Payout — indicadores ausentes ou pouco ponderados
    nos perfis setoriais existentes.
    Retorna série 0-100 (50 = neutro quando sem dados).
    """
    idx = df.index
    parts: list[pd.Series] = []
    weights: list[float] = []

    # P_FCO — preço / fluxo de caixa operacional; menor = empresa mais barata em caixa
    if "P_FCO" in df.columns:
        pfco = pd.to_numeric(df["P_FCO"], errors="coerce")
        valid = pfco.notna() & (pfco > 0)
        if valid.sum() >= 2:
            pfco_w = _winsorize_series(pfco[valid]).reindex(pfco.index)
            pct = _percentile_score(pfco_w, melhor_alto=False) * 100.0
            parts.append(pct.fillna(50.0))
            weights.append(0.45)

    # ROA — retorno sobre ativos; complementa ROE sem confundir alavancagem
    if "ROA" in df.columns:
        roa = pd.to_numeric(df["ROA"], errors="coerce")
        if roa.notna().sum() >= 2:
            roa_w = _winsorize_series(roa.dropna()).reindex(roa.index)
            pct = _percentile_score(roa_w, melhor_alto=True) * 100.0
            parts.append(pct.fillna(50.0))
            weights.append(0.35)

    # Payout saudável: entre 5% e 100% do lucro distribuído = sinal de maturidade
    if "Payout" in df.columns:
        payout = pd.to_numeric(df["Payout"], errors="coerce")
        ps = pd.Series(50.0, index=idx)
        ps[payout.between(0.05, 1.00)] = 75.0   # distribuição saudável
        ps[payout > 1.00]              = 30.0   # distribuindo mais do que lucra
        ps[payout <= 0.0]              = 40.0   # sem distribuição
        parts.append(ps)
        weights.append(0.20)

    if not parts:
        return pd.Series(50.0, index=idx)

    total_w = sum(weights)
    result = sum(p * w for p, w in zip(parts, weights)) / total_w
    return result.clip(0, 100).round(1)


def _risk_engine(df: pd.DataFrame) -> pd.Series:
    """
    Risk Engine: penalizações por sinais fundamentalistas graves.
    Impede que red flags sejam mascarados por pontos fortes isolados.
    Retorna série 0-20 (pontos subtraídos do score_entrada).

    Fix banca C5 (2026-05-26): delega para core/risk_logit.py, que calcula
    probabilidade de distress via logit calibravel e converte para penalidade
    suavizada. Os coeficientes default podem ser trocados depois por ajuste
    em historico rotulado de distress/bankruptcy.
    """
    try:
        from core.risk_logit import distress_risk_score
        return distress_risk_score(df)["r_penalty"]
    except Exception:
        return pd.Series(0.0, index=df.index)

    idx = df.index
    penalty = pd.Series(0.0, index=idx)

    # ROE negativo: penalidade graduada — quanto mais negativo, pior.
    # Escala: ROE -5%  → 3 pts; -15% → 5 pts; -30%+ → 6 pts (cap).
    if "ROE" in df.columns:
        roe = pd.to_numeric(df["ROE"], errors="coerce")
        mask = roe < 0
        # Mapeia |ROE| ∈ [0, 0.30] para pen ∈ [0, 6.0]
        pen_roe = (roe[mask].abs().clip(0, 0.30) / 0.30 * 6.0).fillna(0.0)
        penalty.loc[mask] += pen_roe

    # Endividamento alto: graduado entre 6× e 15× (antes era escalada em 2 steps)
    if "Endividamento_Total" in df.columns:
        debt = pd.to_numeric(df["Endividamento_Total"], errors="coerce")
        mask = (debt > 6) & debt.notna()
        # Escala: debt 6× → 1 pt; 10× → 5 pts; 15×+ → 9 pts (cap)
        pen_debt = ((debt[mask].clip(6, 15) - 6) / 9.0 * 9.0).fillna(0.0)
        penalty.loc[mask] += pen_debt

    # Margem Líquida negativa: graduada por magnitude
    # Escala: ML -2% → 1 pt; -10% → 3 pts; -20%+ → 4 pts (cap)
    if "Margem_Liquida" in df.columns:
        ml = pd.to_numeric(df["Margem_Liquida"], errors="coerce")
        mask = ml < 0
        pen_ml = (ml[mask].abs().clip(0, 0.20) / 0.20 * 4.0).fillna(0.0)
        penalty.loc[mask] += pen_ml

    # Liquidez Corrente baixa: graduada
    # Escala: liq 0.5 → 0 pt; 0.3 → 2 pts; 0.1 ou menor → 3 pts (cap)
    if "Liquidez_Corrente" in df.columns:
        liq = pd.to_numeric(df["Liquidez_Corrente"], errors="coerce")
        mask = (liq < 0.5) & liq.notna()
        pen_liq = ((0.5 - liq[mask].clip(0.1, 0.5)) / 0.4 * 3.0).fillna(0.0)
        penalty.loc[mask] += pen_liq

    # P/VP especulativo: graduado de 15× a 30×
    if "P/VP" in df.columns:
        pvp = pd.to_numeric(df["P/VP"], errors="coerce")
        mask = (pvp > 15) & pvp.notna()
        pen_pvp = ((pvp[mask].clip(15, 30) - 15) / 15.0 * 2.0).fillna(0.0)
        penalty.loc[mask] += pen_pvp

    return penalty.clip(0, 20).round(1)


def _consistency_engine(df: pd.DataFrame,
                         anos_hist: dict[str, int] | None = None) -> pd.Series:
    """
    Consistency Engine: bônus baseado em crescimento histórico consistente.
    Usa slope_log (já calculados no df) e anos de dados — sem duplicar penalidade
    de CV (já aplicada no score_base).
    Retorna série 0-100 (50 = neutro).
    """
    idx = df.index
    parts: list[pd.Series] = []
    weights: list[float] = []

    slope_map = [
        ("ROE_slope_log",              0.28, True),
        ("ROIC_slope_log",             0.24, True),
        ("Margem_Liquida_slope_log",   0.20, True),
        ("Receita_slope_log",          0.16, True),
        ("Margem_Operacional_slope_log", 0.12, True),
    ]
    for col, w, alta in slope_map:
        if col not in df.columns:
            continue
        sl = pd.to_numeric(df[col], errors="coerce")
        if sl.notna().sum() < 2:
            continue
        pct = _percentile_score(_winsorize_series(sl.dropna()).reindex(sl.index), alta) * 100.0
        parts.append(pct.fillna(50.0))
        weights.append(w)

    # Anos de histórico disponível: quanto mais dados, mais confiança
    if anos_hist and "Ticker" in df.columns:
        anos_s = df["Ticker"].map(lambda t: anos_hist.get(t, 0)).astype(float)
        anos_norm = (anos_s.clip(0, 15) / 15.0) * 100.0
        parts.append(anos_norm)
        weights.append(0.22)

    if not parts:
        return pd.Series(50.0, index=idx)

    # Re-normaliza pesos para somar 1
    total_w = sum(weights)
    result = sum(p * w for p, w in zip(parts, weights)) / total_w
    return result.clip(0, 100).round(1)


def _cash_quality_engine(df: pd.DataFrame) -> pd.Series:
    """
    Cash Quality Engine: diferencia lucro contábil de geração real de caixa.
    Usa P_FCO como proxy primário; coerência ROIC×MargemOp como secundário.
    Retorna série 0-100 (50 = neutro quando sem dados).
    """
    idx = df.index
    parts: list[pd.Series] = []
    weights: list[float] = []

    # P_FCO: empresas baratas em relação ao caixa gerado têm maior qualidade
    if "P_FCO" in df.columns:
        pfco = pd.to_numeric(df["P_FCO"], errors="coerce")
        valid = pfco.notna() & (pfco > 0) & (pfco < 200)
        if valid.sum() >= 2:
            pfco_w = _winsorize_series(pfco[valid]).reindex(pfco.index)
            pct = _percentile_score(pfco_w, melhor_alto=False) * 100.0
            parts.append(pct.fillna(50.0))
            weights.append(0.60)

    # Coerência ROIC × Margem Operacional:
    # quando ambos são positivos e ROIC não é muito > MargemOp, lucro é real
    if "ROIC" in df.columns and "Margem_Operacional" in df.columns:
        roic = pd.to_numeric(df["ROIC"], errors="coerce")
        mop  = pd.to_numeric(df["Margem_Operacional"], errors="coerce")
        cq   = pd.Series(50.0, index=idx)
        cq[(roic > 0) & (mop > 0) & (roic < mop * 5)]          = 72.0  # coerente
        cq[(roic > 0) & (mop > 0) & (roic >= mop * 5)]         = 38.0  # ROIC suspeitamente alto
        cq[(roic < 0) | (mop < 0)]                              = 25.0  # operação não rentável
        parts.append(cq)
        weights.append(0.40)

    if not parts:
        return pd.Series(50.0, index=idx)

    total_w = sum(weights)
    result = sum(p * w for p, w in zip(parts, weights)) / total_w
    return result.clip(0, 100).round(1)


def _explicar_score_entrada(row: pd.Series) -> list[tuple[str, str, str]]:
    """
    Gera lista de (fator, valor_fmt, sinal) para exibição na UI.
    sinal ∈ {"positivo", "negativo", "neutro"}
    """
    fatores: list[tuple[str, str, str]] = []

    sb = float(row.get("score_base", 0))
    fatores.append(("Score Base (v2)", f"{sb:.0f}/100", "neutro"))

    # Quality Engine
    qb = float(row.get("q_bonus", 50))
    if qb >= 65:
        fatores.append(("Quality Engine",       f"▲ {qb:.0f}", "positivo"))
    elif qb <= 35:
        fatores.append(("Quality Engine",       f"▼ {qb:.0f}", "negativo"))

    # Consistency Engine
    cb = float(row.get("c_bonus", 50))
    if cb >= 65:
        fatores.append(("Crescimento Consistente", f"▲ {cb:.0f}", "positivo"))
    elif cb <= 35:
        fatores.append(("Histórico Fraco",          f"▼ {cb:.0f}", "negativo"))

    # Cash Quality
    cqb = float(row.get("cq_bonus", 50))
    if cqb >= 65:
        fatores.append(("Geração de Caixa",       f"▲ {cqb:.0f}", "positivo"))
    elif cqb <= 35:
        fatores.append(("Qualidade de Caixa Baixa", f"▼ {cqb:.0f}", "negativo"))

    # Risk penalties
    rp = float(row.get("r_penalty", 0))
    if rp > 0:
        prob = float(row.get("risk_probability", 0) or 0)
        driver = str(row.get("risk_driver", "none") or "none").replace("_", " ").title()
        fatores.append((f"Risco Logit ({driver})", f"{prob:.1f}% / -{rp:.1f} pts", "negativo"))
        if pd.notna(row.get("ROE")) and float(row.get("ROE", 0)) < 0:
            fatores.append(("ROE Negativo", f"−{rp:.0f} pts", "negativo"))
        if pd.notna(row.get("Endividamento_Total")) and float(row.get("Endividamento_Total", 0)) > 6:
            fatores.append(("Endividamento Alto",     f"−{rp:.0f} pts", "negativo"))
        if pd.notna(row.get("Margem_Liquida")) and float(row.get("Margem_Liquida", 0)) < 0:
            fatores.append(("Margem Líquida Negativa", f"−{rp:.0f} pts", "negativo"))
        if pd.notna(row.get("Liquidez_Corrente")) and float(row.get("Liquidez_Corrente", 0)) < 0.5:
            fatores.append(("Liquidez Baixa",         f"−{rp:.0f} pts", "negativo"))
        if not any(s == "negativo" for _, _, s in fatores[1:]):
            fatores.append(("Risco Identificado",     f"−{rp:.0f} pts", "negativo"))

    return fatores


def _compute_score_entrada(
    df_scored: pd.DataFrame,
    anos_hist: dict[str, int] | None = None,
) -> pd.DataFrame:
    """
    Orquestra as 4 novas camadas sobre o score_base existente.

    score_entrada = score_base * 0.60
                  + q_bonus   * 0.20
                  + c_bonus   * 0.10
                  + cq_bonus  * 0.10
                  − risk_penalty
    [0, 100]

    status_entrada:
      Aprovada   → score_entrada ≥ 60 e risk_penalty < 10
      Observação → 30 ≤ score_entrada < 60 e risk_penalty < 10
      Excluída   → score_entrada < 30 ou risk_penalty ≥ 10
    """
    if df_scored.empty:
        return df_scored.copy()

    df = df_scored.copy()
    df["score_base"] = df["score"]                    # preserva o score original

    df["q_bonus"]    = _quality_engine(df)
    try:
        from core.risk_logit import distress_risk_score
        risk_df = distress_risk_score(df)
        df["r_penalty"] = risk_df["r_penalty"]
        df["risk_probability"] = risk_df["risk_probability"]
        df["risk_driver"] = risk_df["risk_driver"]
    except Exception:
        df["r_penalty"] = _risk_engine(df)
        df["risk_probability"] = 0.0
        df["risk_driver"] = "none"
    df["c_bonus"]    = _consistency_engine(df, anos_hist)
    df["cq_bonus"]   = _cash_quality_engine(df)

    df["score_entrada"] = (
        df["score_base"] * 0.60
        + df["q_bonus"]  * 0.20
        + df["c_bonus"]  * 0.10
        + df["cq_bonus"] * 0.10
        - df["r_penalty"]
    ).clip(0, 100).round(1)

    def _status(row: pd.Series) -> str:
        se, rp = float(row["score_entrada"]), float(row["r_penalty"])
        if rp >= 10 or se < 30:
            return "Excluída"
        if se >= 60:
            return "Aprovada"
        return "Observação"

    df["status_entrada"] = df.apply(_status, axis=1)
    df["explicacao"]     = df.apply(_explicar_score_entrada, axis=1)
    df["score_version"]  = SCORE_VERSION   # fix banca A7
    return df


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
    cost_cfg = None,   # CostConfig | None — None = sem custos (backwards compat)
) -> tuple[float, float, float]:
    """
    Grid search 36 combinações (4γ × 3cap × 3soft) em janela de window_months.
    Objetivo: CAGR − 0.60×vol − 0.40×|MDD| − overhead_anual (Calmar-like).
    Aplica shrinkage 40 % em direção aos defaults (γ=0.90, cap=0.25, soft=0.05).
    Usa scores estáticos (rápido) — sem rebalanceamento anual durante a calibração.

    cost_cfg (CostConfig): se fornecido, descona overhead de transação
    estimado da CAGR (fix banca C2). Default None mantém comportamento
    legado para compatibilidade com chamadas existentes.
    """
    # Lazy import — evita ciclo se transaction_costs.py importar outras
    # partes do core no futuro.
    from core.transaction_costs import CostConfig, overhead_anual_estimado
    if cost_cfg is None:
        cost_cfg = CostConfig.desligado()
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
                # Fix banca C1 (2026-05-23): sinal do MDD trocado.
                # Antes: obj = cagr - 0.60*vol + 0.40*mdd  (premiava drawdown)
                # Agora: penalty proporcional ao MDD (forma Calmar-like).
                # Fix banca C2 (2026-05-23): desconta overhead de transação
                # estimado (corretagem, spread bid-ask, IR sobre vendas).
                # Rotação estimada em 40% a.a. (rebalance + decay liderança).
                overhead_bps = overhead_anual_estimado(cost_cfg, rotation_pct_aa=0.40)
                obj    = cagr - 0.60 * vol - 0.40 * mdd - overhead_bps / 10_000.0
                if obj > best_obj:
                    best_obj  = obj
                    best_pars = (gamma, cap, soft)

    g = best_pars[0] * (1 - _CAL_SHRINK) + _GAMMA_DEF * _CAL_SHRINK
    c = best_pars[1] * (1 - _CAL_SHRINK) + _CAP_DEF   * _CAL_SHRINK
    s = best_pars[2] * (1 - _CAL_SHRINK) + _SOFT_DEF  * _CAL_SHRINK
    return round(g, 3), round(c, 3), round(s, 3)


def _calibrate_walk_forward(
    df_precos: pd.DataFrame,
    df_scored: pd.DataFrame,
    tickers_all: list[str],
    taxa_selic_aa: float,
    aporte: float = 1000.0,
    n_folds: int = 4,
    min_window: int = 24,
    cost_cfg=None,
    purge_months: int = 0,   # banca A1c (2026-05-25): gap purged k-fold
    embargo_months: int = 0,
) -> tuple[float, float, float]:
    """
    Fix banca A1+A1c (2026-05-25): walk-forward cross-validation com
    PURGED k-fold opcional (Lopez de Prado, 2018, Advances in Financial
    Machine Learning, cap. 7).

    Em vez de calibrar em uma janela fixa de 36 meses (vulneravel a
    overfit ao periodo escolhido), divide a serie em n_folds janelas
    crescentes (expanding windows) e calibra em cada. Retorna a mediana
    dos best_pars entre folds — robusto a janelas anomalas.

    Exemplo com n_folds=4 e serie de 96 meses (sem purge):
      Fold 1: calibra em meses 1-24
      Fold 2: calibra em meses 1-48
      Fold 3: calibra em meses 1-72
      Fold 4: calibra em meses 1-96 (janela completa)

    Com purge_months=3 e embargo_months=2, cada fold REMOVE os ultimos
    `purge_months` do periodo de treino (evita contaminacao do teste por
    autocorrelacao serial) E pula `embargo_months` apos o teste antes do
    proximo treino. Sem purge/embargo (defaults = 0), comportamento e
    equivalente ao walk-forward simples da rodada 2 (compat retroativa).

    Justificativa (Lopez de Prado 2018, cap. 7):
      Retornos financeiros tem autocorrelacao serial (heteroskedasticidade,
      volatility clustering) que viola IID. K-fold ingenuo permite que
      observacoes proximas vazem de treino para teste, inflando metricas
      out-of-sample. Purge + embargo cria gap temporal estrito.

    Em cada fold roda _calibrate_gamma_cap_soft no subset apropriado e
    coleta best_pars. Mediana de cada hiperparametro entre folds e
    devolvida (mais robusto a outliers que media).
    """
    if df_precos.empty or len(df_precos) < min_window * 2 or df_scored.empty:
        return _GAMMA_DEF, _CAP_DEF, _SOFT_DEF

    n_total = len(df_precos)
    fold_size = max((n_total - min_window) // max(n_folds - 1, 1), 6)

    gammas, caps, softs = [], [], []
    for k in range(n_folds):
        end = min_window + fold_size * k
        if end > n_total:
            end = n_total
        # Banca A1c: purge — remove ultimos purge_months antes do "teste"
        # (representado aqui pelos folds seguintes). Embargo adia o
        # proximo fold em embargo_months.
        end_purged = end - purge_months if purge_months > 0 else end
        if end_purged < min_window:
            continue
        df_fold = df_precos.iloc[:end_purged].copy()
        if len(df_fold) < min_window:
            continue
        g, c, s = _calibrate_gamma_cap_soft(
            df_fold, df_scored, tickers_all, taxa_selic_aa, aporte,
            window_months=len(df_fold), cost_cfg=cost_cfg,
        )
        gammas.append(g); caps.append(c); softs.append(s)
        # Embargo: avanca min_window adicional no proximo fold para
        # garantir gap temporal antes do periodo de calibracao seguinte.
        if embargo_months > 0:
            min_window += embargo_months

    if not gammas:
        return _GAMMA_DEF, _CAP_DEF, _SOFT_DEF

    # Mediana e mais robusta a outliers que media
    return (
        round(float(np.median(gammas)), 3),
        round(float(np.median(caps)),   3),
        round(float(np.median(softs)),  3),
    )


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


def _fco_lucro_rows(batch_fco: dict[str, pd.DataFrame]) -> tuple[list[dict], dict[str, int]]:
    """Calcula FCO/Lucro usando FCO direto ou fallback FCO = FCF - FCI."""
    rows: list[dict] = []
    audit = {
        "tickers": len(batch_fco),
        "sem_colunas": 0,
        "sem_linha_valida": 0,
        "fallback_fcf_fci": 0,
    }
    fco_candidates = ("FCO", "Fluxo_Caixa_Operacional", "Caixa_Operacional")
    fcf_candidates = ("FCF", "Fluxo_Caixa_Livre")
    fci_candidates = ("FCI", "Fluxo_Caixa_Investimento")
    lucro_candidates = ("Lucro_Liquido", "Lucro Líquido", "LucroLiquido")

    for tk_f, df_f in batch_fco.items():
        if df_f is None or df_f.empty:
            audit["sem_linha_valida"] += 1
            continue

        fco_col = next((c for c in fco_candidates if c in df_f.columns), None)
        fcf_col = next((c for c in fcf_candidates if c in df_f.columns), None)
        fci_col = next((c for c in fci_candidates if c in df_f.columns), None)
        ll_col = next((c for c in lucro_candidates if c in df_f.columns), None)
        if not ll_col or (not fco_col and not (fcf_col and fci_col)):
            audit["sem_colunas"] += 1
            continue

        df_calc = df_f.copy()
        if "Data" in df_calc.columns:
            df_calc = df_calc.sort_values("Data")
        df_calc["_lucro"] = pd.to_numeric(df_calc[ll_col], errors="coerce")
        if fco_col:
            df_calc["_fco"] = pd.to_numeric(df_calc[fco_col], errors="coerce")
            fonte = fco_col
        else:
            fcf = pd.to_numeric(df_calc[fcf_col], errors="coerce")
            fci = pd.to_numeric(df_calc[fci_col], errors="coerce")
            df_calc["_fco"] = fcf - fci
            fonte = f"{fcf_col} - {fci_col}"

        valid = df_calc[df_calc["_fco"].notna() & df_calc["_lucro"].notna() & (df_calc["_lucro"].abs() > 1e-6)]
        if valid.empty:
            audit["sem_linha_valida"] += 1
            continue

        last = valid.iloc[-1]
        if not fco_col:
            audit["fallback_fcf_fci"] += 1
        data_val = last.get("Data")
        ano = ""
        try:
            ano = int(pd.to_datetime(data_val).year)
        except Exception:
            pass
        fco = float(last["_fco"])
        lucro = float(last["_lucro"])
        rows.append({
            "Empresa": tk_f,
            "Ano": ano,
            "FCO": fco,
            "Lucro": lucro,
            "FCO/Lucro": fco / lucro,
            "Fonte": fonte,
        })

    return rows, audit


def _score_historico_ano(
    df_hist_batch: dict[str, pd.DataFrame],
    tickers: list[str],
    ano_ref: int,
    pesos: dict[str, tuple[float, bool]],
    tk_grupos: dict[str, dict] | None = None,
    lag: int = 1,
    macro_by_year: dict[int, dict[str, float]] | None = None,
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
    df_sc   = _score_universo(
        df_snap, tickers, pesos,
        macro_context=_macro_for_year(macro_by_year, ano_cutoff),
    )
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
    macro_by_year: dict[int, dict[str, float]] | None = None,
    dividendos: dict[str, "pd.Series"] | None = None,
    cost_cfg=None,             # CostConfig | None — fix banca C2c
    use_markowitz: bool = False,   # fix banca C4cov
    markowitz_alpha: float = 0.50, # peso score vs min-variance (0..1)
    markowitz_lookback_m: int = 36,
) -> tuple[pd.DataFrame, list[str], int]:
    """
    Simula aportes mensais com rebalanceamento anual e publication lag = 1.
    Score do ano N é calculado com dados até N−1 (sem look-ahead bias).
    selic_por_ano: taxa Selic real por ano (da tabela macro); fallback = taxa_selic_aa.
    dividendos: dict {ticker: pd.Series mensal R$/ação} para reinvestimento (App1-compatible).
    cost_cfg: CostConfig com fee/spread/IR (fix banca C2c, 2026-05-25).
      None ⇒ comportamento legado sem custos (compatibilidade).
      Rebalance anual NÃO vende cotas existentes; só redireciona aportes
      futuros — por isso só custos de COMPRA são aplicados. IR de venda
      ainda não é modelado pois não há vendas no fluxo atual.
    Retorna (df_resultado, tickers_top_último_ano, n_efetivo_último_ano).
    """
    # Lazy import — evita ciclo
    from core.transaction_costs import CostConfig, custo_compra
    if cost_cfg is None:
        cost_cfg = CostConfig.desligado()
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
                df_hist_batch, tks_all_valid, ano, pesos, tk_grupos, lag=1,
                macro_by_year=macro_by_year,
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
                # Fix banca C4cov (2026-05-25): combina pesos do score com
                # min-variance restrita usando covariancia historica dos
                # ultimos `markowitz_lookback_m` meses. Reduz concentracao
                # em ativos correlacionados (ex: BBAS+ITUB+SANB ambos bancos).
                if use_markowitz and len(tickers_yr) >= 2:
                    try:
                        from core.markowitz import (
                            min_variance_capped, pesos_hibridos_score_markowitz,
                        )
                        # Janela de retornos ANTES da data atual (lag=0 ok pq
                        # rebalanceia no inicio do ano e usa dados ate dt-1)
                        df_lookback = df.loc[df.index < dt].tail(markowitz_lookback_m)
                        cols_disp = [tk for tk in tickers_yr if tk in df_lookback.columns]
                        if len(cols_disp) >= 2 and len(df_lookback) >= 6:
                            rets = df_lookback[cols_disp].pct_change().dropna()
                            if len(rets) >= 4:
                                mk = min_variance_capped(
                                    cols_disp, rets.to_numpy(), cap=cap,
                                )
                                pesos_est = pesos_hibridos_score_markowitz(
                                    pesos_est, mk, alpha=markowitz_alpha,
                                )
                    except Exception:
                        pass  # fallback silencioso para gamma-tilt puro
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

        # ── Reinvestimento de dividendos — idêntico ao App1 ───────────────
        if dividendos:
            mes = dt.month
            for tk in tks_all_valid:
                px_tk = float(row.get(tk, 0) or 0)
                if px_tk <= 0:
                    continue
                div = _div_mes_sanitizado(dividendos.get(tk), ano, mes, tk, px_tk)
                if div > 0:
                    if cotas_est[tk] > 0:
                        cotas_est[tk] += div * cotas_est[tk] / px_tk
                    if cotas_bench[tk] > 0:
                        cotas_bench[tk] += div * cotas_bench[tk] / px_tk

        # ── Aportes mensais ────────────────────────────────────────────────
        # Fix banca C2c (2026-05-25): desconta custo_compra do valor bruto
        # antes de converter em cotas. Sem cost_cfg ativo, custo_compra = 0
        # e comportamento e identico ao legado.
        est_disp = [tk for tk in tks_est_valid
                    if pd.notna(row.get(tk)) and float(row.get(tk, 0) or 0) > 0]
        if est_disp:
            total_w = sum(pesos_est.get(tk, 0.0) for tk in est_disp) or 1.0
            for tk in est_disp:
                valor_bruto = aporte * pesos_est.get(tk, 0.0) / total_w
                fee = custo_compra(tk, valor_bruto, cost_cfg)
                valor_liq = max(valor_bruto - fee, 0.0)
                cotas_est[tk] += valor_liq / float(row[tk])

        all_disp = [tk for tk in tks_all_valid
                    if pd.notna(row.get(tk)) and float(row.get(tk, 0) or 0) > 0]
        if all_disp:
            por_tk = aporte / len(all_disp)
            for tk in all_disp:
                fee = custo_compra(tk, por_tk, cost_cfg)
                valor_liq = max(por_tk - fee, 0.0)
                cotas_bench[tk] += valor_liq / float(row[tk])

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

    st.info(
        "⚠️ **Ferramenta de análise educacional — não é recomendação de "
        "investimento.** Os scores, backtests e preços-justos exibidos são "
        "estimativas quantitativas baseadas em dados históricos e premissas "
        "do modelo. Rentabilidade passada não garante resultado futuro. "
        "Esta ferramenta não constitui consultoria de valores mobiliários "
        "(CVM). Decisões de alocação são de responsabilidade do investidor."
    )

    df_precos = pd.DataFrame()

    # Ano-base do score: SEMPRE o último ano COMPLETO (anterior ao corrente).
    # A decisão do ano atual usa os fundamentos consolidados do ano anterior,
    # evitando dado parcial do ano em curso (metodologia do usuário).
    import datetime as _dt_ref
    _ano_corrente = _dt_ref.date.today().year
    _ano_base     = _ano_corrente - 1

    # Dados base
    with st.spinner("Carregando múltiplos e histórico…"):
        df_mult_todos = _db.load_multiplos_todos(ano_ref_max=_ano_base)
        anos_hist     = _db.load_historico_anos()

    # Ano de referência efetivo dos dados carregados (pode ser < _ano_base se
    # a ingestão do ano anterior ainda não ocorreu para parte do universo).
    _ano_dados = None
    if not df_mult_todos.empty and "data" in df_mult_todos.columns:
        _datas = pd.to_datetime(df_mult_todos["data"], errors="coerce").dropna()
        if not _datas.empty:
            _ano_dados = int(_datas.dt.year.max())
    _ano_label = _ano_dados if _ano_dados is not None else _ano_base
    st.caption(
        f"📅 **Ano-base do score: {_ano_label}** — a seleção do ano atual "
        f"({_ano_corrente}) usa os fundamentos consolidados do ano anterior. "
        "Dados parciais do ano corrente são ignorados por metodologia."
    )

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

    liq_opts = {
        "Sem filtro":        0.0,
        "≥ R$ 500 mil/dia":  500_000.0,
        "≥ R$ 1 milhão/dia": 1_000_000.0,
        "≥ R$ 5 milhões/dia": 5_000_000.0,
        "≥ R$ 10 milhões/dia": 10_000_000.0,
    }
    sel_liq = fc1.selectbox(
        "Liquidez mínima (negociação)", list(liq_opts.keys()),
        index=2, key="b3_av_liq_min",
        help="Exclui ações com baixo volume diário negociado, onde o spread "
             "encarece a entrada/saída. Baseado na mediana de Close×Volume "
             "dos últimos ~3 meses (yfinance).",
    )
    liq_min_rs = liq_opts[sel_liq]

    with st.expander("⚖️ Pesos do Scoring"):
        usar_pesos_setor = st.checkbox(
            "Usar pesos calibrados por setor (recomendado)",
            value=True, key="b3_av_use_setor_w",
        )
        usar_fama_macbeth = st.checkbox(
            "Calibrar pesos com Fama-MacBeth MVP",
            value=False,
            key="b3_av_use_fm",
            disabled=not usar_pesos_setor,
        )
        fm_alpha = st.slider(
            "Mistura Fama-MacBeth",
            0.0, 0.70, 0.35, 0.05,
            key="b3_av_fm_alpha",
            disabled=not (usar_pesos_setor and usar_fama_macbeth),
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

    # Exclui empresas descontinuadas na B3 — não são investíveis hoje e não
    # devem entrar na análise/criação de portfólio (blocklist defensiva sobre
    # a lista de `setores`, que deveria conter apenas ativas).
    try:
        from core.survivorship import tickers_delisted_set
        _delisted = tickers_delisted_set()
        _antes_del = len(tks_uni)
        tks_uni = [tk for tk in tks_uni if tk not in _delisted]
        _n_removidas = _antes_del - len(tks_uni)
        if _n_removidas:
            st.caption(
                f"🚫 {_n_removidas} empresa(s) descontinuada(s) da B3 "
                "excluída(s) da análise (não investíveis atualmente)."
            )
    except Exception:
        pass

    # Filtro de liquidez de negociação — exclui ações ilíquidas onde o spread
    # encarece a operação (proteção ao investidor iniciante).
    if liq_min_rs > 0 and tks_uni:
        with st.spinner("Verificando liquidez de negociação…"):
            liq_map = _batch_yf_liquidez(tuple(sorted(tks_uni)))
        if liq_map:
            _antes_liq = len(tks_uni)
            # Mantém tickers sem dado de liquidez (não penaliza por falha de API)
            tks_uni = [
                tk for tk in tks_uni
                if liq_map.get(tk) is None or liq_map.get(tk, 0) >= liq_min_rs
            ]
            _n_iliq = _antes_liq - len(tks_uni)
            if _n_iliq:
                st.caption(
                    f"💧 {_n_iliq} ação(ões) abaixo de {sel_liq.replace('≥ ', '')} "
                    "excluída(s) por baixa liquidez."
                )

    if not tks_uni:
        st.info("Nenhuma empresa encontrada com os filtros selecionados.")
        return

    # Teto de universo para performance. Quando excede, mantém as de MELHORES
    # fundamentos (pré-ranking barato), não as primeiras por ordem de aparição.
    _MAX_UNI = 40
    if len(tks_uni) > _MAX_UNI:
        tks_uni = _prerank_tickers(df_mult_todos, tks_uni)[:_MAX_UNI]
        st.info(
            f"Universo limitado a {_MAX_UNI} empresas (de {len(df_filt)}+ "
            "filtradas) — mantidas as de melhores fundamentos por pré-ranking "
            "(ROE/ROIC/DY/Margem/Endividamento). Refine os filtros para "
            "análise mais focada."
        )

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

    cols_av = sorted(set([c for c, _ in _COLS_COMP] + list(_SCORE_RANGES.keys())))
    with st.spinner("Reconciliando vazios/outliers com Fundamentus..."):
        df_mult_enrich, audit_fallback = _enrich_multiplos_fallback_web(
            df_mult_enrich, tks_uni, cols_av
        )

    # Carregar histórico para penalidade de instabilidade + slope_log
    with st.spinner("Calculando scoring v2…"):
        hist_batch = _db.load_multiplos_historico_batch(tuple(sorted(tks_uni)))
        selic_macro = _db.load_selic_macro()
        macro_history = _db.load_macro_history()

    # Enriquecer com slope_log antes do scoring
    df_mult_enrich = _enrich_com_slopes(df_mult_enrich, hist_batch)

    # Pesos por setor ou manuais
    setor_counts = (
        df_filt["SETOR"].dropna().astype(str).str.strip().value_counts()
        if "SETOR" in df_filt.columns and not df_filt.empty else pd.Series(dtype=int)
    )
    setor_prevalente = str(setor_counts.idxmax()) if not setor_counts.empty else ""
    pesos_v2 = _get_pesos_setor(setor_prevalente, pesos_usuario_raw)
    fm_result = None
    if usar_pesos_setor and usar_fama_macbeth and hist_batch:
        try:
            from core.fama_macbeth import (
                blend_with_base_weights, estimate_fama_macbeth_weights,
            )

            fm_result = estimate_fama_macbeth_weights(
                hist_batch,
                candidate_indicators=pesos_v2.keys(),
            )
            if getattr(fm_result, "ok", False):
                pesos_v2 = blend_with_base_weights(
                    pesos_v2,
                    fm_result,
                    alpha=fm_alpha,
                )
        except Exception as exc:
            fm_result = {"erro": str(exc)}

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
        macro_context=_macro_for_year(macro_history),
    )
    tk_info       = {row["ticker"]: row for _, row in df_filt.iterrows()}
    tks_com_score = set(df_scored["Ticker"].tolist()) if not df_scored.empty else set()
    tks_sem_mult  = [tk for tk in tks_uni if tk not in tks_com_score]

    if not audit_fallback.empty:
        with st.expander("Auditoria dos dados usados no scoring"):
            st.caption(
                "Valores vazios, zerados indevidamente ou fora de faixas razoáveis "
                "foram substituídos antes do cálculo do score."
            )
            st.dataframe(audit_fallback, use_container_width=True,
                         height=min(260, 45 + 32 * len(audit_fallback)))

    if usar_pesos_setor and usar_fama_macbeth:
        with st.expander("Calibracao Fama-MacBeth MVP dos pesos"):
            if isinstance(fm_result, dict) and fm_result.get("erro"):
                st.error(f"Falha na calibracao: {fm_result['erro']}")
            elif fm_result is not None and getattr(fm_result, "ok", False):
                fm_cols = st.columns(4)
                fm_cols[0].metric("Anos usados", int(fm_result.n_years))
                fm_cols[1].metric("Tickers", int(fm_result.n_tickers))
                fm_cols[2].metric("Obs.", int(fm_result.n_obs))
                fm_cols[3].metric("Mistura", f"{fm_alpha:.0%}")
                rows_fm = []
                for ind, (peso_final, melhor_alto) in pesos_v2.items():
                    rows_fm.append({
                        "Indicador": ind,
                        "Peso Final %": peso_final * 100.0,
                        "Beta FM": fm_result.betas.get(ind),
                        "Direcao": "maior melhor" if melhor_alto else "menor melhor",
                    })
                st.dataframe(
                    pd.DataFrame(rows_fm).sort_values("Peso Final %", ascending=False),
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        "Peso Final %": st.column_config.NumberColumn(format="%.1f%%"),
                        "Beta FM": st.column_config.NumberColumn(format="%.4f"),
                    },
                )
            else:
                motivo = ", ".join(getattr(fm_result, "warnings", ())) if fm_result is not None else "sem historico"
                st.info(f"Calibracao nao aplicada: {motivo}.")

    with st.expander("Validacao cross-source (Banco x Fundamentus)"):
        st.caption(
            "Compara a fonte primaria do banco com a fonte web em indicadores "
            "canonicos. Divergencias criticas devem ser revisadas antes de "
            "confiar no ranking."
        )
        run_cross = st.checkbox(
            "Executar auditoria cross-source",
            value=False,
            key="b3_run_cross_source",
        )
        if run_cross:
            audit_cross = _cross_source_audit_dataframe(
                df_mult_todos,
                tks_uni,
                ["ROE", "ROIC", "Margem_Liquida", "Margem_Operacional",
                 "DY", "P/L", "P/VP", "EV_EBIT", "Endividamento_Total",
                 "Liquidez_Corrente"],
            )
            if audit_cross.empty:
                st.info("Nao ha pares Banco x Fundamentus suficientes para comparar.")
            else:
                n_crit = int((audit_cross["Severidade"] == "critical").sum())
                n_warn = int((audit_cross["Severidade"] == "warn").sum())
                n_ok = int((audit_cross["Severidade"] == "ok").sum())
                mc1, mc2, mc3 = st.columns(3)
                mc1.metric("Criticas", n_crit)
                mc2.metric("Alertas", n_warn)
                mc3.metric("OK", n_ok)
                st.dataframe(
                    audit_cross,
                    use_container_width=True,
                    height=min(360, 45 + 28 * len(audit_cross)),
                    column_config={
                        "Banco": st.column_config.NumberColumn(format="%.4g"),
                        "Fundamentus": st.column_config.NumberColumn(format="%.4g"),
                        "Consenso": st.column_config.NumberColumn(format="%.4g"),
                        "Spread %": st.column_config.NumberColumn(format="%.1f%%"),
                    },
                )
                if st.button("Salvar historico desta auditoria", key="b3_save_cross_history"):
                    from core.cross_source import append_validation_history

                    saved = append_validation_history(
                        audit_cross.to_dict("records"),
                        run_meta={
                            "setor": sel_set,
                            "subsetor": sel_sub,
                            "segmento": sel_seg,
                            "tickers": list(tks_uni),
                            "score_version": SCORE_VERSION,
                        },
                    )
                    st.success(f"{saved} linhas salvas no historico cross-source.")

        show_cross_history = st.checkbox(
            "Mostrar historico salvo",
            value=False,
            key="b3_show_cross_history",
        )
        if show_cross_history:
            from core.cross_source import load_validation_history, summarize_validation_history

            hist_rows = load_validation_history(limit=300)
            if not hist_rows:
                st.info("Historico ainda vazio.")
            else:
                hist_summary = summarize_validation_history(hist_rows)
                hc1, hc2, hc3 = st.columns(3)
                hc1.metric("Linhas", int(hist_summary["total"]))
                hc2.metric("Criticas", int(hist_summary["critical"]))
                hc3.metric("Alertas", int(hist_summary["warn"]))
                hist_df = pd.DataFrame(hist_rows)
                cols_hist = [
                    c for c in ["run_ts", "Ticker", "Indicador", "Severidade",
                                "Spread %", "Banco", "Fundamentus", "Consenso"]
                    if c in hist_df.columns
                ]
                st.dataframe(
                    hist_df[cols_hist].head(120) if cols_hist else hist_df.head(120),
                    use_container_width=True,
                    hide_index=True,
                )

    # ── Banca A3ui (2026-05-25): Bootstrap CI dos scores ──────────────────────
    # Lazy: so calcula quando usuario marca o checkbox dentro do expander.
    with st.expander("📊 Análise de incerteza (bootstrap) — opcional, lento"):
        st.caption(
            "Reamostra o universo de empresas N vezes e calcula percentis 5 e 95 "
            "do score. IC 90% largo = score sensível a inclusão/exclusão de pares; "
            "IC estreito = score robusto."
        )
        col_btn, col_n = st.columns([1, 1])
        with col_btn:
            run_bs = st.checkbox(
                "Calcular IC dos scores", value=False, key="b3_run_bootstrap"
            )
        with col_n:
            n_bs = st.select_slider(
                "Reamostragens",
                options=[50, 100, 200, 500],
                value=200, key="b3_n_bootstrap",
                disabled=not run_bs,
            )

        if run_bs and not df_scored.empty:
            with st.spinner(f"Bootstrap em {n_bs} reamostragens..."):
                df_bs = _score_universo_bootstrap(
                    df_mult_enrich, tks_uni, pesos_v2,
                    df_hist_batch=hist_batch,
                    group_col_prefer=group_prefer,
                    macro_context=_macro_for_year(macro_history),
                    n_bootstrap=n_bs,
                )
            if not df_bs.empty:
                df_bs["IC 90%"] = df_bs.apply(
                    lambda r: f"[{r['score_p05']:.0f} ; {r['score_p95']:.0f}]", axis=1
                )
                df_bs["Largura IC"] = (df_bs["score_p95"] - df_bs["score_p05"]).round(1)
                df_bs_show = df_bs[["Ticker", "score_mean", "IC 90%",
                                      "Largura IC", "score_std"]].rename(columns={
                    "score_mean": "Score Médio",
                    "score_std":  "Desvio",
                })
                st.dataframe(
                    df_bs_show,
                    use_container_width=True, hide_index=True,
                    column_config={
                        "Score Médio": st.column_config.NumberColumn(format="%.1f"),
                        "Largura IC":  st.column_config.NumberColumn(
                            format="%.1f",
                            help="< 5 = score robusto; > 15 = alta incerteza"),
                        "Desvio":      st.column_config.NumberColumn(format="%.2f"),
                    },
                )
                st.caption(
                    f"✓ Calculado em {n_bs} reamostragens estratificadas. "
                    f"Tickers com largura IC > 15 pontos têm score sensível ao universo "
                    f"de comparação — interpretar com cautela."
                )

    # ── Banca C4ui (2026-05-25): Markowitz pós-seleção ────────────────────────
    price_tickers = (
        tuple(sorted(set(df_scored["Ticker"].tolist()[:12])))
        if not df_scored.empty and "Ticker" in df_scored.columns else tuple()
    )
    df_precos = (
        _batch_yf_precos_mensais(price_tickers, period="5y")
        if price_tickers else pd.DataFrame()
    )

    with st.expander("🎯 Otimização Markowitz pós-seleção — opcional"):
        st.caption(
            "Após selecionar top-N por score, aplica min-variance restrita "
            "considerando covariância dos retornos para reduzir concentração "
            "em ativos correlacionados. Slider α controla mistura: "
            "α=1.0 = score puro (legado); α=0.0 = min-variance puro; "
            "α=0.5 = híbrido recomendado."
        )
        col_run, col_top, col_cap, col_alpha, col_cov = st.columns([1, 1, 1, 2, 1])
        with col_run:
            run_mk = st.checkbox("Calcular", value=False, key="b3_run_markowitz")
        with col_top:
            top_n_mk = st.select_slider(
                "Top N", options=[3, 5, 8, 10], value=5,
                key="b3_mk_topn", disabled=not run_mk,
            )
        with col_cap:
            cap_mk = st.select_slider(
                "Cap %", options=[0.20, 0.25, 0.30, 0.40, 0.50],
                value=0.30, format_func=lambda x: f"{x*100:.0f}%",
                key="b3_mk_cap", disabled=not run_mk,
            )
        with col_alpha:
            alpha_mk = st.slider(
                "α (score ↔ min-var)", 0.0, 1.0, 0.5, 0.05,
                key="b3_mk_alpha", disabled=not run_mk,
                help="0 = min-variance puro; 1 = score puro (legado); 0.5 = híbrido",
            )
        with col_cov:
            cov_method = st.selectbox(
                "Covariância Σ",
                ["Ledoit-Wolf", "Fama-French", "DCC-GARCH"],
                index=0,
                key="b3_mk_cov_method",
                disabled=not run_mk,
                help=(
                    "Ledoit-Wolf: shrinkage amostral (padrão). "
                    "Fama-French: Σ estruturada fatorial M1 banca (Σ=BΣ_FB'+D). "
                    "DCC-GARCH: covariância condicional dinâmica M2 banca (Engle 2002) "
                    "— captura correlações que variam no tempo e sobem em crises."
                ),
            )

        cdiv1, cdiv2 = st.columns([1, 3])
        with cdiv1:
            diversificar = st.checkbox(
                "Diversificar por setor", value=True, key="b3_mk_diversif",
                disabled=not run_mk,
                help="Limita quantas ações do mesmo setor entram na carteira, "
                     "evitando concentração (ex.: top-N só de bancos).",
            )
        with cdiv2:
            max_por_setor = st.select_slider(
                "Máx. por setor", options=[1, 2, 3, 4], value=2,
                key="b3_mk_maxsetor", disabled=not (run_mk and diversificar),
            )

        if run_mk and not df_scored.empty:
            # Ranking completo (melhor→pior) com preço disponível
            _ranked = [
                tk for tk in df_scored["Ticker"].tolist()
                if tk in df_precos.columns
            ]
            if diversificar:
                _setor_map = {
                    tk: (tk_grupos.get(tk, {}) or {}).get("SETOR") or "—"
                    for tk in _ranked
                }
                top_tks = _aplicar_diversificacao_setorial(
                    _ranked, _setor_map, top_n=top_n_mk, max_por_setor=max_por_setor,
                )
            else:
                top_tks = _ranked[:top_n_mk]
            if len(top_tks) < 2:
                st.warning("Top-N não tem 2+ tickers com preços históricos.")
            else:
                returns = (
                    df_precos[top_tks]
                    .iloc[-36:]
                    .pct_change()
                    .dropna()
                    .to_numpy()
                )
                if returns.shape[0] < 6:
                    st.warning("Histórico de retornos insuficiente (<6 obs).")
                else:
                    _spinner_labels = {
                        "Fama-French": "Calculando min-variance (Fama-French Σ)...",
                        "DCC-GARCH":   "Calculando min-variance (DCC-GARCH Σ)...",
                    }
                    with st.spinner(_spinner_labels.get(cov_method, "Calculando min-variance...")):
                        from core.markowitz import (
                            min_variance_capped,
                            min_variance_with_cov,
                            pesos_hibridos_score_markowitz,
                        )
                        mk = None

                        if cov_method == "Fama-French":
                            try:
                                from core.ff_risk_model import (
                                    build_ff_risk_model,
                                    build_br_factors_etf,
                                    ticker_monthly_returns_from_price_df,
                                )
                                tk_rets = ticker_monthly_returns_from_price_df(
                                    df_precos, top_tks
                                )
                                factors = build_br_factors_etf(n_months=36)
                                ff_model = build_ff_risk_model(
                                    top_tks, tk_rets, factors
                                )
                                cov_ff, cov_tks = ff_model.covariance_matrix()
                                idx_map = [cov_tks.index(t) for t in top_tks if t in cov_tks]
                                ordered_tks = [cov_tks[i] for i in idx_map]
                                cov_sub = cov_ff[np.ix_(idx_map, idx_map)]
                                mk = min_variance_with_cov(ordered_tks, cov_sub, cap=cap_mk)
                                st.caption(
                                    f"Modelo FF: {ff_model.n_obs} obs · fatores MKT/SMB/HML"
                                    + (f" · avisos: {ff_model.warnings}" if ff_model.warnings else "")
                                )
                            except Exception as _ff_err:
                                st.warning(f"Fama-French indisponível ({_ff_err}); usando Ledoit-Wolf.")

                        elif cov_method == "DCC-GARCH":
                            try:
                                from core.dcc_garch import fit_dcc_garch
                                import pandas as _pd_dcc
                                if returns.shape[0] < 12:
                                    st.warning("DCC-GARCH requer ≥ 12 observações; usando Ledoit-Wolf.")
                                else:
                                    returns_df = _pd_dcc.DataFrame(returns, columns=top_tks)
                                    dcc = fit_dcc_garch(returns_df)
                                    R_t = dcc.correlations[-1]          # K×K correlação condicional atual
                                    vols = dcc.volatilities[-1]          # K volatilidades condicionais atuais
                                    D = np.diag(vols)
                                    cov_dcc = D @ R_t @ D                # Σ_T condicional
                                    mk = min_variance_with_cov(
                                        list(dcc.tickers), cov_dcc, cap=cap_mk
                                    )
                                    from core.dcc_garch import dcc_regime_score, result_summary
                                    summ = result_summary(dcc)
                                    st.caption(
                                        f"DCC-GARCH: α={dcc.alpha:.3f}, β={dcc.beta:.3f} · "
                                        f"ρ̄ atual={summ['avg_corr_latest']:.3f} · "
                                        f"regime score={summ['regime_score']:.2f}"
                                    )
                            except Exception as _dcc_err:
                                st.warning(f"DCC-GARCH indisponível ({_dcc_err}); usando Ledoit-Wolf.")

                        if mk is None:
                            mk = min_variance_capped(top_tks, returns, cap=cap_mk)
                        score_map_top = {
                            tk: float(df_scored[df_scored["Ticker"] == tk]["score"].iloc[0])
                            for tk in top_tks
                        }
                        w_score_norm = (
                            _apply_cap_soft(
                                _weights_from_scores(top_tks, score_map_top, _GAMMA_DEF),
                                cap_mk, _SOFT_DEF,
                            )
                        )
                        w_hibrid = pesos_hibridos_score_markowitz(
                            w_score_norm, mk, alpha=alpha_mk
                        )

                    # KPIs
                    ck1, ck2, ck3 = st.columns(3)
                    with ck1:
                        st.markdown(_kpi_macro(
                            "Vol. anualizada (MV)",
                            f"{mk.expected_std * (12**0.5)*100:.1f}%",
                            f"Método: {mk.method}",
                            _COR_INF,
                        ), unsafe_allow_html=True)
                    with ck2:
                        st.markdown(_kpi_macro(
                            "Diversificação",
                            f"{mk.diversification:.2f}",
                            "1 − Σwᵢ² (1 = totalmente; 0 = single)",
                            _COR_POS if mk.diversification >= 0.6 else _COR_ALT,
                        ), unsafe_allow_html=True)
                    with ck3:
                        st.markdown(_kpi_macro(
                            "α híbrido",
                            f"{alpha_mk:.2f}",
                            "score × min-variance",
                            _COR_NEU,
                        ), unsafe_allow_html=True)

                    # Tabela comparativa
                    import pandas as _pd
                    df_w = _pd.DataFrame([{
                        "Ticker":      tk,
                        "Score":       round(score_map_top.get(tk, 0.0), 1),
                        "Peso Score":  round(w_score_norm.get(tk, 0.0) * 100, 1),
                        "Peso Min-Var": round(mk.weights.get(tk, 0.0) * 100, 1),
                        "Peso Híbrido": round(w_hibrid.get(tk, 0.0) * 100, 1),
                    } for tk in top_tks])
                    st.dataframe(
                        df_w, hide_index=True, use_container_width=True,
                        column_config={
                            "Score":         st.column_config.NumberColumn(format="%.1f"),
                            "Peso Score":    st.column_config.NumberColumn(format="%.1f%%"),
                            "Peso Min-Var":  st.column_config.NumberColumn(format="%.1f%%"),
                            "Peso Híbrido":  st.column_config.NumberColumn(format="%.1f%%"),
                        },
                    )
                    _cov_label = {
                        "Fama-French": "Fama-French Σ estruturada (M1 banca)",
                        "DCC-GARCH":   "DCC-GARCH Σ condicional (M2 banca)",
                    }.get(cov_method, "Ledoit-Wolf shrinkage")
                    st.caption(
                        f"✓ Janela de 36 meses, cap {cap_mk*100:.0f}%, "
                        f"covariância: {_cov_label}. "
                        f"Concentração efetiva (HHI) caiu de "
                        f"{sum(w**2 for w in w_score_norm.values()):.3f} (score) para "
                        f"{sum(w**2 for w in mk.weights.values()):.3f} (min-variance)."
                    )

    # ── Preço justo + sustentabilidade de dividendos ─────────────────────────
    with st.expander("💰 Preço justo e sustentabilidade de dividendos"):
        st.caption(
            "Responde ao que o ranking relativo não responde: **a ação está "
            "cara ou barata em termos absolutos?** e **o dividendo é "
            "sustentável?** "
            "**MS Graham** = margem de segurança via lucro/patrimônio "
            "(√(22.5/(P/L·P/VP))−1). **MS Bazin** = margem via dividendos "
            "(DY/yield-alvo−1). **Sustentabilidade** = payout + cobertura por "
            "Fluxo de Caixa Operacional (FCO)."
        )
        if df_scored.empty:
            st.info("Sem empresas scoradas para analisar.")
        else:
            _bazin_alvo = st.slider(
                "Yield-alvo Bazin (%)", 4.0, 12.0, 6.0, 0.5,
                key="b3_av_bazin_alvo",
                help="Yield desejado para o método Bazin. Padrão 6%. "
                     "Quanto maior o alvo, mais exigente a margem de segurança.",
            ) / 100.0
            try:
                from core.valuation import valuation_table
                _val_df = valuation_table(df_scored, bazin_yield_alvo=_bazin_alvo)
                # Junta o score para contexto e ordena
                _val_df = _val_df.merge(
                    df_scored[["Ticker", "score"]], on="Ticker", how="left"
                ).rename(columns={"score": "Score"})
                _val_df = _val_df.sort_values("Score", ascending=False)
                _val_cols = [
                    "Ticker", "Score", "MS Graham (%)", "MS Bazin (%)",
                    "Veredito Preço", "Sustentab. Dividendo", "Score Sust.", "Motivo",
                ]
                st.dataframe(
                    _val_df[_val_cols], hide_index=True, use_container_width=True,
                    column_config={
                        "Score":          st.column_config.NumberColumn(format="%.1f"),
                        "MS Graham (%)":  st.column_config.NumberColumn(format="%.1f%%",
                            help="Margem de segurança Graham. >0 = abaixo do valor justo"),
                        "MS Bazin (%)":   st.column_config.NumberColumn(format="%.1f%%",
                            help="Margem de segurança Bazin. >0 = DY supera o yield-alvo"),
                        "Score Sust.":    st.column_config.ProgressColumn(
                            format="%.0f", min_value=0, max_value=100,
                            help="Sustentabilidade do dividendo (0-100)"),
                    },
                )
                _n_desc = _val_df["Veredito Preço"].str.contains("Desconto", na=False).sum()
                _n_caro = _val_df["Veredito Preço"].str.contains("Caro", na=False).sum()
                _n_sust = _val_df["Sustentab. Dividendo"].str.contains("Sustentável", na=False).sum()
                _v1, _v2, _v3 = st.columns(3)
                with _v1:
                    st.metric("🟢 Com desconto", f"{_n_desc} cias",
                              help="Graham/Bazin indicam preço abaixo do justo")
                with _v2:
                    st.metric("🔴 Caras", f"{_n_caro} cias",
                              help="Negociando acima do valor justo estimado")
                with _v3:
                    st.metric("💧 Dividendo sustentável", f"{_n_sust} cias",
                              help="Payout saudável + FCO positivo")
                st.caption(
                    "⚠️ Graham assume P/L e P/VP positivos (empresa lucrativa com "
                    "patrimônio positivo) — empresas fora disso aparecem sem MS Graham. "
                    "Valores extremos de DY/Payout podem indicar evento não recorrente."
                )
            except Exception as _val_err:
                st.warning(f"Valuation indisponível: {_val_err}")

    # ── Resiliência histórica + saúde financeira ─────────────────────────────
    with st.expander("🛡️ Resiliência histórica e saúde financeira — sensibilidade Brasil"):
        st.caption(
            "Três ajustes que capturam empresas de qualidade temporariamente "
            "deprimidas por fatores macro/políticos — problema estrutural em "
            "mercados emergentes. "
            "**A** Bônus de reversão: empresa abaixo da própria média histórica "
            "mas acima da mediana setorial → sinal de oportunidade. "
            "**B** Penalidade de balanço: endividamento crítico + margens negativas "
            "→ risco de sobrevivência (separado do score de qualidade). "
            "**C** Bônus de valuation: P/VP ou EV/EBIT abaixo do próprio histórico "
            "sem deterioração de fundamentais → margem de segurança implícita."
        )
        if df_scored.empty:
            st.info("Sem empresas scoradas para analisar.")
        else:
            _res_cols_exist = all(
                c in df_scored.columns
                for c in ("hist_bonus", "health_penalty", "val_hist_bonus")
            )
            if _res_cols_exist:
                _res_df = df_scored[[
                    "Ticker", "score", "hist_bonus", "val_hist_bonus",
                    "health_penalty",
                ]].copy()
                _res_df["ajuste_liquido"] = (
                    _res_df["hist_bonus"] + _res_df["val_hist_bonus"]
                    - _res_df["health_penalty"]
                ).round(1)
                _res_df["saude"] = _res_df["health_penalty"].apply(
                    lambda p: "🔴 Risco" if p >= 20 else ("🟡 Alerta" if p >= 8 else "🟢 Saudável")
                )
                _res_df = _res_df.rename(columns={
                    "score":          "Score",
                    "hist_bonus":     "Bônus Hist. (%)",
                    "val_hist_bonus": "Bônus Valuation (%)",
                    "health_penalty": "Penalidade Balanço (%)",
                    "ajuste_liquido": "Ajuste Líquido (%)",
                    "saude":          "Saúde",
                })
                st.dataframe(
                    _res_df, hide_index=True, use_container_width=True,
                    column_config={
                        "Score":                  st.column_config.NumberColumn(format="%.1f"),
                        "Bônus Hist. (%)":        st.column_config.NumberColumn(format="%.1f%%",
                            help="A: empresa abaixo da própria média histórica + acima da mediana setorial"),
                        "Bônus Valuation (%)":    st.column_config.NumberColumn(format="%.1f%%",
                            help="C: P/VP ou EV/EBIT abaixo do próprio histórico sem colapso de fundamentais"),
                        "Penalidade Balanço (%)": st.column_config.NumberColumn(format="%.1f%%",
                            help="B: risco de sobrevivência (alavancagem crítica + margem negativa)"),
                        "Ajuste Líquido (%)":     st.column_config.NumberColumn(format="%.1f%%",
                            help="Impacto total dos três ajustes no score (positivo = beneficiou)"),
                    },
                )
                # KPIs de resumo
                _n_risk    = (_res_df["Penalidade Balanço (%)"] >= 20).sum()
                _n_alert   = ((_res_df["Penalidade Balanço (%)"] >= 8) &
                              (_res_df["Penalidade Balanço (%)"] < 20)).sum()
                _n_bonus   = (_res_df["Ajuste Líquido (%)"] > 0).sum()
                _kr1, _kr2, _kr3 = st.columns(3)
                with _kr1:
                    st.metric("🔴 Risco de balanço", f"{_n_risk} cias",
                              help="Penalidade ≥ 20% — endividamento crítico + margem negativa")
                with _kr2:
                    st.metric("🟡 Em alerta", f"{_n_alert} cias",
                              help="Penalidade 8–20% — balanço frágil mas não crítico")
                with _kr3:
                    st.metric("📈 Com bônus líquido", f"{_n_bonus} cias",
                              help="Empresas que se beneficiaram dos ajustes A ou C")
            else:
                st.info(
                    "Ajustes de resiliência não disponíveis — é necessário "
                    "histórico por ticker (df_hist_batch) para calcular A e C. "
                    "O ajuste B (saúde financeira) sempre é aplicado ao score."
                )

    # ── Banca M4ui (2026-05-25): Waterfall Shapley dos engines ────────────────
    with st.expander("🧬 Atribuição Shapley do score — explainability"):
        st.caption(
            "Decomposição quantitativa exata (Lundberg-Lee 2017) do "
            "score_entrada em contribuições por engine. Substitui explicação "
            "qualitativa por valores em pontos exatos: 'Score Base contribuiu "
            "+18 pts, Quality +5 pts, Risk -12 pts'."
        )
        if df_scored.empty:
            st.info("Sem empresas scoradas para analisar.")
        else:
            # Aplica Shapley nos top 10
            from views.empresas_b3 import _compute_score_entrada
            from core.shapley_xai import (
                compute_shapley_engines, explain_score_entrada_shapley,
            )
            df_se = _compute_score_entrada(df_scored.copy(),
                                            anos_hist=anos_hist or {})
            top_tks = df_se["Ticker"].tolist()[:10]
            ticker_sel = st.selectbox(
                "Ticker para análise Shapley",
                top_tks, key="b3_shap_ticker",
            )
            row_sel = df_se[df_se["Ticker"] == ticker_sel].iloc[0].to_dict()
            phis = compute_shapley_engines(row_sel)
            explic = explain_score_entrada_shapley(row_sel)

            # Waterfall plotly
            import plotly.graph_objects as go
            labels = ["Baseline (50)"] + [lbl for lbl, _, _ in explic] + ["Score Final"]
            values = [50.0] + [v for _, v, _ in explic] + [0.0]
            measures = ["absolute"] + ["relative"] * len(explic) + ["total"]
            colors_map = {
                "positivo": "#00C896",
                "negativo": "#FC5C7D",
                "neutro":   "#9CA3AF",
            }
            text_arr = [""] + [f"{v:+.1f}" for _, v, _ in explic] + [f"{50.0 + sum(phis.values()):.1f}"]

            fig = go.Figure(go.Waterfall(
                name="Shapley",
                orientation="v",
                measure=measures,
                x=labels,
                y=values,
                text=text_arr,
                textposition="outside",
                connector={"line": {"color": "#4A5568"}},
                decreasing={"marker": {"color": "#FC5C7D"}},
                increasing={"marker": {"color": "#00C896"}},
                totals={"marker": {"color": "#4A9EFF"}},
            ))
            fig.update_layout(
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                font_color="#CBD5E0",
                showlegend=False,
                yaxis={"title": "Score (0-100)", "range": [0, 100]},
                margin={"t": 30, "b": 0, "l": 0, "r": 0},
                height=350,
            )
            st.plotly_chart(fig, use_container_width=True,
                            config={"displayModeBar": False},
                            key=f"shap_water_{ticker_sel}")

            # Tabela com phi exato
            import pandas as _pd
            df_phi = _pd.DataFrame([
                {"Engine": lbl, "φ (Shapley)": v,
                 "Sinal": ("▲" if sn == "positivo"
                           else "▼" if sn == "negativo" else "—")}
                for lbl, v, sn in explic
            ])
            st.dataframe(df_phi, hide_index=True, use_container_width=True,
                         column_config={"φ (Shapley)": st.column_config.NumberColumn(format="%+.2f pts")})
            st.caption(
                f"Σ φ = {sum(phis.values()):+.2f} pts (baseline 50, "
                f"score final {50 + sum(phis.values()):.1f}). "
                f"Atribuição satisfaz axiomas Lundberg-Lee 2017 "
                f"(efficiency, symmetry, dummy, additivity)."
            )

    # ── Banca M3ui (2026-05-25): Black-Litterman views ────────────────────────
    with st.expander("🔮 Black-Litterman — incorporar suas views"):
        st.caption(
            "Combine prior de equilíbrio (CAPM/IBOV) com SUAS views sobre "
            "retornos esperados via formalismo Bayesiano. Cada view tem "
            "confidence em (0, 1]: alta = sobrepõe ao prior; baixa = "
            "prior domina. Views: absoluta ('PETR3 vai render X%') ou "
            "relativa ('PETR3 vai render X pp acima de VALE3')."
        )
        if df_scored.empty or len(df_scored) < 2:
            st.info("Precisa ao menos 2 empresas scoradas para usar BL.")
        else:
            top_bl = df_scored["Ticker"].tolist()[:8]
            top_bl = [tk for tk in top_bl if tk in df_precos.columns]
            if len(top_bl) < 2:
                st.warning("Top empresas sem séries históricas suficientes.")
            else:
                col_run, col_prior, col_tau = st.columns([1, 1, 1])
                with col_run:
                    run_bl = st.checkbox("Aplicar BL", value=False,
                                          key="b3_bl_run")
                with col_prior:
                    prior_pct = st.number_input(
                        "Prior CAPM (%)", 0.0, 50.0, 10.0, 0.5,
                        key="b3_bl_prior", disabled=not run_bl,
                        help="Retorno de equilíbrio assumido para todos os ativos",
                    )
                with col_tau:
                    tau_bl = st.select_slider(
                        "τ (incerteza prior)",
                        options=[0.025, 0.05, 0.10],
                        value=0.025, key="b3_bl_tau",
                        disabled=not run_bl,
                    )

                st.markdown("**Adicionar views:**")
                v_col1, v_col2, v_col3, v_col4 = st.columns([1, 2, 1, 1])
                with v_col1:
                    v_type = st.selectbox("Tipo", ["absolute", "relative"],
                                           key="b3_bl_v_type",
                                           disabled=not run_bl)
                with v_col2:
                    if v_type == "absolute":
                        v_tk = st.selectbox("Ticker", top_bl,
                                             key="b3_bl_v_tk",
                                             disabled=not run_bl)
                        v_tickers = [v_tk]
                        v_weights = [1.0]
                    else:
                        c_tk1, c_tk2 = st.columns(2)
                        with c_tk1:
                            tk1 = st.selectbox("Maior", top_bl,
                                                key="b3_bl_v_tk1",
                                                disabled=not run_bl)
                        with c_tk2:
                            tk2 = st.selectbox("Menor", [t for t in top_bl
                                                          if t != tk1],
                                                key="b3_bl_v_tk2",
                                                disabled=not run_bl)
                        v_tickers = [tk1, tk2]
                        v_weights = [1.0, -1.0]
                with v_col3:
                    v_ret = st.number_input(
                        "Retorno (%)" if v_type == "absolute" else "Spread (pp)",
                        -50.0, 100.0, 15.0, 0.5, key="b3_bl_v_ret",
                        disabled=not run_bl,
                    )
                with v_col4:
                    v_conf = st.slider("Confiança", 0.1, 1.0, 0.6, 0.05,
                                        key="b3_bl_v_conf",
                                        disabled=not run_bl)

                if run_bl:
                    import numpy as _np
                    from core.black_litterman import BLView, bl_combined_optimization

                    view = BLView(
                        view_type=v_type, tickers=v_tickers, weights=v_weights,
                        expected_return=v_ret / 100.0, confidence=v_conf,
                    )
                    rets_hist = (df_precos[top_bl].iloc[-36:].pct_change()
                                  .dropna().to_numpy())
                    if len(rets_hist) < 6:
                        st.warning("Histórico < 6 retornos — janela insuficiente.")
                    else:
                        prior = _np.full(len(top_bl), prior_pct / 100.0)
                        bl = bl_combined_optimization(top_bl, prior, rets_hist,
                                                       [view], tau=tau_bl)
                        # Tabela prior vs posterior
                        df_bl = _pd.DataFrame([{
                            "Ticker":     tk,
                            "Prior (%)":  prior[i] * 100,
                            "Posterior (%)": bl["expected_returns"][i] * 100,
                            "Δ (pp)":     (bl["expected_returns"][i] - prior[i]) * 100,
                        } for i, tk in enumerate(top_bl)])
                        st.dataframe(df_bl, hide_index=True,
                                      use_container_width=True,
                                      column_config={
                            "Prior (%)":     st.column_config.NumberColumn(format="%.2f%%"),
                            "Posterior (%)": st.column_config.NumberColumn(format="%.2f%%"),
                            "Δ (pp)":        st.column_config.NumberColumn(format="%+.2f"),
                        })
                        st.caption(
                            f"View aplicada: tipo `{v_type}` | "
                            f"confidence={v_conf:.2f} | τ={tau_bl}. "
                            f"Maior shift vs prior: "
                            f"{bl['shift_max']*100:.2f}pp."
                        )

                        # ── Banca M3++ (2026-05-28): pesos ótimos BL→Markowitz ──
                        st.markdown("**Pesos ótimos (BL → Markowitz):**")
                        col_cap_bl, col_ra_bl = st.columns(2)
                        with col_cap_bl:
                            cap_bl = st.select_slider(
                                "Cap por ativo",
                                options=[0.20, 0.25, 0.30, 0.40, 0.50],
                                value=0.40,
                                format_func=lambda x: f"{x*100:.0f}%",
                                key="b3_bl_cap",
                            )
                        with col_ra_bl:
                            ra_bl = st.select_slider(
                                "Aversão a risco (δ)",
                                options=[1.0, 2.5, 5.0, 8.0],
                                value=2.5, key="b3_bl_ra",
                            )
                        from core.black_litterman import apply_bl_to_markowitz
                        opt = apply_bl_to_markowitz(
                            top_bl, prior, rets_hist, [view],
                            tau=tau_bl, cap=cap_bl, risk_aversion=ra_bl,
                            periods_per_year=252,
                        )
                        df_w = _pd.DataFrame([{
                            "Ticker": tk,
                            "Peso (%)": opt["weights"][tk] * 100,
                        } for tk in top_bl]).sort_values("Peso (%)", ascending=False)
                        kc1, kc2, kc3 = st.columns(3)
                        with kc1:
                            st.markdown(_kpi_macro(
                                "Retorno esperado",
                                f"{opt['expected_portfolio_return']*100:+.1f}%",
                                "anualizado (E[R_p])", _COR_POS,
                            ), unsafe_allow_html=True)
                        with kc2:
                            st.markdown(_kpi_macro(
                                "Volatilidade",
                                f"{opt['expected_portfolio_vol']*100:.1f}%",
                                "anualizada (σ_p)", _COR_INF,
                            ), unsafe_allow_html=True)
                        with kc3:
                            st.markdown(_kpi_macro(
                                "Sharpe implícito",
                                f"{opt['sharpe_implicit']:.2f}",
                                "E[R_p] / σ_p", _COR_NEU,
                            ), unsafe_allow_html=True)
                        st.dataframe(
                            df_w, hide_index=True, use_container_width=True,
                            column_config={
                                "Peso (%)": st.column_config.NumberColumn(format="%.1f%%"),
                            },
                        )
                        st.caption(
                            f"Mean-variance com posterior BL + covariância 36m "
                            f"anualizada. Cap {cap_bl*100:.0f}% respeitado "
                            f"(max {max(opt['weights'].values())*100:.1f}%). "
                            f"δ={ra_bl} controla agressividade do tilt vs prior."
                        )

    # ── Banca A6c (2026-05-26): UI cross-source validation ────────────────────
    with st.expander("🔍 Validação cross-source — Fundamentus × DRE"):
        st.caption(
            "Detecta divergências entre fontes do mesmo indicador. "
            "Indicadores com spread > threshold (15-30% por tipo) são "
            "flagados — empresa pode ter dado contaminado em uma das fontes."
        )
        if df_scored.empty:
            st.info("Sem empresas scoradas para validar.")
        else:
            run_a6 = st.checkbox("Executar validação", value=False, key="b3_a6_run")
            if run_a6:
                from core.cross_source import (
                    compare_indicators, batch_validate, resumo_validacao,
                )
                # Monta dict {ticker: {indicador: {fonte: valor}}}
                # Sources: df_mult_enrich (Fundamentus/StatusInvest pipeline)
                # vs df_filt (DRE bruto). MVP: usa o que tem na pipeline atual.
                indics_check = ("ROE", "ROIC", "Margem_Liquida",
                                 "Margem_Operacional", "P/L", "P/VP", "DY",
                                 "EV_EBIT", "Endividamento_Total")
                dados_xs: dict = {}
                # Para o MVP, simula 2 fontes: pipeline atual + DRE histórico
                # (último ano). Em produção, plugar com cache de scrapers.
                for tk in df_scored["Ticker"].tolist()[:30]:
                    row_p = df_mult_enrich[df_mult_enrich["Ticker"] == tk]
                    df_h  = (hist_batch or {}).get(tk)
                    if row_p.empty:
                        continue
                    r_pipeline = row_p.iloc[0]
                    r_dre = (df_h.sort_values("Data").iloc[-1].to_dict()
                             if df_h is not None and not df_h.empty
                             and "Data" in df_h.columns else {})
                    inds_ticker: dict = {}
                    for ind in indics_check:
                        v_pipeline = r_pipeline.get(ind)
                        v_dre = r_dre.get(ind)
                        if v_pipeline is None and v_dre is None:
                            continue
                        # Só compara se ambas as fontes têm valor não-nulo
                        if v_pipeline is None or v_dre is None:
                            continue
                        try:
                            inds_ticker[ind] = {
                                "pipeline": float(v_pipeline),
                                "dre":      float(v_dre),
                            }
                        except (TypeError, ValueError):
                            continue
                    if inds_ticker:
                        dados_xs[tk] = inds_ticker

                flags = batch_validate(dados_xs, indicadores=indics_check)
                resumo = resumo_validacao(flags)

                # KPIs
                ck1, ck2, ck3 = st.columns(3)
                with ck1:
                    st.markdown(_kpi_macro(
                        "Total flags",
                        str(resumo["total"]),
                        f"em {len(dados_xs)} empresas auditadas",
                        _COR_NEU if resumo["total"] == 0 else _COR_ALT,
                    ), unsafe_allow_html=True)
                with ck2:
                    st.markdown(_kpi_macro(
                        "Critical",
                        str(resumo["critical"]),
                        "spread > 2× threshold",
                        _COR_NEG if resumo["critical"] > 0 else _COR_POS,
                    ), unsafe_allow_html=True)
                with ck3:
                    st.markdown(_kpi_macro(
                        "Tickers afetados",
                        str(resumo["tickers_afetados"]),
                        ", ".join(resumo.get("tickers_critical", [])[:5]) or "—",
                        _COR_ALT,
                    ), unsafe_allow_html=True)

                if flags:
                    import pandas as _pd
                    df_flags = _pd.DataFrame([{
                        "Ticker":     f.ticker,
                        "Indicador":  f.indicador,
                        "Severidade": f.severidade,
                        "Mediana":    f.mediana,
                        "Spread (%)": f.spread_rel * 100,
                        "Pipeline":   f.valores.get("pipeline"),
                        "DRE":        f.valores.get("dre"),
                    } for f in flags])
                    st.dataframe(
                        df_flags, hide_index=True, use_container_width=True,
                        column_config={
                            "Spread (%)": st.column_config.NumberColumn(format="%.1f%%"),
                            "Mediana":    st.column_config.NumberColumn(format="%.3f"),
                            "Pipeline":   st.column_config.NumberColumn(format="%.3f"),
                            "DRE":        st.column_config.NumberColumn(format="%.3f"),
                        },
                    )
                    st.caption(
                        "Critical = spread > 2× threshold. Use `consensus_value()` "
                        "(mediana das fontes) para resolver — devolve `None` "
                        "automaticamente se spread > 50%."
                    )
                else:
                    st.success(
                        f"✓ Nenhuma divergência detectada em {len(dados_xs)} "
                        f"empresa(s) e {len(indics_check)} indicador(es).",
                        icon="✅",
                    )

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

    # ── SCORE DE ENTRADA — COMPOSIÇÃO AVANÇADA ──────────────────────────────
    st.markdown("<hr style='margin:20px 0;border-color:#1E2533;'>", unsafe_allow_html=True)
    _sec_hdr("🎯 Score de Entrada — Composição Avançada")

    if not df_scored.empty:
        df_entrada = _compute_score_entrada(df_scored, anos_hist)

        # ── KPI de distribuição de status ────────────────────────────────────
        n_aprov = int((df_entrada["status_entrada"] == "Aprovada").sum())
        n_obs   = int((df_entrada["status_entrada"] == "Observação").sum())
        n_excl  = int((df_entrada["status_entrada"] == "Excluída").sum())
        avg_se  = float(df_entrada["score_entrada"].mean())

        ke1, ke2, ke3, ke4 = st.columns(4, gap="small")
        for col, val, label, cor, css_cls in [
            (ke1, n_aprov, "Aprovadas",   "#00C896", "b3-entrada-aprovada"),
            (ke2, n_obs,   "Observação",  "#F6C90E", "b3-entrada-observacao"),
            (ke3, n_excl,  "Excluídas",   "#FC5C7D", "b3-entrada-excluida"),
            (ke4, None,    "Score Médio", "#4A9EFF", None),
        ]:
            display = f"{avg_se:.1f}" if val is None else str(val)
            col.markdown(
                f'<div style="background:#12151E;border:1px solid #1E2533;'
                f'border-radius:10px;padding:14px 16px;">'
                f'<div style="font-size:0.58rem;font-weight:800;text-transform:uppercase;'
                f'letter-spacing:.12em;color:#4A5568;margin-bottom:6px;">{label}</div>'
                f'<div style="font-size:1.55rem;font-weight:800;color:{cor};'
                f'line-height:1.1;">{display}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )

        st.markdown("<br>", unsafe_allow_html=True)

        # ── Breakdown completo (tabela) ───────────────────────────────────────
        with st.expander("📊 Breakdown completo — todas as empresas", expanded=False):
            st.caption(
                "**Score de Entrada** = Score Base × 0,60 + Quality × 0,20 "
                "+ Consistência × 0,10 + Caixa × 0,10 − Penalidade Risco  ·  [0–100]"
            )
            cols_show = ["Ticker", "score_base", "q_bonus", "c_bonus", "cq_bonus",
                         "risk_probability", "risk_driver", "r_penalty",
                         "score_entrada", "status_entrada"]
            cols_exist = [c for c in cols_show if c in df_entrada.columns]
            df_tbl = df_entrada[cols_exist].copy()
            df_tbl.columns = [
                c.replace("score_base",    "Base (v2)")
                 .replace("q_bonus",       "Quality")
                 .replace("c_bonus",       "Consistência")
                 .replace("cq_bonus",      "Caixa")
                 .replace("risk_probability", "Prob. Risco")
                 .replace("risk_driver",   "Driver")
                 .replace("r_penalty",     "Penalidade")
                 .replace("score_entrada", "Score Entrada")
                 .replace("status_entrada","Status")
                for c in df_tbl.columns
            ]
            st.dataframe(
                df_tbl.sort_values("Score Entrada", ascending=False),
                use_container_width=True,
                hide_index=True,
                height=min(480, 60 + 35 * len(df_tbl)),
                column_config={
                    "Ticker":       st.column_config.TextColumn("Ticker",       width="small"),
                    "Base (v2)":    st.column_config.NumberColumn("Base (v2)",  format="%.1f"),
                    "Quality":      st.column_config.NumberColumn("Quality",    format="%.1f"),
                    "Consistência": st.column_config.NumberColumn("Consistência", format="%.1f"),
                    "Caixa":        st.column_config.NumberColumn("Caixa",      format="%.1f"),
                    "Prob. Risco":  st.column_config.NumberColumn("Prob. Risco", format="%.1f%%"),
                    "Driver":       st.column_config.TextColumn("Driver", width="small"),
                    "Penalidade":   st.column_config.NumberColumn("Penalidade", format="%.1f"),
                    "Score Entrada": st.column_config.ProgressColumn(
                        "Score Entrada", min_value=0, max_value=100, format="%.1f"
                    ),
                    "Status": st.column_config.TextColumn("Status", width="medium"),
                },
            )

        # ── Cards por empresa com explicação ─────────────────────────────────
        _sec_hdr("🔍 Detalhamento por Empresa")
        df_ent_sorted = df_entrada.sort_values("score_entrada", ascending=False)

        for i in range(0, min(len(df_ent_sorted), 20), 3):
            cols_e = st.columns(3, gap="small")
            for j, (_, row) in enumerate(df_ent_sorted.iloc[i:i+3].iterrows()):
                tk   = row["Ticker"]
                se   = float(row["score_entrada"])
                sb   = float(row["score_base"])
                stat = str(row["status_entrada"])
                rp   = float(row.get("r_penalty", 0))
                fatores: list[tuple[str, str, str]] = row.get("explicacao", [])

                css_stat = (
                    "b3-entrada-aprovada"   if stat == "Aprovada"   else
                    "b3-entrada-observacao" if stat == "Observação"  else
                    "b3-entrada-excluida"
                )
                engine_rows = ""
                for lbl, engine_col, cor_fill in [
                    ("Quality",      "q_bonus",  "#4A9EFF"),
                    ("Consistência", "c_bonus",  "#00C896"),
                    ("Caixa",        "cq_bonus", "#A855F7"),
                ]:
                    val_e = float(row.get(engine_col, 50))
                    engine_rows += (
                        f'<div class="b3-engine-row">'
                        f'<span style="width:74px;color:#718096;font-size:0.66rem;">{lbl}</span>'
                        f'<div class="b3-engine-bar-bg">'
                        f'<div class="b3-engine-bar-fill" style="width:{val_e:.0f}%;'
                        f'background:{cor_fill};opacity:0.75;"></div></div>'
                        f'<span style="width:26px;text-align:right;color:#CBD5E0;'
                        f'font-size:0.66rem;">{val_e:.0f}</span>'
                        f'</div>'
                    )

                fatores_html = ""
                for fator, valor, sinal in fatores[:4]:
                    css_f = f"b3-fator-{sinal}"
                    fatores_html += (
                        f'<span class="b3-score-badge" style="'
                        f'background:rgba(30,37,51,1);border:1px solid #2D3748;'
                        f'margin:2px 2px 0 0;font-size:0.60rem;">'
                        f'<span class="{css_f}">{valor}</span>'
                        f' <span style="color:#4A5568;">{fator}</span>'
                        f'</span>'
                    )

                pen_html = (
                    f'<div style="font-size:0.64rem;color:#FC5C7D;margin-top:4px;">'
                    f'⚠ Penalidade: −{rp:.0f} pts</div>'
                    if rp > 0 else ""
                )

                with cols_e[j]:
                    st.markdown(
                        f'<div class="b3-card" style="border-color:'
                        f'{"rgba(0,200,150,.30)" if stat=="Aprovada" else "rgba(246,201,14,.20)" if stat=="Observação" else "rgba(252,92,125,.20)"}'
                        f';">'
                        f'<div style="display:flex;justify-content:space-between;'
                        f'align-items:flex-start;margin-bottom:8px;">'
                        f'<div>'
                        f'<div style="font-size:0.90rem;font-weight:800;color:#E2E8F0;">{tk}</div>'
                        f'<div style="font-size:0.62rem;color:#4A5568;margin-top:1px;">'
                        f'Base: {sb:.0f}  →  Entrada: <b style="color:#E2E8F0;">{se:.0f}</b></div>'
                        f'</div>'
                        f'<span class="{css_stat}">{stat}</span>'
                        f'</div>'
                        f'{engine_rows}'
                        f'<div style="margin-top:6px;line-height:1.6;">{fatores_html}</div>'
                        f'{pen_html}'
                        f'</div>',
                        unsafe_allow_html=True,
                    )
    else:
        st.caption("Score de Entrada disponível após cálculo do Score v2.")

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

    # Custos de transação: ATIVOS por default (backtest realista PF Brasil).
    # Desmarcar mostra o backtest "ideal" (sem corretagem/spread/IR) — útil só
    # para comparação teórica, NÃO para expectativa de retorno real.
    aplicar_custos = st.checkbox(
        "Descontar custos de transação (corretagem, spread, IR) — recomendado",
        value=True, key="b3_av_custos",
        help="Ativado: backtest realista para PF no Brasil (spread bid-ask, "
             "IR 15% sobre lucro acima da isenção mensal de R$ 20k). "
             "Desativado: backtest 'ideal' sem custos — superestima o retorno.",
    )

    if st.button("▶ Simular Backtest", type="primary", key="b3_av_btn_simular"):
        from core.transaction_costs import CostConfig
        _cost_cfg = (
            CostConfig.brasil_pf_default() if aplicar_custos
            else CostConfig.desligado()
        )
        period_code = per_opts[sel_per]
        tks_tuple   = tuple(sorted(tks_uni))
        usar_gamma  = len(tks_uni) >= 5
        anos_map    = {"1y": 1, "3y": 3, "5y": 5, "10y": 10}
        data_inicio = pd.Timestamp.now() - pd.DateOffset(years=anos_map[period_code])

        with st.spinner("Baixando preços mensais… (pode levar alguns segundos)"):
            df_prec_m = _batch_yf_precos_mensais(tks_tuple, period=period_code)

        with st.spinner("Baixando dividendos históricos (pode demorar na primeira execução)…"):
            div_batch_av = _batch_yf_dividendos_mensais(tks_tuple, period=period_code)

        df_bt, tickers_top, n_efetivo = _simular_backtest(
            df_prec_m, df_scored, hist_batch, tks_uni,
            float(aporte), data_inicio, float(taxa_sel) / 100.0,
            pesos_v2, tk_grupos,
            top_n_max=int(top_n), usar_gamma=usar_gamma,
            gamma=st.session_state.get("b3_av_gamma", _GAMMA_DEF),
            cap=st.session_state.get("b3_av_cap",   _CAP_DEF),
            soft=st.session_state.get("b3_av_soft",  _SOFT_DEF),
            selic_por_ano=selic_macro or None,
            macro_by_year=macro_history or None,
            dividendos=div_batch_av,
            cost_cfg=_cost_cfg,
        )
        st.session_state["b3_av_bt_df"]    = df_bt
        st.session_state["b3_av_bt_custos"] = aplicar_custos
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

        _custos_on = st.session_state.get("b3_av_bt_custos", True)
        if _custos_on:
            st.caption(
                "✅ Backtest com **custos de transação descontados** "
                "(corretagem, spread bid-ask e IR 15% sobre lucro acima da "
                "isenção mensal) — estimativa realista para PF no Brasil."
            )
        else:
            st.caption(
                "⚠️ Backtest **sem custos** (ideal/teórico) — superestima o "
                "retorno real. Marque a opção de custos para projeção realista."
            )

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

    if not df_mult_enrich.empty and tks_uni:
        df_qc = df_mult_enrich[df_mult_enrich["Ticker"].isin(tks_uni)].copy()
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
                if not df_mult_enrich.empty and c in df_mult_enrich.columns]
    if len(_sc_opts) >= 2:
        sc1, sc2 = st.columns(2)
        ind_x = sc1.selectbox("Eixo X", _sc_opts, index=0, key="b3_av_scx")
        ind_y = sc2.selectbox("Eixo Y", _sc_opts, index=min(1, len(_sc_opts) - 1),
                              key="b3_av_scy")

        if st.button("🔭 Gerar Scatter", key="b3_av_btn_scatter"):
            df_sc = df_mult_enrich[df_mult_enrich["Ticker"].isin(tks_uni)].copy()
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
        fco_rows, fco_audit = _fco_lucro_rows(batch_fco)
        st.session_state["b3_av_fco_rows"] = fco_rows
        st.session_state["b3_av_fco_audit"] = fco_audit
        st.session_state["b3_av_fco_ran"] = True

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
        df_fco_disp = df_fco[["Empresa", "Ano", "FCO", "Lucro", "FCO/Lucro", "Fonte"]].copy()
        df_fco_disp["FCO"]      = df_fco_disp["FCO"].map(_fv)
        df_fco_disp["Lucro"]    = df_fco_disp["Lucro"].map(_fv)
        df_fco_disp["FCO/Lucro"] = df_fco_disp["FCO/Lucro"].map(lambda v: f"{v:.2f}x")
        st.dataframe(df_fco_disp.set_index("Empresa"), use_container_width=True,
                     height=min(300, 50 + 35 * len(df_fco_disp)))
        audit = st.session_state.get("b3_av_fco_audit", {})
        if audit.get("fallback_fcf_fci", 0):
            st.caption(
                f"FCO calculado por FCF - FCI em {audit['fallback_fcf_fci']} empresa(s), "
                "pois o App4 ainda não possui coluna FCO direta na tabela histórica."
            )
    else:
        st.caption("Clique **💵 Calcular FCO/Lucro** para gerar o gráfico.")

    # ── Metodologia e referências científicas ────────────────────────────────
    _render_metodologia()


def _render_metodologia() -> None:
    """Seção final: técnicas aplicadas e os autores/estudos que as fundamentam."""
    try:
        from core import metodologia as _met
    except Exception:
        return

    _sec_hdr("🔬 Metodologia e referências científicas")
    st.caption(
        f"As empresas deste portfólio não são fruto de opinião: passam por "
        f"**{_met.total_metodos()} análises quantitativas** encadeadas, cada uma "
        "fundamentada em literatura acadêmica consolidada de finanças. "
        "Abaixo, o que cada etapa faz e o estudo que a embasa — para você "
        "confiar no porquê de cada ticker selecionado."
    )

    with st.expander("📚 Ver todas as análises aplicadas e suas referências", expanded=False):
        for etapa, metodos in _met.catalogo_por_etapa().items():
            st.markdown(f"#### {etapa}")
            linhas = [
                f"- **{m.nome}** — {m.o_que_faz}  \n"
                f"  <span style='color:#94A3B8;font-size:0.85em'>📖 {m.referencia}</span>"
                for m in metodos
            ]
            st.markdown("\n".join(linhas), unsafe_allow_html=True)
            st.markdown("")

        st.divider()
        st.markdown("#### 📑 Referências bibliográficas")
        st.caption(
            "Estudos e autores que fundamentam as técnicas acima "
            "(ordem alfabética):"
        )
        refs = "\n".join(f"{i}. {r}" for i, r in enumerate(_met.referencias_ordenadas(), 1))
        st.markdown(refs)

        st.info(
            "⚠️ A robustez metodológica reforça a qualidade da triagem, mas "
            "não elimina o risco de mercado. Use como apoio à decisão, não "
            "como recomendação automática."
        )


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
        'Análise fundamentalista de empresas listadas na B3 e construção '
        'quantitativa de portfólios aplicáveis com dados reais.'
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

    col_t1, col_t2, col_t3, col_t4, col_t5 = st.columns([2, 2, 2.5, 2.5, 2.5])
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
    with col_t4:
        if st.button("🚀 Criação de Portfólio", use_container_width=True,
                     type="primary" if active == 3 else "secondary",
                     key="b3_tab3"):
            st.session_state["b3_active_tab"] = 3
            st.rerun()
    with col_t5:
        if st.button("🧠 Análise de Portfólio", use_container_width=True,
                     type="primary" if active == 4 else "secondary",
                     key="b3_tab4"):
            st.session_state["b3_active_tab"] = 4
            st.rerun()

    st.markdown("<hr style='margin:4px 0 16px;border-color:#1E2533;'>",
                unsafe_allow_html=True)

    if active == 0:
        _tab_empresas(df_set)
    elif active == 1:
        _tab_analise(df_set)
    elif active == 2:
        _tab_avancada(df_set)
    elif active == 3:
        from views import portfolio_b3

        portfolio_b3.render(show_header=False)
    else:
        from views import analise_portfolio_b3

        analise_portfolio_b3.render(show_header=False)
