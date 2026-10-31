"""
views/empresas_b3.py  — Dashboard Fundamentalista B3
  Tab 1 — Empresas por Setor     (listagem por setor + logos)
  Tab 2 — Análise de Empresa     (drilldown: crescimento, DRE, múltiplos)
  Tab 3 — Criação de Portfólio   (views/portfolio_b3.py)
  Tab 4 — Avaliação de Portfólio (views/analise_portfolio_b3.py)
  Tab 5 — Carteira 20            (views/carteira20_b3.py)

Banco de dados:  core.b3_data (facade c/ MARKET_READ_SOURCE) → b3_db legado ou market.*
Preços:          market.historical_prices; yfinance só como reserva, com aviso
                 na tela (_avisar_fonte_precos)
Logos:           thefintz/icones-b3 CDN (público, sem auth)
"""
from __future__ import annotations

import difflib
import html
import logging
import re
from dataclasses import dataclass

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests
import streamlit as st
import yfinance as yf

import core.b3_company_score as _company_score
import core.b3_data as _db  # facade de leitura B3 — fonte financeira única: market.* (brapi)
import core.data_quality as _dq
import core.market_read as _mr  # séries do market.* (preços mensais ajustados) p/ backtest
from core.b3_methodology import SCORE_VERSION
from core.b3_renda_sustentavel import enrich_decision_universe
from core.b3_slopes import SLOPE_COLS, compute_slope_log, enrich_com_slopes
from core.b3_vigencia import REBAL_MONTH as _REBAL_MONTH
from core.busca_empresa import buscar_empresas, normalizar_ticker
from core.crescimento import (
    Crescimento,
    anos_de_datas,
    cagr_regressao,
    retornos_anuais,
    sem_ano_em_curso,
    ultimo_preco_nao_positivo,
)
from core.llm_context_ativo import build_b3_ativo_context
from core.market_companies import normalize_b3_companies
from core.proventos_b3 import FOTO_MAXIMA_DIAS, idade_da_foto, procedencia
from core.validacao_motor import validacao_b3
from design.chat_ativo import render_chat_ativo
from design.componentes import (
    aviso_atualidade_trimestral_b3,
    aviso_cobertura_do_universo,
    aviso_escala_do_score,
    badge_status,
    card_metrica,
    container_pagina,
    cor_token,
    frescor_da_vitrine,
    selo_de_frescor,
)  # KPIs em cards CSS (visual coeso)
from design.lacunas import aviso_lacuna, detalhe_tecnico
from design.market_companies import (
    NAV_ITEMS_B3,
    render_company_logo,
    render_market_css,
    render_market_tabs,
    render_sector_grid,
)
from design.relatorio_analista import secao_relatorio

logger = logging.getLogger(__name__)


# ── Estado degradado do cálculo (INF-M4 / B3-05, auditoria 04/10/2026) ───────
# Um `except Exception: pass` num caminho de decisão troca um erro visível por
# um número que PARECE íntegro: a resiliência zerada seguia com a versão do
# score na tela, o portão de risco aprovava todo mundo com penalidade zero e a
# série do yfinance entrava no backtest sem rótulo. O padrão aqui é o oposto:
# o cálculo registra a degradação no próprio quadro (``attrs``) e a tela a
# nomeia com ``aviso_lacuna`` — que também a manda para o log de lacunas.

def _marcar_degradacao(df, codigo: str, mensagem: str) -> None:
    """Anota em ``df.attrs["degradacoes"]`` que parte do cálculo falhou.

    Lista nova a cada chamada (não ``append``): ``attrs`` é copiado de quadro
    em quadro, e mutar a lista vazaria a marca para o quadro de origem.
    """
    atuais = list(df.attrs.get("degradacoes") or [])
    if not any(d.get("codigo") == codigo for d in atuais):
        atuais.append({"codigo": codigo, "mensagem": mensagem})
    df.attrs["degradacoes"] = atuais


def _degradacoes(obj) -> list[dict]:
    """Degradações anotadas por ``_marcar_degradacao`` (lista vazia se íntegro)."""
    attrs = getattr(obj, "attrs", None) or {}
    return list(attrs.get("degradacoes") or [])


def _avisar_degradacoes(obj, *, entidade: str | None = None,
                        codigos: tuple[str, ...] | None = None) -> int:
    """Nomeia na tela cada degradação do cálculo. Devolve quantas avisou.

    ``codigos`` restringe às marcas desta etapa: ``attrs`` viaja nas cópias,
    e o Score de Entrada herdaria — e repetiria — o aviso da resiliência que
    já saiu no topo do ranking.
    """
    itens = [d for d in _degradacoes(obj)
             if codigos is None or d.get("codigo") in codigos]
    for d in itens:
        aviso_lacuna(d["mensagem"], codigo=d["codigo"], nivel="warning",
                     entidade=entidade)
    return len(itens)


def _avisar_fonte_precos(df: pd.DataFrame, uso: str) -> None:
    """Procedência dos preços mensais (B3-05): o ranking e os fundamentos
    vêm do ``market.*``; quando o preço veio do yfinance, a tela precisa
    dizer, porque é outra fonte, outro ajuste e outra cobertura."""
    attrs = getattr(df, "attrs", None) or {}
    fonte, erro = attrs.get("fonte_precos"), attrs.get("fonte_precos_erro")
    if fonte == "yfinance":
        causa = (f"a leitura do market.historical_prices falhou ({erro})"
                 if erro else "o market.historical_prices não cobre estes tickers")
        aviso_lacuna(
            f"Preços de {uso} vieram do **yfinance ao vivo**: {causa}. "
            "É outra fonte e outro ajuste de proventos que os do ranking; "
            "leia o resultado como estimativa.",
            codigo="tela.b3.precos_fallback_yfinance", nivel="warning")
    elif fonte == "indisponivel":
        aviso_lacuna(
            f"Preços de {uso} indisponíveis: market.historical_prices e "
            f"yfinance falharam ({erro or 'sem dado'}).",
            codigo="tela.b3.precos_indisponiveis", nivel="warning")


# Variável que realmente resolve a conexão desde o cutover de 2026-07;
# `SUPABASE_DB_URL_B3` só alimenta o fallback legado de `core/b3_db.py`.
VAR_CONEXAO = (
    "`SUPABASE_UNIFICADO_URL` (ou `DATABASE_URL` / `SUPABASE_DB_URL`) "
    "no `.env` ou nos secrets do Streamlit Cloud"
)

# ── Constantes ────────────────────────────────────────────────────────────────
_CDN = "https://raw.githubusercontent.com/thefintz/icones-b3/main/icones"


# Campos % do market.* — decimal por contrato (``core.data_quality.PCT_FIELDS``:
# 0.15 = 15%). Antes a tela adivinhava a escala valor a valor: acima de um teto
# por campo o número era tomado como "já em %" e saía sem ×100. Medido em
# 29/10/2026 no snapshot ttm do armazém, todos os sete campos são decimais
# (DY máx. 0,49; margens dentro de ±0,95) — e o teto só errava: o ROA de 1,149
# da HAGA3 (114,9%, dentro da faixa canônica) aparecia como 1,1%.
_PCT_DECIMAL = _dq.PCT_FIELDS

_COR_POS = "#00C896"
_COR_NEG = "#FC5C7D"
_COR_ALT = "#F6C90E"
_COR_INF = "#4A9EFF"
_COR_NEU = "#9CA3AF"

# ── CSS ───────────────────────────────────────────────────────────────────────
_CSS = """
<style>
.b3-ind-card {
    background:var(--app-surface);border:1px solid var(--app-border);border-radius:10px;
    padding:12px 14px;margin-bottom:6px;
}
.b3-ind-label { font-size:0.60rem;font-weight:700;text-transform:uppercase;
                letter-spacing:.09em;color:var(--app-subtle);margin-bottom:4px; }
.b3-ind-value { font-size:1.35rem;font-weight:800;line-height:1.1; }
.b3-ind-sub   { font-size:0.66rem;color:var(--app-subtle);margin-top:3px; }
.b3-score-badge { display:inline-block;padding:2px 8px;border-radius:12px;
                  font-size:0.72rem;font-weight:700; }
.b3-score-high  { background:color-mix(in srgb, var(--app-primary) 15%, transparent);color:var(--app-primary); }
.b3-score-mid   { background:color-mix(in srgb, var(--app-warning) 15%, transparent);color:var(--app-warning); }
.b3-score-low   { background:color-mix(in srgb, var(--app-danger) 15%, transparent);color:var(--app-danger); }
/* Score de Entrada */
.b3-entrada-aprovada   { background:color-mix(in srgb, var(--app-primary) 12%, transparent);border:1px solid color-mix(in srgb, var(--app-primary) 30%, transparent);
                          color:var(--app-primary);border-radius:6px;padding:3px 10px;
                          font-size:0.72rem;font-weight:800; }
.b3-entrada-observacao { background:color-mix(in srgb, var(--app-warning) 12%, transparent);border:1px solid color-mix(in srgb, var(--app-warning) 30%, transparent);
                          color:var(--app-warning);border-radius:6px;padding:3px 10px;
                          font-size:0.72rem;font-weight:800; }
.b3-entrada-excluida   { background:color-mix(in srgb, var(--app-danger) 12%, transparent);border:1px solid color-mix(in srgb, var(--app-danger) 30%, transparent);
                          color:var(--app-danger);border-radius:6px;padding:3px 10px;
                          font-size:0.72rem;font-weight:800; }
.b3-engine-row { display:flex;align-items:center;gap:6px;margin-bottom:4px;
                 font-size:0.74rem; }
.b3-engine-bar-bg { flex:1;background:var(--app-border);border-radius:4px;height:6px;overflow:hidden; }
.b3-engine-bar-fill { height:100%;border-radius:4px;transition:width .3s; }
.b3-fator-pos { color:var(--app-primary);font-size:0.68rem;font-weight:700; }
.b3-fator-neg { color:var(--app-danger);font-size:0.68rem;font-weight:700; }
.b3-fator-neu { color:var(--app-subtle);font-size:0.68rem; }
/* Declaração de disponibilidade contábil (point-in-time) */
.b3-pit-card  { background:var(--app-surface);border:1px solid var(--app-border);border-radius:10px;
                padding:12px 14px;margin-bottom:6px; }
.b3-pit-title { font-size:0.78rem;font-weight:800;line-height:1.25;margin-bottom:5px; }
.b3-pit-text  { font-size:0.70rem;color:var(--app-muted);line-height:1.45; }
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
        if abs(x) >= 1e9:
            return f"R$ {x/1e9:.2f}B"
        if abs(x) >= 1e6:
            return f"R$ {x/1e6:.2f}M"
        if abs(x) >= 1e3:
            return f"R$ {x/1e3:.1f}K"
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
        if np.isnan(x) or np.isinf(x):
            return _COR_NEU
        pos = x > 0
        if invert:
            pos = not pos
        return _COR_POS if pos else _COR_NEG
    except Exception:
        return _COR_NEU


# ══════════════════════════════════════════════════════════════════════════════
# CAGR via regressão em log
# ══════════════════════════════════════════════════════════════════════════════

def _cagr(df: pd.DataFrame, col: str) -> Crescimento | None:
    """CAGR por regressão log (``core.crescimento``) com janela e motivo."""
    if df is None or df.empty or col not in df.columns or "Data" not in df.columns:
        return None
    return cagr_regressao(anos_de_datas(df["Data"]), df[col])


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
#
# _preco_atual/_yf_precos/_yf_dividendos_anuais alimentam SÓ a aba "Análise de
# Empresa" (cotação pontual, gráfico "Preço da Ação" e fallback de dividendos)
# — decisão de produto documentada no A-011 (auditoria 2026-08-19): a cotação
# e o histórico diário de preço permanecem yfinance ao vivo porque
# market.historical_prices só guarda UM candle por MÊS (ver
# core/market_read.py::load_precos_mensais), então trocar a fonte aqui
# perderia granularidade diária num gráfico só de leitura visual, sem uso em
# score/portfolio/backtest (esses já são market-first via
# _batch_yf_precos_mensais). Em troca: (a) o rótulo de fonte fica visível no
# GRÁFICO, não só na cotação pontual, (b) timeout explícito nas chamadas de
# rede, e (c) falha de rede vira estado observável (df.attrs["load_error"] /
# status), distinto de ausência real de dado — antes ambos caíam em silêncio
# para vazio/None. Dividendos anuais (_dividendos_anuais_market_first, mais
# abaixo) SÃO market-first: são somas anuais, sem perda de granularidade.
# ══════════════════════════════════════════════════════════════════════════════

_YF_TIMEOUT_SEGUNDOS = 10


@st.cache_data(ttl=600, show_spinner=False)
def _preco_atual_com_status(ticker: str) -> tuple[float | None, str]:
    """Cotação pontual via yfinance. Retorna (preço, status); status é
    'ok', 'sem_dado' (ticker sem cotação — ausência real) ou 'falha_rede'
    (timeout/erro de conexão — não confundir com ausência de dado, A-011)."""
    tk = ticker.strip().upper().replace(".SA", "")
    falhou_rede = False
    for var in [f"{tk}.SA", tk]:
        try:
            p = yf.Ticker(var).fast_info.last_price
            if p and float(p) > 0:
                return float(p), "ok"
        except (requests.RequestException, TimeoutError, OSError):
            falhou_rede = True
        except Exception:
            pass
    return None, ("falha_rede" if falhou_rede else "sem_dado")


def _preco_atual(ticker: str) -> float | None:
    """Compat: só o preço, sem status (mantém a assinatura pré-A-011 p/
    chamadores que não precisam distinguir causa de ausência)."""
    return _preco_atual_com_status(ticker)[0]


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


def _dividendos_anuais_market_first(ticker: str) -> tuple[pd.DataFrame, str]:
    """Dividendos anuais para a aba "Análise de Empresa" — market-first
    (A-011): lê ``core.market_read.load_dividendos_anuais`` quando o
    warehouse está saudável; cai no yfinance ao vivo (``_yf_dividendos_anuais``,
    inalterada — segue usada como fallback também pelo Portfólio B3) só
    quando ``market.*`` está indisponível ou não cobre o ticker. Sem perda de
    granularidade (são somas anuais dos dois lados), diferente do gráfico de
    preço. Retorna (DataFrame [Data, Dividendos], fonte) para rótulo na UI.

    O dia da resposta da brapi é procedência: vai para o registro, e a
    resposta que parou de renovar entra na fila de Restrições.
    """
    try:
        df_market = _mr.load_dividendos_anuais(ticker)
    except Exception:
        df_market = pd.DataFrame()
    if df_market.empty:
        return _yf_dividendos_anuais(ticker), "yfinance"
    foto = idade_da_foto(df_market.attrs)
    if foto is not None:
        detalhe_tecnico(f"Proventos anuais de {ticker}: {procedencia(df_market.attrs)}.",
                        codigo="b3.proventos_procedencia", entidade=ticker)
        if foto[1] > FOTO_MAXIMA_DIAS:
            aviso_lacuna(
                f"Proventos de {ticker}: a resposta mais nova da brapi é de "
                f"{foto[0]:%d/%m/%Y}, {foto[1]} dias sem renovação.",
                codigo="tela.b3.proventos_foto_velha", entidade=ticker)
    return df_market, df_market.attrs.get("fonte") or "market.dividends"


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

            # Caminho 1: campo direto do info. Só o trailing é decimal: no
            # yfinance 0.2.66 ``dividendYield`` vem em % (WEGE3 3.36 = 3,36%),
            # e lido aqui como decimal virava DY de 336%.
            raw = info.get("trailingAnnualDividendYield")
            if raw is not None:
                try:
                    v = float(raw)
                    if np.isfinite(v) and v >= 0:
                        dy = v
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
                        # 12 meses contados de hoje: ``last("365D")`` contava do
                        # último provento, e quem parou de pagar há anos saía
                        # com o DY do último ano em que pagou.
                        corte = pd.Timestamp.now(tz=divs.index.tz) - pd.Timedelta(days=365)
                        last12 = float(divs[divs.index >= corte].sum())
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
    """Histórico DIÁRIO de preços ajustados via yfinance — colunas [Data, Preco].

    Fonte ao vivo por decisão de produto (A-011): market.historical_prices só
    tem um candle por MÊS, então virar market-first aqui trocaria o gráfico
    diário de anos de histórico por ~12 pontos/ano. Em compensação, falha de
    rede/timeout marca ``df.attrs["load_error"] = "falha_rede"`` num
    DataFrame vazio — distinto de ausência real de dado (vazio sem attrs) —
    para a UI poder mostrar um aviso em vez de a seção sumir em silêncio.
    """
    tk = ticker.strip().upper().replace(".SA", "")
    falhou_rede = False
    for var in [f"{tk}.SA", tk]:
        try:
            hist = yf.Ticker(var).history(
                period="max", auto_adjust=True, timeout=_YF_TIMEOUT_SEGUNDOS,
            )
            if hist is not None and not hist.empty:
                if hasattr(hist.index, "tz") and hist.index.tz is not None:
                    hist.index = hist.index.tz_localize(None)
                df = hist[["Close"]].reset_index()
                df.columns = ["Data", "Preco"]
                df["Data"] = pd.to_datetime(df["Data"])
                return df
        except (requests.RequestException, TimeoutError, OSError):
            falhou_rede = True
        except Exception:
            pass
    out = pd.DataFrame()
    if falhou_rede:
        out.attrs["load_error"] = "falha_rede"
    return out


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


def _batch_yf_precos_mensais(tickers: tuple[str, ...], period: str = "5y",
                             trocas: tuple[tuple[str, str], ...] = ()) -> pd.DataFrame:
    """
    Preços mensais de fechamento AJUSTADOS (retorno total) p/ múltiplos tickers.

    market-first: lê de market.historical_prices (sem rede) quando o market.*
    está ativo; senão cai no yfinance (auto_adjust=True). Retorna DataFrame com
    DatetimeIndex mensal e colunas = tickers sem .SA. ``trocas`` ((antigo,
    atual), ...) chega só ao banco, que une as duas fitas no diário
    (``market_read.load_precos_mensais``); do yfinance as colunas vêm separadas.

    Procedência (B3-05): ``attrs["fonte_precos"]`` diz de onde a série veio
    (``market.historical_prices`` | ``yfinance`` | ``indisponivel``) e
    ``attrs["fonte_precos_erro"]`` guarda a falha que forçou a troca. Antes a
    queda para o yfinance era um ``except: pass`` — o backtest, a calibração e
    o Rank-IC rodavam sobre outra fonte sem que a tela soubesse.
    """
    if not tickers:
        return pd.DataFrame()
    erro_market: str | None = None
    # Fonte primária: banco (market.*) — adjusted_close, sem rede.
    try:
        kw = {"trocas": tuple(trocas)} if trocas else {}
        df_db = _mr.load_precos_mensais(tuple(tickers), **kw)
        if not df_db.empty:
            anos = _period_to_years(period)
            if anos and df_db.index.notna().any():
                corte = df_db.index.max() - pd.DateOffset(years=anos)
                df_db = df_db[df_db.index >= corte]
            df_db.attrs["fonte_precos"] = "market.historical_prices"
            return df_db
    except Exception as exc:  # noqa: BLE001 - qualquer falha do banco cai no yfinance, rotulada
        erro_market = type(exc).__name__
        logger.exception("Preços mensais do market.* falharam para %d ticker(s); "
                         "caindo no yfinance", len(tickers))
    tks_sa = [f"{t.strip().upper().replace('.SA', '')}.SA" for t in tickers]

    def _rotulado(df: pd.DataFrame, fonte: str, erro: str | None) -> pd.DataFrame:
        df.attrs["fonte_precos"] = fonte
        if erro:
            df.attrs["fonte_precos_erro"] = erro
        return df

    try:
        if len(tks_sa) == 1:
            raw = yf.download(tks_sa[0], period=period, interval="1mo",
                              auto_adjust=True, progress=False)
            if raw is None or raw.empty:
                return _rotulado(pd.DataFrame(), "indisponivel", erro_market)
            tk_clean = tks_sa[0].replace(".SA", "").upper()
            close = pd.DataFrame({tk_clean: raw["Close"]})
        else:
            raw = yf.download(tks_sa, period=period, interval="1mo",
                              auto_adjust=True, progress=False)
            if raw is None or raw.empty:
                return _rotulado(pd.DataFrame(), "indisponivel", erro_market)
            close = (
                raw["Close"].copy()
                if isinstance(raw.columns, pd.MultiIndex)
                else raw.copy()
            )
        if hasattr(close.index, "tz") and close.index.tz is not None:
            close.index = close.index.tz_localize(None)
        close.columns = [str(c).replace(".SA", "").strip().upper() for c in close.columns]
        return _rotulado(close.dropna(how="all"), "yfinance", erro_market)
    except Exception as exc:  # noqa: BLE001 - rede/parse do yfinance; vira estado rotulado
        logger.exception("Preços mensais do yfinance falharam para %d ticker(s)",
                         len(tickers))
        erro = "; ".join(e for e in (erro_market, f"yfinance: {type(exc).__name__}") if e)
        return _rotulado(pd.DataFrame(), "indisponivel", erro)


# _batch_yf_liquidez (liquidez média diária via yfinance) foi removida: o
# filtro de liquidez da Criação de Portfólio lê _db.load_giro_diario()
# (armazém local).


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
        st.info("Esta análise não está disponível no momento.")
        aviso_lacuna(
            "O cadastro de setores voltou vazio — o app não conseguiu ler o "
            "banco. Confira " + VAR_CONEXAO + ".",
            codigo="tela.b3.cadastro_setores_vazio")
        return
    # Card mostra só AÇÕES — remove FIIs, ETFs, BDRs e subscrições.
    df_set = _so_acoes(df_set)
    if df_set.empty:
        st.info("Nenhuma ação encontrada na lista de setores.")
        return
    # Sem busca por ticker: a navegação desta aba é por setor, e o botão
    # "Analisar" de cada card já leva à Análise de Empresa.
    companies = normalize_b3_companies(df_set, _logo_url)
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


_SLOPE_COLS: tuple[str, ...] = SLOPE_COLS

# Fonte unica da verdade das faixas: core/data_quality.py (margens apertadas
# para [-100%, +100%], o que captura outliers como UGPA3=190%).
_SCORE_RANGES = _dq.CANONICAL_RANGES


# Definidos em core.b3_slopes para que a Análise do Portfólio calcule o MESMO
# crescimento; duplicar a fórmula já produziu notas divergentes para a mesma
# empresa em telas diferentes.
_compute_slope_log = compute_slope_log


def _enrich_com_slopes(
    df_mult: pd.DataFrame,
    hist_batch: dict[str, pd.DataFrame],
) -> pd.DataFrame:
    """Acrescenta colunas {col}_slope_log ao df_mult calculadas do histórico."""
    return enrich_com_slopes(df_mult, hist_batch)


def _enrich_com_evidencia_historica(
    df_mult: pd.DataFrame,
    hist_batch: dict[str, pd.DataFrame],
    tickers: tuple[str, ...],
) -> pd.DataFrame:
    """Sustentabilidade da distribuição + histórico patrimonial ao df_mult.

    Extraída para ser testável isoladamente (mesmo padrão de
    ``_enrich_com_slopes``): o dossiê da Análise de Empresa chama a MESMA
    função de core que a Criação de Portfólio usa para montar o quadro de
    decisão, em vez de reimplementar a composição.
    """
    return enrich_decision_universe(df_mult, hist_batch, tickers)


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
    # Motor único (A-101/A-105 no motor oficial): múltiplo de preço vira
    # rendimento ANTES da limpeza de faixa. A faixa canônica começa em 0,01
    # para P/L, P/VP, EV_EBIT e P_FCO, então a limpeza transformava prejuízo
    # em ausência e ausência vale o neutro 0,5 — a deficitária ficava na
    # mediana do segmento, mais "barata" que metade das lucrativas. O
    # diagnóstico (core.b3_company_score) já corrigia isso; o oficial, que
    # decide ranking e carteira, não. Agora os dois usam a mesma regra.
    rendimentos = {
        col: _company_score._numeric_metric(df, col)
        for col in pesos
        if col in _company_score._RECIPROCO and col in df.columns
    }
    df = _clean_score_inputs(df, list(pesos.keys()))

    group_col = _resolve_group_col_df(df, prefer=group_col_prefer)
    score = pd.Series(0.0, index=df.index)

    for col, (peso, melhor_alto) in pesos.items():
        if col not in df.columns or peso == 0:
            continue
        if col in rendimentos:
            # Rendimento: maior é melhor, e a ordem entre lucrativas é a
            # mesma do múltiplo (o recíproco é monótono nos positivos).
            s, melhor_alto = rendimentos[col], True
        else:
            # Fonte limpa (market.*): nulo segue para o rank neutro, não é
            # reconstruído por imputação.
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

    # ── Ajustes de resiliência histórica + saúde financeira ──────────────
    # A: bônus para empresa temporariamente abaixo da própria média histórica
    #    mas acima da mediana setorial — sinal de oportunidade, não fraqueza.
    # B: penalidade de risco de sobrevivência (balanço frágil).
    # C: bônus de valuation abaixo do próprio histórico sem deterioração de
    #    fundamentais — margem de segurança à la Graham, adaptada ao Brasil.
    #
    # Tudo ou nada (B3-05): A, C e B mexem em `score_raw` em sequência; uma
    # falha em B deixava A e C aplicados pela metade. O snapshot abaixo volta
    # o score ao estado anterior e a falha vira degradação nomeada. Antes, o
    # `except` chamava `df.setdefault` — que DataFrame não tem —, então a
    # falha derrubava o ranking inteiro com um AttributeError sem relação com
    # a causa; e a intenção declarada (zerar e seguir) mantinha a versão do
    # score na tela como se o número estivesse íntegro.
    _score_antes_resiliencia = df["score_raw"].copy()
    try:
        from core.resilience_score import (
            financial_health_penalty,
            historical_reversion_adj,
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

    except Exception as exc:  # noqa: BLE001 - qualquer falha vira degradação nomeada, não número
        logger.exception("Ajustes de resiliência do score B3 falharam (%d empresas)",
                         len(df))
        df["score_raw"] = _score_antes_resiliencia
        # NaN, não 0,0: zero afirma "sem bônus e balanço saudável" — a tabela
        # de resiliência pintava 🟢 Saudável em todas. Sem cálculo, não há
        # veredito.
        for _col in ("hist_bonus", "val_hist_bonus", "health_penalty"):
            df[_col] = np.nan
        _marcar_degradacao(
            df, "tela.b3.score_sem_resiliencia",
            f"Score B3 {SCORE_VERSION} calculado **sem** os ajustes de "
            f"resiliência e saúde financeira (falha: {type(exc).__name__}). "
            "O ranking não desconta balanço frágil nem dá o bônus de reversão "
            "histórica — não é a metodologia completa.",
        )

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
    "2.25.0": (
        "Auditoria 2026-08 (achado A-002), disponibilidade contabil MEDIDA vs "
        "MODELADA: (A) linha sem vintage real (backfill migration_baseline, "
        "cujo available_at e a data de INGESTAO e por isso e anulado em "
        "core/market_read) deixa de ser tratada como 'disponivel desde "
        "sempre' — a disponibilidade passa a ser MODELADA pelo prazo legal de "
        "publicacao da CVM (31/03 do ano seguinte ao exercicio, constante "
        "_CVM_DF_DEADLINE_*); (B) fail-closed: essa linha so entra se a data "
        "de decisao (ano_ref/rebal_month) for POSTERIOR ao prazo — antes "
        "lag=0 ou rebalance antes de 31/03 aceitavam exercicio ainda nao "
        "publicado (look-ahead latente); (C) vintage MEDIDA nunca e "
        "contornada, mesmo havendo linha baseline do mesmo ticker/ano; "
        "(D) _score_historico_ano passa a receber rebal_month, de modo que a "
        "data de decisao do backtest e do Rank-IC seja a real e nao a "
        "constante default; (E) backtest e Rank-IC publicam a cobertura "
        "point-in-time (PITCoverage em attrs) e a UI rotula o resultado: com "
        "cobertura medida 0 e SIMULACAO com disponibilidade MODELADA, nao "
        "'backtest point-in-time validado'; o card nomeia o risco residual de "
        "restatement (metric_value guarda o valor atual/reapresentado)."
    ),
    "2.27.0": (
        "Auditoria 2026-09, motor unico: _score_universo (oficial) passa a "
        "ranquear P/L, P/VP, EV_EBIT e P_FCO como rendimento (reciproco + piso "
        "para prejuizo apagado, A-101/A-105), a mesma regra de "
        "core.b3_company_score. Antes a faixa canonica (0,01..) anulava o "
        "multiplo negativo e a deficitaria recebia o neutro 0,5. Selecao de "
        "segmentos ganha o teste de excesso sobre Pesos Iguais com intervalo "
        "de 90% (core.b3_selecao) em todos os modos, e a reconstrucao "
        "historica passa a usar a liquidez da epoca (core.b3_universo_pit)."
    ),
    "2.28.0": (
        "Vies de sobrevivencia: a reconstrucao historica passa a incluir as "
        "empresas que sairam da B3 desde 2016 (core.b3_saidas, COTAHIST + DFP "
        "da CVM), so nos anos em que estavam listadas e nunca na decisao "
        "corrente. Na saida a posicao vira caixa pelo ultimo fechamento e e "
        "reinvestida; o Rank-IC usa a ultima cotacao da janela inteira."
    ),
    "2.29.0": (
        "Auditoria B3-01: o preco mensal ajustado perde os saltos com "
        "evidencia de evento societario ou dado corrompido (fator de split "
        "redondo, ajustado explodindo com close estavel, alta > +300%) em "
        "core.b3_precos_saneamento; queda sem evidencia (AMER3 -82% em "
        "01/2023) fica, por ser perda real. As safras winsorizam a secao "
        "transversal a 1%/99%. O EW da safra 2024 cai de +135,6% para -7,6%; "
        "a medicao OOS da 2.28.0 foi feita sobre o preco corrompido e precisa "
        "ser refeita para esta versao."
    ),
    "2.30.0": (
        "Auditoria B3-06/07: papel PARADO sai da elegibilidade por epoca "
        "(core.b3_universo_pit.parados_em). E parado quem ja negociou no "
        "quadro e cujo ultimo mes com negocio terminou ha mais de 90 dias da "
        "decisao, com a regua no ultimo mes negociado do universo da tela; "
        "quem nunca apareceu segue elegivel. Antes a mediana da janela so via "
        "os meses com negocio e o ticker sem linha na janela caia na regra da "
        "ausencia. A regra roda tambem sem piso de volume e tira da carteira "
        "atual quem esta parado hoje, sem tira-lo dos anos em que negociava. "
        "Muda a elegibilidade de cada safra: a medicao OOS e desta versao."
    ),
    "2.31.0": (
        "Auditoria app4, look-ahead da aba B3: os pisos de valor de mercado e "
        "de volume com a foto de HOJE so decidem a carteira atual. Antes "
        "valiam para todos os anos da reconstrucao -- quem encolheu sumia do "
        "passado e o Pesos Iguais perdia justamente as quedas. Cada ano usa o "
        "volume da epoca e o valor de mercado da epoca, estimado em 31/12 do "
        "exercicio lido pela razao de precos sem dividendos "
        "(core.b3_universo_pit.abaixo_do_tamanho_por_ano). A Selic do "
        "backtest deixa de usar a media da serie inteira nos anos sem "
        "observacao: repete o ultimo valor conhecido "
        "(core.b3_safras.selic_sem_futuro). Muda o universo de cada safra: a "
        "medicao OOS e desta versao."
    ),
    "2.32.0": (
        "Auditoria app4, sobras do look-ahead da aba B3. (1) A carteira de "
        "hoje passa a aplicar a mesma penalidade de decaimento por anos de "
        "lideranca que a reconstrucao aplica: antes a evidencia (Rank-IC, "
        "safras, OOS) media uma regra que a tela nao entregava. (2) A medicao "
        "OOS da carteira passa a aplicar o piso de qualidade (nos perfis que o "
        "ligam) e o Score de Entrada com o retrato da propria safra -- "
        "multiplos e serie de PL ate N-1, pela mesma regra de vintage do score "
        "(core.b3_retrato_pit). O portao da Inteligencia dos Ativos e o "
        "parecer de LLM leem o dossie de hoje e seguem cobertos so pela banda "
        "das variantes. Muda a carteira atual e a medicao OOS: ambas sao "
        "desta versao."
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
    except Exception:  # noqa: BLE001 - quem chama marca a degradação; aqui só o registro
        logger.exception("Risk engine B3 (distress_risk_score) falhou; penalidade 0")
        return pd.Series(0.0, index=df.index)


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
    except Exception as exc:  # noqa: BLE001 - qualquer falha vira degradação nomeada
        # O "fallback" `_risk_engine` reimporta o mesmo distress_risk_score e,
        # falhando de novo, devolve 0 — o portão "Excluída por risco" (r ≥ 10)
        # fica desligado. Antes isso saía com probabilidade 0,0 e driver
        # "none", que se lê como "sem risco"; agora o risco fica não apurado
        # (NaN / "indisponivel") e a tela avisa que o portão não rodou.
        logger.exception("Risco de distress do Score de Entrada falhou (%d empresas)",
                         len(df))
        df["r_penalty"] = _risk_engine(df)
        df["risk_probability"] = np.nan
        df["risk_driver"] = "indisponivel"
        _marcar_degradacao(
            df, "tela.b3.score_entrada_sem_risco",
            f"Risco de distress não calculado (falha: {type(exc).__name__}): "
            "o portão que exclui empresas com penalidade de risco ≥ 10 **não "
            "rodou** — \"Aprovada\" aqui não passou pelo filtro de risco.",
        )
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


_GAMMA_DEF, _CAP_DEF, _SOFT_DEF = 0.90, 0.25, 0.05


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


# ── Disponibilidade contábil: MEDIDA vs MODELADA ──────────────────────────────
# Prazo legal de publicação das demonstrações financeiras anuais (CVM 480/2009,
# art. 25, II): até 3 meses do encerramento do exercício social — 31/03 do ano
# seguinte para o exercício encerrado em 31/12, que é o caso da quase totalidade
# das companhias abertas brasileiras. É desta constante, e não do mês de
# rebalance, que sai o modelo de disponibilidade usado quando NÃO há vintage
# medida; se o mês de rebalance mudar, o modelo continua correto.
_CVM_DF_DEADLINE_MES = 3
_CVM_DF_DEADLINE_DIA = 31

_PIT_RISCO_RESTATEMENT = (
    "Risco residual que permanece mesmo com disponibilidade medida: a base "
    "guarda em metric_value o valor ATUAL do indicador. Se a companhia "
    "reapresentou o balanço depois, a simulação usa o número revisado como se "
    "ele já fosse conhecido na época (restatement bias). Não há vintage de "
    "VALOR na base — só de data."
)


def _prazo_publicacao_cvm(ano_fiscal: int) -> pd.Timestamp:
    """Data a partir da qual o exercício `ano_fiscal` é presumidamente público."""
    return pd.Timestamp(int(ano_fiscal) + 1, _CVM_DF_DEADLINE_MES, _CVM_DF_DEADLINE_DIA)


def _classificar_disponibilidade_pit(
    validos: pd.DataFrame, decision_date: pd.Timestamp
) -> tuple[pd.Series, pd.Series]:
    """Classifica cada linha do histórico em MEDIDA/MODELADA e aceita/barra.

    Retorna (medida, aceita) — duas máscaras booleanas alinhadas a `validos`.

    Por que duas categorias: `core/market_read.py::_annual_long()` ANULA o
    `available_at` das vintages `migration_baseline`, porque nelas o carimbo é a
    data da INGESTÃO (recente), não a disponibilidade histórica. A linha sem
    carimbo não é "disponível desde sempre": ela simplesmente não tem
    disponibilidade MEDIDA, e o sistema a substitui por um MODELO — o prazo
    legal de publicação. Tratar as duas como a mesma coisa é o que transformava
    um modelo em fato.

    Regras (fail-closed):
      • vintage MEDIDA nunca é contornada: só passa se `AvailableAt` for anterior
        ou igual à data de decisão. Se uma vintage real concorrente do mesmo
        exercício for posterior (ou inválida), ela também impede o fallback de
        uma linha baseline (`NaT`) desse exercício;
      • linha MODELADA só passa se a data de decisão for POSTERIOR ao prazo legal
        de publicação daquele exercício. Fora dessa janela a disponibilidade não
        é medida nem justificável pelo modelo — aceitá-la é look-ahead (era o
        caso latente com lag=0 ou rebalance antes de 31/03).
    """
    if validos.empty:
        vazio = pd.Series(dtype=bool, index=validos.index)
        return vazio, vazio
    if "AvailableAt" in validos.columns:
        # `NaT` é a proveniência explícita do backfill baseline (logo, pode usar
        # o modelo de prazo CVM). Um valor PRESENTE mas impossível de converter
        # não é baseline: não sabemos quando ficou disponível e ele deve falhar
        # fechado, em vez de ser silenciosamente rebaixado a disponibilidade
        # modelada.
        baseline = validos["AvailableAt"].isna()
        available_at = pd.to_datetime(
            validos["AvailableAt"], errors="coerce", utc=True
        ).dt.tz_convert(None)
        malformado = (~baseline) & available_at.isna()
    else:
        available_at = pd.Series(pd.NaT, index=validos.index)
        malformado = pd.Series(False, index=validos.index)
    # Para contagem e fail-closed, timestamp malformado se comporta como uma
    # vintage não utilizável (não como baseline); assim entra em
    # `linhas_barradas_vintage` e nunca em `linhas_modeladas`.
    medida = available_at.notna() | malformado
    prazo = pd.to_datetime(validos["_ano"].map(_prazo_publicacao_cvm))
    aceita_medida = medida & (available_at <= decision_date)
    # O histórico pode conter simultaneamente uma baseline de migração e uma
    # vintage real para o mesmo ticker/exercício. Como esta função recebe um
    # ticker por vez, `_ano` é a chave do snapshot concorrente. Sem esta
    # redução por exercício, uma vintage posterior seria barrada por linha,
    # mas a NaT do mesmo ano passaria pelo prazo CVM e a reabilitaria.
    vintage_concorrente_barrada = medida & (~aceita_medida)
    ano_com_vintage_concorrente_barrada = (
        vintage_concorrente_barrada.groupby(validos["_ano"]).transform("any")
    )
    aceita = aceita_medida | (
        (~medida)
        & (~ano_com_vintage_concorrente_barrada)
        & (prazo <= decision_date)
    )
    return medida, aceita


@dataclass(frozen=True)
class PITCoverage:
    """Cobertura point-in-time de um (ou vários) snapshots históricos.

    Unidade: contagem de linhas anuais e de snapshots (1 por ticker/decisão).
    `cobertura_medida` é adimensional (0..1) e mede a fração dos snapshots que
    efetivamente entraram no score cuja disponibilidade foi MEDIDA — o restante
    foi MODELADO pelo prazo CVM.
    """

    linhas_avaliadas: int = 0          # linhas que passaram no corte fiscal
    linhas_medidas: int = 0            # aceitas com AvailableAt real
    linhas_modeladas: int = 0          # aceitas pelo prazo CVM (sem vintage)
    linhas_barradas_vintage: int = 0   # vintage real/concor. posterior à decisão
    linhas_barradas_prazo: int = 0     # sem vintage e decisão antes do prazo
    snapshots_medidos: int = 0         # linha usada no score veio de vintage
    snapshots_modelados: int = 0       # linha usada no score veio do modelo
    decisoes: int = 0                  # nº de datas de decisão agregadas

    def __add__(self, other: PITCoverage) -> PITCoverage:
        if not isinstance(other, PITCoverage):
            return NotImplemented
        return PITCoverage(
            linhas_avaliadas=self.linhas_avaliadas + other.linhas_avaliadas,
            linhas_medidas=self.linhas_medidas + other.linhas_medidas,
            linhas_modeladas=self.linhas_modeladas + other.linhas_modeladas,
            linhas_barradas_vintage=(
                self.linhas_barradas_vintage + other.linhas_barradas_vintage),
            linhas_barradas_prazo=(
                self.linhas_barradas_prazo + other.linhas_barradas_prazo),
            snapshots_medidos=self.snapshots_medidos + other.snapshots_medidos,
            snapshots_modelados=(
                self.snapshots_modelados + other.snapshots_modelados),
            decisoes=self.decisoes + other.decisoes,
        )

    @property
    def snapshots(self) -> int:
        return self.snapshots_medidos + self.snapshots_modelados

    @property
    def cobertura_medida(self) -> float:
        """Fração dos snapshots usados com disponibilidade MEDIDA (0..1).

        Sem nenhum snapshot não existe cobertura a reportar: devolve 0.0, que é
        o valor conservador (nada foi medido) — nunca 1.0 por vacuidade.
        """
        return self.snapshots_medidos / self.snapshots if self.snapshots else 0.0

    @property
    def nivel(self) -> str:
        """'medida' | 'mista' | 'modelada' | 'indisponivel'."""
        if not self.snapshots:
            return "indisponivel"
        cob = self.cobertura_medida
        if cob >= 1.0:
            return "medida"
        return "modelada" if cob <= 0.0 else "mista"

    def as_dict(self) -> dict[str, float | int | str]:
        return {
            "linhas_avaliadas": self.linhas_avaliadas,
            "linhas_medidas": self.linhas_medidas,
            "linhas_modeladas": self.linhas_modeladas,
            "linhas_barradas_vintage": self.linhas_barradas_vintage,
            "linhas_barradas_prazo": self.linhas_barradas_prazo,
            "snapshots_medidos": self.snapshots_medidos,
            "snapshots_modelados": self.snapshots_modelados,
            "decisoes": self.decisoes,
            "cobertura_medida": round(self.cobertura_medida, 4),
            "nivel": self.nivel,
        }


def _pit_rotulo_resultado(cov: PITCoverage, contexto: str = "Backtest") -> dict[str, str]:
    """Rótulo honesto do resultado conforme a ORIGEM da disponibilidade.

    Regra de nomenclatura (G1/G2): só é "point-in-time validado" o resultado em
    que 100 % dos snapshots usados tinham disponibilidade MEDIDA. Com cobertura
    medida nula o resultado é uma SIMULAÇÃO com disponibilidade modelada pelo
    prazo CVM — continua útil e continua sem look-ahead conhecido, mas é
    inferência sobre a data de publicação, não observação dela.
    """
    nivel = cov.nivel
    pct = cov.cobertura_medida * 100.0
    if nivel == "medida":
        titulo = f"{contexto} point-in-time validado (disponibilidade medida)"
        detalhe = (
            f"{cov.snapshots_medidos} de {cov.snapshots} snapshots usados têm "
            "vintage real de disponibilidade (available_at observado)."
        )
        cor = _COR_POS
    elif nivel == "mista":
        titulo = (
            f"{contexto} PARCIALMENTE point-in-time — {pct:.0f}% da "
            "disponibilidade é medida"
        )
        detalhe = (
            f"{cov.snapshots_medidos} de {cov.snapshots} snapshots usados têm "
            f"vintage real; os outros {cov.snapshots_modelados} tiveram a "
            "disponibilidade MODELADA pelo prazo legal de publicação (31/03 do "
            "ano seguinte ao exercício, CVM 480)."
        )
        cor = _COR_ALT
    elif nivel == "modelada":
        titulo = (
            f"{contexto} é SIMULAÇÃO com disponibilidade MODELADA (prazo CVM) — "
            "não é backtest point-in-time validado"
        )
        detalhe = (
            f"Nenhum dos {cov.snapshots} snapshots usados tem vintage real: o "
            "histórico vem do backfill (migration_baseline), cujo carimbo é a "
            "data da INGESTÃO e por isso é descartado. A data de publicação foi "
            "INFERIDA do prazo legal (31/03 do ano seguinte ao exercício, "
            "CVM 480), e só entram linhas cujo prazo já venceu na data de "
            "decisão."
        )
        cor = _COR_NEG
    else:
        titulo = f"{contexto} sem snapshot histórico avaliado"
        detalhe = ("Nenhuma linha sobreviveu ao corte fiscal e ao filtro de "
                   "disponibilidade — não há cobertura a declarar.")
        cor = _COR_NEU
    barradas = cov.linhas_barradas_vintage + cov.linhas_barradas_prazo
    detalhe += (
        f" Linhas barradas pelo filtro: {cov.linhas_barradas_vintage} por "
        f"vintage posterior/concorrente e {cov.linhas_barradas_prazo} por prazo "
        f"de publicação não vencido (total {barradas})."
    )
    return {
        "nivel": nivel,
        "cobertura_pct": f"{pct:.0f}%",
        "titulo": titulo,
        "detalhe": detalhe,
        "risco": _PIT_RISCO_RESTATEMENT,
        "cor": cor,
    }


def _pit_card_html(cov: PITCoverage, contexto: str = "Backtest") -> str:
    """Card CSS com o rótulo, a cobertura e o risco residual (padrão do app)."""
    info = _pit_rotulo_resultado(cov, contexto)
    return (
        f'<div class="b3-pit-card" style="border-left:4px solid {cor_token(info["cor"])};">'
        f'<div class="b3-ind-label">Disponibilidade contábil (point-in-time)</div>'
        f'<div class="b3-ind-value" style="color:{cor_token(info["cor"])};">'
        f'{info["cobertura_pct"]} medida</div>'
        f'<div class="b3-ind-sub">cobertura point-in-time = snapshots com '
        f'vintage real ÷ snapshots usados ({cov.snapshots_medidos}/'
        f'{cov.snapshots})</div>'
        f'<div class="b3-pit-title" style="color:{cor_token(info["cor"])};margin-top:8px;">'
        f'{html.escape(info["titulo"])}</div>'
        f'<div class="b3-pit-text">{html.escape(info["detalhe"])}</div>'
        f'<div class="b3-pit-text" style="margin-top:6px;">'
        f'{html.escape(info["risco"])}</div>'
        f'</div>'
    )


class _MapaScores(dict):
    """``{ticker: score}`` que carrega as degradações do score daquele ano.

    O ``_score_universo`` anota em ``attrs`` quando o score saiu sem os
    ajustes de resiliência, mas o mapa devolvido ao backtest é um dict — a
    marca se perdia ali e o resultado era lido como da metodologia completa
    (INF-M4). Subclasse de dict para não mudar o contrato dos chamadores.
    """

    def __init__(self, *args, degradacoes: list[dict] | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.degradacoes: list[dict] = list(degradacoes or [])


def _mapa_sem_resiliencia(score_map) -> bool:
    """O score daquele ano saiu sem os ajustes de resiliência?

    Ponto único da pergunta para backtest, Rank-IC e Criação de Portfólio:
    três cópias da mesma checagem tendem a divergir quando o código muda.
    """
    return any(d.get("codigo") == "tela.b3.score_sem_resiliencia"
               for d in getattr(score_map, "degradacoes", ()))


def _score_historico_ano_com_cobertura(
    df_hist_batch: dict[str, pd.DataFrame],
    tickers: list[str],
    ano_ref: int,
    pesos: dict[str, tuple[float, bool]],
    tk_grupos: dict[str, dict] | None = None,
    lag: int = 1,
    macro_by_year: dict[int, dict[str, float]] | None = None,
    rebal_month: int = _REBAL_MONTH,
) -> tuple[dict[str, float], PITCoverage]:
    """Score histórico do ano ``ano_ref`` (lag=1) e a cobertura PIT da medição.

    Nasceu como irmã de ``_score_historico_ano``, que devolvia só o mapa de
    scores para o backtest, a calibração e o Rank-IC da Análise Avançada. A aba
    saiu em 30/10/2026 e levou a irmã; esta ficou, porque o motor da Criação de
    Portfólio precisa DECLARAR se a disponibilidade dos dados foi medida ou
    modelada.
    """
    ano_cutoff = ano_ref - lag
    decision_date = pd.Timestamp(ano_ref, int(rebal_month), 1)
    rows = []
    cov = PITCoverage(decisoes=1)
    for tk in tickers:
        df_h = df_hist_batch.get(tk)
        if df_h is None or df_h.empty or "Data" not in df_h.columns:
            continue
        df_h = df_h.copy()
        df_h["_ano"] = pd.to_datetime(df_h["Data"], errors="coerce").dt.year
        # reset_index: o snapshot é localizado por rótulo mais adiante; índice
        # duplicado vindo do batch faria `.loc` devolver Series em vez de bool.
        validos = df_h[df_h["_ano"] <= ano_cutoff].reset_index(drop=True)
        medida, aceita = _classificar_disponibilidade_pit(validos, decision_date)
        prazo = pd.to_datetime(validos["_ano"].map(_prazo_publicacao_cvm))
        # Uma baseline cujo prazo já venceu só pode ter sido recusada por uma
        # vintage real concorrente do mesmo exercício; atribuí-la ao prazo CVM
        # esconderia a causa do bloqueio na declaração de cobertura.
        baseline_barrada_por_vintage = (
            (~medida) & (~aceita) & (prazo <= decision_date)
        )
        cov = cov + PITCoverage(
            linhas_avaliadas=len(validos),
            linhas_medidas=int((medida & aceita).sum()),
            linhas_modeladas=int(((~medida) & aceita).sum()),
            linhas_barradas_vintage=int(
                (medida & (~aceita)).sum() + baseline_barrada_por_vintage.sum()
            ),
            linhas_barradas_prazo=int(
                ((~medida) & (~aceita) & (~baseline_barrada_por_vintage)).sum()
            ),
        )
        validos = validos[aceita] if len(validos) else validos
        if validos.empty:
            continue
        ordenadas = validos.sort_values("_ano")
        idx_snap = ordenadas.index[-1]
        row = ordenadas.iloc[-1].to_dict()
        row["Ticker"] = tk
        row = _sanitize_dy(row)
        if tk_grupos:
            row.update(tk_grupos.get(tk) or {})
        rows.append(row)
        cov = cov + (
            PITCoverage(snapshots_medidos=1) if bool(medida.loc[idx_snap])
            else PITCoverage(snapshots_modelados=1)
        )

    if not rows:
        return {}, cov

    df_snap = pd.DataFrame(rows)
    df_sc   = _score_universo(
        df_snap, tickers, pesos,
        macro_context=_macro_for_year(macro_by_year, ano_cutoff),
    )
    if df_sc.empty or "score" not in df_sc.columns:
        return {}, cov
    # descarta scores não-finitos (NaN/inf): empresa sem score computável naquele
    # ano não entra no ranking — evita poluir pesos/backtest com NaN (alpha NaN).
    sc = pd.to_numeric(df_sc["score"], errors="coerce")
    return _MapaScores(
        {tk: float(s) for tk, s in zip(df_sc["Ticker"], sc)
         if pd.notna(s) and np.isfinite(s)},
        degradacoes=_degradacoes(df_sc),
    ), cov


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


# ══════════════════════════════════════════════════════════════════════════════
# TAB 2 — Análise de Empresa
# ══════════════════════════════════════════════════════════════════════════════

def _ind_card(label: str, valor: str, sub: str, cor: str) -> str:
    return (
        f'<div class="b3-ind-card">'
        f'<div class="b3-ind-label">{label}</div>'
        f'<div class="b3-ind-value" style="color:{cor_token(cor)};">{valor}</div>'
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
        if mult.empty:
            return None
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
        """Valor % em escala de exibição (×100 — o banco guarda decimal)."""
        v = _g(key)
        return None if v is None else v * 100.0

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
            # O número aparece como está no banco; o rótulo avisa que ele é
            # implausível (a faixa canônica o tira do score) em vez de escondê-lo.
            if (pct and db_key in _PCT_DECIMAL and v != 0
                    and not _score_value_usable(db_key, v / 100.0)):
                sub, cor = "Fora da faixa plausível — fora do score", _COR_ALT
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
        f'<div style="font-size:0.75rem;font-weight:700;color:var(--app-text);'
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
    # Degradações do dossiê (INF-M4): viajam em ``attrs`` da linha devolvida
    # e a tela as nomeia ao lado da nota, que traz "Metodologia B3 <versão>".
    degradacoes: list[dict] = []
    try:
        historicos = _db.load_multiplos_historico_batch(tickers)
        pares = _enrich_com_slopes(pares, historicos)
        pares = _enrich_com_evidencia_historica(pares, historicos, tickers)
    except Exception as exc:  # noqa: BLE001 - segue com crescimento neutro, mas nomeado
        # O score continua calculável com crescimento neutro e cobertura
        # reduzida — só não pode parecer que usou o histórico.
        logger.exception("Histórico dos pares de %s falhou no dossiê B3", ticker)
        degradacoes.append({
            "codigo": "tela.b3.dossie_sem_historico",
            "mensagem": (f"Histórico dos pares indisponível ({type(exc).__name__}): "
                         "crescimento e evidência histórica entraram neutros na nota."),
        })

    scored = score_cross_section(pares)
    linha = scored[scored["Ticker"] == ticker].copy()
    if linha.empty:
        # NaN, não 50: 50 é a mediana do corte, e classification(50) devolve
        # "Neutra" — um veredito de que a empresa é mediana. Sem nenhum par
        # apurado não há veredito nenhum, e é isso que a tela precisa dizer
        # (achado A-103).
        return (pd.Series({"Ticker": ticker, "score": float("nan"), "coverage": 0.0}),
                referencia)

    # A nota principal continua sendo calculada pelo motor oficial da B3. As
    # seis trilhas são uma decomposição diagnóstica para o radar, não um score
    # concorrente com o ranking já utilizado na Criação de Portfólio.
    try:
        setor_alvo = str(alvo.iloc[0].get("SETOR") or "") if not alvo.empty else ""
        pesos = _get_pesos_setor(setor_alvo)
        oficial = _score_universo(
            pares, list(tickers), pesos, historicos,
            group_col_prefer="SEGMENTO",
        )
        nota = pd.to_numeric(
            oficial.loc[oficial["Ticker"] == ticker, "score"], errors="coerce"
        )
        if not nota.empty and np.isfinite(float(nota.iloc[0])):
            linha.loc[:, "score"] = float(nota.iloc[0])
        else:
            # O motor rodou mas não deu nota finita a este ticker: a tela fica
            # com a média das trilhas — a mesma troca de nota que a exceção
            # abaixo já nomeia. Cair calado nela rotularia como "Metodologia
            # B3 <versão>" uma nota que não é a do ranking.
            logger.warning("Motor oficial do score B3 sem nota finita para %s", ticker)
            degradacoes.append({
                "codigo": "tela.b3.dossie_nota_das_trilhas",
                "mensagem": (f"Motor oficial do score não produziu nota finita para "
                             f"{ticker}: a nota exibida é a média das seis trilhas, "
                             "não a nota do ranking."),
            })
        degradacoes.extend(_degradacoes(oficial))
    except Exception as exc:  # noqa: BLE001 - troca de nota precisa ser nomeada
        # Em indisponibilidade pontual do motor completo, a média das trilhas
        # mantém o painel utilizável — mas é OUTRA nota, não a do ranking da
        # Criação de Portfólio, e a tela dizia "Metodologia B3 <versão>" do mesmo jeito.
        logger.exception("Motor oficial do score B3 falhou no dossiê de %s", ticker)
        degradacoes.append({
            "codigo": "tela.b3.dossie_nota_das_trilhas",
            "mensagem": (f"Motor oficial do score indisponível ({type(exc).__name__}): "
                         f"a nota de {ticker} é a média das seis trilhas, não a "
                         "nota do ranking."),
        })
    out = linha.iloc[0].copy()
    out.attrs["degradacoes"] = degradacoes
    return out, f"{referencia} · {len(pares)} empresa(s)"


def _metric_decimal_pct(mult: pd.Series, key: str) -> str:
    try:
        value = float(mult.get(key))
        if not np.isfinite(value):
            return "—"
        return f"{value * 100.0:.1f}%"
    except (TypeError, ValueError):
        return "—"


def _fmt_pontuacao(valor: object) -> str:
    """Nota em `x/100`, ou travessão quando não há nota — nunca "nan/100"."""
    try:
        numero = float(valor)
    except (TypeError, ValueError):
        return "—"
    return f"{numero:.1f}/100" if np.isfinite(numero) else "—"


def _render_b3_dossie(ticker: str, score_row: pd.Series, referencia: str) -> None:
    from core.b3_company_score import FACTOR_TRACKS, TRACK_LABELS, classification
    from core.dossie_b3 import (
        SEVERIDADE_COBERTURA,
        SEVERIDADE_CONTEXTO,
        SEVERIDADE_RISCO,
        agrupa_flags_por_severidade,
        build_dossie,
    )

    dossie = build_dossie(ticker)
    label, tipo = classification(score_row.get("score"))
    badge_status(label, tipo)
    st.caption(f"Classificação quantitativa relativa a {referencia}.")
    detalhe_tecnico(
        f"Classificação relativa a {referencia}, com "
        f"{float(score_row.get('coverage', 0)):.0f}% de cobertura. "
        "Ausências ficam neutras e reduzem a cobertura.",
        codigo="b3.dossie_cobertura", entidade=ticker)
    # B3-02: o aviso de atualidade sai ao lado do score, e não só na lista de
    # limitações, porque é o score que fica velho. Em 04/10/2026, 344 dos 358
    # scores mensuráveis usavam o TTM até o 2026T1 com o 2026T2 já no banco.
    _aviso_atualidade = (dossie.get("atualidade") or {}).get("aviso")
    if _aviso_atualidade:
        aviso_lacuna(_aviso_atualidade, codigo="tela.b3.score_desatualizado",
                     entidade=ticker)

    if dossie.get("erro"):
        aviso_lacuna(f"Dossiê determinístico indisponível: {dossie['erro']}",
                     codigo="tela.b3.dossie_indisponivel", entidade=ticker)
    else:
        # `red_flags` carrega três coisas distintas; o componente tem de
        # distinguir. `st.warning` para todas fazia 426 de 426 empresas
        # abrirem a tela em amarelo, sendo que só 8 têm risco confirmado.
        # A classificação vem de core.dossie_b3 (fonte única) — não repetir
        # o teste de prefixo aqui.
        _grupos = agrupa_flags_por_severidade(dossie.get("red_flags"))
        for flag in _grupos[SEVERIDADE_RISCO]:
            st.warning(flag)
        for flag in _grupos[SEVERIDADE_CONTEXTO]:
            st.info(flag)
        for flag in _grupos[SEVERIDADE_COBERTURA]:
            aviso_lacuna(flag, codigo="tela.b3.dossie_cobertura_limitada",
                         entidade=ticker)

    fortes: list[str] = []
    invalidacoes: list[str] = []
    trilhas = _trilhas_observadas(score_row, TRACK_LABELS)
    for trilha, valor in trilhas:
        if valor >= 65:
            fortes.append(f"{trilha}: {valor:.1f}/100 entre os pares.")
        elif valor < 40:
            invalidacoes.append(f"{trilha}: {valor:.1f}/100; exige acompanhamento.")
    if not trilhas:
        fortes.append("Sem nota por trilha para este ativo.")
    elif not fortes:
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
            "Pontuação": _dq.to_float(score_row.get(coluna)),
            "Cobertura (%)": float(score_row.get(f"coverage_{key}", 0) or 0),
            "Indicadores B3": ", ".join(nome for nome, _ in FACTOR_TRACKS[key]),
        })
    st.markdown("**Critérios avançados da classificação**")
    st.dataframe(pd.DataFrame(criterios), hide_index=True, width="stretch")

    if not dossie.get("erro"):
        juros = dossie.get("sensibilidade_juros", {})
        if juros.get("regra"):
            st.markdown("**Sensibilidade aos juros brasileiros**")
            st.caption(juros["regra"])
        eventos = dossie.get("eventos_societarios", {}).get("eventos", [])
        if eventos:
            st.markdown("**Eventos societários recentes (CVM)**")
            st.dataframe(pd.DataFrame(eventos[:6]), hide_index=True, width="stretch")


def _trilhas_observadas(score_row, labels: dict[str, str]) -> list[tuple[str, float]]:
    """(rótulo, nota) das trilhas com nota; ausência fica de fora.

    0,0 é a pior posição entre os pares (percentil mínimo com cobertura
    cheia), não ausência — ``float(x or 50)`` a exibia como nota neutra no
    radar, na tese e na tabela de critérios.
    """
    out = []
    for coluna, rotulo in labels.items():
        valor = _dq.to_float(score_row.get(coluna))
        if valor is not None:
            out.append((rotulo, valor))
    return out


def _render_radar_trilhas(ticker: str, tracks: list[tuple[str, float]]) -> None:
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
    st.plotly_chart(fig, width="stretch", key=f"b3_radar_{ticker}")


def _render_b3_score_dashboard(
    ticker: str,
    mult: pd.Series,
    df_set: pd.DataFrame,
) -> None:
    from core.b3_company_score import TRACK_LABELS, classification

    with st.spinner("Calculando pontuação relativa e dossiê…"):
        score_row, referencia = _b3_peer_scores(ticker, mult, df_set)
    _avisar_degradacoes(score_row, entidade=ticker)
    label, tipo = classification(score_row.get("score"))

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        card_metrica("Pontuação fundamentalista", _fmt_pontuacao(score_row.get("score")))
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

    tracks = _trilhas_observadas(score_row, TRACK_LABELS)
    if tracks:
        _render_radar_trilhas(ticker, tracks)
    st.caption(f"Referência da comparação: {referencia}.")
    detalhe_tecnico(f"Metodologia B3 {SCORE_VERSION}.", codigo="b3.metodologia",
                    entidade=ticker)
    aviso_escala_do_score()
    # A-152: `validation_readiness` ja apurava o estado e NOMEAVA os
    # bloqueadores; o unico chamador era o relatorio de confianca, que o
    # usuario nao le. Motor de diagnostico sem porta de entrada e decoracao.
    detalhe_tecnico(validacao_b3().texto, codigo="b3.validacao_motor",
                    entidade=ticker)
    # A-154: por quantos ativos esta nota fala.
    aviso_cobertura_do_universo("b3")

    with st.expander("📄 Dossiê, classificação e critérios avançados"):
        _render_b3_dossie(ticker, score_row, referencia)


def _universo_b3_de(carrega_setores, carrega_multiplos) -> tuple[str, ...]:
    """Une as duas fontes de universo, tolerando falha de qualquer uma delas.

    Separada do wrapper cacheado de proposito: assim o teste exercita a regra
    passando as leituras como argumento, sem depender de monkeypatch em modulo
    compartilhado (que vaza entre testes) nem do cache do Streamlit (que o CI
    desliga).
    """
    universo: set[str] = set()
    try:
        df_set = carrega_setores()
        if not df_set.empty and "ticker" in df_set.columns:
            universo |= {str(t).strip().upper() for t in df_set["ticker"].dropna()}
    except Exception as exc:  # noqa: BLE001 — leitura opcional
        logger.warning("universo B3: load_setores falhou (%s)", exc)
    try:
        df_mult = carrega_multiplos()
        col = next((c for c in df_mult.columns if c.lower() == "ticker"), None)
        if col:
            universo |= {str(t).strip().upper() for t in df_mult[col].dropna()}
    except Exception as exc:  # noqa: BLE001 — leitura opcional
        logger.warning("universo B3: load_multiplos_todos falhou (%s)", exc)
    universo.discard("")
    return tuple(sorted(universo))


@st.cache_data(ttl=3600, show_spinner=False)
def _universo_b3_tickers() -> tuple[str, ...]:
    """Tickers que este módulo sabe analisar: cadastro de setores ∪ fundamentos.

    A união importa. Em 06/09/2026 havia 3 tickers com fundamentos publicados
    (ENAT3, NATU3, PETZ3) ausentes do cadastro de setores; validar só pelo
    cadastro bloquearia empresas que têm dado. Se as duas leituras falharem, o
    universo volta vazio e a validação se desliga — um problema de banco não
    pode virar "ticker inexistente".
    """
    return _universo_b3_de(_db.load_setores, _db.load_multiplos_todos)


def _tab_analise(df_set: pd.DataFrame) -> None:
    # ── Input ─────────────────────────────────────────────────────────────────
    default_tk = st.session_state.get("b3_ticker_sel", "")
    col_inp, col_btn = st.columns([4, 1])
    with col_inp:
        ticker_raw = st.text_input(
            "Ticker ou nome da empresa", value=default_tk,
            key="b3_ticker_input",
            placeholder="Ex.: PETR4, Banco do Brasil, WEG",
        )
    with col_btn:
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("Analisar", type="primary", width="stretch",
                     key="b3_btn_analisar") and ticker_raw.strip():
            # Nome ou ticker: um único candidato vai direto para a análise;
            # vários (ex.: PETR3/PETR4, ou "banco") viram uma escolha abaixo.
            # Sem candidato, o texto segue como ticker e cai na validação do
            # universo — que já sugere tickers parecidos.
            candidatos = buscar_empresas(ticker_raw, df_set, _universo_b3_tickers())
            if len(candidatos) == 1:
                st.session_state["b3_ticker_sel"] = candidatos[0][0]
                st.session_state.pop("b3_busca_candidatos", None)
            elif candidatos:
                st.session_state["b3_busca_candidatos"] = candidatos
                st.session_state["b3_ticker_sel"] = ""
            else:
                st.session_state["b3_ticker_sel"] = normalizar_ticker(ticker_raw)
                st.session_state.pop("b3_busca_candidatos", None)
            st.rerun()

    # Seleção vinda de outro caminho (card do setor) descarta a escolha pendente.
    candidatos = st.session_state.get("b3_busca_candidatos") or []
    if candidatos and st.session_state.get("b3_ticker_sel"):
        st.session_state.pop("b3_busca_candidatos", None)
        candidatos = []
    if candidatos:
        st.caption(f"{len(candidatos)} empresas encontradas — escolha uma:")
        cols = st.columns(min(len(candidatos), 4))
        for i, (c_tk, c_nome) in enumerate(candidatos):
            rotulo = c_tk if c_nome == c_tk else f"{c_tk} — {c_nome}"
            if cols[i % len(cols)].button(rotulo, key=f"b3_cand_{c_tk}",
                                          width="stretch"):
                st.session_state["b3_ticker_sel"] = c_tk
                st.session_state.pop("b3_busca_candidatos", None)
                st.rerun()

    tk = normalizar_ticker(st.session_state.get("b3_ticker_sel", ""))
    if not tk:
        if not candidatos:
            st.info("Digite um ticker ou o nome da empresa e clique em "
                    "**Analisar**.", icon="🔍")
        return

    # ── Ticker conhecido? ─────────────────────────────────────────────────────
    # Sem esta checagem qualquer texto digitado virava um cabeçalho de empresa
    # ("BBSA3 — BBSA3") e a ausência de dados era reportada depois como falha de
    # configuração de banco. Erro de digitação tem que parecer erro de digitação.
    universo = _universo_b3_tickers()
    if universo and tk not in universo:
        sugestoes = difflib.get_close_matches(tk, universo, n=4, cutoff=0.6)
        st.error(
            f"**{tk}** não está entre os {len(universo)} papéis da B3 que este "
            "módulo cobre. O banco está acessível — o que não existe é o ticker.",
            icon="🚫",
        )
        if sugestoes:
            st.caption("Talvez você queira: " + " · ".join(f"`{x}`" for x in sugestoes))
        return

    # ── Header ────────────────────────────────────────────────────────────────
    preco, preco_status = _preco_atual_com_status(tk)
    info_row = (df_set[df_set["ticker"] == tk].iloc[0]
                if not df_set.empty and tk in df_set["ticker"].values else None)
    nome_emp = info_row["nome_empresa"] if info_row is not None else tk
    setor    = info_row["SETOR"]    if info_row is not None else "—"
    subsetor = info_row["SUBSETOR"] if info_row is not None else "—"

    col_logo, col_info, col_preco = st.columns([1, 5, 2])
    with col_logo:
        # Sem <img> cru via unsafe_allow_html — ver achado A-012.
        render_company_logo(tk, _logo_url(tk), size=64)
    with col_info:
        st.markdown(
            f'<h2 style="font-size:1.60rem;font-weight:800;color:var(--app-text);margin:0 0 4px;">'
            f'{tk} — {nome_emp}</h2>'
            f'<div style="font-size:0.78rem;color:var(--app-subtle);">{setor} · {subsetor}</div>',
            unsafe_allow_html=True,
        )
    with col_preco:
        preco_str = f"R$ {preco:,.2f}" if preco else "—"
        if preco_status == "falha_rede":
            preco_legenda = "Cotação indisponível"
            aviso_lacuna("Falha de rede/timeout ao consultar o yfinance.",
                         codigo="tela.b3.cotacao_falha_rede", entidade=tk)
        elif preco_status == "sem_dado":
            preco_legenda = "Cotação indisponível"
            aviso_lacuna("Sem cotação disponível no yfinance.",
                         codigo="tela.b3.cotacao_sem_dado", entidade=tk)
        else:
            preco_legenda = "Cotação"
            detalhe_tecnico("Cotação vinda do yfinance.",
                            codigo="b3.cotacao_fonte", entidade=tk)
        st.markdown(
            f'<div style="text-align:right;padding-top:8px;">'
            f'<div style="font-size:1.60rem;font-weight:800;color:{cor_token(_COR_POS)};">{preco_str}</div>'
            f'<div style="font-size:0.68rem;color:var(--app-subtle);">{preco_legenda}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

    st.markdown("<br>", unsafe_allow_html=True)

    # ── Carregamento de dados ─────────────────────────────────────────────────
    with st.spinner(f"Carregando dados de {tk}…"):
        df_fin       = _db.load_demonstracoes(tk)
        df_mult_hist = _db.load_multiplos_historico(tk)
        # Snapshot limpo do market.* (sem reconciliação por scraping); DY e
        # Payout já vêm nele.
        mult = _db.load_multiplos(tk)
        fontes_recon = {k: "market" for k in getattr(mult, "index", [])
                        if k not in ("Ticker", "data")}
        df_yf_divs, fonte_divs = _dividendos_anuais_market_first(tk)
        df_precos    = _yf_precos(tk)

    # Provento maior que o preço do dia deixa o fator de ajuste negativo e o
    # histórico anterior noutra escala: o gráfico e o retorno anual usam só o
    # trecho depois do último preço ≤ 0.
    corte_preco = (ultimo_preco_nao_positivo(df_precos["Data"], df_precos["Preco"])
                   if not df_precos.empty else None)
    if corte_preco is not None:
        df_precos = df_precos[df_precos["Data"] > corte_preco]
        aviso_lacuna(
            f"Preço ajustado do yfinance chega a ≤ 0 até {corte_preco:%d/%m/%Y} "
            "(provento maior que o preço do dia): o histórico anterior foi "
            "omitido do gráfico e do retorno anual.",
            codigo="tela.b3.preco_ajustado_quebrado", entidade=tk)

    sem_banco = df_fin.empty and mult.empty

    # ══════════════════════════════════════════════════════════════════════════
    # SEÇÃO 1 — Preço da Ação
    # ══════════════════════════════════════════════════════════════════════════
    if not df_precos.empty:
        _sec_hdr("📉 Preço da Ação")
        detalhe_tecnico("Fonte: yfinance (histórico diário; ver nota de arquitetura "
                        "A-011 — market.historical_prices só guarda 1 candle/mês).",
                        codigo="b3.preco_fonte", entidade=tk)

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
        st.plotly_chart(fig_preco, width="stretch",
                        config={"displayModeBar": False}, key=f"b3_preco_{tk}_{sel_per}")

        # Retorno anual
        _sec_hdr("📊 Retorno Anual do Preço")
        ret = retornos_anuais(df_precos["Data"], df_precos["Preco"])
        # "2026 (até 28/10)": o ano aberto é acumulado no ano, não o do ano.
        ret["Ano"]      = ret["Rotulo"]
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
        st.plotly_chart(fig_ret, width="stretch",
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
            fonte = ""
            if field == "Dividendos" and (df_fin.empty or "Dividendos" not in df_fin.columns):
                # Soma de proventos por ano civil: o ano em curso entraria
                # no CAGR como queda.
                df_src = sem_ano_em_curso(df_yf_divs, "Data")
                fonte = f" · fonte: {fonte_divs}"
            cres = _cagr(df_src, field)
            g = cres.taxa if cres is not None else None
            subtitulo = (cres.legenda() if cres is not None else "Sem histórico") + fonte
            with col:
                st.markdown(
                    _ind_card(lbl, _fg(g), subtitulo,
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
            disp_dre = [(coluna, rotulo) for coluna, rotulo in cands_dre if coluna in df_fin.columns]
            if disp_dre:
                opcoes = [rotulo for _, rotulo in disp_dre]
                deflt  = [x for x in ("Receita Líquida", "Lucro Líquido") if x in opcoes]
                sel    = st.multiselect("Indicadores", opcoes, default=deflt or opcoes[:2],
                                        key=f"b3_dre_sel_{tk}")
                if sel:
                    lbl2col  = {rotulo: coluna for coluna, rotulo in disp_dre}
                    cols_sel = [lbl2col[rotulo] for rotulo in sel if rotulo in lbl2col]
                    plot     = df_fin[["Data"] + cols_sel].copy()
                    for c in cols_sel:
                        plot[c] = pd.to_numeric(plot[c], errors="coerce")
                    melt = plot.melt("Data", value_vars=cols_sel,
                                     var_name="Indicador", value_name="Valor")
                    melt["Indicador"] = melt["Indicador"].map({coluna: rotulo for coluna, rotulo in disp_dre})
                    fig = px.line(melt, x="Data", y="Valor", color="Indicador", markers=True,
                                  color_discrete_sequence=[
                                      _COR_POS, _COR_INF, _COR_ALT, _COR_NEG,
                                      "#9B59B6", "#E67E22", _COR_NEU])
                    fig.update_layout(**_plot_layout(340))
                    fig.update_yaxes(title="R$ (valores absolutos)")
                    st.plotly_chart(fig, width="stretch",
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
                    st.plotly_chart(fig_dv, width="stretch",
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
                        pm[c] = pm[c] * 100.0  # decimal → % (ver _PCT_DECIMAL)
                    melt_m = pm.melt("Data", value_vars=sel_cols,
                                      var_name="Indicador", value_name="Valor (%)")
                    melt_m["Indicador"] = melt_m["Indicador"].str.replace("_", " ")
                    fig_m = px.bar(melt_m, x="Data", y="Valor (%)", color="Indicador",
                                   barmode="group",
                                   color_discrete_sequence=[
                                       _COR_POS, _COR_INF, _COR_ALT, _COR_NEG,
                                       "#9B59B6", "#E67E22"])
                    fig_m.update_layout(**_plot_layout(300))
                    st.plotly_chart(fig_m, width="stretch",
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
            st.plotly_chart(fig_fc, width="stretch",
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
                st.plotly_chart(fig_cap, width="stretch",
                                config={"displayModeBar": False}, key=f"b3_cap_{tk}")

    elif df_precos.empty:
        if df_precos.attrs.get("load_error") == "falha_rede":
            st.info("O histórico de preços não está disponível no momento.")
            aviso_lacuna(
                "Não foi possível carregar o histórico de preços: falha de "
                "rede/timeout ao consultar a yfinance. Não é um problema de "
                "configuração de banco.",
                codigo="tela.b3.historico_precos_falha_rede", entidade=tk)
        elif sem_banco:
            st.info(f"**{tk}** ainda não tem dados publicados para análise.")
            aviso_lacuna(
                f"{tk} é um ticker conhecido, mas não há preço nem "
                "fundamentos publicados para ele. Costuma ser papel de baixa "
                "liquidez ou recém-listado, ainda sem safra de dados.",
                codigo="tela.b3.ticker_sem_dados", entidade=tk)
        else:
            st.info("O gráfico de preço não está disponível no momento.")
            aviso_lacuna(
                f"Sem histórico de preços para {tk} na yfinance. Os "
                "fundamentos seguem vindo do banco — só o gráfico de "
                "preço ficou vazio.",
                codigo="tela.b3.historico_precos_vazio", entidade=tk)

    # ══════════════════════════════════════════════════════════════════════════
    # SEÇÃO 3 — Múltiplos Fundamentalistas (agrupados)
    # ══════════════════════════════════════════════════════════════════════════
    _sec_hdr("📐 Rentabilidade")
    inds_rent = _build_indicators(mult, fontes=fontes_recon, grupo="rentabilidade")
    if any(v != "—" for _, v, _, _ in inds_rent):
        _render_cards(inds_rent, n_cols=3)
    else:
        st.caption("Dados de rentabilidade não disponíveis.")
        aviso_lacuna("Indicadores de rentabilidade sem dado para o ativo.",
                     codigo="tela.b3.indicadores_rentabilidade_ausentes", entidade=tk)

    _sec_hdr("💹 Valuation")
    inds_val = _build_indicators(mult, fontes=fontes_recon, grupo="valuation")
    if any(v != "—" for _, v, _, _ in inds_val):
        _render_cards(inds_val, n_cols=4)
    else:
        st.caption("Dados de valuation não disponíveis.")
        aviso_lacuna("Indicadores de valuation sem dado para o ativo.",
                     codigo="tela.b3.indicadores_valuation_ausentes", entidade=tk)

    _sec_hdr("🏗️ Estrutura de Capital")
    inds_est = _build_indicators(mult, fontes=fontes_recon, grupo="estrutura")
    if any(v != "—" for _, v, _, _ in inds_est):
        _render_cards(inds_est, n_cols=3)
    else:
        st.caption("Dados de estrutura não disponíveis.")
        aviso_lacuna("Indicadores de estrutura sem dado para o ativo.",
                     codigo="tela.b3.indicadores_estrutura_ausentes", entidade=tk)

    _sec_hdr("🏆 Score e critérios de avaliação")
    _render_b3_score_dashboard(tk, mult, df_set)

    _sec_hdr("🧭 Relatório do analista")
    relatorio = secao_relatorio(tk, str(nome_emp or "") or None,
                                str(setor or "") or None, mercado="b3")

    render_chat_ativo(
        mercado="b3", ticker=tk, nome=str(nome_emp or ""), accent="#4A9EFF",
        build_context=lambda pergunta: build_b3_ativo_context(
            tk, user_question=pergunta, nome=str(nome_emp or ""),
            setor=str(setor or ""), subsetor=str(subsetor or ""),
            preco=preco, preco_status=preco_status, mult=mult, df_fin=df_fin,
            fontes=fontes_recon, relatorio=relatorio,
        ),
    )


# ══════════════════════════════════════════════════════════════════════════════
# RENDER PRINCIPAL
# ══════════════════════════════════════════════════════════════════════════════

def render() -> None:
    render_market_css()
    st.markdown(_CSS, unsafe_allow_html=True)

    # A idade sai de `updated_at` de `market.calculated_metrics`, NÃO da coluna
    # `data` do quadro de múltiplos: ali `data` é 31/12 do exercício de
    # referência, uma data contábil que fica no futuro o ano corrente inteiro --
    # medir por ela deu -121 dias e aprovaria qualquer atraso para sempre.
    frescor = frescor_da_vitrine("b3")
    from core.frescor import resumo_curto

    container_pagina(
        "Empresas B3",
        "Análise fundamentalista e construção quantitativa de portfólios brasileiros.",
        "🏢",
        metadados=[("Mercado", "B3 · Brasil")],
    )
    if frescor:
        detalhe_tecnico(f"Vitrine B3: {resumo_curto(frescor)}.",
                        codigo="b3.vitrine_idade")
    selo_de_frescor("b3", frescor)
    # O selo acima mede a idade da PUBLICAÇÃO. Uma vitrine publicada ontem
    # pode carregar um trimestre a menos (B3-02), e é isso que o aviso abaixo
    # mede: cobertura do universo e de qual trimestre é a base do score.
    aviso_atualidade_trimestral_b3()

    with st.spinner("Carregando lista de empresas…"):
        df_set = _db.load_setores()
    # Captura a proveniência ANTES de qualquer filtro (attrs pode não sobreviver
    # a operações de indexação/reset_index) — ver core.b3_data.load_setores.
    fallback_legado = bool(getattr(df_set, "attrs", {}).get("fallback_legado"))
    df_set = _so_acoes(df_set)

    if df_set.empty:
        aviso_lacuna(
            "Cadastro de setores vazio — o app não conseguiu ler o banco. "
            "Confira " + VAR_CONEXAO + ".",
            codigo="tela.b3.cadastro_setores_vazio_cabecalho")
    elif fallback_legado:
        aviso_lacuna(
            "Taxonomia setorial via fonte legada (`public.setores`, banco "
            "resolvido separadamente do restante do app) — a fonte primária "
            "(`market.*`) falhou ou está vazia. Classificação de setor/subsetor "
            "pode divergir/estar desatualizada em relação às demais telas.",
            codigo="tela.b3.taxonomia_fonte_legada")

    active = render_market_tabs(state_key="b3_active_tab", key_prefix="b3",
                                itens=NAV_ITEMS_B3)

    if active == 0:
        _tab_empresas(df_set)
    elif active == 1:
        _tab_analise(df_set)
    elif active == 2:
        from views import portfolio_b3

        portfolio_b3.render(show_header=False)
    elif active == 3:
        from views import analise_portfolio_b3

        analise_portfolio_b3.render(show_header=False)
    else:
        from views import carteira20_b3

        carteira20_b3.render(show_header=False, cadastro=df_set)
