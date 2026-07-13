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
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

API_BASE = "https://brapi.dev/api/quote"
FII_API_BASE = "https://brapi.dev/api/v2/fii"
SOURCE_NAME = "brapi.dev"

FII_V2_ENDPOINTS = frozenset({
    "list", "indicators", "indicators/history", "historical", "reports",
    "properties", "properties/history", "portfolio", "portfolio/history",
    "dividends", "financials", "annual-reports",
})


class BrapiError(Exception):
    """Falha de rede/HTTP na brapi."""


class BrapiRateLimited(BrapiError):
    """Falha transitória de limite ou autenticação do provedor."""

    def __init__(self, message: str, *, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class BrapiAuthError(BrapiError):
    """Token ausente, inválido, inativo ou sem permissão para o endpoint."""


def is_rate_limited(exc: Exception) -> bool:
    return isinstance(exc, BrapiRateLimited)


@dataclass(frozen=True)
class FiiApiResponse:
    """Resposta Brapi Pro com metadados suficientes para auditoria."""

    payload: dict
    endpoint: str
    symbols: tuple[str, ...]
    params: dict[str, Any]
    status_code: int
    headers: dict[str, str]
    collected_at: datetime


_AUDIT_HEADERS = ("etag", "last-modified", "date", "content-type", "content-length",
                  "x-ratelimit-limit", "x-ratelimit-remaining", "retry-after")


def _retry_after(headers) -> float | None:
    try:
        return max(float(headers.get("Retry-After")), 0.0)
    except (TypeError, ValueError):
        return None


def _token() -> str:
    tok = os.getenv("BRAPI_TOKEN", "").strip()
    if tok:
        return tok
    try:
        from core.config import settings  # importa config → load_dotenv() popula os.environ
        v = str(getattr(settings, "BRAPI_TOKEN", "") or "").strip()
        if v:
            return v
        # settings não declara BRAPI_TOKEN, mas o import acima carregou o .env:
        # re-checa os.environ. Sem isto, processos que não importaram core.config
        # antes (scripts, testes, jobs avulsos) chamavam a brapi ANÔNIMOS —
        # 401/404 espúrios mesmo com token pago configurado no .env.
        return os.getenv("BRAPI_TOKEN", "").strip()
    except Exception:
        return ""


def _to_float(v):
    try:
        x = float(v)
        return x if x == x and x not in (float("inf"), float("-inf")) else None
    except (TypeError, ValueError):
        return None


# ── Parsers puros ─────────────────────────────────────────────────────────────

def dedup_cash_dividends(items: list[dict]) -> list[dict]:
    """
    Remove ecos de fonte secundária do cashDividends da brapi. Em empresas
    multi-classe (CEBR5/6, BRSR5/6, UNIP5/6...) a brapi mescla no feed de CADA
    classe as linhas de TODAS as classes vindas de uma fonte CSV secundária,
    carimbadas com o ISIN do próprio ticker (assetIssued NÃO discrimina a
    classe) e marcadas em remarks ('csv:payment_date_estimated',
    'unconfirmed-by-third-party'). A mesma fonte também repete o evento
    parcelado ou em escala errada. Regra: quando a mesma (data-ex, label) tem
    entrada confirmada (remarks vazio), as não-confirmadas são eco e caem;
    sem par confirmado, permanecem (não perde evento que só a CSV conhece).
    """
    def _key(it: dict):
        raw = it.get("lastDatePrior") or it.get("approvedOn")
        if not raw:
            return None
        return (str(raw)[:10], str(it.get("label") or "").strip().upper())

    confirmadas = {k for k in (_key(it) for it in items
                               if not str(it.get("remarks") or "").strip()) if k}
    return [it for it in items
            if not (str(it.get("remarks") or "").strip() and _key(it) in confirmadas)]


def parse_cash_dividends(quote: dict) -> list[dict]:
    """Lista de {date: datetime, rate: float, label: str} ordenada por data."""
    out: list[dict] = []
    items = dedup_cash_dividends(
        ((quote or {}).get("dividendsData") or {}).get("cashDividends") or [])
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
    # 429 (rate limit) e 401/403 (throttling transitório do plano Pro, visto em
    # cargas grandes) são RETENTÁVEIS → sobem como BrapiRateLimited p/ o backoff
    # retentar em vez de descartar o ativo (senão FIIs válidos somem do universo).
    if resp.status_code in (429, 401, 403):
        raise BrapiRateLimited(f"HTTP {resp.status_code}")
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


def fetch_fii_v2_response(endpoint: str,
                          symbols: list[str] | tuple[str, ...] | str | None = None, *,
                          params: dict | None = None, timeout: int = 60) -> FiiApiResponse:
    """Consulta um endpoint dedicado e mantém metadados sem expor o token."""
    import requests
    endpoint = str(endpoint).strip("/")
    if endpoint not in FII_V2_ENDPOINTS:
        raise ValueError(f"endpoint FII v2 não permitido: {endpoint}")
    if symbols is None:
        clean = []
    elif isinstance(symbols, str):
        clean = [part.strip().upper().replace(".SA", "") for part in symbols.split(",")]
    else:
        clean = [str(part).strip().upper().replace(".SA", "") for part in symbols]
    clean = list(dict.fromkeys(part for part in clean if part))
    if endpoint != "list" and not clean:
        raise ValueError("symbols deve conter entre 1 e 20 FIIs")
    if len(clean) > 20:
        raise ValueError("symbols deve conter no máximo 20 FIIs")
    query = {**({"symbols": ",".join(clean)} if clean else {}), **(params or {})}
    headers = {"User-Agent": "DashboardFinanceiro/1.0 (+fii-v4)"}
    tok = _token()
    if tok:
        headers["Authorization"] = f"Bearer {tok}"
    try:
        response = requests.get(f"{FII_API_BASE}/{endpoint}", params=query,
                                headers=headers, timeout=timeout)
    except Exception as exc:
        raise BrapiError(str(exc)) from exc
    if response.status_code == 429:
        raise BrapiRateLimited(f"HTTP {response.status_code}",
                               retry_after=_retry_after(response.headers))
    if response.status_code in (401, 403):
        try:
            error_code = str((response.json() or {}).get("code") or "")
        except Exception:
            error_code = ""
        raise BrapiAuthError(f"HTTP {response.status_code} {error_code}".strip())
    if response.status_code != 200:
        raise BrapiError(f"FII v2 {endpoint}: HTTP {response.status_code}")
    try:
        payload = response.json()
    except Exception as exc:
        raise BrapiError(f"FII v2 {endpoint}: json inválido: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("error"):
        raise BrapiError(f"FII v2 {endpoint}: resposta inválida")
    response_headers = getattr(response, "headers", {}) or {}
    audit_headers = {name: str(response_headers[name]) for name in _AUDIT_HEADERS
                     if name in response_headers}
    return FiiApiResponse(
        payload=payload, endpoint=endpoint, symbols=tuple(clean), params=dict(query),
        status_code=response.status_code, headers=audit_headers,
        collected_at=datetime.now(timezone.utc),
    )


def fetch_fii_v2(endpoint: str,
                 symbols: list[str] | tuple[str, ...] | str | None = None, *,
                 params: dict | None = None, timeout: int = 60) -> dict:
    """Compatibilidade: retorna somente o payload do endpoint dedicado."""
    return fetch_fii_v2_response(endpoint, symbols, params=params, timeout=timeout).payload


def fetch_fii_v2_all_pages(endpoint: str,
                           symbols: list[str] | tuple[str, ...] | str | None = None, *,
                           params: dict | None = None, timeout: int = 60,
                           max_pages: int = 100) -> FiiApiResponse:
    """Percorre a paginação e devolve um único envelope auditável."""
    requested = {**(params or {})}
    first = fetch_fii_v2_response(endpoint, symbols, params=requested, timeout=timeout)
    pagination = first.payload.get("pagination") or {}
    if not pagination.get("hasNextPage"):
        return first
    list_keys = [key for key, value in first.payload.items() if isinstance(value, list)]
    if len(list_keys) != 1:
        raise BrapiError(f"FII v2 {endpoint}: envelope paginado ambíguo")
    data_key = list_keys[0]
    merged = dict(first.payload)
    merged[data_key] = list(first.payload.get(data_key) or [])
    page = int(pagination.get("page") or requested.get("page") or 1)
    total_pages = min(int(pagination.get("totalPages") or page), int(max_pages))
    headers = dict(first.headers)
    collected_at = first.collected_at
    while page < total_pages:
        page += 1
        nxt = fetch_fii_v2_response(
            endpoint, symbols, params={**requested, "page": page}, timeout=timeout)
        merged[data_key].extend(nxt.payload.get(data_key) or [])
        merged["pagination"] = nxt.payload.get("pagination") or merged.get("pagination")
        headers.update(nxt.headers)
        collected_at = max(collected_at, nxt.collected_at)
        if not (nxt.payload.get("pagination") or {}).get("hasNextPage"):
            break
    merged["pagination"] = {**(merged.get("pagination") or {}),
                            "fetchedPages": page, "mergedItems": len(merged[data_key])}
    return FiiApiResponse(
        payload=merged, endpoint=endpoint, symbols=first.symbols,
        # Preserva tambem os parametros materializados pela primeira chamada
        # (inclusive symbols), para que fingerprint e cache sejam por universo.
        params={**first.params, **requested, "pages": f"1-{page}"}, status_code=200,
        headers=headers, collected_at=collected_at,
    )


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
