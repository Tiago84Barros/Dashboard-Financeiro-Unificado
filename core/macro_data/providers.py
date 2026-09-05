"""Provedores oficiais; transportes injetáveis deixam os testes sem rede."""

from __future__ import annotations

import csv
import io
import random
import time
from abc import ABC, abstractmethod
from datetime import date, datetime, timezone
from typing import Any, Callable
from urllib.parse import parse_qsl, unquote

import requests

from core.macro_data.models import (
    MacroIndicator,
    MacroObservation,
    MacroRelease,
    ObservationQuery,
    ProviderHealth,
)

RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class ProviderError(RuntimeError):
    pass


class HttpClient:
    """Cliente pequeno com timeout, backoff+jitter e resposta limitada."""

    def __init__(
        self,
        request: Callable[..., Any] = requests.get,
        *,
        timeout_s: int = 20,
        attempts: int = 3,
        max_bytes: int = 10_000_000,
        cache_ttl_s: int = 300,
        min_interval_s: float = 0.0,
        circuit_breaker_failures: int = 3,
        sleep=time.sleep,
        monotonic=time.monotonic,
    ):
        self.request, self.timeout_s, self.attempts, self.max_bytes, self.sleep = (
            request,
            timeout_s,
            attempts,
            max_bytes,
            sleep,
        )
        self.cache_ttl_s = max(cache_ttl_s, 0)
        self.min_interval_s = max(min_interval_s, 0.0)
        self.circuit_breaker_failures = max(circuit_breaker_failures, 1)
        self.monotonic = monotonic
        self._cache: dict[tuple[str, tuple[tuple[str, str], ...]], tuple[float, Any]] = {}
        self._last_request_at = 0.0
        self._consecutive_failures = 0
        self._circuit_open_until = 0.0

    def _cache_key(self, url: str, params: dict[str, Any] | None):
        return url, tuple(sorted((str(k), str(v)) for k, v in (params or {}).items()))

    def _get_cached(self, url: str, params: dict[str, Any] | None) -> Any | None:
        cached = self._cache.get(self._cache_key(url, params))
        if cached and cached[0] > self.monotonic():
            return cached[1]
        return None

    def _cache_value(self, url: str, params: dict[str, Any] | None, value: Any) -> None:
        if self.cache_ttl_s:
            self._cache[self._cache_key(url, params)] = (self.monotonic() + self.cache_ttl_s, value)

    def _guard_request(self) -> None:
        now = self.monotonic()
        if now < self._circuit_open_until:
            raise ProviderError("circuit breaker aberto temporariamente")
        wait = self.min_interval_s - (now - self._last_request_at)
        if wait > 0:
            self.sleep(wait)
        self._last_request_at = self.monotonic()

    def _record_failure(self) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= self.circuit_breaker_failures:
            self._circuit_open_until = self.monotonic() + 60

    def _record_success(self) -> None:
        self._consecutive_failures = 0
        self._circuit_open_until = 0.0

    def get_json(self, url: str, *, params: dict[str, Any] | None = None) -> Any:
        cached = self._get_cached(url, params)
        if cached is not None:
            return cached
        last: Exception | None = None
        for attempt in range(self.attempts):
            try:
                self._guard_request()
                response = self.request(
                    url,
                    params=params,
                    timeout=self.timeout_s,
                    headers={
                        "Accept": "application/json",
                        "User-Agent": "APP4-macro/1.0",
                    },
                )
                if getattr(response, "status_code", 200) in RETRYABLE_STATUS:
                    raise ProviderError(f"HTTP {response.status_code}")
                response.raise_for_status()
                if len(response.content) > self.max_bytes:
                    raise ProviderError("resposta excede o limite permitido")
                payload = response.json()
                self._record_success()
                self._cache_value(url, params, payload)
                return payload
            except (requests.RequestException, ProviderError, ValueError) as exc:
                last = exc
                self._record_failure()
                if attempt + 1 < self.attempts:
                    self.sleep((2**attempt) + random.uniform(0, 0.25))
        raise ProviderError(f"consulta falhou: {type(last).__name__}") from last

    def get_text(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        accept: str = "text/csv",
    ) -> str:
        """Consulta texto limitada, com tipo aceito explícito e sem registrar parâmetros."""
        cached = self._get_cached(url, params)
        if cached is not None:
            return cached
        last: Exception | None = None
        for attempt in range(self.attempts):
            try:
                self._guard_request()
                response = self.request(
                    url,
                    params=params,
                    timeout=self.timeout_s,
                    headers={"Accept": accept, "User-Agent": "APP4-macro/1.0"},
                )
                if getattr(response, "status_code", 200) in RETRYABLE_STATUS:
                    raise ProviderError(f"HTTP {response.status_code}")
                response.raise_for_status()
                if len(response.content) > self.max_bytes:
                    raise ProviderError("resposta excede o limite permitido")
                payload = response.text
                self._record_success()
                self._cache_value(url, params, payload)
                return payload
            except (requests.RequestException, ProviderError, ValueError) as exc:
                last = exc
                self._record_failure()
                if attempt + 1 < self.attempts:
                    self.sleep((2**attempt) + random.uniform(0, 0.25))
        raise ProviderError(f"consulta falhou: {type(last).__name__}") from last


class MacroDataProvider(ABC):
    provider_id: str

    @abstractmethod
    def health_check(self) -> ProviderHealth: ...

    @abstractmethod
    def fetch_metadata(
        self, provider_code: str, country_code: str | None = None
    ) -> list[MacroIndicator]: ...

    @abstractmethod
    def fetch_observations(self, query: ObservationQuery) -> list[MacroObservation]: ...

    def fetch_revisions(self, query: ObservationQuery) -> list[MacroObservation]:
        return []


class FredProvider(MacroDataProvider):
    provider_id = "fred"
    base_url = "https://api.stlouisfed.org/fred"

    def __init__(self, api_key: str | None, client: HttpClient | None = None):
        self.api_key, self.client = api_key, client or HttpClient()

    def _params(self, **extra: Any) -> dict[str, Any]:
        if not self.api_key:
            raise ProviderError("FRED_API_KEY não configurada")
        return {"api_key": self.api_key, "file_type": "json", **extra}

    def health_check(self) -> ProviderHealth:
        if not self.api_key:
            return ProviderHealth(self.provider_id, False, "chave não configurada")
        try:
            self.client.get_json(
                f"{self.base_url}/series", params=self._params(series_id="GNPCA")
            )
            return ProviderHealth(self.provider_id, True, "ok")
        except ProviderError as exc:
            return ProviderHealth(self.provider_id, False, type(exc).__name__)

    def fetch_metadata(
        self, provider_code: str, country_code: str | None = None
    ) -> list[MacroIndicator]:
        data = self.client.get_json(
            f"{self.base_url}/series", params=self._params(series_id=provider_code)
        )
        rows = data.get("seriess") or []
        return [
            MacroIndicator(
                canonical_code=f"fred.{row['id']}",
                provider_code=row["id"],
                provider=self.provider_id,
                name=row.get("title") or row["id"],
                description=row.get("notes"),
                category="unmapped",
                unit=row.get("units") or "unknown",
                frequency=_fred_frequency(row.get("frequency_short")),
                seasonal_adjustment=row.get("seasonal_adjustment_short"),
                source_organization="Federal Reserve Bank of St. Louis (FRED)",
                source_url=f"https://fred.stlouisfed.org/series/{row['id']}",
                country_code=country_code,
            )
            for row in rows
        ]

    def fetch_observations(self, query: ObservationQuery) -> list[MacroObservation]:
        params = self._params(series_id=query.provider_code)
        if query.start:
            params["observation_start"] = query.start.isoformat()
        if query.end:
            params["observation_end"] = query.end.isoformat()
        if query.vintage_date:
            params["realtime_start"] = params["realtime_end"] = (
                query.vintage_date.isoformat()
            )
        data = self.client.get_json(
            f"{self.base_url}/series/observations", params=params
        )
        now = datetime.now(timezone.utc)
        observations = []
        for row in data.get("observations", []):
            try:
                observations.append(
                    _fred_observation(self.provider_id, query, row, now)
                )
            except (KeyError, TypeError, ValueError):
                # Uma linha malformada não autoriza fabricar valor nem derrubar
                # as demais observações válidas do mesmo retorno.
                continue
        return observations

    def fetch_revisions(
        self, query: ObservationQuery, *, max_vintages: int | None = None
    ) -> list[MacroObservation]:
        dates = self.client.get_json(
            f"{self.base_url}/series/vintagedates",
            params=self._params(series_id=query.provider_code),
        ).get("vintage_dates", [])
        selected = []
        for vintage in dates:
            try:
                parsed = date.fromisoformat(vintage)
            except (TypeError, ValueError):
                continue
            if query.start and parsed < query.start:
                continue
            if query.end and parsed > query.end:
                continue
            selected.append(parsed)
        if max_vintages is not None:
            selected = selected[-max(max_vintages, 0) :]
        collected: list[MacroObservation] = []
        for vintage in selected:
            collected.extend(
                self.fetch_observations(
                    ObservationQuery(
                        query.provider_code,
                        query.country_code,
                        query.start,
                        query.end,
                        vintage,
                    )
                )
            )
        return collected


class WorldBankProvider(MacroDataProvider):
    provider_id = "world_bank"
    base_url = "https://api.worldbank.org/v2"

    def __init__(self, client: HttpClient | None = None):
        self.client = client or HttpClient()

    def health_check(self) -> ProviderHealth:
        try:
            self.client.get_json(
                f"{self.base_url}/country/BR", params={"format": "json"}
            )
            return ProviderHealth(self.provider_id, True, "ok")
        except ProviderError as exc:
            return ProviderHealth(self.provider_id, False, type(exc).__name__)

    def fetch_metadata(
        self, provider_code: str, country_code: str | None = None
    ) -> list[MacroIndicator]:
        data = self.client.get_json(
            f"{self.base_url}/indicator/{provider_code}", params={"format": "json"}
        )
        rows = data[1] if isinstance(data, list) and len(data) > 1 else []
        return [
            MacroIndicator(
                canonical_code=f"world_bank.{r['id']}",
                provider_code=r["id"],
                provider=self.provider_id,
                name=r.get("name") or r["id"],
                description=r.get("sourceNote"),
                category="unmapped",
                unit=r.get("unit") or "unknown",
                frequency="annual",
                source_organization=(r.get("source") or {}).get("value", "World Bank"),
                country_code=country_code,
            )
            for r in rows
        ]

    def fetch_observations(self, query: ObservationQuery) -> list[MacroObservation]:
        country = query.country_code or "all"
        page, out = 1, []
        while True:
            params: dict[str, Any] = {"format": "json", "per_page": 1000, "page": page}
            if query.start or query.end:
                params["date"] = (
                    f"{query.start.year if query.start else 1960}:{query.end.year if query.end else date.today().year}"
                )
            data = self.client.get_json(
                f"{self.base_url}/country/{country}/indicator/{query.provider_code}",
                params=params,
            )
            meta, rows = (data + [[], []])[:2] if isinstance(data, list) else ({}, [])
            now = datetime.now(timezone.utc)
            for row in rows or []:
                try:
                    period = date(int(row["date"]), 1, 1)
                except (ValueError, TypeError):
                    continue
                out.append(
                    MacroObservation(
                        self.provider_id,
                        query.provider_code,
                        period,
                        _number(row.get("value")),
                        now,
                        country_code=(row.get("countryiso3code") or query.country_code),
                        provider_updated_at=_parse_dt(row.get("lastupdated")),
                    )
                )
            if page >= int((meta or {}).get("pages") or 1):
                break
            page += 1
        return out


class TradingEconomicsProvider(MacroDataProvider):
    """Calendário opcional; não é requisito para a ingestão oficial básica."""

    provider_id = "trading_economics"
    base_url = "https://api.tradingeconomics.com"

    def __init__(self, api_key: str | None, client: HttpClient | None = None):
        self.api_key, self.client = api_key, client or HttpClient()

    def _params(self) -> dict[str, str]:
        if not self.api_key:
            raise ProviderError("TRADING_ECONOMICS_API_KEY não configurada")
        return {"c": self.api_key, "f": "json"}

    def health_check(self) -> ProviderHealth:
        if not self.api_key:
            return ProviderHealth(self.provider_id, False, "chave não configurada")
        # Não chama endpoint de calendário no health-check para preservar cota.
        return ProviderHealth(
            self.provider_id, True, "credencial configurada; validação ocorre na coleta"
        )

    def fetch_metadata(
        self, provider_code: str, country_code: str | None = None
    ) -> list[MacroIndicator]:
        return []

    def fetch_observations(self, query: ObservationQuery) -> list[MacroObservation]:
        return []

    def fetch_calendar(
        self, country_code: str, start: date, end: date
    ) -> list[MacroRelease]:
        if not country_code.isalpha() or len(country_code) not in {2, 3}:
            raise ProviderError("código de país inválido para calendário")
        data = self.client.get_json(
            f"{self.base_url}/calendar/country/{country_code}/{start.isoformat()}/{end.isoformat()}",
            params=self._params(),
        )
        now = datetime.now(timezone.utc)
        releases = []
        for item in data if isinstance(data, list) else []:
            scheduled = _parse_dt(item.get("Date"))
            if scheduled is None or not item.get("Event"):
                continue
            releases.append(
                MacroRelease(
                    self.provider_id,
                    country_code.upper(),
                    str(item["Event"])[:180],
                    scheduled,
                    now,
                    "released" if item.get("Actual") not in (None, "") else "scheduled",
                    actual_value=_number(item.get("Actual")),
                    previous_value=_number(item.get("Previous")),
                    consensus_value=_number(item.get("Consensus")),
                    forecast_value=_number(item.get("Forecast")),
                    unit=str(item.get("Unit") or "")[:40] or None,
                    importance=_importance(item.get("Importance")),
                    raw_payload_reference=f"te:{country_code}:{scheduled.isoformat()}:{str(item['Event'])[:50]}",
                )
            )
        return releases


class SdmxProvider(MacroDataProvider):
    """Adaptador SDMX deliberadamente genérico para fontes com dataflows distintos.

    O código da série é obrigatório e vem da descoberta oficial do próprio
    provedor; isso evita supor dimensões/códigos em uma URL montada por texto.
    """

    def __init__(
        self,
        provider_id: str,
        base_url: str,
        client: HttpClient | None = None,
        *,
        data_format: str = "csvfile",
    ):
        self.provider_id, self.base_url, self.client, self.data_format = (
            provider_id,
            base_url.rstrip("/"),
            client or HttpClient(),
            data_format,
        )

    def health_check(self) -> ProviderHealth:
        try:
            response = self.client.get_text(
                f"{self.base_url}/dataflow/all/all/latest",
                params={"format": self.data_format},
                accept="application/vnd.sdmx.structure+xml",
            )
            normalized = response.lstrip().lower()
            # Alguns gateways autenticados respondem HTTP 200 com a tela de
            # login. Não se trata isso como fonte disponível para ingestão.
            available = bool(normalized) and not (
                normalized.startswith("<!doctype html") or normalized.startswith("<html")
            )
            return ProviderHealth(
                self.provider_id, available, "ok" if available else "resposta não-SDMX"
            )
        except ProviderError as exc:
            return ProviderHealth(self.provider_id, False, type(exc).__name__)

    def fetch_metadata(
        self, provider_code: str, country_code: str | None = None
    ) -> list[MacroIndicator]:
        # SDMX metadata varies by agency. Conserva o código e exige mapeamento
        # explícito antes de a série entrar na taxonomia canônica.
        return [
            MacroIndicator(
                f"{self.provider_id}.{provider_code}",
                provider_code,
                self.provider_id,
                provider_code,
                "unmapped",
                "unknown",
                "irregular",
                self.provider_id.upper(),
                country_code=country_code,
            )
        ]

    def fetch_observations(self, query: ObservationQuery) -> list[MacroObservation]:
        flow, key = _parse_sdmx_spec(query.provider_code)
        params: dict[str, str] = {"format": self.data_format}
        if query.start:
            params["startPeriod"] = query.start.isoformat()
        if query.end:
            params["endPeriod"] = query.end.isoformat()
        raw = self.client.get_text(f"{self.base_url}/data/{flow}/{key}", params=params)
        return parse_sdmx_csv(
            raw,
            provider=self.provider_id,
            provider_code=query.provider_code,
            country_code=query.country_code,
        )


class BisProvider(SdmxProvider):
    """Adaptador BIS SDMX v2; a rota de dados inclui contexto e agência."""

    def __init__(self, client: HttpClient | None = None):
        super().__init__("bis", "https://stats.bis.org/api/v2", client, data_format="csvfile")

    def health_check(self) -> ProviderHealth:
        try:
            response = self.client.get_text(
                f"{self.base_url}/structure/dataflow/all/all/latest",
                accept="application/vnd.sdmx.structure+xml",
            )
            return ProviderHealth(self.provider_id, bool(response.strip()), "ok")
        except ProviderError as exc:
            return ProviderHealth(self.provider_id, False, type(exc).__name__)

    def fetch_observations(self, query: ObservationQuery) -> list[MacroObservation]:
        flow, key = _parse_sdmx_spec(query.provider_code)
        params: dict[str, str] = {"format": self.data_format}
        if query.start:
            params["startPeriod"] = query.start.isoformat()
        if query.end:
            params["endPeriod"] = query.end.isoformat()
        raw = self.client.get_text(
            f"{self.base_url}/data/dataflow/BIS/{flow}/1.0/{key}", params=params
        )
        return parse_sdmx_csv(
            raw,
            provider=self.provider_id,
            provider_code=query.provider_code,
            country_code=query.country_code,
        )


class EurostatProvider(SdmxProvider):
    """Eurostat usa Statistics API JSON-stat; série e filtros permanecem explícitos."""

    def __init__(self, client: HttpClient | None = None):
        super().__init__(
            "eurostat",
            "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0",
            client,
        )

    def health_check(self) -> ProviderHealth:
        try:
            # Consulta pequena e sem dados pessoais; não afirma disponibilidade
            # sem confirmar que a API pública responde JSON.
            payload = self.client.get_json(
                f"{self.base_url}/data/prc_hicp_manr",
                params={"geo": "EA20", "coicop": "CP00", "unit": "RCH_A", "lastTimePeriod": "1"},
            )
            return ProviderHealth(self.provider_id, isinstance(payload, dict), "ok")
        except ProviderError as exc:
            return ProviderHealth(self.provider_id, False, type(exc).__name__)

    def fetch_observations(self, query: ObservationQuery) -> list[MacroObservation]:
        dataset, filters = _parse_eurostat_spec(query.provider_code)
        params = {"lang": "EN", **filters}
        if query.start:
            params["sinceTimePeriod"] = query.start.isoformat()
        if query.end:
            params["untilTimePeriod"] = query.end.isoformat()
        payload = self.client.get_json(f"{self.base_url}/data/{dataset}", params=params)
        return parse_eurostat_jsonstat(
            payload,
            provider_code=query.provider_code,
            country_code=query.country_code,
        )


def configured_providers(settings) -> dict[str, MacroDataProvider]:
    """Instancia apenas fontes habilitadas, sem vazar chaves ou chamar rede."""
    providers: dict[str, MacroDataProvider] = {}
    if settings.macro_enabled("fred"):
        providers["fred"] = FredProvider(settings.FRED_API_KEY)
    if settings.macro_enabled("world_bank"):
        providers["world_bank"] = WorldBankProvider()
    sdmx = {
        "imf": "https://portal.api.imf.org/gateway/api/v1",
        "oecd": "https://sdmx.oecd.org/public/rest/v1",
        "ecb": "https://data-api.ecb.europa.eu/service",
    }
    for name, url in sdmx.items():
        if settings.macro_enabled(name):
            providers[name] = SdmxProvider(
                name, url, data_format="csvdata" if name == "ecb" else "csvfile"
            )
    if settings.macro_enabled("bis"):
        providers["bis"] = BisProvider()
    if settings.macro_enabled("eurostat"):
        providers["eurostat"] = EurostatProvider()
    if settings.macro_enabled("trading_economics"):
        providers["trading_economics"] = TradingEconomicsProvider(
            settings.TRADING_ECONOMICS_API_KEY
        )
    return providers


def _fred_frequency(value: str | None) -> str:
    return {
        "D": "daily",
        "W": "weekly",
        "BW": "weekly",
        "M": "monthly",
        "Q": "quarterly",
        "SA": "annual",
        "A": "annual",
    }.get(value or "", "irregular")


def _parse_sdmx_spec(spec: str) -> tuple[str, str]:
    """Aceita somente ``dataflow|series-key`` declarado em configuração."""
    flow, separator, key = (spec or "").partition("|")
    # ``%2C`` permite declarar os dataflows modernos da OECD em uma variável
    # cujo separador entre séries é vírgula. A validação ocorre após decodificar.
    flow = unquote(flow)
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._+-")
    flow_allowed = allowed | {",", "@"}
    if (
        not separator
        or not flow
        or not key
        or set(flow) - flow_allowed
        or set(key) - allowed
    ):
        raise ProviderError("série SDMX deve usar o formato dataflow|series-key")
    return flow, key


def _parse_eurostat_spec(spec: str) -> tuple[str, dict[str, str]]:
    """Valida ``dataset|dimensão=valor&...`` para a Statistics API.

    As dimensões permanecem explícitas no ambiente; esta função somente aceita
    identificadores simples para impedir que configuração vire URL arbitrária.
    """
    dataset, separator, raw_filters = (spec or "").partition("|")
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")
    if not separator or not dataset or set(dataset) - allowed:
        raise ProviderError("série Eurostat deve usar dataset|dimensão=valor")
    try:
        pairs = parse_qsl(raw_filters, keep_blank_values=True, strict_parsing=True)
    except ValueError as exc:
        raise ProviderError("filtros Eurostat inválidos") from exc
    filters: dict[str, str] = {}
    for key, value in pairs:
        if (
            not key
            or not value
            or set(key) - allowed
            or set(value) - allowed
            or key in filters
        ):
            raise ProviderError("filtros Eurostat inválidos")
        filters[key] = value
    if not filters:
        raise ProviderError("série Eurostat exige ao menos um filtro explícito")
    return dataset, filters


def parse_eurostat_jsonstat(
    payload: dict[str, Any], *, provider_code: str, country_code: str | None = None
) -> list[MacroObservation]:
    """Converte JSON-stat por índices declarados, sem assumir ordem de colunas."""
    dimensions = payload.get("dimension") if isinstance(payload, dict) else None
    dimension_ids = payload.get("id") if isinstance(payload, dict) else None
    sizes = payload.get("size") if isinstance(payload, dict) else None
    if not isinstance(dimensions, dict) or not isinstance(dimension_ids, list) or not isinstance(sizes, list):
        raise ProviderError("resposta JSON-stat Eurostat inválida")
    if len(dimension_ids) != len(sizes) or "time" not in dimension_ids:
        raise ProviderError("resposta JSON-stat sem dimensão temporal")
    try:
        numeric_sizes = [int(size) for size in sizes]
    except (TypeError, ValueError) as exc:
        raise ProviderError("dimensões JSON-stat inválidas") from exc
    if any(size < 1 for size in numeric_sizes):
        return []
    positions: dict[str, dict[int, str]] = {}
    for dimension_id in dimension_ids:
        index = dimensions.get(dimension_id, {}).get("category", {}).get("index", {})
        if not isinstance(index, dict):
            raise ProviderError("índices JSON-stat inválidos")
        try:
            positions[dimension_id] = {int(position): str(label) for label, position in index.items()}
        except (TypeError, ValueError) as exc:
            raise ProviderError("índices JSON-stat inválidos") from exc
    values = payload.get("value", {})
    items = values.items() if isinstance(values, dict) else enumerate(values) if isinstance(values, list) else ()
    now = datetime.now(timezone.utc)
    observations: list[MacroObservation] = []
    for raw_index, raw_value in items:
        try:
            flat_index = int(raw_index)
        except (TypeError, ValueError):
            continue
        value = _number(raw_value)
        if value is None:
            continue
        coordinates = []
        remainder = flat_index
        for size in reversed(numeric_sizes):
            coordinates.append(remainder % size)
            remainder //= size
        if remainder:
            continue
        coordinates.reverse()
        labels = {
            dimension_id: positions[dimension_id].get(coordinate)
            for dimension_id, coordinate in zip(dimension_ids, coordinates)
        }
        period = _sdmx_period(labels.get("time"))
        if period is None:
            continue
        observations.append(
            MacroObservation(
                "eurostat",
                provider_code,
                period,
                value,
                now,
                country_code=labels.get("geo") or country_code,
                raw_payload_reference=f"eurostat:{provider_code}:{period.isoformat()}",
            )
        )
    return observations


def parse_sdmx_csv(
    raw: str, *, provider: str, provider_code: str, country_code: str | None = None
) -> list[MacroObservation]:
    """Traduz CSV SDMX por nome de coluna, nunca por posição."""
    now = datetime.now(timezone.utc)
    rows: list[MacroObservation] = []
    for row in csv.DictReader(io.StringIO(raw)):
        period = _sdmx_period(row.get("TIME_PERIOD") or row.get("TIME"))
        value = _number(row.get("OBS_VALUE") or row.get("OBS_VALUE_DEC"))
        if period is None or value is None:
            continue
        rows.append(
            MacroObservation(
                provider,
                provider_code,
                period,
                value,
                now,
                country_code=row.get("REF_AREA") or country_code,
                status=row.get("OBS_STATUS"),
                raw_payload_reference=f"sdmx:{provider}:{provider_code}:{period.isoformat()}",
            )
        )
    return rows


def _sdmx_period(value: object) -> date | None:
    text = str(value or "").strip()
    try:
        if len(text) == 4 and text.isdigit():
            return date(int(text), 1, 1)
        if len(text) == 7 and text[4:6] == "-Q":
            return date(int(text[:4]), (int(text[-1]) - 1) * 3 + 1, 1)
        if len(text) == 7 and text[4] == "-":
            return date.fromisoformat(f"{text}-01")
        return date.fromisoformat(text)
    except (TypeError, ValueError):
        return None


def _number(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "", ".") else None
    except (TypeError, ValueError):
        return None


def _parse_dt(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(
            timezone.utc
        )
    except ValueError:
        return None


def _importance(value: object) -> int | None:
    try:
        parsed = int(value)
        return parsed if 0 <= parsed <= 3 else None
    except (TypeError, ValueError):
        return None


def _fred_observation(
    provider: str, query: ObservationQuery, row: dict[str, Any], now: datetime
) -> MacroObservation:
    vintage = row.get("realtime_start")
    return MacroObservation(
        provider,
        query.provider_code,
        date.fromisoformat(row["date"]),
        _number(row.get("value")),
        now,
        country_code=query.country_code,
        vintage_date=date.fromisoformat(vintage) if vintage else None,
        raw_payload_reference=f"fred:{query.provider_code}:{row['date']}:{vintage or 'latest'}",
    )
