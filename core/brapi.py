"""
core/brapi.py
Cliente da API brapi.dev (bolsa brasileira) — fonte robusta para backfill
histórico de dividendos/DY e corroboração de P/L.

API JSON pública (sem scraping): GET /api/quote/{ticker}
  ?range=max&interval=1mo&dividends=true&fundamental=true
Token gratuito via header Authorization: Bearer <BRAPI_TOKEN> (env/Secret).
Plano free: 15.000 req/mês, 1 ticker por requisição.

Schema usado (results[0]):
  regularMarketPrice, priceEarnings, earningsPerShare, marketCap,
  dividendsData.cashDividends[].{paymentDate, rate, label},
  historicalDataPrice[].{date(unix), close, adjustedClose}.

Funções puras (parse/cálculo) testáveis sem rede; IO isolado em fetch_quote.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

API_BASE = "https://brapi.dev/api/quote"
SOURCE_NAME = "brapi.dev"


class BrapiError(Exception):
    """Falha de rede/HTTP na brapi."""


class BrapiRateLimited(BrapiError):
    """HTTP 429 — aciona disjuntor/backoff."""


def is_rate_limited(exc: Exception) -> bool:
    return isinstance(exc, BrapiRateLimited)


def _token() -> str:
    tok = os.getenv("BRAPI_TOKEN", "").strip()
    if tok:
        return tok
    try:
        from core.config import settings
        return str(getattr(settings, "BRAPI_TOKEN", "") or "").strip()
    except Exception:
        return ""


def _to_float(v):
    try:
        x = float(v)
        return x if x == x and x not in (float("inf"), float("-inf")) else None
    except (TypeError, ValueError):
        return None


# ── Parsers puros ─────────────────────────────────────────────────────────────

def parse_cash_dividends(quote: dict) -> list[dict]:
    """Lista de {date: datetime, rate: float, label: str} ordenada por data."""
    out: list[dict] = []
    items = ((quote or {}).get("dividendsData") or {}).get("cashDividends") or []
    for it in items:
        rate = _to_float(it.get("rate"))
        raw = it.get("paymentDate") or it.get("lastDatePrior") or it.get("approvedOn")
        if rate is None or not raw:
            continue
        dt = None
        for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%S.000Z",
                    "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(str(raw)[:len("2026-08-20T03:00:00.000Z")], fmt)
                break
            except ValueError:
                continue
        if dt is None:
            try:
                dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00")).replace(tzinfo=None)
            except Exception:
                continue
        out.append({"date": dt, "rate": rate, "label": str(it.get("label") or "")})
    return sorted(out, key=lambda d: d["date"])


def annual_dividends(quote: dict) -> dict[int, float]:
    """{ano: soma de proventos pagos no ano}."""
    agg: dict[int, float] = {}
    for d in parse_cash_dividends(quote):
        agg[d["date"].year] = agg.get(d["date"].year, 0.0) + d["rate"]
    return agg


def annual_year_end_prices(quote: dict) -> dict[int, float]:
    """{ano: último preço de fechamento do ano} a partir de historicalDataPrice."""
    prices: dict[int, float] = {}
    for it in (quote or {}).get("historicalDataPrice") or []:
        ts = it.get("date")
        close = _to_float(it.get("adjustedClose")) or _to_float(it.get("close"))
        if ts is None or close is None or close <= 0:
            continue
        try:
            y = datetime.fromtimestamp(int(ts), tz=timezone.utc).year
        except Exception:
            continue
        prices[y] = close  # iterando em ordem cronológica → fica o último do ano
    return prices


def annual_dy(quote: dict) -> dict[int, float]:
    """
    {ano: dividend yield decimal} = proventos do ano ÷ preço de fim do ano.
    Só inclui anos com proventos > 0 e preço disponível.
    """
    divs = annual_dividends(quote)
    prices = annual_year_end_prices(quote)
    out: dict[int, float] = {}
    for y, total in divs.items():
        px = prices.get(y)
        if px and px > 0 and total > 0:
            dy = total / px
            if 0 < dy <= 0.50:  # faixa coerente (igual data_quality)
                out[y] = dy
    return out


def trailing_dy(quote: dict, asof: datetime | None = None) -> float | None:
    """DY dos últimos 12 meses ÷ preço atual."""
    price = _to_float((quote or {}).get("regularMarketPrice"))
    if not price or price <= 0:
        return None
    ref = asof or datetime.now(timezone.utc).replace(tzinfo=None)
    total = sum(d["rate"] for d in parse_cash_dividends(quote)
                if (ref - d["date"]).days <= 366 and (ref - d["date"]).days >= 0)
    if total <= 0:
        return None
    dy = total / price
    return dy if 0 < dy <= 0.50 else None


def current_fundamentals(quote: dict) -> dict[str, float]:
    """Indicadores atuais corroboráveis pela brapi (escala BD: % em decimal)."""
    out: dict[str, float] = {}
    pl = _to_float((quote or {}).get("priceEarnings"))
    if pl is not None:
        out["P/L"] = pl
    dy = trailing_dy(quote)
    if dy is not None:
        out["DY"] = dy
    return out


# ── IO ────────────────────────────────────────────────────────────────────────

def disponivel() -> bool:
    """True se há token configurado (ou modo de teste dos 4 tickers gratuitos)."""
    return True  # endpoint responde aos 4 tickers free sem token; token amplia o universo


def fetch_quote(ticker: str, range_: str = "max", interval: str = "1mo",
                dividends: bool = True, fundamental: bool = True,
                modules: str | None = None, timeout: int = 45) -> dict | None:
    """
    Busca cotação+histórico+dividendos (+ módulos do Pro) de um ticker.
    Retorna results[0] (dict) ou None. Levanta BrapiRateLimited em 429.
    """
    import requests
    tk = str(ticker).strip().upper().replace(".SA", "")
    params = {
        "range": range_, "interval": interval,
        "dividends": "true" if dividends else "false",
        "fundamental": "true" if fundamental else "false",
    }
    if modules:
        params["modules"] = modules
    headers = {"User-Agent": "DashboardFinanceiro/1.0 (+data-quality)"}
    tok = _token()
    if tok:
        headers["Authorization"] = f"Bearer {tok}"
    try:
        resp = requests.get(f"{API_BASE}/{tk}", params=params, headers=headers, timeout=timeout)
    except Exception as exc:
        raise BrapiError(str(exc)) from exc
    if resp.status_code == 429:
        raise BrapiRateLimited("HTTP 429")
    if resp.status_code != 200:
        logger.warning("brapi %s: HTTP %s", tk, resp.status_code)
        return None
    try:
        data = resp.json()
    except Exception as exc:
        raise BrapiError(f"json inválido: {exc}") from exc
    results = data.get("results") or []
    return results[0] if results else None


# Módulos fundamentalistas do plano Pro (anuais + trimestrais).
PRO_MODULES = (
    "summaryProfile,defaultKeyStatistics,financialData,"
    "incomeStatementHistory,incomeStatementHistoryQuarterly,"
    "balanceSheetHistory,balanceSheetHistoryQuarterly,"
    "cashflowHistory,cashflowHistoryQuarterly"
)


def fetch_quote_full(ticker: str, range_: str = "max", interval: str = "1mo",
                     timeout: int = 60) -> dict | None:
    """Cotação completa do Pro: histórico + dividendos + módulos fundamentalistas."""
    return fetch_quote(ticker, range_=range_, interval=interval,
                       dividends=True, fundamental=True, modules=PRO_MODULES, timeout=timeout)


def fetch_list(timeout: int = 45) -> list[str]:
    """Lista todos os tickers de ações (endpoint /api/quote/list). [] em falha."""
    import requests
    headers = {"User-Agent": "DashboardFinanceiro/1.0 (+data-quality)"}
    tok = _token()
    if tok:
        headers["Authorization"] = f"Bearer {tok}"
    try:
        resp = requests.get("https://brapi.dev/api/quote/list", headers=headers, timeout=timeout)
    except Exception as exc:
        raise BrapiError(str(exc)) from exc
    if resp.status_code == 429:
        raise BrapiRateLimited("HTTP 429")
    if resp.status_code != 200:
        return []
    try:
        data = resp.json()
    except Exception:
        return []
    out: list[str] = []
    for it in (data.get("stocks") or []):
        tk = (it.get("stock") if isinstance(it, dict) else str(it)) or ""
        tk = str(tk).strip().upper().replace(".SA", "")
        if tk:
            out.append(tk)
    return out


def fetch_fund_list(timeout: int = 45) -> list[str]:
    """
    Lista candidatos a FII (endpoint /api/quote/list?type=fund), ordenados por
    volume desc (mais líquidos primeiro). Inclui ETFs — a classificação FII×ETF
    é feita depois pelo setor do ativo (data_pipeline.market.fii.is_fii).
    """
    import requests
    headers = {"User-Agent": "DashboardFinanceiro/1.0 (+data-quality)"}
    tok = _token()
    if tok:
        headers["Authorization"] = f"Bearer {tok}"
    params = {"type": "fund", "sortBy": "volume", "sortOrder": "desc"}
    if tok:
        params["token"] = tok
    try:
        resp = requests.get("https://brapi.dev/api/quote/list",
                            params=params, headers=headers, timeout=timeout)
    except Exception as exc:
        raise BrapiError(str(exc)) from exc
    if resp.status_code == 429:
        raise BrapiRateLimited("HTTP 429")
    if resp.status_code != 200:
        return []
    try:
        data = resp.json()
    except Exception:
        return []
    out: list[str] = []
    for it in (data.get("stocks") or []):
        tk = (it.get("stock") if isinstance(it, dict) else str(it)) or ""
        tk = str(tk).strip().upper().replace(".SA", "")
        if tk:
            out.append(tk)
    return out
