"""
data_pipeline/us/providers.py
Camada de PROVEDOR de dados americanos — abstração + implementação FMP.

Princípios (do enunciado):
  - A view NUNCA chama a API: só a ingestão usa este módulo.
  - A chave vem SÓ de settings.FMP_API_KEY (env/secrets), nunca de código/banco/UI
    e nunca aparece em logs (mascaramento em _mask).
  - Fonte substituível: as interfaces MarketDataProvider/FundamentalsProvider
    permitem trocar/complementar a FMP sem reescrever a ingestão.
  - Controle de custo: RateLimiter (token bucket), Budget (orçamento de chamadas),
    backoff exponencial + cooldown em 429, e estimate_calls() para dry-run.

Testabilidade: o clock (time_fn/sleep_fn) e o transporte HTTP (session) são
injetáveis — os testes exercitam rate-limit, backoff e budget SEM rede nem sleep
real.
"""
from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

logger = logging.getLogger("us_providers")


# ── Erros ─────────────────────────────────────────────────────────────────────
class ProviderError(RuntimeError):
    """Falha genérica de provedor."""


class RateLimitError(ProviderError):
    """429/limite de taxa atingido após esgotar os retries."""


class BudgetExceededError(ProviderError):
    """Orçamento de chamadas da execução foi atingido."""


class MissingCredentialError(ProviderError):
    """Chave da API ausente — ingestão não pode buscar dados novos."""


# ── Controle de taxa e orçamento ──────────────────────────────────────────────
@dataclass
class RateLimiter:
    """Token bucket simples: no máximo `rate` chamadas por `per` segundos.

    `time_fn`/`sleep_fn` injetáveis tornam o comportamento determinístico em teste.
    """
    rate: int = 300
    per: float = 60.0
    time_fn: Callable[[], float] = time.monotonic
    sleep_fn: Callable[[float], None] = time.sleep
    _allowance: float = field(default=None, init=False)  # type: ignore[assignment]
    _last: float = field(default=None, init=False)        # type: ignore[assignment]

    def __post_init__(self) -> None:
        self._allowance = float(self.rate)
        self._last = self.time_fn()

    def acquire(self) -> float:
        """Bloqueia (via sleep_fn) até haver crédito. Retorna o tempo dormido."""
        if self.rate <= 0:
            return 0.0
        now = self.time_fn()
        elapsed = now - self._last
        self._last = now
        self._allowance = min(float(self.rate), self._allowance + elapsed * (self.rate / self.per))
        slept = 0.0
        if self._allowance < 1.0:
            # tempo até acumular 1 token
            deficit = (1.0 - self._allowance) * (self.per / self.rate)
            self.sleep_fn(deficit)
            slept = deficit
            self._last = self.time_fn()
            self._allowance = 0.0
        else:
            self._allowance -= 1.0
        return slept


@dataclass
class Budget:
    """Orçamento de chamadas por execução (0/None = ilimitado)."""
    limit: Optional[int] = None
    spent: int = 0

    def charge(self, n: int = 1) -> None:
        if self.limit is not None and self.spent + n > self.limit:
            raise BudgetExceededError(
                f"orçamento de {self.limit} chamadas atingido (gastas={self.spent})")
        self.spent += n

    def remaining(self) -> Optional[int]:
        return None if self.limit is None else max(0, self.limit - self.spent)


def _mask(text: str, secret: str) -> str:
    """Remove a chave de qualquer string destinada a log."""
    if secret and secret in text:
        return text.replace(secret, "***")
    return text


# ── Interfaces abstratas ──────────────────────────────────────────────────────
class MarketDataProvider(ABC):
    """Preços/dividendos/splits — dados de mercado."""

    @abstractmethod
    def get_prices_daily(self, symbol: str, start: str | None = None,
                         end: str | None = None) -> list[dict]: ...

    @abstractmethod
    def get_dividends(self, symbol: str) -> list[dict]: ...

    @abstractmethod
    def get_splits(self, symbol: str) -> list[dict]: ...


class FundamentalsProvider(ABC):
    """Perfil e demonstrações financeiras/métricas."""

    @abstractmethod
    def get_universe(self, exchanges: list[str]) -> list[dict]: ...

    @abstractmethod
    def get_profile(self, symbol: str) -> dict | None: ...

    @abstractmethod
    def get_income_statements(self, symbol: str, period: str = "annual",
                              limit: int = 20) -> list[dict]: ...

    @abstractmethod
    def get_balance_sheets(self, symbol: str, period: str = "annual",
                           limit: int = 20) -> list[dict]: ...

    @abstractmethod
    def get_cash_flow_statements(self, symbol: str, period: str = "annual",
                                 limit: int = 20) -> list[dict]: ...

    @abstractmethod
    def get_key_metrics(self, symbol: str, period: str = "annual",
                        limit: int = 20) -> list[dict]: ...


# ── Implementação FMP ─────────────────────────────────────────────────────────
class FmpProvider(MarketDataProvider, FundamentalsProvider):
    """Financial Modeling Prep. Implementa ambas as interfaces.

    O transporte HTTP (`session`) é injetável para teste. Sem `session` real e
    sem `api_key`, qualquer chamada de rede levanta MissingCredentialError —
    nunca faz fallback silencioso.
    """

    STABLE_V3 = "v3"
    STABLE_V4 = "v4"

    def __init__(self, api_key: str, base_url: str = "https://financialmodelingprep.com/api",
                 session: Any = None, rate: int = 300, per: float = 60.0,
                 max_retries: int = 4, budget: Optional[Budget] = None,
                 time_fn: Callable[[], float] = time.monotonic,
                 sleep_fn: Callable[[float], None] = time.sleep,
                 timeout: float = 30.0):
        self.api_key = (api_key or "").strip()
        self.base_url = base_url.rstrip("/")
        self._session = session
        self.max_retries = max_retries
        self.budget = budget or Budget()
        self.timeout = timeout
        self.sleep_fn = sleep_fn
        self.limiter = RateLimiter(rate=rate, per=per, time_fn=time_fn, sleep_fn=sleep_fn)
        self.calls_made = 0

    # -- transporte ------------------------------------------------------------
    @property
    def session(self):
        if self._session is None:
            import requests  # import tardio: evita custo/depêndencia em import de módulo
            self._session = requests.Session()
        return self._session

    def _url(self, path: str, version: str) -> str:
        return f"{self.base_url}/{version}/{path.lstrip('/')}"

    def _get(self, path: str, version: str = STABLE_V3,
             params: dict | None = None) -> Any:
        """GET com rate-limit, budget, retry/backoff exponencial e cooldown 429.

        A chave é injetada só aqui e mascarada em qualquer log.
        """
        if not self.api_key:
            raise MissingCredentialError(
                "FMP_API_KEY ausente — configure a chave para ingerir dados novos.")
        self.budget.charge(1)
        q = dict(params or {})
        q["apikey"] = self.api_key
        url = self._url(path, version)
        last_exc: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            self.limiter.acquire()
            self.calls_made += 1
            try:
                resp = self.session.get(url, params=q, timeout=self.timeout)
            except Exception as exc:  # rede/timeout
                last_exc = exc
                self._backoff(attempt)
                continue
            status = getattr(resp, "status_code", 0)
            if status == 429:
                last_exc = RateLimitError("HTTP 429 (rate limit)")
                self._backoff(attempt, cooldown=True)
                continue
            if 500 <= status < 600:
                last_exc = ProviderError(f"HTTP {status}")
                self._backoff(attempt)
                continue
            if status != 200:
                raise ProviderError(
                    _mask(f"HTTP {status} em {url}", self.api_key))
            try:
                return resp.json()
            except Exception as exc:
                raise ProviderError(
                    _mask(f"resposta não-JSON de {url}: {exc}", self.api_key)) from exc
        if isinstance(last_exc, RateLimitError):
            raise last_exc
        raise ProviderError(
            _mask(f"falha após {self.max_retries} tentativas em {url}: {last_exc}",
                  self.api_key))

    def _backoff(self, attempt: int, cooldown: bool = False) -> None:
        # 0.5, 1, 2, 4... segundos; cooldown 429 é mais agressivo.
        base = 2.0 if cooldown else 0.5
        self.sleep_fn(base * (2 ** (attempt - 1)))

    # -- FundamentalsProvider --------------------------------------------------
    def get_universe(self, exchanges: list[str]) -> list[dict]:
        # /v3/stock/list traz todo o universo; filtramos por bolsa localmente.
        data = self._get("stock/list", self.STABLE_V3) or []
        allow = {e.upper() for e in exchanges}
        out = []
        for row in data:
            exch = str(row.get("exchangeShortName") or row.get("exchange") or "").upper()
            if not allow or exch in allow:
                out.append(row)
        return out

    def get_profile(self, symbol: str) -> dict | None:
        data = self._get(f"profile/{symbol}", self.STABLE_V3) or []
        return data[0] if isinstance(data, list) and data else (data or None)

    def get_income_statements(self, symbol, period="annual", limit=20):
        return self._get(f"income-statement/{symbol}", self.STABLE_V3,
                         {"period": period, "limit": limit}) or []

    def get_balance_sheets(self, symbol, period="annual", limit=20):
        return self._get(f"balance-sheet-statement/{symbol}", self.STABLE_V3,
                         {"period": period, "limit": limit}) or []

    def get_cash_flow_statements(self, symbol, period="annual", limit=20):
        return self._get(f"cash-flow-statement/{symbol}", self.STABLE_V3,
                         {"period": period, "limit": limit}) or []

    def get_key_metrics(self, symbol, period="annual", limit=20):
        return self._get(f"key-metrics/{symbol}", self.STABLE_V3,
                         {"period": period, "limit": limit}) or []

    # -- MarketDataProvider ----------------------------------------------------
    def get_prices_daily(self, symbol, start=None, end=None):
        params: dict = {}
        if start:
            params["from"] = start
        if end:
            params["to"] = end
        data = self._get(f"historical-price-full/{symbol}", self.STABLE_V3, params) or {}
        return data.get("historical", []) if isinstance(data, dict) else []

    def get_dividends(self, symbol):
        data = self._get(f"historical-price-full/stock_dividend/{symbol}",
                         self.STABLE_V3) or {}
        return data.get("historical", []) if isinstance(data, dict) else []

    def get_splits(self, symbol):
        data = self._get(f"historical-price-full/stock_split/{symbol}",
                         self.STABLE_V3) or {}
        return data.get("historical", []) if isinstance(data, dict) else []


# ── Estimativa de carga (dry-run) ─────────────────────────────────────────────
# Endpoints por símbolo cobrados na carga completa (perfil + 3 demonstrações +
# métricas + preços + dividendos + splits). Universo custa 1 chamada.
CALLS_PER_SYMBOL_FULL = 8


def estimate_calls(n_symbols: int, per_symbol: int = CALLS_PER_SYMBOL_FULL) -> dict:
    """Estima chamadas/tempo de uma carga completa (para o comando `estimate`)."""
    calls = 1 + n_symbols * per_symbol  # 1 = universo
    return {
        "symbols": n_symbols,
        "calls_per_symbol": per_symbol,
        "estimated_calls": calls,
    }


def build_default_provider(budget_limit: Optional[int] = None) -> FmpProvider:
    """Fábrica que lê a chave de settings — usada pela CLI, nunca pela view."""
    from core.config import settings
    return FmpProvider(
        api_key=settings.FMP_API_KEY,
        base_url=settings.FMP_BASE_URL,
        budget=Budget(limit=budget_limit),
    )
