"""
views/empresas_b3.py  — Dashboard Fundamentalista B3
  Tab 1 — Empresas por Setor   (listagem por setor + logos)
  Tab 2 — Análise de Empresa   (drilldown: crescimento, DRE, múltiplos)

Banco de dados:  core.b3_data (facade c/ MARKET_READ_SOURCE) → b3_db legado ou market.*
Preços:          yfinance     (sem dependência de DB)
Logos:           thefintz/icones-b3 CDN (público, sem auth)
"""
from __future__ import annotations

import hashlib
import html
import json
import re

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
import yfinance as yf

import core.b3_data as _db  # facade c/ feature flag MARKET_READ_SOURCE (default: legacy)
import core.data_quality as _dq
import core.data_reconciliacao as _recon
import core.market_read as _mr  # séries do market.* (preços mensais ajustados) p/ backtest
from core.b3_methodology import SCORE_VERSION
from core.market_companies import filter_market_companies, normalize_b3_companies
from design.componentes import (
    badge_status,
    card_metrica,
    container_pagina,
)  # KPIs em cards CSS (visual coeso)
from design.market_companies import (
    render_company_search,
    render_market_css,
    render_market_tabs,
    render_sector_grid,
)

# ── Constantes ────────────────────────────────────────────────────────────────
_CDN = "https://raw.githubusercontent.com/thefintz/icones-b3/main/icones"


def _stable_signature(payload: dict) -> str:
    """Assinatura estável para invalidar resultados de sessão obsoletos."""
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]

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


def _kpi_macro(titulo: str, valor: str, sub: str, cor: str) -> str:
    """Card compacto de KPI (mesmo visual de views/investimentos._kpi_macro)."""
    return (
        f'<div style="background:#12151E;border:1px solid #1E2533;'
        f'border-radius:8px;padding:13px 11px 9px;height:100%;">'
        f'<div style="font-size:0.54rem;font-weight:800;text-transform:uppercase;'
        f'letter-spacing:0.11em;color:#4A5568;margin-bottom:5px;">{titulo}</div>'
        f'<div style="font-size:1.15rem;font-weight:800;color:{cor};'
        f'letter-spacing:-0.02em;line-height:1.1;margin-bottom:3px;'
        f'white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">{valor}</div>'
        f'<div style="font-size:0.62rem;color:#4A5568;line-height:1.3;">{sub}</div>'
        f'</div>'
    )


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
def _period_to_years(period: str) -> int | None:
    """'10y'->10, '5y'->5, '1y'->1; None se não reconhecer (sem corte)."""
    try:
        p = str(period).strip().lower()
        if p.endswith("y"):
            return int(p[:-1])
    except Exception:
        pass
    return None


@st.cache_data(ttl=86400, show_spinner=False)
def _yf_ibov_mensal(period: str = "5y") -> pd.Series:
    """Fechamento mensal do IBOV (^BVSP) — benchmark de mercado real do
    backtest (auditoria 2026-07: o 'Benchmark' equal-weight é interno ao
    universo filtrado; sem o índice, 'bater o benchmark' não significava
    bater o mercado)."""
    try:
        raw = yf.download("^BVSP", period=period, interval="1mo",
                          auto_adjust=True, progress=False)
        if raw is None or raw.empty:
            return pd.Series(dtype=float)
        close = raw["Close"]
        if isinstance(close, pd.DataFrame):
            close = close.iloc[:, 0]
        close.index = pd.to_datetime(close.index)
        return close.dropna()
    except Exception:
        return pd.Series(dtype=float)


def _batch_yf_precos_mensais(tickers: tuple[str, ...], period: str = "5y") -> pd.DataFrame:
    """
    Preços mensais de fechamento AJUSTADOS (retorno total) p/ múltiplos tickers.

    market-first: lê de market.historical_prices (sem rede) quando o market.*
    está ativo; senão cai no yfinance (auto_adjust=True). Retorna DataFrame com
    DatetimeIndex mensal e colunas = tickers sem .SA.
    """
    if not tickers:
        return pd.DataFrame()
    # Fonte primária: banco (market.*) — adjusted_close, sem rede.
    if _db.market_active():
        try:
            df_db = _mr.load_precos_mensais(tuple(tickers))
            if not df_db.empty:
                anos = _period_to_years(period)
                if anos and df_db.index.notna().any():
                    corte = df_db.index.max() - pd.DateOffset(years=anos)
                    df_db = df_db[df_db.index >= corte]
                return df_db
        except Exception:
            pass  # fallback yfinance
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


# _batch_yf_liquidez (liquidez média diária via yfinance) foi removida: o
# filtro de liquidez da Análise Avançada lê _db.load_giro_diario() (armazém
# local), em paridade com a Criação de Portfólio.


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
    cont: dict[str, int] = {}
    for tk in tickers_ranked:
        setor = group_map.get(tk) or "—"
        if cont.get(setor, 0) < max_por_setor:
            sel.append(tk)
            cont[setor] = cont.get(setor, 0) + 1
        if len(sel) >= top_n:
            break
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


# Nota: o backtest deixou de baixar/reinvestir dividendos mensais — o preço
# (adjusted_close, retorno total) já embute proventos. A antiga
# _batch_yf_dividendos_mensais foi removida por dupla contagem.


# ══════════════════════════════════════════════════════════════════════════════
# TAB 1 — Empresas por Setor
# ══════════════════════════════════════════════════════════════════════════════

def _eh_acao(ticker: str, nome: str = "") -> bool:
    """
    True só para AÇÕES (ON/PN/units de empresas); filtra FIIs, ETFs, BDRs e
    subscrições do card. Critério por sufixo do ticker B3:
      3/4/5/6/7/8 → ON/PN/PNA/PNB/PNC/PND (ação);
      11          → unit: ação só se tiver nome real (senão é FII/ETF);
      demais (34/35/32, 3511, 211, 12…) → BDR/subscrição/outro → não-ação.
    """
    ticker_norm = str(ticker).strip().upper().replace(".SA", "")
    # Código padrão B3: raiz de quatro caracteres alfanuméricos + classe.
    # Evita resíduos corrompidos do legado como "C3" e "K3".
    if not re.fullmatch(r"[A-Z0-9]{4}\d{1,2}", ticker_norm):
        return False
    m = re.search(r"(\d+)$", ticker_norm)
    if not m:
        return False
    num = m.group(1)
    if num in {"3", "4", "5", "6", "7", "8"}:
        return True
    if num == "11":
        n = str(nome or "").strip()
        return n != "" and n.upper() != str(ticker).strip().upper()
    return False


def _so_acoes(df_set: pd.DataFrame) -> pd.DataFrame:
    """Mantém só ações no card de Empresas B3 (remove FIIs/ETFs/BDRs)."""
    if df_set.empty or "ticker" not in df_set.columns:
        return df_set
    mask = df_set.apply(
        lambda r: _eh_acao(r["ticker"], r.get("nome_empresa", "")), axis=1)
    return df_set[mask].reset_index(drop=True)


def _tab_empresas(df_set: pd.DataFrame) -> None:
    if df_set.empty:
        st.warning(
            "Tabela `setores` não encontrada no banco configurado. "
            "Configure `SUPABASE_DB_URL_B3` no `.env` ou nos secrets do Streamlit Cloud."
        )
        return
    # Card mostra só AÇÕES — remove FIIs, ETFs, BDRs e subscrições.
    df_set = _so_acoes(df_set)
    if df_set.empty:
        st.info("Nenhuma ação encontrada na lista de setores.")
        return
    companies = normalize_b3_companies(df_set, _logo_url)
    busca = render_company_search(
        label="🔍 Buscar ticker (ex.: PETR4)",
        placeholder="Digite e pressione Enter", key="b3_busca",
    )
    if busca:
        ticker_query = busca.upper().replace(".SA", "")
        if ticker_query in set(companies["ticker"]):
            st.session_state["b3_ticker_sel"] = ticker_query
            st.session_state["b3_active_tab"] = 1
            st.rerun()
        companies = filter_market_companies(companies, busca)
    render_sector_grid(
        companies, key_prefix="b3", selected_ticker=st.session_state.get("b3_ticker_sel"),
        selected_state_key="b3_ticker_sel", active_state_key="b3_active_tab",
        unclassified_label="Sem classificação setorial B3",
    )


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

# Fonte unica da verdade das faixas: core/data_quality.py (margens apertadas
# para [-100%, +100%], o que captura outliers como UGPA3=190%).
_SCORE_RANGES = _dq.CANONICAL_RANGES


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


def _sem_acentos(texto: str) -> str:
    """Remove acentos (NFD) p/ matching robusto de nomes de setor B3."""
    import unicodedata
    return "".join(
        ch for ch in unicodedata.normalize("NFD", texto)
        if unicodedata.category(ch) != "Mn"
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

    # Fix auditoria 2026-07: matching determinístico. O matching antigo por
    # palavra solta mandava "Consumo não Cíclico" para o perfil CÍCLICO
    # (palavra "consumo") e "Saúde"/"Comunicações" para o genérico (acentos
    # não normalizados). Agora: normaliza acentos dos dois lados e exige a
    # chave COMPLETA como substring, testando da mais longa para a mais
    # curta ("consumo nao ciclico" antes de "consumo ciclico").
    s_norm = _sem_acentos((setor or "").lower())
    for key in sorted(_PESOS_SETOR, key=len, reverse=True):
        if key in s_norm:
            return _norm_pesos(_PESOS_SETOR[key])
    return _norm_pesos(_PESOS_GENERICO)


# Múltiplos de preço (menor = mais barato). Já existem nas vintages históricas.
_CHEAPNESS_FACTORS: tuple[str, ...] = ("P/L", "P/VP", "EV_EBIT")


def _aplicar_cheapness(
    pesos: dict[str, tuple[float, bool]], w: float
) -> dict[str, tuple[float, bool]]:
    """Mistura fatores de barganha (preço baixo) ao score de qualidade.

    w em [0,1]: reduz o peso da qualidade por (1-w) e adiciona os múltiplos de
    preço (P/L, P/VP, EV/EBIT, menor=melhor) com peso total w, dividido entre
    eles. Como os pesos de entrada somam 1, a saída também soma ~1. Fatores
    ausentes nos dados são ignorados pelo motor de score (não quebram).
    w=0 devolve os pesos originais (comportamento padrão: só qualidade).
    """
    try:
        w = float(w)
    except (TypeError, ValueError):
        return pesos
    if not (w > 0.0):
        return pesos
    w = min(w, 1.0)
    out: dict[str, tuple[float, bool]] = {
        k: (v[0] * (1.0 - w), v[1]) for k, v in pesos.items()
    }
    por_fator = w / len(_CHEAPNESS_FACTORS)
    for f in _CHEAPNESS_FACTORS:
        prev = out.get(f, (0.0, False))[0]
        out[f] = (prev + por_fator, False)  # False = menor é melhor
    return out


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
    scores = [float(s) if (s is not None and np.isfinite(s)) else 0.0 for s in scores]
    mn  = min(scores) if scores else 0.0
    eps = 1e-6
    raw = [(max(s - mn, 0) + eps) ** gamma for s in scores]
    tot = sum(raw) or 1.0
    return {tk: r / tot for tk, r in zip(tickers, raw)}


def _apply_cap_soft(weights: dict[str, float],
                    cap: float = 0.25, soft: float = 0.05) -> dict[str, float]:
    """Aplica tilt suave e projeta em pesos válidos com cap estrito."""
    from core.portfolio_constraints import project_capped_simplex

    tilted = {
        tk: float(v) + (float(v) - soft) * 0.5 if float(v) > soft else float(v)
        for tk, v in weights.items()
    }
    return project_capped_simplex(tilted, cap)


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
    # Cutover: com fonte limpa (market.*) NÃO se imputa — nulo vira rank neutro
    # (ausente = "não sei"), não valor reconstruído. Só repara no legado.
    if not _db.market_active():
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
        # Cutover: no market (dado limpo) não imputa — NaN segue p/ rank neutro.
        s = (pd.to_numeric(df[col], errors="coerce") if _db.market_active()
             else _impute_with_group_median(df, col, group_col))
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
    "2.21.0": (
        "Auditoria cruzada 2026-07-04 (Codex + Claude): "
        "(P1) _get_pesos_setor com normalizacao de acentos e matching "
        "deterministico chave-completa/mais-longa-primeiro — 'Consumo nao "
        "Ciclico' recebia pesos do perfil CICLICO e 'Saude'/'Comunicacoes' "
        "caiam no generico; "
        "(P2) fonte market ponto-no-tempo: load_multiplos_todos(ano_ref_max) "
        "passa a servir metricas do exercicio ANUAL fechado (antes: snapshot "
        "TTM de hoje rotulado com o ano do ultimo balanco); historico anual "
        "limitado ao ultimo ano COM demonstracao publicada; "
        "(P3) gate de completude >= 60%% ponderada no ranking, com lista "
        "visivel de excluidas (antes: ausente virava 0,5 neutro sem limite); "
        "(P4) backtest sem fallback para scores de hoje (era look-ahead) — "
        "inicio da serie cortado no 1o ano com score historico, aviso na UI; "
        "(P5) calibracao gamma/cap/soft via walk-forward purged (Lopez de "
        "Prado) com custos — a versao in-sample foi aposentada da UI; "
        "(P6) captions honestas: custos descontados sao SO de compra (IR de "
        "venda nao e modelado — nao ha vendas), aviso quantificado de vies "
        "de sobrevivencia no resultado; "
        "(P7) Black-Litterman: fix anualizacao 252->12 (vol exibida estava "
        "~4,6x inflada), prior rotulado como uniforme (nao CAPM); "
        "(P8) core/markowitz: Ledoit-Wolf real (intensidade fechada LW2004, "
        "decresce com T) + solver exato SLSQP (scipy em requirements) — "
        "projecao heuristica agora se declara converged=False; "
        "(P9) alpha_selic sem dupla escala na Avaliacao de Portfolio (LLM "
        "recebia 1250%% em vez de 12,5%%); "
        "(P10) Criacao de Portfolio: precos cortados por ano_inicio (Selic/EW "
        "nao recebem mais aportes antes da estrategia existir), overlay do "
        "snapshot atual restrito ao entry guard (backtest usa historico puro) "
        "e reconciliacao web desativada quando market ativo; "
        "(P11) dividendos (R$/acao) fora do grafico de DRE absoluta — "
        "grafico proprio com unidade correta."
    ),
    "2.22.0": (
        "Auditoria cruzada 2026-07-04, rodada 2 (pendencias 1-4): "
        "(Q1) rebalance anual do backtest movido de JANEIRO para ABRIL "
        "(_REBAL_MONTH=4) — balancos FY N-1 sao publicados ate 31/03 (CVM); "
        "janeiro antecipava 2-4 meses de informacao contabil; mesmo ajuste "
        "no backtest por segmento da Criacao de Portfolio; "
        "(Q2) novo modo 'rebalance com vendas' em _simular_backtest: "
        "_executar_rebalance_vendas vende excedentes com custo de venda + "
        "IR 15%% sobre lucro realizado (isencao PF R$ 20k/mes, "
        "core/transaction_costs.custo_venda, que nunca tinha call site) e "
        "recompra deficits; custo medio por ticker rastreado; IR pago e "
        "custos exibidos na UI; "
        "(Q3) benchmark de MERCADO real: linha 'IBOV (aportes)' no backtest "
        "(mesmo fluxo de aportes aplicado ao ^BVSP) — o 'Benchmark' "
        "equal-weight e interno ao universo filtrado; "
        "(Q4) validacao preditiva do score: _rank_ic_por_ano calcula "
        "rank-IC (Spearman) do score (lag=1) vs retorno do ano seguinte, "
        "por ano, com spread top-bottom tercil — expander proprio na "
        "Analise Avancada com interpretacao honesta (Grinold & Kahn); "
        "(Q5) ETL point-in-time (migracao 019): period_end_date, "
        "first_seen_at (proxy de available_at; nunca sobrescrito em "
        "re-ingestao) e raw_payload_id (proveniencia) nas demonstracoes; "
        "(Q6) revisao adversarial da propria rodada: IR apurado por MES "
        "com isencao BINARIA de 20k + compensacao de prejuizos intra-mes "
        "(IN RFB 1.585/2015 — o custo_venda pro-rata subestimava IR), "
        "guarda contra caixa destruido sem destino com preco, dividendo "
        "reinvestido entra na base de custo do IR, linha IBOV paga a mesma "
        "friccao de compra, rank-IC com janela pos-publicacao "
        "(marco/N -> marco/N+1; ano parcial excluido; guarda de IC NaN; "
        "metadados do calculo exibidos), renormalize preserva/corrige "
        "proveniencia (antes zerava raw_payload_id) e cache de colunas sem "
        "envenenamento por falha transitoria + reset por run."
    ),
    "2.23.0": (
        "Auditoria 2026-07-05: cap por projeção matemática, restrição setorial "
        "estrita, universo completo de ações, liquidez fail-closed, validação "
        "temporal com holdouts, valuation por preço de fechamento, TTM com "
        "trimestres consecutivos e versionamento forte de carteiras."
    ),
    "2.24.0": (
        "Auditoria 2026-07-06: caixa pendente em meses sem cotação, nested "
        "walk-forward com fold final intocado, cap global obrigatório, seleção "
        "de segmentos com BH-FDR, quarentena de baselines PIT e substituição "
        "atômica de métricas obsoletas."
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
# Mês do rebalance anual do backtest (auditoria 2026-07): balanços FY N−1
# são publicados até 31/03 (CVM) — rebalancear em JANEIRO antecipava 2-4
# meses de informação contábil. Abril é o primeiro mês realista.
_REBAL_MONTH = 4


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
            if len(tks_cand) * cap < 1.0 - 1e-12:
                continue
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
    df_hist_batch: dict[str, pd.DataFrame] | None = None,
    pesos: dict[str, tuple[float, bool]] | None = None,
    tk_grupos: dict[str, dict] | None = None,
    macro_by_year: dict[int, dict[str, float]] | None = None,
) -> tuple[float, float, float]:
    """
    Walk-forward temporal com blocos futuros realmente fora da amostra.
    Cada combinação de parâmetros é avaliada somente nos holdouts posteriores
    ao histórico de treino, separados por purge e embargo. A escolha usa a
    mediana do objetivo nos holdouts e shrinkage para os defaults.

    O purge cria o intervalo sem observações entre treino e teste. O embargo
    separa o fim do teste do próximo fold. O sinal contábil continua sujeito à
    disponibilidade dos snapshots históricos; essa limitação é exibida na UI.
    """
    if df_precos.empty or not df_hist_batch or not pesos:
        return _GAMMA_DEF, _CAP_DEF, _SOFT_DEF
    score_history: dict[pd.Timestamp, dict[str, float]] = {}
    years = sorted({int(date.year) for date in pd.to_datetime(df_precos.index)})
    for year in years:
        score_map = _score_historico_ano(
            df_hist_batch,
            tickers_all,
            year,
            pesos,
            tk_grupos,
            lag=1,
            macro_by_year=macro_by_year,
        )
        if score_map:
            score_history[pd.Timestamp(year, _REBAL_MONTH, 1)] = score_map
    from core.allocation_calibration import purged_walk_forward_calibration

    params, diagnostics = purged_walk_forward_calibration(
        df_precos,
        score_history,
        tickers_all,
        gamma_grid=_GAMMA_GRID,
        cap_grid=_CAP_GRID,
        soft_grid=_SOFT_GRID,
        defaults=(_GAMMA_DEF, _CAP_DEF, _SOFT_DEF),
        n_folds=n_folds,
        min_train_months=min_window,
        purge_months=purge_months,
        embargo_months=embargo_months,
        shrinkage=_CAL_SHRINK,
        cost_cfg=cost_cfg,
    )
    st.session_state["b3_av_calibration_diagnostics"] = diagnostics
    return params


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
        if "AvailableAt" in validos.columns:
            available_at = pd.to_datetime(
                validos["AvailableAt"], errors="coerce", utc=True
            ).dt.tz_convert(None)
            decision_date = pd.Timestamp(ano_ref, _REBAL_MONTH, 1)
            # NaT = linha do backfill (migration_baseline), cuja disponibilidade
            # real é desconhecida — vale o CORTE FISCAL já aplicado acima, então
            # ela PASSA. Só as vintages com carimbo real (first_seen_proxy)
            # respeitam o ponto-no-tempo estrito. Exigir notna() aqui zerava o
            # histórico inteiro assim que a 1ª vintage real aparecia no lote
            # (a coluna vem para o batch todo) → "Nenhum segmento retornou
            # dados suficientes" na Criação de Portfólio.
            validos = validos[
                available_at.isna() | (available_at <= decision_date)
            ]
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
    # descarta scores não-finitos (NaN/inf): empresa sem score computável naquele
    # ano não entra no ranking — evita poluir pesos/backtest com NaN (alpha NaN).
    sc = pd.to_numeric(df_sc["score"], errors="coerce")
    return {tk: float(s) for tk, s in zip(df_sc["Ticker"], sc)
            if pd.notna(s) and np.isfinite(s)}


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


def _executar_rebalance_vendas(
    cotas: dict[str, float],
    custo_total: dict[str, float],
    pesos_alvo: dict[str, float],
    precos: dict[str, float],
    cost_cfg,
    tax_state: dict[str, float] | None = None,
) -> tuple[float, float, float]:
    """Realinha `cotas` aos pesos-alvo VENDENDO excedentes e comprando déficits.

    Modo "rebalance com vendas" do backtest (auditoria 2026-07): vende o que
    excede o peso-alvo e usa o caixa líquido p/ comprar os subalocados.
    Preço médio por ticker mantido em `custo_total` (custo de aquisição
    acumulado) p/ apurar o lucro tributável.

    IR apurado NO NÍVEL DO MÊS (fix revisão 2026-07, IN RFB 1.585/2015):
      • isenção BINÁRIA — se as vendas do mês excedem R$ 20k, TODO o ganho
        líquido do mês é tributável (não apenas a fração excedente);
      • prejuízos do mesmo mês compensam (soma algébrica dos lucros);
      • prejuízos são carregados entre rebalances via ``tax_state`` e
        compensam ganhos tributáveis posteriores.
    Nota de viés documentada: a série de preços é AJUSTADA (retorno total),
    então o "lucro" tributado embute distribuições que podem ter tratamento
    fiscal distinto do ganho de capital. Desde 2026, dividendos também podem
    integrar a tributação mínima de altas rendas. O IR exibido é aproximação,
    não apuração fiscal individual.

    Muta `cotas` e `custo_total` in place.
    Retorna (ir_pago, custos_venda, custos_compra).
    """
    from core.transaction_costs import custo_compra, custo_venda

    val = {tk: cotas.get(tk, 0.0) * px for tk, px in precos.items()
           if cotas.get(tk, 0.0) > 0 and px > 0}
    v_total = sum(val.values())
    if v_total <= 0:
        return 0.0, 0.0, 0.0
    # Guarda (fix revisão 2026-07): sem nenhum destino com preço no mês,
    # NÃO vende — antes o caixa das vendas era destruído (patrimônio sumia).
    if not any(precos.get(tk, 0.0) > 0 and w > 0 for tk, w in pesos_alvo.items()):
        return 0.0, 0.0, 0.0

    vendas_mes = lucro_mes = fee_v_tot = fee_c_tot = caixa = 0.0

    # 1) vende excedentes (posições fora da nova carteira têm alvo 0);
    #    aqui só custos de EXECUÇÃO — o IR é apurado depois, no mês.
    for tk, v_tk in val.items():
        alvo = pesos_alvo.get(tk, 0.0) * v_total
        if v_tk <= alvo + 1e-9:
            continue
        px = precos[tk]
        q = cotas[tk]
        sell_val = v_tk - alvo
        q_sell = min(sell_val / px, q)
        avg = custo_total.get(tk, 0.0) / q if q > 0 else px
        fee, _ = custo_venda(tk, sell_val, 0.0, 0.0, cost_cfg)  # só spread/corretagem
        vendas_mes += sell_val
        lucro_mes  += (px - avg) * q_sell        # soma ALGÉBRICA (prejuízo compensa)
        fee_v_tot  += fee
        caixa += max(sell_val - fee, 0.0)
        cotas[tk] = q - q_sell
        custo_total[tk] = max(custo_total.get(tk, 0.0) - avg * q_sell, 0.0)

    # 2) IR mensal — isenção binária + compensação intra-mês (RFB 1.585/2015)
    ir_tot = 0.0
    tax_state = tax_state if tax_state is not None else {}
    prejuizo_acumulado = max(float(tax_state.get("prejuizo_acumulado", 0.0)), 0.0)
    if lucro_mes < 0:
        prejuizo_acumulado += abs(lucro_mes)
    if (getattr(cost_cfg, "ativo", False)
            and vendas_mes > getattr(cost_cfg, "isencao_mes", 20_000.0)
            and lucro_mes > 0):
        lucro_tributavel = max(lucro_mes - prejuizo_acumulado, 0.0)
        prejuizo_acumulado = max(prejuizo_acumulado - lucro_mes, 0.0)
        ir_tot = lucro_tributavel * getattr(cost_cfg, "ir_rate", 0.15)
        caixa = max(caixa - ir_tot, 0.0)
    tax_state["prejuizo_acumulado"] = prejuizo_acumulado

    if caixa <= 0:
        return ir_tot, fee_v_tot, fee_c_tot

    # 3) compra os subalocados proporcionalmente ao déficit
    deficits = {}
    for tk, w in pesos_alvo.items():
        px = precos.get(tk)
        if not px or px <= 0 or w <= 0:
            continue
        v_tk = cotas.get(tk, 0.0) * px
        alvo = w * v_total
        if alvo > v_tk + 1e-9:
            deficits[tk] = alvo - v_tk
    if not deficits:
        # sem déficit (caso raro): redistribui o caixa proporcional aos pesos
        deficits = {tk: w for tk, w in pesos_alvo.items()
                    if precos.get(tk) and precos[tk] > 0 and w > 0}
    tot_def = sum(deficits.values())
    if tot_def <= 0:
        return ir_tot, fee_v_tot, fee_c_tot
    for tk, d in deficits.items():
        gasto = caixa * d / tot_def
        fee_c = custo_compra(tk, gasto, cost_cfg)
        liq = max(gasto - fee_c, 0.0)
        cotas[tk] = cotas.get(tk, 0.0) + liq / precos[tk]
        custo_total[tk] = custo_total.get(tk, 0.0) + gasto
        fee_c_tot += fee_c
    return ir_tot, fee_v_tot, fee_c_tot


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
    rebal_month: int = _REBAL_MONTH,     # auditoria 2026-07: abril (FY N−1 publicado)
    rebalance_com_vendas: bool = False,  # True: vende excedentes (custos venda + IR)
) -> tuple[pd.DataFrame, list[str], int]:
    """
    Simula aportes mensais com rebalanceamento anual e publication lag = 1.
    Score do ano N é calculado com dados até N−1 (sem look-ahead bias).
    O rebalance dispara no primeiro mês >= rebal_month (default ABRIL —
    balanços FY N−1 publicados até 31/03; auditoria 2026-07: janeiro
    antecipava 2-4 meses de informação contábil).
    selic_por_ano: taxa Selic real por ano (da tabela macro); fallback = taxa_selic_aa.
    dividendos: dict {ticker: pd.Series mensal R$/ação} para reinvestimento (App1-compatible).
    cost_cfg: CostConfig com fee/spread/IR (fix banca C2c, 2026-05-25).
      None ⇒ comportamento legado sem custos (compatibilidade).
    rebalance_com_vendas:
      False (legado) ⇒ rebalance NÃO vende cotas; só redireciona aportes
        futuros — apenas custos de COMPRA; IR de venda não se aplica.
      True ⇒ realinha a carteira aos novos pesos vendendo excedentes:
        custos de venda + IR 15% (isenção PF R$ 20k/mês) via
        _executar_rebalance_vendas; totais em attrs (ir_pago, custos_rebal).
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

    # Fix auditoria 2026-07: o fallback antigo usava scores de HOJE
    # (df_scored) quando faltava histórico do ano — look-ahead direto (o
    # ranking atual "escolhia" empresas no passado). Agora a simulação
    # COMEÇA no primeiro ano com score histórico disponível (dados até
    # N−1) e o corte vale p/ estratégia, benchmark e Selic (janela justa).
    # Anos pulados vão em df_resultado.attrs["anos_sem_score"] p/ a UI.
    anos_serie = sorted({d.year for d in df.index})
    primeiro_ano_valido = None
    for _ano_scan in anos_serie:
        if _score_historico_ano(df_hist_batch, tks_all_valid, _ano_scan,
                                pesos, tk_grupos, lag=1,
                                macro_by_year=macro_by_year):
            primeiro_ano_valido = _ano_scan
            break
    if primeiro_ano_valido is None:
        return pd.DataFrame(), [], 0
    anos_sem_score = [a for a in anos_serie if a < primeiro_ano_valido]
    # Início efetivo: primeiro mês >= rebal_month do primeiro ano com score —
    # a estratégia só pode agir quando os balanços do exercício-base já foram
    # publicados, e o corte vale p/ todas as séries (janela justa).
    df = df[(df.index.year > primeiro_ano_valido) |
            ((df.index.year == primeiro_ano_valido) &
             (df.index.month >= rebal_month))]
    if df.empty:
        return pd.DataFrame(), [], 0

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
    # custo de aquisição acumulado por ticker (base p/ IR no modo com vendas)
    custo_total_est: dict[str, float] = {tk: 0.0 for tk in tks_all_valid}
    total_ir_pago      = 0.0
    total_custos_rebal = 0.0
    restricoes_inviaveis: list[str] = []
    last_prices: dict[str, float] = {}
    tax_state: dict[str, float] = {"prejuizo_acumulado": 0.0}
    caixa_est = 0.0
    caixa_bench = 0.0

    for dt, row in df.iterrows():
        ano = dt.year
        # Selic anual: macro quando disponível, fallback ao parâmetro do usuário
        selic_aa_ano  = (selic_por_ano or {}).get(ano, taxa_selic_aa)
        taxa_mensal   = (1 + selic_aa_ano) ** (1 / 12) - 1
        selic_acum    = selic_acum * (1 + taxa_mensal) + aporte

        # ── Rebalanceamento anual com publication lag ──────────────────────
        # Dispara no primeiro mês >= rebal_month do ano (abril: FY N−1 já
        # publicado). Jan-mar mantêm a carteira do ano anterior.
        if ano != ultimo_ano_rebal and dt.month >= rebal_month:
            ultimo_ano_rebal = ano

            # Calcula scores com dados até ano − 1
            score_map = _score_historico_ano(
                df_hist_batch, tks_all_valid, ano, pesos, tk_grupos, lag=1,
                macro_by_year=macro_by_year,
            )
            # Fix auditoria 2026-07: sem fallback p/ scores de HOJE (era
            # look-ahead). Gap de histórico no meio da série ⇒ mantém a
            # carteira vigente sem rebalance neste ano (o início da série
            # já foi cortado no pré-scan acima).
            if score_map:
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
                if usar_gamma:
                    from core.portfolio_constraints import minimum_assets_for_cap
                    n_yr = min(
                        len(tks_ranked_yr),
                        max(n_yr, minimum_assets_for_cap(cap)),
                    )
                tickers_yr = tks_ranked_yr[:n_yr]

                if usar_gamma and len(tickers_yr) >= 2:
                    try:
                        pesos_est = _apply_cap_soft(
                            _weights_from_scores(tickers_yr, score_map, gamma), cap, soft
                        )
                    except ValueError as exc:
                        pesos_est = {
                            tk: 1.0 / len(tickers_yr) for tk in tickers_yr
                        }
                        restricoes_inviaveis.append(f"{ano}: {exc}")
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
                                        cap=cap,
                                    )
                        except Exception:
                            pass  # fallback silencioso para gamma-tilt puro
                else:
                    pesos_est = (
                        {tk: 1.0 / len(tickers_yr) for tk in tickers_yr}
                        if tickers_yr else {}
                    )
                tks_est_valid = list(pesos_est.keys())

                # Modo com vendas: realinha a carteira EXISTENTE aos novos
                # pesos (vende excedentes c/ custos+IR, recompra déficits).
                if rebalance_com_vendas:
                    precos_now = {
                        tk: float(row.get(tk) or 0) for tk in tks_all_valid
                        if pd.notna(row.get(tk)) and float(row.get(tk) or 0) > 0
                    }
                    _ir_x, _fv_x, _fc_x = _executar_rebalance_vendas(
                        cotas_est, custo_total_est, pesos_est,
                        precos_now, cost_cfg, tax_state,
                    )
                    total_ir_pago      += _ir_x
                    total_custos_rebal += _fv_x + _fc_x

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
                        _val_div = div * cotas_est[tk]
                        cotas_est[tk] += _val_div / px_tk
                        # base de custo p/ IR (fix revisão 2026-07): dividendo
                        # reinvestido é compra a mercado — sem isso o custo
                        # médio caía e o IR do modo com vendas inflava.
                        custo_total_est[tk] = custo_total_est.get(tk, 0.0) + _val_div
                    if cotas_bench[tk] > 0:
                        cotas_bench[tk] += div * cotas_bench[tk] / px_tk

        # ── Aportes mensais ────────────────────────────────────────────────
        # Fix banca C2c (2026-05-25): desconta custo_compra do valor bruto
        # antes de converter em cotas. Sem cost_cfg ativo, custo_compra = 0
        # e comportamento e identico ao legado.
        est_disp = [tk for tk in tks_est_valid
                    if pd.notna(row.get(tk)) and float(row.get(tk, 0) or 0) > 0]
        caixa_est += aporte
        if est_disp:
            total_w = sum(pesos_est.get(tk, 0.0) for tk in est_disp) or 1.0
            for tk in est_disp:
                valor_bruto = caixa_est * pesos_est.get(tk, 0.0) / total_w
                fee = custo_compra(tk, valor_bruto, cost_cfg)
                valor_liq = max(valor_bruto - fee, 0.0)
                cotas_est[tk] += valor_liq / float(row[tk])
                custo_total_est[tk] = custo_total_est.get(tk, 0.0) + valor_bruto
            caixa_est = 0.0

        all_disp = [tk for tk in tks_all_valid
                    if pd.notna(row.get(tk)) and float(row.get(tk, 0) or 0) > 0]
        caixa_bench += aporte
        for tk in all_disp:
            last_prices[tk] = float(row[tk])
        if all_disp:
            por_tk = caixa_bench / len(all_disp)
            for tk in all_disp:
                fee = custo_compra(tk, por_tk, cost_cfg)
                valor_liq = max(por_tk - fee, 0.0)
                cotas_bench[tk] += valor_liq / float(row[tk])
            caixa_bench = 0.0

        val_est = caixa_est + sum(
            cotas_est[tk] * last_prices[tk]
            for tk in tks_all_valid
            if tk in cotas_est and tk in last_prices
        )
        val_bench = caixa_bench + sum(
            cotas_bench[tk] * last_prices[tk]
            for tk in tks_all_valid
            if tk in last_prices
        )
        rows.append({"Data": dt, "Estratégia": val_est,
                     "Benchmark": val_bench, "Tesouro Selic": selic_acum})

    df_resultado = pd.DataFrame(rows)
    if anos_sem_score:
        # metadado p/ a UI informar o corte do início da série (sem look-ahead)
        df_resultado.attrs["anos_sem_score"] = anos_sem_score
    df_resultado.attrs["rebal_month"]  = int(rebal_month)
    df_resultado.attrs["com_vendas"]   = bool(rebalance_com_vendas)
    df_resultado.attrs["ir_pago"]      = round(float(total_ir_pago), 2)
    df_resultado.attrs["custos_rebal"] = round(float(total_custos_rebal), 2)
    df_resultado.attrs["prejuizo_acumulado"] = round(
        float(tax_state.get("prejuizo_acumulado", 0.0)), 2
    )
    df_resultado.attrs["restricoes_inviaveis"] = sorted(set(restricoes_inviaveis))
    df_resultado.attrs["caixa_pendente_estrategia"] = round(float(caixa_est), 2)
    df_resultado.attrs["caixa_pendente_benchmark"] = round(float(caixa_bench), 2)
    return df_resultado, tickers_top_final, n_efetivo_final


def _rank_ic_por_ano(
    df_precos: pd.DataFrame,
    df_hist_batch: dict[str, pd.DataFrame],
    tickers: list[str],
    pesos: dict[str, tuple[float, bool]],
    tk_grupos: dict[str, dict] | None = None,
    macro_by_year: dict[int, dict[str, float]] | None = None,
    min_n: int = 5,
) -> pd.DataFrame:
    """Validação preditiva do score: rank-IC (Spearman) por ano.

    Para cada ano N: score com dados até N−1 (mesmo motor do backtest,
    lag=1) × retorno da JANELA PÓS-PUBLICAÇÃO — do fim de março/N ao fim
    de março/N+1 (fix revisão 2026-07: medir jan–dez/N seria look-ahead,
    pois o balanço FY N−1 só é público até 31/03; mesma regra do
    rebalance em abril). Anos sem a janela completa (ex.: ano corrente)
    ficam de fora — evita retornos parciais contaminando as médias.
    Também calcula o spread top-tercil − bottom-tercil. IC
    consistentemente > 0 é a evidência mínima de que o ranking
    discrimina retornos — a ausência deste teste era a pendência #4 da
    auditoria 2026-07.
    """
    if df_precos.empty or not tickers:
        return pd.DataFrame()
    tks = [t for t in tickers if t in df_precos.columns]
    if not tks:
        return pd.DataFrame()
    rebal_m = _REBAL_MONTH
    out: list[dict] = []
    for ano in sorted({d.year for d in df_precos.index}):
        # base: último preço até março/N; fim: último preço até março/N+1
        jan0 = df_precos[(df_precos.index.year == ano) &
                         (df_precos.index.month < rebal_m)]
        jan1 = df_precos[(df_precos.index.year == ano + 1) &
                         (df_precos.index.month < rebal_m)]
        if jan0.empty or jan1.empty:
            continue
        score_map = _score_historico_ano(
            df_hist_batch, tks, ano, pesos, tk_grupos, lag=1,
            macro_by_year=macro_by_year,
        )
        if len(score_map) < min_n:
            continue
        rets: dict[str, float] = {}
        for tk in score_map:
            if tk not in df_precos.columns:
                continue
            p0 = jan0[tk].dropna()
            p1 = jan1[tk].dropna()
            if not p0.empty and not p1.empty and float(p0.iloc[-1]) > 0:
                rets[tk] = float(p1.iloc[-1]) / float(p0.iloc[-1]) - 1.0
        comuns = [tk for tk in score_map if tk in rets]
        if len(comuns) < min_n:
            continue
        s = pd.Series({tk: float(score_map[tk]) for tk in comuns})
        r = pd.Series({tk: rets[tk] for tk in comuns})
        ic = float(s.rank().corr(r.rank()))   # Spearman = Pearson dos ranks
        if not np.isfinite(ic):
            continue  # scores/retornos constantes → IC indefinido
        k = max(len(comuns) // 3, 1)
        ordem = s.sort_values(ascending=False).index
        top_m = float(r[ordem[:k]].mean())
        bot_m = float(r[ordem[-k:]].mean())
        out.append({
            "Ano": ano, "N": len(comuns), "Rank-IC": round(ic, 3),
            "Top tercil (%)": round(top_m * 100, 1),
            "Bottom tercil (%)": round(bot_m * 100, 1),
            "Spread (pp)": round((top_m - bot_m) * 100, 1),
        })
    return pd.DataFrame(out)


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


def _b3_peer_scores(
    ticker: str,
    mult: pd.Series,
    df_set: pd.DataFrame,
) -> tuple[pd.Series, str]:
    """Monta o grupo comparável e calcula as seis trilhas do painel individual."""
    from core.b3_company_score import score_cross_section

    universo = _db.load_multiplos_todos()
    if universo is None:
        universo = pd.DataFrame()
    universo = universo.copy()
    if universo.empty:
        universo = pd.DataFrame([mult.to_dict()]) if not mult.empty else pd.DataFrame()
    if "Ticker" not in universo.columns:
        universo["Ticker"] = ""
    universo["Ticker"] = (
        universo["Ticker"].astype(str).str.replace(".SA", "", regex=False)
        .str.strip().str.upper()
    )

    # O snapshot individual é o mais recente e prevalece sobre a linha do universo.
    atual = mult.to_dict() if not mult.empty else {}
    atual["Ticker"] = ticker
    if ticker in set(universo["Ticker"]):
        idx = universo.index[universo["Ticker"] == ticker][0]
        for campo, valor in atual.items():
            universo.loc[idx, campo] = valor
    else:
        universo = pd.concat([universo, pd.DataFrame([atual])], ignore_index=True)

    meta_cols = [c for c in ("ticker", "SETOR", "SUBSETOR", "SEGMENTO") if c in df_set]
    if "ticker" in meta_cols:
        meta = df_set[meta_cols].copy().rename(columns={"ticker": "Ticker"})
        meta["Ticker"] = meta["Ticker"].astype(str).str.replace(".SA", "", regex=False).str.upper()
        meta = meta.drop_duplicates("Ticker")
        universo = universo.drop(columns=[c for c in ("SETOR", "SUBSETOR", "SEGMENTO")
                                           if c in universo.columns], errors="ignore")
        universo = universo.merge(meta, on="Ticker", how="left")

    alvo = universo[universo["Ticker"] == ticker]
    pares = universo
    referencia = "universo B3"
    if not alvo.empty:
        for coluna, rotulo in (
            ("SEGMENTO", "segmento"), ("SUBSETOR", "subsetor"), ("SETOR", "setor")
        ):
            if coluna not in universo.columns:
                continue
            valor = alvo.iloc[0].get(coluna)
            if pd.isna(valor) or not str(valor).strip() or str(valor) == "—":
                continue
            candidatos = universo[universo[coluna] == valor]
            if candidatos["Ticker"].nunique() >= 4:
                pares = candidatos.copy()
                referencia = f"{rotulo} {valor}"
                break

    pares = pares.drop_duplicates("Ticker").reset_index(drop=True)
    tickers = tuple(pares["Ticker"].dropna().astype(str).tolist())
    historicos: dict[str, pd.DataFrame] = {}
    try:
        historicos = _db.load_multiplos_historico_batch(tickers)
        pares = _enrich_com_slopes(pares, historicos)
    except Exception:
        # O score continua válido com crescimento neutro e cobertura reduzida.
        pass

    scored = score_cross_section(pares)
    linha = scored[scored["Ticker"] == ticker].copy()
    if linha.empty:
        return pd.Series({"Ticker": ticker, "score": 50.0, "coverage": 0.0}), referencia

    # A nota principal continua sendo calculada pelo motor oficial da B3. As
    # seis trilhas são uma decomposição diagnóstica para o radar, não um score
    # concorrente com o ranking já utilizado na Análise Avançada/portfólios.
    try:
        setor_alvo = str(alvo.iloc[0].get("SETOR") or "") if not alvo.empty else ""
        pesos = _get_pesos_setor(setor_alvo)
        oficial = _score_universo(
            pares, list(tickers), pesos, historicos,
            group_col_prefer="SEGMENTO",
        )
        nota = oficial.loc[oficial["Ticker"] == ticker, "score"]
        if not nota.empty and np.isfinite(float(nota.iloc[0])):
            linha.loc[:, "score"] = float(nota.iloc[0])
    except Exception:
        # Em indisponibilidade pontual do motor completo, a média das trilhas
        # mantém o painel utilizável e a cobertura continua explícita.
        pass
    return linha.iloc[0], f"{referencia} · {len(pares)} empresa(s)"


def _metric_decimal_pct(mult: pd.Series, key: str) -> str:
    try:
        value = float(mult.get(key))
        if not np.isfinite(value):
            return "—"
        threshold = _MAX_DECIMAL_PCT.get(key, 2.0)
        display = value if abs(value) > threshold else value * 100.0
        return f"{display:.1f}%"
    except (TypeError, ValueError):
        return "—"


def _render_b3_dossie(ticker: str, score_row: pd.Series, referencia: str) -> None:
    from core.b3_company_score import FACTOR_TRACKS, TRACK_LABELS, classification
    from core.dossie_b3 import build_dossie

    dossie = build_dossie(ticker)
    label, tipo = classification(score_row.get("score"))
    badge_status(label, tipo)
    st.caption(
        f"Classificação quantitativa relativa a {referencia}, com "
        f"{float(score_row.get('coverage', 0)):.0f}% de cobertura. "
        "Ausências ficam neutras e reduzem a cobertura."
    )

    if dossie.get("erro"):
        st.info(f"Dossiê determinístico indisponível: {dossie['erro']}")
    else:
        for flag in dossie.get("red_flags", []):
            st.warning(flag)

    fortes: list[str] = []
    invalidacoes: list[str] = []
    for coluna, trilha in TRACK_LABELS.items():
        valor = float(score_row.get(coluna, 50) or 50)
        if valor >= 65:
            fortes.append(f"{trilha}: {valor:.1f}/100 entre os pares.")
        elif valor < 40:
            invalidacoes.append(f"{trilha}: {valor:.1f}/100; exige acompanhamento.")
    if not fortes:
        fortes.append("Nenhuma trilha superou 65 pontos; a leitura permanece equilibrada.")
    if not invalidacoes:
        invalidacoes.append("Reavaliar se fundamentos, preço ou cobertura se deteriorarem.")

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Tese / pontos fortes**")
        for item in fortes:
            st.markdown(f"- {item}")
    with c2:
        st.markdown("**Condições de invalidação**")
        for item in invalidacoes:
            st.markdown(f"- {item}")

    criterios = []
    for coluna, trilha in TRACK_LABELS.items():
        key = coluna.removeprefix("score_")
        criterios.append({
            "Trilha": trilha,
            "Pontuação": float(score_row.get(coluna, 50) or 50),
            "Cobertura (%)": float(score_row.get(f"coverage_{key}", 0) or 0),
            "Indicadores B3": ", ".join(nome for nome, _ in FACTOR_TRACKS[key]),
        })
    st.markdown("**Critérios avançados da classificação**")
    st.dataframe(pd.DataFrame(criterios), hide_index=True, use_container_width=True)

    if not dossie.get("erro"):
        juros = dossie.get("sensibilidade_juros", {})
        if juros.get("regra"):
            st.markdown("**Sensibilidade aos juros brasileiros**")
            st.caption(juros["regra"])
        eventos = dossie.get("eventos_societarios", {}).get("eventos", [])
        if eventos:
            st.markdown("**Eventos societários recentes (CVM)**")
            st.dataframe(pd.DataFrame(eventos[:6]), hide_index=True, use_container_width=True)


def _render_b3_score_dashboard(
    ticker: str,
    mult: pd.Series,
    df_set: pd.DataFrame,
) -> None:
    from core.b3_company_score import TRACK_LABELS, classification

    with st.spinner("Calculando pontuação relativa e dossiê…"):
        score_row, referencia = _b3_peer_scores(ticker, mult, df_set)
    label, tipo = classification(score_row.get("score"))

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        card_metrica("Pontuação fundamentalista", f"{float(score_row.get('score', 50)):.1f}/100")
        badge_status(label, tipo)
    with c2:
        card_metrica("Cobertura", f"{float(score_row.get('coverage', 0)):.0f}%")
    with c3:
        card_metrica("ROIC", _metric_decimal_pct(mult, "ROIC"))
    with c4:
        try:
            p_fco = float(mult.get("P_FCO"))
            caixa_yield = f"{100 / p_fco:.1f}%" if np.isfinite(p_fco) and p_fco > 0 else "—"
        except (TypeError, ValueError):
            caixa_yield = "—"
        card_metrica("Retorno do fluxo de caixa operacional", caixa_yield)

    tracks = [(rotulo, float(score_row.get(coluna, 50) or 50))
              for coluna, rotulo in TRACK_LABELS.items()]
    fig = go.Figure(go.Scatterpolar(
        r=[valor for _, valor in tracks] + [tracks[0][1]],
        theta=[nome for nome, _ in tracks] + [tracks[0][0]],
        fill="toself", line=dict(color=_COR_INF),
        fillcolor="rgba(74,158,255,.22)",
    ))
    fig.update_layout(
        template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)", font=dict(color="#CBD5E0"),
        height=360, margin=dict(l=20, r=20, t=20, b=20), showlegend=False,
        polar=dict(
            radialaxis=dict(range=[0, 100], gridcolor="#2D3748", tickfont=dict(size=10)),
            angularaxis=dict(gridcolor="#4A5568"), bgcolor="rgba(0,0,0,0)",
        ),
    )
    st.plotly_chart(fig, use_container_width=True, key=f"b3_radar_{ticker}")
    st.caption(f"Referência da comparação: {referencia}. Metodologia B3 {SCORE_VERSION}.")

    with st.expander("📄 Dossiê, classificação e critérios avançados"):
        _render_b3_dossie(ticker, score_row, referencia)


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
        if _db.market_active():
            # snapshot limpo do market.* (sem reconciliação por scraping)
            mult = _db.load_multiplos(tk)
            fontes_recon = {k: "market" for k in getattr(mult, "index", [])
                            if k not in ("Ticker", "data")}
        else:
            recon = _recon.get_multiplos_reconciliados(tk)
            mult = _recon.reconciliacao_to_series(recon)
            fontes_recon = dict(recon.get("_fontes", {}))
        # DY/Payout via yfinance só como patch do legado; no market o snapshot já traz.
        yf_divs_mult = {} if _db.market_active() else _yf_multiplos_dividendos(tk)
        df_yf_divs   = _yf_dividendos_anuais(tk)
        df_precos    = _yf_precos(tk)

    # Patch DY / Payout ausentes com yfinance (apenas no legado)
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
        # color= divide os dados em 2 traces (positivo/negativo); o eixo categórico
        # usa categoryorder="trace" por padrão e agruparia os anos por SINAL,
        # quebrando a cronologia (o sort_values acima não basta: a ordem é do eixo).
        fig_ret.update_yaxes(categoryorder="array",
                             categoryarray=ret_sorted["Ano"].tolist())
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
            # Fix auditoria 2026-07: "Dividendos" saiu deste gráfico — a coluna
            # é soma anual POR AÇÃO (R$/ação: market.dividends.rate e backfill
            # yfinance), unidade incompatível com Receita/EBIT/Lucro (R$ totais);
            # no mesmo eixo a linha ficava achatada em ~0. Ganhou gráfico próprio.
            cands_dre = [
                ("Receita_Liquida", "Receita Líquida"), ("EBIT", "EBIT"),
                ("Lucro_Liquido", "Lucro Líquido"), ("Patrimonio_Liquido", "Patrimônio Líquido"),
                ("Divida_Liquida", "Dívida Líquida"), ("Divida_Total", "Dívida Total"),
                ("Ativo_Total", "Ativo Total"),
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
                    fig.update_yaxes(title="R$ (valores absolutos)")
                    st.plotly_chart(fig, use_container_width=True,
                                    config={"displayModeBar": False}, key=f"b3_dre_{tk}")

            # Dividendos em gráfico próprio, com a unidade correta (R$/ação)
            if "Dividendos" in df_fin.columns:
                _div_plot = df_fin[["Data", "Dividendos"]].copy()
                _div_plot["Dividendos"] = pd.to_numeric(
                    _div_plot["Dividendos"], errors="coerce")
                _div_plot = _div_plot.dropna()
                if not _div_plot.empty:
                    _sec_hdr("💰 Dividendos por ação")
                    fig_dv = px.bar(_div_plot, x="Data", y="Dividendos",
                                    color_discrete_sequence=[_COR_ALT])
                    fig_dv.update_layout(**_plot_layout(240))
                    fig_dv.update_yaxes(title="R$/ação (soma anual)")
                    st.plotly_chart(fig_dv, use_container_width=True,
                                    config={"displayModeBar": False},
                                    key=f"b3_dre_divps_{tk}")

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

        # Fluxo de Caixa (condicional) — exige df não-vazio e coluna 'Data'
        # (df vazio traz as colunas FC + 'data' minúscula sem renomear → quebraria
        # o select df_fin[["Data"] + fco_cols] abaixo).
        fco_cols = [c for c in ("FCO", "FCI", "FCF", "Fluxo_Caixa_Operacional",
                                 "Fluxo_Caixa_Investimento", "Fluxo_Caixa_Livre")
                    if c in df_fin.columns] if (not df_fin.empty and "Data" in df_fin.columns) else []
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

    _sec_hdr("🏆 Score e critérios de avaliação")
    _render_b3_score_dashboard(tk, mult, df_set)


# ══════════════════════════════════════════════════════════════════════════════
# TAB 3 — Análise Avançada
# ══════════════════════════════════════════════════════════════════════════════

def _render_saude_do_ranking(df_mult: pd.DataFrame, df_scored: pd.DataFrame,
                             top_n: int = 20) -> None:
    """Piso de qualidade sobre as PRIMEIRAS do ranking desta aba.

    Por que existe. A Criação de Portfólio é extensão desta tela e já reprova
    pelo piso absoluto; aqui o ranking ordenava por score e nada dizia que a
    líder tinha patrimônio negativo ou queimava caixa. É o mesmo padrão de
    "motor de diagnóstico sem porta de entrada" que originou o piso — uma aba
    ao lado. Medido em 01/08/2026: **117 das 427 empresas do universo (27%)**
    são CRÍTICAS pelo piso e podiam liderar este ranking sem qualquer sinal.

    Aqui o piso NÃO remove ninguém, e isso é deliberado: esta é a tela de
    análise, onde ver a empresa ruim é parte do trabalho. Quem decide alocação
    é a Criação de Portfólio, e lá ele reprova de fato.
    """
    from core.b3_holdings_health import ATENCAO, CRITICO, check_holdings

    if df_scored is None or df_scored.empty or df_mult is None or df_mult.empty:
        return
    alvos = [str(t).upper() for t in df_scored["Ticker"].head(top_n).tolist()]
    if not alvos:
        return
    try:
        saude = check_holdings(df_mult, alvos)
    except Exception:
        return

    criticos = [h for h in saude if h.nivel == CRITICO]
    atencao = [h for h in saude if h.nivel == ATENCAO]
    if not criticos and not atencao:
        return

    _sec_hdr("🩺 Piso de qualidade sobre as primeiras do ranking")
    c1, c2, c3 = st.columns(3)
    with c1:
        card_metrica(f"Avaliadas (top {len(alvos)})", len(alvos))
    with c2:
        card_metrica("Críticas", len(criticos),
                     positivo=(False if criticos else None))
    with c3:
        card_metrica("Atenção", len(atencao))

    st.caption(
        "Score mede atratividade relativa; isto mede se a empresa se sustenta. "
        "São perguntas diferentes, e uma empresa pode liderar a primeira e "
        "falhar a segunda. Nada é removido desta tela — na **Criação de "
        "Portfólio** o mesmo piso reprova e substitui no próprio segmento."
    )
    for h in criticos:
        st.error(f"**{h.ticker}** — {h.resumo}", icon="🚨")
    if atencao:
        with st.expander(f"Em atenção ({len(atencao)})", expanded=False):
            for h in atencao:
                st.warning(f"**{h.ticker}** — {h.resumo}", icon="⚠️")


def _render_calibracao_segmento(calib) -> None:
    """Painel da calibração ótima automática do segmento (aba Análise Avançada)."""
    from core.segment_calibration import pesos_rows, criterios_rows

    alvo = (f"segmento **{calib.segmento}**" if calib.segmento and calib.segmento != "Todos"
            else f"setor **{calib.setor}**")
    with st.expander(
        f"🎯 Calibração ótima automática · {calib.arquetipo_label}", expanded=True
    ):
        st.caption(
            f"O App4 carregou automaticamente a calibração ótima para o {alvo}, "
            f"classificado como **{calib.arquetipo_label}**. {calib.descricao} "
            "Você pode aceitar esta calibração (recomendado) ou testar ajustes "
            "manuais no expander **⚖️ Pesos do Scoring**."
        )

        b1, b2, b3, b4 = st.columns(4)
        _trad_debt = {"ignorar": "não se aplica", "tolerante": "tolerante",
                      "normal": "padrão", "rigoroso": "rigoroso"}
        with b1:
            card_metrica("Tratamento de dívida", _trad_debt.get(calib.debt_treatment, "—"))
        with b2:
            card_metrica("Dividendos", calib.dy_relevancia.capitalize())
        with b3:
            card_metrica("Foco de valuation", calib.valuation_foco.capitalize())
        with b4:
            card_metrica("Liquidez corrente",
                         "relevante" if calib.liquidez_relevante else "não se aplica")

        st.markdown("**✅ Pesos dos indicadores — aplicados ao score**")
        st.dataframe(
            pd.DataFrame(pesos_rows(calib)),
            hide_index=True, use_container_width=True,
            column_config={"Peso %": st.column_config.NumberColumn(format="%.1f%%")},
        )

        st.markdown("**📋 Critérios do segmento — perfil recomendado**")
        st.dataframe(
            pd.DataFrame(criterios_rows(calib)),
            hide_index=True, use_container_width=True,
        )

        ap = calib.aprovacao
        lr = calib.limites_risco
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            card_metrica("Score de entrada mín.", f"{ap['score_entrada_min']:.0f}",
                         accent="#00C896")
        with c2:
            card_metrica("Penalidade de risco máx.", f"{ap['risk_penalty_max']:.0f}",
                         accent="#FC5C7D")
        with c3:
            card_metrica("Teto de endividamento",
                         f"{lr['endividamento_teto']:.2f}" if lr.get("endividamento_teto") else "N/A",
                         accent="#F6C90E")
        with c4:
            card_metrica("Winsorização",
                         f"{calib.winsor[0]*100:.0f}–{calib.winsor[1]*100:.0f}%", accent="#4A9EFF")

        st.caption(
            "ℹ️ Os **pesos** acima são aplicados diretamente ao ranking deste "
            "segmento. Os **critérios, limites de risco e winsorização** compõem "
            "o perfil recomendado do segmento (normalização: "
            f"{calib.normalizacao.lower()}). Fonte: {calib.fonte}."
        )


def _tab_avancada(df_set: pd.DataFrame) -> None:
    if df_set.empty:
        st.warning("Banco não configurado. Configure `SUPABASE_DB_URL_B3`.")
        return

    st.markdown(
        '<div style="background:rgba(56,189,248,.06);border-left:3px solid #38BDF8;'
        'border-radius:6px;padding:12px 16px;margin-bottom:12px;font-size:0.84rem;'
        'color:#CBD5E1;">'
        '<strong>🔬 Etapa 1 de 3 · Banco de testes por segmento.</strong> '
        'Escolha <strong>um segmento</strong> e valide de perto como cada filtro, '
        'indicador, cálculo e score se comporta dentro dele — identifique outliers, '
        'confira a matemática e calibre a metodologia <em>antes</em> de aplicá-la em '
        'escala. As etapas seguintes são <strong>Criação de Portfólio</strong> '
        '(aplica em todos os segmentos) e <strong>Avaliação de Portfólio</strong> '
        '(julga a carteira como conjunto).'
        '</div>',
        unsafe_allow_html=True,
    )

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
    _fonte_pit = ""
    try:
        if _db.market_active():
            # Fix auditoria 2026-07: com fonte market, load_multiplos_todos
            # (ano_ref_max) agora serve métricas do exercício ANUAL fechado
            # — antes servia o snapshot TTM de hoje rotulado com o ano do
            # último balanço, contradizendo a frase abaixo.
            _fonte_pit = (" Fonte market.*: métricas anuais fechadas. A data "
                          "de publicação histórica ainda é aproximada; períodos "
                          "anteriores ao versionamento não são point-in-time.")
    except Exception:
        pass
    st.caption(
        f"📅 **Ano-base do score: {_ano_label}** — a seleção do ano atual "
        f"({_ano_corrente}) usa os fundamentos consolidados do ano anterior. "
        "Dados parciais do ano corrente são ignorados por metodologia."
        + _fonte_pit
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
        # index=1 (R$ 500 mil), não 2: o mesmo piso do perfil recomendado da
        # Criação de Portfólio, que é extensão desta aba. Duas telas com pisos
        # diferentes davam universos diferentes para a MESMA pergunta.
        index=1, key="b3_av_liq_min",
        help="Exclui ações com baixo volume diário negociado, onde o spread "
             "encarece a entrada/saída. Mediana do volume financeiro diário "
             "do último ano, do armazém local — a MESMA medida que a Criação "
             "de Portfólio usa, para as duas abas não discordarem sobre a "
             "mesma empresa.",
    )
    liq_min_rs = liq_opts[sel_liq]

    with st.expander("⚖️ Pesos do Scoring"):
        usar_pesos_setor = st.checkbox(
            "Usar pesos calibrados por setor (recomendado)",
            value=True, key="b3_av_use_setor_w",
            disabled=sel_set == "Todos",
        )
        if sel_set == "Todos":
            usar_pesos_setor = False
            st.caption(
                "Com **Todos os setores**, o score usa pesos genéricos para "
                "não aplicar o perfil de um setor aos demais. Selecione um "
                "setor para habilitar pesos setoriais calibrados."
            )
        usar_auto_calib = st.checkbox(
            "Calibração automática ótima por segmento (recomendado)",
            value=True, key="b3_av_use_autocalib",
            disabled=not usar_pesos_setor,
            help="Carrega automaticamente a calibração ótima do segmento "
                 "selecionado (arquétipo financeiro + distribuição real): "
                 "ajusta pesos, tratamento de dívida/liquidez/dividendos e "
                 "critérios ao tipo de negócio. Desmarque para usar os pesos "
                 "genéricos do setor.",
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
        cheapness_pct_av = st.slider(
            "Peso de barganha no score (%)",
            0, 50, 0, 5,
            key="b3_av_cheapness",
            help="Mistura múltiplos de preço (P/L, P/VP, EV/EBIT: menor = melhor) "
                 "ao score de qualidade. 0% = só qualidade (padrão). Ex.: 30% dá "
                 "peso a 'estar barato' — captura a tese de comprar boas empresas "
                 "na baixa. Vale tanto para pesos do setor quanto manuais.",
        )
    cheapness_weight_av = float(cheapness_pct_av) / 100.0

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

    # Filtro de liquidez de negociação — exclui ações ilíquidas onde o spread
    # encarece a operação (proteção ao investidor iniciante).
    if liq_min_rs > 0 and tks_uni:
        # Armazém local, não yfinance. Três motivos, todos medidos em
        # 01/08/2026: (1) a Criação de Portfólio já usa esta fonte, e duas telas
        # com fontes distintas discordavam sobre a mesma empresa; (2) é offline
        # — a versão anterior derrubava a aba INTEIRA quando a rede falhava, e
        # "nenhum ativo aprovado" é o pior modo de falha possível para uma tela
        # de análise; (3) as duas fontes concordam de fato: razão mediana
        # armazém/yfinance de 1,02 em 14 tickers, 12 deles dentro de ±26%.
        #
        # A janela do armazém é maior (~12 meses contra 3 do yfinance) e por
        # isso mais conservadora: liquidez que apareceu num trimestre de alta
        # não vira permanente. EUCA4 deu R$ 698 mil aqui contra R$ 1,94 mi no
        # yfinance justamente porque subiu 43% em 2026 — a pergunta que importa
        # é se dá para sair num mês ruim, não no melhor trimestre.
        try:
            liq_map = _db.load_giro_diario()
        except Exception:
            liq_map = {}
        if not liq_map:
            # Sem a leitura, NÃO filtra. O bloco de exclusão abaixo removeria
            # todos os tickers (get() devolve None para cada um) — trocar um
            # modo de falha por outro pior.
            st.warning(
                "⚠️ Filtro de liquidez **não aplicado**: não foi possível ler o "
                "volume negociado. A análise segue com o universo completo — "
                "trate os nomes como não verificados quanto à negociabilidade."
            )
        else:
            _antes_liq = len(tks_uni)
            _sem_liq = [tk for tk in tks_uni if liq_map.get(tk) is None]
            tks_uni = [
                tk for tk in tks_uni
                if liq_map.get(tk) is not None and liq_map[tk] >= liq_min_rs
            ]
            _n_iliq = _antes_liq - len(tks_uni)
            if _n_iliq:
                st.caption(
                    f"💧 {_n_iliq} ação(ões) abaixo do mínimo ou sem liquidez "
                    f"verificável foram excluídas ({len(_sem_liq)} sem dado)."
                )

    if not tks_uni:
        st.info("Nenhuma empresa encontrada com os filtros selecionados.")
        return

    # O ranking final usa todo o universo filtrado. O antigo teto de 40
    # empresas alterava os percentis antes do score definitivo.

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
    # Cutover: o patch por scraping (Fundamentus) só faz sentido sobre o legado;
    # com market.* (fonte limpa) não se reconcilia por web — vazio fica vazio.
    if _db.market_active():
        audit_fallback = pd.DataFrame()
    else:
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

    # ── Transparência + saneamento de dados (qualidade antes do ranking) ──────
    try:
        from views.data_quality_panel import render_quality_report, render_healing_panel
        with st.expander("🩺 Qualidade & saneamento dos dados (antes do ranking)",
                         expanded=False):
            _df_qa = df_mult_enrich[df_mult_enrich["Ticker"].isin(tks_uni)] \
                if "Ticker" in df_mult_enrich.columns else df_mult_enrich
            render_quality_report(_df_qa, key_prefix="av_dq")
            st.markdown("---")
            render_healing_panel(list(tks_uni), key_prefix="av_heal")
    except Exception as _exc_qa:  # nunca quebra a aba
        st.caption(f"Painel de qualidade indisponível: {_exc_qa}")

    # Pesos por setor ou manuais
    setor_counts = (
        df_filt["SETOR"].dropna().astype(str).str.strip().value_counts()
        if "SETOR" in df_filt.columns and not df_filt.empty else pd.Series(dtype=int)
    )
    setor_prevalente = str(setor_counts.idxmax()) if not setor_counts.empty else ""
    setor_referencia = setor_prevalente if sel_set != "Todos" else ""

    # Calibração ótima automática por segmento (laboratório de calibração).
    # Pesos manuais > calibração automática > pesos genéricos do setor.
    seg_calib = None
    if pesos_usuario_raw is not None:
        pesos_v2 = _get_pesos_setor(setor_referencia, pesos_usuario_raw)
    elif usar_auto_calib:
        try:
            from core.segment_calibration import build_segment_calibration
            _base_pesos = _get_pesos_setor(setor_referencia)
            _df_seg = df_mult_enrich[df_mult_enrich["Ticker"].isin(tks_uni)]
            seg_calib = build_segment_calibration(
                setor_referencia,
                sel_seg if sel_seg != "Todos" else "",
                _base_pesos,
                df_segment=_df_seg,
            )
            pesos_v2 = seg_calib.pesos
        except Exception:
            pesos_v2 = _get_pesos_setor(setor_referencia)
    else:
        pesos_v2 = _get_pesos_setor(setor_referencia)

    if seg_calib is not None:
        _render_calibracao_segmento(seg_calib)

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

    # ── Gate de completude mínima (fix auditoria 2026-07) ───────────────────
    # Antes, empresa com quase nenhum indicador disponível era ranqueada
    # mesmo assim (ausente vira percentil neutro 0,5) e podia ocupar boa
    # posição sem dados que a sustentem. Agora: completude ponderada pelos
    # pesos do score (ignorando slopes, que medem tendência) precisa ser
    # ≥ 60%; excluídas aparecem em lista visível abaixo do ranking.
    _COMPLETUDE_MIN = 0.60
    _inds_comp = [c for c in pesos_v2
                  if c in df_mult_enrich.columns and not c.endswith("_slope_log")]
    excluidas_dados: list[dict] = []
    if _inds_comp and not df_mult_enrich.empty and tks_uni:
        _w_tot = sum(pesos_v2[c][0] for c in _inds_comp) or 1.0
        _comp_map: dict[str, float] = {}
        for _tk_c in tks_uni:
            _sub_c = df_mult_enrich[df_mult_enrich["Ticker"] == _tk_c]
            if _sub_c.empty:
                _comp_map[_tk_c] = 0.0
                continue
            _row_c = _sub_c.iloc[-1]
            _comp_map[_tk_c] = sum(
                pesos_v2[c][0] for c in _inds_comp if pd.notna(_row_c.get(c))
            ) / _w_tot
        excluidas_dados = sorted(
            [{"Ticker": t, "Completude (%)": round(_comp_map.get(t, 0.0) * 100)}
             for t in tks_uni if _comp_map.get(t, 0.0) < _COMPLETUDE_MIN],
            key=lambda d: d["Completude (%)"],
        )
        _excl_comp = {d["Ticker"] for d in excluidas_dados}
        if _excl_comp:
            tks_uni = [t for t in tks_uni if t not in _excl_comp]
    if not tks_uni:
        st.warning(
            "Nenhuma empresa do filtro atende à completude mínima de dados "
            f"({_COMPLETUDE_MIN:.0%} dos indicadores do score). Ranking não "
            "gerado — ranquear sem dados criaria posições sem sustentação."
        )
        return

    # Scoring v2 — coluna de grupo: mais granular que o filtro atual
    group_prefer = (
        "SUBSETOR" if sel_seg != "Todos" else
        "SEGMENTO" if sel_sub != "Todos" else
        "SEGMENTO"
    )
    # Barganha (cheapness): mistura múltiplos de preço ao score de qualidade —
    # mesma lógica da Criação de Portfólio. 0% preserva o comportamento atual.
    pesos_v2 = _aplicar_cheapness(pesos_v2, cheapness_weight_av)
    df_scored = _score_universo(
        df_mult_enrich, tks_uni, pesos_v2,
        df_hist_batch=hist_batch,
        group_col_prefer=group_prefer,
        macro_context=_macro_for_year(macro_history),
    )
    if excluidas_dados:
        st.caption(
            f"🧪 **{len(excluidas_dados)} empresa(s) fora do ranking por dados "
            f"insuficientes** (completude ponderada < {_COMPLETUDE_MIN:.0%} dos "
            "indicadores do score). Detalhe no expander abaixo."
        )
        with st.expander("Empresas excluídas por completude de dados"):
            st.caption(
                "Ranquear com indicador ausente atribui percentil neutro 0,5 — "
                "aceitável para UMA lacuna pontual, enganoso quando a maioria "
                "dos indicadores falta. Estas empresas voltam ao ranking "
                "quando a ingestão cobrir seus fundamentos."
            )
            st.dataframe(pd.DataFrame(excluidas_dados),
                         use_container_width=True, hide_index=True,
                         height=min(300, 45 + 32 * len(excluidas_dados)))
    _render_saude_do_ranking(df_mult_enrich, df_scored)

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
                with fm_cols[0]:
                    card_metrica("Anos usados", int(fm_result.n_years), accent="#4A9EFF")
                with fm_cols[1]:
                    card_metrica("Tickers", int(fm_result.n_tickers), accent="#4A9EFF")
                with fm_cols[2]:
                    card_metrica("Obs.", int(fm_result.n_obs), accent="#4A9EFF")
                with fm_cols[3]:
                    card_metrica("Mistura", f"{fm_alpha:.0%}", accent="#B084F6")
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
        if _db.market_active():
            st.caption("Auditoria cross-source (Fundamentus) desativada: fonte única "
                       "brapi (market.*), monitorada pelos controles internos do pipeline.")
            run_cross = False
        else:
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
                with mc1:
                    card_metrica("Críticas", n_crit, accent="#FC5C7D")
                with mc2:
                    card_metrica("Alertas", n_warn, accent="#F6C90E")
                with mc3:
                    card_metrica("OK", n_ok, accent="#00C896")
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
                with hc1:
                    card_metrica("Linhas", int(hist_summary["total"]), accent="#4A9EFF")
                with hc2:
                    card_metrica("Críticas", int(hist_summary["critical"]), accent="#FC5C7D")
                with hc3:
                    card_metrica("Alertas", int(hist_summary["warn"]), accent="#F6C90E")
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
                "Top N", options=[5, 8, 10], value=5,
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
                    "Ledoit-Wolf: shrinkage com intensidade em forma fechada "
                    "(LW 2004; decresce com mais observações). "
                    "Fama-French: Σ estruturada fatorial M1 banca (Σ=BΣ_FB'+D). "
                    "DCC-GARCH: covariância condicional dinâmica M2 banca (Engle 2002) "
                    "— atenção: com ~36 obs mensais os parâmetros são pouco "
                    "identificados (grid search); use como indicação, não medida."
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
                if len(top_tks) < top_n_mk:
                    st.warning(
                        f"A restrição setorial permite somente {len(top_tks)} "
                        f"dos {top_n_mk} ativos pedidos; o limite foi preservado."
                    )
            else:
                top_tks = _ranked[:top_n_mk]
            from core.portfolio_constraints import minimum_assets_for_cap
            _min_cap = minimum_assets_for_cap(cap_mk)
            if len(top_tks) < _min_cap:
                st.warning(
                    f"Cap de {cap_mk:.0%} exige ao menos {_min_cap} ativos, "
                    f"mas as restrições deixaram {len(top_tks)}. Otimização não executada."
                )
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
                            w_score_norm, mk, alpha=alpha_mk, cap=cap_mk
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
                    card_metrica("🟢 Com desconto", f"{_n_desc} cias", accent="#00C896",
                                 ajuda="Graham/Bazin indicam preço abaixo do justo")
                with _v2:
                    card_metrica("🔴 Caras", f"{_n_caro} cias", accent="#FC5C7D",
                                 ajuda="Negociando acima do valor justo estimado")
                with _v3:
                    card_metrica("💧 Dividendo sustentável", f"{_n_sust} cias", accent="#4A9EFF",
                                 ajuda="Payout saudável + FCO positivo")
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
                    card_metrica("🔴 Risco de balanço", f"{_n_risk} cias", accent="#FC5C7D",
                                 ajuda="Penalidade ≥ 20% — endividamento crítico + margem negativa")
                with _kr2:
                    card_metrica("🟡 Em alerta", f"{_n_alert} cias", accent="#F6C90E",
                                 ajuda="Penalidade 8–20% — balanço frágil mas não crítico")
                with _kr3:
                    card_metrica("📈 Com bônus líquido", f"{_n_bonus} cias", accent="#00C896",
                                 ajuda="Empresas que se beneficiaram dos ajustes A ou C")
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
            "Combine um prior UNIFORME (o mesmo retorno esperado para todos "
            "os ativos, definido por você abaixo) com SUAS views via "
            "formalismo Bayesiano. Nota de honestidade (auditoria 2026-07): "
            "a reverse optimization dos pesos de mercado (prior de "
            "equilíbrio CAPM/IBOV) ainda NÃO está implementada — sem "
            "estrutura cross-sectional no prior, o posterior reflete apenas "
            "a mecânica da sua view. Cada view tem confidence em (0, 1]: "
            "alta = sobrepõe ao prior; baixa = prior domina. Views: "
            "absoluta ('PETR3 vai render X%') ou relativa ('PETR3 vai "
            "render X pp acima de VALE3')."
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
                        "Prior uniforme (% a.a.)", 0.0, 50.0, 10.0, 0.5,
                        key="b3_bl_prior", disabled=not run_bl,
                        help="Retorno esperado assumido IGUAL para todos os "
                             "ativos (não é o prior de equilíbrio CAPM — "
                             "reverse optimization pendente).",
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
                            _cap_bl_options = [
                                value for value in [0.20, 0.25, 0.30, 0.40, 0.50]
                                if len(top_bl) * value >= 1.0 - 1e-12
                            ]
                            cap_bl = st.select_slider(
                                "Cap por ativo",
                                options=_cap_bl_options,
                                value=(0.40 if 0.40 in _cap_bl_options
                                       else _cap_bl_options[0]),
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
                        # Fix auditoria 2026-07: rets_hist são retornos
                        # MENSAIS (_batch_yf_precos_mensais, interval=1mo);
                        # periods_per_year=252 anualizava como se fossem
                        # diários e inflava a vol exibida ~4,6x (√21) e
                        # deflacionava o Sharpe na mesma proporção.
                        opt = apply_bl_to_markowitz(
                            top_bl, prior, rets_hist, [view],
                            tau=tau_bl, cap=cap_bl, risk_aversion=ra_bl,
                            periods_per_year=12,
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
        if _db.market_active():
            st.caption("Desativada: fonte única brapi (market.*) — não há scraping "
                       "Fundamentus/Status Invest para cruzar.")
            run_a6 = False
        elif df_scored.empty:
            st.info("Sem empresas scoradas para validar.")
            run_a6 = False
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
                 .replace("risk_probability", "Índice heurístico de risco")
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
                    "Índice heurístico de risco": st.column_config.NumberColumn(
                        "Índice heurístico de risco", format="%.1f"
                    ),
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
    top_n    = bk2.selectbox("Top-N Estratégia", [5, 10], index=0, key="b3_av_topn")
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

    # Auto-calibração γ / cap / soft — walk-forward purged (auditoria 2026-07:
    # antes usava grid search numa janela única in-sample, o MESMO período
    # depois exibido no backtest, e sem custos; a versão walk-forward existia
    # mas nunca era chamada pela UI).
    with st.expander("🔧 Auto-calibração de parâmetros (γ, cap, soft)"):
        st.caption(
            "Nested walk-forward: cada fold escolhe γ/cap/soft somente no "
            "histórico anterior, aplica purge de 3m + embargo de 2m e mede a "
            "escolha no bloco futuro. O último fold fica intocado para "
            "auditoria final e não escolhe os parâmetros. Custos de turnover "
            "entram no objetivo. Na fonte legada, as datas contábeis anteriores "
            "ao corte PIT continuam aproximadas e o resultado é rotulado assim."
        )
        if st.button("⚙️ Calibrar agora", key="b3_av_btn_cal"):
            from core.transaction_costs import CostConfig as _CalCost
            with st.spinner("Calibrando parâmetros (walk-forward purged)…"):
                per_cal    = per_opts.get(sel_per, "3y")
                tks_cal    = tuple(sorted(tks_uni))
                df_prec_cal = _batch_yf_precos_mensais(tks_cal, period=per_cal)
                g_cal, c_cal, s_cal = _calibrate_walk_forward(
                    df_prec_cal, df_scored, tks_uni,
                    float(taxa_sel) / 100.0, float(aporte),
                    n_folds=4, min_window=18,
                    purge_months=3, embargo_months=2,
                    cost_cfg=_CalCost.brasil_pf_default(),
                    df_hist_batch=hist_batch,
                    pesos=pesos_v2,
                    tk_grupos=tk_grupos,
                    macro_by_year=macro_history or None,
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
        "Descontar custos de compra (corretagem + meia-spread) — recomendado",
        value=True, key="b3_av_custos",
        help="Ativado: desconta o custo de COMPRA de cada aporte (meia-spread "
             "bid-ask + corretagem). No modo 'sem vendas' o rebalance apenas "
             "redireciona os aportes novos (IR de venda não se aplica); no "
             "modo 'com vendas' há também custos de venda e IR 15%. "
             "Desativado: backtest 'ideal' sem custos — superestima o retorno.",
    )

    # Modo de rebalance (auditoria 2026-07): o rebalance acontece em ABRIL —
    # balanços FY N−1 são publicados até 31/03 (CVM); janeiro antecipava
    # 2-4 meses de informação contábil.
    modo_rebal = st.radio(
        "Modo de rebalanceamento anual (abril)",
        ["Sem vendas — redireciona apenas os aportes novos (legado)",
         "Com vendas — realinha pesos vendendo excedentes (custos de venda + IR 15%)"],
        index=0, key="b3_av_modo_rebal",
        help="No modo com vendas, o IR de 15% incide sobre o lucro realizado "
             "nas vendas do rebalance, respeitando a isenção mensal PF de "
             "R$ 20 mil em vendas (IN RFB 1.585/2015).",
    )
    _com_vendas = modo_rebal.startswith("Com vendas")
    _score_fingerprint = (
        df_scored[[c for c in ("Ticker", "score", "score_version")
                   if c in df_scored.columns]]
        .sort_values("Ticker")
        .round(8)
        .to_dict("records")
        if not df_scored.empty else []
    )
    _history_fingerprint = {
        ticker: {
            "last_data": str(pd.to_datetime(frame.get("Data"), errors="coerce").max()),
            "last_available": str(
                pd.to_datetime(frame.get("AvailableAt"), errors="coerce", utc=True).max()
            ) if "AvailableAt" in frame else "",
            "rows": len(frame),
        }
        for ticker, frame in (hist_batch or {}).items()
        if frame is not None and not frame.empty
    }
    _bt_signature = _stable_signature({
        "score_version": SCORE_VERSION,
        "tickers": sorted(tks_uni),
        "weights": pesos_v2,
        "aporte": float(aporte),
        "top_n": int(top_n),
        "period": sel_per,
        "selic": float(taxa_sel),
        "costs": bool(aplicar_custos),
        "sales": bool(_com_vendas),
        "gamma": st.session_state.get("b3_av_gamma", _GAMMA_DEF),
        "cap": st.session_state.get("b3_av_cap", _CAP_DEF),
        "soft": st.session_state.get("b3_av_soft", _SOFT_DEF),
        "score_data": _score_fingerprint,
        "history_data": _history_fingerprint,
    })

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

        with st.spinner("Carregando preços mensais ajustados…"):
            df_prec_m = _batch_yf_precos_mensais(tks_tuple, period=period_code)

        # adjusted_close já é RETORNO TOTAL (proventos+splits reinvestidos) → NÃO
        # reinvestir dividendos de novo (evita dupla contagem). Por isso dividendos=None.
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
            dividendos=None,
            cost_cfg=_cost_cfg,
            rebalance_com_vendas=_com_vendas,
        )

        # Benchmark de MERCADO real (auditoria 2026-07): mesmo fluxo de
        # aportes aplicado ao IBOV — o "Benchmark" equal-weight é interno
        # ao universo filtrado e não representa "bater o mercado".
        ibov_px = _yf_ibov_mensal(period_code)
        if not df_bt.empty and not ibov_px.empty:
            _px_map = {(d.year, d.month): float(v)
                       for d, v in ibov_px.items() if pd.notna(v) and v > 0}
            # aporte de mês sem preço não some: fica em caixa pendente e é
            # investido no próximo mês com cotação disponível
            _cotas_i, _pend_i, _last_px = 0.0, 0.0, None
            _serie_i: list[float] = []
            for _d in pd.to_datetime(df_bt["Data"]):
                _px = _px_map.get((_d.year, _d.month))
                _pend_i += float(aporte)
                if _px:
                    # mesma fricção de compra dos demais (fix revisão 2026-07:
                    # sem isto o IBOV comprava a preço cheio e ganhava
                    # vantagem estrutural quando custos estavam ativados);
                    # proxy: spread de large cap (ETF de índice é líquido).
                    _fee_i = 0.0
                    if _cost_cfg.ativo:
                        _fee_i = (_cost_cfg.corretagem_fixa +
                                  _pend_i * (_cost_cfg.spread_bps_large / 2.0 / 10_000.0))
                    _cotas_i += max(_pend_i - _fee_i, 0.0) / _px
                    _pend_i = 0.0
                    _last_px = _px
                _serie_i.append(_cotas_i * (_px or _last_px or 0.0) + _pend_i)
            df_bt["IBOV (aportes)"] = _serie_i

        st.session_state["b3_av_bt_df"]    = df_bt
        st.session_state["b3_av_bt_custos"] = aplicar_custos
        st.session_state["b3_av_bt_top"]   = tickers_top
        st.session_state["b3_av_bt_aport"] = float(aporte)
        st.session_state["b3_av_bt_n"]     = n_efetivo
        st.session_state["b3_av_bt_gamma"] = usar_gamma
        st.session_state["b3_av_bt_signature"] = _bt_signature

    df_bt      = st.session_state.get("b3_av_bt_df",    pd.DataFrame())
    tks_top    = st.session_state.get("b3_av_bt_top",   [])
    aport_bt   = st.session_state.get("b3_av_bt_aport", 0.0)
    n_efetivo  = st.session_state.get("b3_av_bt_n",     0)
    bt_gamma   = st.session_state.get("b3_av_bt_gamma", False)
    if st.session_state.get("b3_av_bt_signature") != _bt_signature:
        df_bt = pd.DataFrame()
        tks_top = []

    if not df_bt.empty:
        # Exibição: "Benchmark" (equal-weight interno) → "Pesos Iguais".
        df_bt_disp = df_bt.rename(columns={"Benchmark": "Pesos Iguais"})
        colunas_bt = [c for c in ("Estratégia", "Pesos Iguais", "IBOV (aportes)",
                                  "Tesouro Selic")
                      if c in df_bt_disp.columns]
        melt_bt = df_bt_disp.melt("Data", value_vars=colunas_bt,
                              var_name="Carteira", value_name="Patrimônio (R$)")
        fig_bt = px.line(
            melt_bt, x="Data", y="Patrimônio (R$)", color="Carteira",
            color_discrete_map={
                "Estratégia":       _COR_POS,
                "Pesos Iguais":     _COR_NEU,
                "IBOV (aportes)":   "#E67E22",
                "Tesouro Selic":    _COR_ALT,
            },
        )
        st.caption(
            "🎯 **Como interpretar:** a linha **Pesos Iguais** (carteira que "
            "compra todas as empresas do universo em partes iguais) é a "
            "referência de **habilidade** — superá-la mostra que a *seleção* "
            "agregou valor, e isso é **neutro ao cenário macro** (se o mercado "
            "caiu, os Pesos Iguais caíram junto). A linha **Tesouro Selic** é "
            "**diagnóstico de timing** (vale estar em ações agora?), não critério "
            "de qualidade da seleção."
        )
        fig_bt.update_traces(line_width=2.0)
        fig_bt.update_layout(**_plot_layout(380))
        st.plotly_chart(fig_bt, use_container_width=True,
                        config={"displayModeBar": False}, key="b3_av_bt_chart")

        _custos_on     = st.session_state.get("b3_av_bt_custos", True)
        _bt_com_vendas = bool(df_bt.attrs.get("com_vendas"))
        if _custos_on and _bt_com_vendas:
            st.caption(
                "✅ Backtest com **rebalance com vendas**: custos de compra em "
                "cada aporte + custos de venda e **IR 15% sobre o ganho "
                "líquido MENSAL** nos rebalances (isenção binária de R$ 20 "
                "mil/mês em vendas + compensação de prejuízos do mês, IN RFB "
                f"1.585/2015). IR pago na simulação: "
                f"**R$ {float(df_bt.attrs.get('ir_pago', 0.0)):,.2f}** · "
                f"custos de rebalance: "
                f"**R$ {float(df_bt.attrs.get('custos_rebal', 0.0)):,.2f}**. "
                "Nota fiscal: a série ajustada mistura valorização e "
                "distribuições. Desde 2026, dividendos podem integrar a "
                "tributação mínima de altas rendas. O valor é uma estimativa "
                "simplificada, não uma apuração fiscal individual."
            )
        elif _custos_on:
            st.caption(
                "✅ Backtest com **custos de compra descontados** (meia-spread "
                "bid-ask + corretagem em cada aporte, na estratégia E no "
                "benchmark). Neste modo o rebalance anual não vende posições — "
                "só redireciona aportes futuros —, logo **IR de venda não é "
                "modelado**. Use o modo 'com vendas' para simular IR e custos "
                "de venda."
            )
        else:
            st.caption(
                "⚠️ Backtest **sem custos** (ideal/teórico) — superestima o "
                "retorno real. Marque a opção de custos para projeção realista."
            )
        _restricoes_bt = df_bt.attrs.get("restricoes_inviaveis") or []
        if _restricoes_bt:
            st.warning(
                "Alguns anos não tinham ativos suficientes para o cap "
                "solicitado. O backtest usou equal-weight nesses anos: "
                + "; ".join(_restricoes_bt[:8])
            )
        _caixa_pendente = float(
            df_bt.attrs.get("caixa_pendente_estrategia", 0.0) or 0.0
        )
        if _caixa_pendente > 0:
            st.caption(
                f"Caixa pendente por falta de cotação no fim da janela: "
                f"**R$ {_caixa_pendente:,.2f}** — preservado no patrimônio."
            )
        st.caption(
            "📅 **Rebalance anual em abril** — os balanços do exercício "
            "anterior são publicados até 31/03 (CVM); rebalancear em janeiro "
            "anteciparia informação ainda não pública (auditoria 2026-07). "
            "Linha **IBOV (aportes)**: o mesmo fluxo de aportes aplicado ao "
            "índice — referência de mercado real; a linha **Pesos Iguais** "
            "(carteira igualitária) é interna ao universo filtrado."
        )

        # Auditoria 2026-07: transparência dos dois vieses estruturais da
        # simulação — sobrevivência (universo só de listadas hoje) e corte
        # de início por indisponibilidade de score histórico.
        try:
            from core.survivorship import flag_survivorship_universe
            _dt_ini_bt = pd.to_datetime(df_bt["Data"].iloc[0]).date()
            _sv = flag_survivorship_universe(list(tks_uni), data_ref=_dt_ini_bt)
            if _sv["n_delisted"] > 0:
                st.caption(
                    f"⚠️ **Viés de sobrevivência:** o universo simulado só contém "
                    f"empresas listadas hoje — {_sv['n_delisted']} empresa(s) da "
                    f"lista curada de deslistadas estavam vivas em "
                    f"{_dt_ini_bt.year} e não entram na simulação. A lista é "
                    "incompleta; por isso não é exibida uma falsa estimativa "
                    "pontual de cobertura ou de bps de viés."
                )
        except Exception:
            pass
        _anos_ss = df_bt.attrs.get("anos_sem_score") or []
        if _anos_ss:
            st.caption(
                f"ℹ️ **Início efetivo da simulação ajustado:** {len(_anos_ss)} "
                f"ano(s) sem histórico contábil suficiente para score "
                f"({', '.join(str(a) for a in _anos_ss)}) foram excluídos de "
                "TODAS as séries (estratégia, benchmark e Selic) — sem "
                "fallback para scores atuais, sem look-ahead."
            )

        # KPI patrimônio final
        _sec_hdr("💰 Patrimônio Final")
        ultima      = df_bt.iloc[-1]
        total_aport = aport_bt * len(df_bt) if aport_bt > 0 else 1.0

        _kpi_series = [("Estratégia", _COR_POS), ("Benchmark", _COR_NEU)]
        if "IBOV (aportes)" in df_bt.columns:
            _kpi_series.append(("IBOV (aportes)", "#E67E22"))
        _kpi_series.append(("Tesouro Selic", _COR_ALT))
        kpi_bt = st.columns(len(_kpi_series), gap="small")
        for idx, (nome_c, cor_c) in enumerate(_kpi_series):
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

    # ── VALIDAÇÃO PREDITIVA DO SCORE (auditoria 2026-07, pendência #4) ───────
    with st.expander("🔬 Poder preditivo do score (Rank-IC) — por ano"):
        _ic_signature = _stable_signature({
            "score_version": SCORE_VERSION,
            "tickers": sorted(tks_uni),
            "weights": pesos_v2,
            "period": sel_per,
            "groups": tk_grupos,
            "score_data": _score_fingerprint,
            "history_data": _history_fingerprint,
        })
        st.caption(
            "Mede se o score REALMENTE discrimina retornos: para cada ano N, "
            "correlaciona o RANKING do score (dados até N−1, mesmo motor do "
            "backtest) com o RANKING dos retornos da janela pós-publicação — "
            "fim de março/N a fim de março/N+1, quando o balanço FY N−1 já é "
            "público (Spearman). Referências práticas (Grinold & Kahn): "
            "|IC| < 0,03 ≈ nulo; 0,03–0,08 fraco; > 0,08 relevante. Amostras "
            "pequenas tornam o IC instável — leia como indício, não como "
            "prova; e o universo de sobreviventes tende a SUPERESTIMAR o IC."
        )
        if st.button("Calcular poder preditivo (Rank-IC)", key="b3_av_btn_ic"):
            with st.spinner("Calculando IC por ano…"):
                _prec_ic = _batch_yf_precos_mensais(
                    tuple(sorted(tks_uni)), period=per_opts.get(sel_per, "5y"))
                st.session_state["b3_av_ic_df"] = _rank_ic_por_ano(
                    _prec_ic, hist_batch, tks_uni, pesos_v2,
                    tk_grupos, macro_history or None,
                )
                # registra com que parâmetros o IC foi calculado (evita
                # leitura de resultado obsoleto após mudar filtros)
                st.session_state["b3_av_ic_meta"] = (
                    f"{len(tks_uni)} empresas · período "
                    f"{sel_per} · pesos de "
                    f"{len(pesos_v2)} indicadores"
                )
                st.session_state["b3_av_ic_signature"] = _ic_signature
        df_ic = st.session_state.get("b3_av_ic_df", pd.DataFrame())
        if st.session_state.get("b3_av_ic_signature") != _ic_signature:
            df_ic = pd.DataFrame()
        _ic_meta = st.session_state.get("b3_av_ic_meta")
        if _ic_meta and not df_ic.empty:
            st.caption(f"🧾 Calculado sobre: {_ic_meta}. Se você mudou "
                       "filtros/período depois, recalcule.")
        if not df_ic.empty:
            _ic_med     = float(df_ic["Rank-IC"].mean())
            _ic_pct_pos = float((df_ic["Rank-IC"] > 0).mean()) * 100
            _spread_med = float(df_ic["Spread (pp)"].mean())
            icc1, icc2, icc3 = st.columns(3)
            with icc1:
                card_metrica("IC médio (Spearman)", f"{_ic_med:+.3f}",
                             accent=_COR_POS if _ic_med > 0 else _COR_NEG)
            with icc2:
                card_metrica("Anos com IC > 0", f"{_ic_pct_pos:.0f}%",
                             accent="#4A9EFF")
            with icc3:
                card_metrica("Spread top−bottom (pp)", f"{_spread_med:+.1f}",
                             accent=_COR_POS if _spread_med > 0 else _COR_NEG)
            st.dataframe(df_ic, hide_index=True, use_container_width=True,
                         height=min(300, 45 + 35 * len(df_ic)))
            if _ic_med <= 0.0:
                st.warning(
                    "IC médio ≤ 0 nesta janela/universo: o score NÃO demonstrou "
                    "poder preditivo de retorno aqui. Use-o como filtro de "
                    "qualidade/red-flags, não como expectativa de alfa."
                )
            else:
                st.caption(
                    "IC > 0 é condição necessária, não suficiente: verifique a "
                    "consistência entre os anos antes de concluir que há sinal."
                )
        elif "b3_av_ic_df" in st.session_state:
            st.info("Sem anos com dados suficientes para calcular o IC neste "
                    "universo/período (mín. 5 empresas com score e retorno).")

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
        f"Este é o banco de testes por segmento: as empresas aprovadas aqui passam "
        f"por **{_met.total_metodos(_met.ENG_AV)} análises quantitativas** "
        "encadeadas, cada uma fundamentada em literatura acadêmica consolidada de "
        "finanças. Abaixo, o que cada etapa faz e o estudo que a embasa — para você "
        "validar se o modelo está classificando corretamente este segmento antes de "
        "aplicá-lo em escala na aba Criação de Portfólio."
    )

    with st.expander("📚 Ver todas as análises aplicadas e suas referências", expanded=False):
        for etapa, metodos in _met.catalogo_por_etapa(_met.ENG_AV).items():
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
        refs = "\n".join(
            f"{i}. {r}" for i, r in enumerate(_met.referencias_ordenadas(_met.ENG_AV), 1)
        )
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
    render_market_css()
    st.markdown(_CSS, unsafe_allow_html=True)

    container_pagina(
        "Empresas B3",
        "Análise fundamentalista e construção quantitativa de portfólios brasileiros.",
        "🏢",
        metadados=[("Mercado", "B3 · Brasil")],
    )

    with st.spinner("Carregando lista de empresas…"):
        df_set = _db.load_setores()
    df_set = _so_acoes(df_set)

    if df_set.empty:
        st.caption(
            "⚠️ Banco não configurado — configure `SUPABASE_DB_URL_B3` "
            "no `.env` ou nos secrets do Streamlit Cloud."
        )

    active = render_market_tabs(state_key="b3_active_tab", key_prefix="b3")

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
