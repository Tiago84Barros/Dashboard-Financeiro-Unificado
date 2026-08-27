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
import threading
import time
from typing import Any, Callable, Optional

from data_pipeline.us import edgar_facts as ef
from data_pipeline.us.identity import normalize_cik, normalize_symbol
from data_pipeline.us.providers import (
    Budget,
    FundamentalsProvider,
    MissingCredentialError,
    ProviderError,
    RateLimiter,
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


# ── A-147: BDC e fundo fechado, que a SEC nao classifica ──────────────────────
#
# `sic` e `sicDescription` voltam VAZIOS no submissions de toda BDC (medido em
# FS KKR, Hercules, Goldman Sachs BDC, Oaktree, Sixth Street e mais 51). Sem SIC
# a regra de `core/us_instrumento.py` fica cega -- ela reconhece a descricao
# "closed-end management investment offices", que nunca chega. O resultado eram
# 40 fundos de credito disputando ranking com companhia operacional.
#
# O que a SEC fornece de fato e o FORMULARIO. N-54A e a eleicao formal de virar
# BDC sob o Investment Company Act; N-2 e o registro de fundo fechado; N-CSR e
# NPORT sao os relatorios periodicos de companhia de investimento. Nenhuma
# empresa operacional os arquiva: medido em 15 suspeitos contra 10 controles
# (Exxon, Morgan Stanley, HCA, Unisys...), a separacao foi 14x0 -- e o unico
# suspeito sem marca era o Central Bancompany, banco de verdade, que o sinal
# corretamente deixou passar.
_FORMS_COMPANHIA_INVESTIMENTO = ("N-54A", "N-2", "N-CSR", "NPORT", "N-6F", "N-23C")

# A eleicao de BDC nao e permanente: a companhia pode RETIRA-LA arquivando um
# N-54C, e a partir dai volta a ser operacional. `filings.recent` guarda anos de
# historico, entao a simples presenca de um N-54A antigo diz o que a empresa
# FOI, nao o que ela e. Medido em 27/08/2026, tres das 50 marcadas eram isso:
# NewtekOne (retirada em 2023, hoje holding bancaria, SIC 6021), Medallion
# Financial (retirada em 2018, SIC 6199) e MacKenzie Realty (retirada em 2020,
# SIC 6798). Excluir essas do universo seria o espelho exato do defeito que o
# A-147 veio consertar. O SIC confirma por fora: companhia de investimento
# ativa nao tem SIC nenhum na SEC, e as tres recuperaram o seu ao sair.
_FORM_RETIRADA_ELEICAO = ("N-54C",)


def _ultima_data_de_forma(recentes: dict, prefixos: tuple[str, ...]) -> str | None:
    """Data do filing mais recente entre os formularios com estes prefixos.

    Datas ISO comparam corretamente como texto. Devolve None quando nenhum
    formulario casa OU quando a lista de datas nao acompanha a de formularios.
    """
    formularios = recentes.get("form") or []
    datas = recentes.get("filingDate") or []
    achadas = [
        str(datas[i]) for i, form in enumerate(formularios)
        if i < len(datas) and str(form or "").upper().strip().startswith(prefixos)
        and datas[i]
    ]
    return max(achadas) if achadas else None


def _tem_forma(recentes: dict, prefixos: tuple[str, ...]) -> bool:
    return any(str(f or "").upper().strip().startswith(prefixos)
               for f in (recentes.get("form") or []))


def _e_companhia_de_investimento(sub: dict) -> bool:
    """True quando os filings identificam BDC ou fundo fechado registrado HOJE."""
    recentes = ((sub or {}).get("filings", {}) or {}).get("recent", {}) or {}
    if not _tem_forma(recentes, _FORMS_COMPANHIA_INVESTIMENTO):
        return False
    if not _tem_forma(recentes, _FORM_RETIRADA_ELEICAO):
        return True
    eleicao = _ultima_data_de_forma(recentes, _FORMS_COMPANHIA_INVESTIMENTO)
    retirada = _ultima_data_de_forma(recentes, _FORM_RETIRADA_ELEICAO)
    if eleicao is None or retirada is None:
        # Sem data para comparar, a retirada existente e a evidencia mais
        # especifica: marcar como veiculo quem arquivou saida seria afirmar o
        # contrario do unico documento que fala do assunto.
        return False
    # Reeleicao depois da saida volta a valer: compara, nao assume ordem.
    return eleicao > retirada


class EdgarProvider(FundamentalsProvider):
    """Fundamentos via SEC EDGAR. Sem chave; exige User-Agent de contato."""

    def __init__(self, user_agent: str, session: Any = None, rate: int = 4,
                 per: float = 1.0, max_retries: int = 3,
                 budget: Optional[Budget] = None, timeout: float = 20.0,
                 time_fn: Callable[[], float] = time.monotonic,
                 sleep_fn: Callable[[float], None] = time.sleep):
        self.user_agent = (user_agent or "").strip()
        self._session = session
        self._thread_local = threading.local()
        self.max_retries = max_retries
        self.budget = budget or Budget()
        self.timeout = timeout
        self.sleep_fn = sleep_fn
        # SEC pede ≤ 10 req/s. Quatro chamadas/s reduz throttling silencioso em
        # varreduras longas e ainda mantém boa vazão com múltiplos workers.
        self.limiter = RateLimiter(rate=rate, per=per, time_fn=time_fn, sleep_fn=sleep_fn)
        self.calls_made = 0
        self._ticker_map: dict[str, str] | None = None
        # A-146: CIK conhecido localmente, para o ticker que o arquivo da SEC
        # deixou de listar. Ver `set_cik_hints`.
        self._cik_hints: dict[str, str] = {}
        self._facts_cache: dict[str, dict | None] = {}
        self._cache_lock = threading.Lock()

    @property
    def session(self):
        if self._session is not None:
            return self._session
        # requests.Session não é thread-safe. Cada worker mantém sua própria
        # conexão persistente, evitando travamentos raros em backfills longos.
        session = getattr(self._thread_local, "session", None)
        if session is None:
            import requests
            session = requests.Session()
            self._thread_local.session = session
        return session

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
        with self._cache_lock:
            cached = self._ticker_map
        if cached is None:
            data = self._get(TICKERS_URL) or {}
            rows = data.values() if isinstance(data, dict) else data
            mapped = {}
            for r in rows:
                sym = normalize_symbol(r.get("ticker"))
                cik = normalize_cik(r.get("cik_str") or r.get("cik"))
                if sym and cik:
                    mapped[sym] = cik
            with self._cache_lock:
                if self._ticker_map is None:
                    self._ticker_map = mapped
                cached = self._ticker_map
        return cached or {}

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

    def set_cik_hints(self, hints: dict) -> None:
        """Registra ticker→CIK ja conhecido, como ultimo recurso (A-146).

        `company_tickers.json` nao lista todo registrante: 21 empresas ativas do
        universo (CPRX, TMHC, AVNS, NFBK...) sumiram das duas listagens da SEC e
        por isso `get_profile` devolvia vazio, a ingestao abortava o simbolo e a
        empresa ficava congelada num parser antigo -- continuando elegível e
        disputando ranking com dado velho. O CIK delas ja estava em
        `market_us.companies`, obtido quando a SEC ainda as listava.

        A ordem importa: o arquivo oficial vem primeiro porque reflete
        reestruturacao recente; a dica so entra quando ele nao responde.
        """
        self._cik_hints = {normalize_symbol(k) or "": normalize_cik(v)
                           for k, v in (hints or {}).items()
                           if normalize_symbol(k) and normalize_cik(v)}

    def _cik_for(self, symbol: str) -> Optional[str]:
        sym = normalize_symbol(symbol) or ""
        if sym in _CIK_OVERRIDES:      # reestruturação conhecida → CIK operacional
            return _CIK_OVERRIDES[sym]
        return self.ticker_map().get(sym) or self._cik_hints.get(sym)

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
            "_investment_company": _e_companhia_de_investimento(sub),
            "_sic": sub.get("sic"),
            "_former_names": sub.get("formerNames") or [],
            "_source": "sec_edgar",
        }

    # ── demonstrações (XBRL) ─────────────────────────────────────────────────
    def company_facts(self, symbol: str) -> dict | None:
        """companyfacts com cache do ÚLTIMO símbolo — as três demonstrações
        (income/balance/cashflow) são pedidas em sequência para o mesmo símbolo;
        sem o cache, baixaríamos o JSON (vários MB) 3× por empresa (gargalo real
        descoberto na varredura em escala)."""
        sym = normalize_symbol(symbol) or ""
        with self._cache_lock:
            if sym in self._facts_cache:
                return self._facts_cache[sym]
        cik = self._cik_for(sym)
        facts = self._get(COMPANYFACTS_URL.format(cik=cik)) if cik else None
        with self._cache_lock:
            self._facts_cache[sym] = facts
            # Limite pequeno: cada JSON pode ter vários MB.
            while len(self._facts_cache) > 16:
                self._facts_cache.pop(next(iter(self._facts_cache)))
        return facts

    def _rows(self, symbol: str, builder, limit: int) -> list[dict]:
        cf = self.company_facts(symbol)
        if not cf:
            return []
        rows = builder(cf, normalize_symbol(symbol))
        return rows[-limit:] if limit else rows

    def get_income_statements(self, symbol, period="annual", limit=20):
        builder = ef.build_income_rows if period == "annual" else ef.build_income_quarterly_rows
        return self._rows(symbol, builder, limit) if period in {"annual", "quarterly"} else []

    def get_balance_sheets(self, symbol, period="annual", limit=20):
        builder = ef.build_balance_rows if period == "annual" else ef.build_balance_quarterly_rows
        return self._rows(symbol, builder, limit) if period in {"annual", "quarterly"} else []

    def get_cash_flow_statements(self, symbol, period="annual", limit=20):
        builder = ef.build_cashflow_rows if period == "annual" else ef.build_cashflow_quarterly_rows
        return self._rows(symbol, builder, limit) if period in {"annual", "quarterly"} else []

    def get_key_metrics(self, symbol, period="annual", limit=20):
        # A SEC não publica múltiplos — o projeto os calcula em core/us_metrics.py
        # a partir das demonstrações + preço. Nada a buscar aqui.
        return []


def build_edgar_provider(budget_limit: Optional[int] = None) -> EdgarProvider:
    """Fábrica que lê SEC_USER_AGENT de settings — usada pela CLI, nunca pela view."""
    from core.config import settings
    return EdgarProvider(user_agent=settings.SEC_USER_AGENT,
                         budget=Budget(limit=budget_limit))
