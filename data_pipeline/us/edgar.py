"""
data_pipeline/us/edgar.py
Provedor SEC EDGAR — fundamentos de domínio público, sem licença restritiva.

Implementa FundamentalsProvider (mesma interface do FmpProvider), então a
ingestão, o score, o dossiê, a carteira e o backtest não mudam.

Requisitos da SEC (não são opcionais):
  - User-Agent identificando quem consome, com e-mail de contato (sem ele → 403).
    Configurado em SEC_USER_AGENT. Não é segredo — é identificação, e por isso
    pode aparecer em log, ao contrário de uma API key.
  - Máximo de 10 requisições por segundo (RateLimiter abaixo).

Endpoints:
  - www.sec.gov/files/company_tickers.json          → universo (ticker ↔ CIK)
  - data.sec.gov/submissions/CIK##########.json     → perfil/metadados
  - data.sec.gov/api/xbrl/companyfacts/CIK####.json → todos os fatos XBRL
"""
from __future__ import annotations

import logging
import time
from typing import Any, Callable, Optional

from data_pipeline.us import edgar_facts as ef
from data_pipeline.us.identity import normalize_cik, normalize_symbol
from data_pipeline.us.providers import (
    Budget, FundamentalsProvider, MissingCredentialError, ProviderError, RateLimiter,
)

logger = logging.getLogger("us_edgar")

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
# Variante com a bolsa de listagem: {"fields":["cik","name","ticker","exchange"],
#  "data":[[320193,"Apple Inc.","AAPL","Nasdaq"],...]}
TICKERS_EXCHANGE_URL = "https://www.sec.gov/files/company_tickers_exchange.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"

# Bolsas aceitas na análise principal (EDGAR usa estes rótulos em `exchanges`).
_EXCHANGE_MAP = {"NYSE": "NYSE", "NASDAQ": "NASDAQ", "NYSEAMER": "AMEX",
                 "NYSE AMERICAN": "AMEX", "AMEX": "AMEX", "CBOE": "CBOE"}

# Override ticker→CIK para reestruturações onde o mapa oficial da SEC passou a
# apontar para uma holding nova SEM histórico, deixando as demonstrações no CIK
# antigo (a empresa operacional). Curado e documentado — é a camada de
# reconciliação de identidade que o módulo prevê. Ex.: em 2025 a ExxonMobil criou
# "ExxonMobil Holdings Corp" (CIK 2115436, vazia); os 19 anos ficam no 34088.
_CIK_OVERRIDES = {
    "XOM": "0000034088",   # Exxon Mobil Corporation (operacional, com histórico)
}


class EdgarProvider(FundamentalsProvider):
    """Fundamentos via SEC EDGAR. Sem chave; exige User-Agent de contato."""

    def __init__(self, user_agent: str, session: Any = None, rate: int = 8,
                 per: float = 1.0, max_retries: int = 4,
                 budget: Optional[Budget] = None, timeout: float = 30.0,
                 time_fn: Callable[[], float] = time.monotonic,
                 sleep_fn: Callable[[float], None] = time.sleep):
        self.user_agent = (user_agent or "").strip()
        self._session = session
        self.max_retries = max_retries
        self.budget = budget or Budget()
        self.timeout = timeout
        self.sleep_fn = sleep_fn
        # SEC pede ≤ 10 req/s; 8 dá margem.
        self.limiter = RateLimiter(rate=rate, per=per, time_fn=time_fn, sleep_fn=sleep_fn)
        self.calls_made = 0
        self._ticker_map: dict[str, str] | None = None
        self._facts_cache: tuple[str, dict | None] = ("", None)  # (symbol, facts)

    @property
    def session(self):
        if self._session is None:
            import requests
            self._session = requests.Session()
        return self._session

    def _get(self, url: str) -> Any:
        if not self.user_agent:
            raise MissingCredentialError(
                "SEC_USER_AGENT ausente — a SEC exige identificação com e-mail de "
                "contato (ex.: 'Seu Nome seu@email.com'), senão responde 403.")
        self.budget.charge(1)
        headers = {"User-Agent": self.user_agent,
                   "Accept-Encoding": "gzip, deflate"}
        last: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            self.limiter.acquire()
            self.calls_made += 1
            try:
                resp = self.session.get(url, headers=headers, timeout=self.timeout)
            except Exception as exc:  # rede/timeout
                last = exc
                self.sleep_fn(0.5 * (2 ** (attempt - 1)))
                continue
            status = getattr(resp, "status_code", 0)
            if status == 404:
                return None                      # empresa sem fatos XBRL
            if status == 429 or 500 <= status < 600:
                last = ProviderError(f"HTTP {status}")
                self.sleep_fn(1.0 * (2 ** (attempt - 1)))
                continue
            if status != 200:
                raise ProviderError(f"HTTP {status} em {url}")
            try:
                return resp.json()
            except Exception as exc:
                raise ProviderError(f"resposta não-JSON de {url}: {exc}") from exc
        raise ProviderError(f"falha após {self.max_retries} tentativas em {url}: {last}")

    # ── universo / identidade ────────────────────────────────────────────────
    def ticker_map(self) -> dict[str, str]:
        """{symbol: cik10}. Cacheado no provider (1 chamada para todo o universo)."""
        if self._ticker_map is None:
            data = self._get(TICKERS_URL) or {}
            rows = data.values() if isinstance(data, dict) else data
            self._ticker_map = {}
            for r in rows:
                sym = normalize_symbol(r.get("ticker"))
                cik = normalize_cik(r.get("cik_str") or r.get("cik"))
                if sym and cik:
                    self._ticker_map[sym] = cik
        return self._ticker_map

    def get_universe(self, exchanges: list[str]) -> list[dict]:
        """Universo da SEC com bolsa de listagem (company_tickers_exchange.json)."""
        data = self._get(TICKERS_EXCHANGE_URL) or {}
        allow = {e.upper() for e in (exchanges or [])}
        out = []
        if isinstance(data, dict) and "data" in data:
            fields = [str(f).lower() for f in data.get("fields", [])]
            for row in data["data"]:
                r = dict(zip(fields, row))
                sym = normalize_symbol(r.get("ticker"))
                cik = normalize_cik(r.get("cik"))
                exch = _EXCHANGE_MAP.get(str(r.get("exchange") or "").upper())
                if not sym or not cik:
                    continue
                if allow and (exch is None or exch not in allow):
                    continue
                out.append({"symbol": sym, "cik": cik, "name": r.get("name"),
                            "exchangeShortName": exch})
        return out

    def _cik_for(self, symbol: str) -> Optional[str]:
        sym = normalize_symbol(symbol) or ""
        if sym in _CIK_OVERRIDES:      # reestruturação conhecida → CIK operacional
            return _CIK_OVERRIDES[sym]
        return self.ticker_map().get(sym)

    def get_profile(self, symbol: str) -> dict | None:
        cik = self._cik_for(symbol)
        if not cik:
            return None
        sub = self._get(SUBMISSIONS_URL.format(cik=cik))
        if not sub:
            return None
        exchanges = sub.get("exchanges") or []
        exch = None
        for e in exchanges:
            mapped = _EXCHANGE_MAP.get(str(e).upper())
            if mapped:
                exch = mapped
                break
        # perfil no formato que normalize.map_profile entende (chaves estilo FMP)
        return {
            "symbol": normalize_symbol(symbol),
            "cik": cik,
            "companyName": sub.get("name"),
            "exchangeShortName": exch or (exchanges[0] if exchanges else None),
            "industry": sub.get("sicDescription"),
            "sector": sub.get("sicDescription"),   # SIC é a única taxonomia da SEC
            "country": (sub.get("addresses", {}) or {}).get("business", {}).get(
                "stateOrCountry"),
            "currency": "USD",
            "description": sub.get("sicDescription"),
            "website": sub.get("website"),
            "isActivelyTrading": not bool(sub.get("formerNames") and not exchanges),
            "_sic": sub.get("sic"),
            "_former_names": sub.get("formerNames") or [],
        }

    # ── demonstrações (XBRL) ─────────────────────────────────────────────────
    def company_facts(self, symbol: str) -> dict | None:
        """companyfacts com cache do ÚLTIMO símbolo — as três demonstrações
        (income/balance/cashflow) são pedidas em sequência para o mesmo símbolo;
        sem o cache, baixaríamos o JSON (vários MB) 3× por empresa (gargalo real
        descoberto na varredura em escala)."""
        sym = normalize_symbol(symbol) or ""
        if self._facts_cache[0] == sym:
            return self._facts_cache[1]
        cik = self._cik_for(sym)
        facts = self._get(COMPANYFACTS_URL.format(cik=cik)) if cik else None
        self._facts_cache = (sym, facts)
        return facts

    def _rows(self, symbol: str, builder, limit: int) -> list[dict]:
        cf = self.company_facts(symbol)
        if not cf:
            return []
        rows = builder(cf, normalize_symbol(symbol))
        return rows[-limit:] if limit else rows

    def get_income_statements(self, symbol, period="annual", limit=20):
        if period != "annual":
            return []      # 10-Q/trimestral entra depois; não fingir que existe
        return self._rows(symbol, ef.build_income_rows, limit)

    def get_balance_sheets(self, symbol, period="annual", limit=20):
        if period != "annual":
            return []
        return self._rows(symbol, ef.build_balance_rows, limit)

    def get_cash_flow_statements(self, symbol, period="annual", limit=20):
        if period != "annual":
            return []
        return self._rows(symbol, ef.build_cashflow_rows, limit)

    def get_key_metrics(self, symbol, period="annual", limit=20):
        # A SEC não publica múltiplos — o projeto os calcula em core/us_metrics.py
        # a partir das demonstrações + preço. Nada a buscar aqui.
        return []


def build_edgar_provider(budget_limit: Optional[int] = None) -> EdgarProvider:
    """Fábrica que lê SEC_USER_AGENT de settings — usada pela CLI, nunca pela view."""
    from core.config import settings
    return EdgarProvider(user_agent=settings.SEC_USER_AGENT,
                         budget=Budget(limit=budget_limit))
